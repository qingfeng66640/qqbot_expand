"""QQ 2xx 业务码白名单离线回归。"""
from __future__ import annotations

from typing import Any
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from src.app.plugin_system.api import adapter_api
from ..src import bridge
from ..src.bridge import ADAPTER_SIGNATURE, api_request
from .conftest import FakeSendHandler, make_plugin


@pytest.mark.parametrize("field", ["code", "err_code"])
@pytest.mark.parametrize("message", [None, "private-error-canary token=synthetic"])
@pytest.mark.parametrize("status", [200, 201, 202])
@pytest.mark.parametrize("route", ["adapter", "http", "once"])
async def test_nonzero_business_error_is_safe_without_message(
    monkeypatch: pytest.MonkeyPatch, field: str, message: str | None,
    status: int, route: str,
) -> None:
    """Keep real HTTPX, discovery and bridge; replace only public Adapter edges."""
    payload: dict[str, Any] = {field: 10001}
    if message is not None:
        payload["message"] = message
    requests: list[httpx.Request] = []
    logger = Mock()
    monkeypatch.setattr(bridge, "logger", logger)

    def respond(request: httpx.Request) -> httpx.Response:
        """Return an offline official error envelope even for a 2xx status."""
        requests.append(request)
        return httpx.Response(status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        class PublicHandler(FakeSendHandler):
            """Model only Adapter authentication and its public HTTP delegation."""

            async def post_api(
                self, url: str, headers: dict[str, str], body: dict[str, Any],
            ) -> dict[str, Any]:
                """Use the actual HTTPX boundary, without bypassing bridge logic."""
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                return response.json()

        handler = PublicHandler()

        def get_adapter(signature: str) -> SimpleNamespace:
            """Expose only the same public signature and send_handler property."""
            assert signature == ADAPTER_SIGNATURE
            return SimpleNamespace(send_handler=handler)

        monkeypatch.setattr(adapter_api, "get_adapter", get_adapter)
        result = await api_request(
            make_plugin(http_client=client), "GET" if route == "http" else "POST",
            "/v2/synthetic", retry_network_errors=route != "once",
        )
    assert result == {"success": False, "data": None, "error": "调用失败"}
    assert len(requests) == 1
    assert "private-error-canary" not in str(result) + str(logger.mock_calls)
    assert "synthetic" not in str(logger.mock_calls)


@pytest.mark.parametrize("route", ["adapter", "http"])
@pytest.mark.parametrize("zero", [0, "0", "000"])
async def test_zero_business_codes_preserve_nested_success(
    route: str, zero: int | str, patch_send_handler: FakeSendHandler,
) -> None:
    """An explicit zero code must not turn a legitimate response into failure."""
    payload = {"code": zero, "err_code": zero, "data": {"url": "https://qun.qq.com/synthetic"}}
    patch_send_handler.post_result = payload
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(201, json=payload)),
    ) as client:
        result = await api_request(
            make_plugin(http_client=client), "POST" if route == "adapter" else "GET", "/v2/synthetic",
        )
    assert result == {"success": True, "data": payload, "error": None}
