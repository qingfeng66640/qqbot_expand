"""QQ Bot 扩展能力插件入口。

本插件依附于 ``qqbot_adapter`` 运行，自身不建立 WebSocket 连接、不管理 token，
只做两件事：

1. 把 QQ 开放平台的消息结构体（keyboard / ark / embed / markdown 模板 /
   message_reference）封装成 Service 与 Tool，补齐适配器未覆盖的消息类型。
2. 提供一个统一的 QQ 开放 API 调用出口，覆盖 ``SendHandler.post_api()``
   无法处理的 GET / PUT / DELETE 请求。

生命周期职责：``BaseService`` 每次 ``get_service()`` 都会新建实例，
因此长生命周期资源（httpx 客户端）必须挂在插件实例上，由本文件的
``on_plugin_loaded`` / ``on_plugin_unloaded`` 负责创建与释放。
"""
from __future__ import annotations

import httpx

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseConfig, BasePlugin, register_plugin

from .config import QQBotExpandConfig
from .handlers.group_join_request_event_handler import QQBotGroupJoinRequestEventHandler
from .handlers.interaction_event_handler import QQBotInteractionEventHandler
from .services import ALL_SERVICES
from .src.interaction import InteractionRuntime
from .src.join_requests import JoinRequestRuntime
from .src.sent_messages import SentMessageRegistry
from .services.chunked_media_service import QQBotChunkedMediaService
from .services.group_admin_service import QQBotGroupAdminService
from .services.group_info_service import QQBotGroupInfoService
from .services.interaction_service import QQBotInteractionService
from .services.menu_panel_service import QQBotMenuPanelService
from .services.message_service import QQBotMessageService
from .services.raw_service import QQBotRawService
from .services.utility_service import QQBotUtilityService
from .tools import ALL_TOOLS
from .tools.group_admin import QQReviewGroupJoinRequestTool, QQSetGroupMemberMuteTool
from .tools.menu_panel import (
    QQCreatePanelTool,
    QQDeletePanelTool,
    QQGetMenuPanelTool,
    QQListPanelsTool,
    QQUpdateMenuTool,
    QQUpdatePanelTargetsTool,
    QQUpdatePanelTool,
)
from .tools.group_info import QQGetCurrentGroupBotStateTool, QQGetCurrentGroupInfoTool
from .tools.send_ark import QQSendArkTool
from .tools.send_keyboard import QQSendKeyboardTool
from .tools.send_reply import QQSendReplyTool
from .tools.utility import QQGenerateShareLinkTool, QQRecallCurrentMessageTool

logger = get_logger("qqbot_expand")

__all__ = ["QQBotExpandPlugin"]


@register_plugin
class QQBotExpandPlugin(BasePlugin):
    """QQ Bot 扩展能力插件。

    Attributes:
        http_client: 全插件共享的 httpx 异步客户端，供各 Service 发起
            非 POST 请求；未加载完成时为 None。
    """

    plugin_name = "qqbot_expand"
    plugin_description = "为 qqbot_adapter 补齐按钮/ark/embed/模板 Markdown 等消息类型，并提供统一的 QQ 开放 API 调用通道"
    plugin_version = "0.4.0"
    configs = [QQBotExpandConfig]

    def __init__(self, config: BaseConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 框架注入的插件配置实例。
        """
        super().__init__(config)
        self.http_client: httpx.AsyncClient | None = None
        self.interaction_runtime = InteractionRuntime(self)
        self.join_request_runtime = JoinRequestRuntime(self)
        self.sent_messages = SentMessageRegistry()

    def get_components(self) -> list[type]:
        """返回插件注册的全部组件。

        ``features.enable_tools`` 关闭时不注册 Tool，只保留 Service，
        避免把 QQ 专属能力暴露给 LLM。

        Returns:
            组件类列表。
        """
        # 注：此处刻意逐个字面量列出并使用无注解赋值 + append，
        # 以便 mpdt 的 ComponentValidator 能静态解析出组件清单。
        components = [
            QQBotMessageService,
            QQBotChunkedMediaService,
            QQBotGroupAdminService,
            QQBotGroupInfoService,
            QQBotInteractionService,
            QQBotMenuPanelService,
            QQBotRawService,
            QQBotUtilityService,
            QQBotInteractionEventHandler,
            QQBotGroupJoinRequestEventHandler,
        ]
        if self._tools_enabled():
            components.append(QQSendKeyboardTool)
            components.append(QQSendArkTool)
            components.append(QQSendReplyTool)
        if self._group_info_tools_enabled():
            components.append(QQGetCurrentGroupInfoTool)
            components.append(QQGetCurrentGroupBotStateTool)
        if self._utility_tools_enabled():
            components.append(QQRecallCurrentMessageTool)
            components.append(QQGenerateShareLinkTool)
        if self._group_admin_tools_enabled():
            components.append(QQReviewGroupJoinRequestTool)
            components.append(QQSetGroupMemberMuteTool)
        if self._menu_panel_tools_enabled():
            components.append(QQGetMenuPanelTool)
            components.append(QQListPanelsTool)
            components.append(QQUpdateMenuTool)
            components.append(QQCreatePanelTool)
            components.append(QQUpdatePanelTool)
            components.append(QQDeletePanelTool)
            components.append(QQUpdatePanelTargetsTool)
        return components

    async def on_plugin_loaded(self) -> None:
        """插件加载时重置互动运行时并创建共享 httpx 客户端。"""
        await self.interaction_runtime.reset()
        await self.join_request_runtime.close()
        http_cfg = getattr(self.config, "http", None)
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                float(getattr(http_cfg, "request_timeout", 30.0)),
                connect=float(getattr(http_cfg, "connect_timeout", 10.0)),
            ),
            limits=httpx.Limits(
                max_keepalive_connections=int(
                    getattr(http_cfg, "max_keepalive_connections", 20)
                ),
                max_connections=int(getattr(http_cfg, "max_connections", 50)),
                keepalive_expiry=float(getattr(http_cfg, "keepalive_expiry", 30.0)),
            ),
            http2=self._http2_enabled(http_cfg),
            trust_env=False,
        )
        if self._interaction_enabled():
            logger.info(
                "qqbot_expand 互动回调已启用；请确保 qqbot_adapter 使用 intents=100663296"
            )
        tool_count = len(ALL_TOOLS) if self._tools_enabled() else 0
        logger.info(
            f"qqbot_expand 插件已加载: {tool_count} 个 Tool, {len(ALL_SERVICES)} 个 Service"
        )

    @staticmethod
    def _http2_enabled(http_cfg: object) -> bool:
        """决定是否启用 HTTP/2。

        httpx 的 ``http2=True`` 依赖可选包 ``h2``，缺失时会在构造客户端阶段直接
        抛 ImportError 导致插件加载失败。这里主动降级到 HTTP/1.1，
        保证插件在未装 ``httpx[http2]`` 的环境下依然可用。

        Args:
            http_cfg: HTTP 配置段，可能为 None。

        Returns:
            最终是否启用 HTTP/2。
        """
        if not bool(getattr(http_cfg, "http2", True)):
            return False
        try:
            import h2  # noqa: F401
        except ImportError:
            logger.warning("未安装 h2，HTTP/2 已自动降级为 HTTP/1.1")
            return False
        return True

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时先清理互动任务，再关闭共享 httpx 客户端。"""
        try:
            await self.interaction_runtime.close()
        except Exception as exc:  # noqa: BLE001 - 卸载阶段不应抛出
            logger.warning(f"关闭互动运行时失败: {exc}")
        try:
            await self.join_request_runtime.close()
        except Exception as exc:  # noqa: BLE001 - 卸载阶段不应抛出
            logger.warning(f"关闭入群申请运行时失败: {exc}")
        if self.http_client is not None:
            try:
                await self.http_client.aclose()
            except Exception as exc:  # noqa: BLE001 - 卸载阶段不应抛出
                logger.warning(f"关闭 httpx 客户端失败: {exc}")
            finally:
                self.http_client = None
        logger.info("qqbot_expand 插件已卸载")

    def _interaction_enabled(self) -> bool:
        """读取互动回调开关，配置缺失时默认启用。"""
        interaction = getattr(self.config, "interaction", None)
        return bool(getattr(interaction, "enabled", True))

    def _tools_enabled(self) -> bool:
        """读取 ``features.enable_tools`` 开关。

        Returns:
            是否注册精选 Tool；配置缺失时默认为 True。
        """
        features = getattr(self.config, "features", None)
        return bool(getattr(features, "enable_tools", True))

    def _utility_tools_enabled(self) -> bool:
        """仅在显式启用时注册撤回与分享链接 Tool。"""
        features = getattr(self.config, "features", None)
        return self._tools_enabled() and bool(
            getattr(features, "enable_utility_tools", False)
        )

    def _group_info_tools_enabled(self) -> bool:
        """仅在显式启用时注册当前群信息 Tool。"""
        features = getattr(self.config, "features", None)
        return self._tools_enabled() and bool(
            getattr(features, "enable_group_info_tools", False)
        )

    def _menu_panel_tools_enabled(self) -> bool:
        """仅在显式启用菜单面板 Tool 且存在操作者白名单时注册。"""
        features = getattr(self.config, "features", None)
        operators = getattr(features, "menu_panel_allowed_operator_openids", []) or []
        return (
            self._tools_enabled()
            and bool(getattr(features, "enable_menu_panel_tools", False))
            and bool(getattr(features, "enable_menu_panel_service", False))
            and any(isinstance(operator, str) and operator.strip() for operator in operators)
        )

    def _group_admin_tools_enabled(self) -> bool:
        """仅在显式启用且配置目标群白名单时注册群管理 Tool。"""
        features = getattr(self.config, "features", None)
        allowed_groups = getattr(features, "group_admin_allowed_group_openids", []) or []
        return (
            self._tools_enabled()
            and bool(getattr(features, "enable_group_admin_tools", False))
            and any(isinstance(group, str) and group.strip() for group in allowed_groups)
        )
