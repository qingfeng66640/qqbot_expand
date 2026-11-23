"""QQ 用户基本信息的只读传输、严格响应校验与插件级 10 QPS 限流。

依据本地 2026-11-17T18:26:32 归档的 GET /v2/users/{user_openid}/info 契约。
认证仅使用 Adapter 公共 send_handler；不重试、不跟随重定向、不缓存个人信息。
限流器只保存单调时钟时间戳，生命周期跟随 Expand 插件实例。
"""

from __future__ import annotations

import re
from asyncio import Lock, sleep
from collections import deque
from time import monotonic
from typing import Any
from unicodedata import category
from urllib.parse import quote

import httpx

from .bridge import failure, resolve_send_handler, success
from .errors import (
    ERROR_ADAPTER_NOT_READY,
    ERROR_GENERIC,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_TOKEN,
)
from .http_diagnostics import user_info_http_diagnostics

__all__ = ["UserInfoRateLimiter", "query_user_info"]

_ERRORS = {
    "invalid_user_openid": "user_openid 必须是非空且不含路径分隔符或控制字符的字符串",
    "adapter_not_ready": ERROR_ADAPTER_NOT_READY,
    "client_not_ready": "HTTP 客户端未初始化或已关闭",
    "authentication_failed": ERROR_TOKEN,
    "no_friend_relationship": "用户与机器人无好友关系，无法查询用户信息",
    "rate_limited": ERROR_RATE_LIMIT,
    "server_error": ERROR_SERVER,
    "http_error": "QQ 用户信息接口 HTTP 请求失败",
    "timeout": ERROR_TIMEOUT,
    "network_error": ERROR_NETWORK,
    "request_failed": ERROR_GENERIC,
    "invalid_response": "QQ 用户信息响应格式无效",
    "api_error": "QQ 用户信息接口业务错误",
}
_AUTH_CODES = {"11241", "11242", "11243", "11251", "11261", "11262", "11273", "11275"}


class UserInfoRateLimiter:
    """在单个事件循环中串行分配滚动一秒窗口内的十次请求额度。"""

    def __init__(self) -> None:
        """仅保存锁和时间戳，不保存 openid、响应、token 或其他个人信息。"""
        self._lock = Lock()
        self._attempts: deque[float] = deque()

    async def acquire(self) -> None:
        """在物理 HTTP 尝试前等待额度；取消等待不消耗额度。"""
        async with self._lock:
            while True:
                now = monotonic()
                while self._attempts and self._attempts[0] <= now - 1.0:
                    self._attempts.popleft()
                if len(self._attempts) < 10:
                    self._attempts.append(now)
                    return
                # sleep 可能提前唤醒，必须重新检查实际单调时间。
                await sleep(self._attempts[0] + 1.0 - now)


def _fail(error_type: str) -> dict[str, Any]:
    """保持既有 success/data/error 结构，仅附加稳定的只读分类。"""
    return {**failure(_ERRORS[error_type]), "error_type": error_type}


def _valid_openid(value: str) -> bool:
    """拒绝路径注入和模糊编码，不假设平台未规定的长度或 hex 格式。"""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    return not any(
        char in "/\\%?#" or char.isspace() or category(char).startswith("C")
        for char in value
    )


def _business_error(payload: dict[str, Any]) -> str | None:
    """同时识别 err_code/code，不依赖可能变化且含敏感信息的 message。"""
    codes: set[str] = set()
    for key in ("err_code", "code"):
        if key not in payload:
            continue
        value = payload[key]
        if type(value) not in (int, str) or not re.fullmatch(
            r"[0-9]{1,10}", str(value)
        ):
            return "invalid_response"
        if str(value) != "0":
            codes.add(str(value))
    if "40184001" in codes:
        return "no_friend_relationship"
    if codes & _AUTH_CODES:
        return "authentication_failed"
    if codes:
        return "api_error"
    return None


def _decode_response(response: httpx.Response) -> dict[str, Any]:
    """保留状态与业务错误分类，成功仅透出严格校验的四个元数据字段。"""
    try:
        payload = response.json()
    except (ValueError, RecursionError):
        payload = None
    business_error = _business_error(payload) if isinstance(payload, dict) else None
    if business_error == "no_friend_relationship":
        return _fail(business_error)
    if response.status_code == 401 or business_error == "authentication_failed":
        return _fail("authentication_failed")
    if response.status_code == 429:
        return _fail("rate_limited")
    if response.status_code >= 500:
        return _fail("server_error")
    if not 200 <= response.status_code < 300:
        return _fail("http_error")
    if business_error is not None:
        return _fail(business_error)
    if not isinstance(payload, dict):
        return _fail("invalid_response")
    for key, expected_type in (
        ("username", str),
        ("avatar", str),
        ("allow_proactive_msg", bool),
    ):
        if type(payload.get(key)) is not expected_type:
            return _fail("invalid_response")
    data = {key: payload[key] for key in ("username", "avatar", "allow_proactive_msg")}
    if "union_openid" in payload:
        if type(payload["union_openid"]) is not str:
            return _fail("invalid_response")
        data["union_openid"] = payload["union_openid"]
    return {**success(data), "error_type": None}


async def query_user_info(plugin: Any, user_openid: str) -> dict[str, Any]:
    """通过公共认证与共享客户端查询用户信息；一次调用最多一次 HTTP 尝试。

    Args:
        plugin: 持有共享 http_client 的 Expand 插件实例。
        user_openid: 当前 bot 的用户 OpenID；不接受群/member/union 标识替代路由。

    Returns:
        兼容 success/data/error 的字典，额外包含 error_type（成功为 None）。
        username/avatar/allow_proactive_msg 必须存在且类型正确；union_openid 可缺失。
        开关缺失或查询失败均返回 data=None，绝不推断 allow_proactive_msg=False。

    Raises:
        asyncio.CancelledError: 调用方取消直接传播，不触发重试或后台任务。
    """
    if not _valid_openid(user_openid):
        return _fail("invalid_user_openid")
    try:
        send_handler = resolve_send_handler()
    except Exception:
        # 公共属性访问失败时也不回显异常、URL 或凭据。
        return _fail("adapter_not_ready")
    if send_handler is None:
        return _fail("adapter_not_ready")
    client = getattr(plugin, "http_client", None)
    if client is None or client.is_closed:
        return _fail("client_not_ready")
    try:
        token = await send_handler.get_token()
    except Exception:
        return _fail("authentication_failed")
    if (
        not isinstance(token, str)
        or not token
        or not token.isascii()
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in token)
    ):
        return _fail("authentication_failed")
    # 由插件构造并拥有，Service 重建和同实例客户端重载均不清空额度。
    await plugin.user_info_rate_limiter.acquire()
    try:
        with user_info_http_diagnostics():
            response = await client.request(
                "GET",
                f"{send_handler.base_url.rstrip('/')}/v2/users/{quote(user_openid, safe='')}/info",
                headers={
                    "Authorization": f"QQBot {token}",
                    "Accept": "application/json",
                },
                # 共享客户端即使启用 redirects，此查询也不能偷偷增加物理尝试。
                follow_redirects=False,
            )
    except httpx.TimeoutException:
        return _fail("timeout")
    except httpx.RequestError:
        return _fail("network_error")
    except Exception:
        return _fail("request_failed")
    return _decode_response(response)
