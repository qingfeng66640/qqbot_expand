"""素材二进制上传的 URL/SSRF、DNS 绑定、TLS、私密日志与 ETag 回归。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import ssl
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from ..src import cos_transport, material_policy, material_upload
from ..src.cos_transport import CosTransportError
from ..src.material_policy import (
    MaterialError,
    image_payload,
    normalize_etag,
    parse_prepared,
    validate_presigned_url,
)
from .test_material_service import HOST, SECRET_URL, image_bytes, prepared

PUBLIC_IP = "1.1.1.1"
ACCEL_HOST = "test-bucket-1250000000.cos.accelerate.myqcloud.com"


def address_info(address: str) -> tuple[Any, ...]:
    """生成 getaddrinfo 的单个记录。"""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))


class RecordingStream(httpcore.AsyncNetworkStream):
    """运行真实 httpcore HTTP/1.1 协议处理的离线网络流。"""

    def __init__(self, response: bytes, peer: str = PUBLIC_IP) -> None:
        """记录二进制写入、TLS 参数与连接关闭。"""
        self.response = response
        self.peer = peer
        self.writes: list[bytes] = []
        self.tls: tuple[ssl.SSLContext, str | None] | None = None
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """一次返回响应头，不返回真实外部数据。"""
        result, self.response = self.response, b""
        return result

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """保留真实编码后的 HTTP 字节。"""
        self.writes.append(buffer)

    async def aclose(self) -> None:
        """标记流已回收。"""
        self.closed = True

    async def start_tls(
        self, ssl_context: ssl.SSLContext, server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """只记录参数；生产 TLS 验证由 AnyIO TLSStream 执行。"""
        self.tls = (ssl_context, server_hostname)
        return self

    def get_extra_info(self, info: str) -> Any:
        """提供与生产 AnyIO 后端相同的 peer 字段。"""
        return (self.peer, 443) if info == "server_addr" else None


@pytest.mark.parametrize("url", [
    "http://" + HOST + "/a", "https://example.com/a", "https://examplecos.evil/a",
    "https://" + HOST + ".evil.example/a", "https://evil.myqcloud.com/a",
    "https://x.cos.ap-guangzhou.myqcloud.com.evil/a", "https://x.cos.myqcloud.com/a",
    "https://x.cos-internal.ap-guangzhou.myqcloud.com/a", "https://x.cos.accelerate.myqcloud.com/a",
    "https://x-1250000000.cos-internal.accelerate.tencentcos.cn/a",
    "https://" + ACCEL_HOST + ".example.invalid/a", "https://" + ACCEL_HOST + ":8443/a",
    "https://user@" + ACCEL_HOST + "/a", "https://" + ACCEL_HOST + "/a#fragment",
    "https://a.b-1250000000.cos.accelerate.myqcloud.com/a",
    "https://" + "x" * 62 + "-1.cos.accelerate.myqcloud.com/a",
    "https://x.cos..myqcloud.com/a", "https://x.cos.ap-guangzhou.myqcloud.com./a",
    "https://a.b.cos.ap-guangzhou.myqcloud.com/a", "https://127.0.0.1/a",
    "https://[::1]/a", "https://2130706433/a", "https://localhost/a",
    "https://user:secret@" + HOST + "/a", "https://" + HOST + ":8443/a",
    "https://" + HOST + ":/a", "https://" + HOST + "/a#fragment",
    "https://" + HOST + "/a\n?secret", " https://" + HOST + "/a",
    "https://" + HOST + "\\@evil.example/a", "https://%62ucket.cos.ap-guangzhou.myqcloud.com/a",
    "https://ｂucket.cos.ap-guangzhou.myqcloud.com/a", "https://" + HOST,
    "https://[invalid/a", "https://" + HOST + "/a\x7f", None, 123,
])
def test_rejects_unsafe_urls(url: Any) -> None:
    """任何不符合受控 COS HTTPS 边界的地址都不能发网。"""
    with pytest.raises(MaterialError):
        validate_presigned_url(url)


@pytest.mark.parametrize("url", [
    SECRET_URL, "https://" + HOST + ":443/a?sign=1",
    "https://bucket.cos.eu-frankfurt.myqcloud.com/object",
    "https://bucket.cos.ap-shanghai-fsi.myqcloud.com/object",
    "https://" + ACCEL_HOST + "/object?sig=opaque", "https://" + ACCEL_HOST + ":443/object",
])
def test_allows_cos_host_boundary(url: str) -> None:
    """仅接受地域和官方公网加速域名，保持原始 URL 不变。"""
    assert validate_presigned_url(url) == url


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "192.0.2.1", "198.18.0.1", "224.0.0.1", "240.0.0.1",
    "::1", "::", "fe80::1", "fc00::1", "ff02::1", "2001:db8::1",
    "::ffff:127.0.0.1", "::ffff:8.8.8.8", "2002:7f00:1::", "not-an-ip",
])
async def test_rejects_resolved_non_public_addresses(
    monkeypatch: pytest.MonkeyPatch, address: str,
) -> None:
    """DNS 混合公网/非公网记录也整体拒绝，不尝试连接其中任何地址。"""
    resolve = AsyncMock(return_value=[address_info(PUBLIC_IP), address_info(address)])
    connect = AsyncMock()
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: SimpleNamespace(connect_tcp=connect))
    with pytest.raises(CosTransportError):
        await cos_transport._PublicCOSBackend().connect_tcp(HOST, 443, timeout=1)
    connect.assert_not_awaited()


@pytest.mark.parametrize("host", [HOST, ACCEL_HOST])
async def test_pin_public_ip_keep_tls_hostname_and_isolate_headers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, host: str,
) -> None:
    """真实协议层连接数值公网地址，SNI/证书校验仍用 COS 主机且无签名日志。"""
    content = image_bytes()
    digest = hashlib.md5(content).hexdigest()
    response = (
        f'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nETag: "{digest}"\r\n'
        f'Set-Cookie: private={SECRET_URL}\r\nX-Debug: {SECRET_URL}\r\n\r\n'
    ).encode()
    stream = RecordingStream(response)
    connect = AsyncMock(return_value=stream)
    resolve = AsyncMock(side_effect=[[address_info(PUBLIC_IP)], [address_info("127.0.0.1")]])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: SimpleNamespace(connect_tcp=connect))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("SSL_CERT_FILE", "must-not-inherit-certificate-file")
    client = material_upload.MaterialUploadClient(request_timeout=1, connect_timeout=1)
    client._client.headers["Authorization"] = "QQBot MUST-NOT-SEND"
    client._client.headers["X-Default-Secret"] = "MUST-NOT-SEND"
    client._client.cookies.set("session", "MUST-NOT-SEND", domain=host)
    client._client.auth = ("MUST-NOT-SEND", "MUST-NOT-SEND")
    ticket = parse_prepared({**prepared(), "presigned_url": SECRET_URL.replace(HOST, host)})
    caplog.set_level(logging.DEBUG)
    try:
        assert await client.put_prepared(ticket, content, "image/png", digest) == digest
        assert resolve.await_count == 1
        assert connect.await_args.args == (PUBLIC_IP, 443)
        assert stream.tls is not None
        context, hostname = stream.tls
        assert hostname == host
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        sent = b"".join(stream.writes)
        assert f"Host: {host}".encode() in sent
        assert b"Content-Type: image/png" in sent
        assert b"MUST-NOT-SEND" not in sent
        assert b"Authorization:" not in sent
        assert b"Cookie:" not in sent
        assert content in sent
        assert client._client.trust_env is False
        assert not client._client.follow_redirects
        assert not client._client.cookies
        assert "DO-NOT-LEAK" not in caplog.text
        assert "private-object" not in caplog.text
        # 第一次连接已经绑定公网 IP，DNS 后续变化只能导致下一次连接被拒绝。
        with pytest.raises(MaterialError):
            await client.put_prepared(ticket, content, "image/png", digest)
        assert connect.await_count == 1
        logging.getLogger("httpx").info("unrelated-log-kept")
        assert "unrelated-log-kept" in caplog.text
    finally:
        await client.aclose()
    assert stream.closed


async def test_peer_address_must_match_pinned_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """底层连接异常替换目标时关闭连接而非继续 TLS/PUT。"""
    stream = RecordingStream(b"", peer="8.8.8.8")
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[address_info(PUBLIC_IP)]))
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: SimpleNamespace(connect_tcp=AsyncMock(return_value=stream)))
    with pytest.raises(CosTransportError):
        await cos_transport._PublicCOSBackend().connect_tcp(HOST, 443, timeout=1)
    assert stream.closed
    assert stream.tls is None


@pytest.mark.parametrize("addresses", [[], [address_info("2001:4860:4860::8888")]])
async def test_dns_empty_or_public_ipv6(monkeypatch: pytest.MonkeyPatch, addresses: list[Any]) -> None:
    """无 DNS 记录失败；正常公网 IPv6 也采用数值地址绑定。"""
    stream = RecordingStream(b"", peer="2001:4860:4860::8888")
    connect = AsyncMock(return_value=stream)
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=addresses))
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: SimpleNamespace(connect_tcp=connect))
    backend = cos_transport._PublicCOSBackend()
    if not addresses:
        with pytest.raises(CosTransportError):
            await backend.connect_tcp(HOST, 443, timeout=1)
        connect.assert_not_awaited()
    else:
        connection = await backend.connect_tcp(HOST, 443, timeout=1)
        assert connection.get_extra_info("server_addr") == (stream.peer, 443)
        assert connect.await_args.args[0] == "2001:4860:4860::8888"


@pytest.mark.parametrize("port", [80, 444])
async def test_backend_rejects_nonstandard_port(port: int) -> None:
    """连接后端自身也执行标准端口限制。"""
    with pytest.raises(CosTransportError):
        await cos_transport._PublicCOSBackend().connect_tcp(HOST, port)


@pytest.mark.parametrize("etag", ["", "x", 'W/"' + "a" * 32 + '"', "a" * 32 + "-2", '"' + "b" * 32 + '"'])
def test_etag_missing_weak_multipart_or_mismatch(etag: str) -> None:
    """只接受完整文件 MD5，而非弱 ETag 或 multipart ETag。"""
    with pytest.raises(MaterialError):
        normalize_etag(etag, "a" * 32)


@pytest.mark.parametrize("etag", ["a" * 32, '"' + "a" * 32 + '"', ' "' + "A" * 32 + '" '])
def test_etag_normalization(etag: str) -> None:
    """大小写与正常引号可规范化，但不改变内容摘要。"""
    assert normalize_etag(etag, "a" * 32) == "a" * 32


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 400, 403, 500])
async def test_no_redirect_no_retry(
    monkeypatch: pytest.MonkeyPatch, status: int, caplog: pytest.LogCaptureFixture,
) -> None:
    """错误及 Location 响应均只发送一次，不记录 URL 或响应正文。"""
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        """返回诱导跳转到私网的响应。"""
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://127.0.0.1/"}, text=SECRET_URL)

    monkeypatch.setattr(cos_transport, "_CosTransport", lambda: httpx.MockTransport(reply))
    client = material_upload.MaterialUploadClient(request_timeout=1, connect_timeout=1)
    caplog.set_level(logging.DEBUG)
    try:
        with pytest.raises(MaterialError):
            await client.put_prepared(parse_prepared(prepared()), b"content", "image/png", "a" * 32)
        assert len(calls) == 1
        assert "DO-NOT-LEAK" not in caplog.text
    finally:
        await client.aclose()


async def test_put_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT 总时限覆盖阻塞响应，不依赖每次 read 的超时刷新。"""
    async def wait_forever(request: httpx.Request) -> httpx.Response:
        """仅等待取消，不发出任何网络请求。"""
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(cos_transport, "_CosTransport", lambda: httpx.MockTransport(wait_forever))
    client = material_upload.MaterialUploadClient(request_timeout=0.01, connect_timeout=0.01)
    try:
        with pytest.raises(TimeoutError):
            await client.put_prepared(parse_prepared(prepared()), b"content", "image/png", "a" * 32)
    finally:
        await client.aclose()


def test_pixel_budget_and_safe_prepared_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """压缩炸弹限制在网络前执行，票据 repr 不含预签名 URL。"""
    monkeypatch.setattr(material_policy, "MAX_DECODED_PIXELS", 1)
    with pytest.raises(MaterialError):
        image_payload("a.png", image_bytes(), "image/png")
    assert SECRET_URL not in repr(parse_prepared(prepared()))


@pytest.mark.parametrize("error", [asyncio.CancelledError(), ssl.SSLError("handshake failed")])
async def test_tls_failure_or_cancellation_closes_tcp(error: BaseException) -> None:
    """握手失败/取消必须释放已经连接的 TCP，而不是等连接池猜测状态。"""
    stream = RecordingStream(b"")
    stream.start_tls = AsyncMock(side_effect=error)
    wrapper = cos_transport._ClosingTLSStream(stream)
    with pytest.raises(type(error)):
        await wrapper.start_tls(ssl.create_default_context(), HOST, 1)
    assert stream.closed


async def test_transport_rejects_non_put() -> None:
    """专用传输不成为任意 HTTP 请求出口。"""
    transport = cos_transport._CosTransport()
    try:
        with pytest.raises(CosTransportError):
            await transport.handle_async_request(httpx.Request("GET", SECRET_URL))
    finally:
        await transport.aclose()


async def test_dns_timeout_cancellation_and_backend_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """解析时限有界、取消传播，连接失败不自动尝试另一地址。"""
    loop = asyncio.get_running_loop()
    resolve = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(loop, "getaddrinfo", resolve)
    connect = AsyncMock(side_effect=httpcore.ConnectError("offline"))
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: SimpleNamespace(connect_tcp=connect))
    backend = cos_transport._PublicCOSBackend()
    with pytest.raises(asyncio.CancelledError):
        await backend.connect_tcp(HOST, 443, timeout=1)
    connect.assert_not_awaited()

    async def stalled(*args: Any, **kwargs: Any) -> list[Any]:
        """阻塞 DNS，供总时限取消。"""
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(loop, "getaddrinfo", stalled)
    with pytest.raises(TimeoutError):
        await backend.connect_tcp(HOST, 443, timeout=0.01)
    connect.assert_not_awaited()
    monkeypatch.setattr(loop, "getaddrinfo", AsyncMock(return_value=[address_info(PUBLIC_IP), address_info("8.8.8.8")]))
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp(HOST, 443, timeout=1)
    connect.assert_awaited_once()
