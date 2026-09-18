"""add image workbench artifact

Revision ID: 6504579f8788
Revises: 9f3c2a7b8d11
Create Date: 2026-09-17 14:11:00

产物独立建表 + 任务表去掉产物字段（设计 D7/D8）+ 清单结构的一次性数据迁移
（design Migration Plan A）。

四步（顺序不可换，第 2 步要用到第 3 步要删的列）:

1. 建 ``image_workbench_artifact``；
2. 既有 ``image_workbench_job.image_path`` 非空的行，各写成一行产物；
3. 删除任务表的 ``image_path`` / ``image_size``；
4. 把 ``image_workbench_model.capability_json`` 的旧结构逐行转成新结构。

**不搬动既有文件**（D8）：``file_path`` 沿用旧路径，存储节点可能是 ftp/sftp/s3/obs/
oss/cos，搬文件不可行也没有必要——``file_path`` 是权威，目录布局只是约定。

**回滚是有损的**，必须先知情：多产物任务的其余产物行会随表被删（文件留在存储节点上
成为孤儿，降级前应先确认无多产物任务）；条件依赖与来源标记在旧结构里无处存放，会丢失。
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

from app.core.logger import logger

# revision identifiers, used by Alembic.
revision: str = "6504579f8788"
down_revision: str | None = "9f3c2a7b8d11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 旧结构 → 新结构的标签（新结构的每个参数条目都要有中英显示名；旧结构里没有这项信息）
_LABELS: dict[str, dict[str, str]] = {
    "size": {"zh": "尺寸", "en": "Size"},
    "output_format": {"zh": "输出格式", "en": "Output format"},
    "watermark": {"zh": "水印", "en": "Watermark"},
}

_EXTENSION_FORMATS: dict[str, str] = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif"}


def _job_table() -> sa.TableClause:
    return sa.table(
        "image_workbench_job",
        sa.column("id", sa.Integer),
        sa.column("uuid", sa.String),
        sa.column("image_path", sa.String),
        sa.column("image_size", sa.String),
        sa.column("created_time", sa.DateTime(timezone=True)),
        sa.column("updated_time", sa.DateTime(timezone=True)),
        sa.column("is_deleted", sa.Boolean),
    )


def _artifact_table() -> sa.TableClause:
    return sa.table(
        "image_workbench_artifact",
        sa.column("id", sa.Integer),
        sa.column("job_id", sa.Integer),
        sa.column("z_index", sa.Integer),
        sa.column("file_path", sa.String),
        sa.column("image_size", sa.String),
        sa.column("output_format", sa.String),
        sa.column("uuid", sa.String),
        sa.column("is_deleted", sa.Boolean),
        sa.column("created_time", sa.DateTime(timezone=True)),
        sa.column("updated_time", sa.DateTime(timezone=True)),
    )


def _model_table() -> sa.TableClause:
    return sa.table(
        "image_workbench_model",
        sa.column("id", sa.Integer),
        sa.column("service_id", sa.Integer),
        sa.column("model_id", sa.String),
        sa.column("display_name", sa.String),
        sa.column("capability_json", sa.JSON),
        sa.column("status", sa.Integer),
        sa.column("remark", sa.String),
    )


def _infer_output_format(file_path: str | None) -> str | None:
    """由扩展名推断输出格式；推断不出时留空（不猜）。"""
    if not file_path:
        return None
    return _EXTENSION_FORMATS.get(Path(file_path).suffix.lower())


# ───────────────────── 旧结构 ↔ 新结构的清单转换 ─────────────────────


def _legacy_to_capability(legacy: dict) -> dict:
    """旧结构（固定八字段）→ 新结构（参数条目清单）。

    ``layer_decomposition`` / ``background`` 两个条目**不生成**——旧结构里没有它们，
    生成出来就等于凭空断言该模型支持这两个参数。存量库要么由管理员按新种子重填，
    要么重建库（design Migration Plan A 第 4 步）。
    """
    params: list[dict] = []

    sizes = [item for item in (legacy.get("sizes") or []) if isinstance(item, str) and item.strip()]
    if sizes:
        params.append(
            {
                "key": "size",
                "label": _LABELS["size"],
                "control": "select",
                "allowed": sizes,
                "allow_custom": bool(legacy.get("allow_custom_size")),
                "default": sizes[0],
                "source": "documented",
            }
        )

    formats = [item for item in (legacy.get("output_formats") or []) if isinstance(item, str) and item.strip()]
    if formats:
        default_format = legacy.get("default_output_format")
        params.append(
            {
                "key": "output_format",
                "label": _LABELS["output_format"],
                "control": "select",
                "allowed": formats,
                "default": default_format if default_format in formats else formats[0],
                "source": "documented",
            }
        )

    params.append(
        {
            "key": "watermark",
            "label": _LABELS["watermark"],
            "control": "switch",
            "default": bool(legacy.get("watermark_default", False)),
            "source": "documented",
        }
    )

    supported = bool(legacy.get("support_reference", True))
    return {
        "reference": {"supported": supported, "max_images": int(legacy.get("max_reference_images") or 0) if supported else 0},
        "modes": [],
        "params": params,
    }


def _convert_capability_row(raw: dict) -> dict:
    """库内清单 → 新结构的清单；**已是新结构的行原样返回**。

    迁移可重跑：混合状态的库（部分行已被人手改成新结构）不该被再次「转换」——
    那会把 ``modes`` / ``when`` 这类旧结构里根本没有的信息抹掉。
    """
    if "params" in raw:
        return raw
    return _legacy_to_capability(raw)


def _capability_to_legacy(capability: dict) -> dict:
    """新结构 → 旧结构（回滚用，**有损**：条件依赖与来源标记无处存放，会丢失）。"""
    legacy: dict = {
        "sizes": [],
        "allow_custom_size": False,
        "support_reference": True,
        "max_reference_images": 0,
        "output_formats": ["png", "jpeg"],
        "default_output_format": "png",
        "watermark_default": False,
        "extra_params": {},
    }
    reference = capability.get("reference") or {}
    legacy["support_reference"] = bool(reference.get("supported", True))
    legacy["max_reference_images"] = int(reference.get("max_images") or 0) if legacy["support_reference"] else 0

    for param in capability.get("params") or []:
        key = param.get("key")
        if key == "size":
            legacy["sizes"] = list(param.get("allowed") or [])
            legacy["allow_custom_size"] = bool(param.get("allow_custom"))
        elif key == "output_format":
            legacy["output_formats"] = list(param.get("allowed") or [])
            if param.get("default") is not None:
                legacy["default_output_format"] = param["default"]
        elif key == "watermark":
            legacy["watermark_default"] = bool(param.get("default", False))
    if not legacy["output_formats"]:
        legacy["output_formats"] = [legacy["default_output_format"]]
    return legacy


# ────────────────────────────── upgrade ──────────────────────────────


def upgrade() -> None:
    # 1. 产物表
    op.create_table(
        "image_workbench_artifact",
        sa.Column("job_id", sa.Integer(), nullable=False, comment="绘图任务ID"),
        sa.Column("z_index", sa.Integer(), nullable=True, comment="层级(底图为空)"),
        sa.Column("name", sa.String(length=128), nullable=True, comment="图层名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="图层描述"),
        sa.Column("bounding_box_json", sa.JSON(), nullable=True, comment="图层在底图中所占的位置(绝对与归一化两种表示)"),
        sa.Column("file_path", sa.String(length=512), nullable=False, comment="产物在存储节点中的相对路径"),
        sa.Column("image_size", sa.String(length=32), nullable=True, comment="产物宽高(如2048x2048)"),
        sa.Column("output_format", sa.String(length=16), nullable=True, comment="产物格式(png/jpeg/webp)"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("uuid", sa.String(length=64), nullable=False, comment="UUID全局唯一标识"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, comment="是否已删除(0:未删除 1:已删除)"),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False, comment="创建时间"),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False, comment="更新时间"),
        sa.Column("deleted_time", sa.DateTime(timezone=True), nullable=True, comment="删除时间"),
        sa.ForeignKeyConstraint(["job_id"], ["image_workbench_job.id"], name=op.f("fk_image_workbench_artifact_job_id_image_workbench_job"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_image_workbench_artifact")),
        comment="绘图工作台-绘图产物表",
    )
    with op.batch_alter_table("image_workbench_artifact", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_image_workbench_artifact_created_time"), ["created_time"], unique=False)
        batch_op.create_index(batch_op.f("ix_image_workbench_artifact_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_image_workbench_artifact_is_deleted"), ["is_deleted"], unique=False)
        batch_op.create_index(batch_op.f("ix_image_workbench_artifact_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_image_workbench_artifact_uuid"), ["uuid"], unique=True)

    bind = op.get_bind()
    jobs = _job_table()
    artifacts = _artifact_table()

    # 2. 既有单产物任务 → 各写成一行产物（底图：z_index 为空；文件不动）
    legacy_rows = bind.execute(sa.select(jobs.c.id, jobs.c.uuid, jobs.c.image_path, jobs.c.image_size, jobs.c.created_time, jobs.c.updated_time).where(jobs.c.image_path.isnot(None))).all()
    if legacy_rows:
        now = datetime.now(UTC)
        bind.execute(
            sa.insert(artifacts),
            [
                {
                    "job_id": row.id,
                    "z_index": None,
                    "file_path": row.image_path,
                    "image_size": row.image_size,
                    "output_format": _infer_output_format(row.image_path),
                    "uuid": uuid4().hex,
                    "is_deleted": False,
                    "created_time": row.created_time or now,
                    "updated_time": row.updated_time or now,
                }
                for row in legacy_rows
                if row.image_path
            ],
        )
        logger.info("绘图工作台迁移：{} 个既有单产物任务已转成产物行", len(legacy_rows))

    # 3. 任务表去掉产物字段（两处记录「这次的产物是哪个文件」必然漂移）
    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reference_images_json", sa.JSON(), nullable=True, comment="参考图在本系统内的相对路径数组"))
        batch_op.alter_column(
            "params_json",
            existing_type=mysql.JSON(),
            comment="参数快照(等于实际发给上游的参数集)",
            existing_comment="参数快照(size/output_format/watermark/reference_images)",
            existing_nullable=True,
        )
        batch_op.drop_column("image_path")
        batch_op.drop_column("image_size")

    # 4. 清单结构：旧 → 新
    models = _model_table()
    for row in bind.execute(sa.select(models.c.id, models.c.capability_json)).all():
        legacy = row.capability_json if isinstance(row.capability_json, dict) else {}
        dropped = list((legacy.get("extra_params") or {}).keys())
        if dropped:
            # 不静默吞掉：这些键原本可以让管理员设置用户看不见的上游字段，
            # 迁移后无处安放，必须让运维看见丢了什么（设计 D6）
            logger.warning("绘图工作台迁移：绘图模型 #{} 的 extra_params 已废弃，以下键被丢弃: {}", row.id, dropped)
        converted = _convert_capability_row(legacy)
        if converted != legacy:
            bind.execute(sa.update(models).where(models.c.id == row.id).values(capability_json=converted))


def downgrade() -> None:
    bind = op.get_bind()
    jobs = _job_table()
    artifacts = _artifact_table()
    models = _model_table()

    # 回滚前先把产物行读出来：产物表马上就要被删掉
    # 回填取值顺序：底图（z_index 为空）优先，无底图时取层级最小的图层——旧结构只有一个
    # 「这次的产物是哪个文件」，底图才是那张完整画面。
    artifact_rows = bind.execute(
        sa.select(artifacts.c.job_id, artifacts.c.z_index, artifacts.c.file_path, artifacts.c.image_size).order_by(artifacts.c.job_id, artifacts.c.z_index.is_(None).desc(), artifacts.c.z_index)
    ).all()
    backfill: dict[int, tuple[str, str | None]] = {}
    for row in artifact_rows:
        backfill.setdefault(row.job_id, (row.file_path, row.image_size))

    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.add_column(sa.Column("image_size", mysql.VARCHAR(length=32), nullable=True, comment="产物宽高(如2048x2048)"))
        batch_op.add_column(sa.Column("image_path", mysql.VARCHAR(length=512), nullable=True, comment="产物在存储节点中的相对路径(成功时非空)"))

    for job_id, (file_path, image_size) in backfill.items():
        bind.execute(sa.update(jobs).where(jobs.c.id == job_id).values(image_path=file_path, image_size=image_size))

    with op.batch_alter_table("image_workbench_job", schema=None) as batch_op:
        batch_op.alter_column(
            "params_json",
            existing_type=mysql.JSON(),
            comment="参数快照(size/output_format/watermark/reference_images)",
            existing_comment="参数快照(等于实际发给上游的参数集)",
            existing_nullable=True,
        )
        batch_op.drop_column("reference_images_json")

    # 清单结构：新 → 旧（有损：条件依赖与来源标记丢失）
    for row in bind.execute(sa.select(models.c.id, models.c.capability_json)).all():
        capability = row.capability_json if isinstance(row.capability_json, dict) else {}
        if "params" not in capability:
            continue
        bind.execute(sa.update(models).where(models.c.id == row.id).values(capability_json=_capability_to_legacy(capability)))

    with op.batch_alter_table("image_workbench_artifact", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_image_workbench_artifact_uuid"))
        batch_op.drop_index(batch_op.f("ix_image_workbench_artifact_job_id"))
        batch_op.drop_index(batch_op.f("ix_image_workbench_artifact_is_deleted"))
        batch_op.drop_index(batch_op.f("ix_image_workbench_artifact_id"))
        batch_op.drop_index(batch_op.f("ix_image_workbench_artifact_created_time"))

    op.drop_table("image_workbench_artifact")
