"""qqbot_expand Tool 组件包。"""
from __future__ import annotations

from .group_admin import QQReviewGroupJoinRequestTool, QQSetGroupMemberMuteTool
from .group_info import QQGetCurrentGroupBotStateTool, QQGetCurrentGroupInfoTool
from .menu_panel import ALL_MENU_PANEL_TOOLS
from .send_ark import QQSendArkTool
from .send_keyboard import QQSendKeyboardTool
from .send_reply import QQSendReplyTool
from .utility import QQGenerateShareLinkTool, QQRecallCurrentMessageTool

__all__ = [
    "ALL_GROUP_ADMIN_TOOLS",
    "ALL_GROUP_INFO_TOOLS",
    "ALL_MENU_PANEL_TOOLS",
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
