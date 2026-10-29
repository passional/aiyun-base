"""绘图工作台 - 能力声明的结构、求值与校验（设计 D1/D2/D3/D5）

能力声明是**数据**：模型支持哪些出图参数、取值范围、默认值、何时可用、有哪些出图
方式，全部落在这份清单里，前后端都读它。本文件是它的唯一定义处——存储时按它做结构
校验，提交时按它校验参数并补齐默认值，组装上游请求体时按它的参数键逐层建字典。

结构（``image_workbench_model.capability_json``）::

    {
        "reference": {"supported": true, "max_images": 14},
        "modes": [
            {"key": "layer_decomposition", "label": {"zh": "图层拆分", "en": "Layer decomposition"}, "trigger": {"param": "layer_decomposition", "eq": true}},
            {"key": "interactive_edit", "label": {"zh": "交互编辑", "en": "Interactive edit"}, "trigger": None,
             "marker": {"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>", "range": 1000}},
        ],
        "params": [
            {
                "key": "size",
                "label": {"zh": "尺寸", "en": "Size"},
                "control": "select",
                "allowed": ["1K", "1.5K", "2K"],
                "allow_custom": false,
                "default": "2K",
                "source": "documented",
                "when": null,
                "overrides": {"layer_decomposition": {"allowed": ["1K", "1.5K", "2K", "auto"], "default": "auto"}},
            }
        ],
    }

三条容易写歪的约定，读代码前先对齐：

1. **参数键就是上游字段路径，点号表示嵌套**（D4）：``optimize_prompt_options.mode``
   组装成 ``{"optimize_prompt_options": {"mode": ...}}``。参数与上游字段一一对应，
   不设「本地名 → 上游名」的映射表。
2. **参数的发与不发只由清单决定，不由接入方式决定**（D4）：接入方式只决定参考图的
   上行形态。一个 OpenAI 兼容的模型，其清单里不声明 ``output_format`` 与
   ``watermark``，它们自然不会被发送——因此「参数快照 == 实际发送的参数集」恒成立。
3. **出图方式是派生结果，不是独立字段**（D3）：请求里没有 ``mode``，清单用
   ``modes[].trigger`` 声明「哪个参数取哪个值即进入哪种出图方式」，参数的
   ``overrides`` 再按出图方式覆盖取值域与默认值。不一致的状态因此不存在。
4. **靠坐标表达位置的方式自带写法**（D4）：``trigger`` 为 null 的方式必须声明
   ``marker``（占位形态 + 坐标标记写法 + 坐标区间）。提示词里的占位由用户摆位置、
   标记写法由声明决定，服务端只做替换——把写法硬编码进代码，前端、后端与上游三处
   就各有一份真相了。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from app.core.exceptions import CustomException
from app.core.logger import logger

ParamControl = Literal["select", "multiselect", "switch", "number", "text"]
"""参数条目的取值形态：既是前端的渲染指令，也是后端的校验依据（同源，不会各说各话）。"""

ParamSource = Literal["documented", "inferred", "measured"]
"""取值来源标记：上游文档 / 未实测的推断 / 已实测。未实测的在界面上必须可辨认。"""

RequestFact = Literal["reference_count", "reference_has_alpha"]
"""本次请求的事实集合——**封闭**（D2）。

只有「参考图数量」（整数）与「参考图是否带透明通道」（布尔）两项。集合封闭是为了让
不认识新事实的调用方安全地把它当作**不满足**（保守置灰），而不是猜。
"""

_FACT_LABEL: dict[str, str] = {
    "reference_count": "本次参考图数量",
    "reference_has_alpha": "本次参考图含透明通道",
}

_OPERATOR_LABEL: dict[str, str] = {"eq": "为", "ne": "不为", "in": "属于", "not_in": "不属于"}

# 自定义尺寸的形态：「宽x高」的像素值（select 且 allow_custom 时额外接受）
_CUSTOM_VALUE_PATTERN = re.compile(r"^\d{2,5}[xX*]\d{2,5}$")

_MISSING = object()
"""事实缺失的哨兵：缺失即不满足（保守置灰），与「值恰好等于 None」区分开。"""

_MARKER_FIELDS: tuple[str, ...] = ("x1", "y1", "x2", "y2")
"""坐标标记模板里必须出现的四个占位名（顺序即左上点、右下点的语义）。"""


def _value_key(value: Any) -> str:
    """取值去重与比较用的规范化键（``True`` 与 ``1`` 不可互认）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _ensure_unique(values: list[Any], what: str) -> None:
    seen: set[str] = set()
    for value in values:
        key = _value_key(value)
        if key in seen:
            raise ValueError(f"{what}「{_render_value(value)}」重复")
        seen.add(key)


def _render_value(value: Any) -> str:
    """取值 → 面向用户的短文案（仅用于报错与不可用原因）。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "、".join(_render_value(item) for item in value) or "（空）"
    return str(value)


def _is_custom_value(value: Any) -> bool:
    """是否符合「自定义取值」形态（今天只有尺寸一种：``1024x1024``）。"""
    return isinstance(value, str) and bool(_CUSTOM_VALUE_PATTERN.fullmatch(value.strip()))


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """丢掉「没写就是没有」的键，让序列化结果与声明里写出来的形态一致。

    响应链用的是 ``model_dump()``（不带 ``by_alias``、不排除空值），不收口的话线上 JSON
    会多出一串 ``null`` 与 ``{}``，且别名（``in``）会退化成 Python 字段名（``in_``）
    ——前端要按 Python 侧的名字去认，与 design D1/D2 写下来的结构对不上。
    """
    return {key: value for key, value in data.items() if value is not None and value != {} and value != []}


# ─────────────────────────── 结构定义 ───────────────────────────


class ImageWorkbenchLabelSchema(BaseModel):
    """面向用户的显示名（中英各一份，前端按当前语言取用）。"""

    zh: str = Field(..., min_length=1, max_length=64, description="中文显示名")
    en: str = Field(..., min_length=1, max_length=64, description="英文显示名")

    @field_validator("zh", "en")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("显示名不能为空")
        return value.strip()


class ImageWorkbenchCapabilityReferenceSchema(BaseModel):
    """参考图能力（原 ``support_reference`` / ``max_reference_images`` 迁入）。

    缺省**不支持**：清单就是模型的参数面，一份还没配置过的清单不该顺手替管理员断言
    「这个模型支持参考图」。「支持但上限为 0」是自相矛盾的状态，这里不默认制造它。
    """

    supported: bool = Field(default=False, description="是否支持参考图")
    max_images: int = Field(default=0, ge=0, description="参考图张数上限")

    @model_validator(mode="after")
    def _normalize(self) -> ImageWorkbenchCapabilityReferenceSchema:
        if not self.supported and self.max_images:
            # 不支持参考图的模型带上限没有意义，归一为 0，避免前端误显示入口
            self.max_images = 0
        return self


class ImageWorkbenchWhenClauseSchema(BaseModel):
    """``when`` 合取式里的单个条件：左值二选一，算子四选一（D2）。

    左值 ``{"param": "<参数键>"}``（另一参数的当前值）或 ``{"request": "<请求事实>"}``；
    算子 ``eq`` / ``ne`` / ``in`` / ``not_in``。不引入 ``or`` / ``not``——已知规则全是
    合取，一旦引入等于开始写一个小型表达式语言（ADR-0006 刻意推迟的东西）。
    """

    model_config = ConfigDict(populate_by_name=True)

    param: str | None = Field(default=None, min_length=1, max_length=128, description="左值：另一个参数的键")
    request: RequestFact | None = Field(default=None, description="左值：本次请求的事实")
    eq: Any = Field(default=None, description="算子：等于")
    ne: Any = Field(default=None, description="算子：不等于")
    in_: list[Any] | None = Field(default=None, alias="in", description="算子：属于其中之一")
    not_in: list[Any] | None = Field(default=None, description="算子：不属于其中任何一个")

    @model_validator(mode="after")
    def _exactly_one_left_and_operator(self) -> ImageWorkbenchWhenClauseSchema:
        if (self.param is None) == (self.request is None):
            raise ValueError("条件必须且只能指定 param（另一个参数）或 request（请求事实）之一")
        operators = [name for name in ("eq", "ne", "in_", "not_in") if getattr(self, name) is not None]
        if len(operators) != 1:
            raise ValueError("条件必须且只能指定 eq / ne / in / not_in 之一")
        if self.in_ is not None and not self.in_:
            raise ValueError("算子 in 的取值不得为空")
        if self.not_in is not None and not self.not_in:
            raise ValueError("算子 not_in 的取值不得为空")
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        """按声明里的写法序列化（``in`` 而不是 ``in_``），并只留下生效的左值与算子。"""
        data = handler(self)
        if data.get("in_") is not None:
            data["in"] = data.pop("in_")
        return _compact(data)

    @property
    def operator(self) -> str:
        """生效的算子名（``in`` / ``not_in`` 归一为声明里的写法）。"""
        for name, declared in (("eq", "eq"), ("ne", "ne"), ("in_", "in"), ("not_in", "not_in")):
            if getattr(self, name) is not None:
                return declared
        return ""

    @property
    def right(self) -> Any:
        """生效的右值。"""
        return getattr(self, {"in": "in_", "not_in": "not_in"}.get(self.operator, self.operator), None)


class ImageWorkbenchWhenSchema(BaseModel):
    """条件依赖：一个**合取式**（``all`` 是唯一的组合方式）。"""

    all: list[ImageWorkbenchWhenClauseSchema] = Field(..., min_length=1, description="全部满足才可用")

    @field_validator("all")
    @classmethod
    def _not_empty(cls, value: list[ImageWorkbenchWhenClauseSchema]) -> list[ImageWorkbenchWhenClauseSchema]:
        if not value:
            raise ValueError("条件依赖 all 不得为空数组；无依赖时请整体省略（null）")
        return value


class ImageWorkbenchParamOverrideSchema(BaseModel):
    """某参数在某出图方式下的覆盖（只覆盖会随出图方式变化的两项）。"""

    allowed: list[Any] | None = Field(default=None, description="覆盖后的取值域(缺省沿用本条目)")
    default: Any = Field(default=None, description="覆盖后的默认值(缺省沿用本条目)")


class ImageWorkbenchModeTriggerSchema(BaseModel):
    """出图方式的触发条件：某参数取某值即进入该方式（D3）。"""

    param: str = Field(..., min_length=1, max_length=128, description="触发参数键")
    eq: Any = Field(..., description="触发取值")


def _template_fields(template: str) -> list[str]:
    """取出模板里的全部占位名（``{n}`` / ``{x1}`` …），用于校验占比对得上。"""
    return re.findall(r"\{([a-zA-Z0-9_]*)\}", template)


class ImageWorkbenchModeMarkerSchema(BaseModel):
    """靠坐标表达位置的出图方式，在提示词里的**占位形态**与**坐标标记写法**（D4）。

    ```jsonc
    {"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>", "range": 1000}
    ```

    ``range`` 是坐标空间的大小：合法坐标是 ``0 .. range-1`` 的整数（上游把坐标归一化到
    1000×1000、取值 0–999）。区间在这里声明而不是写死在代码里——规格要求「坐标超出
    **能力声明规定的区间**」被拒绝，代码就不该自己记一个数字。

    两种写法的**占位名都取自声明**（规格：MUST NOT 硬编码在代码里），因此校验的是
    「占位名对不对得上」而不是「写得像不像我期望的那串」：``placeholder`` 里必须有一个
    ``{n}``（选区序号），``template`` 里必须有 ``{x1}{y1}{x2}{y2}``（四个坐标）——换成
    ``[AREA-{n}]`` / ``{{x1},{y1}}`` 同样合规，渲染出来的就是上游要的那一种。
    """

    placeholder: str = Field(..., min_length=1, max_length=64, description="提示词里的占位形态(含 {n})")
    template: str = Field(..., min_length=1, max_length=128, description="坐标标记写法(含 {x1}{y1}{x2}{y2})")
    range: int = Field(default=1000, ge=2, description="坐标空间：合法坐标为 0..range-1 的整数")

    @field_validator("placeholder")
    @classmethod
    def _check_placeholder(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("占位形态不能为空")
        fields = _template_fields(value)
        if fields.count("n") != 1 or len(fields) != 1:
            raise ValueError(f"占位形态「{value}」必须含且只含一个选区序号占位 {{n}}")
        return value

    @field_validator("template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("坐标标记写法不能为空")
        fields = _template_fields(value)
        missing = [name for name in _MARKER_FIELDS if fields.count(name) != 1]
        if missing or len(fields) != len(_MARKER_FIELDS):
            raise ValueError(f"坐标标记写法「{value}」必须含且只含 {', '.join('{' + name + '}' for name in _MARKER_FIELDS)} 四个坐标占位")
        return value

    def pattern(self) -> re.Pattern[str]:
        """占位形态 → 识别提示词里引用的正则（``区域{n}`` → ``区域(\\d+)``）。

        按 ``{n}`` 切成前后两段分别转义：占位形态里的其它字符（括号、点号等）都当字面量，
        不让它们退化成正则元字符。
        """
        prefix, _, suffix = self.placeholder.partition("{n}")
        return re.compile(re.escape(prefix) + r"(\d+)" + re.escape(suffix))

    def render(self, bbox: list[int]) -> str:
        """填好坐标的标记（``<bbox>120 180 640 760</bbox>``）。

        逐个替换 ``{x1}`` 这类占位名，不借 ``str.format``：模板是**上游约定的一段字面
        文本**，管理员在里面写什么标点都不该被当成本地语言的转义规则去解释（``str.format``
        会把 ``{{`` 当转义、把孤立的 ``}`` 当语法错误）。
        """
        x1, y1, x2, y2 = bbox
        rendered = self.template
        for name, value in zip(_MARKER_FIELDS, (x1, y1, x2, y2), strict=True):
            rendered = rendered.replace("{" + name + "}", str(value))
        return rendered


class ImageWorkbenchModeSchema(BaseModel):
    """一种出图方式。

    ``trigger`` 可以为 null：表示该出图方式不由参数触发，而是该模型固有的能力位
    （交互编辑就是这样——上游没有对应参数，它靠提示词里的坐标标记表达）。trigger 为
    null 时该方式不参与参数派生，只用于决定是否向用户提供它的入口。

    ``trigger`` 为 null 的方式**必须**声明 ``marker``：不由参数触发的方式（今天只有
    交互编辑这一种）就是靠坐标表达位置的，而占位形态与标记写法正是它与上游的全部约定，
    缺了它这个方式无从落地。触发式的出图方式（如图层拆分）与坐标无关，不要求也不禁止
    声明 ``marker``——规格明写「强加一份空声明只会让本来合规的声明被判为不合规」。
    """

    key: str = Field(..., min_length=1, max_length=64, description="出图方式标识")
    label: ImageWorkbenchLabelSchema = Field(..., description="面向用户的显示名")
    trigger: ImageWorkbenchModeTriggerSchema | None = Field(default=None, description="触发条件(缺省为固有方式)")
    marker: ImageWorkbenchModeMarkerSchema | None = Field(default=None, description="提示词占位与坐标标记写法(固有方式必填)")

    @field_validator("key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("出图方式标识不能为空")
        return value.strip()

    @model_validator(mode="after")
    def _marker_required_when_intrinsic(self) -> ImageWorkbenchModeSchema:
        if self.trigger is None and self.marker is None:
            raise ValueError(f"出图方式「{self.key}」不由参数触发，必须声明提示词占位形态与坐标标记写法（marker）")
        return self


class ImageWorkbenchParamSchema(BaseModel):
    """出图参数条目——参数的全部知识集中在这一处。"""

    key: str = Field(..., min_length=1, max_length=128, description="参数键(同时是上游请求体的字段路径，点号表示嵌套)")
    label: ImageWorkbenchLabelSchema = Field(..., description="面向用户的显示名")
    control: ParamControl = Field(default="select", description="取值形态(前端渲染指令)")
    allowed: list[Any] | None = Field(default=None, description="取值域(select/multiselect 必填)")
    allow_custom: bool = Field(default=False, description="select 时是否额外允许填自定义「宽x高」")
    default: Any = Field(default=None, description="默认值(必填且必须落在取值域内)")
    source: ParamSource = Field(default="documented", description="取值来源(documentation/inferred/measured)")
    when: ImageWorkbenchWhenSchema | None = Field(default=None, description="条件依赖(缺省为总是可用)")
    overrides: dict[str, ImageWorkbenchParamOverrideSchema] = Field(default_factory=dict, description="按出图方式覆盖取值域与默认值")

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("参数键不能为空")
        stripped = value.strip()
        if stripped != value:
            raise ValueError("参数键不得含首尾空白")
        if any(not segment for segment in stripped.split(".")):
            raise ValueError("参数键的点号路径不得出现空段")
        return stripped

    @model_validator(mode="after")
    def _check_domain(self) -> ImageWorkbenchParamSchema:
        """逐条目自检：取值域本身、默认值形态、默认值是否落在域内、覆盖是否越界。

        类型错误与越界分开报：只说「越界」，管理员会去改取值域；说「必须是布尔值」，
        他才知道是控件类型选错了。
        """
        allowed = self.allowed
        if allowed is not None:
            _ensure_unique(allowed, f"参数「{self.key}」的取值")
        if self.control in ("select", "multiselect") and not allowed:
            raise ValueError(f"参数「{self.key}」的 control 为 {self.control}，必须声明非空的取值域 allowed")
        if self.control == "switch" and allowed:
            raise ValueError(f"参数「{self.key}」的 control 为 switch，取值域恒为是/否，不得声明 allowed")

        if self.control == "multiselect":
            # 多选的默认值是一组值，逐个都要在域内（_value_matches 判的是单值，不适用）
            if not isinstance(self.default, list):
                raise ValueError(f"参数「{self.key}」的 control 为 multiselect，默认值必须是数组")
            domain = {_value_key(candidate) for candidate in allowed or []}
            for item in self.default:
                if _value_key(item) not in domain:
                    raise ValueError(f"参数「{self.key}」的默认值「{_render_value(item)}」不在取值域内")
        else:
            if self.default is None:
                raise ValueError(f"参数「{self.key}」未声明默认值")
            if not _value_matches(self.control, allowed, self.allow_custom, self.default):
                raise ValueError(
                    f"参数「{self.key}」的默认值「{_render_value(self.default)}」不在取值域内（control 为 {self.control}，可取：{_domain_text(self.control, allowed, self.allow_custom)}）"
                )

        for mode_key, override in self.overrides.items():
            if override.allowed is not None:
                _ensure_unique(override.allowed, f"参数「{self.key}」在出图方式「{mode_key}」下的取值")
            if override.default is None:
                continue
            # 覆盖出来的默认值同样必须落在**该方式下生效的**取值域内，否则该方式一命中就发出一个越界值
            domain = override.allowed if override.allowed is not None else allowed
            if self.control == "multiselect":
                if not isinstance(override.default, list):
                    raise ValueError(f"参数「{self.key}」在出图方式「{mode_key}」下的默认值必须是数组")
                if any(_value_key(item) not in {_value_key(candidate) for candidate in domain or []} for item in override.default):
                    raise ValueError(f"参数「{self.key}」在出图方式「{mode_key}」下的默认值不在该方式的取值域内")
            elif not _value_matches(self.control, domain, self.allow_custom, override.default):
                raise ValueError(f"参数「{self.key}」在出图方式「{mode_key}」下的默认值「{_render_value(override.default)}」不在该方式的取值域内")
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        """与 ``when`` 同理：线上 JSON 与落库 JSON、与 design D1 的结构三者一致。"""
        return _compact(handler(self))


class ImageWorkbenchCapabilitySchema(BaseModel):
    """能力声明——逐模型可配，代码不内置按模型标识推断的能力表。

    ``params`` 的顺序即前端的呈现顺序；``modes`` 的顺序即出图方式的优先级（多个
    trigger 同时命中时取声明在前的那一个）。
    """

    reference: ImageWorkbenchCapabilityReferenceSchema = Field(default_factory=ImageWorkbenchCapabilityReferenceSchema, description="参考图能力")
    modes: list[ImageWorkbenchModeSchema] = Field(default_factory=list, description="该模型支持的出图方式")
    params: list[ImageWorkbenchParamSchema] = Field(default_factory=list, description="出图参数条目")

    @property
    def coordinate_mode(self) -> ImageWorkbenchModeSchema | None:
        """声明了坐标标记写法的那一种出图方式（今天即交互编辑）。

        **判据是「有没有标记语法」而不是「键是不是叫 interactive_edit」**：规格把靠坐标
        表达位置的出图方式定义为必带占位形态与标记写法的那一类，用标记当判据，键名就不必
        硬编码进代码。工作台的画布入口、选区的坐标区间、最终提示词的渲染三处共用它。
        """
        return next((mode for mode in self.modes if mode.marker is not None), None)

    @model_validator(mode="after")
    def _check_references(self) -> ImageWorkbenchCapabilitySchema:
        """跨条目引用校验：写错要到运行期才暴露，因此在存储入口一次性挡住。"""
        param_keys = [param.key for param in self.params]
        _ensure_unique(param_keys, "参数键")
        mode_keys = [mode.key for mode in self.modes]
        _ensure_unique(mode_keys, "出图方式标识")
        known_params = set(param_keys)
        known_modes = set(mode_keys)
        by_key = {param.key: param for param in self.params}

        for param in self.params:
            if param.when:
                for clause in param.when.all:
                    if clause.param is not None and clause.param not in known_params:
                        raise ValueError(f"参数「{param.key}」的条件依赖引用了不存在的参数「{clause.param}」")
            for mode_key in param.overrides:
                if mode_key not in known_modes:
                    raise ValueError(f"参数「{param.key}」的覆盖引用了不存在的出图方式「{mode_key}」")

        for mode in self.modes:
            if mode.trigger is None:
                continue
            target = by_key.get(mode.trigger.param)
            if target is None:
                raise ValueError(f"出图方式「{mode.key}」的触发条件引用了不存在的参数「{mode.trigger.param}」")
            if not _value_in_domain(target, mode.trigger.eq):
                raise ValueError(f"出图方式「{mode.key}」的触发取值「{_render_value(mode.trigger.eq)}」不在参数「{target.key}」的取值域内")
        return self


def _value_in_domain(param: ImageWorkbenchParamSchema, value: Any) -> bool:
    """取值是否落在某参数**条目自身**声明的取值域内（不含按出图方式的覆盖）。"""
    return _value_matches(param.control, param.allowed, param.allow_custom, value)


def _value_matches(control: str, allowed: list[Any] | None, allow_custom: bool, value: Any) -> bool:
    """取值是否落在取值域内——声明与校验共用这一段，避免两处各写一套判断。

    ``number`` / ``text`` 不声明 ``allowed`` 时是无界的；一旦声明了，就照它拦。
    """
    if control == "switch":
        return isinstance(value, bool)
    if control == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    elif control == "text":
        if not isinstance(value, str):
            return False
    elif allow_custom and _is_custom_value(value):
        return True
    if allowed:
        return _value_key(value) in {_value_key(candidate) for candidate in allowed}
    return control in ("number", "text")


def _domain_text(control: str, allowed: list[Any] | None, allow_custom: bool) -> str:
    """取值域 → 面向用户的短文案（报错与「为何不可用」共用）。"""
    if control == "switch":
        return "是、否"
    text = "、".join(_render_value(item) for item in allowed or [])
    if control == "select" and allow_custom:
        text = f"{text}（或自定义「宽x高」）" if text else "自定义「宽x高」"
    return text or {"number": "任意数字", "text": "任意文本"}.get(control, "（空）")


# ────────────────────────── 提示词渲染 ──────────────────────────


def render_prompt(
    capability: ImageWorkbenchCapabilitySchema,
    prompt: str,
    selections: list[tuple[int, list[int]]],
) -> tuple[str, list[int], list[int]]:
    """按声明的占位形态把提示词里的占位符替换成填好坐标的标记（D4）。

    返回 ``(最终提示词, 引用了却没有坐标的序号, 有坐标却没被引用的序号)``。两个「对不上」
    的清单都由**调用方**决定怎么拒——本函数只负责如实报告，不替调用方决定措辞。

    位置本身就是语义（文档示例是「把 ``<bbox>…</bbox>`` 那个**区域**换成花园」），所以
    是**用户在自己的句子里决定标记出现在哪**、系统只决定写法：服务端不把标记追加到句首
    或句尾，那样做没有任何文档支持，等于赌。

    没有坐标方式的声明时原样返回：这不构成错误，只是没有可渲染的东西（规格：不使用坐标
    的出图方式 MUST NOT 被要求声明标记语法）。
    """
    mode = capability.coordinate_mode
    if mode is None or mode.marker is None:
        # 不声明坐标方式却带着选区来，是「模型不支持交互编辑」，由调用方先行拒绝；
        # 走到这里只能报告「什么都没渲染」。
        return prompt, [], []

    marker = mode.marker
    by_index = dict(selections)
    seen: set[int] = set()
    rendered: set[int] = set()

    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        seen.add(index)
        bbox = by_index.get(index)
        if bbox is None:
            # 引用了却拿不到坐标：原样留着，由调用方拒掉这次提交（绝不静默丢弃）
            return match.group(0)
        rendered.add(index)
        return marker.render(bbox)

    final = marker.pattern().sub(_replace, prompt)
    return final, sorted(seen - rendered), sorted(set(by_index) - rendered)


# ─────────────────────────── 求值 ───────────────────────────


@dataclass(frozen=True)
class ResolvedParam:
    """某参数在**本次请求条件下**的解析结果（可用性 + 生效的取值域与默认值）。"""

    key: str
    control: str
    allowed: list[Any]
    allow_custom: bool
    default: Any
    available: bool
    reason: str | None = None
    override_mode: str | None = None
    """生效覆盖所来自的出图方式；无覆盖时为 None。"""

    def accepts(self, value: Any) -> bool:
        """取值是否落在**本次条件下生效的**取值域内。"""
        return _value_matches(self.control, self.allowed, self.allow_custom, value)

    def domain_text(self) -> str:
        """取值域 → 面向用户的短文案（报错提示用）。"""
        return _domain_text(self.control, self.allowed, self.allow_custom)


@dataclass(frozen=True)
class CapabilityEvaluation:
    """一次求值的完整结果。"""

    params: dict[str, ResolvedParam] = field(default_factory=dict)
    modes: tuple[ImageWorkbenchModeSchema, ...] = ()
    active_mode: str | None = None
    """本次条件下命中的出图方式标识；未命中为 None。"""

    def get(self, key: str) -> ResolvedParam | None:
        return self.params.get(key)

    @property
    def available_keys(self) -> list[str]:
        return [key for key, param in self.params.items() if param.available]


def _eval_clause(clause: ImageWorkbenchWhenClauseSchema, values: dict[str, Any], facts: dict[str, Any]) -> bool:
    """单个条件的求值：事实缺失即不满足（保守置灰）。"""
    if clause.param is not None:
        left = values.get(clause.param, _MISSING)
    else:
        left = facts.get(clause.request, _MISSING)
    if left is _MISSING:
        # 调用方不认识该左值：安全的做法是当作不满足，而不是猜
        return False
    operator, right = clause.operator, clause.right
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    keys = {_value_key(item) for item in right or []}
    if operator == "in":
        return _value_key(left) in keys
    if operator == "not_in":
        return _value_key(left) not in keys
    return False


def _describe_when(
    capability: ImageWorkbenchCapabilitySchema,
    when: ImageWorkbenchWhenSchema,
    values: dict[str, Any],
    facts: dict[str, Any],
) -> str:
    """条件依赖 → 「为何不可用」的说明文案（只列**当前不满足**的那几条）。"""
    labels = {param.key: param.label.zh for param in capability.params}
    parts: list[str] = []
    for clause in when.all:
        if _eval_clause(clause, values, facts):
            continue
        if clause.param is not None:
            left = f"参数「{labels.get(clause.param, clause.param)}」"
        else:
            left = _FACT_LABEL.get(clause.request or "", clause.request or "")
        operator = _OPERATOR_LABEL.get(clause.operator, clause.operator)
        parts.append(f"{left}{operator}「{_render_value(clause.right)}」")
    return "仅当 " + " 且 ".join(parts) + " 时可用"


def evaluate_capability(
    capability: ImageWorkbenchCapabilitySchema,
    *,
    submitted: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> CapabilityEvaluation:
    """求值：算出**当前请求条件下**每个参数的可用性与生效的取值域 / 默认值（D5 第 2 步）。

    - 出图方式由参数值派生（D3）：先用「用户提交值，未提交则条目自身默认值」定出
      命中的方式，再据此应用 ``overrides``；
    - ``when`` 的求值同样用「提交值，未提交则默认值」代入（这是设计 D5 明写的用法：
      未提交的参数以默认值参与求值，不等于要把默认值写进最后的结果）；
    - 不认识的左值（含缺失的请求事实）一律按**不满足**处理。
    """
    submitted = dict(submitted or {})
    facts = dict(facts or {})

    # 第一步：用条目自身默认值定出出图方式（此时还不知道覆盖）
    base_values = {param.key: submitted.get(param.key, param.default) for param in capability.params}
    active_mode: str | None = None
    for mode in capability.modes:
        if mode.trigger is not None and base_values.get(mode.trigger.param, _MISSING) == mode.trigger.eq:
            active_mode = mode.key
            break

    # 第二步：应用该方式对本条目取值域与默认值的覆盖
    effective: dict[str, ResolvedParam] = {}
    for param in capability.params:
        override = param.overrides.get(active_mode) if active_mode else None
        allowed = list(override.allowed) if (override is not None and override.allowed is not None) else list(param.allowed or [])
        default = override.default if (override is not None and override.default is not None) else param.default
        effective[param.key] = ResolvedParam(
            key=param.key,
            control=param.control,
            allowed=allowed,
            allow_custom=param.allow_custom,
            default=default,
            available=True,
            override_mode=active_mode if override is not None else None,
        )

    # 第三步：以「提交值，未提交则生效默认值」代入，逐条求 when
    values = {param.key: submitted.get(param.key, effective[param.key].default) for param in capability.params}
    resolved: dict[str, ResolvedParam] = {}
    for param in capability.params:
        if param.when is None:
            resolved[param.key] = effective[param.key]
            continue
        satisfied = all(_eval_clause(clause, values, facts) for clause in param.when.all)
        resolved[param.key] = ResolvedParam(
            key=param.key,
            control=param.control,
            allowed=effective[param.key].allowed,
            allow_custom=effective[param.key].allow_custom,
            default=effective[param.key].default,
            available=satisfied,
            reason=None if satisfied else _describe_when(capability, param.when, values, facts),
            override_mode=effective[param.key].override_mode,
        )
    return CapabilityEvaluation(params=resolved, modes=tuple(capability.modes), active_mode=active_mode)


def resolve_params(
    capability: ImageWorkbenchCapabilitySchema,
    *,
    submitted: dict[str, Any] | None,
    facts: dict[str, Any] | None,
) -> tuple[dict[str, Any], CapabilityEvaluation]:
    """校验用户**显式提交**的参数并补齐默认值，返回 ``(完整参数集, 求值结果)``（D5 第 3–5 步）。

    顺序不可颠倒：

    1. 求值（``when`` 的求值需要参数值，未提交的以默认值代入）；
    2. 拒绝用户显式提交里的三类值——键不存在于清单、取值超出**当前条件下生效的**
       取值域、该参数在当前条件下不可用；
    3. **只为当前条件下可用、且用户未提交的参数**补默认值。

    第 3 步的限定是关键：``background`` 的可用性要求「参考图恰好一张且带透明通道」，
    若先无条件补齐再校验，**每一次没传参考图的普通请求**都会被补上它的默认值、随即被
    它自己的可用性条件拒掉——一条默认值就能把整条生成路径堵死。
    """
    submitted = dict(submitted or {})
    evaluation = evaluate_capability(capability, submitted=submitted, facts=facts)

    for key, value in submitted.items():
        param = evaluation.params.get(key)
        if param is None:
            raise CustomException(msg=f"参数「{key}」不在该绘图模型的能力声明内", code=400)
        if not param.available:
            raise CustomException(
                msg=f"参数「{param.key}」在当前条件下不可用：{param.reason}" if param.reason else f"参数「{param.key}」在当前条件下不可用",
                code=400,
            )
        if not param.accepts(value):
            raise CustomException(
                msg=f"参数「{param.key}」的取值「{_render_value(value)}」不在该模型支持的取值内，可选：{param.domain_text()}",
                code=400,
            )

    filled: dict[str, Any] = {}
    for key, param in evaluation.params.items():
        if key in submitted:
            filled[key] = submitted[key]
        elif param.available:
            # 用户未提交且当前可用 → 用清单默认值补齐；不可用的参数 MUST NOT 进快照
            filled[key] = param.default
    return filled, evaluation


# ─────────────────────────── 库内 JSON ↔ 结构 ───────────────────────────


def parse_capability(raw: dict | None) -> ImageWorkbenchCapabilitySchema:
    """库内能力声明 JSON → 结构化对象（缺省与超纲字段都收敛到 D1 的定义域）。"""
    if not raw:
        return ImageWorkbenchCapabilitySchema()
    try:
        return ImageWorkbenchCapabilitySchema.model_validate(raw)
    except Exception:  # noqa: BLE001
        # 历史 / 手改数据不符合 D1 结构时按空清单兜底，不因一条脏数据让整个列表 500
        logger.warning("绘图模型能力声明不符合 D1 结构，已按空清单兜底: {}", raw)
        return ImageWorkbenchCapabilitySchema()


def dump_capability(capability: ImageWorkbenchCapabilitySchema) -> dict:
    """结构化对象 → 库内 JSON（``in`` 等别名原样落库，空值不落）。"""
    return capability.model_dump(by_alias=True, exclude_none=True)


__all__ = [
    "CapabilityEvaluation",
    "ImageWorkbenchCapabilityReferenceSchema",
    "ImageWorkbenchCapabilitySchema",
    "ImageWorkbenchLabelSchema",
    "ImageWorkbenchModeMarkerSchema",
    "ImageWorkbenchModeSchema",
    "ImageWorkbenchModeTriggerSchema",
    "ImageWorkbenchParamOverrideSchema",
    "ImageWorkbenchParamSchema",
    "ImageWorkbenchWhenClauseSchema",
    "ImageWorkbenchWhenSchema",
    "ParamControl",
    "ParamSource",
    "RequestFact",
    "ResolvedParam",
    "dump_capability",
    "evaluate_capability",
    "parse_capability",
    "render_prompt",
    "resolve_params",
]
