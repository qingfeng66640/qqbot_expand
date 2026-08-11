"""本插件发送消息的短期归属记录。"""
from __future__ import annotations

import time
from collections import OrderedDict

__all__ = ["SentMessageRegistry"]


class SentMessageRegistry:
    """记录本插件成功发送、且仍可能撤回的消息。"""

    def __init__(self, ttl: float = 120.0) -> None:
        self._ttl = ttl
        self._records: OrderedDict[str, tuple[str, str, float]] = OrderedDict()

    def record(self, message_id: str, target_type: str, target_id: str) -> None:
        """记录一条成功发送的消息。"""
        if message_id:
            self._records[message_id] = (target_type, target_id, time.monotonic())

    def claim(self, message_id: str, target_type: str, target_id: str) -> bool:
        """验证消息归属与撤回有效期，并消耗记录。"""
        record = self._records.get(message_id)
        if record is None:
            return False
        recorded_type, recorded_target, created_at = record
        if (recorded_type, recorded_target) != (target_type, target_id):
            return False
        if time.monotonic() - created_at > self._ttl:
            self._records.pop(message_id, None)
            return False
        self._records.pop(message_id, None)
        return True

    def clear(self) -> None:
        """清理全部记录。"""
        self._records.clear()
