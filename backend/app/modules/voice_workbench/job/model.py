"""语音工作台 - 转写任务数据模型。

一次转写是同步请求的持久化证据：原文件名、字节数、时长、服务与模型快照、转写文本、
分段、状态、耗时、错误与上游用量。**不存音频路径**——原始音频从不落盘（ADR-0011）。

``UserMixin`` 提供 ``created_id``；转写任务的可见范围由服务层显式按 ``created_id``
过滤，超管也不越权。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import JSONType, ModelMixin, UserMixin


class VoiceWorkbenchJobModel(ModelMixin, UserMixin):
    """语音转写任务表"""

    __tablename__: str = "voice_workbench_job"
    __table_args__: dict[str, str] = {"comment": "语音工作台-转写任务表"}

    # 引用保护：被任务引用的服务/模型拒绝删除，由服务层先行拦截；FK 取 RESTRICT 兜底
    service_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("voice_workbench_service.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="语音服务ID",
    )
    model_pk: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("voice_workbench_model.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="语音识别模型ID",
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="上游模型标识快照")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="上传音频字节数(转码后)")
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, comment="音频时长(秒，无法解析时为0)")
    text: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="转写文本")
    segments_json: Mapped[list | None] = mapped_column(JSONType, default=None, nullable=True, comment="转写分段(毫秒起止时间与文本)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True, comment="状态(running/success/failed)")
    error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="失败原因")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="识别耗时(毫秒)")
    usage_json: Mapped[dict | None] = mapped_column(JSONType, default=None, nullable=True, comment="上游用量(上游audio_info原样)")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="结束时间")

    __mapper_args__ = {"confirm_deleted_rows": False}
