"""消费 qqbot_adapter 发布的 GROUP_JOIN_REQUEST 事件。"""
from __future__ import annotations

import copy
from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.concurrency import get_task_manager
from src.kernel.event import EventDecision

from ..src.join_requests import JoinRequestRuntime

__all__ = ["QQBotGroupJoinRequestEventHandler"]


class QQBotGroupJoinRequestEventHandler(BaseEventHandler):
    """仅分发入群申请事件给受信回调，绝不自动审批。"""

    name = "qqbot_group_join_request"
    handler_name = "qqbot_group_join_request"
    description = "分发 QQ 群入群申请事件给受信回调"
    handler_description = "分发 QQ 群入群申请事件给受信回调"
    init_subscribe = ["qqbot_adapter.group_join_request"]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """去重后交由 TaskManager 异步执行回调，保持 EventBus 参数不变。"""
        if event_name != self.init_subscribe[0]:
            return EventDecision.PASS, params
        request_id = params.get("join_request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            return EventDecision.PASS, params
        runtime: JoinRequestRuntime = self.plugin.join_request_runtime
        if not await runtime.claim(request_id):
            return EventDecision.PASS, params
        coroutine = runtime.process(copy.deepcopy(params))
        try:
            task_info = get_task_manager().create_task(
                coroutine,
                name=f"qqbot_group_join_request:{request_id}",
                daemon=False,
            )
        except Exception:
            coroutine.close()
            return EventDecision.PASS, params
        runtime.track_task(task_info)
        return EventDecision.SUCCESS, params
