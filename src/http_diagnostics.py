"""仅在用户查询 HTTP 调用所属任务内，将依赖诊断替换为固定安全摘要。

httpx INFO 包含完整 URL；httpcore DEBUG 包含头和异常 repr；HTTP/2 的
hpack DEBUG 可能包含认证头。请求期日志仅在当前任务内脱敏；HPACK 动态表
淘汰日志的精确值脱敏则跟随共享客户端直到关闭，不保存 openid 或认证值。
不改级别、处理器、连接池参数或真实请求；客户端及在途请求/响应释放后不保留 hook。
"""

from __future__ import annotations

import logging
import re
from asyncio import Task, current_task, shield
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from threading import RLock
from types import TracebackType
from typing import Any, cast
from weakref import finalize

import httpx

from src.kernel.concurrency import get_task_manager

__all__ = ["SharedHTTPClient", "user_info_http_diagnostics"]

_HTTP_LOGGERS = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
    "hpack.hpack",
    "hpack.table",
)


def _redact(record: logging.LogRecord, message: str) -> None:
    """替换诊断值并清除格式化缓存，不调用可能泄露秘密的原始 __str__/repr。"""
    record.msg = message
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None
    record.__dict__.pop("message", None)


class _HeaderTableDiagnostics(logging.Filter):
    """只脱敏 HPACK 延迟淘汰的用户 info 路径/认证值，不保存具体值。"""

    def __init__(self) -> None:
        """一代模块共享一个精确 Filter，安装期由仍存活的客户端租约决定。"""
        super().__init__()
        self._logger = logging.getLogger("hpack.table")
        self._lock = RLock()
        self._users = 0

    def filter(self, record: logging.LogRecord) -> bool:
        """普通淘汰/调整容量日志原样保留，敏感值仅以固定摘要代替。"""
        if (
            record.msg != "Evicting %s: %s from the header table"
            or not isinstance(record.args, tuple)
            or len(record.args) != 2
        ):
            return True
        name, value = record.args
        if name in (b"authorization", b"proxy-authorization") or (
            name == b":path"
            and isinstance(value, bytes)
            and re.fullmatch(rb"/v2/users/[^/?#]+/info", value)
        ):
            _redact(
                record, "QQ user info HTTP sensitive header eviction (value redacted)"
            )
        return True

    @contextmanager
    def lease(self) -> Iterator[None]:
        """同代多个客户端共享安装，最后一个释放时只移除自己的 Filter。"""
        with self._lock:
            if self._users == 0:
                self._logger.addFilter(self)
            self._users += 1
        try:
            yield
        finally:
            with self._lock:
                self._users -= 1
                if self._users == 0:
                    self._logger.removeFilter(self)


_header_table_diagnostics = _HeaderTableDiagnostics()


class _LeasedResponseStream(httpx.AsyncByteStream):
    """Keep the request lease until deferred body/connection cleanup has finished."""

    def __init__(
        self, stream: httpx.AsyncByteStream, lease: AbstractContextManager[None],
    ) -> None:
        """Transfer an already entered request lease without storing HTTP data."""
        self._stream = stream
        self._release = finalize(self, lease.__exit__, None, None, None)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Delegate streaming bytes and exceptions without modifying the response."""
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        """Only release after successful cleanup; a failed close can be retried."""
        if self._release.alive:
            await self._stream.aclose()
            self._release()


class SharedHTTPClient(httpx.AsyncClient):
    """插件自持共享客户端：保留 httpx 行为，仅拥有 HPACK 诊断的生命周期。

    必须由插件创建并关闭；测试/嵌入者若注入裸 AsyncClient，仅有请求期保护，
    不具有本客户端的延迟 HPACK 保护。不猴子补丁宿主或第三方对象。
    """

    def __init__(self, **kwargs: Any) -> None:
        """原样传递 httpx 参数；构造成功后才安装不含个人信息的诊断租约。"""
        super().__init__(**kwargs)
        lease = _header_table_diagnostics.lease()
        lease.__enter__()
        # 回调不捕获 self；已关闭、重复关闭或被 GC 时都只释放一次。
        self._release_diagnostics = finalize(self, lease.__exit__, None, None, None)
        self._close_task: Task[None] | None = None

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        """Protect assigned requests independently of the pool's close bookkeeping.

        A pool may forget connections before a failed close; queued requests still
        own their HTTP/2 state. Streaming responses retain this lease until their
        actual stream closes, not merely until response headers arrive.
        """
        lease = _header_table_diagnostics.lease()
        lease.__enter__()
        try:
            response = await super().send(request, **kwargs)
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        if response.is_closed:
            lease.__exit__(None, None, None)
        else:
            response.stream = _LeasedResponseStream(cast(httpx.AsyncByteStream, response.stream), lease)
        return response

    async def aclose(self) -> None:
        """关闭共享池；调用方取消不终止清理，失败保留保护并允许显式重试。"""
        await self.__aexit__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        """两种关闭入口共享 TaskManager 任务，底层池关闭成功才释放 guard。"""
        if not self._release_diagnostics.alive:
            return
        if self._close_task is None or self._close_task.done():
            self._close_task = cast(
                Task[None],
                get_task_manager()
                .create_task(
                    self._close_transports(exc_type, exc_value, traceback),
                    name="qqbot_expand_close_shared_http",
                )
                .task,
            )
            self._close_task.add_done_callback(_observe_close_result)
        await shield(self._close_task)

    async def _close_transports(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """HTTPX 公共上下文退出会重试关闭池，不被 CLOSED 标记提前短路。"""
        await super().__aexit__(exc_type, exc_value, traceback)
        self._release_diagnostics()


def _observe_close_result(task: Task[None]) -> None:
    """调用方取消后仍取出后台异常，避免未读取异常日志泄露底层信息。"""
    if not task.cancelled():
        task.exception()


class _RequestDiagnostics(logging.Filter):
    """一代模块拥有一组可恢复的 hook，多个请求共享安装但隔离任务上下文。"""

    def __init__(self) -> None:
        """不在导入时安装 hook；只保存日志器引用和非敏感的任务归属。"""
        super().__init__()
        self._owner: ContextVar[Task[Any] | None] = ContextVar(
            "qqbot_user_info_http_task", default=None
        )
        self._lock = RLock()
        self._users = 0
        self._loggers = tuple(logging.getLogger(name) for name in _HTTP_LOGGERS)
        self._summary_logger = logging.getLogger("qqbot_expand.user_info.http")

    def filter(self, record: logging.LogRecord) -> bool:
        """保留事件及级别，仅替换当前请求任务的敏感参数、消息和异常。"""
        owner = self._owner.get()
        if owner is None:
            return True
        try:
            task = current_task()
        except RuntimeError:
            # to_thread 会复制 ContextVar，但线程并非这个 HTTP 请求任务。
            return True
        if task is not owner:
            # 创建子任务也会继承 ContextVar，不应误处理子任务的无关日志。
            return True
        _redact(record, "QQ user info HTTP diagnostic (sensitive details redacted)")
        return True

    @contextmanager
    def scope(self) -> Iterator[None]:
        """引用计数避免嵌套/并发重复安装，所有退出路径均恢复并释放 hook。"""
        token = self._owner.set(current_task())
        with self._lock:
            if self._users == 0:
                for logger in self._loggers:
                    logger.addFilter(self)
            self._users += 1
        try:
            yield
        finally:
            self._owner.reset(token)
            with self._lock:
                self._users -= 1
                if self._users == 0:
                    for logger in self._loggers:
                        logger.removeFilter(self)
            # 即使 HTTP 在生成 httpx INFO 前失败/取消，仍保留一次安全诊断。
            self._summary_logger.info("QQ user info HTTP attempt ended")


_diagnostics = _RequestDiagnostics()


def user_info_http_diagnostics() -> AbstractContextManager[None]:
    """绑定当前代实例，模块被卸载/重新导入也由旧作用域自行清理旧 hook。

    必须从异步任务进入，并且只包裹本次 client.request 的 await。
    不包含认证、限流等待、响应解析，也不永久改变第三方日志策略。
    """
    return _diagnostics.scope()
