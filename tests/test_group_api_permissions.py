"""Business failures in successful HTTP envelopes must not leak into Tools."""
from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from ..services.group_members_service import QQBotGroupMembersService
from ..src import bridge
from ..src.bridge import api_request
from .conftest import FakeHttpClient, FakeResponse, FakeSendHandler, make_plugin


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


@pytest.mark.parametrize("code,expected", [
    (40103011, "机器人"), (40103012, "群主"),
])
@pytest.mark.parametrize("field", ["code", "err_code"])
@pytest.mark.parametrize("transport", ["adapter", "http", "http_error", "adapter_error"])
async def test_current_removal_permission_codes_are_not_token_errors(
    code: int, expected: str, field: str, transport: str,
    patch_send_handler: FakeSendHandler,
) -> None:
    """New documented operation switches are distinct from HTTP authentication."""
    payload = {field: str(code), "message": "secret-canary token=synthetic"}
    client = FakeHttpClient([FakeResponse(400 if transport == "http_error" else 200, payload)])
    patch_send_handler.post_result = payload
    if transport == "adapter_error":
        request = httpx.Request("POST", "https://api.bot.qq.com/synthetic")
        patch_send_handler.post_result = httpx.HTTPStatusError(
            "secret-canary", request=request, response=httpx.Response(400, request=request, json=payload),
        )
    result = await api_request(
        make_plugin(http_client=client), "POST", "/v2/groups/group/batch_remove_members",
        {"member_openids": ["member"]}, retry_network_errors=transport in {"adapter", "adapter_error"},
    )
    assert result["success"] is False
    assert expected in result["error"]
    assert "token" not in result["error"]
    assert "secret-canary" not in str(result)
