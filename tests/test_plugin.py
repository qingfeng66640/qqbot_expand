"""插件装配层测试：生命周期、配置、manifest 一致性、适配器解析。"""
from __future__ import annotations

from types import SimpleNamespace

from unittest.mock import AsyncMock

import pytest

from ..config import QQBotExpandConfig
from ..handlers import QQBotGroupJoinRequestEventHandler, QQBotInteractionEventHandler
from ..plugin import QQBotExpandPlugin
from ..services import ALL_SERVICES
from ..src.bridge import ADAPTER_SIGNATURE, resolve_send_handler
from ..tools import (
    ALL_GROUP_ADMIN_TOOLS,
    ALL_GROUP_INFO_TOOLS,
    ALL_MENU_PANEL_TOOLS,
    ALL_TOOLS,
    ALL_UTILITY_TOOLS,
)

PLUGIN_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def load_manifest() -> dict:
    """读取 manifest.json。

    Returns:
        解析后的 manifest 字典。
    """
    import json

    return json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))


class TestManifestConsistency:
    """manifest 与代码必须一一对应，否则组件注册会静默失配。"""

    def test_components_match_code(self) -> None:
        """manifest 声明的组件与 ALL_SERVICES / ALL_TOOLS 完全一致。"""
        declared = {
            (item["component_type"], item["component_name"])
            for item in load_manifest()["include"]
        }
        actual = (
            {("service", svc.service_name) for svc in ALL_SERVICES}
            | {("tool", tool.tool_name) for tool in ALL_TOOLS}
            | {("tool", tool.tool_name) for tool in ALL_GROUP_INFO_TOOLS}
            | {("tool", tool.tool_name) for tool in ALL_UTILITY_TOOLS}
            | {("tool", tool.tool_name) for tool in ALL_GROUP_ADMIN_TOOLS}
            | {("tool", tool.tool_name) for tool in ALL_MENU_PANEL_TOOLS}
            | {("event_handler", QQBotInteractionEventHandler.name)}
            | {("event_handler", QQBotGroupJoinRequestEventHandler.name)}
        )
        assert declared == actual

    def test_depends_on_adapter(self) -> None:
        """本插件是 qqbot_adapter 的扩展，依赖必须声明。"""
        assert "qqbot_adapter" in load_manifest()["dependencies"]["plugins"]

    def test_version_matches_plugin_class(self) -> None:
        """manifest 版本号与插件类保持同步。"""
        assert load_manifest()["version"] == QQBotExpandPlugin.plugin_version

    def test_declares_httpx_dependency(self) -> None:
        """非 POST 请求依赖自持的 httpx 客户端。"""
        deps = " ".join(load_manifest()["python_dependencies"])
        assert "httpx" in deps


class TestConfig:
    """配置默认值。"""

    def test_defaults(self) -> None:
        """默认开箱可用，raw 通道默认开启但方法受限。"""
        config = QQBotExpandConfig()
        assert config.plugin.enabled is True
        assert config.features.enable_tools is True
        assert config.features.allow_raw_request is True
        assert config.features.debug_log_payload is False
        assert config.interaction.enabled is True
        assert config.interaction.callback_timeout > 0
        assert config.interaction.button_data_max_length >= 3
        assert config.interaction.dedup_ttl > 0
        assert config.interaction.dedup_capacity > 0
        assert config.features.enable_group_admin_tools is False
        assert config.features.group_admin_allowed_group_openids == []
        assert config.features.enable_menu_panel_service is False
        assert config.features.enable_menu_panel_tools is False
        assert config.features.allow_global_menu_write is False
        assert config.features.allow_panel_create is False
        assert config.features.allow_panel_delete is False
        assert config.features.menu_panel_allowed_operator_openids == []
        assert config.features.menu_panel_allowed_group_openids == []
        assert config.features.menu_panel_allowed_panel_ids == []
        assert config.features.menu_panel_profiles == []
        assert set(config.features.raw_allowed_methods) == {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }

    def test_http_defaults(self) -> None:
        """HTTP 参数需有合理默认，避免未配置时不可用。"""
        http = QQBotExpandConfig().http
        assert http.request_timeout > 0
        assert http.connect_timeout > 0
        assert http.max_connections >= http.max_keepalive_connections
        assert http.retry_max_attempts >= 0


class TestPluginLifecycle:
    """插件加载与卸载。"""

    def test_components_include_services_and_tools(self) -> None:
        """默认注册全部 Service 与 Tool。"""
        plugin = QQBotExpandPlugin(QQBotExpandConfig())
        components = plugin.get_components()
        assert set(components) == set(ALL_SERVICES) | set(ALL_TOOLS) | {
            QQBotInteractionEventHandler,
            QQBotGroupJoinRequestEventHandler,
        }

    def test_tools_can_be_disabled(self) -> None:
        """关闭 enable_tools 后只注册 Service。"""
        config = QQBotExpandConfig()
        config.features.enable_tools = False
        plugin = QQBotExpandPlugin(config)
        assert set(plugin.get_components()) == set(ALL_SERVICES) | {
            QQBotInteractionEventHandler,
            QQBotGroupJoinRequestEventHandler,
        }

    def test_menu_panel_tools_require_explicit_authorization(self) -> None:
        """菜单面板 Tool 仅在 Service、Tool 和操作者白名单均开启时注册。"""
        config = QQBotExpandConfig()
        config.features.enable_menu_panel_service = True
        config.features.enable_menu_panel_tools = True
        plugin = QQBotExpandPlugin(config)
        assert set(ALL_MENU_PANEL_TOOLS).isdisjoint(plugin.get_components())

        config.features.menu_panel_allowed_operator_openids = ["operator"]
        assert set(ALL_MENU_PANEL_TOOLS).issubset(plugin.get_components())

    def test_http_client_absent_before_load(self) -> None:
        """加载前不应持有客户端。"""
        assert QQBotExpandPlugin(QQBotExpandConfig()).http_client is None

    async def test_load_creates_client_then_unload_closes_it(self) -> None:
        """客户端由插件持有，卸载时必须关闭并置空。"""
        plugin = QQBotExpandPlugin(QQBotExpandConfig())

        await plugin.on_plugin_loaded()
        client = plugin.http_client
        assert client is not None
        assert client.is_closed is False
        # 不信任环境代理，避免请求被劫持
        assert client.trust_env is False

        await plugin.on_plugin_unloaded()
        assert plugin.http_client is None
        assert client.is_closed is True

    async def test_unload_is_idempotent(self) -> None:
        """未加载或重复卸载都不应抛异常。"""
        plugin = QQBotExpandPlugin(QQBotExpandConfig())
        await plugin.on_plugin_unloaded()
        await plugin.on_plugin_unloaded()
        assert plugin.http_client is None

    async def test_unload_survives_close_failure(self) -> None:
        """关闭失败也不能让卸载流程崩溃。"""
        plugin = QQBotExpandPlugin(QQBotExpandConfig())

        class BrokenClient:
            async def aclose(self) -> None:
                raise RuntimeError("boom")

        plugin.http_client = BrokenClient()  # type: ignore[assignment]
        await plugin.on_plugin_unloaded()
        assert plugin.http_client is None

    async def test_unload_survives_runtime_close_failure(self) -> None:
        """互动运行时关闭失败也不得阻断 HTTP 客户端清理。"""
        plugin = QQBotExpandPlugin(QQBotExpandConfig())
        plugin.interaction_runtime.close = AsyncMock(side_effect=RuntimeError("boom"))
        await plugin.on_plugin_unloaded()
        plugin.interaction_runtime.close.assert_awaited_once()

    async def test_runtime_can_reload_after_unload(self) -> None:
        """同一插件实例重新加载后应恢复 callback 注册能力。"""
        plugin = QQBotExpandPlugin(QQBotExpandConfig())
        await plugin.on_plugin_unloaded()
        await plugin.on_plugin_loaded()
        try:
            assert plugin.interaction_runtime.register(
                "demo", "run", lambda _ctx, _payload: 0
            ) is True
        finally:
            await plugin.on_plugin_unloaded()

    async def test_http2_can_be_disabled_by_config(self) -> None:
        """配置关闭 HTTP/2 时不应尝试导入 h2。"""
        config = QQBotExpandConfig()
        config.http.http2 = False
        plugin = QQBotExpandPlugin(config)

        await plugin.on_plugin_loaded()
        try:
            assert plugin.http_client is not None
        finally:
            await plugin.on_plugin_unloaded()

    def test_http2_degrades_without_h2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未安装 h2 时自动降级，而不是让插件加载失败。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "h2":
                raise ImportError("no h2")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert QQBotExpandPlugin._http2_enabled(SimpleNamespace(http2=True)) is False

    def test_http2_enabled_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """h2 可用且配置开启时启用 HTTP/2。"""
        import builtins
        import sys

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "h2":
                return SimpleNamespace()
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.delitem(sys.modules, "h2", raising=False)
        assert QQBotExpandPlugin._http2_enabled(SimpleNamespace(http2=True)) is True


class TestResolveSendHandler:
    """适配器解析。"""

    def test_returns_send_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常情况下取出适配器的 send_handler 公共属性。"""
        from src.app.plugin_system.api import adapter_api

        handler = object()
        captured: list[str] = []

        def fake_get_adapter(signature: str) -> object:
            captured.append(signature)
            return SimpleNamespace(send_handler=handler)

        monkeypatch.setattr(adapter_api, "get_adapter", fake_get_adapter)

        assert resolve_send_handler() is handler
        assert captured == [ADAPTER_SIGNATURE]

    def test_returns_none_when_adapter_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """适配器未启动时返回 None 而非抛异常。"""
        from src.app.plugin_system.api import adapter_api

        monkeypatch.setattr(adapter_api, "get_adapter", lambda _sig: None)
        assert resolve_send_handler() is None

    def test_returns_none_when_lookup_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """查询本身失败时同样降级为 None。"""
        from src.app.plugin_system.api import adapter_api

        def boom(_sig: str) -> object:
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr(adapter_api, "get_adapter", boom)
        assert resolve_send_handler() is None

    def test_returns_none_when_handler_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """适配器已加载但尚未初始化 send_handler。"""
        from src.app.plugin_system.api import adapter_api

        monkeypatch.setattr(
            adapter_api, "get_adapter", lambda _sig: SimpleNamespace()
        )
        assert resolve_send_handler() is None

    def test_does_not_touch_private_members(self) -> None:
        """约定只读公共属性，不得访问适配器私有成员。"""
        import ast

        source = (PLUGIN_ROOT / "src" / "bridge.py").read_text(encoding="utf-8")
        accessed = {
            node.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
        }
        assert "_send_handler" not in accessed
        assert "_token_mgr" not in accessed
