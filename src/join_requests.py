"""GROUP_JOIN_REQUEST 的受信回调注册、去重与任务协调。"""
from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("qqbot_expand.join_request")

JoinRequestCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class JoinRequestRuntime:
    """协调入群申请事件的受信回调。"""

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._callbacks: dict[str, JoinRequestCallback] = {}
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._tasks: list[Any] = []
        self._lock = asyncio.Lock()

    async def claim(self, join_request_id: str) -> bool:
        """在 TTL 内只接受一次相同申请事件。"""
        async with self._lock:
            now = time.monotonic()
            ttl = float(getattr(getattr(self._plugin.config, "interaction", None), "dedup_ttl", 300.0))
            while self._seen and next(iter(self._seen.values())) + ttl <= now:
                self._seen.popitem(last=False)
            if join_request_id in self._seen:
                return False
            self._seen[join_request_id] = now
            return True

    async def register(self, name: str, callback: JoinRequestCallback, *, replace: bool = False) -> bool:
        """注册受信异步或同步回调。"""
        if not isinstance(name, str) or not name.strip() or not callable(callback):
            return False
        async with self._lock:
            if name in self._callbacks and not replace:
                return False
            self._callbacks[name] = callback
            return True

    async def unregister(self, name: str) -> bool:
        """注销指定回调。"""
        async with self._lock:
            return self._callbacks.pop(name, None) is not None

    async def process(self, params: dict[str, Any]) -> None:
        """顺序执行事件快照中的受信回调，不自动审批。"""
        async with self._lock:
            callbacks = list(self._callbacks.items())
        for name, callback in callbacks:
            try:
                result = callback(copy.deepcopy(params))
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"入群申请回调失败: name={name} error={exc}")

    def track_task(self, task_info: Any) -> None:
        """登记本运行时创建的任务。"""
        self._tasks.append(task_info)

    async def close(self) -> None:
        """取消任务并清空回调与去重状态。"""
        for task_info in tuple(self._tasks):
            cancel = getattr(task_info, "cancel", None)
            if callable(cancel):
                cancel()
        self._tasks.clear()
        async with self._lock:
            self._callbacks.clear()
            self._seen.clear()
