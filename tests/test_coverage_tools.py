"""补齐 QQ Tool 的运行时授权、当前目标推导与 Service 返回契约。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, TypeVar
from unittest.mock import AsyncMock

import pytest

from src.app.plugin_system.base import BaseTool

from ..services.group_admin_service import QQBotGroupAdminService
from ..services.group_info_service import QQBotGroupInfoService
from ..services.group_members_service import QQBotGroupMembersService
from ..services.menu_panel_service import QQBotMenuPanelService
from ..services.utility_service import QQBotUtilityService
from ..tools.group_admin import QQReviewGroupJoinRequestTool, QQSetGroupMemberMuteTool
from ..tools.group_info import QQGetCurrentGroupBotStateTool, QQGetCurrentGroupInfoTool
from ..tools.group_members import (
    QQBatchRemoveGroupMembersTool,
    QQManageGroupBlacklistTool,
    QQQueryGroupMembersTool,
)
from ..tools.menu_panel import (
    QQCreatePanelTool,
    QQDeletePanelTool,
    QQGetMenuPanelTool,
    QQListPanelsTool,
    QQUpdateMenuTool,
    QQUpdatePanelTargetsTool,
    QQUpdatePanelTool,
)
from ..tools.send_keyboard import QQSendKeyboardTool
from ..tools.utility import QQGenerateShareLinkTool, QQRecallCurrentMessageTool
from .conftest import make_plugin

ToolT = TypeVar("ToolT", bound=BaseTool)


def _message(chat_type: str = "group") -> SimpleNamespace:
    """提供当前操作者和会话，目标只从消息而不是 Tool 参数取得。"""
    return SimpleNamespace(
        message_id="trigger-1",
        chat_type=chat_type,
        sender_id="operator-1",
        extra={"group_id": "group-1"},
    )


def _bind(
    tool_type: type[ToolT], plugin: Any, message: SimpleNamespace | None
) -> ToolT:
    """沿用框架运行时上下文绑定入口。"""
    tool = tool_type(plugin)
    tool._bind_runtime_context(stream_id="coverage-stream", message=message)
    return tool


@pytest.fixture
def tool_plugin() -> SimpleNamespace:
    """显式授权当前群及操作者，不加载任何磁盘配置。"""
    return make_plugin(
        enable_group_info_tools=True,
        enable_group_members_tools=True,
        enable_menu_panel_service=True,
        enable_menu_panel_tools=True,
        allow_global_menu_write=True,
        allow_panel_create=True,
        allow_panel_delete=True,
        group_admin_allowed_group_openids=["group-1"],
        group_members_allowed_group_openids=["group-1"],
        menu_panel_allowed_operator_openids=[" operator-1 ", ""],
        menu_panel_allowed_group_openids=[" group-1 "],
        menu_panel_allowed_panel_ids=["panel-1"],
    )


def _service_result(success: bool) -> dict[str, Any]:
    """构造边界服务的稳定结果，不替换 Tool 本体。"""
    return {"success": success, "data": {"id": "result-1"}, "error": "service denied"}


@pytest.mark.parametrize(
    ("tool_type", "arguments", "feature", "expected"),
    [
        (QQReviewGroupJoinRequestTool, {"member_openid": "member", "op": "approve"},
         "enable_tools", "QQ LLM"),
        (QQReviewGroupJoinRequestTool, {"member_openid": "member", "op": "approve"},
         "enable_group_admin_service", "群管理 Service"),
        (QQGetCurrentGroupInfoTool, {}, "enable_tools", "QQ LLM"),
        (QQGetCurrentGroupInfoTool, {}, "enable_group_info_tools", "群信息 LLM"),
        (QQGetCurrentGroupInfoTool, {}, "enable_group_info_service", "群信息 Service"),
        (QQQueryGroupMembersTool, {"query": "members"}, "enable_tools", "QQ LLM"),
        (QQQueryGroupMembersTool, {"query": "members"},
         "enable_group_members_service", "群成员管理 Service"),
        (QQManageGroupBlacklistTool, {"op": "add", "member_openids": ["member"]},
         "enable_group_members_tools", "群成员管理 LLM"),
        (QQBatchRemoveGroupMembersTool, {"member_openids": ["member"]},
         "enable_group_members_tools", "群成员管理 LLM"),
    ],
)
async def test_group_tools_recheck_disabled_features(
    tool_plugin: SimpleNamespace,
    tool_type: type[BaseTool],
    arguments: dict[str, Any],
    feature: str,
    expected: str,
) -> None:
    """组件即使已注册，关闭开关后也不能继续执行群操作。"""
    setattr(tool_plugin.config.features, feature, False)
    ok, error = await _bind(tool_type, tool_plugin, _message()).execute(**arguments)
    assert ok is False
    assert expected in error


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize(
    ("tool_type", "service_type", "method", "arguments", "expected_args", "expected_kwargs"),
    [
        (QQGetCurrentGroupInfoTool, QQBotGroupInfoService, "get_group_info", {},
         ("group-1",), {}),
        (QQGetCurrentGroupBotStateTool, QQBotGroupInfoService, "get_bot_group_state", {},
         ("group-1",), {}),
        (QQSetGroupMemberMuteTool, QQBotGroupAdminService, "set_member_mute_states",
         {"members": [{"op": "del", "member_openid": "member"}]},
         ("group-1", [{"op": "del", "member_openid": "member"}]), {}),
        (QQQueryGroupMembersTool, QQBotGroupMembersService, "get_member",
         {"query": "member", "member_openid": "member"}, ("group-1", "member"), {}),
        (QQBatchRemoveGroupMembersTool, QQBotGroupMembersService, "batch_remove_members",
         {"member_openids": ["member"], "confirm": True},
         ("group-1", ["member"]), {}),
    ],
)
async def test_group_tools_forward_current_target_and_results(
    monkeypatch: pytest.MonkeyPatch,
    tool_plugin: SimpleNamespace,
    success: bool,
    tool_type: type[BaseTool],
    service_type: type,
    method: str,
    arguments: dict[str, Any],
    expected_args: tuple[Any, ...],
    expected_kwargs: dict[str, Any],
) -> None:
    """群目标固定为触发群，成功数据和服务失败原因均不丢失。"""
    response = _service_result(success)
    call = AsyncMock(return_value=response)
    monkeypatch.setattr(service_type, method, call)
    result = await _bind(tool_type, tool_plugin, _message()).execute(**arguments)
    assert result == (success, response["data"] if success else response["error"])
    call.assert_awaited_once_with(*expected_args, **expected_kwargs)


@pytest.mark.parametrize(
    ("overrides", "chat_type", "panel_id", "expected"),
    [
        ({"enable_tools": False}, "group", "", "QQ LLM"),
        ({"enable_menu_panel_tools": False}, "group", "", "菜单面板 LLM"),
        ({"enable_menu_panel_service": False}, "group", "", "菜单面板 Service"),
        ({"menu_panel_allowed_operator_openids": []}, "group", "", "操作者"),
        ({}, "unknown", "", "无法从当前 QQ 会话"),
        ({"menu_panel_allowed_group_openids": []}, "group", "", "当前群"),
        ({}, "group", "foreign-panel", "当前面板"),
    ],
)
async def test_menu_authorization_stops_before_service(
    monkeypatch: pytest.MonkeyPatch,
    tool_plugin: SimpleNamespace,
    overrides: dict[str, Any],
    chat_type: str,
    panel_id: str,
    expected: str,
) -> None:
    """菜单读取也必须经过开关、操作者、当前群与面板白名单。"""
    for name, value in overrides.items():
        setattr(tool_plugin.config.features, name, value)
    get_menu, get_panel = AsyncMock(), AsyncMock()
    monkeypatch.setattr(QQBotMenuPanelService, "get_menu", get_menu)
    monkeypatch.setattr(QQBotMenuPanelService, "get_panel", get_panel)
    message = _message(chat_type) if chat_type != "unknown" else _message("group")
    if chat_type == "unknown":
        message.extra = {}
    result = await _bind(QQGetMenuPanelTool, tool_plugin, message).execute(panel_id)
    assert result[0] is False
    assert expected in result[1]
    get_menu.assert_not_awaited()
    get_panel.assert_not_awaited()


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize(
    ("tool_type", "method", "arguments", "expected_args"),
    [
        (QQGetMenuPanelTool, "get_menu", {}, ()),
        (QQGetMenuPanelTool, "get_panel", {"panel_id": "panel-1"}, ("panel-1",)),
        (QQListPanelsTool, "list_panels", {"scope": "group", "cursor": "next", "limit": 7},
         ("group", "next", 7)),
        (QQUpdateMenuTool, "update_menu", {"items": [], "confirm": True}, ([],)),
        (QQUpdatePanelTool, "update_panel",
         {"panel_id": "panel-1", "panel": {"name": "panel"}, "confirm": True},
         ("panel-1", {"name": "panel"})),
        (QQDeletePanelTool, "delete_panel", {"panel_id": "panel-1", "confirm": True},
         ("panel-1",)),
    ],
)
async def test_menu_tools_call_service_and_preserve_result(
    monkeypatch: pytest.MonkeyPatch,
    tool_plugin: SimpleNamespace,
    success: bool,
    tool_type: type[BaseTool],
    method: str,
    arguments: dict[str, Any],
    expected_args: tuple[Any, ...],
) -> None:
    """只替换 Service 边界，真实执行各菜单 Tool 的授权和结果转换。"""
    response = _service_result(success)
    call = AsyncMock(return_value=response)
    monkeypatch.setattr(QQBotMenuPanelService, method, call)
    result = await _bind(tool_type, tool_plugin, _message()).execute(**arguments)
    assert result == (success, response["data"] if success else response["error"])
    call.assert_awaited_once_with(*expected_args)


@pytest.mark.parametrize(
    ("tool_type", "method", "arguments", "override", "expected"),
    [
        (QQListPanelsTool, "list_panels", {"scope": "group"},
         {"enable_tools": False}, "QQ LLM"),
        (QQUpdateMenuTool, "update_menu", {"items": [], "confirm": True},
         {"enable_tools": False}, "QQ LLM"),
        (QQUpdatePanelTool, "update_panel",
         {"panel_id": "panel-1", "panel": {}, "confirm": False}, {}, "confirm"),
        (QQDeletePanelTool, "delete_panel", {"panel_id": "panel-1", "confirm": True},
         {"allow_panel_delete": False}, "面板删除未启用"),
    ],
)
async def test_menu_mutations_reject_missing_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tool_plugin: SimpleNamespace,
    tool_type: type[BaseTool],
    method: str,
    arguments: dict[str, Any],
    override: dict[str, Any],
    expected: str,
) -> None:
    """显式确认或独立开关不能绕过通用授权门禁。"""
    for name, value in override.items():
        setattr(tool_plugin.config.features, name, value)
    call = AsyncMock()
    monkeypatch.setattr(QQBotMenuPanelService, method, call)
    result = await _bind(tool_type, tool_plugin, _message()).execute(**arguments)
    assert result[0] is False
    assert expected in result[1]
    call.assert_not_awaited()


@pytest.mark.parametrize("chat_type", ["group", "private"])
async def test_create_panel_uses_only_current_conversation(
    monkeypatch: pytest.MonkeyPatch, tool_plugin: SimpleNamespace, chat_type: str
) -> None:
    """未指定 profile 时，群和私聊目标均固定来自触发消息。"""
    create = AsyncMock(return_value=_service_result(True))
    monkeypatch.setattr(QQBotMenuPanelService, "create_panel", create)
    panel = {"name": "panel", "items": []}
    result = await _bind(QQCreatePanelTool, tool_plugin, _message(chat_type)).execute(panel, True)
    assert result[0] is True
    create.assert_awaited_once_with(
        "group" if chat_type == "group" else "c2c",
        "specific",
        panel,
        user_openids=None if chat_type == "group" else ["operator-1"],
        group_openids=["group-1"] if chat_type == "group" else None,
    )


@pytest.mark.parametrize(
    ("profile_name", "profile_changes", "expected"),
    [
        (" ", {}, "profile_name 不能为空"),
        ("missing", {}, "未找到"),
        ("trusted", {"group_openids": ["foreign-group"]}, "未授权的群目标"),
        ("trusted", {"allow_target_update": False}, "未授权修改关联对象"),
        ("trusted", {"panel_id": "foreign-panel"}, "当前面板"),
    ],
)
async def test_target_update_cannot_escape_trusted_profile(
    monkeypatch: pytest.MonkeyPatch,
    tool_plugin: SimpleNamespace,
    profile_name: str,
    profile_changes: dict[str, Any],
    expected: str,
) -> None:
    """profile 必须存在、授权群目标、允许关联修改且面板在白名单。"""
    profile = {
        "name": "trusted", "panel_id": "panel-1", "group_openids": ["group-1"],
        "allow_target_update": True,
    }
    profile.update(profile_changes)
    tool_plugin.config.features.menu_panel_profiles = [{"name": "other"}, profile]
    update = AsyncMock()
    monkeypatch.setattr(QQBotMenuPanelService, "update_panel_targets", update)
    result = await _bind(QQUpdatePanelTargetsTool, tool_plugin, _message()).execute(
        profile_name, "add", True
    )
    assert result[0] is False
    assert expected in result[1]
    update.assert_not_awaited()


async def test_target_update_uses_profile_targets_and_skips_other_profiles(
    monkeypatch: pytest.MonkeyPatch, tool_plugin: SimpleNamespace
) -> None:
    """名称匹配后仅转发受信配置中的目标和面板，不使用当前群替换它们。"""
    tool_plugin.config.features.menu_panel_allowed_group_openids.append("group-2")
    tool_plugin.config.features.menu_panel_profiles = [
        {"name": "other"},
        {"name": "trusted", "panel_id": " panel-1 ", "group_openids": ["group-2"],
         "allow_target_update": True},
    ]
    update = AsyncMock(return_value=_service_result(True))
    monkeypatch.setattr(QQBotMenuPanelService, "update_panel_targets", update)
    result = await _bind(QQUpdatePanelTargetsTool, tool_plugin, _message()).execute(
        " trusted ", "del", True
    )
    assert result[0] is True
    update.assert_awaited_once_with(
        "panel-1", "del", user_openids=None, group_openids=["group-2"]
    )


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("chat_type", ["group", "private"])
async def test_recall_tool_resolves_current_target(
    monkeypatch: pytest.MonkeyPatch,
    tool_plugin: SimpleNamespace,
    success: bool,
    chat_type: str,
) -> None:
    """撤回 Tool 只传递当前会话目标及显式消息 ID。"""
    response = _service_result(success)
    recall = AsyncMock(return_value=response)
    monkeypatch.setattr(QQBotUtilityService, "recall_message", recall)
    result = await _bind(QQRecallCurrentMessageTool, tool_plugin, _message(chat_type)).execute(
        "sent-message", True
    )
    assert result == (success, response["data"] if success else response["error"])
    recall.assert_awaited_once_with(
        "group" if chat_type == "group" else "user",
        "group-1" if chat_type == "group" else "operator-1",
        "sent-message",
    )


async def test_recall_without_trigger_has_no_service_call(
    monkeypatch: pytest.MonkeyPatch, tool_plugin: SimpleNamespace
) -> None:
    """确认撤回仍不能绕过发送目标校验。"""
    recall = AsyncMock()
    monkeypatch.setattr(QQBotUtilityService, "recall_message", recall)
    ok, error = await _bind(QQRecallCurrentMessageTool, tool_plugin, None).execute("m1", True)
    assert ok is False
    assert "发送目标" in error
    recall.assert_not_awaited()


@pytest.mark.parametrize("success", [True, False])
async def test_share_tool_forwards_explicit_arguments(
    monkeypatch: pytest.MonkeyPatch, tool_plugin: SimpleNamespace, success: bool
) -> None:
    """分享工具按关键字转发用户指定字段，并保留服务返回值。"""
    response = _service_result(success)
    generate = AsyncMock(return_value=response)
    monkeypatch.setattr(QQBotUtilityService, "generate_share_link", generate)
    result = await _bind(QQGenerateShareLinkTool, tool_plugin, _message()).execute(
        "source"
    )
    assert result == (success, response["data"] if success else response["error"])
    generate.assert_awaited_once_with(
        callback_data="source"
    )


async def test_keyboard_rejects_callback_only_group_id(tool_plugin: SimpleNamespace) -> None:
    """指令和链接按钮不能误带仅适用于回调按钮的互斥分组。"""
    ok, error = await _bind(QQSendKeyboardTool, tool_plugin, _message()).execute(
        "choose", [{"label": "run", "command": "/run", "group_id": "exclusive"}]
    )
    assert ok is False
    assert "不支持 group_id" in error
