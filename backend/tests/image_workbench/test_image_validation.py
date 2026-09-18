"""参考图格式校验与透明通道判定测试（任务 2.4）

``has_alpha`` 靠**头部解析**得出（design D9）：后端没有图像处理库，只为读一个标志
引入 Pillow 不划算。这里用构造出来的真实字节序列逐类覆盖：

- PNG：IHDR colour type 4 / 6 直接判有；3（调色板）再看 ``tRNS`` 块；0 / 2 判无
- WebP：``VP8X`` 的 alpha 标志位，或 ``VP8L`` 头部的 alpha 位
- JPEG / GIF：判无
"""

import struct
import zlib
from pathlib import Path

import pytest

from app.config.path_conf import BASE_DIR
from app.core.exceptions import CustomException
from app.modules.image_workbench.job import image_validation
from app.modules.image_workbench.job.image_validation import (
    detect_alpha,
    detect_alpha_file,
    detect_image_format,
    validate_reference_image_bytes,
)

# ─────────────────────────── 构造真实字节序列 ───────────────────────────


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data))


def _png(colour_type: int, *, trns: bytes | None = None) -> bytes:
    """按 colour type 构造一个结构完整（CRC 正确）的 1x1 PNG。"""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, colour_type, 0, 0, 0)
    chunks = [_png_chunk(b"IHDR", ihdr)]
    if trns is not None:
        chunks.append(_png_chunk(b"tRNS", trns))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")))
    chunks.append(_png_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _webp(fourcc: bytes, payload: bytes) -> bytes:
    body = fourcc + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def _webp_vp8x(*, alpha: bool) -> bytes:
    flags = 0x10 if alpha else 0x00
    return _webp(b"VP8X", bytes([flags, 0, 0, 0]) + struct.pack("<I", 1)[:3] * 2)


def _webp_vp8l(*, alpha: bool) -> bytes:
    header = (1 << 28) if alpha else 0
    return _webp(b"VP8L", b"\x2f" + struct.pack("<I", header) + b"\x00" * 8)


_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32 + b"\xff\xd9"
_GIF = b"GIF89a" + b"\x00" * 32


# ────────────────────────── 格式识别 ───────────────────────────


@pytest.mark.parametrize(
    ("content", "expected"),
    [(_png(6), "png"), (_JPEG, "jpeg"), (_GIF, "gif"), (_webp_vp8x(alpha=False), "webp"), (b"not an image", None), (b"", None)],
)
def test_detect_image_format(content: bytes, expected: str | None) -> None:
    assert detect_image_format(content) == expected


def test_validate_reference_image_bytes_returns_format_and_checks_extension() -> None:
    assert validate_reference_image_bytes(_png(6), "ref.png") == "png"
    with pytest.raises(CustomException):
        validate_reference_image_bytes(_png(6), "ref.jpg")
    with pytest.raises(CustomException):
        validate_reference_image_bytes(_GIF, "ref.png")


# ─────────────────── PNG：colour type 与 tRNS ───────────────────


@pytest.mark.parametrize(
    ("colour_type", "trns", "expected", "why"),
    [
        (6, None, True, "真彩 + alpha"),
        (4, None, True, "灰度 + alpha"),
        (3, b"\x00", True, "调色板 + tRNS（透明度在 tRNS 块里）"),
        (3, None, False, "调色板但没有 tRNS"),
        (2, None, False, "真彩无 alpha"),
        (0, None, False, "灰度无 alpha"),
    ],
)
def test_png_alpha_by_colour_type_and_trns(colour_type: int, trns: bytes | None, expected: bool, why: str) -> None:
    content = _png(colour_type, trns=trns)
    assert detect_image_format(content) == "png"
    assert detect_alpha(content) is expected, why


def test_truncated_png_does_not_crash_and_defaults_to_no_alpha() -> None:
    """截断 / 头异常的 PNG 不抛异常（保守判为无透明通道）。"""
    full = _png(6)
    assert detect_alpha(full[:12]) is False
    assert detect_alpha(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40) is False


# ────────────────── WebP：VP8X / VP8L ───────────────────


@pytest.mark.parametrize("alpha", [True, False])
def test_webp_alpha_by_vp8x_flag(alpha: bool) -> None:
    content = _webp_vp8x(alpha=alpha)
    assert detect_image_format(content) == "webp"
    assert detect_alpha(content) is alpha


@pytest.mark.parametrize("alpha", [True, False])
def test_webp_alpha_by_vp8l_header(alpha: bool) -> None:
    content = _webp_vp8l(alpha=alpha)
    assert detect_image_format(content) == "webp"
    assert detect_alpha(content) is alpha


def test_webp_lossy_without_alpha_chunk_is_false() -> None:
    """简单（有损）格式直接以 ``VP8 `` 开头：没有 alpha，也不该去猜。"""
    content = _webp(b"VP8 ", b"\x00" * 16)
    assert detect_image_format(content) == "webp"
    assert detect_alpha(content) is False


# ─────────────────── JPEG / GIF ──────────────────


@pytest.mark.parametrize(("content", "fmt"), [(_JPEG, "jpeg"), (_GIF, "gif")])
def test_jpeg_and_gif_are_treated_as_opaque(content: bytes, fmt: str) -> None:
    """JPEG / GIF 判为无透明通道（GIF 的索引透明是否被上游接受见 design Open Questions）。"""
    assert detect_image_format(content) == fmt
    assert detect_alpha(content) is False


# ─────────────────── 提交时按文件判定 ───────────────────


def test_detect_alpha_file_reads_from_disk(tmp_path: Path) -> None:
    transparent = tmp_path / "a.png"
    transparent.write_bytes(_png(6))
    opaque = tmp_path / "b.png"
    opaque.write_bytes(_png(2))
    assert detect_alpha_file(transparent) is True
    assert detect_alpha_file(opaque) is False
    # 读不到就当没有（存在性由提交校验另报错）
    assert detect_alpha_file(tmp_path / "missing.png") is False


# ─────────────────── 不引入图像处理依赖 ───────────────────


def test_no_image_processing_dependency_introduced() -> None:
    """判定只用标准库（struct/zlib 都没有，纯字节切片）；不引入 Pillow 一类依赖。"""
    source = Path(image_validation.__file__).read_text(encoding="utf-8")
    for forbidden in ("PIL", "from PIL", "import cv2", "imageio", "numpy", "wand"):
        assert forbidden not in source, f"参考图校验不得依赖 {forbidden}"

    pyproject = (BASE_DIR / "pyproject.toml").read_text(encoding="utf-8").lower()
    for forbidden in ("pillow", "opencv", "imageio", "wand"):
        assert forbidden not in pyproject, f"依赖清单里不得出现 {forbidden}"
