"""受控的 QQ 群成员查询、黑名单管理与批量移除 Tool。"""
from __future__ import annotations

from typing import Annotated, Literal

from src.app.plugin_system.base import BaseTool

from ..services.group_members_service import QQBotGroupMembersService
from ..src.constants import TARGET_TYPE_GROUP
from ..src.targets import resolve_target

__all__ = [
    "QQBatchRemoveGroupMembersTool",
    "QQManageGroupBlacklistTool",
    "QQQueryGroupMembersTool",
]

BlacklistOp = Literal["add", "del"]


class _GroupMembersTool(BaseTool):
    """从当前 QQ 群会话安全推导群成员管理目标。"""

    associated_platforms = ["qq"]

    def _allowed_group_openid(self) -> tuple[str | None, str]:
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        if not bool(getattr(features, "enable_tools", True)):
            return "QQ LLM 工具当前未启用", ""
        if not bool(getattr(features, "enable_group_members_tools", False)):
            return "群成员管理 LLM 工具当前未启用", ""
        if not bool(getattr(features, "enable_group_members_service", False)):
            return "群成员管理 Service 当前未启用", ""
        target = resolve_target(self.trigger_message)
        if target is None or target.target_type != TARGET_TYPE_GROUP:
            return "群成员管理工具只能在 QQ 群会话中使用", ""
        allowed = {
            str(item).strip()
            for item in getattr(features, "group_members_allowed_group_openids", []) or []
            if str(item).strip()
        }
        if target.target_id not in allowed:
            return "当前群不在群成员管理工具白名单中", ""
        return None, target.target_id


class QQQueryGroupMembersTool(_GroupMembersTool):
    """查询当前群的成员列表、成员详情或黑名单。"""

    tool_name = "qq_query_group_members"
    tool_description = (
        "查询当前 QQ 群的成员列表、单个成员详情或群黑名单。只读操作，"
        "不能指定其他群。相关接口需机器人获得平台白名单权限。"
    )

    async def execute(
        self,
        query: Annotated[Literal["members", "member", "blacklist"], "查询类型"],
        member_openid: Annotated[str, "查询 member 时必填的目标成员 openid"] = "",
        cursor: Annotated[str, "分页游标，首次查询留空"] = "",
        limit: Annotated[int, "黑名单单页数量 1~100，默认 20；成员列表由 QQ 固定每页 30"] = 20,
    ) -> tuple[bool, str | dict]:
        """按类型查询当前群成员/黑名单。"""
        error, group_openid = self._allowed_group_openid()
        if error:
            return False, error
        service = QQBotGroupMembersService(self.plugin)
        if query == "member":
            if not isinstance(member_openid, str) or not member_openid.strip():
                return False, "查询 member 时 member_openid 不能为空"
            result = await service.get_member(group_openid, member_openid)
        elif query == "blacklist":
            result = await service.list_blacklist(group_openid, cursor=cursor, limit=limit)
        elif query == "members":
            result = await service.list_members(group_openid, cursor=cursor)
        else:
            return False, "query 只能是 members、member 或 blacklist"
        return (True, result["data"]) if result["success"] else (False, result["error"])


class QQManageGroupBlacklistTool(_GroupMembersTool):
    """加入或移出当前群的群黑名单。"""

    tool_name = "qq_manage_group_blacklist"
    tool_description = (
        "把用户加入或移出当前 QQ 群的黑名单。加入黑名单要求目标已不在群中；"
        "单次最多 20 人。属高权限操作，必须显式确认。"
    )

    async def execute(
        self,
        op: Annotated[BlacklistOp, "操作类型：add 加入黑名单，del 移出黑名单"],
        member_openids: Annotated[list[str], "目标成员 openid 列表，单次最多 20 个"],
        confirm: Annotated[bool, "必须显式传 true 才会执行"] = False,
    ) -> tuple[bool, str | dict]:
        """更新当前群黑名单。"""
        error, group_openid = self._allowed_group_openid()
        if error:
            return False, error
        if confirm is not True:
            return False, "黑名单操作需显式传 confirm=true"
        result = await QQBotGroupMembersService(self.plugin).update_blacklist(
            group_openid, op, member_openids
        )
        return (True, result["data"]) if result["success"] else (False, result["error"])


class QQBatchRemoveGroupMembersTool(_GroupMembersTool):
    """仅批量移除当前群成员；群黑名单是另一个独立操作。"""

    tool_name = "qq_batch_remove_group_members"
    tool_description = (
        "仅把一批成员移出当前 QQ 群（单次最多 20 人），不会自动加入群黑名单。"
        "属高权限操作，必须显式确认。"
    )

    async def execute(
        self,
        member_openids: Annotated[list[str], "需要移除的成员 openid 列表，单次最多 20 个"],
        confirm: Annotated[bool, "必须显式传 true 才会执行"] = False,
    ) -> tuple[bool, str | dict]:
        """批量移除当前群成员。"""
        error, group_openid = self._allowed_group_openid()
        if error:
            return False, error
        if confirm is not True:
            return False, "批量移除成员需显式传 confirm=true"
        result = await QQBotGroupMembersService(self.plugin).batch_remove_members(
            group_openid, member_openids
        )
        return (True, result["data"]) if result["success"] else (False, result["error"])
