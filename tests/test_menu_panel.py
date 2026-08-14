"""QQ 菜单与指令面板 Service、策略与 Tool 测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ..services.menu_panel_service import QQBotMenuPanelService
from ..src.interaction_features import extract_feature_id
from ..src.menu_panel_policy import (
    normalize_menu,
    normalize_panel,
    normalize_panel_create,
    normalize_targets,
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
from .conftest import FakeHttpClient, FakeResponse, make_plugin


VALID_PANEL = {
    "items": [
        {"name": "/help", "desc": "查看帮助", "type": "command"},
        {
            "name": "官网",
            "desc": "打开官网",
            "type": "link",
            "only_admin": False,
            "link": "https://example.com",
        },
    ],
    "remark": "测试面板",
}
VALID_MENU = [
    {"name": "帮助", "type": "send_message", "send_message": "/help"},
    {
        "name": "更多",
        "type": "menu",
        "sub_menu_items": [
            {"name": "官网", "type": "link", "link": "https://example.com"}
        ],
    },
]


def _message(
    *, sender_id: str = "operator", chat_type: str = "private"
) -> SimpleNamespace:
    """构造菜单面板 Tool 的触发消息。"""
    return SimpleNamespace(
        sender_id=sender_id,
        chat_type=chat_type,
        message_id="message-1",
        extra={"group_id": "group-1"} if chat_type == "group" else {},
    )


def _tool(tool_type: type, plugin: object, *, chat_type: str = "private"):
    """构造并绑定 Tool 触发消息。"""
    tool = tool_type(plugin)
    tool.trigger_message = _message(chat_type=chat_type)
    return tool


def _enabled_plugin(*, http_client=None, **overrides):
    """构造已开启且具备最小授权的插件替身。"""
    values = {
        "http_client": http_client,
        "enable_menu_panel_service": True,
        "enable_menu_panel_tools": True,
        "allow_global_menu_write": True,
        "allow_panel_create": True,
        "allow_panel_delete": True,
        "menu_panel_allowed_operator_openids": ["operator"],
        "menu_panel_allowed_group_openids": ["group-1"],
        "menu_panel_allowed_panel_ids": ["panel/1"],
        "menu_panel_profiles": [
            {
                "name": "current-group",
                "scope": "group",
                "target_type": "specific",
                "group_openids": ["group-1"],
                "panel_id": "panel/1",
                "allow_target_update": True,
            }
        ],
    }
    values.update(overrides)
    return make_plugin(**values)


class TestMenuPanelPolicy:
    """官方菜单和面板字段约束。"""

    def test_normalizes_menu_and_drops_unknown_fields(self) -> None:
        """合法菜单被白名单重建，未知字段不会透传。"""
        items = [{**VALID_MENU[0], "unknown": "ignored"}]
        error, body = normalize_menu(items)
        assert error is None
        assert body == {
            "menu": {
                "items": [
                    {"name": "帮助", "type": "send_message", "send_message": "/help"}
                ]
            }
        }

    @pytest.mark.parametrize(
        ("items", "expected"),
        [
            ([{"name": "x", "type": "link", "link": "http://bad"}], "HTTPS"),
            ([{"name": "x", "type": "switch", "switch": {"switch_id": "s"}}], "switch"),
            ([{"name": "x", "type": "menu", "sub_menu_items": []}], "1~5"),
            ([{"name": "x", "type": "invalid"}], "type"),
        ],
    )
    def test_rejects_invalid_menu_items(self, items, expected: str) -> None:
        """非法链接、开关、子菜单和类型会被拒绝。"""
        error, _ = normalize_menu(items)
        assert error and expected in error

    def test_rejects_menu_item_overflow(self) -> None:
        """一级菜单最多十项。"""
        error, _ = normalize_menu([VALID_MENU[0]] * 11)
        assert error

    def test_normalizes_panel(self) -> None:
        """面板最多保留官方字段。"""
        panel = {**VALID_PANEL, "unknown": True}
        error, normalized = normalize_panel(panel)
        assert error is None
        assert "unknown" not in normalized
        assert normalized["items"][0] == {
            "name": "/help",
            "desc": "查看帮助",
            "type": "command",
            "only_admin": False,
        }

    @pytest.mark.parametrize(
        ("scope", "target_type", "users", "groups"),
        [
            ("channel", "specific", None, ["g1"]),
            ("dm", "specific", ["u1"], None),
            ("c2c", "specific", None, ["g1"]),
            ("group", "specific", ["u1"], None),
            ("group", "all", None, ["g1"]),
        ],
    )
    def test_rejects_invalid_scope_target_combinations(
        self, scope, target_type, users, groups
    ) -> None:
        """scope、target_type 与 OpenID 类型必须匹配。"""
        error, _ = normalize_panel_create(
            scope,
            target_type,
            VALID_PANEL,
            user_openids=users,
            group_openids=groups,
        )
        assert error

    def test_rejects_duplicate_or_mixed_targets(self) -> None:
        """关联目标不可重复，也不能同时传用户和群。"""
        assert normalize_targets("add", user_openids=["u1", "u1"])[0]
        assert normalize_targets("add", user_openids=["u1"], group_openids=["g1"])[0]
        assert normalize_targets("replace", group_openids=["g1"])[0]


class TestMenuPanelService:
    """八个官方端点的请求映射。"""

    async def test_service_defaults_to_disabled(self) -> None:
        """默认关闭时不会调用网络出口。"""
        result = await QQBotMenuPanelService(make_plugin()).get_menu()
        assert result == {
            "success": False,
            "data": None,
            "error": "菜单与指令面板 Service 未启用",
        }

    async def test_get_and_update_menu(self, patch_send_handler) -> None:
        """菜单查询使用 GET，更新使用 PUT。"""
        client = FakeHttpClient(
            [FakeResponse(200, {"version": 1}), FakeResponse(200, {"version": 2})]
        )
        service = QQBotMenuPanelService(_enabled_plugin(http_client=client))
        assert (await service.get_menu())["success"] is True
        assert (await service.update_menu(VALID_MENU))["success"] is True
        assert [call["method"] for call in client.calls] == ["GET", "PUT"]
        assert client.calls[1]["json"]["menu"]["items"][1]["type"] == "menu"

    async def test_list_panels_query(self, patch_send_handler) -> None:
        """面板列表查询携带 scope、cursor 和 limit。"""
        client = FakeHttpClient([FakeResponse(200, {"records": []})])
        result = await QQBotMenuPanelService(
            _enabled_plugin(http_client=client)
        ).list_panels("group", "next", 10)
        assert result["success"] is True
        assert client.calls[0]["method"] == "GET"
        assert "scope=group" in client.calls[0]["url"]
        assert "cursor=next" in client.calls[0]["url"]
        assert "limit=10" in client.calls[0]["url"]

    async def test_create_panel_uses_adapter_post(
        self, patch_send_handler, send_handler
    ) -> None:
        """创建面板使用 Adapter POST 出口。"""
        send_handler.post_result = {"panel_id": "panel-1"}
        result = await QQBotMenuPanelService(_enabled_plugin()).create_panel(
            "group",
            "specific",
            VALID_PANEL,
            group_openids=["group-1"],
        )
        assert result == {
            "success": True,
            "data": {"panel_id": "panel-1"},
            "error": None,
        }
        url, _, body = send_handler.posts[0]
        assert url.endswith("/v2/panels")
        assert body["group_openids"] == ["group-1"]

    async def test_panel_detail_update_delete_and_targets(
        self, patch_send_handler
    ) -> None:
        """详情、更新、删除和关联操作编码 panel_id 并使用正确方法。"""
        client = FakeHttpClient([FakeResponse(200, {}) for _ in range(4)])
        service = QQBotMenuPanelService(_enabled_plugin(http_client=client))
        assert (await service.get_panel("panel/1"))["success"] is True
        assert (await service.update_panel("panel/1", VALID_PANEL))["success"] is True
        assert (await service.delete_panel("panel/1"))["success"] is True
        assert (
            await service.update_panel_targets(
                "panel/1", "add", group_openids=["group-1"]
            )
        )["success"] is True
        assert [call["method"] for call in client.calls] == [
            "GET",
            "PUT",
            "DELETE",
            "PUT",
        ]
        assert all("panel%2F1" in call["url"] for call in client.calls)
        assert client.calls[-1]["url"].endswith("/target")

    async def test_rejects_invalid_list_limit_without_network(self) -> None:
        """非法分页参数在请求前失败。"""
        client = FakeHttpClient()
        result = await QQBotMenuPanelService(
            _enabled_plugin(http_client=client)
        ).list_panels("group", limit=51)
        assert result["success"] is False
        assert client.calls == []


class TestMenuPanelTools:
    """LLM Tool 的运行期安全门禁。"""

    async def test_tools_require_operator_and_current_group_whitelists(self) -> None:
        """操作者或当前群未授权时拒绝。"""
        plugin = _enabled_plugin(menu_panel_allowed_operator_openids=[])
        ok, message = await _tool(QQGetMenuPanelTool, plugin).execute()
        assert ok is False
        assert "操作者" in message

        plugin = _enabled_plugin(menu_panel_allowed_group_openids=[])
        ok, message = await _tool(
            QQGetMenuPanelTool, plugin, chat_type="group"
        ).execute()
        assert ok is False
        assert "当前群" in message

    async def test_menu_write_requires_switch_and_confirmation(self) -> None:
        """全局菜单写入需要独立开关和 confirm。"""
        tool = _tool(QQUpdateMenuTool, _enabled_plugin())
        assert (await tool.execute(VALID_MENU, False))[0] is False
        tool = _tool(
            QQUpdateMenuTool,
            _enabled_plugin(allow_global_menu_write=False),
        )
        assert (await tool.execute(VALID_MENU, True))[0] is False

    async def test_panel_update_and_delete_require_authorized_panel(self) -> None:
        """更新和删除不能操作白名单外的 panel_id。"""
        plugin = _enabled_plugin()
        update = _tool(QQUpdatePanelTool, plugin)
        delete = _tool(QQDeletePanelTool, plugin)
        assert (await update.execute("other", VALID_PANEL, True))[0] is False
        assert (await delete.execute("other", True))[0] is False
        assert (await delete.execute("panel/1", False))[0] is False

    def test_create_schema_makes_profile_optional(self) -> None:
        """创建面板暴露准确项目结构，且 profile 可选。"""
        parameters = QQCreatePanelTool.to_schema()["function"]["parameters"]
        assert set(parameters["required"]) == {"panel", "confirm"}
        assert parameters["properties"]["profile_name"]["default"] == ""
        item_schema = parameters["properties"]["panel"]["properties"]["items"][
            "items"
        ]
        assert set(item_schema["properties"]) == {
            "name",
            "desc",
            "type",
            "only_admin",
            "link",
        }
        assert set(item_schema["required"]) == {"name", "type"}
        assert item_schema["properties"]["type"]["enum"] == ["command", "link"]
        assert {"label", "command", "url"}.isdisjoint(item_schema["properties"])

    def test_menu_and_target_enums_are_exposed(self) -> None:
        """菜单结构、场景和关联操作均向模型暴露枚举。"""
        menu = QQUpdateMenuTool.to_schema()["function"]["parameters"]["properties"]
        menu_item = menu["items"]["items"]
        assert menu_item["properties"]["type"]["enum"] == [
            "switch",
            "send_message",
            "link",
            "menu",
        ]
        assert set(menu_item["properties"]) == {
            "name",
            "type",
            "switch",
            "send_message",
            "link",
            "sub_menu_items",
        }
        list_properties = QQListPanelsTool.to_schema()["function"]["parameters"][
            "properties"
        ]
        assert list_properties["scope"]["enum"] == [
            "c2c",
            "group",
            "channel",
            "dm",
        ]
        target_properties = QQUpdatePanelTargetsTool.to_schema()["function"][
            "parameters"
        ]["properties"]
        assert target_properties["op"]["enum"] == ["add", "del"]

    async def test_create_defaults_to_current_private_user(self, monkeypatch) -> None:
        """私聊省略 profile 时只投放到当前用户。"""
        request = AsyncMock(return_value={"success": True, "data": {"panel_id": "p1"}})
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", request)
        tool = _tool(QQCreatePanelTool, _enabled_plugin())
        ok, data = await tool.execute(VALID_PANEL, True)
        assert ok is True
        assert data == {"panel_id": "p1"}
        assert request.await_args.args[:3] == ("c2c", "specific", VALID_PANEL)
        assert request.await_args.kwargs["user_openids"] == ["operator"]
        assert request.await_args.kwargs["group_openids"] is None

    async def test_create_defaults_to_current_group(self, monkeypatch) -> None:
        """群聊省略 profile 时只投放到当前白名单群。"""
        request = AsyncMock(return_value={"success": True, "data": {"panel_id": "p1"}})
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", request)
        tool = _tool(QQCreatePanelTool, _enabled_plugin(), chat_type="group")
        ok, _ = await tool.execute(VALID_PANEL, True)
        assert ok is True
        assert request.await_args.args[:3] == ("group", "specific", VALID_PANEL)
        assert request.await_args.kwargs["user_openids"] is None
        assert request.await_args.kwargs["group_openids"] == ["group-1"]

    async def test_create_current_target_keeps_authorization(self, monkeypatch) -> None:
        """默认当前目标仍受确认、创建开关和白名单约束。"""
        request = AsyncMock(return_value={"success": True, "data": {}})
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", request)

        tool = _tool(QQCreatePanelTool, _enabled_plugin())
        assert (await tool.execute(VALID_PANEL, False))[0] is False

        tool = _tool(
            QQCreatePanelTool,
            _enabled_plugin(allow_panel_create=False),
        )
        assert (await tool.execute(VALID_PANEL, True))[0] is False

        tool = _tool(
            QQCreatePanelTool,
            _enabled_plugin(menu_panel_allowed_operator_openids=[]),
        )
        assert (await tool.execute(VALID_PANEL, True))[0] is False

        tool = _tool(
            QQCreatePanelTool,
            _enabled_plugin(menu_panel_allowed_group_openids=[]),
            chat_type="group",
        )
        assert (await tool.execute(VALID_PANEL, True))[0] is False
        request.assert_not_awaited()

    async def test_create_uses_explicit_profile_targets(self, monkeypatch) -> None:
        """显式 profile 仍可使用预配置目标。"""
        request = AsyncMock(return_value={"success": True, "data": {"panel_id": "p1"}})
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", request)
        tool = _tool(QQCreatePanelTool, _enabled_plugin())
        ok, data = await tool.execute(
            VALID_PANEL,
            True,
            profile_name="current-group",
        )
        assert ok is True
        assert data == {"panel_id": "p1"}
        assert request.await_args.kwargs["group_openids"] == ["group-1"]

    async def test_profile_cannot_target_group_outside_whitelist(
        self, monkeypatch
    ) -> None:
        """profile 不能绕过群目标白名单。"""
        request = AsyncMock(return_value={"success": True, "data": {}})
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", request)
        plugin = _enabled_plugin(
            menu_panel_profiles=[
                {
                    "name": "outside-group",
                    "scope": "group",
                    "target_type": "specific",
                    "group_openids": ["group-2"],
                }
            ]
        )
        tool = _tool(QQCreatePanelTool, plugin, chat_type="group")
        ok, message = await tool.execute(
            VALID_PANEL,
            True,
            profile_name="outside-group",
        )
        assert ok is False
        assert "未授权的群目标" in message
        request.assert_not_awaited()

    async def test_target_update_uses_profile_targets(self, monkeypatch) -> None:
        """关联对象由 profile 固定，Tool 不接受任意 OpenID。"""
        request = AsyncMock(return_value={"success": True, "data": {}})
        monkeypatch.setattr(QQBotMenuPanelService, "update_panel_targets", request)
        tool = _tool(QQUpdatePanelTargetsTool, _enabled_plugin())
        assert (await tool.execute("current-group", "add", False))[0] is False
        assert (await tool.execute("current-group", "add", True))[0] is True
        assert request.await_args.args[:2] == ("panel/1", "add")
        assert request.await_args.kwargs["group_openids"] == ["group-1"]


class TestInteractionFeature:
    """快捷菜单 feature_id 的无契约侵入提取。"""

    def test_extracts_feature_id_from_raw_event(self) -> None:
        """从官方字段路径读取并修剪 feature_id。"""
        raw_event = {"data": {"resolved": {"feature_id": " feature-1 "}}}
        assert extract_feature_id(raw_event) == "feature-1"

    @pytest.mark.parametrize(
        "raw_event",
        [{}, None, {"data": {"resolved": {"feature_id": 1}}}],
    )
    def test_invalid_feature_id_safely_degrades(self, raw_event) -> None:
        """字段缺失或类型错误时返回空串。"""
        assert extract_feature_id(raw_event) == ""
