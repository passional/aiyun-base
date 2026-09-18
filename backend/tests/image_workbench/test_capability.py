"""能力声明的结构校验、求值与「校验 + 补齐默认值」测试（任务 1.1 / 1.2 / 1.3）

纯单元测试：不建库、不起 HTTP，直接对着 ``config/capability.py`` 断言设计 D1/D2/D3/D5
的每一条约束。用一份与种子同构的清单做基准，个别用例只改它的一处。
"""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.exceptions import CustomException
from app.modules.image_workbench.config.capability import (
    ImageWorkbenchCapabilitySchema,
    dump_capability,
    evaluate_capability,
    parse_capability,
    render_prompt,
    resolve_params,
)


def _capability(**overrides: Any) -> dict[str, Any]:
    """与种子同构的基准清单：七个参数、一种出图方式、条件依赖与覆盖俱全。"""
    data: dict[str, Any] = {
        "reference": {"supported": True, "max_images": 10},
        "modes": [
            {
                "key": "layer_decomposition",
                "label": {"zh": "图层拆分", "en": "Layer decomposition"},
                "trigger": {"param": "layer_decomposition", "eq": True},
            }
        ],
        "params": [
            {
                "key": "size",
                "label": {"zh": "尺寸", "en": "Size"},
                "control": "select",
                "allowed": ["1K", "1.5K", "2K"],
                "default": "2K",
                "source": "documented",
                "overrides": {"layer_decomposition": {"allowed": ["1K", "1.5K", "2K", "auto"], "default": "auto"}},
            },
            {
                "key": "layer_decomposition",
                "label": {"zh": "图层拆分", "en": "Layer decomposition"},
                "control": "switch",
                "default": False,
                "source": "inferred",
            },
            {
                "key": "output_format",
                "label": {"zh": "输出格式", "en": "Output format"},
                "control": "select",
                "allowed": ["png", "jpeg"],
                "default": "png",
                "source": "documented",
            },
            {
                "key": "background",
                "label": {"zh": "图片透明通道", "en": "Transparent background"},
                "control": "select",
                "allowed": ["opaque", "transparent"],
                "default": "opaque",
                "source": "documented",
                "when": {
                    "all": [
                        {"param": "output_format", "not_in": ["jpeg"]},
                        {"request": "reference_count", "eq": 1},
                        {"request": "reference_has_alpha", "eq": True},
                    ]
                },
            },
            {
                "key": "watermark",
                "label": {"zh": "水印", "en": "Watermark"},
                "control": "switch",
                "default": False,
                "source": "documented",
            },
            {
                "key": "optimize_prompt_options.mode",
                "label": {"zh": "提示词优化档位", "en": "Prompt optimization"},
                "control": "select",
                "allowed": ["standard", "fast"],
                "default": "standard",
                "source": "documented",
            },
            {
                "key": "response_format",
                "label": {"zh": "返回格式", "en": "Response format"},
                "control": "select",
                "allowed": ["url", "b64_json"],
                "default": "url",
                "source": "documented",
            },
        ],
    }
    data.update(overrides)
    return data


def _param(capability: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in capability["params"] if item["key"] == key)


def _without_param(capability: dict[str, Any], key: str) -> dict[str, Any]:
    capability["params"] = [item for item in capability["params"] if item["key"] != key]
    return capability


def _parsed(**overrides: Any) -> ImageWorkbenchCapabilitySchema:
    return ImageWorkbenchCapabilitySchema.model_validate(_capability(**overrides))


# ─────────────────────── 1.1 清单结构 ───────────────────────


def test_default_outside_declared_domain_rejected() -> None:
    """默认值落在取值域之外 → 拒绝保存，并指出是哪个参数。"""
    capability = _capability()
    _param(capability, "size")["default"] = "4K"
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    assert "size" in str(excinfo.value) and "默认值" in str(excinfo.value)

    # 出图方式覆盖出来的默认值同样受约束
    capability = _capability()
    _param(capability, "size")["overrides"]["layer_decomposition"]["default"] = "8K"
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    assert "size" in str(excinfo.value)


def test_duplicate_allowed_values_rejected() -> None:
    """同一条目内取值重复 → 拒绝。"""
    capability = _capability()
    _param(capability, "output_format")["allowed"] = ["png", "png"]
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    assert "重复" in str(excinfo.value)

    # 参数键也不能重复
    capability = _capability()
    _param(capability, "watermark")["key"] = "size"
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    assert "重复" in str(excinfo.value)


def test_when_referencing_unknown_param_rejected() -> None:
    """when 引用了不存在的参数键 → 拒绝，并指出引用方与缺失方。"""
    capability = _capability()
    _param(capability, "background")["when"] = {"all": [{"param": "output_formats", "eq": "png"}]}
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    message = str(excinfo.value)
    assert "background" in message and "output_formats" in message


def test_mode_trigger_referencing_unknown_param_rejected() -> None:
    """modes[].trigger 指向不存在的参数 → 拒绝；触发取值不在取值域内同样拒绝。"""
    capability = _capability()
    capability["modes"][0]["trigger"] = {"param": "layer_decompose", "eq": True}
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    message = str(excinfo.value)
    assert "layer_decomposition" in message and "layer_decompose" in message

    # 参数存在但触发取值不在它的取值域内（switch 写成了字符串）
    capability = _capability()
    capability["modes"][0]["trigger"] = {"param": "layer_decomposition", "eq": "true"}
    with pytest.raises(ValidationError) as excinfo:
        ImageWorkbenchCapabilitySchema.model_validate(capability)
    assert "取值域" in str(excinfo.value)


def test_when_requires_exactly_one_left_and_one_operator() -> None:
    """条件必须二选一左值、四选一算子（封闭词表），不得写成表达式。"""
    capability = _capability()
    _param(capability, "watermark")["when"] = {"all": [{"eq": True}]}
    with pytest.raises(ValidationError):
        ImageWorkbenchCapabilitySchema.model_validate(capability)

    capability = _capability()
    _param(capability, "watermark")["when"] = {"all": [{"param": "size", "eq": "2K", "ne": "1K"}]}
    with pytest.raises(ValidationError):
        ImageWorkbenchCapabilitySchema.model_validate(capability)

    # 请求事实集合是封闭的：不认识的事实名直接拒绝
    capability = _capability()
    _param(capability, "watermark")["when"] = {"all": [{"request": "reference_has_alpha_v2", "eq": True}]}
    with pytest.raises(ValidationError):
        ImageWorkbenchCapabilitySchema.model_validate(capability)


def test_capability_round_trips_through_json() -> None:
    """合法声明可解析、可按别名落库、并原样读回（含 ``in`` 算子与点号参数键）。"""
    capability = _parsed()
    dumped = dump_capability(capability)
    assert dumped["params"][0]["key"] == "size"
    assert "when" not in dumped["params"][0], "无依赖的条目不该落出 when 键"
    assert dumped["params"][3]["when"]["all"][0] == {"param": "output_format", "not_in": ["jpeg"]}
    assert parse_capability(dumped).model_dump() == capability.model_dump()

    capability = _capability()
    _param(capability, "watermark")["when"] = {"all": [{"request": "reference_count", "in": [1, 2]}]}
    assert dump_capability(ImageWorkbenchCapabilitySchema.model_validate(capability))["params"][4]["when"]["all"][0] == {
        "request": "reference_count",
        "in": [1, 2],
    }


def test_parse_capability_falls_back_on_dirty_data() -> None:
    """旧结构 / 手改数据不该让整个列表 500，兜底为空清单。"""
    legacy = {
        "sizes": ["1K", "2K"],
        "support_reference": True,
        "max_reference_images": 10,
        "output_formats": ["png"],
        "watermark_default": False,
        "extra_params": {},
    }
    assert parse_capability(legacy).params == []
    assert parse_capability(None).params == []


# ─────────────────────── 1.2 求值器 ───────────────────────


def test_when_unsatisfied_marks_param_unavailable_with_reason() -> None:
    """when 不满足 → 不可用，并且能说明为何不可用。"""
    capability = _parsed()
    # 输出格式为 jpeg 时三条条件全不满足
    evaluation = evaluate_capability(capability, submitted={"output_format": "jpeg"}, facts={"reference_count": 0, "reference_has_alpha": False})
    background = evaluation.get("background")
    assert background is not None and not background.available
    assert background.reason is not None and background.reason.startswith("仅当")
    assert "输出格式" in background.reason and "本次参考图数量" in background.reason

    # 只差最后一个条件时，说明文案只列不满足的那一条
    evaluation = evaluate_capability(
        capability,
        submitted={"output_format": "png"},
        facts={"reference_count": 1, "reference_has_alpha": False},
    )
    background = evaluation.get("background")
    assert not background.available
    assert "本次参考图含透明通道" in background.reason and "输出格式" not in background.reason


def test_when_satisfied_marks_param_available() -> None:
    """when 全部满足 → 可用，其余无 when 的条目也一律可用。"""
    capability = _parsed()
    evaluation = evaluate_capability(
        capability,
        submitted={"output_format": "png"},
        facts={"reference_count": 1, "reference_has_alpha": True},
    )
    background = evaluation.get("background")
    assert background is not None and background.available and background.reason is None
    assert evaluation.available_keys == list(evaluation.params)


def test_unknown_request_fact_is_treated_as_unsatisfied() -> None:
    """不认识的请求事实按不满足处理（保守置灰），而不是猜。"""
    capability = _parsed()
    # 事实整体缺失：reference_count / reference_has_alpha 都不认识
    evaluation = evaluate_capability(capability, submitted={}, facts={})
    assert not evaluation.get("background").available

    # 只认识一半：仍不满足
    evaluation = evaluate_capability(capability, submitted={}, facts={"reference_count": 1})
    assert not evaluation.get("background").available

    # 事实说的是别的名字（调用方版本较旧）：同样不满足
    evaluation = evaluate_capability(
        capability,
        submitted={},
        facts={"reference_count_v2": 1, "reference_has_alpha_v2": True},
    )
    assert not evaluation.get("background").available


def test_mode_derivation_and_overrides() -> None:
    """出图方式由参数值派生（请求里没有 mode），命中后取覆盖的取值域与默认值。"""
    capability = _parsed()

    plain = evaluate_capability(capability, submitted={}, facts={})
    assert plain.active_mode is None
    assert plain.get("size").allowed == ["1K", "1.5K", "2K"]
    assert plain.get("size").default == "2K"
    assert plain.get("size").override_mode is None

    layered = evaluate_capability(capability, submitted={"layer_decomposition": True}, facts={})
    assert layered.active_mode == "layer_decomposition"
    assert layered.get("size").allowed == ["1K", "1.5K", "2K", "auto"]
    assert layered.get("size").default == "auto"
    assert layered.get("size").override_mode == "layer_decomposition"


def test_mode_trigger_null_is_intrinsic_and_not_derived() -> None:
    """trigger 为 null 的出图方式是固有能力位，不参与派生，也不改变任何取值域。

    固有方式必须自带标记语法（1.1），因此这里用一个声明了 marker 的方式——它仍然不参与
    派生，这正是本条要守住的语义。
    """
    capability = _without_param(_capability(), "layer_decomposition")
    # 固有方式（交互编辑）必须自带标记语法（1.1）
    capability["modes"] = [
        {"key": "interactive_edit", "label": {"zh": "交互编辑", "en": "Interactive edit"}, "trigger": None, "marker": {"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>"}},
    ]
    # 触发该方式的参数与其覆盖一并移除，清单仍然自洽
    for param in capability["params"]:
        param.pop("overrides", None)
    parsed = ImageWorkbenchCapabilitySchema.model_validate(capability)

    evaluation = evaluate_capability(parsed, submitted={}, facts={})
    assert evaluation.active_mode is None
    assert [mode.key for mode in evaluation.modes] == ["interactive_edit"]
    assert parsed.coordinate_mode.key == "interactive_edit"


# ─────────────────────── 1.3 校验与默认值补齐 ───────────────────────


def test_submitted_param_outside_domain_rejected() -> None:
    """用户显式提交的取值超出（当前条件下生效的）取值域 → 拒绝。"""
    capability = _parsed()
    with pytest.raises(CustomException) as excinfo:
        resolve_params(capability, submitted={"size": "4K"}, facts={})
    assert "4K" in excinfo.value.msg and "1K" in excinfo.value.msg

    # 出图方式命中后用的是覆盖过的取值域：`auto` 在普通模式下不被接受
    with pytest.raises(CustomException) as excinfo:
        resolve_params(capability, submitted={"size": "auto"}, facts={})
    assert "auto" in excinfo.value.msg

    values, _ = resolve_params(capability, submitted={"layer_decomposition": True, "size": "auto"}, facts={})
    assert values["size"] == "auto"


def test_submitted_unknown_key_rejected() -> None:
    """用户显式提交了清单里没有的键 → 拒绝（不再有通用透传字段）。"""
    capability = _parsed()
    with pytest.raises(CustomException) as excinfo:
        resolve_params(capability, submitted={"strength": 0.5}, facts={})
    assert "strength" in excinfo.value.msg

    # 旧结构里的 extra_params 透传键在新结构下同样被拒
    with pytest.raises(CustomException):
        resolve_params(capability, submitted={"output_format_extra": "png"}, facts={})


def test_submitted_unavailable_param_rejected_with_reason() -> None:
    """用户绕过表单提交了当前条件下不可用的参数 → 拒绝并说明为何不可用。"""
    capability = _parsed()
    with pytest.raises(CustomException) as excinfo:
        resolve_params(capability, submitted={"background": "transparent"}, facts={"reference_count": 0, "reference_has_alpha": False})
    assert "background" in excinfo.value.msg and "不可用" in excinfo.value.msg

    # 条件满足时同一个参数就能提交
    values, _ = resolve_params(
        capability,
        submitted={"background": "transparent"},
        facts={"reference_count": 1, "reference_has_alpha": True},
    )
    assert values["background"] == "transparent"


def test_defaults_filled_only_for_available_params() -> None:
    """未提交的参数只在**当前条件下可用**时才补默认值，不可用的既不补也不进快照。"""
    capability = _parsed()

    # 普通请求：没有参考图 → background 不可用
    values, evaluation = resolve_params(capability, submitted={}, facts={"reference_count": 0, "reference_has_alpha": False})
    assert "background" not in values, "不可用的参数 MUST NOT 进快照"
    assert set(values) == set(evaluation.available_keys)
    assert values["size"] == "2K" and values["watermark"] is False and values["response_format"] == "url"
    assert values["optimize_prompt_options.mode"] == "standard", "点号键原样进快照，组装时再按路径建嵌套"

    # 条件满足时它才被补上默认值
    values, _ = resolve_params(capability, submitted={}, facts={"reference_count": 1, "reference_has_alpha": True})
    assert values["background"] == "opaque"


def test_plain_request_without_reference_image_passes() -> None:
    """普通请求（无参考图、未提交 background）必须正常通过。

    这是「先无条件补齐默认值再校验」那种写法会堵死整条生成路径的直接反例：background
    的默认值是「opaque」，一旦被无差别补齐，随即会被它自己的可用性条件拒掉。
    """
    capability = _parsed()
    values, evaluation = resolve_params(capability, submitted={"size": "1K"}, facts={"reference_count": 0, "reference_has_alpha": False})
    assert values["size"] == "1K"
    assert evaluation.get("background").available is False
    assert "background" not in values

    # 连 facts 都不给的调用方（例如前端先渲染一次表单）同样不该被拒
    values, _ = resolve_params(capability, submitted={"size": "2K"}, facts={})
    assert "background" not in values


def test_capability_without_any_param_accepts_empty_submission() -> None:
    """空清单（模型没有任何可配参数）下，提交空参数字典是合法的。"""
    capability = ImageWorkbenchCapabilitySchema.model_validate({"reference": {"supported": False, "max_images": 0}, "modes": [], "params": []})
    values, evaluation = resolve_params(capability, submitted={}, facts={})
    assert values == {} and evaluation.available_keys == []
    # 无清单却提交参数 → 拒绝
    with pytest.raises(CustomException):
        resolve_params(capability, submitted={"size": "2K"}, facts={})


def test_resolve_params_does_not_mutate_capability() -> None:
    """求值只读取声明，不写回（同一份清单可被反复求值）。"""
    capability = _capability()
    parsed = ImageWorkbenchCapabilitySchema.model_validate(deepcopy(capability))
    before = dump_capability(parsed)
    resolve_params(parsed, submitted={"layer_decomposition": True, "size": "auto"}, facts={"reference_count": 1, "reference_has_alpha": True})
    assert dump_capability(parsed) == before

# ─────────────── 1.1 固有出图方式与提示词标记语法（design D4） ───────────────


def _mode(**overrides: Any) -> dict[str, Any]:
    """一种固有出图方式（不由参数触发）的声明，逐用例只改一处。"""
    data: dict[str, Any] = {
        "key": "interactive_edit",
        "label": {"zh": "交互编辑", "en": "Interactive edit"},
        "trigger": None,
        "marker": {"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>", "range": 1000},
    }
    data.update(overrides)
    return data


def _with_modes(*modes: dict[str, Any]):
    """在基准清单上追加出图方式，返回解析后的能力声明。"""
    data = _capability()
    data["modes"] = [*data["modes"], *modes]
    return ImageWorkbenchCapabilitySchema.model_validate(data)


def test_intrinsic_mode_without_marker_is_rejected() -> None:
    """声明了交互编辑却缺标记语法 → 拒绝保存。

    不由参数触发的方式就是靠坐标表达位置的那一类，占位形态与标记写法是它与上游的全部
    约定；缺了它，这个方式无从落地——前端不知道插什么、服务端不知道该替换成什么。
    """
    with pytest.raises(ValidationError):
        ImageWorkbenchCapabilitySchema.model_validate({"modes": [_mode(marker=None)], "params": []})


def test_triggered_mode_is_not_required_to_declare_marker() -> None:
    """不使用坐标的出图方式（如图层拆分）不声明标记语法，仍是合规声明。

    规格明写：强加一份空声明只会让本来合规的能力声明被判为不合规。基准清单里的
    ``layer_decomposition`` 正是这种情形。
    """
    capability = _with_modes(_mode())
    triggered = next(mode for mode in capability.modes if mode.key == "layer_decomposition")
    assert triggered.marker is None
    # 判据是「有没有标记语法」，不是「键叫什么」：有标记的那一种才是坐标方式
    assert capability.coordinate_mode is not None and capability.coordinate_mode.key == "interactive_edit"


def test_marker_placeholders_must_line_up() -> None:
    """模板里的占位与选区序号能对上：占位形态含且只含一个 {n}，标记写法含四个坐标。"""
    for broken in (
        {"placeholder": "区域", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>"},
        {"placeholder": "区域{n}{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>"},
        {"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2}</bbox>"},
        {"placeholder": "区域{n}", "template": "<bbox>{x1} {x1} {y2} {y2}</bbox>"},
    ):
        with pytest.raises(ValidationError):
            ImageWorkbenchCapabilitySchema.model_validate({"modes": [_mode(marker=broken)], "params": []})


def test_declared_placeholder_and_template_drive_rendering() -> None:
    """写法来自声明：换成另一套占位与模板，渲染随之改变且不动代码（design D4 / D10）。"""
    capability = _with_modes(_mode(marker={"placeholder": "[{n}号区]", "template": "{{x1},{y1},{x2},{y2}}", "range": 500}))
    final, missing, unused = render_prompt(capability, "把[1号区]换成猫", [(1, [10, 20, 30, 40])])
    assert (final, missing, unused) == ("把{10,20,30,40}换成猫", [], [])


def test_referenced_selection_without_coordinate_is_reported_not_swallowed() -> None:
    """引用了选区却对不上坐标 → 如实报告，绝不静默丢弃（design D4 第一条硬规则）。"""
    capability = _with_modes(_mode())
    final, missing, unused = render_prompt(capability, "把区域2换掉", [(1, [1, 2, 3, 4])])
    # 原样的占位符留在最终提示词里，两个对不上的序号都报出来
    assert missing == [2] and unused == [1] and "区域2" in final


def test_multiple_selections_render_in_place() -> None:
    """一次任务带多个选区：每个占位都在用户摆的位置上被替换（design D3）。"""
    capability = _with_modes(_mode())
    final, missing, unused = render_prompt(
        capability,
        "把区域1换成一只猫，背景区域2调亮",
        [(1, [120, 180, 640, 760]), (2, [0, 0, 10, 10])],
    )
    assert final == "把<bbox>120 180 640 760</bbox>换成一只猫，背景<bbox>0 0 10 10</bbox>调亮"
    assert (missing, unused) == ([], [])


def test_placeholder_is_not_matched_across_indices() -> None:
    """占位识别不误伤同前缀的序号：``区域1`` 不得匹配到 ``区域12`` 的前半截。"""
    capability = _with_modes(_mode())
    final, missing, unused = render_prompt(capability, "把区域12换掉", [(12, [1, 2, 3, 4])])
    assert final == "把<bbox>1 2 3 4</bbox>换掉" and (missing, unused) == ([], [])


def test_marker_range_comes_from_declaration() -> None:
    """坐标区间由声明给出（规格：坐标超出**能力声明规定的区间**即拒绝）。"""
    capability = _with_modes(_mode(marker={"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>", "range": 500}))
    assert capability.coordinate_mode.marker.range == 500
    with pytest.raises(ValidationError):
        ImageWorkbenchCapabilitySchema.model_validate({"modes": [_mode(marker={"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>", "range": 1})], "params": []})
