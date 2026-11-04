"""语音工作台 - 配置业务层（语音服务 / 语音识别模型 CRUD）

凭证处理照绘图工作台与渠道评估的先例：明文经 ``CryptoUtil.encrypt`` 落库、出参只回显
脱敏占位、编辑留空沿用原值。语音服务是独立实体（ADR-0010），不复用「平台」或「绘图服务」。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from .crud import VoiceWorkbenchModelCRUD, VoiceWorkbenchServiceCRUD
from .model import VoiceWorkbenchModelModel, VoiceWorkbenchServiceModel
from .schema import (
    VoiceWorkbenchModelCreateSchema,
    VoiceWorkbenchModelOutSchema,
    VoiceWorkbenchModelQueryParam,
    VoiceWorkbenchModelUpdateSchema,
    VoiceWorkbenchServiceCreateSchema,
    VoiceWorkbenchServiceOutSchema,
    VoiceWorkbenchServiceQueryParam,
    VoiceWorkbenchServiceUpdateSchema,
)


def mask_credential(encrypted: str | None) -> str | None:
    """密文 → 脱敏占位（仅展示尾 4 位明文字符）。"""
    if not encrypted:
        return None
    try:
        from app.utils.crypto_util import CryptoUtil

        plain = CryptoUtil.decrypt(encrypted)
    except Exception:  # noqa: BLE001
        return "••••••••"
    if not plain:
        return None
    if len(plain) <= 4:
        return "••••"
    return f"••••{plain[-4:]}"


def decrypt_credential(encrypted: str | None) -> str:
    """密文 → 明文凭证（仅供发起上游请求的调用层使用，绝不出现在响应中）。"""
    if not encrypted:
        return ""
    from app.utils.crypto_util import CryptoUtil

    return CryptoUtil.decrypt(encrypted)


def _service_to_out(obj: Any) -> VoiceWorkbenchServiceOutSchema:
    out = VoiceWorkbenchServiceOutSchema.model_validate(obj)
    out.api_key_masked = mask_credential(obj.api_key_enc)
    out.has_api_key = bool(obj.api_key_enc)
    return out


def _model_to_out(obj: VoiceWorkbenchModelModel, service_names: dict[int, str] | None = None) -> VoiceWorkbenchModelOutSchema:
    return VoiceWorkbenchModelOutSchema.model_validate(
        {
            "id": obj.id,
            "uuid": obj.uuid,
            "created_time": obj.created_time,
            "updated_time": obj.updated_time,
            "is_deleted": obj.is_deleted,
            "deleted_time": obj.deleted_time,
            "service_id": obj.service_id,
            "service_name": (service_names or {}).get(obj.service_id),
            "display_name": obj.display_name,
            "model_name": obj.model_name,
            "resource_id": obj.resource_id,
            "endpoint_url": obj.endpoint_url,
            "max_file_mb": obj.max_file_mb,
            "max_duration_sec": obj.max_duration_sec,
            "status": obj.status,
            "remark": obj.remark,
        }
    )


class VoiceWorkbenchServiceService:
    """语音服务管理服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = VoiceWorkbenchServiceCRUD(auth, db)

    async def detail(self, id: int) -> VoiceWorkbenchServiceOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="语音服务不存在")
        return _service_to_out(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: VoiceWorkbenchServiceQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[VoiceWorkbenchServiceOutSchema]:
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
        )
        result.items = [_service_to_out(item) for item in result.items]  # type: ignore[assignment]
        return result

    async def get_list(self, search: VoiceWorkbenchServiceQueryParam | None = None, order_by: list[dict] | None = None) -> list[VoiceWorkbenchServiceOutSchema]:
        obj_list = await self.crud.get_list(search=search_to_dict(search), order_by=order_by or [{"id": "asc"}])
        return [_service_to_out(obj) for obj in obj_list]

    async def create(self, data: VoiceWorkbenchServiceCreateSchema) -> VoiceWorkbenchServiceOutSchema:
        from app.core.exceptions import CustomException
        from app.utils.crypto_util import CryptoUtil

        if await self.crud.get(name=data.name):
            raise CustomException(msg=f"创建失败，语音服务名称「{data.name}」已存在", code=400)
        obj = await self.crud.create(
            {
                "name": data.name,
                "api_key_enc": CryptoUtil.encrypt(data.api_key.strip()) if (data.api_key and data.api_key.strip()) else None,
                "status": data.status,
                "remark": data.remark,
            }
        )
        return _service_to_out(obj)

    async def update(self, id: int, data: VoiceWorkbenchServiceUpdateSchema) -> VoiceWorkbenchServiceOutSchema:
        from app.core.exceptions import CustomException
        from app.utils.crypto_util import CryptoUtil

        obj = await self.crud.get_or_404(id=id, msg="语音服务不存在")
        exists = await self.crud.get(name=data.name)
        if exists and exists.id != id:
            raise CustomException(msg=f"更新失败，语音服务名称「{data.name}」已存在", code=400)
        update_dict: dict[str, Any] = {
            "name": data.name,
            "status": data.status,
            "remark": data.remark,
        }
        # 留空/缺省表示沿用原凭证，只有显式填写才重新加密
        if data.api_key and data.api_key.strip():
            update_dict["api_key_enc"] = CryptoUtil.encrypt(data.api_key.strip())
        for key, value in update_dict.items():
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return _service_to_out(obj)

    async def delete(self, ids: list[int]) -> None:
        """删除保护：被任一模型引用（含软删）的服务拒绝删除，提示改为停用。"""
        from app.core.exceptions import CustomException

        for id_ in ids:
            if await self._is_referenced(service_id=id_):
                obj = await self.crud.get(id=id_)
                name = obj.name if obj else str(id_)
                raise CustomException(msg=f"语音服务「{name}」存在语音识别模型，禁止删除；请改为「停用」", code=400)
        await self.crud.delete(ids=ids)

    async def _is_referenced(self, *, service_id: int) -> bool:
        from sqlalchemy import select

        result = await self.db.execute(select(VoiceWorkbenchModelModel.id).where(VoiceWorkbenchModelModel.service_id == service_id).limit(1))
        return result.first() is not None

    async def get_model(self, id: int) -> VoiceWorkbenchServiceModel:
        """按 ID 取语音服务模型实例（供转写侧读取凭证与端点）。"""
        return await self.crud.get_or_404(id=id, msg="语音服务不存在")  # type: ignore[return-value]


class VoiceWorkbenchModelConfigService:
    """语音识别模型管理服务（两级结构的下级）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = VoiceWorkbenchModelCRUD(auth, db)
        self.service_crud = VoiceWorkbenchServiceCRUD(auth, db)

    async def _service_name_map(self) -> dict[int, str]:
        from sqlalchemy import select

        rows = await self.db.execute(select(VoiceWorkbenchServiceModel.id, VoiceWorkbenchServiceModel.name))
        return {row[0]: row[1] for row in rows.all()}

    async def _ensure_service(self, service_id: int) -> VoiceWorkbenchServiceModel:
        """所属服务不存在是调用方入参错误，返回 400 而不是 500。"""
        from app.core.exceptions import CustomException

        obj = await self.service_crud.get(id=service_id)
        if obj is None:
            raise CustomException(msg="所属语音服务不存在", code=400)
        return obj

    async def detail(self, id: int) -> VoiceWorkbenchModelOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="语音识别模型不存在")
        return _model_to_out(obj, await self._service_name_map())

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: VoiceWorkbenchModelQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[VoiceWorkbenchModelOutSchema]:
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
        )
        names = await self._service_name_map()
        result.items = [_model_to_out(item, names) for item in result.items]  # type: ignore[assignment]
        return result

    async def get_list(self, search: VoiceWorkbenchModelQueryParam | None = None, order_by: list[dict] | None = None) -> list[VoiceWorkbenchModelOutSchema]:
        obj_list = await self.crud.get_list(search=search_to_dict(search), order_by=order_by or [{"id": "asc"}])
        names = await self._service_name_map()
        return [_model_to_out(obj, names) for obj in obj_list]

    async def create(self, data: VoiceWorkbenchModelCreateSchema) -> VoiceWorkbenchModelOutSchema:
        await self._ensure_service(data.service_id)
        obj = await self.crud.create(
            {
                "service_id": data.service_id,
                "display_name": data.display_name,
                "model_name": data.model_name,
                "resource_id": data.resource_id,
                "endpoint_url": data.endpoint_url,
                "max_file_mb": data.max_file_mb,
                "max_duration_sec": data.max_duration_sec,
                "status": data.status,
                "remark": data.remark,
            }
        )
        return _model_to_out(obj, await self._service_name_map())

    async def update(self, id: int, data: VoiceWorkbenchModelUpdateSchema) -> VoiceWorkbenchModelOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="语音识别模型不存在")
        await self._ensure_service(data.service_id)
        for key, value in {
            "service_id": data.service_id,
            "display_name": data.display_name,
            "model_name": data.model_name,
            "resource_id": data.resource_id,
            "endpoint_url": data.endpoint_url,
            "max_file_mb": data.max_file_mb,
            "max_duration_sec": data.max_duration_sec,
            "status": data.status,
            "remark": data.remark,
        }.items():
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return _model_to_out(obj, await self._service_name_map())

    async def delete(self, ids: list[int]) -> None:
        """删除保护：被任一历史转写任务（含软删）引用的模型拒绝删除，提示改为停用。"""
        from app.core.exceptions import CustomException

        for id_ in ids:
            if await self._is_referenced(model_pk=id_):
                obj = await self.crud.get(id=id_)
                name = obj.display_name if obj else str(id_)
                raise CustomException(msg=f"语音识别模型「{name}」存在历史转写任务，禁止删除；请改为「停用」", code=400)
        await self.crud.delete(ids=ids)

    async def _is_referenced(self, *, model_pk: int) -> bool:
        from sqlalchemy import select

        from ..job.model import VoiceWorkbenchJobModel

        result = await self.db.execute(select(VoiceWorkbenchJobModel.id).where(VoiceWorkbenchJobModel.model_pk == model_pk).limit(1))
        return result.first() is not None

    async def get_model(self, id: int) -> VoiceWorkbenchModelModel:
        return await self.crud.get_or_404(id=id, msg="语音识别模型不存在")  # type: ignore[return-value]
