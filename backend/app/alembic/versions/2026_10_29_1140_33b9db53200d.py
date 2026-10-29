"""合并上游 ops 初始迁移与本地迁移链

Revision ID: 33b9db53200d
Revises: b7f2c4a9e1d3, e082c18b2f01
Create Date: 2026-10-29 11:40:09.841879

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33b9db53200d'
down_revision: Union[str, None] = ('b7f2c4a9e1d3', 'e082c18b2f01')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
