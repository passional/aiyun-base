"""new-api 后台日志字段解析与降级

口径（设计 D2，已对 new-api 源码核实）：
- ``use_time``：秒（int/float）→ 转毫秒存 ``be_use_time_ms``
- ``other.frt``：毫秒（首字时间）→ ``be_frt_ms``；``other`` 为 JSON 字符串
- ``request_id``：请求标识，用于样本 1:1 配对
- tokens：``prompt_tokens`` / ``completion_tokens``
- 消耗：``quota``
- 渠道编号：``channel_id``（兼容 ``channel`` 字段名）

降级：任一字段缺失时对应解析结果置 ``None``（后台首字显示"—"、配对退化为
"后台无记录"标注），不凭空填值。
"""

import json
from typing import Any


def _parse_other(record: dict[str, Any]) -> dict[str, Any]:
    """解析 other 字段（JSON 字符串或已解构的 dict）。"""
    other = record.get("other")
    if isinstance(other, dict):
        return other
    if isinstance(other, str) and other.strip():
        try:
            parsed = json.loads(other)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _to_int(value: Any) -> int | None:
    """宽容整数转换（兼容 str/int/float/None）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def parse_log_record(record: dict[str, Any]) -> dict[str, Any]:
    """单条后台日志 → 配对所需字段（缺失字段置 None）。

    返回键：request_id / frt_ms / use_time_ms / tokens_in / tokens_out /
    cost / channel_id / model_name / created_at
    """
    other = _parse_other(record)
    # request_id 存在性探测：顶层或 other 内（new-api 版本差异），缺失置 None
    request_id = record.get("request_id") or other.get("request_id") or None
    # use_time 秒 → 毫秒；缺失置 None
    use_time = _to_int(record.get("use_time"))
    use_time_ms = use_time * 1000 if use_time is not None else None
    # other.frt 毫秒（首字）；缺失置 None（降级：后台首字显示"—"）
    frt_ms = _to_int(other.get("frt"))
    channel_id = _to_int(record.get("channel_id"))
    if channel_id is None:
        channel_id = _to_int(record.get("channel"))
    return {
        "request_id": str(request_id) if request_id else None,
        "frt_ms": frt_ms,
        "use_time_ms": use_time_ms,
        "tokens_in": _to_int(record.get("prompt_tokens")),
        "tokens_out": _to_int(record.get("completion_tokens")),
        "cost": _to_int(record.get("quota")),
        "channel_id": channel_id,
        "model_name": record.get("model_name"),
        "created_at": _to_int(record.get("created_at")),
    }
