"""绘图工作台 - 绘图任务与产物数据模型（设计 D2/D7）

一次生成的执行单元：提示词 + 参数快照 + 状态 / 用量；**产物独立建表**。
``UserMixin`` 提供 ``created_id``，数据范围过滤依赖它（无需自建 ``user_id``）。

``model_id`` 是提交时的**标识快照**，防上游标识漂移影响历史展示；``params_json`` 是
**完整且已解析的参数集快照**（等于实际发给上游的参数集）。

**产物为什么独立成表**（D7）：一次任务可以产出底图 + 最多十六个图层，把多产物塞进
JSON 列会让「按图层建立引用」「按图层查询」无处落脚，下载与删除也只能整包处理。
``image_workbench_job.image_path`` / ``image_size`` 因此删除——两处记录「这次的产物
是哪个文件」必然漂移。``file_path`` 存产物在存储节点中的相对路径——**绝不存上游 URL**
（上游地址 24 小时失效）。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin, UserMixin

# 引用表名与唯一约束名（删除保护按产物反查时用到，取成常量免得两处各写一遍字面量）
JOB_ARTIFACT_REF_TABLE = "image_workbench_job_artifact_ref"
JOB_ARTIFACT_REF_UNIQUE = "ux_image_workbench_job_artifact_ref_job_artifact"


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
    prompt: Mapped[str] = mapped_column(Text, nullable=False, comment="提示词(用户原话，含区域占位符)")
    # 最终提示词是**快照**而非按需重算（design D5）：标记写法一旦改过，拿原话重渲染出的
    # 文本就不等于当时真正发出去的那段；重试也以它为准（D11），不重新渲染。
    prompt_final: Mapped[str] = mapped_column(Text, nullable=False, comment="最终提示词(实际发给上游的文本，含坐标标记)")
    selections_json: Mapped[list | None] = mapped_column(JSON, default=None, nullable=True, comment="选区快照(序号、坐标与来源产物标识)")
    params_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="参数快照(等于实际发给上游的参数集)")
    reference_images_json: Mapped[list | None] = mapped_column(JSON, default=None, nullable=True, comment="参考图在本系统内的相对路径数组")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True, comment="状态(pending/running/success/failed/cancelled)")
    error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="失败原因")
    usage_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="用量明细(上游usage原样+生成张数)")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="任务耗时(毫秒)")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="执行开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="执行结束时间")

    artifacts: Mapped[list["ImageWorkbenchArtifactModel"]] = relationship(
        "ImageWorkbenchArtifactModel",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __mapper_args__ = {"confirm_deleted_rows": False}


class ImageWorkbenchArtifactModel(ModelMixin):
    """绘图产物表（设计 D7）

    ``z_index`` **可空**：底图为 ``NULL``，图层存上游给的层级值。底图不是图层
    （见 ``CONTEXT.md``），用「无层级」而非「层级 0」表达这一点——查询「取底图」
    就是 ``z_index IS NULL``。

    ``bounding_box_json`` 存上游的位置框（绝对与归一化两种表示都留），交互编辑将直接
    取归一化那份作为坐标标记——两者是同一个 0–999 坐标系。
    """

    __tablename__: str = "image_workbench_artifact"
    __table_args__: tuple = ({"comment": "绘图工作台-绘图产物表"},)

    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("image_workbench_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="绘图任务ID",
    )
    z_index: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="层级(底图为空)")
    name: Mapped[str | None] = mapped_column(String(128), default=None, nullable=True, comment="图层名称")
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="图层描述")
    bounding_box_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="图层在底图中所占的位置(绝对与归一化两种表示)")
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, comment="产物在存储节点中的相对路径")
    image_size: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True, comment="产物宽高(如2048x2048)")
    output_format: Mapped[str | None] = mapped_column(String(16), default=None, nullable=True, comment="产物格式(png/jpeg/webp)")

    job: Mapped[ImageWorkbenchJobModel] = relationship("ImageWorkbenchJobModel", back_populates="artifacts")

    __mapper_args__ = {"confirm_deleted_rows": False}

    @property
    def is_base(self) -> bool:
        """是否为底图（无层级即底图）。"""
        return self.z_index is None


class ImageWorkbenchJobArtifactRefModel(ModelMixin):
    """绘图任务 → 产物的引用关系（design D6/D7）。

    **两类引用写在同一张表里**：选区来源（``selections[].artifact_id``，用户点选了某一层）
    与画面来源（合成图用到的那次生成的全部产物）。从删除保护的角度它们没有区别——都是
    「这张产物被谁用过」，因此不需要两张表，也不需要给引用标类型。

    **为什么是关联表而不是 JSON 列**：删除保护是热路径，每次删任务都要问「我的产物有没有
    被别人引用」。反向查询（按 ``artifact_id`` 找引用它的任务）放 JSON 列只能全表扫，
    这里靠 ``artifact_id`` 索引一次解决。

    **为什么 ``job_id`` 与 ``artifact_id`` 都是 CASCADE**：引用行自身没有独立价值，它存在
    只为回答「谁用过谁」。任务被删（含软删时由服务层显式清掉引用行）或产物行被删时，对应的
    引用行不该留下来把一张早已不存在的产物继续标成「被引用」。
    """

    __tablename__: str = JOB_ARTIFACT_REF_TABLE
    __table_args__ = (
        Index(JOB_ARTIFACT_REF_UNIQUE, "job_id", "artifact_id", unique=True),
        {"comment": "绘图工作台-绘图任务产物引用表"},
    )

    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("image_workbench_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="引用方绘图任务ID",
    )
    artifact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("image_workbench_artifact.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="被引用的产物ID",
    )

    __mapper_args__ = {"confirm_deleted_rows": False}
