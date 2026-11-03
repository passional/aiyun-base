"""转写任务 API 测试：成功/失败落库、前置校验、串行锁、用户隔离、软删、启动恢复。

上游客户端在类边界被替换为假实现，测试内不建立任何 WebSocket 连接。
"""

import asyncio
import json
import sqlite3
import struct
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.setting import settings
from app.modules.voice_workbench.job import service as service_module
from app.modules.voice_workbench.job.client import TranscriptionResult, TranscriptionSegment, VoiceUpstreamClient, VoiceUpstreamError
from app.utils.password_util import PwdUtil

JOBS_API = "/voice_workbench/jobs"
SERVICES_API = "/voice_workbench/services"
MODELS_API = "/voice_workbench/models"

_JOB_MENUS = ["voice_workbench:job:query", "voice_workbench:job:add", "voice_workbench:job:delete"]


class _FakeUpstream:
    result: TranscriptionResult | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.result = TranscriptionResult(
            text="你好世界",
            segments=[
                TranscriptionSegment(start_time=0, end_time=1000, text="你好"),
                TranscriptionSegment(start_time=1000, end_time=2000, text="世界"),
            ],
            usage={"duration": 2000},
            logid="log-1",
        )
        cls.error = None
        cls.calls = []


async def _fake_transcribe(self, *, model_name: str, uid: str, audio: bytes) -> TranscriptionResult:
    _FakeUpstream.calls.append({"model_name": model_name, "uid": uid, "size": len(audio)})
    if _FakeUpstream.error is not None:
        raise _FakeUpstream.error
    assert _FakeUpstream.result is not None
    return _FakeUpstream.result


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch: pytest.MonkeyPatch):
    _FakeUpstream.reset()
    monkeypatch.setattr(VoiceUpstreamClient, "transcribe", _fake_transcribe)
    yield _FakeUpstream
    _FakeUpstream.reset()


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _wav(duration_sec: int = 1) -> bytes:
    data = b"\x00" * (16000 * 2 * duration_sec)
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data_chunk = b"data" + struct.pack("<I", len(data)) + data
    body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _db_row(sql: str, params: tuple = ()) -> tuple | None:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _db_rows(sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _db_exec(sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _admin_id() -> int:
    return _db_row("select id from sys_user where username = 'admin'")[0]


def _create_pair(
    test_client: TestClient,
    auth_headers: dict,
    *,
    api_key: str | None = "sk-test-key",
    service_status: int = 0,
    model_status: int = 0,
    max_file_mb: int = 20,
    max_duration_sec: int = 300,
) -> tuple[dict, dict]:
    service = test_client.post(
        f"{SERVICES_API}/create",
        json={"name": _uniq("svc"), "api_key": api_key, "status": service_status, "remark": None},
        headers=auth_headers,
    )
    assert service.status_code == 200, service.text
    service_data = service.json()["data"]
    model = test_client.post(
        f"{MODELS_API}/create",
        json={
            "service_id": service_data["id"],
            "display_name": _uniq("豆包"),
            "model_name": "bigmodel",
            "resource_id": "volc.seedasr.sauc.duration",
            "endpoint_url": "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async",
            "max_file_mb": max_file_mb,
            "max_duration_sec": max_duration_sec,
            "status": model_status,
            "remark": None,
        },
        headers=auth_headers,
    )
    assert model.status_code == 200, model.text
    return service_data, model.json()["data"]


def _transcribe(test_client: TestClient, headers: dict, model_id: int, content: bytes, filename: str = "录音.wav"):
    return test_client.post(
        f"{JOBS_API}/transcribe",
        files={"file": (filename, content, "audio/wav")},
        data={"model_id": str(model_id)},
        headers=headers,
    )


def _create_scoped_account(test_client: TestClient, permissions: list[str]) -> dict[str, str]:
    username = _uniq("vw-user")
    now = "2026-11-03 00:00:00.000000"
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        cur = conn.cursor()
        cur.execute(
            'insert into sys_role (name, code, "order", status, description, data_scope, uuid, is_deleted, created_time, updated_time) values (?, ?, 999, 0, ?, 1, ?, 0, ?, ?)',
            (username, f"role-{username}", None, uuid.uuid4().hex, now, now),
        )
        role_id = cur.lastrowid
        for permission in permissions:
            cur.execute("insert into sys_role_menus (role_id, menu_id) select ?, id from sys_menu where permission = ?", (role_id, permission))
        cur.execute(
            "insert into sys_user (username, password, name, is_superuser, status, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, 0, ?, 0, ?, ?)",
            (username, PwdUtil.hash_password("123456"), username, uuid.uuid4().hex, now, now),
        )
        user_id = cur.lastrowid
        cur.execute("insert into sys_user_roles (user_id, role_id) values (?, ?)", (user_id, role_id))
        conn.commit()
    finally:
        conn.close()

    resp = test_client.post("/system/auth/login", data={"username": username, "password": "123456"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _lock_key(user_id: int) -> str:
    return f"voice_workbench:transcribe:lock:{user_id}"


# ─────────────────────── 成功与失败落库 ───────────────────────


def test_transcribe_success_persists_job_and_response(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    content = _wav(1)

    resp = _transcribe(test_client, auth_headers, model["id"], content, filename="会议.wav")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "success" and data["status_label"] == "识别成功"
    assert data["text"] == "你好世界"
    assert [(s["start_time"], s["end_time"], s["text"]) for s in data["segments"]] == [(0, 1000, "你好"), (1000, 2000, "世界")]
    assert data["service_name"] and data["model_display_name"]
    assert data["model_name"] == "bigmodel"
    assert data["original_filename"] == "会议.wav"
    assert data["file_size"] == len(content)
    assert data["duration_sec"] == 1
    assert data["usage"] == {"duration": 2000}
    assert data["elapsed_ms"] is not None and data["created_id"] is not None

    row = _db_row(
        "select status, text, segments_json, usage_json, error, finished_at, created_id from voice_workbench_job where id = ?",
        (data["id"],),
    )
    assert row[0] == "success" and row[1] == "你好世界"
    assert json.loads(row[2])[1]["text"] == "世界"
    assert json.loads(row[3]) == {"duration": 2000}
    assert row[4] is None and row[5] is not None and row[6] is not None

    # 原始音频不落盘：任务表没有任何音频路径列
    columns = {item[1] for item in _db_rows("pragma table_info(voice_workbench_job)")}
    assert not {"audio_path", "file_path", "audio_file", "storage_path"} & columns

    # 请求结束释放每用户锁
    assert asyncio.run(test_client.app.state.redis.get(_lock_key(_admin_id()))) is None


def test_upstream_error_marks_job_failed_and_returns_readable_error(test_client: TestClient, auth_headers: dict, fake_upstream) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    fake_upstream.error = VoiceUpstreamError("上游鉴权失败", code=45000001)

    resp = _transcribe(test_client, auth_headers, model["id"], _wav(1))
    assert resp.status_code == 400, resp.text
    assert resp.json()["msg"] == "上游鉴权失败"

    row = _db_row(
        "select id, status, error, elapsed_ms, finished_at, text from voice_workbench_job where model_pk = ? order by id desc limit 1",
        (model["id"],),
    )
    assert row[1] == "failed" and row[2] == "上游鉴权失败"
    assert row[3] is not None and row[4] is not None and row[5] is None

    # 失败后锁同样释放，用户可以手动重试
    assert asyncio.run(test_client.app.state.redis.get(_lock_key(_admin_id()))) is None


# ─────────────────────── 前置校验（不建任务 / 建失败任务） ───────────────────────


def test_disabled_model_is_rejected_without_creating_job(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers, model_status=1)
    before = _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0]

    resp = _transcribe(test_client, auth_headers, model["id"], _wav(1))
    assert resp.status_code == 400
    assert "已停用" in resp.json()["msg"]
    assert _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0] == before


def test_disabled_service_is_rejected_without_creating_job(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers, service_status=1)
    before = _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0]

    resp = _transcribe(test_client, auth_headers, model["id"], _wav(1))
    assert resp.status_code == 400
    assert "已停用" in resp.json()["msg"]
    assert _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0] == before


def test_service_without_credential_is_rejected_without_creating_job(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers, api_key=None)
    before = _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0]

    resp = _transcribe(test_client, auth_headers, model["id"], _wav(1))
    assert resp.status_code == 400
    assert "尚未配置调用凭证" in resp.json()["msg"]
    assert _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0] == before


def test_oversized_file_creates_failed_job(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers, max_file_mb=1, max_duration_sec=1800)
    content = _wav(1)
    oversized = content[:44] + b"\x00" * (1024 * 1024 + 100)

    resp = _transcribe(test_client, auth_headers, model["id"], oversized)
    assert resp.status_code == 400
    assert "文件大小超过上限" in resp.json()["msg"]

    row = _db_row("select status, error, file_size, duration_sec, finished_at from voice_workbench_job where model_pk = ? order by id desc limit 1", (model["id"],))
    assert row[0] == "failed" and "文件大小超过上限" in row[1]
    assert row[2] == len(oversized) and row[3] == 0 and row[4] is not None


def test_oversized_duration_creates_failed_job(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers, max_duration_sec=1)
    resp = _transcribe(test_client, auth_headers, model["id"], _wav(2))
    assert resp.status_code == 400
    assert "音频时长超过上限" in resp.json()["msg"]

    row = _db_row("select status, error, duration_sec from voice_workbench_job where model_pk = ? order by id desc limit 1", (model["id"],))
    assert row[0] == "failed" and "音频时长超过上限" in row[1] and row[2] == 2


def test_invalid_wav_creates_failed_job(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    resp = _transcribe(test_client, auth_headers, model["id"], b"this is not a wav file")
    assert resp.status_code == 400
    assert "WAV" in resp.json()["msg"]

    row = _db_row("select status, error, duration_sec from voice_workbench_job where model_pk = ? order by id desc limit 1", (model["id"],))
    assert row[0] == "failed" and "WAV" in row[1] and row[2] == 0


# ─────────────────────── 串行锁 ───────────────────────


def test_per_user_lock_conflict_rejects_second_request(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    key = _lock_key(_admin_id())
    asyncio.run(test_client.app.state.redis.set(key, "1", ex=600, nx=True))
    before = _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0]
    try:
        resp = _transcribe(test_client, auth_headers, model["id"], _wav(1))
        assert resp.status_code == 400
        assert "有正在进行的识别任务" in resp.json()["msg"]
        assert _db_row("select count(*) from voice_workbench_job where model_pk = ?", (model["id"],))[0] == before
    finally:
        asyncio.run(test_client.app.state.redis.delete(key))

    # 锁释放后同一用户可以继续
    ok = _transcribe(test_client, auth_headers, model["id"], _wav(1))
    assert ok.status_code == 200, ok.text


# ─────────────────────── 用户隔离与软删 ───────────────────────


def test_jobs_are_owner_only_even_for_superuser(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    alice = _create_scoped_account(test_client, _JOB_MENUS)
    bob = _create_scoped_account(test_client, _JOB_MENUS)

    alice_job = _transcribe(test_client, alice, model["id"], _wav(1)).json()["data"]

    def _visible_ids(headers: dict) -> set[int]:
        items = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 100}, headers=headers).json()["data"]["items"]
        return {item["id"] for item in items}

    assert alice_job["id"] in _visible_ids(alice)
    assert alice_job["id"] not in _visible_ids(bob), "列表不得包含他人任务"
    assert alice_job["id"] not in _visible_ids(auth_headers), "超管也不得看到他人任务"

    assert test_client.get(f"{JOBS_API}/detail/{alice_job['id']}", headers=bob).status_code == 404
    assert test_client.get(f"{JOBS_API}/detail/{alice_job['id']}", headers=auth_headers).status_code == 404
    denied = test_client.request("DELETE", f"{JOBS_API}/delete", json=[alice_job["id"]], headers=bob)
    assert denied.status_code == 400
    assert "无权访问" in denied.json()["msg"]

    # 越权删除必须整体被拒，不能部分生效
    own = _transcribe(test_client, bob, model["id"], _wav(1)).json()["data"]
    mixed = test_client.request("DELETE", f"{JOBS_API}/delete", json=[own["id"], alice_job["id"]], headers=bob)
    assert mixed.status_code == 400
    assert _db_row("select is_deleted from voice_workbench_job where id = ?", (own["id"],))[0] == 0


def test_delete_is_soft_and_owner_scoped(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    job = _transcribe(test_client, auth_headers, model["id"], _wav(1)).json()["data"]

    deleted = test_client.request("DELETE", f"{JOBS_API}/delete", json=[job["id"]], headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    assert _db_row("select is_deleted from voice_workbench_job where id = ?", (job["id"],))[0] == 1

    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 100}, headers=auth_headers).json()["data"]["items"]
    assert all(item["id"] != job["id"] for item in listed)
    assert test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=auth_headers).status_code == 404


# ─────────────────────── 可选模型 ───────────────────────


def test_optionselect_only_lists_fully_usable_models(test_client: TestClient, auth_headers: dict) -> None:
    _s1, usable = _create_pair(test_client, auth_headers)
    _s2, disabled_model = _create_pair(test_client, auth_headers, model_status=1)
    _s3, disabled_service = _create_pair(test_client, auth_headers, service_status=1)
    _s4, no_key = _create_pair(test_client, auth_headers, api_key=None)

    resp = test_client.get(f"{JOBS_API}/models/optionselect", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]
    ids = {item["id"] for item in items}
    assert usable["id"] in ids
    assert not {disabled_model["id"], disabled_service["id"], no_key["id"]} & ids
    target = next(item for item in items if item["id"] == usable["id"])
    assert target["service_name"] and target["model_name"] == "bigmodel"
    assert target["max_file_mb"] == 20 and target["max_duration_sec"] == 300


def test_transcribe_requires_add_permission(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    viewer = _create_scoped_account(test_client, ["voice_workbench:job:query"])
    resp = _transcribe(test_client, viewer, model["id"], _wav(1))
    assert resp.status_code == 403


def test_list_filters_and_default_desc_order(test_client: TestClient, auth_headers: dict) -> None:
    _service, model = _create_pair(test_client, auth_headers)
    first = _transcribe(test_client, auth_headers, model["id"], _wav(1), filename="甲.wav").json()["data"]
    second = _transcribe(test_client, auth_headers, model["id"], _wav(1), filename="乙.wav").json()["data"]

    items = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 100, "model_pk": model["id"]}, headers=auth_headers).json()["data"]["items"]
    ids = [item["id"] for item in items]
    assert ids == [second["id"], first["id"]], "缺省排序必须是 id desc"

    filtered = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 100, "original_filename": "乙"}, headers=auth_headers).json()["data"]["items"]
    assert [item["id"] for item in filtered] == [second["id"]]
    assert first["id"] not in {item["id"] for item in filtered}


# ─────────────────────── 启动恢复 ───────────────────────


@pytest.fixture
async def job_sessionmaker():
    engine = create_async_engine(f"sqlite+aiosqlite:///{settings.DATABASE_NAME}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_recover_interrupted_jobs(monkeypatch: pytest.MonkeyPatch, job_sessionmaker: async_sessionmaker) -> None:
    _service, model = _create_pair_for_recovery()
    _db_exec(
        "insert into voice_workbench_job (service_id, model_pk, model_name, original_filename, file_size, duration_sec, status, uuid, is_deleted, started_at, created_time, updated_time) "
        "values (?, ?, 'bigmodel', 'stale.wav', 32000, 1, 'running', ?, 0, datetime('now'), datetime('now'), datetime('now'))",
        (_service["id"], model["id"], uuid.uuid4().hex),
    )
    job_id = _db_row("select id from voice_workbench_job where original_filename = 'stale.wav' order by id desc limit 1")[0]

    monkeypatch.setattr(service_module, "async_db_session", job_sessionmaker)
    recovered = await service_module.recover_interrupted_jobs()
    assert recovered >= 1
    assert await service_module.recover_interrupted_jobs() == 0

    row = _db_row("select status, error, finished_at from voice_workbench_job where id = ?", (job_id,))
    assert row[0] == "failed" and row[1] == "服务重启导致任务中断" and row[2] is not None


def _create_pair_for_recovery() -> tuple[dict, dict]:
    """恢复测试只查库，直接插入服务/模型行，避免依赖 HTTP 夹具。"""
    now = "2026-11-03 00:00:00.000000"
    _db_exec(
        "insert into voice_workbench_service (name, status, uuid, is_deleted, created_time, updated_time) values (?, 0, ?, 0, ?, ?)",
        (_uniq("recovery-svc"), uuid.uuid4().hex, now, now),
    )
    service_id = _db_row("select id from voice_workbench_service order by id desc limit 1")[0]
    _db_exec(
        "insert into voice_workbench_model (service_id, display_name, model_name, resource_id, endpoint_url, max_file_mb, max_duration_sec, status, uuid, is_deleted, created_time, updated_time) "
        "values (?, '恢复模型', 'bigmodel', 'r', 'wss://example.com/ws', 20, 300, 0, ?, 0, ?, ?)",
        (service_id, uuid.uuid4().hex, now, now),
    )
    model_id = _db_row("select id from voice_workbench_model order by id desc limit 1")[0]
    return {"id": service_id}, {"id": model_id}
