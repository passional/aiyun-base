"""模型映射测试（渠道模型串 → 规范模型）

- 归一化与差异依据：normalize / reasons_for / build_suggestions（纯函数）
- 服务层：规范模型 CRUD、别名唯一性、软删除后可复用、建议确认、未确认串的观测来源

服务层直测（eval_db + fake_auth），纯函数直测。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.modules.channel_eval.model_map.normalize import (
    DATE,
    PREFIX,
    SEPARATOR,
    build_suggestions,
    normalize,
    reasons_for,
)
from app.modules.channel_eval.model_map.schema import (
    CanonicalModelCreateSchema,
    CanonicalModelUpdateSchema,
    ConfirmSuggestionSchema,
)
from app.modules.channel_eval.model_map.service import ChannelEvalModelMapService
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.run.model import ChannelEvalRunComboModel, ChannelEvalRunModel


async def _make_platform(db: async_sessionmaker, *, name: str, baseline: bool, ptype: str) -> int:
    from app.utils.crypto_util import CryptoUtil

    async with db() as session, session.begin():
        platform = ChannelEvalPlatformModel(
            name=name,
            type=ptype,
            base_url=f"https://{name}.example.com",
            test_key_enc=CryptoUtil.encrypt("sk-test"),
            admin_key_enc=CryptoUtil.encrypt("sk-admin") if ptype == "admin" else None,
            is_baseline=baseline,
        )
        session.add(platform)
        await session.flush()
        return platform.id


async def _make_run(
    db: async_sessionmaker,
    platform_id: int,
    *,
    combos: tuple[tuple[int, str, str], ...] = ((70, "ch70", "m1"),),
) -> int:
    """观测来源用例：只需 run + combo，样本非本文件关注点。"""
    async with db() as session, session.begin():
        run = ChannelEvalRunModel(
            platform_id=platform_id,
            status="success",
            params_json={"request_count": 2, "warmup": False},
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        for channel_id, channel_name, model in combos:
            session.add(ChannelEvalRunComboModel(run_id=run.id, channel_id=channel_id, channel_name=channel_name, model=model, planned=2))
        return run.id


async def _map_svc(fake_auth: AuthSchema, db: async_sessionmaker, method: str, *args):
    """在独立事务中执行一次模型映射服务调用（事务边界对齐 db_getter 的 session.begin）。"""
    async with db() as session, session.begin():
        service = ChannelEvalModelMapService(fake_auth, session)
        return await getattr(service, method)(*args)


# ─────────────────────── 归一化（纯函数） ───────────────────────


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("openai/gpt-6-sol", "gpt-6-sol"),  # 渠道前缀
        ("claude-opus-5-5", "anthropic/claude-opus-5.5"),  # 前缀 + 分隔符
        ("gpt-5.6-sol", "gpt-5.6-sol-20260115"),  # 日期戳
        ("GPT-6-Sol", "gpt-6-sol"),  # 大小写
    ],
)
def test_normalize_same_key(left: str, right: str) -> None:
    """命名习惯差异抹平后落同一个分组键。"""
    assert normalize(left) == normalize(right), f"「{left}」与「{right}」应归一为同一键"


def test_normalize_keeps_variant_suffix() -> None:
    """变体后缀不剥离：-thinking 通常是真不同的模型，误并比漏配更危险。"""
    assert normalize("gpt-6-sol") != normalize("gpt-6-sol-thinking"), "变体后缀不得被剥离"


def test_reasons_for_date_stamp() -> None:
    """日期戳组：跳过日期变换后组会散开，依据只有 date。"""
    assert reasons_for(["gpt-5.6-sol", "gpt-5.6-sol-20260115"]) == [DATE]


def test_reasons_for_prefix() -> None:
    """渠道前缀组：跳过前缀变换后组会散开，依据只有 prefix。"""
    assert reasons_for(["openai/gpt-6-sol", "gpt-6-sol"]) == [PREFIX]


def test_reasons_for_separator() -> None:
    """分隔符组：跳过分隔符变换后组会散开，依据只有 separator。"""
    assert reasons_for(["gpt_6_sol", "gpt-6-sol"]) == [SEPARATOR]


def test_build_suggestions_skips_variant_suffix_pair() -> None:
    """变体后缀对不产建议：归一化键不同，各自独立、绝不合并。"""
    assert build_suggestions(["gpt-6-sol", "gpt-6-sol-thinking"], set()) == [], "变体后缀对不得产出建议"


def test_build_suggestions_excludes_confirmed() -> None:
    """已确认归属的串不参与建议（已在别名表里，再建议一次是噪音）。"""
    suggestions = build_suggestions(["openai/gpt-6-sol", "gpt-6-sol", "gpt-6-sol-alt", "openai/gpt-6-sol-alt"], {"openai/gpt-6-sol"})
    assert [item["aliases"] for item in suggestions] == [["gpt-6-sol-alt", "openai/gpt-6-sol-alt"]], "已确认串应被排除"


def test_build_suggestions_single_string_ignored() -> None:
    """孤串不成组：组内至少两个不同的原始串才产建议。"""
    assert build_suggestions(["gpt-6-sol"], set()) == [], "单个串不得产出建议"


# ─────────────────────── 规范模型 CRUD ───────────────────────


async def test_create_canonical_with_aliases_and_detail(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """创建规范模型：别名随创建写入，detail 原样返回。"""
    created = await _map_svc(
        fake_auth,
        eval_db,
        "create",
        CanonicalModelCreateSchema(display_name="GPT-6 Sol", family="GPT", remark="手工录入", aliases=["openai/gpt-6-sol", "gpt-6-sol"]),
    )
    assert created.display_name == "GPT-6 Sol"
    assert created.family == "GPT"
    assert created.remark == "手工录入"

    detail = await _map_svc(fake_auth, eval_db, "detail", created.id)
    assert detail.id == created.id
    assert detail.display_name == "GPT-6 Sol"
    assert [item.alias for item in detail.aliases] == ["gpt-6-sol", "openai/gpt-6-sol"], "detail 应按别名升序返回全部别名"
    assert {item.source for item in detail.aliases} == {"manual"}, "手工录入的来源应为 manual"


async def test_alias_cannot_belong_to_two_canonical_models(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """一个渠道模型串至多属于一个规范模型：创建与编辑两条路径都拒绝，并指名当前归属。"""
    await _map_svc(fake_auth, eval_db, "create", CanonicalModelCreateSchema(display_name="模型甲", aliases=["shared-model"]))
    other = await _map_svc(fake_auth, eval_db, "create", CanonicalModelCreateSchema(display_name="模型乙", aliases=["other-model"]))

    # 创建路径：串已被模型甲占用
    with pytest.raises(CustomException) as create_exc:
        await _map_svc(fake_auth, eval_db, "create", CanonicalModelCreateSchema(display_name="模型丙", aliases=["shared-model"]))
    assert "已属于" in str(create_exc.value), "异常应说明串已属于其它规范模型"
    assert "模型甲" in str(create_exc.value), "异常应指名当前归属的规范模型"

    # 编辑路径：同样被拦住
    with pytest.raises(CustomException) as update_exc:
        await _map_svc(
            fake_auth,
            eval_db,
            "update",
            other.id,
            CanonicalModelUpdateSchema(display_name="模型乙", aliases=["other-model", "shared-model"]),
        )
    assert "shared-model" in str(update_exc.value)
    assert "模型甲" in str(update_exc.value), "异常应指名当前归属的规范模型"


async def test_update_syncs_alias_set(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """编辑时别名集合同步为提交值：新增的插入、移除的软删除（移除后可被别处复用）。"""
    created = await _map_svc(
        fake_auth,
        eval_db,
        "create",
        CanonicalModelCreateSchema(display_name="模型甲", aliases=["alias-one", "alias-two"]),
    )
    updated = await _map_svc(
        fake_auth,
        eval_db,
        "update",
        created.id,
        CanonicalModelUpdateSchema(display_name="模型甲", aliases=["alias-two", "alias-three"]),
    )
    assert sorted(item.alias for item in updated.aliases) == ["alias-three", "alias-two"], "别名集合应与提交值一致"

    # 被移除的 alias-one 已软删除，可被新的规范模型复用
    reused = await _map_svc(fake_auth, eval_db, "create", CanonicalModelCreateSchema(display_name="模型乙", aliases=["alias-one"]))
    assert [item.alias for item in reused.aliases] == ["alias-one"], "移除后的串应可重新归属"


async def test_delete_soft_deletes_aliases_and_allows_reuse(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """删除规范模型：别名一并软删除，这些串可被新规范模型复用（无数据库唯一约束正是为此）。"""
    created = await _map_svc(fake_auth, eval_db, "create", CanonicalModelCreateSchema(display_name="模型甲", aliases=["shared-model"]))
    await _map_svc(fake_auth, eval_db, "delete", [created.id])

    # 旧规范模型不可见
    with pytest.raises(CustomException, match="规范模型不存在"):
        await _map_svc(fake_auth, eval_db, "detail", created.id)

    # 串可被重新归属——「删掉再建回来」的往返必须成立
    reused = await _map_svc(fake_auth, eval_db, "create", CanonicalModelCreateSchema(display_name="模型乙", aliases=["shared-model"]))
    assert [item.alias for item in reused.aliases] == ["shared-model"], "软删除后同一串应可重新归属"
    assert reused.id != created.id


# ─────────────────────── 建议与未确认串 ───────────────────────


async def test_confirm_suggestion_removes_strings_from_suggestions_and_unmapped(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """确认建议：该组串落成规范模型后，不再出现在建议与未确认清单里。"""
    platform_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    await _make_run(eval_db, platform_id, combos=((70, "ch70", "claude-opus-5-5"), (72, "ch72", "anthropic/claude-opus-5.5")))

    suggestions = await _map_svc(fake_auth, eval_db, "suggestions")
    assert len(suggestions) == 1, "两条同族串应产出一条建议"
    group = suggestions[0]
    assert group.suggested_name == "claude-opus-5-5", "建议名优先无渠道前缀者"
    assert [item.alias for item in group.observations] == ["anthropic/claude-opus-5.5", "claude-opus-5-5"]
    assert set(group.reason_codes) == {PREFIX, SEPARATOR}, "前缀与分隔符差异是这一组的依据"

    unmapped_before = await _map_svc(fake_auth, eval_db, "unmapped")
    assert [item.alias for item in unmapped_before.observations] == ["anthropic/claude-opus-5.5", "claude-opus-5-5"]
    assert unmapped_before.suggestion_count == 2

    confirmed = await _map_svc(
        fake_auth,
        eval_db,
        "confirm",
        ConfirmSuggestionSchema(display_name="Claude Opus 5.5", family="Claude", aliases=[item.alias for item in group.observations]),
    )
    assert confirmed.display_name == "Claude Opus 5.5"
    assert {item.alias for item in confirmed.aliases} == {"claude-opus-5-5", "anthropic/claude-opus-5.5"}
    assert {item.source for item in confirmed.aliases} == {"auto"}, "确认建议的来源应为 auto"

    assert await _map_svc(fake_auth, eval_db, "suggestions") == [], "已确认的串不应再产出建议"
    unmapped_after = await _map_svc(fake_auth, eval_db, "unmapped")
    assert unmapped_after.observations == [], "已确认的串不应再出现在未确认清单里"
    assert unmapped_after.suggestion_count == 0


async def test_unmapped_reports_observation_sources(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """未确认清单：每个串带上它出现过的平台与渠道，供人工判断是否同族。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")
    await _make_run(eval_db, apiworld_id, combos=((70, "ch70", "claude-opus-5-5"),))
    await _make_run(eval_db, aihubmix_id, combos=((0, "baseline", "claude-opus-5-5"), (71, "ch71", "anthropic/claude-opus-5.5")))

    unmapped = await _map_svc(fake_auth, eval_db, "unmapped")
    assert [item.alias for item in unmapped.observations] == ["anthropic/claude-opus-5.5", "claude-opus-5-5"]
    by_alias = {item.alias: item for item in unmapped.observations}

    assert by_alias["claude-opus-5-5"].platforms == ["aihubmix", "apiworld"], "应列出该串出现过的平台"
    assert by_alias["claude-opus-5-5"].channels == ["baseline", "ch70"], "应列出该串出现过的渠道"
    assert by_alias["anthropic/claude-opus-5.5"].platforms == ["aihubmix"]
    assert by_alias["anthropic/claude-opus-5.5"].channels == ["ch71"]

    assert all(item.has_suggestion for item in unmapped.observations), "同族对的两条串都应落在建议里"
    assert unmapped.suggestion_count == 2


async def test_unmapped_lone_string_has_no_suggestion(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """孤串无同族：出现在未确认清单里，但不带建议标记。"""
    platform_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    await _make_run(eval_db, platform_id, combos=((70, "ch70", "gpt-6-sol"),))

    unmapped = await _map_svc(fake_auth, eval_db, "unmapped")
    assert [item.alias for item in unmapped.observations] == ["gpt-6-sol"]
    assert unmapped.observations[0].has_suggestion is False
    assert unmapped.suggestion_count == 0
    assert await _map_svc(fake_auth, eval_db, "suggestions") == []
