"""In-flight requests retain privacy protection after a partial transport close."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
import httpx
import pytest
from hpack import Encoder

from src.kernel.concurrency import TaskManager

from ..src.http_diagnostics import SharedHTTPClient, user_info_http_diagnostics
from .test_user_info_service import diagnostic_logging_state, manager as manager, start


class PartialCloseTransport(httpx.AsyncBaseTransport):
    """Model a pool that drops its connection list before a close I/O failure."""

    def __init__(self) -> None:
        """Initialize deterministic request and cleanup barriers."""
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_calls = 0
        with user_info_http_diagnostics():
            self.encoder = Encoder()
            self.encoder.encode([(b":path", b"/v2/users/inflight-canary/info")])

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """An already assigned request may resume even after a pool close retry."""
        self.entered.set()
        await self.release.wait()
        self.encoder.header_table_size = 32
        return httpx.Response(200, content=b"completed")

    async def aclose(self) -> None:
        """First close fails; the emptied pool then reports success."""
        self.close_calls += 1
        if self.close_calls == 1:
            raise OSError("synthetic close failure")


async def test_inflight_send_retains_guard_after_partial_pool_close_retry(
    manager: TaskManager, caplog: pytest.LogCaptureFixture,
) -> None:
    """Do not equate pool cleanup return with all assigned requests having ended."""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    transport = PartialCloseTransport()
    client = SharedHTTPClient(transport=transport)
    request = start(manager, client.get("https://api.bot.qq.com/ordinary"))
    try:
        await asyncio.wait_for(transport.entered.wait(), 3)
        with pytest.raises(OSError, match="synthetic close failure"):
            await client.aclose()
        await client.aclose()
        assert diagnostic_logging_state() != before
        transport.release.set()
        assert (await asyncio.wait_for(request, 3)).content == b"completed"
        assert "inflight-canary" not in caplog.text
        assert "sensitive header eviction" in caplog.text
        assert diagnostic_logging_state() == before
    finally:
        transport.release.set()
        await asyncio.gather(request, return_exceptions=True)
        await client.aclose()


class DeferredStream(httpx.AsyncByteStream):
    """Keep a streamed response alive after its originating client is closed."""

    def __init__(self, outcome: str) -> None:
        """Prepare body iteration and explicit close failure modes."""
        self.outcome = outcome
        self.close_calls = 0
        with user_info_http_diagnostics():
            self.encoder = Encoder()
            self.encoder.encode([(b":path", b"/v2/users/stream-canary/info")])

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Exercise a genuine HPACK eviction during deferred body consumption."""
        self.encoder.header_table_size = 32
        if self.outcome == "read_error":
            raise httpx.ReadError("synthetic read failure")
        yield b"streamed"

    async def aclose(self) -> None:
        """A failed/cancelled close must retain its response-owned lease."""
        self.close_calls += 1
        if self.outcome == "close_error":
            raise OSError("synthetic stream close failure")
        if self.outcome == "close_cancel":
            raise asyncio.CancelledError


@pytest.mark.parametrize("outcome", ["success", "read_error", "close_error", "close_cancel"])
async def test_stream_lifetime_keeps_guard_until_real_stream_close(
    outcome: str, caplog: pytest.LogCaptureFixture,
) -> None:
    """Read/close/error preserve HTTPX semantics without dropping privacy early."""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    stream = DeferredStream(outcome)
    async with SharedHTTPClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream),
    )) as client:
        response = await client.send(client.build_request("GET", "https://api.bot.qq.com/plain"), stream=True)
    try:
        assert diagnostic_logging_state() != before
        if outcome == "read_error":
            with pytest.raises(httpx.ReadError):
                await response.aread()
            await response.aclose()
        elif outcome in {"close_error", "close_cancel"}:
            error: type[BaseException] = OSError if outcome == "close_error" else asyncio.CancelledError
            with pytest.raises(error):
                await response.aclose()
            assert diagnostic_logging_state() != before
            stream.outcome = "success"
            # HTTPX marks Response closed before awaiting its stream; retry the owned stream.
            await response.stream.aclose()
        else:
            assert await response.aread() == b"streamed"
        assert "stream-canary" not in caplog.text
        assert diagnostic_logging_state() == before
    finally:
        stream.outcome = "success"
        await response.stream.aclose()


@pytest.mark.parametrize("failure", [httpx.ConnectError, asyncio.CancelledError])
async def test_send_failure_restores_request_lease(
    failure: type[BaseException],
) -> None:
    """A failed request returns its lease but not the still-open client's lease."""
    before = diagnostic_logging_state()

    def respond(request: httpx.Request) -> httpx.Response:
        """Raise only at the network boundary, not inside the lease implementation."""
        raise failure("synthetic")

    async with SharedHTTPClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(failure):
            await client.get("https://api.bot.qq.com/plain")
        assert diagnostic_logging_state() != before
    assert diagnostic_logging_state() == before
