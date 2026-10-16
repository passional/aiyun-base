"""渠道评估 - 模型映射服务（spec: channel-eval/model-mapping）

- 别名表是「已确认归属」的唯一存放处；疑似同族的建议每次实时算，不落库、不参与分组。
- 解析（渠道模型串 → 规范模型）发生在报告渲染时，而非运行创建时：补一条别名，
  历史报告立刻正确；导出件在导出时刻冻结，归档不受后续维护影响。
- 唯一性（显示名、别名串）在未删除行内由服务层校验，不加数据库唯一约束——本仓库的删除
  是软删除，数据库唯一索引会让「删掉再建回来」永久失败。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, BaseSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.channel_eval.model_map.crud import CanonicalModelCRUD
from app.modules.channel_eval.model_map.model import ChannelEvalCanonicalModel, ChannelEvalModelAlias
from app.modules.channel_eval.model_map.normalize import build_suggestions
from app.modules.channel_eval.model_map.schema import (
    AliasObservationSchema,
    CanonicalModelCreateSchema,
    CanonicalModelOutSchema,
    CanonicalModelQueryParam,
    CanonicalModelUpdateSchema,
    ConfirmSuggestionSchema,
    ModelAliasOutSchema,
    SuggestionGroupSchema,
    UnmappedModelSchema,
)
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.run.model import ChannelEvalRunComboModel, ChannelEvalRunModel
from app.utils.common_util import search_to_dict


@dataclass(frozen=True)
class AliasTarget:
    """别名解析结果：报告分组用它把组合归到规范模型。"""

    canonical_id: int
    display_name: str
    family: str | None


async def resolve_alias_map(db: AsyncSession) -> dict[str, AliasTarget]:
    """已确认的「渠道模型串 → 规范模型」映射。报告分组经此解析。"""
    rows = (
        await db.execute(
            select(
                ChannelEvalModelAlias.alias,
                ChannelEvalCanonicalModel.id,
                ChannelEvalCanonicalModel.display_name,
                ChannelEvalCanonicalModel.family,
            )
            .join(ChannelEvalCanonicalModel, ChannelEvalModelAlias.canonical_id == ChannelEvalCanonicalModel.id)
            .where(
                ChannelEvalModelAlias.is_deleted.is_(False),
                ChannelEvalCanonicalModel.is_deleted.is_(False),
            )
        )
    ).all()
    return {alias: AliasTarget(canonical_id=canonical_id, display_name=display_name, family=family) for alias, canonical_id, display_name, family in rows}


class ChannelEvalModelMapService:
    """模型映射服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.crud = CanonicalModelCRUD(auth, db)

    # ─────────────────────── CRUD ───────────────────────

    async def detail(self, id: int) -> CanonicalModelOutSchema:
        obj = await self.crud.get_or_404(id=id, msg="规范模型不存在")
        aliases = await self._aliases_of([obj.id])
        return self._to_out(obj, aliases.get(obj.id, []))

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: CanonicalModelQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> PageResultSchema[CanonicalModelOutSchema]:
        offset = (page_no - 1) * page_size
        result = await self.crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=search_to_dict(search),
        )
        objs: Sequence[ChannelEvalCanonicalModel] = result.items
        aliases = await self._aliases_of([obj.id for obj in objs])
        return PageResultSchema[CanonicalModelOutSchema](
            page_no=result.page_no,
            page_size=result.page_size,
            total=result.total,
            has_next=result.has_next,
            items=[self._to_out(obj, aliases.get(obj.id, [])) for obj in objs],
        )

    async def create(self, data: CanonicalModelCreateSchema) -> CanonicalModelOutSchema:
        return await self._create_with_aliases(data.display_name, data.family, data.remark, data.aliases, source="manual")

    async def update(self, id: int, data: CanonicalModelUpdateSchema) -> CanonicalModelOutSchema:
        await self.crud.get_or_404(id=id, msg="规范模型不存在")
        await self._ensure_name_free(data.display_name, exclude_id=id)
        await self._ensure_aliases_free(data.aliases, exclude_id=id)
        await self.crud.update(
            id,
            {"display_name": data.display_name, "family": data.family, "remark": data.remark},
        )
        await self._sync_aliases(id, data.aliases)
        return await self.detail(id)

    async def delete(self, ids: list[int]) -> None:
        """删除规范模型：别名一并软删除。

        别名不跟着删，这些串会永远被「已属于其它规范模型」拦住，再也归不到别处。
        """
        for id in ids:
            await self.crud.get_or_404(id=id, msg="规范模型不存在")
        await self.db.execute(
            update(ChannelEvalModelAlias).where(ChannelEvalModelAlias.canonical_id.in_(ids), ChannelEvalModelAlias.is_deleted.is_(False)).values(is_deleted=True, deleted_time=datetime.now(UTC))
        )
        await self.crud.delete(ids=ids)

    # ─────────────────────── 建议与未确认串 ───────────────────────

    async def suggestions(self) -> list[SuggestionGroupSchema]:
        observed = await self._observed()
        groups = build_suggestions(observed.keys(), await self._confirmed())
        return [
            SuggestionGroupSchema(
                suggested_name=group["suggested_name"],
                normalized=group["normalized"],
                observations=[self._observation(alias, observed, has_suggestion=True) for alias in group["aliases"]],
                reason_codes=group["reasons"],
                reasons=group["reason_labels"],
            )
            for group in groups
        ]

    async def unmapped(self) -> UnmappedModelSchema:
        observed = await self._observed()
        confirmed = await self._confirmed()
        pending = {alias for group in build_suggestions(observed.keys(), confirmed) for alias in group["aliases"]}
        items = [self._observation(alias, observed, alias in pending) for alias in sorted(set(observed) - confirmed)]
        return UnmappedModelSchema(observations=items, suggestion_count=sum(1 for item in items if item.has_suggestion))

    async def confirm(self, data: ConfirmSuggestionSchema) -> CanonicalModelOutSchema:
        """确认一条建议：一组串落成一个规范模型。"""
        return await self._create_with_aliases(data.display_name, data.family, None, data.aliases, source="auto")

    # ─────────────────────── 内部 ───────────────────────

    async def _create_with_aliases(
        self,
        display_name: str,
        family: str | None,
        remark: str | None,
        aliases: list[str],
        source: str,
    ) -> CanonicalModelOutSchema:
        await self._ensure_name_free(display_name)
        await self._ensure_aliases_free(aliases)
        obj = await self.crud.create({"display_name": display_name, "family": family, "remark": remark})
        await self._insert_aliases(obj.id, aliases, source)
        return await self.detail(obj.id)

    async def _insert_aliases(self, canonical_id: int, aliases: list[str], source: str) -> None:
        for alias in aliases:
            self.db.add(ChannelEvalModelAlias(canonical_id=canonical_id, alias=alias, source=source))
        await self.db.flush()

    async def _sync_aliases(self, canonical_id: int, wanted: list[str]) -> None:
        """编辑时把别名集合同步为 ``wanted``：不在其中的软删除，新出现的插入。"""
        existing = {
            row.alias: row
            for row in (
                await self.db.execute(
                    select(ChannelEvalModelAlias).where(
                        ChannelEvalModelAlias.canonical_id == canonical_id,
                        ChannelEvalModelAlias.is_deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        }
        wanted_set = set(wanted)
        now = datetime.now(UTC)
        for alias, row in existing.items():
            if alias not in wanted_set:
                row.is_deleted = True
                row.deleted_time = now
        await self.db.flush()
        # 已软删除的旧行不再计入 existing，同名串会重新插入一行——数据库无唯一约束，正是为此
        await self._insert_aliases(canonical_id, [alias for alias in wanted if alias not in existing], "manual")

    async def _ensure_name_free(self, display_name: str, exclude_id: int | None = None) -> None:
        stmt = select(ChannelEvalCanonicalModel).where(
            ChannelEvalCanonicalModel.display_name == display_name,
            ChannelEvalCanonicalModel.is_deleted.is_(False),
        )
        if exclude_id is not None:
            stmt = stmt.where(ChannelEvalCanonicalModel.id != exclude_id)
        found = (await self.db.execute(stmt)).scalars().first()
        if found is not None:
            raise CustomException(msg=f"显示名「{display_name}」已被规范模型 #{found.id} 使用", code=400, status_code=400)

    async def _ensure_aliases_free(self, aliases: list[str], exclude_id: int | None = None) -> None:
        """一个渠道模型串至多属于一个规范模型。"""
        if not aliases:
            return
        stmt = select(ChannelEvalModelAlias.alias, ChannelEvalModelAlias.canonical_id).where(
            ChannelEvalModelAlias.alias.in_(aliases),
            ChannelEvalModelAlias.is_deleted.is_(False),
        )
        if exclude_id is not None:
            stmt = stmt.where(ChannelEvalModelAlias.canonical_id != exclude_id)
        rows = (await self.db.execute(stmt)).all()
        if not rows:
            return
        owners = {
            obj.id: obj.display_name
            for obj in (await self.db.execute(select(ChannelEvalCanonicalModel).where(ChannelEvalCanonicalModel.id.in_({canonical_id for _, canonical_id in rows})))).scalars().all()
        }
        detail = "、".join(f"{alias}（已属于「{owners.get(canonical_id, canonical_id)}」）" for alias, canonical_id in rows)
        raise CustomException(msg=f"以下渠道模型串已属于其它规范模型：{detail}", code=400, status_code=400)

    async def _aliases_of(self, canonical_ids: Sequence[int]) -> dict[int, list[ChannelEvalModelAlias]]:
        if not canonical_ids:
            return {}
        rows = (
            (
                await self.db.execute(
                    select(ChannelEvalModelAlias)
                    .where(
                        ChannelEvalModelAlias.canonical_id.in_(list(canonical_ids)),
                        ChannelEvalModelAlias.is_deleted.is_(False),
                    )
                    .order_by(ChannelEvalModelAlias.alias)
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[int, list[ChannelEvalModelAlias]] = {}
        for row in rows:
            grouped.setdefault(row.canonical_id, []).append(row)
        return grouped

    async def _confirmed(self) -> set[str]:
        rows = (await self.db.execute(select(ChannelEvalModelAlias.alias).where(ChannelEvalModelAlias.is_deleted.is_(False)))).all()
        return {alias for (alias,) in rows}

    async def _observed(self) -> dict[str, dict[str, set[str]]]:
        """已观测到的渠道模型串 → 它出现过的平台与渠道（供人工判断是否同族）。"""
        rows = (
            await self.db.execute(
                select(
                    ChannelEvalRunComboModel.model,
                    ChannelEvalPlatformModel.name,
                    ChannelEvalRunComboModel.channel_name,
                )
                .join(ChannelEvalRunModel, ChannelEvalRunComboModel.run_id == ChannelEvalRunModel.id)
                .join(ChannelEvalPlatformModel, ChannelEvalRunModel.platform_id == ChannelEvalPlatformModel.id)
                .where(ChannelEvalRunModel.is_deleted.is_(False))
            )
        ).all()
        observed: dict[str, dict[str, set[str]]] = {}
        for model, platform_name, channel_name in rows:
            entry = observed.setdefault(model, {"platforms": set(), "channels": set()})
            if platform_name:
                entry["platforms"].add(platform_name)
            if channel_name:
                entry["channels"].add(channel_name)
        return observed

    @staticmethod
    def _observation(alias: str, observed: dict[str, dict[str, set[str]]], has_suggestion: bool) -> AliasObservationSchema:
        entry = observed.get(alias) or {"platforms": set(), "channels": set()}
        return AliasObservationSchema(
            alias=alias,
            platforms=sorted(entry["platforms"]),
            channels=sorted(entry["channels"]),
            has_suggestion=has_suggestion,
        )

    @staticmethod
    def _to_out(obj: ChannelEvalCanonicalModel, aliases: Sequence[ChannelEvalModelAlias]) -> CanonicalModelOutSchema:
        # 经 BaseSchema 转一道，避免 model_validate 去读 aliases 关系触发异步懒加载
        return CanonicalModelOutSchema(
            **BaseSchema.model_validate(obj).model_dump(),
            display_name=obj.display_name,
            family=obj.family,
            remark=obj.remark,
            status=obj.status,
            aliases=[ModelAliasOutSchema.model_validate(alias) for alias in aliases],
        )
