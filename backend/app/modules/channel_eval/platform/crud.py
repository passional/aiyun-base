"""渠道评估 - 平台数据层"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.platform.schema import (
    ChannelEvalPlatformCreateSchema,
    ChannelEvalPlatformUpdateSchema,
)


class ChannelEvalPlatformCRUD(CRUDBase[ChannelEvalPlatformModel, ChannelEvalPlatformCreateSchema, ChannelEvalPlatformUpdateSchema]):
    """平台数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ChannelEvalPlatformModel, auth=auth, db=db)
