"""绘图工作台 - 配置 Pydantic 模型（设计 D3 能力声明 / D9 API 契约）

凭证安全约定（spec: service-config / 绘图服务登记与凭证管理）：
- 创建/编辑入参接收**明文**凭证，字段名刻意取 ``api_key``——路由层
  ``OperationLogRoute._redact_sensitive`` 的敏感键集合已含该名，操作日志自动落 ``******``；
- 任何出参**不回显明文或密文**，仅回显脱敏占位（``api_key_masked``）；
- 编辑时留空表示沿用原凭证。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.base_schema import BaseQueryParam, BaseSchema

Dialect = Literal["ark", "openai"]
"""接入方式：ark=火山方舟（参考图与提示词同请求体），openai=OpenAI 兼容（参考图走 /images/edits 多部分表单）"""

_DIALECT_LABEL: dict[str, str] = {"ark": "火山方舟", "openai": "OpenAI 兼容"}


class ImageWorkbenchServiceCreateSchema(BaseModel):
    """绘图服务创建参数"""

    name: str = Field(..., min_length=1, max_length=100, description="绘图服务名称")
    dialect: Dialect = Field(default="ark", description="接入方式(ark:火山方舟 openai:OpenAI兼容)")
    base_url: str = Field(..., min_length=1, max_length=255, description="完整API前缀(含/v1或/api/plan/v3)")
    api_key: str | None = Field(default=None, max_length=512, description="调用凭证明文")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, max_length=500, description="备注")

    @field_validator("name", "base_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("不能为空")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        # base_url 填**完整 API 前缀**（ADR-0005）：只去掉结尾斜杠，
        # 多级路径段（如 /api/plan/v3）原样保留，不推断也不补全版本段
        return value.rstrip("/")


class ImageWorkbenchServiceUpdateSchema(ImageWorkbenchServiceCreateSchema):
    """绘图服务编辑参数（``api_key`` 留空/缺省表示保留原值）"""

    api_key: str | None = Field(default=None, max_length=512, description="调用凭证明文(留空保留原值)")


class ImageWorkbenchServiceOutSchema(BaseSchema):
    """绘图服务响应模型（凭证仅脱敏回显）"""

    name: str = Field(description="绘图服务名称")
    dialect: str = Field(description="接入方式")
    dialect_label: str | None = Field(default=None, description="接入方式显示名")
    base_url: str = Field(description="完整API前缀")
    status: int = Field(description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, description="备注")
    api_key_masked: str | None = Field(default=None, description="凭证脱敏占位")
    has_api_key: bool = Field(default=False, description="是否已配置凭证")


class ImageWorkbenchServiceQueryParam(BaseQueryParam):
    """绘图服务查询参数"""

    name: str | None = Field(default=None, max_length=100, description="绘图服务名称", json_schema_extra={"q": "like"})
    dialect: str | None = Field(default=None, max_length=16, description="接入方式", json_schema_extra={"q": "eq"})
    status: int | None = Field(default=None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})


def dialect_label(dialect: str | None) -> str | None:
    """接入方式取值 → 界面显示名。"""
    if not dialect:
        return None
    return _DIALECT_LABEL.get(dialect, dialect)


class ImageWorkbenchTestConnectionSchema(BaseModel):
    """连通性测试参数（用「所填值」发起只读探测；编辑态可回退库内值）

    ``api_key`` 同样命中操作日志的敏感键集合，探测请求体不会把凭证明文带进日志。
    """

    service_id: int | None = Field(default=None, ge=1, description="绘图服务ID(编辑态用于回退库内值)")
    dialect: Dialect | None = Field(default=None, description="接入方式(缺省取库内值)")
    base_url: str | None = Field(default=None, max_length=255, description="完整API前缀(留空回退库内值)")
    api_key: str | None = Field(default=None, max_length=512, description="调用凭证明文(编辑态留空回退库内凭证)")
    model_id: str | None = Field(default=None, max_length=128, description="可选：核对上游模型列表中是否有该标识")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value:
            return None
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return value


class ImageWorkbenchTestConnectionResultSchema(BaseModel):
    """连通性测试结果（成功附上游模型标识供核对，失败给明确原因）"""

    success: bool = Field(description="是否连通成功")
    message: str = Field(description="成功说明或失败原因")
    dialect: str | None = Field(default=None, description="实际使用的接入方式")
    base_url: str | None = Field(default=None, description="实际使用的完整API前缀")
    model_ids: list[str] = Field(default_factory=list, description="上游返回的模型标识")
    model_found: bool | None = Field(default=None, description="待核对模型是否在上游列表中(未指定 model_id 时为 None)")


# ────────────────────── 绘图模型（两级结构的下级） ───────────────────────


class ImageWorkbenchCapabilitySchema(BaseModel):
    """能力声明（设计 D3）——逐模型可配，代码不内置按模型标识推断的能力表。

    尺寸取值**不做归一化翻译**：上游 A 收档位字符串（``1K``/``1.5K``）、上游 B 收固定
    像素枚举（``1024x1024``），硬套一层"方/横/竖"映射会在像素与宽高比约束上立刻碎掉。
    因此 ``sizes`` 就是该模型可选的取值字面量列表，原样透传给上游。

    ``watermark_default`` 默认 **false**：上游默认是 true，不显式传 false 会给成品图
    打上「AI 生成」水印。
    """

    sizes: list[str] = Field(default_factory=list, description='可选尺寸取值(档位字符串或"宽x高"，空数组表示该模型无尺寸概念)')
    allow_custom_size: bool = Field(default=False, description='是否额外允许填自定义"宽x高"')
    support_reference: bool = Field(default=True, description="是否支持参考图")
    max_reference_images: int = Field(default=10, ge=0, description="参考图张数上限")
    output_formats: list[str] = Field(default_factory=lambda: ["png", "jpeg"], description="可选输出格式")
    default_output_format: str = Field(default="png", description="默认输出格式")
    watermark_default: bool = Field(default=False, description="本次是否加水印的默认值")
    extra_params: dict = Field(default_factory=dict, description="原样透传给上游的默认参数")

    @field_validator("sizes", "output_formats")
    @classmethod
    def _strip_items(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("取值不得重复")
        return cleaned

    @model_validator(mode="after")
    def _check_default_format(self) -> "ImageWorkbenchCapabilitySchema":
        if self.output_formats and self.default_output_format not in self.output_formats:
            raise ValueError(f"默认输出格式 {self.default_output_format!r} 不在可选输出格式中")
        if not self.support_reference and self.max_reference_images:
            # 不支持参考图的模型带上限没有意义，直接归一为 0，避免前端误显示入口
            self.max_reference_images = 0
        return self


class ImageWorkbenchModelCreateSchema(BaseModel):
    """绘图模型创建参数"""

    service_id: int = Field(..., ge=1, description="所属绘图服务ID")
    model_id: str = Field(..., min_length=1, max_length=128, description="上游模型标识(原样提交，不做规范化)")
    display_name: str = Field(..., min_length=1, max_length=100, description="工作台展示名")
    capability: ImageWorkbenchCapabilitySchema = Field(default_factory=ImageWorkbenchCapabilitySchema, description="能力声明")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, max_length=500, description="备注")

    @field_validator("model_id", "display_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("不能为空")
        # model_id 原样保留（含大小写与日期后缀）：只校验非空，不做 strip/规范化
        return value

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("remark")
    @classmethod
    def _strip_remark(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class ImageWorkbenchModelUpdateSchema(ImageWorkbenchModelCreateSchema):
    """绘图模型编辑参数（能力声明整体覆盖）"""


class ImageWorkbenchModelOutSchema(BaseSchema):
    """绘图模型响应模型（能力声明解析为结构化对象，缺省字段补默认值）"""

    service_id: int = Field(description="所属绘图服务ID")
    service_name: str | None = Field(default=None, description="所属绘图服务名称")
    model_id: str = Field(description="上游模型标识(原样)")
    display_name: str = Field(description="工作台展示名")
    capability: ImageWorkbenchCapabilitySchema = Field(description="能力声明")
    status: int = Field(description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, description="备注")


class ImageWorkbenchModelQueryParam(BaseQueryParam):
    """绘图模型查询参数"""

    service_id: int | None = Field(default=None, ge=1, description="所属绘图服务ID", json_schema_extra={"q": "eq"})
    model_id: str | None = Field(default=None, max_length=128, description="模型标识", json_schema_extra={"q": "like"})
    display_name: str | None = Field(default=None, max_length=100, description="展示名", json_schema_extra={"q": "like"})
    status: int | None = Field(default=None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})
