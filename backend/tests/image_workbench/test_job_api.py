"""绘图任务提交与历史 API 测试（任务 4.1、4.6）

走真实 HTTP（TestClient）验证接口层：参数校验按能力声明收敛、并发闸门、
历史列表 / 重试 / 删除 / 下载。上游客户端被替换为假实现，测试内不发起任何真实调用。
"""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.setting import settings
from app.modules.image_workbench.job.client import ImageArtifact, ImageGenerationResult, ImageUpstreamClient

JOBS_API = "/image_workbench/jobs"
SERVICES_API = "/image_workbench/services"
MODELS_API = "/image_workbench/models"

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_TERMINAL = {"success", "failed", "cancelled"}


class _FakeUpstream:
    """假上游：类属性驱动，便于逐测试定制。"""

    gate: threading.Event | None = None

    @staticmethod
    async def generate(self, **kwargs: Any) -> ImageGenerationResult:
        gate = _FakeUpstream.gate
        while gate is not None and not gate.is_set():
            await __import__("asyncio").sleep(0.02)
        return ImageGenerationResult(
            artifacts=[ImageArtifact(url="https://upstream.example.com/tmp/a.png", size="2048x2048")],
            usage={"total_tokens": 7},
            raw={"data": []},
        )

    @staticmethod
    async def fetch_image_bytes(self, artifact: ImageArtifact) -> bytes:
        return _PNG_BYTES


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch: pytest.MonkeyPatch):
    """全局替换上游调用，避免测试触网。"""
    _FakeUpstream.gate = None
    monkeypatch.setattr(ImageUpstreamClient, "generate", _FakeUpstream.generate)
    monkeypatch.setattr(ImageUpstreamClient, "fetch_image_bytes", _FakeUpstream.fetch_image_bytes)
    yield _FakeUpstream
    _FakeUpstream.gate = None


def _capability(**overrides: Any) -> dict[str, Any]:
    capability = {
        "sizes": ["1K", "2K"],
        "allow_custom_size": False,
        "support_reference": True,
        "max_reference_images": 2,
        "output_formats": ["png", "jpeg"],
        "default_output_format": "png",
        "watermark_default": False,
        "extra_params": {},
    }
    capability.update(overrides)
    return capability


def _seed_service_and_model(test_client: TestClient, auth_headers: dict, name: str, capability: dict | None = None) -> int:
    """经 API 落一套 绘图服务 + 绘图模型，返回 model_pk。"""
    service = test_client.post(
        f"{SERVICES_API}/create",
        json={
            "name": name,
            "dialect": "ark",
            "base_url": "https://ark.example.com/api/plan/v3",
            "api_key": "sk-test-key",
            "status": 0,
        },
        headers=auth_headers,
    )
    assert service.status_code == 200, service.text
    service_id = service.json()["data"]["id"]
    model = test_client.post(
        f"{MODELS_API}/create",
        json={
            "service_id": service_id,
            "model_id": "doubao-seedream-5-0-pro",
            "display_name": "Seedream 5.0 pro",
            "capability": capability if capability is not None else _capability(),
            "status": 0,
        },
        headers=auth_headers,
    )
    assert model.status_code == 200, model.text
    return model.json()["data"]["id"]


def _submit(test_client: TestClient, auth_headers: dict, model_pk: int, **overrides) -> dict:
    payload: dict[str, Any] = {"model_pk": model_pk, "prompt": "一只在雪地里的猫", "size": "2K", "output_format": "png"}
    payload.update(overrides)
    resp = test_client.post(f"{JOBS_API}/create", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _wait_terminal(test_client: TestClient, auth_headers: dict, job_id: int, timeout: float = 20.0) -> dict:
    """轮询到任务终态（引擎在应用事件循环里异步执行）。"""
    deadline = time.time() + timeout
    detail: dict = {}
    while time.time() < deadline:
        resp = test_client.get(f"{JOBS_API}/detail/{job_id}", headers=auth_headers)
        if resp.status_code == 200:
            detail = resp.json()["data"]
            if detail["status"] in _TERMINAL:
                return detail
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束，最后状态: {detail.get('status')}")


# ─────────────────────── 4.1 提交校验 ───────────────────────


def test_submit_validates_size_against_capability(test_client: TestClient, auth_headers: dict) -> None:
    """越界尺寸被拒，提示该模型支持的取值。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "size-guard")

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "size": "4K"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "1K" in resp.json()["msg"] and "4K" in resp.json()["msg"]

    # 未声明尺寸的模型：不接受任何尺寸取值
    bare_pk = _seed_service_and_model(test_client, auth_headers, "size-guard-bare", _capability(sizes=[]))
    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": bare_pk, "prompt": "x", "size": "1K"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


def test_submit_rejects_too_many_reference_images(test_client: TestClient, auth_headers: dict) -> None:
    """参考图张数超上限被拒，提示该模型允许的上限。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "ref-limit", _capability(max_reference_images=1))

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "size": "1K", "reference_images": ["static/upload/a.png", "static/upload/b.png"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "最多 1 张" in resp.json()["msg"]


def test_submit_rejects_reference_image_on_unsupported_model(test_client: TestClient, auth_headers: dict) -> None:
    """不支持参考图的模型即使用户绕过前端提交参考图，后端也拒绝。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "job-no-ref", _capability(support_reference=False, max_reference_images=0))

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "size": "1K", "reference_images": ["static/upload/a.png"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "不支持参考图" in resp.json()["msg"]


def test_submit_rejects_out_of_upload_dir_reference(test_client: TestClient, auth_headers: dict) -> None:
    """参考图路径必须落在上传目录内（否则可借该字段读取服务器上的任意文件）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "ref-path")

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "size": "1K", "reference_images": ["/etc/passwd"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "参考图" in resp.json()["msg"]


def test_submit_rejects_non_image_file_inside_upload_dir(test_client: TestClient, auth_headers: dict) -> None:
    """即使路径合法，内容不是图片也必须在创建绘图任务前被拒绝。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "ref-content")
    bad_reference = settings.UPLOAD_FILE_PATH / "file" / "not-an-image.png"
    bad_reference.parent.mkdir(parents=True, exist_ok=True)
    bad_reference.write_bytes(b"this is not an image")

    try:
        resp = test_client.post(
            f"{JOBS_API}/create",
            json={
                "model_pk": model_pk,
                "prompt": "x",
                "size": "1K",
                "reference_images": [str(bad_reference)],
            },
            headers=auth_headers,
        )
        if resp.status_code == 200:
            _wait_terminal(test_client, auth_headers, resp.json()["data"]["id"])
        assert resp.status_code == 400, resp.text
        assert "图片" in resp.json()["msg"]
    finally:
        bad_reference.unlink(missing_ok=True)


def test_submit_rejects_when_job_in_progress(test_client: TestClient, auth_headers: dict) -> None:
    """并发闸门：该用户已有进行中的任务时再次提交被拒。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "gate")
    gate = threading.Event()
    _FakeUpstream.gate = gate

    first = _submit(test_client, auth_headers, model_pk)
    assert first["status"] == "pending"

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "再来一张", "size": "1K"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "正在进行" in resp.json()["msg"]

    # 放行并等待首个任务结束，避免后台任务悬挂到测试结束
    gate.set()
    _FakeUpstream.gate = None
    assert _wait_terminal(test_client, auth_headers, first["id"])["status"] == "success"


def test_simultaneous_submissions_only_create_one_active_job(
    test_client: TestClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个同时通过计数检查的请求中，数据库仍只能接受一个活跃绘图任务。"""
    from app.modules.image_workbench.job.crud import ImageWorkbenchJobCRUD

    model_pk = _seed_service_and_model(test_client, auth_headers, "concurrent-gate")
    gate = threading.Event()
    count_barrier = threading.Barrier(2)
    original_count = ImageWorkbenchJobCRUD.count_active_by_user

    async def coordinated_count(self, user_id: int) -> int:
        count = await original_count(self, user_id)
        await __import__("asyncio").to_thread(count_barrier.wait, 5)
        return count

    monkeypatch.setattr(ImageWorkbenchJobCRUD, "count_active_by_user", coordinated_count)
    _FakeUpstream.gate = gate

    def submit(prompt: str):
        return test_client.post(
            f"{JOBS_API}/create",
            json={"model_pk": model_pk, "prompt": prompt, "size": "1K"},
            headers=auth_headers,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, ["并发 A", "并发 B"]))

    try:
        assert sorted(response.status_code for response in responses) == [200, 400], [response.text for response in responses]
        rejected = next(response for response in responses if response.status_code == 400)
        assert "正在进行" in rejected.json()["msg"]
    finally:
        gate.set()
        _FakeUpstream.gate = None
        for response in responses:
            if response.status_code == 200:
                _wait_terminal(test_client, auth_headers, response.json()["data"]["id"])


def test_submit_records_params_snapshot_and_generates_single_image(test_client: TestClient, auth_headers: dict) -> None:
    """提交即返回；一次任务恰好产出一张图，参数快照按能力声明收敛后落库。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "snapshot")

    job = _submit(test_client, auth_headers, model_pk)
    assert job["status"] in {"pending", "running"}
    assert job["params"]["size"] == "2K"
    assert job["params"]["output_format"] == "png"
    assert job["params"]["watermark"] is False  # 能力声明默认值，用户侧不暴露开关

    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert done["status"] == "success"
    assert done["image_path"] and done["image_path"].endswith(".png")
    assert done["image_url"], "本地存储节点应给出可直接访问的产物地址"
    # 种子节点 host=static/upload，静态挂载点是 backend/static，对外 URL 空间以 /api/v1 为根：
    # 既不能重复一段（/static/static/upload/...），也不能漏掉 ROOT_PATH（/static/upload/...）
    assert done["image_url"] == f"/api/v1/static/upload/{done['image_path']}"
    assert done["image_size"] == "2048x2048"
    assert done["elapsed_ms"] is not None


# ─────────────────────── 4.6 历史 / 重试 / 删除 / 下载 ──────────────────────


def test_list_and_detail_return_visible_jobs(test_client: TestClient, auth_headers: dict) -> None:
    """列表与详情按可见范围返回，含状态、耗时与产物缩略所需字段。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "list")
    job = _submit(test_client, auth_headers, model_pk)
    done = _wait_terminal(test_client, auth_headers, job["id"])

    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]["items"]
    assert any(item["id"] == job["id"] for item in listed)
    target = next(item for item in listed if item["id"] == job["id"])
    assert target["status_label"] == "生成成功"
    assert target["model_id"] == "doubao-seedream-5-0-pro"
    assert target["image_url"] == done["image_url"]


def test_retry_creates_new_job_and_keeps_original(test_client: TestClient, auth_headers: dict) -> None:
    """重试以原参数新建任务，原失败记录原样保留。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "retry")
    job = _submit(test_client, auth_headers, model_pk, size="1K")
    _wait_terminal(test_client, auth_headers, job["id"])

    # 手工把首个任务置为失败（直连测试库，避免与应用事件循环里的全局引擎抢会话），
    # 模拟上游报错后用户点「重试」的入口
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute("update image_workbench_job set status = 'failed', error = '上游报错' where id = ?", (job["id"],))
        conn.commit()
    finally:
        conn.close()

    resp = test_client.post(f"{JOBS_API}/{job['id']}/retry", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    retried = resp.json()["data"]
    assert retried["id"] != job["id"], "重试必须产生新的任务行"
    assert retried["params"]["size"] == "1K" and retried["prompt"] == job["prompt"]

    original = test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=auth_headers).json()["data"]
    assert original["status"] == "failed" and original["error"] == "上游报错"
    _wait_terminal(test_client, auth_headers, retried["id"])


def test_delete_removes_record_and_artifact_file(test_client: TestClient, auth_headers: dict) -> None:
    """删除 = 软删记录 + 真删产物文件（不是只隐藏记录）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "delete")
    job = _submit(test_client, auth_headers, model_pk)
    done = _wait_terminal(test_client, auth_headers, job["id"])

    artifact = Path(settings.UPLOAD_FILE_PATH) / done["image_path"]
    assert artifact.is_file(), f"产物应已落盘: {artifact}"

    resp = test_client.request("DELETE", f"{JOBS_API}/delete", json=[job["id"]], headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert not artifact.exists(), "删除必须移除产物文件本体"

    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]["items"]
    assert all(item["id"] != job["id"] for item in listed), "软删记录不应出现在列表"


def test_delete_in_progress_job_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """进行中的任务拒绝删除（提示需先取消）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "delete-active")
    gate = threading.Event()
    _FakeUpstream.gate = gate

    job = _submit(test_client, auth_headers, model_pk)
    resp = test_client.request("DELETE", f"{JOBS_API}/delete", json=[job["id"]], headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert "正在进行" in resp.json()["msg"]

    gate.set()
    _FakeUpstream.gate = None
    _wait_terminal(test_client, auth_headers, job["id"])


def test_cancel_releases_quota_and_stops_waiting(test_client: TestClient, auth_headers: dict) -> None:
    """取消后任务转入已取消状态，并立即释放该用户的并发额度。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "cancel")
    gate = threading.Event()
    _FakeUpstream.gate = gate

    job = _submit(test_client, auth_headers, model_pk)
    resp = test_client.post(f"{JOBS_API}/{job['id']}/cancel", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert "不保证" in resp.json()["msg"], "取消语义必须在响应里如实告知"

    cancelled = test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=auth_headers).json()["data"]
    assert cancelled["status"] == "cancelled"

    # 额度已释放：可以立刻提交新任务
    gate.set()
    _FakeUpstream.gate = None
    _wait_terminal(test_client, auth_headers, job["id"])
    second = _submit(test_client, auth_headers, model_pk, size="1K")
    _wait_terminal(test_client, auth_headers, second["id"])

    # 已终态的任务不可重复取消
    resp = test_client.post(f"{JOBS_API}/{job['id']}/cancel", headers=auth_headers)
    assert resp.status_code == 400, resp.text


def test_download_returns_artifact_file(test_client: TestClient, auth_headers: dict) -> None:
    """下载产物：返回图片文件本体。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "download")
    job = _submit(test_client, auth_headers, model_pk)
    _wait_terminal(test_client, auth_headers, job["id"])

    resp = test_client.get(f"{JOBS_API}/{job['id']}/download", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.content == _PNG_BYTES
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_detail_of_unknown_job_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """不存在（或不可见）的任务标识被拒绝，不返回任何内容。"""
    resp = test_client.get(f"{JOBS_API}/detail/99999999", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["data"] is None


# ────────────── 数据范围（可见范围默认仅本人） ───────────────


def _create_scoped_account(test_client: TestClient, username: str, permissions: list[str], data_scope: int = 1) -> dict:
    """建一个「指定菜单权限 + 指定数据范围」的普通账号并登录，返回认证头。

    直连测试库插入（角色 → 菜单授权 → 用户 → 用户角色），复现真实登录链路的
    权限装配：权限码取自所授权菜单的 ``permission`` 列。
    """
    import uuid

    from app.utils.password_util import PwdUtil

    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        cur = conn.cursor()
        now = "2026-09-16 00:00:00.000000"
        cur.execute(
            'insert into sys_role (name, code, "order", status, description, data_scope, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, ?, ?, ?, 0, ?, ?)',
            (username, f"role-{username}", 999, None, data_scope, uuid.uuid4().hex, now, now),
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


_JOB_MENUS = [
    "image_workbench:job:query",
    "image_workbench:job:create",
    "image_workbench:job:delete",
    "image_workbench:job:download",
]


def test_job_user_cannot_delete_files_through_common_download(test_client: TestClient) -> None:
    """绘图权限不能扩大成公共上传目录的任意文件删除权限。"""
    user = _create_scoped_account(test_client, "iw-file-guard", ["image_workbench:job:create"])
    protected = settings.UPLOAD_FILE_PATH / "file" / "other-module-file.png"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_bytes(_PNG_BYTES)

    try:
        resp = test_client.post(
            "/common/file/download",
            json={"file_path": str(protected), "delete": True},
            headers=user,
        )
        assert resp.status_code == 403, resp.text
        assert protected.is_file()
    finally:
        protected.unlink(missing_ok=True)


def test_reference_upload_is_module_scoped_and_rejects_non_images(test_client: TestClient) -> None:
    """持有生成权限即可上传参考图，但不能借此上传非图片文件。"""
    user = _create_scoped_account(test_client, "iw-reference-upload", ["image_workbench:job:create"])

    valid = test_client.post(
        f"{JOBS_API}/reference-images/upload",
        files={"file": ("reference.png", _PNG_BYTES, "image/png")},
        headers=user,
    )
    assert valid.status_code == 200, valid.text
    uploaded_path = Path(valid.json()["data"]["file_path"])

    try:
        invalid = test_client.post(
            f"{JOBS_API}/reference-images/upload",
            files={"file": ("not-image.png", b"not an image", "image/png")},
            headers=user,
        )
        assert invalid.status_code == 400, invalid.text
        assert "图片" in invalid.json()["msg"]

        common = test_client.post(
            "/common/file/upload?upload_type=file",
            files={"file": ("reference.png", _PNG_BYTES, "image/png")},
            headers=user,
        )
        assert common.status_code == 403, common.text
    finally:
        uploaded_path.unlink(missing_ok=True)


def test_cross_user_access_is_denied(test_client: TestClient, auth_headers: dict) -> None:
    """甲看不到也下载、删除不了乙的绘图任务；可见范围默认仅本人。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "scope")
    alice = _create_scoped_account(test_client, "iw-alice", _JOB_MENUS)
    bob = _create_scoped_account(test_client, "iw-bob", _JOB_MENUS)

    job = _submit(test_client, alice, model_pk)
    done = _wait_terminal(test_client, alice, job["id"])
    assert done["status"] == "success"

    # 列表：只含自己的任务
    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 100}, headers=bob).json()["data"]["items"]
    assert all(item["id"] != job["id"] for item in listed), "列表不得包含他人任务"

    # 详情 / 下载 / 删除：一律被拒，且不返回任何内容
    assert test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=bob).status_code == 404
    assert test_client.get(f"{JOBS_API}/{job['id']}/download", headers=bob).status_code == 404
    assert test_client.request("DELETE", f"{JOBS_API}/delete", json=[job["id"]], headers=bob).status_code == 404

    # 乙的越权尝试不影响甲的任务
    assert test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=alice).json()["data"]["status"] == "success"


def test_button_level_permissions_enforced_by_backend(test_client: TestClient, auth_headers: dict) -> None:
    """只持有查询权限的账号：绕过前端直调生成 / 删除接口同样被后端拒绝。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "perm")
    viewer = _create_scoped_account(test_client, "iw-viewer", ["image_workbench:job:query"])

    resp = test_client.post(f"{JOBS_API}/create", json={"model_pk": model_pk, "prompt": "x"}, headers=viewer)
    assert resp.status_code == 403, resp.text
    assert test_client.request("DELETE", f"{JOBS_API}/delete", json=[1], headers=viewer).status_code == 403
    # 查询本身可用
    assert test_client.get(f"{JOBS_API}/list", headers=viewer).status_code == 200
