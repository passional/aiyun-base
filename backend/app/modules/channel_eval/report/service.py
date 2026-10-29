"""渠道评估 - 对比报告服务（spec: channel-eval/compare-report）

- 报告 = 选择 + 备注，不复制测量数据；Web 视图打开时基于所选运行的持久化数据实时渲染
- 基准校验：基准运行须为基准渠道产生的成功运行
- 分组按「规范模型」：渠道模型串经**已确认**的模型映射解析后归组；未确认归属的独立成组并标记，
  绝不与其它模型合并、也绝不隐藏——静默丢弃正是这个模块最伤人的失败方式
- 自动结论仅陈述阈值事实（高于基准幅度 / 异常样本 / 成功率非100%），无评价性措辞
- 可比性提醒仅陈述运行参数差异事实，同样不含建议性措辞
- 口径说明由视图数据生成，其中模型映射一段列出本次实际生效的映射关系
- HTML 导出：Jinja2 自包含渲染，数据 JSON 内嵌、零外部依赖、离线可开（导出即冻结）
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.channel_eval.model_map.normalize import build_suggestions
from app.modules.channel_eval.model_map.service import AliasTarget, resolve_alias_map
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
    ReportAlignmentSchema,
    ReportAnomalyItemSchema,
    ReportComboViewSchema,
    ReportComparableNoteSchema,
    ReportConclusionSchema,
    ReportMappingSchema,
    ReportModelGroupSchema,
    ReportRunConfigSchema,
    ReportRunInfoSchema,
    ReportSampleViewSchema,
)

# 自动结论阈值：客户端/后台指标高于基准该幅度即陈述事实（spec scenario：高于基准 2 秒以上）
CONCLUSION_DIFF_THRESHOLD_MS = 2000

# 对齐状态
ALIGNED = "aligned"
UNMAPPED = "unmapped"
SINGLETON = "singleton"

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _json_safe(value: Any) -> Markup:
    """把视图数据内嵌为 JSON：保留非 ASCII，同时转义 HTML 危险字符。

    不用 Jinja 自带的 ``tojson``——它会把中文转成 ``\\uXXXX``，浏览器解析没问题，
    但导出件是要归档、要被人直接打开看的文件，满屏转义码是不可接受的。
    转义 ``<`` 是必需的：防止数据里的 ``</script>`` 提前闭合脚本块。
    """
    text = json.dumps(value, ensure_ascii=False)
    for token, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("'", "\\u0027")):
        text = text.replace(token, escaped)
    return Markup(text)


_env.filters["json_safe"] = _json_safe


def _avg(values: list[float | int | None]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


def _run_label(config: ReportRunConfigSchema) -> str:
    return f"运行 #{config.run_id}（{config.platform_name or '未知平台'}）"


def _num(value: Any) -> str:
    """参数值呈现：整数不带小数点，避免「间隔 2.0 秒」这种原始浮点感。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value}"


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
                raise CustomException(msg=f"测试运行 {run_id} 不存在", code=400)
        if data.baseline_run_id is not None:
            run = await self.db.get(ChannelEvalRunModel, data.baseline_run_id)
            if run is None:
                raise CustomException(msg="基准运行不存在", code=400)
            platform = await self.db.get(ChannelEvalPlatformModel, run.platform_id)
            if platform is None or not platform.is_baseline:
                raise CustomException(msg="基准运行须来自基准渠道", code=400)
            if run.status != "success":
                raise CustomException(msg="基准运行须为成功完成的运行", code=400)

    # ─────────────────────── 视图聚合 ───────────────────────

    async def view(self, id: int) -> ChannelEvalReportViewSchema:
        report = await self.crud.get_or_404(id=id, msg="报告不存在")
        return await self._build_view(ChannelEvalReportOutSchema.model_validate(report))

    async def _build_view(self, report: ChannelEvalReportOutSchema) -> ChannelEvalReportViewSchema:
        """按规范模型分组并列各运行组合与基准；客户端 vs 后台交叉对比；无数据"—"、无基准"无基准数据"。"""
        all_run_ids = list(report.run_ids or [])
        if report.baseline_run_id is not None and report.baseline_run_id not in all_run_ids:
            all_run_ids.append(report.baseline_run_id)

        runs_info: list[ReportRunInfoSchema] = []
        baseline_run_info: ReportRunInfoSchema | None = None
        # run_id -> (combo聚合列表, 异常条目列表)
        combo_views_by_run: dict[int, list[ReportComboViewSchema]] = {}
        anomaly_items: list[ReportAnomalyItemSchema] = []
        run_configs: list[ReportRunConfigSchema] = []

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
            run_configs.append(self._build_run_config(info, run, combos, samples, threshold_ms))

        # ── 按规范模型分组（渠道模型串 → 已确认别名 → 规范模型）──
        alias_map = await resolve_alias_map(self.db)
        observed_strings = {combo_view.model for views in combo_views_by_run.values() for combo_view in views}
        pending = {alias for group in build_suggestions(sorted(observed_strings), set(alias_map)) for alias in group["aliases"]}
        suggestions_by_alias: dict[str, list[str]] = {}
        for group in build_suggestions(sorted(observed_strings), set(alias_map)):
            for alias in group["aliases"]:
                suggestions_by_alias[alias] = group["aliases"]

        baseline_combos_by_key: dict[str, ReportComboViewSchema] = {}
        if baseline_run_info is not None:
            for combo_view in combo_views_by_run.get(baseline_run_info.id, []):
                baseline_combos_by_key[self._group_key(combo_view.model, alias_map)[0]] = combo_view

        models: dict[str, ReportModelGroupSchema] = {}
        for run_id in report.run_ids or []:
            for combo_view in combo_views_by_run.get(run_id, []):
                key, display_name, family = self._group_key(combo_view.model, alias_map)
                group = models.get(key)
                if group is None:
                    group = ReportModelGroupSchema(
                        model=display_name,
                        model_key=key,
                        raw_models=[],
                        family=family,
                        alignment=ALIGNED if key.startswith("c") else (UNMAPPED if combo_view.model in pending else SINGLETON),
                        has_baseline=key in baseline_combos_by_key,
                        combos=[],
                    )
                    models[key] = group
                if combo_view.model not in group.raw_models:
                    group.raw_models.append(combo_view.model)
                baseline = baseline_combos_by_key.get(key)
                if baseline is not None:
                    group.baseline = baseline
                    combo_view.ttft_vs_baseline_ms = round(combo_view.ttft_avg_ms - baseline.ttft_avg_ms, 2) if combo_view.ttft_avg_ms is not None and baseline.ttft_avg_ms is not None else None
                    combo_view.total_vs_baseline_ms = round(combo_view.total_avg_ms - baseline.total_avg_ms, 2) if combo_view.total_avg_ms is not None and baseline.total_avg_ms is not None else None
                group.combos.append(combo_view)

        for group in models.values():
            if group.baseline is None:
                group.baseline = baseline_combos_by_key.get(group.model_key)
            # 基准那一侧的串也要算进组内串，否则「两侧命名不同」标注不出来——
            # 而基准恰恰是最常见的那一侧
            if group.baseline is not None and group.baseline.model not in group.raw_models:
                group.raw_models.append(group.baseline.model)
            group.raw_models.sort()
            group.alignment_note = self._alignment_note(group, suggestions_by_alias)

        ordered_groups = sorted(models.values(), key=lambda item: item.model)
        alignment = self._build_alignment(ordered_groups, alias_map, observed_strings)
        conclusions = self._build_conclusions(ordered_groups)
        return ChannelEvalReportViewSchema(
            report=report,
            runs=runs_info,
            baseline_run=baseline_run_info,
            models=ordered_groups,
            anomalies=anomaly_items,
            conclusions=conclusions,
            alignment=alignment,
            comparable_notes=self._build_comparable_notes(run_configs),
            run_configs=run_configs,
            caliber_notes=self._build_caliber_notes(alignment),
            generated_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _group_key(model: str, alias_map: dict[str, AliasTarget]) -> tuple[str, str, str | None]:
        """渠道模型串 → (分组键, 显示名, 系列)。未确认归属时以串自身为键。"""
        target = alias_map.get(model)
        if target is not None:
            return f"c{target.canonical_id}", target.display_name, target.family
        return f"raw:{model}", model, None

    @staticmethod
    def _alignment_note(group: ReportModelGroupSchema, suggestions_by_alias: dict[str, list[str]]) -> str | None:
        """组内串不同时标注命名差异；未确认归属时指出疑似同族的串。"""
        if len(group.raw_models) > 1:
            return "组内渠道模型串不同：" + " / ".join(group.raw_models)
        if group.alignment == UNMAPPED and group.raw_models:
            model = group.raw_models[0]
            siblings = [item for item in suggestions_by_alias.get(model, []) if item != model]
            if siblings:
                return f"「{model}」疑似与 " + "、".join(f"「{item}」" for item in siblings) + " 同族，尚未确认归属"
            return f"「{model}」尚未确认归属"
        return None

    def _build_alignment(
        self,
        groups: list[ReportModelGroupSchema],
        alias_map: dict[str, AliasTarget],
        observed_strings: set[str],
    ) -> ReportAlignmentSchema:
        # 只数「有疑似同族但未确认」的组。singleton（只在一个渠道出现、根本没有同族候选）
        # 不算未对齐——它没有任何待办，把它算进去会让一份含 30 个独有模型的报告喊「30 个未确认归属」。
        unmapped = [group for group in groups if group.alignment == UNMAPPED]
        return ReportAlignmentSchema(
            group_count=len(groups),
            unmapped_count=len(unmapped),
            # 只列本次报告真的用到的映射，别把整张别名表倒出来
            mappings=[ReportMappingSchema(alias=alias, canonical=target.display_name) for alias, target in sorted(alias_map.items()) if alias in observed_strings],
            notes=[group.alignment_note for group in unmapped if group.alignment_note],
        )

    @staticmethod
    def _build_caliber_notes(alignment: ReportAlignmentSchema) -> list[str]:
        """指标口径说明：固定口径 + 本次实际生效的映射关系。"""
        notes = [
            "客户端实测：评估系统以真实流式请求（stream + include_usage）向平台发起测得；首 Token 时延 = 发出请求到收到第一个内容分片；总耗时 = 至流式结束；速率 = 输出 tokens ÷（总耗时 − 首 Token 时延）。",
            "后台日志：平台侧 new-api 记录的每请求数据（other.frt 首字毫秒 / use_time 秒），运行结束后按请求标识 1:1 配对写回；未配对样本标注「后台无记录」，不凭空填值。",
            "聚合口径：任何均值基于剔除热身与异常（延迟超过异常阈值，默认 30 秒）后的成功样本；并列给出含异常参考均值；失败样本只计入成功率。",
            "成功率 = 成功样本数 ÷ 计划请求次数（热身不计入统计）。",
            "基准对比：基准数据取管理员选定的基准渠道运行快照；基准未覆盖的模型显示「无基准数据」。",
            "模型映射：按规范模型分组，渠道模型串经管理员确认的别名归属到规范模型。",
            "自动结论仅陈述阈值性事实（客户端指标高于基准 2 秒及以上、出现异常样本、成功率非 100%），不含评价性措辞；可比性提醒同样只陈述运行参数差异事实。",
            "本文件为导出时刻的数据快照，之后运行数据、基准选择或模型映射的变化不影响本文件内容。",
        ]
        if alignment.mappings:
            mapping_text = "；".join(f"{item.alias} → {item.canonical}" for item in alignment.mappings)
            notes[5] = f"模型映射：按规范模型分组。本次实际生效的映射关系为 —— {mapping_text}。渠道前缀与分隔符只是各渠道的命名习惯。"
        else:
            notes[5] = "模型映射：按规范模型分组。本次报告未用到任何已确认的别名，各渠道模型串按原样分组。"
        if alignment.unmapped_count:
            notes.append(f"疑似同族未确认：本次有 {alignment.unmapped_count} 个模型存在疑似同族的渠道模型串，尚未确认归属。它们已独立成行并标记，未与其它模型合并、也未隐藏。")
        return notes

    @staticmethod
    def _build_run_config(
        info: ReportRunInfoSchema,
        run: ChannelEvalRunModel,
        combos: list[ChannelEvalRunComboModel],
        samples: list[ChannelEvalSampleModel],
        threshold_ms: int,
    ) -> ReportRunConfigSchema:
        """运行参数快照与执行结果计数（热身只有开关，无次数——不得虚构）。"""
        params = run.params_json or {}
        non_warmup = [sample for sample in samples if not sample.is_warmup]
        return ReportRunConfigSchema(
            run_id=run.id,
            platform_name=info.platform_name,
            is_baseline=info.is_baseline,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            combo_count=len(combos),
            request_count=params.get("request_count"),
            warmup=params.get("warmup"),
            interval_seconds=params.get("interval_seconds"),
            timeout_seconds=params.get("timeout_seconds"),
            anomaly_threshold_seconds=params.get("anomaly_threshold_seconds"),
            prompt=params.get("prompt"),
            success=sum(1 for sample in non_warmup if sample.status == "success"),
            failed=sum(1 for sample in non_warmup if sample.status == "failed"),
            anomaly=sum(1 for sample in non_warmup if sample.status == "success" and is_anomaly(sample, threshold_ms)),
        )

    @staticmethod
    def _build_comparable_notes(configs: list[ReportRunConfigSchema]) -> list[ReportComparableNoteSchema]:
        """可比性提醒：只陈述参数差异事实，不写"建议统一…"这类建议性措辞。"""
        if len(configs) < 2:
            return []
        notes: list[ReportComparableNoteSchema] = []
        # 影响可比性的参数：值不同即警示
        warn_fields: list[tuple[str, str, Callable[[Any], str]]] = [
            ("warmup", "热身", lambda value: "开启" if value else "关闭"),
            ("prompt", "请求模板", lambda value: f"「{value}」"),
            ("request_count", "每组合正式次数", _num),
            ("anomaly_threshold_seconds", "异常阈值(秒)", _num),
            ("interval_seconds", "请求间隔(秒)", _num),
            ("timeout_seconds", "单请求超时(秒)", _num),
        ]
        for attr, name, formatter in warn_fields:
            if len({getattr(config, attr) for config in configs}) < 2:
                continue
            detail = "，".join(f"{_run_label(config)}为{formatter(getattr(config, attr))}" for config in configs)
            notes.append(ReportComparableNoteSchema(level="warning", content=f"{name}不同：{detail}。"))

        if len({(config.started_at, config.finished_at) for config in configs}) > 1:
            detail = "，".join(f"{_run_label(config)} {config.started_at or '—'} 至 {config.finished_at or '—'}" for config in configs)
            notes.append(ReportComparableNoteSchema(level="info", content=f"执行时间窗不同：{detail}。"))
        if len({config.combo_count for config in configs}) > 1:
            detail = "，".join(f"{_run_label(config)}为 {config.combo_count} 个组合" for config in configs)
            notes.append(ReportComparableNoteSchema(level="info", content=f"覆盖的组合数不同：{detail}。"))
        return notes

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
            samples=[
                ReportSampleViewSchema(
                    seq=sample.seq,
                    is_warmup=sample.is_warmup,
                    status=sample.status,
                    ttft_ms=sample.ttft_ms,
                    total_ms=sample.total_ms,
                    tps=sample.tps,
                    tokens_out=sample.tokens_out,
                    be_matched=sample.be_matched,
                    be_frt_ms=sample.be_frt_ms,
                    be_use_time_ms=sample.be_use_time_ms,
                    error=sample.error,
                )
                for sample in samples
            ],
        )

    # ─────────────────────── 自动结论 ───────────────────────

    @staticmethod
    def _build_conclusions(groups: list[ReportModelGroupSchema]) -> list[ReportConclusionSchema]:
        """规则结论：仅阈值事实，无评价性/建议性措辞。"""
        conclusions: list[ReportConclusionSchema] = []
        threshold_s = CONCLUSION_DIFF_THRESHOLD_MS / 1000
        for group in groups:
            model = group.model
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
