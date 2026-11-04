"""WAV 校验纯函数测试：RIFF 解析、格式约束、data 长度与时长计算。"""

import struct

import pytest

from app.modules.voice_workbench.job.wav_validation import WavValidationError, inspect_wav


def _chunk(chunk_id: bytes, body: bytes) -> bytes:
    pad = b"\x00" if len(body) % 2 else b""
    return chunk_id + struct.pack("<I", len(body)) + body + pad


def _fmt(*, audio_format: int = 1, channels: int = 1, sample_rate: int = 16000, bits: int = 16, extra: bytes = b"") -> bytes:
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    body = struct.pack("<HHIIHH", audio_format, channels, sample_rate, byte_rate, block_align, bits) + extra
    return _chunk(b"fmt ", body)


def _wav(data: bytes = b"\x00" * 32000, *, fmt: bytes | None = None, before_data: bytes = b"") -> bytes:
    body = b"WAVE" + (fmt if fmt is not None else _fmt()) + before_data + _chunk(b"data", data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_valid_wav_returns_size_and_fields() -> None:
    info = inspect_wav(_wav(b"\x00" * 32000))
    assert info.data_size == 32000
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.bits_per_sample == 16
    assert info.duration_sec == 1


def test_duration_rounds_up_to_whole_seconds() -> None:
    assert inspect_wav(_wav(b"\x00" * 1)).duration_sec == 1
    assert inspect_wav(_wav(b"\x00" * 32001)).duration_sec == 2
    assert inspect_wav(_wav(b"\x00" * 64000)).duration_sec == 2


def test_non_riff_or_non_wave_rejected() -> None:
    with pytest.raises(WavValidationError, match="RIFF/WAVE"):
        inspect_wav(b"not a wave file at all")
    with pytest.raises(WavValidationError, match="RIFF/WAVE"):
        inspect_wav(b"RIFX" + b"\x00" * 4 + b"WAVE" + _fmt() + _chunk(b"data", b"\x00" * 10))


@pytest.mark.parametrize(
    ("fmt", "message"),
    [
        (_fmt(audio_format=3), "PCM"),
        (_fmt(sample_rate=8000), "16000Hz"),
        (_fmt(bits=8), "16bit"),
        (_fmt(channels=2), "单声道"),
    ],
)
def test_invalid_format_fields_rejected(fmt: bytes, message: str) -> None:
    with pytest.raises(WavValidationError, match=message):
        inspect_wav(_wav(b"\x00" * 32000, fmt=fmt))


def test_data_declared_length_beyond_file_rejected() -> None:
    # 声称 data 有 100 字节，实际只给 10 字节
    content = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + _fmt() + b"data" + struct.pack("<I", 100) + b"\x00" * 10
    with pytest.raises(WavValidationError, match="超出文件实际字节"):
        inspect_wav(content)


def test_empty_data_rejected() -> None:
    with pytest.raises(WavValidationError, match="data 块为空"):
        inspect_wav(_wav(b""))


def test_missing_fmt_or_data_rejected() -> None:
    only_data = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + _chunk(b"data", b"\x00" * 10)
    with pytest.raises(WavValidationError, match="fmt"):
        inspect_wav(only_data)

    only_fmt = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + _fmt()
    with pytest.raises(WavValidationError, match="data"):
        inspect_wav(only_fmt)


def test_extended_fmt_chunk_is_allowed() -> None:
    info = inspect_wav(_wav(b"\x00" * 32000, fmt=_fmt(extra=b"\x00" * 8)))
    assert info.duration_sec == 1


def test_duplicate_data_chunk_rejected() -> None:
    """重复 data 块可把真实时长藏在第二块，或用小尾块骗过时长校验。"""
    body = b"WAVE" + _fmt() + _chunk(b"data", b"\x00" * 32000) + _chunk(b"data", b"\x00" * 2)
    content = b"RIFF" + struct.pack("<I", len(body)) + body
    with pytest.raises(WavValidationError, match="多个 data"):
        inspect_wav(content)


def test_duplicate_fmt_chunk_rejected() -> None:
    body = b"WAVE" + _fmt() + _fmt(sample_rate=8000) + _chunk(b"data", b"\x00" * 32000)
    content = b"RIFF" + struct.pack("<I", len(body)) + body
    with pytest.raises(WavValidationError, match="多个 fmt"):
        inspect_wav(content)


def test_odd_sized_chunk_is_padding_aligned() -> None:
    """data 前的附加块长度为奇数时必须跳过 1 字节填充，否则 data 起始位置错位。"""
    list_chunk = _chunk(b"LIST", b"abc")
    assert len(list_chunk) == 8 + 3 + 1
    info = inspect_wav(_wav(b"\x00" * 32000, before_data=list_chunk))
    assert info.data_size == 32000
