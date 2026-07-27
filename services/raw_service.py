"""QQ 开放 API 通用调用 Service。

``qqbot_adapter`` 的 ``SendHandler.post_api()`` 只支持 POST，无法触达
``GET /users/@me``、``PUT /interactions/{id}``、``DELETE`` 类接口。
本 Service 提供一个统一的逃生出口，让其他插件在本插件尚未包装某个 openapi 时
也能直接调用。

**安全约束**：

- 受 ``features.allow_raw_request`` 总开关管控，关闭后一律拒绝；
- 受 ``features.raw_allowed_methods`` 白名单管控，默认允许 GET/POST/PUT/DELETE；
- ``path`` 必须是以 ``/`` 开头的相对路径，桥接层会拒绝绝对 URL 与 ``..``，
  防止请求被打到 QQ 域名之外的主机（SSRF）。

本 Service **不注册为 Tool**，避免把任意 API 调用能力直接交给 LLM。
"""
from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.bridge import SERVICE_SIGNATURE, api_request, failure
from ..src.constants import RAW_SUPPORTED_METHODS

__all__ = ["QQBotRawService"]


class QQBotRawService(BaseService):
    """QQ 开放 API 通用调用服务。"""

    service_name = "qqbot_raw"
    service_description = "统一调用任意 QQ 开放 API（GET/POST/PUT/DELETE），并提供桥接状态探测"
    version = "0.2.0"

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        *,
        force_production: bool = False,
    ) -> dict[str, Any]:
        """向 QQ 开放平台发起一次任意 API 请求。

        Args:
            method: HTTP 方法，大小写不敏感。
            path: 以 ``/`` 开头的相对路径，例如 ``/users/@me``。
            body: 请求体，用于 POST / PUT。
            query: 查询参数，用于 GET / DELETE。
            force_production: 强制使用正式域名。互动应答等沙箱不支持的接口需置 True。

        Returns:
            ``{"success": bool, "data": dict | None, "error": str | None}``。
        """
        if not self._raw_enabled():
            return failure("raw 通道已被 features.allow_raw_request 关闭")

        normalized_method = str(method or "").strip().upper()
        if normalized_method not in RAW_SUPPORTED_METHODS:
            return failure(f"method 只能是 {sorted(RAW_SUPPORTED_METHODS)} 之一")
        if normalized_method not in self._allowed_methods():
            return failure(f"{normalized_method} 不在 features.raw_allowed_methods 白名单内")

        return await api_request(
            self.plugin,
            normalized_method,
            path,
            body,
            query=query,
            force_production=force_production,
        )

    async def get_status(self) -> dict[str, Any]:
        """探测桥接是否可用。

        转发 ``qqbot_adapter:service:qqbot`` 的 ``get_status()``，并附上本插件
        自身的就绪信息，方便调用方一次性判断链路是否打通。

        Returns:
            适配器状态字段（``connected`` / ``bot_id`` / ``env`` 等），额外附加
            ``http_client_ready``、``raw_enabled``。
        """
        status: dict[str, Any] = {
            "http_client_ready": getattr(self.plugin, "http_client", None) is not None,
            "raw_enabled": self._raw_enabled(),
        }
        try:
            from src.app.plugin_system.api import service_api

            adapter_service = service_api.get_service(SERVICE_SIGNATURE)
        except Exception:  # noqa: BLE001 - 适配器不可用不应让调用方崩溃
            adapter_service = None

        if adapter_service is None:
            status["connected"] = False
            status["error"] = "qqbot_adapter 服务不可用"
            return status

        adapter_status = await adapter_service.get_status()
        if isinstance(adapter_status, dict):
            status.update(adapter_status)
        return status

    # ============ 配置读取 ============

    def _raw_enabled(self) -> bool:
        """读取 ``features.allow_raw_request`` 总开关。

        Returns:
            raw 通道是否可用；配置缺失时默认为 True。
        """
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        return bool(getattr(features, "allow_raw_request", True))

    def _allowed_methods(self) -> set[str]:
        """读取 ``features.raw_allowed_methods`` 白名单。

        Returns:
            归一化为大写的方法集合；配置缺失时回落到全部受支持方法。
        """
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        configured = getattr(features, "raw_allowed_methods", None)
        if not configured:
            return set(RAW_SUPPORTED_METHODS)
        return {str(item).strip().upper() for item in configured}
