"""渠道评估 - 平台管理服务

- 凭证 CryptoUtil 加密落库，出参仅脱敏回显；
- 删除前校验存在测试运行则拒绝（spec: platform-access / 平台登记与凭证管理）；
- 渠道目录实时拉取（不持久化，ADR-0003）+ 路由可达性提示（备用渠道标记，仅提示不阻断）。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from ..run.new_api_client import NewApiClient, probe_pin_capability, test_admin_connection, test_user_connection
from ..run.provision import build_client
from .crud import ChannelEvalPlatformCRUD
from .model import ChannelEvalPlatformModel
from .schema import (
    ChannelEvalChannelCatalogSchema,
    ChannelEvalChannelItemSchema,
    ChannelEvalPlatformCreateSchema,
    ChannelEvalPlatformOutSchema,
    ChannelEvalPlatformQueryParam,
    ChannelEvalPlatformUpdateSchema,
    ChannelEvalTestConnectionResultSchema,
    ChannelEvalTestConnectionSchema,
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


def _to_out(obj: Any) -> ChannelEvalPlatformOutSchema:
    out = ChannelEvalPlatformOutSchema.model_validate(obj)
    out.admin_key_masked = mask_credential(obj.admin_key_enc)
    out.test_key_masked = mask_credential(obj.test_key_enc)
    return out


class ChannelEvalPlatformService:
    """平台管理服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = ChannelEvalPlatformCRUD(auth, db)

    # ─────────────────────── CRUD ───────────────────────

    async def detail(self, id: int) -> ChannelEvalPlatformOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="平台不存在")
        return _to_out(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ChannelEvalPlatformQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[ChannelEvalPlatformOutSchema]:
        offset = (page_no - 1) * page_size
        result = await self.crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search),
        )
        # 脱敏凭证需基于模型重算，不传 out_schema 直接拿模型实例
        items = [_to_out(item) for item in result.items]  # type: ignore[arg-type]
        result.items = items  # type: ignore[assignment]
        return result

    async def get_list(self, search: ChannelEvalPlatformQueryParam | None = None, order_by: list[dict] | None = None) -> list[ChannelEvalPlatformOutSchema]:
        obj_list = await self.crud.get_list(search=search_to_dict(search), order_by=order_by or [{"id": "asc"}])
        return [_to_out(obj) for obj in obj_list]

    async def create(self, data: ChannelEvalPlatformCreateSchema) -> ChannelEvalPlatformOutSchema:
        from app.utils.crypto_util import CryptoUtil

        obj = await self.crud.create(
            {
                "name": data.name,
                "type": data.type,
                "base_url": data.base_url,
                "admin_key_enc": CryptoUtil.encrypt(data.admin_key) if data.type == "admin" else None,
                "admin_user_id": ((data.admin_user_id or "").strip() or None) if data.type == "admin" else None,
                "test_key_enc": CryptoUtil.encrypt(data.test_key),
                "is_baseline": data.is_baseline,
                "status": data.status,
                "remark": data.remark,
            }
        )
        return _to_out(obj)

    async def update(self, id: int, data: ChannelEvalPlatformUpdateSchema) -> ChannelEvalPlatformOutSchema:
        from app.utils.crypto_util import CryptoUtil

        obj = await self.crud.get_or_404(id=id, msg="平台不存在")
        update_dict: dict[str, Any] = {
            "name": data.name,
            "type": data.type,
            "base_url": data.base_url,
            "is_baseline": data.is_baseline,
            "status": data.status,
            "remark": data.remark,
        }
        # 凭证：留空/缺省保留原值；填写则重新加密
        if data.admin_key and data.admin_key.strip():
            update_dict["admin_key_enc"] = CryptoUtil.encrypt(data.admin_key.strip())
        if data.test_key and data.test_key.strip():
            update_dict["test_key_enc"] = CryptoUtil.encrypt(data.test_key.strip())
        # 管理用户ID：字段缺省(None)保留原值；显式置空/清空则清除
        if data.admin_user_id is not None:
            update_dict["admin_user_id"] = (data.admin_user_id or "").strip() or None
        if data.type == "user":
            # 仅用户级平台无管理凭证
            if not (data.admin_key and data.admin_key.strip()):
                update_dict["admin_key_enc"] = None
            update_dict["admin_user_id"] = None
        for key, value in update_dict.items():
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return _to_out(obj)

    async def delete(self, ids: list[int]) -> None:
        """删除前校验：存在测试运行的平台拒绝删除。"""
        from sqlalchemy import select

        from ..run.model import ChannelEvalRunModel

        for id_ in ids:
            runs = await self.db.execute(select(ChannelEvalRunModel.id).where(ChannelEvalRunModel.platform_id == id_).limit(1))
            if runs.first() is not None:
                platform = await self.crud.get(id=id_)
                raise CustomException(msg=f"平台「{platform.name if platform else id_}」存在历史运行数据，禁止删除", code=400, status_code=400)
        await self.crud.delete(ids=ids)

    # ─────────────────────── 测试连接 ───────────────────────

    async def test_connection(self, data: ChannelEvalTestConnectionSchema) -> ChannelEvalTestConnectionResultSchema:
        """用「所填凭证」发只读请求验证可用性，返回成功/失败原因（不阻断平台保存）。"""
        from app.utils.crypto_util import CryptoUtil

        base_url = data.base_url
        admin_key = (data.admin_key or "").strip()
        admin_user_id = (data.admin_user_id or "").strip() or None
        test_key = (data.test_key or "").strip()
        platform_type = data.type
        # 编辑态：未重填凭证时回退库内凭证
        if data.platform_id:
            saved = await self.crud.get(id=data.platform_id)
            if saved:
                if not base_url:
                    base_url = saved.base_url
                if platform_type is None:
                    platform_type = saved.type  # type: ignore[assignment]
                if not admin_key and saved.admin_key_enc:
                    admin_key = CryptoUtil.decrypt(saved.admin_key_enc)
                if not admin_user_id:
                    admin_user_id = saved.admin_user_id
                if not test_key and saved.test_key_enc:
                    test_key = CryptoUtil.decrypt(saved.test_key_enc)
        if platform_type is None:
            platform_type = "admin"
        if not base_url:
            return ChannelEvalTestConnectionResultSchema(success=False, message="缺少平台基础地址")

        if platform_type == "admin":
            if not admin_key:
                return ChannelEvalTestConnectionResultSchema(success=False, message="缺少管理凭证")
            ok, message = await test_admin_connection(base_url, admin_key, admin_user_id=admin_user_id)
            if not ok:
                return ChannelEvalTestConnectionResultSchema(success=ok, message=message)
            # 指定渠道（pin）前置校验：专用评估令牌继承管理凭证所属用户，
            # 该用户必须是管理员（new-api rc.21 指定渠道机制前提），否则运行创建必被拒
            ok2, message2 = await probe_pin_capability(base_url, admin_key)
            return ChannelEvalTestConnectionResultSchema(success=ok2, message=f"{message}；{message2}")
        # user 型（基准渠道）：用测试凭证拉模型列表
        if not test_key:
            return ChannelEvalTestConnectionResultSchema(success=False, message="缺少测试凭证")
        ok, message = await test_user_connection(base_url, test_key)
        return ChannelEvalTestConnectionResultSchema(success=ok, message=message)

    # ─────────────────────── 渠道目录实时拉取 ───────────────────────

    async def get_channel_catalog(self, id: int, *, for_run: bool = False) -> ChannelEvalChannelCatalogSchema:
        """实时拉取平台渠道目录（不持久化；失败返回明确错误，不展示过期缓存）。"""
        from app.utils.crypto_util import CryptoUtil

        # 测试运行选择的平台是共享资源；其独立接口已校验 run:add，按主键读取以免
        # 通用 CRUD 的「仅本人」数据范围屏蔽管理员登记的平台。
        platform = await self.db.get(ChannelEvalPlatformModel, id) if for_run else await self.crud.get_or_404(id=id, msg="平台不存在")
        if platform is None or platform.is_deleted:
            raise CustomException(msg="平台不存在", code=404, status_code=404)
        if platform.status != 0:
            raise CustomException(msg="平台已停用，请先启用后再拉取渠道目录", code=400, status_code=400)

        if platform.type == "user":
            # 基准渠道：整平台视为一条隐式渠道，模型目录经 /v1/models 实时拉取
            client = NewApiClient(platform.base_url, test_key=CryptoUtil.decrypt(platform.test_key_enc))
            try:
                models = await client.list_models()
            except Exception as e:  # noqa: BLE001
                raise CustomException(msg=f"拉取平台模型目录失败: {e}", code=400, status_code=400) from e
            implicit = ChannelEvalChannelItemSchema(
                channel_id=0,
                name=f"{platform.name}（隐式渠道）",
                status=1,
                enabled=True,
                groups=[],
                priority=0,
                weight=0,
                models=models,
            )
            return ChannelEvalChannelCatalogSchema(
                platform_id=platform.id,
                platform_name=platform.name,
                is_baseline=True,
                channels=[implicit],
                pulled_at=datetime.now(UTC).isoformat(),
            )

        if not platform.admin_key_enc:
            raise CustomException(msg="该平台未配置管理凭证，无法拉取渠道目录", code=400, status_code=400)
        client = build_client(platform)
        try:
            raw_channels = await client.list_channels()
        except Exception as e:  # noqa: BLE001
            raise CustomException(msg=f"拉取渠道目录失败: {e}", code=400, status_code=400) from e

        items = [
            ChannelEvalChannelItemSchema(
                channel_id=int(c.get("id") or 0),
                name=str(c.get("name") or ""),
                status=int(c.get("status") or 0),
                enabled=int(c.get("status") or 0) == 1,
                groups=[g.strip() for g in str(c.get("group") or "").split(",") if g.strip()],
                priority=int(c.get("priority") or 0),
                weight=int(c.get("weight") or 0),
                models=[m.strip() for m in str(c.get("models") or "").split(",") if m.strip()],
            )
            for c in raw_channels
        ]
        mark_standby_channels(items)
        return ChannelEvalChannelCatalogSchema(
            platform_id=platform.id,
            platform_name=platform.name,
            is_baseline=bool(platform.is_baseline),
            channels=items,
            pulled_at=datetime.now(UTC).isoformat(),
        )


def mark_standby_channels(channels: list[ChannelEvalChannelItemSchema]) -> None:
    """路由可达性提示：同分组同模型存在更高优先级渠道时标记备用渠道（仅提示不阻断）。

    规则（spec: platform-access / 渠道路由可达性提示）：
    - 仅统计启用渠道；
    - 对每个 (分组, 模型)：候选中最高优先级 > 本渠道优先级 → 本渠道在该组合下不承接线上常规流量；
    - 渠道在任一 (分组, 模型) 组合下被更高优先级覆盖 → is_standby=True 并给出提示文案。
    """
    # (group, model) -> 最高优先级
    top_priority: dict[tuple[str, str], int] = {}
    for channel in channels:
        if not channel.enabled:
            continue
        for group in channel.groups:
            for model in channel.models:
                key = (group, model)
                current = top_priority.get(key)
                if current is None or channel.priority > current:
                    top_priority[key] = channel.priority
    for channel in channels:
        if not channel.enabled:
            continue
        shadowed: list[str] = []
        for group in channel.groups:
            for model in channel.models:
                if channel.priority < top_priority.get((group, model), channel.priority):
                    shadowed.append(model)
        if shadowed:
            channel.is_standby = True
            preview = "、".join(sorted(set(shadowed))[:5])
            more = "" if len(set(shadowed)) <= 5 else " 等"
            channel.standby_hint = f"同分组存在更高优先级渠道，该渠道当前不承接线上常规流量（涉及模型：{preview}{more}）"
