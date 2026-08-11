"""群管理 Service 与受控 Tool 测试。"""
from __future__ import annotations

from types import SimpleNamespace

from ..services.group_admin_service import QQBotGroupAdminService
from ..tools.group_admin import QQReviewGroupJoinRequestTool, QQSetGroupMemberMuteTool
from .conftest import FakeHttpClient, FakeResponse, make_plugin


def _group_message(group_id: str = "g1") -> SimpleNamespace:
    return SimpleNamespace(chat_type="group", extra={"group_id": group_id}, message_id="m1")


class TestGroupAdminService:
    """验证官方群管理路径与参数边界。"""

    async def test_updates_strategy_with_patch(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {"is_enable": "off"})])
        service = QQBotGroupAdminService(make_plugin(http_client=client))
        result = await service.update_join_approval_strategy("st1", is_enable="off")
        assert result["success"] is True
        assert client.calls[0]["method"] == "PATCH"
        assert client.calls[0]["url"].endswith("/v2/groups/join_approval_strategy/st1")
        assert client.calls[0]["json"] == {"is_enable": "off"}

    async def test_creates_strategy_with_single_group_identifier(self, patch_send_handler) -> None:
        service = QQBotGroupAdminService(make_plugin(http_client=FakeHttpClient()))
        result = await service.create_join_approval_strategy(group_openids=["g1"])
        assert result["success"] is True
        assert patch_send_handler.posts[-1][2] == {"group_openids": ["g1"], "is_enable": "on"}

    async def test_rejects_conflicting_group_identifiers(self) -> None:
        service = QQBotGroupAdminService(make_plugin())
        result = await service.create_join_approval_strategy(group_openids=["g1"], group_ids=["1"])
        assert result["success"] is False

    async def test_approves_join_request(self, patch_send_handler) -> None:
        service = QQBotGroupAdminService(make_plugin(http_client=FakeHttpClient()))
        result = await service.approve_join_request("g1", "u1", "approve", join_request_id="r1")
        assert result["success"] is True
        url, _, body = patch_send_handler.posts[-1]
        assert url.endswith("/v2/groups/g1/approval_join_request/u1")
        assert body == {"op": "approve", "join_request_id": "r1"}

    async def test_rejects_disabled_service(self) -> None:
        service = QQBotGroupAdminService(make_plugin(enable_group_admin_service=False))
        result = await service.approve_join_request("g1", "u1", "approve")
        assert result["success"] is False
        assert "未启用" in result["error"]

    async def test_rejects_invalid_approval_fields(self) -> None:
        service = QQBotGroupAdminService(make_plugin())
        result = await service.approve_join_request("g1", "u1", "approve", reject_reason="no")
        assert result["success"] is False

    async def test_encodes_path_parameters(self, patch_send_handler) -> None:
        service = QQBotGroupAdminService(make_plugin(http_client=FakeHttpClient()))
        result = await service.approve_join_request("g/1", "u?1", "approve")
        assert result["success"] is True
        assert "/v2/groups/g%2F1/approval_join_request/u%3F1" in patch_send_handler.posts[-1][0]

    async def test_validates_group_action(self) -> None:
        service = QQBotGroupAdminService(make_plugin())
        invalid = await service.update_join_approval_strategy(
            "st1", group_action={"op": "add", "group_openids": "g1"}
        )
        assert invalid["success"] is False

    async def test_sets_member_mutes(self, patch_send_handler) -> None:
        service = QQBotGroupAdminService(make_plugin(http_client=FakeHttpClient()))
        result = await service.set_member_mute_states("g1", [{"op": "add", "member_openid": "u1", "mute_expire_at": "2026-08-11T00:00:00+08:00"}])
        assert result["success"] is True
        assert patch_send_handler.posts[-1][2]["members"][0]["op"] == "add"

    async def test_rejects_invalid_mute_member_fields(self) -> None:
        service = QQBotGroupAdminService(make_plugin())
        result = await service.set_member_mute_states(
            "g1", [{"op": "add", "member_openid": "u1", "mute_expire_at": "invalid", "unexpected": "value"}]
        )
        assert result["success"] is False

    async def test_mute_member_omits_unknown_fields(self, patch_send_handler) -> None:
        service = QQBotGroupAdminService(make_plugin(http_client=FakeHttpClient()))
        result = await service.set_member_mute_states(
            "g1", [{"op": "del", "member_openid": "u1", "unexpected": "value"}]
        )
        assert result["success"] is True
        assert patch_send_handler.posts[-1][2]["members"] == [{"op": "del", "member_openid": "u1"}]


class TestGroupAdminTools:
    """验证 Tool 只可操作触发群的白名单。"""

    async def test_review_tool_rejects_non_whitelisted_group(self) -> None:
        tool = QQReviewGroupJoinRequestTool(make_plugin(http_client=FakeHttpClient()))
        tool.trigger_message = _group_message()
        success, message = await tool.execute("u1", "approve")
        assert success is False
        assert "白名单" in message

    async def test_review_tool_uses_trigger_group(self, patch_send_handler) -> None:
        tool = QQReviewGroupJoinRequestTool(make_plugin(http_client=FakeHttpClient(), group_admin_allowed_group_openids=["g1"]))
        tool.trigger_message = _group_message()
        success, _ = await tool.execute("u1", "approve", "r1")
        assert success is True
        assert "/v2/groups/g1/approval_join_request/u1" in patch_send_handler.posts[-1][0]

    async def test_review_tool_requires_join_request_id(self) -> None:
        tool = QQReviewGroupJoinRequestTool(
            make_plugin(
                http_client=FakeHttpClient(),
                group_admin_allowed_group_openids=["g1"],
            )
        )
        tool.trigger_message = _group_message()
        success, message = await tool.execute("u1", "approve")
        assert success is False
        assert "join_request_id" in message

    async def test_tools_recheck_runtime_enable_flags(self) -> None:
        tool = QQReviewGroupJoinRequestTool(
            make_plugin(
                http_client=FakeHttpClient(),
                enable_group_admin_tools=False,
                group_admin_allowed_group_openids=["g1"],
            )
        )
        tool.trigger_message = _group_message()
        success, message = await tool.execute("u1", "approve")
        assert success is False
        assert "未启用" in message

    async def test_mute_tool_rejects_private_message(self) -> None:
        tool = QQSetGroupMemberMuteTool(make_plugin(http_client=FakeHttpClient(), group_admin_allowed_group_openids=["g1"]))
        tool.trigger_message = SimpleNamespace(chat_type="private", sender_id="u1", extra={})
        success, message = await tool.execute([{"op": "del", "member_openid": "u2"}])
        assert success is False
        assert "群会话" in message
