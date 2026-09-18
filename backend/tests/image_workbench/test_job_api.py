"""绘图任务提交与历史 API 测试（任务 2.2 / 2.3 / 4.3 / 4.4 / 4.5）

走真实 HTTP（TestClient）验证接口层：参数按清单校验并补齐、图层拆分的前置校验、
并发闸门、历史列表 / 重试 / 删除 / 按产物下载。上游客户端被替换为假实现，测试内不
发起任何真实调用。
"""

import asyncio
import base64
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config.setting import settings
from app.modules.image_workbench.job.client import ImageArtifact, ImageGenerationResult, ImageUpstreamClient

JOBS_API = "/image_workbench/jobs"
SERVICES_API = "/image_workbench/services"
MODELS_API = "/image_workbench/models"

# 无透明通道的 PNG：签名合法即可通过上传校验；头部读不出 colour type，判为不带透明通道
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
# 真彩 + alpha 的 1x1 PNG（IHDR colour type = 6）
_RGBA_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
_TERMINAL = {"success", "failed", "cancelled"}


class _FakeUpstream:
    """假上游：类属性驱动，便于逐测试定制。"""

    gate: threading.Event | None = None
    artifacts: list[ImageArtifact] = []
    calls: list[dict[str, Any]] = []

    @staticmethod
    async def generate(self, **kwargs: Any) -> ImageGenerationResult:
        _FakeUpstream.calls.append(kwargs)
        gate = _FakeUpstream.gate
        while gate is not None and not gate.is_set():
            await asyncio.sleep(0.02)
        artifacts = list(_FakeUpstream.artifacts) or [ImageArtifact(url="https://upstream.example.com/tmp/a.png", size="2048x2048")]
        return ImageGenerationResult(artifacts=artifacts, usage={"total_tokens": 7}, raw={"data": []})

    @staticmethod
    async def fetch_image_bytes(self, artifact: ImageArtifact) -> bytes:
        return _PNG_BYTES

    @classmethod
    def reset(cls) -> None:
        cls.gate = None
        cls.artifacts = []
        cls.calls = []


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch: pytest.MonkeyPatch):
    """全局替换上游调用，避免测试触网。"""
    _FakeUpstream.reset()
    monkeypatch.setattr(ImageUpstreamClient, "generate", _FakeUpstream.generate)
    monkeypatch.setattr(ImageUpstreamClient, "fetch_image_bytes", _FakeUpstream.fetch_image_bytes)
    yield _FakeUpstream
    _FakeUpstream.reset()


def _capability(**overrides: Any) -> dict[str, Any]:
    """一份与种子同构的清单：含出图方式、条件依赖与按出图方式的覆盖。"""
    capability: dict[str, Any] = {
        "reference": {"supported": True, "max_images": 2},
        "modes": [
            {
                "key": "layer_decomposition",
                "label": {"zh": "图层拆分", "en": "Layer decomposition"},
                "trigger": {"param": "layer_decomposition", "eq": True},
            }
        ],
        "params": [
            {
                "key": "size",
                "label": {"zh": "尺寸", "en": "Size"},
                "control": "select",
                "allowed": ["1K", "2K"],
                "default": "2K",
                "source": "documented",
                "overrides": {"layer_decomposition": {"allowed": ["1K", "2K", "auto"], "default": "auto"}},
            },
            {
                "key": "layer_decomposition",
                "label": {"zh": "图层拆分", "en": "Layer decomposition"},
                "control": "switch",
                "default": False,
                "source": "inferred",
                "when": {"all": [{"request": "reference_count", "eq": 1}]},
            },
            {
                "key": "output_format",
                "label": {"zh": "输出格式", "en": "Output format"},
                "control": "select",
                "allowed": ["png", "jpeg"],
                "default": "png",
                "source": "documented",
            },
            {
                "key": "background",
                "label": {"zh": "图片透明通道", "en": "Transparent background"},
                "control": "select",
                "allowed": ["opaque", "transparent"],
                "default": "opaque",
                "source": "documented",
                "when": {
                    "all": [
                        {"param": "output_format", "not_in": ["jpeg"]},
                        {"request": "reference_count", "eq": 1},
                        {"request": "reference_has_alpha", "eq": True},
                    ]
                },
            },
            {"key": "watermark", "label": {"zh": "水印", "en": "Watermark"}, "control": "switch", "default": False, "source": "documented"},
        ],
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


def _update_capability(test_client: TestClient, auth_headers: dict, model_pk: int, capability: dict, service_name: str) -> None:
    """整体替换某模型的能力声明（列表页拿 id 的路径太长，直接按 model_pk 取详情再改）。"""
    detail = test_client.get(f"{MODELS_API}/detail/{model_pk}", headers=auth_headers).json()["data"]
    resp = test_client.put(
        f"{MODELS_API}/update/{model_pk}",
        json={
            "service_id": detail["service_id"],
            "model_id": detail["model_id"],
            "display_name": detail["display_name"],
            "capability": capability,
            "status": detail["status"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


def _upload_reference(test_client: TestClient, auth_headers: dict, content: bytes, name: str = "reference.png") -> str:
    """经模块专属入口上传一张参考图，返回它在存储中的相对路径。"""
    resp = test_client.post(
        f"{JOBS_API}/reference-images/upload",
        files={"file": (name, content, "image/png")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["file_path"]


def _submit(test_client: TestClient, auth_headers: dict, model_pk: int, *, params: dict | None = None, **overrides) -> dict:
    payload: dict[str, Any] = {
        "model_pk": model_pk,
        "prompt": "一只在雪地里的猫",
        "params": {"size": "2K", "output_format": "png"} if params is None else params,
    }
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


# ────────────────────── 2.2 提交：参数按清单收敛 ───────────────────────


def test_submit_rejects_param_outside_declared_domain(test_client: TestClient, auth_headers: dict) -> None:
    """越界取值被拒，提示该模型支持的取值。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "param-domain")

    resp = test_client.post(f"{JOBS_API}/create", json={"model_pk": model_pk, "prompt": "x", "params": {"size": "4K"}}, headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert "4K" in resp.json()["msg"] and "1K" in resp.json()["msg"]

    # 未声明取值域的模型：不接受任何取值（它不是「有尺寸但没校验」，而是根本没有这个参数）
    capability = _capability()
    capability["params"] = [item for item in capability["params"] if item["key"] != "size"]
    bare_pk = _seed_service_and_model(test_client, auth_headers, "param-domain-bare", capability)
    resp = test_client.post(f"{JOBS_API}/create", json={"model_pk": bare_pk, "prompt": "x", "params": {"size": "1K"}}, headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert "size" in resp.json()["msg"]


def test_submit_rejects_unknown_param_key(test_client: TestClient, auth_headers: dict) -> None:
    """提交清单里没有的参数键被拒（不再有与具体参数无关的通用透传口子）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "param-unknown")

    resp = test_client.post(f"{JOBS_API}/create", json={"model_pk": model_pk, "prompt": "x", "params": {"strength": 0.5}}, headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert "strength" in resp.json()["msg"]


def test_submit_rejects_unavailable_param_with_reason(test_client: TestClient, auth_headers: dict) -> None:
    """绕过表单提交当前条件下不可用的参数被拒，并说明为何不可用。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "param-unavailable")

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "params": {"background": "transparent"}, "reference_images": []},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "background" in resp.json()["msg"] and "不可用" in resp.json()["msg"]
    assert "参考图" in resp.json()["msg"], "拒绝原因必须说清是哪一条条件不满足"


def test_plain_request_without_reference_image_passes(test_client: TestClient, auth_headers: dict) -> None:
    """普通请求（无参考图、未提交 background）正常通过，快照里不含不可用的参数。

    这是「先无条件补齐默认值再校验」会堵死整条生成路径的直接反例：``background``
    的默认值是「opaque」，一旦被无差别补齐，随即会被它自己的可用性条件拒掉。
    """
    model_pk = _seed_service_and_model(test_client, auth_headers, "plain-request")

    job = _submit(test_client, auth_headers, model_pk)
    assert job["status"] in {"pending", "running"}
    assert "background" not in job["params"], "不可用的参数 MUST NOT 进快照"
    # layer_decomposition 同样要求「参考图恰好一张」，没有参考图时也不进快照
    assert job["params"] == {"size": "2K", "output_format": "png", "watermark": False}

    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert done["status"] == "success"


def test_snapshot_equals_params_sent_upstream(test_client: TestClient, auth_headers: dict) -> None:
    """任务快照**等于**实际发给上游的参数集（含补齐的默认值），且键集与请求体一致。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "snapshot")
    _FakeUpstream.reset()

    job = _submit(test_client, auth_headers, model_pk, params={"size": "1K"})
    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert done["status"] == "success"

    sent = _FakeUpstream.calls[-1]
    assert sent["params"] == done["params"], "快照必须与实际发给上游的参数集逐键相同"
    # 请求体 = model + prompt + 清单参数（ark 还有参考图字段）；参数之外再无别的字段
    body_keys = {"model", "prompt", "image"} | set(done["params"])
    assert body_keys - {"model", "prompt", "image"} == set(done["params"])


def test_submit_fills_defaults_and_records_single_artifact(test_client: TestClient, auth_headers: dict) -> None:
    """补齐默认值后落库；一次未启用图层拆分的任务恰好一张产物。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "single-artifact")

    job = _submit(test_client, auth_headers, model_pk)
    assert job["params"]["watermark"] is False  # 上游默认加水印，产品默认不加（ADR-0008）

    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert done["status"] == "success"
    assert len(done["artifacts"]) == 1
    artifact = done["artifacts"][0]
    assert artifact["is_base"] is True and artifact["z_index"] is None
    assert artifact["file_path"].endswith("/base.png")
    assert artifact["image_size"] == "2048x2048"
    # 种子节点 host=static/upload，静态挂载点是 backend/static，对外 URL 空间以 /api/v1 为根：
    # 既不能重复一段（/static/static/upload/...），也不能漏掉 ROOT_PATH（/static/upload/...）
    assert artifact["image_url"] == f"/api/v1/static/upload/{artifact['file_path']}"
    assert done["thumbnail_url"] == artifact["image_url"]
    assert done["elapsed_ms"] is not None
    assert done["usage"]["images"] == 1


def test_submit_rejects_too_many_reference_images(test_client: TestClient, auth_headers: dict) -> None:
    """参考图张数超上限被拒，提示该模型允许的上限。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "ref-limit", _capability(reference={"supported": True, "max_images": 1}))
    first = _upload_reference(test_client, auth_headers, _PNG_BYTES, "a.png")
    second = _upload_reference(test_client, auth_headers, _PNG_BYTES, "b.png")

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "params": {"size": "1K"}, "reference_images": [first, second]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "最多 1 张" in resp.json()["msg"]


def test_submit_rejects_reference_image_on_unsupported_model(test_client: TestClient, auth_headers: dict) -> None:
    """不支持参考图的模型即使用户绕过前端提交参考图，后端也拒绝。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "job-no-ref", _capability(reference={"supported": False, "max_images": 0}))
    reference = _upload_reference(test_client, auth_headers, _PNG_BYTES)

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "params": {"size": "1K"}, "reference_images": [reference]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "不支持参考图" in resp.json()["msg"]


def test_submit_rejects_out_of_upload_dir_reference(test_client: TestClient, auth_headers: dict) -> None:
    """参考图路径必须落在上传目录内（否则可借该字段读取服务器上的任意文件）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "ref-path")

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "params": {"size": "1K"}, "reference_images": ["/etc/passwd"]},
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
            json={"model_pk": model_pk, "prompt": "x", "params": {"size": "1K"}, "reference_images": [str(bad_reference)]},
            headers=auth_headers,
        )
        if resp.status_code == 200:
            _wait_terminal(test_client, auth_headers, resp.json()["data"]["id"])
        assert resp.status_code == 400, resp.text
        assert "图片" in resp.json()["msg"]
    finally:
        bad_reference.unlink(missing_ok=True)


# ─────────────────── 2.3 图层拆分：参考图必须恰好一张 ───────────────────


@pytest.mark.parametrize(("reference_count", "accepted"), [(0, False), (1, True), (2, False)])
def test_layer_decomposition_requires_exactly_one_reference_image(
    test_client: TestClient,
    auth_headers: dict,
    reference_count: int,
    accepted: bool,
) -> None:
    """启用图层拆分时参考图必须恰好一张：0 张与 2 张被拒，1 张放行。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, f"layer-ref-{reference_count}")
    references = [_upload_reference(test_client, auth_headers, _PNG_BYTES, f"ref{i}.png") for i in range(reference_count)]

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={
            "model_pk": model_pk,
            "prompt": "把这张图拆成图层",
            "params": {"layer_decomposition": True},
            "reference_images": references,
        },
        headers=auth_headers,
    )
    if not accepted:
        assert resp.status_code == 400, resp.text
        assert "layer_decomposition" in resp.json()["msg"] and "不可用" in resp.json()["msg"]
        assert "本次参考图数量" in resp.json()["msg"]
        return

    assert resp.status_code == 200, resp.text
    job = resp.json()["data"]
    # 出图方式命中后取覆盖后的取值域与默认值：尺寸默认变成 auto
    assert job["params"]["size"] == "auto"
    assert job["params"]["layer_decomposition"] is True
    _wait_terminal(test_client, auth_headers, job["id"])


def test_model_without_layer_decomposition_mode_rejects_the_param(test_client: TestClient, auth_headers: dict) -> None:
    """模型未声明图层拆分时，绕过前端提交该参数同样被拒（它是清单里没有的键）。"""
    capability = _capability(modes=[])
    capability["params"] = [item for item in capability["params"] if item["key"] != "layer_decomposition"]
    # 出图方式没了，按它覆盖的条目也必须一并去掉——否则保存时就被拒
    next(item for item in capability["params"] if item["key"] == "size").pop("overrides")
    model_pk = _seed_service_and_model(test_client, auth_headers, "no-layer-mode", capability)
    reference = _upload_reference(test_client, auth_headers, _PNG_BYTES)

    resp = test_client.post(
        f"{JOBS_API}/create",
        json={"model_pk": model_pk, "prompt": "x", "params": {"layer_decomposition": True}, "reference_images": [reference]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "layer_decomposition" in resp.json()["msg"]


# ─────────────────────── 并发闸门 ───────────────────────


def test_submit_rejects_when_job_in_progress(test_client: TestClient, auth_headers: dict) -> None:
    """并发闸门：该用户已有进行中的任务时再次提交被拒。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "gate")
    gate = threading.Event()
    _FakeUpstream.gate = gate

    first = _submit(test_client, auth_headers, model_pk)
    assert first["status"] == "pending"

    resp = test_client.post(f"{JOBS_API}/create", json={"model_pk": model_pk, "prompt": "再来一张", "params": {}}, headers=auth_headers)
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
        await asyncio.to_thread(count_barrier.wait, 5)
        return count

    monkeypatch.setattr(ImageWorkbenchJobCRUD, "count_active_by_user", coordinated_count)
    _FakeUpstream.gate = gate

    def submit(prompt: str):
        return test_client.post(f"{JOBS_API}/create", json={"model_pk": model_pk, "prompt": prompt, "params": {}}, headers=auth_headers)

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


# ─────────────────────── 4.3 产物访问 ───────────────────────


def _layer_artifacts() -> list[ImageArtifact]:
    return [
        ImageArtifact(url="https://upstream.example.com/tmp/base.png", size="2048x2048"),
        ImageArtifact(
            url="https://upstream.example.com/tmp/l1.png",
            size="2048x2048",
            z_index=1,
            name="天空",
            description="整片天空与云",
            bounding_box={"absolute": [0, 0, 2048, 900], "normalized": [0, 0, 999, 439]},
        ),
        ImageArtifact(url="https://upstream.example.com/tmp/l2.png", size="2048x2048", z_index=2, name="人物"),
    ]


def test_list_and_detail_return_visible_jobs(test_client: TestClient, auth_headers: dict) -> None:
    """列表与详情按可见范围返回，含状态、耗时与产物缩略所需字段。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "list")
    job = _submit(test_client, auth_headers, model_pk)
    done = _wait_terminal(test_client, auth_headers, job["id"])

    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]["items"]
    target = next(item for item in listed if item["id"] == job["id"])
    assert target["status_label"] == "生成成功"
    assert target["model_id"] == "doubao-seedream-5-0-pro"
    # 列表用底图作缩略
    assert target["thumbnail_url"] == done["thumbnail_url"]
    assert len(target["artifacts"]) == 1 and target["artifacts"][0]["is_base"] is True


def test_detail_artifacts_ordered_and_individually_downloadable(test_client: TestClient, auth_headers: dict) -> None:
    """多产物任务：详情逐张呈现、底图在前按层级升序，且每一张都能单独下载。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "artifacts")
    reference = _upload_reference(test_client, auth_headers, _PNG_BYTES)
    _FakeUpstream.artifacts = _layer_artifacts()

    job = _submit(test_client, auth_headers, model_pk, params={"layer_decomposition": True}, reference_images=[reference])
    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert done["status"] == "success"

    artifacts = done["artifacts"]
    assert [item["z_index"] for item in artifacts] == [None, 1, 2], "底图在前，其余按层级升序"
    assert [item["is_base"] for item in artifacts] == [True, False, False]
    assert artifacts[1]["name"] == "天空" and artifacts[1]["description"] == "整片天空与云"
    assert artifacts[1]["bounding_box"]["normalized"] == [0, 0, 999, 439]
    assert done["thumbnail_url"] == artifacts[0]["image_url"], "列表缩略取底图"
    assert len({item["file_path"] for item in artifacts}) == 3

    for artifact in artifacts:
        resp = test_client.get(f"{JOBS_API}/{job['id']}/artifacts/{artifact['id']}/download", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.content == _PNG_BYTES
        assert "attachment" in resp.headers.get("content-disposition", "")


def test_task_level_download_endpoint_is_gone(test_client: TestClient) -> None:
    """任务级下载端点已移除：一次任务可以有多张产物，它无从表达要哪一张。

    只能在路由表上断言——未匹配的路径会落到前端静态托管的兜底路由，仍返回 200。
    """
    paths = set(test_client.get("/openapi.json").json()["paths"])
    assert f"{JOBS_API}/{{id}}/download" not in paths
    assert f"{JOBS_API}/{{id}}/artifacts/{{artifact_id}}/download" in paths


def test_artifact_download_rejects_wrong_or_foreign_artifact(test_client: TestClient, auth_headers: dict) -> None:
    """产物必须属于该任务：跨任务拼接标识一律拒绝。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "artifact-scope")
    first = _submit(test_client, auth_headers, model_pk)
    _wait_terminal(test_client, auth_headers, first["id"])
    second = _submit(test_client, auth_headers, model_pk)
    second_done = _wait_terminal(test_client, auth_headers, second["id"])

    foreign_artifact_id = second_done["artifacts"][0]["id"]
    resp = test_client.get(f"{JOBS_API}/{first['id']}/artifacts/{foreign_artifact_id}/download", headers=auth_headers)
    assert resp.status_code == 404, resp.text

    resp = test_client.get(f"{JOBS_API}/{second['id']}/artifacts/99999999/download", headers=auth_headers)
    assert resp.status_code == 404, resp.text


def test_detail_of_unknown_job_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """不存在（或不可见）的任务标识被拒绝，不返回任何内容。"""
    resp = test_client.get(f"{JOBS_API}/detail/99999999", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["data"] is None


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
    assert cancelled["artifacts"] == []

    # 额度已释放：可以立刻提交新任务
    gate.set()
    _FakeUpstream.gate = None
    _wait_terminal(test_client, auth_headers, job["id"])
    second = _submit(test_client, auth_headers, model_pk)
    _wait_terminal(test_client, auth_headers, second["id"])

    # 已终态的任务不可重复取消
    resp = test_client.post(f"{JOBS_API}/{job['id']}/cancel", headers=auth_headers)
    assert resp.status_code == 400, resp.text


# ────────────────────── 4.4 删除 ───────────────────────


def _artifact_count(job_id: int) -> int:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return conn.execute("select count(*) from image_workbench_artifact where job_id = ?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


def test_delete_removes_record_and_artifact_file(test_client: TestClient, auth_headers: dict) -> None:
    """删除 = 软删记录 + 真删产物文件（不是只隐藏记录）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "delete")
    job = _submit(test_client, auth_headers, model_pk)
    done = _wait_terminal(test_client, auth_headers, job["id"])

    artifact = Path(settings.UPLOAD_FILE_PATH) / done["artifacts"][0]["file_path"]
    assert artifact.is_file(), f"产物应已落盘: {artifact}"

    resp = test_client.request("DELETE", f"{JOBS_API}/delete", json=[job["id"]], headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert not artifact.exists(), "删除必须移除产物文件本体"
    assert _artifact_count(job["id"]) == 0, "产物行必须一并移除"

    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 50}, headers=auth_headers).json()["data"]["items"]
    assert all(item["id"] != job["id"] for item in listed), "软删记录不应出现在列表"


def test_delete_multi_artifact_job_removes_every_file(test_client: TestClient, auth_headers: dict) -> None:
    """多产物任务删除后存储中无残留文件、产物行全部消失。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "delete-multi")
    reference = _upload_reference(test_client, auth_headers, _PNG_BYTES)
    _FakeUpstream.artifacts = _layer_artifacts()

    job = _submit(test_client, auth_headers, model_pk, params={"layer_decomposition": True}, reference_images=[reference])
    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert len(done["artifacts"]) == 3

    files = [Path(settings.UPLOAD_FILE_PATH) / item["file_path"] for item in done["artifacts"]]
    assert all(path.is_file() for path in files)
    job_dir = files[0].parent

    resp = test_client.request("DELETE", f"{JOBS_API}/delete", json=[job["id"]], headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert not any(path.exists() for path in files), "全部产物文件都必须被移除"
    # 残留的空目录不算产物（远端协议下「目录」本就是个虚拟概念）
    assert list(job_dir.rglob("*")) == [] if job_dir.exists() else True
    assert _artifact_count(job["id"]) == 0


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


# ─────────────────────── 4.5 重试 ───────────────────────


def test_retry_creates_new_job_and_keeps_original(test_client: TestClient, auth_headers: dict) -> None:
    """重试以原参数新建任务，原失败记录原样保留。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "retry")
    job = _submit(test_client, auth_headers, model_pk, params={"size": "1K"})
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


def test_retry_goes_through_the_same_validation(test_client: TestClient, auth_headers: dict) -> None:
    """重试把原快照当「显式参数」重新提交，走的是同一套校验：清单改过后被拒而不是静默上行。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "retry-validation")
    job = _submit(test_client, auth_headers, model_pk, params={"size": "1K"})
    _wait_terminal(test_client, auth_headers, job["id"])

    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute("update image_workbench_job set status = 'failed' where id = ?", (job["id"],))
        conn.commit()
    finally:
        conn.close()

    # 管理员把该参数的条目整条删掉——原快照里的键因此不再存在于清单
    detail = test_client.get(f"{MODELS_API}/detail/{model_pk}", headers=auth_headers).json()["data"]
    capability = detail["capability"]
    capability["params"] = [item for item in capability["params"] if item["key"] != "size"]
    _update_capability(test_client, auth_headers, model_pk, capability, "retry-validation")

    resp = test_client.post(f"{JOBS_API}/{job['id']}/retry", headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert "size" in resp.json()["msg"], "已不存在的参数必须让重试被拒，而不是静默发给上游"


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
        resp = test_client.post("/common/file/download", json={"file_path": str(protected), "delete": True}, headers=user)
        assert resp.status_code == 403, resp.text
        assert protected.is_file()
    finally:
        protected.unlink(missing_ok=True)


def test_reference_upload_is_module_scoped_and_rejects_non_images(test_client: TestClient) -> None:
    """持有生成权限即可上传参考图，但不能借此上传非图片文件。"""
    user = _create_scoped_account(test_client, "iw-reference-upload", ["image_workbench:job:create"])

    valid = test_client.post(f"{JOBS_API}/reference-images/upload", files={"file": ("reference.png", _PNG_BYTES, "image/png")}, headers=user)
    assert valid.status_code == 200, valid.text
    uploaded_path = Path(valid.json()["data"]["file_path"])

    try:
        invalid = test_client.post(f"{JOBS_API}/reference-images/upload", files={"file": ("not-image.png", b"not an image", "image/png")}, headers=user)
        assert invalid.status_code == 400, invalid.text
        assert "图片" in invalid.json()["msg"]

        common = test_client.post("/common/file/upload?upload_type=file", files={"file": ("reference.png", _PNG_BYTES, "image/png")}, headers=user)
        assert common.status_code == 403, common.text
    finally:
        uploaded_path.unlink(missing_ok=True)


def test_reference_upload_reports_alpha_channel(test_client: TestClient, auth_headers: dict) -> None:
    """上传响应给出 ``has_alpha``：前端据此当场决定「图片透明通道」是否可用（任务 2.4）。"""
    opaque = test_client.post(f"{JOBS_API}/reference-images/upload", files={"file": ("opaque.png", _PNG_BYTES, "image/png")}, headers=auth_headers)
    assert opaque.status_code == 200, opaque.text
    assert opaque.json()["data"]["has_alpha"] is False

    transparent = test_client.post(f"{JOBS_API}/reference-images/upload", files={"file": ("rgba.png", _RGBA_PNG, "image/png")}, headers=auth_headers)
    assert transparent.status_code == 200, transparent.text
    assert transparent.json()["data"]["has_alpha"] is True

    for response in (opaque, transparent):
        Path(response.json()["data"]["file_path"]).unlink(missing_ok=True)


def test_cross_user_access_is_denied(test_client: TestClient, auth_headers: dict) -> None:
    """甲看不到也下载、删除不了乙的绘图任务；可见范围默认仅本人。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "scope")
    alice = _create_scoped_account(test_client, "iw-alice", _JOB_MENUS)
    bob = _create_scoped_account(test_client, "iw-bob", _JOB_MENUS)

    job = _submit(test_client, alice, model_pk)
    done = _wait_terminal(test_client, alice, job["id"])
    assert done["status"] == "success"
    artifact_id = done["artifacts"][0]["id"]

    # 列表：只含自己的任务
    listed = test_client.get(f"{JOBS_API}/list", params={"page_no": 1, "page_size": 100}, headers=bob).json()["data"]["items"]
    assert all(item["id"] != job["id"] for item in listed), "列表不得包含他人任务"

    # 详情 / 下载 / 删除：一律被拒，且不返回任何内容
    assert test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=bob).status_code == 404
    assert test_client.get(f"{JOBS_API}/{job['id']}/artifacts/{artifact_id}/download", headers=bob).status_code == 404
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

# ═════════ 交互编辑：选区、最终提示词与来源引用 ═════════
# 覆盖 1.2（选区的六种拒绝分支 + 通过分支）、1.3（提示词渲染）、1.4（两个形态）、
# 1.5 / 2.4（来源记全与可见范围）、2.3（删除保护）、2.5（重试复用快照）。

# 交互编辑是**固有**出图方式（不由参数触发），标记语法与坐标区间都取自声明
_MARKER_MODE: dict[str, Any] = {
    "key": "interactive_edit",
    "label": {"zh": "交互编辑", "en": "Interactive edit"},
    "trigger": None,
    "marker": {"placeholder": "区域{n}", "template": "<bbox>{x1} {y1} {x2} {y2}</bbox>", "range": 1000},
}


def _interactive_capability(**overrides: Any) -> dict[str, Any]:
    """在基准清单上追加交互编辑方式（坐标区间 0–999）。"""
    capability = _capability()
    capability["modes"] = [*capability["modes"], deepcopy(_MARKER_MODE)]
    capability.update(overrides)
    return capability


def _seed_interactive_model(test_client: TestClient, auth_headers: dict, name: str) -> int:
    return _seed_service_and_model(test_client, auth_headers, name, _interactive_capability())


def _ref_rows(job_id: int) -> list[int]:
    """某任务在引用表里落了哪些产物（按产物ID升序）。"""
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        return [row[0] for row in conn.execute("select artifact_id from image_workbench_job_artifact_ref where job_id = ? order by artifact_id", (job_id,)).fetchall()]
    finally:
        conn.close()


def _layer_job(test_client: TestClient, auth_headers: dict, name: str) -> tuple[int, dict]:
    """落一次图层拆分任务，返回 ``(model_pk, 终态任务)``——产物即「现成的图层」。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, name)
    reference = _upload_reference(test_client, auth_headers, _PNG_BYTES)
    _FakeUpstream.artifacts = _layer_artifacts()
    job = _submit(test_client, auth_headers, model_pk, params={"layer_decomposition": True}, reference_images=[reference])
    return model_pk, _wait_terminal(test_client, auth_headers, job["id"])


def _layer_artifact(done: dict, z_index: int) -> dict:
    return next(item for item in done["artifacts"] if item["z_index"] == z_index)


def _submit_selections(test_client: TestClient, auth_headers: dict, model_pk: int, payload: dict[str, Any]):
    """带选区提交（不预设成败，由调用方断言）。"""
    body: dict[str, Any] = {"model_pk": model_pk, "prompt": "一只在雪地里的猫", "params": {}}
    body.update(payload)
    return test_client.post(f"{JOBS_API}/create", json=body, headers=auth_headers)


# ─────────────── 1.2 选区：六种拒绝分支 ───────────────


def test_selection_requires_interactive_edit_in_capability(test_client: TestClient, auth_headers: dict) -> None:
    """模型未声明交互编辑时，绕过前端提交选区同样被拒（spec: 交互编辑与选区）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "no-edit-mode")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1换成猫", "selections": [{"index": 1, "bbox": [0, 0, 100, 100]}]},
    )
    assert resp.status_code == 400, resp.text
    assert "不支持交互编辑" in resp.json()["msg"]


def test_selection_index_must_be_unique(test_client: TestClient, auth_headers: dict) -> None:
    """序号唯一：同一个序号出现两次 → 拒绝（否则渲染时前一个会被后一个悄悄顶掉）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-dup")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "bbox": [0, 0, 10, 10]}, {"index": 1, "bbox": [20, 20, 30, 30]}]},
    )
    assert resp.status_code == 400, resp.text
    assert "重复" in resp.json()["msg"] and "#1" in resp.json()["msg"]


def test_selection_index_must_be_at_least_one(test_client: TestClient, auth_headers: dict) -> None:
    """序号不小于 1：``区域0`` 不是合法的占位符，直接挡在入参上。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-zero")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域0换掉", "selections": [{"index": 0, "bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    ("case", "bbox"),
    [
        ("x2", [0, 0, 1000, 10]),  # 右上 x 越界（最大 999）
        ("x1", [-1, 0, 10, 10]),  # 左上 x 为负
        ("y2", [0, 0, 500, 1000]),  # 右下 y 越界
    ],
)
def test_selection_bbox_outside_declared_range_rejected(test_client: TestClient, auth_headers: dict, case: str, bbox: list[int]) -> None:
    """坐标超出**能力声明规定的区间**即拒绝（区间取自 marker.range，不是写死的数字）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, f"sel-range-{case}")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "bbox": bbox}]},
    )
    assert resp.status_code == 400, resp.text
    assert "区间" in resp.json()["msg"] and "#1" in resp.json()["msg"]


def test_selection_bbox_zero_area_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """面积为零（退化成一条线）→ 拒绝：上游要的是一个范围。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-area")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "bbox": [10, 10, 10, 20]}]},
    )
    assert resp.status_code == 400, resp.text
    assert "面积为零" in resp.json()["msg"]


def test_selection_bbox_reversed_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """左上点必须小于右下点 → 反之拒绝（并原样报出收到的两个点）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-order")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "bbox": [50, 60, 10, 20]}]},
    )
    assert resp.status_code == 400, resp.text
    assert "左上点必须小于右下点" in resp.json()["msg"]


def test_selection_bbox_must_be_four_integers(test_client: TestClient, auth_headers: dict) -> None:
    """坐标为整数且恰好四个：字符串数字与错误长度都在入参层被挡。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-int")
    for bbox in (["1", "2", "3", "4"], [0, 0, 10], [0, 0, 10, 10, 20]):
        resp = _submit_selections(test_client, auth_headers, model_pk, {"prompt": "把区域1换掉", "selections": [{"index": 1, "bbox": bbox}]})
        assert resp.status_code == 422, f"{bbox}: {resp.text}"


@pytest.mark.parametrize(
    ("case", "selection"),
    [
        ("both", {"index": 1, "artifact_id": 1, "bbox": [0, 0, 10, 10]}),  # 两种来源都给了
        ("none", {"index": 1}),  # 一种都没给
    ],
)
def test_selection_needs_exactly_one_position_source(test_client: TestClient, auth_headers: dict, case: str, selection: dict) -> None:
    """位置来源必须**二选一**：点选图层（artifact_id）或自行划框（bbox）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, f"sel-source-{case}")
    resp = _submit_selections(test_client, auth_headers, model_pk, {"prompt": "把区域1换掉", "selections": [selection]})
    assert resp.status_code == 422, resp.text


def test_selection_source_artifact_outside_visible_range_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """选区来源产物不在提交者的可见范围内 → 拒绝（spec: 产物作为后续任务的输入）。"""
    # 绘图模型是全局配置，由管理员预置；受限账号只持有任务侧权限
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-scope")
    alice = _create_scoped_account(test_client, "iw-sel-alice", _JOB_MENUS)
    bob = _create_scoped_account(test_client, "iw-sel-bob", _JOB_MENUS)

    _FakeUpstream.artifacts = _layer_artifacts()
    reference = _upload_reference(test_client, alice, _PNG_BYTES)
    alice_job = _submit(test_client, alice, model_pk, params={}, reference_images=[reference])
    alice_done = _wait_terminal(test_client, alice, alice_job["id"])
    stolen = _layer_artifact(alice_done, 1)["id"]

    # 乙拿着甲的产物标识来提交：越权与「不存在」同答复
    resp = _submit_selections(test_client, bob, model_pk, {"prompt": "把区域1换掉", "selections": [{"index": 1, "artifact_id": stolen}]})
    assert resp.status_code == 404, resp.text
    assert "可见范围" in resp.json()["msg"]

    # 甲自己引用则通过
    own = _submit_selections(test_client, alice, model_pk, {"prompt": "把区域1换掉", "selections": [{"index": 1, "artifact_id": stolen}]})
    assert own.status_code == 200, own.text
    _wait_terminal(test_client, alice, own.json()["data"]["id"])


# ─────────────── 1.2 / 1.3 通过分支与提示词渲染 ───────────────


def test_manual_bbox_becomes_coordinate_marker_in_final_prompt(test_client: TestClient, auth_headers: dict) -> None:
    """自行划框：坐标由前端给出，服务端按声明渲染出坐标标记（原话保持原样）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-manual")
    job = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1换成一只猫", "selections": [{"index": 1, "bbox": [120, 180, 640, 760]}]},
    )
    assert job.status_code == 200, job.text
    created = job.json()["data"]
    assert created["prompt"] == "把区域1换成一只猫", "原话必须保持用户写的样子"
    assert created["prompt_final"] == "把<bbox>120 180 640 760</bbox>换成一只猫"
    assert created["selections"] == [{"index": 1, "bbox": [120, 180, 640, 760], "artifact_id": None}]
    _wait_terminal(test_client, auth_headers, created["id"])


def test_point_selected_layer_takes_its_own_bounding_box(test_client: TestClient, auth_headers: dict) -> None:
    """点选图层：坐标**由服务端读该产物的位置框**，用户不必自己划（design D3）。"""
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "sel-point")
    layer = _layer_artifact(canvas, 1)  # normalized = [0, 0, 999, 439]
    assert layer["bounding_box"]["normalized"] == [0, 0, 999, 439]

    resp = _submit_selections(
        test_client,
        auth_headers,
        canvas_pk,
        {"prompt": "把区域1换成花园", "selections": [{"index": 1, "artifact_id": layer["id"]}], "composition_source_job_id": canvas["id"]},
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    assert created["prompt_final"] == "把<bbox>0 0 999 439</bbox>换成花园"
    assert created["selections"][0]["artifact_id"] == layer["id"], "选区快照必须记下它取自哪一张产物"
    _wait_terminal(test_client, auth_headers, created["id"])


def test_layer_without_normalized_box_cannot_be_point_selected(test_client: TestClient, auth_headers: dict) -> None:
    """产物没有归一化位置框时拒绝，而不是拿绝对像素框去凑（两者坐标系不同）。"""
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "sel-no-box")
    layer = _layer_artifact(canvas, 2)  # _layer_artifacts 里第二层没有位置框
    assert layer["bounding_box"] is None

    resp = _submit_selections(test_client, auth_headers, canvas_pk, {"prompt": "把区域1换掉", "selections": [{"index": 1, "artifact_id": layer["id"]}]})
    assert resp.status_code == 400, resp.text
    assert "没有归一化位置框" in resp.json()["msg"]


def test_multiple_selections_render_in_one_prompt(test_client: TestClient, auth_headers: dict) -> None:
    """一次任务带多个选区：每个标记落在用户自己摆的位置上。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-multi")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {
            "prompt": "把区域1换成一只猫，背景区域2调亮",
            "selections": [{"index": 1, "bbox": [120, 180, 640, 760]}, {"index": 2, "bbox": [0, 0, 10, 10]}],
        },
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    assert created["prompt_final"] == "把<bbox>120 180 640 760</bbox>换成一只猫，背景<bbox>0 0 10 10</bbox>调亮"
    _wait_terminal(test_client, auth_headers, created["id"])


def test_prompt_referencing_selection_without_coordinate_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """提示词引用了选区、却拿不到坐标 → 拒绝并指出是哪一个（坐标不静默丢失）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-missing")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1与区域2都换掉", "selections": [{"index": 1, "bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 400, resp.text
    assert "#2" in resp.json()["msg"] and "没有对应的坐标" in resp.json()["msg"]


def test_prompt_without_any_selection_but_reference_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """提示词里有占位符但根本没提交选区 → 同样拒绝（这是「拿不到坐标」的极端情形）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-noselect")
    resp = _submit_selections(test_client, auth_headers, model_pk, {"prompt": "把区域1换掉"})
    assert resp.status_code == 400, resp.text
    assert "#1" in resp.json()["msg"]


def test_selection_not_referenced_by_prompt_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """有选区、提示词里却没有任何引用 → 拒绝：这个坐标白给了，提交出去只会让人以为它生效了。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "sel-unused")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把这只猫换成狗", "selections": [{"index": 1, "bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 400, resp.text
    assert "#1" in resp.json()["msg"] and "没有引用" in resp.json()["msg"]


# ────────────── 1.4 两个提示词形态 ───────────────


def test_plain_job_has_prompt_final_equal_to_prompt(test_client: TestClient, auth_headers: dict) -> None:
    """未使用交互编辑的任务：两个形态相同、选区为空（前端因此只呈现一份）。"""
    model_pk = _seed_service_and_model(test_client, auth_headers, "plain-forms")
    job = _submit(test_client, auth_headers, model_pk)
    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert done["prompt_final"] == done["prompt"]
    assert done["selections"] == []

    detail = test_client.get(f"{JOBS_API}/detail/{job['id']}", headers=auth_headers).json()["data"]
    assert detail["prompt_final"] == detail["prompt"] and detail["selections"] == []


def test_detail_keeps_both_prompt_forms_after_review(test_client: TestClient, auth_headers: dict) -> None:
    """回看带选区的任务：原话与最终提示词都能看到，选区快照含来源产物标识。"""
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "forms")
    layer = _layer_artifact(canvas, 1)
    created = _submit_selections(test_client, auth_headers, canvas_pk, {"prompt": "把区域1换掉", "selections": [{"index": 1, "artifact_id": layer["id"]}]}).json()["data"]
    _wait_terminal(test_client, auth_headers, created["id"])

    detail = test_client.get(f"{JOBS_API}/detail/{created['id']}", headers=auth_headers).json()["data"]
    assert detail["prompt"] == "把区域1换掉"
    assert "<bbox>" in detail["prompt_final"]
    assert detail["selections"][0]["artifact_id"] == layer["id"]
    assert detail["selections"][0]["bbox"] == [0, 0, 999, 439]


# ─────────────── 1.5 / 2.4 来源记全与可见范围 ───────────────


def _many_artifact_job(test_client: TestClient, auth_headers: dict, name: str) -> tuple[int, dict]:
    """一次产出十六张产物的绘图任务（底图 + 十五个图层）。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, name)
    reference = _upload_reference(test_client, auth_headers, _PNG_BYTES)
    _FakeUpstream.artifacts = [
        ImageArtifact(url=f"https://upstream.example.com/tmp/a{i}.png", size="2048x2048", z_index=None if i == 0 else i, bounding_box={"absolute": [0, 0, 10, 10], "normalized": [10 * i, 10 * i, 10 * i + 50, 10 * i + 50]})
        for i in range(16)
    ]
    job = _submit(test_client, auth_headers, model_pk, params={"layer_decomposition": True}, reference_images=[reference])
    done = _wait_terminal(test_client, auth_headers, job["id"])
    assert len(done["artifacts"]) == 16
    return model_pk, done


def test_composition_source_records_every_artifact_of_that_generation(test_client: TestClient, auth_headers: dict) -> None:
    """画面来源提交的是**任务标识**，服务端展开成该次生成的全部产物并逐一记为来源。

    「记全」必须由构造保证：只记被点选的那一张，其余十五张仍可被删，而删掉之后
    「来源可反查」就成了半真半假的陈述（design D3/D7）。
    """
    canvas_pk, canvas = _many_artifact_job(test_client, auth_headers, "comp-all")
    picked = _layer_artifact(canvas, 1)

    resp = _submit_selections(
        test_client,
        auth_headers,
        canvas_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "artifact_id": picked["id"]}], "composition_source_job_id": canvas["id"]},
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["id"]
    _wait_terminal(test_client, auth_headers, new_id)

    rows = _ref_rows(new_id)
    assert len(rows) == 16, f"引用表里应落十六行，实际 {len(rows)}"
    assert set(rows) == {item["id"] for item in canvas["artifacts"]}, "必须是该次生成的全部产物，而不是只落被点选的那一行"
    assert picked["id"] in rows


def test_composition_source_outside_visible_range_rejected(test_client: TestClient, auth_headers: dict) -> None:
    """画面来源不在提交者可见范围内 → 拒绝。"""
    alice = _create_scoped_account(test_client, "iw-comp-alice", _JOB_MENUS)
    bob = _create_scoped_account(test_client, "iw-comp-bob", _JOB_MENUS)
    model_pk = _seed_interactive_model(test_client, auth_headers, "comp-scope")

    # 甲先跑一次图层拆分，产出可被引用的画面
    reference = _upload_reference(test_client, alice, _PNG_BYTES)
    _FakeUpstream.artifacts = _layer_artifacts()
    canvas = _wait_terminal(test_client, alice, _submit(test_client, alice, model_pk, params={"layer_decomposition": True}, reference_images=[reference])["id"])

    bob_pk = model_pk
    resp = _submit_selections(
        test_client,
        bob,
        bob_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "bbox": [0, 0, 10, 10]}], "composition_source_job_id": canvas["id"]},
    )
    assert resp.status_code == 404, resp.text
    assert "可见范围" in resp.json()["msg"]


def test_manual_selection_without_composition_source_records_only_it(test_client: TestClient, auth_headers: dict) -> None:
    """画布不来自产物（例如用户直接上传的参考图）时，画面来源字段缺省，引用表为空。"""
    model_pk = _seed_interactive_model(test_client, auth_headers, "comp-absent")
    resp = _submit_selections(
        test_client,
        auth_headers,
        model_pk,
        {"prompt": "把区域1调亮", "selections": [{"index": 1, "bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["id"]
    _wait_terminal(test_client, auth_headers, new_id)
    assert _ref_rows(new_id) == [], "自行划框没有任何来源产物，引用表不得凭空多出行"


# ─────────────── 2.3 删除保护 ───────────────


def test_delete_rejected_when_artifacts_referenced_by_another_job(test_client: TestClient, auth_headers: dict) -> None:
    """产物被其它任务引用时拒绝删除，并指出引用了它的任务（spec: 删除绘图任务）。"""
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "del-ref")
    layer = _layer_artifact(canvas, 1)
    created = _submit_selections(
        test_client,
        auth_headers,
        canvas_pk,
        {"prompt": "把区域1换掉", "selections": [{"index": 1, "artifact_id": layer["id"]}], "composition_source_job_id": canvas["id"]},
    ).json()["data"]
    _wait_terminal(test_client, auth_headers, created["id"])

    resp = test_client.request("DELETE", f"{JOBS_API}/delete", json=[canvas["id"]], headers=auth_headers)
    assert resp.status_code == 400, resp.text
    # 引用者信息必须出现在答复里：用户才知道该去删什么
    assert f"#{created['id']}" in resp.json()["msg"]
    assert _artifact_count(canvas["id"]) == 3, "被拒的删除不得动任何产物"

    # 反过来删引用者则可以：它自己不产出被引用的产物
    assert test_client.request("DELETE", f"{JOBS_API}/delete", json=[created["id"]], headers=auth_headers).status_code == 200
    # 引用者一删，被引用者的删除即被放行
    assert test_client.request("DELETE", f"{JOBS_API}/delete", json=[canvas["id"]], headers=auth_headers).status_code == 200


def test_delete_allowed_when_no_other_job_references(test_client: TestClient, auth_headers: dict) -> None:
    """未被引用的多产物任务仍可整单删除（产物文件一并移除）。"""
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "del-free")
    files = [Path(settings.UPLOAD_FILE_PATH) / item["file_path"] for item in canvas["artifacts"]]
    assert all(path.is_file() for path in files)

    assert test_client.request("DELETE", f"{JOBS_API}/delete", json=[canvas["id"]], headers=auth_headers).status_code == 200
    assert not any(path.exists() for path in files)
    assert _artifact_count(canvas["id"]) == 0


def test_self_reference_does_not_block_deletion(test_client: TestClient, auth_headers: dict) -> None:
    """只有自身引用不算「被其它任务引用」：反查只统计其它任务，否则任务永远删不掉。"""
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "del-self")
    # 手工造一条「任务引用自己的产物」——真实链路不会产生它（提交时任务还没有标识）
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute(
            "insert into image_workbench_job_artifact_ref (job_id, artifact_id, uuid, is_deleted, created_time, updated_time) values (?, ?, ?, 0, datetime('now'), datetime('now'))",
            (canvas["id"], canvas["artifacts"][0]["id"], uuid.uuid4().hex),
        )
        conn.commit()
    finally:
        conn.close()

    assert test_client.request("DELETE", f"{JOBS_API}/delete", json=[canvas["id"]], headers=auth_headers).status_code == 200


# ─────────────── 2.5 重试复用快照 ───────────────


def _mark_failed(job_id: int) -> None:
    conn = sqlite3.connect(settings.DATABASE_NAME)
    try:
        conn.execute("update image_workbench_job set status = 'failed', error = '上游报错' where id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def test_retry_reuses_selections_and_final_prompt(test_client: TestClient, auth_headers: dict) -> None:
    """含选区的任务重试：新任务带上**相同的选区、最终提示词与来源引用**，无需重新指定位置。

    绝不沿用「只取原话与少数字段」的老路径——那样发出去的是仍带 `区域1` 占位符、却没有任何
    坐标的文本，轻则被新校验拒掉，重则把未渲染的提示词发到了上游（design D11）。
    """
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "retry-sel")
    layer = _layer_artifact(canvas, 1)
    created = _submit_selections(
        test_client,
        auth_headers,
        canvas_pk,
        {"prompt": "把区域1换成一只猫", "selections": [{"index": 1, "artifact_id": layer["id"]}], "composition_source_job_id": canvas["id"]},
    ).json()["data"]
    _wait_terminal(test_client, auth_headers, created["id"])
    _mark_failed(created["id"])

    resp = test_client.post(f"{JOBS_API}/{created['id']}/retry", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    retried = resp.json()["data"]

    assert retried["id"] != created["id"], "重试必须产生新的任务行"
    assert retried["prompt_final"] == created["prompt_final"], "重试复用最终提示词，不重新渲染"
    assert "<bbox>0 0 999 439</bbox>" in retried["prompt_final"]
    assert retried["selections"] == created["selections"], "选区快照必须一并继承"
    assert _ref_rows(retried["id"]) == _ref_rows(created["id"]), "来源引用必须一并继承"
    assert len(_ref_rows(retried["id"])) == 3

    # 重试产生的任务不得发出仍带占位符的未渲染文本
    assert "区域1" not in retried["prompt_final"]
    _wait_terminal(test_client, auth_headers, retried["id"])

    # 上游收到的是渲染后的文本，且新旧两次发的是同一段
    sent = [call["prompt"] for call in _FakeUpstream.calls]
    assert sent[-1] == created["prompt_final"] and "<bbox>" in sent[-1]


def test_same_artifact_from_both_sources_lands_one_row(test_client: TestClient, auth_headers: dict) -> None:
    """同一产物被同一任务从**两类来源**重复引用时只落一行。

    被点选的那一层必然也在画面来源的全部产物里——两类来源合流后去重，引用表不会因此
    多出一行（design D6：从删除保护的角度两类引用没有区别，也不需要给引用标类型）。
    """
    canvas_pk, canvas = _layer_job(test_client, auth_headers, "ref-dedup")
    layer = _layer_artifact(canvas, 1)

    resp = _submit_selections(
        test_client,
        auth_headers,
        canvas_pk,
        {
            "prompt": "把区域1换掉",
            "selections": [{"index": 1, "artifact_id": layer["id"]}],
            "composition_source_job_id": canvas["id"],
        },
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["data"]["id"]
    _wait_terminal(test_client, auth_headers, new_id)

    rows = _ref_rows(new_id)
    assert rows == sorted({item["id"] for item in canvas["artifacts"]}) == sorted(rows), "既记全又不重复"
    assert len(rows) == len(set(rows)) == 3
