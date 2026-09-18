"""绘图工作台 - 配置业务层（绘图服务 / 绘图模型 CRUD 与连通性测试）

凭证处理照 ``channel_eval/platform/service.py`` 的先例：明文经 ``CryptoUtil.encrypt``
落库、出参只回显脱敏占位、编辑留空沿用原值。绘图服务是独立实体（ADR-0004），
不引用也不复用渠道评估的「平台」。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.utils.common_util import search_to_dict

from .capability import dump_capability, parse_capability
from .crud import ImageWorkbenchModelCRUD, ImageWorkbenchServiceCRUD
from .model import ImageWorkbenchModelModel, ImageWorkbenchServiceModel
from .schema import (
    ImageWorkbenchModelCreateSchema,
    ImageWorkbenchModelOutSchema,
    ImageWorkbenchModelQueryParam,
    ImageWorkbenchModelUpdateSchema,
    ImageWorkbenchServiceCreateSchema,
    ImageWorkbenchServiceOutSchema,
    ImageWorkbenchServiceQueryParam,
    ImageWorkbenchServiceUpdateSchema,
    ImageWorkbenchTestConnectionResultSchema,
    ImageWorkbenchTestConnectionSchema,
    dialect_label,
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


def _to_out(obj: Any) -> ImageWorkbenchServiceOutSchema:
    out = ImageWorkbenchServiceOutSchema.model_validate(obj)
    out.api_key_masked = mask_credential(obj.api_key_enc)
    out.has_api_key = bool(obj.api_key_enc)
    out.dialect_label = dialect_label(obj.dialect)
    return out


def decrypt_credential(encrypted: str | None) -> str:
    """密文 → 明文凭证（仅供发起上游请求的调用层使用，绝不出现在响应中）。"""
    if not encrypted:
        return ""
    from app.utils.crypto_util import CryptoUtil

    return CryptoUtil.decrypt(encrypted)


class ImageWorkbenchServiceService:
    """绘图服务管理服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ImageWorkbenchServiceCRUD(auth, db)

    # ─────────────────────── CRUD ───────────────────────

    async def detail(self, id: int) -> ImageWorkbenchServiceOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="绘图服务不存在")
        return _to_out(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ImageWorkbenchServiceQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ImageWorkbenchServiceOutSchema]:
        offset = (page_no - 1) * page_size
        result = await self.crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
        )
        # 脱敏凭证需基于模型重算，不传 out_schema 直接拿模型实例
        result.items = [_to_out(item) for item in result.items]  # type: ignore[assignment]
        return result

    async def get_list(self, search: ImageWorkbenchServiceQueryParam | None = None, order_by: list[dict] | None = None) -> list[ImageWorkbenchServiceOutSchema]:
        obj_list = await self.crud.get_list(search=search_to_dict(search), order_by=order_by or [{"id": "asc"}])
        return [_to_out(obj) for obj in obj_list]

    async def create(self, data: ImageWorkbenchServiceCreateSchema) -> ImageWorkbenchServiceOutSchema:
        from app.utils.crypto_util import CryptoUtil

        exists = await self.crud.get(name=data.name)
        if exists:
            from app.core.exceptions import CustomException

            raise CustomException(msg=f"创建失败，绘图服务名称「{data.name}」已存在", code=400, status_code=400)
        obj = await self.crud.create(
            {
                "name": data.name,
                "dialect": data.dialect,
                "base_url": data.base_url,
                "api_key_enc": CryptoUtil.encrypt(data.api_key) if (data.api_key and data.api_key.strip()) else None,
                "status": data.status,
                "remark": data.remark,
            }
        )
        return _to_out(obj)

    async def update(self, id: int, data: ImageWorkbenchServiceUpdateSchema) -> ImageWorkbenchServiceOutSchema:
        from app.core.exceptions import CustomException
        from app.utils.crypto_util import CryptoUtil

        obj = await self.crud.get_or_404(id=id, msg="绘图服务不存在")
        exists = await self.crud.get(name=data.name)
        if exists and exists.id != id:
            raise CustomException(msg=f"更新失败，绘图服务名称「{data.name}」已存在", code=400, status_code=400)
        update_dict: dict[str, Any] = {
            "name": data.name,
            "dialect": data.dialect,
            "base_url": data.base_url,
            "status": data.status,
            "remark": data.remark,
        }
        # 凭证：留空/缺省保留原值；填写则重新加密
        if data.api_key and data.api_key.strip():
            update_dict["api_key_enc"] = CryptoUtil.encrypt(data.api_key.strip())
        for key, value in update_dict.items():
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return _to_out(obj)

    async def delete(self, ids: list[int]) -> None:
        """删除前引用保护：被绘图任务引用的绘图服务拒绝删除并提示可停用（设计 D8）。"""
        from app.core.exceptions import CustomException

        for id_ in ids:
            referenced = await self._is_referenced(service_id=id_)
            if referenced:
                obj = await self.crud.get(id=id_)
                name = obj.name if obj else str(id_)
                raise CustomException(
                    msg=f"绘图服务「{name}」存在历史绘图任务，禁止删除；如需停用其对外可用性，请改为「停用」",
                    code=400,
                    status_code=400,
                )
        await self.crud.delete(ids=ids)

    async def _is_referenced(self, *, service_id: int) -> bool:
        """是否存在引用该绘图服务的绘图任务（含软删记录：历史信息不得悬空）。"""
        from sqlalchemy import select

        from ..job.model import ImageWorkbenchJobModel

        result = await self.db.execute(select(ImageWorkbenchJobModel.id).where(ImageWorkbenchJobModel.service_id == service_id).limit(1))
        return result.first() is not None

    async def get_model(self, id: int) -> ImageWorkbenchServiceModel:
        """按 ID 取绘图服务模型实例（供生成侧读取凭证与 base_url）。"""
        return await self.crud.get_or_404(id=id, msg="绘图服务不存在")  # type: ignore[return-value]

    # ────────────────────── 连通性测试 ───────────────────────

    async def test_connection(self, data: ImageWorkbenchTestConnectionSchema) -> ImageWorkbenchTestConnectionResultSchema:
        """用「所填值」发一次**只读**探测请求验证可用性，返回成功/失败原因。

        - 探测走 ``GET {base_url}/models``：零成本、不产生任何绘图任务（spec:
          service-config / 连通性测试「MUST NOT 在工作台上产生可见的绘图任务」）；
        - 编辑态（传 ``service_id``）未重填的字段回退库内已存值，凭证留空同样回退；
        - 失败**不阻断保存**：本方法只返回结果，不写库、不抛业务异常。
        """
        from ..job.client import ImageUpstreamClient, ImageUpstreamError

        base_url = data.base_url
        dialect = data.dialect
        api_key = (data.api_key or "").strip()
        saved: ImageWorkbenchServiceModel | None = None
        if data.service_id:
            saved = await self.crud.get(id=data.service_id)
            if saved is not None:
                if not base_url:
                    base_url = saved.base_url
                if dialect is None:
                    dialect = saved.dialect  # type: ignore[assignment]
                if not api_key and saved.api_key_enc:
                    api_key = decrypt_credential(saved.api_key_enc)
        if dialect is None:
            dialect = "ark"
        if not base_url:
            return ImageWorkbenchTestConnectionResultSchema(success=False, message="缺少 base_url，请先填写完整 API 前缀", dialect=dialect)
        if not api_key:
            return ImageWorkbenchTestConnectionResultSchema(
                success=False,
                message="该绘图服务尚未配置调用凭证，请填写 API Key 后再测试",
                dialect=dialect,
                base_url=base_url,
            )

        client = ImageUpstreamClient(base_url, api_key=api_key, dialect=dialect)
        try:
            model_ids = await client.list_models()
        except ImageUpstreamError as e:
            return ImageWorkbenchTestConnectionResultSchema(success=False, message=e.message, dialect=dialect, base_url=base_url)
        except Exception as e:  # noqa: BLE001
            return ImageWorkbenchTestConnectionResultSchema(success=False, message=f"连通性测试异常: {e}", dialect=dialect, base_url=base_url)

        model_found: bool | None = None
        if data.model_id:
            model_found = data.model_id in model_ids
        if model_ids:
            preview = "、".join(model_ids[:5])
            more = f" 等 {len(model_ids)} 个" if len(model_ids) > 5 else ""
            message = f"连通成功，上游返回模型：{preview}{more}"
        else:
            message = "连通成功（上游未返回模型列表）"
        if model_found is True:
            message += f"；已核对到模型 {data.model_id}"
        elif model_found is False:
            message += f"；注意：上游模型列表中未出现 {data.model_id}（部分上游不提供完整列表，仅供参考）"
        return ImageWorkbenchTestConnectionResultSchema(
            success=True,
            message=message,
            dialect=dialect,
            base_url=base_url,
            model_ids=model_ids,
            model_found=model_found,
        )


def _model_to_out(obj: ImageWorkbenchModelModel, service_names: dict[int, str] | None = None) -> ImageWorkbenchModelOutSchema:
    out = ImageWorkbenchModelOutSchema.model_validate(
        {
            "id": obj.id,
            "uuid": obj.uuid,
            "created_time": obj.created_time,
            "updated_time": obj.updated_time,
            "is_deleted": obj.is_deleted,
            "deleted_time": obj.deleted_time,
            "service_id": obj.service_id,
            "service_name": (service_names or {}).get(obj.service_id),
            "model_id": obj.model_id,
            "display_name": obj.display_name,
            "capability": parse_capability(obj.capability_json),
            "status": obj.status,
            "remark": obj.remark,
        }
    )
    return out


class ImageWorkbenchModelService:
    """绘图模型管理服务（两级结构的下级，能力声明逐模型可配）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ImageWorkbenchModelCRUD(auth, db)
        self.service_crud = ImageWorkbenchServiceCRUD(auth, db)

    async def _service_name_map(self) -> dict[int, str]:
        from sqlalchemy import select

        rows = await self.db.execute(select(ImageWorkbenchServiceModel.id, ImageWorkbenchServiceModel.name))
        return {row[0]: row[1] for row in rows.all()}

    async def _ensure_service(self, service_id: int) -> ImageWorkbenchServiceModel:
        """create/update 的前置校验：所属绘图服务必须存在。

        这里显式返回 400（而不是 ``get_or_404`` 的默认 500）：传错 ``service_id``
        是调用方的入参错误，报 500 会把配置错误混进服务端故障。
        """
        from app.core.exceptions import CustomException

        obj = await self.service_crud.get(id=service_id)
        if obj is None:
            raise CustomException(msg="所属绘图服务不存在", code=400, status_code=400)
        return obj

    async def detail(self, id: int) -> ImageWorkbenchModelOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="绘图模型不存在")
        return _model_to_out(obj, await self._service_name_map())

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ImageWorkbenchModelQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ImageWorkbenchModelOutSchema]:
        result = await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
        )
        names = await self._service_name_map()
        result.items = [_model_to_out(item, names) for item in result.items]  # type: ignore[assignment]
        return result

    async def get_list(self, search: ImageWorkbenchModelQueryParam | None = None, order_by: list[dict] | None = None) -> list[ImageWorkbenchModelOutSchema]:
        obj_list = await self.crud.get_list(search=search_to_dict(search), order_by=order_by or [{"id": "asc"}])
        names = await self._service_name_map()
        return [_model_to_out(obj, names) for obj in obj_list]

    async def create(self, data: ImageWorkbenchModelCreateSchema) -> ImageWorkbenchModelOutSchema:
        from app.core.exceptions import CustomException

        await self._ensure_service(data.service_id)
        dup = await self.crud.get(service_id=data.service_id, model_id=data.model_id)
        if dup:
            raise CustomException(msg=f"创建失败，该绘图服务下已存在模型「{data.model_id}」", code=400, status_code=400)
        obj = await self.crud.create(
            {
                "service_id": data.service_id,
                "model_id": data.model_id,
                "display_name": data.display_name,
                "capability_json": dump_capability(data.capability),
                "status": data.status,
                "remark": data.remark,
            }
        )
        return _model_to_out(obj, await self._service_name_map())

    async def update(self, id: int, data: ImageWorkbenchModelUpdateSchema) -> ImageWorkbenchModelOutSchema:
        from app.core.exceptions import CustomException

        obj = await self.crud.get_or_404(id=id, msg="绘图模型不存在")
        await self._ensure_service(data.service_id)
        dup = await self.crud.get(service_id=data.service_id, model_id=data.model_id)
        if dup and dup.id != id:
            raise CustomException(msg=f"更新失败，该绘图服务下已存在模型「{data.model_id}」", code=400, status_code=400)
        for key, value in {
            "service_id": data.service_id,
            "model_id": data.model_id,
            "display_name": data.display_name,
            "capability_json": dump_capability(data.capability),
            "status": data.status,
            "remark": data.remark,
        }.items():
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return _model_to_out(obj, await self._service_name_map())

    async def delete(self, ids: list[int]) -> None:
        """删除前引用保护：被绘图任务引用的绘图模型拒绝删除并提示可停用（设计 D8）。"""
        from app.core.exceptions import CustomException

        for id_ in ids:
            referenced = await self._is_referenced(model_pk=id_)
            if referenced:
                obj = await self.crud.get(id=id_)
                name = obj.display_name if obj else str(id_)
                raise CustomException(
                    msg=f"绘图模型「{name}」存在历史绘图任务，禁止删除；如需停用其对外可用性，请改为「停用」",
                    code=400,
                    status_code=400,
                )
        await self.crud.delete(ids=ids)

    async def _is_referenced(self, *, model_pk: int) -> bool:
        """是否存在引用该绘图模型的绘图任务（含软删记录：历史信息不得悬空）。"""
        from sqlalchemy import select

        from ..job.model import ImageWorkbenchJobModel

        result = await self.db.execute(select(ImageWorkbenchJobModel.id).where(ImageWorkbenchJobModel.model_pk == model_pk).limit(1))
        return result.first() is not None

    async def get_model(self, id: int) -> ImageWorkbenchModelModel:
        """按 ID 取绘图模型实例（供生成侧读取能力声明与所属服务）。"""
        return await self.crud.get_or_404(id=id, msg="绘图模型不存在")  # type: ignore[return-value]
