"""受信调用方使用的 QQ 本地字节分片媒体上传 Service。"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import httpx

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure, success
from ..src.constants import MEDIA_FILE_TYPES, TARGET_TYPE_GROUP, TARGET_TYPE_USER
from ..src.cos_transport import private_cos_diagnostics, validate_presigned_url

__all__ = ["QQBotChunkedMediaService"]

_MAX_FILE_SIZE = 200 * 1024 * 1024
_MD5_10M_SIZE = 10_002_432


def _plan_parts(parts: list[Any], file_size: int) -> list[tuple[int, str, int, int]]:
    """在上传前验证完整分片计划；末片可短于分片容量。"""
    indices = [part.get("index") if isinstance(part, dict) else None for part in parts]
    if indices:
        start = indices[0]
        if (type(start) is not int or start not in (0, 1)
                or any(type(index) is not int or index != expected
                       for expected, index in enumerate(indices, start=start))):
            raise ValueError("预上传响应的分片索引必须是从 0 或 1 连续递增的整数")

    plan: list[tuple[int, str, int, int]] = []
    urls: set[str] = set()
    offset = 0
    for part in parts:
        size = part.get("block_size")
        if isinstance(size, str) and re.fullmatch(r"[0-9]{1,9}", size):
            size = int(size)
        if type(size) is not int or not 0 < size <= _MAX_FILE_SIZE:
            raise ValueError("预上传响应包含无效分片")
        try:
            url = validate_presigned_url(part.get("presigned_url"))
            normalized_url = str(httpx.URL(url))
            scheme_end = url.find("://") + 3
            authority_end = url.find("/", scheme_end)
            equivalent_url = url
            if authority_end >= scheme_end and url[authority_end - 4:authority_end] == ":443":
                # Only the explicitly allowed default port may be removed; keep the
                # signed path and query byte-for-byte as returned by QQ.
                equivalent_url = url[:authority_end - 4] + url[authority_end:]
            if normalized_url != equivalent_url or equivalent_url in urls:
                raise ValueError
        except (ValueError, httpx.InvalidURL):
            raise ValueError("预上传响应包含无效分片") from None
        if offset >= file_size:
            raise ValueError("预上传响应分片大小与文件不匹配")
        end = min(offset + size, file_size)
        plan.append((part["index"], url, offset, end))
        urls.add(equivalent_url)
        offset = end
    if offset != file_size:
        raise ValueError("预上传响应分片大小与文件不匹配")
    return plan


class QQBotChunkedMediaService(BaseService):
    """将受信调用方提供的内存字节按官方流程上传并合并。"""

    service_name = "qqbot_chunked_media"
    service_description = "通过 QQ 官方分片协议上传受信调用方提供的媒体字节"
    version = "0.4.0"

    async def upload_bytes(
        self, target_type: str, target_id: str, file_type: int, file_name: str, content: bytes
    ) -> dict[str, Any]:
        """上传 bytes 并返回官方的 ``file_info``。"""
        if target_type not in {TARGET_TYPE_GROUP, TARGET_TYPE_USER}:
            return failure("target_type 必须为 user 或 group")
        if file_type not in MEDIA_FILE_TYPES or isinstance(file_type, bool):
            return failure("file_type 不合法")
        if not isinstance(file_name, str) or not file_name.strip():
            return failure("file_name 不能为空")
        if not isinstance(content, bytes) or not content:
            return failure("content 必须为非空 bytes")
        if len(content) > _MAX_FILE_SIZE:
            return failure("文件不能超过 200MB")
        error, encoded_target = encode_path_segment(target_id, "target_id")
        if error:
            return failure(error)
        resource = "groups" if target_type == TARGET_TYPE_GROUP else "users"
        digest = hashlib.md5(content).hexdigest()
        prepare = await api_request(
            self.plugin,
            "POST",
            f"/v2/{resource}/{encoded_target}/upload_prepare",
            {
                "file_type": file_type,
                "file_size": str(len(content)),
                "file_name": file_name.strip(),
                "md5": digest,
                "sha1": hashlib.sha1(content).hexdigest(),
                "md5_10m": hashlib.md5(content[:_MD5_10M_SIZE]).hexdigest(),
            },
        )
        if not prepare["success"]:
            return prepare
        data = prepare["data"] or {}
        upload_id = data.get("upload_id")
        parts = data.get("parts")
        if not isinstance(upload_id, str) or not upload_id or not isinstance(parts, list):
            return failure("预上传响应缺少 upload_id 或 parts")
        try:
            plan = _plan_parts(parts, len(content))
        except ValueError as exc:
            return failure(str(exc))
        client = getattr(self.plugin, "chunked_client", None)
        if client is None:
            return failure("HTTP 客户端未初始化")
        for index, url, start, end in plan:
            chunk = content[start:end]
            try:
                # 专用 COS 连接池不共享 QQ 鉴权，不在日志暴露签名 URL。
                with private_cos_diagnostics():
                    request = httpx.Request("PUT", url, content=chunk)
                    response = await client.send(request, auth=None, follow_redirects=False, stream=True)
                    await response.aclose()
            except Exception:
                return failure("上传分片失败")
            if not 200 <= response.status_code < 300:
                return failure(f"上传分片返回 HTTP {response.status_code}")
            finished = await api_request(
                self.plugin,
                "POST",
                f"/v2/{resource}/{encoded_target}/upload_part_finish",
                {"upload_id": upload_id, "part_index": index, "block_size": str(len(chunk)), "md5": hashlib.md5(chunk).hexdigest()},
            )
            if not finished["success"]:
                return finished
        merged = await api_request(
            self.plugin,
            "POST",
            f"/v2/{resource}/{encoded_target}/files",
            {"file_type": file_type, "file_name": file_name.strip(), "upload_id": upload_id, "srv_send_msg": False},
        )
        if not merged["success"]:
            return merged
        receipt = merged["data"]
        file_info = receipt.get("file_info")
        if not isinstance(file_info, str) or not file_info.strip():
            return failure("QQ API 合并响应缺少 file_info")
        # QQ 可额外返回 raw_url（短期 COS 签名下载地址），不把它交给调用方。
        return success({key: receipt[key] for key in ("file_info", "file_uuid", "ttl") if key in receipt})
