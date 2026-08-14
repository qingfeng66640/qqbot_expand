"""引用回复 Tool。

在多人群聊里，纯文本回复经常分不清在回应谁。本 Tool 让 LLM 明确引用某条
历史消息，客户端会把被引用的原文折叠展示在回复上方。

被引用索引可由 LLM 显式给出；不给则使用触发消息携带的 QQ ``msg_idx``。
"""
from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.base import BaseTool

from ..services.message_service import QQBotMessageService
from ..src.targets import resolve_target

__all__ = ["QQSendReplyTool"]


class QQSendReplyTool(BaseTool):
    """向当前会话发送一条引用回复。"""

    tool_name = "qq_send_reply"
    tool_description = (
        "在 QQ 会话中发送一条引用回复，被引用的原消息会折叠显示在回复上方。"
        "适合在多人群聊中明确回应某条具体消息，避免歧义。"
    )
    associated_platforms = ["qq"]

    async def execute(
        self,
        content: Annotated[str, "回复的文本内容，不能为空"],
        reference_message_id: Annotated[
            str, "被引用消息的 QQ 引用索引（msg_idx/ref_idx）；留空则使用触发消息的索引"
        ] = "",
    ) -> tuple[bool, str | dict]:
        """发送引用回复。

        Args:
            content: 回复文本。
            reference_message_id: 被引用消息 id，留空则用触发消息。

        Returns:
            ``(是否成功, 结果描述)``。
        """
        if not content or not content.strip():
            return False, "content 不能为空"

        target = resolve_target(self.trigger_message)
        if target is None:
            return False, "无法从当前会话推导 QQ 发送目标"

        reference_id = str(reference_message_id or "").strip() or target.ref_idx
        if not reference_id:
            return False, "当前消息没有可用的 QQ 引用索引"

        service = QQBotMessageService(self.plugin)
        result = await service.send_reply(
            target.target_type,
            target.target_id,
            content,
            reference_id,
            ignore_get_message_error=True,
            msg_id=target.msg_id,
        )
        if not result["success"]:
            return False, f"引用回复发送失败: {result['error']}"
        return True, {
            "message_id": result["message_id"],
            "ref_idx": result["ref_idx"],
            "reference_message_id": reference_id,
        }
