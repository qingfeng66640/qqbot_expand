"""接收 qqbot_adapter 标准互动事件并调度插件 worker。"""
from __future__ import annotations

import copy
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.kernel.concurrency import get_task_manager
from src.kernel.event import EventDecision

from ..src.interaction import InteractionContext

logger = get_logger("qqbot_expand")

_EXPECTED_KEYS = {
    "event_id",
    "interaction_id",
    "interaction_type",
    "scene",
    "chat_type",
    "target_type",
    "target_id",
    "operator_openid",
    "button_id",
    "button_data",
    "raw_event",
}


class QQBotInteractionEventHandler(BaseEventHandler):
    """消费标准化 INTERACTION_CREATE EventBus 事件。"""

    name = "qqbot_interaction"
    handler_name = "qqbot_interaction"
    description = "路由并应答 QQ 按钮互动事件"
    handler_description = "路由并应答 QQ 按钮互动事件"
    init_subscribe = ["qqbot_adapter.interaction_create"]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """校验固定键集、独立复制上下文并通过 TaskManager 调度。"""
        try:
            interaction_config = getattr(self.plugin.config, "interaction", None)
            if not bool(getattr(interaction_config, "enabled", True)):
                return EventDecision.PASS, params
            if event_name != self.init_subscribe[0] or not _EXPECTED_KEYS.issubset(params):
                logger.warning("收到缺少必填字段或未知来源的 QQ 互动事件，已跳过")
                return EventDecision.PASS, params
            interaction_id = params["interaction_id"]
            if not isinstance(interaction_id, str) or not interaction_id:
                return EventDecision.PASS, params
            runtime = self.plugin.interaction_runtime
            if not await runtime.claim_processing(interaction_id):
                return EventDecision.PASS, params
            try:
                context = InteractionContext(
                    event_id=params["event_id"],
                    interaction_id=interaction_id,
                    interaction_type=params["interaction_type"],
                    scene=params["scene"],
                    chat_type=params["chat_type"],
                    target_type=params["target_type"],
                    target_id=params["target_id"],
                    operator_openid=params["operator_openid"],
                    button_id=params["button_id"],
                    button_data=params["button_data"],
                    raw_event=copy.deepcopy(params["raw_event"]),
                )
            except Exception:  # noqa: BLE001 - 失败时撤销已认领 ID
                await runtime.release_processing(interaction_id, forget_processed=True)
                raise
            coroutine = runtime.process(context)
            try:
                task_info = get_task_manager().create_task(
                    coroutine,
                    name=f"qqbot_interaction:{interaction_id}",
                    daemon=False,
                )
                runtime.track_task(task_info)
            except Exception:  # noqa: BLE001 - 调度失败需释放 claim 且不击穿 EventBus
                coroutine.close()
                await runtime.release_processing(
                    interaction_id, forget_processed=True
                )
                logger.error(f"调度 QQ 互动事件失败: id={interaction_id}")
                return EventDecision.PASS, params
            return EventDecision.SUCCESS, params
        except Exception:  # noqa: BLE001 - EventBus 边界不向外抛异常
            logger.error("QQ 互动事件处理器执行失败")
            return EventDecision.PASS, params
