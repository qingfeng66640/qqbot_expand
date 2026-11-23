"""菜单面板中文长度与 QQ 失败诊断的回归测试。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ..services.menu_panel_service import QQBotMenuPanelService
from ..src.managed_panels import ManagedPanelRuntime
from ..src.menu_panel_policy import normalize_menu, normalize_panel
from .conftest import FakeSendHandler, make_plugin


@pytest.mark.parametrize(
    ("field", "text", "accepted"),
    [
        ("name", "中" * 7, True),
        ("name", "中" * 8, False),
        ("name", "a" * 14, True),
        ("name", "a" * 15, False),
        ("name", "a" * 12 + "中", True),
        ("name", "a" * 13 + "中", False),
        ("name", "Ａ" * 7, True),
        ("name", "Ａ" * 8, False),
        ("desc", "中" * 15, True),
        ("desc", "中" * 16, False),
        ("desc", "a" * 30, True),
        ("desc", "a" * 31, False),
        ("desc", "a" * 28 + "中", True),
        ("desc", "a" * 29 + "中", False),
        ("desc", "", True),
    ],
)
def test_panel_display_text_uses_weighted_length(
    field: str, text: str, accepted: bool
) -> None:
    """名称和描述按中文/全角双字符计数，并标出项目索引。"""
    item = {"name": "状态", "desc": "说明", "type": "command", field: text}
    error, body = normalize_panel(
        {"items": [{"name": "/help", "type": "command"}, item]}
    )

    assert (error is None) is accepted
    if accepted:
        assert body["items"][1][field] == text
    else:
        assert error is not None and f"panel.items[1].{field}" in error
        assert body == {}


@pytest.mark.parametrize(
    ("nested", "name", "accepted"),
    [
        (False, "中" * 5, True),
        (False, "中" * 6, False),
        (False, "a" * 10, True),
        (False, "a" * 11, False),
        (False, "a" * 8 + "中", True),
        (False, "a" * 9 + "中", False),
        (True, "中" * 7, True),
        (True, "中" * 8, False),
        (True, "a" * 14, True),
        (True, "a" * 15, False),
    ],
)
def test_menu_names_use_weighted_length(
    nested: bool, name: str, accepted: bool
) -> None:
    """一级及二级菜单分别限制为 10 和 14 个展示字符。"""
    item: dict[str, Any] = {
        "name": name, "type": "send_message", "send_message": "/help"
    }
    items = (
        [{"name": "更多", "type": "menu", "sub_menu_items": [item]}]
        if nested else [item]
    )
    error, body = normalize_menu(items)

    assert (error is None) is accepted
    if not accepted:
        path = "menu.items[0].sub_menu_items[0].name" if nested else "menu.items[0].name"
        assert error is not None and path in error
        assert body == {}


def test_display_limits_do_not_change_url_remark_or_message_limits() -> None:
    """展示字符规则不外溢到链接、备注或发送正文。"""
    link = "https://example.com/" + "中" * 1200
    remark = "中" * 255
    error, body = normalize_panel(
        {"items": [{"name": "官网", "type": "link", "link": link}], "remark": remark}
    )
    assert error is None
    assert body["items"][0]["link"] == link
    assert body["remark"] == remark
    error, body = normalize_menu(
        [{"name": "帮助", "type": "send_message", "send_message": "中" * 1024}]
    )
    assert error is None
    assert body["menu"]["items"][0]["send_message"] == "中" * 1024


async def test_overlong_panel_is_rejected_before_adapter_post(
    patch_send_handler: FakeSendHandler,
) -> None:
    """真实 Service 校验拒绝超长中文，不能只测替身或工具层。"""
    service = QQBotMenuPanelService(make_plugin(enable_menu_panel_service=True))
    result = await service.create_panel(
        "group", "all", {"items": [{"name": "中" * 8, "type": "command"}]}
    )
    assert result["success"] is False
    assert "panel.items[0].name" in result["error"]
    assert patch_send_handler.posts == []


async def test_overlong_managed_panel_stops_before_ledger_or_remote_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """托管面板复用同一校验，任何非法声明停止整轮远端写入。"""
    plugin = make_plugin(enable_menu_panel_service=True)
    plugin.config.managed_panels = SimpleNamespace(
        enabled=True,
        items=[{
            "managed_key": "group-links-panel",
            "scope": "group",
            "target_type": "all",
            "panel": {"items": [{"name": "中" * 8, "type": "command"}]},
        }],
    )
    load = AsyncMock(return_value=None)
    create = AsyncMock(return_value={"success": True, "data": {"panel_id": "p1"}})
    monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.load_json", load)
    monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.save_json", AsyncMock())
    monkeypatch.setattr(QQBotMenuPanelService, "create_panel", create)

    result = await ManagedPanelRuntime(plugin).reconcile_once()

    assert result == {"created": 0, "updated": 0, "unchanged": 0, "failed": 1}
    load.assert_not_awaited()
    create.assert_not_awaited()
