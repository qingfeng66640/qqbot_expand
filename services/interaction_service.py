"""QQ 互动回调应答与 callback 注册 Service。

``qqbot_adapter`` 收到 ``INTERACTION_CREATE`` 后只发布
``qqbot_adapter.interaction_create``，不发送 ACK；本插件的 EventHandler 是 callback
链路的 ACK 所有者。只有 ``type=11``（消息按钮）与 ``type=12``（单聊快捷菜单）
需要调用 ``PUT /interactions/{interaction_id}``。

所有 Service 实例共享插件级 ``InteractionRuntime``。ACK 在网络请求前写入带 TTL 和容量
上限的去重表；即使请求超时或失败也不会自动重试，因为 QQ 可能已经收到第一次请求。
外部调用方不应再次应答已由 EventHandler 接管的 interaction_id。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request
from ..src.constants import (
    INTERACTION_ACK_REQUIRED_TYPES,
    INTERACTION_CODE_DESCRIPTIONS,
    INTERACTION_CODES,
    PATH_INTERACTION_ACK,
)

__all__ = ["QQBotInteractionService"]


class QQBotInteractionService(BaseService):
    """QQ 互动回调应答服务。"""

    service_name = "qqbot_interaction"
    service_description = (
        "应答 QQ 按钮互动回调（PUT /interactions/{id}），可自定义提示码"
    )
    version = "0.2.0"

    async def ack(self, interaction_id: str, code: int = 0) -> dict[str, Any]:
        """应答一次互动回调。"""
        return await self._ack(interaction_id, code, owned_by_worker=False)

    async def _ack(
        self,
        interaction_id: str,
        code: int,
        *,
        owned_by_worker: bool,
    ) -> dict[str, Any]:
        """应答一次互动回调。

        Args:
            interaction_id: 互动事件 id，取自 ``INTERACTION_CREATE`` 事件的 ``id`` 字段。
            code: 应答码，决定客户端弹出的提示文案。
                0 操作成功 / 1 操作失败 / 2 操作频繁 / 3 重复操作 /
                4 没有权限 / 5 仅管理员操作。
            owned_by_worker: 仅由内部 EventHandler worker 传入，用于确认当前业务所有权。

        Returns:
            包含 ``success``、``code``、``description``、``error`` 和
            ``duplicate`` 的稳定结构。
        """
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            return self._failure("interaction_id 不能为空", code)
        if not isinstance(code, int) or isinstance(code, bool):
            return self._failure("code 必须是整数", code)
        if code not in INTERACTION_CODES:
            return self._failure(
                f"code 只能是 {sorted(INTERACTION_CODES)} 之一，收到 {code}", code
            )

        normalized_id = interaction_id.strip()
        runtime = getattr(self.plugin, "interaction_runtime", None)
        ack_claim = (
            await runtime.claim_ack(normalized_id, owned_by_worker=owned_by_worker)
            if runtime is not None
            else "claimed"
        )
        if ack_claim == "duplicate":
            return {
                "success": True,
                "code": code,
                "description": INTERACTION_CODE_DESCRIPTIONS[code],
                "error": None,
                "duplicate": True,
            }
        if ack_claim == "processing":
            return self._failure("互动事件正在由 EventHandler 处理，不能抢先应答", code)
        if ack_claim == "capacity":
            return self._failure("ACK 去重容量已满，为避免重复应答已拒绝请求", code)

        path = PATH_INTERACTION_ACK.format(interaction_id=normalized_id)
        # 互动应答接口沙箱不支持，强制正式域名；ACK 网络失败也不得重试。
        result = await api_request(
            self.plugin,
            "PUT",
            path,
            {"code": code},
            force_production=True,
            retry_network_errors=False,
        )
        if not result["success"]:
            return self._failure(result["error"], code)
        return {
            "success": True,
            "code": code,
            "description": INTERACTION_CODE_DESCRIPTIONS[code],
            "error": None,
            "duplicate": False,
        }

    def register_callback(
        self,
        namespace: str,
        action: str,
        callback: Any,
        permission: Any = None,
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        """向插件共享运行时注册精确互动路由。"""
        try:
            registered = self.plugin.interaction_runtime.register(
                namespace,
                action,
                callback,
                permission,
                replace=replace,
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return {"success": False, "registered": False, "error": str(exc)}
        if not registered:
            return {
                "success": False,
                "registered": False,
                "error": "互动回调路由已注册；如需替换请设置 replace=True",
            }
        return {"success": True, "registered": True, "error": None}

    def unregister_callback(
        self,
        namespace: str,
        action: str,
        callback: Any = None,
    ) -> dict[str, Any]:
        """从插件共享运行时注销精确互动路由。"""
        removed = self.plugin.interaction_runtime.unregister(
            namespace, action, callback
        )
        if not removed:
            return {
                "success": False,
                "removed": False,
                "error": "互动回调路由不存在或 callback 身份不匹配",
            }
        return {"success": True, "removed": True, "error": None}

    @staticmethod
    def describe_code(code: int) -> str:
        """查询应答码对应的客户端提示文案。

        Args:
            code: 应答码。

        Returns:
            文案描述；未知 code 返回空串。
        """
        return INTERACTION_CODE_DESCRIPTIONS.get(code, "")

    @staticmethod
    def needs_ack(interaction_type: int) -> bool:
        """判断某个互动类型是否必须调用应答接口。

        官方只要求 ``type=11``（消息按钮）与 ``type=12``（单聊快捷菜单）应答，
        其余类型（消息反馈、清空会话、授权变更等）无需应答。

        Args:
            interaction_type: ``INTERACTION_CREATE`` 事件的 ``type`` 字段。

        Returns:
            是否需要应答。
        """
        return interaction_type in INTERACTION_ACK_REQUIRED_TYPES

    @staticmethod
    def _failure(error: str, code: int) -> dict[str, Any]:
        """构造应答失败返回体。

        Args:
            error: 已脱敏的错误描述。
            code: 本次尝试使用的应答码。

        Returns:
            统一返回结构。
        """
        return {
            "success": False,
            "code": code,
            "description": INTERACTION_CODE_DESCRIPTIONS.get(code, ""),
            "error": error,
            "duplicate": False,
        }
