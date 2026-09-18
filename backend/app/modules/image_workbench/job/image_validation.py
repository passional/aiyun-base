"""绘图工作台参考图校验与透明通道判定。

参考图最终会被发送到外部绘图服务。调用方不能只信浏览器 MIME 或文件扩展名；
本模块按文件内容识别首期支持的位图格式，并在上传和绘图任务提交两个入口复用。

**透明通道的判定放在上传时**（design D9）：规格要求「参数的可用性随本次请求的输入
变化」在上传后即可见，而不是等到提交。后端没有图像处理库，因此这里只做**头部解析**
——读出格式头里的 alpha 标志，不引入 Pillow 一类的依赖：

- PNG：IHDR 的 colour type 为 4 / 6 → 有；为 3（调色板）时再找 ``tRNS`` 块；
- WebP：``VP8X`` 的 alpha 标志位，或 ``VP8L`` 头部的 alpha 位；
- JPEG / GIF：判为无（GIF 的索引透明是否被上游接受见 design 的 Open Questions，
  最终由上游裁决；``has_alpha`` 无论对错都只是前端置灰的依据）。
"""

from pathlib import Path

from app.config.setting import settings
from app.core.exceptions import CustomException

_FORMAT_EXTENSIONS: dict[str, frozenset[str]] = {
    "jpeg": frozenset({".jpg", ".jpeg"}),
    "png": frozenset({".png"}),
    "gif": frozenset({".gif"}),
    "webp": frozenset({".webp"}),
}
_SUPPORTED_LABEL = "PNG、JPEG、GIF、WebP"

# PNG 的 IHDR 位置：8 字节签名 + 4 字节长度 + 4 字节类型名，数据区第 10 字节是 colour type
_PNG_IHDR_COLOUR_TYPE_OFFSET = 25
# 有 alpha 通道的 PNG colour type：4=灰度+alpha、6=真彩+alpha；3=调色板（透明度在 tRNS 块）
_PNG_ALPHA_COLOUR_TYPES = frozenset({4, 6})
_PNG_PALETTE_COLOUR_TYPE = 3
# VP8X 的数据区起点（RIFF 12 字节 + 块长度 4 + 块类型 4），首字节的 bit4 是 alpha 标志
_WEBP_VP8X_FLAGS_OFFSET = 20
_WEBP_VP8X_ALPHA_MASK = 0x10
# VP8L：数据区首字节是签名 0x2f，其后 4 字节小端存放 宽14/高14/alpha1/version3
_WEBP_VP8L_SIGNATURE_OFFSET = 20
_WEBP_VP8L_SIGNATURE = 0x2F
_WEBP_VP8L_HEADER_OFFSET = 21
_WEBP_VP8L_ALPHA_SHIFT = 28


def detect_image_format(content: bytes) -> str | None:
    """按文件签名识别参考图格式；不能识别时返回 ``None``。"""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return None


def _png_has_alpha(content: bytes) -> bool:
    """PNG：先看 IHDR 的 colour type，调色板图再找 ``tRNS`` 块。"""
    if len(content) > _PNG_IHDR_COLOUR_TYPE_OFFSET and content[12:16] == b"IHDR":
        colour_type = content[_PNG_IHDR_COLOUR_TYPE_OFFSET]
        if colour_type in _PNG_ALPHA_COLOUR_TYPES:
            return True
        if colour_type != _PNG_PALETTE_COLOUR_TYPE:
            return False
    # colour type 读不出来（截断 / 头异常）时不早退，仍按块扫描兜底
    offset = 8
    while offset + 8 <= len(content):
        length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        if chunk_type == b"tRNS":
            return True
        if chunk_type == b"IEND":
            return False
        offset += 12 + length
    return False


def _webp_has_alpha(content: bytes) -> bool:
    """WebP：``VP8X`` 的 alpha 标志位，或 ``VP8L`` 头部的 alpha 位。"""
    chunk_type = content[12:16]
    if chunk_type == b"VP8X" and len(content) > _WEBP_VP8X_FLAGS_OFFSET:
        return bool(content[_WEBP_VP8X_FLAGS_OFFSET] & _WEBP_VP8X_ALPHA_MASK)
    if chunk_type == b"VP8L" and len(content) >= _WEBP_VP8L_HEADER_OFFSET + 4:
        if content[_WEBP_VP8L_SIGNATURE_OFFSET] != _WEBP_VP8L_SIGNATURE:
            return False
        header = int.from_bytes(content[_WEBP_VP8L_HEADER_OFFSET : _WEBP_VP8L_HEADER_OFFSET + 4], "little")
        return bool((header >> _WEBP_VP8L_ALPHA_SHIFT) & 1)
    return False


def detect_alpha(content: bytes, image_format: str | None = None) -> bool:
    """参考图字节 → 是否带透明通道（仅头部解析，无图像处理依赖）。"""
    fmt = image_format or detect_image_format(content)
    if fmt == "png":
        return _png_has_alpha(content)
    if fmt == "webp":
        return _webp_has_alpha(content)
    return False


def detect_alpha_file(path: str | Path) -> bool:
    """已落在上传目录中的参考图 → 是否带透明通道（提交时判定请求事实用）。"""
    try:
        content = Path(path).read_bytes()
    except OSError:  # noqa: BLE001 — 读不到就当没有；提交校验另有存在性检查负责报错
        return False
    return detect_alpha(content)


def validate_reference_image_bytes(content: bytes, filename: str | None = None) -> str:
    """确认内容是受支持且大小合规的参考图，返回识别出的格式。"""
    if not content:
        raise CustomException(msg="参考图内容为空", code=400, status_code=400)
    if len(content) > settings.MAX_FILE_SIZE:
        limit_mb = settings.MAX_FILE_SIZE // (1024 * 1024)
        raise CustomException(msg=f"参考图大小超过限制，最大允许 {limit_mb}MB", code=400, status_code=400)

    image_format = detect_image_format(content)
    if image_format is None:
        raise CustomException(msg=f"参考文件不是受支持的图片，仅支持 {_SUPPORTED_LABEL}", code=400, status_code=400)

    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix not in _FORMAT_EXTENSIONS[image_format]:
            raise CustomException(msg=f"参考图扩展名与图片内容不一致，仅支持 {_SUPPORTED_LABEL}", code=400, status_code=400)
    return image_format


def validate_reference_image_file(path: str | Path) -> None:
    """校验已经落在上传目录中的参考图，防止调用方绕过专用上传入口。"""
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise CustomException(msg=f"参考图不存在或无法读取: {file_path}", code=400, status_code=400) from exc
    if size > settings.MAX_FILE_SIZE:
        limit_mb = settings.MAX_FILE_SIZE // (1024 * 1024)
        raise CustomException(msg=f"参考图大小超过限制，最大允许 {limit_mb}MB", code=400, status_code=400)
    try:
        content = file_path.read_bytes()
    except OSError as exc:
        raise CustomException(msg=f"参考图不存在或无法读取: {file_path}", code=400, status_code=400) from exc
    validate_reference_image_bytes(content, file_path.name)


__all__ = [
    "detect_alpha",
    "detect_alpha_file",
    "detect_image_format",
    "validate_reference_image_bytes",
    "validate_reference_image_file",
]
