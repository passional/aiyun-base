"""渠道评估 - 测试运行 Pydantic 模型"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.base_schema import BaseQueryParam, BaseSchema
from app.core.validator import DateTimeStr

RunStatusType = Literal["pending", "running", "cancelling", "success", "failed", "cancelled"]


class ChannelEvalRunPlatformOptionSchema(BaseModel):
    """测试运行可选平台：只暴露选择所需字段。"""

    id: int
    name: str
    type: str
    is_baseline: bool
    status: int

# 运行参数默认值（spec: test-run / 测试运行创建）
DEFAULT_REQUEST_COUNT = 3
DEFAULT_WARMUP = True
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_PROMPT = "请用一段话介绍你自己，并数到 5。"
DEFAULT_ANOMALY_THRESHOLD_SECONDS = 30


class ChannelEvalRunComboInputSchema(BaseModel):
    """「渠道 × 模型」组合入参（渠道编号 + 名称快照 + 模型ID）"""

    channel_id: int = Field(..., ge=0, description="渠道编号(基准渠道隐式渠道为0)")
    channel_name: str = Field(..., min_length=1, max_length=255, description="渠道名称快照")
    model: str = Field(..., min_length=1, max_length=255, description="模型ID")


class ChannelEvalRunCreateSchema(BaseModel):
    """运行创建参数（组合清单 + 参数快照随运行持久化）"""

    platform_id: int = Field(..., ge=1, description="平台ID")
    combos: list[ChannelEvalRunComboInputSchema] = Field(..., description="「渠道 × 模型」组合清单（空列表由服务层拒绝并给出中文提示）")
    request_count: int = Field(default=DEFAULT_REQUEST_COUNT, ge=1, le=50, description="每组合请求次数")
    warmup: bool = Field(default=DEFAULT_WARMUP, description="是否热身(热身不计入统计)")
    interval_seconds: float = Field(default=DEFAULT_INTERVAL_SECONDS, ge=0, le=60, description="同组合请求间隔(秒)")
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=5, le=1800, description="单请求超时(秒)")
    prompt: str = Field(default=DEFAULT_PROMPT, min_length=1, max_length=4000, description="测试输入(prompt)")
    anomaly_threshold_seconds: int = Field(default=DEFAULT_ANOMALY_THRESHOLD_SECONDS, ge=1, le=600, description="异常阈值(秒，ttft/total超过即异常)")

    @model_validator(mode="after")
    def _validate_combos(self) -> "ChannelEvalRunCreateSchema":
        """组合为空禁止创建；同渠道同模型组合去重。"""
        seen: set[tuple[int, str]] = set()
        for combo in self.combos:
            key = (combo.channel_id, combo.model)
            if key in seen:
                raise ValueError(f"组合重复：渠道 #{combo.channel_id} × {combo.model}")
            seen.add(key)
        return self


class ChannelEvalSampleOutSchema(BaseModel):
    """样本响应模型（客户端实测 + 配对后台字段）"""

    model_config = {"from_attributes": True}

    id: int | None = Field(default=None, description="主键ID")
    combo_id: int = Field(description="组合ID")
    seq: int = Field(description="组合内序号(热身为0)")
    is_warmup: bool = Field(description="是否热身")
    request_id: str | None = Field(default=None, description="平台返回的请求标识")
    ttft_ms: int | None = Field(default=None, description="首Token时延(毫秒)")
    total_ms: int | None = Field(default=None, description="总耗时(毫秒)")
    tokens_in: int | None = Field(default=None, description="输入tokens")
    tokens_out: int | None = Field(default=None, description="输出tokens")
    tps: float | None = Field(default=None, description="生成速率(tokens/s)")
    status: str = Field(description="样本状态(success/failed)")
    error: str | None = Field(default=None, description="失败错误信息")
    started_at: DateTimeStr | None = Field(default=None, description="请求发出时间")
    be_matched: bool = Field(default=False, description="是否配对到后台日志")
    be_frt_ms: int | None = Field(default=None, description="后台首字时间(毫秒)")
    be_use_time_ms: int | None = Field(default=None, description="后台总耗时(毫秒)")
    be_tokens_in: int | None = Field(default=None, description="后台输入tokens")
    be_tokens_out: int | None = Field(default=None, description="后台输出tokens")
    be_cost: int | None = Field(default=None, description="后台消耗(quota)")
    be_channel_id: int | None = Field(default=None, description="后台记录的实际渠道编号")


class ChannelEvalComboAggregateSchema(BaseModel):
    """组合聚合结果（剔除热身/异常/失败样本的均值 + 含异常参考均值 + 成功率）"""

    combo_id: int = Field(description="组合ID")
    channel_id: int = Field(description="渠道编号")
    channel_name: str = Field(description="渠道名称快照")
    model: str = Field(description="模型ID")
    status: str = Field(description="组合状态")
    planned: int = Field(description="计划请求次数")
    success: int = Field(description="成功样本数")
    failed: int = Field(description="失败样本数")
    anomaly: int = Field(description="异常样本数")
    error: str | None = Field(default=None, description="组合级失败原因")
    executed: int = Field(default=0, description="实际执行次数(非热身)")
    success_rate: float | None = Field(default=None, description="成功率(0-1，未执行为None)")
    ttft_avg_ms: float | None = Field(default=None, description="首Token时延均值(剔除热身/异常/失败)")
    total_avg_ms: float | None = Field(default=None, description="总耗时均值(剔除热身/异常/失败)")
    tps_avg: float | None = Field(default=None, description="生成速率均值(剔除热身/异常/失败)")
    ttft_with_anomaly_avg_ms: float | None = Field(default=None, description="首Token时延均值(含异常)")
    total_with_anomaly_avg_ms: float | None = Field(default=None, description="总耗时均值(含异常)")


class ChannelEvalRunOutSchema(BaseSchema):
    """运行响应模型（列表用，详情见 ChannelEvalRunDetailSchema）"""

    platform_id: int = Field(description="平台ID")
    platform_name: str | None = Field(default=None, description="平台名称")
    status: str = Field(description="状态")
    params: dict[str, Any] | None = Field(default=None, description="参数快照")
    combo_count: int = Field(default=0, description="组合数")
    started_at: DateTimeStr | None = Field(default=None, description="执行开始时间")
    finished_at: DateTimeStr | None = Field(default=None, description="执行结束时间")
    error: str | None = Field(default=None, description="失败/取消原因")


class ChannelEvalRunDetailSchema(ChannelEvalRunOutSchema):
    """运行详情：参数快照 + 组合清单（含聚合） + 逐样本明细"""

    combos: list[ChannelEvalComboAggregateSchema] = Field(default_factory=list, description="组合聚合列表")
    samples: list[ChannelEvalSampleOutSchema] = Field(default_factory=list, description="样本明细(含后台配对字段)")


class ChannelEvalRunQueryParam(BaseQueryParam):
    """运行查询参数"""

    platform_id: int | None = Field(default=None, ge=1, description="平台ID", json_schema_extra={"q": "eq"})
    status: str | None = Field(default=None, max_length=16, description="状态", json_schema_extra={"q": "eq"})


class ChannelEvalRunProgressSchema(BaseModel):
    """运行进度（Redis 存储 + SSE 推送载荷）"""

    run_id: int = Field(description="运行ID")
    status: str = Field(description="运行状态")
    total_combos: int = Field(default=0, description="总组合数")
    completed_combos: int = Field(default=0, description="已完成组合数")
    current_combo: str | None = Field(default=None, description="当前执行的组合描述")
    current_sample: int = Field(default=0, description="当前组合内请求序号")
    message: str | None = Field(default=None, description="附加信息")
