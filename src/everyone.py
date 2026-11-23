"""隔离待发送 JSON，并只检查 QQ 可解释文本字段的 @全体意图。"""
from __future__ import annotations

import json
import re
from typing import Any

_EVERYONE_INTENT = re.compile(
    r"<\s*qqbot-at-everyone(?=[\s/>]|$)|@everyone|<\s*@all\s*>",
    re.IGNORECASE,
)


def snapshot_message_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """在首次 await 前获得独立 JSON 请求体，让检查与发送共享同一快照。

    按 HTTP 的 JSON 语义复制嵌套容器（元组也转为数组），不调用 deepcopy、
    default 或不透明对象的转换方法。标准编码器拒绝循环、超深结构、非 JSON
    对象与非有限数值；异常统一脱敏，不能把原始值带到调用方。

    Raises:
        ValueError: 请求体不能安全序列化为 JSON。
    """
    try:
        return json.loads(json.dumps(payload, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("消息体必须为可序列化 JSON，不能包含循环引用、过深结构或非 JSON 值") from None


def contains_everyone(value: object) -> bool:
    """识别新全体标签和旧协议意图，不把单人 @ 当作全体。"""
    return isinstance(value, str) and _EVERYONE_INTENT.search(value) is not None


def payload_mentions_everyone(payload: dict[str, Any]) -> bool:
    """检查 content、markdown.content 与模板 params.values 的字符串。

    只走官方文本字段的固定层级；忽略按钮回调、媒体等不透明数据，
    不对未知对象调用 str，也不递归扫描超大或循环容器。
    """
    if contains_everyone(payload.get("content")):
        return True
    markdown = payload.get("markdown")
    if not isinstance(markdown, dict):
        return False
    if contains_everyone(markdown.get("content")):
        return True
    params = markdown.get("params")
    if not isinstance(params, list):
        return False
    for item in params:
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        if contains_everyone(values):
            return True
        if isinstance(values, (list, tuple)) and any(contains_everyone(value) for value in values):
            return True
    return False
