"""QQ 消息撤回与机器人分享链接 Service。"""
from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure
from ..src.constants import TARGET_TYPE_GROUP, TARGET_TYPE_USER

__all__ = ["QQBotUtilityService"]


class QQBotUtilityService(BaseService):
    """提供受控的撤回与分享链接 API。"""

    service_name = "qqbot_utility"
    service_description = "撤回本插件近期发送的 QQ 消息，并生成机器人分享链接"
    version = "0.4.0"

    async def recall_message(
        self, target_type: str, target_id: str, message_id: str
    ) -> dict[str, Any]:
        """撤回本插件在两分钟内发送到对应目标的消息。"""
        if target_type not in {TARGET_TYPE_GROUP, TARGET_TYPE_USER}:
            return failure("target_type 必须为 user 或 group")
        target_error, encoded_target = encode_path_segment(target_id, "target_id")
        message_error, encoded_message = encode_path_segment(message_id, "message_id")
        if target_error or message_error:
            return failure(target_error or message_error or "message_id 不能为空")
        registry = getattr(self.plugin, "sent_messages", None)
        if registry is None or not registry.claim(message_id, target_type, target_id):
            return failure("只能撤回本插件两分钟内发送到当前目标的消息")
        path_type = "groups" if target_type == TARGET_TYPE_GROUP else "users"
        return await api_request(
            self.plugin,
            "DELETE",
            f"/v2/{path_type}/{encoded_target}/messages/{encoded_message}",
        )

    async def generate_share_link(
        self, *, url_link: str = "", callback_data: str = ""
    ) -> dict[str, Any]:
        """生成机器人分享链接。"""
        if not isinstance(url_link, str) or not isinstance(callback_data, str):
            return failure("url_link 与 callback_data 必须是字符串")
        if len(callback_data) > 32:
            return failure("callback_data 最长 32 个字符")
        body: dict[str, Any] = {}
        if url_link.strip():
            body["url_link"] = url_link.strip()
        if callback_data:
            body["callback_data"] = callback_data
        return await api_request(self.plugin, "POST", "/v2/generate_url_link", body)
