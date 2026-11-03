"""语音工作台 - 转写任务 Pydantic 模型。

上游返回的分段以毫秒记录起止时间；列表与详情出参把 ``segments_json`` 暴露为
``segments``，把 ``usage_json`` 暴露为 ``usage``，与前端契约一一对应。
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema, DateTimeStr

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

_STATUS_LABEL: dict[str, str] = {
    STATUS_RUNNING: "识别中",
    STATUS_SUCCESS: "识别成功",
    STATUS_FAILED: "识别失败",
}


def status_label(status: str | None) -> str | None:
    """任务状态取值 → 界面显示名。"""
    if not status:
        return None
    return _STATUS_LABEL.get(status, status)


class VoiceWorkbenchJobModelOptionSchema(BaseModel):
    """工作台可选语音识别模型（模型启用、服务启用且服务已配置凭证）"""

    id: int = Field(description="语音识别模型ID")
    service_id: int = Field(description="所属语音服务ID")
    service_name: str = Field(description="所属语音服务名称")
    display_name: str = Field(description="工作台展示名")
    model_name: str = Field(description="上游模型标识")
    max_file_mb: int = Field(description="单文件大小上限(MB)")
    max_duration_sec: int = Field(description="音频时长上限(秒)")


class VoiceWorkbenchSegmentSchema(BaseModel):
    """一个转写分段（毫秒）"""

    start_time: int = Field(description="开始时间(毫秒)")
    end_time: int = Field(description="结束时间(毫秒)")
    text: str = Field(description="分段文本")


class VoiceWorkbenchJobOutSchema(BaseSchema):
    """转写任务响应模型（列表与详情共用）"""

    service_id: int = Field(description="语音服务ID")
    service_name: str | None = Field(default=None, description="语音服务名称")
    model_pk: int = Field(description="语音识别模型ID")
    model_name: str = Field(description="上游模型标识快照")
    model_display_name: str | None = Field(default=None, description="语音识别模型展示名")
    original_filename: str = Field(description="原始文件名")
    file_size: int = Field(description="上传音频字节数")
    duration_sec: int = Field(description="音频时长(秒)")
    text: str | None = Field(default=None, description="转写文本")
    segments: list[VoiceWorkbenchSegmentSchema] = Field(default_factory=list, description="转写分段(毫秒)")
    status: str = Field(description="状态(running/success/failed)")
    status_label: str | None = Field(default=None, description="状态显示名")
    error: str | None = Field(default=None, description="失败原因")
    elapsed_ms: int | None = Field(default=None, description="识别耗时(毫秒)")
    usage: dict[str, Any] | None = Field(default=None, description="上游用量")
    started_at: DateTimeStr | None = Field(default=None, description="开始时间")
    finished_at: DateTimeStr | None = Field(default=None, description="结束时间")
    created_id: int | None = Field(default=None, description="提交人ID")


class VoiceWorkbenchJobQueryParam(BaseQueryParam):
    """转写任务查询参数（可见范围由服务层强制按 created_id 过滤）"""

    original_filename: str | None = Field(default=None, max_length=255, description="原始文件名", json_schema_extra={"q": "like"})
    status: str | None = Field(default=None, max_length=16, description="状态", json_schema_extra={"q": "eq"})
    model_pk: int | None = Field(default=None, ge=1, description="语音识别模型ID", json_schema_extra={"q": "eq"})

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str | None) -> str | None:
        if value is not None and value not in _STATUS_LABEL:
            raise ValueError("状态只允许 running/success/failed")
        return value
