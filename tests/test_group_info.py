"""当前群信息 Service 与 Tool 测试。"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from ..services.group_info_service import QQBotGroupInfoService
from ..tools.group_info import QQGetCurrentGroupBotStateTool, QQGetCurrentGroupInfoTool
from .conftest import FakeHttpClient, FakeResponse, FakeSendHandler, make_plugin


def _group_message() -> SimpleNamespace:
    return SimpleNamespace(chat_type="group", extra={"group_id": "g1"}, message_id="m1")


class TestGroupInfoService:
    """验证群信息 API 封装。"""

    async def test_gets_group_info(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"group_openid": "g1"})])
        result = await QQBotGroupInfoService(make_plugin(http_client=client)).get_group_info("g1")
        assert result["success"] is True
        assert client.calls[0]["method"] == "GET"
        assert client.calls[0]["url"].endswith("/v2/groups/g1/info")

    async def test_gets_bot_group_state(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"member_role": "admin"})])
        result = await QQBotGroupInfoService(make_plugin(http_client=client)).get_bot_group_state("g1")
        assert result["success"] is True
        assert client.calls[0]["url"].endswith("/v2/groups/g1/bot_state")

    async def test_encodes_group_openid(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        await QQBotGroupInfoService(make_plugin(http_client=client)).get_group_info("g/1")
        assert "/v2/groups/g%2F1/info" in client.calls[0]["url"]


class TestGroupInfoTools:
    """验证 Tool 只使用触发群。"""

    @pytest.mark.parametrize("union", ["union-group", "", None])
    async def test_info_tool_uses_group_openid_and_preserves_union_metadata(
        self, patch_send_handler: FakeSendHandler, union: str | None,
    ) -> None:
        """Union 字段原样返回；即使上下文提供它，也不能用于请求路由。"""
        payload = {"group_openid": "g1", "group_name": "测试群"}
        if union is not None:
            payload["union_group_openid"] = union
        requests: list[httpx.Request] = []

        def reply(request: httpx.Request) -> httpx.Response:
            """仅替换 HTTP 边界，保留真实 Tool 和 Service 调用链。"""
            requests.append(request)
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply), trust_env=False) as client:
            plugin = make_plugin(http_client=client)
            plugin.config.features.enable_group_info_tools = True
            tool = QQGetCurrentGroupInfoTool(plugin)
            tool.trigger_message = _group_message()
            tool.trigger_message.extra["union_group_openid"] = "not-route"
            success, data = await tool.execute()

        assert success is True
        assert data == payload
        assert [(request.method, request.url.path) for request in requests] == [("GET", "/v2/groups/g1/info")]

    async def test_state_tool_rejects_private_message(self) -> None:
        plugin = make_plugin(http_client=FakeHttpClient())
        plugin.config.features.enable_group_info_tools = True
        tool = QQGetCurrentGroupBotStateTool(plugin)
        tool.trigger_message = SimpleNamespace(chat_type="private", sender_id="u1", extra={})
        success, message = await tool.execute()
        assert success is False
        assert "群会话" in message
