"""绘图任务执行引擎直测（任务 4.2、4.5）

以独立 sqlite（``iw_db``）+ monkeypatch 替换引擎模块的会话工厂与上游客户端：
- 4.2 状态机 pending→running→success；取消语义（cancelled 不被成功覆盖）
- 4.2 多产物转存：全成功 / 中途写入失败（整单失败且不留部分产物）/ 上游只回 b64
- 4.5 用量张数按实际产物数
- 进度写 Redis
- 启动恢复钩子
"""

import asyncio
import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.image_workbench.config.model import (
    ImageWorkbenchModelModel,
    ImageWorkbenchServiceModel,
)
from app.modules.image_workbench.job import engine as engine_mod
from app.modules.image_workbench.job.client import ImageArtifact, ImageGenerationResult
from app.modules.image_workbench.job.model import ImageWorkbenchArtifactModel, ImageWorkbenchJobModel
from app.modules.image_workbench.job.progress import read_progress
from app.modules.task.storage.node.model import StorageNodeModel
from app.utils.crypto_util import CryptoUtil

# 最小合法 PNG 头部：转存逻辑按字节魔数判扩展名
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

_CAPABILITY: dict[str, Any] = {
    "reference": {"supported": True, "max_images": 4},
    "modes": [],
    "params": [
        {
            "key": "size",
            "label": {"zh": "尺寸", "en": "Size"},
            "control": "select",
            "allowed": ["1K", "2K"],
            "default": "2K",
            "source": "documented",
        },
        {
            "key": "output_format",
            "label": {"zh": "输出格式", "en": "Output format"},
            "control": "select",
            "allowed": ["png", "jpeg"],
            "default": "png",
            "source": "documented",
        },
        {"key": "watermark", "label": {"zh": "水印", "en": "Watermark"}, "control": "switch", "default": False, "source": "documented"},
    ],
}


def _base_artifact(**overrides: Any) -> ImageArtifact:
    kwargs: dict[str, Any] = {"url": "https://upstream.example.com/tmp/a.png", "size": "2048x2048"}
    kwargs.update(overrides)
    return ImageArtifact(**kwargs)


class _FakeUpstream:
    """假上游客户端：不发起任何真实 HTTP，行为由类属性驱动。"""

    artifacts: list[ImageArtifact] = []
    usage: dict[str, Any] | None = None
    fail_with: Exception | None = None
    gate: asyncio.Event | None = None
    generate_calls: list[dict[str, Any]] = []

    def __init__(self, base_url: str, *, api_key: str | None = None, dialect: str = "ark", timeout: float | None = None) -> None:
        self.base_url = base_url
        self.dialect = dialect
        self.api_key = api_key

    async def generate(self, **kwargs: Any) -> ImageGenerationResult:
        _FakeUpstream.generate_calls.append(kwargs)
        if _FakeUpstream.gate is not None:
            await _FakeUpstream.gate.wait()  # 挂住：模拟长耗时的上游出图
        if _FakeUpstream.fail_with is not None:
            raise _FakeUpstream.fail_with
        return ImageGenerationResult(
            artifacts=list(_FakeUpstream.artifacts),
            usage=_FakeUpstream.usage,
            raw={"data": []},
        )

    async def fetch_image_bytes(self, artifact: ImageArtifact) -> bytes:
        if artifact.b64_json:
            return base64.b64decode(artifact.b64_json)
        return _PNG_BYTES


class _RecordingAdapter:
    """真写盘的存储适配器替身：可指定第几次上传失败，并记录上传与删除。"""

    def __init__(self, root: Path, *, fail_on: int | None = None) -> None:
        self.root = root
        self.fail_on = fail_on
        self.uploads: list[str] = []
        self.deleted: list[str] = []

    async def upload(self, local_path: str, remote_path: str) -> str:
        if self.fail_on is not None and len(self.uploads) + 1 == self.fail_on:
            raise RuntimeError("模拟存储节点写入失败")
        target = self.root / remote_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(local_path).read_bytes())
        self.uploads.append(remote_path)
        return remote_path

    async def delete(self, remote_path: str) -> None:
        (self.root / remote_path).unlink(missing_ok=True)
        self.deleted.append(remote_path)

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_upstream(monkeypatch: pytest.MonkeyPatch):
    """把引擎模块内的上游客户端换成假客户端，并复位其类属性。"""
    _FakeUpstream.artifacts = [_base_artifact()]
    _FakeUpstream.usage = {"total_tokens": 42, "generated_images": 1}
    _FakeUpstream.fail_with = None
    _FakeUpstream.gate = None
    _FakeUpstream.generate_calls = []
    monkeypatch.setattr(engine_mod, "ImageUpstreamClient", _FakeUpstream)
    yield _FakeUpstream


@pytest.fixture
async def engine_env(iw_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch, tmp_path):
    """引擎直测环境：会话工厂指向独立库，默认存储节点指向临时目录。"""
    monkeypatch.setattr(engine_mod, "async_db_session", iw_db)
    async with iw_db() as db, db.begin():
        db.add(StorageNodeModel(name="本地上传目录", protocol="local", host=str(tmp_path), port=0, is_default=True, status=0))
    yield engine_mod


async def _seed_job(
    db_maker: async_sessionmaker,
    *,
    status: str = "pending",
    capability: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    reference_images: list[str] | None = None,
    created_id: int | None = None,
    name: str = "ark-plan",
) -> tuple[int, int, int]:
    """插入 绘图服务 + 绘图模型 + 绘图任务，返回 (service_id, model_pk, job_id)。"""
    async with db_maker() as db, db.begin():
        service = ImageWorkbenchServiceModel(
            name=name,
            dialect="ark",
            base_url="https://ark.example.com/api/plan/v3",
            api_key_enc=CryptoUtil.encrypt("sk-test"),
            status=0,
        )
        db.add(service)
        await db.flush()
        model = ImageWorkbenchModelModel(
            service_id=service.id,
            model_id="doubao-seedream-5-0-pro",
            display_name="Seedream 5.0 pro",
            capability_json=capability if capability is not None else _CAPABILITY,
            status=0,
        )
        db.add(model)
        await db.flush()
        job = ImageWorkbenchJobModel(
            service_id=service.id,
            model_pk=model.id,
            model_id=model.model_id,
            prompt="一只在雪地里的猫",
            prompt_final="一只在雪地里的猫",
            params_json=params if params is not None else {"size": "2K", "output_format": "png", "watermark": False},
            reference_images_json=reference_images,
            status=status,
            created_id=created_id,
        )
        db.add(job)
        await db.flush()
        return service.id, model.id, job.id


async def _fetch_job(db_maker: async_sessionmaker, job_id: int) -> ImageWorkbenchJobModel:
    async with db_maker() as db:
        return await db.get(ImageWorkbenchJobModel, job_id)


async def _fetch_artifacts(db_maker: async_sessionmaker, job_id: int) -> list[ImageWorkbenchArtifactModel]:
    async with db_maker() as db:
        rows = (await db.execute(select(ImageWorkbenchArtifactModel).where(ImageWorkbenchArtifactModel.job_id == job_id))).scalars().all()
        return sorted(rows, key=lambda row: (row.z_index is not None, row.z_index or 0))


# ─────────────────────── 状态机与取消 ───────────────────────


async def test_full_job_state_machine(engine_env, fake_upstream, iw_db: async_sessionmaker) -> None:
    """假上游跑完一次任务：pending→running→success，产物行落库、参数原样上行。"""
    _, _, job_id = await _seed_job(iw_db)

    await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.status == "success"
    assert job.error is None
    assert job.started_at is not None and job.finished_at is not None

    artifacts = await _fetch_artifacts(iw_db, job_id)
    assert len(artifacts) == 1
    assert artifacts[0].z_index is None and artifacts[0].is_base is True
    assert artifacts[0].file_path.endswith(f"/{job.uuid}/base.png")
    # 上游 URL 绝不进 file_path（design D5：上游地址 24 小时失效）
    assert "https://" not in artifacts[0].file_path
    assert artifacts[0].image_size == "2048x2048"

    # 参数按快照**原样**上行：引擎不做任何参数分支
    call = fake_upstream.generate_calls[-1]
    assert call["model"] == "doubao-seedream-5-0-pro"
    assert call["params"] == {"size": "2K", "output_format": "png", "watermark": False}
    assert call["reference_images"] is None


async def test_cancel_marks_cancelled_and_success_never_overwrites(engine_env, fake_upstream, iw_db: async_sessionmaker) -> None:
    """取消：挂住的上游调用被中断，任务落 cancelled；后续成功写入不得覆盖它。"""
    _, _, job_id = await _seed_job(iw_db)
    fake_upstream.gate = asyncio.Event()  # 让引擎停在 await 边界上

    task = engine_mod.start_job_engine(job_id)
    for _ in range(200):  # 等引擎真正进入上游调用（引擎内有真实 DB I/O）
        await asyncio.sleep(0.02)
        if fake_upstream.generate_calls:
            break
    assert fake_upstream.generate_calls, "引擎应已开始调用上游"

    # 模拟取消端点：落终态 + 请求取消
    async with iw_db() as db, db.begin():
        job = await db.get(ImageWorkbenchJobModel, job_id)
        job.status = "cancelled"
    engine_mod.request_cancel(job_id)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await _fetch_job(iw_db, job_id)).status == "cancelled"

    # 取消优先：即使引擎随后尝试写终态成功，也不得覆盖 cancelled
    await engine_mod._finalize(job_id, "success", artifacts=[{"file_path": "image_workbench/2026/09/x.png"}])
    job = await _fetch_job(iw_db, job_id)
    assert job.status == "cancelled"
    assert await _fetch_artifacts(iw_db, job_id) == [], "取消后不得留下指向产物的成功记录"


# ────────────────────── 多产物转存 ───────────────────────


def _layer_artifacts() -> list[ImageArtifact]:
    """上游图层拆分的返回：一张底图 + 两个图层。"""
    return [
        _base_artifact(),
        _base_artifact(
            url="https://upstream.example.com/tmp/l1.png",
            z_index=1,
            name="天空",
            description="整片天空与云",
            bounding_box={"absolute": [0, 0, 2048, 900], "normalized": [0, 0, 999, 439]},
        ),
        _base_artifact(url="https://upstream.example.com/tmp/l2.png", z_index=2, name="人物"),
    ]


async def test_layer_decomposition_stores_base_and_all_layers(engine_env, fake_upstream, iw_db: async_sessionmaker, tmp_path) -> None:
    """多产物全部转存：底图 base.png、图层 layer_01.png / layer_02.png 各自落盘并落行。"""
    fake_upstream.artifacts = _layer_artifacts()
    _, _, job_id = await _seed_job(iw_db, params={"size": "auto", "layer_decomposition": True})

    await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.status == "success"
    artifacts = await _fetch_artifacts(iw_db, job_id)
    assert len(artifacts) == 3

    names = [Path(row.file_path).name for row in artifacts]
    assert names == ["base.png", "layer_01.png", "layer_02.png"]
    assert Path(artifacts[0].file_path).parent == Path(f"image_workbench/{job.created_time:%Y/%m}/{job.uuid}")
    assert all((tmp_path / row.file_path).is_file() for row in artifacts), "每一张都必须真实落盘"

    assert artifacts[0].z_index is None and artifacts[0].name is None and artifacts[0].bounding_box_json is None
    assert artifacts[1].z_index == 1 and artifacts[1].name == "天空" and artifacts[1].description == "整片天空与云"
    assert artifacts[1].bounding_box_json == {"absolute": [0, 0, 2048, 900], "normalized": [0, 0, 999, 439]}
    assert artifacts[2].z_index == 2 and artifacts[2].name == "人物"

    # 生成张数按实际产物数记（4.5）
    assert job.usage_json["images"] == 3
    assert job.usage_json["upstream"] == {"total_tokens": 42, "generated_images": 1}


async def test_mid_transfer_failure_leaves_no_partial_artifacts(engine_env, fake_upstream, iw_db: async_sessionmaker, tmp_path) -> None:
    """第二张写入失败 → 整单失败，已上传的那张被回滚，产物行一张不留。"""
    fake_upstream.artifacts = _layer_artifacts()
    _, _, job_id = await _seed_job(iw_db, params={"size": "auto", "layer_decomposition": True})
    adapter = _RecordingAdapter(tmp_path, fail_on=2)

    with patch.object(engine_mod.StorageNodeService, "build_adapter", staticmethod(lambda source: adapter)):
        await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.status == "failed"
    assert "存储节点写入失败" in (job.error or "")
    assert await _fetch_artifacts(iw_db, job_id) == [], "失败任务不得留下产物行"
    assert adapter.deleted == adapter.uploads, "已上传的那张必须被回滚"
    assert list(tmp_path.rglob("*.png")) == [], "存储节点不得留下部分产物"


async def test_artifact_stored_from_b64_only_response(engine_env, fake_upstream, iw_db: async_sessionmaker, tmp_path) -> None:
    """上游只回 b64_json（无 url）时同样能取到字节并逐张落盘。"""
    fake_upstream.artifacts = [
        ImageArtifact(b64_json=base64.b64encode(_PNG_BYTES).decode("ascii")),
        ImageArtifact(b64_json=base64.b64encode(_PNG_BYTES).decode("ascii"), z_index=1, name="图层一"),
    ]
    _, _, job_id = await _seed_job(iw_db)

    await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.status == "success"
    artifacts = await _fetch_artifacts(iw_db, job_id)
    assert len(artifacts) == 2
    assert all((tmp_path / row.file_path).read_bytes() == _PNG_BYTES for row in artifacts)


async def test_storage_upload_failure_marks_job_failed(engine_env, fake_upstream, iw_db: async_sessionmaker, tmp_path) -> None:
    """转存失败 → 任务 failed，且不留产物行（不留指向失效地址的成功记录）。"""
    _, _, job_id = await _seed_job(iw_db)
    adapter = _RecordingAdapter(tmp_path, fail_on=1)

    with patch.object(engine_mod.StorageNodeService, "build_adapter", staticmethod(lambda source: adapter)):
        await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.status == "failed"
    assert await _fetch_artifacts(iw_db, job_id) == []
    assert "存储节点写入失败" in (job.error or "")


# ─────────────────────── 用量与进度 ───────────────────────


async def test_usage_and_elapsed_recorded(engine_env, fake_upstream, iw_db: async_sessionmaker) -> None:
    """成功任务的 usage_json 含上游字段与生成张数，elapsed_ms 非空。"""
    _, _, job_id = await _seed_job(iw_db)

    await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.usage_json is not None
    assert job.usage_json["images"] == 1
    assert job.usage_json["upstream"] == {"total_tokens": 42, "generated_images": 1}
    assert job.elapsed_ms is not None and job.elapsed_ms >= 0


async def test_progress_written_to_redis(engine_env, fake_upstream, iw_db: async_sessionmaker) -> None:
    """引擎把进度写 Redis（断线重连据此回放，不停留在「进行中」）。"""
    _, _, job_id = await _seed_job(iw_db)

    await engine_mod._execute_job(job_id)

    redis = engine_mod._get_redis()
    snapshot = await read_progress(redis, job_id)
    assert snapshot is not None
    assert snapshot["job_id"] == job_id
    assert snapshot["status"] == "success", "终态必须落进度，供重连回放"
    assert snapshot["artifact_count"] == 1


# ─────────────────────── 启动恢复钩子 ───────────────────────


async def test_recover_interrupted_jobs(engine_env, iw_db: async_sessionmaker) -> None:
    """启动恢复：遗留 pending/running 置 failed 且原因可辨识为执行进程中断。"""
    _, _, pending_id = await _seed_job(iw_db, status="pending", name="svc-pending")
    _, _, running_id = await _seed_job(iw_db, status="running", name="svc-running")
    _, _, done_id = await _seed_job(iw_db, status="success", name="svc-success")

    recovered = await engine_mod.recover_interrupted_jobs()
    assert recovered == 2

    for job_id in (pending_id, running_id):
        job = await _fetch_job(iw_db, job_id)
        assert job.status == "failed"
        assert job.error == "执行进程中断，任务已终止"
        assert job.finished_at is not None
    assert (await _fetch_job(iw_db, done_id)).status == "success", "已结束的任务不受恢复钩子影响"


async def test_recover_is_idempotent(engine_env, iw_db: async_sessionmaker) -> None:
    """恢复钩子幂等：连续调用第二次不再改动任何任务。"""
    _, _, job_id = await _seed_job(iw_db, status="running")
    assert await engine_mod.recover_interrupted_jobs() == 1
    assert await engine_mod.recover_interrupted_jobs() == 0
    assert (await _fetch_job(iw_db, job_id)).status == "failed"


# ─────────────────────── 参考图路径越界防护 ───────────────────────


async def test_reference_image_outside_upload_dir_rejected(engine_env, iw_db: async_sessionmaker) -> None:
    """参考图路径必须落在上传目录内（否则可借该字段读取服务器任意文件）。"""
    from app.core.exceptions import CustomException

    # 该文件中不存在于上传目录内（settings.UPLOAD_FILE_PATH = static/upload）
    with pytest.raises(CustomException) as exc:
        engine_mod.resolve_reference_files(["/etc/passwd"])
    assert "上传目录" in exc.value.msg or "不存在" in exc.value.msg


async def test_engine_rejects_tampered_reference_path(engine_env, fake_upstream, iw_db: async_sessionmaker) -> None:
    """执行期再次校验参考图路径：库内被改成越界路径时任务失败，不上行任何文件。"""
    _, _, job_id = await _seed_job(iw_db, reference_images=["/etc/passwd"])

    await engine_mod._execute_job(job_id)

    job = await _fetch_job(iw_db, job_id)
    assert job.status == "failed"
    assert not fake_upstream.generate_calls, "越界参考图不得发起上游调用"


async def test_job_list_query_filters(engine_env, iw_db: async_sessionmaker) -> None:
    """引擎侧不改动查询语义：确保 select 与模型列名匹配（防拼写漂移）。"""
    _, _, job_id = await _seed_job(iw_db)
    async with iw_db() as db:
        row = (await db.execute(select(ImageWorkbenchJobModel.id).where(ImageWorkbenchJobModel.id == job_id))).scalar()
    assert row == job_id


# ────────────── 产物对外地址：静态挂载点与存储根目录不是同一个根 ──────────────


class _FakeStorageSource:
    """存储节点配置的最小替身（只需 protocol / host 两列）。"""

    def __init__(self, protocol: str, host: str | None) -> None:
        self.protocol = protocol
        self.host = host


def test_artifact_url_is_relative_to_static_root(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """种子节点 host=static/upload（相对后端工作目录），静态挂载点服务 backend/static。

    URL 必须是 ``{ROOT_PATH}{STATIC_URL}`` 打头（对外 URL 空间以 /api/v1 为根），
    且只拼 host 相对静态根目录的那一段：漏掉二者之一都会 404
    （``/static/static/upload/...`` 或 ``/static/upload/...``）。
    """
    static_dir = tmp_path / "static"
    monkeypatch.setattr(engine_mod.path_conf, "STATIC_DIR", static_dir)
    monkeypatch.chdir(tmp_path)

    seed_node = _FakeStorageSource("local", "static/upload")
    assert (
        engine_mod.artifact_url(seed_node, "image_workbench/2026/09/uuid/base.png")
        == "/api/v1/static/upload/image_workbench/2026/09/uuid/base.png"
    )

    # host 恰为静态根目录时不能再补一段
    assert engine_mod.artifact_url(_FakeStorageSource("local", "static"), "a.png") == "/api/v1/static/a.png"

    # 绝对路径形式的同一节点给出相同结果
    abs_node = _FakeStorageSource("local", str(static_dir / "upload"))
    assert engine_mod.artifact_url(abs_node, "a.png") == "/api/v1/static/upload/a.png"


def test_artifact_url_degrades_when_not_locally_served(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """非本地协议、空 host、静态根目录之外、缺产物路径一律返回 None（前端退化走下载接口）。"""
    monkeypatch.setattr(engine_mod.path_conf, "STATIC_DIR", tmp_path / "static")

    assert engine_mod.artifact_url(_FakeStorageSource("local", "/data/elsewhere"), "a.png") is None
    assert engine_mod.artifact_url(_FakeStorageSource("s3", "bucket"), "a.png") is None
    assert engine_mod.artifact_url(_FakeStorageSource("local", ""), "a.png") is None
    assert engine_mod.artifact_url(_FakeStorageSource("local", "static/upload"), None) is None
    assert engine_mod.artifact_url(None, "a.png") is None
