"""QQ 菜单与指令面板管理 Tool。"""
from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.base import BaseTool

from ..services.menu_panel_service import QQBotMenuPanelService
from ..src.targets import resolve_target
from .schema_types import (
    MenuItemInput,
    PanelInput,
    PanelScope,
    PanelTargetOp,
)

__all__ = [
    "ALL_MENU_PANEL_TOOLS",
    "QQCreatePanelTool",
    "QQDeletePanelTool",
    "QQGetMenuPanelTool",
    "QQListPanelsTool",
    "QQUpdateMenuTool",
    "QQUpdatePanelTargetsTool",
    "QQUpdatePanelTool",
]


def _features(tool: BaseTool) -> Any:
    """读取 Tool 所属插件的功能配置。"""
    return getattr(getattr(tool.plugin, "config", None), "features", None)


def _configured_strings(features: Any, name: str) -> set[str]:
    """将配置中的字符串列表归一化为集合。"""
    return {
        item.strip()
        for item in getattr(features, name, []) or []
        if isinstance(item, str) and item.strip()
    }


def _authorized(tool: BaseTool, *, panel_id: str = "") -> tuple[str | None, Any]:
    """验证总开关、操作者、当前群和可选面板白名单。"""
    features = _features(tool)
    if not bool(getattr(features, "enable_tools", True)):
        return "QQ LLM 工具当前未启用", None
    if not bool(getattr(features, "enable_menu_panel_tools", False)):
        return "菜单面板 LLM 工具当前未启用", None
    if not bool(getattr(features, "enable_menu_panel_service", False)):
        return "菜单面板 Service 当前未启用", None
    operator = str(getattr(tool.trigger_message, "sender_id", "") or "").strip()
    if not operator or operator not in _configured_strings(
        features, "menu_panel_allowed_operator_openids"
    ):
        return "当前操作者不在菜单面板白名单中", None
    target = resolve_target(tool.trigger_message)
    if target is None:
        return "无法从当前 QQ 会话识别操作目标", None
    if target.target_type == "group" and target.target_id not in _configured_strings(
        features, "menu_panel_allowed_group_openids"
    ):
        return "当前群不在菜单面板白名单中", None
    if panel_id and panel_id not in _configured_strings(
        features, "menu_panel_allowed_panel_ids"
    ):
        return "当前面板不在菜单面板白名单中", None
    return None, target


def _profile(features: Any, name: str) -> tuple[str | None, dict[str, Any]]:
    """按名称读取受信 profile，并校验其中的群目标白名单。"""
    if not isinstance(name, str) or not name.strip():
        return "profile_name 不能为空", {}
    for profile in getattr(features, "menu_panel_profiles", []) or []:
        if not isinstance(profile, dict) or profile.get("name") != name.strip():
            continue
        group_openids = {
            item.strip()
            for item in profile.get("group_openids", []) or []
            if isinstance(item, str) and item.strip()
        }
        allowed_groups = _configured_strings(
            features, "menu_panel_allowed_group_openids"
        )
        if not group_openids.issubset(allowed_groups):
            return "profile 包含未授权的群目标", {}
        return None, profile
    return "未找到已授权的菜单面板 profile", {}


def _result(result: dict[str, Any]) -> tuple[bool, str | dict]:
    """将 Service 返回值转换为 Tool 返回约定。"""
    return (True, result["data"]) if result["success"] else (False, result["error"])


class _MenuPanelTool(BaseTool):
    """菜单面板 Tool 的通用授权门禁。"""

    associated_platforms = ["qq"]


class QQGetMenuPanelTool(_MenuPanelTool):
    """查询自定义菜单或指定面板。"""

    tool_name = "qq_get_menu_panel"
    tool_description = "查询 QQ 自定义菜单或已授权的指令面板详情。"

    async def execute(
        self,
        panel_id: Annotated[str, "已授权的面板 ID；留空查询全局菜单"] = "",
    ) -> tuple[bool, str | dict]:
        """查询全局菜单或面板详情。"""
        error, _ = _authorized(self, panel_id=panel_id)
        if error:
            return False, error
        service = QQBotMenuPanelService(self.plugin)
        return _result(
            await (service.get_panel(panel_id) if panel_id else service.get_menu())
        )


class QQListPanelsTool(_MenuPanelTool):
    """查询指定场景的指令面板列表。"""

    tool_name = "qq_list_panels"
    tool_description = "查询 QQ 指令面板列表；只读，不修改配置。"

    async def execute(
        self,
        scope: Annotated[PanelScope, "面板场景：c2c、group、channel 或 dm"],
        cursor: str = "",
        limit: int = 20,
    ) -> tuple[bool, str | dict]:
        """查询指定场景的面板列表。"""
        error, _ = _authorized(self)
        if error:
            return False, error
        return _result(
            await QQBotMenuPanelService(self.plugin).list_panels(scope, cursor, limit)
        )


class QQUpdateMenuTool(_MenuPanelTool):
    """覆盖全局自定义菜单。"""

    tool_name = "qq_update_menu"
    tool_description = "覆盖 Bot 对所有单聊用户生效的 QQ 自定义菜单，必须 confirm=true。"

    async def execute(
        self,
        items: Annotated[
            list[MenuItemInput],
            "完整菜单项列表；根据 type 使用 switch、send_message、link 或 sub_menu_items",
        ],
        confirm: Annotated[bool, "确认覆盖全局菜单；必须为 true"],
    ) -> tuple[bool, str | dict]:
        """经显式确认后覆盖全局菜单。"""
        features = _features(self)
        if not bool(getattr(features, "allow_global_menu_write", False)):
            return False, "全局菜单写入未启用"
        if confirm is not True:
            return False, "必须将 confirm 设为 true"
        error, _ = _authorized(self)
        if error:
            return False, error
        return _result(await QQBotMenuPanelService(self.plugin).update_menu(items))


class QQCreatePanelTool(_MenuPanelTool):
    """为当前会话或受信 profile 创建指令面板。"""

    tool_name = "qq_create_panel"
    tool_description = "为当前 QQ 会话创建指令面板；跨目标或批量创建可选受信 profile，必须 confirm=true。"

    async def execute(
        self,
        panel: Annotated[
            PanelInput,
            "指令面板内容；项目使用 name/desc/type/only_admin/link，不使用 label/command/url",
        ],
        confirm: Annotated[bool, "确认创建；必须为 true"],
        profile_name: Annotated[
            str, "可选的受信 profile 名称；留空时使用当前群或当前私聊用户"
        ] = "",
    ) -> tuple[bool, str | dict]:
        """为当前会话或 profile 内固定的目标创建面板。"""
        features = _features(self)
        if not bool(getattr(features, "allow_panel_create", False)):
            return False, "创建指令面板未启用"
        if confirm is not True:
            return False, "必须将 confirm 设为 true"
        error, target = _authorized(self)
        if error:
            return False, error

        if isinstance(profile_name, str) and profile_name.strip():
            profile_error, profile = _profile(features, profile_name)
            if profile_error:
                return False, profile_error
            scope = profile.get("scope")
            target_type = profile.get("target_type", "all")
            user_openids = profile.get("user_openids")
            group_openids = profile.get("group_openids")
        elif target.target_type == "group":
            scope = "group"
            target_type = "specific"
            user_openids = None
            group_openids = [target.target_id]
        else:
            scope = "c2c"
            target_type = "specific"
            user_openids = [target.target_id]
            group_openids = None

        return _result(
            await QQBotMenuPanelService(self.plugin).create_panel(
                scope,
                target_type,
                panel,
                user_openids=user_openids,
                group_openids=group_openids,
            )
        )


class QQUpdatePanelTool(_MenuPanelTool):
    """更新已授权指令面板。"""

    tool_name = "qq_update_panel"
    tool_description = "更新白名单中的 QQ 指令面板，不改变其关联目标，必须 confirm=true。"

    async def execute(
        self,
        panel_id: Annotated[str, "配置白名单内的面板 ID"],
        panel: Annotated[
            PanelInput,
            "新的面板内容；项目使用 name/desc/type/only_admin/link，不使用 label/command/url",
        ],
        confirm: Annotated[bool, "确认覆盖面板内容；必须为 true"],
    ) -> tuple[bool, str | dict]:
        """经显式确认后更新白名单面板。"""
        if confirm is not True:
            return False, "必须将 confirm 设为 true"
        error, _ = _authorized(self, panel_id=panel_id)
        if error:
            return False, error
        return _result(
            await QQBotMenuPanelService(self.plugin).update_panel(panel_id, panel)
        )


class QQDeletePanelTool(_MenuPanelTool):
    """删除已授权指令面板。"""

    tool_name = "qq_delete_panel"
    tool_description = "删除白名单中的 QQ 指令面板，必须启用删除开关并 confirm=true。"

    async def execute(
        self, panel_id: str, confirm: bool
    ) -> tuple[bool, str | dict]:
        """经独立开关和显式确认后删除面板。"""
        features = _features(self)
        if not bool(getattr(features, "allow_panel_delete", False)):
            return False, "面板删除未启用"
        if confirm is not True:
            return False, "必须将 confirm 设为 true"
        error, _ = _authorized(self, panel_id=panel_id)
        if error:
            return False, error
        return _result(await QQBotMenuPanelService(self.plugin).delete_panel(panel_id))


class QQUpdatePanelTargetsTool(_MenuPanelTool):
    """按受信 profile 更新面板关联对象。"""

    tool_name = "qq_update_panel_targets"
    tool_description = "按已配置 profile 增删面板关联用户或群，目标不能由 LLM 指定，必须 confirm=true。"

    async def execute(
        self,
        profile_name: Annotated[str, "包含 panel_id 和固定目标的受信 profile 名称"],
        op: Annotated[PanelTargetOp, "关联对象操作：add 添加或 del 删除"],
        confirm: Annotated[bool, "确认修改关联对象；必须为 true"],
    ) -> tuple[bool, str | dict]:
        """使用 profile 中固定的 panel_id 和目标集合更新关联。"""
        if confirm is not True:
            return False, "必须将 confirm 设为 true"
        features = _features(self)
        profile_error, profile = _profile(features, profile_name)
        if profile_error:
            return False, profile_error
        if profile.get("allow_target_update") is not True:
            return False, "该 profile 未授权修改关联对象"
        panel_id = str(profile.get("panel_id", "") or "").strip()
        error, _ = _authorized(self, panel_id=panel_id)
        if error:
            return False, error
        return _result(
            await QQBotMenuPanelService(self.plugin).update_panel_targets(
                panel_id,
                op,
                user_openids=profile.get("user_openids"),
                group_openids=profile.get("group_openids"),
            )
        )


ALL_MENU_PANEL_TOOLS: list[type] = [
    QQGetMenuPanelTool,
    QQListPanelsTool,
    QQUpdateMenuTool,
    QQCreatePanelTool,
    QQUpdatePanelTool,
    QQDeletePanelTool,
    QQUpdatePanelTargetsTool,
]
