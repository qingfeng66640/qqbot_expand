"""补齐 QQ bridge 与辅助 Service 的输入、传输和去重边界。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from src.app.plugin_system.api import adapter_api
from src.kernel.concurrency import get_task_manager

from ..services.group_info_service import QQBotGroupInfoService
from ..services.interaction_service import QQBotInteractionService
from ..services.message_service import QQBotMessageService
from ..services.raw_service import QQBotRawService
from ..services.utility_service import QQBotUtilityService
from ..src.bridge import ADAPTER_SIGNATURE, encode_path_segment
from .conftest import FakeHttpClient, FakeResponse, FakeSendHandler, make_plugin


@pytest.fixture
def adapter_boundary(monkeypatch: pytest.MonkeyPatch) -> FakeSendHandler:
    """替换适配器公共查找边界，真实执行 bridge 解析但永不连接 QQ。"""
    handler = FakeSendHandler(base_url="https://sandbox.api.sgroup.qq.com")

    def get_adapter(signature: str) -> SimpleNamespace:
        """只接受生产 bridge 使用的已知适配器签名。"""
        assert signature == ADAPTER_SIGNATURE
        return SimpleNamespace(send_handler=handler)

    monkeypatch.setattr(adapter_api, "get_adapter", get_adapter)
    return handler


async def test_group_read_cancelled_after_headers_is_not_retried_and_next_read_recovers(
    adapter_boundary: FakeSendHandler,
) -> None:
    """J05：通过真实 Service→bridge→HTTPX 在首个 200 的响应体边界取消。"""
    headers_received = asyncio.Event()
    response_closed = asyncio.Event()
    requests: list[tuple[str, str]] = []

    class InFlightBody(httpx.AsyncByteStream):
        """让调用停在 200 响应头之后、应用拿到响应体之前。"""

        async def __aiter__(self) -> AsyncIterator[bytes]:
            """阻塞正文，直到调用者取消。"""
            headers_received.set()
            await asyncio.Event().wait()
            yield b'{}'

        async def aclose(self) -> None:
            """记下 HTTPX 已回收首个响应。"""
            response_closed.set()

    async def respond(request: httpx.Request) -> httpx.Response:
        """首个请求挂起于响应体，此后同一 URL 返回正常 JSON。"""
        requests.append((request.method, request.url.path))
        if len(requests) == 1:
            return httpx.Response(200, stream=InFlightBody())
        return httpx.Response(200, json={"group_name": "Recovered"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = QQBotGroupInfoService(make_plugin(http_client=client))
        task = get_task_manager().create_task(
            service.get_group_info("fixture-group"), name="j05-cancelled-group-read",
        ).task
        assert task is not None
        try:
            await asyncio.wait_for(headers_received.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert response_closed.is_set()
        assert requests == [("GET", "/v2/groups/fixture-group/info")]
        assert await service.get_group_info("fixture-group") == {
            "success": True, "data": {"group_name": "Recovered"}, "error": None,
        }
    assert requests == [("GET", "/v2/groups/fixture-group/info")] * 2
    assert not adapter_boundary.posts


@pytest.mark.parametrize("value", [None, 1, "", " \t "])
def test_path_segments_reject_empty_or_non_string_values(value: Any) -> None:
    """外部目标必须是非空字符串，错误返回不带可拼接的路径片段。"""
    assert encode_path_segment(value, "target_id") == ("target_id 不能为空", "")


@pytest.mark.parametrize(
    ("target_type", "target_id", "message_id", "expected"),
    [
        ("channel", "group-1", "message-1", "target_type"),
        ("group", "", "message-1", "target_id"),
        ("group", "group-1", " ", "message_id"),
        ("user", None, "message-1", "target_id"),
    ],
)
async def test_recall_rejects_invalid_target_before_claim_or_network(
    adapter_boundary: FakeSendHandler,
    target_type: str,
    target_id: Any,
    message_id: str,
    expected: str,
) -> None:
    """非法目标不能消耗合法消息归属记录，也不能触达传输层。"""
    client = FakeHttpClient()
    plugin = make_plugin(http_client=client)
    plugin.sent_messages.record("message-1", "group", "group-1")
    result = await QQBotUtilityService(plugin).recall_message(target_type, target_id, message_id)
    assert result["success"] is False
    assert expected in result["error"]
    assert client.calls == []
    assert adapter_boundary.posts == []
    assert plugin.sent_messages.claim("message-1", "group", "group-1")


async def test_recall_uses_encoded_user_target_and_consumes_record_on_failure(
    adapter_boundary: FakeSendHandler,
) -> None:
    """单聊撤回编码路径；HTTP 失败也不重用已消费记录重复撤回。"""
    client = FakeHttpClient([FakeResponse(403, {"code": 403, "message": "denied"})])
    plugin = make_plugin(http_client=client)
    plugin.sent_messages.record("message/1", "user", "user/1")
    service = QQBotUtilityService(plugin)
    first = await service.recall_message("user", "user/1", "message/1")
    second = await service.recall_message("user", "user/1", "message/1")
    assert not first["success"]
    assert not second["success"]
    assert "本插件" in second["error"]
    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "DELETE"
    assert client.calls[0]["url"].endswith("/v2/users/user%2F1/messages/message%2F1")
    assert adapter_boundary.posts == []


@pytest.mark.parametrize(("url_link", "callback_data"), [(None, ""), ("", 12)])
async def test_share_rejects_non_string_inputs_without_network(
    adapter_boundary: FakeSendHandler, url_link: Any, callback_data: Any
) -> None:
    """分享链接的两个可选字段也必须满足字符串契约。"""
    client = FakeHttpClient()
    result = await QQBotUtilityService(make_plugin(http_client=client)).generate_share_link(
        url_link=url_link, callback_data=callback_data
    )
    assert result["success"] is False
    assert "字符串" in result["error"]
    assert adapter_boundary.posts == []
    assert client.calls == []


@pytest.mark.parametrize(
    ("url_link", "callback_data", "body"),
    [
        ("", "source", {"callback_data": "source"}),
        ("", "", {}),
        ("", "c" * 32, {"callback_data": "c" * 32}),
    ],
)
async def test_share_optional_fields_are_normalized_at_boundary(
    adapter_boundary: FakeSendHandler,
    url_link: str,
    callback_data: str,
    body: dict[str, str],
) -> None:
    """仅下发文档列出的回传标识，省略空字段并接受恰好 32 字符。"""
    adapter_boundary.post_result = {"url": "https://example.invalid/generated"}
    result = await QQBotUtilityService(make_plugin()).generate_share_link(
        url_link=url_link, callback_data=callback_data
    )
    assert result["success"] is True
    assert result["data"] == adapter_boundary.post_result
    assert len(adapter_boundary.posts) == 1
    assert adapter_boundary.posts[0][0].endswith("/v2/generate_url_link")
    assert adapter_boundary.posts[0][2] == body


async def test_message_send_without_optional_registry_returns_platform_ids(
    adapter_boundary: FakeSendHandler,
) -> None:
    """兼容未提供归属 registry 的宿主，仍返回 QQ 消息 ID 与引用索引。"""
    plugin = make_plugin()
    del plugin.sent_messages
    adapter_boundary.post_result = {"id": "sent-1", "ext_info": {"ref_idx": " ref-1 "}}
    result = await QQBotMessageService(plugin).send_text("user", "user-1", "hello")
    assert result == {
        "success": True, "message_id": "sent-1", "ref_idx": "ref-1", "error": None
    }
    assert len(adapter_boundary.posts) == 1
    assert adapter_boundary.posts[0][0].endswith("/v2/users/user-1/messages")


@pytest.mark.parametrize("method", [" get ", "PATCH", "DELETE"])
async def test_raw_redirect_is_failure_and_never_replayed_or_followed(
    adapter_boundary: FakeSendHandler, method: str
) -> None:
    """使用 httpx MockTransport 验证 3xx 不能被误判成功或跳转到第三方。"""
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        """模拟非空响应也不应发生的外域跳转。"""
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://outside.invalid/token"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        plugin = make_plugin(http_client=client)
        plugin.config.http.retry_max_attempts = 3
        result = await QQBotRawService(plugin).request(
            method, "/users/@me", query={"cursor": "a/b", "unset": None}
        )
    assert result["success"] is False
    assert len(requests) == 1
    assert requests[0].method == method.strip().upper()
    assert requests[0].url.host == "sandbox.api.sgroup.qq.com"
    assert requests[0].url.params == httpx.QueryParams({"cursor": "a/b"})
    assert requests[0].headers["Authorization"] == "QQBot fake-token"
    assert adapter_boundary.posts == []


@pytest.mark.parametrize(
    ("settings", "method", "path", "error_fragment"),
    [
        ({"allow_raw_request": False}, "GET", "/users/@me", "关闭"),
        ({"raw_allowed_methods": ["get"]}, "DELETE", "/users/@me", "白名单"),
        ({}, "TRACE", "/users/@me", "method"),
        ({}, "GET", "https://outside.invalid/secret", "相对路径"),
        ({}, "GET", "//outside.invalid/secret", "//"),
        ({}, "GET", "/v2/../secret", "路径穿越"),
    ],
)
async def test_raw_authorization_and_paths_fail_before_adapter_lookup(
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, Any],
    method: str,
    path: str,
    error_fragment: str,
) -> None:
    """raw 开关、方法白名单及路径校验都在获取 token 之前完成。"""
    lookup = Mock(side_effect=AssertionError("must not access adapter"))
    monkeypatch.setattr(adapter_api, "get_adapter", lookup)
    result = await QQBotRawService(make_plugin(**settings)).request(method, path)
    assert result["success"] is False
    assert error_fragment in result["error"]
    lookup.assert_not_called()


async def test_ack_network_failure_is_once_only_across_service_instances(
    adapter_boundary: FakeSendHandler,
) -> None:
    """失败 ACK 不重试；同一插件的另一个 Service 也只能得到重复应答结果。"""
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        """模拟已经可能发出请求后的网络异常，不建立 socket。"""
        requests.append(request)
        raise httpx.ReadTimeout("uncertain ACK result", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        plugin = make_plugin(http_client=client)
        plugin.config.http.retry_max_attempts = 3
        first = await QQBotInteractionService(plugin).ack(" interaction-1 ", 0)
        second = await QQBotInteractionService(plugin).ack("interaction-1", 0)
        assert first["success"] is False
        assert first["duplicate"] is False
        assert second["success"] is True
        assert second["duplicate"] is True
        assert len(requests) == 1
        assert requests[0].method == "PUT"
        assert requests[0].url == "https://api.bot.qq.com/interactions/interaction-1"
        assert requests[0].content == b'{"code":0}'
        assert adapter_boundary.posts == []
        await plugin.interaction_runtime.close()
