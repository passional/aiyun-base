"""WAV 头部解析与校验（纯函数）。

上游只接受 16kHz / 16bit / 单声道 PCM WAV（ADR-0011），这里只做校验，不做转码。
RIFF 块按字节对齐：每个块头 8 字节（id + uint32 size），块体后若长度为奇数补 1 字节。
``fmt`` 块允许大于 16 字节（扩展格式），只读取前 16 字节的公共字段。
"""

import math
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class WavInfo:
    duration_sec: int
    data_size: int
    sample_rate: int
    channels: int
    bits_per_sample: int


class WavValidationError(ValueError):
    """上传内容不是可用的 16kHz 单声道 PCM16 WAV。"""


def inspect_wav(content: bytes) -> WavInfo:
    """解析并校验 WAV；不合规时抛 ``WavValidationError``（消息可直接展示给用户）。"""
    if len(content) < 12 or content[0:4] != b"RIFF" or content[8:12] != b"WAVE":
        raise WavValidationError("不是有效的 WAV 文件（缺少 RIFF/WAVE 头）")

    fmt: tuple[int, int, int, int] | None = None
    data_size: int | None = None
    offset = 12
    while offset + 8 <= len(content):
        chunk_id = content[offset : offset + 4]
        size = struct.unpack("<I", content[offset + 4 : offset + 8])[0]
        body_start = offset + 8
        body_end = body_start + size
        if body_end > len(content):
            raise WavValidationError(f"WAV 块「{chunk_id.decode('latin-1')}」声明长度超出文件实际字节")

        if chunk_id == b"fmt ":
            if fmt is not None:
                # 多个 fmt 块让「按哪一块校验」失去唯一答案；解析器与上游可能各选一块，
                # 因此拒绝而不是赌。合法编码器不会产出重复块。
                raise WavValidationError("WAV 包含多个 fmt 块，无法确定有效格式")
            if size < 16:
                raise WavValidationError("WAV fmt 块长度不合法")
            audio_format, channels, sample_rate, _byte_rate, _block_align, bits = struct.unpack("<HHIIHH", content[body_start : body_start + 16])
            fmt = (audio_format, channels, sample_rate, bits)
        elif chunk_id == b"data":
            if data_size is not None:
                # 只认第一块会放过「第二块藏真音频」的文件，只认最后一块又能用一小块
                # 把时长压到限额以下；重复 data 块直接拒绝。
                raise WavValidationError("WAV 包含多个 data 块，无法确定音频长度")
            data_size = size

        offset = body_end + (size & 1)

    if fmt is None:
        raise WavValidationError("WAV 缺少 fmt 块")
    if data_size is None:
        raise WavValidationError("WAV 缺少 data 块")
    if data_size == 0:
        raise WavValidationError("WAV data 块为空")

    audio_format, channels, sample_rate, bits = fmt
    if audio_format != 1:
        raise WavValidationError("不支持的 WAV 编码（仅支持 PCM）")
    if sample_rate != 16000:
        raise WavValidationError(f"采样率必须为 16000Hz（当前 {sample_rate}Hz）")
    if bits != 16:
        raise WavValidationError(f"位深必须为 16bit（当前 {bits}bit）")
    if channels != 1:
        raise WavValidationError(f"声道数必须为单声道（当前 {channels} 声道）")

    duration_sec = math.ceil(data_size / (sample_rate * channels * (bits // 8)))
    return WavInfo(
        duration_sec=duration_sec,
        data_size=data_size,
        sample_rate=sample_rate,
        channels=channels,
        bits_per_sample=bits,
    )
