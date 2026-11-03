"""预置语音服务/模型与菜单权限守卫。

规格要求开箱即有「火山引擎语音」服务（凭证为空）与默认 ASR 模型，管理员只需填 key；
菜单必须暴露两个页面与全部权限码，否则功能存在但入口不可达。
"""

import sqlite3

from fastapi.testclient import TestClient

from app.config.setting import settings

_EXPECTED_PERMISSIONS = {
    "voice_workbench:service:query",
    "voice_workbench:service:add",
    "voice_workbench:service:edit",
    "voice_workbench:service:delete",
    "voice_workbench:job:query",
    "voice_workbench:job:add",
    "voice_workbench:job:delete",
}

_ROUTE_NAMES = ("VoiceWorkbench", "VoiceWorkbenchJob", "VoiceWorkbenchConfig")


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_seeded_service_has_no_credential_and_default_model(test_client: TestClient) -> None:
    service = _rows("select id, name, api_key_enc, status from voice_workbench_service where name = '火山引擎语音'")
    assert len(service) == 1, "应预置且仅预置一条「火山引擎语音」服务"
    service_id, _name, api_key_enc, status = service[0]
    assert api_key_enc is None, "种子 MUST NOT 含任何调用凭证"
    assert status == 0

    models = _rows(
        "select display_name, model_name, resource_id, endpoint_url, max_file_mb, max_duration_sec, status "
        "from voice_workbench_model where service_id = ?",
        (service_id,),
    )
    assert len(models) == 1
    assert models[0] == (
        "豆包流式语音识别 2.0",
        "bigmodel",
        "volc.seedasr.sauc.duration",
        "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async",
        20,
        300,
        0,
    )


def test_menu_tree_exposes_both_pages_and_permissions(test_client: TestClient) -> None:
    placeholders = ",".join("?" for _ in _ROUTE_NAMES)
    rows = _rows(
        f"select id, name, route_name, route_path, component_path, keep_alive, parent_id from sys_menu where route_name in ({placeholders})",
        _ROUTE_NAMES,
    )
    by_route = {row[2]: row for row in rows}
    assert set(by_route) == set(_ROUTE_NAMES), f"菜单树缺失: {set(_ROUTE_NAMES) - set(by_route)}"

    root = by_route["VoiceWorkbench"]
    assert root[3] == "/voice-workbench" and root[6] is None
    assert root[5] == 1

    job = by_route["VoiceWorkbenchJob"]
    config = by_route["VoiceWorkbenchConfig"]
    assert job[3] == "job" and job[4] == "voice_workbench/job/index"
    assert config[3] == "config" and config[4] == "voice_workbench/config/index"
    assert job[6] == root[0] and config[6] == root[0]
    assert job[5] == 1 and config[5] == 1, "KeepAlive 按组件名匹配，必须打开"

    seeded = {
        row[0]
        for row in _rows("select permission from sys_menu where permission like 'voice_workbench:%' and type = 3")
    }
    assert seeded == _EXPECTED_PERMISSIONS, f"多: {seeded - _EXPECTED_PERMISSIONS}；少: {_EXPECTED_PERMISSIONS - seeded}"
