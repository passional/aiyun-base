"""平台管理 API 测试（任务 2.1~2.5）

- 2.1 CRUD：创建/脱敏回显/删除保护
- 2.2 测试连接（mock 外部响应，成功与失败分支）
- 2.3 渠道目录实时拉取（字段映射 + 不缓存）
- 2.4 路由可达性提示（备用渠道标记）
- 2.5 菜单种子与 RBAC（超管可见、普通角色 403）
"""

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config.setting import settings
from app.utils.crypto_util import CryptoUtil

API = "/channel_eval/platforms"


@pytest.fixture(autouse=True)
def _no_external_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用运行创建对外部平台的建令牌 + pin 探测调用（本文件仅删除保护用例会创建运行）。"""

    async def _fake_ensure(db, platform, run):
        run.eval_token_id = 99
        run.eval_token_key_enc = CryptoUtil.encrypt("sk-eval-run")
        return "sk-eval-run"

    async def _fake_probe(base_url, token_key):
        return True, "指定渠道（pin）可用"

    monkeypatch.setattr("app.modules.channel_eval.run.service.ensure_eval_token", _fake_ensure)
    monkeypatch.setattr("app.modules.channel_eval.run.service.probe_pin_capability", _fake_probe)


def _create_platform(test_client: TestClient, auth_headers: dict, **overrides) -> dict:
    payload = {
        "name": "apiworld",
        "type": "admin",
        "base_url": "https://api.example.com",
        "admin_key": "sk-admin-plain-1234",
        "test_key": "sk-test-plain-5678",
        "is_baseline": False,
        "status": 0,
    }
    payload.update(overrides)
    resp = test_client.post(f"{API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text  # SuccessResponse 统一 200（仓库约定）
    return resp.json()["data"]


# ─────────────────────── 2.1 CRUD ───────────────────────


def test_create_platform_and_masked_response(test_client: TestClient, auth_headers: dict) -> None:
    """创建成功；响应只回显脱敏占位，不出现明文/密文凭证。"""
    data = _create_platform(test_client, auth_headers, admin_user_id="42")
    assert data["name"] == "apiworld"
    assert data["admin_user_id"] == "42"
    assert data["admin_key_masked"] == "••••1234"
    assert data["test_key_masked"] == "••••5678"
    body = str(data)
    assert "sk-admin-plain-1234" not in body
    assert "sk-test-plain-5678" not in body

    # 列表同样脱敏
    resp = test_client.get(f"{API}/list", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items, "列表应为空列表以上"
    target = next(item for item in items if item["name"] == "apiworld")
    assert target["admin_key_masked"] == "••••1234"
    assert target["admin_user_id"] == "42"
    assert "sk-admin-plain-1234" not in str(target)


def test_platform_credentials_encrypted_at_rest(test_client: TestClient, auth_headers: dict) -> None:
    """凭证加密落库（Fernet 密文，非明文）。"""
    data = _create_platform(test_client, auth_headers)
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        row = conn.execute("select admin_key_enc, test_key_enc from channel_eval_platform where id = ?", (data["id"],)).fetchone()
    finally:
        conn.close()
    assert row and row[0] and row[1]
    admin_enc, test_enc = row
    assert "sk-admin-plain-1234" not in admin_enc
    assert "sk-test-plain-5678" not in test_enc
    assert admin_enc.startswith("gAAAAA") and test_enc.startswith("gAAAAA")


def test_create_platform_requires_admin_key(test_client: TestClient, auth_headers: dict) -> None:
    """管理型平台缺管理凭证 → 拒绝。"""
    resp = test_client.post(
        f"{API}/create",
        json={
            "name": "nokey",
            "type": "admin",
            "base_url": "https://x.example.com",
            "test_key": "sk-test",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_delete_platform_with_runs_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """删除存在测试运行的平台 → 拒绝并提示。"""
    platform = _create_platform(test_client, auth_headers, name="has-runs")
    # 创建一条运行（禁用引擎启动，保持 pending 以便校验删除保护）
    with patch(
        "app.modules.channel_eval.run.service.ChannelEvalRunService._launch_after_commit",
        lambda self, run_id: None,
    ):
        resp = test_client.post(
            "/channel_eval/runs/create",
            json={
                "platform_id": platform["id"],
                "combos": [{"channel_id": 70, "channel_name": "ch70", "model": "m1"}],
                "request_count": 1,
                "warmup": False,
                "interval_seconds": 0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
    resp = test_client.request("DELETE", f"{API}/delete", json=[platform["id"]], headers=auth_headers)
    assert resp.status_code == 400
    assert "历史运行数据" in resp.json()["msg"]

    # 无运行的平台可删除
    empty_platform = _create_platform(test_client, auth_headers, name="no-runs")
    resp = test_client.request("DELETE", f"{API}/delete", json=[empty_platform["id"]], headers=auth_headers)
    assert resp.status_code == 200


def test_update_platform_keep_credentials(test_client: TestClient, auth_headers: dict) -> None:
    """编辑时凭证留空 → 保留原值；重填 → 更新；管理用户ID可显式清空。"""
    platform = _create_platform(test_client, auth_headers, name="edit-me", admin_user_id="42")
    # 字段缺省(不传) → 保留原值
    resp = test_client.put(
        f"{API}/update/{platform['id']}",
        json={
            "name": "edit-me",
            "type": "admin",
            "base_url": "https://api.example.com",
            "admin_key": "",
            "test_key": "",
            "is_baseline": True,
            "status": 0,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["admin_key_masked"] == "••••1234"  # 保留原值
    assert data["admin_user_id"] == "42"  # 缺省保留
    assert data["is_baseline"] is True

    # 显式清空 → 移除
    resp = test_client.put(
        f"{API}/update/{platform['id']}",
        json={
            "name": "edit-me",
            "type": "admin",
            "base_url": "https://api.example.com",
            "admin_key": "",
            "test_key": "",
            "admin_user_id": "",
            "is_baseline": False,
            "status": 0,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["admin_user_id"] is None


# ─────────────────────── 2.2 测试连接 ───────────────────────


def test_test_connection_success_and_failure(test_client: TestClient, auth_headers: dict) -> None:
    """mock 外部响应覆盖成功与失败分支；测试连接不依赖已保存平台。"""
    with (
        patch(
            "app.modules.channel_eval.platform.service.test_admin_connection",
            new=AsyncMock(return_value=(True, "管理凭证有效（用户: root）")),
        ),
        patch(
            "app.modules.channel_eval.platform.service.probe_pin_capability",
            new=AsyncMock(return_value=(True, "指定渠道（pin）可用")),
        ),
    ):
        resp = test_client.post(
            f"{API}/test-connection",
            json={
                "type": "admin",
                "base_url": "https://api.example.com",
                "admin_key": "sk-admin",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is True and "管理凭证有效" in data["message"]
        assert "指定渠道（pin）可用" in data["message"]

    with patch(
        "app.modules.channel_eval.platform.service.test_admin_connection",
        new=AsyncMock(return_value=(False, "HTTP 401: 无效凭证")),
    ):
        resp = test_client.post(
            f"{API}/test-connection",
            json={
                "type": "admin",
                "base_url": "https://api.example.com",
                "admin_key": "sk-bad",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is False and "401" in data["message"]


def test_test_connection_pin_probe_failure_for_non_admin(test_client: TestClient, auth_headers: dict) -> None:
    """管理凭证有效但属普通用户（pin 探测 403）→ 测试连接失败并给出明确原因。"""
    with (
        patch(
            "app.modules.channel_eval.platform.service.test_admin_connection",
            new=AsyncMock(return_value=(True, "管理凭证有效（用户: user1）")),
        ),
        patch(
            "app.modules.channel_eval.platform.service.probe_pin_capability",
            new=AsyncMock(return_value=(False, "指定渠道不可用：该平台管理凭证对应的是普通用户")),
        ),
    ):
        resp = test_client.post(
            f"{API}/test-connection",
            json={
                "type": "admin",
                "base_url": "https://api.example.com",
                "admin_key": "sk-normal-user",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is False and "普通用户" in data["message"]


def test_test_connection_for_saved_platform(test_client: TestClient, auth_headers: dict) -> None:
    """POST /platforms/{id}/test-connection：无 body 时回退库内凭证与库内管理用户ID。"""
    platform = _create_platform(test_client, auth_headers, name="saved-conn", admin_user_id="42")
    with (
        patch(
            "app.modules.channel_eval.platform.service.test_admin_connection",
            new=AsyncMock(return_value=(True, "ok")),
        ) as mocked,
        patch(
            "app.modules.channel_eval.platform.service.probe_pin_capability",
            new=AsyncMock(return_value=(True, "指定渠道（pin）可用")),
        ),
    ):
        resp = test_client.post(f"{API}/{platform['id']}/test-connection", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["success"] is True
        # 回退到库内解密凭证与库内管理用户ID
        args = mocked.call_args[0]
        assert args[0] == "https://api.example.com"
        assert args[1].startswith("gAAAAA") is False  # 传的是明文
        assert mocked.call_args.kwargs["admin_user_id"] == "42"


# ─────────────────────── 2.3 渠道目录实时拉取 ───────────────────────

_RAW_CHANNELS = [
    {
        "id": 70,
        "name": "【京东云】Claude_B类",
        "status": 1,
        "group": "default,vip",
        "priority": 10,
        "weight": 5,
        "models": "claude-opus-5,claude-fable-5",
    },
    {
        "id": 72,
        "name": "【UCloud】Claude",
        "status": 2,
        "group": "default",
        "priority": 10,
        "weight": 0,
        "models": "claude-opus-5",
    },
]


def test_get_platform_channels_mapping_and_no_cache(test_client: TestClient, auth_headers: dict) -> None:
    """字段映射正确；每次实时拉取（不缓存）。"""
    platform = _create_platform(test_client, auth_headers, name="catalog")
    fake_client = MagicMock()
    list_channels = AsyncMock(side_effect=lambda: [dict(c) for c in _RAW_CHANNELS])
    fake_client.list_channels = list_channels
    with patch("app.modules.channel_eval.platform.service.build_client", return_value=fake_client):
        for _ in range(2):
            resp = test_client.get(f"{API}/{platform['id']}/channels", headers=auth_headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["platform_id"] == platform["id"]
            assert len(data["channels"]) == 2
            ch70 = next(c for c in data["channels"] if c["channel_id"] == 70)
            assert ch70["name"] == "【京东云】Claude_B类"
            assert ch70["enabled"] is True
            assert ch70["groups"] == ["default", "vip"]
            assert ch70["priority"] == 10 and ch70["weight"] == 5
            assert ch70["models"] == ["claude-opus-5", "claude-fable-5"]
            ch72 = next(c for c in data["channels"] if c["channel_id"] == 72)
            assert ch72["enabled"] is False
    assert list_channels.await_count == 2  # 不缓存


def test_get_platform_channels_failure(test_client: TestClient, auth_headers: dict) -> None:
    """目录拉取失败 → 明确错误（不展示过期缓存）。"""
    platform = _create_platform(test_client, auth_headers, name="catalog-fail")
    fake_client = MagicMock()
    fake_client.list_channels = AsyncMock(side_effect=RuntimeError("connection refused"))
    with patch("app.modules.channel_eval.platform.service.build_client", return_value=fake_client):
        resp = test_client.get(f"{API}/{platform['id']}/channels", headers=auth_headers)
        assert resp.status_code == 400
        assert "拉取渠道目录失败" in resp.json()["msg"]


def test_baseline_platform_catalog_implicit_channel(test_client: TestClient, auth_headers: dict) -> None:
    """user 型平台（基准渠道）：整平台视为一条隐式渠道，模型来自 /v1/models。"""
    platform = _create_platform(test_client, auth_headers, name="aihubmix", type="user", admin_key=None, is_baseline=True)
    fake_client = MagicMock()
    fake_client.list_models = AsyncMock(return_value=["gpt-5.6", "claude-opus-5"])
    with patch("app.modules.channel_eval.platform.service.NewApiClient", return_value=fake_client):
        resp = test_client.get(f"{API}/{platform['id']}/channels", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["is_baseline"] is True
        assert len(data["channels"]) == 1
        implicit = data["channels"][0]
        assert implicit["channel_id"] == 0
        assert implicit["models"] == ["gpt-5.6", "claude-opus-5"]


# ─────────────────────── 2.4 路由可达性提示 ───────────────────────


def test_standby_marking_tiered_and_flat() -> None:
    """同分组同模型优先级分层 → 备用渠道标记；平权 → 不标记。"""
    from app.modules.channel_eval.platform.schema import ChannelEvalChannelItemSchema
    from app.modules.channel_eval.platform.service import mark_standby_channels

    tiered = [
        ChannelEvalChannelItemSchema(
            channel_id=1,
            name="A",
            status=1,
            enabled=True,
            groups=["default"],
            priority=10,
            weight=0,
            models=["m1"],
        ),
        ChannelEvalChannelItemSchema(
            channel_id=2,
            name="B",
            status=1,
            enabled=True,
            groups=["default"],
            priority=0,
            weight=0,
            models=["m1"],
        ),
    ]
    mark_standby_channels(tiered)
    by_id = {c.channel_id: c for c in tiered}
    assert by_id[1].is_standby is False
    assert by_id[2].is_standby is True
    assert "不承接线上常规流量" in (by_id[2].standby_hint or "")

    # 平权：同分组同优先级 → 均非备用
    flat = [
        ChannelEvalChannelItemSchema(
            channel_id=3,
            name="C",
            status=1,
            enabled=True,
            groups=["default"],
            priority=10,
            weight=0,
            models=["m1"],
        ),
        ChannelEvalChannelItemSchema(
            channel_id=4,
            name="D",
            status=1,
            enabled=True,
            groups=["default"],
            priority=10,
            weight=0,
            models=["m1"],
        ),
    ]
    mark_standby_channels(flat)
    assert all(c.is_standby is False for c in flat)

    # 分组不同 → 不互为备用
    diff_group = [
        ChannelEvalChannelItemSchema(
            channel_id=5,
            name="E",
            status=1,
            enabled=True,
            groups=["g1"],
            priority=10,
            weight=0,
            models=["m1"],
        ),
        ChannelEvalChannelItemSchema(
            channel_id=6,
            name="F",
            status=1,
            enabled=True,
            groups=["g2"],
            priority=0,
            weight=0,
            models=["m1"],
        ),
    ]
    mark_standby_channels(diff_group)
    assert all(c.is_standby is False for c in diff_group)


# ─────────────────────── 2.5 菜单与 RBAC ───────────────────────


def test_menu_seed_and_rbac(test_client: TestClient, auth_headers: dict) -> None:
    """超管可见「评估中心」菜单；普通角色不可见且接口 403。"""
    # 超管：菜单包含评估中心
    resp = test_client.get("/system/user/current/info", headers=auth_headers)
    assert resp.status_code == 200
    menus = resp.json()["data"]["menus"]
    flat_names = []

    def walk(nodes):
        for node in nodes or []:
            flat_names.append(node.get("name"))
            walk(node.get("children"))

    walk(menus)
    assert "评估中心" in flat_names
    for name in ("平台管理", "测试运行", "对比报告", "模型对齐"):
        assert name in flat_names

    # 超管可访问接口
    resp = test_client.get(f"{API}/list", headers=auth_headers)
    assert resp.status_code == 200

    # 普通角色：登录后接口 403，菜单不含评估中心
    login = test_client.post("/system/auth/login", data={"username": "user", "password": "123456"})
    assert login.status_code == 200, login.text
    user_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    resp = test_client.get(f"{API}/list", headers=user_headers)
    assert resp.status_code == 403
    resp = test_client.get("/system/user/current/info", headers=user_headers)
    assert resp.status_code == 200
    user_menus = []

    def walk2(nodes):
        for node in nodes or []:
            user_menus.append(node.get("name"))
            walk2(node.get("children"))

    walk2(resp.json()["data"]["menus"])
    assert "评估中心" not in user_menus


def test_openapi_docs_tags(test_client: TestClient) -> None:
    """任务 1.1 验证：/docs 的 OpenAPI 结构中出现 channel_eval 相关标签与接口。"""
    resp = test_client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    channel_eval_paths = [p for p in schema["paths"] if p.startswith("/channel_eval/")]
    assert channel_eval_paths, "OpenAPI 中应包含 channel_eval 接口"
    tags: set[str] = set()
    for ops in schema["paths"].values():
        for op in ops.values():
            tags.update(op.get("tags", []))
    assert any("评估中心" in tag for tag in tags), f"应包含评估中心标签: {tags}"
