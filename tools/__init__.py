"""qqbot_expand Tool 组件包。

导出三个面向 LLM 的精选 Tool，均限定 ``associated_platforms = ["qq"]``，
只在 QQ 官方 Bot 会话中激活：

- ``qq_send_keyboard``：发按钮菜单
- ``qq_send_ark``：发 ark 富文本卡片
- ``qq_send_reply``：发引用回复

其余能力（embed、模板 Markdown、互动应答、raw 通道）只走 Service，
不暴露给 LLM。
"""
from __future__ import annotations

from .group_admin import QQReviewGroupJoinRequestTool, QQSetGroupMemberMuteTool
from .group_info import QQGetCurrentGroupBotStateTool, QQGetCurrentGroupInfoTool
from .send_ark import QQSendArkTool
from .send_keyboard import QQSendKeyboardTool
from .send_reply import QQSendReplyTool
from .utility import QQGenerateShareLinkTool, QQRecallCurrentMessageTool

__all__ = [
    "ALL_GROUP_ADMIN_TOOLS",
    "ALL_GROUP_INFO_TOOLS",
    "ALL_UTILITY_TOOLS",
    "ALL_TOOLS",
    "QQGetCurrentGroupBotStateTool",
    "QQGetCurrentGroupInfoTool",
    "QQReviewGroupJoinRequestTool",
    "QQSetGroupMemberMuteTool",
    "QQSendArkTool",
    "QQSendKeyboardTool",
    "QQSendReplyTool",
    "QQGenerateShareLinkTool",
    "QQRecallCurrentMessageTool",
]

ALL_GROUP_INFO_TOOLS: list[type] = [
    QQGetCurrentGroupInfoTool,
    QQGetCurrentGroupBotStateTool,
]

ALL_UTILITY_TOOLS: list[type] = [
    QQRecallCurrentMessageTool,
    QQGenerateShareLinkTool,
]

ALL_GROUP_ADMIN_TOOLS: list[type] = [
    QQReviewGroupJoinRequestTool,
    QQSetGroupMemberMuteTool,
]

ALL_TOOLS: list[type] = [
    QQSendKeyboardTool,
    QQSendArkTool,
    QQSendReplyTool,
]
