"""SSH 终端会话管理测试（假 paramiko 通道，不联网）。"""

import queue
import threading
import time
from typing import Any

import pytest

from app.core.exceptions import CustomException
from app.modules.ops.ssh.terminal import (
    MAX_SESSIONS_PER_USER,
    TerminalManager,
)


class _FakeChannel:
    """阻塞式假通道：recv 最多等 5 秒（close 时立即返回 b""）。"""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.size: tuple[int, int] | None = None
        self.closed = False
        self._event = threading.Event()

    def settimeout(self, timeout: float) -> None:
        return None

    def recv(self, size: int) -> bytes:
        self._event.wait(5)
        return b""

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def resize_pty(self, width: int, height: int) -> None:
        self.size = (width, height)

    def close(self) -> None:
        self.closed = True
        self._event.set()

    def recv_exit_status(self) -> int:
        return 0


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _open(user_id: int = 1) -> Any:
    channel = _FakeChannel()
    client = _FakeClient()
    session = TerminalManager.open(
        user_id=user_id,
        host_id=1,
        host_name="web-1",
        client=client,  # type: ignore[arg-type]
        channel=channel,  # type: ignore[arg-type]
        cols=120,
        rows=30,
    )
    return session, channel, client


@pytest.fixture(autouse=True)
def _cleanup() -> Any:
    yield
    for session_id in list(TerminalManager._sessions):
        TerminalManager.close(session_id)


async def test_open_poll_write_resize_close() -> None:
    session, channel, client = _open()
    assert session.session_id
    # 模拟读取线程塞入的输出
    session.output.put(b"hello ")
    session.output.put(b"world")
    data, closed, _ = TerminalManager.poll(session.session_id, 1, timeout=0.3)
    assert data == b"hello world" and closed is False

    TerminalManager.write(session.session_id, 1, b"ls\r")
    assert channel.sent == [b"ls\r"]

    TerminalManager.resize(session.session_id, 1, 100, 40)
    assert channel.size == (100, 40)

    TerminalManager.close(session.session_id, 1)
    assert channel.closed is True and client.closed is True


async def test_poll_after_close_raises() -> None:
    session, _, _ = _open()
    TerminalManager.close(session.session_id, 1)
    with pytest.raises(CustomException):
        TerminalManager.poll(session.session_id, 1, timeout=0)


async def test_other_user_cannot_access_session() -> None:
    session, _, _ = _open(user_id=1)
    with pytest.raises(CustomException):
        TerminalManager.poll(session.session_id, 2, timeout=0)
    with pytest.raises(CustomException):
        TerminalManager.write(session.session_id, 2, b"x")


async def test_per_user_session_limit() -> None:
    for _ in range(MAX_SESSIONS_PER_USER):
        _open(user_id=7)
    with pytest.raises(CustomException, match="单用户最多"):
        _open(user_id=7)


async def test_resync_drains_buffered_output() -> None:
    session, _, _ = _open()
    session.output.put(b"stale")
    TerminalManager.poll(session.session_id, 1, timeout=0)  # 模拟 resync
    data, _, _ = TerminalManager.poll(session.session_id, 1, timeout=0)
    assert data == b""


async def test_reader_thread_marks_closed_on_channel_eof() -> None:
    """通道 EOF（recv 返回 b""）后读取线程应把会话标记为 closed。"""
    channel = _FakeChannel()
    channel._event.set()  # 让 recv 立刻返回 b""
    TerminalManager.open(
        user_id=9,
        host_id=1,
        host_name="h",
        client=_FakeClient(),  # type: ignore[arg-type]
        channel=channel,  # type: ignore[arg-type]
        cols=80,
        rows=24,
    )
    deadline = time.monotonic() + 2
    while TerminalManager.count() and time.monotonic() < deadline:
        session = next(iter(TerminalManager._sessions.values()))
        if session.closed:
            break
        time.sleep(0.02)
    assert session.closed is True or TerminalManager.count() == 0


def test_queue_type_is_thread_safe() -> None:
    q: queue.Queue[bytes] = queue.Queue()
    q.put(b"a")
    assert q.get_nowait() == b"a"
