"""绘图工作台 - 配置数据层（绘图服务 / 绘图模型）"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import ImageWorkbenchModelModel, ImageWorkbenchServiceModel
from .schema import (
    ImageWorkbenchModelCreateSchema,
    ImageWorkbenchModelUpdateSchema,
    ImageWorkbenchServiceCreateSchema,
    ImageWorkbenchServiceUpdateSchema,
)


class ImageWorkbenchServiceCRUD(CRUDBase[ImageWorkbenchServiceModel, ImageWorkbenchServiceCreateSchema, ImageWorkbenchServiceUpdateSchema]):
    """绘图服务数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ImageWorkbenchServiceModel, auth=auth, db=db)


class ImageWorkbenchModelCRUD(CRUDBase[ImageWorkbenchModelModel, ImageWorkbenchModelCreateSchema, ImageWorkbenchModelUpdateSchema]):
    """绘图模型数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ImageWorkbenchModelModel, auth=auth, db=db)
