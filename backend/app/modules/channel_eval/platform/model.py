"""渠道评估 - 平台数据模型

- ``ChannelEvalPlatformModel``: 评估目标平台（apiworld / aihubmix 等 new-api 站点），
  凭证加密存储（CryptoUtil），接口只回显脱敏占位。

  指定渠道（ADR-0001）的专用评估令牌按运行粒度存于 ``channel_eval_run``
  （``eval_token_id``/``eval_token_key_enc``），不再使用逐渠道产物表。
"""

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin


class ChannelEvalPlatformModel(ModelMixin, UserMixin):
    """评估平台表"""

    __tablename__: str = "channel_eval_platform"
    __table_args__: dict[str, str] = {"comment": "渠道评估-平台表"}

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="平台名称")
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="admin", comment="平台类型(admin:new-api管理型 user:仅用户级)")
    base_url: Mapped[str] = mapped_column(String(255), nullable=False, comment="平台基础地址")
    admin_key_enc: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True, comment="管理凭证密文(仅管理型)")
    admin_user_id: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="管理用户ID(New-Api-User请求头, 可选)")
    test_key_enc: Mapped[str] = mapped_column(String(512), nullable=False, comment="测试凭证密文")
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否基准渠道")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:启用 1:停用)")
    remark: Mapped[str | None] = mapped_column(String(500), default=None, nullable=True, comment="备注")
