"""群成员与黑名单 Service 与 Tool 测试。"""
from __future__ import annotations

from types import SimpleNamespace

from ..services.group_members_service import QQBotGroupMembersService
from ..tools.group_members import (
    QQBatchRemoveGroupMembersTool,
    QQManageGroupBlacklistTool,
    QQQueryGroupMembersTool,
)
from .conftest import FakeHttpClient, FakeResponse, make_plugin


def _group_message() -> SimpleNamespace:
    return SimpleNamespace(chat_type="group", extra={"group_id": "g1"}, message_id="m1")


class TestGroupMembersService:
    """验证群成员 API 封装与参数校验。"""

    async def test_lists_members(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"members": [], "next_cursor": ""})])
        result = await QQBotGroupMembersService(make_plugin(http_client=client)).list_members("g1")
        assert result["success"] is True
        assert client.calls[0]["method"] == "GET"
        assert client.calls[0]["url"].endswith("/v2/groups/g1/members?cursor=")

    async def test_lists_members_with_cursor(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"members": [], "next_cursor": ""})])
        await QQBotGroupMembersService(make_plugin(http_client=client)).list_members("g1", cursor="abc")
        assert "cursor=abc" in client.calls[0]["url"]

    async def test_rejects_non_string_cursor(self, patch_send_handler) -> None:
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).list_members("g1", cursor=123)  # type: ignore[arg-type]
        assert result["success"] is False
        assert "cursor" in result["error"]

    async def test_gets_member(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"member_openid": "m1"})])
        result = await QQBotGroupMembersService(make_plugin(http_client=client)).get_member("g1", "m1")
        assert result["success"] is True
        assert client.calls[0]["url"].endswith("/v2/groups/g1/members/m1")

    async def test_encodes_openids(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        await QQBotGroupMembersService(make_plugin(http_client=client)).get_member("g/1", "m/2")
        assert "/v2/groups/g%2F1/members/m%2F2" in client.calls[0]["url"]

    async def test_lists_blacklist(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"users": [], "next_cursor": ""})])
        result = await QQBotGroupMembersService(make_plugin(http_client=client)).list_blacklist("g1", limit=50)
        assert result["success"] is True
        assert "limit=50" in client.calls[0]["url"]

    async def test_rejects_invalid_blacklist_limit(self, patch_send_handler) -> None:
        service = QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient()))
        for limit in (0, 101, True, "20"):  # type: ignore[list-item]
            result = await service.list_blacklist("g1", limit=limit)  # type: ignore[arg-type]
            assert result["success"] is False
            assert "limit" in result["error"]

    async def test_updates_blacklist_add(self, patch_send_handler) -> None:
        handler = patch_send_handler
        handler.post_results = [{"fail_openids": []}]
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).update_blacklist(
            "g1", "add", ["m1", "m2"]
        )
        assert result["success"] is True
        url, _, body = handler.posts[0]
        assert url.endswith("/v2/groups/g1/member_blacklist")
        assert body == {"op": "add", "member_openids": ["m1", "m2"]}

    async def test_updates_blacklist_del(self, patch_send_handler) -> None:
        handler = patch_send_handler
        handler.post_results = [{"fail_openids": []}]
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).update_blacklist(
            "g1", "del", ["m1"]
        )
        assert result["success"] is True
        assert handler.posts[0][2]["op"] == "del"

    async def test_rejects_invalid_blacklist_op(self, patch_send_handler) -> None:
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).update_blacklist(
            "g1", "clear", ["m1"]
        )
        assert result["success"] is False
        assert "op" in result["error"]

    async def test_rejects_too_many_blacklist_openids(self, patch_send_handler) -> None:
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).update_blacklist(
            "g1", "add", [f"m{i}" for i in range(21)]
        )
        assert result["success"] is False
        assert "member_openids" in result["error"]

    async def test_rejects_empty_blacklist_openid_item(self, patch_send_handler) -> None:
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).update_blacklist(
            "g1", "add", ["m1", "  "]
        )
        assert result["success"] is False
        assert "member_openids" in result["error"]

    async def test_batch_remove_members(self, patch_send_handler) -> None:
        handler = patch_send_handler
        handler.post_results = [{"remove_members_result": "success"}]
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).batch_remove_members(
            "g1", ["m1"], add_to_member_blacklist=True
        )
        assert result["success"] is True
        url, _, body = handler.posts[0]
        assert url.endswith("/v2/groups/g1/batch_remove_members")
        assert body == {"member_openids": ["m1"], "add_to_member_blacklist": True}

    async def test_batch_remove_rejects_too_many(self, patch_send_handler) -> None:
        result = await QQBotGroupMembersService(make_plugin(http_client=FakeHttpClient())).batch_remove_members(
            "g1", [f"m{i}" for i in range(21)]
        )
        assert result["success"] is False
        assert "member_openids" in result["error"]

    async def test_service_disabled_rejects_all(self, patch_send_handler) -> None:
        plugin = make_plugin(http_client=FakeHttpClient(), enable_group_members_service=False)
        service = QQBotGroupMembersService(plugin)
        for coro in (
            service.list_members("g1"),
            service.get_member("g1", "m1"),
            service.list_blacklist("g1"),
            service.update_blacklist("g1", "add", ["m1"]),
            service.batch_remove_members("g1", ["m1"]),
        ):
            result = await coro
            assert result["success"] is False
            assert "未启用" in result["error"]


class TestGroupMembersTools:
    """验证 Tool 的白名单、confirm 与触发群约束。"""

    def _tool(self, plugin: SimpleNamespace, cls: type) -> object:
        tool = cls(plugin)
        tool.trigger_message = _group_message()
        return tool

    async def test_query_members(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"members": [{"member_openid": "m1"}]})])
        plugin = make_plugin(
            http_client=client,
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        success, data = await self._tool(plugin, QQQueryGroupMembersTool).execute(query="members")  # type: ignore[attr-defined]
        assert success is True
        assert data["members"][0]["member_openid"] == "m1"

    async def test_query_member_requires_openid(self, patch_send_handler) -> None:
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        success, message = await self._tool(plugin, QQQueryGroupMembersTool).execute(query="member")  # type: ignore[attr-defined]
        assert success is False
        assert "member_openid" in message

    async def test_query_blacklist(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"users": [], "next_cursor": ""})])
        plugin = make_plugin(
            http_client=client,
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        success, _ = await self._tool(plugin, QQQueryGroupMembersTool).execute(query="blacklist")  # type: ignore[attr-defined]
        assert success is True

    async def test_blacklist_requires_confirm(self, patch_send_handler) -> None:
        handler = patch_send_handler
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        success, message = await self._tool(plugin, QQManageGroupBlacklistTool).execute(  # type: ignore[attr-defined]
            op="add", member_openids=["m1"]
        )
        assert success is False
        assert "confirm" in message
        assert handler.posts == []

    async def test_blacklist_with_confirm_posts(self, patch_send_handler) -> None:
        handler = patch_send_handler
        handler.post_results = [{"fail_openids": []}]
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        success, data = await self._tool(plugin, QQManageGroupBlacklistTool).execute(  # type: ignore[attr-defined]
            op="add", member_openids=["m1"], confirm=True
        )
        assert success is True
        assert data["fail_openids"] == []

    async def test_batch_remove_requires_confirm(self, patch_send_handler) -> None:
        handler = patch_send_handler
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        success, message = await self._tool(plugin, QQBatchRemoveGroupMembersTool).execute(  # type: ignore[attr-defined]
            member_openids=["m1"]
        )
        assert success is False
        assert "confirm" in message
        assert handler.posts == []

    async def test_rejects_when_tools_disabled(self, patch_send_handler) -> None:
        plugin = make_plugin(http_client=FakeHttpClient(), enable_group_members_tools=False)
        success, message = await self._tool(plugin, QQQueryGroupMembersTool).execute(query="members")  # type: ignore[attr-defined]
        assert success is False
        assert "未启用" in message

    async def test_rejects_empty_whitelist(self, patch_send_handler) -> None:
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=[],
        )
        success, message = await self._tool(plugin, QQQueryGroupMembersTool).execute(query="members")  # type: ignore[attr-defined]
        assert success is False
        assert "白名单" in message

    async def test_rejects_group_not_in_whitelist(self, patch_send_handler) -> None:
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["other"],
        )
        success, message = await self._tool(plugin, QQQueryGroupMembersTool).execute(query="members")  # type: ignore[attr-defined]
        assert success is False
        assert "白名单" in message

    async def test_rejects_private_message(self) -> None:
        plugin = make_plugin(
            http_client=FakeHttpClient(),
            enable_group_members_tools=True,
            group_members_allowed_group_openids=["g1"],
        )
        tool = QQQueryGroupMembersTool(plugin)
        tool.trigger_message = SimpleNamespace(chat_type="private", sender_id="u1", extra={})
        success, message = await tool.execute(query="members")  # type: ignore[attr-defined]
        assert success is False
        assert "群会话" in message
