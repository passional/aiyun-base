"""渠道模型串的归一化与「疑似同族」建议（只产建议，不自动生效）

归一化把命名习惯差异抹平后作为分组键；差异类型用于向管理员说明「凭什么说它们是同族」。
只有「跳过该变换就会把这一组拆开」的变换才算依据——否则会列出一堆无关理由。

刻意**不**剥离 ``-thinking`` / ``-latest`` 这类变体后缀：那些通常是真的不同模型，
合并它们是误并，比漏配更危险（漏配看得见，误并会让报告给出看起来正常的错误结论）。
"""

import re
from collections.abc import Collection, Iterable, Sequence

# 差异类型
PREFIX = "prefix"
SEPARATOR = "separator"
CASE = "case"
DATE = "date"

REASON_LABELS: dict[str, str] = {
    PREFIX: "渠道前缀",
    SEPARATOR: "分隔符",
    CASE: "大小写",
    DATE: "日期戳",
}

_SEPARATOR_RE = re.compile(r"[\s_.]+")
_COLLAPSE_RE = re.compile(r"-{2,}")
# 尾部日期戳：-20260115 / -2026-01-15 / @2026-01-15（分隔符统一为 - 之后再判）
_DATE_RE = re.compile(r"[-@](20\d{2})-?(?:0[1-9]|1[0-2])-?(?:0[1-9]|[12]\d|3[01])$")


def normalize(model: str, skip: frozenset[str] = frozenset()) -> str:
    """把渠道模型串归一化为分组键。

    ``skip`` 里的变换被跳过，用于判定某类差异是否是这一组的成因。
    """
    text = model.strip()
    if PREFIX not in skip and "/" in text:
        text = text.rsplit("/", 1)[-1]
    if CASE not in skip:
        text = text.lower()
    if SEPARATOR not in skip:
        text = _COLLAPSE_RE.sub("-", _SEPARATOR_RE.sub("-", text))
    if DATE not in skip:
        text = _DATE_RE.sub("", text)
    return text.strip("-")


def reasons_for(group: Sequence[str]) -> list[str]:
    """这一组「凭什么算同族」：跳过某类变换后组会散开的，才是依据。"""
    return [kind for kind in (PREFIX, SEPARATOR, CASE, DATE) if len({normalize(m, frozenset({kind})) for m in group}) > 1]


def suggested_name(members: Sequence[str]) -> str:
    """建议显示名：优先无渠道前缀者，其次更短者，再次字典序。"""
    return min(members, key=lambda m: ("/" in m, len(m), m))


def build_suggestions(observed: Iterable[str], confirmed: Collection[str]) -> list[dict]:
    """把未确认归属的串按归一化键分组，组内 ≥2 个不同原始串的产出一条建议。

    已确认归属的串不参与——它们已经在别名表里，再建议一次是噪音。
    """
    groups: dict[str, list[str]] = {}
    for model in dict.fromkeys(observed):  # 去重且保序
        if model in confirmed:
            continue
        groups.setdefault(normalize(model), []).append(model)

    suggestions: list[dict] = []
    for key in sorted(groups):
        members = sorted(groups[key])
        if len(members) < 2:
            continue
        reasons = reasons_for(members)
        suggestions.append(
            {
                "normalized": key,
                "aliases": members,
                "reasons": reasons,
                "reason_labels": [REASON_LABELS[r] for r in reasons],
                "suggested_name": suggested_name(members),
            }
        )
    return suggestions
