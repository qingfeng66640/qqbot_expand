"""提供受控的 QQ 消息撤回与分享链接工具。"""
from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.base import BaseTool

from ..services.utility_service import QQBotUtilityService
from ..src.targets import resolve_target

__all__ = ["QQRecallCurrentMessageTool", "QQGenerateShareLinkTool"]


class QQRecallCurrentMessageTool(BaseTool):
    """撤回本插件近期发送到当前会话的消息。"""

    tool_name = "qq_recall_current_message"
    tool_description = "撤回本插件两分钟内发送到当前 QQ 会话的一条消息，必须明确确认。"
    associated_platforms = ["qq"]

    async def execute(
        self,
        message_id: Annotated[str, "要撤回的本插件消息 ID"],
        confirm: Annotated[bool, "确认撤回该消息，必须为 true"],
    ) -> tuple[bool, str | dict]:
        """撤回当前会话中记录的插件消息。

        Args:
            message_id: 要撤回的本插件消息 ID。
            confirm: 是否明确确认撤回，必须为 ``True``。

        Returns:
            操作是否成功，以及撤回结果或错误描述。
        """
        if confirm is not True:
            return False, "必须将 confirm 设为 true 才能撤回消息"
        target = resolve_target(self.trigger_message)
        if target is None:
            return False, "无法从当前会话推导 QQ 发送目标"
        result = await QQBotUtilityService(self.plugin).recall_message(
            target.target_type, target.target_id, message_id
        )
        return (True, result["data"]) if result["success"] else (False, result["error"])


class QQGenerateShareLinkTool(BaseTool):
    """按显式参数生成机器人分享链接。"""

    tool_name = "qq_generate_share_link"
    tool_description = "生成机器人分享链接；仅在用户明确要求分享或邀请机器人时使用。"
    associated_platforms = ["qq"]

    async def execute(
        self,
        callback_data: Annotated[str, "可选回传标识，最长 32 个字符"] = "",
    ) -> tuple[bool, str | dict]:
        """生成机器人分享链接。

        Args:
            callback_data: 可选回传标识，最长 32 个字符。

        Returns:
            操作是否成功，以及生成的分享链接或错误描述。
        """
        result = await QQBotUtilityService(self.plugin).generate_share_link(
            callback_data=callback_data
        )
        return (True, result["data"]) if result["success"] else (False, result["error"])
