"""渠道评估 - 测试运行数据模型

- ``ChannelEvalRunModel``: 一次完整评估执行（运行参数快照固化于 ``params_json``）。
- ``ChannelEvalRunComboModel``: 「渠道 × 模型」组合清单即测试集快照（渠道编号与名称快照），
  聚合计数冗余便于列表展示。
- ``ChannelEvalSampleModel``: 单样本——客户端实测指标 + 配对的后台日志字段（``be_*``）。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin, UserMixin


class ChannelEvalRunModel(ModelMixin, UserMixin):
    """测试运行表"""

    __tablename__: str = "channel_eval_run"
    __table_args__: dict[str, str] = {"comment": "渠道评估-测试运行表"}

    platform_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channel_eval_platform.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="平台ID",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True, comment="状态(pending/running/cancelling/success/failed/cancelled)")
    params_json: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True, comment="运行参数快照(次数/热身/间隔/超时/prompt/异常阈值)")
    schedule_hint: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True, comment="定时提示(预留，二期接入调度器)")
    # 指定渠道（pin）专用评估令牌：每次运行创建时经平台管理接口建 1 个管理员令牌
    # （命名约定 eval-run{运行ID}），本运行全部组合复用并 pin 到各自渠道（ADR-0001）。
    eval_token_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="评估令牌ID(new-api侧)")
    eval_token_key_enc: Mapped[str | None] = mapped_column(String(512), default=None, nullable=True, comment="评估令牌密钥密文(仅管理型平台)")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="执行开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="执行结束时间")
    error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="失败/取消原因")

    combos: Mapped[list["ChannelEvalRunComboModel"]] = relationship("ChannelEvalRunComboModel", back_populates="run", cascade="all, delete-orphan", passive_deletes=True)

    __mapper_args__ = {"confirm_deleted_rows": False}


class ChannelEvalRunComboModel(ModelMixin):
    """测试运行组合表（渠道 × 模型，测试集快照）"""

    __tablename__: str = "channel_eval_run_combo"
    __table_args__: dict[str, str] = {"comment": "渠道评估-运行组合表"}

    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channel_eval_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="运行ID",
    )
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="渠道编号快照")
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="渠道名称快照")
    model: Mapped[str] = mapped_column(String(255), nullable=False, comment="模型ID")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", comment="组合状态(pending/success/failed)")
    planned: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="计划请求次数")
    success: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="成功样本数")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="失败样本数")
    anomaly: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="异常样本数(超阈值)")
    error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="组合级失败原因(如定向准备失败)")

    run: Mapped[ChannelEvalRunModel] = relationship("ChannelEvalRunModel", back_populates="combos")
    samples: Mapped[list["ChannelEvalSampleModel"]] = relationship("ChannelEvalSampleModel", back_populates="combo", cascade="all, delete-orphan", passive_deletes=True)

    __mapper_args__ = {"confirm_deleted_rows": False}


class ChannelEvalSampleModel(ModelMixin):
    """测试样本表（客户端实测 + 配对后台日志字段）"""

    __tablename__: str = "channel_eval_sample"
    __table_args__: dict[str, str] = {"comment": "渠道评估-样本表"}

    combo_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channel_eval_run_combo.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="组合ID",
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="组合内序号(热身为0)")
    is_warmup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否热身请求(不计聚合)")
    request_id: Mapped[str | None] = mapped_column(String(128), default=None, nullable=True, comment="平台返回的请求标识")
    ttft_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="首Token时延(毫秒)")
    total_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="总耗时(毫秒)")
    tokens_in: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="输入tokens")
    tokens_out: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="输出tokens")
    tps: Mapped[float | None] = mapped_column(Float, default=None, nullable=True, comment="生成速率(tokens/s)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success", comment="样本状态(success/failed)")
    error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="失败错误信息")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="请求发出时间")
    # ── 后台日志配对字段（be_* = backend）──
    be_matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否配对到后台日志")
    be_frt_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="后台首字时间(毫秒)")
    be_use_time_ms: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="后台总耗时(毫秒)")
    be_tokens_in: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="后台输入tokens")
    be_tokens_out: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="后台输出tokens")
    be_cost: Mapped[int | None] = mapped_column(BigInteger, default=None, nullable=True, comment="后台消耗(quota)")
    be_channel_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, comment="后台记录的实际渠道编号")

    combo: Mapped[ChannelEvalRunComboModel] = relationship("ChannelEvalRunComboModel", back_populates="samples")

    __mapper_args__ = {"confirm_deleted_rows": False}
