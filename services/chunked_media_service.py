"""受信调用方使用的 QQ 本地字节分片媒体上传 Service。"""
from __future__ import annotations

import hashlib
from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure
from ..src.constants import MEDIA_FILE_TYPES, TARGET_TYPE_GROUP, TARGET_TYPE_USER

__all__ = ["QQBotChunkedMediaService"]

_MAX_FILE_SIZE = 200 * 1024 * 1024
_MD5_10M_SIZE = 10_002_432


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
        expected = list(range(len(parts)))
        if [part.get("index") if isinstance(part, dict) else None for part in parts] != expected:
            return failure("预上传响应的分片索引必须从 0 连续递增")
        offset = 0
        client = getattr(self.plugin, "http_client", None)
        if client is None:
            return failure("HTTP 客户端未初始化")
        for part in parts:
            if not isinstance(part, dict):
                return failure("预上传响应包含无效分片")
            try:
                size = int(part.get("block_size", 0))
            except (TypeError, ValueError):
                return failure("预上传响应包含无效分片")
            url = part.get("presigned_url")
            if size <= 0 or not isinstance(url, str) or not url.startswith("https://"):
                return failure("预上传响应包含无效分片")
            chunk = content[offset : offset + size]
            if not chunk:
                return failure("预上传响应分片大小与文件不匹配")
            try:
                response = await client.put(url, content=chunk)
            except Exception:
                return failure("上传分片失败")
            if response.status_code >= 400:
                return failure(f"上传分片返回 HTTP {response.status_code}")
            finished = await api_request(
                self.plugin,
                "POST",
                f"/v2/{resource}/{encoded_target}/upload_part_finish",
                {"upload_id": upload_id, "part_index": part["index"], "block_size": str(len(chunk)), "md5": hashlib.md5(chunk).hexdigest()},
            )
            if not finished["success"]:
                return finished
            offset += len(chunk)
        if offset != len(content):
            return failure("预上传响应分片大小与文件不匹配")
        return await api_request(
            self.plugin,
            "POST",
            f"/v2/{resource}/{encoded_target}/files",
            {"file_type": file_type, "file_name": file_name.strip(), "upload_id": upload_id, "srv_send_msg": False},
        )
