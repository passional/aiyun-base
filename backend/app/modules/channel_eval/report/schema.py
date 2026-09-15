"""渠道评估 - 对比报告 Pydantic 模型（spec: channel-eval/compare-report）"""

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr


class ChannelEvalReportCreateSchema(BaseModel):
    """报告创建参数（报告=选择+备注，不复制测量数据）"""

    name: str = Field(..., min_length=1, max_length=100, description="报告名称")
    run_ids: list[int] = Field(..., description="所选测试运行ID列表(一至多次，可跨平台)")
    baseline_run_id: int | None = Field(default=None, ge=1, description="基准运行ID(须为基准渠道的成功运行)")
    note: str | None = Field(default=None, max_length=4000, description="管理员备注")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("报告名称不能为空")
        return value.strip()

    @field_validator("run_ids")
    @classmethod
    def _validate_run_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("至少选择一次测试运行")
        return list(dict.fromkeys(value))


class ChannelEvalReportUpdateSchema(ChannelEvalReportCreateSchema):
    """报告编辑参数"""


class ChannelEvalReportOutSchema(BaseSchema):
    """报告响应模型"""

    name: str = Field(description="报告名称")
    run_ids: list[int] = Field(default_factory=list, description="所选测试运行ID列表")
    baseline_run_id: int | None = Field(default=None, description="基准运行ID")
    note: str | None = Field(default=None, description="管理员备注")
    status: int = Field(default=0, description="状态(0:正常 1:停用)")


class ChannelEvalReportQueryParam(BaseQueryParam):
    """报告查询参数"""

    name: str | None = Field(default=None, max_length=100, description="报告名称", json_schema_extra={"q": "like"})


# ─────────────────────── 视图聚合模型（GET /reports/{id}/view） ───────────────────────


class ReportRunInfoSchema(BaseModel):
    """报告中运行的标识信息"""

    id: int = Field(description="运行ID")
    platform_id: int = Field(description="平台ID")
    platform_name: str | None = Field(default=None, description="平台名称")
    status: str = Field(description="运行状态")
    is_baseline: bool = Field(default=False, description="是否来自基准渠道")
    created_time: DateTimeStr | None = Field(default=None, description="创建时间")
    combo_count: int = Field(default=0, description="组合数")


class ReportComboViewSchema(BaseModel):
    """对比视图中的单组合聚合（客户端实测 + 后台日志 + 交叉对比 + 基准差值）"""

    run_id: int = Field(description="运行ID")
    platform_id: int = Field(description="平台ID")
    platform_name: str | None = Field(default=None, description="平台名称")
    channel_id: int = Field(description="渠道编号")
    channel_name: str = Field(description="渠道名称快照")
    model: str = Field(description="模型ID")
    is_baseline: bool = Field(default=False, description="是否基准运行数据")
    # 计数
    planned: int = Field(default=0, description="计划请求次数")
    success: int = Field(default=0, description="成功样本数")
    failed: int = Field(default=0, description="失败样本数")
    anomaly: int = Field(default=0, description="异常样本数")
    executed: int = Field(default=0, description="实际执行次数(非热身)")
    success_rate: float | None = Field(default=None, description="成功率(0-1)")
    # 客户端实测聚合（剔除热身/异常/失败）
    ttft_avg_ms: float | None = Field(default=None, description="客户端首Token时延均值(ms)")
    total_avg_ms: float | None = Field(default=None, description="客户端总耗时均值(ms)")
    tps_avg: float | None = Field(default=None, description="生成速率均值(tokens/s)")
    ttft_with_anomaly_avg_ms: float | None = Field(default=None, description="客户端首Token时延均值(含异常)")
    total_with_anomaly_avg_ms: float | None = Field(default=None, description="客户端总耗时均值(含异常)")
    # 后台日志聚合（配对样本）
    be_matched_count: int = Field(default=0, description="配对到后台日志的样本数")
    be_frt_avg_ms: float | None = Field(default=None, description="后台首字时间均值(ms)")
    be_use_time_avg_ms: float | None = Field(default=None, description="后台总耗时均值(ms)")
    be_tokens_out_avg: float | None = Field(default=None, description="后台输出tokens均值")
    # 交叉对比（客户端 − 后台）
    cross_ttft_diff_ms: float | None = Field(default=None, description="客户端TTFT均值 − 后台首字均值(ms)")
    cross_total_diff_ms: float | None = Field(default=None, description="客户端总耗时均值 − 后台总耗时均值(ms)")
    # 与基准差值（同模型；无基准数据为 None）
    ttft_vs_baseline_ms: float | None = Field(default=None, description="客户端TTFT均值 − 基准均值(ms)")
    total_vs_baseline_ms: float | None = Field(default=None, description="客户端总耗时均值 − 基准均值(ms)")


class ReportModelGroupSchema(BaseModel):
    """按模型分组的对比视图"""

    model: str = Field(description="模型ID")
    baseline: ReportComboViewSchema | None = Field(default=None, description="基准运行同模型组合(无则None)")
    has_baseline: bool = Field(default=False, description="是否存在基准数据")
    combos: list[ReportComboViewSchema] = Field(default_factory=list, description="非基准组合列表")


class ReportAnomalyItemSchema(BaseModel):
    """异常清单条目（超阈值样本与失败样本）"""

    kind: str = Field(description="条目类型(anomaly:延迟超阈值 failed:请求失败)")
    run_id: int = Field(description="运行ID")
    platform_name: str | None = Field(default=None, description="平台名称")
    channel_id: int = Field(description="渠道编号")
    channel_name: str = Field(description="渠道名称快照")
    model: str = Field(description="模型ID")
    seq: int = Field(description="组合内序号(热身为0)")
    is_warmup: bool = Field(default=False, description="是否热身")
    status: str = Field(description="样本状态")
    ttft_ms: int | None = Field(default=None, description="首Token时延(ms)")
    total_ms: int | None = Field(default=None, description="总耗时(ms)")
    error: str | None = Field(default=None, description="失败错误信息")
    be_matched: bool = Field(default=False, description="是否配对到后台日志")


class ReportConclusionSchema(BaseModel):
    """自动结论（仅阈值事实，无评价性措辞）"""

    content: str = Field(description="事实陈述")


class ChannelEvalReportViewSchema(BaseModel):
    """报告视图聚合（Web 视图与 HTML 导出共用）"""

    report: ChannelEvalReportOutSchema = Field(description="报告信息")
    runs: list[ReportRunInfoSchema] = Field(default_factory=list, description="所选运行列表")
    baseline_run: ReportRunInfoSchema | None = Field(default=None, description="基准运行")
    models: list[ReportModelGroupSchema] = Field(default_factory=list, description="按模型分组的对比数据")
    anomalies: list[ReportAnomalyItemSchema] = Field(default_factory=list, description="异常清单")
    conclusions: list[ReportConclusionSchema] = Field(default_factory=list, description="自动结论")
    generated_at: str = Field(description="视图生成时间(ISO格式)")
