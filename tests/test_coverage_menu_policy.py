"""菜单和面板嵌套类型、目标约束及 Service 预检覆盖。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from ..services.menu_panel_service import QQBotMenuPanelService
from .conftest import FakeHttpClient, FakeSendHandler, make_plugin


PANEL: dict[str, Any] = {"items": [{"name": "/help", "type": "command"}]}


@pytest.mark.parametrize(
    ("items", "error"),
    [
        (None, "最多 10 项的列表"),
        ([None], "菜单项必须是对象"),
        ([{"name": " ", "type": "link"}], "menu.items[0].name"),
        ([{"name": "help", "type": "send_message", "send_message": None}], "send_message 不能为空"),
        ([{"name": "help", "type": "send_message", "send_message": "x" * 1025}], "1024"),
        ([{"name": "site", "type": "link", "link": " "}], "link 不能为空"),
        ([{"name": "site", "type": "link", "link": "https://user:pass@example.test"}], "HTTPS"),
        ([{"name": "site", "type": "link", "link": "https:///missing-host"}], "HTTPS"),
        ([{"name": "mode", "type": "switch", "switch": None}], "switch 必须包含"),
        ([{"name": "mode", "type": "switch", "switch": {"default": 1, "switch_id": "s"}}], "布尔 default"),
        ([{"name": "mode", "type": "switch", "switch": {"default": False}}], "switch_id 不能为空"),
        ([{"name": "mode", "type": "switch", "switch": {"default": False, "switch_id": "x" * 65}}], "64"),
        ([{"name": "more", "type": "menu", "sub_menu_items": {}}], "1~5"),
        ([{"name": "more", "type": "menu", "sub_menu_items": [None]}], "菜单项必须是对象"),
        ([{"name": "more", "type": "menu", "sub_menu_items": [{"name": "bad", "type": "switch"}]}], "send_message"),
        ([{"name": "more", "type": "menu", "sub_menu_items": [{"name": "x", "type": "menu"}]}], "type 只能是"),
        ([{"name": "more", "type": "menu", "sub_menu_items": [None] * 6}], "1~5"),
    ],
)
async def test_invalid_menu_nested_fields_never_reach_put(
    items: Any, error: str, patch_send_handler: FakeSendHandler,
) -> None:
    """通过真实菜单 Service 触发嵌套校验，禁止部分菜单写入。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))
    original = deepcopy(items)

    result = await service.update_menu(items)

    assert result["success"] is False
    assert result["data"] is None
    assert error in result["error"]
    assert items == original
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize(
    ("panel", "error"),
    [
        (None, "panel 必须是对象"),
        ({}, "panel.items 数量"),
        ({"items": {}}, "panel.items 数量"),
        ({"items": []}, "panel.items 数量"),
        ({"items": PANEL["items"] * 21}, "1~20"),
        ({**PANEL, "remark": None}, "panel.remark"),
        ({**PANEL, "remark": "x" * 256}, "255"),
        ({"items": [PANEL["items"][0], None]}, "panel.items[1] 必须是对象"),
        ({"items": [PANEL["items"][0], "not-an-object"]}, "panel.items[1] 必须是对象"),
        ({"items": [{"name": "/help", "type": "command", "desc": None}]}, "panel.items[0].desc 必须是字符串"),
        ({"items": [{"name": "/help", "type": "command", "desc": []}]}, "panel.items[0].desc 必须是字符串"),
        ({"items": [{"name": None, "type": "command"}]}, "panel.items[0].name"),
        ({"items": [{"name": "bad", "type": "switch"}]}, "command 或 link"),
        ({"items": [{"name": "/help", "type": "command", "only_admin": 1}]}, "only_admin 必须是布尔值"),
        ({"items": [{"name": "site", "type": "link", "link": "http://example.test"}]}, "HTTPS"),
        ({"items": [{"name": "site", "type": "link", "link": None}]}, "link 不能为空"),
    ],
)
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_invalid_panel_types_fail_before_any_write(
    panel: Any,
    error: str,
    operation: str,
    patch_send_handler: FakeSendHandler,
) -> None:
    """包括非对象 item 和非字符串 desc 两条新增 guard，创建/更新均拒绝。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))
    original = deepcopy(panel)

    if operation == "create":
        result = await service.create_panel("group", "all", panel)
    else:
        result = await service.update_panel("owned-panel", panel)

    assert result["success"] is False
    assert result["data"] is None
    assert error in result["error"]
    assert panel == original
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize(
    ("scope", "target_type", "users", "groups", "error"),
    [
        ("private", "all", None, None, "scope"),
        ("group", "everyone", None, None, "target_type"),
        ("channel", "specific", None, ["g1"], "只支持全局"),
        ("dm", "specific", ["u1"], None, "只支持全局"),
        ("c2c", "all", ["u1"], None, "不能指定关联对象"),
        ("group", "all", None, ["g1"], "不能指定关联对象"),
        ("group", "specific", ["u1"], ["g1"], "与 scope 不匹配"),
        ("c2c", "specific", ["u1"], [], "与 scope 不匹配"),
        ("c2c", "specific", None, ["g1"], "user_openids"),
        ("group", "specific", ["u1"], None, "group_openids"),
        ("c2c", "specific", ["u1", " u1 "], None, "重复值"),
        ("group", "specific", None, ["g1", None], "非空字符串"),
        ("group", "specific", None, [" "], "非空字符串"),
        ("group", "specific", None, "g1", "1~20"),
        ("group", "specific", None, [f"g{i}" for i in range(21)], "1~20"),
    ],
)
async def test_create_panel_rejects_scope_and_target_mismatch(
    scope: str,
    target_type: str,
    users: Any,
    groups: Any,
    error: str,
    patch_send_handler: FakeSendHandler,
) -> None:
    """场景、对象种类、重复值和数量均在请求前校验。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))

    result = await service.create_panel(scope, target_type, PANEL, user_openids=users, group_openids=groups)

    assert result["success"] is False
    assert error in result["error"]
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("scope", ["c2c", "group", "channel", "dm"])
async def test_global_panel_accepts_supported_scopes_and_drops_unknown_fields(
    scope: str, patch_send_handler: FakeSendHandler,
) -> None:
    """所有全局场景可创建，未知字段不会越过官方请求白名单。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))
    panel = {
        "unknown": True, "remark": "note",
        "items": [{"name": " /help ", "type": "command", "unknown": "no", "link": "ignored"}],
    }
    original = deepcopy(panel)

    result = await service.create_panel(scope, "all", panel)

    assert result["success"] is True
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][2] == {
        "scope": scope, "target_type": "all",
        "panel": {"items": [{"name": "/help", "type": "command", "desc": "", "only_admin": False}], "remark": "note"},
    }
    assert panel == original
    assert client.calls == []


async def test_nested_menu_and_switch_are_rebuilt_without_unknown_fields(
    patch_send_handler: FakeSendHandler,
) -> None:
    """有效开关和二级链接规范化后完整 PUT，不修改调用方输入。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))
    items = [
        {"name": " mode ", "type": "switch", "switch": {"switch_id": " s1 ", "default": False, "private": True}, "private": True},
        {"name": "more", "type": "menu", "sub_menu_items": [{"name": "site", "type": "link", "link": " https://example.test/ ", "private": True}]},
    ]
    original = deepcopy(items)

    result = await service.update_menu(items)

    assert result["success"] is True
    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "PUT"
    assert client.calls[0]["url"].endswith("/v2/menu")
    assert client.calls[0]["json"] == {"menu": {"items": [
        {"name": "mode", "type": "switch", "switch": {"switch_id": "s1", "default": False}},
        {"name": "more", "type": "menu", "sub_menu_items": [{"name": "site", "type": "link", "link": "https://example.test/"}]},
    ]}}
    assert items == original
    assert patch_send_handler.posts == []


@pytest.mark.parametrize(
    ("method", "kwargs", "error"),
    [
        ("list_panels", {"scope": "private"}, "scope"),
        ("list_panels", {"scope": "group", "cursor": []}, "cursor"),
        ("list_panels", {"scope": "group", "limit": True}, "limit"),
        ("list_panels", {"scope": "group", "limit": 51}, "limit"),
        ("get_panel", {"panel_id": " "}, "panel_id"),
        ("delete_panel", {"panel_id": ""}, "panel_id"),
        ("update_panel", {"panel_id": None, "panel": PANEL}, "panel_id"),
        ("update_panel_targets", {"panel_id": " ", "op": "add", "user_openids": ["u1"]}, "panel_id"),
        ("update_panel_targets", {"panel_id": "p1", "op": "replace", "user_openids": ["u1"]}, "op"),
        ("update_panel_targets", {"panel_id": "p1", "op": "add"}, "必须且只能"),
        ("update_panel_targets", {"panel_id": "p1", "op": "add", "user_openids": ["u1"], "group_openids": ["g1"]}, "必须且只能"),
        ("update_panel_targets", {"panel_id": "p1", "op": "del", "group_openids": ["g1", " g1 "]}, "重复值"),
        ("update_panel_targets", {"panel_id": "p1", "op": "add", "user_openids": ["u1", False]}, "非空字符串"),
    ],
)
async def test_panel_service_preflight_guards_block_all_io(
    method: str,
    kwargs: dict[str, Any],
    error: str,
    patch_send_handler: FakeSendHandler,
) -> None:
    """路径、分页和关联对象错误不会落到统一 HTTP 出口。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))

    result = await getattr(service, method)(**kwargs)

    assert result["success"] is False
    assert error in result["error"]
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize(("scope", "key"), [("c2c", "user_openids"), ("group", "group_openids")])
async def test_specific_panel_and_target_update_accept_twenty_normalized_ids(
    scope: str, key: str, patch_send_handler: FakeSendHandler,
) -> None:
    """specific 关联对象的 20 人上界支持创建及删除关联，不改变对象种类。"""
    client = FakeHttpClient()
    service = QQBotMenuPanelService(make_plugin(http_client=client, enable_menu_panel_service=True))
    identifiers = [f" id{i} " for i in range(20)]
    normalized = [f"id{i}" for i in range(20)]

    created = await service.create_panel(scope, "specific", PANEL, **{key: identifiers})
    updated = await service.update_panel_targets("p/1", "del", **{key: identifiers})

    assert created["success"] is True and updated["success"] is True
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][2][key] == normalized
    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "PUT"
    assert client.calls[0]["url"].endswith("/v2/panels/p%2F1/target")
    assert client.calls[0]["json"] == {"op": "del", key: normalized}
