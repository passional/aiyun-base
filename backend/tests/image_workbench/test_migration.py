"""产物表与清单结构迁移测试（任务 3.1 / 3.2 / 3.3）

在**真实的前一版 schema** 上跑真实迁移：先把临时库升到上一版，塞进旧结构的数据行，
再升到本版，最后断言数据被正确改写、文件路径没被动过；downgrade 再断言能回到旧结构。

刻意不走「模型层直测」：迁移的价值全在**存量库**上，而存量库的 schema 只有 alembic
自己建得出来。
"""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from loguru import logger

from app.config.path_conf import BASE_DIR
from app.config.setting import settings
from app.modules.image_workbench.config.capability import ImageWorkbenchCapabilitySchema

# 上一版（产物表之前）与本版（产物表 + 清单新结构）的 revision
_PREVIOUS = "9f3c2a7b8d11"
_TARGET = "6504579f8788"

_LEGACY_CAPABILITY: dict[str, Any] = {
    "sizes": ["1K", "1.5K", "2K"],
    "allow_custom_size": False,
    "support_reference": True,
    "max_reference_images": 10,
    "output_formats": ["png", "jpeg"],
    "default_output_format": "png",
    "watermark_default": False,
    "extra_params": {},
}


def _run_alembic(db_path: Path, action: str, target: str) -> None:
    """把 alembic 指向临时库跑一次 upgrade / downgrade（``ASYNC_DB_URI`` 是实时算的）。"""
    saved = (settings.DATABASE_TYPE, settings.DATABASE_NAME)
    settings.DATABASE_TYPE = "sqlite"
    settings.DATABASE_NAME = str(db_path)
    try:
        config = Config(str(BASE_DIR / "alembic.ini"))
        getattr(command, action)(config, target)
    finally:
        settings.DATABASE_TYPE, settings.DATABASE_NAME = saved


def _exec(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _seed_legacy_rows(db_path: Path, *, extra_params: dict | None = None) -> dict[str, Any]:
    """在旧版 schema 上塞入 服务 / 模型 / 两个任务（一个成功带产物、一个失败无产物）。"""
    capability = dict(_LEGACY_CAPABILITY)
    capability["extra_params"] = extra_params or {}
    now = "2026-09-17 10:00:00.000000"
    service_id = _exec(
        db_path,
        "insert into image_workbench_service (name, dialect, base_url, status, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, ?, 0, ?, ?)",
        ("legacy-ark", "ark", "https://ark.example.com/api/plan/v3", uuid.uuid4().hex, now, now),
    )
    service_id = _exec(db_path, "select id from image_workbench_service where name = 'legacy-ark'")[0][0]
    _exec(
        db_path,
        "insert into image_workbench_model (service_id, model_id, display_name, capability_json, status, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, ?, 0, ?, 0, ?, ?)",
        (service_id, "legacy-model", "Legacy", json.dumps(capability), uuid.uuid4().hex, now, now),
    )
    model_pk = _exec(db_path, "select id from image_workbench_model where model_id = 'legacy-model'")[0][0]

    for suffix, (status, image_path) in {"ok": ("success", "image_workbench/2026/09/legacy-ok.png"), "bad": ("failed", None)}.items():
        _exec(
            db_path,
            "insert into image_workbench_job (service_id, model_pk, model_id, prompt, params_json, status, image_path, image_size, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (
                service_id,
                model_pk,
                "legacy-model",
                f"legacy-{suffix}",
                json.dumps({"size": "2K", "output_format": "png", "watermark": False, "reference_images": []}),
                status,
                image_path,
                "2048x2048" if image_path else None,
                uuid.uuid4().hex,
                now,
                now,
            ),
        )
    job_ids = {row[0]: row[1] for row in _exec(db_path, "select prompt, id from image_workbench_job")}
    return {"service_id": service_id, "model_pk": model_pk, "job_ids": job_ids}


def _ensure_sys_tables(db_path: Path) -> None:
    """补出 ``sys_user`` / ``sys_dept``。

    真实库里这两张表一直在（新建库走 create_all 自举，迁移只承担增量），但**空库直接跑
    alembic** 建不出它们；而 ``batch_alter_table`` 会解析任务表的外键目标，缺表即
    ``NoSuchTableError``。这里按真实库的形态补上最小表，避免测试库自己造出一个现实中
    不存在的场景。
    """
    existing = {row[0] for row in _exec(db_path, "select name from sqlite_master where type='table'")}
    if "sys_dept" not in existing:
        _exec(db_path, "create table sys_dept (id integer primary key autoincrement, uuid varchar(64), is_deleted boolean default 0)")
    if "sys_user" not in existing:
        _exec(
            db_path,
            "create table sys_user (id integer primary key autoincrement, username varchar(64), uuid varchar(64), is_deleted boolean default 0, dept_id integer references sys_dept(id))",
        )


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """升到上一版的临时库（路径必须以 .db 结尾，sqlite 分支才会原样使用）。"""
    db_path = tmp_path / "legacy.db"
    _run_alembic(db_path, "upgrade", _PREVIOUS)
    _ensure_sys_tables(db_path)
    return db_path


# ─────────────────── 3.1 迁移存在且往返无报错 ───────────────────


def test_upgrade_and_downgrade_round_trip(legacy_db: Path) -> None:
    """空库上 upgrade → downgrade → 再 upgrade 全程无报错，产物表该在的时候在。"""
    _run_alembic(legacy_db, "upgrade", _TARGET)
    tables = {row[0] for row in _exec(legacy_db, "select name from sqlite_master where type='table'")}
    assert "image_workbench_artifact" in tables
    columns = {row[1] for row in _exec(legacy_db, "pragma table_info(image_workbench_job)")}
    assert "image_path" not in columns and "image_size" not in columns
    assert "reference_images_json" in columns

    _run_alembic(legacy_db, "downgrade", _PREVIOUS)
    columns = {row[1] for row in _exec(legacy_db, "pragma table_info(image_workbench_job)")}
    assert {"image_path", "image_size"} <= columns
    assert "image_workbench_artifact" not in {row[0] for row in _exec(legacy_db, "select name from sqlite_master where type='table'")}

    _run_alembic(legacy_db, "upgrade", _TARGET)
    assert "image_workbench_artifact" in {row[0] for row in _exec(legacy_db, "select name from sqlite_master where type='table'")}


# ─────────────────── 3.2 既有单产物任务 → 产物行 ───────────────────


def test_legacy_image_path_becomes_artifact_row_without_moving_files(legacy_db: Path) -> None:
    """产物行数与成功任务数一致、``file_path`` 与旧值相同、文件未被移动。"""
    _seed_legacy_rows(legacy_db)
    _run_alembic(legacy_db, "upgrade", _TARGET)

    rows = _exec(legacy_db, "select job_id, z_index, file_path, image_size, output_format from image_workbench_artifact")
    assert len(rows) == 1, "只有 image_path 非空的任务才转成产物行"
    job_id, z_index, file_path, image_size, output_format = rows[0]
    assert z_index is None, "迁移出来的产物是底图（无层级）"
    assert file_path == "image_workbench/2026/09/legacy-ok.png", "file_path 必须沿用旧路径，不搬动文件"
    assert image_size == "2048x2048"
    assert output_format == "png", "格式由扩展名推断"

    failed_job_id = _exec(legacy_db, "select id from image_workbench_job where status = 'failed'")[0][0]
    assert job_id != failed_job_id


def test_downgrade_backfills_legacy_columns_from_base_artifact(legacy_db: Path) -> None:
    """downgrade 后两列回填且与旧值一致（底图优先）。"""
    _seed_legacy_rows(legacy_db)
    _run_alembic(legacy_db, "upgrade", _TARGET)
    _run_alembic(legacy_db, "downgrade", _PREVIOUS)

    rows = _exec(legacy_db, "select prompt, image_path, image_size from image_workbench_job order by id")
    backfilled = {row[0]: (row[1], row[2]) for row in rows}
    assert backfilled["legacy-ok"] == ("image_workbench/2026/09/legacy-ok.png", "2048x2048")
    assert backfilled["legacy-bad"] == (None, None)


def test_downgrade_prefers_base_over_smallest_layer(legacy_db: Path) -> None:
    """有多个产物时回填**底图**（旧结构只有一个「这次的产物是哪个文件」）。"""
    _seed_legacy_rows(legacy_db)
    _run_alembic(legacy_db, "upgrade", _TARGET)
    job_id = _exec(legacy_db, "select id from image_workbench_job where prompt = 'legacy-ok'")[0][0]
    now = "2026-09-17 11:00:00.000000"
    _exec(
        legacy_db,
        "insert into image_workbench_artifact (job_id, z_index, name, file_path, image_size, output_format, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (job_id, 1, "天空", "image_workbench/2026/09/uuid/layer_01.png", "2048x2048", "png", uuid.uuid4().hex, now, now),
    )

    _run_alembic(legacy_db, "downgrade", _PREVIOUS)

    assert _exec(legacy_db, "select image_path from image_workbench_job where id = ?", (job_id,))[0][0] == "image_workbench/2026/09/legacy-ok.png"


# ────────────────── 3.3 清单结构迁移 ───────────────────


def test_legacy_capability_is_converted_to_new_structure(legacy_db: Path) -> None:
    """旧结构逐行转成新结构，转出来的每一行都能被新 schema 解析。"""
    _seed_legacy_rows(legacy_db)
    _run_alembic(legacy_db, "upgrade", _TARGET)

    raw = _exec(legacy_db, "select capability_json from image_workbench_model")[0][0]
    capability = ImageWorkbenchCapabilitySchema.model_validate(json.loads(raw))

    assert capability.reference.supported is True and capability.reference.max_images == 10
    by_key = {param.key: param for param in capability.params}
    assert set(by_key) == {"size", "output_format", "watermark"}
    assert by_key["size"].allowed == ["1K", "1.5K", "2K"]
    assert by_key["size"].default == "1K" and by_key["size"].allow_custom is False
    assert by_key["output_format"].default == "png"
    assert by_key["watermark"].control == "switch" and by_key["watermark"].default is False
    assert all(param.source == "documented" for param in capability.params)
    # 旧结构里没有这两个参数，迁移不得凭空断言该模型支持它们
    assert "layer_decomposition" not in by_key and "background" not in by_key
    assert capability.modes == []


def test_extra_params_non_empty_produces_warning(legacy_db: Path) -> None:
    """``extra_params`` 非空 → 一条警告日志列出被丢弃的键（不静默吞掉）。"""
    _seed_legacy_rows(legacy_db, extra_params={"sequential_image_generation": "disabled", "quality": "high"})

    collected: list[str] = []
    sink_id = logger.add(collected.append, level="WARNING", format="{message}")
    try:
        _run_alembic(legacy_db, "upgrade", _TARGET)
    finally:
        logger.remove(sink_id)

    warnings = [line for line in collected if "extra_params" in line]
    assert len(warnings) == 1, f"应恰好产生一条 extra_params 警告，实际: {collected}"
    assert "sequential_image_generation" in warnings[0] and "quality" in warnings[0]
    # 新结构里不再有 extra_params 这个口子
    raw = json.loads(_exec(legacy_db, "select capability_json from image_workbench_model")[0][0])
    assert "extra_params" not in raw


def test_downgrade_restores_legacy_capability_shape(legacy_db: Path) -> None:
    """downgrade 把清单反推回旧结构（有损：条件依赖与来源标记丢失）。"""
    _seed_legacy_rows(legacy_db)
    _run_alembic(legacy_db, "upgrade", _TARGET)
    _run_alembic(legacy_db, "downgrade", _PREVIOUS)

    legacy = json.loads(_exec(legacy_db, "select capability_json from image_workbench_model")[0][0])
    assert legacy["sizes"] == ["1K", "1.5K", "2K"]
    assert legacy["output_formats"] == ["png", "jpeg"] and legacy["default_output_format"] == "png"
    assert legacy["support_reference"] is True and legacy["max_reference_images"] == 10
    assert legacy["watermark_default"] is False and legacy["allow_custom_size"] is False


def test_migration_is_idempotent_on_already_converted_rows() -> None:
    """已是新结构的行不被再次转换：``modes`` / ``when`` 这些旧结构里没有的信息不被抹掉。

    直接测迁移里的那一层守卫（``_convert_capability_row``）——经由 alembic 跑不出这个
    性质，因为 downgrade 本身有损，会把 ``modes`` 反向丢掉。
    """
    from importlib import import_module

    migration = import_module("app.alembic.versions.2026_09_17_1411_6504579f8788")

    modern = {
        "reference": {"supported": True, "max_images": 14},
        "modes": [
            {
                "key": "layer_decomposition",
                "label": {"zh": "图层拆分", "en": "Layer decomposition"},
                "trigger": {"param": "watermark", "eq": True},
            }
        ],
        "params": [{"key": "watermark", "label": {"zh": "水印", "en": "Watermark"}, "control": "switch", "default": False, "source": "inferred"}],
    }
    assert migration._convert_capability_row(modern) == modern

    # 旧结构的行照常转换；两种形态都能被新 schema 解析
    converted = migration._convert_capability_row(dict(_LEGACY_CAPABILITY))
    ImageWorkbenchCapabilitySchema.model_validate(converted)
    assert "params" in converted and "sizes" not in converted
# ═══════ 3.1 / 3.2 交互编辑的结构变更（prompt_final / 选区快照 / 引用表） ═══════

# 本变更的前一版与本版 revision
_CANVAS_PREVIOUS = "6504579f8788"
_CANVAS_TARGET = "3b7c9e14d2a8"


def _seed_history(db_path: Path, *, jobs: int = 3) -> list[int]:
    """在**本变更之前那一版**的 schema 上塞入服务 / 模型 / 若干历史任务。

    刻意**不写** ``prompt_final`` 与 ``selections_json``——那一版还没有这两列，历史行本来
    就没有它们。这正是回填要处理的对象。
    """
    now = "2026-09-17 16:00:00.000000"
    _exec(
        db_path,
        "insert into image_workbench_service (name, dialect, base_url, status, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, ?, 0, ?, ?)",
        ("canvas-ark", "ark", "https://ark.example.com/api/plan/v3", uuid.uuid4().hex, now, now),
    )
    service_id = _exec(db_path, "select id from image_workbench_service where name = 'canvas-ark'")[0][0]
    _exec(
        db_path,
        "insert into image_workbench_model (service_id, model_id, display_name, status, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, ?, 0, ?, ?)",
        (service_id, "canvas-model", "Canvas", uuid.uuid4().hex, now, now),
    )
    model_pk = _exec(db_path, "select id from image_workbench_model where model_id = 'canvas-model'")[0][0]

    for index in range(jobs):
        _exec(
            db_path,
            "insert into image_workbench_job "
            "(service_id, model_pk, model_id, prompt, params_json, status, reference_images_json, uuid, is_deleted, created_time, updated_time) "
            "values (?, ?, ?, ?, ?, 'success', '[]', ?, 0, ?, ?)",
            (service_id, model_pk, "canvas-model", f"历史任务 {index}", json.dumps({"size": "2K"}), uuid.uuid4().hex, now, now),
        )
    return [row[0] for row in _exec(db_path, "select id from image_workbench_job order by id")]


def _mark_job_as_selection_task(db_path: Path, job_id: int) -> None:
    """把某行改成「用过交互编辑」的样子（**只能在新版 schema 上做**）。

    真实链路里这种行由新版代码写出来；它也正是回滚会真正丢东西的那一类。
    """
    _exec(
        db_path,
        "update image_workbench_job set prompt = ?, prompt_final = ?, selections_json = ? where id = ?",
        (
            "把区域1换成一只猫",
            "把<bbox>120 180 640 760</bbox>换成一只猫",
            json.dumps([{"index": 1, "bbox": [120, 180, 640, 760]}]),
            job_id,
        ),
    )


@pytest.fixture
def canvas_db(tmp_path: Path) -> Path:
    """升到**交互编辑变更之前**那一版的临时库。

    分两步升：产物表那次迁移自己就要 ``batch_alter_table`` 解析 ``sys_user`` 外键，而空库
    直接跑 alembic 建不出 sys 表——先把 sys 表按真实库的形态补上，再升到本变更的前一版。
    """
    db_path = tmp_path / "canvas.db"
    _run_alembic(db_path, "upgrade", _PREVIOUS)
    _ensure_sys_tables(db_path)
    _run_alembic(db_path, "upgrade", _CANVAS_PREVIOUS)
    return db_path


# ─────────────── 3.1 两列与回填 ───────────────


def test_upgrade_backfills_prompt_final_for_every_history_row(canvas_db: Path) -> None:
    """含历史任务的库上 upgrade 后，**每一行**的 ``prompt_final`` 等于 ``prompt``。

    历史任务本来就没有坐标标记，它的 ``prompt`` 就是当时真正发出去的文本——回填值因此等于
    真实值，而不是编出来的近似。
    """
    _seed_history(canvas_db)
    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)

    columns = {row[1] for row in _exec(canvas_db, "pragma table_info(image_workbench_job)")}
    assert {"prompt_final", "selections_json"} <= columns
    rows = _exec(canvas_db, "select prompt, prompt_final, selections_json from image_workbench_job order by id")
    assert len(rows) == 3
    assert all(prompt_final == prompt for prompt, prompt_final, _ in rows), "每一行都必须回填为 prompt"
    assert all(selections is None for _, _, selections in rows), "历史行没有选区"


def test_downgrade_removes_both_columns_and_ref_table(canvas_db: Path) -> None:
    """downgrade 后两列消失、引用表消失，且不报错。"""
    _seed_history(canvas_db)
    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)
    _run_alembic(canvas_db, "downgrade", _CANVAS_PREVIOUS)

    columns = {row[1] for row in _exec(canvas_db, "pragma table_info(image_workbench_job)")}
    assert "prompt_final" not in columns and "selections_json" not in columns
    tables = {row[0] for row in _exec(canvas_db, "select name from sqlite_master where type='table'")}
    assert "image_workbench_job_artifact_ref" not in tables
    # 任务行本身与人写的原话仍在（丢掉的是最终形态，不是历史记录）
    assert len(_exec(canvas_db, "select id from image_workbench_job")) == 3


# ─────────────── 3.2 回滚语义 ───────────────


def test_rows_without_selections_round_trip_losslessly(canvas_db: Path) -> None:
    """**库里没有带选区的任务**时：downgrade 再 upgrade 数据可往返，两列无损。

    design Migration Plan 写的正是这个条件——「库里没有带选区的任务时，回滚才是无损的」，
    而不是无条件无损。
    """
    _seed_history(canvas_db)
    before = _exec(canvas_db, "select prompt, params_json from image_workbench_job order by id")

    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)
    assert all(prompt_final == prompt for prompt, prompt_final, _ in _exec(canvas_db, "select prompt, prompt_final, selections_json from image_workbench_job order by id"))

    _run_alembic(canvas_db, "downgrade", _CANVAS_PREVIOUS)
    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)

    after = _exec(canvas_db, "select prompt, prompt_final, params_json from image_workbench_job order by id")
    assert [(row[0], row[2]) for row in after] == before, "原话与参数快照必须一字不差"
    assert all(row[0] == row[1] for row in after), "再次 upgrade 后回填依然成立"


def test_downgrade_reports_the_loss_of_tasks_that_used_selections(canvas_db: Path) -> None:
    """带选区的任务在回滚后**无法重建**最终提示词——这条损失必须被报出来。

    这类任务的 ``prompt`` 里只剩 ``区域1`` 占位符，选区一旦丢失就无从解释；参数快照里也没有
    坐标。这正是当初保留 ``prompt_final`` 快照的理由，回滚删掉它当然有损失。
    """
    job_ids = _seed_history(canvas_db)
    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)
    _mark_job_as_selection_task(canvas_db, job_ids[0])

    collected: list[str] = []
    sink_id = logger.add(collected.append, level="WARNING", format="{message}")
    try:
        _run_alembic(canvas_db, "downgrade", _CANVAS_PREVIOUS)
    finally:
        logger.remove(sink_id)

    warnings = [line for line in collected if "回滚" in line]
    assert len(warnings) == 1, f"应恰好产生一条回滚损失警告，实际: {collected}"
    assert "无法重建" in warnings[0] and "删除保护" in warnings[0]

    # 数据确实丢了：只剩带占位符的原话，最终提示词与选区都不在了
    columns = {row[1] for row in _exec(canvas_db, "pragma table_info(image_workbench_job)")}
    assert "prompt_final" not in columns and "selections_json" not in columns
    assert len(_exec(canvas_db, "select id from image_workbench_job where prompt like '%区域1%'")) == 1, "原话本身仍在（历史记录不随回滚消失）"


def test_downgrade_removes_the_basis_of_delete_protection(canvas_db: Path) -> None:
    """引用关联表一删，**删除保护静默失效**：被引用的产物在回滚后可以被删掉。

    保护的全部依据就是这张表——``referencing_jobs`` 从它反查引用者。表没了，查询便再也找
    不到任何引用者，删除不再被阻断，而且不会有人提示。这是回滚前必须先确认的事。
    """
    job_ids = _seed_history(canvas_db, jobs=2)
    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)

    now = "2026-09-17 16:30:00.000000"
    _exec(
        canvas_db,
        "insert into image_workbench_artifact (job_id, z_index, file_path, uuid, is_deleted, created_time, updated_time) values (?, NULL, ?, ?, 0, ?, ?)",
        (job_ids[0], "image_workbench/2026/09/canvas/base.png", uuid.uuid4().hex, now, now),
    )
    artifact_id = _exec(canvas_db, "select id from image_workbench_artifact")[0][0]
    _exec(
        canvas_db,
        "insert into image_workbench_job_artifact_ref (job_id, artifact_id, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, ?, ?)",
        (job_ids[1], artifact_id, uuid.uuid4().hex, now, now),
    )
    assert len(_exec(canvas_db, "select id from image_workbench_job_artifact_ref")) == 1, "保护成立：产物被另一个任务引用着"

    _run_alembic(canvas_db, "downgrade", _CANVAS_PREVIOUS)

    tables = {row[0] for row in _exec(canvas_db, "select name from sqlite_master where type='table'")}
    assert "image_workbench_job_artifact_ref" not in tables, "保护的唯一依据（关联表）已随回滚消失"
    assert len(_exec(canvas_db, "select id from image_workbench_artifact")) == 1, "产物行还在，也没人再拦着删除它"


def test_background_data_migration_corrects_only_seeded_pro_model(canvas_db: Path) -> None:
    """已建库的布尔声明必须改成上游字符串域，其他服务和参数保留原样。"""
    _run_alembic(canvas_db, "upgrade", _CANVAS_TARGET)
    now = "2026-09-17 19:18:00.000000"
    for name in ("火山方舟 Agent Plan", "custom-ark"):
        _exec(
            canvas_db,
            "insert into image_workbench_service (name, dialect, base_url, status, uuid, is_deleted, created_time, updated_time) values (?, 'ark', 'https://example.com/api/v3', 0, ?, 0, ?, ?)",
            (name, uuid.uuid4().hex, now, now),
        )
    services = dict(_exec(canvas_db, "select name, id from image_workbench_service"))
    old = {
        "reference": {"supported": True, "max_images": 10},
        "modes": [],
        "params": [
            {"key": "background", "label": {"zh": "图片透明通道", "en": "Transparent background"}, "control": "switch", "default": False, "source": "measured"},
            {"key": "watermark", "label": {"zh": "水印", "en": "Watermark"}, "control": "switch", "default": False, "source": "documented"},
        ],
    }
    for name in services:
        _exec(
            canvas_db,
            "insert into image_workbench_model (service_id, model_id, display_name, capability_json, status, uuid, is_deleted, created_time, updated_time) values (?, 'doubao-seedream-5-0-pro', 'Pro', ?, 0, ?, 0, ?, ?)",
            (services[name], json.dumps(old), uuid.uuid4().hex, now, now),
        )

    _run_alembic(canvas_db, "upgrade", "c8e71a9b402d")
    rows = _exec(
        canvas_db,
        "select s.name, m.capability_json from image_workbench_model m join image_workbench_service s on s.id=m.service_id",
    )
    by_service = {name: json.loads(raw) for name, raw in rows}
    seeded = {param["key"]: param for param in by_service["火山方舟 Agent Plan"]["params"]}
    assert seeded["background"]["control"] == "select"
    assert seeded["background"]["allowed"] == ["opaque", "transparent"]
    assert seeded["background"]["default"] == "opaque"
    assert seeded["background"]["source"] == "documented"
    assert seeded["watermark"] == old["params"][1]
    assert by_service["custom-ark"] == old
