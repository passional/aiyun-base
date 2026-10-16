"""渠道评估 - 模型映射数据模型

- ``ChannelEvalCanonicalModel``: 规范模型——跨渠道对齐后的比较单元，报告按它分组。
- ``ChannelEvalModelAlias``: 模型别名——归属于某个规范模型的一个渠道模型串。

别名表是「已确认归属」的唯一存放处；未经确认的疑似同族只是实时算出的建议，
不落库、不参与报告分组。

唯一性由服务层在未删除行内校验，**不加数据库唯一约束**：本仓库的删除是软删除
（``is_deleted`` 置位、行仍在），数据库唯一索引会让「删掉一条别名后再加回来」永久失败。
这与既有表的做法一致（如 ``channel_eval_platform.name`` 同样不设唯一约束）。
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin, UserMixin


class ChannelEvalCanonicalModel(ModelMixin, UserMixin):
    """规范模型表"""

    __tablename__: str = "channel_eval_canonical_model"
    __table_args__: dict[str, str] = {"comment": "渠道评估-规范模型表"}

    display_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="显示名(报告中呈现)")
    family: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True, comment="系列(如 Claude/GPT，用于报告筛选)")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:正常 1:停用)")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="备注")

    aliases: Mapped[list["ChannelEvalModelAlias"]] = relationship(
        "ChannelEvalModelAlias",
        back_populates="canonical",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __mapper_args__ = {"confirm_deleted_rows": False}


class ChannelEvalModelAlias(ModelMixin):
    """模型别名表（渠道模型串 → 规范模型，一个串至多属于一个规范模型）"""

    __tablename__: str = "channel_eval_model_alias"
    __table_args__: dict[str, str] = {"comment": "渠道评估-模型别名表"}

    canonical_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channel_eval_canonical_model.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="规范模型ID",
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True, comment="渠道模型串")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", comment="来源(manual:手工录入 auto:确认自动建议)")

    canonical: Mapped[ChannelEvalCanonicalModel] = relationship("ChannelEvalCanonicalModel", back_populates="aliases")

    __mapper_args__ = {"confirm_deleted_rows": False}
