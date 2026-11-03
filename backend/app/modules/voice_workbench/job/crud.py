"""语音工作台 - 转写任务数据层。

``CRUDBase`` 的数据范围默认对超管放行，而转写任务要求「所有用户（含超管）只看本人」。
因此服务层每次查询都显式带上 ``created_id``，不依赖数据范围机制。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import VoiceWorkbenchJobModel
from .schema import VoiceWorkbenchJobQueryParam


class VoiceWorkbenchJobCRUD(CRUDBase[VoiceWorkbenchJobModel, VoiceWorkbenchJobQueryParam, VoiceWorkbenchJobQueryParam]):
    """转写任务数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=VoiceWorkbenchJobModel, auth=auth, db=db)
