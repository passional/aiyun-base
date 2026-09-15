"""测试运行 API 测试（任务 4.1 运行创建 / 4.5 取消 API）

引擎启动统一禁用（_launch_after_commit 置空），只验证创建校验与状态控制；
执行引擎行为由 test_engine.py 直测覆盖。
运行创建会调外部平台建评估令牌 + pin 探测（ADR-0001），单测中统一禁用
（真实逻辑由 test_provision_and_logs.py / test_new_api_client.py 覆盖）。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.utils.crypto_util import CryptoUtil

API = "/channel_eval/runs"


@pytest.fixture(autouse=True)
def _no_external_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用运行创建对外部平台的建令牌 + pin 探测调用（测试环境无真实网络）。"""

    async def _fake_ensure(db, platform, run):
        run.eval_token_id = 99
        run.eval_token_key_enc = CryptoUtil.encrypt("sk-eval-run")
        return "sk-eval-run"

    async def _fake_probe(base_url, token_key):
        return True, "指定渠道（pin）可用"

    monkeypatch.setattr("app.modules.channel_eval.run.service.ensure_eval_token", _fake_ensure)
    monkeypatch.setattr("app.modules.channel_eval.run.service.probe_pin_capability", _fake_probe)


def _create_platform(test_client: TestClient, auth_headers: dict, name: str) -> dict:
    resp = test_client.post(
        "/channel_eval/platforms/create",
        json={
            "name": name,
            "type": "admin",
            "base_url": "https://api.example.com",
            "admin_key": "sk-admin",
            "test_key": "sk-test",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _create_run(test_client: TestClient, auth_headers: dict, platform_id: int, combos: list[dict] | None = None) -> dict:
    resp = test_client.post(
        f"{API}/create",
        json={
            "platform_id": platform_id,
            "combos": combos if combos is not None else [{"channel_id": 70, "channel_name": "ch70", "model": "m1"}],
            "request_count": 2,
            "warmup": False,
            "interval_seconds": 0,
        },
        headers=auth_headers,
    )
    return resp  # type: ignore[return-value]


def test_create_run_empty_combos_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """组合为空 → 拒绝并提示至少选择一个组合。"""
    platform = _create_platform(test_client, auth_headers, "run-empty")
    resp = _create_run(test_client, auth_headers, platform["id"], combos=[])
    assert resp.status_code == 400
    assert "至少选择一个" in resp.json()["msg"]


def test_create_run_persists_combos_and_params(test_client: TestClient, auth_headers: dict) -> None:
    """组合清单（渠道编号+名称快照+模型）与参数快照随运行持久化。"""
    platform = _create_platform(test_client, auth_headers, "run-ok")
    combos = [
        {"channel_id": 70, "channel_name": "【京东云】ch70", "model": "claude-opus-5"},
        {"channel_id": 70, "channel_name": "【京东云】ch70", "model": "claude-fable-5"},
        {"channel_id": 72, "channel_name": "【UCloud】ch72", "model": "claude-opus-5"},
    ]
    with patch(
        "app.modules.channel_eval.run.service.ChannelEvalRunService._launch_after_commit",
        lambda self, run_id: None,
    ):
        resp = _create_run(test_client, auth_headers, platform["id"], combos=combos)
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["data"]["id"]

        detail = test_client.get(f"{API}/detail/{run_id}", headers=auth_headers).json()["data"]
        assert detail["status"] == "pending"
        assert detail["combo_count"] == 3
        assert len(detail["combos"]) == 3
        assert {c["model"] for c in detail["combos"]} == {"claude-opus-5", "claude-fable-5"}
        # 参数快照
        params = detail["params"]
        assert params["request_count"] == 2
        assert params["warmup"] is False
        assert params["interval_seconds"] == 0
        assert params["anomaly_threshold_seconds"] == 30


def test_create_run_serial_constraint(test_client: TestClient, auth_headers: dict) -> None:
    """同平台存在 pending/running 运行 → 拒绝新建；其它平台不受影响。"""
    platform_a = _create_platform(test_client, auth_headers, "serial-a")
    platform_b = _create_platform(test_client, auth_headers, "serial-b")
    with patch(
        "app.modules.channel_eval.run.service.ChannelEvalRunService._launch_after_commit",
        lambda self, run_id: None,
    ):
        first = _create_run(test_client, auth_headers, platform_a["id"])
        assert first.status_code == 200

        # 同平台第二个运行 → 拒绝
        second = _create_run(test_client, auth_headers, platform_a["id"])
        assert second.status_code == 400
        assert "串行" in second.json()["msg"]

        # 其它平台 → 允许
        other = _create_run(test_client, auth_headers, platform_b["id"])
        assert other.status_code == 200


def test_create_run_unknown_platform(test_client: TestClient, auth_headers: dict) -> None:
    """平台不存在 → 拒绝。"""
    resp = _create_run(test_client, auth_headers, 999999)
    assert resp.status_code == 400
    assert "平台不存在" in resp.json()["msg"]


def test_create_run_rejected_when_pin_probe_fails(test_client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """指定渠道（pin）探测失败（如管理用户非管理员）→ 拒绝创建运行，事务回滚不落库。"""
    from app.modules.channel_eval.run import service as run_service

    platform = _create_platform(test_client, auth_headers, "pin-bad")

    async def _failing_probe(base_url, token_key):
        return False, "指定渠道不可用：该平台管理凭证对应的是普通用户"

    with (
        patch(
            "app.modules.channel_eval.run.service.ChannelEvalRunService._launch_after_commit",
            lambda self, run_id: None,
        ),
        patch.object(run_service, "probe_pin_capability", new=_failing_probe),
    ):
        resp = _create_run(test_client, auth_headers, platform["id"])
    assert resp.status_code == 400
    assert "普通用户" in resp.json()["msg"]

    # 运行未落库（无残留 pending 阻塞）：可再次创建成功
    resp2 = _create_run(test_client, auth_headers, platform["id"])
    assert resp2.status_code == 200


def test_cancel_run_api(test_client: TestClient, auth_headers: dict) -> None:
    """取消 API：pending → cancelling；非执行中状态不可取消。"""
    from app.modules.channel_eval.run.registry import run_task_registry

    platform = _create_platform(test_client, auth_headers, "cancel")
    with patch(
        "app.modules.channel_eval.run.service.ChannelEvalRunService._launch_after_commit",
        lambda self, run_id: None,
    ):
        resp = _create_run(test_client, auth_headers, platform["id"])
        run_id = resp.json()["data"]["id"]

    try:
        cancel = test_client.post(f"{API}/{run_id}/cancel", headers=auth_headers)
        assert cancel.status_code == 200
        detail = test_client.get(f"{API}/detail/{run_id}", headers=auth_headers).json()["data"]
        assert detail["status"] == "cancelling"

        # cancelling 状态再次取消 → 拒绝（不可重复取消）
        cancel_again = test_client.post(f"{API}/{run_id}/cancel", headers=auth_headers)
        assert cancel_again.status_code == 400
    finally:
        run_task_registry.clear(run_id)
