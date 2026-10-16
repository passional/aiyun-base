"""渠道评估 - 模型映射数据层"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.modules.channel_eval.model_map.model import ChannelEvalCanonicalModel
from app.modules.channel_eval.model_map.schema import CanonicalModelCreateSchema, CanonicalModelUpdateSchema


class CanonicalModelCRUD(CRUDBase[ChannelEvalCanonicalModel, CanonicalModelCreateSchema, CanonicalModelUpdateSchema]):
    """规范模型数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ChannelEvalCanonicalModel, auth=auth, db=db)
