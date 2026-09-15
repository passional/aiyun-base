"""渠道评估 - 对比报告数据模型

报告 = 选择 + 备注，不复制测量数据（ADR-0002：基准引用取「管理员选定的一次基准
测试运行」并随导出冻结；Web 视图打开时基于所选运行的持久化数据实时渲染）。
"""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin


class ChannelEvalReportModel(ModelMixin, UserMixin):
    """对比报告表"""

    __tablename__: str = "channel_eval_report"
    __table_args__: dict[str, str] = {"comment": "渠道评估-对比报告表"}

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="报告名称")
    run_ids: Mapped[list | None] = mapped_column(JSON, default=None, nullable=True, comment="所选测试运行ID列表")
    baseline_run_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="基准运行ID(基准渠道的成功运行)")
    note: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="管理员备注")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="状态(0:正常 1:停用)")
