"""用户查询依赖日志的任务隔离、嵌套、热重载与真实协议诊断验证。"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import json
import logging
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpcore
import httpx
import pytest
from hpack import Decoder, Encoder
from httpcore._trace import Trace
from hyperframe.frame import DataFrame, Frame, HeadersFrame, SettingsFrame

from src.app.plugin_system.api import adapter_api

from .. import plugin as plugin_module
from ..config import QQBotExpandConfig
from ..plugin import QQBotExpandPlugin
from ..services.user_info_service import QQBotUserInfoService
from ..src import http_diagnostics
from ..src.http_diagnostics import SharedHTTPClient, user_info_http_diagnostics
from .test_user_info_service import (
    _DIAGNOSTIC_LOGGERS,
    assert_private_diagnostics_redacted,
    diagnostic_logging_state,
    manager as manager,
    start,
)
from src.kernel.concurrency import TaskManager


@pytest.mark.parametrize("name", _DIAGNOSTIC_LOGGERS)
async def test_dependency_records_preserve_events_without_sensitive_payloads(
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    """每个明确依赖日志器都有摘要；消息/参数/异常/缓存字段均不保留秘密。"""
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(name)
    before = diagnostic_logging_state()
    with user_info_http_diagnostics():
        try:
            raise ValueError("exception-canary https://private.invalid")
        except ValueError:
            logger.error("request %s", "token-canary", exc_info=True, stack_info=True)
    assert_private_diagnostics_redacted(caplog)
    records = [record for record in caplog.records if record.name == name]
    assert len(records) == 1 and records[0].levelno == logging.ERROR
    assert records[0].exc_info is records[0].exc_text is records[0].stack_info is None
    assert records[0].args == ()
    assert diagnostic_logging_state() == before


async def test_actual_httpcore_trace_and_hpack_headers_are_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """运行真实 httpcore Trace 与 HTTP/2 HPACK 编解码，不只手工模拟日志文本。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    with user_info_http_diagnostics():
        async with Trace(
            "receive_response_headers",
            logging.getLogger("httpcore.http2"),
            kwargs={"request": "https://private.invalid/openid-canary"},
        ) as trace:
            trace.return_value = [
                (b"location", b"https://avatar.invalid/avatar-canary")
            ]
        encoder = Encoder()
        block = encoder.encode(
            [
                (b":path", b"/v2/users/openid-canary/info"),
                (b"authorization", b"QQBot token-canary"),
            ]
        )
        decoder = Decoder()
        assert decoder.decode(block) == [
            (":path", "/v2/users/openid-canary/info"),
            ("authorization", "QQBot token-canary"),
        ]
        encoder.header_table_size = 0
    assert_private_diagnostics_redacted(caplog)
    assert any(record.name == "hpack.hpack" for record in caplog.records)
    assert any(record.name == "hpack.table" for record in caplog.records)
    assert diagnostic_logging_state() == before


async def test_nested_scopes_install_once_and_restore_outer_owner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """嵌套退出不提前移除 hook 或清空外层 ContextVar；无关 logger 原样输出。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    with user_info_http_diagnostics():
        active = diagnostic_logging_state()
        for name in _DIAGNOSTIC_LOGGERS:
            assert len(active[name][0]) == len(before[name][0]) + 1
        with user_info_http_diagnostics():
            assert diagnostic_logging_state() == active
            logging.getLogger("httpx").info("inner-openid-canary")
        assert diagnostic_logging_state() == active
        logging.getLogger("httpx").info("outer-openid-canary")
        logging.getLogger("unrelated.application").info("unrelated message retained")
    assert diagnostic_logging_state() == before
    assert_private_diagnostics_redacted(caplog)
    assert "unrelated message retained" in caplog.text
    assert len([record for record in caplog.records if record.name == "httpx"]) == 2


async def test_child_task_and_thread_with_inherited_context_are_not_redacted(
    caplog: pytest.LogCaptureFixture,
    manager: TaskManager,
) -> None:
    """ContextVar 会被任务/线程复制，任务身份校验防止无关诊断被误脱敏。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    release = asyncio.Event()
    child_entered = asyncio.Event()

    async def child() -> None:
        """由父请求创建但语义独立的任务应保留自己的诊断。"""
        logging.getLogger("httpx").info("child unrelated before release")
        child_entered.set()
        await release.wait()
        logging.getLogger("httpx").info("child unrelated after scope")

    def thread_work() -> None:
        """to_thread 传播 ContextVar，但没有事件循环/当前任务。"""
        logging.getLogger("httpx").info("thread unrelated request")

    with user_info_http_diagnostics():
        task = start(manager, child())
        await asyncio.wait_for(child_entered.wait(), timeout=3)
        await asyncio.to_thread(thread_work)
        logging.getLogger("httpx").info("parent-openid-canary")
    release.set()
    await asyncio.wait_for(task, timeout=3)
    assert_private_diagnostics_redacted(caplog)
    for text in (
        "child unrelated before release",
        "child unrelated after scope",
        "thread unrelated request",
    ):
        assert text in caplog.text
    assert diagnostic_logging_state() == before


async def test_copied_module_generation_removes_its_own_filters(
    caplog: pytest.LogCaptureFixture,
    manager: TaskManager,
) -> None:
    """模拟卸载并重新导入：旧请求仍在运行，新代退出不能删除旧代 hook。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    old_entered = asyncio.Event()
    old_release = asyncio.Event()

    async def old_request() -> None:
        """旧代请求在模块卸载后继续拥有自己的上下文和过滤器。"""
        with user_info_http_diagnostics():
            old_entered.set()
            await old_release.wait()
            logging.getLogger("httpx").info("old-openid-canary")

    old_task = start(manager, old_request())
    await asyncio.wait_for(old_entered.wait(), timeout=3)
    old_active = diagnostic_logging_state()
    name = "_qqbot_user_info_http_diagnostics_reload_test"
    spec = importlib.util.spec_from_file_location(name, Path(http_diagnostics.__file__))
    assert spec is not None and spec.loader is not None
    new_module = importlib.util.module_from_spec(spec)
    sys.modules[name] = new_module
    try:
        spec.loader.exec_module(new_module)
        # 导入本身不安装 hook。
        assert diagnostic_logging_state() == old_active
        with new_module.user_info_http_diagnostics():
            new_active = diagnostic_logging_state()
            for key in _DIAGNOSTIC_LOGGERS:
                assert len(new_active[key][0]) == len(before[key][0]) + 2
            # 模块缓存删除不应破坏正在运行的 scope 的 finally。
            del sys.modules[name]
            logging.getLogger("httpx").info("new-openid-canary")
        assert diagnostic_logging_state() == old_active
        old_release.set()
        await asyncio.wait_for(old_task, timeout=3)
    finally:
        sys.modules.pop(name, None)
        old_release.set()
        await asyncio.wait_for(old_task, timeout=3)
    assert diagnostic_logging_state() == before
    assert_private_diagnostics_redacted(caplog)
    logging.getLogger("httpx").info("after module unload retained")
    assert "after module unload retained" in caplog.text


async def test_existing_filters_and_record_formatting_remain_compatible(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """不移除别人的 Filter；旧格式化缓存也不能残留个人数据。"""
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("httpx")
    seen: list[logging.LogRecord] = []

    class ExistingFilter(logging.Filter):
        """模拟宿主已安装的观察器与格式化缓存。"""

        def filter(self, record: logging.LogRecord) -> bool:
            """保留原有 Filter 语义。"""
            record.message = record.getMessage()
            record.exc_text = "exception-cache-canary"
            seen.append(record)
            return True

    existing = ExistingFilter()
    logger.addFilter(existing)
    try:
        before = diagnostic_logging_state()
        with user_info_http_diagnostics():
            logger.info("cached-openid-canary")
        assert diagnostic_logging_state() == before and len(seen) == 1
        assert_private_diagnostics_redacted(caplog)
    finally:
        logger.removeFilter(existing)


def test_non_async_entry_cannot_leave_hooks_installed() -> None:
    """不支持的无事件循环调用在安装前失败，不遗留全局 Filter。"""
    before = diagnostic_logging_state()
    with pytest.raises(RuntimeError, match="no running event loop"):
        with user_info_http_diagnostics():
            pytest.fail("必须在异步任务中进入")
    assert diagnostic_logging_state() == before


class MemoryHTTP2Stream(httpcore.AsyncNetworkStream):
    """沿用 verifier 的内存帧重现：仅替换网络流，客户端协议栈全部真实执行。"""

    def __init__(self, *, first_status: int = 200, pause: bool = False) -> None:
        """预编码服务器响应，避免模拟服务器 HPACK 日志污染客户端捕获。"""
        self.pending = SettingsFrame(0).serialize()
        self.buffer = b""
        self.header_blocks = {
            status: Encoder().encode(
                [
                    (b":status", str(status).encode()),
                    (b"content-type", b"application/json"),
                ]
            )
            for status in (200, first_status)
        }
        self.body = json.dumps(
            {
                "username": "synthetic",
                "avatar": "avatar",
                "allow_proactive_msg": False,
            }
        ).encode()
        self.first_status = first_status
        self.pause = pause
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.requests: list[bytes] = []

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """仅模拟 TLS 的 ALPN 结果，无真实网络/证书/凭据。"""
        return self

    def get_extra_info(self, key: str) -> Any:
        """让真实 httpcore 按协商结果选择 HTTP/2。"""
        if key == "ssl_object":
            return SimpleNamespace(selected_alpn_protocol=lambda: "h2")
        return None

    async def aclose(self) -> None:
        """记录真实客户端关闭是否到达网络边界。"""
        self.closed = True

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """只在预先定义的请求边界暂停，取消测试不使用固定 sleep。"""
        if self.pause and self.requests:
            self.entered.set()
            await self.release.wait()
        assert self.pending, "unexpected read without synthetic HTTP/2 frames"
        data, self.pending = self.pending[:max_bytes], self.pending[max_bytes:]
        return data

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """解析真实 h2 输出的帧并提供合法响应，不改写请求/压缩状态。"""
        if buffer.startswith(b"PRI * HTTP/2.0"):
            buffer = buffer[24:]
        self.buffer += buffer
        while len(self.buffer) >= 9:
            frame, length = Frame.parse_frame_header(memoryview(self.buffer[:9]))
            if len(self.buffer) < 9 + length:
                break
            frame.parse_body(memoryview(self.buffer[9 : 9 + length]))
            self.buffer = self.buffer[9 + length :]
            if isinstance(frame, HeadersFrame):
                self.requests.append(frame.data)
                response = HeadersFrame(frame.stream_id)
                status = self.first_status if len(self.requests) == 1 else 200
                response.data = self.header_blocks[status]
                response.flags.add("END_HEADERS")
                body = DataFrame(frame.stream_id)
                body.data = self.body
                body.flags.add("END_STREAM")
                self.pending += response.serialize() + body.serialize()


class MemoryHTTP2Backend(httpcore.AsyncNetworkBackend):
    """替换连接边界，计数证明后续普通请求复用同一 HTTP/2 连接。"""

    def __init__(self, stream: MemoryHTTP2Stream) -> None:
        """保存唯一内存连接。"""
        self.stream = stream
        self.connects = 0

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        """拒绝任何真实外部连接，只返回内存流。"""
        self.connects += 1
        return self.stream


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
@pytest.mark.parametrize("outcome", [200, 401, 429, "cancel"])
@pytest.mark.parametrize("close_mode", ["direct", "unload"])
async def test_real_shared_http2_delayed_evictions_are_private_until_close(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    manager: TaskManager,
    level: int,
    outcome: int | str,
    close_mode: str,
) -> None:
    """真实生产客户端→httpcore→h2→HPACK：scope 后淘汰仍安全，关闭立即移除 hook。"""
    stream = MemoryHTTP2Stream(
        first_status=outcome if isinstance(outcome, int) else 200,
        pause=outcome == "cancel",
    )
    backend = MemoryHTTP2Backend(stream)

    async def token() -> str:
        """公共认证替身，无真实 token。"""
        return "offline-token-delayed-canary"

    monkeypatch.setattr(
        adapter_api,
        "get_adapter",
        lambda signature: SimpleNamespace(
            send_handler=SimpleNamespace(
                base_url="https://api.bot.qq.com", get_token=token
            ),
        ),
    )
    config = QQBotExpandConfig()
    config.http.http2 = True
    config.interaction.enabled = False
    plugin = QQBotExpandPlugin(config)
    before = diagnostic_logging_state()
    await plugin.on_plugin_loaded()
    client = plugin.http_client
    assert client is not None
    # 唯一协议替身是网络后端；真实生产共享客户端/池/HPACK 状态不替换。
    client._transport._pool._network_backend = backend
    try:
        caplog.set_level(level)
        task = start(
            manager,
            QQBotUserInfoService(plugin=plugin).get_user_info("openid-delayed-canary"),
        )
        if outcome == "cancel":
            await asyncio.wait_for(stream.entered.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            stream.release.set()
        else:
            result = await asyncio.wait_for(task, timeout=3)
            assert result["success"] is (outcome == 200)
        assert_private_diagnostics_redacted(caplog)
        caplog.clear()
        for number in range(70):
            response = await client.get(
                f"https://api.bot.qq.com/v2/groups/ordinary-{number}/info",
                headers={"x-ordinary-padding": f"ordinary-{number}" + "x" * 100},
            )
            assert response.status_code == 200 and response.http_version == "HTTP/2"
        assert backend.connects == 1 and len(stream.requests) == 71
        assert (
            "HTTP Request: GET https://api.bot.qq.com/v2/groups/ordinary-69/info"
            in caplog.text
        )
        for record in caplog.records:
            assert "canary" not in str(vars(record))
        if level == logging.DEBUG:
            assert "sensitive header eviction" in caplog.text
            assert "Evicting b'x-ordinary-padding'" in caplog.text
        if close_mode == "direct":
            await client.aclose()
        else:
            await plugin.on_plugin_unloaded()
        assert client.is_closed and stream.closed
        assert diagnostic_logging_state() == before
        # 验证真实编码请求的内容未被脱敏 Filter 改写；模拟服务器解码不计入客户端日志。
        with caplog.at_level(logging.WARNING):
            request_headers = Decoder().decode(stream.requests[0])
        assert (":path", "/v2/users/openid-delayed-canary/info") in request_headers
        assert (
            "authorization",
            "QQBot offline-token-delayed-canary",
        ) in request_headers
    finally:
        await plugin.on_plugin_unloaded()


@pytest.mark.parametrize(
    "sensitive_name", [b":path", b"authorization", b"proxy-authorization"]
)
async def test_lifetime_filter_redacts_only_sensitive_hpack_evictions(
    caplog: pytest.LogCaptureFixture,
    sensitive_name: bytes,
) -> None:
    """真实 HPACK 动态表在请求 scope 之后淘汰；非敏感 header 日志仍保留。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    async with SharedHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    ) as client:
        with user_info_http_diagnostics():
            encoder = Encoder()
            value = (
                b"/v2/users/secret-openid-canary/info"
                if sensitive_name == b":path"
                else b"secret-token-canary"
            )
            encoder.encode([(sensitive_name, value), (b"x-normal", b"normal-value")])
        caplog.clear()
        encoder.header_table_size = 32
        assert "sensitive header eviction" in caplog.text
        assert "Evicting b'x-normal': b'normal-value'" in caplog.text
        assert all("canary" not in str(vars(record)) for record in caplog.records)
        # 精确脱敏不改真实客户端，也不影响同生命周期中的普通请求。
        response = await client.get("https://unrelated.invalid/plain")
        assert response.status_code == 204
        assert "HTTP Request: GET https://unrelated.invalid/plain" in caplog.text
    assert diagnostic_logging_state() == before


@pytest.mark.parametrize(
    ("message", "args"),
    [
        ("other diagnostic %s", ("value",)),
        ("Evicting %s: %s from the header table", {"key": "value"}),
        ("Evicting %s: %s from the header table", (b":path",)),
        ("Evicting %s: %s from the header table", (b":path", 12)),
        (
            "Evicting %s: %s from the header table",
            (b":path", b"/v2/groups/normal/info"),
        ),
        (
            "Evicting %s: %s from the header table",
            (b":path", b"/v2/users/normal/messages"),
        ),
    ],
)
async def test_lifetime_filter_does_not_rewrite_unrelated_or_unknown_records(
    message: str,
    args: Any,
) -> None:
    """不认识的日志参数形状保留给原日志系统处理，不擅自吞记录或改值。"""
    before = diagnostic_logging_state()
    async with SharedHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    ):
        installed = [
            item
            for item in logging.getLogger("hpack.table").filters
            if item not in before["hpack.table"][0]
        ]
        assert len(installed) == 1
        record = logging.LogRecord(
            "hpack.table", logging.DEBUG, __file__, 1, message, (), None
        )
        record.args = args
        initial = vars(record).copy()
        assert installed[0].filter(record) is True
        assert vars(record) == initial
    assert diagnostic_logging_state() == before


class ClosingTransport(httpx.AsyncBaseTransport):
    """只控制关闭边界，不替换被测客户端的构造、关闭、Filter 或 finalizer。"""

    def __init__(self, outcome: str) -> None:
        """准备可观测关闭屏障。"""
        self.outcome = outcome
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_calls = 0
        with user_info_http_diagnostics():
            self.encoder = Encoder()
            self.encoder.encode([(b":path", b"/v2/users/closing-canary/info")])

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """真实 httpx 收发边界的离线响应。"""
        return httpx.Response(204)

    async def aclose(self) -> None:
        """模拟底层池成功、错误或被调用方取消，不使用固定 sleep。"""
        self.close_calls += 1
        self.entered.set()
        if self.outcome == "cancel":
            await self.release.wait()
        if self.outcome == "error":
            raise RuntimeError("transport close failed")
        self.encoder.header_table_size = 32
        self.closed.set()


@pytest.mark.parametrize("mode", ["direct", "context"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_client_close_releases_lifetime_filter_on_every_exit(
    manager: TaskManager,
    caplog: pytest.LogCaptureFixture,
    mode: str,
    outcome: str,
) -> None:
    """取消不终止池清理，关闭错误可显式重试；只在真实关闭成功后移除 guard。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    transport = ClosingTransport(outcome)
    client = SharedHTTPClient(transport=transport)
    try:
        if mode == "context":
            await client.__aenter__()
        response = await client.get("https://unrelated.invalid/close-test")
        assert response.status_code == 204
        close = client.aclose() if mode == "direct" else client.__aexit__()
        task = start(manager, close)
        await asyncio.wait_for(transport.entered.wait(), timeout=3)
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not transport.closed.is_set()
            assert diagnostic_logging_state() != before
            transport.release.set()
            await client.aclose()
            assert transport.close_calls == 1
        elif outcome == "error":
            with pytest.raises(RuntimeError, match="transport close failed"):
                await task
            assert not transport.closed.is_set()
            assert diagnostic_logging_state() != before
            transport.outcome = "success"
            await client.aclose()
            assert transport.close_calls == 2
        else:
            await task
        assert transport.closed.is_set() and client.is_closed
        assert diagnostic_logging_state() == before
        assert "sensitive header eviction" in caplog.text
        assert "canary" not in caplog.text
        calls = transport.close_calls
        await client.aclose()
        assert transport.close_calls == calls
        assert diagnostic_logging_state() == before
    finally:
        transport.outcome = "success"
        transport.release.set()
        await client.aclose()


async def test_cancelled_owned_close_task_keeps_guard_until_explicit_retry(
    monkeypatch: pytest.MonkeyPatch,
    manager: TaskManager,
) -> None:
    """框架直接取消清理任务不同于调用方取消；保留 guard，允许后续显式清理。"""
    monkeypatch.setattr(http_diagnostics, "get_task_manager", lambda: manager)
    before = diagnostic_logging_state()
    transport = ClosingTransport("cancel")
    client = SharedHTTPClient(transport=transport)
    waiter = start(manager, client.aclose())
    await asyncio.wait_for(transport.entered.wait(), timeout=3)
    owned = [
        info
        for info in manager.get_active_tasks()
        if info.name == "qqbot_expand_close_shared_http"
    ]
    assert len(owned) == 1
    owned[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert diagnostic_logging_state() != before and not transport.closed.is_set()
    transport.release.set()
    await client.aclose()
    assert transport.closed.is_set() and transport.close_calls == 2
    assert diagnostic_logging_state() == before


async def test_concurrent_close_waiters_share_one_cleanup_task(
    monkeypatch: pytest.MonkeyPatch,
    manager: TaskManager,
) -> None:
    """并发 aclose/上下文退出共享同一底层清理，不因一个调用方取消而丢保护。"""
    monkeypatch.setattr(http_diagnostics, "get_task_manager", lambda: manager)
    before = diagnostic_logging_state()
    transport = ClosingTransport("cancel")
    client = SharedHTTPClient(transport=transport)
    first = start(manager, client.aclose())
    await asyncio.wait_for(transport.entered.wait(), timeout=3)
    second_entered = asyncio.Event()

    async def second_close() -> None:
        """进入第二个关闭调用，并以 Task 屏障确认其已参与等待。"""
        second_entered.set()
        await client.__aexit__()

    second = start(manager, second_close())
    await asyncio.wait_for(second_entered.wait(), timeout=3)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done() and diagnostic_logging_state() != before
    assert transport.close_calls == 1
    transport.release.set()
    await asyncio.wait_for(second, timeout=3)
    assert diagnostic_logging_state() == before and transport.closed.is_set()


async def test_multiple_clients_and_new_module_generation_keep_independent_leases(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """同代多 client 只一组 hook；新旧代关闭只清理自身，不留永久日志策略。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    first = SharedHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    )
    second = SharedHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    )
    old_active = diagnostic_logging_state()
    assert len(old_active["hpack.table"][0]) == len(before["hpack.table"][0]) + 1
    name = "_qqbot_hpack_lifetime_reload_test"
    spec = importlib.util.spec_from_file_location(name, Path(http_diagnostics.__file__))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        assert diagnostic_logging_state() == old_active
        async with module.SharedHTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(204))
        ):
            assert (
                len(logging.getLogger("hpack.table").filters)
                == len(before["hpack.table"][0]) + 2
            )
            del sys.modules[name]
            await first.aclose()
            assert (
                len(logging.getLogger("hpack.table").filters)
                == len(before["hpack.table"][0]) + 2
            )
        assert diagnostic_logging_state() == old_active
        with user_info_http_diagnostics():
            encoder = Encoder()
            encoder.encode([(b":path", b"/v2/users/old-generation-canary/info")])
        caplog.clear()
        encoder.header_table_size = 32
        assert (
            "sensitive header eviction" in caplog.text and "canary" not in caplog.text
        )
        await second.aclose()
    finally:
        sys.modules.pop(name, None)
        await first.aclose()
        await second.aclose()
    assert diagnostic_logging_state() == before


def test_failed_client_construction_and_abandoned_unopened_client_leave_no_hook() -> (
    None
):
    """真实 httpx 参数构造失败时未安装；无人持有的未打开 client 也能释放租约。"""
    before = diagnostic_logging_state()
    with pytest.raises(TypeError):
        SharedHTTPClient(base_url=object())
    assert diagnostic_logging_state() == before
    client = SharedHTTPClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    )
    assert diagnostic_logging_state() != before
    del client
    gc.collect()
    assert diagnostic_logging_state() == before


@pytest.mark.parametrize("outcome", ["later_constructor_error", "load_cancelled"])
async def test_failed_plugin_load_releases_created_client_lease(
    monkeypatch: pytest.MonkeyPatch,
    manager: TaskManager,
    outcome: str,
) -> None:
    """HTTP 客户端已构造后其他资源失败或加载取消，不依赖 GC/后续卸载清理。"""
    before = diagnostic_logging_state()
    config = QQBotExpandConfig()
    plugin = QQBotExpandPlugin(config)
    clients: list[httpx.AsyncClient] = []
    entered, release = asyncio.Event(), asyncio.Event()

    def reject_material(**kwargs: Any) -> None:
        """仅替换另一资源的构造边界，被测共享客户端和钩子均真实执行。"""
        assert plugin.http_client is not None
        clients.append(plugin.http_client)
        raise RuntimeError("material constructor failed")

    async def hold_schedule() -> bool:
        """暂停最后一个加载 await，使用同步屏障精确取消。"""
        assert plugin.http_client is not None
        clients.append(plugin.http_client)
        entered.set()
        await release.wait()
        return False

    try:
        if outcome == "later_constructor_error":
            config.features.enable_material_service = True
            monkeypatch.setattr(plugin_module, "MaterialUploadClient", reject_material)
            with pytest.raises(RuntimeError, match="material constructor failed"):
                await plugin.on_plugin_loaded()
        else:
            monkeypatch.setattr(plugin.managed_panel_runtime, "schedule", hold_schedule)
            task = start(manager, plugin.on_plugin_loaded())
            await asyncio.wait_for(entered.wait(), timeout=3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(clients) == 1 and clients[0].is_closed
        assert plugin.http_client is None
        assert diagnostic_logging_state() == before
    finally:
        release.set()
        await plugin.on_plugin_unloaded()


async def test_plugin_reload_closes_old_client_and_releases_old_lease() -> None:
    """同实例重载不丢弃旧连接池，旧客户端关闭后仍只保留新客户端租约。"""
    before = diagnostic_logging_state()
    config = QQBotExpandConfig()
    config.interaction.enabled = False
    plugin = QQBotExpandPlugin(config)
    try:
        await plugin.on_plugin_loaded()
        first = plugin.http_client
        assert isinstance(first, SharedHTTPClient)
        active = diagnostic_logging_state()
        await plugin.on_plugin_loaded()
        second = plugin.http_client
        assert isinstance(second, SharedHTTPClient) and second is not first
        assert first.is_closed and not second.is_closed
        assert diagnostic_logging_state() == active
    finally:
        await plugin.on_plugin_unloaded()
    assert diagnostic_logging_state() == before
