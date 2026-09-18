"""绘图工作台 - 绘图任务路由（设计 D9：/jobs 组，image_workbench:job:* 权限码）

SSE 事件流（``GET /jobs/{id}/events``）按设计 D9 只要求**登录态**：浏览器的 SSE
不能自定义请求头，前端经 ``utils/sse`` 以 fetch + ``Authorization`` 建流，因此这里
用 ``get_current_user`` 而非某个具体权限码。可见性由「进度只按任务所有者定向推送 +
快照携 owner_id 校验」保证，越权者拿到一条静默的空流。
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Path, Query, Request, Security, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse, UploadFileResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter, get_current_user, redis_getter
from app.core.logger import logger
from app.core.router_class import OperationLogRoute
from app.core.sse_manager import SSE_QUEUE_MAX_SIZE
from app.modules.common.file.service import FileService
from app.utils.upload_util import UploadUtil

from .image_validation import detect_alpha, validate_reference_image_bytes
from .progress import read_progress
from .schema import (
    ImageWorkbenchJobCreateSchema,
    ImageWorkbenchJobModelOptionSchema,
    ImageWorkbenchJobOutSchema,
    ImageWorkbenchJobQueryParam,
    ImageWorkbenchReferenceUploadSchema,
)
from .service import ImageWorkbenchJobService
from .sse_manager import image_workbench_job_manager

ImageWorkbenchJobRouter = APIRouter(route_class=OperationLogRoute, prefix="/jobs", tags=["绘图工作台-绘图任务"])

_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@ImageWorkbenchJobRouter.post(
    "/reference-images/upload",
    summary="上传绘图参考图",
    response_model=ResponseSchema[ImageWorkbenchReferenceUploadSchema],
)
async def upload_reference_image_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:create"]))],
    request: Request,
    file: Annotated[UploadFile, File(description="绘图参考图")],
) -> JSONResponse:
    """模块专属上传入口：校验真实图片内容，不扩大公共文件模块权限。

    响应里的 ``has_alpha`` 由**头部解析**在此时判出（design D9），前端据此当场决定
    「图片透明通道」等依赖本次输入的参数是否可用。
    """
    UploadUtil.check_file_size(file)
    content = await file.read()
    image_format = validate_reference_image_bytes(content, file.filename)
    has_alpha = detect_alpha(content, image_format)
    await file.seek(0)
    result = await FileService.upload_service(
        base_url=str(request.base_url),
        file=file,
        upload_type="file",
    )
    return SuccessResponse(
        data=ImageWorkbenchReferenceUploadSchema(**result.model_dump(), has_alpha=has_alpha),
        msg="上传参考图成功",
    )


@ImageWorkbenchJobRouter.get("/models/optionselect", summary="工作台可选绘图模型（含能力声明）", response_model=ResponseSchema[list[ImageWorkbenchJobModelOptionSchema]])
async def get_job_model_optionselect_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await ImageWorkbenchJobService(auth, db).list_available_models()
    return SuccessResponse(data=result, msg="查询可选绘图模型成功")


@ImageWorkbenchJobRouter.get("/list", summary="查询绘图任务列表", response_model=ResponseSchema[PageResultSchema[ImageWorkbenchJobOutSchema]])
async def get_job_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ImageWorkbenchJobQueryParam, Query()],
) -> JSONResponse:
    result = await ImageWorkbenchJobService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询绘图任务列表成功")


@ImageWorkbenchJobRouter.get("/detail/{id}", summary="获取绘图任务详情", response_model=ResponseSchema[ImageWorkbenchJobOutSchema])
async def get_job_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图任务ID", ge=1)],
) -> JSONResponse:
    result = await ImageWorkbenchJobService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取绘图任务详情成功")


@ImageWorkbenchJobRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="提交绘图任务", response_model=ResponseSchema[ImageWorkbenchJobOutSchema])
async def create_job_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ImageWorkbenchJobCreateSchema, Body(description="绘图任务提交参数(参数按清单校验，未提交的由服务端补默认值)")],
) -> JSONResponse:
    result = await ImageWorkbenchJobService(auth, db).submit(data=data)
    return SuccessResponse(data=result, msg="已提交绘图任务，正在异步生成")


@ImageWorkbenchJobRouter.post("/{id}/cancel", summary="取消进行中的绘图任务", response_model=ResponseSchema[None])
async def cancel_job_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图任务ID", ge=1)],
) -> JSONResponse:
    await ImageWorkbenchJobService(auth, db).cancel(id=id)
    # 取消语义必须如实告知：我们并不掌握上游的计费时点
    return SuccessResponse(msg="任务已取消（放弃等待本次结果，不保证不产生上游费用）")


@ImageWorkbenchJobRouter.post("/{id}/retry", status_code=status.HTTP_201_CREATED, summary="以原参数重试", response_model=ResponseSchema[ImageWorkbenchJobOutSchema])
async def retry_job_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图任务ID", ge=1)],
) -> JSONResponse:
    result = await ImageWorkbenchJobService(auth, db).retry(id=id)
    return SuccessResponse(data=result, msg="已以原参数重新提交绘图任务")


@ImageWorkbenchJobRouter.delete("/delete", summary="删除绘图任务（软删记录 + 真删产物）", response_model=ResponseSchema[None])
async def delete_job_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="绘图任务ID列表")],
) -> JSONResponse:
    await ImageWorkbenchJobService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除绘图任务成功")


@ImageWorkbenchJobRouter.get("/{id}/artifacts/{artifact_id}/download", summary="下载单张产物")
async def download_artifact_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:job:download"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    background_tasks: BackgroundTasks,
    id: Annotated[int, Path(description="绘图任务ID", ge=1)],
    artifact_id: Annotated[int, Path(description="产物ID", ge=1)],
) -> UploadFileResponse:
    """**按产物**逐张下载（设计 D11）。

    任务级下载端点已移除：一次任务可以产出底图 + 多个图层，「任务 → 一个文件」的
    假设不再成立，整包下载也无从表达要哪一张。
    """
    file_path, file_name, is_temp = await ImageWorkbenchJobService(auth, db).download(id=id, artifact_id=artifact_id)
    if is_temp:
        # 非本地存储节点：产物先落到临时文件，响应结束后清理
        background_tasks.add_task(_remove_temp_file, file_path)
    suffix = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return UploadFileResponse(
        file_path=file_path,
        filename=file_name,
        media_type=_IMAGE_MEDIA_TYPES.get(suffix, "application/octet-stream"),
    )


def _remove_temp_file(file_path: str) -> None:
    import os

    try:
        os.unlink(file_path)
    except OSError as e:  # noqa: BLE001 — 临时文件清理失败不影响已完成的下载
        logger.warning("绘图工作台：下载临时文件清理失败 {}: {}", file_path, e)


@ImageWorkbenchJobRouter.get("/{id}/events", summary="任务进度实时流(SSE)", response_class=EventSourceResponse)
async def job_events_controller(
    auth: Annotated[AuthSchema, Security(get_current_user)],
    request: Request,
    redis: Annotated[Redis, Depends(redis_getter)],
    id: Annotated[int, Path(description="绘图任务ID", ge=1)],
) -> AsyncGenerator[ServerSentEvent, None]:
    """任务进度实时通道（SSE）：连接时先回放 Redis 进度（断线重连以此为准），
    再实时消费引擎的定向推送。空闲保活由框架内置 ping 处理。

    断线重连后**不会**停留在「进行中」——回放的就是库内已收敛的最新状态。
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAX_SIZE)
    image_workbench_job_manager.connect(auth.user.id, queue, redis=getattr(request.app.state, "redis", None))
    try:
        snapshot = await read_progress(redis, id)
        if snapshot is not None and not _snapshot_visible(snapshot, auth):
            # 越权：不回放任何内容，也不推送（进度本就只定向推给所有者）
            snapshot = None
        if snapshot is not None:
            yield ServerSentEvent(event="job_progress", data=json.dumps(snapshot, ensure_ascii=False))
        while True:
            message = await queue.get()
            data = message.get("data", message)
            if isinstance(data, dict) and data.get("job_id") not in (None, id):
                continue  # 其它任务的进度事件，不发给本连接
            payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            yield ServerSentEvent(event=str(message.get("type", "job_progress")), data=payload)
    finally:
        image_workbench_job_manager.disconnect(auth.user.id, queue)
        logger.info("绘图任务进度 SSE 已断开: job={} user={}", id, auth.user.id)


def _snapshot_visible(snapshot: dict, auth: AuthSchema) -> bool:
    """进度快照是否对调用者可见（超管或任务所有者）。"""
    if auth.user.is_superuser:
        return True
    owner_id = snapshot.get("owner_id")
    return owner_id is None or owner_id == auth.user.id
