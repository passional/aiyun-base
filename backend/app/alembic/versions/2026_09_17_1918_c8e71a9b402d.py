"""correct Seedream 5.0 pro background declaration

Revision ID: c8e71a9b402d
Revises: 3b7c9e14d2a8
Create Date: 2026-09-17 19:18:00

The upstream field is a string enum (opaque/transparent). The previous seed
declared it as a boolean switch, so a one-image edit with alpha sent false
and received HTTP 400. Update only the seeded Agent Plan model when it still
has that boolean declaration; preserve other models and administrator edits.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e71a9b402d"
down_revision: str | None = "3b7c9e14d2a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    services = sa.table("image_workbench_service", sa.column("id", sa.Integer), sa.column("name", sa.String))
    models = sa.table(
        "image_workbench_model",
        sa.column("id", sa.Integer),
        sa.column("service_id", sa.Integer),
        sa.column("model_id", sa.String),
        sa.column("capability_json", sa.JSON),
        sa.column("remark", sa.String),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(models.c.id, models.c.capability_json, models.c.remark)
        .select_from(models.join(services, models.c.service_id == services.c.id))
        .where(services.c.name == "火山方舟 Agent Plan", models.c.model_id == "doubao-seedream-5-0-pro")
    ).all()
    for row in rows:
        capability = row.capability_json
        if not isinstance(capability, dict):
            continue
        params = capability.get("params")
        if not isinstance(params, list):
            continue
        background = next((item for item in params if isinstance(item, dict) and item.get("key") == "background"), None)
        if not background or background.get("control") != "switch" or not isinstance(background.get("default"), bool):
            continue
        background["control"] = "select"
        background["allowed"] = ["opaque", "transparent"]
        background["default"] = "transparent" if background["default"] else "opaque"
        background["source"] = "documented"
        remark = row.remark
        if remark:
            remark = remark.replace(
                "layer_decomposition 与 background 标 measured：已在 /api/plan/v3 上实测确认两者被接受",
                "layer_decomposition 标 measured：已在 /api/plan/v3 上实测确认被接受；background 按文档声明为 opaque/transparent 字符串，布尔值已实测会被拒绝",
            )
        bind.execute(
            sa.update(models).where(models.c.id == row.id).values(capability_json=capability, remark=remark)
        )


def downgrade() -> None:
    # Data correction: restoring the boolean declaration would reintroduce
    # upstream HTTP 400. Older application versions accept select controls.
    pass
