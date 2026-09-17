"""绘图工作台 - 绘图任务数据模型（设计 D2：``image_workbench_job``）

一次单图生成的执行单元：提示词 + 参数快照 + 状态 / 产物 / 用量。
``UserMixin`` 提供 ``created_id``，数据范围过滤依赖它（无需自建 ``user_id``）。

``model_id`` 是提交时的**标识快照**，防上游标识漂移影响历史展示；``image_path``
存产物在存储节点中的相对路径（成功时非空）——**绝不存上游 URL**（上游地址 24 小时失效）。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin


class ImageWorkbenchJobModel(ModelMixin, UserMixin):
    """绘图任务表"""

    __tablename__: str = "image_workbench_job"
    __table_args__ = (
        Index("ux_image_workbench_job_active_owner_id", "active_owner_id", unique=True),
        {"comment": "绘图工作台-绘图任务表"},
    )

    # 引用保护（设计 D8）：被绘图任务引用的绘图服务 / 绘图模型拒绝删除，
    # 由服务层先行拦截并提示可停用；FK 取 RESTRICT 作为数据库侧兜底。
    service_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("image_workbench_service.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="绘图服务ID",
    )
    model_pk: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("image_workbench_model.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="绘图模型ID",
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型标识快照(防上游标识漂移)")
    # 活跃槽位：pending/running 时等于 created_id，终态时为 NULL。数据库唯一约束把
    # “每用户最多一个进行中任务”变成原子不变量，避免两个并发请求同时通过先查后写。
    active_owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="进行中任务的用户占位ID(终态为空)")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, comment="提示词")
    params_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="参数快照(size/output_format/watermark/reference_images)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True, comment="状态(pending/running/success/failed/cancelled)")
    error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="失败原因")
    usage_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="用量明细(上游usage原样+生成张数)")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="任务耗时(毫秒)")
    image_path: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True, comment="产物在存储节点中的相对路径(成功时非空)")
    image_size: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True, comment="产物宽高(如2048x2048)")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="执行开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="执行结束时间")

    __mapper_args__ = {"confirm_deleted_rows": False}
