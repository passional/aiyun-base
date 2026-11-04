"""语音工作台 - 配置数据层（语音服务 / 语音识别模型）"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import VoiceWorkbenchModelModel, VoiceWorkbenchServiceModel
from .schema import (
    VoiceWorkbenchModelCreateSchema,
    VoiceWorkbenchModelUpdateSchema,
    VoiceWorkbenchServiceCreateSchema,
    VoiceWorkbenchServiceUpdateSchema,
)


class VoiceWorkbenchServiceCRUD(CRUDBase[VoiceWorkbenchServiceModel, VoiceWorkbenchServiceCreateSchema, VoiceWorkbenchServiceUpdateSchema]):
    """语音服务数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=VoiceWorkbenchServiceModel, auth=auth, db=db)


class VoiceWorkbenchModelCRUD(CRUDBase[VoiceWorkbenchModelModel, VoiceWorkbenchModelCreateSchema, VoiceWorkbenchModelUpdateSchema]):
    """语音识别模型数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=VoiceWorkbenchModelModel, auth=auth, db=db)
