"""上游二进制协议测试：用假传输注入固定帧，测试内不建立任何网络连接。

帧格式按契约独立构造（不复用客户端常量），这样请求头、序号与 gzip 断言能真正
发现实现漂移。
"""

import asyncio
import gzip
import json
import struct
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.voice_workbench.job import client as client_module
from app.modules.voice_workbench.job.client import (
    AUDIO_CHUNK_BYTES,
    VoiceUpstreamClient,
    VoiceUpstreamError,
)

FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ERROR_RESPONSE = 0b1111
NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_WITH_SEQUENCE = 0b0011
JSON_SERIALIZATION = 0b0001
GZIP_COMPRESSION = 0b0001


def _header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes([0x11, (message_type << 4) | flags, (serialization << 4) | compression, 0])


def _gzip_json(payload: dict) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _server_frame(payload: dict, *, flags: int = 0, sequence: int | None = None, compress: bool = True) -> bytes:
    body = _gzip_json(payload) if compress else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    frame = _header(FULL_SERVER_RESPONSE, flags, JSON_SERIALIZATION, GZIP_COMPRESSION if compress else 0)
    if flags & POS_SEQUENCE:
        frame += struct.pack(">i", sequence if sequence is not None else 0)
    return frame + struct.pack(">I", len(body)) + body


def _error_frame(code: int, payload: dict) -> bytes:
    body = _gzip_json(payload)
    return _header(SERVER_ERROR_RESPONSE, NO_SEQUENCE, JSON_SERIALIZATION, GZIP_COMPRESSION) + struct.pack(">I", code) + struct.pack(">I", len(body)) + body


def _parse_header(frame: bytes) -> tuple[int, int, int, int, int]:
    return frame[0], frame[1] >> 4, frame[1] & 0x0F, frame[2] >> 4, frame[2] & 0x0F


class _FakeWS:
    def __init__(self, frames: list[bytes], *, delay: float = 0.0) -> None:
        self.frames = list(frames)
        self.delay = delay
        self.sent: list[bytes] = []
        self.response = SimpleNamespace(headers={"X-Tt-Logid": "log-123"})

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    def __aiter__(self) -> "_FakeWS":
        return self

    async def __anext__(self) -> bytes:
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)


class _FakeConnection:
    def __init__(self, ws: _FakeWS) -> None:
        self.ws = ws

    async def __aenter__(self) -> _FakeWS:
        return self.ws

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _install(frames: list[bytes], *, delay: float = 0.0) -> tuple[_FakeWS, list[tuple[str, dict[str, Any]]]]:
    ws = _FakeWS(frames, delay=delay)
    calls: list[tuple[str, dict[str, Any]]] = []

    def _connect(url: str, **kwargs: Any) -> _FakeConnection:
        calls.append((url, kwargs))
        return _FakeConnection(ws)

    return ws, calls


async def test_handshake_headers_and_full_request_json() -> None:
    ws, calls = _install([_server_frame({"result": {"text": "ok"}}, flags=NEG_SEQUENCE)])
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="sk-key", resource_id="res-1", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    await client.transcribe(model_name="bigmodel", uid="42", audio=b"wav-bytes")

    url, kwargs = calls[0]
    assert url == "wss://asr.example.com/ws"
    assert kwargs["additional_headers"]["X-Api-Key"] == "sk-key"
    assert kwargs["additional_headers"]["X-Api-Resource-Id"] == "res-1"
    assert kwargs["additional_headers"]["X-Api-Sequence"] == "-1"
    assert kwargs["additional_headers"]["X-Api-Request-Id"]
    assert kwargs["additional_headers"]["X-Api-Connect-Id"]
    assert kwargs["max_size"] is None

    full = ws.sent[0]
    assert _parse_header(full) == (0x11, FULL_CLIENT_REQUEST, NO_SEQUENCE, JSON_SERIALIZATION, GZIP_COMPRESSION)
    length = struct.unpack(">I", full[4:8])[0]
    body = json.loads(gzip.decompress(full[8 : 8 + length]).decode("utf-8"))
    assert body == {
        "user": {"uid": "42"},
        "audio": {"format": "wav", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": False,
        },
    }


async def test_audio_chunk_sequence_and_final_negative_sequence() -> None:
    audio = bytes(range(256)) * ((AUDIO_CHUNK_BYTES * 2 + 10) // 256 + 1)
    audio = audio[: AUDIO_CHUNK_BYTES * 2 + 10]
    ws, calls = _install([_server_frame({"result": {"text": "ok"}}, flags=NEG_SEQUENCE)])
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    await client.transcribe(model_name="m", uid="1", audio=audio)

    frames = ws.sent[1:]
    assert len(frames) == 3
    expected_chunks = [audio[0:6400], audio[6400:12800], audio[12800:]]
    for index, (frame, chunk) in enumerate(zip(frames, expected_chunks, strict=True), start=1):
        assert _parse_header(frame) == (0x11, AUDIO_ONLY_REQUEST, POS_SEQUENCE if index < 3 else NEG_WITH_SEQUENCE, 0, GZIP_COMPRESSION)
        sequence = struct.unpack(">i", frame[4:8])[0]
        assert sequence == (index if index < 3 else -3)
        length = struct.unpack(">I", frame[8:12])[0]
        assert gzip.decompress(frame[12 : 12 + length]) == chunk


async def test_success_response_aggregates_latest_text_segments_and_usage() -> None:
    frames = [
        _server_frame({"result": {"text": "你", "utterances": [{"start_time": 0, "end_time": 1000, "text": "你"}]}}),
        _server_frame(
            {
                "result": {"text": "你好", "utterances": [{"start_time": 0, "end_time": 1000, "text": "你"}, {"start_time": 1000, "end_time": 2000, "text": "好"}]},
                "audio_info": {"duration": 2000},
            },
            flags=NEG_SEQUENCE,
        ),
    ]
    ws, calls = _install(frames)
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    result = await client.transcribe(model_name="m", uid="1", audio=b"wav")

    assert result.text == "你好"
    assert [(s.start_time, s.end_time, s.text) for s in result.segments] == [(0, 1000, "你"), (1000, 2000, "好")]
    assert result.usage == {"duration": 2000}
    assert result.logid == "log-123"


async def test_empty_final_text_keeps_latest_non_empty_text() -> None:
    frames = [
        _server_frame({"result": {"text": "保留我"}}),
        _server_frame({"result": {"text": ""}}, flags=NEG_SEQUENCE),
    ]
    ws, calls = _install(frames)
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    result = await client.transcribe(model_name="m", uid="1", audio=b"wav")
    assert result.text == "保留我"


async def test_negative_sequence_ends_stream() -> None:
    frames = [
        _server_frame({"result": {"text": "a"}}, flags=NEG_WITH_SEQUENCE, sequence=-1),
        _server_frame({"result": {"text": "never"}}, flags=NO_SEQUENCE),
    ]
    ws, calls = _install(frames)
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    result = await client.transcribe(model_name="m", uid="1", audio=b"wav")
    assert result.text == "a"


async def test_raw_payload_without_gzip_magic_is_accepted() -> None:
    ws, calls = _install([_server_frame({"result": {"text": "raw"}}, flags=NEG_SEQUENCE, compress=False)])
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    result = await client.transcribe(model_name="m", uid="1", audio=b"wav")
    assert result.text == "raw"


async def test_error_frame_raises_with_code_and_message() -> None:
    ws, calls = _install([_error_frame(45000001, {"error": "invalid api key"})])
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    with pytest.raises(VoiceUpstreamError) as exc:
        await client.transcribe(model_name="m", uid="1", audio=b"wav")
    assert exc.value.code == 45000001
    assert exc.value.message == "invalid api key"


async def test_timeout_is_reported_as_readable_error() -> None:
    ws, calls = _install([_server_frame({"result": {"text": "late"}}, flags=NEG_SEQUENCE)], delay=1.0)
    client = VoiceUpstreamClient(
        "wss://asr.example.com/ws",
        api_key="k",
        resource_id="r",
        connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1],
        timeout_sec=0.05,
    )

    with pytest.raises(VoiceUpstreamError, match="上游识别超时"):
        await client.transcribe(model_name="m", uid="1", audio=b"wav")


async def test_stream_ending_without_final_packet_is_an_error() -> None:
    """连接在末包前关闭不能当成功：否则空结果会被写成一次成功的转写。"""
    ws, calls = _install([_server_frame({"result": {"text": "未完成"}})])
    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=lambda url, **kw: (calls.append((url, kw)), _FakeConnection(ws))[1])

    with pytest.raises(VoiceUpstreamError, match="最终结果前关闭"):
        await client.transcribe(model_name="m", uid="1", audio=b"wav")


async def test_connection_error_is_reported_as_readable_error() -> None:
    def _connect(url: str, **kwargs: Any) -> Any:
        raise OSError("connection refused")

    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r", connect=_connect)
    with pytest.raises(VoiceUpstreamError, match="上游连接失败"):
        await client.transcribe(model_name="m", uid="1", audio=b"wav")


def test_default_connect_is_websockets_asyncio_client() -> None:
    """默认注入缝指向 websockets 的 asyncio 客户端，而不是真实发起连接。"""
    from websockets.asyncio.client import connect

    client = VoiceUpstreamClient("wss://asr.example.com/ws", api_key="k", resource_id="r")
    assert client._connect is connect
    assert client_module.AUDIO_CHUNK_BYTES == 6400
