"""qqbot_expand Service 组件包。"""
from __future__ import annotations

from .chunked_media_service import QQBotChunkedMediaService
from .group_admin_service import QQBotGroupAdminService
from .group_info_service import QQBotGroupInfoService
from .group_members_service import QQBotGroupMembersService
from .interaction_service import QQBotInteractionService
from .material_service import QQBotMaterialService
from .menu_panel_service import QQBotMenuPanelService
from .message_service import QQBotMessageService
from .raw_service import QQBotRawService
from .utility_service import QQBotUtilityService
from .user_info_service import QQBotUserInfoService

__all__ = [
    "ALL_SERVICES",
    "QQBotChunkedMediaService",
    "QQBotGroupAdminService",
    "QQBotGroupInfoService",
    "QQBotGroupMembersService",
    "QQBotInteractionService",
    "QQBotMaterialService",
    "QQBotMenuPanelService",
    "QQBotMessageService",
    "QQBotRawService",
    "QQBotUtilityService",
    "QQBotUserInfoService",
]

ALL_SERVICES: list[type] = [
    QQBotMessageService,
    QQBotChunkedMediaService,
    QQBotMaterialService,
    QQBotGroupAdminService,
    QQBotGroupInfoService,
    QQBotGroupMembersService,
    QQBotInteractionService,
    QQBotMenuPanelService,
    QQBotRawService,
    QQBotUtilityService,
    QQBotUserInfoService,
]
