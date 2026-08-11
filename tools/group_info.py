"""当前 QQ 群的只读信息查询 Tool。"""
from __future__ import annotations

from src.app.plugin_system.base import BaseTool

from ..services.group_info_service import QQBotGroupInfoService
from ..src.constants import TARGET_TYPE_GROUP
from ..src.targets import resolve_target

__all__ = ["QQGetCurrentGroupInfoTool", "QQGetCurrentGroupBotStateTool"]


class _CurrentGroupTool(BaseTool):
    """从触发消息安全确定当前群。"""

    associated_platforms = ["qq"]

    def _current_group_openid(self) -> tuple[str | None, str]:
        """返回当前群 OpenID，拒绝非群会话和关闭的能力。"""
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        if not bool(getattr(features, "enable_tools", True)):
            return "QQ LLM 工具当前未启用", ""
        if not bool(getattr(features, "enable_group_info_tools", False)):
            return "群信息 LLM 工具当前未启用", ""
        if not bool(getattr(features, "enable_group_info_service", True)):
            return "群信息 Service 当前未启用", ""
        target = resolve_target(self.trigger_message)
        if target is None or target.target_type != TARGET_TYPE_GROUP:
            return "群信息工具只能在 QQ 群会话中使用", ""
        return None, target.target_id


class QQGetCurrentGroupInfoTool(_CurrentGroupTool):
    """查询当前 QQ 群基本信息。"""

    tool_name = "qq_get_current_group_info"
    tool_description = "查询当前 QQ 群的基本信息，返回群 OpenID、名称、简介、标签和成员数量。"

    async def execute(self) -> tuple[bool, str | dict]:
        """查询当前触发群的基本信息。"""
        error, group_openid = self._current_group_openid()
        if error:
            return False, error
        result = await QQBotGroupInfoService(self.plugin).get_group_info(group_openid)
        return (True, result["data"]) if result["success"] else (False, result["error"])


class QQGetCurrentGroupBotStateTool(_CurrentGroupTool):
    """查询机器人在当前 QQ 群中的状态。"""

    tool_name = "qq_get_current_group_bot_state"
    tool_description = "查询机器人在当前 QQ 群中的角色、入群时间、主动推送许可和收消息设置。"

    async def execute(self) -> tuple[bool, str | dict]:
        """查询机器人在当前触发群内的状态。"""
        error, group_openid = self._current_group_openid()
        if error:
            return False, error
        result = await QQBotGroupInfoService(self.plugin).get_bot_group_state(group_openid)
        return (True, result["data"]) if result["success"] else (False, result["error"])
