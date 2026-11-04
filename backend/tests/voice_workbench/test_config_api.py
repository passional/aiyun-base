"""语音服务 / 语音识别模型配置 API 测试。

覆盖契约要求：密文落库、掩码、留空保留、名称唯一、删除保护、模型 CRUD、权限，
以及操作日志不泄露凭证明文。
"""

import sqlite3
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config.setting import settings
from app.utils.crypto_util import CryptoUtil
from app.utils.password_util import PwdUtil

SERVICES_API = "/voice_workbench/services"
MODELS_API = "/voice_workbench/models"


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _db_row(sql: str, params: tuple = ()) -> tuple | None:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _db_exec(sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _create_service(test_client: TestClient, auth_headers: dict, **overrides) -> dict:
    payload = {
        "name": _uniq("svc"),
        "api_key": "aaaaaaaa1234",  # 测试夹具：故意低熵，避免密钥扫描误报
        "status": 0,
        "remark": "测试语音服务",
    }
    payload.update(overrides)
    resp = test_client.post(f"{SERVICES_API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _create_model(test_client: TestClient, auth_headers: dict, service_id: int, **overrides) -> dict:
    payload = {
        "service_id": service_id,
        "display_name": _uniq("模型"),
        "model_name": "bigmodel",
        "resource_id": "volc.seedasr.sauc.duration",
        "endpoint_url": "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async",
        "max_file_mb": 20,
        "max_duration_sec": 300,
        "status": 0,
        "remark": None,
    }
    payload.update(overrides)
    resp = test_client.post(f"{MODELS_API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _insert_job(service_id: int, model_pk: int, model_name: str, *, is_deleted: int = 0) -> None:
    _db_exec(
        "insert into voice_workbench_job (service_id, model_pk, model_name, original_filename, file_size, duration_sec, status, uuid, is_deleted, created_time, updated_time) "
        "values (?, ?, ?, ?, 32000, 1, 'failed', ?, ?, datetime('now'), datetime('now'))",
        (service_id, model_pk, model_name, "a.wav", uuid.uuid4().hex, is_deleted),
    )


def _create_scoped_account(test_client: TestClient, permissions: list[str]) -> dict[str, str]:
    """建一个只有指定权限码的普通账号并登录，返回认证头。"""
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


# ─────────────────────── 凭证与掩码 ───────────────────────


def test_create_service_masked_response_and_encrypted_at_rest(test_client: TestClient, auth_headers: dict) -> None:
    data = _create_service(test_client, auth_headers, name=_uniq("enc"))
    assert data["api_key_masked"] == "••••1234"
    assert data["has_api_key"] is True
    assert "aaaaaaaa1234" not in str(data)

    row = _db_row("select api_key_enc from voice_workbench_service where id = ?", (data["id"],))
    assert row and row[0].startswith("gAAAAA"), "凭证必须为 Fernet 密文"
    assert "aaaaaaaa1234" not in row[0]
    assert CryptoUtil.decrypt(row[0]) == "aaaaaaaa1234"

    listed = test_client.get(f"{SERVICES_API}/list", params={"page_no": 1, "page_size": 100}, headers=auth_headers).json()["data"]["items"]
    target = next(item for item in listed if item["id"] == data["id"])
    assert target["api_key_masked"] == "••••1234" and "aaaaaaaa1234" not in str(target)
    detail = test_client.get(f"{SERVICES_API}/detail/{data['id']}", headers=auth_headers).json()["data"]
    assert detail["api_key_masked"] == "••••1234"


def test_create_service_without_credential_is_allowed(test_client: TestClient, auth_headers: dict) -> None:
    data = _create_service(test_client, auth_headers, api_key=None)
    assert data["has_api_key"] is False
    assert data["api_key_masked"] is None


def test_update_service_keeps_credential_when_blank(test_client: TestClient, auth_headers: dict) -> None:
    data = _create_service(test_client, auth_headers)
    before = _db_row("select api_key_enc from voice_workbench_service where id = ?", (data["id"],))[0]

    blank = test_client.put(
        f"{SERVICES_API}/update/{data['id']}",
        json={"name": _uniq("renamed"), "api_key": "", "status": 1, "remark": None},
        headers=auth_headers,
    )
    assert blank.status_code == 200, blank.text
    assert _db_row("select api_key_enc from voice_workbench_service where id = ?", (data["id"],))[0] == before

    omitted = test_client.put(
        f"{SERVICES_API}/update/{data['id']}",
        json={"name": blank.json()["data"]["name"], "status": 0, "remark": None},
        headers=auth_headers,
    )
    assert omitted.status_code == 200, omitted.text
    assert _db_row("select api_key_enc from voice_workbench_service where id = ?", (data["id"],))[0] == before

    refilled = test_client.put(
        f"{SERVICES_API}/update/{data['id']}",
        json={"name": blank.json()["data"]["name"], "api_key": "sk-new-plain-9999", "status": 0, "remark": None},
        headers=auth_headers,
    )
    assert refilled.status_code == 200, refilled.text
    assert refilled.json()["data"]["api_key_masked"] == "••••9999"
    new_enc = _db_row("select api_key_enc from voice_workbench_service where id = ?", (data["id"],))[0]
    assert new_enc != before and CryptoUtil.decrypt(new_enc) == "sk-new-plain-9999"


def test_service_name_is_unique_on_create_and_update(test_client: TestClient, auth_headers: dict) -> None:
    name = _uniq("dup")
    _create_service(test_client, auth_headers, name=name)
    other = _create_service(test_client, auth_headers)

    duplicate = test_client.post(f"{SERVICES_API}/create", json={"name": name, "status": 0}, headers=auth_headers)
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.json()["msg"]

    rename = test_client.put(f"{SERVICES_API}/update/{other['id']}", json={"name": name, "status": 0}, headers=auth_headers)
    assert rename.status_code == 400
    assert "已存在" in rename.json()["msg"]


def test_operation_log_redacts_plaintext_credential(test_client: TestClient, auth_headers: dict) -> None:
    plaintext = "sk-log-hunter-do-not-leak"
    captured: list[dict] = []

    async def _capture(log_data: dict) -> None:
        captured.append(log_data)

    old_methods = settings.OPERATION_RECORD_METHOD
    settings.OPERATION_RECORD_METHOD = ["POST"]
    try:
        with patch("app.core.router_class._write_operation_log_async", new=_capture):
            _create_service(test_client, auth_headers, name=_uniq("log"), api_key=plaintext)
    finally:
        settings.OPERATION_RECORD_METHOD = old_methods

    logs = [entry for entry in captured if entry["request_path"] == f"{SERVICES_API}/create"]
    assert logs, "创建请求应被操作日志采集到"
    assert plaintext not in logs[-1]["request_payload"]
    assert "******" in logs[-1]["request_payload"]
    assert plaintext not in logs[-1]["response_json"]


# ─────────────────────── 模型 CRUD ───────────────────────


def test_model_crud_roundtrip_and_service_name(test_client: TestClient, auth_headers: dict) -> None:
    service = _create_service(test_client, auth_headers)
    model = _create_model(test_client, auth_headers, service["id"], model_name="bigmodel", display_name=_uniq("豆包"))
    assert model["service_id"] == service["id"]
    assert model["service_name"] == service["name"]
    assert model["model_name"] == "bigmodel"
    assert model["resource_id"] == "volc.seedasr.sauc.duration"
    assert model["max_file_mb"] == 20 and model["max_duration_sec"] == 300

    detail = test_client.get(f"{MODELS_API}/detail/{model['id']}", headers=auth_headers).json()["data"]
    assert detail == model

    listed = test_client.get(f"{MODELS_API}/list", params={"service_id": service["id"], "page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]
    assert [item["id"] for item in listed["items"]] == [model["id"]]

    updated = test_client.put(
        f"{MODELS_API}/update/{model['id']}",
        json={
            "service_id": service["id"],
            "display_name": "改名后的模型",
            "model_name": "bigmodel-v2",
            "resource_id": "volc.seedasr.sauc.duration.v2",
            "endpoint_url": "wss://proxy.example.com/asr",
            "max_file_mb": 50,
            "max_duration_sec": 600,
            "status": 1,
            "remark": "已停用",
        },
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["data"]
    assert body["display_name"] == "改名后的模型" and body["model_name"] == "bigmodel-v2"
    assert body["endpoint_url"] == "wss://proxy.example.com/asr"
    assert body["status"] == 1

    deleted = test_client.request("DELETE", f"{MODELS_API}/delete", json=[model["id"]], headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    assert _db_row("select count(*) from voice_workbench_model where id = ?", (model["id"],))[0] == 0


def test_model_requires_existing_service(test_client: TestClient, auth_headers: dict) -> None:
    resp = test_client.post(
        f"{MODELS_API}/create",
        json={
            "service_id": 999999,
            "display_name": "孤儿模型",
            "model_name": "m",
            "resource_id": "r",
            "endpoint_url": "wss://example.com/ws",
            "max_file_mb": 20,
            "max_duration_sec": 300,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "所属语音服务不存在" in resp.json()["msg"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_file_mb": 0},
        {"max_file_mb": 501},
        {"max_duration_sec": 0},
        {"max_duration_sec": 1801},
        {"status": 2},
        {"endpoint_url": "https://example.com/ws"},
    ],
)
def test_model_field_validation(test_client: TestClient, auth_headers: dict, overrides: dict) -> None:
    service = _create_service(test_client, auth_headers)
    payload = {
        "service_id": service["id"],
        "display_name": "模型",
        "model_name": "m",
        "resource_id": "r",
        "endpoint_url": "wss://example.com/ws",
        "max_file_mb": 20,
        "max_duration_sec": 300,
        "status": 0,
    }
    payload.update(overrides)
    resp = test_client.post(f"{MODELS_API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 422, resp.text


def test_optionselect_returns_services_and_models(test_client: TestClient, auth_headers: dict) -> None:
    service = _create_service(test_client, auth_headers)
    model = _create_model(test_client, auth_headers, service["id"])
    services = test_client.get(f"{SERVICES_API}/optionselect", headers=auth_headers).json()["data"]
    assert any(item["id"] == service["id"] for item in services)
    models = test_client.get(f"{MODELS_API}/optionselect", headers=auth_headers).json()["data"]
    assert any(item["id"] == model["id"] for item in models)


# ─────────────────────── 删除保护 ───────────────────────


def test_delete_service_referenced_by_model_is_rejected(test_client: TestClient, auth_headers: dict) -> None:
    service = _create_service(test_client, auth_headers)
    _create_model(test_client, auth_headers, service["id"])
    spare = _create_service(test_client, auth_headers)

    resp = test_client.request("DELETE", f"{SERVICES_API}/delete", json=[service["id"]], headers=auth_headers)
    assert resp.status_code == 400
    assert "语音识别模型" in resp.json()["msg"] and "停用" in resp.json()["msg"]
    assert _db_row("select count(*) from voice_workbench_service where id = ?", (service["id"],))[0] == 1

    # 软删的模型同样算引用：历史不得悬空
    soft_service = _create_service(test_client, auth_headers)
    _db_exec(
        "insert into voice_workbench_model (service_id, display_name, model_name, resource_id, endpoint_url, max_file_mb, max_duration_sec, status, uuid, is_deleted, created_time, updated_time) "
        "values (?, '软删模型', 'm', 'r', 'wss://example.com/ws', 20, 300, 0, ?, 1, datetime('now'), datetime('now'))",
        (soft_service["id"], uuid.uuid4().hex),
    )
    soft = test_client.request("DELETE", f"{SERVICES_API}/delete", json=[soft_service["id"]], headers=auth_headers)
    assert soft.status_code == 400

    ok = test_client.request("DELETE", f"{SERVICES_API}/delete", json=[spare["id"]], headers=auth_headers)
    assert ok.status_code == 200, ok.text
    assert _db_row("select count(*) from voice_workbench_service where id = ?", (spare["id"],))[0] == 0


def test_delete_model_referenced_by_job_is_rejected(test_client: TestClient, auth_headers: dict) -> None:
    service = _create_service(test_client, auth_headers)
    model = _create_model(test_client, auth_headers, service["id"])
    spare = _create_model(test_client, auth_headers, service["id"], display_name=_uniq("备用"))

    _insert_job(service["id"], model["id"], model["model_name"])
    resp = test_client.request("DELETE", f"{MODELS_API}/delete", json=[model["id"]], headers=auth_headers)
    assert resp.status_code == 400
    assert "历史转写任务" in resp.json()["msg"] and "停用" in resp.json()["msg"]

    # 软删的任务仍然算引用
    _db_exec("update voice_workbench_job set is_deleted = 1 where model_pk = ?", (model["id"],))
    again = test_client.request("DELETE", f"{MODELS_API}/delete", json=[model["id"]], headers=auth_headers)
    assert again.status_code == 400

    ok = test_client.request("DELETE", f"{MODELS_API}/delete", json=[spare["id"]], headers=auth_headers)
    assert ok.status_code == 200, ok.text


# ─────────────────────── 权限 ───────────────────────


def test_config_write_requires_permissions(test_client: TestClient) -> None:
    viewer = _create_scoped_account(test_client, ["voice_workbench:service:query"])

    assert test_client.get(f"{SERVICES_API}/list", headers=viewer).status_code == 200
    create = test_client.post(f"{SERVICES_API}/create", json={"name": _uniq("nope"), "status": 0}, headers=viewer)
    assert create.status_code == 403
    update = test_client.put(f"{SERVICES_API}/update/1", json={"name": _uniq("nope"), "status": 0}, headers=viewer)
    assert update.status_code == 403
    delete = test_client.request("DELETE", f"{SERVICES_API}/delete", json=[1], headers=viewer)
    assert delete.status_code == 403

    no_permission = _create_scoped_account(test_client, ["voice_workbench:job:query"])
    assert test_client.get(f"{SERVICES_API}/list", headers=no_permission).status_code == 403
