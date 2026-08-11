"""QQ 互动路由、去重、EventHandler 调度与 worker 测试。"""

from __future__ import annotations

import ast
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.kernel.concurrency import TaskInfo, get_task_manager
from src.kernel.event import EventDecision

from ..handlers import interaction_event_handler as handler_module
from ..handlers.interaction_event_handler import QQBotInteractionEventHandler
from ..services.interaction_service import QQBotInteractionService
from ..src.interaction import CallbackResult, InteractionContext, InteractionRuntime
from .conftest import FakeHttpClient, FakeResponse, make_plugin


def make_context(**overrides: object) -> InteractionContext:
    """构造标准互动上下文。"""
    values = {
        "event_id": "i1",
        "interaction_id": "i1",
        "interaction_type": 11,
        "scene": "c2c",
        "chat_type": 2,
        "target_type": "user",
        "target_id": "u1",
        "operator_openid": "u1",
        "button_id": "b1",
        "button_data": "demo:run:payload",
        "raw_event": {"id": "i1"},
    }
    values.update(overrides)
    return InteractionContext(**values)  # type: ignore[arg-type]


def make_params(**overrides: object) -> dict[str, object]:
    """构造适配器固定 11 键参数。"""
    context = make_context(**overrides)
    return {
        "event_id": context.event_id,
        "interaction_id": context.interaction_id,
        "interaction_type": context.interaction_type,
        "scene": context.scene,
        "chat_type": context.chat_type,
        "target_type": context.target_type,
        "target_id": context.target_id,
        "operator_openid": context.operator_openid,
        "button_id": context.button_id,
        "button_data": context.button_data,
        "raw_event": context.raw_event,
    }


class TestRouting:
    """精确路由、权限和异常归一化。"""

    async def test_success_and_payload_can_be_empty(self) -> None:
        plugin = make_plugin()
        callback = AsyncMock(return_value=CallbackResult(True, 3, "done"))
        plugin.interaction_runtime.register("demo", "run", callback)

        result = await plugin.interaction_runtime.route(
            make_context(button_data="demo:run:")
        )

        assert result == CallbackResult(True, 3, "done")
        assert result.handled is True
        callback.assert_awaited_once()
        assert callback.await_args.args[1] == ""

    async def test_unknown_and_malformed_are_unhandled(self) -> None:
        runtime = make_plugin().interaction_runtime
        unknown = await runtime.route(make_context(button_data="none:run:x"))
        malformed = await runtime.route(make_context(button_data="bad"))
        non_string = await runtime.route(
            make_context(button_data=1)  # type: ignore[arg-type]
        )

        assert unknown == CallbackResult(False, 1, None)
        assert malformed == CallbackResult(False, 1, None)
        assert non_string == CallbackResult(False, 1, None)

    async def test_permission_supports_sync_and_async(self) -> None:
        runtime = make_plugin().interaction_runtime
        callback = AsyncMock(return_value=0)
        runtime.register("demo", "sync", callback, lambda _ctx, _payload: False)

        async def deny(_context: InteractionContext, _payload: str) -> bool:
            return False

        runtime.register("demo", "async", callback, deny)
        assert await runtime.route(
            make_context(button_data="demo:sync:x")
        ) == CallbackResult(False, 4, None)
        assert await runtime.route(
            make_context(button_data="demo:async:x")
        ) == CallbackResult(False, 4, None)
        callback.assert_not_awaited()

    async def test_callback_exception_and_timeout_return_code_one(self) -> None:
        plugin = make_plugin(callback_timeout=0.01)

        async def boom(_context: InteractionContext, _payload: str) -> int:
            raise RuntimeError("boom")

        async def slow(_context: InteractionContext, _payload: str) -> int:
            await asyncio.sleep(1)
            return 0

        plugin.interaction_runtime.register("demo", "boom", boom)
        plugin.interaction_runtime.register("demo", "slow", slow)
        boom_result = await plugin.interaction_runtime.route(
            make_context(button_data="demo:boom:x")
        )
        slow_result = await plugin.interaction_runtime.route(
            make_context(button_data="demo:slow:x")
        )

        assert boom_result == CallbackResult(False, 1, None)
        assert slow_result == CallbackResult(False, 1, None)

    def test_register_validation_replace_and_protected_unregister(self) -> None:
        runtime = make_plugin().interaction_runtime
        def original(_ctx: InteractionContext, _payload: str) -> int:
            return 0

        def replacement(_ctx: InteractionContext, _payload: str) -> int:
            return 1

        assert runtime.register("demo", "run", original) is True
        assert runtime.register("demo", "run", replacement) is False
        assert runtime.unregister("demo", "run", replacement) is False
        assert runtime.register("demo", "run", replacement, replace=True) is True
        assert runtime.unregister("demo", "run", original) is False
        assert runtime.unregister("demo", "run", replacement) is True
        assert runtime.unregister("demo", "run") is False
        with pytest.raises(ValueError):
            runtime.register("bad:name", "run", original)

    async def test_closed_runtime_rejects_registration(self) -> None:
        runtime = make_plugin().interaction_runtime
        await runtime.close()

        with pytest.raises(RuntimeError, match="已关闭"):
            runtime.register("demo", "run", lambda _ctx, _payload: 0)

    async def test_register_guards_and_result_validation(self) -> None:
        runtime = make_plugin().interaction_runtime
        with pytest.raises(TypeError):
            runtime.register("demo", "run", None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            runtime.register("demo", "run", lambda _ctx, _payload: 0, "bad")  # type: ignore[arg-type]
        runtime.register("demo", "bad_code", lambda _ctx, _payload: 6)
        runtime.register(
            "demo",
            "bad_message",
            lambda _ctx, _payload: CallbackResult(True, 0, 1),  # type: ignore[arg-type]
        )
        runtime.register(
            "demo", "bad_handled", lambda _ctx, _payload: CallbackResult(1, 0, None)
        )  # type: ignore[arg-type]
        runtime.register(
            "demo",
            "explicit_unhandled",
            lambda _ctx, _payload: CallbackResult(False, 3, None),
        )
        assert (
            await runtime.route(make_context(button_data="demo:bad_code:x"))
        ) == CallbackResult(False, 1, None)
        assert (
            await runtime.route(make_context(button_data="demo:bad_message:x"))
        ) == CallbackResult(False, 1, None)
        assert (
            await runtime.route(make_context(button_data="demo:bad_handled:x"))
        ) == CallbackResult(False, 1, None)
        assert await runtime.route(
            make_context(button_data="demo:explicit_unhandled:x")
        ) == CallbackResult(False, 3, None)

    async def test_button_data_length_and_route_characters(self) -> None:
        plugin = make_plugin(button_data_max_length=8)
        runtime = plugin.interaction_runtime
        assert (
            await runtime.route(make_context(button_data="demo:run:x"))
        ).ack_code == 1
        plugin.config.interaction.button_data_max_length = 100
        assert (
            await runtime.route(make_context(button_data="bad!:run:x"))
        ).ack_code == 1


class TestClaims:
    """处理中 claim 与 ACK consumed 相互独立。"""

    async def test_processing_duplicate_release_and_ttl(self) -> None:
        now = [0.0]
        plugin = make_plugin(dedup_ttl=10.0)
        runtime = InteractionRuntime(plugin, clock=lambda: now[0])
        assert await runtime.claim_processing("i1") is True
        assert await runtime.claim_processing("i1") is False
        await runtime.release_processing("i1")
        assert await runtime.claim_processing("i1") is False
        now[0] = 11.0
        assert await runtime.claim_processing("i1") is True

    async def test_schedule_failure_can_forget_processed_claim(self) -> None:
        runtime = make_plugin().interaction_runtime
        assert await runtime.claim_processing("i1") is True
        await runtime.release_processing("i1", forget_processed=True)
        assert await runtime.claim_processing("i1") is True

    async def test_ack_ttl_and_capacity(self) -> None:
        now = [0.0]
        plugin = make_plugin(dedup_ttl=10.0, dedup_capacity=2)
        runtime = InteractionRuntime(plugin, clock=lambda: now[0])
        assert await runtime.claim_ack("a") == "claimed"
        assert await runtime.claim_ack("a") == "duplicate"
        assert await runtime.claim_ack("b") == "claimed"
        assert await runtime.claim_ack("c") == "capacity"
        assert await runtime.claim_ack("a") == "duplicate"
        now[0] = 11.0
        assert await runtime.claim_ack("b") == "claimed"

    async def test_ack_capacity_refuses_network_request(
        self, patch_send_handler
    ) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client, dedup_capacity=1)
        service = QQBotInteractionService(plugin)

        first = await service.ack("i1")
        full = await service.ack("i2")

        assert first["success"] is True
        assert full["success"] is False
        assert full["duplicate"] is False
        assert "容量已满" in full["error"]
        assert len(client.calls) == 1

    async def test_external_ack_cannot_race_worker(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)
        service = QQBotInteractionService(plugin)
        assert await plugin.interaction_runtime.claim_processing("i1") is True

        result = await service.ack("i1")

        assert result["success"] is False
        assert "正在由 EventHandler 处理" in result["error"]
        assert not client.calls

    async def test_duplicate_ack_never_hits_network(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)
        service = QQBotInteractionService(plugin)

        first = await service.ack("i1")
        duplicate = await service.ack("i1")

        assert first["success"] is True
        assert first["duplicate"] is False
        assert duplicate["duplicate"] is True
        assert len(client.calls) == 1


class TestWorker:
    """worker ACK 类型、消息回复与释放行为。"""

    @pytest.mark.parametrize("interaction_type", [11, 12])
    async def test_required_types_ack_once(
        self, monkeypatch, interaction_type: int
    ) -> None:
        plugin = make_plugin()
        plugin.interaction_runtime.register("demo", "run", lambda _ctx, _payload: 0)
        ack = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(QQBotInteractionService, "_ack", ack)
        await plugin.interaction_runtime.claim_processing("i1")

        await plugin.interaction_runtime.process(
            make_context(interaction_type=interaction_type)
        )

        ack.assert_awaited_once_with("i1", 0, owned_by_worker=True)
        assert await plugin.interaction_runtime.claim_processing("i1") is False

    async def test_non_ack_type_routes_without_ack(self, monkeypatch) -> None:
        plugin = make_plugin()
        callback = AsyncMock(return_value=0)
        plugin.interaction_runtime.register("demo", "run", callback)
        ack = AsyncMock()
        monkeypatch.setattr(QQBotInteractionService, "_ack", ack)

        await plugin.interaction_runtime.process(make_context(interaction_type=13))

        callback.assert_awaited_once()
        ack.assert_not_awaited()

    async def test_message_uses_event_id_and_never_msg_id(self, monkeypatch) -> None:
        plugin = make_plugin()
        plugin.interaction_runtime.register(
            "demo", "run", lambda _ctx, _payload: CallbackResult(True, 0, "hello")
        )
        monkeypatch.setattr(QQBotInteractionService, "ack", AsyncMock())
        message_service = SimpleNamespace(
            send_text=AsyncMock(return_value={"success": True})
        )
        monkeypatch.setattr(
            "src.app.plugin_system.api.service_api.get_service",
            lambda signature: message_service,
        )

        await plugin.interaction_runtime.process(make_context())

        args = message_service.send_text.await_args
        assert args.args == ("user", "u1", "hello")
        assert args.kwargs == {"event_id": "i1"}
        assert "msg_id" not in args.kwargs

    async def test_message_skips_unknown_target_and_missing_service(
        self, monkeypatch
    ) -> None:
        plugin = make_plugin()
        plugin.interaction_runtime.register(
            "demo", "run", lambda _ctx, _payload: CallbackResult(True, 0, "hello")
        )
        monkeypatch.setattr(QQBotInteractionService, "ack", AsyncMock())
        get_service = AsyncMock()
        monkeypatch.setattr(
            "src.app.plugin_system.api.service_api.get_service", get_service
        )
        await plugin.interaction_runtime.process(make_context(target_type="guild"))
        get_service.assert_not_called()

        monkeypatch.setattr(
            "src.app.plugin_system.api.service_api.get_service", lambda _signature: None
        )
        await plugin.interaction_runtime.process(make_context())

    async def test_process_contains_ack_exception(self, monkeypatch) -> None:
        plugin = make_plugin()
        plugin.interaction_runtime.register("demo", "run", lambda _ctx, _payload: 0)
        monkeypatch.setattr(
            QQBotInteractionService,
            "ack",
            AsyncMock(side_effect=RuntimeError("ack failed")),
        )
        result = await plugin.interaction_runtime.process(make_context())
        assert result == CallbackResult(True, 0, None)


    async def test_process_contains_message_exception(self, monkeypatch) -> None:
        plugin = make_plugin()
        plugin.interaction_runtime.register(
            "demo", "run", lambda _ctx, _payload: CallbackResult(True, 0, "hello")
        )
        monkeypatch.setattr(QQBotInteractionService, "ack", AsyncMock())
        message_service = SimpleNamespace(
            send_text=AsyncMock(side_effect=RuntimeError("send failed"))
        )
        monkeypatch.setattr(
            "src.app.plugin_system.api.service_api.get_service",
            lambda _signature: message_service,
        )

        result = await plugin.interaction_runtime.process(make_context())

        assert result == CallbackResult(False, 1, None)


class TestEventHandler:
    """EventBus 键集契约与 TaskManager 调度。"""

    async def test_params_keys_unchanged_and_task_manager_used(
        self, monkeypatch
    ) -> None:
        plugin = make_plugin()
        handler = QQBotInteractionEventHandler(plugin)
        params = make_params()
        original_keys = set(params)
        captured: dict[str, object] = {}

        class FakeManager:
            def create_task(self, coro, name=None, daemon=False):
                captured.update(coro=coro, name=name, daemon=daemon)
                return TaskInfo(
                    task_id="task-1", name=name, coro=coro, daemon=daemon
                )

        monkeypatch.setattr(handler_module, "get_task_manager", lambda: FakeManager())
        decision, returned = await handler.execute(
            "qqbot_adapter.interaction_create", params
        )
        await captured["coro"]  # type: ignore[misc]

        assert decision is EventDecision.SUCCESS
        assert returned is params
        assert set(returned) == original_keys
        assert captured["daemon"] is False
        assert captured["name"] == "qqbot_interaction:i1"

    async def test_extra_adapter_fields_do_not_skip_event(self, monkeypatch) -> None:
        plugin = make_plugin()
        handler = QQBotInteractionEventHandler(plugin)
        params = make_params()
        params["adapter_trace"] = "extra"
        captured: dict[str, object] = {}

        class FakeManager:
            def create_task(self, coro, name=None, daemon=False):
                captured["coro"] = coro
                return TaskInfo(task_id="task-extra", name=name, coro=coro, daemon=daemon)

        monkeypatch.setattr(handler_module, "get_task_manager", lambda: FakeManager())
        decision, returned = await handler.execute(
            "qqbot_adapter.interaction_create", params
        )
        await captured["coro"]  # type: ignore[misc]

        assert decision is EventDecision.SUCCESS
        assert returned is params
        assert returned["adapter_trace"] == "extra"

    async def test_duplicate_dispatch_after_worker_finishes_stays_suppressed(
        self, monkeypatch
    ) -> None:
        plugin = make_plugin()
        handler = QQBotInteractionEventHandler(plugin)
        callback = AsyncMock(return_value=0)
        plugin.interaction_runtime.register("demo", "run", callback)
        monkeypatch.setattr(QQBotInteractionService, "ack", AsyncMock())
        scheduled: list[object] = []

        class FakeManager:
            def create_task(self, coro, name=None, daemon=False):
                scheduled.append(coro)
                return TaskInfo(task_id="task-1", name=name, coro=coro, daemon=daemon)

        monkeypatch.setattr(handler_module, "get_task_manager", lambda: FakeManager())
        first, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params()
        )
        await scheduled[0]  # type: ignore[misc]
        second, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params()
        )

        assert first is EventDecision.SUCCESS
        assert second is EventDecision.PASS
        callback.assert_awaited_once()

    async def test_duplicate_dispatch_passes_without_second_task(
        self, monkeypatch
    ) -> None:
        plugin = make_plugin()
        handler = QQBotInteractionEventHandler(plugin)
        assert await plugin.interaction_runtime.claim_processing("i1") is True
        manager = SimpleNamespace(create_task=pytest.fail)
        monkeypatch.setattr(handler_module, "get_task_manager", lambda: manager)

        decision, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params()
        )

        assert decision is EventDecision.PASS

    async def test_missing_required_key_passes(self) -> None:
        plugin = make_plugin()
        handler = QQBotInteractionEventHandler(plugin)
        params = make_params()
        del params["button_data"]
        decision, returned = await handler.execute(
            "qqbot_adapter.interaction_create", params
        )
        assert decision is EventDecision.PASS
        assert returned is params

    async def test_disabled_bad_id_and_schedule_failure_pass(self, monkeypatch) -> None:
        plugin = make_plugin()
        handler = QQBotInteractionEventHandler(plugin)
        plugin.config.interaction.enabled = False
        decision, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params()
        )
        assert decision is EventDecision.PASS

        plugin.config.interaction.enabled = True
        decision, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params(interaction_id="")
        )
        assert decision is EventDecision.PASS

        class BrokenManager:
            def create_task(self, _coro, **_kwargs):
                raise RuntimeError("schedule failed")

        monkeypatch.setattr(handler_module, "get_task_manager", lambda: BrokenManager())
        decision, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params(interaction_id="i2")
        )
        assert decision is EventDecision.PASS
        assert await plugin.interaction_runtime.claim_processing("i2") is True

    async def test_unexpected_runtime_error_is_contained(self) -> None:
        plugin = make_plugin()
        plugin.interaction_runtime.claim_processing = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        handler = QQBotInteractionEventHandler(plugin)
        decision, _ = await handler.execute(
            "qqbot_adapter.interaction_create", make_params()
        )
        assert decision is EventDecision.PASS


class TestCleanupAndIsolation:
    """卸载清理与插件源码隔离。"""

    async def test_close_cancels_registered_task(self) -> None:
        plugin = make_plugin()
        started = asyncio.Event()

        async def wait_forever() -> None:
            started.set()
            await asyncio.Event().wait()

        manager = get_task_manager()
        info = manager.create_task(wait_forever(), name="interaction-cleanup-test")
        plugin.interaction_runtime.track_task(info)
        await started.wait()
        await plugin.interaction_runtime.close()
        assert info.task is not None
        assert info.task.cancelled()

    def test_no_cross_plugin_source_import(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        imported: set[str] = set()
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
        assert not any("plugins.qqbot_adapter" in name for name in imported)
