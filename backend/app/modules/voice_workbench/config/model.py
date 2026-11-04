"""语音工作台 - 配置数据模型。

语音服务是独立上游实体（ADR-0010）：只持有名称、调用凭证、状态与备注；语音识别
模型挂在服务下，携带上游模型标识、资源标识、完整 WebSocket 端点与识别限额。
两张表只继承 ``ModelMixin``，配置不参与数据范围过滤；被引用时由服务层拒绝删除
（FK 取 RESTRICT 作为数据库侧兜底）。
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin


class VoiceWorkbenchServiceModel(ModelMixin):
    """语音服务表（管理员配置的上游线路，一条服务一个调用凭证）"""

    __tablename__: str = "voice_workbench_service"
    __table_args__: dict[str, str] = {"comment": "语音工作台-语音服务表"}

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, comment="语音服务名称")
    api_key_enc: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True, comment="调用凭证密文(Fernet)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:启用 1:停用)")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="备注")

    models: Mapped[list["VoiceWorkbenchModelModel"]] = relationship("VoiceWorkbenchModelModel", back_populates="service")

    __mapper_args__ = {"confirm_deleted_rows": False}


class VoiceWorkbenchModelModel(ModelMixin):
    """语音识别模型表（挂在语音服务下）

    ``model_name`` 是上游请求体里的模型标识，原样存取不做规范化：它是管理员从上游
    文档抄下来的值，改写成别的形态只会让请求命中不了目标模型。
    """

    __tablename__: str = "voice_workbench_model"
    __table_args__: dict[str, str] = {"comment": "语音工作台-语音识别模型表"}

    service_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("voice_workbench_service.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="语音服务ID",
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="工作台展示名")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="上游模型标识(原样提交)")
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="上游资源标识(X-Api-Resource-Id)")
    endpoint_url: Mapped[str] = mapped_column(String(512), nullable=False, comment="完整WebSocket端点URL")
    max_file_mb: Mapped[int] = mapped_column(Integer, nullable=False, comment="单文件大小上限(MB)")
    max_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, comment="音频时长上限(秒)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:启用 1:停用)")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="备注")

    service: Mapped[VoiceWorkbenchServiceModel] = relationship("VoiceWorkbenchServiceModel", back_populates="models")

    __mapper_args__ = {"confirm_deleted_rows": False}
