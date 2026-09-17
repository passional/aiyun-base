"""绘图工作台 - 配置路由（设计 D9：/services 与 /models 两组，同属 image_workbench:service:* 权限码）

绘图服务与绘图模型是主从两级结构，永远一起被编辑，因此同放一个功能目录、共用一组权限码；
但对外仍是两组资源路径，故在此定义两个 APIRouter（routers.py 分别挂载）。
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
    ImageWorkbenchModelCreateSchema,
    ImageWorkbenchModelOutSchema,
    ImageWorkbenchModelQueryParam,
    ImageWorkbenchModelUpdateSchema,
    ImageWorkbenchServiceCreateSchema,
    ImageWorkbenchServiceOutSchema,
    ImageWorkbenchServiceQueryParam,
    ImageWorkbenchServiceUpdateSchema,
    ImageWorkbenchTestConnectionResultSchema,
    ImageWorkbenchTestConnectionSchema,
)
from .service import ImageWorkbenchModelService, ImageWorkbenchServiceService

ImageWorkbenchServiceRouter = APIRouter(route_class=OperationLogRoute, prefix="/services", tags=["绘图工作台-绘图服务"])
ImageWorkbenchModelRouter = APIRouter(route_class=OperationLogRoute, prefix="/models", tags=["绘图工作台-绘图模型"])


@ImageWorkbenchServiceRouter.get("/list", summary="查询绘图服务列表", response_model=ResponseSchema[PageResultSchema[ImageWorkbenchServiceOutSchema]])
async def get_service_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ImageWorkbenchServiceQueryParam, Query()],
) -> JSONResponse:
    result = await ImageWorkbenchServiceService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询绘图服务列表成功")


@ImageWorkbenchServiceRouter.get("/optionselect", summary="获取全部绘图服务（下拉）", response_model=ResponseSchema[list[ImageWorkbenchServiceOutSchema]])
async def get_service_optionselect_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await ImageWorkbenchServiceService(auth, db).get_list()
    return SuccessResponse(data=result, msg="查询绘图服务列表成功")


@ImageWorkbenchServiceRouter.get("/detail/{id}", summary="获取绘图服务详情", response_model=ResponseSchema[ImageWorkbenchServiceOutSchema])
async def get_service_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图服务ID", ge=1)],
) -> JSONResponse:
    result = await ImageWorkbenchServiceService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取绘图服务详情成功")


@ImageWorkbenchServiceRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建绘图服务", response_model=ResponseSchema[ImageWorkbenchServiceOutSchema])
async def create_service_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ImageWorkbenchServiceCreateSchema, Body(description="绘图服务创建参数")],
) -> JSONResponse:
    result = await ImageWorkbenchServiceService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建绘图服务成功")


@ImageWorkbenchServiceRouter.put("/update/{id}", summary="修改绘图服务", response_model=ResponseSchema[ImageWorkbenchServiceOutSchema])
async def update_service_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图服务ID", ge=1)],
    data: Annotated[ImageWorkbenchServiceUpdateSchema, Body(description="绘图服务修改参数")],
) -> JSONResponse:
    result = await ImageWorkbenchServiceService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改绘图服务成功")


@ImageWorkbenchServiceRouter.delete("/delete", summary="删除绘图服务", response_model=ResponseSchema[None])
async def delete_service_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="绘图服务ID列表")],
) -> JSONResponse:
    await ImageWorkbenchServiceService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除绘图服务成功")


@ImageWorkbenchServiceRouter.post(
    "/test-connection",
    summary="测试连接（用所填值，不依赖已保存的绘图服务）",
    response_model=ResponseSchema[ImageWorkbenchTestConnectionResultSchema],
)
async def test_connection_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ImageWorkbenchTestConnectionSchema, Body(description="连通性测试参数")],
) -> JSONResponse:
    result = await ImageWorkbenchServiceService(auth, db).test_connection(data=data)
    return SuccessResponse(data=result, msg="连通性测试完成")


@ImageWorkbenchServiceRouter.post(
    "/{id}/test-connection",
    summary="测试已保存绘图服务的连接",
    response_model=ResponseSchema[ImageWorkbenchTestConnectionResultSchema],
)
async def test_saved_service_connection_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图服务ID", ge=1)],
    data: Annotated[ImageWorkbenchTestConnectionSchema | None, Body(description="连通性测试参数（留空回退库内值）")] = None,
) -> JSONResponse:
    payload = data or ImageWorkbenchTestConnectionSchema()
    payload.service_id = id
    result = await ImageWorkbenchServiceService(auth, db).test_connection(data=payload)
    return SuccessResponse(data=result, msg="连通性测试完成")


@ImageWorkbenchModelRouter.get("/list", summary="查询绘图模型列表", response_model=ResponseSchema[PageResultSchema[ImageWorkbenchModelOutSchema]])
async def get_model_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ImageWorkbenchModelQueryParam, Query()],
) -> JSONResponse:
    result = await ImageWorkbenchModelService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询绘图模型列表成功")


@ImageWorkbenchModelRouter.get("/detail/{id}", summary="获取绘图模型详情", response_model=ResponseSchema[ImageWorkbenchModelOutSchema])
async def get_model_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图模型ID", ge=1)],
) -> JSONResponse:
    result = await ImageWorkbenchModelService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取绘图模型详情成功")


@ImageWorkbenchModelRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建绘图模型", response_model=ResponseSchema[ImageWorkbenchModelOutSchema])
async def create_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ImageWorkbenchModelCreateSchema, Body(description="绘图模型创建参数")],
) -> JSONResponse:
    result = await ImageWorkbenchModelService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建绘图模型成功")


@ImageWorkbenchModelRouter.put("/update/{id}", summary="修改绘图模型", response_model=ResponseSchema[ImageWorkbenchModelOutSchema])
async def update_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="绘图模型ID", ge=1)],
    data: Annotated[ImageWorkbenchModelUpdateSchema, Body(description="绘图模型修改参数")],
) -> JSONResponse:
    result = await ImageWorkbenchModelService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改绘图模型成功")


@ImageWorkbenchModelRouter.delete("/delete", summary="删除绘图模型", response_model=ResponseSchema[None])
async def delete_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["image_workbench:service:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="绘图模型ID列表")],
) -> JSONResponse:
    await ImageWorkbenchModelService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除绘图模型成功")
