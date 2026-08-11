"""QQ 当前群基本信息与机器人群内状态 Service。"""
from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure

__all__ = ["QQBotGroupInfoService"]


class QQBotGroupInfoService(BaseService):
    """查询 QQ 群基本信息和机器人在群内的状态。"""

    service_name = "qqbot_group_info"
    service_description = "查询 QQ 群基本信息与机器人群内状态"
    version = "0.4.0"

    def _service_enabled(self) -> bool:
        """读取只读群信息 Service 开关。"""
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        return bool(getattr(features, "enable_group_info_service", True))

    async def _request(self, suffix: str, group_openid: str) -> dict[str, Any]:
        """校验群 OpenID 后调用只读接口。"""
        if not self._service_enabled():
            return failure("群信息 Service 未启用")
        error, encoded = encode_path_segment(group_openid, "group_openid")
        if error:
            return failure(error)
        return await api_request(self.plugin, "GET", f"/v2/groups/{encoded}/{suffix}")

    async def get_group_info(self, group_openid: str) -> dict[str, Any]:
        """获取指定群的基本信息。"""
        return await self._request("info", group_openid)

    async def get_bot_group_state(self, group_openid: str) -> dict[str, Any]:
        """获取机器人在指定群内的状态。"""
        return await self._request("bot_state", group_openid)
