"""add job prompt_final selections and artifact refs

Revision ID: 3b7c9e14d2a8
Revises: 6504579f8788
Create Date: 2026-09-17 17:45:00

交互编辑的三处结构变更，**一次迁移做完**（design Migration Plan）：

1. ``image_workbench_job`` 增加 ``prompt_final``，历史行回填为 ``prompt`` 的当前值；
2. ``image_workbench_job`` 增加 ``selections_json``（选区快照，含来源产物标识）；
3. 新建 ``image_workbench_job_artifact_ref``（产物引用关联表，删除保护的依据）。

**回填为什么必须显式做**：``prompt_final`` 语义上非空，但历史行的这一列还没有值。
直接加一个 NOT NULL 列会在存量库上失败（sqlite 报 「Cannot add a NOT NULL column with
default value NULL」，MySQL 严格模式下同样拒绝）。历史任务本来就没有坐标标记，其
``prompt`` **就是**当时真正发出去的文本，因此回填值等于真实值，不是编出来的近似。

**回滚是有损的，而且必须让运维看见**（design Migration Plan 的回滚一节）：

- 带选区任务的最终提示词**无法重建**。这类任务的 ``prompt`` 里只剩 ``区域1`` 这样的
  占位符，选区一旦丢失，占位符就无从解释；参数快照里也没有坐标。这正是当初保留
  ``prompt_final`` 快照的理由——快照存在的意义就是它不可重建，删掉它当然会有损失。
- 引用关联表一删，**删除保护静默失效**：被引用的产物在回滚后可以被删掉，而且不会有人
  提示。关联表里没有需要保留的历史事实，需要留下的是这条知情记录。

因此回滚有两个前置条件（写进操作步骤而不是靠记性）：**先确认库中不存在带选区的任务，
或先把这些任务的原话、最终提示词、选区快照与来源引用导出留存**。库里没有带选区的任务
时，回滚才是无损的。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

from app.core.logger import logger

# revision identifiers, used by Alembic.
revision: str = "3b7c9e14d2a8"
down_revision: str | None = "6504579f8788"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REF_TABLE = "image_workbench_job_artifact_ref"


def upgrade() -> None:
    bind = op.get_bind()
    job = sa.table(
        "image_workbench_job",
        sa.column("id", sa.Integer),
        sa.column("prompt", sa.Text),
        sa.column("prompt_final", sa.Text),
        sa.column("selections_json", sa.JSON),
    )

    # 1+2. 两列一起加：prompt_final 先可空，回填后再收紧为 NOT NULL
    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.add_column(sa.Column("prompt_final", sa.Text(), nullable=True, comment="最终提示词(实际发给上游的文本，含坐标标记)"))
        batch_op.add_column(sa.Column("selections_json", sa.JSON(), nullable=True, comment="选区快照(序号、坐标与来源产物标识)"))
        batch_op.alter_column(
            "prompt",
            existing_type=mysql.TEXT(),
            comment="提示词(用户原话，含区域占位符)",
            existing_comment="提示词",
            existing_nullable=False,
        )
    # 历史行没有坐标标记，它的 prompt 就是当时真正发出去的文本
    backfilled = bind.execute(sa.update(job).where(job.c.prompt_final.is_(None)).values(prompt_final=job.c.prompt)).rowcount
    if backfilled:
        logger.info("绘图工作台迁移：{} 个历史任务的 prompt_final 已回填为其 prompt", backfilled)
    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.alter_column("prompt_final", existing_type=mysql.TEXT(), nullable=False, comment="最终提示词(实际发给上游的文本，含坐标标记)")

    # 3. 产物引用关联表（两类引用合写一张表，不给引用标类型）
    op.create_table(
        _REF_TABLE,
        sa.Column("job_id", sa.Integer(), nullable=False, comment="引用方绘图任务ID"),
        sa.Column("artifact_id", sa.Integer(), nullable=False, comment="被引用的产物ID"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("uuid", sa.String(length=64), nullable=False, comment="UUID全局唯一标识"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, comment="是否已删除(0:未删除 1:已删除)"),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False, comment="创建时间"),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False, comment="更新时间"),
        sa.Column("deleted_time", sa.DateTime(timezone=True), nullable=True, comment="删除时间"),
        sa.ForeignKeyConstraint(["job_id"], ["image_workbench_job.id"], name=op.f(f"fk_{_REF_TABLE}_job_id_image_workbench_job"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["image_workbench_artifact.id"], name=op.f(f"fk_{_REF_TABLE}_artifact_id_image_workbench_artifact"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_REF_TABLE}")),
        # 同一产物被同一任务从两类来源重复引用时只落一行（design D6）
        sa.UniqueConstraint("job_id", "artifact_id", name=op.f("ux_image_workbench_job_artifact_ref_job_artifact")),
        comment="绘图工作台-绘图任务产物引用表",
    )
    with op.batch_alter_table(_REF_TABLE, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(f"ix_{_REF_TABLE}_artifact_id"), ["artifact_id"], unique=False)
        batch_op.create_index(batch_op.f(f"ix_{_REF_TABLE}_created_time"), ["created_time"], unique=False)
        batch_op.create_index(batch_op.f(f"ix_{_REF_TABLE}_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f(f"ix_{_REF_TABLE}_is_deleted"), ["is_deleted"], unique=False)
        batch_op.create_index(batch_op.f(f"ix_{_REF_TABLE}_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f(f"ix_{_REF_TABLE}_uuid"), ["uuid"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    job = sa.table("image_workbench_job", sa.column("prompt_final", sa.Text))

    # 回滚前先如实报出要丢什么（不静默吞掉，design Migration Plan）
    placeholders = bind.execute(
        sa.select(sa.func.count()).select_from(job).where(job.c.prompt_final.like("%<bbox>%")),
    ).scalar()
    if placeholders:
        logger.warning(
            "绘图工作台回滚：库中存在 {} 个带坐标标记的任务，其最终提示词与选区快照将随本次回滚丢失且无法重建；"
            "引用关联表一并删除，删除保护随之失效",
            placeholders,
        )

    with op.batch_alter_table(_REF_TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(f"ix_{_REF_TABLE}_uuid"))
        batch_op.drop_index(batch_op.f(f"ix_{_REF_TABLE}_job_id"))
        batch_op.drop_index(batch_op.f(f"ix_{_REF_TABLE}_is_deleted"))
        batch_op.drop_index(batch_op.f(f"ix_{_REF_TABLE}_id"))
        batch_op.drop_index(batch_op.f(f"ix_{_REF_TABLE}_created_time"))
        batch_op.drop_index(batch_op.f(f"ix_{_REF_TABLE}_artifact_id"))
    op.drop_table(_REF_TABLE)

    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.alter_column(
            "prompt",
            existing_type=mysql.TEXT(),
            comment="提示词",
            existing_comment="提示词(用户原话，含区域占位符)",
            existing_nullable=False,
        )
        batch_op.drop_column("selections_json")
        batch_op.drop_column("prompt_final")
