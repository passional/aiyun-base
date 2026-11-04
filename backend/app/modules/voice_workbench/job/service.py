"""语音工作台 - 转写任务业务层。

**转写不使用请求级事务**：校验失败与上游失败都要求「先落一条失败任务，再抛业务错误」，
而请求级事务会随异常整体回滚。因此 ``transcribe`` 打开独立会话，并在关键节点显式
提交；``finally`` 释放每用户 Redis 锁。

**可见范围**：列表、详情、删除一律显式按 ``created_id == 当前用户`` 过滤，超管也不例外
（spec：历史只返回本人任务）。
"""

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import UploadFile
from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.common_util import search_to_dict

from ..config.model import VoiceWorkbenchModelModel, VoiceWorkbenchServiceModel
from ..config.service import decrypt_credential
from .client import VoiceUpstreamClient, VoiceUpstreamError
from .crud import VoiceWorkbenchJobCRUD
from .model import VoiceWorkbenchJobModel
from .schema import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    VoiceWorkbenchJobModelOptionSchema,
    VoiceWorkbenchJobOutSchema,
    VoiceWorkbenchJobQueryParam,
    VoiceWorkbenchSegmentSchema,
    status_label,
)
from .wav_validation import WavValidationError, inspect_wav

LOCK_TTL_SEC = 600


def _now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started: datetime) -> int:
    return max(0, int((_now() - started).total_seconds() * 1000))


def _safe_filename(name: str | None) -> str:
    """只保留文件名本身，去掉客户端可能带上的目录段，并裁到列宽。"""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (base or "audio.wav")[:255]


def _job_to_out(job: VoiceWorkbenchJobModel, *, service_name: str | None, model_display_name: str | None) -> VoiceWorkbenchJobOutSchema:
    segments: list[VoiceWorkbenchSegmentSchema] = []
    for item in job.segments_json or []:
        if not isinstance(item, dict):
            continue
        try:
            segments.append(VoiceWorkbenchSegmentSchema(**item))
        except (TypeError, ValueError):
            continue
    return VoiceWorkbenchJobOutSchema(
        id=job.id,
        uuid=job.uuid,
        created_time=job.created_time,
        updated_time=job.updated_time,
        is_deleted=job.is_deleted,
        deleted_time=job.deleted_time,
        service_id=job.service_id,
        service_name=service_name,
        model_pk=job.model_pk,
        model_name=job.model_name,
        model_display_name=model_display_name,
        original_filename=job.original_filename,
        file_size=job.file_size,
        duration_sec=job.duration_sec,
        text=job.text,
        segments=segments,
        status=job.status,
        status_label=status_label(job.status),
        error=job.error,
        elapsed_ms=job.elapsed_ms,
        usage=job.usage_json if isinstance(job.usage_json, dict) else None,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_id=job.created_id,
    )


class VoiceWorkbenchJobService:
    """转写任务服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = VoiceWorkbenchJobCRUD(auth, db)

    # ────────────────────── 工作台可选模型 ──────────────────────

    async def list_available_models(self) -> list[VoiceWorkbenchJobModelOptionSchema]:
        """可选模型：模型启用、所属服务启用、服务已配置凭证。"""
        stmt = (
            select(VoiceWorkbenchModelModel, VoiceWorkbenchServiceModel.name)
            .join(VoiceWorkbenchServiceModel, VoiceWorkbenchModelModel.service_id == VoiceWorkbenchServiceModel.id)
            .where(
                VoiceWorkbenchModelModel.status == 0,
                VoiceWorkbenchModelModel.is_deleted == False,  # noqa: E712
                VoiceWorkbenchServiceModel.status == 0,
                VoiceWorkbenchServiceModel.is_deleted == False,  # noqa: E712
                VoiceWorkbenchServiceModel.api_key_enc.isnot(None),
                VoiceWorkbenchServiceModel.api_key_enc != "",
            )
            .order_by(VoiceWorkbenchServiceModel.id, VoiceWorkbenchModelModel.id)
        )
        rows = await self.db.execute(stmt)
        return [
            VoiceWorkbenchJobModelOptionSchema(
                id=model.id,
                service_id=model.service_id,
                service_name=service_name,
                display_name=model.display_name,
                model_name=model.model_name,
                max_file_mb=model.max_file_mb,
                max_duration_sec=model.max_duration_sec,
            )
            for model, service_name in rows.all()
        ]

    # ─────────────────────────── 转写 ───────────────────────────

    async def transcribe(self, *, file: UploadFile, model_id: int, redis: Redis) -> VoiceWorkbenchJobOutSchema:
        """同步完成一次转写；任何失败都在释放锁之前落库。"""
        owner_id = self.auth.user.id or 0
        lock_key = f"voice_workbench:transcribe:lock:{owner_id}"
        acquired = await redis.set(lock_key, "1", ex=LOCK_TTL_SEC, nx=True)
        if not acquired:
            raise CustomException(msg="有正在进行的识别任务，请等待其结束", code=400)

        try:
            async with async_db_session() as db:
                crud = VoiceWorkbenchJobCRUD(self.auth, db)
                model, service = await self._require_usable_model(db, model_id)

                limit_bytes = model.max_file_mb * 1024 * 1024
                # 只读到「上限 + 1」字节：超限文件不必整块进内存，也能在解析 WAV 前拒绝。
                content = await file.read(limit_bytes + 1)
                filename = _safe_filename(file.filename)
                if len(content) > limit_bytes:
                    file_size = file.size if isinstance(file.size, int) and file.size >= 0 else len(content)
                    error = f"文件大小超过上限 {model.max_file_mb} MB"
                    await self._record_failed(crud, model=model, service=service, filename=filename, file_size=file_size, duration_sec=0, error=error)
                    raise CustomException(msg=error, code=400)
                file_size = len(content)

                try:
                    info = inspect_wav(content)
                except WavValidationError as exc:
                    await self._record_failed(crud, model=model, service=service, filename=filename, file_size=file_size, duration_sec=0, error=str(exc))
                    raise CustomException(msg=str(exc), code=400) from exc

                if info.duration_sec > model.max_duration_sec:
                    error = f"音频时长超过上限 {model.max_duration_sec} 秒"
                    await self._record_failed(
                        crud,
                        model=model,
                        service=service,
                        filename=filename,
                        file_size=file_size,
                        duration_sec=info.duration_sec,
                        error=error,
                    )
                    raise CustomException(msg=error, code=400)

                started = _now()
                job = await crud.create(
                    {
                        "service_id": service.id,
                        "model_pk": model.id,
                        "model_name": model.model_name,
                        "original_filename": filename,
                        "file_size": file_size,
                        "duration_sec": info.duration_sec,
                        "status": STATUS_RUNNING,
                        "started_at": started,
                    }
                )
                await db.commit()

                try:
                    client = VoiceUpstreamClient(
                        model.endpoint_url,
                        api_key=decrypt_credential(service.api_key_enc),
                        resource_id=model.resource_id,
                    )
                    result = await client.transcribe(model_name=model.model_name, uid=str(owner_id), audio=content)
                except VoiceUpstreamError as exc:
                    await self._finish_failed(db, job, exc.message, started)
                    raise CustomException(msg=exc.message, code=400) from exc
                except Exception as exc:
                    # 预期之外的故障按 5xx 暴露，但任务不能永久停在 running
                    await self._finish_failed(db, job, f"识别失败: {exc}", started)
                    raise

                job.text = result.text
                job.segments_json = [asdict(segment) for segment in result.segments]
                job.usage_json = result.usage
                job.elapsed_ms = _elapsed_ms(started)
                job.status = STATUS_SUCCESS
                job.finished_at = _now()
                await db.commit()
                return _job_to_out(job, service_name=service.name, model_display_name=model.display_name)
        finally:
            try:
                await redis.delete(lock_key)
            except Exception as exc:  # noqa: BLE001 — 释放失败不能覆盖原始错误
                logger.warning("语音工作台：释放转写锁失败 key={} error={}", lock_key, exc)

    async def _require_usable_model(self, db: AsyncSession, model_id: int) -> tuple[VoiceWorkbenchModelModel, VoiceWorkbenchServiceModel]:
        stmt = (
            select(VoiceWorkbenchModelModel, VoiceWorkbenchServiceModel)
            .join(VoiceWorkbenchServiceModel, VoiceWorkbenchModelModel.service_id == VoiceWorkbenchServiceModel.id)
            .where(
                VoiceWorkbenchModelModel.id == model_id,
                VoiceWorkbenchModelModel.is_deleted == False,  # noqa: E712
                VoiceWorkbenchServiceModel.is_deleted == False,  # noqa: E712
            )
        )
        row = (await db.execute(stmt)).first()
        if row is None:
            raise CustomException(msg="语音识别模型不存在", code=400)
        model, service = row
        if model.status != 0:
            raise CustomException(msg="语音识别模型已停用，请先在语音服务配置中启用", code=400)
        if service.status != 0:
            raise CustomException(msg="所属语音服务已停用，请先在语音服务配置中启用", code=400)
        if not service.api_key_enc:
            raise CustomException(msg="所属语音服务尚未配置调用凭证，请先在语音服务配置中填写", code=400)
        return model, service

    @staticmethod
    async def _record_failed(
        crud: VoiceWorkbenchJobCRUD,
        *,
        model: VoiceWorkbenchModelModel,
        service: VoiceWorkbenchServiceModel,
        filename: str,
        file_size: int,
        duration_sec: int,
        error: str,
    ) -> None:
        now = _now()
        await crud.create(
            {
                "service_id": service.id,
                "model_pk": model.id,
                "model_name": model.model_name,
                "original_filename": filename,
                "file_size": file_size,
                "duration_sec": duration_sec,
                "status": STATUS_FAILED,
                "error": error,
                "started_at": now,
                "finished_at": now,
            }
        )
        await crud.db.commit()

    @staticmethod
    async def _finish_failed(db: AsyncSession, job: VoiceWorkbenchJobModel, error: str, started: datetime) -> None:
        job.status = STATUS_FAILED
        job.error = error
        job.elapsed_ms = _elapsed_ms(started)
        job.finished_at = _now()
        await db.commit()

    # ─────────────────────── 历史（仅本人） ───────────────────────

    async def _context_maps(self) -> tuple[dict[int, str], dict[int, str]]:
        service_rows = await self.db.execute(select(VoiceWorkbenchServiceModel.id, VoiceWorkbenchServiceModel.name))
        model_rows = await self.db.execute(select(VoiceWorkbenchModelModel.id, VoiceWorkbenchModelModel.display_name))
        return {row[0]: row[1] for row in service_rows.all()}, {row[0]: row[1] for row in model_rows.all()}

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: VoiceWorkbenchJobQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[VoiceWorkbenchJobOutSchema]:
        search_dict = search_to_dict(search) or {}
        search_dict["created_id"] = self.auth.user.id
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_dict,
        )
        service_names, model_names = await self._context_maps()
        result.items = [_job_to_out(job, service_name=service_names.get(job.service_id), model_display_name=model_names.get(job.model_pk)) for job in result.items]  # type: ignore[assignment]
        return result

    async def require_job(self, id: int) -> VoiceWorkbenchJobModel:
        job = await self.crud.get(id=id, created_id=self.auth.user.id)
        if job is None:
            raise CustomException(msg="转写任务不存在", code=404)
        return job  # type: ignore[return-value]

    async def detail(self, id: int) -> VoiceWorkbenchJobOutSchema:
        job = await self.require_job(id)
        service_names, model_names = await self._context_maps()
        return _job_to_out(job, service_name=service_names.get(job.service_id), model_display_name=model_names.get(job.model_pk))

    async def delete(self, ids: list[int]) -> None:
        for id_ in ids:
            if await self.crud.get(id=id_, created_id=self.auth.user.id) is None:
                raise CustomException(msg="删除失败，存在无权访问或已删除的转写任务", code=400)
        await self.crud.delete(ids=ids)


async def recover_interrupted_jobs() -> int:
    """启动恢复钩子：把进程重启前遗留的 ``running`` 任务置为 ``failed``。

    执行进程已随重启消失，这些任务不可能再有结果；标注「服务重启导致任务中断」
    让历史里不出现永久悬空的「识别中」。
    """
    async with async_db_session() as db, db.begin():
        result = await db.execute(select(VoiceWorkbenchJobModel).where(VoiceWorkbenchJobModel.status == STATUS_RUNNING))
        jobs = result.scalars().all()
        now = _now()
        for job in jobs:
            job.status = STATUS_FAILED
            job.error = "服务重启导致任务中断"
            job.finished_at = now
        return len(jobs)
