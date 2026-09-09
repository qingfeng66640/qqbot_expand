"""QQ 群成员与黑名单管理 Service。

覆盖 QQ OpenAPI v2 的群成员列表/详情、群黑名单查询与操作、批量移除成员
五个端点。相关接口目前处于平台"内邀接入"阶段：机器人未获白名单时 QQ 返回
错误码 11253，本插件按普通 API 权限错误脱敏透传。

批量写操作（黑名单增删、移除成员）属高权限敏感能力，与群管理 Service 相同：
默认关闭、仅供受信插件或管理员工作流调用，不自动注册为 LLM Tool。
"""
from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure

__all__ = ["QQBotGroupMembersService"]

_BLACKLIST_OPS: frozenset[str] = frozenset({"add", "del"})
_BLACKLIST_MAX_OPENIDS = 20
_BLACKLIST_MAX_LIMIT = 100


def _string_list(values: Any, field: str, max_length: int) -> tuple[str, list[str]]:
    """校验非空字符串列表，返回 (错误, 规范化列表)。"""
    if not isinstance(values, list) or not 1 <= len(values) <= max_length:
        return f"{field} 必须是 1~{max_length} 个元素的列表", []
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            return f"{field} 每项必须是非空字符串", []
        normalized.append(value.strip())
    return "", normalized


class QQBotGroupMembersService(BaseService):
    """查询 QQ 群成员与黑名单、执行黑名单增删和批量移除成员。"""

    service_name = "qqbot_group_members"
    service_description = "查询 QQ 群成员列表/详情、群黑名单，并执行黑名单增删与批量移除成员"
    version = "0.6.0"

    def _service_enabled(self) -> bool:
        """检查群成员 Service 是否已显式启用。"""
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        return bool(getattr(features, "enable_group_members_service", False))

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """检查 Service 开关后调用统一请求出口。"""
        if not self._service_enabled():
            return failure("群成员管理 Service 未启用")
        return await api_request(self.plugin, method, path, body, query=query)

    async def _group_path(self, group_openid: str, suffix: str) -> tuple[str, str]:
        """校验群 OpenID 并拼出路径前缀。"""
        error, encoded = encode_path_segment(group_openid, "group_openid")
        if error:
            return error, ""
        return "", f"/v2/groups/{encoded}/{suffix}"

    async def list_members(self, group_openid: str, cursor: str = "") -> dict[str, Any]:
        """分页查询群成员列表，每页最多 30 条（60 QPM）。"""
        if not isinstance(cursor, str):
            return failure("cursor 必须是字符串")
        error, path = await self._group_path(group_openid, "members")
        if error:
            return failure(error)
        return await self._request("GET", path, query={"cursor": cursor})

    async def get_member(self, group_openid: str, member_openid: str) -> dict[str, Any]:
        """查询单个群成员详情（30 QPM）。"""
        group_error, path = await self._group_path(group_openid, "members")
        if group_error:
            return failure(group_error)
        member_error, encoded_member = encode_path_segment(member_openid, "member_openid")
        if member_error:
            return failure(member_error)
        return await self._request("GET", f"{path}/{encoded_member}")

    async def list_blacklist(self, group_openid: str, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        """分页查询群黑名单，limit 1~100，默认 20（30 QPM）。"""
        if not isinstance(cursor, str):
            return failure("cursor 必须是字符串")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _BLACKLIST_MAX_LIMIT:
            return failure("limit 必须在 1~100 之间")
        error, path = await self._group_path(group_openid, "member_blacklist")
        if error:
            return failure(error)
        return await self._request("GET", path, query={"cursor": cursor, "limit": limit})

    async def update_blacklist(self, group_openid: str, op: str, member_openids: list[str]) -> dict[str, Any]:
        """加入或移出群黑名单，单次最多 20 人（60 QPM）。

        QQ 侧要求：目标成员仍在群中时无法加入黑名单，需先移除。
        """
        if op not in _BLACKLIST_OPS:
            return failure("op 只能是 add 或 del")
        list_error, openids = _string_list(member_openids, "member_openids", _BLACKLIST_MAX_OPENIDS)
        if list_error:
            return failure(list_error)
        error, path = await self._group_path(group_openid, "member_blacklist")
        if error:
            return failure(error)
        return await self._request("POST", path, {"op": op, "member_openids": openids})

    async def batch_remove_members(
        self, group_openid: str, member_openids: list[str], *, add_to_member_blacklist: bool = False
    ) -> dict[str, Any]:
        """批量移除群成员，单次最多 20 人，可选同时拉黑（30 QPM）。"""
        list_error, openids = _string_list(member_openids, "member_openids", _BLACKLIST_MAX_OPENIDS)
        if list_error:
            return failure(list_error)
        if not isinstance(add_to_member_blacklist, bool):
            return failure("add_to_member_blacklist 必须是布尔值")
        error, path = await self._group_path(group_openid, "batch_remove_members")
        if error:
            return failure(error)
        return await self._request(
            "POST",
            path,
            {"member_openids": openids, "add_to_member_blacklist": add_to_member_blacklist},
        )
