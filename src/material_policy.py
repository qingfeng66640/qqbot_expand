"""图片素材的纯校验与响应白名单；不保留临时预览地址。"""
from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

# 官方标注 10MB；本实现明确采用 10 MiB（10 * 1024 * 1024 字节）。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# 与 Pillow 默认解压炸弹警戒值一致，并对动画累计解码像素计数。
MAX_DECODED_PIXELS = 89_478_485
# 本地工作量预算，避免极小像素但超多帧的动画占用解码器；不是 QQ 协议上限。
MAX_IMAGE_FRAMES = 1024
_FORMATS = {"png": ("PNG", "image/png"), "jpg": ("JPEG", "image/jpeg"),
            "jpeg": ("JPEG", "image/jpeg"), "gif": ("GIF", "image/gif"),
            "webp": ("WEBP", "image/webp")}
_REFERENCE = re.compile(r"qqbot-media://image/v1/[A-Za-z0-9_~.=-]{1,4096}\Z")


class MaterialError(ValueError):
    """只包含本地固定安全文案的素材校验错误。"""


@dataclass(frozen=True)
class PreparedUpload:
    """来自受信 prepare 响应的短生命周期上传票据，repr 隐藏签名地址。"""

    upload_id: str
    expires_in: int
    presigned_url: str = field(repr=False)


def _text(value: Any, name: str, *, empty: bool = False, limit: int = 1024) -> str:
    """校验有界文本，禁止控制字符及被错误装进普通字段的 URL。"""
    if (not isinstance(value, str) or len(value) > limit
            or (not empty and not value.strip())
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "://" in value):
        raise MaterialError(f"{name} 不合法")
    return value


def validate_image_id(value: Any) -> str:
    """校验不透明 ID；保留可编码的特殊字符，不允许路径穿越。"""
    result = _text(value, "image_id")
    if result != result.strip() or ".." in result:
        raise MaterialError("image_id 不合法")
    return result


def validate_cursor(value: Any) -> str:
    """校验单页游标而不尝试解释其内部编码。"""
    return _text(value, "cursor", empty=True, limit=4096)


def _file_name(value: Any) -> str:
    """文件名仅接受 basename，不接受目录、驱动器或空白修饰。"""
    result = _text(value, "file_name", limit=255)
    if result != result.strip() or any(char in result for char in '/\\:'):
        raise MaterialError("file_name 必须是 basename")
    if "." not in result or not result.rsplit(".", 1)[0]:
        raise MaterialError("file_name 缺少有效扩展名")
    if result.rsplit(".", 1)[1].lower() not in _FORMATS:
        raise MaterialError("图片仅支持 png/jpeg/gif/webp")
    return result


def validate_image_input(file_name: str, content: bytes, content_type: str) -> str:
    """提交 CPU 执行器前进行廉价的名称、类型和字节预算校验。"""
    name = _file_name(file_name)
    if not isinstance(content, bytes) or not content:
        raise MaterialError("content 必须为非空 bytes")
    if len(content) > MAX_IMAGE_BYTES:
        raise MaterialError("图片不能超过 10 MiB（10485760 字节）")
    _image_format, mime = _FORMATS[name.rsplit(".", 1)[1].lower()]
    if content_type != mime:
        raise MaterialError("文件扩展名与 Content-Type 不一致")
    return name


def image_payload(file_name: str, content: bytes, content_type: str) -> dict[str, Any]:
    """验证真实解码类型、帧数和像素预算，生成 prepare JSON；由 CPU 进程池运行。"""
    name = validate_image_input(file_name, content, content_type)
    image_format, mime = _FORMATS[name.rsplit(".", 1)[1].lower()]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format != image_format:
                    raise MaterialError("真实图片类型与扩展名/Content-Type 不一致")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                frames = getattr(image, "n_frames", 1)
                if not 1 <= frames <= MAX_IMAGE_FRAMES:
                    raise MaterialError("图片帧数超出本地安全预算（最多 1024 帧）")
                pixels = 0
                for frame in range(frames):
                    image.seek(frame)
                    pixels += image.width * image.height
                    if pixels > MAX_DECODED_PIXELS:
                        raise MaterialError("图片累计解码像素过大")
                    image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        if isinstance(exc, MaterialError):
            raise
        raise MaterialError("图片内容无效或解码大小超限") from None
    return {
        "file_name": name, "file_size": len(content), "content_type": mime,
        "md5": hashlib.md5(content, usedforsecurity=False).hexdigest(),
        "sha1": hashlib.sha1(content, usedforsecurity=False).hexdigest(),
    }


def validate_presigned_url(value: Any) -> str:
    """复用 COS 策略并保留素材错误契约；独立的图片校验 worker 不调用此函数。"""
    from .cos_transport import CosTransportError, validate_presigned_url as validate_url

    try:
        return validate_url(value)
    except CosTransportError as exc:
        raise MaterialError(str(exc)) from None


def _integer(value: Any, name: str, *, maximum: int, minimum: int = 0) -> int:
    """拒绝 bool、浮点和字符串，严格校验协议整数范围。"""
    if type(value) is not int or not minimum <= value <= maximum:
        raise MaterialError(f"{name} 不合法")
    return value


def _list_timestamp(value: Any, name: str) -> int:
    """校验素材列表的非负 int64 时间戳，兼容 QQ 的 ASCII 十进制字符串。"""
    if isinstance(value, str) and len(value) <= 19 and value.isascii() and value.isdecimal():
        value = int(value)
    return _integer(value, name, maximum=2**63 - 1)


def parse_prepared(data: Any) -> PreparedUpload:
    """验证 prepare 的三个必要字段，绝不把票据回传调用方。"""
    if not isinstance(data, dict):
        raise MaterialError("prepare 响应不合法")
    return PreparedUpload(
        upload_id=_text(data.get("upload_id"), "upload_id"),
        expires_in=_integer(data.get("expires_in"), "expires_in", minimum=1, maximum=2**32 - 1),
        presigned_url=validate_presigned_url(data.get("presigned_url")),
    )


def _reference(value: Any) -> str:
    """只接受正式素材引用，不接受公网 URL 或带 query 的伪引用。"""
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
        raise MaterialError("素材引用不合法")
    return value


def completed_data(data: Any) -> dict[str, Any]:
    """只返回素材 ID、引用及滑动有效期窗口，剔除 preview_url。"""
    if not isinstance(data, dict):
        raise MaterialError("complete 响应不合法")
    return {
        "image_id": validate_image_id(data.get("image_id")),
        "url": _reference(data.get("url")),
        "ttl": _integer(data.get("ttl"), "ttl", minimum=1, maximum=2**32 - 1),
    }


def listed_data(data: Any) -> dict[str, Any]:
    """单页查询的字段白名单，不缓存、不返回临时预览链接。"""
    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        raise MaterialError("图片列表响应不合法")
    if len(data["images"]) > 100:
        raise MaterialError("图片列表超出单机器人数量上限")
    images = []
    for item in data["images"]:
        if not isinstance(item, dict):
            raise MaterialError("图片列表条目不合法")
        images.append({
            "image_id": validate_image_id(item.get("image_id")),
            "url": _reference(item.get("url")), "file_name": _file_name(item.get("file_name")),
            "created_at": _list_timestamp(item.get("created_at"), "created_at"),
            "expires_at": _list_timestamp(item.get("expires_at"), "expires_at"),
        })
    total = data.get("total")
    # This count is uint64, unlike the two int64 timestamps above.
    if isinstance(total, str) and len(total) <= 20 and total.isascii() and total.isdecimal():
        total = int(total)
    return {"images": images, "next_cursor": validate_cursor(data.get("next_cursor")),
            "total": _integer(total, "total", maximum=2**64 - 1)}


def normalize_etag(value: str, expected_md5: str) -> str:
    """规范化普通 COS ETag，拒绝弱标签、分片标签及校验不匹配。"""
    digest = value.strip()
    if len(digest) == 34 and digest[0] == digest[-1] == '"':
        digest = digest[1:-1]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", digest) or digest.lower() != expected_md5:
        raise MaterialError("上传 ETag 缺失或与文件 MD5 不一致；未调用 complete")
    return digest.lower()
