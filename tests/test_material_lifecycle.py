"""素材资源的插件生命周期、共享并发、取消与真实目录加载集成测试。"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.kernel.concurrency import TaskManager

from ..config import QQBotExpandConfig
from ..plugin import QQBotExpandPlugin
from ..services.material_service import QQBotMaterialService
from ..src.material_policy import MaterialError
from ..src.material_upload import MATERIAL_CONCURRENCY, MaterialUploadClient
from .test_material_service import completed, image_bytes, prepared


async def test_resources_belong_to_plugin_and_reload() -> None:
    """默认不创建上传客户端；显式开启后的资源供所有 Service 实例共享。"""
    config = QQBotExpandConfig()
    plugin = QQBotExpandPlugin(config)
    assert plugin.material_upload is None
    await plugin.on_plugin_loaded()
    assert plugin.material_upload is None
    await plugin.on_plugin_unloaded()
    config.features.enable_material_service = True
    await plugin.on_plugin_loaded()
    first = plugin.material_upload
    assert first is not None
    services = [QQBotMaterialService(plugin=plugin), QQBotMaterialService(plugin=plugin)]
    assert services[0] is not services[1]
    assert services[0]._runtime() is services[1]._runtime() is first
    assert first._client is not plugin.http_client
    await plugin.on_plugin_unloaded()
    assert first.is_closed and first._client.is_closed
    assert plugin.material_upload is None
    await plugin.on_plugin_unloaded()
    await plugin.on_plugin_loaded()
    try:
        assert plugin.material_upload is not None
        assert plugin.material_upload is not first
        assert not plugin.material_upload.is_closed
    finally:
        await plugin.on_plugin_unloaded()


async def test_unload_cancels_inflight_and_queued_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """插件卸载取消在途和排队调用，第三个调用在 prepare 前等待共享名额。"""
    config = QQBotExpandConfig()
    config.features.enable_material_service = True
    plugin = QQBotExpandPlugin(config)
    await plugin.on_plugin_loaded()
    runtime = plugin.material_upload
    assert runtime is not None
    entered = 0
    ready = asyncio.Event()

    async def block_upload(*args: Any, **kwargs: Any) -> str:
        """阻塞 PUT，让卸载流程负责取消。"""
        nonlocal entered
        entered += 1
        if entered == MATERIAL_CONCURRENCY:
            ready.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    control = AsyncMock(return_value=prepared())
    monkeypatch.setattr(QQBotMaterialService, "_control", control)
    monkeypatch.setattr(runtime, "put_prepared", block_upload)
    manager = TaskManager()
    try:
        async with manager.group(name="material-unload-test", timeout=2) as group:
            tasks = [group.create_task(
                QQBotMaterialService(plugin=plugin).upload_image("a.png", image_bytes(), "image/png"),
                name=f"material-operation-{index}",
            ).task for index in range(MATERIAL_CONCURRENCY + 1)]
            async with asyncio.timeout(1):
                await ready.wait()
            assert control.await_count == MATERIAL_CONCURRENCY
            await plugin.on_plugin_unloaded()
            for task in tasks:
                assert task is not None
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not runtime._active_tasks
            assert runtime._client.is_closed
            assert plugin.material_upload is None
            assert control.await_count == MATERIAL_CONCURRENCY
    finally:
        await plugin.on_plugin_unloaded()


async def test_canceled_waiter_does_not_consume_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """排队调用取消后不执行 prepare，其他调用正常释放槽并完成。"""
    from ..services import material_service

    runtime = MaterialUploadClient(request_timeout=1, connect_timeout=1)
    config = QQBotExpandConfig()
    config.features.enable_material_service = True
    plugin = QQBotExpandPlugin(config)
    plugin.material_upload = runtime
    ready, release = asyncio.Event(), asyncio.Event()
    entered = 0

    async def put(*args: Any, **kwargs: Any) -> str:
        """占用并发槽，直到测试显式放行。"""
        nonlocal entered
        entered += 1
        if entered == MATERIAL_CONCURRENCY:
            ready.set()
        await release.wait()
        return "a" * 32

    async def control(plugin: Any, method: str, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """仅替换网络边界，保留 Service 的实际流程与操作门限。"""
        data = prepared() if path.endswith("/prepare") else completed()
        return {"success": True, "data": data, "error": None}

    monkeypatch.setattr(runtime, "put_prepared", put)
    monkeypatch.setattr(material_service, "api_request", control)
    manager = TaskManager()
    try:
        async with manager.group(name="material-waiter-test", timeout=2) as group:
            tasks = [group.create_task(
                QQBotMaterialService(plugin=plugin).upload_image("a.png", image_bytes(), "image/png"),
                name=f"material-waiter-{index}",
            ).task for index in range(MATERIAL_CONCURRENCY + 1)]
            async with asyncio.timeout(1):
                await ready.wait()
            waiter = tasks[-1]
            assert waiter is not None
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            for task in tasks[:-1]:
                assert task is not None
                assert (await task)["success"]
        assert entered == MATERIAL_CONCURRENCY
        assert not runtime._active_tasks
    finally:
        await runtime.aclose()


async def test_closed_runtime_rejects_new_work() -> None:
    """关闭资源后不能重新入队或复活客户端。"""
    runtime = MaterialUploadClient(request_timeout=1, connect_timeout=1)
    await runtime.aclose()
    await runtime.aclose()
    with pytest.raises(MaterialError):
        async with runtime.operation():
            raise AssertionError("unreachable")


async def test_unload_sanitizes_close_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """资源关闭失败不能把预签名 URL 写进日志或阻断其他资源清理。"""
    from .. import plugin as plugin_module

    plugin = QQBotExpandPlugin(QQBotExpandConfig())
    runtime = AsyncMock()
    runtime.aclose.side_effect = RuntimeError("https://secret.example/presigned?credential=SECRET")
    plugin.material_upload = runtime
    # logger.warning 为同步 API，使用普通记录函数避免引入未等待协程。
    messages: list[str] = []
    monkeypatch.setattr(plugin_module.logger, "warning", messages.append)
    await plugin.on_plugin_unloaded()
    assert plugin.material_upload is None
    assert messages == ["关闭图片素材上传资源失败"]


async def test_operation_timeout_and_log_filter_cleanup() -> None:
    """完整操作总时限覆盖所有阶段，关闭时移除自身日志过滤器。"""
    runtime = MaterialUploadClient(request_timeout=0.01, connect_timeout=0.01)
    try:
        with pytest.raises(TimeoutError):
            async with runtime.operation():
                await asyncio.Event().wait()
        assert not runtime._active_tasks
    finally:
        await runtime.aclose()
    assert runtime._log_filter not in logging.getLogger("httpx").filters


@pytest.mark.parametrize("enabled", [False, True])
def test_real_plugin_manager_folder_registration(enabled: bool) -> None:
    """在隔离解释器运行真实目录注册/卸载，禁止网络与生产配置读写。"""
    root = Path(__file__).resolve().parents[3]
    script = f'''
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import hashlib
from io import BytesIO
import httpcore
from PIL import Image
from src.kernel.concurrency import get_task_manager
from src.core.components.loader import load_manifest
from src.core.components.registry import get_global_registry
from src.core.managers.plugin_manager import PluginManager
from src.core.managers.service_manager import get_service_manager

class Configs:
    def load_config(self, name, cls):
        config = cls()
        config.features.enable_material_service = {enabled!r}
        config.interaction.enabled = False
        config.managed_panels.enabled = False
        return config

async def check():
    manager = PluginManager()
    path = str(Path("plugins/qqbot_expand").resolve())
    manifest = await load_manifest(path)
    assert manifest is not None
    signature = "qqbot_expand:service:qqbot_material"
    events = SimpleNamespace(register_plugin_handlers=AsyncMock(), unregister_plugin_handlers=AsyncMock())
    with patch("src.core.managers.config_manager.get_config_manager", return_value=Configs()), \\
         patch("src.core.managers.plugin_manager._global_plugin_manager", manager), \\
         patch("src.core.managers.plugin_manager._record_plugin_telemetry_event", new=AsyncMock()), \\
         patch("src.core.managers.event_manager.get_event_manager", return_value=events), \\
         patch("src.kernel.event.get_event_bus", return_value=SimpleNamespace(publish=AsyncMock())), \\
         patch.object(httpcore.AnyIOBackend, "connect_tcp", new=AsyncMock(side_effect=AssertionError("network forbidden"))) as network:
        assert await manager.load_plugin_from_manifest(path, manifest)
        plugin = manager.get_plugin("qqbot_expand")
        assert plugin is not None
        first = get_service_manager().get_service(signature)
        second = get_service_manager().get_service(signature)
        assert first is not None and second is not None and first is not second
        assert first.plugin is second.plugin is plugin
        assert signature in get_global_registry().list_all()
        resource = plugin.material_upload
        assert (resource is not None) is {enabled!r}
        if not {enabled!r}:
            assert not (await first.list_images())["success"]
        else:
            output = BytesIO()
            Image.new("RGB", (2, 2), "red").save(output, format="PNG")
            content = output.getvalue()
            responses = [
                dict(upload_id="probe", expires_in=60,
                     presigned_url="https://bucket-123.cos.ap-guangzhou.myqcloud.com/probe?sign=hidden"),
                dict(image_id="probe_image", url="qqbot-media://image/v1/probe", ttl=7776000),
            ]
            control = AsyncMock(side_effect=responses)
            upload = AsyncMock(return_value=hashlib.md5(content).hexdigest())
            try:
                with patch.object(first, "_control", control), patch.object(resource, "put_prepared", upload):
                    sent = await first.upload_image("probe.png", content, "image/png")
                assert sent["success"], sent
                assert control.await_count == 2 and upload.await_count == 1
            finally:
                get_task_manager().shutdown_process_pool(wait=True)
        assert await manager.unload_plugin("qqbot_expand")
        assert signature not in get_global_registry().list_all()
        assert plugin.material_upload is None
        if resource is not None:
            assert resource.is_closed and resource._client.is_closed
        network.assert_not_awaited()
    print("material-lifecycle-ok")

asyncio.run(check())
'''
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", script], cwd=root,
        capture_output=True, text=True, encoding="utf-8", timeout=90, check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    assert "material-lifecycle-ok" in result.stdout
