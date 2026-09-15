"""渠道评估 - 对比报告路由（spec: channel-eval/compare-report，设计 D6）"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    ChannelEvalReportCreateSchema,
    ChannelEvalReportOutSchema,
    ChannelEvalReportQueryParam,
    ChannelEvalReportUpdateSchema,
    ChannelEvalReportViewSchema,
)
from .service import ChannelEvalReportService

ReportRouter = APIRouter(route_class=OperationLogRoute, prefix="/reports", tags=["评估中心-对比报告"])


@ReportRouter.get("/list", summary="查询报告列表", response_model=ResponseSchema[PageResultSchema[ChannelEvalReportOutSchema]])
async def get_report_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ChannelEvalReportQueryParam, Query()],
) -> JSONResponse:
    result = await ChannelEvalReportService(auth, db).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询报告列表成功")


@ReportRouter.get("/detail/{id}", summary="获取报告详情", response_model=ResponseSchema[ChannelEvalReportOutSchema])
async def get_report_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="报告ID", ge=1)],
) -> JSONResponse:
    result = await ChannelEvalReportService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取报告详情成功")


@ReportRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建对比报告", response_model=ResponseSchema[ChannelEvalReportOutSchema])
async def create_report_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:add"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[ChannelEvalReportCreateSchema, Body(description="报告创建参数")],
) -> JSONResponse:
    result = await ChannelEvalReportService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建对比报告成功")


@ReportRouter.put("/update/{id}", summary="修改对比报告", response_model=ResponseSchema[ChannelEvalReportOutSchema])
async def update_report_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:edit"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="报告ID", ge=1)],
    data: Annotated[ChannelEvalReportUpdateSchema, Body(description="报告修改参数")],
) -> JSONResponse:
    result = await ChannelEvalReportService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改对比报告成功")


@ReportRouter.delete("/delete", summary="删除对比报告", response_model=ResponseSchema[None])
async def delete_report_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="报告ID列表")],
) -> JSONResponse:
    await ChannelEvalReportService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除对比报告成功")


@ReportRouter.get("/{id}/view", summary="获取报告视图聚合", response_model=ResponseSchema[ChannelEvalReportViewSchema])
async def get_report_view_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="报告ID", ge=1)],
) -> JSONResponse:
    result = await ChannelEvalReportService(auth, db).view(id=id)
    return SuccessResponse(data=result, msg="获取报告视图成功")


@ReportRouter.get("/{id}/export", summary="导出自包含 HTML 报告", response_class=Response)
async def export_report_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["channel_eval:report:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="报告ID", ge=1)],
) -> Response:
    html_content = await ChannelEvalReportService(auth, db).export_html(id=id)
    filename = f"channel-eval-report-{id}.html"
    return Response(
        content=html_content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
