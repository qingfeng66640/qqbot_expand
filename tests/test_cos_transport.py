"""预签名 COS 共享传输的离线边界测试。"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from ..src import cos_transport
from ..src.cos_transport import CosTransportError, validate_presigned_url

HOST = "fixture-1234567890.cos.ap-guangzhou.myqcloud.com"
SECRET_URL = f"https://{HOST}/object?sign=offline-fixture"

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
    with pytest.raises(CosTransportError):
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


async def test_put_uses_pinned_tls_and_suppresses_only_cos_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """以模拟 DNS 和 TCP 驱动真实 HTTP/1.1，核对 TLS 与请求日志。"""
    stream = RecordingStream(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    dns = AsyncMock(return_value=[address_info(PUBLIC_IP)])
    connect = AsyncMock(return_value=stream)
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    with caplog.at_level(logging.DEBUG):
        async with cos_transport.create_cos_client(request_timeout=1.0, connect_timeout=1.0) as client:
            with cos_transport.private_cos_diagnostics():
                response = await client.send(httpx.Request("PUT", SECRET_URL, content=b"offline"), auth=None)
            assert response.status_code == 200
            assert not client.trust_env and not client.follow_redirects
        logging.getLogger("httpx").info("unrelated-log-kept")
    assert dns.await_args.args[:2] == (HOST, 443)
    assert connect.await_args.args[:2] == (PUBLIC_IP, 443)
    assert stream.tls is not None and stream.tls[1] == HOST
    assert stream.tls[0].check_hostname and stream.tls[0].verify_mode == ssl.CERT_REQUIRED
    assert b"PUT /object?sign=offline-fixture HTTP/1.1" in b"".join(stream.writes)
    assert stream.closed and SECRET_URL not in caplog.text
    assert "unrelated-log-kept" in caplog.text
