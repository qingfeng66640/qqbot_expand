"""素材错误响应、元数据拒绝与关闭竞态的补充契约测试。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

from ..services import material_service
from ..src.material_policy import (
    MaterialError,
    PreparedUpload,
    completed_data,
    image_payload,
    listed_data,
    parse_prepared,
    validate_image_id,
)
from ..src.cos_transport import CosTransportError, _public_address
from ..src.material_upload import MaterialUploadClient
from .conftest import FakeResponse
from .test_material_service import HOST, image_bytes, material_env as material_env


@pytest.mark.parametrize("method", ["list", "delete"])
async def test_unexpected_control_error_is_sanitized(
    method: str, material_env: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非预期控制面异常也不得透传 URL 或破坏 Service 失败结构。"""
    request = AsyncMock(side_effect=RuntimeError("https://private.example/?secret=hidden"))
    monkeypatch.setattr(material_service, "api_request", request)
    result = (
        await material_env.service.list_images()
        if method == "list"
        else await material_env.service.delete_image("image_123", confirm=True)
    )
    assert result["success"] is False and result["data"] is None
    assert "secret" not in result["error"] and "private.example" not in result["error"]
    request.assert_awaited_once()


async def test_delete_rejects_nonempty_success_body(material_env: Any) -> None:
    """删除端点返回非空对象时不能宣称成功，也不能重放删除。"""
    client = material_env.plugin.http_client
    client.responses = [FakeResponse(200, {"unexpected": True})]
    result = await material_env.service.delete_image("image_123", confirm=True)
    assert result["success"] is False and "远端结果未知" in result["error"]
    assert len(client.calls) == 1 and client.calls[0]["method"] == "DELETE"


@pytest.mark.parametrize("value", [" image_123", "image_123 ", "a..b"])
def test_image_id_rejects_ambiguous_whitespace_or_traversal(value: str) -> None:
    """素材 ID 不能在路径编码前被静默修剪或包含穿越语义。"""
    with pytest.raises(MaterialError, match="image_id"):
        validate_image_id(value)


def test_file_name_requires_a_basename_before_extension() -> None:
    """仅扩展名不能作为有效图片文件名。"""
    with pytest.raises(MaterialError, match="扩展名"):
        image_payload(".png", image_bytes(), "image/png")


@pytest.mark.parametrize("parser", [parse_prepared, completed_data])
def test_control_response_must_be_an_object(parser: Any) -> None:
    """prepare 和 complete 的非对象响应在取得字段前即被拒绝。"""
    with pytest.raises(MaterialError, match="响应不合法"):
        parser(None)


def test_inventory_rejects_overflow_before_processing_items() -> None:
    """单页超出单机器人素材上限时不处理或返回任意条目。"""
    with pytest.raises(MaterialError, match="数量上限"):
        listed_data({"images": [{} for _ in range(101)]})


def test_dns_scoped_address_is_not_treated_as_public() -> None:
    """带本地作用域的 IPv6 不能作为 COS 公网连接目标。"""
    with pytest.raises(CosTransportError, match="无效地址"):
        _public_address("fe80::1%eth0")


async def test_close_between_initial_guard_and_slot_acquisition() -> None:
    """等待名额期间发生关闭，获得名额后必须再次拒绝工作。"""
    runtime = MaterialUploadClient(request_timeout=1, connect_timeout=1)

    @asynccontextmanager
    async def closing_gate() -> AsyncIterator[None]:
        """模拟关闭发生在 semaphore 的 await 边界，不发送网络请求。"""
        runtime.is_closed = True
        yield

    runtime._semaphore = closing_gate()  # type: ignore[assignment]
    try:
        with pytest.raises(MaterialError, match="已关闭"):
            async with runtime.operation():
                pytest.fail("关闭后不应进入调用方的业务体")
        assert not runtime._active_tasks and runtime._drained.is_set()
    finally:
        # 此测试直接模拟 is_closed 转换；显式恢复后按真实方法释放客户端。
        runtime.is_closed = False
        await runtime.aclose()


async def test_closed_runtime_rejects_direct_put() -> None:
    """已关闭资源不能通过直接 PUT 入口复活客户端或发出请求。"""
    runtime = MaterialUploadClient(request_timeout=1, connect_timeout=1)
    await runtime.aclose()
    prepared = PreparedUpload("upload_123", 60, f"https://{HOST}/private?sign=hidden")
    with pytest.raises(MaterialError, match="已关闭"):
        await runtime.put_prepared(prepared, b"image", "image/png", "a" * 32)
    assert runtime._client.is_closed
