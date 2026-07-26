"""与 qqbot_adapter 的桥接层，以及统一的 QQ 开放 API 请求出口。

设计要点：

1. **只碰公共属性**。本模块通过 ``adapter_api.get_adapter()`` 拿到
   ``QQBotAdapter`` 实例，再取其 ``send_handler`` 公共属性，绝不访问
   ``_send_handler`` / ``_token_mgr`` 之类的私有成员。

2. **POST 复用适配器**。``SendHandler.post_api()`` 内置了 401 重试与错误处理，
   POST 请求一律走它，避免重复实现重试语义。

3. **非 POST 自持客户端**。``post_api()`` 只支持 POST，GET / PUT / DELETE
   由本插件自己的 ``httpx.AsyncClient`` 发出，token 仍向适配器索取。
   该客户端的生命周期挂在 ``QQBotExpandPlugin`` 上（Service 每次 get 都是新实例，
   不能缓存长生命周期资源）。

4. **路径安全**。``path`` 必须是以 ``/`` 开头的相对路径，拒绝绝对 URL 与
   ``..``，防止 LLM 参数把请求打到任意主机上。
"""
from __future__ import annotations

import asyncio
import random
from typing import Any
from urllib.parse import urlencode

from src.app.plugin_system.api.log_api import get_logger

from .constants import API_BASE_PRODUCTION
from .errors import ERROR_GENERIC, sanitize_error, sanitize_http_status

logger = get_logger("qqbot_expand")

__all__ = [
    "ADAPTER_SIGNATURE",
    "SERVICE_SIGNATURE",
    "api_request",
    "build_url",
    "failure",
    "resolve_send_handler",
    "success",
    "validate_path",
]

# qqbot_adapter 暴露的组件签名
ADAPTER_SIGNATURE = "qqbot_adapter:adapter:qqbot_adapter"
SERVICE_SIGNATURE = "qqbot_adapter:service:qqbot"

_JSON_HEADERS = {"Content-Type": "application/json"}


# ============ 统一返回结构 ============


def success(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造成功返回体。

    Args:
        data: QQ API 的响应 JSON。

    Returns:
        ``{"success": True, "data": ..., "error": None}``。
    """
    return {"success": True, "data": data or {}, "error": None}


def failure(error: str) -> dict[str, Any]:
    """构造失败返回体。

    Args:
        error: 已脱敏的错误描述。

    Returns:
        ``{"success": False, "data": None, "error": ...}``。
    """
    return {"success": False, "data": None, "error": error}


# ============ 适配器桥接 ============


def resolve_send_handler() -> Any | None:
    """解析 qqbot_adapter 的 SendHandler。

    Returns:
        SendHandler 实例；适配器未启动或尚未初始化时返回 None。
    """
    try:
        from src.app.plugin_system.api import adapter_api

        adapter = adapter_api.get_adapter(ADAPTER_SIGNATURE)
    except Exception as exc:  # noqa: BLE001 - 适配器不可用不应让调用方崩溃
        logger.warning(f"获取 qqbot_adapter 失败: {exc}")
        return None

    if adapter is None:
        logger.warning("qqbot_adapter 未启动，无法调用 QQ 开放 API")
        return None
    return getattr(adapter, "send_handler", None)


# ============ 路径与 URL ============


def validate_path(path: str) -> str | None:
    """校验 API 路径的合法性。

    Args:
        path: 待校验的相对路径。

    Returns:
        错误描述；合法时返回 None。
    """
    if not path or not isinstance(path, str):
        return "path 不能为空"
    stripped = path.strip()
    if not stripped.startswith("/"):
        return "path 必须以 / 开头（只接受相对路径）"
    if stripped.startswith("//"):
        return "path 不能以 // 开头（疑似协议相对 URL）"
    if "://" in stripped:
        return "path 不能包含完整 URL，只接受相对路径"
    if ".." in stripped:
        return "path 不能包含 .. 路径穿越"
    return None


def build_url(
    base_url: str, path: str, query: dict[str, Any] | None = None
) -> str:
    """拼接完整请求 URL。

    Args:
        base_url: API 基础地址（沙箱或正式）。
        path: 以 / 开头的相对路径。
        query: 查询参数，值会被 urlencode。

    Returns:
        完整 URL。
    """
    url = f"{base_url.rstrip('/')}{path.strip()}"
    if query:
        filtered = {k: v for k, v in query.items() if v is not None}
        if filtered:
            url = f"{url}?{urlencode(filtered, doseq=True)}"
    return url


# ============ 统一请求出口 ============


async def api_request(
    plugin: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    query: dict[str, Any] | None = None,
    force_production: bool = False,
) -> dict[str, Any]:
    """向 QQ 开放平台发起一次 API 请求。

    Args:
        plugin: ``QQBotExpandPlugin`` 实例，用于取共享 httpx 客户端与配置。
        method: HTTP 方法，大小写不敏感。
        path: 以 ``/`` 开头的相对路径，如 ``/v2/users/xxx/messages``。
        body: 请求体（POST / PUT 用）。
        query: 查询参数（GET / DELETE 用）。
        force_production: 强制使用正式域名。互动 ACK 等接口沙箱不支持，需置 True。

    Returns:
        ``{"success": bool, "data": dict | None, "error": str | None}``。
    """
    normalized_method = str(method or "").strip().upper()
    if not normalized_method:
        return failure("method 不能为空")

    path_error = validate_path(path)
    if path_error:
        return failure(path_error)

    send_handler = resolve_send_handler()
    if send_handler is None:
        return failure("qqbot_adapter 未就绪，无法调用 QQ 开放 API")

    base_url = API_BASE_PRODUCTION if force_production else str(send_handler.base_url)
    url = build_url(base_url, path, query)

    if _should_log_payload(plugin):
        logger.info(f"[qqbot_expand] {normalized_method} {url} body={body}")

    if normalized_method == "POST":
        return await _request_via_adapter(send_handler, url, body or {})
    return await _request_via_http(plugin, send_handler, normalized_method, url, body)


def _should_log_payload(plugin: Any) -> bool:
    """读取 ``features.debug_log_payload`` 开关。

    Args:
        plugin: 插件实例。

    Returns:
        是否需要打印完整请求体。
    """
    config = getattr(plugin, "config", None)
    features = getattr(config, "features", None)
    return bool(getattr(features, "debug_log_payload", False))


def _retry_settings(plugin: Any) -> tuple[int, float, float, float]:
    """读取重试相关配置，缺失时回落到与适配器一致的默认值。

    Args:
        plugin: 插件实例。

    Returns:
        ``(max_attempts, backoff_base, backoff_max, jitter)``。
    """
    http_cfg = getattr(getattr(plugin, "config", None), "http", None)
    return (
        int(getattr(http_cfg, "retry_max_attempts", 3)),
        float(getattr(http_cfg, "retry_backoff_base", 1.0)),
        float(getattr(http_cfg, "retry_backoff_max", 10.0)),
        float(getattr(http_cfg, "retry_jitter", 0.3)),
    )


async def _request_via_adapter(
    send_handler: Any, url: str, body: dict[str, Any]
) -> dict[str, Any]:
    """走适配器的 ``post_api()`` 发送 POST 请求。

    Args:
        send_handler: 适配器的 SendHandler。
        url: 完整 URL。
        body: 请求体。

    Returns:
        统一返回结构。
    """
    try:
        token = await send_handler.get_token()
    except Exception as exc:  # noqa: BLE001 - 错误须脱敏后返回
        logger.warning(f"获取 access_token 失败: {exc}")
        return failure(sanitize_error(exc))

    headers = {"Authorization": f"QQBot {token}", **_JSON_HEADERS}
    try:
        response = await send_handler.post_api(url, headers, body)
    except Exception as exc:  # noqa: BLE001 - 错误须脱敏后返回
        logger.warning(f"POST {url} 失败: {exc}")
        return failure(sanitize_error(exc))

    if not isinstance(response, dict):
        return success({})
    # QQ API 失败时返回 {"code": xxx, "message": "..."}，此处只透出错误码归类
    if response.get("code") and response.get("message"):
        logger.warning(f"POST {url} 返回业务错误: {response}")
        return failure(sanitize_error(str(response.get("code"))))
    return success(response)


async def _request_via_http(
    plugin: Any,
    send_handler: Any,
    method: str,
    url: str,
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    """走本插件自持的 httpx 客户端发送非 POST 请求。

    Args:
        plugin: 插件实例，提供 ``http_client``。
        send_handler: 适配器的 SendHandler，用于取 token。
        method: 已归一化的 HTTP 方法。
        url: 完整 URL。
        body: 请求体，None 表示不带 body。

    Returns:
        统一返回结构。
    """
    client = getattr(plugin, "http_client", None)
    if client is None:
        return failure("HTTP 客户端未初始化，请确认插件已正确加载")

    max_attempts, backoff_base, backoff_max, jitter = _retry_settings(plugin)
    last_error = ERROR_GENERIC

    for attempt in range(max_attempts + 1):
        try:
            token = await send_handler.get_token()
        except Exception as exc:  # noqa: BLE001 - 错误须脱敏后返回
            logger.warning(f"获取 access_token 失败: {exc}")
            return failure(sanitize_error(exc))

        headers = {"Authorization": f"QQBot {token}", **_JSON_HEADERS}
        try:
            response = await client.request(method, url, headers=headers, json=body)
        except Exception as exc:  # noqa: BLE001 - 网络错误可重试
            last_error = sanitize_error(exc)
            logger.warning(f"{method} {url} 第 {attempt + 1} 次请求失败: {exc}")
            if attempt >= max_attempts:
                break
            await _sleep_backoff(attempt, backoff_base, backoff_max, jitter)
            continue

        # HTTP 状态码错误不重试，直接归类返回
        if response.status_code >= 400:
            logger.warning(f"{method} {url} 返回 HTTP {response.status_code}")
            return failure(sanitize_http_status(response.status_code))
        return success(_parse_json(response))

    return failure(last_error)


def _parse_json(response: Any) -> dict[str, Any]:
    """尽力把响应解析成 dict。

    Args:
        response: httpx 响应对象。

    Returns:
        解析出的字典；非 JSON 或非对象时返回 ``{"raw": "..."}``。
    """
    if not response.content:
        return {}
    try:
        parsed = response.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应回落成原始文本
        return {"raw": response.text}
    if isinstance(parsed, dict):
        return parsed
    return {"data": parsed}


async def _sleep_backoff(
    attempt: int, base: float, maximum: float, jitter: float
) -> None:
    """按指数退避 + 抖动休眠。

    Args:
        attempt: 当前是第几次重试（从 0 开始）。
        base: 退避基准秒数。
        maximum: 退避上限秒数。
        jitter: 抖动系数（0~1）。
    """
    delay = min(base * (2**attempt), maximum)
    delay += delay * jitter * random.random()
    await asyncio.sleep(delay)
