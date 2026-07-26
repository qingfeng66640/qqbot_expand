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

from .send_ark import QQSendArkTool
from .send_keyboard import QQSendKeyboardTool
from .send_reply import QQSendReplyTool

__all__ = [
    "ALL_TOOLS",
    "QQSendArkTool",
    "QQSendKeyboardTool",
    "QQSendReplyTool",
]

ALL_TOOLS: list[type] = [
    QQSendKeyboardTool,
    QQSendArkTool,
    QQSendReplyTool,
]
