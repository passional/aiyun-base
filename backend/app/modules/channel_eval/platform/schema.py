"""渠道评估 - 平台管理 Pydantic 模型

凭证安全约定：
- 创建/编辑入参接收**明文**凭证（``admin_key`` / ``test_key``），服务层加密落库；
- 任何出参**不回显明文或密文**，仅回显脱敏占位（``*_masked``）。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.base_schema import BaseQueryParam, BaseSchema

PlatformType = Literal["admin", "user"]
"""平台类型：admin=new-api 管理型（可拉渠道目录/管理日志），user=仅用户级（基准渠道）"""


class ChannelEvalPlatformCreateSchema(BaseModel):
    """平台创建参数"""

    name: str = Field(..., min_length=1, max_length=100, description="平台名称")
    type: PlatformType = Field(default="admin", description="平台类型(admin:new-api管理型 user:仅用户级)")
    base_url: str = Field(..., min_length=1, max_length=255, description="平台基础地址")
    admin_key: str | None = Field(default=None, max_length=512, description="管理凭证明文(仅管理型)")
    admin_user_id: str | None = Field(default=None, max_length=64, description="管理用户ID(New-Api-User请求头, 可选)")
    test_key: str = Field(..., min_length=1, max_length=512, description="测试凭证明文")
    is_baseline: bool = Field(default=False, description="是否基准渠道")
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
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate_admin_key(self) -> "ChannelEvalPlatformCreateSchema":
        """管理型平台必须提供管理凭证。"""
        if self.type == "admin" and not (self.admin_key and self.admin_key.strip()):
            raise ValueError("new-api 管理型平台必须填写管理凭证")
        return self


class ChannelEvalPlatformUpdateSchema(BaseModel):
    """平台编辑参数（凭证留空/缺省表示保留原值）"""

    name: str = Field(..., min_length=1, max_length=100, description="平台名称")
    type: PlatformType = Field(default="admin", description="平台类型")
    base_url: str = Field(..., min_length=1, max_length=255, description="平台基础地址")
    admin_key: str | None = Field(default=None, max_length=512, description="管理凭证明文(留空保留原值)")
    admin_user_id: str | None = Field(default=None, max_length=64, description="管理用户ID(New-Api-User请求头, 留空保留原值)")
    test_key: str | None = Field(default=None, max_length=512, description="测试凭证明文(留空保留原值)")
    is_baseline: bool = Field(default=False, description="是否基准渠道")
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
        return value.rstrip("/")


class ChannelEvalPlatformOutSchema(BaseSchema):
    """平台响应模型（凭证仅脱敏回显）"""

    name: str = Field(description="平台名称")
    type: str = Field(description="平台类型")
    base_url: str = Field(description="平台基础地址")
    admin_user_id: str | None = Field(default=None, description="管理用户ID(New-Api-User请求头)")
    is_baseline: bool = Field(description="是否基准渠道")
    status: int = Field(description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, description="备注")
    admin_key_masked: str | None = Field(default=None, description="管理凭证脱敏占位")
    test_key_masked: str | None = Field(default=None, description="测试凭证脱敏占位")


class ChannelEvalPlatformQueryParam(BaseQueryParam):
    """平台查询参数"""

    name: str | None = Field(default=None, max_length=100, description="平台名称", json_schema_extra={"q": "like"})
    type: str | None = Field(default=None, max_length=16, description="平台类型", json_schema_extra={"q": "eq"})
    is_baseline: bool | None = Field(default=None, description="是否基准渠道", json_schema_extra={"q": "eq"})
    status: int | None = Field(default=None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})


class ChannelEvalTestConnectionSchema(BaseModel):
    """测试连接参数（用「所填凭证」发起只读请求；编辑态可回退库内凭证）"""

    type: PlatformType | None = Field(default=None, description="平台类型(缺省取库内平台类型)")
    base_url: str | None = Field(default=None, max_length=255, description="平台基础地址(留空回退库内地址)")
    admin_key: str | None = Field(default=None, max_length=512, description="管理凭证明文(编辑态留空回退库内凭证)")
    admin_user_id: str | None = Field(default=None, max_length=64, description="管理用户ID(编辑态留空回退库内值)")
    test_key: str | None = Field(default=None, max_length=512, description="测试凭证明文(编辑态留空回退库内凭证)")
    platform_id: int | None = Field(default=None, ge=1, description="平台ID(编辑态用于回退库内凭证)")

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


class ChannelEvalTestConnectionResultSchema(BaseModel):
    """测试连接结果"""

    success: bool = Field(description="是否连接成功")
    message: str = Field(description="成功说明或失败原因")


class ChannelEvalChannelItemSchema(BaseModel):
    """渠道目录条目（实时拉取，不持久化）"""

    channel_id: int = Field(description="渠道编号")
    name: str = Field(description="渠道名称")
    status: int = Field(description="new-api渠道状态(1:启用 2:手动停用 3:自动停用)")
    enabled: bool = Field(description="是否启用")
    groups: list[str] = Field(default_factory=list, description="分组列表")
    priority: int = Field(description="优先级")
    weight: int = Field(description="权重")
    models: list[str] = Field(default_factory=list, description="支持的模型列表")
    is_standby: bool = Field(default=False, description="可达性提示：是否存在同分组同模型的高优先级渠道(备用渠道)")
    standby_hint: str | None = Field(default=None, description="备用渠道提示文案")


class ChannelEvalChannelCatalogSchema(BaseModel):
    """渠道目录（实时拉取结果）"""

    platform_id: int = Field(description="平台ID")
    platform_name: str = Field(description="平台名称")
    is_baseline: bool = Field(description="是否基准渠道(整平台视为一条隐式渠道)")
    channels: list[ChannelEvalChannelItemSchema] = Field(default_factory=list, description="渠道列表")
    pulled_at: str = Field(description="拉取时间(ISO格式)")
