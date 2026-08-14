"""从触发消息推导 QQ 发送目标。

Tool 的参数由 LLM 填写，若让它自行编造 openid 既不可靠也有越权风险。
本模块统一从 ``BaseTool.trigger_message`` 反推目标，LLM 只需关心消息内容。

推导依据（``qqbot_adapter`` 入站链路的既定字段）：

- 群聊：``message.chat_type == "group"``，群 openid 落在 ``message.extra["group_id"]``
  （由 ``MessageBuilder.from_group(group_openid)`` 写入）
- 私聊：目标即发送者，openid 落在 ``message.sender_id``
- 被动回复所需的 ``msg_id`` 取自 ``message.message_id``
- 引用回复所需的 ``ref_idx`` 取自 ``message.extra["qq_ref_idx"]``
"""
from __future__ import annotations

from typing import Any, NamedTuple

from .constants import TARGET_TYPE_GROUP, TARGET_TYPE_USER

__all__ = ["QQTarget", "resolve_target"]


class QQTarget(NamedTuple):
    """一次发送所需的目标信息。

    Attributes:
        target_type: ``"user"`` 或 ``"group"``。
        target_id: 目标 openid。
        msg_id: 被动回复关联的原始消息 id，缺失时为空串。
        ref_idx: 引用回复关联的消息索引，缺失时为空串。
    """

    target_type: str
    target_id: str
    msg_id: str
    ref_idx: str


def resolve_target(message: Any) -> QQTarget | None:
    """从触发消息推导发送目标。

    Args:
        message: ``BaseTool.trigger_message``，可能为 None。

    Returns:
        推导出的目标；消息缺失或平台不是 QQ 官方 Bot 时返回 None。
    """
    if message is None:
        return None

    msg_id = str(getattr(message, "message_id", "") or "").strip()
    extra = getattr(message, "extra", None) or {}
    ref_idx = str(extra.get("qq_ref_idx", "") or "").strip()

    if str(getattr(message, "chat_type", "") or "") == "group":
        group_openid = str(extra.get("group_id", "") or "").strip()
        if not group_openid:
            return None
        return QQTarget(TARGET_TYPE_GROUP, group_openid, msg_id, ref_idx)

    user_openid = str(getattr(message, "sender_id", "") or "").strip()
    if not user_openid:
        return None
    return QQTarget(TARGET_TYPE_USER, user_openid, msg_id, ref_idx)
