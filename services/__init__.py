"""qqbot_expand Service 组件包。"""
from __future__ import annotations

from .chunked_media_service import QQBotChunkedMediaService
from .group_admin_service import QQBotGroupAdminService
from .group_info_service import QQBotGroupInfoService
from .interaction_service import QQBotInteractionService
from .menu_panel_service import QQBotMenuPanelService
from .message_service import QQBotMessageService
from .raw_service import QQBotRawService
from .utility_service import QQBotUtilityService

__all__ = [
    "ALL_SERVICES",
    "QQBotChunkedMediaService",
    "QQBotGroupAdminService",
    "QQBotGroupInfoService",
    "QQBotInteractionService",
    "QQBotMenuPanelService",
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
    QQBotMenuPanelService,
    QQBotRawService,
    QQBotUtilityService,
]
