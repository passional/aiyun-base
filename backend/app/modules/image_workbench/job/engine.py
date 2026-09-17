"""绘图工作台 - 绘图任务执行引擎（设计 D5/D6/D7）

抄 ``channel_eval/run/engine.py`` 的整套做法：

- 请求事务提交后经 ``after_commit`` 启动（提前启动读不到未提交行）；
- ``asyncio.create_task`` + 模块级 ``_BG_TASKS`` 强引用集合，防 FastAPI 不持有引用
  导致任务被静默取消；
- 状态机 ``pending→running→success/failed/cancelled``，后台任务用独立会话
  （``async_db_session``），不占用请求事务；
- 进度写 Redis（``progress.py``）供断线重连，与 SSE 推送并行。

**产物转存（设计 D5）**：上游图片地址 24 小时后失效，因此拿到结果后立即取字节 →
临时文件 → 默认存储节点 ``adapter.upload`` → ``image_path`` 落库 → 删临时文件。
任一步失败即任务 ``failed``；``image_path`` 里**绝不存上游 URL**。

**取消（设计 D6）**：取消端点把任务直接落终态 ``cancelled`` 并在此置取消标志 + 取消
执行任务；引擎的收尾一律先读库内状态，已被取消则不覆盖（取消优先于成功）。
"""

import asyncio
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import path_conf
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules.task.storage.core.base import StorageProtocol
from app.modules.task.storage.node.service import StorageNodeService

from ..config.model import ImageWorkbenchModelModel, ImageWorkbenchServiceModel
from ..config.service import _parse_capability, decrypt_credential
from .client import ImageUpstreamClient, ImageUpstreamError
from .model import ImageWorkbenchJobModel
from .progress import write_progress
from .schema import STATUS_CANCELLED, STATUS_FAILED, STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS
from .sse_manager import image_workbench_job_manager

# 后台任务强引用集合：防止 asyncio.Task 被垃圾回收静默取消
_BG_TASKS: set[asyncio.Task] = set()
# job_id → 执行任务：取消时据此中断在途请求（含长耗时的上游出图）
_TASK_REGISTRY: dict[int, asyncio.Task] = {}
# 已请求取消的 job_id（任务边界处的快速退出判据）
_CANCEL_FLAGS: set[int] = set()

# 进度 Redis 键有效期（7 天，供断线重连读取终态）
_PROGRESS_TTL = 7 * 24 * 3600
# 产物在存储节点中的目录前缀（按日期分片，与 UploadUtil 的既有做法一致）
_ARTIFACT_PREFIX = "image_workbench"

_redis_client: Any = None


def _get_redis() -> Any:
    """引擎专用 Redis 连接（懒加载单例；测试环境 from_url 已被 mock）。"""
    global _redis_client
    if _redis_client is None:
        from redis.asyncio import Redis

        _redis_client = Redis.from_url(settings.REDIS_URI, encoding="utf-8", decode_responses=True)
    return _redis_client


# ────────────────────────── 任务注册与取消 ───────────────────────────


def start_job_engine(job_id: int) -> asyncio.Task:
    """启动绘图任务的后台执行（请求事务提交后调用）。"""
    task = asyncio.create_task(_execute_job(job_id), name=f"image-workbench-job-{job_id}")
    _BG_TASKS.add(task)
    _TASK_REGISTRY[job_id] = task

    def _cleanup(finished: asyncio.Task) -> None:
        _BG_TASKS.discard(finished)
        if _TASK_REGISTRY.get(job_id) is finished:
            _TASK_REGISTRY.pop(job_id, None)
        _CANCEL_FLAGS.discard(job_id)

    task.add_done_callback(_cleanup)
    return task


def is_cancelled(job_id: int) -> bool:
    """该任务是否已被请求取消。"""
    return job_id in _CANCEL_FLAGS


def request_cancel(job_id: int) -> None:
    """请求取消：置取消标志并取消在途执行任务。

    仅在确有存活的任务时登记标志（标志随任务结束清理，不无界增长）；任务未启动
    时由取消端点写入的终态 ``cancelled`` 兜底（引擎起始处会读到并直接退出）。
    """
    task = _TASK_REGISTRY.get(job_id)
    if task is not None and not task.done():
        _CANCEL_FLAGS.add(job_id)
        task.cancel()


async def recover_interrupted_jobs() -> int:
    """启动恢复钩子：把进程重启前遗留的进行中任务置为 ``failed``（任务 4.7）。

    照 ``recover_interrupted_runs()`` 的先例：执行进程已随重启消失，这些任务不可能
    再有结果，**不伪装成成功**，而是标注「执行进程中断」让用户一键重试
    （spec: generation / 服务重启后的任务状态「MUST 使任务状态不出现永久悬空」）。
    """
    async with async_db_session() as db, db.begin():
        result = await db.execute(select(ImageWorkbenchJobModel).where(ImageWorkbenchJobModel.status.in_([STATUS_PENDING, STATUS_RUNNING])))
        jobs = result.scalars().all()
        for job in jobs:
            job.status = STATUS_FAILED
            job.active_owner_id = None
            job.error = "执行进程中断，任务已终止"
            job.finished_at = datetime.now(UTC)
        return len(jobs)


# ─────────────────────────── 进度推送 ───────────────────────────


async def push_job_progress(job_id: int, owner_id: int | None, **fields: Any) -> None:
    """进度写 Redis + 定向 SSE 推送（尽力而为，不中断引擎）。

    按**任务所有者**推送：每条连接只收自己提交的任务的进度。
    """
    # owner_id 随进度一起落 Redis：SSE 端点据此判定回放快照的可见性，无需再查库
    payload: dict[str, Any] = {"job_id": job_id, "owner_id": owner_id}
    payload.update(fields)
    try:
        await write_progress(_get_redis(), payload, ttl=_PROGRESS_TTL)
    except Exception as e:  # noqa: BLE001
        logger.warning("绘图任务 {} 进度写 Redis 失败（不影响执行）: {}", job_id, e)
    try:
        await image_workbench_job_manager.send_to_user(owner_id, {"type": "job_progress", "data": payload})
    except Exception as e:  # noqa: BLE001
        logger.warning("绘图任务 {} 进度 SSE 推送失败（不影响执行）: {}", job_id, e)


# ─────────────────────────── 参考图解析 ───────────────────────────


def resolve_reference_files(paths: list[str] | None) -> list[str]:
    """参考图相对路径 → 本机绝对路径（同时是**越界校验**，提交与执行两处共用）。

    参考图由用户经模块专属 ``POST /image_workbench/jobs/reference-images/upload`` 上传，
    已落在 ``static/upload`` 下
    （design D2 的「参考图复用已有文件」）。这里只接受**该目录内**的路径：否则用户
    可以借参考图字段把服务器上的任意文件读出来上行给上游，是实打实的信息外泄面。
    """
    if not paths:
        return []
    root = settings.UPLOAD_FILE_PATH.resolve()
    resolved: list[str] = []
    for raw in paths:
        candidate = Path(raw)
        abs_path = (candidate if candidate.is_absolute() else Path.cwd() / candidate).resolve()
        if not abs_path.is_relative_to(root):
            raise CustomException(msg=f"参考图路径不在上传目录内: {raw}", code=400, status_code=400)
        if not abs_path.is_file():
            raise CustomException(msg=f"参考图不存在或已被清理: {raw}", code=400, status_code=400)
        resolved.append(str(abs_path))
    return resolved


# ─────────────────────────── 存储节点（产物） ───────────────────────────


def _guess_extension(raw: bytes, output_format: str | None) -> str:
    """产物扩展名：以**字节魔数**为准（上游实际回什么就是什么），再退回声明格式。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"GIF8"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    declared = (output_format or "").strip().lower()
    if declared and declared.isalnum():
        return "jpg" if declared == "jpeg" else declared
    return "png"


async def store_artifact_bytes(db: Any, job_uuid: str, raw: bytes, output_format: str | None) -> str:
    """产物字节 → 默认存储节点，返回其在存储节点中的相对路径。

    远端路径 ``image_workbench/{yyyy}/{mm}/{job_uuid}.{ext}``（按日期分片）。写临时
    文件 → ``adapter.upload`` → 删临时文件；失败向上抛，由调用方把任务判为 ``failed``。
    """
    now = datetime.now(UTC)
    extension = _guess_extension(raw, output_format)
    remote_path = f"{_ARTIFACT_PREFIX}/{now:%Y/%m}/{job_uuid}.{extension}"

    source = await StorageNodeService(AuthSchema(), db).get_active_source()
    adapter = StorageNodeService.build_adapter(source)
    fd, tmp_path = tempfile.mkstemp(prefix="iw_artifact_", suffix=f".{extension}")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        await adapter.upload(tmp_path, remote_path)
    finally:
        await adapter.close()
        try:
            os.unlink(tmp_path)
        except OSError:  # noqa: PERF203 — 临时文件清理失败不影响产物落库
            logger.warning("绘图工作台：产物临时文件清理失败: {}", tmp_path)
    return remote_path


def _local_abs_path(host: str | None, remote_path: str) -> str:
    """存储节点中的相对路径 → 本机绝对路径（local 协议专用，含穿越防护）。"""
    root = Path(host or "").resolve()
    abs_path = (root / remote_path.lstrip("/")).resolve()
    if not abs_path.is_relative_to(root):
        raise CustomException(msg="产物路径非法")
    return str(abs_path)


def artifact_url(source: Any, remote_path: str | None) -> str | None:
    """产物对外的直接访问地址（设计 D7）。

    两处与前缀有关的坑，缺一都会 404：

    1. 本地节点的 ``host`` 是**存储根目录**（种子值 ``static/upload``，相对后端工作目录
       解析），而静态挂载点服务的是 ``path_conf.STATIC_DIR``（``backend/static``）——
       两者不是同一个根。先把 host 换算成相对静态根目录的一段再拼，否则会得到
       ``/static/static/upload/...``，落到 ``backend/static/static/...``。
    2. 应用的对外 URL 空间以 ``ROOT_PATH`` 为根（Swagger 在 ``/api/v1/docs``，客户端
       也带 ``/api/v1`` 前缀），静态资源公开地址因此是 ``{ROOT_PATH}{STATIC_URL}`` =
       ``/api/v1/static``。只给 ``{STATIC_URL}`` 时请求进不了静态根（StaticFiles 拿
       scope 里含前缀的 path 去减挂载点，减不掉就 404）。

    种子节点据此得到 ``/api/v1/static/upload/{remote_path}``——前端经既有的 ``/api/v1``
    代理（dev 的 vite、docker 的 nginx）即可直接 ``<img>``，无需额外的静态路由。

    非本地协议（需签名 URL，首期不实现）、host 为空、或 host 落在静态根目录之外时返回
    None，前端退化为走下载接口。
    """
    if not remote_path or source is None:
        return None
    if getattr(source, "protocol", None) != StorageProtocol.LOCAL.value:
        return None
    host = (source.host or "").strip()
    if not host:
        return None
    static_root = path_conf.STATIC_DIR.resolve()
    root = Path(host)
    root_abs = (root if root.is_absolute() else Path.cwd() / root).resolve()
    if not root_abs.is_relative_to(static_root):
        return None
    relative = root_abs.relative_to(static_root).as_posix()
    prefix = "" if relative in ("", ".") else f"{relative.strip('/')}/"
    return f"{settings.ROOT_PATH}{settings.STATIC_URL}/{prefix}{remote_path.lstrip('/')}"


async def delete_artifact(db: Any, remote_path: str | None) -> None:
    """删除存储节点上的产物**文件本体**（设计 D8：软删记录 + 真删产物）。"""
    if not remote_path:
        return
    source = await StorageNodeService(AuthSchema(), db).get_active_source()
    adapter = StorageNodeService.build_adapter(source)
    try:
        await adapter.delete(remote_path)
    finally:
        await adapter.close()


async def prepare_artifact_download(db: Any, remote_path: str) -> tuple[str, bool]:
    """产物 → 本机可读路径，返回 ``(绝对路径, 是否为需清理的临时文件)``。

    ``local`` 协议直接读盘（零拷贝）；其它协议先经适配器下载到临时文件再返回，
    由调用方在响应结束后清理。
    """
    source = await StorageNodeService(AuthSchema(), db).get_active_source()
    if getattr(source, "protocol", None) == StorageProtocol.LOCAL.value:
        abs_path = _local_abs_path(source.host, remote_path)
        if not os.path.isfile(abs_path):
            raise CustomException(msg="产物文件不存在或已被清理")
        return abs_path, False

    adapter = StorageNodeService.build_adapter(source)
    fd, tmp_path = tempfile.mkstemp(prefix="iw_download_", suffix=Path(remote_path).suffix)
    os.close(fd)
    try:
        await adapter.download(remote_path, tmp_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        finally:
            await adapter.close()
        raise
    await adapter.close()
    return tmp_path, True


# ─────────────────────────── 主流程 ───────────────────────────


async def _finalize(
    job_id: int,
    status: str,
    *,
    owner_id: int | None = None,
    error: str | None = None,
    image_path: str | None = None,
    image_size: str | None = None,
    usage: dict[str, Any] | None = None,
    elapsed_ms: int | None = None,
    message: str | None = None,
) -> None:
    """收敛任务终态并推送进度。

    **取消优先**：库内已是 ``cancelled`` 时任何非取消终态都不覆盖——否则用户点了取消
    却在几秒后看到「生成成功」，取消语义就废了（spec: generation / 取消进行中的任务）。
    """
    async with async_db_session() as db, db.begin():
        job = await db.get(ImageWorkbenchJobModel, job_id)
        if job is None:
            return
        if job.status == STATUS_CANCELLED and status != STATUS_CANCELLED:
            logger.info("绘图任务 {} 已被取消，忽略终态 {} 的写入", job_id, status)
            return
        job.status = status
        if status in {STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED}:
            job.active_owner_id = None
        job.finished_at = datetime.now(UTC)
        job.error = error
        if image_path is not None:
            job.image_path = image_path
        if image_size is not None:
            job.image_size = image_size
        if usage is not None:
            job.usage_json = usage
        if elapsed_ms is not None:
            job.elapsed_ms = elapsed_ms
    await push_job_progress(
        job_id,
        owner_id,
        status=status,
        error=error,
        image_path=image_path,
        elapsed_ms=elapsed_ms,
        message=message,
    )


async def _execute_job(job_id: int) -> None:
    """执行一次绘图任务（独立于请求事务）。"""
    logger.info("绘图工作台：任务 {} 开始执行", job_id)
    started = time.perf_counter()
    owner_id: int | None = None
    try:
        async with async_db_session() as db:
            job = await db.get(ImageWorkbenchJobModel, job_id)
            if job is None:
                logger.warning("绘图工作台：任务 {} 不存在，跳过执行", job_id)
                return
            owner_id = job.created_id
            job_uuid = job.uuid
            model_id = job.model_id
            prompt = job.prompt
            params: dict[str, Any] = dict(job.params_json or {})
            model = await db.get(ImageWorkbenchModelModel, job.model_pk)
            service = await db.get(ImageWorkbenchServiceModel, job.service_id)

        if model is None or service is None:
            await _finalize(job_id, STATUS_FAILED, owner_id=owner_id, error="绘图模型或绘图服务已被删除", elapsed_ms=_elapsed(started), message="任务失败")
            return

        # 状态机：pending → running（已终态则直接退出，例如提交后立刻被取消）
        async with async_db_session() as db, db.begin():
            job = await db.get(ImageWorkbenchJobModel, job_id)
            if job is None or job.status != STATUS_PENDING:
                logger.info("绘图工作台：任务 {} 当前状态非 pending，跳过执行", job_id)
                return
            job.status = STATUS_RUNNING
            job.started_at = datetime.now(UTC)
            job.error = None
        await push_job_progress(job_id, owner_id, status=STATUS_RUNNING, message="已提交上游，正在生成…")

        if is_cancelled(job_id):
            await _finalize(job_id, STATUS_CANCELLED, owner_id=owner_id, elapsed_ms=_elapsed(started), message="任务已取消")
            return

        capability = _parse_capability(model.capability_json)
        reference_images = resolve_reference_files(params.get("reference_images") or [])
        client = ImageUpstreamClient(
            service.base_url,
            api_key=decrypt_credential(service.api_key_enc),
            dialect=service.dialect,
        )
        result = await client.generate(
            model=model_id,
            prompt=prompt,
            size=params.get("size"),
            output_format=params.get("output_format"),
            watermark=params.get("watermark"),
            reference_images=reference_images or None,
            extra_params=capability.extra_params,
        )
        if len(result.artifacts) != 1:
            logger.warning("绘图工作台：任务 {} 上游返回 {} 张图，按「一次任务一张图」取首张", job_id, len(result.artifacts))

        if is_cancelled(job_id):
            await _finalize(job_id, STATUS_CANCELLED, owner_id=owner_id, elapsed_ms=_elapsed(started), message="任务已取消")
            return

        # 产物转存（设计 D5）：拿到结果立即落盘，绝不把上游 URL 当长期访问方式
        await push_job_progress(job_id, owner_id, status=STATUS_RUNNING, message="正在转存产物…")
        raw_bytes = await client.fetch_image_bytes(result.artifacts[0])
        if is_cancelled(job_id):
            await _finalize(job_id, STATUS_CANCELLED, owner_id=owner_id, elapsed_ms=_elapsed(started), message="任务已取消")
            return

        async with async_db_session() as db:
            image_path = await store_artifact_bytes(db, job_uuid, raw_bytes, params.get("output_format"))

        await _finalize(
            job_id,
            STATUS_SUCCESS,
            owner_id=owner_id,
            image_path=image_path,
            image_size=result.artifacts[0].size,
            usage={"images": 1, "upstream": result.usage or {}},
            elapsed_ms=_elapsed(started),
            message="生成成功",
        )
        logger.info("绘图工作台：任务 {} 执行完成，产物 {}", job_id, image_path)
    except asyncio.CancelledError:
        # 用户取消：取消端点已把任务落为终态 cancelled，这里只补耗时并收敛进度
        logger.info("绘图工作台：任务 {} 被取消，停止执行", job_id)
        await _finalize(job_id, STATUS_CANCELLED, owner_id=owner_id, elapsed_ms=_elapsed(started), message="任务已取消")
        raise
    except ImageUpstreamError as e:
        logger.warning("绘图工作台：任务 {} 上游调用失败: {}", job_id, e.message)
        await _finalize(job_id, STATUS_FAILED, owner_id=owner_id, error=e.message, elapsed_ms=_elapsed(started), message="任务失败")
    except Exception as e:  # noqa: BLE001
        logger.error("绘图工作台：任务 {} 执行异常: {}", job_id, e)
        await _finalize(job_id, STATUS_FAILED, owner_id=owner_id, error=str(e)[:2000], elapsed_ms=_elapsed(started), message="任务失败")


def _elapsed(started: float) -> int:
    """引擎自计时（毫秒）：比 started_at/finished_at 之差更贴近真实耗时。"""
    return round((time.perf_counter() - started) * 1000)
