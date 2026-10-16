"""对比报告测试（任务 6.1~6.4）

- 6.1 报告 CRUD + 基准校验（基准运行须为基准渠道的成功运行）
- 6.2 视图聚合（按模型分组、基准并列、交叉对比差值、无数据/无基准）
- 6.3 自动结论规则（仅阈值事实、无评价性措辞）
- 6.4 自包含 HTML 导出（单文件、零外部资源引用）

服务层直测（eval_db）+ API 层冒烟（test_client）。
"""

import json
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.modules.channel_eval.model_map.schema import CanonicalModelCreateSchema
from app.modules.channel_eval.model_map.service import ChannelEvalModelMapService
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.report.schema import ChannelEvalReportCreateSchema
from app.modules.channel_eval.report.service import ChannelEvalReportService
from app.modules.channel_eval.run.model import (
    ChannelEvalRunComboModel,
    ChannelEvalRunModel,
    ChannelEvalSampleModel,
)


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
    status: str = "success",
    combos: tuple[tuple[int, str, str], ...] = ((70, "ch70", "m1"),),
    samples: dict[tuple[int, str], list[dict]] | None = None,
) -> int:
    """samples: {(channel_id, model): [sample 字段 ...]}"""
    async with db() as session, session.begin():
        run = ChannelEvalRunModel(
            platform_id=platform_id,
            status=status,
            params_json={"request_count": 2, "warmup": False, "interval_seconds": 0, "timeout_seconds": 10, "prompt": "hi", "anomaly_threshold_seconds": 30},
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        for channel_id, channel_name, model in combos:
            combo = ChannelEvalRunComboModel(run_id=run.id, channel_id=channel_id, channel_name=channel_name, model=model, planned=2)
            session.add(combo)
            await session.flush()
            for sample_fields in (samples or {}).get((channel_id, model), []):
                sample_fields.setdefault("status", "success")
                session.add(ChannelEvalSampleModel(combo_id=combo.id, **sample_fields))
        return run.id


async def _make_run_with_params(
    db: async_sessionmaker,
    platform_id: int,
    *,
    params: dict,
    combos: tuple[tuple[int, str, str], ...] = ((70, "ch70", "m1"),),
    samples: dict[tuple[int, str], list[dict]] | None = None,
) -> int:
    """同 _make_run，但运行参数快照由调用方给定；不写执行时间窗（可比性用例需两运行时间窗一致）。"""
    async with db() as session, session.begin():
        run = ChannelEvalRunModel(platform_id=platform_id, status="success", params_json=params, started_at=None, finished_at=None)
        session.add(run)
        await session.flush()
        for channel_id, channel_name, model in combos:
            combo = ChannelEvalRunComboModel(run_id=run.id, channel_id=channel_id, channel_name=channel_name, model=model, planned=params.get("request_count") or 0)
            session.add(combo)
            await session.flush()
            for sample_fields in (samples or {}).get((channel_id, model), []):
                sample_fields.setdefault("status", "success")
                session.add(ChannelEvalSampleModel(combo_id=combo.id, **sample_fields))
        return run.id


async def _map_svc(fake_auth: AuthSchema, db, method: str, *args):
    """在独立事务中执行一次模型映射服务调用（报告用例准备别名用）。"""
    async with db() as session, session.begin():
        service = ChannelEvalModelMapService(fake_auth, session)
        return await getattr(service, method)(*args)


async def _svc(fake_auth: AuthSchema, db, method: str, *args):
    """在独立事务中执行一次服务调用（事务边界对齐 db_getter 的 session.begin）。"""
    async with db() as session, session.begin():
        service = ChannelEvalReportService(fake_auth, session)
        return await getattr(service, method)(*args)


# ─────────────────────── 6.1 CRUD + 基准校验 ───────────────────────


async def test_report_crud_and_baseline_validation(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """run_ids/baseline_run_id/note 持久化；基准校验拒绝分支。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")

    normal_run = await _make_run(eval_db, apiworld_id)
    baseline_ok_run = await _make_run(eval_db, aihubmix_id, status="success")
    baseline_failed_run = await _make_run(eval_db, aihubmix_id, status="failed")

    # 基准来自非基准渠道 → 拒绝
    with pytest.raises(CustomException, match="基准运行须来自基准渠道"):
        await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="r1", run_ids=[normal_run], baseline_run_id=normal_run))

    # 基准运行非成功状态 → 拒绝
    with pytest.raises(CustomException, match="基准运行须为成功完成的运行"):
        await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="r2", run_ids=[normal_run], baseline_run_id=baseline_failed_run))

    # 所选运行不存在 → 拒绝
    with pytest.raises(CustomException, match="不存在"):
        await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="r3", run_ids=[999999]))

    # 合法创建：run_ids / baseline_run_id / note 持久化
    created = await _svc(
        fake_auth,
        eval_db,
        "create",
        ChannelEvalReportCreateSchema(
            name="渠道对比报告",
            run_ids=[normal_run],
            baseline_run_id=baseline_ok_run,
            note="管理员备注内容",
        ),
    )
    assert created.run_ids == [normal_run]
    assert created.baseline_run_id == baseline_ok_run
    assert created.note == "管理员备注内容"

    # 编辑：改选基准运行
    updated = await _svc(
        fake_auth,
        eval_db,
        "update",
        created.id,
        ChannelEvalReportCreateSchema(name="渠道对比报告-改", run_ids=[normal_run], baseline_run_id=None, note=None),
    )
    assert updated.name == "渠道对比报告-改"
    assert updated.baseline_run_id is None


# ─────────────────────── 6.2 视图聚合 ───────────────────────


async def test_report_view_aggregation(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """按模型分组并列各运行组合与基准；交叉对比差值；无基准模型标记。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")

    # 平台 A：渠道 70 (m1, m2)，渠道 72 (m1)；带客户端 + 后台样本
    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "m1"), (70, "ch70", "m2"), (72, "ch72", "m1")),
        samples={
            (70, "m1"): [
                {"seq": 1, "ttft_ms": 4200, "total_ms": 9000, "tokens_out": 20, "be_matched": True, "be_frt_ms": 2500, "be_use_time_ms": 8000},
                {"seq": 2, "ttft_ms": 4400, "total_ms": 9200, "tokens_out": 20, "be_matched": True, "be_frt_ms": 2600, "be_use_time_ms": 8200},
            ],
            (70, "m2"): [
                {"seq": 1, "ttft_ms": 1000, "total_ms": 2000, "tokens_out": 10},
            ],
            (72, "m1"): [
                {"seq": 1, "ttft_ms": 6000, "total_ms": 11000, "tokens_out": 5},
                {"seq": 2, "status": "failed", "error": "timeout", "ttft_ms": None, "total_ms": None},
            ],
        },
    )
    # 基准运行：仅覆盖 m1（m2 无基准数据）
    baseline_run = await _make_run(
        eval_db,
        aihubmix_id,
        combos=((0, "aihubmix（隐式渠道）", "m1"),),
        samples={
            (0, "m1"): [
                {"seq": 1, "ttft_ms": 1000, "total_ms": 3000, "tokens_out": 20, "be_matched": True, "be_frt_ms": 800, "be_use_time_ms": 2800},
            ],
        },
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="视图测试", run_ids=[run_a], baseline_run_id=baseline_run))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    # 运行标识：所选运行 + 基准运行并列
    assert {r.id for r in view.runs} == {run_a, baseline_run}
    assert view.baseline_run is not None and view.baseline_run.id == baseline_run
    assert view.baseline_run.is_baseline is True

    models = {group.model: group for group in view.models}
    assert set(models) == {"m1", "m2"}

    # m1：基准 + 两条渠道并列
    m1 = models["m1"]
    assert m1.has_baseline is True
    assert m1.baseline is not None and m1.baseline.is_baseline is True
    assert {c.channel_id for c in m1.combos} == {70, 72}

    ch70 = next(c for c in m1.combos if c.channel_id == 70)
    assert ch70.ttft_avg_ms == 4300  # (4200+4400)/2
    assert ch70.total_avg_ms == 9100
    # 交叉对比：客户端 − 后台（spec scenario：4.2s vs 2.5s → +1.7s）
    assert ch70.be_frt_avg_ms == 2550
    assert ch70.cross_ttft_diff_ms == 4300 - 2550
    assert ch70.cross_total_diff_ms == 9100 - 8100
    # 与基准差值
    assert ch70.ttft_vs_baseline_ms == 4300 - 1000
    # 成功率：72 渠道 1/2
    ch72 = next(c for c in m1.combos if c.channel_id == 72)
    assert ch72.success_rate == 0.5
    # 后台无数据 → be 均值 None（前端显示"—"），交叉差值 None
    assert ch72.be_frt_avg_ms is None
    assert ch72.cross_ttft_diff_ms is None

    # m2：无基准数据
    m2 = models["m2"]
    assert m2.has_baseline is False
    assert m2.baseline is None
    assert m2.combos[0].ttft_vs_baseline_ms is None

    # 异常清单：失败样本
    assert any(item.kind == "failed" and item.channel_id == 72 for item in view.anomalies)


# ─────────────────────── 6.3 自动结论 ───────────────────────


async def test_report_conclusions_factual_only(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """结论仅阈值事实：高于基准幅度、异常样本、成功率非 100%；无评价性措辞。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")

    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "m1"), (72, "ch72", "m2")),
        samples={
            # ttft 均值 5000 高于基准 1000 达 4 秒（≥ 2 秒阈值）
            (70, "m1"): [
                {"seq": 1, "ttft_ms": 4900, "total_ms": 9000},
                {"seq": 2, "ttft_ms": 5100, "total_ms": 9200},
            ],
            # 异常样本（45s > 30s 阈值）+ 失败样本（成功率 1/2）
            (72, "m2"): [
                {"seq": 1, "ttft_ms": 45000, "total_ms": 46000},
                {"seq": 2, "status": "failed", "error": "timeout", "ttft_ms": None, "total_ms": None},
            ],
        },
    )
    baseline_run = await _make_run(
        eval_db,
        aihubmix_id,
        combos=((0, "baseline", "m1"),),
        samples={(0, "m1"): [{"seq": 1, "ttft_ms": 1000, "total_ms": 3000}]},
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="结论测试", run_ids=[run_a], baseline_run_id=baseline_run))
    view = await _svc(fake_auth, eval_db, "view", created.id)
    contents = [c.content for c in view.conclusions]
    joined = "\n".join(contents)

    # 阈值事实：高于基准 2 秒以上
    assert any("高于基准" in c and "首 Token 时延" in c for c in contents)
    # 异常样本事实
    assert any("异常样本" in c for c in contents)
    # 成功率非 100% 事实
    assert any("成功率" in c for c in contents)
    # 无基准数据的模型 → 陈述"无基准数据"
    assert any("无基准数据" in c for c in contents)

    # 无评价性/建议性措辞
    for word in ("建议", "避开", "优选", "推荐", "更好", "较差", "应该"):
        assert word not in joined, f"结论包含评价性措辞: {word}"

    # 事实陈述包含具体数值
    assert "5000" in joined  # ttft 均值
    assert "1 条" in joined  # 异常样本数


# ─────────────────────── 6.4 自包含 HTML 导出 ───────────────────────


async def test_export_html_self_contained(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """导出物为单文件自包含 HTML：零外部资源引用、关键区块齐全、数据内嵌。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")
    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "m1"),),
        samples={(70, "m1"): [{"seq": 1, "ttft_ms": 4200, "total_ms": 9000, "be_matched": True, "be_frt_ms": 2500, "be_use_time_ms": 8000}]},
    )
    baseline_run = await _make_run(
        eval_db,
        aihubmix_id,
        combos=((0, "baseline", "m1"),),
        samples={(0, "m1"): [{"seq": 1, "ttft_ms": 1000, "total_ms": 3000}]},
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="导出测试报告", run_ids=[run_a], baseline_run_id=baseline_run, note="备注内容 XYZ"))
    html = await _svc(fake_auth, eval_db, "export_html", created.id)

    # 报告头：名称 / 生成时间 / 运行与基准标识
    assert "导出测试报告" in html
    assert "生成时间" in html
    assert f"#{run_a}" in html and f"#{baseline_run}" in html
    # 关键区块（区块名以本变更的 compare-report 规格为准）
    for section in (
        "比较范围",
        "关键指标概览",
        "核心结论",
        "逐模型性能可视化",
        "详细指标对比",
        "交叉对比",
        "异常清单",
        "测试配置与执行信息",
        "自动结论与管理员备注",
        "指标口径说明",
    ):
        assert section in html, f"缺少区块: {section}"
    # 备注随导出包含
    assert "备注内容 XYZ" in html
    # 数据 JSON 内嵌（导出件为客户端渲染：内容由内嵌 JSON 生成）
    assert 'id="report-data"' in html
    payload = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', html, re.S).group(1))
    # 新增板块的数据都在内嵌快照里：对齐汇总、可比性提醒、运行参数快照、自动口径说明
    assert "alignment" in payload and "unmapped_count" in payload["alignment"]
    assert "comparable_notes" in payload
    assert payload["run_configs"] and payload["run_configs"][0]["run_id"] in {run_a, baseline_run}
    assert payload["caliber_notes"]
    # 原始样本明细随导出，供「展开原始样本」使用
    assert payload["models"][0]["combos"][0]["samples"]
    assert "raw_models" in payload["models"][0] and "model_key" in payload["models"][0]
    # 零外部资源引用：无 script src / link href / img src
    assert "<script src" not in html
    assert "<link" not in html
    assert "<img" not in html
    assert not re.search(r'(?:src|href)="https?://', html)
    # 基准区分样式存在
    assert "baseline-row" in html or "基准" in html


async def test_export_freezes_data(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """导出后数据冻结：新增样本不影响已导出文件内容（文件是导出时刻的快照）。"""
    from sqlalchemy import select

    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "m1"),),
        samples={(70, "m1"): [{"seq": 1, "ttft_ms": 1200, "total_ms": 2400}]},
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="冻结测试", run_ids=[run_a]))
    exported = await _svc(fake_auth, eval_db, "export_html", created.id)
    assert "1200" in exported  # 导出时刻的数据快照

    # 之后产生新样本数据（ttft 7777，带可识别数值）
    async with eval_db() as session, session.begin():
        combo = (await session.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == run_a))).scalars().one()
        session.add(ChannelEvalSampleModel(combo_id=combo.id, seq=2, status="success", ttft_ms=45000, total_ms=46000))

    # 已导出文件不变：不含新数据（文件内容为导出时刻冻结的快照；45s 样本会进异常清单显示原值）
    assert "45000" not in exported
    assert "1200" in exported

    # 重新导出才包含新数据（Web 视图实时，文件导出即冻结）
    re_exported = await _svc(fake_auth, eval_db, "export_html", created.id)
    assert "45000" in re_exported


# ─────────────────────── 模型映射驱动的分组与对齐 ───────────────────────


async def _make_sibling_runs(eval_db: async_sessionmaker) -> tuple[int, int]:
    """两渠道各跑一条同族但命名不同的串（claude-opus-5-5 / anthropic/claude-opus-5.5）。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")
    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "claude-opus-5-5"),),
        samples={(70, "claude-opus-5-5"): [{"seq": 1, "ttft_ms": 1200, "total_ms": 2400}]},
    )
    run_b = await _make_run(
        eval_db,
        aihubmix_id,
        combos=((72, "ch72", "anthropic/claude-opus-5.5"),),
        samples={(72, "anthropic/claude-opus-5.5"): [{"seq": 1, "ttft_ms": 1100, "total_ms": 2300}]},
    )
    return run_a, run_b


async def test_report_grouping_with_confirmed_alias(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """已确认别名：两渠道的不同命名串归到同一规范模型组，并标注命名差异。"""
    canonical = await _map_svc(
        fake_auth,
        eval_db,
        "create",
        CanonicalModelCreateSchema(display_name="Claude Opus 5.5", family="Claude", aliases=["claude-opus-5-5", "anthropic/claude-opus-5.5"]),
    )
    run_a, run_b = await _make_sibling_runs(eval_db)

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="对齐分组", run_ids=[run_a, run_b]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert len(view.models) == 1, "已确认别名的两条串应归为同一组"
    group = view.models[0]
    assert group.model == "Claude Opus 5.5", "组名应为规范模型显示名"
    assert group.model_key == f"c{canonical.id}"
    assert group.family == "Claude"
    assert group.alignment == "aligned"
    assert group.raw_models == ["anthropic/claude-opus-5.5", "claude-opus-5-5"], "raw_models 应含组内两条原始串"
    assert {combo.channel_id for combo in group.combos} == {70, 72}, "两渠道的组合应并列在同一组"
    assert group.alignment_note is not None
    assert "组内渠道模型串不同" in group.alignment_note, "应说明组内命名差异"
    assert "claude-opus-5-5" in group.alignment_note and "anthropic/claude-opus-5.5" in group.alignment_note
    assert view.alignment.unmapped_count == 0


async def test_report_grouping_without_alias_stays_split(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """未确认归属：同族串绝不合并，各自独立成组并标记 unmapped（建议待确认）。"""
    run_a, run_b = await _make_sibling_runs(eval_db)

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="未对齐分组", run_ids=[run_a, run_b]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert len(view.models) == 2, "未确认归属的同族串不得合并"
    assert {group.model for group in view.models} == {"claude-opus-5-5", "anthropic/claude-opus-5.5"}
    assert {group.alignment for group in view.models} == {"unmapped"}, "两组都应标记为待确认归属"
    assert view.alignment.unmapped_count == 2
    assert view.alignment.mappings == []
    # 每组说明里点出疑似同族的另一条串
    for group in view.models:
        assert group.alignment_note is not None
        assert "疑似与" in group.alignment_note and "尚未确认归属" in group.alignment_note


async def test_report_grouping_singleton(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """孤串：未发现同族 → singleton（既非已对齐，也无待确认的建议）。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "gpt-6-sol"),),
        samples={(70, "gpt-6-sol"): [{"seq": 1, "ttft_ms": 800, "total_ms": 1600}]},
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="孤串分组", run_ids=[run_a]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert len(view.models) == 1
    group = view.models[0]
    assert group.model == "gpt-6-sol", "未确认归属时组名即渠道模型串本身"
    assert group.model_key == "raw:gpt-6-sol"
    assert group.alignment == "singleton"
    assert group.raw_models == ["gpt-6-sol"]
    assert group.alignment_note is None, "无同族也无命名差异，无需说明"
    # singleton 没有待办，不计入「疑似同族待确认」——否则一份含大量独有模型的报告
    # 会喊出同样多的「未确认归属」，纯噪音
    assert view.alignment.unmapped_count == 0
    assert not any("疑似同族未确认" in note for note in view.caliber_notes)


async def test_report_alignment_mappings_list_only_used(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """对齐汇总只列本次报告真的用到的映射，不把整张别名表倒出来。"""
    await _map_svc(
        fake_auth,
        eval_db,
        "create",
        CanonicalModelCreateSchema(display_name="Claude Opus 5.5", aliases=["claude-opus-5-5", "anthropic/claude-opus-5.5"]),
    )
    await _map_svc(
        fake_auth,
        eval_db,
        "create",
        CanonicalModelCreateSchema(display_name="GPT-6 Sol", aliases=["openai/gpt-6-sol", "gpt-6-sol"]),
    )
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    run_a = await _make_run(
        eval_db,
        apiworld_id,
        combos=((70, "ch70", "claude-opus-5-5"),),
        samples={(70, "claude-opus-5-5"): [{"seq": 1, "ttft_ms": 1200, "total_ms": 2400}]},
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="映射清单", run_ids=[run_a]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert [(item.alias, item.canonical) for item in view.alignment.mappings] == [("claude-opus-5-5", "Claude Opus 5.5")], "只列本次用到的映射"


async def test_caliber_notes_reflect_effective_mappings(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """口径说明：未用别名时声明按原样分组；补一条别名后同一报告立刻列出实际生效的映射。"""
    run_a, run_b = await _make_sibling_runs(eval_db)
    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="口径说明", run_ids=[run_a, run_b]))

    before = await _svc(fake_auth, eval_db, "view", created.id)
    joined_before = "\n".join(before.caliber_notes)
    assert "未用到任何已确认的别名" in joined_before, "无别名时应声明按原样分组"
    assert "→" not in joined_before

    # 解析发生在渲染时：补一条别名，历史报告立刻正确
    await _map_svc(
        fake_auth,
        eval_db,
        "create",
        CanonicalModelCreateSchema(display_name="Claude Opus 5.5", aliases=["claude-opus-5-5", "anthropic/claude-opus-5.5"]),
    )
    after = await _svc(fake_auth, eval_db, "view", created.id)
    joined_after = "\n".join(after.caliber_notes)
    assert "未用到任何已确认的别名" not in joined_after
    assert "claude-opus-5-5 → Claude Opus 5.5" in joined_after, "应列出实际生效的映射关系"
    assert "anthropic/claude-opus-5.5 → Claude Opus 5.5" in joined_after


# ─────────────────────── 可比性提醒与运行配置 ───────────────────────

_BASE_PARAMS: dict = {"request_count": 2, "warmup": False, "interval_seconds": 0, "timeout_seconds": 10, "prompt": "hi", "anomaly_threshold_seconds": 30}


async def test_comparable_notes_warn_on_param_difference_without_advice(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """热身开关不同 → warning，仅陈述差异事实，不含建议性措辞。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")
    run_a = await _make_run_with_params(eval_db, apiworld_id, params=_BASE_PARAMS)
    run_b = await _make_run_with_params(eval_db, aihubmix_id, params={**_BASE_PARAMS, "warmup": True})

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="可比性-热身", run_ids=[run_a, run_b]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert [note.level for note in view.comparable_notes] == ["warning"], "只有热身开关不同，应只产出一条警示"
    content = view.comparable_notes[0].content
    assert "热身不同" in content
    assert "开启" in content and "关闭" in content
    for note in view.comparable_notes:
        assert "建议" not in note.content, "可比性提醒不得含建议性措辞"


async def test_comparable_notes_empty_when_params_identical(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """参数与时间窗完全一致 → 无可比性提醒。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")
    run_a = await _make_run_with_params(eval_db, apiworld_id, params=_BASE_PARAMS)
    run_b = await _make_run_with_params(eval_db, aihubmix_id, params=dict(_BASE_PARAMS))

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="可比性-一致", run_ids=[run_a, run_b]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert view.comparable_notes == [], "参数一致时不应有可比性提醒"


async def test_comparable_notes_warn_on_request_count(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """每组合正式次数不同 → warning。"""
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    aihubmix_id = await _make_platform(eval_db, name="aihubmix", baseline=True, ptype="user")
    run_a = await _make_run_with_params(eval_db, apiworld_id, params=_BASE_PARAMS)
    run_b = await _make_run_with_params(eval_db, aihubmix_id, params={**_BASE_PARAMS, "request_count": 5})

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="可比性-次数", run_ids=[run_a, run_b]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert [note.level for note in view.comparable_notes] == ["warning"]
    content = view.comparable_notes[0].content
    assert "每组合正式次数不同" in content
    assert "建议" not in content


async def test_run_configs_carry_params_and_execution_counts(eval_db: async_sessionmaker, fake_auth: AuthSchema) -> None:
    """运行配置：参数取自 params_json（热身只有开关、不得虚构次数）与成功/失败/异常计数。"""
    params = {"request_count": 3, "warmup": True, "interval_seconds": 0.5, "timeout_seconds": 20, "anomaly_threshold_seconds": 30, "prompt": "hi"}
    apiworld_id = await _make_platform(eval_db, name="apiworld", baseline=False, ptype="admin")
    run_a = await _make_run_with_params(
        eval_db,
        apiworld_id,
        params=params,
        combos=((70, "ch70", "m1"),),
        samples={
            (70, "m1"): [
                {"seq": 0, "is_warmup": True, "ttft_ms": 500, "total_ms": 1000},  # 热身：只开关，不计入计数
                {"seq": 1, "ttft_ms": 1000, "total_ms": 2000},
                {"seq": 2, "ttft_ms": 45000, "total_ms": 46000},  # 超 30s 阈值 → 异常
                {"seq": 3, "status": "failed", "error": "timeout", "ttft_ms": None, "total_ms": None},
            ]
        },
    )

    created = await _svc(fake_auth, eval_db, "create", ChannelEvalReportCreateSchema(name="运行配置", run_ids=[run_a]))
    view = await _svc(fake_auth, eval_db, "view", created.id)

    assert len(view.run_configs) == 1
    config = view.run_configs[0]
    assert config.run_id == run_a
    assert config.platform_name == "apiworld"
    assert config.status == "success"
    assert config.combo_count == 1
    assert config.request_count == 3
    assert config.warmup is True, "热身是布尔开关，不得被虚构为次数"
    assert config.interval_seconds == 0.5
    assert config.timeout_seconds == 20
    assert config.anomaly_threshold_seconds == 30
    assert config.prompt == "hi"
    assert config.success == 2, "成功计数剔除热身样本"
    assert config.failed == 1
    assert config.anomaly == 1
