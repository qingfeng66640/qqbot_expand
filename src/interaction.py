"""QQ 互动回调路由与去重运行时。"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.app.plugin_system.api import service_api
from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import TaskInfo

logger = get_logger("qqbot_expand")

_SAFE_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ACK_TYPES = {11, 12}


@dataclass(frozen=True)
class InteractionContext:
    """一次标准化 QQ 互动事件的独立快照。"""

    event_id: str
    interaction_id: str
    interaction_type: int | None
    scene: str
    chat_type: int | None
    target_type: str
    target_id: str
    operator_openid: str
    button_id: str
    button_data: str
    raw_event: dict[str, Any]


@dataclass(frozen=True)
class CallbackResult:
    """互动业务回调的结果。"""

    handled: bool
    ack_code: int
    message: str | None


PermissionCallback = Callable[[InteractionContext, str], bool | Awaitable[bool]]
InteractionCallback = Callable[
    [InteractionContext, str],
    CallbackResult | int | Awaitable[CallbackResult | int],
]


class InteractionRuntime:
    """管理互动路由、处理中 claim、ACK claim 与后台任务。"""

    def __init__(
        self, plugin: Any, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """初始化运行时并挂接插件配置。"""
        self.plugin = plugin
        self._clock = clock
        self._callbacks: dict[
            tuple[str, str], tuple[InteractionCallback, PermissionCallback | None]
        ] = {}
        self._processing: set[str] = set()
        self._processed: OrderedDict[str, float] = OrderedDict()
        self._consumed: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()
        self._tasks: dict[str, TaskInfo] = {}
        self._closed = False

    def register(
        self,
        namespace: str,
        action: str,
        callback: InteractionCallback,
        permission: PermissionCallback | None = None,
        *,
        replace: bool = False,
    ) -> bool:
        """按精确 ``(namespace, action)`` 注册回调。"""
        self._validate_route_part(namespace, "namespace")
        self._validate_route_part(action, "action")
        if not callable(callback):
            raise TypeError("callback 必须可调用")
        if permission is not None and not callable(permission):
            raise TypeError("permission 必须可调用")
        if self._closed:
            raise RuntimeError("互动运行时已关闭，不能注册回调")
        route = (namespace, action)
        if route in self._callbacks and not replace:
            return False
        self._callbacks[route] = (callback, permission)
        return True

    def unregister(
        self,
        namespace: str,
        action: str,
        callback: InteractionCallback | None = None,
    ) -> bool:
        """注销精确路由，可选择校验当前回调身份。"""
        route = (namespace, action)
        registered = self._callbacks.get(route)
        if registered is None or (
            callback is not None and registered[0] is not callback
        ):
            return False
        del self._callbacks[route]
        return True

    async def route(self, context: InteractionContext) -> CallbackResult:
        """解析 button_data 并执行权限与业务回调。"""
        parsed = self._parse_button_data(context.button_data)
        if parsed is None:
            return CallbackResult(False, 1, None)
        namespace, action, payload = parsed
        registered = self._callbacks.get((namespace, action))
        if registered is None:
            return CallbackResult(False, 1, None)
        callback, permission = registered
        timeout = self._callback_timeout
        try:
            if permission is not None:
                allowed = permission(context, payload)
                if inspect.isawaitable(allowed):
                    allowed = await asyncio.wait_for(allowed, timeout=timeout)
                if not allowed:
                    return CallbackResult(False, 4, None)
            result = callback(context, payload)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=timeout)
            return self._normalize_result(result)
        except Exception as exc:  # noqa: BLE001 - 回调不得击穿事件任务
            logger.warning(
                f"QQ 互动回调执行失败: id={context.interaction_id} error={exc}"
            )
            return CallbackResult(False, 1, None)

    async def process(self, context: InteractionContext) -> CallbackResult:
        """路由互动、按类型 ACK，并按需用 event_id 回复文本。"""
        try:
            result = await self.route(context)
            if context.interaction_type in _ACK_TYPES:
                from ..services.interaction_service import QQBotInteractionService

                try:
                    await QQBotInteractionService(self.plugin)._ack(
                        context.interaction_id,
                        result.ack_code,
                        owned_by_worker=True,
                    )
                except Exception as exc:  # noqa: BLE001 - ACK 不得重试
                    logger.warning(
                        f"QQ 互动 ACK 失败: id={context.interaction_id} error={exc}"
                    )
            if result.message:
                await self._send_message(context, result.message)
            return result
        except Exception as exc:  # noqa: BLE001 - worker 必须封闭异常
            logger.warning(
                f"处理 QQ 互动事件失败: id={context.interaction_id} error={exc}"
            )
            return CallbackResult(False, 1, None)
        finally:
            await self.release_processing(context.interaction_id)

    async def claim_processing(self, interaction_id: str) -> bool:
        """在调度任务前原子认领 interaction_id。"""
        async with self._lock:
            now = self._clock()
            self._prune_records(self._processed, now)
            if (
                self._closed
                or interaction_id in self._processing
                or interaction_id in self._processed
                or len(self._processed) >= self._dedup_capacity
            ):
                return False
            self._processing.add(interaction_id)
            self._processed[interaction_id] = now
            return True

    async def release_processing(
        self, interaction_id: str, *, forget_processed: bool = False
    ) -> None:
        """释放处理中 ID；仅任务调度失败时撤销已处理记录。"""
        async with self._lock:
            self._processing.discard(interaction_id)
            if forget_processed:
                self._processed.pop(interaction_id, None)

    async def claim_ack(
        self,
        interaction_id: str,
        *,
        owned_by_worker: bool = False,
    ) -> str:
        """认领 ACK，拒绝与已认领业务事件竞争的外部调用。"""
        async with self._lock:
            now = self._clock()
            self._prune_records(self._consumed, now)
            if interaction_id in self._consumed:
                return "duplicate"
            if interaction_id in self._processing and not owned_by_worker:
                return "processing"
            if len(self._consumed) >= self._dedup_capacity:
                logger.error(
                    "QQ 互动 ACK 去重表已满，本次拒绝 ACK 以避免重复应答: "
                    f"id={interaction_id}"
                )
                return "capacity"
            self._consumed[interaction_id] = now
            return "claimed"

    def track_task(self, task_info: TaskInfo) -> None:
        """登记由 TaskManager 创建的 worker。"""
        self._tasks[task_info.task_id] = task_info
        if task_info.task is not None:
            task_info.task.add_done_callback(
                lambda _task, task_id=task_info.task_id: self._tasks.pop(task_id, None)
            )

    async def close(self) -> None:
        """停止接受新任务、取消并等待现有 worker。"""
        async with self._lock:
            self._closed = True
            tasks = [
                info.task for info in self._tasks.values() if info.task is not None
            ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._tasks.clear()
            self._processing.clear()
            self._processed.clear()
            self._consumed.clear()
            self._callbacks.clear()

    async def reset(self) -> None:
        """插件重新加载前恢复可接收状态并清空旧互动数据。"""
        async with self._lock:
            self._closed = False
            self._tasks.clear()
            self._processing.clear()
            self._processed.clear()
            self._consumed.clear()
            self._callbacks.clear()

    async def _send_message(self, context: InteractionContext, message: str) -> None:
        """向可回复目标发送仅关联 event_id 的文本。"""
        if context.target_type not in {"user", "group"} or not context.target_id:
            logger.warning(
                f"QQ 互动结果无法回复到当前目标: id={context.interaction_id} "
                f"target_type={context.target_type or 'unknown'}"
            )
            return
        service = service_api.get_service("qqbot_expand:service:qqbot_message")
        if service is None:
            logger.warning("qqbot_message Service 未就绪，无法发送互动结果")
            return
        await service.send_text(
            context.target_type,
            context.target_id,
            message,
            event_id=context.event_id,
        )

    def _parse_button_data(self, button_data: Any) -> tuple[str, str, str] | None:
        """校验并解析 ``namespace:action:payload``。"""
        if not isinstance(button_data, str):
            return None
        if len(button_data) > self._button_data_max_length:
            return None
        parts = button_data.split(":", 2)
        if len(parts) != 3:
            return None
        namespace, action, payload = parts
        if not _SAFE_PART.fullmatch(namespace) or not _SAFE_PART.fullmatch(action):
            return None
        return namespace, action, payload

    @staticmethod
    def _normalize_result(result: CallbackResult | int) -> CallbackResult:
        """把业务返回值归一为 CallbackResult。"""
        if isinstance(result, CallbackResult):
            handled = result.handled
            ack_code = result.ack_code
            message = result.message
            if not isinstance(handled, bool):
                return CallbackResult(False, 1, None)
        else:
            handled = True
            ack_code = result
            message = None
        if (
            not isinstance(ack_code, int)
            or isinstance(ack_code, bool)
            or ack_code not in range(6)
        ):
            return CallbackResult(False, 1, None)
        if message is not None and not isinstance(message, str):
            return CallbackResult(False, 1, None)
        return CallbackResult(handled, ack_code, message)

    @staticmethod
    def _validate_route_part(value: str, label: str) -> None:
        """校验注册路由的安全字符与长度。"""
        if not isinstance(value, str) or not _SAFE_PART.fullmatch(value):
            raise ValueError(f"{label} 仅允许 1~64 位字母、数字、下划线或连字符")

    def _prune_records(self, records: OrderedDict[str, float], now: float) -> None:
        """在短临界区内惰性清理过期去重记录。"""
        cutoff = now - self._dedup_ttl
        while records:
            _, timestamp = next(iter(records.items()))
            if timestamp > cutoff:
                break
            records.popitem(last=False)

    @property
    def _interaction_config(self) -> Any:
        """返回互动配置段。"""
        return getattr(getattr(self.plugin, "config", None), "interaction", None)

    @property
    def _callback_timeout(self) -> float:
        return float(getattr(self._interaction_config, "callback_timeout", 5.0))

    @property
    def _button_data_max_length(self) -> int:
        return int(getattr(self._interaction_config, "button_data_max_length", 1024))

    @property
    def _dedup_ttl(self) -> float:
        return float(getattr(self._interaction_config, "dedup_ttl", 300.0))

    @property
    def _dedup_capacity(self) -> int:
        return int(getattr(self._interaction_config, "dedup_capacity", 4096))
