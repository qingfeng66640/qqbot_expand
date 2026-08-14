"""从 QQ Interaction 原始事件读取菜单功能标识。"""
from __future__ import annotations

from typing import Any


def extract_feature_id(raw_event: Any, maximum: int = 256) -> str:
    """读取 ``data.resolved.feature_id``，缺失或非法时返回空串。"""
    if not isinstance(raw_event, dict):
        return ""
    data = raw_event.get("data")
    resolved = data.get("resolved") if isinstance(data, dict) else None
    value = resolved.get("feature_id") if isinstance(resolved, dict) else None
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    return normalized if 0 < len(normalized) <= maximum else ""
