"""绘图工作台 - 绘图任务业务层

本文件承载生成侧的全部业务（提交校验 / 并发闸门 / 历史与产物）。配置侧
（绘图服务与绘图模型）在 ``config/`` 目录。

**可见范围**：列表、详情、取消、重试、删除、下载一律经 ``CRUDBase``（数据范围由
``Permission`` 按 ``created_id`` 施加），越权标识与「不存在」同答复，不泄露他人
任务的存在性（spec: access / 绘图任务的可见范围）。
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.task.storage.node.service import StorageNodeService
from app.utils.common_util import search_to_dict

from ..config.model import ImageWorkbenchModelModel, ImageWorkbenchServiceModel
from ..config.schema import ImageWorkbenchCapabilitySchema
from ..config.service import _parse_capability
from .crud import ImageWorkbenchJobCRUD
from .engine import (
    artifact_url,
    delete_artifact,
    prepare_artifact_download,
    push_job_progress,
    request_cancel,
    resolve_reference_files,
    start_job_engine,
)
from .image_validation import validate_reference_image_file
from .model import ImageWorkbenchJobModel
from .schema import (
    ACTIVE_STATUSES,
    STATUS_CANCELLED,
    STATUS_PENDING,
    TERMINAL_STATUSES,
    ImageWorkbenchJobCreateSchema,
    ImageWorkbenchJobModelOptionSchema,
    ImageWorkbenchJobOutSchema,
    ImageWorkbenchJobQueryParam,
    status_label,
)

# 自定义尺寸的形态：「宽x高」的像素值（能力声明开启 allow_custom_size 时才接受）
_CUSTOM_SIZE_PATTERN = re.compile(r"^\d{2,5}[xX*]\d{2,5}$")


def _is_custom_size(value: str | None) -> bool:
    return bool(value and _CUSTOM_SIZE_PATTERN.fullmatch(value.strip()))


class ImageWorkbenchJobService:
    """绘图任务服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ImageWorkbenchJobCRUD(auth, db)

    # ────────────────────── 工作台可选模型 ──────────────────────

    async def list_available_models(self) -> list[ImageWorkbenchJobModelOptionSchema]:
        """工作台可选模型：模型启用 且 所属服务启用 且 服务已配置凭证（设计 D8）。

        停用的绘图服务与其下模型天然不在此列表中；历史绘图任务不受影响
        （它们只依赖已落库的 ``image_path`` 与 ``model_id`` 快照）。
        """
        stmt = (
            select(ImageWorkbenchModelModel, ImageWorkbenchServiceModel.name)
            .join(ImageWorkbenchServiceModel, ImageWorkbenchModelModel.service_id == ImageWorkbenchServiceModel.id)
            .where(
                ImageWorkbenchModelModel.status == 0,
                ImageWorkbenchModelModel.is_deleted == False,  # noqa: E712
                ImageWorkbenchServiceModel.status == 0,
                ImageWorkbenchServiceModel.is_deleted == False,  # noqa: E712
                ImageWorkbenchServiceModel.api_key_enc.isnot(None),
                ImageWorkbenchServiceModel.api_key_enc != "",
            )
            .order_by(ImageWorkbenchServiceModel.id, ImageWorkbenchModelModel.id)
        )
        rows = await self.db.execute(stmt)
        return [
            ImageWorkbenchJobModelOptionSchema(
                id=model.id,
                service_id=model.service_id,
                service_name=service_name,
                model_id=model.model_id,
                display_name=model.display_name,
                capability=_parse_capability(model.capability_json),
            )
            for model, service_name in rows.all()
        ]

    # ─────────────────────────── 提交 ───────────────────────────

    async def submit(self, data: ImageWorkbenchJobCreateSchema) -> ImageWorkbenchJobOutSchema:
        """提交一次绘图任务：能力校验 → 并发闸门 → 落库 pending → 提交后异步执行。

        参数 MUST 在提交时收敛到该模型的能力声明范围内（spec: generation / 提交绘图任务
        「不在该模型能力声明范围内的尺寸取值、输出格式或不支持的参考图 MUST 被拒绝」）。
        """
        model, service = await self._require_usable_model(data.model_pk)
        capability = _parse_capability(model.capability_json)

        size = self._validate_size(data.size, capability)
        output_format = self._validate_output_format(data.output_format, capability)
        reference_images = await self._validate_reference_images(data.reference_images, capability)
        # 水印用户侧不暴露开关（design D3）：缺省取能力声明的默认值
        watermark = data.watermark if data.watermark is not None else capability.watermark_default

        await self._check_concurrency()

        try:
            job = await self.crud.create(
                {
                    "service_id": service.id,
                    "model_pk": model.id,
                    # 标识快照：上游模型标识漂移不影响历史展示（design D2）
                    "model_id": model.model_id,
                    # 数据库唯一活跃槽位：终态释放为 NULL；并发插入时只能有一个成功
                    "active_owner_id": self.auth.user.id or None,
                    "prompt": data.prompt,
                    "params_json": {
                        "size": size,
                        "output_format": output_format,
                        "watermark": watermark,
                        "reference_images": reference_images,
                    },
                    "status": STATUS_PENDING,
                }
            )
        except CustomException as exc:
            if self._is_active_slot_conflict(exc):
                raise CustomException(
                    msg="您有正在进行的绘图任务，请等待其结束或取消后再提交",
                    code=400,
                    status_code=400,
                ) from exc
            raise
        await self.db.flush()
        # 事务提交后再启动后台执行：提前启动读不到未提交行（design D5）
        self._launch_after_commit(job.id)
        return await self._to_out(job)

    @staticmethod
    def _is_active_slot_conflict(exc: CustomException) -> bool:
        """仅将「用户活跃任务槽」唯一约束冲突转成友好提示。

        其他完整性错误仍原样上抛，避免把外键或必填字段问题误报成并发冲突。
        """
        cause = exc.__cause__
        if not isinstance(cause, IntegrityError):
            return False
        detail = str(cause.orig or cause).lower()
        return "ux_image_workbench_job_active_owner_id" in detail or "active_owner_id" in detail

    def _launch_after_commit(self, job_id: int) -> None:
        """请求事务提交后再启动后台执行（后台任务用独立会话）。"""

        @event.listens_for(self.db.sync_session, "after_commit", once=True)
        def _launch_on_commit(_session) -> None:
            start_job_engine(job_id)

    async def _require_usable_model(self, model_pk: int) -> tuple[ImageWorkbenchModelModel, ImageWorkbenchServiceModel]:
        """模型可用性：模型启用 且 所属服务启用 且 服务已配置凭证（design D8）。"""
        model = await self.db.get(ImageWorkbenchModelModel, model_pk)
        if model is None or model.is_deleted or model.status != 0:
            raise CustomException(msg="该绘图模型不存在或已停用，请重新选择", code=400, status_code=400)
        service = await self.db.get(ImageWorkbenchServiceModel, model.service_id)
        if service is None or service.is_deleted or service.status != 0:
            raise CustomException(msg="该绘图模型所属的绘图服务已停用，请重新选择", code=400, status_code=400)
        if not service.api_key_enc:
            raise CustomException(msg="该绘图服务尚未配置调用凭证，请联系管理员", code=400, status_code=400)
        return model, service

    @staticmethod
    def _validate_size(size: str | None, capability: ImageWorkbenchCapabilitySchema) -> str | None:
        """尺寸取值必须落在能力声明内（不做「方/横/竖」归一化翻译，design D3）。"""
        if not size:
            return None
        if size in capability.sizes:
            return size
        if capability.allow_custom_size and _is_custom_size(size):
            return size
        if capability.sizes:
            raise CustomException(msg=f"尺寸「{size}」不在该模型支持的取值内，可选：{'、'.join(capability.sizes)}", code=400, status_code=400)
        raise CustomException(msg="该绘图模型未声明可用尺寸，请勿指定尺寸参数", code=400, status_code=400)

    @staticmethod
    def _validate_output_format(output_format: str | None, capability: ImageWorkbenchCapabilitySchema) -> str | None:
        if not capability.output_formats:
            return None
        value = output_format or capability.default_output_format
        if value not in capability.output_formats:
            raise CustomException(msg=f"输出格式「{value}」不在该模型支持的取值内，可选：{'、'.join(capability.output_formats)}", code=400, status_code=400)
        return value

    @staticmethod
    async def _validate_reference_images(reference_images: list[str], capability: ImageWorkbenchCapabilitySchema) -> list[str]:
        """参考图：支持与否、张数上限（能力声明），以及路径必须落在上传目录内。"""
        if not reference_images:
            return []
        if not capability.support_reference:
            raise CustomException(msg="该绘图模型不支持参考图，请移除参考图后重试", code=400, status_code=400)
        if len(reference_images) > capability.max_reference_images:
            raise CustomException(
                msg=f"参考图最多 {capability.max_reference_images} 张，当前 {len(reference_images)} 张",
                code=400,
                status_code=400,
            )
        # 存在性与越界校验（越界即拒绝，避免借参考图字段读取服务器上的任意文件）
        resolved = resolve_reference_files(reference_images)
        for path in resolved:
            validate_reference_image_file(path)
        return reference_images

    async def _check_concurrency(self) -> None:
        """并发闸门：该用户已有进行中的任务即拒绝（spec: generation / 并发闸门）。"""
        user_id = self.auth.user.id
        if not user_id:
            return
        active = await self.crud.count_active_by_user(user_id)
        if active >= 1:
            raise CustomException(msg="您有正在进行的绘图任务，请等待其结束或取消后再提交", code=400, status_code=400)

    # ─────────────────────────── 查询 ───────────────────────────

    async def require_job(self, id: int) -> ImageWorkbenchJobModel:
        """按可见范围取任务；不可见与不存在同答复（不泄露他人任务的存在性）。"""
        job = await self.crud.get(id=id)
        if job is None:
            raise CustomException(msg="绘图任务不存在或不在您的可见范围内", code=400, status_code=404)
        return job

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ImageWorkbenchJobQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ImageWorkbenchJobOutSchema]:
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
        )
        context = await self._out_context()
        items = [await self._to_out(obj, context=context) for obj in result.items]  # type: ignore[union-attr]
        result.items = items  # type: ignore[assignment]
        return result

    async def detail(self, id: int) -> ImageWorkbenchJobOutSchema:
        job = await self.require_job(id)
        return await self._to_out(job)

    async def _out_context(self) -> dict[str, Any]:
        """列表页的批量上下文：名称映射 + 默认存储节点，避免逐行 N+1。"""
        service_names = {row[0]: row[1] for row in (await self.db.execute(select(ImageWorkbenchServiceModel.id, ImageWorkbenchServiceModel.name))).all()}
        model_names = {row[0]: row[1] for row in (await self.db.execute(select(ImageWorkbenchModelModel.id, ImageWorkbenchModelModel.display_name))).all()}
        try:
            source = await StorageNodeService(AuthSchema(), self.db).get_active_source()
        except Exception:  # noqa: BLE001 — 未配置存储源不该让历史列表整体 500
            source = None
        return {"service_names": service_names, "model_names": model_names, "source": source}

    async def _to_out(self, job: ImageWorkbenchJobModel, context: dict[str, Any] | None = None) -> ImageWorkbenchJobOutSchema:
        ctx = context if context is not None else await self._out_context()
        return ImageWorkbenchJobOutSchema(
            id=job.id,
            uuid=job.uuid,
            created_time=job.created_time,
            updated_time=job.updated_time,
            is_deleted=job.is_deleted,
            deleted_time=job.deleted_time,
            created_id=job.created_id,
            service_id=job.service_id,
            service_name=ctx["service_names"].get(job.service_id),
            model_pk=job.model_pk,
            model_id=job.model_id,
            model_display_name=ctx["model_names"].get(job.model_pk),
            prompt=job.prompt,
            params=job.params_json,
            status=job.status,
            status_label=status_label(job.status),
            error=job.error,
            usage=job.usage_json,
            elapsed_ms=job.elapsed_ms,
            image_path=job.image_path,
            # 停用 / 删除的模型不影响历史展示（名称取自裸查，不走软删过滤）
            image_url=artifact_url(ctx["source"], job.image_path),
            image_size=job.image_size,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    # ─────────────────────────── 取消 / 重试 ───────────────────────────

    async def cancel(self, id: int) -> None:
        """取消进行中的任务：直接落终态 + 中断在途执行（design D6）。

        取消**立即**释放该用户的并发额度、界面立即停止等待；引擎在收尾时读到
        ``cancelled`` 便不再覆盖（取消优先于成功）。取消语义为「放弃等待」，不保证
        上游不产生费用——接口返回与前端确认框都写明这一点。
        """
        job = await self.require_job(id)
        if job.status not in ACTIVE_STATUSES:
            raise CustomException(msg=f"当前状态（{status_label(job.status)}）不可取消", code=400, status_code=400)
        job.status = STATUS_CANCELLED
        job.active_owner_id = None
        job.finished_at = datetime.now(UTC)
        job.error = None
        await self.db.flush()
        request_cancel(id)
        await push_job_progress(id, job.created_id, status=STATUS_CANCELLED, message="任务已取消")

    async def retry(self, id: int) -> ImageWorkbenchJobOutSchema:
        """以原参数新建任务（spec: generation / 失败与手动重试）。

        原失败记录**原样保留**（MUST NOT 覆盖或删除）；重试同样受并发闸门约束。
        """
        job = await self.require_job(id)
        if job.status not in TERMINAL_STATUSES:
            raise CustomException(msg="仅已结束的任务可重试，进行中的任务请先取消", code=400, status_code=400)
        params: dict[str, Any] = dict(job.params_json or {})
        return await self.submit(
            ImageWorkbenchJobCreateSchema(
                model_pk=job.model_pk,
                prompt=job.prompt,
                size=params.get("size"),
                output_format=params.get("output_format"),
                watermark=params.get("watermark"),
                reference_images=params.get("reference_images") or [],
            )
        )

    # ────────────────────────── 删除 / 下载 ───────────────────────────

    async def delete(self, ids: list[int]) -> None:
        """删除已结束的任务：软删记录 + **真删**存储节点上的产物文件（design D8）。

        进行中的任务 MUST NOT 可删除；产物文件删除失败则整体失败（不留「记录没了
        文件还在」的悬空产物）。
        """
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空", code=400, status_code=400)
        for id_ in ids:
            job = await self.require_job(id_)
            if job.status not in TERMINAL_STATUSES:
                raise CustomException(msg=f"任务 #{id_} 正在进行中，请先取消后再删除", code=400, status_code=400)
            await delete_artifact(self.db, job.image_path)
        await self.crud.delete(ids=ids)

    async def download(self, id: int) -> tuple[str, str, bool]:
        """下载产物，返回 ``(本机绝对路径, 下载文件名, 是否为需清理的临时文件)``。"""
        job = await self.require_job(id)
        if not job.image_path:
            raise CustomException(msg="该任务没有可下载的产物", code=400, status_code=400)
        abs_path, is_temp = await prepare_artifact_download(self.db, job.image_path)
        return abs_path, f"{job.uuid}{Path(job.image_path).suffix}", is_temp
