"""渠道评估 - 对比报告服务（spec: channel-eval/compare-report）

- 报告 = 选择 + 备注，不复制测量数据；Web 视图打开时基于所选运行的持久化数据实时渲染
- 基准校验：基准运行须为基准渠道产生的成功运行
- 自动结论仅陈述阈值事实（高于基准幅度 / 异常样本 / 成功率非100%），无评价性措辞
- HTML 导出：Jinja2 自包含渲染，数据 JSON 内嵌、零外部依赖、离线可开（导出即冻结）
"""

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.run.aggregation import is_anomaly, threshold_ms_from_params
from app.modules.channel_eval.run.model import (
    ChannelEvalRunComboModel,
    ChannelEvalRunModel,
    ChannelEvalSampleModel,
)
from app.utils.common_util import search_to_dict

from .crud import ChannelEvalReportCRUD
from .schema import (
    ChannelEvalReportCreateSchema,
    ChannelEvalReportOutSchema,
    ChannelEvalReportQueryParam,
    ChannelEvalReportUpdateSchema,
    ChannelEvalReportViewSchema,
    ReportAnomalyItemSchema,
    ReportComboViewSchema,
    ReportConclusionSchema,
    ReportModelGroupSchema,
    ReportRunInfoSchema,
)

# 自动结论阈值：客户端/后台指标高于基准该幅度即陈述事实（spec scenario：高于基准 2 秒以上）
CONCLUSION_DIFF_THRESHOLD_MS = 2000

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _avg(values: list[float | int | None]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


class ChannelEvalReportService:
    """对比报告服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ChannelEvalReportCRUD(auth, db)

    # ─────────────────────── CRUD ───────────────────────

    async def detail(self, id: int) -> ChannelEvalReportOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="报告不存在")
        return ChannelEvalReportOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ChannelEvalReportQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ChannelEvalReportOutSchema]:
        offset = (page_no - 1) * page_size
        return await self.crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=ChannelEvalReportOutSchema,
        )

    async def create(self, data: ChannelEvalReportCreateSchema) -> ChannelEvalReportOutSchema:
        await self._validate_selection(data)
        obj = await self.crud.create(
            {
                "name": data.name,
                "run_ids": data.run_ids,
                "baseline_run_id": data.baseline_run_id,
                "note": data.note,
            }
        )
        return ChannelEvalReportOutSchema.model_validate(obj)

    async def update(self, id: int, data: ChannelEvalReportUpdateSchema) -> ChannelEvalReportOutSchema:
        await self.crud.get_or_404(id=id, msg="报告不存在")
        await self._validate_selection(data)
        obj = await self.crud.update(
            id,
            {
                "name": data.name,
                "run_ids": data.run_ids,
                "baseline_run_id": data.baseline_run_id,
                "note": data.note,
            },
        )
        return ChannelEvalReportOutSchema.model_validate(obj)

    async def delete(self, ids: list[int]) -> None:
        await self.crud.delete(ids=ids)

    async def _validate_selection(self, data: ChannelEvalReportCreateSchema) -> None:
        """校验所选运行存在；基准运行须为基准渠道的成功运行。"""
        for run_id in data.run_ids:
            run = await self.db.get(ChannelEvalRunModel, run_id)
            if run is None:
                raise CustomException(msg=f"测试运行 {run_id} 不存在", code=400, status_code=400)
        if data.baseline_run_id is not None:
            run = await self.db.get(ChannelEvalRunModel, data.baseline_run_id)
            if run is None:
                raise CustomException(msg="基准运行不存在", code=400, status_code=400)
            platform = await self.db.get(ChannelEvalPlatformModel, run.platform_id)
            if platform is None or not platform.is_baseline:
                raise CustomException(msg="基准运行须来自基准渠道", code=400, status_code=400)
            if run.status != "success":
                raise CustomException(msg="基准运行须为成功完成的运行", code=400, status_code=400)

    # ─────────────────────── 视图聚合 ───────────────────────

    async def view(self, id: int) -> ChannelEvalReportViewSchema:
        report = await self.crud.get_or_404(id=id, msg="报告不存在")
        return await self._build_view(ChannelEvalReportOutSchema.model_validate(report))

    async def _build_view(self, report: ChannelEvalReportOutSchema) -> ChannelEvalReportViewSchema:
        """按模型分组并列各运行组合与基准；客户端 vs 后台交叉对比；无数据"—"、无基准"无基准数据"。"""
        all_run_ids = list(report.run_ids or [])
        if report.baseline_run_id is not None and report.baseline_run_id not in all_run_ids:
            all_run_ids.append(report.baseline_run_id)

        runs_info: list[ReportRunInfoSchema] = []
        baseline_run_info: ReportRunInfoSchema | None = None
        # run_id -> (combo聚合列表, 异常条目列表)
        combo_views_by_run: dict[int, list[ReportComboViewSchema]] = {}
        anomaly_items: list[ReportAnomalyItemSchema] = []

        for run_id in all_run_ids:
            run = await self.db.get(ChannelEvalRunModel, run_id)
            if run is None:
                continue
            platform = await self.db.get(ChannelEvalPlatformModel, run.platform_id)
            info = ReportRunInfoSchema(
                id=run.id,
                platform_id=run.platform_id,
                platform_name=platform.name if platform else None,
                status=run.status,
                is_baseline=bool(platform.is_baseline) if platform else False,
                created_time=run.created_time,
                combo_count=0,
            )
            runs_info.append(info)
            if report.baseline_run_id == run.id:
                baseline_run_info = info

            combos = (await self.db.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == run.id).order_by(ChannelEvalRunComboModel.id))).scalars().all()
            samples = (
                (
                    await self.db.execute(
                        select(ChannelEvalSampleModel)
                        .join(ChannelEvalRunComboModel, ChannelEvalSampleModel.combo_id == ChannelEvalRunComboModel.id)
                        .where(ChannelEvalRunComboModel.run_id == run.id)
                        .order_by(ChannelEvalSampleModel.id)
                    )
                )
                .scalars()
                .all()
            )
            info.combo_count = len(combos)
            threshold_ms = threshold_ms_from_params(run.params_json)
            samples_by_combo: dict[int, list[ChannelEvalSampleModel]] = {}
            for sample in samples:
                samples_by_combo.setdefault(sample.combo_id, []).append(sample)

            for combo in combos:
                combo_samples = samples_by_combo.get(combo.id, [])
                combo_views_by_run.setdefault(run.id, []).append(self._build_combo_view(run.id, info, combo, combo_samples, threshold_ms))
                # 异常清单：超阈值样本 + 失败样本（非热身）
                for sample in combo_samples:
                    if sample.is_warmup:
                        continue
                    kind = None
                    if sample.status == "failed":
                        kind = "failed"
                    elif is_anomaly(sample, threshold_ms):
                        kind = "anomaly"
                    if kind:
                        anomaly_items.append(
                            ReportAnomalyItemSchema(
                                kind=kind,
                                run_id=run.id,
                                platform_name=info.platform_name,
                                channel_id=combo.channel_id,
                                channel_name=combo.channel_name,
                                model=combo.model,
                                seq=sample.seq,
                                is_warmup=sample.is_warmup,
                                status=sample.status,
                                ttft_ms=sample.ttft_ms,
                                total_ms=sample.total_ms,
                                error=sample.error,
                                be_matched=sample.be_matched,
                            )
                        )

        # 按模型分组
        baseline_combos_by_model: dict[str, ReportComboViewSchema] = {}
        if baseline_run_info is not None:
            for combo_view in combo_views_by_run.get(baseline_run_info.id, []):
                baseline_combos_by_model[combo_view.model] = combo_view

        models: dict[str, ReportModelGroupSchema] = {}
        for run_id in report.run_ids or []:
            for combo_view in combo_views_by_run.get(run_id, []):
                group = models.setdefault(
                    combo_view.model,
                    ReportModelGroupSchema(model=combo_view.model, combos=[], has_baseline=combo_view.model in baseline_combos_by_model),
                )
                baseline = baseline_combos_by_model.get(combo_view.model)
                if baseline is not None:
                    group.baseline = baseline
                    combo_view.ttft_vs_baseline_ms = round(combo_view.ttft_avg_ms - baseline.ttft_avg_ms, 2) if combo_view.ttft_avg_ms is not None and baseline.ttft_avg_ms is not None else None
                    combo_view.total_vs_baseline_ms = round(combo_view.total_avg_ms - baseline.total_avg_ms, 2) if combo_view.total_avg_ms is not None and baseline.total_avg_ms is not None else None
                group.combos.append(combo_view)
        for group in models.values():
            if group.baseline is None:
                group.baseline = baseline_combos_by_model.get(group.model)

        conclusions = self._build_conclusions(models)
        return ChannelEvalReportViewSchema(
            report=report,
            runs=runs_info,
            baseline_run=baseline_run_info,
            models=[models[key] for key in sorted(models)],
            anomalies=anomaly_items,
            conclusions=conclusions,
            generated_at=datetime.now(UTC).isoformat(),
        )

    def _build_combo_view(
        self,
        run_id: int,
        run_info: ReportRunInfoSchema,
        combo: ChannelEvalRunComboModel,
        samples: list[ChannelEvalSampleModel],
        threshold_ms: int,
    ) -> ReportComboViewSchema:
        """单组合聚合视图：客户端(剔除/含异常) + 后台(配对) + 交叉差值。"""
        non_warmup = [s for s in samples if not s.is_warmup]
        success_samples = [s for s in non_warmup if s.status == "success"]
        anomaly_samples = [s for s in success_samples if is_anomaly(s, threshold_ms)]
        valid_samples = [s for s in success_samples if not is_anomaly(s, threshold_ms)]
        # 后台侧：与客户端同口径的有效样本中已配对者
        matched_valid = [s for s in valid_samples if s.be_matched]
        ttft_avg = _avg([s.ttft_ms for s in valid_samples])
        total_avg = _avg([s.total_ms for s in valid_samples])
        be_frt_avg = _avg([s.be_frt_ms for s in matched_valid])
        be_use_time_avg = _avg([s.be_use_time_ms for s in matched_valid])
        planned = combo.planned or 0
        success_rate: float | None = None
        if planned > 0:
            success_rate = round(len(success_samples) / planned, 4)
        elif non_warmup:
            success_rate = round(len(success_samples) / len(non_warmup), 4)
        return ReportComboViewSchema(
            run_id=run_id,
            platform_id=run_info.platform_id,
            platform_name=run_info.platform_name,
            channel_id=combo.channel_id,
            channel_name=combo.channel_name,
            model=combo.model,
            is_baseline=run_info.is_baseline,
            planned=planned,
            success=len(success_samples),
            failed=sum(1 for s in non_warmup if s.status == "failed"),
            anomaly=len(anomaly_samples),
            executed=len(non_warmup),
            success_rate=success_rate,
            ttft_avg_ms=ttft_avg,
            total_avg_ms=total_avg,
            tps_avg=_avg([s.tps for s in valid_samples]),
            ttft_with_anomaly_avg_ms=_avg([s.ttft_ms for s in success_samples]),
            total_with_anomaly_avg_ms=_avg([s.total_ms for s in success_samples]),
            be_matched_count=sum(1 for s in non_warmup if s.be_matched),
            be_frt_avg_ms=be_frt_avg,
            be_use_time_avg_ms=be_use_time_avg,
            be_tokens_out_avg=_avg([s.be_tokens_out for s in matched_valid]),
            cross_ttft_diff_ms=(round(ttft_avg - be_frt_avg, 2) if ttft_avg is not None and be_frt_avg is not None else None),
            cross_total_diff_ms=(round(total_avg - be_use_time_avg, 2) if total_avg is not None and be_use_time_avg is not None else None),
        )

    # ─────────────────────── 自动结论 ───────────────────────

    @staticmethod
    def _build_conclusions(models: dict[str, ReportModelGroupSchema]) -> list[ReportConclusionSchema]:
        """规则结论：仅阈值事实，无评价性/建议性措辞。"""
        conclusions: list[ReportConclusionSchema] = []
        threshold_s = CONCLUSION_DIFF_THRESHOLD_MS / 1000
        for model in sorted(models):
            group = models[model]
            if group.baseline is None:
                conclusions.append(ReportConclusionSchema(content=f"模型 {model}：无基准数据，未进行基准对比。"))
            for combo in group.combos:
                label = f"模型 {model} / {combo.channel_name}（#{combo.channel_id}，{combo.platform_name}）"
                baseline = group.baseline
                if baseline is not None:
                    if combo.ttft_vs_baseline_ms is not None and combo.ttft_vs_baseline_ms >= CONCLUSION_DIFF_THRESHOLD_MS:
                        conclusions.append(
                            ReportConclusionSchema(
                                content=(
                                    f"{label} 客户端首 Token 时延均值 {combo.ttft_avg_ms:.0f} ms，"
                                    f"高于基准 {baseline.ttft_avg_ms:.0f} ms 达 {combo.ttft_vs_baseline_ms / 1000:.1f} 秒"
                                    f"（≥ {threshold_s:.0f} 秒）。"
                                )
                            )
                        )
                    if combo.total_vs_baseline_ms is not None and combo.total_vs_baseline_ms >= CONCLUSION_DIFF_THRESHOLD_MS:
                        conclusions.append(
                            ReportConclusionSchema(
                                content=(
                                    f"{label} 客户端总耗时均值 {combo.total_avg_ms:.0f} ms，"
                                    f"高于基准 {baseline.total_avg_ms:.0f} ms 达 {combo.total_vs_baseline_ms / 1000:.1f} 秒"
                                    f"（≥ {threshold_s:.0f} 秒）。"
                                )
                            )
                        )
                if combo.anomaly > 0:
                    conclusions.append(ReportConclusionSchema(content=f"{label} 出现异常样本 {combo.anomaly} 条（延迟超过异常阈值）。"))
                if combo.success_rate is not None and combo.success_rate < 1:
                    pct = combo.success_rate * 100
                    conclusions.append(ReportConclusionSchema(content=f"{label} 成功率 {pct:.0f}%（{combo.success}/{combo.planned or combo.executed}）。"))
        return conclusions

    # ─────────────────────── HTML 导出 ───────────────────────

    async def export_html(self, id: int) -> str:
        """渲染自包含 HTML（数据冻结于导出时刻）。"""
        view = await self.view(id)
        template = _env.get_template("export_report.html.j2")
        return template.render(view=view.model_dump(mode="json"))
