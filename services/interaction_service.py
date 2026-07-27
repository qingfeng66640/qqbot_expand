"""QQ 互动回调应答 Service。

按钮被点击后，QQ 会下发 ``INTERACTION_CREATE`` 事件（需 ``intents`` 含
``1<<26``）并等待机器人应答 ``PUT /interactions/{interaction_id}``，客户端据此
显示"操作成功 / 操作频繁 / 没有权限"等提示；不应答则按钮会一直转圈直到超时。

按官方文档：只有 ``type=11``（消息按钮）与 ``type=12``（单聊快捷菜单）需要应答；
同一个 ``interaction_id`` **只能应答一次**，且过期后不可再答；接口限频 50 QPS。

**已知限制（务必阅读 README 的同名章节）**：

1. ``qqbot_adapter`` 收到 ``INTERACTION_CREATE`` 后会自行应答 ``{"code": 0}``
   并 ``return None``，事件不进核心，本插件收不到回调 payload；
2. 因"只能应答一次"，本 Service 的自定义 code 会与适配器的自动应答**竞争**，
   晚到的一方会失败；
3. 该接口沙箱环境不可用，本 Service 强制走正式域名。

综上，本 Service 适用于"调用方通过其他途径拿到 interaction_id"的场景，
不能作为完整的互动回调链路使用。
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
    service_description = "应答 QQ 按钮互动回调（PUT /interactions/{id}），可自定义提示码"
    version = "0.2.0"

    async def ack(self, interaction_id: str, code: int = 0) -> dict[str, Any]:
        """应答一次互动回调。

        Args:
            interaction_id: 互动事件 id，取自 ``INTERACTION_CREATE`` 事件的 ``id`` 字段。
            code: 应答码，决定客户端弹出的提示文案。
                0 操作成功 / 1 操作失败 / 2 操作频繁 / 3 重复操作 /
                4 没有权限 / 5 仅管理员操作。

        Returns:
            ``{"success": bool, "code": int, "description": str, "error": str | None}``。
        """
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            return self._failure("interaction_id 不能为空", code)
        if not isinstance(code, int) or isinstance(code, bool):
            return self._failure("code 必须是整数", code)
        if code not in INTERACTION_CODES:
            return self._failure(
                f"code 只能是 {sorted(INTERACTION_CODES)} 之一，收到 {code}", code
            )

        path = PATH_INTERACTION_ACK.format(interaction_id=interaction_id.strip())
        # 互动应答接口沙箱不支持，强制正式域名
        result = await api_request(
            self.plugin, "PUT", path, {"code": code}, force_production=True
        )
        if not result["success"]:
            return self._failure(result["error"], code)
        return {
            "success": True,
            "code": code,
            "description": INTERACTION_CODE_DESCRIPTIONS[code],
            "error": None,
        }

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
        }
