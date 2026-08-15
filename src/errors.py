"""对外错误信息脱敏。

QQ 开放平台的原始错误里可能带上 token、appid、完整 URL 等敏感信息，
而本插件的返回值会经由 Service / Tool 直接流向 LLM 与用户侧。
这里统一做白名单式归类：只输出预先定义好的短语，绝不透传原文。

设计与 ``qqbot_adapter`` 的 ``_sanitize_proactive_error`` 保持同一思路，
但覆盖面更广（额外区分 403 / 404 / 5xx / 参数错误）。
"""
from __future__ import annotations

__all__ = [
    "ERROR_ADAPTER_NOT_READY",
    "ERROR_BAD_REQUEST",
    "ERROR_FORBIDDEN",
    "ERROR_GENERIC",
    "ERROR_NETWORK",
    "ERROR_NOT_FOUND",
    "ERROR_RATE_LIMIT",
    "ERROR_SERVER",
    "ERROR_TIMEOUT",
    "ERROR_TOKEN",
    "sanitize_error",
    "sanitize_http_status",
]

# ============ 归类后的对外文案 ============

ERROR_ADAPTER_NOT_READY = "qqbot_adapter 未就绪，无法调用 QQ 开放 API"
ERROR_TOKEN = "token 获取失败或已失效"
ERROR_FORBIDDEN = "无权限调用该接口"
ERROR_NOT_FOUND = "接口不存在或目标已失效"
ERROR_RATE_LIMIT = "QQ API 限频"
ERROR_BAD_REQUEST = "请求参数不被 QQ API 接受"
ERROR_SERVER = "QQ 服务端错误"
ERROR_TIMEOUT = "请求超时"
ERROR_NETWORK = "网络错误"
ERROR_GENERIC = "调用失败"

# 关键字 -> 文案。顺序敏感：先匹配到的先返回，故把更具体的放在前面。
_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("timeout", "timed out", "readtimeout", "connecttimeout"), ERROR_TIMEOUT),
    (("401", "unauthorized", "token", "access_token", "invalid credential"), ERROR_TOKEN),
    (("429", "rate limit", "ratelimit", "too many requests", "频率"), ERROR_RATE_LIMIT),
    (("403", "forbidden", "permission"), ERROR_FORBIDDEN),
    (("404", "not found"), ERROR_NOT_FOUND),
    (("400", "bad request", "invalid parameter"), ERROR_BAD_REQUEST),
    (("500", "502", "503", "504", "internal server error", "bad gateway"), ERROR_SERVER),
    (("connection", "network", "dns", "ssl", "proxy", "unreachable"), ERROR_NETWORK),
)


def sanitize_error(exc: BaseException | str) -> str:
    """把任意异常 / 错误串归类成白名单文案。

    Args:
        exc: 捕获到的异常对象，或已经取出的错误描述串。

    Returns:
        白名单内的中文错误描述，绝不包含原始异常内容。
    """
    text = str(exc).lower()
    for keywords, message in _KEYWORD_RULES:
        if any(keyword in text for keyword in keywords):
            return message
    return ERROR_GENERIC


def sanitize_http_status(status_code: int) -> str:
    """把 HTTP 状态码归类成白名单文案。

    Args:
        status_code: QQ API 返回的 HTTP 状态码。

    Returns:
        白名单内的中文错误描述。
    """
    if status_code == 401:
        return ERROR_TOKEN
    if status_code == 403:
        return ERROR_FORBIDDEN
    if status_code == 404:
        return ERROR_NOT_FOUND
    if status_code == 429:
        return ERROR_RATE_LIMIT
    if 400 <= status_code < 500:
        return ERROR_BAD_REQUEST
    if status_code >= 500:
        return ERROR_SERVER
    return ERROR_GENERIC
