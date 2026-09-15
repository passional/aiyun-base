"""组合聚合口径（spec: test-run / 异常口径）

任何聚合统计基于**剔除热身与异常后的成功样本**计算，展示时并列给出
**含异常参考均值**；失败样本只计成功率。无有效数据时均值为 None（展示"—"）。
"""

from typing import Any

from app.modules.channel_eval.run.model import ChannelEvalRunComboModel, ChannelEvalSampleModel
from app.modules.channel_eval.run.schema import (
    ChannelEvalComboAggregateSchema,
    ChannelEvalSampleOutSchema,
)


def is_anomaly(sample: ChannelEvalSampleModel, threshold_ms: int) -> bool:
    """异常口径：成功样本的 ttft_ms 或 total_ms 超过阈值（默认 30s）。"""
    if sample.status != "success":
        return False
    if sample.ttft_ms is not None and sample.ttft_ms > threshold_ms:
        return True
    if sample.total_ms is not None and sample.total_ms > threshold_ms:
        return True
    return False


def _avg(values: list[int | float | None]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


def aggregate_combo(
    combo: ChannelEvalRunComboModel,
    samples: list[ChannelEvalSampleModel],
    threshold_ms: int,
) -> ChannelEvalComboAggregateSchema:
    """单组合聚合：剔除热身/异常/失败的均值 + 含异常参考均值 + 成功率。"""
    non_warmup = [s for s in samples if not s.is_warmup]
    success_samples = [s for s in non_warmup if s.status == "success"]
    anomaly_samples = [s for s in success_samples if is_anomaly(s, threshold_ms)]
    valid_samples = [s for s in success_samples if not is_anomaly(s, threshold_ms)]
    executed = len(non_warmup)
    planned = combo.planned or 0
    success_rate: float | None = None
    if planned > 0:
        success_rate = round(len(success_samples) / planned, 4)
    elif executed > 0:
        success_rate = round(len(success_samples) / executed, 4)
    return ChannelEvalComboAggregateSchema(
        combo_id=combo.id,
        channel_id=combo.channel_id,
        channel_name=combo.channel_name,
        model=combo.model,
        status=combo.status,
        planned=planned,
        success=len(success_samples),
        failed=sum(1 for s in non_warmup if s.status == "failed"),
        anomaly=len(anomaly_samples),
        error=combo.error,
        executed=executed,
        success_rate=success_rate,
        ttft_avg_ms=_avg([s.ttft_ms for s in valid_samples]),
        total_avg_ms=_avg([s.total_ms for s in valid_samples]),
        tps_avg=_avg([s.tps for s in valid_samples]),
        ttft_with_anomaly_avg_ms=_avg([s.ttft_ms for s in success_samples]),
        total_with_anomaly_avg_ms=_avg([s.total_ms for s in success_samples]),
    )


def combo_samples_by_id(samples: list[ChannelEvalSampleModel]) -> dict[int, list[ChannelEvalSampleModel]]:
    """样本按组合ID分组。"""
    grouped: dict[int, list[ChannelEvalSampleModel]] = {}
    for sample in samples:
        grouped.setdefault(sample.combo_id, []).append(sample)
    return grouped


def threshold_ms_from_params(params: dict[str, Any] | None) -> int:
    """运行参数 → 异常阈值（毫秒）。"""
    params = params or {}
    return int(params.get("anomaly_threshold_seconds") or 30) * 1000


def samples_to_out(samples: list[ChannelEvalSampleModel]) -> list[ChannelEvalSampleOutSchema]:
    return [ChannelEvalSampleOutSchema.model_validate(s) for s in samples]
