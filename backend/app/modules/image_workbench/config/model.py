"""绘图工作台 - 配置数据模型（设计 D2：绘图服务 / 绘图模型两张表）

- ``ImageWorkbenchServiceModel``: 绘图服务（名称 / 接入方式 / 完整 API 前缀 / 加密凭证）；
- ``ImageWorkbenchModelModel``: 绘图模型（模型标识 / 展示名 / 能力声明 JSON），隶属绘图服务。

与渠道评估的「平台」互不相干（ADR-0004）：不做渠道 pin、不拉 new-api 管理接口。

``base_url`` 语义与「平台」刻意不同（ADR-0005）：填**完整 API 前缀**（含 ``/v1`` 或
``/api/plan/v3``），代码只在其后追加资源路径，不推断也不补全版本段。

两张配置表只继承 ``ModelMixin``（设计 D2）：它们不参与数据范围过滤，且被引用时由
服务层拒绝删除（D8）、未被引用时按普通删除处理——``name`` 唯一，软删会让同名服务
无法重建，这里刻意不留软删记录。``UserMixin`` 只有绘图任务需要（``created_id`` 即
数据范围的判据）。
"""

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin


class ImageWorkbenchServiceModel(ModelMixin):
    """绘图服务表（管理员配置的上游线路，持有凭证与 base_url）"""

    __tablename__: str = "image_workbench_service"
    __table_args__: dict[str, str] = {"comment": "绘图工作台-绘图服务表"}

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, comment="绘图服务名称")
    dialect: Mapped[str] = mapped_column(String(16), nullable=False, default="ark", comment="接入方式(ark:火山方舟 openai:OpenAI兼容)")
    base_url: Mapped[str] = mapped_column(String(255), nullable=False, comment="完整API前缀(含/v1或/api/plan/v3)")
    api_key_enc: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True, comment="调用凭证密文(Fernet)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:启用 1:停用)")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="备注")

    models: Mapped[list["ImageWorkbenchModelModel"]] = relationship("ImageWorkbenchModelModel", back_populates="service", cascade="all, delete-orphan", passive_deletes=True)

    __mapper_args__ = {"confirm_deleted_rows": False}


class ImageWorkbenchModelModel(ModelMixin):
    """绘图模型表（绘图服务下的两级结构，逐模型能力声明）

    类名与表名 ``image_workbench_model`` 一致（仓库既有 ``<表名驼峰>Model`` 约定）。
    ``model_id`` 为上游真实模型标识，**原样存取不做规范化**：同一模型在不同入口下
    写法不同（``doubao-seedream-5-0-pro`` vs ``doubao-seedream-5-0-pro-260628``），
    能力也不按标识命名规则推断（spec: service-config / 绘图模型与两级结构）。
    """

    __tablename__: str = "image_workbench_model"
    __table_args__: dict[str, str] = {"comment": "绘图工作台-绘图模型表"}

    service_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("image_workbench_service.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="绘图服务ID",
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="上游模型标识(原样提交)")
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="工作台展示名")
    capability_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="能力声明(尺寸/参考图/输出格式/水印默认值/透传参数)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:启用 1:停用)")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="备注")

    service: Mapped[ImageWorkbenchServiceModel] = relationship("ImageWorkbenchServiceModel", back_populates="models")

    __mapper_args__ = {"confirm_deleted_rows": False}
