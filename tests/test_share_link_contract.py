"""分享链接参数与官方响应包装的离线回归。"""
from __future__ import annotations

import inspect

import pytest

from ..services.utility_service import QQBotUtilityService
from ..tools.utility import QQGenerateShareLinkTool
from .conftest import FakeSendHandler, make_plugin


@pytest.mark.parametrize("link", ["https://example.test", " ", "url"])
async def test_undocumented_share_url_is_rejected_without_request(
    link: str, patch_send_handler: FakeSendHandler,
) -> None:
    """Old URL arguments are not ignored or sent as undocumented fields."""
    result = await QQBotUtilityService(make_plugin()).generate_share_link(url_link=link)
    assert result["success"] is False
    assert "url_link" in result["error"]
    assert patch_send_handler.posts == []


def test_share_tool_does_not_expose_deprecated_url() -> None:
    """不将旧字段列为模型参数。"""
    assert 'url_link' not in inspect.signature(QQGenerateShareLinkTool.execute).parameters


@pytest.mark.parametrize("callback", ["", "来源😊", "x" * 32])
async def test_share_link_preserves_official_nested_response_without_new_side_effects(
    callback: str, patch_send_handler: FakeSendHandler,
) -> None:
    """The wrapper retains the official data.url response without flattening it."""
    patch_send_handler.post_result = {"data": {"url": "https://qun.qq.com/synthetic-share"}}
    result = await QQBotUtilityService(make_plugin()).generate_share_link(callback_data=callback)
    assert result["success"] is True
    assert result["data"]["data"]["url"] == "https://qun.qq.com/synthetic-share"
    assert patch_send_handler.posts[0][2] == ({"callback_data": callback} if callback else {})
    assert len(patch_send_handler.posts) == 1
