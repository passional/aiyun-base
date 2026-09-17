"""绘图工作台参考图校验。

参考图最终会被发送到外部绘图服务。调用方不能只信浏览器 MIME 或文件扩展名；
本模块按文件内容识别首期支持的位图格式，并在上传和绘图任务提交两个入口复用。
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
    "detect_image_format",
    "validate_reference_image_bytes",
    "validate_reference_image_file",
]
