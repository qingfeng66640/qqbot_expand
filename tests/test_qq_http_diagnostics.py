"""QQ HTTP 错误诊断只记录白名单字段的回归。"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from ..src import bridge, errors
from .conftest import FakeHttpClient, FakeSendHandler, make_plugin


@pytest.mark.parametrize("code", ["30001", "40030001"])
def test_qq_parameter_codes_are_classified_without_http_status(code: str) -> None:
    """QQ 的短业务码与完整业务码都归类为参数错误。"""
    assert errors.sanitize_error(code) == errors.ERROR_BAD_REQUEST


@pytest.mark.parametrize("method", ["POST", "PUT"])
async def test_http_failure_logs_only_allowlisted_diagnostics(
    method: str,
    patch_send_handler: FakeSendHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保留状态、业务码和 trace，不记录正文、请求 URL 或原始异常。"""
    trace_id = "b35c7d6b3c4127908f254f49cc5289ba"
    payload = {
        "code": 30001, "err_code": 40030001, "trace_id": trace_id,
        "message": "secret-from-server", "access_token": "secret-from-body",
    }
    response = httpx.Response(
        400, json=payload,
        request=httpx.Request(method, "https://api.bot.qq.com/v2/panels?token=secret-in-url"),
    )
    if method == "POST":
        patch_send_handler.post_result = httpx.HTTPStatusError(
            "secret-from-exception", request=response.request, response=response
        )
    client = FakeHttpClient([response])
    messages: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", messages.append)

    result = await bridge.api_request(
        make_plugin(http_client=client), method, "/v2/panels", {"private": "request-secret"}
    )

    assert result == {"success": False, "data": None, "error": errors.ERROR_BAD_REQUEST}
    assert len(messages) == 1
    diagnostic = messages[0]
    for expected in ["status=400", "code=30001", "err_code=40030001", f"trace_id={trace_id}"]:
        assert expected in diagnostic
    for secret in ["secret", "api.bot.qq.com", "参数错误"]:
        assert secret not in diagnostic
    assert len(patch_send_handler.posts) == (1 if method == "POST" else 0)
    assert len(client.calls) == (0 if method == "POST" else 1)


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize("payload", [None, [], {"code": True, "err_code": "token=secret", "trace_id": "bad\nsecret"}])
async def test_malformed_error_body_does_not_break_or_leak(
    method: str,
    payload: Any,
    patch_send_handler: FakeSendHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 JSON、非对象或恶意诊断字段不能破坏失败路径或污染日志。"""
    response = httpx.Response(
        400, text="secret-not-json" if payload is None else None,
        json=payload if payload is not None else None,
        request=httpx.Request(method, "https://api.bot.qq.com/v2/panels"),
    )
    if method == "POST":
        patch_send_handler.post_result = httpx.HTTPStatusError(
            "secret-exception", request=response.request, response=response
        )
    messages: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", messages.append)
    result = await bridge.api_request(
        make_plugin(http_client=FakeHttpClient([response])), method, "/v2/panels", {}
    )
    assert result["error"] == errors.ERROR_BAD_REQUEST
    assert len(messages) == 1
    assert "status=400" in messages[0]
    assert all(value not in messages[0] for value in ["secret", "True", "bad", "api.bot.qq.com"])


async def test_business_failure_is_classified_and_logged_without_raw_body(
    patch_send_handler: FakeSendHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 成功但携带业务错误时也不丢业务码，不透传错误正文。"""
    patch_send_handler.post_result = {
        "code": 30001, "err_code": 40030001,
        "message": "server-secret", "trace_id": "a" * 32,
    }
    messages: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", messages.append)
    result = await bridge.api_request(make_plugin(), "POST", "/v2/panels", {})
    assert result["error"] == errors.ERROR_BAD_REQUEST
    assert "code=30001" in messages[0]
    assert "err_code=40030001" in messages[0]
    assert "trace_id=" + "a" * 32 in messages[0]
    assert "server-secret" not in messages[0]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"code": "30001", "err_code": "40030001"}, "code=30001 err_code=40030001"),
        ({"code": -1, "err_code": "1" * 11, "trace_id": "secret"}, ""),
        ({"code": True, "err_code": 1.5, "message": "secret"}, ""),
        ({"trace_id": "A" * 32}, "trace_id=" + "A" * 32),
        ({}, ""),
    ],
)
def test_diagnostic_fields_are_bounded_and_allowlisted(
    payload: dict[str, Any], expected: str,
) -> None:
    """只允许受限的数字和十六进制标识符进入诊断日志。"""
    assert errors.format_api_error_diagnostics(payload) == expected


@pytest.mark.parametrize("method", ["POST", "GET"])
@pytest.mark.parametrize("token_failure", [True, False])
async def test_token_and_network_failures_do_not_log_original_exception(
    method: str,
    token_failure: bool,
    patch_send_handler: FakeSendHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有响应体的错误也保留分类，且不把原始异常带入日志。"""
    if token_failure:
        monkeypatch.setattr(
            patch_send_handler, "get_token",
            AsyncMock(side_effect=RuntimeError("401 secret-token")),
        )
    else:
        patch_send_handler.post_result = httpx.ConnectError("connection secret-network")
    client = FakeHttpClient([
        httpx.ConnectError("connection secret-network"),
        httpx.ConnectError("connection secret-network"),
    ])
    messages: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", messages.append)
    result = await bridge.api_request(make_plugin(http_client=client), method, "/x", {})
    assert result["error"] == (errors.ERROR_TOKEN if token_failure else errors.ERROR_NETWORK)
    assert messages and all("secret" not in message for message in messages)


@pytest.mark.parametrize("method", ["POST", "PUT"])
async def test_deeply_nested_error_json_keeps_failure_contract(
    method: str,
    patch_send_handler: FakeSendHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """诊断解析遇到递归深度错误时不能中断真实 HTTP 失败返回。"""
    response = httpx.Response(
        400,
        content=("[" * 1200 + '"secret-deep-body"' + "]" * 1200).encode(),
        request=httpx.Request(method, "https://api.bot.qq.com/v2/panels"),
    )
    if method == "POST":
        patch_send_handler.post_result = httpx.HTTPStatusError(
            "secret-exception", request=response.request, response=response
        )
    messages: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", messages.append)
    result = await bridge.api_request(
        make_plugin(http_client=FakeHttpClient([response])), method, "/v2/panels", {}
    )
    assert result == {"success": False, "data": None, "error": errors.ERROR_BAD_REQUEST}
    assert len(messages) == 1 and "status=400" in messages[0]
    assert "secret" not in messages[0]
