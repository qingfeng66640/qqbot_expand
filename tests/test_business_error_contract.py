"""Business failures in successful HTTP envelopes must not leak into Tools."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from src.app.plugin_system.api import adapter_api

from ..services.group_members_service import QQBotGroupMembersService
from ..src import bridge
from ..src.bridge import ADAPTER_SIGNATURE, api_request
from ..tools.utility import QQGenerateShareLinkTool
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


@pytest.mark.parametrize(
    ("status", "codes", "expected"),
    [
        (400, {"code": 11253, "err_code": 40012010}, "无权限调用该接口"),
        (200, {"code": 11253, "err_code": 40012010}, "无权限调用该接口"),
        (400, {"code": "0000011253", "err_code": "40012010"}, "无权限调用该接口"),
        (200, {"code": "0000011253", "err_code": "40012010"}, "无权限调用该接口"),
        (400, {"code": 40012010, "err_code": 11253}, "无权限调用该接口"),
        (400, {"code": 0, "err_code": 40012010}, "请求参数不被 QQ API 接受"),
        (200, {"code": 0, "err_code": 40012010}, "请求参数不被 QQ API 接受"),
        (400, {"code": 111253, "err_code": 40012010}, "请求参数不被 QQ API 接受"),
        (401, {"code": 11253, "err_code": 40012010}, "token 获取失败或已失效"),
        (429, {"code": 11253, "err_code": 40012010}, "QQ API 限频"),
    ],
)
async def test_member_query_classifies_platform_permission(
    monkeypatch: pytest.MonkeyPatch, patch_send_handler: FakeSendHandler,
    status: int, codes: dict[str, int | str], expected: str,
) -> None:
    """Replay the QQ member-detail dual-code response at the real Service/HTTP boundary."""
    requests: list[httpx.Request] = []
    logger = Mock()
    monkeypatch.setattr(bridge, "logger", logger)

    def respond(request: httpx.Request) -> httpx.Response:
        """Supply only a local, redacted fixture matching the real response shape."""
        requests.append(request)
        return httpx.Response(status, json={**codes, "message": "private-error-canary"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = QQBotGroupMembersService(plugin=make_plugin(http_client=client))
        result = await service.get_member("fixture-group", "fixture-member")

    assert result == {"success": False, "data": None, "error": expected}
    assert [(req.method, req.url.path) for req in requests] == [
        ("GET", "/v2/groups/fixture-group/members/fixture-member"),
    ]
    assert not patch_send_handler.posts
    assert "private-error-canary" not in str(result) + str(logger.mock_calls)


@pytest.mark.parametrize("consumer", ["share_tool", "blacklist"])
async def test_public_consumers_do_not_return_business_error_bodies(
    consumer: str, patch_send_handler: FakeSendHandler,
) -> None:
    """A business failure cannot masquerade as a link or blacklist success."""
    patch_send_handler.post_result = {
        "err_code": 11253, "message": "private-error-canary token=synthetic",
    }
    plugin = make_plugin()
    if consumer == "share_tool":
        result = await QQGenerateShareLinkTool(plugin=plugin).execute("source")
        assert result == (False, "无权限调用该接口")
    else:
        result = await QQBotGroupMembersService(plugin=plugin).update_blacklist(
            "group", "add", ["member"],
        )
        assert result == {"success": False, "data": None, "error": "无权限调用该接口"}
    assert "private-error-canary" not in str(result)
    assert len(patch_send_handler.posts) == 1


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


@pytest.mark.parametrize(("payload", "expected"), [
    (None, None), ([], None), ({}, None), ({"code": ""}, None),
    ({"err_code": None, "code": 0}, None),
    ({"code": False}, "调用失败"), ({"err_code": 0.0}, "调用失败"),
    ({"code": {"secret": "canary"}}, "调用失败"),
    ({"code": "9" * 5000}, "调用失败"),
    ({"code": "private-error-canary"}, "调用失败"),
    ({"err_code": "000", "code": 11253}, "无权限调用该接口"),
    ({"err_code": 40103011}, "机器人管理端已关闭移除群成员操作权限"),
    ({"err_code": 40103012}, "群主已关闭移除群成员操作权限"),
])
def test_business_code_shape_and_mixed_fields(
    payload: Any, expected: str | None,
) -> None:
    """Unknown or malformed explicit error codes never expose their raw value."""
    from ..src.errors import api_business_error

    assert api_business_error(payload) == expected
