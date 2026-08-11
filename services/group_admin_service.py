"""QQ 群管理开放 API Service。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure

logger = get_logger("qqbot_expand.group_admin")

__all__ = ["QQBotGroupAdminService"]


def _nonempty(value: str, name: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"{name} 不能为空"
    return None


def _string_list(value: list[str], name: str, maximum: int) -> tuple[str | None, list[str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        return f"{name} 数量必须在 1~{maximum} 之间", []
    normalized = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(normalized) != len(value):
        return f"{name} 必须全部为非空字符串", []
    return None, normalized


def _path_segment(value: str, name: str) -> tuple[str | None, str]:
    """校验并编码 QQ API 路径参数。"""
    return encode_path_segment(value, name)


def _rfc3339(value: Any, name: str) -> str | None:
    """校验可选 RFC3339 时间戳。"""
    if not isinstance(value, str) or not value.strip():
        return f"{name} 必须是非空 RFC3339 时间戳"
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return f"{name} 必须是 RFC3339 时间戳"
    return None


def _mute_member(member: Any) -> tuple[str | None, dict[str, Any]]:
    """校验并构造单个群禁言操作。"""
    if not isinstance(member, dict):
        return "members 每项必须是对象", {}
    op = member.get("op")
    member_openid = member.get("member_openid")
    if op not in {"add", "update", "del"}:
        return "members 每项 op 必须是 add、update 或 del", {}
    error = _nonempty(member_openid, "member_openid")
    if error:
        return error, {}
    result: dict[str, Any] = {"op": op, "member_openid": member_openid.strip()}
    if op in {"add", "update"} and "mute_expire_at" in member:
        timestamp_error = _rfc3339(member["mute_expire_at"], "mute_expire_at")
        if timestamp_error:
            return timestamp_error, {}
        result["mute_expire_at"] = member["mute_expire_at"].strip()
    return None, result


class QQBotGroupAdminService(BaseService):
    """供受信插件调用的 QQ 群管理能力。"""

    service_name = "qqbot_group_admin"
    service_description = "管理 QQ 群入群审批策略、入群申请与成员禁言"
    version = "0.2.0"

    def _service_enabled(self) -> bool:
        """检查高权限群管理 Service 是否已显式启用。"""
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        return bool(getattr(features, "enable_group_admin_service", False))

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._service_enabled():
            return failure("群管理 Service 未启用")
        return await api_request(self.plugin, method, path, body, query=query)

    async def register_join_request_callback(
        self, name: str, callback: Any, *, replace: bool = False
    ) -> bool:
        """注册受信入群申请事件回调，不执行自动审批。"""
        runtime = getattr(self.plugin, "join_request_runtime", None)
        return bool(runtime and await runtime.register(name, callback, replace=replace))

    async def unregister_join_request_callback(self, name: str) -> bool:
        """注销受信入群申请事件回调。"""
        runtime = getattr(self.plugin, "join_request_runtime", None)
        return bool(runtime and await runtime.unregister(name))

    async def list_join_approval_strategies(
        self, cursor: str = "", limit: int = 20
    ) -> dict[str, Any]:
        """分页查询生效中的入群自动审批策略。"""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            return failure("limit 必须在 1~100 之间")
        return await self._request(
            "GET",
            "/v2/groups/join_approval_strategy",
            query={"cursor": cursor, "limit": limit},
        )

    async def create_join_approval_strategy(self, *, group_openids: list[str] | None = None, group_ids: list[str] | None = None, is_enable: str = "on", expire_at: str = "", remark: str = "") -> dict[str, Any]:
        if bool(group_openids) == bool(group_ids):
            return failure("group_openids 与 group_ids 必须且只能提供一个")
        groups = group_openids if group_openids else group_ids
        key = "group_openids" if group_openids else "group_ids"
        error, normalized = _string_list(groups or [], key, 100)
        if error:
            return failure(error)
        if is_enable not in {"on", "off"}:
            return failure("is_enable 只能是 on 或 off")
        if len(remark) > 255:
            return failure("remark 最多 255 个字符")
        body: dict[str, Any] = {key: normalized, "is_enable": is_enable}
        if expire_at:
            body["expire_at"] = expire_at
        if remark:
            body["remark"] = remark
        return await self._request("POST", "/v2/groups/join_approval_strategy", body)

    async def delete_join_approval_strategy(self, strategy_id: str) -> dict[str, Any]:
        error, strategy_id = _path_segment(strategy_id, "strategy_id")
        return failure(error) if error else await self._request("DELETE", f"/v2/groups/join_approval_strategy/{strategy_id}")

    async def update_join_approval_strategy(self, strategy_id: str, *, is_enable: str = "", expire_at: str = "", remark: str = "", group_action: dict[str, Any] | None = None) -> dict[str, Any]:
        error, strategy_id = _path_segment(strategy_id, "strategy_id")
        if error:
            return failure(error)
        if not any((is_enable, expire_at, remark, group_action)):
            return failure("至少提供一个可更新字段")
        if is_enable and is_enable not in {"on", "off"}:
            return failure("is_enable 只能是 on 或 off")
        if len(remark) > 255:
            return failure("remark 最多 255 个字符")
        body: dict[str, Any] = {}
        if is_enable:
            body["is_enable"] = is_enable
        if expire_at:
            body["expire_at"] = expire_at
        if remark:
            body["remark"] = remark
        if group_action:
            if not isinstance(group_action, dict) or group_action.get("op") not in {"add", "del"}:
                return failure("group_action 必须包含 add 或 del 操作")
            has_openids = "group_openids" in group_action
            has_ids = "group_ids" in group_action
            if has_openids == has_ids:
                return failure("group_action 必须且只能提供一种群标识列表")
            key = "group_openids" if has_openids else "group_ids"
            list_error, groups = _string_list(group_action[key], key, 100)
            if list_error:
                return failure(list_error)
            body["group_action"] = {"op": group_action["op"], key: groups}
        return await self._request("PATCH", f"/v2/groups/join_approval_strategy/{strategy_id}", body)

    async def execute_join_approval_strategy(self, strategy_id: str) -> dict[str, Any]:
        error, strategy_id = _path_segment(strategy_id, "strategy_id")
        return failure(error) if error else await self._request("POST", f"/v2/groups/join_approval_strategy/{strategy_id}/execute", {})

    async def update_strategy_whitelist_users(self, strategy_id: str, op: str, whitelist_users: list[str]) -> dict[str, Any]:
        error, strategy_id = _path_segment(strategy_id, "strategy_id")
        list_error, users = _string_list(whitelist_users, "whitelist_users", 10000)
        if error or list_error or op not in {"add", "del"}:
            return failure(error or list_error or "op 只能是 add 或 del")
        return await self._request("POST", f"/v2/groups/join_approval_strategy/{strategy_id}/whitelist_users", {"op": op, "whitelist_users": users})

    async def list_join_requests(self, group_openid: str, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        error, group_openid = _path_segment(group_openid, "group_openid")
        if error or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            return failure(error or "limit 必须在 1~100 之间")
        return await self._request("GET", f"/v2/groups/{group_openid}/join_request_list", query={"cursor": cursor, "limit": limit})

    async def approve_join_request(self, group_openid: str, member_openid: str, op: str, *, join_request_id: str = "", reject_reason: str = "", add_to_member_blacklist: bool = False) -> dict[str, Any]:
        error, group_openid = _path_segment(group_openid, "group_openid")
        member_error, member_openid = _path_segment(member_openid, "member_openid")
        if error or member_error or op not in {"approve", "decline"} or not isinstance(add_to_member_blacklist, bool):
            return failure(error or member_error or "op 只能是 approve 或 decline")
        if op == "approve" and (reject_reason or add_to_member_blacklist):
            return failure("approve 不支持拒绝理由或加入黑名单")
        body: dict[str, Any] = {"op": op}
        if join_request_id:
            body["join_request_id"] = join_request_id
        if reject_reason:
            body["reject_reason"] = reject_reason
        if add_to_member_blacklist:
            body["add_to_member_blacklist"] = True
        logger.info(f"提交入群审批: group={group_openid[:8]} op={op}")
        return await self._request("POST", f"/v2/groups/{group_openid}/approval_join_request/{member_openid}", body)

    async def get_restrict_chat_setting(self, group_openid: str) -> dict[str, Any]:
        error, group_openid = _path_segment(group_openid, "group_openid")
        return failure(error) if error else await self._request("GET", f"/v2/groups/{group_openid}/restrict_chat_setting")

    async def set_member_mute_states(self, group_openid: str, members: list[dict[str, Any]]) -> dict[str, Any]:
        error, group_openid = _path_segment(group_openid, "group_openid")
        if error or not isinstance(members, list) or not 1 <= len(members) <= 10:
            return failure(error or "members 数量必须在 1~10 之间")
        normalized_members: list[dict[str, Any]] = []
        for member in members:
            member_error, normalized = _mute_member(member)
            if member_error:
                return failure(member_error)
            normalized_members.append(normalized)
        logger.info(f"设置群禁言: group={group_openid[:8]} count={len(normalized_members)}")
        return await self._request("POST", f"/v2/groups/{group_openid}/restrict_chat_setting", {"members": normalized_members})
