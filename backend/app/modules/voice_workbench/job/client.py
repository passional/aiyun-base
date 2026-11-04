"""火山引擎方舟 Agent Plan 语音识别 WebSocket 客户端。

实现官方二进制协议：握手头携带凭证/资源标识/请求标识，全量请求为 JSON + gzip，
音频按固定大小切片并逐片 gzip 发送，末片使用负序号与 ``NEG_WITH_SEQUENCE``。

``connect`` 是测试注入缝：单测传入假传输，永远不触网。
"""

import asyncio
import gzip
import json
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

# 协议常量（官方二进制协议，全部大端）
_PROTOCOL_VERSION = 1
_PROTOCOL_HEADER_SIZE = 1

FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_WITH_SEQUENCE = 0b0011

JSON_SERIALIZATION = 0b0001
NO_SERIALIZATION = 0b0000
GZIP_COMPRESSION = 0b0001

GZIP_MAGIC = b"\x1f\x8b"
AUDIO_CHUNK_BYTES = 6400  # 200ms @ 16kHz/16bit/单声道
# 上游明确要求发包间隔 100~200ms：20ms（10 倍速）在真实语音下会让服务端处理不过来，
# 触发 45000081「Timed out waiting for the next audio packet」。0.1s 即 2 倍速，
# 是规范内最快的取值；发送耗时约等于音频时长的一半。
CHUNK_INTERVAL_SEC = 0.1
DEFAULT_TIMEOUT_SEC = 280.0


@dataclass(frozen=True)
class TranscriptionSegment:
    start_time: int
    end_time: int
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptionSegment]
    usage: dict | None = None
    logid: str | None = None


class VoiceUpstreamError(Exception):
    """上游识别失败：消息可直接展示给用户。"""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes(
        [
            (_PROTOCOL_VERSION << 4) | _PROTOCOL_HEADER_SIZE,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0,
        ]
    )


def _read_u32(buf: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">I", buf, offset)[0], offset + 4


def _read_i32(buf: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">i", buf, offset)[0], offset + 4


def _decompress(payload: bytes, compression: int) -> bytes:
    if compression == GZIP_COMPRESSION and payload[:2] == GZIP_MAGIC:
        return gzip.decompress(payload)
    return payload


def _full_client_request(*, model_name: str, uid: str) -> bytes:
    payload = {
        "user": {"uid": uid},
        "audio": {"format": "wav", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": model_name,
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": False,
        },
    }
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return _header(FULL_CLIENT_REQUEST, NO_SEQUENCE, JSON_SERIALIZATION, GZIP_COMPRESSION) + struct.pack(">I", len(compressed)) + compressed


def _audio_request(chunk: bytes, *, sequence: int, final: bool) -> bytes:
    compressed = gzip.compress(chunk)
    flags = NEG_WITH_SEQUENCE if final else POS_SEQUENCE
    return _header(AUDIO_ONLY_REQUEST, flags, NO_SERIALIZATION, GZIP_COMPRESSION) + struct.pack(">i", sequence) + struct.pack(">I", len(compressed)) + compressed


def _error_payload(payload: bytes) -> str:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")
    if isinstance(data, dict):
        message = data.get("error") or data.get("message")
        if message:
            return str(message)
        return json.dumps(data, ensure_ascii=False)
    return str(data)


def _segments_of(utterances: list[Any]) -> list[TranscriptionSegment]:
    segments: list[TranscriptionSegment] = []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        segments.append(
            TranscriptionSegment(
                start_time=int(item.get("start_time") or 0),
                end_time=int(item.get("end_time") or 0),
                text=str(item.get("text") or ""),
            )
        )
    return segments


class VoiceUpstreamClient:
    """一次转写 = 一条 WebSocket 连接。"""

    def __init__(
        self,
        endpoint_url: str,
        *,
        api_key: str,
        resource_id: str,
        connect: Callable[..., Any] | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.resource_id = resource_id
        self.timeout_sec = timeout_sec
        if connect is None:
            from websockets.asyncio.client import connect as default_connect

            connect = default_connect
        self._connect = connect

    async def transcribe(self, *, model_name: str, uid: str, audio: bytes) -> TranscriptionResult:
        """发送完整 WAV 并聚合响应；所有失败都转成 ``VoiceUpstreamError``。"""
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid4()),
            "X-Api-Connect-Id": str(uuid4()),
            "X-Api-Sequence": "-1",
        }
        latest_text = ""
        latest_utterances: list[Any] = []
        usage: dict | None = None
        logid: str | None = None
        final_received = False
        try:
            async with asyncio.timeout(self.timeout_sec):
                async with self._connect(
                    self.endpoint_url,
                    additional_headers=headers,
                    max_size=None,
                    ping_interval=20,
                    close_timeout=5,
                ) as ws:
                    await ws.send(_full_client_request(model_name=model_name, uid=uid))
                    # 发送与接收必须并行：优化版双向流在音频还在发送时就会回增量结果。
                    # 若先发完再读，接收缓冲触顶后 websockets 会暂停传输层读取，连
                    # keepalive 的 pong 也读不到，长音频会以「keepalive ping timeout」断开。
                    send_task = asyncio.create_task(self._send_audio(ws, audio))
                    try:
                        async for message in ws:
                            if not isinstance(message, (bytes, bytearray, memoryview)):
                                continue
                            buf = bytes(message)
                            if len(buf) < 4:
                                raise VoiceUpstreamError("上游响应格式不完整")
                            flags = buf[1] & 0x0F
                            message_type = buf[1] >> 4
                            compression = buf[2] & 0x0F

                            if message_type == SERVER_ERROR_RESPONSE:
                                offset = 4
                                code, offset = _read_u32(buf, offset)
                                length, offset = _read_u32(buf, offset)
                                if offset + length > len(buf):
                                    raise VoiceUpstreamError("上游错误响应格式不完整", code)
                                payload = _decompress(buf[offset : offset + length], compression)
                                raise VoiceUpstreamError(_error_payload(payload), code)

                            offset = 4
                            sequence: int | None = None
                            if flags & POS_SEQUENCE:
                                sequence, offset = _read_i32(buf, offset)
                            length, offset = _read_u32(buf, offset)
                            if offset + length > len(buf):
                                raise VoiceUpstreamError("上游响应负载长度超出实际字节")
                            payload = _decompress(buf[offset : offset + length], compression)

                            if message_type == FULL_SERVER_RESPONSE:
                                data = json.loads(payload.decode("utf-8"))
                                result = data.get("result") or {}
                                text = result.get("text") or ""
                                if text:
                                    latest_text = text
                                utterances = result.get("utterances")
                                if isinstance(utterances, list):
                                    latest_utterances = utterances
                                if "audio_info" in data:
                                    usage = data.get("audio_info")

                            if flags & NEG_SEQUENCE or (sequence is not None and sequence < 0):
                                final_received = True
                                break
                    except BaseException:
                        # 读取异常/超时取消时，发送任务不会自己结束，先收掉再上抛。
                        send_task.cancel()
                        try:
                            await send_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        raise
                    # 末包意味着最后一帧音频已经到达上游；正常路径只等发送任务
                    # 收尾并传播它自己的异常，不取消。
                    await send_task

                    if not final_received:
                        raise VoiceUpstreamError("上游连接在返回最终结果前关闭")

                    response = getattr(ws, "response", None)
                    headers_obj = getattr(response, "headers", None)
                    if headers_obj is not None:
                        logid = headers_obj.get("X-Tt-Logid") or headers_obj.get("x-tt-logid")
        except TimeoutError as exc:
            raise VoiceUpstreamError("上游识别超时") from exc
        except VoiceUpstreamError:
            raise
        except Exception as exc:  # noqa: BLE001 — 握手/连接/解析异常统一转成可读业务错误
            raise VoiceUpstreamError(f"上游连接失败: {exc}") from exc

        return TranscriptionResult(
            text=latest_text,
            segments=_segments_of(latest_utterances),
            usage=usage,
            logid=logid,
        )

    @staticmethod
    async def _send_audio(ws: Any, audio: bytes) -> None:
        chunks = [audio[i : i + AUDIO_CHUNK_BYTES] for i in range(0, len(audio), AUDIO_CHUNK_BYTES)] or [b""]
        total = len(chunks)
        # 上游按「连接内自增序号」校验每一帧：full client request 占用 1 号，
        # 因此首个音频帧必须是 2 号，之后逐帧 +1。末帧不带正序号，而是取
        # 「本帧本应拿到的序号」的负数（例如 4 帧音频：2、3、4、-5）。
        # 从 1 开始会被拒：autoAssignedSequence (2) mismatch sequence in request (1)。
        for index, chunk in enumerate(chunks):
            final = index == total - 1
            sequence = -(index + 2) if final else index + 2
            await ws.send(_audio_request(chunk, sequence=sequence, final=final))
            if not final:
                await asyncio.sleep(CHUNK_INTERVAL_SEC)
