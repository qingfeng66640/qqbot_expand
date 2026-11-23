"""图片素材 Service 的真实控制面组包、失败边界和白名单回归。"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from ..services.material_service import QQBotMaterialService
from ..src import cos_transport, material_upload
from ..src.material_policy import MAX_IMAGE_BYTES
from .conftest import FakeHttpClient, FakeResponse, FakeSendHandler, make_plugin

HOST = "bucket-123.cos.ap-guangzhou.myqcloud.com"
SECRET_URL = f"https://{HOST}/private-object?sign=DO-NOT-LEAK"
REFERENCE = "qqbot-media://image/v1/opaque_token"


def image_bytes(fmt: str = "PNG") -> bytes:
    """生成可被真实解码的极小图片，不依赖外部文件。"""
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format=fmt)
    return output.getvalue()


def prepared(**changes: Any) -> dict[str, Any]:
    """构造包含私密 URL 的 prepare 响应。"""
    return {"upload_id": "upload_123", "presigned_url": SECRET_URL,
            "expires_in": 3600, **changes}


def completed(**changes: Any) -> dict[str, Any]:
    """构造包含必须剔除字段的 complete 响应。"""
    return {"image_id": "image_123", "url": REFERENCE, "ttl": 7776000,
            "preview_url": SECRET_URL, "extra": {"secret": SECRET_URL}, **changes}


class MaterialControlClient(FakeHttpClient):
    """同时支持既有 Adapter POST 与显式一次性 HTTP 路径的控制面替身。"""

    def __init__(self, handler: FakeSendHandler) -> None:
        """共用预设响应和控制面记录，GET/DELETE 沿用公共测试装置。"""
        super().__init__()
        self.handler = handler

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], json: Any = None,
    ) -> FakeResponse:
        """一次性 POST 不调用带内部重试的 post_api，其他方法保持原约定。"""
        if method != "POST":
            return await super().request(method, url, headers=headers, json=json)
        self.handler.posts.append((url, headers, json))
        result = (self.handler.post_results.pop(0) if self.handler.post_results
                  else self.handler.post_result)
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(200, result)


@pytest.fixture
async def material_env(
    monkeypatch: pytest.MonkeyPatch, patch_send_handler: FakeSendHandler,
) -> AsyncIterator[SimpleNamespace]:
    """连接真实 Service/bridge 与离线 HTTP 替身。"""
    content = image_bytes()
    puts: list[httpx.Request] = []

    async def upload(request: httpx.Request) -> httpx.Response:
        """记录二进制 PUT，不连接真实网络。"""
        puts.append(request)
        return httpx.Response(200, headers={"ETag": f'"{hashlib.md5(request.content).hexdigest()}"'})

    monkeypatch.setattr(cos_transport, "_CosTransport", lambda: httpx.MockTransport(upload))
    runtime = material_upload.MaterialUploadClient(request_timeout=1.0, connect_timeout=1.0)
    plugin = make_plugin(http_client=MaterialControlClient(patch_send_handler))
    plugin.config.features.enable_material_service = True
    plugin.config.features.allow_material_delete = True
    plugin.material_upload = runtime
    patch_send_handler.post_results = [prepared(), completed()]
    env = SimpleNamespace(
        plugin=plugin, service=QQBotMaterialService(plugin=plugin), content=content,
        handler=patch_send_handler, puts=puts, runtime=runtime,
    )
    try:
        yield env
    finally:
        await runtime.aclose()


async def test_upload_complete_contract(material_env: Any) -> None:
    """prepare/PUT/complete 使用官方字段，且素材引用不是消息 ID。"""
    env = material_env
    result = await env.service.upload_image("test.png", env.content, "image/png")
    assert result == {"success": True, "data": {
        "image_id": "image_123", "url": REFERENCE, "ttl": 7776000,
    }, "error": None}
    assert len(env.handler.posts) == 2
    url, headers, body = env.handler.posts[0]
    assert url == "https://api.bot.qq.com/v2/media-images/prepare"
    assert headers["Authorization"] == "QQBot fake-token"
    assert body == {
        "file_name": "test.png", "file_size": len(env.content),
        "content_type": "image/png", "md5": hashlib.md5(env.content).hexdigest(),
        "sha1": hashlib.sha1(env.content).hexdigest(),
    }
    assert len(env.puts) == 1
    assert env.puts[0].method == "PUT"
    assert env.puts[0].content == env.content
    assert env.puts[0].headers["Content-Type"] == "image/png"
    assert "authorization" not in env.puts[0].headers
    assert "cookie" not in env.puts[0].headers
    assert env.handler.posts[1][0].endswith("/v2/media-images/complete")
    assert env.handler.posts[1][2] == {"upload_id": "upload_123", "md5": body["md5"]}
    assert "DO-NOT-LEAK" not in repr(result)


@pytest.mark.parametrize(("fmt", "name", "mime"), [
    ("PNG", "你好.PNG", "image/png"), ("JPEG", "a.jpeg", "image/jpeg"),
    ("JPEG", "a.jpg", "image/jpeg"), ("GIF", "a.gif", "image/gif"),
    ("WEBP", "a.webp", "image/webp"),
])
async def test_supported_real_formats(material_env: Any, fmt: str, name: str, mime: str) -> None:
    """所有支持格式都校验真实内容，允许常见 jpg 后缀。"""
    assert (await material_env.service.upload_image(name, image_bytes(fmt), mime))["success"]


@pytest.mark.parametrize(("name", "content", "mime"), [
    ("a.png", b"", "image/png"), ("a.png", bytearray(b"x"), "image/png"),
    ("a.png", "not bytes", "image/png"), ("", b"x", "image/png"),
    ("../a.png", b"x", "image/png"), ("a/b.png", b"x", "image/png"),
    ("C:\\a.png", b"x", "image/png"), ("a\n.png", b"x", "image/png"),
    (" a.png", b"x", "image/png"), ("a.png", b"not an image", "image/png"),
    ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"),
    ("a.png", b"x", "image/jpeg"), ("a.bmp", b"x", "image/bmp"),
    ("a.png", b"x", None),
])
async def test_invalid_input_has_no_network(material_env: Any, name: Any, content: Any, mime: Any) -> None:
    """非法内容和路径在 prepare 前失败。"""
    result = await material_env.service.upload_image(name, content, mime)
    assert result["success"] is False
    assert material_env.handler.posts == material_env.puts == []


async def test_type_mismatch_and_size_boundary(material_env: Any) -> None:
    """大小口径为 10 MiB，边界本身可接受，超一字节无网络。"""
    env = material_env
    assert not (await env.service.upload_image("a.png", image_bytes("JPEG"), "image/png"))["success"]
    assert env.handler.posts == []
    content = env.content + b"\0" * (MAX_IMAGE_BYTES - len(env.content))
    assert (await env.service.upload_image("a.png", content, "image/png"))["success"]
    env.handler.posts.clear()
    env.puts.clear()
    assert not (await env.service.upload_image("a.png", content + b"\0", "image/png"))["success"]
    assert env.handler.posts == env.puts == []


@pytest.mark.parametrize("payload", [
    {}, [], "non-json", prepared(upload_id=""), prepared(upload_id=None),
    prepared(expires_in=0), prepared(expires_in=True), prepared(expires_in="3600"),
    prepared(expires_in=2**32), prepared(presigned_url="https://example.com/a"),
    prepared(presigned_url=None),
])
async def test_prepare_response_validation(material_env: Any, payload: Any) -> None:
    """畸形 prepare 响应不能触发 PUT 或 complete。"""
    env = material_env
    env.handler.post_results = [payload]
    result = await env.service.upload_image("a.png", env.content, "image/png")
    assert result["success"] is False
    assert len(env.handler.posts) == 1
    assert env.puts == []
    assert "DO-NOT-LEAK" not in repr(result)


@pytest.mark.parametrize("payload", [
    {}, "non-json", completed(image_id=""), completed(url="https://evil.example/secret"),
    completed(url=REFERENCE + "?secret=1"), completed(ttl=True), completed(ttl=0),
])
async def test_complete_response_validation(material_env: Any, payload: Any) -> None:
    """complete 异常不重建，不返回私密字段，并提示结果未知。"""
    env = material_env
    env.handler.post_results = [prepared(), payload]
    result = await env.service.upload_image("a.png", env.content, "image/png")
    assert not result["success"]
    assert "结果未知" in result["error"]
    assert len(env.handler.posts) == 2
    assert len(env.puts) == 1
    assert env.plugin.http_client.calls == []
    assert "DO-NOT-LEAK" not in repr(result)


@pytest.mark.parametrize("stage", ["prepare", "complete"])
async def test_unknown_result_is_not_replayed(material_env: Any, stage: str) -> None:
    """副作用请求的异常只返回结果未知，不能自动重传或补偿删除。"""
    env = material_env
    error = httpx.ReadTimeout(SECRET_URL)
    env.handler.post_results = [error] if stage == "prepare" else [prepared(), error]
    result = await env.service.upload_image("a.png", env.content, "image/png")
    assert not result["success"]
    assert "结果未知" in result["error"]
    assert len(env.handler.posts) == (1 if stage == "prepare" else 2)
    assert len(env.puts) == (0 if stage == "prepare" else 1)
    assert env.plugin.http_client.calls == []
    assert "DO-NOT-LEAK" not in repr(result)


async def test_put_failure_never_completes(material_env: Any) -> None:
    """二进制网络错误不继续 complete，也不自动删除。"""
    env = material_env
    env.runtime.put_prepared = AsyncMock(side_effect=httpx.ReadError(SECRET_URL))
    result = await env.service.upload_image("a.png", env.content, "image/png")
    assert not result["success"]
    assert len(env.handler.posts) == 1
    assert env.plugin.http_client.calls == []
    assert "DO-NOT-LEAK" not in repr(result)


async def test_list_one_page_and_whitelist(material_env: Any) -> None:
    """只请求指定单页，剔除顶层和图片中的预览链接。"""
    env = material_env
    item = {"image_id": "image_123", "url": REFERENCE, "file_name": "a.png",
            "created_at": 100, "expires_at": 200, "preview_url": SECRET_URL}
    env.plugin.http_client.responses = [FakeResponse(200, {
        "images": [item], "next_cursor": "next+cursor", "total": 10, "preview_url": SECRET_URL,
    })]
    result = await env.service.list_images(cursor="page+1&x=2")
    assert result["success"]
    assert result["data"] == {"images": [{k: v for k, v in item.items() if k != "preview_url"}],
                              "next_cursor": "next+cursor", "total": 10}
    assert len(env.plugin.http_client.calls) == 1
    assert env.plugin.http_client.calls[0]["url"].endswith("?cursor=page%2B1%26x%3D2")
    assert "DO-NOT-LEAK" not in repr(result)


async def test_list_first_page_and_empty_inventory(material_env: Any) -> None:
    """首页不发送 cursor，空库存是成功结果。"""
    env = material_env
    env.plugin.http_client.responses = [FakeResponse(200, {"images": [], "total": 0, "next_cursor": ""})]
    assert (await env.service.list_images())["success"]
    assert env.plugin.http_client.calls[0]["url"] == "https://api.bot.qq.com/v2/media-images"


@pytest.mark.parametrize("total", [0, 2**64 - 1, "0", "18446744073709551615"])
async def test_list_normalizes_qq_uint64_total(material_env: Any, total: int | str) -> None:
    """QQ 的整数和合法十进制字符串总数均返回为 int。"""
    material_env.plugin.http_client.responses = [FakeResponse(200, {
        "images": [], "next_cursor": "", "total": total,
    })]
    result = await material_env.service.list_images()
    assert result["success"]
    assert type(result["data"]["total"]) is int
    assert result["data"]["total"] == int(total)


@pytest.mark.parametrize("total", ["", "-1", "+1", " 1", "1.0", "1e2", "１", "1_0",
                                   "18446744073709551616", "0" * 21, -1, 2**64, 1.0, True, None])
async def test_list_rejects_non_uint64_total(material_env: Any, total: object) -> None:
    """拒绝无效文本和越界值，不放宽其它字段的协议约束。"""
    material_env.plugin.http_client.responses = [FakeResponse(200, {
        "images": [], "next_cursor": "", "total": total,
    })]
    result = await material_env.service.list_images()
    assert not result["success"]


@pytest.mark.parametrize("created_at,expires_at", [(0, 2**63 - 1), ("0", "9223372036854775807")])
async def test_list_normalizes_material_timestamps(
    material_env: Any, created_at: int | str, expires_at: int | str,
) -> None:
    """接受范围内的 QQ 时间字段，并且只返回整数、不返回预览链接。"""
    item = {"image_id": "image_123", "url": REFERENCE, "file_name": "a.png", "created_at": created_at,
            "expires_at": expires_at, "preview_url": SECRET_URL}
    material_env.plugin.http_client.responses = [FakeResponse(200, {"images": [item], "next_cursor": "", "total": "1"})]
    result = await material_env.service.list_images()
    assert result["success"]
    parsed = result["data"]["images"][0]
    assert type(parsed["created_at"]) is type(parsed["expires_at"]) is int
    assert (parsed["created_at"], parsed["expires_at"]) == (int(created_at), int(expires_at))
    assert "preview_url" not in parsed


@pytest.mark.parametrize("field", ["created_at", "expires_at"])
@pytest.mark.parametrize("value", ["", "-1", "+1", " 1", "1.0", "1e2", "１", "1_0", "9223372036854775808",
                                    "0" * 20, -1, 2**63, 1.0, True, None])
async def test_list_rejects_non_int64_material_timestamps(material_env: Any, field: str, value: object) -> None:
    """任一时间字段非法均拒绝整页，不把缺失或畸形时间当成有效值。"""
    item = {"image_id": "image_123", "url": REFERENCE, "file_name": "a.png", "created_at": 100, "expires_at": 200}
    material_env.plugin.http_client.responses = [FakeResponse(200, {
        "images": [{**item, field: value}], "next_cursor": "", "total": 1,
    })]
    assert not (await material_env.service.list_images())["success"]


@pytest.mark.parametrize("response", [
    FakeResponse(200, None, SECRET_URL), FakeResponse(200, []), FakeResponse(200, {}),
    FakeResponse(200, {"images": [None], "next_cursor": "", "total": 1}),
    FakeResponse(200, {"images": [], "next_cursor": 1, "total": 0}),
    FakeResponse(200, {"images": [], "next_cursor": "", "total": True}),
    FakeResponse(403, {"preview_url": SECRET_URL}),
])
async def test_list_rejects_invalid_responses(material_env: Any, response: FakeResponse) -> None:
    """非 JSON 和畸形查询响应不透传给调用方。"""
    material_env.plugin.http_client.responses = [response]
    result = await material_env.service.list_images()
    assert not result["success"]
    assert "DO-NOT-LEAK" not in repr(result)


@pytest.mark.parametrize("confirm", [False, 1, "true", None])
async def test_delete_requires_boolean_confirmation(material_env: Any, confirm: Any) -> None:
    """删除必须显式传入布尔 True。"""
    assert not (await material_env.service.delete_image("image", confirm=confirm))["success"]
    assert material_env.plugin.http_client.calls == []


async def test_delete_encodes_id_and_never_replays(material_env: Any) -> None:
    """删除路径逐段编码；网络超时仅执行一次。"""
    env = material_env
    env.plugin.http_client.responses = [httpx.ReadTimeout(SECRET_URL)]
    result = await env.service.delete_image("id/with ?#%", confirm=True)
    assert not result["success"]
    assert "结果未知" in result["error"]
    calls = env.plugin.http_client.calls
    assert len(calls) == 1
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/id%2Fwith%20%3F%23%25/delete")
    assert "DO-NOT-LEAK" not in repr(result)


async def test_delete_success_and_disable(material_env: Any) -> None:
    """删除仅返回空白名单；关闭删除开关后拒绝网络操作。"""
    env = material_env
    assert await env.service.delete_image("image", confirm=True) == {"success": True, "data": {}, "error": None}
    env.plugin.config.features.allow_material_delete = False
    env.plugin.http_client.calls.clear()
    assert not (await env.service.delete_image("image", confirm=True))["success"]
    assert env.plugin.http_client.calls == []


async def test_feature_off_and_missing_runtime(material_env: Any) -> None:
    """默认关闭、缺失运行资源时均 fail closed。"""
    env = material_env
    env.plugin.config.features.enable_material_service = False
    for result in [await env.service.upload_image("a.png", env.content, "image/png"),
                   await env.service.list_images(), await env.service.delete_image("id", confirm=True)]:
        assert not result["success"]
    env.plugin.config.features.enable_material_service = True
    env.plugin.material_upload = None
    assert not (await env.service.upload_image("a.png", env.content, "image/png"))["success"]
    assert env.handler.posts == env.puts == env.plugin.http_client.calls == []


@pytest.mark.parametrize("stage", ["prepare", "put", "complete"])
async def test_cancellation_propagates(material_env: Any, stage: str) -> None:
    """任何阶段的取消原样传播，释放操作槽且无后续网络调用。"""
    env = material_env
    if stage == "put":
        env.runtime.put_prepared = AsyncMock(side_effect=asyncio.CancelledError)
    else:
        env.handler.post_results = [asyncio.CancelledError()] if stage == "prepare" else [prepared(), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await env.service.upload_image("a.png", env.content, "image/png")
    assert len(env.handler.posts) == (2 if stage == "complete" else 1)
    assert not env.runtime._active_tasks


async def test_control_requests_explicitly_disable_retries(material_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """E3 必须向公共 bridge 显式请求禁重试，不在 Service 内自建鉴权。"""
    from ..services import material_service
    from ..src.bridge import api_request

    spy = AsyncMock(wraps=api_request)
    monkeypatch.setattr(material_service, "api_request", spy)
    assert (await material_env.service.upload_image("a.png", material_env.content, "image/png"))["success"]
    assert all(call.kwargs["retry_network_errors"] is False for call in spy.await_args_list)


async def test_one_shot_posts_do_not_use_retrying_adapter(material_env: Any) -> None:
    """真实 bridge 必须兑现禁重试语义，不进入 Adapter 内部重试循环。

    当前 bridge 的 POST 分支忽略 retry_network_errors；此回归锁定父会话的集成修复。
    素材 Service 不通过访问 Adapter 私有字段临时篡改共享重试设置。
    """
    env = material_env
    env.handler.post_api = AsyncMock(side_effect=AssertionError("retrying POST must not be called"))
    result = await env.service.upload_image("a.png", env.content, "image/png")
    assert result["success"]
    env.handler.post_api.assert_not_awaited()
    assert len(env.handler.posts) == 2
