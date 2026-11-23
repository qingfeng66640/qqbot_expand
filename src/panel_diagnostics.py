"""面板创建的白名单诊断：仅输出固定文案、枚举、计数和数值业务码。"""
from __future__ import annotations

import re
from typing import Any

from .errors import sanitize_http_status
from .menu_panel_policy import _display_length


_PANEL_CAUSES: dict[str, tuple[str, str]] = {
    "30001": ("invalid_parameter", "参数错误；请检查请求字段"),
    "40030001": ("invalid_parameter", "参数错误；请检查请求字段"),
    "40030008": ("invalid_url", "链接格式错误；请检查 HTTPS URL"),
    "40030009": ("operation_conflict", "面板操作并发冲突；请稍后手动重试"),
    "40030011": ("invalid_scope", "生效场景 scope 不合法"),
    "40030012": ("invalid_target_type", "生效范围 target_type 不合法"),
    "40030013": ("quantity_limit", "超出平台数量限制；请核对面板、元素及关联对象数量"),
    "40030015": ("invalid_item_type", "面板元素类型不合法"),
    "40030016": ("missing_field", "必填字段缺失"),
    "40030018": ("unsupported_scope", "当前场景不支持此操作"),
    "40030020": ("content_rejected", "面板内容未通过平台安全校验；请检查后修改内容"),
    "40030021": ("global_target_conflict", "全局面板不能指定关联对象"),
}


def _numeric_codes(payload: Any) -> dict[str, str]:
    """提取最多十位的非负十进制码，拒绝布尔、浮点和任意原文。"""
    codes: dict[str, str] = {}
    if not isinstance(payload, dict):
        return codes
    for key in ("code", "err_code"):
        value = payload.get(key)
        if type(value) is int and 0 <= value <= 9_999_999_999:
            codes[key] = str(value)
        elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            codes[key] = str(int(value))
    return codes


def panel_has_business_error(payload: Any) -> bool:
    """非零数值码不依赖 message；兼容原有 code/message 失败约定。"""
    if not isinstance(payload, dict):
        return False
    codes = _numeric_codes(payload)
    return any(value != "0" for value in codes.values()) or (
        "code" not in codes and bool(payload.get("code") and payload.get("message"))
    )


def _enum(value: Any, allowed: tuple[str, ...]) -> str:
    """仅输出已知枚举；非法值替换为固定标识。"""
    return value if isinstance(value, str) and value in allowed else "invalid"


def _count(value: Any) -> int:
    """统计列表长度并在超出官方上限时饱和，不输出列表元素。"""
    return min(len(value), 21) if isinstance(value, list) else 0


def _display_units(value: Any, maximum: int) -> int:
    """有界计算展示宽度，异常长输入仅记录超过上限的标记值。"""
    return min(_display_length(value[:maximum]), maximum) if isinstance(value, str) else 0


def panel_create_summary(body: Any) -> str:
    """从请求生成固定操作标识和无内容摘要，不含 URL、目标 ID 或正文。"""
    body = body if isinstance(body, dict) else {}
    panel = body.get("panel")
    panel = panel if isinstance(panel, dict) else {}
    raw_items = panel.get("items")
    items = [item for item in raw_items[:20] if isinstance(item, dict)] if isinstance(raw_items, list) else []
    remark = panel.get("remark")
    fields: dict[str, str | int] = {
        "operation": "panel_create",
        "scope": _enum(body.get("scope"), ("c2c", "group", "channel", "dm")),
        "target_type": _enum(body.get("target_type"), ("all", "specific")),
        "item_count": _count(raw_items),
        "command_count": sum(item.get("type") == "command" for item in items),
        "link_count": sum(item.get("type") == "link" for item in items),
        "name_units_max": max((_display_units(item.get("name"), 15) for item in items), default=0),
        "desc_units_max": max((_display_units(item.get("desc"), 31) for item in items), default=0),
        "remark_chars": min(len(remark), 256) if isinstance(remark, str) else 0,
        "user_count": _count(body.get("user_openids")),
        "group_count": _count(body.get("group_openids")),
    }
    return " ".join(f"{key}={value}" for key, value in fields.items())


def panel_create_failure(status: int | None, payload: Any) -> tuple[str, str]:
    """返回可交付的安全文案和机器诊断，不推测未知码或未记录的 HTTP 状态。"""
    codes = _numeric_codes(payload)
    selected = codes.get("err_code")
    if selected in {None, "0"}:
        selected = codes.get("code", "0")
    cause, reason = _PANEL_CAUSES.get(
        selected, ("unknown", "QQ API 拒绝创建，具体原因未确认")
    )
    if status is not None and status != 400 and not 200 <= status < 300:
        cause, reason = "http_status", sanitize_http_status(status)
    status_text = f"HTTP {status}" if status is not None else "HTTP 状态未记录"
    code_fields = [f"{key}={value}" for key, value in codes.items()]
    details = "，".join([status_text, *code_fields])
    error = f"创建指令面板失败：{reason}（{details}）"
    diagnostic = " ".join([f"status={status if status is not None else 'unrecorded'}",
                           f"cause={cause}", *code_fields])
    return error, diagnostic
