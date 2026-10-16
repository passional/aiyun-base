"""渠道评估 - 模型映射路由（spec: channel-eval/model-mapping）"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    CanonicalModelCreateSchema,
    CanonicalModelOutSchema,
    CanonicalModelQueryParam,
    CanonicalModelUpdateSchema,
    ConfirmSuggestionSchema,
    SuggestionGroupSchema,
    UnmappedModelSchema,
)
from .service import ChannelEvalModelMapService

ModelMapRouter = APIRouter(route_class=OperationLogRoute, prefix="/model-maps", tags=["评估中心-模型对齐"])


@ModelMapRouter.get("/list", summary="查询规范模型列表", response_model=ResponseSchema[PageResultSchema[CanonicalModelOutSchema]])
async def get_canonical_model_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[CanonicalModelQueryParam, Query()],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询规范模型列表成功")


@ModelMapRouter.get("/detail/{id}", summary="获取规范模型详情", response_model=ResponseSchema[CanonicalModelOutSchema])
async def get_canonical_model_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="规范模型ID", ge=1)],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取规范模型详情成功")


@ModelMapRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="新增规范模型", response_model=ResponseSchema[CanonicalModelOutSchema])
async def create_canonical_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[CanonicalModelCreateSchema, Body(description="规范模型创建参数")],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="新增规范模型成功")


@ModelMapRouter.put("/update/{id}", summary="修改规范模型", response_model=ResponseSchema[CanonicalModelOutSchema])
async def update_canonical_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="规范模型ID", ge=1)],
    data: Annotated[CanonicalModelUpdateSchema, Body(description="规范模型修改参数")],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改规范模型成功")


@ModelMapRouter.delete("/delete", summary="删除规范模型", response_model=ResponseSchema[None])
async def delete_canonical_model_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="规范模型ID列表")],
) -> JSONResponse:
    await ChannelEvalModelMapService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除规范模型成功")


@ModelMapRouter.get("/suggestions", summary="查询疑似同族建议", response_model=ResponseSchema[list[SuggestionGroupSchema]])
async def get_model_suggestions_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).suggestions()
    return SuccessResponse(data=result, msg="查询疑似同族建议成功")


@ModelMapRouter.get("/unmapped", summary="查询未确认归属的渠道模型串", response_model=ResponseSchema[UnmappedModelSchema])
async def get_unmapped_models_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).unmapped()
    return SuccessResponse(data=result, msg="查询未确认模型串成功")


@ModelMapRouter.post("/confirm", status_code=status.HTTP_201_CREATED, summary="确认一条建议", response_model=ResponseSchema[CanonicalModelOutSchema])
async def confirm_suggestion_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:model_map:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ConfirmSuggestionSchema, Body(description="确认建议参数")],
) -> JSONResponse:
    result = await ChannelEvalModelMapService(auth, db).confirm(data=data)
    return SuccessResponse(data=result, msg="确认建议成功")
