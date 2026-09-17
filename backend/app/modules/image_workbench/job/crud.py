"""绘图工作台 - 绘图任务数据层

数据范围过滤由 ``CRUDBase`` 统一经 ``Permission`` 施加（模型继承 ``UserMixin``，
按 ``created_id`` 过滤），因此列表、详情、下载拿到的一定是调用者可见范围内的任务
（spec: access / 绘图任务的可见范围「任何用户 MUST NOT 通过直接构造请求标识读取范围
以外的绘图任务」）。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import ImageWorkbenchJobModel
from .schema import ACTIVE_STATUSES, ImageWorkbenchJobCreateSchema


class ImageWorkbenchJobCRUD(CRUDBase[ImageWorkbenchJobModel, ImageWorkbenchJobCreateSchema, ImageWorkbenchJobCreateSchema]):
    """绘图任务数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ImageWorkbenchJobModel, auth=auth, db=db)

    async def count_active_by_user(self, user_id: int) -> int:
        """统计某用户进行中（``pending``/``running``）的任务数——并发闸门用。

        显式按 ``user_id`` 过滤并绕开数据范围：闸门约束的是**提交者本人的额度**
        （spec: generation / 并发闸门「MUST 按用户独立计算」），不能因调用者是
        管理员（数据范围更宽）就把别人的任务算进他的额度。
        """
        stmt = (
            select(func.count(ImageWorkbenchJobModel.id))
            .where(ImageWorkbenchJobModel.created_id == user_id)
            .where(ImageWorkbenchJobModel.status.in_(sorted(ACTIVE_STATUSES)))
            .where(ImageWorkbenchJobModel.is_deleted == False)  # noqa: E712 — 软删任务不占额度
        )
        return (await self.db.execute(stmt)).scalar() or 0
