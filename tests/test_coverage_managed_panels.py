"""托管面板真实对账的存储故障、Adapter 等待与关闭边界覆盖。"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Coroutine
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import RootModel

from src.app.plugin_system.api import service_api
from src.kernel.concurrency import TaskInfo, TaskManager

from ..src import bridge, managed_panels
from ..src.managed_panels import ManagedPanelRuntime
from .conftest import FakeHttpClient, FakeResponse, FakeSendHandler, make_plugin


PANEL: dict[str, Any] = {
    "items": [{"name": "/help", "desc": "", "type": "command", "only_admin": False}],
    "remark": "managed",
}
OLD_PANEL: dict[str, Any] = {"items": [{"name": "/old", "type": "command"}]}
ZERO_RESULT = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}


def _spec(key: Any = "default") -> dict[str, Any]:
    """提供合法且每次独立的声明式配置。"""
    return {"managed_key": key, "scope": "group", "target_type": "all", "panel": deepcopy(PANEL)}


def _ledger() -> dict[str, Any]:
    """提供只绑定一个既有远端面板的账本。"""
    return {"schema_version": 1, "bindings": {"default": {"panel_id": "owned/1", "desired_fingerprint": "old"}}}


def _runtime(client: FakeHttpClient, items: list[Any] | None = None) -> ManagedPanelRuntime:
    """组装真实 Runtime 和 Service，所有网络出口均为记录型替身。"""
    plugin = make_plugin(http_client=client, enable_menu_panel_service=True)
    plugin.config.managed_panels = SimpleNamespace(enabled=True, items=[_spec()] if items is None else items)
    return ManagedPanelRuntime(plugin)


class MemoryLedger:
    """以复制语义模拟 JSON 存储，不访问生产账本。"""

    def __init__(self) -> None:
        """初始化存储状态及可注入的读写故障。"""
        self.data: Any = None
        self.load_error: Exception | None = None
        self.save_error: Exception | None = None
        self.loads = 0
        self.loaded: list[Any] = []
        self.saves: list[dict[str, Any]] = []

    async def load(self, store: str, name: str) -> Any:
        """读取独立副本并保留返回对象以检查运行时的回滚。"""
        assert (store, name) == ("qqbot_expand", "managed_panels_ledger")
        self.loads += 1
        if self.load_error is not None:
            raise self.load_error
        snapshot = deepcopy(self.data)
        self.loaded.append(snapshot)
        return snapshot

    async def save(self, store: str, name: str, value: dict[str, Any]) -> None:
        """记录保存尝试，失败时不改变持久化值。"""
        assert (store, name) == ("qqbot_expand", "managed_panels_ledger")
        self.saves.append(deepcopy(value))
        if self.save_error is not None:
            raise self.save_error
        self.data = deepcopy(value)


@pytest.fixture(autouse=True)
def ledger_store(monkeypatch: pytest.MonkeyPatch) -> MemoryLedger:
    """为本文件每个测试强制隔离存储读写边界。"""
    store = MemoryLedger()
    monkeypatch.setattr(managed_panels.storage_api, "load_json", store.load)
    monkeypatch.setattr(managed_panels.storage_api, "save_json", store.save)
    return store


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"schema_version": 99, "bindings": {}},
        {"schema_version": 1, "bindings": []},
        {"schema_version": 1},
        {"schema_version": 1, "bindings": {7: {"panel_id": "p1"}}},
        {"schema_version": 1, "bindings": {"bad key": {"panel_id": "p1"}}},
        {"schema_version": 1, "bindings": {"default": []}},
        {"schema_version": 1, "bindings": {"default": {"panel_id": 1}}},
        {"schema_version": 1, "bindings": {"default": {"panel_id": " "}}},
        {"schema_version": 1, "bindings": {"default": {"panel_id": "p1", "desired_fingerprint": None}}},
    ],
)
async def test_corrupt_ledger_stops_before_any_api(
    raw: Any, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """账本顶层和绑定结构任一损坏，不能退化为空账本重新创建。"""
    ledger_store.data = deepcopy(raw)
    client = FakeHttpClient()

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert ledger_store.loads == 1
    assert ledger_store.data == raw
    assert ledger_store.saves == []
    assert client.calls == []
    assert patch_send_handler.posts == []


async def test_ledger_read_exception_stops_all_remote_io(
    ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """存储读失败保守停止，不能创建、查询或保存空账本。"""
    ledger_store.load_error = OSError("unreadable ledger")
    client = FakeHttpClient()

    result = await _runtime(client, [_spec("one"), _spec("two")]).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 2}
    assert ledger_store.loads == 1
    assert ledger_store.saves == []
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("invalid", [None, _spec(None), _spec("bad key"), {**_spec("bad-scope"), "scope": "private"}])
async def test_invalid_specs_abort_valid_specs_before_loading_ledger(
    invalid: Any, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """标量、空 key、非法 key 或场景会使整轮配置原子拒绝。"""
    client = FakeHttpClient()

    result = await _runtime(client, [_spec(), invalid]).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert ledger_store.loads == 0
    assert ledger_store.saves == []
    assert client.calls == []
    assert patch_send_handler.posts == []


async def test_pydantic_spec_uses_real_service_and_persists_normalized_binding(
    ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """model_dump 配置经过真实校验与 Service 后才确立所有权。"""
    client = FakeHttpClient()
    patch_send_handler.post_result = {"panel_id": " new-panel "}
    model = RootModel[dict[str, Any]](root=_spec())
    runtime = _runtime(client, [model])

    result = await runtime.reconcile_once()

    assert result == {**ZERO_RESULT, "created": 1}
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][0].endswith("/v2/panels")
    assert patch_send_handler.posts[0][2] == {"scope": "group", "target_type": "all", "panel": PANEL}
    binding = ledger_store.data["bindings"]["default"]
    assert binding["panel_id"] == "new-panel"
    assert binding["desired_fingerprint"] == runtime._fingerprint(PANEL)
    assert binding["last_success_at"]
    assert len(ledger_store.saves) == 1
    assert client.calls == []


@pytest.mark.parametrize("payload", [{}, {"panel_id": 1}, {"panel_id": " "}, {"code": 30001, "message": "bad request"}])
async def test_failed_create_does_not_claim_or_scan_panels(
    payload: dict[str, Any], ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """失败或缺少有效 panel_id 的创建响应不得成为 ownership 绑定。"""
    client = FakeHttpClient()
    patch_send_handler.post_result = payload

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][0].endswith("/v2/panels")
    assert ledger_store.data is None
    assert ledger_store.saves == []
    assert client.calls == []


async def test_create_save_failure_keeps_orphan_unclaimed_without_compensation(
    ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """真实创建后保存失败不删除、不枚举、不偷偷认领远端面板。"""
    client = FakeHttpClient()
    patch_send_handler.post_result = {"panel_id": "orphan"}
    ledger_store.data = {"schema_version": 1, "bindings": {}}
    original = deepcopy(ledger_store.data)
    ledger_store.save_error = OSError("disk full")

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert ledger_store.data == original
    assert ledger_store.loaded[0] == original
    assert len(ledger_store.saves) == 1
    assert ledger_store.saves[0]["bindings"]["default"]["panel_id"] == "orphan"
    assert len(patch_send_handler.posts) == 1
    assert client.calls == []


@pytest.mark.parametrize("payload", [[], {}, {"panel": None}, {"panel": {"items": [False]}}])
async def test_invalid_remote_panel_does_not_trigger_update_or_replacement(
    payload: Any, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """查询成功但内容不可校验时停止，不能当作面板不存在。"""
    ledger_store.data = _ledger()
    client = FakeHttpClient([FakeResponse(200, payload)])

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert ledger_store.data == _ledger()
    assert ledger_store.saves == []
    assert [(call["method"], call["url"].rsplit("/", 1)[-1]) for call in client.calls] == [("GET", "owned%2F1")]
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_non_404_read_failure_preserves_owned_panel(
    status: int, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """权限、限频和服务错误都不能启动替代创建。"""
    ledger_store.data = _ledger()
    client = FakeHttpClient([FakeResponse(status, {})])

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert len(client.calls) == 1 and client.calls[0]["method"] == "GET"
    assert ledger_store.data == _ledger()
    assert ledger_store.saves == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("save_failure", [False, True])
async def test_update_failure_does_not_change_persisted_ownership(
    save_failure: bool, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """远端 PUT 失败或成功后保存失败均报告失败，不删除或创建面板。"""
    ledger_store.data = _ledger()
    if save_failure:
        ledger_store.save_error = OSError("disk full")
    client = FakeHttpClient([
        FakeResponse(200, {"panel": OLD_PANEL}), FakeResponse(200 if save_failure else 403, {}),
    ])

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert [call["method"] for call in client.calls] == ["GET", "PUT"]
    assert all(call["url"].endswith("/v2/panels/owned%2F1") for call in client.calls)
    assert client.calls[1]["json"] == {"panel": PANEL}
    assert ledger_store.data == _ledger()
    assert len(ledger_store.saves) == int(save_failure)
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("save_failure", [False, True])
async def test_failed_replacement_restores_original_binding(
    save_failure: bool, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """明确 404 后重建或保存失败时恢复内存原绑定，不扫描或补偿删除。"""
    ledger_store.data = _ledger()
    client = FakeHttpClient([FakeResponse(404, {})])
    patch_send_handler.post_result = {"panel_id": "replacement"} if save_failure else {}
    if save_failure:
        ledger_store.save_error = OSError("disk full")

    result = await _runtime(client).reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert ledger_store.loaded[0] == _ledger()
    assert ledger_store.data == _ledger()
    assert len(ledger_store.saves) == int(save_failure)
    assert len(client.calls) == 1 and client.calls[0]["method"] == "GET"
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][0].endswith("/v2/panels")


async def test_closed_runtime_never_loads_or_writes(
    ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """已关闭实例的显式对账和调度均无副作用。"""
    client = FakeHttpClient()
    runtime = _runtime(client)
    await runtime.close()

    assert await runtime.reconcile_once() == ZERO_RESULT
    assert await runtime.schedule() is False
    assert ledger_store.loads == 0
    assert ledger_store.saves == []
    assert client.calls == []
    assert patch_send_handler.posts == []


async def test_close_during_ledger_load_stops_before_first_panel(
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """加载账本期间关闭会命中循环前检查，不继续发创建请求。"""
    client = FakeHttpClient()
    runtime = _runtime(client)

    async def load_then_close(store: str, name: str) -> Any:
        """在真实存储 await 边界模拟卸载。"""
        result = await ledger_store.load(store, name)
        await runtime.close()
        return result

    monkeypatch.setattr(managed_panels.storage_api, "load_json", load_then_close)

    assert await runtime.reconcile_once() == ZERO_RESULT
    assert ledger_store.loads == 1
    assert ledger_store.saves == []
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("status", [200, 404])
async def test_close_during_owned_lookup_prevents_update_or_replacement(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """查询期间关闭分别阻断内容更新和 404 替代创建。"""
    ledger_store.data = _ledger()
    client = FakeHttpClient([FakeResponse(status, {"panel": OLD_PANEL})])
    runtime = _runtime(client)
    original_request = client.request

    async def request_then_close(
        method: str, url: str, *, headers: dict[str, str], json: Any = None,
    ) -> FakeResponse:
        """在真实查询边界返回结果前关闭实例。"""
        response = await original_request(method, url, headers=headers, json=json)
        await runtime.close()
        return response

    monkeypatch.setattr(client, "request", request_then_close)

    result = await runtime.reconcile_once()

    assert result == {**ZERO_RESULT, "failed": 1}
    assert ledger_store.loaded[0] == _ledger()
    assert ledger_store.data == _ledger()
    assert ledger_store.saves == []
    assert len(client.calls) == 1 and client.calls[0]["method"] == "GET"
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("gate", ["disabled", "service-disabled", "empty"])
async def test_schedule_gates_do_not_start_tasks(
    gate: str, ledger_store: MemoryLedger, patch_send_handler: FakeSendHandler,
) -> None:
    """托管开关、Service 开关和空配置分别拒绝后台调度。"""
    client = FakeHttpClient()
    runtime = _runtime(client)
    if gate == "disabled":
        runtime.plugin.config.managed_panels.enabled = False
    elif gate == "service-disabled":
        runtime.plugin.config.features.enable_menu_panel_service = False
    else:
        runtime.plugin.config.managed_panels.items = []

    assert await runtime.schedule() is False
    assert runtime._tasks == {}
    assert ledger_store.loads == 0
    assert client.calls == []
    assert patch_send_handler.posts == []


async def test_schedule_failure_closes_unstarted_coroutine(
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """TaskManager 创建失败时传播原异常并关闭协程，不能漏任务或访问远端。"""
    client = FakeHttpClient()
    runtime = _runtime(client)
    coroutines: list[Coroutine[Any, Any, Any]] = []

    class RejectingTaskManager:
        """模拟调度器在提交阶段失败，而不替换对账逻辑。"""

        def create_task(
            self, coroutine: Coroutine[Any, Any, Any], *, name: str, daemon: bool,
        ) -> TaskInfo:
            """记录待提交协程后模拟调度失败。"""
            assert name == "qqbot_expand_managed_panels_reconcile"
            assert daemon is True
            coroutines.append(coroutine)
            raise RuntimeError("scheduler unavailable")

    manager = RejectingTaskManager()
    monkeypatch.setattr(managed_panels, "get_task_manager", lambda: manager)

    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        await runtime.schedule()

    assert len(coroutines) == 1
    assert inspect.getcoroutinestate(coroutines[0]) == inspect.CORO_CLOSED
    assert runtime._tasks == {}
    assert runtime._closed is False
    assert ledger_store.loads == 0
    assert client.calls == []
    assert patch_send_handler.posts == []


async def test_scheduled_reconcile_waits_for_ready_before_storage_and_qq(
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """真实面板 Service 不会在 Gateway 就绪前读账本或请求 QQ。"""
    ledger_store.data = _ledger()
    client = FakeHttpClient([FakeResponse(200, {"panel": OLD_PANEL}), FakeResponse(200, {})])
    runtime = _runtime(client)
    observed_activity = asyncio.Event()
    ready = asyncio.Event()
    timeouts: list[float] = []
    original_request = client.request

    async def wait_until_ready(*, timeout: float) -> bool:
        """保持 Gateway 未就绪，直到检查完成。"""
        timeouts.append(timeout)
        observed_activity.set()
        await ready.wait()
        return True

    async def record_request(
        method: str, url: str, *, headers: dict[str, str], json: Any = None,
    ) -> FakeResponse:
        """即使旧实现绕过等待，测试也会被提前请求唤醒。"""
        response = await original_request(method, url, headers=headers, json=json)
        observed_activity.set()
        return response

    def get_service(signature: str) -> SimpleNamespace:
        """仅提供约定签名的 Adapter Service。"""
        assert signature == bridge.SERVICE_SIGNATURE
        return SimpleNamespace(wait_until_ready=wait_until_ready)

    monkeypatch.setattr(service_api, "get_service", get_service)
    monkeypatch.setattr(client, "request", record_request)
    try:
        assert await runtime.schedule() is True
        assert await runtime.schedule() is False
        task = next(iter(runtime._tasks.values())).task
        assert task is not None
        async with asyncio.timeout(5):
            await observed_activity.wait()
            assert client.calls == []
            assert patch_send_handler.posts == []
            assert ledger_store.loads == 0
            assert ledger_store.saves == []
            assert timeouts == [60.0]
            ready.set()
            await task
        assert [call["method"] for call in client.calls] == ["GET", "PUT"]
        assert client.calls[1]["json"] == {"panel": PANEL}
        assert patch_send_handler.posts == []
        assert ledger_store.loads == 1
        assert len(ledger_store.saves) == 1
        assert ledger_store.data["bindings"]["default"]["panel_id"] == "owned/1"
    finally:
        await runtime.close()


@pytest.mark.parametrize("state", ["timeout", "unregistered", "legacy"])
async def test_readiness_unavailable_skips_scheduled_reconciliation(
    state: str,
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """就绪超时、服务未注册或版本过旧均只告警一次，不试探 QQ API。"""
    client = FakeHttpClient()
    runtime = _runtime(client)
    wait = AsyncMock(return_value=False)  # Adapter 合约用 False 表示超时或失败。
    services = {
        "timeout": SimpleNamespace(wait_until_ready=wait),
        "unregistered": None,
        "legacy": SimpleNamespace(),
    }
    get_service = Mock(return_value=services[state])
    resolve_handler = Mock(return_value=patch_send_handler)
    warnings: list[str] = []
    monkeypatch.setattr(service_api, "get_service", get_service)
    monkeypatch.setattr(bridge, "resolve_send_handler", resolve_handler)
    monkeypatch.setattr(managed_panels.logger, "warning", warnings.append)

    try:
        assert await runtime.schedule() is True
        task = next(iter(runtime._tasks.values())).task
        assert task is not None
        async with asyncio.timeout(5):
            await task
    finally:
        await runtime.close()

    get_service.assert_called_once_with(bridge.SERVICE_SIGNATURE)
    (warning,) = warnings
    assert "对账已跳过" in warning
    if state == "timeout":
        wait.assert_awaited_once_with(timeout=60.0)
        assert "未就绪" in warning
    else:
        wait.assert_not_called()
        assert bridge.SERVICE_SIGNATURE in warning
        assert "wait_until_ready" in warning
        assert "已启用" in warning and "配套版本" in warning
    resolve_handler.assert_not_called()
    assert ledger_store.loads == 0
    assert ledger_store.saves == []
    assert patch_send_handler.posts == []
    assert client.calls == []


@pytest.fixture
def ready_adapter(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """为不测试就绪状态的任务提供兼容的 Adapter Service。"""
    wait = AsyncMock(return_value=True)
    service = SimpleNamespace(wait_until_ready=wait)
    monkeypatch.setattr(service_api, "get_service", lambda _signature: service)
    return wait


async def test_adapter_withdrawn_during_reconciliation_does_not_replay(
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
    ready_adapter: AsyncMock,
) -> None:
    """首个绑定更新后 Adapter 撤走，后续创建正常失败且不重放整轮。"""
    ledger_store.data = _ledger()
    client = FakeHttpClient([FakeResponse(200, {"panel": OLD_PANEL}), FakeResponse(200, {})])
    runtime = _runtime(client, [_spec(), _spec("second")])
    resolve_handler = Mock(side_effect=[patch_send_handler, patch_send_handler, None])
    errors: list[str] = []
    summaries: list[str] = []
    monkeypatch.setattr(bridge, "resolve_send_handler", resolve_handler)
    monkeypatch.setattr(managed_panels.logger, "error", errors.append)
    monkeypatch.setattr(managed_panels.logger, "info", summaries.append)

    try:
        assert await runtime.schedule() is True
        task = next(iter(runtime._tasks.values())).task
        assert task is not None
        async with asyncio.timeout(5):
            await task
    finally:
        await runtime.close()

    ready_adapter.assert_awaited_once_with(timeout=60.0)
    assert resolve_handler.call_count == 3
    assert ledger_store.loads == 1
    assert len(ledger_store.saves) == 1
    assert list(ledger_store.data["bindings"]) == ["default"]
    assert [call["method"] for call in client.calls] == ["GET", "PUT"]
    assert client.calls[1]["json"] == {"panel": PANEL}
    assert patch_send_handler.posts == []
    (error,) = errors
    assert "创建托管面板失败: key=second" in error
    (summary,) = summaries
    assert "created=0 updated=1 unchanged=0 failed=1" in summary


@pytest.mark.usefixtures("ready_adapter")
async def test_task_manager_cancellation_reset_and_reschedule_are_single_flight(
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """真实 TaskManager 启动、关闭并重置真实对账，旧任务不能留下后台写入。"""
    client = FakeHttpClient()
    runtime = _runtime(client)
    manager = TaskManager()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    patch_send_handler.post_result = {"panel_id": "created-after-reset"}

    async def blocking_load(store: str, name: str) -> Any:
        """把任务阻塞在存储边界，以验证卸载能取消真实对账。"""
        started.set()
        try:
            await release.wait()
            return await ledger_store.load(store, name)
        finally:
            cancelled.set()

    monkeypatch.setattr(managed_panels, "get_task_manager", lambda: manager)
    monkeypatch.setattr(managed_panels.storage_api, "load_json", blocking_load)
    try:
        assert await runtime.schedule() is True
        info = next(iter(runtime._tasks.values()))
        assert await runtime.schedule() is False
        async with asyncio.timeout(5):
            await started.wait()
            await runtime.close()
        assert info.task is not None and info.task.cancelled()
        assert cancelled.is_set()
        assert runtime._tasks == {}
        assert patch_send_handler.posts == []
        assert ledger_store.saves == []
        await runtime.reset()
        assert runtime._closed is False
        monkeypatch.setattr(managed_panels.storage_api, "load_json", ledger_store.load)
        assert await runtime.schedule() is True
        restarted = next(iter(runtime._tasks.values()))
        assert restarted.task is not None
        async with asyncio.timeout(5):
            await restarted.task
        assert runtime._tasks == {}
        assert ledger_store.data["bindings"]["default"]["panel_id"] == "created-after-reset"
        assert len(patch_send_handler.posts) == 1
        assert len(ledger_store.saves) == 1
        assert client.calls == []
    finally:
        await runtime.close()


@pytest.mark.usefixtures("ready_adapter")
async def test_close_handles_task_finished_before_done_callback(
    monkeypatch: pytest.MonkeyPatch,
    ledger_store: MemoryLedger,
    patch_send_handler: FakeSendHandler,
) -> None:
    """存储信号唤醒先于完成回调时，close 可清理已完成的真实调度任务。"""
    client = FakeHttpClient()
    runtime = _runtime(client)
    manager = TaskManager()
    loaded = asyncio.Event()
    patch_send_handler.post_result = {"panel_id": "created"}

    async def signal_load(store: str, name: str) -> Any:
        """返回内存账本并唤醒调用方，之后的替身 IO 不让出执行权。"""
        value = await ledger_store.load(store, name)
        loaded.set()
        return value

    monkeypatch.setattr(managed_panels, "get_task_manager", lambda: manager)
    monkeypatch.setattr(managed_panels.storage_api, "load_json", signal_load)
    try:
        assert await runtime.schedule() is True
        info = next(iter(runtime._tasks.values()))
        async with asyncio.timeout(5):
            await loaded.wait()
            assert info.task is not None and info.task.done()
            await runtime.close()
        assert not info.task.cancelled()
        assert info.task.result() is None
        assert runtime._tasks == {}
        assert ledger_store.data["bindings"]["default"]["panel_id"] == "created"
        assert len(patch_send_handler.posts) == 1
        assert client.calls == []
    finally:
        await runtime.close()
