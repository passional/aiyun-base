"""执行引擎直测（任务 4.2~4.6、5.1~5.3）

以独立 sqlite（eval_db）+ monkeypatch 替换引擎模块的会话工厂与外部依赖：
- 4.2 状态机 pending→running→success/failed/cancelled、组合边界取消、顺序请求+间隔
- 4.3 客户端实测采集口径（假 SSE 流：TTFT/总耗时/tps/request_id/失败样本）
- 4.4 指定渠道（pin）接线（评估令牌解析成功/失败分支）
- 4.5 进度写 Redis
- 4.6 启动恢复钩子
- 5.1 运行结束后日志拉取（时间窗 + 令牌名 + 分页拉全）
- 5.2 request_id 1:1 配对（成功/缺失/重复）
- 5.3 组合聚合（异常口径：45s 样本在 30s 阈值下剔除）
"""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.modules.channel_eval.run import engine as engine_mod
from app.modules.channel_eval.run.aggregation import aggregate_combo
from app.modules.channel_eval.run.model import (
    ChannelEvalRunComboModel,
    ChannelEvalRunModel,
    ChannelEvalSampleModel,
)
from app.modules.channel_eval.run.progress import read_progress
from app.modules.channel_eval.run.provision import ProvisionError
from app.modules.channel_eval.run.registry import run_task_registry
from app.utils.crypto_util import CryptoUtil


def _draft(**overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "request_id": f"req-{uuid4().hex[:8]}",
        "ttft_ms": 500,
        "total_ms": 1500,
        "tokens_in": 10,
        "tokens_out": 20,
        "tps": 20.0,
        "status": "success",
        "error": None,
        "started_at": datetime.now(UTC),
    }
    draft.update(overrides)
    return draft


async def _setup_run(
    db_maker: async_sessionmaker,
    *,
    platform_type: str = "user",
    combos: tuple[tuple[int, str, str], ...] = ((70, "ch70", "m1"),),
    params: dict[str, Any] | None = None,
    run_status: str = "pending",
) -> tuple[int, int, list[int]]:
    """插入平台 + 运行 + 组合，返回 (platform_id, run_id, combo_ids)。"""
    merged: dict[str, Any] = {
        "request_count": 2,
        "warmup": True,
        "interval_seconds": 0,
        "timeout_seconds": 10,
        "prompt": "hi",
        "anomaly_threshold_seconds": 30,
    }
    merged.update(params or {})
    async with db_maker() as db, db.begin():
        platform = ChannelEvalPlatformModel(
            name="p",
            type=platform_type,
            base_url="https://p.example.com",
            test_key_enc=CryptoUtil.encrypt("sk-user"),
            admin_key_enc=CryptoUtil.encrypt("sk-admin") if platform_type == "admin" else None,
        )
        db.add(platform)
        await db.flush()
        run = ChannelEvalRunModel(platform_id=platform.id, status=run_status, params_json=merged)
        if run_status in ("running", "success", "cancelled"):
            run.started_at = datetime.now(UTC)
        db.add(run)
        await db.flush()
        combo_ids: list[int] = []
        for channel_id, channel_name, model in combos:
            combo = ChannelEvalRunComboModel(
                run_id=run.id,
                channel_id=channel_id,
                channel_name=channel_name,
                model=model,
                planned=merged["request_count"],
            )
            db.add(combo)
            await db.flush()
            combo_ids.append(combo.id)
        return platform.id, run.id, combo_ids


async def _fetch_run(db_maker: async_sessionmaker, run_id: int) -> ChannelEvalRunModel:
    async with db_maker() as db:
        return await db.get(ChannelEvalRunModel, run_id)


async def _fetch_combos(db_maker: async_sessionmaker, run_id: int) -> list[ChannelEvalRunComboModel]:
    async with db_maker() as db:
        result = await db.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == run_id).order_by(ChannelEvalRunComboModel.id))
        return list(result.scalars().all())


async def _fetch_samples(db_maker: async_sessionmaker, run_id: int) -> list[ChannelEvalSampleModel]:
    async with db_maker() as db:
        result = await db.execute(
            select(ChannelEvalSampleModel)
            .join(ChannelEvalRunComboModel, ChannelEvalSampleModel.combo_id == ChannelEvalRunComboModel.id)
            .where(ChannelEvalRunComboModel.run_id == run_id)
            .order_by(ChannelEvalSampleModel.id)
        )
        return list(result.scalars().all())


@pytest.fixture
async def engine_env(eval_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch):
    """引擎直测环境：会话工厂指向独立库、日志拉取延迟清零。"""
    monkeypatch.setattr(engine_mod, "async_db_session", eval_db)
    monkeypatch.setattr(engine_mod, "LOG_PULL_DELAY_SECONDS", 0)
    yield engine_mod


# ─────────────────────── 4.2 状态机与顺序执行 ───────────────────────


async def test_full_run_state_machine_and_sequence(engine_env, eval_db: async_sessionmaker) -> None:
    """假 LLM 流跑完整运行：pending→running→success；热身先行；同组合顺序请求。"""
    _, run_id, _ = await _setup_run(eval_db, combos=((70, "ch70", "m1"), (72, "ch72", "m2")), params={"request_count": 2, "warmup": True})
    observed: list[tuple[int, int]] = []  # (combo 执行时的样本序号, 运行状态)

    async def fake_measure(base_url, token_key, model, params, **kwargs):
        async with eval_db() as db:
            run = await db.get(ChannelEvalRunModel, run_id)
            observed.append((len(observed) + 1, run.status))
        return _draft()

    with (
        patch.object(engine_env, "_measure_once", new=fake_measure),
        patch.object(engine_env, "_pull_and_pair_logs", new=AsyncMock()),
    ):
        await engine_env._execute_run(run_id)

    run = await _fetch_run(eval_db, run_id)
    assert run.status == "success"
    assert run.started_at is not None and run.finished_at is not None
    assert run.error is None
    # 执行期间运行状态始终为 running（状态机）
    assert all(status == "running" for _, status in observed)
    # 2 组合 × (1 热身 + 2 正式) = 6 样本
    samples = await _fetch_samples(eval_db, run_id)
    assert len(samples) == 6
    warmup = [s for s in samples if s.is_warmup]
    assert len(warmup) == 2  # 每组合一次热身
    assert all(s.seq == 0 for s in warmup)
    assert all(s.status == "success" for s in samples)
    combos = await _fetch_combos(eval_db, run_id)
    assert all(c.status == "success" for c in combos)
    assert all((c.planned, c.success, c.failed, c.anomaly) == (2, 2, 0, 0) for c in combos)


async def test_cancel_semantics(engine_env, eval_db: async_sessionmaker) -> None:
    """取消：停止后续请求，状态 cancelled，已完成样本保留。"""
    _, run_id, _ = await _setup_run(eval_db, params={"request_count": 3, "warmup": False})
    observed: list[int] = []

    async def counting_measure(base_url, token_key, model, params, **kwargs):
        observed.append(1)
        # 第 1 次请求完成后取消 → 引擎应在第 2 次请求前停止
        if len(observed) == 1:
            run_task_registry.mark_cancel(run_id)
        return _draft()

    with (
        patch.object(engine_env, "_measure_once", new=counting_measure),
        patch.object(engine_env, "_pull_and_pair_logs", new=AsyncMock()),
    ):
        await engine_env._execute_run(run_id)

    run = await _fetch_run(eval_db, run_id)
    assert run.status == "cancelled"
    samples = await _fetch_samples(eval_db, run_id)
    assert len(samples) == 1  # 后续请求已停止，已产生样本保留
    combos = await _fetch_combos(eval_db, run_id)
    assert combos[0].status == "failed"  # 未完成的组合收敛为 failed
    assert run_task_registry.is_canceled(run_id) is False  # finally 清理


async def test_run_fails_on_engine_exception(engine_env, eval_db: async_sessionmaker) -> None:
    """引擎异常 → 运行 failed 且 error 记录原因。"""
    _, run_id, _ = await _setup_run(eval_db, params={"warmup": False})

    async def boom(base_url, token_key, model, params, **kwargs):
        raise RuntimeError("boom")

    with (
        patch.object(engine_env, "_measure_once", new=boom),
        patch.object(engine_env, "_pull_and_pair_logs", new=AsyncMock()),
    ):
        await engine_env._execute_run(run_id)
    run = await _fetch_run(eval_db, run_id)
    assert run.status == "failed"
    assert "boom" in (run.error or "")


# ─────────────────────── 4.3 客户端实测采集口径 ───────────────────────


class _HttpxShim:
    """把 engine 模块里的 httpx.AsyncClient 替换为 MockTransport 版本。"""

    def __init__(self, handler) -> None:
        self._handler = handler

    def AsyncClient(self, timeout=None, trust_env=False, **kwargs):  # noqa: N802
        return httpx.AsyncClient(timeout=timeout, transport=httpx.MockTransport(self._handler), trust_env=False)


_SSE_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
    'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":8}}\n\n'
    "data: [DONE]\n\n"
)


async def test_measure_once_success_metrics(engine_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功流式请求：TTFT=首内容 delta、总耗时=至 [DONE]、tps、request_id 响应头。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        # 指定渠道（pin）：请求头拼 Bearer <key>-<渠道ID>
        assert request.headers["Authorization"] == "Bearer sk-x-70"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"]["include_usage"] is True
        assert body["model"] == "m1"
        return httpx.Response(200, headers={"X-Oneapi-Request-Id": "req-1"}, content=_SSE_BODY.encode())

    class _FakeClock:
        """每次 perf_counter 调用推进 0.1s，使 TTFT/总耗时确定可比（100ms/200ms）"""

        def __init__(self) -> None:
            self._now = 1000.0

        def perf_counter(self) -> float:
            current = self._now
            self._now += 0.1
            return current

    monkeypatch.setattr(engine_env, "time", clock := _FakeClock())
    monkeypatch.setattr(engine_env, "httpx", _HttpxShim(handler))
    draft = await engine_env._measure_once(
        "https://p.example.com", "sk-x", "m1", {"prompt": "hi", "timeout_seconds": 10}, channel_id=70, pinned=True
    )
    assert draft["status"] == "success"
    assert draft["request_id"] == "req-1"
    assert draft["tokens_in"] == 10
    assert draft["tokens_out"] == 8
    # 假时钟：t0 后首内容 delta +100ms，[DONE] 后 +200ms
    assert draft["ttft_ms"] == 100
    assert draft["total_ms"] == 200
    # tps = 输出 tokens ÷ (总耗时 − TTFT) = 8 ÷ 0.1s = 80
    assert draft["tps"] == 80.0
    assert clock


async def test_measure_once_failure_records_error(engine_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """失败样本：记错误信息，标记 failed。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "upstream unavailable"}})

    monkeypatch.setattr(engine_env, "httpx", _HttpxShim(handler))
    draft = await engine_env._measure_once(
        "https://p.example.com", "sk-x", "m1", {"prompt": "hi", "timeout_seconds": 10}, channel_id=70, pinned=False
    )
    assert draft["status"] == "failed"
    assert "503" in (draft["error"] or "")
    assert draft["tps"] is None


# ─────────────────────── 4.4 指定渠道（pin）接线 ───────────────────────


async def test_provision_wiring_success_and_failure(engine_env, eval_db: async_sessionmaker) -> None:
    """admin 平台：评估令牌解析成功的组合正常执行；解析失败组合标记失败并继续其余组合。"""
    _, run_id, _ = await _setup_run(
        eval_db,
        platform_type="admin",
        combos=((70, "ch70", "m1"), (72, "ch72", "m2")),
        params={"request_count": 1, "warmup": False},
    )

    # 第 1 个组合（#70）解析失败；第 2 个组合（#72）成功
    calls = {"n": 0}

    async def fake_resolve(db, platform, run):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProvisionError("评估令牌准备失败: 平台管理接口不可达")
        return "tok-run"

    with (
        patch.object(engine_env, "resolve_eval_token_key", new=fake_resolve),
        patch.object(engine_env, "_measure_once", new=AsyncMock(return_value=_draft())),
        patch.object(engine_env, "_pull_and_pair_logs", new=AsyncMock()),
    ):
        await engine_env._execute_run(run_id)

    run = await _fetch_run(eval_db, run_id)
    assert run.status == "success"  # 单组合令牌解析失败不影响整体执行完成
    combos = {c.channel_id: c for c in await _fetch_combos(eval_db, run_id)}
    assert combos[70].status == "failed"  # 第 1 组合令牌解析失败
    assert "评估令牌准备失败" in (combos[70].error or "")
    assert combos[72].status == "success"  # 第 2 组合解析成功并正常执行
    samples = await _fetch_samples(eval_db, run_id)
    assert all(s.combo_id == combos[72].id for s in samples)  # 失败组合未发请求


# ─────────────────────── 4.5 进度写 Redis ───────────────────────


async def test_progress_written_to_redis(engine_env, eval_db: async_sessionmaker) -> None:
    """执行期间进度写 Redis（断线重连以此为准）。"""
    _, run_id, _ = await _setup_run(eval_db, params={"request_count": 1, "warmup": False})
    with (
        patch.object(engine_env, "_measure_once", new=AsyncMock(return_value=_draft())),
        patch.object(engine_env, "_pull_and_pair_logs", new=AsyncMock()),
    ):
        await engine_env._execute_run(run_id)
    progress = await read_progress(engine_env._get_redis(), run_id)
    assert progress is not None
    assert progress["run_id"] == run_id
    assert progress["status"] == "success"
    assert progress["total_combos"] == 1


# ─────────────────────── 4.6 启动恢复钩子 ───────────────────────


async def test_recover_interrupted_runs(engine_env, eval_db: async_sessionmaker) -> None:
    """进程启动：running/cancelling → failed（error=进程重启），样本保留。"""
    _, running_run_id, running_combo_ids = await _setup_run(eval_db, run_status="running")
    running_combo_id = running_combo_ids[0]
    _, cancelling_run_id, _ = await _setup_run(eval_db, run_status="cancelling")
    async with eval_db() as db, db.begin():
        db.add(ChannelEvalSampleModel(combo_id=running_combo_id, seq=1, status="success"))
        combo = await db.get(ChannelEvalRunComboModel, running_combo_id)
        combo.status = "running"

    recovered = await engine_env.recover_interrupted_runs()
    assert recovered == 2

    run = await _fetch_run(eval_db, running_run_id)
    assert run.status == "failed"
    assert "进程重启" in (run.error or "")
    assert run.finished_at is not None
    samples = await _fetch_samples(eval_db, running_run_id)
    assert len(samples) == 1  # 已产生样本保留

    cancelling = await _fetch_run(eval_db, cancelling_run_id)
    assert cancelling.status == "failed"

    combos = await _fetch_combos(eval_db, running_run_id)
    assert combos[0].status == "failed"  # 卡在 running 的组合收敛


# ─────────────────────── 5.1 + 5.2 日志拉取与配对 ───────────────────────


async def test_pull_and_pair_logs_admin(engine_env, eval_db: async_sessionmaker) -> None:
    """管理级平台：按 token_name + 时间窗拉取；配对成功/缺失/重复三种情况。"""
    _platform_id, run_id, combo_ids = await _setup_run(eval_db, platform_type="admin", params={"request_count": 3, "warmup": False})
    combo_id = combo_ids[0]
    # 直接构造已完成运行 + 4 个样本：r-1 正常、r-2 后台缺失、r-3 后台重复、r-4 无 request_id
    async with eval_db() as db, db.begin():
        run = await db.get(ChannelEvalRunModel, run_id)
        run.status = "success"
        run.started_at = datetime.now(UTC)
        run.finished_at = datetime.now(UTC)
        for seq, rid in enumerate(["r-1", "r-2", "r-3", None], start=1):
            db.add(
                ChannelEvalSampleModel(
                    combo_id=combo_id,
                    seq=seq,
                    status="success",
                    request_id=rid,
                    ttft_ms=100,
                    total_ms=900,
                )
            )

    fake_client = MagicMock()
    list_logs = AsyncMock(
        return_value=[
            # r-1 正常记录
            {"request_id": "r-1", "use_time": 1, "other": '{"frt": 800}', "prompt_tokens": 10, "completion_tokens": 20, "quota": 100, "channel_id": 70},
            # r-3 重复（两条，取首条 frt=700）
            {"request_id": "r-3", "use_time": 2, "other": '{"frt": 700}', "prompt_tokens": 1, "completion_tokens": 2, "quota": 3, "channel_id": 70},
            {"request_id": "r-3", "use_time": 9, "other": '{"frt": 9000}', "prompt_tokens": 9, "completion_tokens": 9, "quota": 9, "channel_id": 99},
        ]
    )
    fake_client.list_logs = list_logs

    with patch.object(engine_env, "build_client", return_value=fake_client):
        await engine_env._pull_and_pair_logs(run_id)

    # list_logs 以本运行专用评估令牌名（eval-run{运行ID}）+ 运行时间窗调用
    args = list_logs.call_args[0]
    assert args[0] == f"eval-run{run_id}"
    assert isinstance(args[1], int) and isinstance(args[2], int)

    samples = {s.request_id or f"no-rid-{s.seq}": s for s in await _fetch_samples(eval_db, run_id)}
    # 配对成功
    s1 = samples["r-1"]
    assert s1.be_matched is True
    assert s1.be_frt_ms == 800
    assert s1.be_use_time_ms == 1000
    assert s1.be_tokens_in == 10 and s1.be_tokens_out == 20
    assert s1.be_cost == 100 and s1.be_channel_id == 70
    # 后台缺失 → 标注「后台无记录」（be_matched=False，客户端数据不受影响）
    s2 = samples["r-2"]
    assert s2.be_matched is False
    assert s2.be_frt_ms is None and s2.be_use_time_ms is None
    assert s2.ttft_ms == 100  # 客户端实测不受影响
    # 重复 request_id → 取首条
    s3 = samples["r-3"]
    assert s3.be_matched is True
    assert s3.be_frt_ms == 700
    assert s3.be_channel_id == 70
    # 无 request_id → 无法配对
    s4 = samples["no-rid-4"]
    assert s4.be_matched is False


async def test_pull_and_pair_logs_baseline_user(engine_env, eval_db: async_sessionmaker) -> None:
    """基准渠道（user 平台）走 list_self_logs + 时间窗。"""
    _platform_id, run_id, combo_ids = await _setup_run(eval_db, platform_type="user", params={"request_count": 1, "warmup": False})
    combo_id = combo_ids[0]
    async with eval_db() as db, db.begin():
        run = await db.get(ChannelEvalRunModel, run_id)
        run.status = "success"
        run.started_at = datetime.now(UTC)
        run.finished_at = datetime.now(UTC)
        db.add(ChannelEvalSampleModel(combo_id=combo_id, seq=1, status="success", request_id="self-1"))

    fake_client = MagicMock()
    list_self_logs = AsyncMock(return_value=[{"request_id": "self-1", "use_time": 3, "other": '{"frt": 2500}', "channel_id": 0}])
    fake_client.list_self_logs = list_self_logs

    with patch.object(engine_env, "NewApiClient", return_value=fake_client):
        await engine_env._pull_and_pair_logs(run_id)

    assert list_self_logs.await_count == 1
    samples = await _fetch_samples(eval_db, run_id)
    assert samples[0].be_matched is True
    assert samples[0].be_frt_ms == 2500
    assert samples[0].be_use_time_ms == 3000


async def test_pull_logs_pagination(engine_env, eval_db: async_sessionmaker) -> None:
    """分页拉全：跨页记录全部取回参与配对。"""
    _platform_id, run_id, combo_ids = await _setup_run(eval_db, platform_type="user", params={"request_count": 2, "warmup": False})
    combo_id = combo_ids[0]
    async with eval_db() as db, db.begin():
        run = await db.get(ChannelEvalRunModel, run_id)
        run.status = "success"
        run.started_at = datetime.now(UTC)
        run.finished_at = datetime.now(UTC)
        for seq in (1, 2):
            db.add(ChannelEvalSampleModel(combo_id=combo_id, seq=seq, status="success", request_id=f"pg-{seq}"))

    page = {"n": 0}
    records_page1 = [{"request_id": "pg-1", "use_time": 1, "other": '{"frt": 100}'}] * 100
    records_page2 = [{"request_id": "pg-2", "use_time": 2, "other": '{"frt": 200}'}]

    # 用真实 NewApiClient 的分页逻辑（_paginate），仅 mock 底层 HTTP GET
    from app.modules.channel_eval.run.new_api_client import NewApiClient

    client = NewApiClient("https://p.example.com", test_key="sk-user")

    async def fake_user_get(path, params=None):
        page["n"] += 1
        if params and params.get("p") == 1:
            # 第一页 100 条（== PAGE_SIZE → 继续翻页），全局 total 101
            return {"items": records_page1, "total": 101}
        return records_page2  # 第二页 list 形态（旧版兼容）

    with (
        patch.object(client, "_user_get", new=fake_user_get),
        patch.object(engine_env, "NewApiClient", return_value=client),
    ):
        await engine_env._pull_and_pair_logs(run_id)
    assert page["n"] == 2  # 翻页拉全

    samples = {s.request_id: s for s in await _fetch_samples(eval_db, run_id)}
    assert samples["pg-1"].be_matched is True
    assert samples["pg-2"].be_matched is True  # 第二页记录也拉全并配对


# ─────────────────────── 5.3 组合聚合（异常口径） ───────────────────────


async def test_aggregate_run_anomaly_threshold(engine_env, eval_db: async_sessionmaker) -> None:
    """spec 异常口径：45s 样本在 30s 阈值下被剔除；含异常参考均值并列；写回 combo 计数。"""
    _, run_id, combo_ids = await _setup_run(eval_db, params={"request_count": 3, "warmup": True})
    combo_id = combo_ids[0]
    async with eval_db() as db, db.begin():
        run = await db.get(ChannelEvalRunModel, run_id)
        run.status = "success"
        run.started_at = datetime.now(UTC)
        run.finished_at = datetime.now(UTC)
        # 热身（不计统计）
        db.add(ChannelEvalSampleModel(combo_id=combo_id, seq=0, is_warmup=True, status="success", ttft_ms=100, total_ms=200))
        # 正常成功样本
        db.add(ChannelEvalSampleModel(combo_id=combo_id, seq=1, status="success", ttft_ms=1000, total_ms=2000, tokens_out=10))
        # 异常样本：45s 超过 30s 阈值
        db.add(ChannelEvalSampleModel(combo_id=combo_id, seq=2, status="success", ttft_ms=45000, total_ms=46000))
        # 失败样本
        db.add(ChannelEvalSampleModel(combo_id=combo_id, seq=3, status="failed", error="timeout", ttft_ms=None, total_ms=None))

    await engine_env._aggregate_run(run_id)

    combo = (await _fetch_combos(eval_db, run_id))[0]
    assert combo.planned == 3
    assert combo.success == 2  # 正常 + 异常（异常属于成功样本）
    assert combo.failed == 1
    assert combo.anomaly == 1

    # 聚合口径：剔除热身/异常/失败
    async with eval_db() as db:
        combo_obj = await db.get(ChannelEvalRunComboModel, combo_id)
        samples = (await db.execute(select(ChannelEvalSampleModel).where(ChannelEvalSampleModel.combo_id == combo_id))).scalars().all()
    aggregated = aggregate_combo(combo_obj, list(samples), threshold_ms=30 * 1000)
    assert aggregated.ttft_avg_ms == 1000  # 仅正常样本
    assert aggregated.total_avg_ms == 2000
    # 含异常参考均值 = (1000 + 45000) / 2
    assert aggregated.ttft_with_anomaly_avg_ms == 23000
    assert aggregated.total_with_anomaly_avg_ms == 24000
    # 成功率 = 2/3
    assert aggregated.success_rate == round(2 / 3, 4)
