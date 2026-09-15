"""对比报告 API 冒烟测试（路由接线 / 导出端点响应契约）"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.utils.crypto_util import CryptoUtil

PLATFORM_API = "/channel_eval/platforms"
RUN_API = "/channel_eval/runs"
REPORT_API = "/channel_eval/reports"


@pytest.fixture(autouse=True)
def _no_external_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用运行创建对外部平台的建令牌 + pin 探测调用（本文件仅 _setup_run 会创建运行）。"""

    async def _fake_ensure(db, platform, run):
        run.eval_token_id = 99
        run.eval_token_key_enc = CryptoUtil.encrypt("sk-eval-run")
        return "sk-eval-run"

    async def _fake_probe(base_url, token_key):
        return True, "指定渠道（pin）可用"

    monkeypatch.setattr("app.modules.channel_eval.run.service.ensure_eval_token", _fake_ensure)
    monkeypatch.setattr("app.modules.channel_eval.run.service.probe_pin_capability", _fake_probe)


def _setup_run(test_client: TestClient, auth_headers: dict, name: str) -> int:
    platform = test_client.post(
        f"{PLATFORM_API}/create",
        json={
            "name": name,
            "type": "admin",
            "base_url": "https://api.example.com",
            "admin_key": "sk-admin",
            "test_key": "sk-test",
        },
        headers=auth_headers,
    ).json()["data"]
    with patch(
        "app.modules.channel_eval.run.service.ChannelEvalRunService._launch_after_commit",
        lambda self, run_id: None,
    ):
        run = test_client.post(
            f"{RUN_API}/create",
            json={
                "platform_id": platform["id"],
                "combos": [{"channel_id": 70, "channel_name": "ch70", "model": "m1"}],
                "request_count": 1,
                "warmup": False,
                "interval_seconds": 0,
            },
            headers=auth_headers,
        ).json()["data"]
    return run["id"]


def test_report_api_crud_view_export(test_client: TestClient, auth_headers: dict) -> None:
    """报告 CRUD + view 聚合 + export HTML 附件下载。"""
    run_id = _setup_run(test_client, auth_headers, "report-api")

    # 创建（无基准）
    resp = test_client.post(
        f"{REPORT_API}/create",
        json={"name": "API 冒烟报告", "run_ids": [run_id], "note": "备注"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()["data"]
    assert report["run_ids"] == [run_id]

    # 基准校验拒绝：非基准渠道的运行
    resp = test_client.post(
        f"{REPORT_API}/create",
        json={"name": "bad", "run_ids": [run_id], "baseline_run_id": run_id},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "基准运行须来自基准渠道" in resp.json()["msg"]

    # 列表 / 详情
    assert test_client.get(f"{REPORT_API}/list", headers=auth_headers).status_code == 200
    assert test_client.get(f"{REPORT_API}/detail/{report['id']}", headers=auth_headers).status_code == 200

    # 编辑
    resp = test_client.put(
        f"{REPORT_API}/update/{report['id']}",
        json={"name": "API 冒烟报告-改", "run_ids": [run_id], "note": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # 视图聚合
    resp = test_client.get(f"{REPORT_API}/{report['id']}/view", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    view = resp.json()["data"]
    assert view["report"]["id"] == report["id"]
    assert view["runs"][0]["id"] == run_id

    # 导出自包含 HTML 附件
    resp = test_client.get(f"{REPORT_API}/{report['id']}/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "API 冒烟报告" in resp.text
    assert "<script src" not in resp.text

    # 删除
    resp = test_client.request("DELETE", f"{REPORT_API}/delete", json=[report["id"]], headers=auth_headers)
    assert resp.status_code == 200
