"""User profile discovery must not leak Adapter lookup exception details."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from src.app.plugin_system.api import adapter_api

from ..config import QQBotExpandConfig
from ..plugin import QQBotExpandPlugin
from ..services.user_info_service import QQBotUserInfoService
from ..src import bridge


async def test_adapter_discovery_exception_is_redacted_through_public_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep real Service/discovery code and replace only registry and HTTP edges."""
    canary = "secret-token https://api.test/?appsecret=synthetic-secret"

    def unavailable(signature: str) -> None:
        """Model a registry failure whose details are not safe for logs."""
        raise RuntimeError(canary)

    def reject_network(request: httpx.Request) -> httpx.Response:
        """Discovery failure must never reach HTTP."""
        pytest.fail("Unexpected profile HTTP request")

    logger = MagicMock()
    monkeypatch.setattr(adapter_api, "get_adapter", unavailable)
    monkeypatch.setattr(bridge, "logger", logger)
    plugin = QQBotExpandPlugin(QQBotExpandConfig())
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_network)) as client:
        plugin.http_client = client
        result = await QQBotUserInfoService(plugin=plugin).get_user_info("user")
    assert result["success"] is False
    assert result["data"] is None
    assert result["error_type"] == "adapter_not_ready"
    logger.warning.assert_called_once_with("获取 qqbot_adapter 失败，适配器不可用")
    assert canary not in str(logger.mock_calls)
    assert "synthetic-secret" not in str(result)
