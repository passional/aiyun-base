"""语音工作台 - 转写任务路由（/jobs 组，voice_workbench:job:* 权限码）"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Form, Path, Query, Request, Security, UploadFile
from fastapi.responses import JSONResponse
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter, redis_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    VoiceWorkbenchJobModelOptionSchema,
    VoiceWorkbenchJobOutSchema,
    VoiceWorkbenchJobQueryParam,
)
from .service import VoiceWorkbenchJobService

VoiceWorkbenchJobRouter = APIRouter(route_class=OperationLogRoute, prefix="/jobs", tags=["语音工作台-转写任务"])


@VoiceWorkbenchJobRouter.get(
    "/models/optionselect",
    summary="工作台可选语音识别模型",
    response_model=ResponseSchema[list[VoiceWorkbenchJobModelOptionSchema]],
)
async def get_job_model_optionselect_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:job:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await VoiceWorkbenchJobService(auth, db).list_available_models()
    return SuccessResponse(data=result, msg="查询可选语音识别模型成功")


@VoiceWorkbenchJobRouter.post("/transcribe", summary="上传音频并同步转写", response_model=ResponseSchema[VoiceWorkbenchJobOutSchema])
async def transcribe_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:job:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    redis: Annotated[Redis, Depends(redis_getter)],
    file: Annotated[UploadFile, File(description="音频文件(16kHz 单声道 PCM16 WAV)")],
    model_id: Annotated[int, Form(ge=1, description="语音识别模型ID")],
) -> JSONResponse:
    result = await VoiceWorkbenchJobService(auth, db).transcribe(file=file, model_id=model_id, redis=redis)
    return SuccessResponse(data=result, msg="语音识别成功")


@VoiceWorkbenchJobRouter.get("/list", summary="查询转写任务列表", response_model=ResponseSchema[PageResultSchema[VoiceWorkbenchJobOutSchema]])
async def get_job_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:job:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    request: Request,
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[VoiceWorkbenchJobQueryParam, Query()],
) -> JSONResponse:
    # PaginationQueryParam 的缺省是 id asc；契约要求本模块缺省 id desc，
    # 因此只在调用方真的传了 order_by 时才采用它。
    order_by = page.order_by if "order_by" in request.query_params else None
    result = await VoiceWorkbenchJobService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=order_by,
    )
    return SuccessResponse(data=result, msg="查询转写任务列表成功")


@VoiceWorkbenchJobRouter.get("/detail/{id}", summary="获取转写任务详情", response_model=ResponseSchema[VoiceWorkbenchJobOutSchema])
async def get_job_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:job:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="转写任务ID", ge=1)],
) -> JSONResponse:
    result = await VoiceWorkbenchJobService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取转写任务详情成功")


@VoiceWorkbenchJobRouter.delete("/delete", summary="删除转写任务（软删，仅本人）", response_model=ResponseSchema[None])
async def delete_job_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:job:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="转写任务ID列表")],
) -> JSONResponse:
    await VoiceWorkbenchJobService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除转写任务成功")
