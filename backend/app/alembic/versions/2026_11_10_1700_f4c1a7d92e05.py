"""合并上游公告已读迁移与本地语音工作台迁移

Revision ID: f4c1a7d92e05
Revises: 028cad380a37, a1b2c3d4e5f6
Create Date: 2026-11-10 17:00:00.000000

上游 a1b2c3d4e5f6（公告已读）基于根迁移 e082c18b2f01，本地 028cad380a37
（语音工作台）经 33b9db53200d 已收口过 e082c18b2f01；两条链在此合并为单一 head。
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = 'f4c1a7d92e05'
down_revision: str | None = ('028cad380a37', 'a1b2c3d4e5f6')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
