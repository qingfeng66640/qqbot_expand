"""声明式托管面板 ownership 与对账行为测试。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..services.menu_panel_service import QQBotMenuPanelService
from ..src.errors import ERROR_NOT_FOUND
from ..src.managed_panels import ManagedPanelRuntime
from .conftest import make_plugin


PANEL = {
    "items": [
        {"name": "/help", "desc": "查看帮助", "type": "command"},
        {
            "name": "官网",
            "desc": "打开官网",
            "type": "link",
            "link": "https://example.com",
        },
    ],
    "remark": "固定入口",
}


def _spec(key: str = "default", panel: dict | None = None) -> SimpleNamespace:
    """构造一项合法托管配置。"""
    return SimpleNamespace(
        managed_key=key,
        scope="group",
        target_type="specific",
        user_openids=[],
        group_openids=["group-1"],
        panel=panel or PANEL,
    )


def _plugin(items: list | None = None, *, enabled: bool = True):
    """构造启用菜单 Service 的托管面板插件替身。"""
    plugin = make_plugin(enable_menu_panel_service=True)
    plugin.config.managed_panels = SimpleNamespace(
        enabled=enabled,
        items=items if items is not None else [_spec()],
    )
    return plugin


def _ledger(panel_id: str = "managed-panel") -> dict:
    """构造合法 ownership ledger。"""
    return {
        "schema_version": 1,
        "bindings": {
            "default": {
                "panel_id": panel_id,
                "desired_fingerprint": "old",
                "last_success_at": "2026-08-14T00:00:00+00:00",
            }
        },
    }


class TestManagedPanelReconciliation:
    """对账状态机与非托管面板隔离。"""

    async def test_first_run_only_creates_and_persists_binding(self, monkeypatch) -> None:
        """空账本只创建新面板，不扫描或认领远端既有面板。"""
        runtime = ManagedPanelRuntime(_plugin())
        load = AsyncMock(return_value=None)
        save = AsyncMock()
        create = AsyncMock(
            return_value={"success": True, "data": {"panel_id": "created-1"}, "error": None}
        )
        get = AsyncMock()
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.load_json", load)
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.save_json", save)
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", create)
        monkeypatch.setattr(QQBotMenuPanelService, "get_panel", get)

        result = await runtime.reconcile_once()

        assert result == {"created": 1, "updated": 0, "unchanged": 0, "failed": 0}
        get.assert_not_awaited()
        assert save.await_args.args[2]["bindings"]["default"]["panel_id"] == "created-1"

    async def test_equal_owned_panel_is_noop(self, monkeypatch) -> None:
        """账本绑定面板内容相同时只查询，不更新。"""
        runtime = ManagedPanelRuntime(_plugin())
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.load_json",
            AsyncMock(return_value=_ledger()),
        )
        monkeypatch.setattr(
            QQBotMenuPanelService,
            "get_panel",
            AsyncMock(return_value={"success": True, "data": {"panel": PANEL}, "error": None}),
        )
        update = AsyncMock()
        monkeypatch.setattr(QQBotMenuPanelService, "update_panel", update)

        result = await runtime.reconcile_once()

        assert result["unchanged"] == 1
        update.assert_not_awaited()

    async def test_changed_panel_updates_only_owned_id(self, monkeypatch) -> None:
        """内容变化时仅更新 ledger 记录的 panel_id。"""
        runtime = ManagedPanelRuntime(_plugin())
        save = AsyncMock()
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.load_json",
            AsyncMock(return_value=_ledger("owned-id")),
        )
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.save_json", save)
        monkeypatch.setattr(
            QQBotMenuPanelService,
            "get_panel",
            AsyncMock(
                return_value={
                    "success": True,
                    "data": {"panel": {"items": [{"name": "/old", "type": "command"}]}},
                    "error": None,
                }
            ),
        )
        update = AsyncMock(return_value={"success": True, "data": {}, "error": None})
        monkeypatch.setattr(QQBotMenuPanelService, "update_panel", update)

        result = await runtime.reconcile_once()

        assert result["updated"] == 1
        assert update.await_args.args[0] == "owned-id"
        assert save.await_args.args[2]["bindings"]["default"]["panel_id"] == "owned-id"

    async def test_explicit_not_found_creates_replacement(self, monkeypatch) -> None:
        """明确 404 时为同一 managed_key 新建，不搜索替代面板。"""
        runtime = ManagedPanelRuntime(_plugin())
        save = AsyncMock()
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.load_json",
            AsyncMock(return_value=_ledger("missing-id")),
        )
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.save_json", save)
        monkeypatch.setattr(
            QQBotMenuPanelService,
            "get_panel",
            AsyncMock(return_value={"success": False, "data": None, "error": ERROR_NOT_FOUND}),
        )
        create = AsyncMock(
            return_value={"success": True, "data": {"panel_id": "replacement"}, "error": None}
        )
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", create)

        result = await runtime.reconcile_once()

        assert result["created"] == 1
        assert save.await_args.args[2]["bindings"]["default"]["panel_id"] == "replacement"

    async def test_non_404_get_failure_never_creates(self, monkeypatch) -> None:
        """查询错误不能被误判为面板缺失。"""
        runtime = ManagedPanelRuntime(_plugin())
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.load_json",
            AsyncMock(return_value=_ledger()),
        )
        monkeypatch.setattr(
            QQBotMenuPanelService,
            "get_panel",
            AsyncMock(return_value={"success": False, "data": None, "error": "QQ API 限频"}),
        )
        create = AsyncMock()
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", create)

        result = await runtime.reconcile_once()

        assert result["failed"] == 1
        create.assert_not_awaited()

    async def test_corrupt_ledger_blocks_all_remote_writes(self, monkeypatch) -> None:
        """损坏账本不能退化为空账本后重复创建。"""
        runtime = ManagedPanelRuntime(_plugin())
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.load_json",
            AsyncMock(return_value={"schema_version": 999, "bindings": {}}),
        )
        create = AsyncMock()
        get = AsyncMock()
        monkeypatch.setattr(QQBotMenuPanelService, "create_panel", create)
        monkeypatch.setattr(QQBotMenuPanelService, "get_panel", get)

        result = await runtime.reconcile_once()

        assert result["failed"] == 1
        create.assert_not_awaited()
        get.assert_not_awaited()

    async def test_create_storage_failure_does_not_compensate(self, monkeypatch) -> None:
        """创建后账本保存失败时不删除、扫描或认领。"""
        runtime = ManagedPanelRuntime(_plugin())
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.load_json",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "plugins.qqbot_expand.src.managed_panels.storage_api.save_json",
            AsyncMock(side_effect=OSError("disk full")),
        )
        monkeypatch.setattr(
            QQBotMenuPanelService,
            "create_panel",
            AsyncMock(
                return_value={"success": True, "data": {"panel_id": "orphan"}, "error": None}
            ),
        )
        delete = AsyncMock()
        list_panels = AsyncMock()
        monkeypatch.setattr(QQBotMenuPanelService, "delete_panel", delete)
        monkeypatch.setattr(QQBotMenuPanelService, "list_panels", list_panels)

        result = await runtime.reconcile_once()

        assert result["failed"] == 1
        delete.assert_not_awaited()
        list_panels.assert_not_awaited()

    async def test_removed_config_never_touches_retained_binding(self, monkeypatch) -> None:
        """配置移除只停止管理，账本和远端均不变。"""
        runtime = ManagedPanelRuntime(_plugin(items=[]))
        load = AsyncMock(return_value=_ledger())
        get = AsyncMock()
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.load_json", load)
        monkeypatch.setattr(QQBotMenuPanelService, "get_panel", get)

        result = await runtime.reconcile_once()

        assert result == {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
        load.assert_not_awaited()
        get.assert_not_awaited()

    async def test_duplicate_keys_are_all_rejected(self, monkeypatch) -> None:
        """重复 managed_key 的所有声明都不能触碰远端。"""
        runtime = ManagedPanelRuntime(_plugin(items=[_spec(), _spec()]))
        load = AsyncMock()
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.load_json", load)

        result = await runtime.reconcile_once()

        assert result["failed"] == 2
        load.assert_not_awaited()

    async def test_any_invalid_spec_blocks_valid_specs(self, monkeypatch) -> None:
        """任一声明非法时整轮停止，避免配置被部分应用。"""
        runtime = ManagedPanelRuntime(_plugin(items=[_spec(), _spec(key="bad key")]))
        load = AsyncMock()
        monkeypatch.setattr("plugins.qqbot_expand.src.managed_panels.storage_api.load_json", load)

        result = await runtime.reconcile_once()

        assert result["failed"] == 1
        load.assert_not_awaited()


class TestManagedPanelLifecycle:
    """后台任务调度与关闭。"""

    async def test_schedule_is_single_flight_and_close_cancels(self, monkeypatch) -> None:
        """同一实例只调度一次，卸载关闭会取消并等待任务。"""
        runtime = ManagedPanelRuntime(_plugin())
        started = asyncio.Event()

        async def wait_forever():
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runtime, "reconcile_once", wait_forever)
        assert await runtime.schedule() is True
        assert await runtime.schedule() is False
        await started.wait()
        await runtime.close()
        assert runtime._tasks == {}

    async def test_reset_cancels_old_task_before_rescheduling(self, monkeypatch) -> None:
        """reset 不得遗留旧对账任务与新任务重叠。"""
        runtime = ManagedPanelRuntime(_plugin())
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def wait_forever():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(runtime, "reconcile_once", wait_forever)
        assert await runtime.schedule() is True
        await started.wait()
        await runtime.reset()
        assert cancelled.is_set()
        assert runtime._tasks == {}
        assert runtime._closed is False
