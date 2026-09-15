"""渠道评估 - 平台管理路由（spec: channel-eval/platform-access）"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    ChannelEvalChannelCatalogSchema,
    ChannelEvalPlatformCreateSchema,
    ChannelEvalPlatformOutSchema,
    ChannelEvalPlatformQueryParam,
    ChannelEvalPlatformUpdateSchema,
    ChannelEvalTestConnectionResultSchema,
    ChannelEvalTestConnectionSchema,
)
from .service import ChannelEvalPlatformService

PlatformRouter = APIRouter(route_class=OperationLogRoute, prefix="/platforms", tags=["评估中心-平台管理"])


@PlatformRouter.get("/list", summary="查询平台列表", response_model=ResponseSchema[PageResultSchema[ChannelEvalPlatformOutSchema]])
async def get_platform_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ChannelEvalPlatformQueryParam, Query()],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询平台列表成功")


@PlatformRouter.get("/optionselect", summary="获取全部平台（下拉）", response_model=ResponseSchema[list[ChannelEvalPlatformOutSchema]])
async def get_platform_optionselect_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).get_list()
    return SuccessResponse(data=result, msg="查询平台列表成功")


@PlatformRouter.get("/detail/{id}", summary="获取平台详情", response_model=ResponseSchema[ChannelEvalPlatformOutSchema])
async def get_platform_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="平台ID", ge=1)],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取平台详情成功")


@PlatformRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建平台", response_model=ResponseSchema[ChannelEvalPlatformOutSchema])
async def create_platform_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ChannelEvalPlatformCreateSchema, Body(description="平台创建参数")],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建平台成功")


@PlatformRouter.put("/update/{id}", summary="修改平台", response_model=ResponseSchema[ChannelEvalPlatformOutSchema])
async def update_platform_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="平台ID", ge=1)],
    data: Annotated[ChannelEvalPlatformUpdateSchema, Body(description="平台修改参数")],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改平台成功")


@PlatformRouter.delete("/delete", summary="删除平台", response_model=ResponseSchema[None])
async def delete_platform_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="平台ID列表")],
) -> JSONResponse:
    await ChannelEvalPlatformService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除平台成功")


@PlatformRouter.post("/test-connection", summary="测试连接（用所填凭证，不依赖已保存平台）", response_model=ResponseSchema[ChannelEvalTestConnectionResultSchema])
async def test_connection_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ChannelEvalTestConnectionSchema, Body(description="测试连接参数")],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).test_connection(data=data)
    return SuccessResponse(data=result, msg="测试连接完成")


@PlatformRouter.post("/{id}/test-connection", summary="测试已保存平台的连接", response_model=ResponseSchema[ChannelEvalTestConnectionResultSchema])
async def test_platform_connection_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="平台ID", ge=1)],
    data: Annotated[ChannelEvalTestConnectionSchema | None, Body(description="测试连接参数（凭证留空回退库内凭证）")] = None,
) -> JSONResponse:
    payload = data or ChannelEvalTestConnectionSchema()
    payload.platform_id = id
    result = await ChannelEvalPlatformService(auth, db).test_connection(data=payload)
    return SuccessResponse(data=result, msg="测试连接完成")


@PlatformRouter.get("/{id}/channels", summary="实时拉取平台渠道目录", response_model=ResponseSchema[ChannelEvalChannelCatalogSchema])
async def get_platform_channels_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:platform:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="平台ID", ge=1)],
) -> JSONResponse:
    result = await ChannelEvalPlatformService(auth, db).get_channel_catalog(id=id)
    return SuccessResponse(data=result, msg="拉取渠道目录成功")
