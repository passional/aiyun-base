"""渠道评估 - 测试运行路由（spec: channel-eval/test-run，设计 D6）"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter, redis_getter
from app.core.logger import logger
from app.core.router_class import OperationLogRoute
from app.core.sse_manager import SSE_QUEUE_MAX_SIZE

from .model import ChannelEvalRunModel
from .progress import read_progress
from .schema import (
    ChannelEvalRunCreateSchema,
    ChannelEvalRunDetailSchema,
    ChannelEvalRunOutSchema,
    ChannelEvalRunQueryParam,
)
from .service import ChannelEvalRunService
from .sse_manager import channel_eval_run_manager

RunRouter = APIRouter(route_class=OperationLogRoute, prefix="/runs", tags=["评估中心-测试运行"])

_TERMINAL_STATUSES = {"success", "failed", "cancelled"}


@RunRouter.get("/list", summary="查询运行列表", response_model=ResponseSchema[PageResultSchema[ChannelEvalRunOutSchema]])
async def get_run_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:run:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ChannelEvalRunQueryParam, Query()],
) -> JSONResponse:
    result = await ChannelEvalRunService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询运行列表成功")


@RunRouter.get("/detail/{id}", summary="获取运行详情", response_model=ResponseSchema[ChannelEvalRunDetailSchema])
async def get_run_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:run:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="运行ID", ge=1)],
) -> JSONResponse:
    result = await ChannelEvalRunService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取运行详情成功")


@RunRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建测试运行", response_model=ResponseSchema[ChannelEvalRunOutSchema])
async def create_run_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:run:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ChannelEvalRunCreateSchema, Body(description="运行创建参数")],
) -> JSONResponse:
    result = await ChannelEvalRunService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建测试运行成功，已开始异步执行")


@RunRouter.post("/{id}/cancel", summary="取消执行中的运行", response_model=ResponseSchema[None])
async def cancel_run_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:run:cancel"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="运行ID", ge=1)],
) -> JSONResponse:
    await ChannelEvalRunService(auth, db).cancel(id=id)
    return SuccessResponse(msg="已请求取消，运行将在当前请求完成后停止")


@RunRouter.get("/{id}/events", summary="运行进度实时流(SSE)", response_class=EventSourceResponse)
async def run_events_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:run:query"]))],
    request: Request,
    redis: Annotated[Redis, Depends(redis_getter)],
    id: Annotated[int, Path(description="运行ID", ge=1)],
) -> AsyncGenerator[ServerSentEvent, None]:
    """运行进度实时通道（SSE）：连接时先回放 Redis 进度（断线重连以此为准），
    再实时消费引擎广播；运行已结束时回放终态快照。空闲保活由框架内置 ping 处理。
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAX_SIZE)
    channel_eval_run_manager.connect(auth.user.id, queue, redis=getattr(request.app.state, "redis", None))
    try:
        # 初始快照：Redis 进度优先（D4：断线重连以 Redis 进度为准），无则回放数据库终态
        snapshot = await read_progress(redis, id)
        if snapshot is None:
            from app.core.database import async_db_session

            async with async_db_session() as session:
                run = await session.get(ChannelEvalRunModel, id)
                if run is not None:
                    snapshot = {"run_id": id, "status": run.status}
        if snapshot is not None:
            yield ServerSentEvent(event="run_progress", data=json.dumps(snapshot, ensure_ascii=False))
        while True:
            message = await queue.get()
            data = message.get("data", message)
            if isinstance(data, dict) and data.get("run_id") not in (None, id):
                continue  # 其它运行的进度事件，不发给本连接
            payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            yield ServerSentEvent(event=str(message.get("type", "run_progress")), data=payload)
    finally:
        channel_eval_run_manager.disconnect(auth.user.id, queue)
        logger.info("运行进度 SSE 已断开: run={} user={}", id, auth.user.id)
