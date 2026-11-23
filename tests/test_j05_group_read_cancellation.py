"""离线验证群只读请求在响应头之后取消和恢复。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from src.kernel.concurrency import get_task_manager

from ..services.group_info_service import QQBotGroupInfoService
from .conftest import FakeSendHandler, make_plugin


async def test_group_read_cancelled_after_headers_is_not_retried_and_next_read_recovers(
    patch_send_handler: FakeSendHandler,
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
    assert not patch_send_handler.posts
