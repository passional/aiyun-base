"""语音工作台 - 配置 Pydantic 模型。

凭证约定沿用绘图工作台：入参字段名为 ``api_key``（命中操作日志敏感键集合），
落库前加密；任何出参只回显 ``api_key_masked`` 与 ``has_api_key``；编辑留空沿用原值。
"""

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema


class VoiceWorkbenchServiceCreateSchema(BaseModel):
    """语音服务创建参数"""

    name: str = Field(..., min_length=1, max_length=100, description="语音服务名称")
    api_key: str | None = Field(default=None, max_length=512, description="调用凭证明文")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, max_length=500, description="备注")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("不能为空")
        return value.strip()

    @field_validator("remark")
    @classmethod
    def _strip_remark(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class VoiceWorkbenchServiceUpdateSchema(VoiceWorkbenchServiceCreateSchema):
    """语音服务编辑参数（``api_key`` 留空/缺省表示保留原值）"""

    api_key: str | None = Field(default=None, max_length=512, description="调用凭证明文(留空保留原值)")


class VoiceWorkbenchServiceOutSchema(BaseSchema):
    """语音服务响应模型（凭证仅脱敏回显）"""

    name: str = Field(description="语音服务名称")
    status: int = Field(description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, description="备注")
    api_key_masked: str | None = Field(default=None, description="凭证脱敏占位")
    has_api_key: bool = Field(default=False, description="是否已配置凭证")


class VoiceWorkbenchServiceQueryParam(BaseQueryParam):
    """语音服务查询参数"""

    name: str | None = Field(default=None, max_length=100, description="语音服务名称", json_schema_extra={"q": "like"})
    status: int | None = Field(default=None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})


class VoiceWorkbenchModelCreateSchema(BaseModel):
    """语音识别模型创建参数"""

    service_id: int = Field(..., ge=1, description="所属语音服务ID")
    display_name: str = Field(..., min_length=1, max_length=100, description="工作台展示名")
    model_name: str = Field(..., min_length=1, max_length=128, description="上游模型标识(原样提交)")
    resource_id: str = Field(..., min_length=1, max_length=128, description="上游资源标识")
    endpoint_url: str = Field(..., min_length=1, max_length=512, description="完整WebSocket端点URL")
    max_file_mb: int = Field(..., ge=1, le=500, description="单文件大小上限(MB)")
    max_duration_sec: int = Field(..., ge=1, le=1800, description="音频时长上限(秒)")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, max_length=500, description="备注")

    @field_validator("display_name", "model_name", "resource_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("不能为空")
        # model_name 原样保留：管理员填什么，请求体就发什么
        return value

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("resource_id", "endpoint_url")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("endpoint_url")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        if not (value.startswith("ws://") or value.startswith("wss://")):
            raise ValueError("endpoint_url 必须以 ws:// 或 wss:// 开头")
        return value

    @field_validator("remark")
    @classmethod
    def _strip_remark(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class VoiceWorkbenchModelUpdateSchema(VoiceWorkbenchModelCreateSchema):
    """语音识别模型编辑参数"""


class VoiceWorkbenchModelOutSchema(BaseSchema):
    """语音识别模型响应模型"""

    service_id: int = Field(description="所属语音服务ID")
    service_name: str | None = Field(default=None, description="所属语音服务名称")
    display_name: str = Field(description="工作台展示名")
    model_name: str = Field(description="上游模型标识(原样)")
    resource_id: str = Field(description="上游资源标识")
    endpoint_url: str = Field(description="完整WebSocket端点URL")
    max_file_mb: int = Field(description="单文件大小上限(MB)")
    max_duration_sec: int = Field(description="音频时长上限(秒)")
    status: int = Field(description="状态(0:启用 1:停用)")
    remark: str | None = Field(default=None, description="备注")


class VoiceWorkbenchModelQueryParam(BaseQueryParam):
    """语音识别模型查询参数"""

    service_id: int | None = Field(default=None, ge=1, description="所属语音服务ID", json_schema_extra={"q": "eq"})
    display_name: str | None = Field(default=None, max_length=100, description="工作台展示名", json_schema_extra={"q": "like"})
    model_name: str | None = Field(default=None, max_length=128, description="上游模型标识", json_schema_extra={"q": "like"})
    status: int | None = Field(default=None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})
