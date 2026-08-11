"""qqbot_expand Service 组件包。

导出三个 Service：

- ``qqbot_message``：按钮 / ark / embed / 模板 Markdown / 引用回复发送
- ``qqbot_group_admin``：群入群审批策略、申请审批与成员禁言
- ``qqbot_interaction``：互动回调应答
- ``qqbot_raw``：任意 QQ 开放 API 调用与桥接状态探测
"""
from __future__ import annotations

from .chunked_media_service import QQBotChunkedMediaService
from .group_admin_service import QQBotGroupAdminService
from .group_info_service import QQBotGroupInfoService
from .interaction_service import QQBotInteractionService
from .message_service import QQBotMessageService
from .raw_service import QQBotRawService
from .utility_service import QQBotUtilityService

__all__ = [
    "ALL_SERVICES",
    "QQBotChunkedMediaService",
    "QQBotGroupAdminService",
    "QQBotGroupInfoService",
    "QQBotInteractionService",
    "QQBotMessageService",
    "QQBotRawService",
    "QQBotUtilityService",
]

ALL_SERVICES: list[type] = [
    QQBotMessageService,
    QQBotChunkedMediaService,
    QQBotGroupAdminService,
    QQBotGroupInfoService,
    QQBotInteractionService,
    QQBotRawService,
    QQBotUtilityService,
]
