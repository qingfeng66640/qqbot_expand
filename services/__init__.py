"""qqbot_expand Service 组件包。

导出三个 Service：

- ``qqbot_message``：按钮 / ark / embed / 模板 Markdown / 引用回复发送
- ``qqbot_interaction``：互动回调应答
- ``qqbot_raw``：任意 QQ 开放 API 调用与桥接状态探测
"""
from __future__ import annotations

from .interaction_service import QQBotInteractionService
from .message_service import QQBotMessageService
from .raw_service import QQBotRawService

__all__ = [
    "ALL_SERVICES",
    "QQBotInteractionService",
    "QQBotMessageService",
    "QQBotRawService",
]

ALL_SERVICES: list[type] = [
    QQBotMessageService,
    QQBotInteractionService,
    QQBotRawService,
]
