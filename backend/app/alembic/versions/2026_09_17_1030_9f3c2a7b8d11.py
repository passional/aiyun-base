"""add image workbench active job slot

Revision ID: 9f3c2a7b8d11
Revises: 022aabbd3d3d
Create Date: 2026-09-17 10:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3c2a7b8d11"
down_revision: str | None = "022aabbd3d3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 只增加 nullable 列和索引，SQLite/MySQL/PostgreSQL 都可原地执行。
    # 避免 batch 重建整张任务表，减少锁表时间与数据搬迁风险。
    op.add_column(
        "image_workbench_job",
        sa.Column(
            "active_owner_id",
            sa.Integer(),
            nullable=True,
            comment="进行中任务的用户占位ID(终态为空)",
        ),
    )
    op.create_index(
        "ux_image_workbench_job_active_owner_id",
        "image_workbench_job",
        ["active_owner_id"],
        unique=True,
    )


def downgrade() -> None:
    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.drop_index("ux_image_workbench_job_active_owner_id")
        batch_op.drop_column("active_owner_id")
