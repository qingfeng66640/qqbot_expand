"""补齐短期消息归属、入群回调与互动运行时的状态边界。"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from src.kernel.concurrency import TaskManager

from ..src import join_requests
from ..src.join_requests import JoinRequestRuntime
from .conftest import make_plugin


@pytest.fixture
def task_manager() -> Iterator[TaskManager]:
    """用独立 TaskManager 执行真实任务，并释放其进程池资源。"""
    manager = TaskManager()
    try:
        yield manager
    finally:
        manager.shutdown_process_pool(wait=True, cancel_futures=True)


async def test_join_request_dedup_expires_oldest_without_discarding_fresh_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入群申请在 TTL 内去重，恰好到期时删除旧项但保留后来的新申请。"""
    now = [0.0]
    monkeypatch.setattr(join_requests, "time", SimpleNamespace(monotonic=lambda: now[0]))
    runtime = JoinRequestRuntime(make_plugin(dedup_ttl=10.0))
    assert await runtime.claim("old")
    now[0] = 5.0
    assert await runtime.claim("fresh")
    assert not await runtime.claim("old")
    now[0] = 10.0
    assert await runtime.claim("old")
    assert not await runtime.claim("fresh")
    await runtime.close()
    assert await runtime.claim("fresh")
    await runtime.close()


@pytest.mark.parametrize(("name", "callback"), [(None, Mock()), ("  ", Mock()), ("name", None)])
async def test_join_callback_registration_rejects_invalid_inputs(
    name: Any, callback: Any
) -> None:
    """注册入口拒绝空名称、非字符串名称及不可调用对象。"""
    runtime = JoinRequestRuntime(make_plugin())
    assert await runtime.register(name, callback) is False
    await runtime.close()


async def test_join_callbacks_replace_unregister_and_isolate_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单个同步回调异常不阻断后续异步回调，每个回调都收到独立深拷贝。"""
    runtime = JoinRequestRuntime(make_plugin())
    seen: list[tuple[str, str]] = []
    warning = Mock()
    monkeypatch.setattr(join_requests.logger, "warning", warning)

    def old_callback(params: dict[str, Any]) -> None:
        """旧回调若未被替换会产生可识别的观察值。"""
        seen.append(("old", params["raw_event"]["member"]))

    def broken_callback(params: dict[str, Any]) -> None:
        """修改本地副本后抛错，不能污染其他订阅者。"""
        params["raw_event"]["member"] = "mutated"
        raise RuntimeError("callback failed")

    async def async_callback(params: dict[str, Any]) -> None:
        """正常异步订阅者只观察原始参数。"""
        seen.append(("async", params["raw_event"]["member"]))

    def sync_callback(params: dict[str, Any]) -> None:
        """正常同步回调无需 await，仍然参与顺序分发。"""
        seen.append(("sync", params["raw_event"]["member"]))

    assert await runtime.register("first", old_callback)
    assert not await runtime.register("first", broken_callback)
    assert await runtime.register("first", broken_callback, replace=True)
    assert await runtime.register("second", async_callback)
    assert await runtime.register("third", sync_callback)
    assert not await runtime.unregister("missing")
    params = {"raw_event": {"member": "member-1"}}
    try:
        await runtime.process(params)
        assert seen == [("async", "member-1"), ("sync", "member-1")]
        assert params == {"raw_event": {"member": "member-1"}}
        warning.assert_called_once_with("入群申请回调失败: name=first error=callback failed")
        assert await runtime.unregister("first")
        assert not await runtime.unregister("first")
        assert await runtime.unregister("second")
        assert await runtime.unregister("third")
        await runtime.process(params)
        assert len(seen) == 2
    finally:
        await runtime.close()


async def test_join_close_cancels_task_manager_workers(task_manager: TaskManager) -> None:
    """关闭运行时取消真实 TaskInfo，且清空回调和申请去重记录。"""
    runtime = JoinRequestRuntime(make_plugin())
    entered = asyncio.Event()

    async def wait_for_cancellation(params: dict[str, Any]) -> None:
        """模拟尚未完成的受信异步订阅者。"""
        entered.set()
        await asyncio.Event().wait()

    await runtime.register("wait", wait_for_cancellation)
    await runtime.claim("request-1")
    info = task_manager.create_task(runtime.process({}), name="coverage-join-cancel")
    runtime.track_task(info)
    assert info.task is not None
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        await runtime.close()
        with pytest.raises(asyncio.CancelledError):
            await info.task
        assert runtime._tasks == []
        assert await runtime.claim("request-1")
        assert await runtime.register("wait", wait_for_cancellation)
    finally:
        info.cancel()
        await asyncio.gather(info.task, return_exceptions=True)
        await runtime.close()
