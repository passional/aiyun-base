"""渠道评估 - 测试运行数据层"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.modules.channel_eval.run.model import (
    ChannelEvalRunComboModel,
    ChannelEvalRunModel,
    ChannelEvalSampleModel,
)
from app.modules.channel_eval.run.schema import ChannelEvalRunCreateSchema


class ChannelEvalRunCRUD(CRUDBase[ChannelEvalRunModel, ChannelEvalRunCreateSchema, ChannelEvalRunCreateSchema]):
    """测试运行数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ChannelEvalRunModel, auth=auth, db=db)

    async def has_active_run(self, platform_id: int, exclude_id: int | None = None) -> bool:
        """同平台串行约束：存在 pending/running/cancelling 运行则拒绝新建。"""
        conditions = [
            ChannelEvalRunModel.platform_id == platform_id,
            ChannelEvalRunModel.status.in_(["pending", "running", "cancelling"]),
        ]
        if exclude_id is not None:
            conditions.append(ChannelEvalRunModel.id != exclude_id)
        result = await self.db.execute(select(func.count()).select_from(ChannelEvalRunModel).where(*conditions))
        return (result.scalar() or 0) > 0


class ChannelEvalRunComboCRUD(CRUDBase[ChannelEvalRunComboModel, ChannelEvalRunCreateSchema, ChannelEvalRunCreateSchema]):
    """运行组合数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ChannelEvalRunComboModel, auth=auth, db=db)


class ChannelEvalSampleCRUD(CRUDBase[ChannelEvalSampleModel, ChannelEvalRunCreateSchema, ChannelEvalRunCreateSchema]):
    """样本数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ChannelEvalSampleModel, auth=auth, db=db)
