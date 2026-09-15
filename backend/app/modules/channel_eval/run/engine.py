"""渠道评估 - 测试运行执行引擎（设计 D4/D5）

- asyncio 后台任务执行（请求事务提交后经 after_commit 启动，读不到未提交行）
- 状态机 pending→running→success/failed/cancelled（cancelling 为取消过渡态）
- 进程内任务注册表支撑取消：取消置 cancelling，任务在组合边界与请求边界检查退出
- 同组合顺序请求 + 间隔；热身先行且 is_warmup=true
- 运行结束（含 cancelled/failed 的已测部分）延迟约 5s 拉全后台日志，按 request_id
  1:1 配对写回 be_*，未配对样本 be_matched=false（后台无记录）
- 聚合剔除热身/异常/失败样本，写回 combo 计数

后台任务使用独立 DB 会话（async_db_session），不阻塞请求。
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.config.setting import settings
from app.core.database import async_db_session
from app.core.logger import logger
from app.modules.channel_eval.platform.model import ChannelEvalPlatformModel
from app.utils.crypto_util import CryptoUtil

from .aggregation import is_anomaly
from .log_parser import parse_log_record
from .model import ChannelEvalRunComboModel, ChannelEvalRunModel, ChannelEvalSampleModel
from .new_api_client import NewApiClient
from .progress import write_progress
from .provision import ProvisionError, build_client, eval_run_token_name, resolve_eval_token_key
from .registry import run_task_registry
from .sse_manager import channel_eval_run_manager

# 后台任务强引用集合：防止 asyncio.Task 被垃圾回收静默取消
_BG_TASKS: set[asyncio.Task] = set()

# 运行结束后等待后台日志落库的延迟（设计 D5：约 5s）
LOG_PULL_DELAY_SECONDS = 5.0
# 日志拉取时间窗前后缓冲（秒）
LOG_WINDOW_BUFFER_SECONDS = 60
# 进度 Redis 键有效期（7 天，供断线重连读取终态）
_PROGRESS_TTL = 7 * 24 * 3600

_redis_client: Any = None


def _get_redis() -> Any:
    """引擎专用 Redis 连接（懒加载单例；测试环境 from_url 已被 mock）。"""
    global _redis_client
    if _redis_client is None:
        from redis.asyncio import Redis

        _redis_client = Redis.from_url(settings.REDIS_URI, encoding="utf-8", decode_responses=True)
    return _redis_client


def start_run_engine(run_id: int) -> asyncio.Task:
    """启动运行的后台执行任务。"""
    task = asyncio.create_task(_execute_run(run_id), name=f"channel-eval-run-{run_id}")
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def recover_interrupted_runs() -> int:
    """启动恢复钩子：进程启动时将 running/cancelling 的运行置 failed（error=进程重启）。

    同时把卡在 running 的组合收敛为 failed，保持数据一致；已产生样本保留。
    """
    async with async_db_session() as db, db.begin():
        result = await db.execute(select(ChannelEvalRunModel).where(ChannelEvalRunModel.status.in_(["running", "cancelling"])))
        runs = result.scalars().all()
        for run in runs:
            run.status = "failed"
            run.error = "进程重启，运行中断"
            run.finished_at = datetime.now(UTC)
            combo_result = await db.execute(
                select(ChannelEvalRunComboModel).where(
                    ChannelEvalRunComboModel.run_id == run.id,
                    ChannelEvalRunComboModel.status == "running",
                )
            )
            for combo in combo_result.scalars().all():
                combo.status = "failed"
                if not combo.error:
                    combo.error = "进程重启，运行中断"
        return len(runs)


# ─────────────────────────── 进度推送 ───────────────────────────


async def _push_progress(run_id: int, **fields: Any) -> None:
    """进度写 Redis + SSE 广播（尽力而为，不中断引擎）。"""
    payload: dict[str, Any] = {"run_id": run_id}
    payload.update(fields)
    try:
        await write_progress(_get_redis(), payload, ttl=_PROGRESS_TTL)
    except Exception as e:  # noqa: BLE001
        logger.warning("运行 {} 进度写 Redis 失败（不影响执行）: {}", run_id, e)
    try:
        await channel_eval_run_manager.broadcast({"type": "run_progress", "data": payload})
    except Exception as e:  # noqa: BLE001
        logger.warning("运行 {} 进度 SSE 推送失败（不影响执行）: {}", run_id, e)


# ─────────────────────────── 主流程 ───────────────────────────


async def _execute_run(run_id: int) -> None:
    """执行一次测试运行（独立于请求事务）。"""
    logger.info("渠道评估：运行 {} 开始执行", run_id)
    try:
        async with async_db_session() as db:
            run = await db.get(ChannelEvalRunModel, run_id)
            if run is None:
                logger.warning("渠道评估：运行 {} 不存在，跳过执行", run_id)
                return
            platform = await db.get(ChannelEvalPlatformModel, run.platform_id)
            combos = (await db.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == run_id).order_by(ChannelEvalRunComboModel.id))).scalars().all()
            params: dict[str, Any] = dict(run.params_json or {})
        if platform is None:
            await _finalize_run(run_id, "failed", error="平台不存在")
            return

        # 状态机：pending → running（已是 cancelling 则保持，等组合边界退出）
        async with async_db_session() as db, db.begin():
            run = await db.get(ChannelEvalRunModel, run_id)
            if run is None or run.status not in ("pending", "cancelling"):
                return
            if run.status == "pending":
                run.status = "running"
            run.started_at = datetime.now(UTC)
            run.error = None
        await _push_progress(run_id, status="running", total_combos=len(combos), completed_combos=0, current_combo=None)

        # 逐组合顺序执行（避免对线上平台施加并发压力）
        for index, combo in enumerate(combos):
            # 组合边界取消检查
            if run_task_registry.is_canceled(run_id):
                break
            await _push_progress(
                run_id,
                status="running",
                total_combos=len(combos),
                completed_combos=index,
                current_combo=f"#{combo.channel_id} {combo.channel_name} × {combo.model}",
                current_sample=0,
            )
            await _execute_combo(run_id, platform, combo, params)

        cancelled = run_task_registry.is_canceled(run_id)

        # 后台日志拉取与配对（cancelled/failed 的已测部分同样处理）
        await _push_progress(
            run_id,
            status="running",
            total_combos=len(combos),
            completed_combos=len(combos),
            current_combo=None,
            message="正在拉取平台后台日志并配对…",
        )
        try:
            await _pull_and_pair_logs(run_id)
        except Exception as e:  # noqa: BLE001
            logger.error("运行 {} 后台日志拉取/配对失败（样本保留为「后台无记录」）: {}", run_id, e)

        # 组合聚合（剔除热身/异常/失败样本口径，写回 combo 计数）
        try:
            await _aggregate_run(run_id)
        except Exception as e:  # noqa: BLE001
            logger.error("运行 {} 组合聚合失败: {}", run_id, e)

        final_status = "cancelled" if cancelled else "success"
        await _finalize_run(run_id, final_status, error="已被管理员取消" if cancelled else None)
        await _push_progress(
            run_id,
            status=final_status,
            total_combos=len(combos),
            completed_combos=len(combos),
            current_combo=None,
            message="运行已结束",
        )
        logger.info("渠道评估：运行 {} 执行完成，终态 {}", run_id, final_status)
    except Exception as e:  # noqa: BLE001
        logger.error("渠道评估：运行 {} 执行异常: {}", run_id, e)
        await _finalize_run(run_id, "failed", error=str(e))
        await _push_progress(run_id, status="failed", message=str(e))
    finally:
        run_task_registry.clear(run_id)


async def _finalize_run(run_id: int, status: str, error: str | None = None) -> None:
    async with async_db_session() as db, db.begin():
        run = await db.get(ChannelEvalRunModel, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(UTC)
        run.error = error
        if status != "success":
            # 非成功终态：把卡在 running 的组合收敛为 failed（保留已产生样本）
            combo_result = await db.execute(
                select(ChannelEvalRunComboModel).where(
                    ChannelEvalRunComboModel.run_id == run_id,
                    ChannelEvalRunComboModel.status == "running",
                )
            )
            for combo in combo_result.scalars().all():
                combo.status = "failed"
                if not combo.error:
                    combo.error = error or f"运行{status}，组合未完成"


async def _set_combo_status(combo_id: int, status: str, error: str | None = None) -> None:
    async with async_db_session() as db, db.begin():
        combo = await db.get(ChannelEvalRunComboModel, combo_id)
        if combo is None:
            return
        combo.status = status
        if error is not None:
            combo.error = error


# ─────────────────────────── 单组合执行 ───────────────────────────


async def _execute_combo(run_id: int, platform: ChannelEvalPlatformModel, combo: ChannelEvalRunComboModel, params: dict[str, Any]) -> None:
    """执行单个「渠道 × 模型」组合：解析评估令牌 → 热身 → N 次 pin 请求。

    评估令牌解析失败 → 该组合标记失败并继续其余组合（spec: test-run / 指定渠道执行）。
    """
    await _set_combo_status(combo.id, "running")

    # 1. 解析请求令牌（admin 平台：本运行专用评估令牌 + pin 到组合渠道；
    #    user 平台：直接用平台测试凭证，外部平台视为隐式渠道无需 pin）
    if platform.type == "user":
        token_key = CryptoUtil.decrypt(platform.test_key_enc)
        pinned = False
    else:
        try:
            async with async_db_session() as db, db.begin():
                run_row = await db.get(ChannelEvalRunModel, run_id)
                if run_row is None:
                    raise ProvisionError("运行不存在")
                platform_obj = await db.get(ChannelEvalPlatformModel, platform.id)
                if platform_obj is None:
                    raise ProvisionError("平台不存在")
                token_key = await resolve_eval_token_key(db, platform_obj, run_row)
        except ProvisionError as e:
            await _count_combo_failure(combo.id, planned=int(params.get("request_count") or 0), error=str(e))
            logger.warning("运行 {} 组合 {} 评估令牌解析失败: {}", run_id, combo.id, e)
            return
        pinned = True

    request_count = int(params.get("request_count") or 1)
    interval = float(params.get("interval_seconds") or 0)
    warmup = bool(params.get("warmup"))

    # 2. 热身请求（is_warmup=true，不计入任何聚合统计）
    if warmup:
        if run_task_registry.is_canceled(run_id):
            return
        draft = await _measure_once(
            base_url=platform.base_url,
            token_key=token_key,
            model=combo.model,
            params=params,
            channel_id=combo.channel_id,
            pinned=pinned,
        )
        await _append_sample(combo.id, seq=0, is_warmup=True, draft=draft)

    # 3. N 次顺序请求（请求边界亦检查取消，尽快停止后续请求）
    for seq in range(1, request_count + 1):
        if run_task_registry.is_canceled(run_id):
            return
        draft = await _measure_once(
            base_url=platform.base_url,
            token_key=token_key,
            model=combo.model,
            params=params,
            channel_id=combo.channel_id,
            pinned=pinned,
        )
        await _append_sample(combo.id, seq=seq, is_warmup=False, draft=draft)
        await _push_progress(
            run_id,
            status="running",
            current_sample=seq,
            message=f"#{combo.channel_id} {combo.channel_name} × {combo.model} 第 {seq}/{request_count} 次",
        )
        if seq < request_count and interval > 0:
            await asyncio.sleep(interval)

    await _set_combo_status(combo.id, "success")


async def _count_combo_failure(combo_id: int, planned: int, error: str) -> None:
    """评估令牌解析失败的组合：status=failed + error + planned 计数。"""
    async with async_db_session() as db, db.begin():
        combo = await db.get(ChannelEvalRunComboModel, combo_id)
        if combo is None:
            return
        combo.status = "failed"
        combo.error = error
        combo.planned = planned


# ─────────────────────────── 客户端实测采集（D5） ───────────────────────────


async def _measure_once(base_url: str, token_key: str, model: str, params: dict[str, Any], *, channel_id: int | None = None, pinned: bool = False) -> dict[str, Any]:
    """发起一次真实流式 chat 请求并采集客户端实测指标。

    口径（spec: test-run / 客户端实测采集）：
    - TTFT = 发出请求到收到第一个内容分片
    - 总耗时 = 至流式结束（[DONE]）
    - tps = 输出 tokens ÷ (总耗时 − TTFT)
    - request_id 取响应头 X-Oneapi-Request-Id（缺失时探测 X-Request-Id，均缺为 None）
    - 失败样本记错误信息，只计成功率

    指定渠道（pin，ADR-0001）：``pinned=True`` 时请求头拼 ``Bearer <key>-<渠道ID>``，
    使请求 100% 命中该渠道（new-api v1.0.0-rc.21+ 原生机制，仅管理员令牌可用）。
    """
    timeout_seconds = int(params.get("timeout_seconds") or 300)
    prompt = str(params.get("prompt") or "")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    auth = f"Bearer {token_key}-{channel_id}" if pinned else f"Bearer {token_key}"
    headers = {"Authorization": auth, "Content-Type": "application/json"}
    started_at = datetime.now(UTC)
    t0 = time.perf_counter()
    draft: dict[str, Any] = {
        "request_id": None,
        "ttft_ms": None,
        "total_ms": None,
        "tokens_in": None,
        "tokens_out": None,
        "tps": None,
        "status": "success",
        "error": None,
        "started_at": started_at,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
            async with client.stream("POST", f"{base_url.rstrip('/')}/v1/chat/completions", headers=headers, json=payload) as response:
                request_id = response.headers.get("x-oneapi-request-id") or response.headers.get("x-request-id")
                if request_id:
                    draft["request_id"] = request_id
                if response.status_code != 200:
                    body = (await response.aread())[:500].decode("utf-8", errors="replace")
                    raise RuntimeError(f"HTTP {response.status_code}: {body or '(空响应体)'}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = (choices[0] or {}).get("delta") or {}
                        content = delta.get("content")
                        if content and draft["ttft_ms"] is None:
                            draft["ttft_ms"] = round((time.perf_counter() - t0) * 1000)
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        if usage.get("prompt_tokens") is not None:
                            draft["tokens_in"] = int(usage["prompt_tokens"])
                        if usage.get("completion_tokens") is not None:
                            draft["tokens_out"] = int(usage["completion_tokens"])
        draft["total_ms"] = round((time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        draft["status"] = "failed"
        draft["error"] = str(e)[:2000]
        draft["total_ms"] = round((time.perf_counter() - t0) * 1000)

    # tps = 输出 tokens ÷ (总耗时 − TTFT)
    if draft["status"] == "success" and draft["tokens_out"] and draft["total_ms"] and draft["ttft_ms"] is not None and draft["total_ms"] > draft["ttft_ms"]:
        draft["tps"] = round(draft["tokens_out"] / ((draft["total_ms"] - draft["ttft_ms"]) / 1000), 2)
    return draft


async def _append_sample(combo_id: int, seq: int, is_warmup: bool, draft: dict[str, Any]) -> None:
    async with async_db_session() as db, db.begin():
        db.add(
            ChannelEvalSampleModel(
                combo_id=combo_id,
                seq=seq,
                is_warmup=is_warmup,
                request_id=draft.get("request_id"),
                ttft_ms=draft.get("ttft_ms"),
                total_ms=draft.get("total_ms"),
                tokens_in=draft.get("tokens_in"),
                tokens_out=draft.get("tokens_out"),
                tps=draft.get("tps"),
                status=draft.get("status") or "failed",
                error=draft.get("error"),
                started_at=draft.get("started_at"),
            )
        )


# ─────────────────────────── 后台日志拉取与配对（D5） ───────────────────────────


async def _pull_and_pair_logs(run_id: int) -> None:
    """运行结束后拉取后台日志并与样本按 request_id 1:1 配对。"""
    async with async_db_session() as db:
        run = await db.get(ChannelEvalRunModel, run_id)
        if run is None:
            return
        platform = await db.get(ChannelEvalPlatformModel, run.platform_id)
        combos = (await db.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == run_id))).scalars().all()
        samples = (
            (
                await db.execute(
                    select(ChannelEvalSampleModel)
                    .join(ChannelEvalRunComboModel, ChannelEvalSampleModel.combo_id == ChannelEvalRunComboModel.id)
                    .where(ChannelEvalRunComboModel.run_id == run_id)
                    .order_by(ChannelEvalSampleModel.id)
                )
            )
            .scalars()
            .all()
        )
    if platform is None or not samples:
        return
    # 延迟约 5s 待平台侧日志落库（设计 D5）
    await asyncio.sleep(LOG_PULL_DELAY_SECONDS)

    window_start = (run.started_at or datetime.now(UTC)) - timedelta(seconds=LOG_WINDOW_BUFFER_SECONDS)
    window_end = (run.finished_at or datetime.now(UTC)) + timedelta(seconds=LOG_WINDOW_BUFFER_SECONDS)
    start_ts = int(window_start.timestamp())
    end_ts = int(window_end.timestamp())

    records: list[dict] = []
    if platform.type == "user":
        client = NewApiClient(platform.base_url, test_key=CryptoUtil.decrypt(platform.test_key_enc))
        records = await client.list_self_logs(start_ts, end_ts)
    else:
        client = build_client(platform)
        # 按本运行专用评估令牌名（= eval-run{运行ID} 约定）+ 时间窗分页拉全；
        # 专用令牌与日常测试令牌隔离，时间窗内该令牌流量即本运行流量
        records.extend(await client.list_logs(eval_run_token_name(run_id), start_ts, end_ts))

    # request_id → 解析记录（重复 request_id 取首条并告警，不凭空配对）
    by_request_id: dict[str, dict] = {}
    duplicate_ids: set[str] = set()
    for record in records:
        parsed = parse_log_record(record)
        request_id = parsed.get("request_id")
        if not request_id:
            continue
        if request_id in by_request_id:
            duplicate_ids.add(request_id)
            continue
        by_request_id[request_id] = parsed
    if duplicate_ids:
        logger.warning("运行 {} 后台日志存在重复 request_id（取首条）: {}", run_id, sorted(duplicate_ids)[:5])

    # 1:1 配对写回 be_*；未配对样本 be_matched=false（后台无记录）
    async with async_db_session() as db, db.begin():
        sample_ids = [sample.id for sample in samples]
        db_samples = (await db.execute(select(ChannelEvalSampleModel).where(ChannelEvalSampleModel.id.in_(sample_ids)))).scalars().all()
        for sample in db_samples:
            parsed = by_request_id.get(sample.request_id) if sample.request_id else None
            if parsed is not None:
                sample.be_matched = True
                sample.be_frt_ms = parsed.get("frt_ms")
                sample.be_use_time_ms = parsed.get("use_time_ms")
                sample.be_tokens_in = parsed.get("tokens_in")
                sample.be_tokens_out = parsed.get("tokens_out")
                sample.be_cost = parsed.get("cost")
                sample.be_channel_id = parsed.get("channel_id")
            else:
                sample.be_matched = False
    matched = sum(1 for sample in samples if (sample.request_id or "") in by_request_id)
    logger.info("运行 {} 后台日志配对完成: {}/{} 样本配对成功", run_id, matched, len(samples))


# ─────────────────────────── 组合聚合（spec: 异常口径，口径定义见 aggregation.py） ───────────────────────────


async def _aggregate_run(run_id: int) -> None:
    """写回 combo 计数：planned/success/failed/anomaly。"""
    async with async_db_session() as db:
        run = await db.get(ChannelEvalRunModel, run_id)
        if run is None:
            return
        params: dict[str, Any] = dict(run.params_json or {})
        request_count = int(params.get("request_count") or 0)
        threshold_ms = int(params.get("anomaly_threshold_seconds") or 30) * 1000
        combos = (await db.execute(select(ChannelEvalRunComboModel).where(ChannelEvalRunComboModel.run_id == run_id))).scalars().all()
        for combo in combos:
            samples = (await db.execute(select(ChannelEvalSampleModel).where(ChannelEvalSampleModel.combo_id == combo.id))).scalars().all()
            non_warmup = [s for s in samples if not s.is_warmup]
            combo.planned = request_count
            combo.success = sum(1 for s in non_warmup if s.status == "success")
            combo.failed = sum(1 for s in non_warmup if s.status == "failed")
            combo.anomaly = sum(1 for s in non_warmup if is_anomaly(s, threshold_ms))
        await db.commit()
