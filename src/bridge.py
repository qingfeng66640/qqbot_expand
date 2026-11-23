"""与 qqbot_adapter 的桥接层，以及统一的 QQ 开放 API 请求出口。

设计要点：

1. **只碰公共属性**。本模块通过 ``adapter_api.get_adapter()`` 拿到
   ``QQBotAdapter`` 实例，再取其 ``send_handler`` 公共属性，绝不访问
   ``_send_handler`` / ``_token_mgr`` 之类的私有成员。

2. **普通 POST 复用适配器**。``SendHandler.post_api()`` 内置了 401 重试与错误处理，
   默认 POST 走它；显式禁止重试的 POST 不走该内部可重试出口。

3. **其余请求自持客户端**。GET / PUT / DELETE 及显式一次性 POST
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
from urllib.parse import quote, urlencode

import httpx

from src.app.plugin_system.api.log_api import get_logger

from .constants import API_BASE_PRODUCTION
from .errors import (
    ERROR_ADAPTER_NOT_READY,
    ERROR_FORBIDDEN,
    ERROR_GENERIC,
    api_business_error,
    format_api_error_diagnostics,
    is_api_access_denied,
    removal_permission_error,
    sanitize_error,
    sanitize_http_status,
)
from .panel_diagnostics import panel_create_failure, panel_create_summary, panel_has_business_error

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
    except Exception:  # 发现异常可能包含凭据或 URL，只记录固定摘要。
        logger.warning("获取 qqbot_adapter 失败，适配器不可用")
        return None

    if adapter is None:
        logger.warning("qqbot_adapter 未启动，无法调用 QQ 开放 API")
        return None
    return getattr(adapter, "send_handler", None)


# ============ 路径与 URL ============


def encode_path_segment(value: str, name: str) -> tuple[str | None, str]:
    """校验并编码相对 API 路径中的单个外部参数。"""
    if not isinstance(value, str) or not value.strip():
        return f"{name} 不能为空", ""
    return None, quote(value.strip(), safe="")


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
    retry_network_errors: bool = True,
    operation: str | None = None,
) -> dict[str, Any]:
    """向 QQ 开放平台发起一次 API 请求。

    Args:
        plugin: ``QQBotExpandPlugin`` 实例，用于取共享 httpx 客户端与配置。
        method: HTTP 方法，大小写不敏感。
        path: 以 ``/`` 开头的相对路径，如 ``/v2/users/xxx/messages``。
        body: 请求体（POST / PUT 用）。
        query: 查询参数（GET / DELETE 用）。
        force_production: 强制使用正式域名。互动 ACK 等接口沙箱不支持，需置 True。
        retry_network_errors: 网络异常时是否按配置重试；ACK 等一次性操作应置 False。
        operation: 可选诊断标识，仅 panel_create 与 POST /v2/panels 匹配时启用。

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
        return failure(ERROR_ADAPTER_NOT_READY)

    base_url = API_BASE_PRODUCTION if force_production else str(send_handler.base_url)
    url = build_url(base_url, path, query)

    panel_context = (
        panel_create_summary(body)
        if operation == "panel_create" and normalized_method == "POST" and path.strip() == "/v2/panels"
        else None
    )
    if _should_log_payload(plugin):
        if panel_context is not None:
            logger.info(panel_context)
        else:
            logger.info(f"[qqbot_expand] {normalized_method} {url} body={body}")

    # Adapter 的公共 POST 出口内部会重试网络异常；显式一次性请求不能走它。
    # 保留普通 POST 的既有语义，仅禁重试时复用插件客户端和 Adapter token。
    if normalized_method == "POST" and retry_network_errors:
        return await _request_via_adapter(send_handler, url, body or {}, panel_context=panel_context)
    return await _request_via_http(
        plugin,
        send_handler,
        normalized_method,
        url,
        body,
        retry_network_errors=retry_network_errors,
        panel_context=panel_context,
    )


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


def _log_panel_failure(context: str, status: int | None, payload: Any) -> str:
    """关联面板操作与安全业务诊断，返回同一错误文案供 Service/Tool 使用。"""
    error, details = panel_create_failure(status, payload)
    logger.warning(f"{context} {details}")
    return error


def _log_http_failure(method: str, response: Any, *, panel_context: str | None = None) -> str:
    """记录可关联的 QQ 错误码，不记录响应正文、完整 URL 或异常文本。"""
    try:
        payload = response.json()
    except (ValueError, RecursionError):
        # 诊断为尽力读取；畸形或过深 JSON 不能破坏 HTTP 失败返回。
        payload = None
    if panel_context is not None:
        return _log_panel_failure(panel_context, response.status_code, payload)
    details = format_api_error_diagnostics(payload)
    logger.warning(f"{method} QQ API 失败: status={response.status_code} {details}".rstrip())
    if permission_error := removal_permission_error(payload):
        return permission_error
    if response.status_code == 400 and is_api_access_denied(payload):
        return ERROR_FORBIDDEN
    return sanitize_http_status(response.status_code)


async def _request_via_adapter(
    send_handler: Any, url: str, body: dict[str, Any], *, panel_context: str | None = None
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
        error = sanitize_error(exc)
        logger.warning(f"获取 access_token 失败: {error}")
        return failure(error)

    headers = {"Authorization": f"QQBot {token}", **_JSON_HEADERS}
    try:
        response = await send_handler.post_api(url, headers, body)
    except httpx.HTTPStatusError as exc:
        return failure(_log_http_failure("POST", exc.response, panel_context=panel_context))
    except Exception as exc:  # noqa: BLE001 - 错误须脱敏后返回
        error = sanitize_error(exc)
        logger.warning(f"POST 请求失败: {error}")
        return failure(error)

    if not isinstance(response, dict):
        return success({})
    if panel_context is not None:
        # Adapter 返回解码字典而非 Response，此处不猜测实际 HTTP 状态。
        if panel_has_business_error(response):
            return failure(_log_panel_failure(panel_context, None, response))
        return success(response)
    # QQ 的 2xx 也可能带业务错误；只依据错误码，不依赖 message 或透传正文。
    if business_error := api_business_error(response):
        logger.warning(f"POST QQ API 业务失败: {format_api_error_diagnostics(response)}")
        return failure(business_error)
    return success(response)


async def _request_via_http(
    plugin: Any,
    send_handler: Any,
    method: str,
    url: str,
    body: dict[str, Any] | None,
    *,
    retry_network_errors: bool = True,
    panel_context: str | None = None,
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
    if not retry_network_errors:
        max_attempts = 0
    last_error = ERROR_GENERIC

    attempt = 0
    while True:
        try:
            token = await send_handler.get_token()
        except Exception as exc:  # noqa: BLE001 - 错误须脱敏后返回
            error = sanitize_error(exc)
            logger.warning(f"获取 access_token 失败: {error}")
            return failure(error)

        headers = {"Authorization": f"QQBot {token}", **_JSON_HEADERS}
        try:
            response = await client.request(method, url, headers=headers, json=body)
        except Exception as exc:  # noqa: BLE001 - 网络错误可重试
            last_error = sanitize_error(exc)
            logger.warning(f"{method} 第 {attempt + 1} 次请求失败: {last_error}")
            if attempt >= max_attempts:
                break
            await _sleep_backoff(attempt, backoff_base, backoff_max, jitter)
            attempt += 1
            continue

        # 未取得 2xx 确认即失败；3xx 不能因空正文被解析为成功，也不重放。
        if not 200 <= response.status_code < 300:
            return failure(_log_http_failure(method, response, panel_context=panel_context))
        payload = _parse_json(response)
        if panel_context is not None and panel_has_business_error(payload):
            return failure(_log_panel_failure(panel_context, response.status_code, payload))
        if business_error := api_business_error(payload):
            logger.warning(f"{method} QQ API 业务失败: {format_api_error_diagnostics(payload)}")
            return failure(business_error)
        return success(payload)

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
