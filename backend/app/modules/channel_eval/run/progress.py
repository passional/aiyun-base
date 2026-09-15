"""运行进度 Redis 读写（D4：断线重连以 Redis 进度为准）"""

import json
from typing import Any

from redis.asyncio.client import Redis

from app.common.enums import RedisInitKeyConfig

_PROGRESS_PREFIX = RedisInitKeyConfig.CHANNEL_EVAL_RUN_PROGRESS.key


def _key(run_id: int) -> str:
    return f"{_PROGRESS_PREFIX}:{run_id}"


async def write_progress(redis: Redis, payload: dict[str, Any], ttl: int | None = None) -> None:
    """进度写 Redis（尽力而为：Redis 抖动不中断执行引擎）。"""
    try:
        await redis.set(_key(int(payload["run_id"])), json.dumps(payload, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001
        pass


async def read_progress(redis: Redis, run_id: int) -> dict[str, Any] | None:
    """读取进度（不存在返回 None）。"""
    try:
        raw = await redis.get(_key(run_id))
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
