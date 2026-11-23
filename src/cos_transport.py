"""COS 预签名上传的共享 URL、DNS、TLS 与请求诊断边界。"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import ssl
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx


class CosTransportError(ValueError):
    """COS 传输只报告本地安全错误，不暴露签名。"""


_COS_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cos\."
    r"(?:ap|eu|na|sa|me|af)-[a-z]{2,24}(?:-[a-z0-9]{1,12})?\.myqcloud\.com\Z"
)
# The public acceleration endpoint requires a single BucketName-APPID label.
_COS_ACCELERATE_HOST = re.compile(
    r"(?=[a-z0-9-]{1,63}\.)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-[0-9]+\.cos\.accelerate\.myqcloud\.com\Z"
)


def validate_cos_host(host: str) -> None:
    """仅接受 COS 地域或官方公网全球加速域名，拒绝自定义及内网域名。"""
    if not (_COS_HOST.fullmatch(host) or _COS_ACCELERATE_HOST.fullmatch(host)):
        raise CosTransportError("上传地址不是允许的 COS 公网域名")


def validate_presigned_url(value: Any) -> str:
    """验证 HTTPS/443 与严格域名边界；DNS 校验在实际连接时完成。"""
    if (not isinstance(value, str) or not 1 <= len(value) <= 8192
            or not value.isascii() or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise CosTransportError("prepare 上传地址不合法")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or not parsed.hostname
                or parsed.netloc not in (parsed.hostname, f"{parsed.hostname}:443")
                or not parsed.path.startswith("/")):
            raise ValueError
        validate_cos_host(parsed.hostname)
    except ValueError:
        raise CosTransportError("prepare 上传地址未通过 HTTPS/COS 安全校验") from None
    return value


COS_CONCURRENCY = 2
_PRIVATE_HTTP: ContextVar[bool] = ContextVar("qqbot_cos_private_http", default=False)
_HTTP_LOGGERS = ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy")


class _PrivateHTTPFilter(logging.Filter):
    """只屏蔽当前 COS 操作的 HTTP 日志，不改变其他任务的日志级别。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """避免 httpx URL 和 httpcore 响应头 trace 泄漏预签名地址。"""
        return not _PRIVATE_HTTP.get()


def _public_address(value: str) -> str:
    """拒绝所有非公网、保留、组播以及可嵌入私网 IPv4 的转换地址。"""
    try:
        if "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError:
        raise CosTransportError("COS DNS 返回无效地址") from None
    if (not address.is_global or address.is_reserved or address.is_multicast
            or address.is_loopback or address.is_link_local or address.is_unspecified
            or (isinstance(address, ipaddress.IPv6Address)
                and (address.ipv4_mapped or address.sixtofour or address.teredo
                     or address.is_site_local))):
        raise CosTransportError("COS DNS 返回非公网地址")
    return str(address)


class _ClosingTLSStream(httpcore.AsyncNetworkStream):
    """确保 TLS 握手取消时也关闭已建立的 TCP 流（不吞掉取消）。"""

    def __init__(self, stream: httpcore.AsyncNetworkStream) -> None:
        """包装已校验并绑定公网地址的连接。"""
        self._stream = stream

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """委托读取，后续请求取消由 httpcore 回收响应连接。"""
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """委托发送二进制 HTTP 数据。"""
        await self._stream.write(buffer, timeout)

    async def aclose(self) -> None:
        """关闭当前 TCP/TLS 流。"""
        await self._stream.aclose()

    async def start_tls(
        self, ssl_context: ssl.SSLContext, server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """保持原域名校验和 SNI，在 BaseException（包括取消）时释放 TCP。"""
        try:
            stream = await self._stream.start_tls(ssl_context, server_hostname, timeout)
        except BaseException:
            await self._stream.aclose()
            raise
        return _ClosingTLSStream(stream)

    def get_extra_info(self, info: str) -> Any:
        """保留 peer 与 TLS 对象等 httpcore 所需元数据。"""
        return self._stream.get_extra_info(info)


class _PublicCOSBackend(httpcore.AsyncNetworkBackend):
    """校验 DNS 后只连接选定数值 IP，原域名由 httpcore 保留用于 TLS/SNI。"""

    def __init__(self) -> None:
        """复用公开 AnyIO 网络后端，不修改 httpx/httpcore 私有属性。"""
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """只解析一次且校验全部结果，连接过程绝不重新解析 COS 主机。"""
        validate_cos_host(host)
        if port != 443 or local_address is not None:
            raise CosTransportError("COS 连接参数不合法")
        async with asyncio.timeout(timeout):
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
            )
            if not addresses:
                raise CosTransportError("COS DNS 未返回地址")
            validated = [_public_address(str(item[4][0])) for item in addresses]
            selected = validated[0]
            stream = await self._backend.connect_tcp(
                selected, port, timeout=timeout, socket_options=socket_options,
            )
            # AnyIO 对数值 IP 不调用域名解析。再核对 peer，防止连接后端替换目标。
            try:
                peer = stream.get_extra_info("server_addr")
                if not peer or _public_address(str(peer[0])) != selected:
                    raise CosTransportError("COS 连接目标与校验地址不一致")
            except BaseException:
                await stream.aclose()
                raise
            return _ClosingTLSStream(stream)


class _CosTransport(httpx.AsyncBaseTransport):
    """专用于预签名 PUT 的 COS 传输，只保留响应 ETag。"""

    def __init__(self) -> None:
        """不继承代理、证书环境变量、默认头、cookie 或任何 QQ 客户端。"""
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=_PublicCOSBackend(), proxy=None,
            max_connections=COS_CONCURRENCY, max_keepalive_connections=0,
            http2=False, retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """只发一次 PUT，不跟随 Location，不下载可能无界的错误响应体。"""
        if request.method != "PUT":
            raise CosTransportError("COS 上传传输仅允许 PUT")
        validate_presigned_url(str(request.url))
        response = await self._pool.handle_async_request(httpcore.Request(
            method="PUT",
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        ))
        try:
            headers = [(name, value) for name, value in response.headers if name.lower() == b"etag"]
            return httpx.Response(response.status, headers=headers, content=b"")
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        """关闭连接池，取消时 httpcore 同样负责回收连接。"""
        await self._pool.aclose()


def create_cos_client(*, request_timeout: float, connect_timeout: float) -> httpx.AsyncClient:
    """创建无代理、无环境配置、无默认鉴权且不跟随重定向的 COS 客户端。"""
    return httpx.AsyncClient(
        transport=_CosTransport(), trust_env=False, auth=None, follow_redirects=False,
        timeout=httpx.Timeout(request_timeout, connect=connect_timeout),
    )


@contextmanager
def private_cos_diagnostics() -> Iterator[None]:
    """仅屏蔽当前 COS 操作的依赖日志，退出时恢复日志过滤器。"""
    loggers = tuple(logging.getLogger(name) for name in _HTTP_LOGGERS)
    log_filter = _PrivateHTTPFilter()
    for logger in loggers:
        logger.addFilter(log_filter)
    token = _PRIVATE_HTTP.set(True)
    try:
        yield
    finally:
        _PRIVATE_HTTP.reset(token)
        for logger in loggers:
            logger.removeFilter(log_filter)
