"""QQ 自定义菜单与指令面板的输入校验。"""
from __future__ import annotations

from typing import Any
from unicodedata import east_asian_width
from urllib.parse import urlsplit

_SCOPES = {"c2c", "group", "channel", "dm"}
_TARGET_TYPES = {"all", "specific"}


def _display_length(value: str) -> int:
    """计算展示字符数：中文及全角宽字符计 2，其余字符计 1。"""
    return sum(2 if east_asian_width(char) in {"W", "F"} else 1 for char in value)


def _text(
    value: Any, name: str, maximum: int, *, display: bool = False
) -> tuple[str | None, str]:
    """校验非空文本，仅展示字段使用中文双字符计数。"""
    if not isinstance(value, str) or not value.strip():
        return f"{name} 不能为空", ""
    normalized = value.strip()
    length = _display_length(normalized) if display else len(normalized)
    if length > maximum:
        unit = "展示字符（中文/全角计 2）" if display else "字符"
        return f"{name} 最多 {maximum} 个{unit}，当前 {length}", ""
    return None, normalized


def _https_url(value: Any, name: str) -> tuple[str | None, str]:
    """校验不含用户信息的 HTTPS URL。"""
    error, normalized = _text(value, name, 2048)
    if error:
        return error, ""
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return f"{name} 必须是合法 HTTPS URL", ""
    return None, normalized


def _string_list(value: Any, name: str, maximum: int) -> tuple[str | None, list[str]]:
    """校验非空且不重复的字符串列表。"""
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        return f"{name} 数量必须在 1~{maximum} 之间", []
    normalized = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(normalized) != len(value):
        return f"{name} 必须全部为非空字符串", []
    if len(set(normalized)) != len(normalized):
        return f"{name} 不能包含重复值", []
    return None, normalized


def normalize_menu(items: Any) -> tuple[str | None, dict[str, Any]]:
    """校验并构造完整菜单请求体。"""
    if not isinstance(items, list) or len(items) > 10:
        return "菜单项必须是最多 10 项的列表", {}
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        error, result = _normalize_menu_item(item, sub=False, path=f"menu.items[{index}]")
        if error:
            return error, {}
        normalized.append(result)
    return None, {"menu": {"items": normalized}}


def _normalize_menu_item(
    item: Any, *, sub: bool, path: str
) -> tuple[str | None, dict[str, Any]]:
    """校验一级或二级菜单项，并定位展示名称的具体字段。"""
    if not isinstance(item, dict):
        return "菜单项必须是对象", {}
    error, name = _text(item.get("name"), f"{path}.name", 14 if sub else 10, display=True)
    if error:
        return error, {}
    allowed = {"send_message", "link"} if sub else {"switch", "send_message", "link", "menu"}
    item_type = item.get("type")
    if item_type not in allowed:
        return f"菜单项 type 只能是 {', '.join(sorted(allowed))}", {}
    result: dict[str, Any] = {"name": name, "type": item_type}
    if item_type == "send_message":
        error, message = _text(item.get("send_message"), "send_message", 1024)
        if error:
            return error, {}
        result["send_message"] = message
    elif item_type == "link":
        error, link = _https_url(item.get("link"), "link")
        if error:
            return error, {}
        result["link"] = link
    elif item_type == "switch":
        switch = item.get("switch")
        if not isinstance(switch, dict) or not isinstance(switch.get("default"), bool):
            return "switch 必须包含 switch_id 和布尔 default", {}
        error, switch_id = _text(switch.get("switch_id"), "switch_id", 64)
        if error:
            return error, {}
        result["switch"] = {"switch_id": switch_id, "default": switch["default"]}
    else:
        children = item.get("sub_menu_items")
        if not isinstance(children, list) or not 1 <= len(children) <= 5:
            return "sub_menu_items 数量必须在 1~5 之间", {}
        result["sub_menu_items"] = []
        for index, child in enumerate(children):
            child_error, normalized = _normalize_menu_item(
                child, sub=True, path=f"{path}.sub_menu_items[{index}]"
            )
            if child_error:
                return child_error, {}
            result["sub_menu_items"].append(normalized)
    return None, result


def normalize_panel(panel: Any) -> tuple[str | None, dict[str, Any]]:
    """校验并构造面板内容。"""
    if not isinstance(panel, dict):
        return "panel 必须是对象", {}
    items = panel.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        return "panel.items 数量必须在 1~20 之间", {}
    remark = panel.get("remark", "")
    if not isinstance(remark, str) or len(remark) > 255:
        return "panel.remark 最多 255 个字符", {}
    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"panel.items[{index}]"
        if not isinstance(item, dict):
            return f"{path} 必须是对象", {}
        error, name = _text(item.get("name"), f"{path}.name", 14, display=True)
        if error:
            return error, {}
        desc = item.get("desc", "")
        if not isinstance(desc, str):
            return f"{path}.desc 必须是字符串", {}
        desc_length = _display_length(desc)
        if desc_length > 30:
            return f"{path}.desc 最多 30 个展示字符（中文/全角计 2），当前 {desc_length}", {}
        item_type = item.get("type")
        if item_type not in {"command", "link"}:
            return "面板项 type 只能是 command 或 link", {}
        only_admin = item.get("only_admin", False)
        if not isinstance(only_admin, bool):
            return "only_admin 必须是布尔值", {}
        result: dict[str, Any] = {"name": name, "desc": desc, "type": item_type, "only_admin": only_admin}
        if item_type == "link":
            link_error, link = _https_url(item.get("link"), "link")
            if link_error:
                return link_error, {}
            result["link"] = link
        normalized_items.append(result)
    return None, {"items": normalized_items, "remark": remark}


def normalize_panel_create(scope: Any, target_type: Any, panel: Any, user_openids: Any = None, group_openids: Any = None) -> tuple[str | None, dict[str, Any]]:
    """校验创建面板参数。"""
    if scope not in _SCOPES:
        return "scope 只能是 c2c、group、channel 或 dm", {}
    if target_type not in _TARGET_TYPES:
        return "target_type 只能是 all 或 specific", {}
    if scope in {"channel", "dm"} and target_type != "all":
        return "channel 和 dm 只支持全局面板", {}
    panel_error, normalized_panel = normalize_panel(panel)
    if panel_error:
        return panel_error, {}
    body: dict[str, Any] = {"scope": scope, "target_type": target_type, "panel": normalized_panel}
    if target_type == "all":
        if user_openids or group_openids:
            return "全局面板不能指定关联对象", {}
        return None, body
    key = "user_openids" if scope == "c2c" else "group_openids" if scope == "group" else ""
    values = user_openids if key == "user_openids" else group_openids
    if not key or (user_openids is not None and group_openids is not None):
        return "specific 面板的关联对象与 scope 不匹配", {}
    error, normalized = _string_list(values, key, 20)
    if error:
        return error, {}
    body[key] = normalized
    return None, body


def normalize_targets(op: Any, user_openids: Any = None, group_openids: Any = None) -> tuple[str | None, dict[str, Any]]:
    """校验面板关联对象变更。"""
    if op not in {"add", "del"}:
        return "op 只能是 add 或 del", {}
    if (user_openids is None) == (group_openids is None):
        return "必须且只能提供 user_openids 或 group_openids", {}
    key = "user_openids" if user_openids is not None else "group_openids"
    error, values = _string_list(user_openids if user_openids is not None else group_openids, key, 20)
    return (error, {}) if error else (None, {"op": op, key: values})
