"""SSE 跨进程监听：空闲超时不得让订阅协程退出。

回归的故障现场：Redis 客户端带 ``socket_timeout``（``settings.POOL_TIMEOUT``，默认 30s），
pubsub 空闲到点就抛 ``Timeout reading from localhost:6379``。旧实现用
``pubsub.listen()``，异常直接穿出循环 → 监听协程退出并记一条 ERROR；在下次 SSE 连接
把它重启之前，别的 worker 广播来的推送投不进本进程。而「这段时间没人发消息」并不是故障。
"""

import asyncio
import json

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core import sse_manager
from app.core.sse_manager import SSEConnectionManager


class _FakePubSub:
    """按脚本产出：先抛空闲超时，再给出一条消息，脚本用完后一直阻塞。"""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        return None

    async def _next(self):
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item  # 与真实 pubsub 一致：超时异常穿出读取调用
            return item
        await asyncio.sleep(3600)  # 没有消息就一直等着（真实实现也是阻塞读）
        return None

    async def get_message(self, ignore_subscribe_messages: bool = False, timeout=None):
        return await self._next()

    async def listen(self):
        """旧实现用的接口：异常同样穿出生成器（redis-py 的 listen 不捕获超时）。"""
        while True:
            item = await self._next()
            if item is not None:
                yield item

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


def _message(payload: dict, *, sender: str = "other-process") -> dict:
    return {
        "type": "message",
        "data": json.dumps({"sender": sender, "target": {"users": [7]}, "data": payload}, ensure_ascii=False),
    }


async def test_idle_timeout_keeps_listener_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """空闲超时只表示「没有消息」，监听协程必须继续，并照常投递后续消息。"""
    pubsub = _FakePubSub(
        [
            RedisTimeoutError("Timeout reading from localhost:6379"),
            RedisTimeoutError("Timeout reading from localhost:6379"),
            _message({"status": "success"}),
        ]
    )
    manager = SSEConnectionManager("test_idle_timeout")
    dispatched: list[tuple] = []

    async def fake_dispatch(target: dict, data: dict) -> None:
        dispatched.append((target, data))

    monkeypatch.setattr(manager, "_dispatch_local", fake_dispatch)

    task = asyncio.create_task(manager._listen(_FakeRedis(pubsub)))
    try:
        for _ in range(100):
            if dispatched:
                break
            await asyncio.sleep(0.01)
        assert dispatched == [({"users": [7]}, {"status": "success"})], "空闲超时之后的消息未被投递"
        assert not task.done(), "空闲超时不应结束监听协程"
    finally:
        # 监听协程若已自己退出（修复前的行为），就没有可取消的东西——让主断言去报错
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert pubsub.closed is True
        sse_manager._managers.remove(manager)


async def test_own_message_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """本进程广播出去又回环过来的消息不重复投递（既有行为，顺带锁住）。"""
    pubsub = _FakePubSub([_message({"x": 1}, sender=sse_manager._PROCESS_TOKEN)])
    manager = SSEConnectionManager("test_loopback")
    dispatched: list[tuple] = []

    async def fake_dispatch(target: dict, data: dict) -> None:
        dispatched.append((target, data))

    monkeypatch.setattr(manager, "_dispatch_local", fake_dispatch)

    task = asyncio.create_task(manager._listen(_FakeRedis(pubsub)))
    try:
        await asyncio.sleep(0.05)
        assert dispatched == []
        assert not task.done()
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        sse_manager._managers.remove(manager)