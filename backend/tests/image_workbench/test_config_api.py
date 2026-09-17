"""绘图服务 / 绘图模型配置 API 测试（任务 2.1~2.4）

- 2.1 CRUD：创建 / 脱敏回显 / 留空沿用 / 操作日志脱敏
- 2.2 绘图模型 CRUD：两级结构、能力声明解析、模型标识原样存取
- 2.3 连通性测试：成功与失败分支，且不产生可见绘图任务
- 2.4 引用保护与停用：有引用拒绝删除、停用后不出现在工作台可选模型
"""

import sqlite3
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.setting import settings
from app.modules.image_workbench.job.client import ImageUpstreamClient
from app.utils.crypto_util import CryptoUtil

SERVICES_API = "/image_workbench/services"
MODELS_API = "/image_workbench/models"


def _create_service(test_client: TestClient, auth_headers: dict, **overrides) -> dict:
    payload = {
        "name": "ark-plan",
        "dialect": "ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "api_key": "sk-plain-abcdef1234",
        "status": 0,
        "remark": "火山方舟 Agent Plan",
    }
    payload.update(overrides)
    resp = test_client.post(f"{SERVICES_API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text  # SuccessResponse 统一 200（仓库约定）
    return resp.json()["data"]


def _db_row(sql: str, params: tuple = ()) -> tuple | None:
    """直连测试库读一行（验证加密落库 / 操作日志等响应看不见的事实）。"""
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


# ─────────────────────── 2.1 CRUD ───────────────────────


def test_create_service_masked_response_and_encrypted_at_rest(test_client: TestClient, auth_headers: dict) -> None:
    """创建成功：响应只回显脱敏占位、无明文；凭证加密落库。"""
    data = _create_service(test_client, auth_headers)
    assert data["name"] == "ark-plan"
    assert data["dialect"] == "ark" and data["dialect_label"] == "火山方舟"
    assert data["api_key_masked"] == "••••1234"
    assert data["has_api_key"] is True
    assert "sk-plain-abcdef1234" not in str(data)

    row = _db_row("select api_key_enc from image_workbench_service where id = ?", (data["id"],))
    assert row and row[0]
    enc = row[0]
    assert "sk-plain-abcdef1234" not in enc
    assert enc.startswith("gAAAAA"), "凭证必须为 Fernet 密文"
    assert CryptoUtil.decrypt(enc) == "sk-plain-abcdef1234"

    # 列表与详情同样脱敏
    listed = test_client.get(f"{SERVICES_API}/list", headers=auth_headers).json()["data"]["items"]
    target = next(item for item in listed if item["id"] == data["id"])
    assert target["api_key_masked"] == "••••1234"
    assert "sk-plain-abcdef1234" not in str(target)
    detail = test_client.get(f"{SERVICES_API}/detail/{data['id']}", headers=auth_headers).json()["data"]
    assert detail["api_key_masked"] == "••••1234" and "sk-plain-abcdef1234" not in str(detail)


def test_create_service_without_credential_is_allowed(test_client: TestClient, auth_headers: dict) -> None:
    """凭证可留空（种子预置的火山服务即为空，避免未配置时误入工作台可选模型）。"""
    data = _create_service(test_client, auth_headers, name="no-key", api_key=None)
    assert data["has_api_key"] is False
    assert data["api_key_masked"] is None


def test_update_service_keeps_credential_when_blank(test_client: TestClient, auth_headers: dict) -> None:
    """编辑留空 → 沿用原凭证；重填 → 更新为新凭证。"""
    data = _create_service(test_client, auth_headers, name="keep-key")
    before = _db_row("select api_key_enc from image_workbench_service where id = ?", (data["id"],))[0]

    blank = test_client.put(
        f"{SERVICES_API}/update/{data['id']}",
        json={
            "name": "keep-key-renamed",
            "dialect": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "",
            "status": 0,
            "remark": None,
        },
        headers=auth_headers,
    )
    assert blank.status_code == 200, blank.text
    body = blank.json()["data"]
    assert body["name"] == "keep-key-renamed" and body["dialect"] == "openai"
    assert body["base_url"] == "https://api.example.com/v1"
    after = _db_row("select api_key_enc from image_workbench_service where id = ?", (data["id"],))[0]
    assert after == before, "留空必须沿用原凭证"

    # 缺省 api_key 字段同样沿用
    omitted = test_client.put(
        f"{SERVICES_API}/update/{data['id']}",
        json={"name": "keep-key-renamed", "dialect": "openai", "base_url": "https://api.example.com/v1", "status": 0},
        headers=auth_headers,
    )
    assert omitted.status_code == 200, omitted.text
    assert _db_row("select api_key_enc from image_workbench_service where id = ?", (data["id"],))[0] == before

    refilled = test_client.put(
        f"{SERVICES_API}/update/{data['id']}",
        json={
            "name": "keep-key-renamed",
            "dialect": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-new-plain-9999",
            "status": 0,
        },
        headers=auth_headers,
    )
    assert refilled.status_code == 200, refilled.text
    assert refilled.json()["data"]["api_key_masked"] == "••••9999"
    new_enc = _db_row("select api_key_enc from image_workbench_service where id = ?", (data["id"],))[0]
    assert new_enc != before and CryptoUtil.decrypt(new_enc) == "sk-new-plain-9999"


def test_update_service_rejects_duplicate_name(test_client: TestClient, auth_headers: dict) -> None:
    """名称唯一：创建重名与改成已存在的名称都被拒。"""
    _create_service(test_client, auth_headers, name="dup-a")
    other = _create_service(test_client, auth_headers, name="dup-b")
    resp = test_client.post(f"{SERVICES_API}/create", json={"name": "dup-a", "dialect": "ark", "base_url": "https://x.example.com/v1"}, headers=auth_headers)
    assert resp.status_code == 400
    resp = test_client.put(
        f"{SERVICES_API}/update/{other['id']}",
        json={"name": "dup-a", "dialect": "ark", "base_url": "https://x.example.com/v1", "status": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_base_url_must_include_scheme(test_client: TestClient, auth_headers: dict) -> None:
    """base_url 必须以 http(s):// 开头（完整 API 前缀，ADR-0005）。"""
    resp = test_client.post(
        f"{SERVICES_API}/create",
        json={"name": "bad-url", "dialect": "ark", "base_url": "ark.cn-beijing.volces.com/api/plan/v3"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_optionselect_returns_services(test_client: TestClient, auth_headers: dict) -> None:
    """下拉选项返回全部绘图服务。"""
    created = _create_service(test_client, auth_headers, name="opt-1")
    items = test_client.get(f"{SERVICES_API}/optionselect", headers=auth_headers).json()["data"]
    assert any(item["id"] == created["id"] for item in items)


def test_operation_log_does_not_contain_plaintext_credential(test_client: TestClient, auth_headers: dict) -> None:
    """操作日志不含凭证明文（spec: service-config / 凭证泄露防护）。

    入参字段名 ``api_key`` 命中 ``OperationLogRoute._redact_sensitive`` 的敏感键集合，
    故请求体落库时凭证被替换为 ``******``。

    这里在路由层的落库回调上取日志记录（而非查 ``sys_operation_log``）：sqlite 测试库里
    后台任务写日志会与请求事务抢锁（见 conftest 说明），拦下采集结果才能确定性断言。
    """
    plaintext = "sk-log-hunter-do-not-leak"
    captured: list[dict] = []

    async def _capture(log_data: dict) -> None:
        captured.append(log_data)

    old_methods = settings.OPERATION_RECORD_METHOD
    settings.OPERATION_RECORD_METHOD = ["POST", "PUT"]
    try:
        with patch("app.core.router_class._write_operation_log_async", new=_capture):
            created = _create_service(test_client, auth_headers, name="log-redact", api_key=plaintext)
    finally:
        settings.OPERATION_RECORD_METHOD = old_methods

    logs = [entry for entry in captured if entry["request_path"] == f"{SERVICES_API}/create"]
    assert logs, "创建请求应被操作日志采集到"
    log = logs[-1]
    payload = log["request_payload"]
    assert "log-redact" in payload, "日志应包含非敏感参数，确认记录的是本用例的请求"
    assert plaintext not in payload, "凭证明文不得进入操作日志"
    assert "******" in payload
    # 响应体同样不含明文
    assert plaintext not in log["response_json"]
    # 库内凭证确实是密文（对照：不是"因为没存"才没泄露）
    assert CryptoUtil.decrypt(_db_row("select api_key_enc from image_workbench_service where id = ?", (created["id"],))[0]) == plaintext


# ─────────────────────── 2.2 绘图模型 CRUD ───────────────────────


def _create_model(test_client: TestClient, auth_headers: dict, service_id: int, **overrides) -> dict:
    payload = {
        "service_id": service_id,
        "model_id": "doubao-seedream-5-0-pro",
        "display_name": "Seedream 5.0 pro",
        "capability": {
            "sizes": ["1K", "1.5K", "2K"],
            "allow_custom_size": False,
            "support_reference": True,
            "max_reference_images": 10,
            "output_formats": ["png", "jpeg"],
            "default_output_format": "png",
            "watermark_default": False,
            "extra_params": {"sequential_image_generation": "disabled"},
        },
        "status": 0,
    }
    payload.update(overrides)
    resp = test_client.post(f"{MODELS_API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_model_belongs_to_service_and_capability_roundtrip(test_client: TestClient, auth_headers: dict) -> None:
    """两级结构：模型挂在服务下；能力声明按 D3 逐字段存取一致。"""
    service = _create_service(test_client, auth_headers, name="caps")
    model = _create_model(test_client, auth_headers, service["id"])
    assert model["service_id"] == service["id"]
    assert model["service_name"] == "caps"
    assert model["capability"]["sizes"] == ["1K", "1.5K", "2K"]
    assert model["capability"]["support_reference"] is True
    assert model["capability"]["max_reference_images"] == 10
    assert model["capability"]["default_output_format"] == "png"
    assert model["capability"]["watermark_default"] is False
    assert model["capability"]["extra_params"] == {"sequential_image_generation": "disabled"}

    # 重新打开（详情）原样回显
    detail = test_client.get(f"{MODELS_API}/detail/{model['id']}", headers=auth_headers).json()["data"]
    assert detail["capability"] == model["capability"]

    # 按 service_id 过滤的列表只含该服务下的模型
    other = _create_service(test_client, auth_headers, name="caps-other")
    _create_model(test_client, auth_headers, other["id"], model_id="other-model", display_name="Other")
    listed = test_client.get(f"{MODELS_API}/list", params={"service_id": service["id"], "page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]
    assert [item["id"] for item in listed["items"]] == [model["id"]]
    all_listed = test_client.get(f"{MODELS_API}/list", params={"page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]
    assert {item["id"] for item in all_listed["items"]} >= {model["id"]}


def test_capability_defaults_applied_when_omitted(test_client: TestClient, auth_headers: dict) -> None:
    """能力声明缺省时按 D3 默认值落库（尤其 watermark_default=false）。"""
    service = _create_service(test_client, auth_headers, name="defaults")
    model = _create_model(test_client, auth_headers, service["id"], model_id="default-model", display_name="D", capability={})
    cap = model["capability"]
    assert cap["sizes"] == []
    assert cap["allow_custom_size"] is False
    assert cap["support_reference"] is True
    assert cap["max_reference_images"] == 10
    assert cap["output_formats"] == ["png", "jpeg"]
    assert cap["default_output_format"] == "png"
    assert cap["watermark_default"] is False, "上游默认加水印，必须显式给出 false 作为默认值"
    assert cap["extra_params"] == {}


def test_invalid_capability_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """能力声明按 D3 结构校验：默认格式必须可选、张数不能为负、取值不可重复。"""
    service = _create_service(test_client, auth_headers, name="bad-caps")
    url = f"{MODELS_API}/create"

    def _post(capability: dict):
        return test_client.post(
            url,
            json={"service_id": service["id"], "model_id": "m-bad", "display_name": "M", "capability": capability},
            headers=auth_headers,
        )

    assert _post({"output_formats": ["png"], "default_output_format": "jpeg"}).status_code == 422
    assert _post({"max_reference_images": -1}).status_code == 422
    assert _post({"sizes": ["1K", "1K"]}).status_code == 422


def test_unsupported_reference_normalizes_max_images(test_client: TestClient, auth_headers: dict) -> None:
    """不支持参考图的模型，张数上限归一为 0（前端据此不出现参考图入口）。"""
    service = _create_service(test_client, auth_headers, name="no-ref")
    model = _create_model(
        test_client,
        auth_headers,
        service["id"],
        model_id="no-ref-model",
        display_name="N",
        capability={"support_reference": False, "max_reference_images": 10},
    )
    assert model["capability"]["support_reference"] is False
    assert model["capability"]["max_reference_images"] == 0


def test_model_id_preserved_verbatim(test_client: TestClient, auth_headers: dict) -> None:
    """模型标识原样存取：带不带日期后缀是两条不同记录，不做规范化改写。"""
    service = _create_service(test_client, auth_headers, name="verbatim")
    short = _create_model(test_client, auth_headers, service["id"], model_id="doubao-seedream-5-0-pro", display_name="短")
    long = _create_model(test_client, auth_headers, service["id"], model_id="doubao-seedream-5-0-pro-260628", display_name="长")
    assert short["model_id"] == "doubao-seedream-5-0-pro"
    assert long["model_id"] == "doubao-seedream-5-0-pro-260628"
    assert short["id"] != long["id"]
    detail = test_client.get(f"{MODELS_API}/detail/{long['id']}", headers=auth_headers).json()["data"]
    assert detail["model_id"] == "doubao-seedream-5-0-pro-260628"

    # 同一服务下重复标识被拒（两级内的唯一性），而不是被"规范化后"静默合并
    dup = test_client.post(
        f"{MODELS_API}/create",
        json={"service_id": service["id"], "model_id": "doubao-seedream-5-0-pro-260628", "display_name": "再来一个"},
        headers=auth_headers,
    )
    assert dup.status_code == 400
    assert "已存在" in dup.json()["msg"]


def test_model_requires_existing_service(test_client: TestClient, auth_headers: dict) -> None:
    """挂在不存在的绘图服务下 → 拒绝。"""
    resp = test_client.post(
        f"{MODELS_API}/create",
        json={"service_id": 999999, "model_id": "orphan", "display_name": "孤儿"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_update_model_overrides_capability(test_client: TestClient, auth_headers: dict) -> None:
    """编辑能力声明整体覆盖（含把参考图关掉）。"""
    service = _create_service(test_client, auth_headers, name="edit-caps")
    model = _create_model(test_client, auth_headers, service["id"], model_id="edit-me", display_name="E")
    resp = test_client.put(
        f"{MODELS_API}/update/{model['id']}",
        json={
            "service_id": service["id"],
            "model_id": "edit-me",
            "display_name": "E2",
            "capability": {"sizes": ["2K", "3K", "4K"], "support_reference": False},
            "status": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["display_name"] == "E2" and data["status"] == 1
    assert data["capability"]["sizes"] == ["2K", "3K", "4K"]
    assert data["capability"]["support_reference"] is False and data["capability"]["max_reference_images"] == 0


# ────────────────────── 2.3 连通性测试 ───────────────────────


def _mock_upstream(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """把 ``ImageUpstreamClient`` 的 HTTP 出口换成 MockTransport，并记录请求。"""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def _client(self, timeout=None):
        return httpx.AsyncClient(timeout=timeout, transport=httpx.MockTransport(wrapped), trust_env=False)

    monkeypatch.setattr(ImageUpstreamClient, "_client", _client)
    return calls


def _models_ok_handler(ids: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})

    return handler


def _job_count() -> int:
    return _db_row("select count(*) from image_workbench_job")[0]


def test_test_connection_success_with_form_values(test_client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """凭证有效：用所填 base_url 探测，返回上游模型标识供核对；不落库、不产生任务。"""
    calls = _mock_upstream(monkeypatch, _models_ok_handler(["doubao-seedream-5-0-pro", "doubao-seedream-5-0-lite"]))
    jobs_before = _job_count()
    resp = test_client.post(
        f"{SERVICES_API}/test-connection",
        json={
            "dialect": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "api_key": "sk-probe-key",
            "model_id": "doubao-seedream-5-0-pro",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["success"] is True
    assert data["model_ids"] == ["doubao-seedream-5-0-lite", "doubao-seedream-5-0-pro"]
    assert data["model_found"] is True
    assert "doubao-seedream-5-0-pro" in data["message"]
    # 只读探测：URL 为完整前缀 + /models（多级路径原样保留），带 Bearer 鉴权
    assert len(calls) == 1
    assert str(calls[0].url) == "https://ark.cn-beijing.volces.com/api/plan/v3/models"
    assert calls[0].headers["Authorization"] == "Bearer sk-probe-key"
    assert calls[0].method == "GET"
    assert _job_count() == jobs_before, "连通性测试不得产生绘图任务"


def test_test_connection_reports_model_not_found(test_client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """连通成功但待核对模型不在上游列表中：仍算连通，但明确提示。"""
    _mock_upstream(monkeypatch, _models_ok_handler(["some-other-model"]))
    resp = test_client.post(
        f"{SERVICES_API}/test-connection",
        json={"dialect": "ark", "base_url": "https://x.example.com/v1", "api_key": "sk-1", "model_id": "doubao-seedream-5-0-pro"},
        headers=auth_headers,
    )
    data = resp.json()["data"]
    assert data["success"] is True and data["model_found"] is False
    assert "未出现" in data["message"]


def test_test_connection_failure_invalid_credential(test_client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """凭证无效：返回明确失败原因，且不影响绘图服务保存（探测不写库）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    _mock_upstream(monkeypatch, handler)
    jobs_before = _job_count()
    resp = test_client.post(
        f"{SERVICES_API}/test-connection",
        json={"dialect": "openai", "base_url": "https://api.example.com/v1", "api_key": "sk-bad"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["success"] is False
    assert "401" in data["message"] and "Invalid API key" in data["message"]
    assert _job_count() == jobs_before


def test_test_connection_failure_network_error(test_client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url 不可达：返回明确失败原因。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_upstream(monkeypatch, handler)
    resp = test_client.post(
        f"{SERVICES_API}/test-connection",
        json={"dialect": "ark", "base_url": "https://unreachable.example.com/api/plan/v3", "api_key": "sk-x"},
        headers=auth_headers,
    )
    data = resp.json()["data"]
    assert data["success"] is False
    assert "网络请求失败" in data["message"]


def test_test_connection_falls_back_to_saved_values(test_client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """用已存值测试：``/{id}/test-connection`` 回退库内 base_url 与加密凭证。"""
    service = _create_service(test_client, auth_headers, name="saved-probe", api_key="sk-saved-secret")
    calls = _mock_upstream(monkeypatch, _models_ok_handler(["m1"]))

    resp = test_client.post(f"{SERVICES_API}/{service['id']}/test-connection", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["success"] is True
    assert data["base_url"] == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert calls[0].headers["Authorization"] == "Bearer sk-saved-secret", "库内凭证需解密后上行"

    # 表单值优先：填了新 base_url 与凭证则用新的
    calls2 = _mock_upstream(monkeypatch, _models_ok_handler(["m1"]))
    resp = test_client.post(
        f"{SERVICES_API}/{service['id']}/test-connection",
        json={"base_url": "https://override.example.com/v1", "api_key": "sk-override"},
        headers=auth_headers,
    )
    assert resp.json()["data"]["base_url"] == "https://override.example.com/v1"
    assert str(calls2[0].url) == "https://override.example.com/v1/models"
    assert calls2[0].headers["Authorization"] == "Bearer sk-override"


def test_test_connection_without_credential_reports_clearly(test_client: TestClient, auth_headers: dict) -> None:
    """未配置凭证（种子预置的火山服务即如此）→ 明确提示先填凭证。"""
    service = _create_service(test_client, auth_headers, name="no-cred", api_key=None)
    resp = test_client.post(f"{SERVICES_API}/{service['id']}/test-connection", json={}, headers=auth_headers)
    data = resp.json()["data"]
    assert data["success"] is False
    assert "凭证" in data["message"]


# ────────────────── 2.4 引用保护与停用 ─────────────────────


def _insert_job(service_id: int, model_pk: int, model_id: str) -> None:
    """直插一条绘图任务（引用保护的判据是"是否存在引用"，无需走生成链路）。"""
    import uuid as _uuid

    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute(
            "insert into image_workbench_job "
            "(service_id, model_pk, model_id, prompt, status, uuid, is_deleted, created_time, updated_time) "
            "values (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (service_id, model_pk, model_id, "p", "success", _uuid.uuid4().hex, 0),
        )
        conn.commit()
    finally:
        conn.close()


def test_delete_referenced_service_rejected_with_deactivate_hint(test_client: TestClient, auth_headers: dict) -> None:
    """被绘图任务引用的绘图服务拒绝删除，并提示可停用；未被引用的可删。"""
    service = _create_service(test_client, auth_headers, name="ref-svc")
    model = _create_model(test_client, auth_headers, service["id"], model_id="ref-model", display_name="R")
    spare = _create_service(test_client, auth_headers, name="spare-svc")

    # 无引用：可删除（连同其下模型，FK ondelete=CASCADE）
    resp = test_client.request("DELETE", f"{SERVICES_API}/delete", json=[spare["id"]], headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert _db_row("select count(*) from image_workbench_service where id = ?", (spare["id"],))[0] == 0

    # 有引用：拒绝删除，提示可停用，且记录仍在
    _insert_job(service["id"], model["id"], "ref-model")
    resp = test_client.request("DELETE", f"{SERVICES_API}/delete", json=[service["id"]], headers=auth_headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "历史绘图任务" in body["msg"] and "停用" in body["msg"]
    assert "ref-svc" in body["msg"], "提示里应带上绘图服务名称"
    assert _db_row("select count(*) from image_workbench_service where id = ?", (service["id"],))[0] == 1


def test_delete_referenced_model_rejected_with_deactivate_hint(test_client: TestClient, auth_headers: dict) -> None:
    """被绘图任务引用的绘图模型拒绝删除并提示可停用。"""
    service = _create_service(test_client, auth_headers, name="ref-svc-2")
    model = _create_model(test_client, auth_headers, service["id"], model_id="ref-model-2", display_name="R2")
    spare = _create_model(test_client, auth_headers, service["id"], model_id="spare-model", display_name="S")
    _insert_job(service["id"], model["id"], "ref-model-2")

    resp = test_client.request("DELETE", f"{MODELS_API}/delete", json=[model["id"]], headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert "历史绘图任务" in resp.json()["msg"] and "停用" in resp.json()["msg"]

    resp = test_client.request("DELETE", f"{MODELS_API}/delete", json=[spare["id"]], headers=auth_headers)
    assert resp.status_code == 200, "无引用的模型应可删除"


def test_disabled_model_absent_from_workbench_optionselect(test_client: TestClient, auth_headers: dict) -> None:
    """停用后不出现在工作台可选模型中（模型停用 / 服务停用 / 服务无凭证，三种都不可选）。"""
    service = _create_service(test_client, auth_headers, name="opt-svc", api_key="sk-has-key")
    enabled = _create_model(test_client, auth_headers, service["id"], model_id="opt-enabled", display_name="E")
    disabled_model = _create_model(test_client, auth_headers, service["id"], model_id="opt-disabled", display_name="D", status=1)
    no_cred_service = _create_service(test_client, auth_headers, name="opt-nocred", api_key=None)
    no_cred_model = _create_model(test_client, auth_headers, no_cred_service["id"], model_id="opt-nocred-model", display_name="N")

    def _option_ids() -> set[int]:
        resp = test_client.get("/image_workbench/jobs/models/optionselect", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        return {item["id"] for item in resp.json()["data"]}

    ids = _option_ids()
    assert enabled["id"] in ids
    assert disabled_model["id"] not in ids, "停用的绘图模型不得出现在可选列表中"
    assert no_cred_model["id"] not in ids, "服务未配置凭证时其模型不得出现在可选列表中"

    # 服务停用 → 其下模型整体不可选；重新启用并配好凭证后恢复
    resp = test_client.put(
        f"{SERVICES_API}/update/{service['id']}",
        json={"name": "opt-svc", "dialect": "ark", "base_url": "https://ark.example.com/api/plan/v3", "status": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids = _option_ids()
    assert enabled["id"] not in ids and disabled_model["id"] not in ids

    resp = test_client.put(
        f"{SERVICES_API}/update/{service['id']}",
        json={"name": "opt-svc", "dialect": "ark", "base_url": "https://ark.example.com/api/plan/v3", "status": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids = _option_ids()
    assert enabled["id"] in ids, "服务恢复启用后其启用中的模型应重新可选"
    assert disabled_model["id"] not in ids

    # 停用不影响已有历史任务：引用它的绘图任务记录仍完整保留（列表/下载的
    # 端到端断言随 4.6 的历史接口一并补齐）
    _insert_job(service["id"], disabled_model["id"], "opt-disabled")
    assert _db_row("select count(*) from image_workbench_job where model_pk = ?", (disabled_model["id"],))[0] == 1
