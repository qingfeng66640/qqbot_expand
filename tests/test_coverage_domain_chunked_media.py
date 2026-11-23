"""分片上传 Service 的真实阶段中断与字节边界回归。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import httpcore
import httpx
import pytest

from ..config import QQBotExpandConfig
from ..plugin import QQBotExpandPlugin
from ..services.chunked_media_service import QQBotChunkedMediaService
from ..src.errors import ERROR_BAD_REQUEST
from .conftest import FakeHttpClient, FakeSendHandler, make_plugin
from .test_cos_transport import ACCEL_HOST, PUBLIC_IP, RecordingStream, address_info

_COS_ORIGIN = "https://fixture-1234567890.cos.ap-guangzhou.myqcloud.com"


class UploadClient(FakeHttpClient):
    """记录签名 URL PUT，不打开任何真实网络连接。"""

    def __init__(self) -> None:
        """初始化普通 HTTP 和分片 PUT 两个边界记录。"""
        super().__init__()
        self.send = AsyncMock(return_value=httpx.Response(200))


def _part(index: int = 0, size: Any = "3", url: Any = f"{_COS_ORIGIN}/0") -> dict[str, Any]:
    """构造预上传响应中的分片描述。"""
    return {"index": index, "block_size": size, "presigned_url": url}


def _prepare(parts: Any = None) -> dict[str, Any]:
    """构造最小成功预上传响应。"""
    return {"upload_id": "upload-1", "parts": [_part()] if parts is None else parts}


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"target_type": "dm"}, "target_type"),
        ({"file_type": 0}, "file_type"),
        ({"file_type": True}, "file_type"),
        ({"file_name": None}, "file_name"),
        ({"file_name": " "}, "file_name"),
        ({"content": "abc"}, "bytes"),
        ({"content": bytearray(b"abc")}, "bytes"),
        ({"content": b""}, "bytes"),
        ({"target_id": " "}, "target_id"),
    ],
)
async def test_upload_input_errors_prevent_prepare(
    overrides: dict[str, Any], error: str, patch_send_handler: FakeSendHandler,
) -> None:
    """错误本地输入在分配远端 upload_id 之前被拒绝。"""
    client = UploadClient()
    service = QQBotChunkedMediaService(make_plugin(chunked_client=client))
    kwargs = {"target_type": "group", "target_id": "g1", "file_type": 4, "file_name": "a.txt", "content": b"abc"}
    kwargs.update(overrides)

    result = await service.upload_bytes(**kwargs)

    assert result["success"] is False
    assert error in result["error"]
    assert patch_send_handler.posts == []
    assert client.calls == []
    client.send.assert_not_awaited()


async def test_file_above_real_size_limit_does_not_prepare(
    patch_send_handler: FakeSendHandler,
) -> None:
    """使用真实 200MiB+1 字节触发大小上界，不修改生产阈值或 len。"""
    client = UploadClient()
    service = QQBotChunkedMediaService(make_plugin(chunked_client=client))
    content = b"x" * (200 * 1024 * 1024 + 1)

    result = await service.upload_bytes("group", "g1", 4, "large.bin", content)

    assert result == {"success": False, "data": None, "error": "文件不能超过 200MB"}
    assert patch_send_handler.posts == []
    assert client.calls == []
    client.send.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({}, "缺少 upload_id 或 parts"),
        ({"upload_id": 1, "parts": []}, "缺少 upload_id 或 parts"),
        ({"upload_id": "", "parts": []}, "缺少 upload_id 或 parts"),
        ({"upload_id": "upload-1", "parts": {}}, "缺少 upload_id 或 parts"),
        (_prepare([None]), "分片索引"),
        (_prepare([_part(0), _part(2)]), "分片索引"),
        (_prepare([_part(1), _part(3)]), "分片索引"),
        (_prepare([_part(2)]), "分片索引"),
        (_prepare([_part(-1), _part(0)]), "分片索引"),
        (_prepare([_part(1), _part(1)]), "分片索引"),
        (_prepare([_part(1), _part(0)]), "分片索引"),
        (_prepare([{**_part(), "index": True}]), "分片索引"),
        (_prepare([_part(0), {**_part(1), "index": 1.0}]), "分片索引"),
        (_prepare([{**_part(), "index": "1"}]), "分片索引"),
        (_prepare([_part(size=None)]), "无效分片"),
        (_prepare([_part(size=0)]), "无效分片"),
        (_prepare([_part(size=-1)]), "无效分片"),
        (_prepare([_part(url=None)]), "无效分片"),
        (_prepare([_part(url="http://cos.test/0")]), "无效分片"),
        (_prepare([]), "分片大小与文件不匹配"),
    ],
)
async def test_bad_prepare_response_never_uploads_or_merges(
    payload: dict[str, Any], error: str, patch_send_handler: FakeSendHandler,
) -> None:
    """损坏的预上传描述不能用于 PUT、完成通知或合并。"""
    client = UploadClient()
    patch_send_handler.post_result = payload
    result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
        "group", "g1", 4, "a.txt", b"abc"
    )

    assert result["success"] is False
    assert error in result["error"]
    assert [urlsplit(url).path for url, _, _ in patch_send_handler.posts] == ["/v2/groups/g1/upload_prepare"]
    assert client.calls == []
    client.send.assert_not_awaited()


async def test_missing_http_client_stops_after_prepare(
    patch_send_handler: FakeSendHandler,
) -> None:
    """预上传经 Adapter 成功后仍须具备分片 HTTP 客户端。"""
    patch_send_handler.post_result = _prepare()

    ordinary = UploadClient()
    result = await QQBotChunkedMediaService(make_plugin(http_client=ordinary)).upload_bytes(
        "group", "g1", 4, "a.txt", b"abc",
    )

    assert result == {"success": False, "data": None, "error": "HTTP 客户端未初始化"}
    ordinary.send.assert_not_awaited()
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][0].endswith("/upload_prepare")


@pytest.mark.parametrize("stage", ["prepare", "put-exception", "put-http", "finish", "merge"])
async def test_each_upload_failure_stops_downstream_stages(
    stage: str, patch_send_handler: FakeSendHandler,
) -> None:
    """只替换 HTTP 边界，真实 Service 按 prepare/PUT/finish/merge 顺序中止。"""
    client = UploadClient()
    api_error = {"code": 30001, "message": "private upstream detail"}
    patch_send_handler.post_results = [
        api_error if stage == "prepare" else _prepare(),
        api_error if stage == "finish" else {},
        api_error if stage == "merge" else {"file_info": "file-1"},
    ]
    if stage == "put-exception":
        client.send.side_effect = OSError("private transport detail")
    elif stage == "put-http":
        client.send.return_value = httpx.Response(503)

    result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
        "group", "g1", 4, "a.txt", b"abc"
    )

    expected_error = {
        "put-exception": "上传分片失败", "put-http": "上传分片返回 HTTP 503",
    }.get(stage, ERROR_BAD_REQUEST)
    assert result == {"success": False, "data": None, "error": expected_error}
    expected_stages = ["upload_prepare"]
    if stage in {"finish", "merge"}:
        expected_stages.append("upload_part_finish")
    if stage == "merge":
        expected_stages.append("files")
    assert [url.rsplit("/", 1)[-1] for url, _, _ in patch_send_handler.posts] == expected_stages
    assert client.send.await_count == (0 if stage == "prepare" else 1)
    assert client.calls == []


@pytest.mark.parametrize("parts", [[_part(size=2)], [_part(), _part(1, 1, f"{_COS_ORIGIN}/1")]])
async def test_total_part_size_mismatch_never_uploads(
    parts: list[dict[str, Any]], patch_send_handler: FakeSendHandler,
) -> None:
    """分片容量不足或多出空分片，在首个 PUT 之前拒绝整份计划。"""
    client = UploadClient()
    patch_send_handler.post_results = [_prepare(parts), {}]

    result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
        "group", "g1", 4, "a.txt", b"abc"
    )

    assert result == {"success": False, "data": None, "error": "预上传响应分片大小与文件不匹配"}
    assert [url.rsplit("/", 1)[-1] for url, _, _ in patch_send_handler.posts] == ["upload_prepare"]
    client.send.assert_not_awaited()
    assert client.calls == []


@pytest.mark.parametrize("changes", [
    {"block_size": True},
    {"block_size": 1.5},
    {"block_size": "1_0"},
    {"presigned_url": "https://fixture.cos.ap-guangzhou.myqcloud.com.invalid/part"},
    {"presigned_url": "https://fixture.cos.ap-guangzhou.myqcloud.com:444/part"},
    {"presigned_url": f"{_COS_ORIGIN}/part#unexpected"},
    {"presigned_url": f"{_COS_ORIGIN}/first"},
])
async def test_bad_later_part_prevents_all_cos_requests(
    changes: dict[str, Any], patch_send_handler: FakeSendHandler,
) -> None:
    """即使首片合法，较晚的非法字段也必须在 COS I/O 前被拒绝。"""
    patch_send_handler.post_results = [_prepare([
        _part(1, "2", f"{_COS_ORIGIN}/first"),
        {**_part(2, "1", f"{_COS_ORIGIN}/second"), **changes},
    ]), {}, {}, {"file_info": "unexpected"}]
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Capture any unexpected binary upload; no real requests are sent."""
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
            "group", "g1", 4, "a.txt", b"abc"
        )

    assert result == {"success": False, "data": None, "error": "预上传响应包含无效分片"}
    assert not requests
    assert [url.rsplit("/", 1)[-1] for url, _, _ in patch_send_handler.posts] == ["upload_prepare"]


@pytest.mark.parametrize("client_redirects", [False, True])
async def test_cos_redirect_never_confirms_and_request_has_no_client_credentials(
    client_redirects: bool, patch_send_handler: FakeSendHandler,
) -> None:
    """真实 HTTPX 请求必须无默认凭据，且 307 不跟随、不确认。"""
    signed_url = f"{_COS_ORIGIN}/part?q-signature=fixture%2Bvalue"
    patch_send_handler.post_results = [_prepare([_part(1, "5", signed_url)]), {}, {"file_info": "unexpected"}]
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Return a same-host redirect only for the first in-memory request."""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(307, headers={"Location": f"{_COS_ORIGIN}/elsewhere"})
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=client_redirects,
        auth=("fixture-user", "fixture-pass"), cookies={"fixture": "private"},
        headers={"Authorization": "Bearer fixture", "X-Union-Appid": "fixture-app"},
    ) as client:
        result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
            "group", "g1", 4, "a.txt", b"abc"
        )

    assert result == {"success": False, "data": None, "error": "上传分片返回 HTTP 307"}
    assert [(request.method, str(request.url), request.content) for request in requests] == [
        ("PUT", signed_url, b"abc"),
    ]
    assert set(requests[0].headers) == {"host", "content-length"}
    assert [url.rsplit("/", 1)[-1] for url, _, _ in patch_send_handler.posts] == ["upload_prepare"]


@pytest.mark.parametrize("host", [urlsplit(_COS_ORIGIN).hostname, ACCEL_HOST])
@pytest.mark.parametrize("public_dns", [True, False])
@pytest.mark.parametrize("explicit_default_port", [False, True])
async def test_production_chunked_put_uses_private_pinned_cos_transport(
    host: str, public_dns: bool, explicit_default_port: bool,
    monkeypatch: pytest.MonkeyPatch,
    patch_send_handler: FakeSendHandler, caplog: pytest.LogCaptureFixture,
) -> None:
    """只替换 DNS/TCP 和普通 HTTP 出口，运行真实插件、Service 和 COS 传输。"""
    port = ":443" if explicit_default_port else ""
    signed_url = f"https://{host}{port}/first%2Fpart?partNumber=1&signature=offline-signed%2Bcanary&partNumber=2"
    patch_send_handler.post_results = [_prepare([_part(1, "8", signed_url)]), {}, {"file_info": "opaque"}]
    addresses = [address_info(PUBLIC_IP)]
    if not public_dns:
        addresses.append(address_info("127.0.0.1"))
    dns = AsyncMock(return_value=addresses)
    stream = RecordingStream(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    tcp = AsyncMock(return_value=stream)
    ordinary_requests: list[httpx.Request] = []

    async def ordinary(_transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        """捕获普通池中的请求，不访问外网；COS 不应到这里。"""
        ordinary_requests.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", tcp)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", ordinary)
    config = QQBotExpandConfig()
    plugin = QQBotExpandPlugin(config)
    group_url = "https://api.bot.qq.com/v2/groups/fixture-group/info"
    with caplog.at_level(logging.DEBUG):
        await plugin.on_plugin_loaded()
        chunked_client = plugin.chunked_client
        try:
            assert chunked_client is not None and not chunked_client.trust_env
            assert chunked_client is not plugin.http_client
            result = await QQBotChunkedMediaService(plugin).upload_bytes("group", "g1", 1, "a.png", b"fixture")
            assert plugin.http_client is not None
            assert (await plugin.http_client.get(group_url)).is_success
        finally:
            await plugin.on_plugin_unloaded()

    assert plugin.chunked_client is None and chunked_client.is_closed
    assert "offline-signed" not in caplog.text
    assert group_url in caplog.text
    assert [(req.method, str(req.url)) for req in ordinary_requests] == [("GET", group_url)]
    assert dns.await_args.args[:2] == (host, 443)
    if public_dns:
        assert result == {"success": True, "data": {"file_info": "opaque"}, "error": None}
        assert tcp.await_args.args[:2] == (PUBLIC_IP, 443)
        assert stream.tls is not None
        context, sni = stream.tls
        assert sni == host and context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        request_bytes = b"".join(stream.writes)
        assert b"PUT /first%2Fpart?partNumber=1&signature=offline-signed%2Bcanary&partNumber=2 HTTP/1.1" in request_bytes
        assert b"authorization:" not in request_bytes.lower() and b"cookie:" not in request_bytes.lower()
        assert stream.closed
        stages = ["upload_prepare", "upload_part_finish", "files"]
    else:
        assert result == {"success": False, "data": None, "error": "上传分片失败"}
        tcp.assert_not_awaited()
        stages = ["upload_prepare"]
    assert [url.rsplit("/", 1)[-1] for url, _, _ in patch_send_handler.posts] == stages


@pytest.mark.parametrize(("receipt", "expected"), [
    ({}, None),
    ({"file_info": None}, None),
    ({"file_info": " \t"}, None),
    ({"file_info": {"unexpected": "fixture"}}, None),
    ({"file_info": "opaque+/=", "file_uuid": "uuid", "ttl": 60,
      "raw_url": f"{_COS_ORIGIN}/file?q-signature=private-fixture"},
     {"file_info": "opaque+/=", "file_uuid": "uuid", "ttl": 60}),
])
async def test_merge_requires_file_info_and_does_not_return_signed_url(
    receipt: dict[str, Any], expected: dict[str, Any] | None, patch_send_handler: FakeSendHandler,
) -> None:
    """合并必须返回不透明媒体信息；官方可选的签名下载地址不对外返回。"""
    patch_send_handler.post_results = [_prepare([_part(1, "8")]), {}, receipt]
    client = UploadClient()
    result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
        "group", "g1", 1, "a.png", b"fixture",
    )
    assert result == {
        "success": expected is not None, "data": expected,
        "error": None if expected is not None else "QQ API 合并响应缺少 file_info",
    }
    assert [url.rsplit("/", 1)[-1] for url, _, _ in patch_send_handler.posts] == [
        "upload_prepare", "upload_part_finish", "files",
    ]
    client.send.assert_awaited_once()
    assert client.send.await_args.args[0].content == b"fixture"


@pytest.mark.parametrize("index_base", [0, 1])
async def test_two_part_user_upload_has_exact_hashes_and_no_cos_auth_header(
    patch_send_handler: FakeSendHandler, index_base: int,
) -> None:
    """两个分片按原始 0/1 基索引确认，字节切分不受基数影响。"""
    client = UploadClient()
    content = b"abcdef"
    patch_send_handler.post_results = [
        _prepare([_part(index_base, 2), _part(index_base + 1, 4, f"{_COS_ORIGIN}/1")]),
        {}, {}, {"file_info": "file-1"},
    ]

    result = await QQBotChunkedMediaService(make_plugin(chunked_client=client)).upload_bytes(
        "user", "u/1", 4, " a.txt ", content
    )

    assert result == {"success": True, "data": {"file_info": "file-1"}, "error": None}
    assert [urlsplit(url).path for url, _, _ in patch_send_handler.posts] == [
        "/v2/users/u%2F1/upload_prepare", "/v2/users/u%2F1/upload_part_finish",
        "/v2/users/u%2F1/upload_part_finish", "/v2/users/u%2F1/files",
    ]
    assert patch_send_handler.posts[0][2] == {
        "file_type": 4, "file_size": "6", "file_name": "a.txt",
        "md5": hashlib.md5(content).hexdigest(), "sha1": hashlib.sha1(content).hexdigest(),
        "md5_10m": hashlib.md5(content).hexdigest(),
    }
    assert [(call.args[0].method, str(call.args[0].url), call.args[0].content)
            for call in client.send.await_args_list] == [
        ("PUT", f"{_COS_ORIGIN}/0", b"ab"),
        ("PUT", f"{_COS_ORIGIN}/1", b"cdef"),
    ]
    assert all(call.kwargs == {"auth": None, "follow_redirects": False, "stream": True}
               and set(call.args[0].headers) == {"host", "content-length"}
               for call in client.send.await_args_list)
    for index, chunk in enumerate([b"ab", b"cdef"]):
        assert patch_send_handler.posts[index + 1][2] == {
            "upload_id": "upload-1", "part_index": index_base + index,
            "block_size": str(len(chunk)), "md5": hashlib.md5(chunk).hexdigest(),
        }
    assert patch_send_handler.posts[-1][2] == {
        "file_type": 4, "file_name": "a.txt", "upload_id": "upload-1", "srv_send_msg": False,
    }
    assert client.calls == []
