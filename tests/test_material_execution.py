"""素材解码调度、帧数预算和控制面重定向失败的回归测试。"""
from __future__ import annotations

import asyncio
import struct
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.kernel.concurrency import TaskManager

from ..services import material_service
from ..src import material_policy, material_validation
from ..src.material_policy import MaterialError, image_payload
from .conftest import FakeResponse
from .test_material_service import completed, image_bytes, material_env as material_env, prepared


def animated_gif(frames: int) -> bytes:
    """构造真实可解码的1×1 GIF，独立帧不被图片编码器自动合并。"""
    header = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0)
    palette = b"\x00\x00\x00\xff\xff\xff"
    frame = b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00"
    return header + palette + frame * frames + b";"


def test_frame_budget_rejects_many_tiny_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """字节和像素预算不能代替帧数预算；超多极小帧必须被拒绝。"""
    monkeypatch.setattr(material_policy, "MAX_IMAGE_FRAMES", 2, raising=False)
    with pytest.raises(MaterialError, match="帧数"):
        image_payload("animation.gif", animated_gif(3), "image/gif")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_delete_redirect_is_never_success(status: int, material_env: Any) -> None:
    """删除未取得2xx确认时不能把空响应体视为成功。"""
    client = material_env.plugin.http_client
    client.responses = [FakeResponse(status)]
    result = await material_env.service.delete_image("image_123", confirm=True)
    assert result["success"] is False and result["data"] is None
    assert len(client.calls) == 1 and client.calls[0]["method"] == "DELETE"


async def test_complete_redirect_with_valid_body_is_not_trusted(material_env: Any) -> None:
    """3xx 即使带看似合法的 complete 数据，也不能冒充完成确认。"""
    request = AsyncMock(side_effect=[FakeResponse(200, prepared()), FakeResponse(302, completed())])
    material_env.plugin.http_client.request = request
    result = await material_env.service.upload_image("a.png", material_env.content, "image/png")
    assert result["success"] is False and result["data"] is None
    assert request.await_count == 2 and len(material_env.puts) == 1


async def test_service_dispatches_decode_to_framework_executor(
    material_env: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纯函数仍真实执行，但异步 Service 必须通过框架 CPU 调度入口。"""
    async def run(function: Any, *args: Any, timeout: float | None = None) -> Any:
        """单元测试只替换执行器边界，保留实际图片解码和payload校验。"""
        return function(*args)

    process = AsyncMock(side_effect=run)
    monkeypatch.setattr(
        material_service, "get_task_manager", lambda: SimpleNamespace(to_process=process), raising=False,
    )
    result = await material_env.service.upload_image("a.png", material_env.content, "image/png")
    assert result["success"] is True
    process.assert_awaited_once()
    assert process.await_args.args[0].func is subprocess.run


@pytest.mark.parametrize("mode", ["success", "timeout", "cancel", "worker_error"])
async def test_real_executor_keeps_loop_and_cancellation_responsive(
    mode: str, material_env: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """实际使用框架进程池，验证定时器、总超时和取消不会被解码阻塞。"""
    manager = TaskManager(process_workers=1)
    await manager.to_process(abs, -1, timeout=5)
    monkeypatch.setattr(material_service, "get_task_manager", lambda: manager)
    monkeypatch.setattr(
        material_validation, "_WORKER_SCRIPT",
        "import time; time.sleep(0.15)\n" + material_validation._WORKER_SCRIPT,
    )
    timer_fired = asyncio.Event()
    handle = asyncio.get_running_loop().call_later(0.01, timer_fired.set)
    try:
        if mode == "worker_error":
            script = material_validation._WORKER_SCRIPT
            monkeypatch.setattr(material_validation, "_WORKER_SCRIPT", "raise RuntimeError('private secret')")
            rejected = await material_env.service.upload_image("a.png", material_env.content, "image/png")
            assert not rejected["success"] and "secret" not in rejected["error"]
            assert material_env.handler.posts == []
            assert await manager.to_process(abs, -1, timeout=5) == 1
            monkeypatch.setattr(material_validation, "_WORKER_SCRIPT", script)
            accepted = await material_env.service.upload_image("a.png", material_env.content, "image/png")
            assert accepted["success"] and len(material_env.handler.posts) == 2
        elif mode == "timeout":
            material_env.runtime._request_timeout = 0.01
            result = await material_env.service.upload_image("a.png", material_env.content, "image/png")
            assert not result["success"] and "超时" in result["error"]
            assert material_env.handler.posts == []
        elif mode == "cancel":
            task = manager.create_task(
                material_env.service.upload_image("a.png", material_env.content, "image/png"),
                name="cancel-material-validation",
            ).task
            assert task is not None
            await asyncio.sleep(0.03)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert material_env.handler.posts == []
        else:
            result = await material_env.service.upload_image("a.png", material_env.content, "image/png")
            assert result["success"] and len(material_env.handler.posts) == 2
        assert timer_fired.is_set()
        assert not material_env.runtime._active_tasks
    finally:
        handle.cancel()
        # 测试拥有这个独立框架执行器；生产共享执行器由框架关闭。
        manager.shutdown_process_pool(wait=True)


async def test_real_executor_rejects_excess_frames_before_prepare(material_env: Any) -> None:
    """真实异步入口拒绝超帧 GIF，既不发 prepare，也不启动上传。"""
    result = await material_env.service.upload_image(
        "a.gif", animated_gif(material_policy.MAX_IMAGE_FRAMES + 1), "image/gif",
    )
    assert not result["success"] and "帧数" in result["error"]
    assert material_env.handler.posts == [] and material_env.puts == []


@pytest.mark.parametrize(("fmt", "name", "mime"), [
    ("PNG", "a.png", "image/png"), ("JPEG", "a.jpg", "image/jpeg"),
    ("GIF", "a.gif", "image/gif"), ("WEBP", "a.webp", "image/webp"),
])
def test_decode_policy_formats_and_metadata(fmt: str, name: str, mime: str) -> None:
    """独立检查 CPU 纯函数，不能只因 Service 返回成功而认为图片校验充分。"""
    content = image_bytes(fmt)
    payload = image_payload(name, content, mime)
    assert payload["file_size"] == len(content) and payload["content_type"] == mime
    assert len(payload["md5"]) == 32 and len(payload["sha1"]) == 40


def test_decode_policy_rejects_invalid_pixels_and_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """类型伪装、坏内容和解码像素超限均是 CPU 校验失败而非网络失败。"""
    with pytest.raises(MaterialError, match="真实图片类型"):
        image_payload("a.png", image_bytes("JPEG"), "image/png")
    with pytest.raises(MaterialError, match="图片内容无效"):
        image_payload("a.png", b"not-an-image", "image/png")
    monkeypatch.setattr(material_policy, "MAX_DECODED_PIXELS", 1)
    with pytest.raises(MaterialError, match="累计解码像素"):
        image_payload("a.png", image_bytes(), "image/png")
