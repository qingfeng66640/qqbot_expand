"""补齐插件装配生命周期及 EventBus 边界的可达失败路径。"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.kernel.concurrency import TaskManager
from src.kernel.event import EventDecision

from ..config import QQBotExpandConfig
from ..handlers import group_join_request_event_handler as join_handler_module
from ..handlers import interaction_event_handler as interaction_handler_module
from ..handlers.group_join_request_event_handler import QQBotGroupJoinRequestEventHandler
from ..handlers.interaction_event_handler import QQBotInteractionEventHandler
from ..plugin import QQBotExpandPlugin
from ..services import ALL_SERVICES
from ..src.join_requests import JoinRequestRuntime
from ..tools import (
    ALL_GROUP_ADMIN_TOOLS,
    ALL_GROUP_INFO_TOOLS,
    ALL_GROUP_MEMBERS_TOOLS,
    ALL_MENU_PANEL_TOOLS,
    ALL_TOOLS,
    ALL_UTILITY_TOOLS,
)
from .conftest import make_plugin
from .test_interaction import make_params


@pytest.fixture
def task_manager() -> Iterator[TaskManager]:
    """为每个事件测试提供独立 TaskManager，不使用全局任务注册表。"""
    manager = TaskManager()
    try:
        yield manager
    finally:
        manager.shutdown_process_pool(wait=True, cancel_futures=True)


@pytest.mark.parametrize("tools_enabled", [True, False])
def test_all_opt_in_components_obey_master_switch(tools_enabled: bool) -> None:
    """所有可选 Tool 显式开启后注册，总开关关闭则仍只保留服务和事件。"""
    config = QQBotExpandConfig()
    features = config.features
    features.enable_tools = tools_enabled
    features.enable_group_info_tools = True
    features.enable_utility_tools = True
    features.enable_group_admin_tools = True
    features.enable_group_members_tools = True
    features.enable_menu_panel_tools = True
    features.enable_menu_panel_service = True
    features.group_admin_allowed_group_openids = ["", " group-1 "]
    features.group_members_allowed_group_openids = [" ", "group-1"]
    features.menu_panel_allowed_operator_openids = [" ", "operator-1"]
    actual = QQBotExpandPlugin(config).get_components()
    expected = set(ALL_SERVICES) | {
        QQBotInteractionEventHandler, QQBotGroupJoinRequestEventHandler
    }
    if tools_enabled:
        expected.update(
            ALL_TOOLS + ALL_GROUP_INFO_TOOLS + ALL_UTILITY_TOOLS
            + ALL_GROUP_ADMIN_TOOLS + ALL_GROUP_MEMBERS_TOOLS + ALL_MENU_PANEL_TOOLS
        )
    assert set(actual) == expected
    assert len(actual) == len(expected)


@pytest.mark.parametrize(
    ("flag", "whitelist", "components"),
    [
        ("enable_group_admin_tools", "group_admin_allowed_group_openids", ALL_GROUP_ADMIN_TOOLS),
        ("enable_group_members_tools", "group_members_allowed_group_openids", ALL_GROUP_MEMBERS_TOOLS),
        ("enable_menu_panel_tools", "menu_panel_allowed_operator_openids", ALL_MENU_PANEL_TOOLS),
    ],
)
def test_opt_in_components_reject_blank_whitelists(
    flag: str, whitelist: str, components: list[type]
) -> None:
    """仅打开开关而未配置有效白名单不能暴露高权限工具。"""
    config = QQBotExpandConfig()
    config.features.enable_menu_panel_service = True
    setattr(config.features, flag, True)
    setattr(config.features, whitelist, ["", "  "])
    assert set(components).isdisjoint(QQBotExpandPlugin(config).get_components())


def test_retry_configuration_rejects_negative_attempts() -> None:
    """合法配置不允许负重试次数，因此不伪造空 range 来覆盖 HTTP 循环出口。"""
    http_section = type(QQBotExpandConfig().http)
    with pytest.raises(ValidationError, match="retry_max_attempts"):
        http_section(retry_max_attempts=-1)


@pytest.mark.parametrize("material_stays_enabled", [True, False])
async def test_repeated_load_closes_old_material_without_intermediate_unload(
    material_stays_enabled: bool,
) -> None:
    """连续调用加载钩子实际走旧素材关闭分支，禁用后还必须清空旧引用。"""
    config = QQBotExpandConfig()
    config.http.http2 = False
    config.features.enable_material_service = True
    plugin = QQBotExpandPlugin(config)
    clients = []
    try:
        await plugin.on_plugin_loaded()
        original = plugin.material_upload
        original_chunked = plugin.chunked_client
        assert original is not None
        assert not original.is_closed
        assert original_chunked is not None and not original_chunked.is_closed
        clients.append(plugin.http_client)
        config.features.enable_material_service = material_stays_enabled
        await plugin.on_plugin_loaded()
        clients.append(plugin.http_client)
        assert original.is_closed
        assert original._client.is_closed
        assert original_chunked.is_closed
        assert plugin.chunked_client is not None and plugin.chunked_client is not original_chunked
        assert not plugin.chunked_client.is_closed
        if material_stays_enabled:
            assert plugin.material_upload is not None
            assert plugin.material_upload is not original
            assert not plugin.material_upload.is_closed
        else:
            assert plugin.material_upload is None
    finally:
        await plugin.on_plugin_unloaded()
        # 当前加载钩子会替换共享 HTTP 客户端；单独释放测试保留的旧实例。
        for client in clients:
            if client is not None:
                await client.aclose()


async def test_load_survives_schedule_failure_with_interaction_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """可选运行时调度失败不阻断 HTTP 初始化，关闭互动时不打印启用提示。"""
    from .. import plugin as plugin_module

    config = QQBotExpandConfig()
    config.http.http2 = False
    config.interaction.enabled = False
    config.features.enable_tools = False
    plugin = QQBotExpandPlugin(config)
    schedule = AsyncMock(side_effect=RuntimeError("scheduler stopped"))
    monkeypatch.setattr(plugin.managed_panel_runtime, "schedule", schedule)
    info, error = Mock(), Mock()
    monkeypatch.setattr(plugin_module.logger, "info", info)
    monkeypatch.setattr(plugin_module.logger, "error", error)
    try:
        await plugin.on_plugin_loaded()
        assert plugin.http_client is not None
        assert not plugin.http_client.is_closed
        schedule.assert_awaited_once_with()
        error.assert_called_once_with("调度托管面板对账失败: scheduler stopped")
        assert not any("互动回调已启用" in call.args[0] for call in info.call_args_list)
        assert any("0 个 Tool" in call.args[0] for call in info.call_args_list)
    finally:
        await plugin.on_plugin_unloaded()


async def test_unload_continues_after_dependent_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """面板与入群运行时关闭异常不能阻断其他运行时及 HTTP 客户端释放。"""
    from .. import plugin as plugin_module

    config = QQBotExpandConfig()
    config.http.http2 = False
    plugin = QQBotExpandPlugin(config)
    await plugin.on_plugin_loaded()
    client = plugin.http_client
    assert client is not None
    chunked_client = plugin.chunked_client
    assert chunked_client is not None
    chunked_close = AsyncMock(side_effect=RuntimeError("synthetic-private-close-detail"))
    managed_close = AsyncMock(side_effect=RuntimeError("managed close failed"))
    join_close = AsyncMock(side_effect=RuntimeError("join close failed"))
    warning = Mock()
    monkeypatch.setattr(chunked_client, "aclose", chunked_close)
    monkeypatch.setattr(plugin.managed_panel_runtime, "close", managed_close)
    monkeypatch.setattr(plugin.join_request_runtime, "close", join_close)
    monkeypatch.setattr(plugin_module.logger, "warning", warning)
    try:
        await plugin.on_plugin_unloaded()
        managed_close.assert_awaited_once_with()
        join_close.assert_awaited_once_with()
        chunked_close.assert_awaited_once_with()
        assert plugin.chunked_client is None and plugin.http_client is None
        assert client.is_closed
        assert await plugin.interaction_runtime.claim_processing("after-unload") is False
        assert [call.args[0] for call in warning.call_args_list] == [
            "关闭分片上传资源失败",
            "关闭托管面板运行时失败: managed close failed",
            "关闭入群申请运行时失败: join close failed",
        ]
    finally:
        await type(chunked_client).aclose(chunked_client)
        await client.aclose()


@pytest.mark.parametrize(
    ("event_name", "request_id"),
    [
        ("foreign.event", "request-1"),
        ("qqbot_adapter.group_join_request", None),
        ("qqbot_adapter.group_join_request", 123),
        ("qqbot_adapter.group_join_request", "  "),
    ],
)
async def test_join_handler_ignores_wrong_source_and_invalid_ids(
    monkeypatch: pytest.MonkeyPatch, event_name: str, request_id: object
) -> None:
    """无效事件在访问运行时或调度任务前直接放行，保留原参数对象。"""
    manager_lookup = Mock(side_effect=AssertionError("must not schedule"))
    monkeypatch.setattr(join_handler_module, "get_task_manager", manager_lookup)
    params = {"join_request_id": request_id}
    decision, returned = await QQBotGroupJoinRequestEventHandler(None).execute(event_name, params)
    assert decision is EventDecision.PASS
    assert returned is params
    manager_lookup.assert_not_called()


async def test_join_handler_closes_coroutine_when_scheduler_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务创建失败时关闭尚未启动的协程，避免未等待协程及任务泄漏。"""
    plugin = make_plugin()
    plugin.join_request_runtime = JoinRequestRuntime(plugin)
    create_task = Mock(side_effect=RuntimeError("scheduler unavailable"))
    monkeypatch.setattr(
        join_handler_module, "get_task_manager",
        lambda: SimpleNamespace(create_task=create_task),
    )
    params = {"join_request_id": "request-1", "raw_event": {"member": "member-1"}}
    try:
        decision, returned = await QQBotGroupJoinRequestEventHandler(plugin).execute(
            "qqbot_adapter.group_join_request", params
        )
        assert decision is EventDecision.PASS
        assert returned is params
        create_task.assert_called_once()
        assert create_task.call_args.args[0].cr_frame is None
    finally:
        await plugin.join_request_runtime.close()


async def test_join_handler_dispatches_snapshot_once_with_real_task_manager(
    monkeypatch: pytest.MonkeyPatch, task_manager: TaskManager
) -> None:
    """真实 TaskManager 执行回调，重复事件不重放且输入原对象不被回调改写。"""
    plugin = make_plugin()
    runtime = plugin.join_request_runtime = JoinRequestRuntime(plugin)
    received: list[dict[str, Any]] = []
    completed = asyncio.Event()

    def callback(params: dict[str, Any]) -> None:
        """记录 worker 的独立快照，模拟业务回调修改私有副本。"""
        received.append(params)
        params["raw_event"]["member"] = "callback-change"
        completed.set()

    await runtime.register("coverage", callback)
    monkeypatch.setattr(join_handler_module, "get_task_manager", lambda: task_manager)
    handler = QQBotGroupJoinRequestEventHandler(plugin)
    params = {"join_request_id": "request-1", "raw_event": {"member": "member-1"}}
    try:
        first, returned = await handler.execute("qqbot_adapter.group_join_request", params)
        second, _ = await handler.execute("qqbot_adapter.group_join_request", params)
        async with asyncio.timeout(2):
            await completed.wait()
        assert first is EventDecision.SUCCESS
        assert second is EventDecision.PASS
        assert returned is params
        assert len(received) == 1
        assert params["raw_event"] == {"member": "member-1"}
    finally:
        await runtime.close()


async def test_interaction_handler_without_plugin_passes_event() -> None:
    """互动 handler 在插件依赖缺失时封闭异常，不击穿 EventBus。"""
    params = make_params()
    decision, returned = await QQBotInteractionEventHandler(None).execute(
        "qqbot_adapter.interaction_create", params
    )
    assert decision is EventDecision.PASS
    assert returned is params


async def test_interaction_snapshot_failure_releases_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事件携带不可复制的外部扩展对象时撤销 claim，修正后可重新认领。"""
    plugin = make_plugin()
    # 原生锁不可 deepcopy，模拟 EventBus 外部插件放入的非法扩展对象。
    params = make_params(raw_event={"extension": threading.Lock()})
    manager_lookup = Mock(side_effect=AssertionError("must not schedule"))
    monkeypatch.setattr(interaction_handler_module, "get_task_manager", manager_lookup)
    try:
        decision, returned = await QQBotInteractionEventHandler(plugin).execute(
            "qqbot_adapter.interaction_create", params
        )
        assert decision is EventDecision.PASS
        assert returned is params
        manager_lookup.assert_not_called()
        assert await plugin.interaction_runtime.claim_processing("i1") is True
    finally:
        await plugin.interaction_runtime.close()
