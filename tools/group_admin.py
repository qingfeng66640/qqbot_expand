"""受控的 QQ 群入群审批与成员禁言 Tool。"""
from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.base import BaseTool

from ..services.group_admin_service import QQBotGroupAdminService
from ..src.constants import TARGET_TYPE_GROUP
from ..src.targets import resolve_target

__all__ = ["QQReviewGroupJoinRequestTool", "QQSetGroupMemberMuteTool"]


class _GroupAdminTool(BaseTool):
    """从当前 QQ 群会话安全推导群管理目标。"""

    associated_platforms = ["qq"]

    def _allowed_group_openid(self) -> tuple[str | None, str]:
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        if not bool(getattr(features, "enable_tools", True)):
            return "QQ LLM 工具当前未启用", ""
        if not bool(getattr(features, "enable_group_admin_tools", False)):
            return "群管理 LLM 工具当前未启用", ""
        if not bool(getattr(features, "enable_group_admin_service", False)):
            return "群管理 Service 当前未启用", ""
        target = resolve_target(self.trigger_message)
        if target is None or target.target_type != TARGET_TYPE_GROUP:
            return "群管理工具只能在 QQ 群会话中使用", ""
        allowed = {
            str(item).strip()
            for item in getattr(features, "group_admin_allowed_group_openids", []) or []
            if str(item).strip()
        }
        if target.target_id not in allowed:
            return "当前群不在群管理工具白名单中", ""
        return None, target.target_id


class QQReviewGroupJoinRequestTool(_GroupAdminTool):
    """审批当前群的单条入群申请。"""

    tool_name = "qq_review_group_join_request"
    tool_description = "审批当前 QQ 群的一条入群申请。只可批准或拒绝当前群的申请，不能指定其他群。"

    async def execute(
        self,
        member_openid: Annotated[str, "申请人的 member_openid"],
        op: Annotated[str, "审批动作：approve 通过，decline 拒绝"],
        join_request_id: Annotated[str, "入群申请 ID；建议从受信插件提供的申请事件中取得"] = "",
        reject_reason: Annotated[str, "拒绝原因；仅 decline 时可填"] = "",
        add_to_member_blacklist: Annotated[bool, "拒绝时是否加入群黑名单"] = False,
    ) -> tuple[bool, str | dict]:
        """审批当前群的入群申请。"""
        error, group_openid = self._allowed_group_openid()
        if error:
            return False, error
        if not isinstance(join_request_id, str) or not join_request_id.strip():
            return False, "join_request_id 不能为空"
        result = await QQBotGroupAdminService(self.plugin).approve_join_request(
            group_openid,
            member_openid,
            op,
            join_request_id=join_request_id,
            reject_reason=reject_reason,
            add_to_member_blacklist=add_to_member_blacklist,
        )
        return (True, result["data"]) if result["success"] else (False, result["error"])


class QQSetGroupMemberMuteTool(_GroupAdminTool):
    """设置当前群成员的禁言状态。"""

    tool_name = "qq_set_group_member_mute"
    tool_description = "设置当前 QQ 群普通成员的禁言状态。目标群由当前会话确定，不能指定其他群。"

    async def execute(
        self,
        members: Annotated[
            list[dict[str, str]],
            '禁言操作列表（最多 10 项），每项包含 op=add/update/del、member_openid，add/update 可含 mute_expire_at（RFC3339）',
        ],
    ) -> tuple[bool, str | dict]:
        """更新当前群成员禁言状态。"""
        error, group_openid = self._allowed_group_openid()
        if error:
            return False, error
        result = await QQBotGroupAdminService(self.plugin).set_member_mute_states(
            group_openid, members
        )
        return (True, result["data"]) if result["success"] else (False, result["error"])
