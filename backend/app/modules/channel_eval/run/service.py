"""渠道评估 - 测试运行服务（spec: channel-eval/test-run）"""

from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.utils.common_util import search_to_dict

from .aggregation import aggregate_combo, combo_samples_by_id, samples_to_out, threshold_ms_from_params
from .crud import ChannelEvalRunCRUD
from .engine import start_run_engine
from .model import ChannelEvalRunComboModel, ChannelEvalRunModel, ChannelEvalSampleModel
from .new_api_client import probe_pin_capability
from .provision import ProvisionError, ensure_eval_token
from .registry import run_task_registry
from .schema import (
    ChannelEvalRunCreateSchema,
    ChannelEvalRunDetailSchema,
    ChannelEvalRunOutSchema,
    ChannelEvalRunQueryParam,
)


class ChannelEvalRunService:
    """测试运行服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ChannelEvalRunCRUD(auth, db)

    # ─────────────────────── 创建 ───────────────────────

    async def create(self, data: ChannelEvalRunCreateSchema) -> ChannelEvalRunOutSchema:
        """创建运行：组合清单 + 参数快照持久化；同平台串行约束；随后启动异步执行。"""
        if not data.combos:
            raise CustomException(msg="至少选择一个「渠道 × 模型」组合", code=400, status_code=400)

        platform = await self.db.get(ChannelEvalPlatformModel, data.platform_id)
        if platform is None:
            raise CustomException(msg="平台不存在", code=400, status_code=400)
        if platform.status != 0:
            raise CustomException(msg="平台已停用，无法发起测试运行", code=400, status_code=400)

        # 同平台串行：存在 pending/running/cancelling 运行则拒绝（避免日志时间窗交叉污染）
        if await self.crud.has_active_run(data.platform_id):
            raise CustomException(msg="该平台存在待执行/执行中的运行，请等待其完成后再创建（同平台串行执行）", code=400, status_code=400)

        params: dict[str, Any] = {
            "request_count": data.request_count,
            "warmup": data.warmup,
            "interval_seconds": data.interval_seconds,
            "timeout_seconds": data.timeout_seconds,
            "prompt": data.prompt,
            "anomaly_threshold_seconds": data.anomaly_threshold_seconds,
        }
        run = await self.crud.create({"platform_id": data.platform_id, "status": "pending", "params_json": params})
        for combo in data.combos:
            self.db.add(
                ChannelEvalRunComboModel(
                    run_id=run.id,
                    channel_id=combo.channel_id,
                    channel_name=combo.channel_name,
                    model=combo.model,
                    status="pending",
                    planned=data.request_count,
                )
            )
        await self.db.flush()
        # 指定渠道（ADR-0001）：admin 平台在创建时建 1 个专用管理员令牌并探测 pin 能力。
        # 失败即拒绝创建（事务回滚），避免运行建成后全部样本因 403/401 失败。
        if platform.type == "admin":
            try:
                token_key = await ensure_eval_token(self.db, platform, run)
            except ProvisionError as e:
                raise CustomException(msg=str(e), code=400, status_code=400) from e
            ok, message = await probe_pin_capability(platform.base_url, token_key)
            if not ok:
                raise CustomException(msg=message, code=400, status_code=400)
            await self.db.flush()
        self._launch_after_commit(run.id)
        return await self._to_out(run)

    def _launch_after_commit(self, run_id: int) -> None:
        """请求事务提交后再启动后台执行（后台任务用独立会话，提前启动读不到未提交行）。"""

        @event.listens_for(self.db.sync_session, "after_commit", once=True)
        def _launch_on_commit(_session) -> None:
            start_run_engine(run_id)

    # ─────────────────────── 查询 ───────────────────────

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ChannelEvalRunQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ChannelEvalRunOutSchema]:
        offset = (page_no - 1) * page_size
        result = await self.crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
        )
        platform_names = await self._platform_name_map()
        items: list[ChannelEvalRunOutSchema] = []
        for obj in result.items:  # type: ignore[union-attr]
            out = await self._to_out(obj, platform_names=platform_names)
            items.append(out)
        result.items = items  # type: ignore[assignment]
        return result

    async def detail(self, id: int) -> ChannelEvalRunDetailSchema:
        """运行详情：参数快照 + 组合清单（含聚合） + 逐样本明细（含后台配对字段）。"""
        run = await self.crud.get_or_404(id=id, msg="运行不存在")
        platform = await self.db.get(ChannelEvalPlatformModel, run.platform_id)
        combos = (await self.db.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == id).order_by(ChannelEvalRunComboModel.id))).scalars().all()
        samples = (
            (
                await self.db.execute(
                    select(ChannelEvalSampleModel)
                    .join(ChannelEvalRunComboModel, ChannelEvalSampleModel.combo_id == ChannelEvalRunComboModel.id)
                    .where(ChannelEvalRunComboModel.run_id == id)
                    .order_by(ChannelEvalSampleModel.id)
                )
            )
            .scalars()
            .all()
        )
        threshold_ms = threshold_ms_from_params(run.params_json)
        grouped = combo_samples_by_id(list(samples))
        aggregates = [aggregate_combo(combo, grouped.get(combo.id, []), threshold_ms) for combo in combos]
        base = await self._to_out(run)
        payload = base.model_dump()
        payload.update(
            platform_name=platform.name if platform else None,
            combos=aggregates,
            samples=samples_to_out(list(samples)),
        )
        return ChannelEvalRunDetailSchema(**payload)

    async def _platform_name_map(self) -> dict[int, str]:
        result = await self.db.execute(select(ChannelEvalPlatformModel.id, ChannelEvalPlatformModel.name))
        return {row[0]: row[1] for row in result.all()}

    async def _to_out(self, run: ChannelEvalRunModel, platform_names: dict[int, str] | None = None) -> ChannelEvalRunOutSchema:
        if platform_names is None:
            platform_names = await self._platform_name_map()
        combo_count = len(run.combos) if "combos" in run.__dict__ else None
        if combo_count is None:
            result = await self.db.execute(select(ChannelEvalRunComboModel.id).where(ChannelEvalRunComboModel.run_id == run.id))
            combo_count = len(result.all())
        return ChannelEvalRunOutSchema(
            id=run.id,
            created_time=run.created_time,
            updated_time=run.updated_time,
            platform_id=run.platform_id,
            platform_name=platform_names.get(run.platform_id),
            status=run.status,
            params=run.params_json,
            combo_count=combo_count,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
        )

    # ─────────────────────── 取消 ───────────────────────

    async def cancel(self, id: int) -> None:
        """取消执行中的运行：置 cancelling，引擎在组合/请求边界检查并退出；已产生样本保留。"""
        run = await self.crud.get_or_404(id=id, msg="运行不存在")
        if run.status not in ("pending", "running"):
            raise CustomException(msg=f"当前状态（{run.status}）不可取消", code=400, status_code=400)
        run.status = "cancelling"
        await self.db.flush()
        run_task_registry.mark_cancel(id)
