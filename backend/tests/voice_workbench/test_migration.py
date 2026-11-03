"""语音工作台迁移守卫：三张表可 upgrade / downgrade 往返，且不存音频路径。

不依赖历史迁移链（仓库首个迁移在示例表上不可重放）：先把空库戳到上一版
（本迁移的 down_revision），再只应用本迁移。这与「存量库增量升级」的真实路径一致。
"""

import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config.path_conf import BASE_DIR
from app.config.setting import settings

_VOICE_TABLES = {"voice_workbench_service", "voice_workbench_model", "voice_workbench_job"}


def _run_alembic(db_path: Path, action: str, target: str) -> None:
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


def _tables(db_path: Path) -> set[str]:
    return {row[0] for row in _exec(db_path, "select name from sqlite_master where type='table'")}


def _revisions() -> tuple[str, str]:
    config = Config(str(BASE_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    previous: Any = script.get_revision(head).down_revision
    assert isinstance(previous, str), f"本迁移应只有一个上一版，实际 {previous!r}"
    return previous, head


def test_migration_round_trip_and_tables(tmp_path: Path) -> None:
    previous, head = _revisions()
    db_path = tmp_path / "voice_workbench_migration.db"

    _run_alembic(db_path, "stamp", previous)
    _run_alembic(db_path, "upgrade", head)
    assert _VOICE_TABLES <= _tables(db_path)

    columns = {row[1] for row in _exec(db_path, "pragma table_info(voice_workbench_job)")}
    assert {"service_id", "model_pk", "model_name", "original_filename", "file_size", "duration_sec", "segments_json", "status", "created_id"} <= columns
    assert not {"audio_path", "file_path", "audio_file", "storage_path"} & columns, "原始音频不得落盘"

    _run_alembic(db_path, "downgrade", previous)
    assert not (_VOICE_TABLES & _tables(db_path))

    _run_alembic(db_path, "upgrade", head)
    assert _VOICE_TABLES <= _tables(db_path)
