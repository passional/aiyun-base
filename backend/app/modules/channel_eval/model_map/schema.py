"""渠道评估 - 模型映射 Pydantic 模型（spec: channel-eval/model-mapping）"""

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema


def _clean_aliases(value: list[str]) -> list[str]:
    """去空白、丢空串、去重且保序。"""
    return list(dict.fromkeys(item.strip() for item in value if item and item.strip()))


class CanonicalModelCreateSchema(BaseModel):
    """规范模型创建/编辑参数（别名以整体提交，服务端做增删同步）"""

    display_name: str = Field(..., min_length=1, max_length=100, description="显示名(报告中呈现)")
    family: str | None = Field(default=None, max_length=50, description="系列(如 Claude/GPT)")
    remark: str | None = Field(default=None, max_length=500, description="备注")
    aliases: list[str] = Field(default_factory=list, description="渠道模型串列表")

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("显示名不能为空")
        return value.strip()

    @field_validator("family", "remark")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, value: list[str]) -> list[str]:
        return _clean_aliases(value)


class CanonicalModelUpdateSchema(CanonicalModelCreateSchema):
    """规范模型编辑参数"""


class ConfirmSuggestionSchema(BaseModel):
    """确认一条自动建议：一组串落成一个规范模型"""

    display_name: str = Field(..., min_length=1, max_length=100, description="显示名")
    family: str | None = Field(default=None, max_length=50, description="系列(如 Claude/GPT)")
    aliases: list[str] = Field(..., min_length=2, description="该组包含的渠道模型串")

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("显示名不能为空")
        return value.strip()

    @field_validator("family")
    @classmethod
    def _strip_family(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, value: list[str]) -> list[str]:
        cleaned = _clean_aliases(value)
        if len(cleaned) < 2:
            raise ValueError("确认建议至少需要两个不同的渠道模型串")
        return cleaned


class CanonicalModelQueryParam(BaseQueryParam):
    """规范模型查询参数"""

    display_name: str | None = Field(default=None, max_length=100, description="显示名", json_schema_extra={"q": "like"})
    family: str | None = Field(default=None, max_length=50, description="系列", json_schema_extra={"q": "like"})


class ModelAliasOutSchema(BaseSchema):
    """别名响应模型"""

    canonical_id: int = Field(description="规范模型ID")
    alias: str = Field(description="渠道模型串")
    source: str = Field(description="来源(manual/auto)")


class CanonicalModelOutSchema(BaseSchema):
    """规范模型响应模型"""

    display_name: str = Field(description="显示名")
    family: str | None = Field(default=None, description="系列")
    remark: str | None = Field(default=None, description="备注")
    status: int = Field(default=0, description="状态(0:正常 1:停用)")
    aliases: list[ModelAliasOutSchema] = Field(default_factory=list, description="别名列表")


class AliasObservationSchema(BaseModel):
    """一个渠道模型串在哪儿出现过（供人工判断是否同族）"""

    alias: str = Field(description="渠道模型串")
    platforms: list[str] = Field(default_factory=list, description="出现过的平台名")
    channels: list[str] = Field(default_factory=list, description="出现过的渠道名")
    has_suggestion: bool = Field(default=False, description="是否落在某条自动建议里")


class SuggestionGroupSchema(BaseModel):
    """一条「疑似同族」建议"""

    suggested_name: str = Field(description="建议的规范模型显示名")
    normalized: str = Field(description="归一化键")
    observations: list[AliasObservationSchema] = Field(default_factory=list, description="组内各串及其来源")
    reason_codes: list[str] = Field(default_factory=list, description="判断依据代码(prefix/separator/case/date)")
    reasons: list[str] = Field(default_factory=list, description="判断依据中文标签")


class UnmappedModelSchema(BaseModel):
    """未确认归属的渠道模型串清单"""

    observations: list[AliasObservationSchema] = Field(default_factory=list, description="未确认串列表")
    suggestion_count: int = Field(default=0, description="其中落在建议里的条数")
