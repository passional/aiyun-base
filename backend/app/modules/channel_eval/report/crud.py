"""渠道评估 - 对比报告数据层"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.modules.channel_eval.report.model import ChannelEvalReportModel
from app.modules.channel_eval.report.schema import (
    ChannelEvalReportCreateSchema,
    ChannelEvalReportUpdateSchema,
)


class ChannelEvalReportCRUD(CRUDBase[ChannelEvalReportModel, ChannelEvalReportCreateSchema, ChannelEvalReportUpdateSchema]):
    """对比报告数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ChannelEvalReportModel, auth=auth, db=db)
