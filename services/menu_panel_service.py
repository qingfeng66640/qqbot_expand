"""QQ 自定义菜单与指令面板管理 Service。"""
from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment, failure
from ..src.menu_panel_policy import normalize_menu, normalize_panel, normalize_panel_create, normalize_targets

__all__ = ["QQBotMenuPanelService"]


class QQBotMenuPanelService(BaseService):
    """供受信插件调用的 QQ 菜单与指令面板管理能力。"""

    service_name = "qqbot_menu_panel"
    service_description = "查询和管理 QQ 自定义菜单与指令面板"
    version = "0.6.0"

    def _service_enabled(self) -> bool:
        """检查菜单面板 Service 是否已显式启用。"""
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        return bool(getattr(features, "enable_menu_panel_service", False))

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> dict[str, Any]:
        """检查 Service 开关后调用统一请求出口。"""
        if not self._service_enabled():
            return failure("菜单与指令面板 Service 未启用")
        return await api_request(self.plugin, method, path, body, query=query)

    async def get_menu(self) -> dict[str, Any]:
        """查询 Bot 全局自定义菜单。"""
        return await self._request("GET", "/v2/menu")

    async def update_menu(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """完整覆盖 Bot 全局自定义菜单。"""
        error, body = normalize_menu(items)
        return failure(error) if error else await self._request("PUT", "/v2/menu", body)

    async def list_panels(self, scope: str, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        """分页查询指定场景的生效面板。"""
        if scope not in {"c2c", "group", "channel", "dm"}:
            return failure("scope 只能是 c2c、group、channel 或 dm")
        if not isinstance(cursor, str):
            return failure("cursor 必须是字符串")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            return failure("limit 必须在 1~50 之间")
        return await self._request("GET", "/v2/panels", query={"scope": scope, "cursor": cursor, "limit": limit})

    async def create_panel(self, scope: str, target_type: str, panel: dict[str, Any], *, user_openids: list[str] | None = None, group_openids: list[str] | None = None) -> dict[str, Any]:
        """创建一个指令面板。"""
        error, body = normalize_panel_create(scope, target_type, panel, user_openids, group_openids)
        return failure(error) if error else await self._request("POST", "/v2/panels", body)

    async def get_panel(self, panel_id: str) -> dict[str, Any]:
        """查询单个指令面板详情。"""
        error, encoded = encode_path_segment(panel_id, "panel_id")
        return failure(error) if error else await self._request("GET", f"/v2/panels/{encoded}")

    async def update_panel(self, panel_id: str, panel: dict[str, Any]) -> dict[str, Any]:
        """覆盖指定面板的内容和备注。"""
        error, encoded = encode_path_segment(panel_id, "panel_id")
        panel_error, normalized = normalize_panel(panel)
        if error or panel_error:
            return failure(error or panel_error or "panel 不合法")
        return await self._request("PUT", f"/v2/panels/{encoded}", {"panel": normalized})

    async def delete_panel(self, panel_id: str) -> dict[str, Any]:
        """删除指定指令面板。"""
        error, encoded = encode_path_segment(panel_id, "panel_id")
        return failure(error) if error else await self._request("DELETE", f"/v2/panels/{encoded}")

    async def update_panel_targets(self, panel_id: str, op: str, *, user_openids: list[str] | None = None, group_openids: list[str] | None = None) -> dict[str, Any]:
        """增加或删除指定面板的用户/群关联对象。"""
        error, encoded = encode_path_segment(panel_id, "panel_id")
        target_error, body = normalize_targets(op, user_openids, group_openids)
        if error or target_error:
            return failure(error or target_error or "关联对象不合法")
        return await self._request("PUT", f"/v2/panels/{encoded}/target", body)
