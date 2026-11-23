"""Current archived request bodies and nested share-link responses."""
from __future__ import annotations

import inspect

from ..services.group_members_service import QQBotGroupMembersService
from .conftest import FakeSendHandler, make_plugin


async def test_default_member_removal_sends_only_current_documented_fields(
    patch_send_handler: FakeSendHandler,
) -> None:
    """Omitting the old optional false field preserves default removal semantics."""
    patch_send_handler.post_result = {"remove_members_result": "success"}
    result = await QQBotGroupMembersService(make_plugin()).batch_remove_members("group", ["member"])
    assert result["success"] is True
    assert patch_send_handler.posts[0][2] == {"member_openids": ["member"]}
    assert len(patch_send_handler.posts) == 1


async def test_old_combined_remove_option_fails_without_partial_side_effect(
    patch_send_handler: FakeSendHandler,
) -> None:
    """Do not silently remove only or synthesize a second blacklist operation."""
    result = await QQBotGroupMembersService(make_plugin()).batch_remove_members(
        "group", ["member"], add_to_member_blacklist=True,
    )
    assert result["success"] is False
    assert "update_blacklist" in result["error"]
    assert patch_send_handler.posts == []


async def test_remove_and_blacklist_remain_independently_invoked_operations(
    patch_send_handler: FakeSendHandler,
) -> None:
    """A caller decides each step separately; removal never schedules blacklist."""
    service = QQBotGroupMembersService(make_plugin())
    patch_send_handler.post_result = {"remove_members_result": "success"}
    assert (await service.batch_remove_members("group", ["member"]))["success"]
    assert len(patch_send_handler.posts) == 1
    patch_send_handler.post_result = {"fail_openids": []}
    assert (await service.update_blacklist("group", "add", ["member"]))["success"]
    assert len(patch_send_handler.posts) == 2
    assert patch_send_handler.posts[1][0].endswith("/member_blacklist")
    assert patch_send_handler.posts[1][2] == {"op": "add", "member_openids": ["member"]}


def test_removal_tool_does_not_advertise_legacy_blacklist_option() -> None:
    """The deprecated compatibility switch is not a Tool argument."""
    from ..tools.group_members import QQBatchRemoveGroupMembersTool

    assert "add_to_member_blacklist" not in inspect.signature(QQBatchRemoveGroupMembersTool.execute).parameters
