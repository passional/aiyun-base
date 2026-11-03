"""语音工作台 - 配置路由（/services 与 /models 两组，同属 voice_workbench:service:* 权限码）

语音服务与语音识别模型是主从两级结构，共用一组权限码；对外仍是两组资源路径，
因此定义两个 APIRouter，由 routers.py 分别挂载。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    VoiceWorkbenchModelCreateSchema,
    VoiceWorkbenchModelOutSchema,
    VoiceWorkbenchModelQueryParam,
    VoiceWorkbenchModelUpdateSchema,
    VoiceWorkbenchServiceCreateSchema,
    VoiceWorkbenchServiceOutSchema,
    VoiceWorkbenchServiceQueryParam,
    VoiceWorkbenchServiceUpdateSchema,
)
from .service import VoiceWorkbenchModelConfigService, VoiceWorkbenchServiceService

VoiceWorkbenchServiceRouter = APIRouter(route_class=OperationLogRoute, prefix="/services", tags=["语音工作台-语音服务"])
VoiceWorkbenchModelRouter = APIRouter(route_class=OperationLogRoute, prefix="/models", tags=["语音工作台-语音识别模型"])


@VoiceWorkbenchServiceRouter.get("/list", summary="查询语音服务列表", response_model=ResponseSchema[PageResultSchema[VoiceWorkbenchServiceOutSchema]])
async def get_service_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[VoiceWorkbenchServiceQueryParam, Query()],
) -> JSONResponse:
    result = await VoiceWorkbenchServiceService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询语音服务列表成功")


@VoiceWorkbenchServiceRouter.get("/optionselect", summary="获取全部语音服务（下拉）", response_model=ResponseSchema[list[VoiceWorkbenchServiceOutSchema]])
async def get_service_optionselect_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await VoiceWorkbenchServiceService(auth, db).get_list()
    return SuccessResponse(data=result, msg="查询语音服务列表成功")


@VoiceWorkbenchServiceRouter.get("/detail/{id}", summary="获取语音服务详情", response_model=ResponseSchema[VoiceWorkbenchServiceOutSchema])
async def get_service_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="语音服务ID", ge=1)],
) -> JSONResponse:
    result = await VoiceWorkbenchServiceService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取语音服务详情成功")


@VoiceWorkbenchServiceRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建语音服务", response_model=ResponseSchema[VoiceWorkbenchServiceOutSchema])
async def create_service_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[VoiceWorkbenchServiceCreateSchema, Body(description="语音服务创建参数")],
) -> JSONResponse:
    result = await VoiceWorkbenchServiceService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建语音服务成功")


@VoiceWorkbenchServiceRouter.put("/update/{id}", summary="修改语音服务", response_model=ResponseSchema[VoiceWorkbenchServiceOutSchema])
async def update_service_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="语音服务ID", ge=1)],
    data: Annotated[VoiceWorkbenchServiceUpdateSchema, Body(description="语音服务修改参数")],
) -> JSONResponse:
    result = await VoiceWorkbenchServiceService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改语音服务成功")


@VoiceWorkbenchServiceRouter.delete("/delete", summary="删除语音服务", response_model=ResponseSchema[None])
async def delete_service_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="语音服务ID列表")],
) -> JSONResponse:
    await VoiceWorkbenchServiceService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除语音服务成功")


@VoiceWorkbenchModelRouter.get("/list", summary="查询语音识别模型列表", response_model=ResponseSchema[PageResultSchema[VoiceWorkbenchModelOutSchema]])
async def get_model_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[VoiceWorkbenchModelQueryParam, Query()],
) -> JSONResponse:
    result = await VoiceWorkbenchModelConfigService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询语音识别模型列表成功")


@VoiceWorkbenchModelRouter.get("/optionselect", summary="获取全部语音识别模型（下拉）", response_model=ResponseSchema[list[VoiceWorkbenchModelOutSchema]])
async def get_model_optionselect_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await VoiceWorkbenchModelConfigService(auth, db).get_list()
    return SuccessResponse(data=result, msg="查询语音识别模型列表成功")


@VoiceWorkbenchModelRouter.get("/detail/{id}", summary="获取语音识别模型详情", response_model=ResponseSchema[VoiceWorkbenchModelOutSchema])
async def get_model_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="语音识别模型ID", ge=1)],
) -> JSONResponse:
    result = await VoiceWorkbenchModelConfigService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取语音识别模型详情成功")


@VoiceWorkbenchModelRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建语音识别模型", response_model=ResponseSchema[VoiceWorkbenchModelOutSchema])
async def create_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[VoiceWorkbenchModelCreateSchema, Body(description="语音识别模型创建参数")],
) -> JSONResponse:
    result = await VoiceWorkbenchModelConfigService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建语音识别模型成功")


@VoiceWorkbenchModelRouter.put("/update/{id}", summary="修改语音识别模型", response_model=ResponseSchema[VoiceWorkbenchModelOutSchema])
async def update_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="语音识别模型ID", ge=1)],
    data: Annotated[VoiceWorkbenchModelUpdateSchema, Body(description="语音识别模型修改参数")],
) -> JSONResponse:
    result = await VoiceWorkbenchModelConfigService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改语音识别模型成功")


@VoiceWorkbenchModelRouter.delete("/delete", summary="删除语音识别模型", response_model=ResponseSchema[None])
async def delete_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["voice_workbench:service:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="语音识别模型ID列表")],
) -> JSONResponse:
    await VoiceWorkbenchModelConfigService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除语音识别模型成功")
