"""声明式托管面板的所有权账本与自动对账运行时。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from src.app.plugin_system.api import storage_api
from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import TaskInfo, get_task_manager

from ..services.menu_panel_service import QQBotMenuPanelService
from .errors import ERROR_NOT_FOUND
from .menu_panel_policy import normalize_panel, normalize_panel_create

logger = get_logger("qqbot_expand")

_LEDGER_SCHEMA_VERSION = 1
_STORE_NAME = "qqbot_expand"
_LEDGER_NAME = "managed_panels_ledger"
_MANAGED_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PROCESS_LOCK = asyncio.Lock()


class ManagedPanelRuntime:
    """仅操作 ownership ledger 明确绑定面板的一次性对账运行时。"""

    def __init__(self, plugin: Any) -> None:
        """初始化运行时。"""
        self.plugin = plugin
        self._lock = asyncio.Lock()
        self._tasks: dict[str, TaskInfo] = {}
        self._closed = False

    async def reset(self) -> None:
        """取消旧任务后恢复可调度状态。"""
        await self.close()
        async with self._lock:
            self._closed = False

    async def schedule(self) -> bool:
        """按配置原子地调度一次后台对账。"""
        managed = getattr(getattr(self.plugin, "config", None), "managed_panels", None)
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        items = getattr(managed, "items", None) or []
        async with self._lock:
            if (
                self._closed
                or not bool(getattr(managed, "enabled", False))
                or not bool(getattr(features, "enable_menu_panel_service", False))
                or not items
                or self._tasks
            ):
                return False
            coroutine = self.reconcile_once()
            try:
                task_info = get_task_manager().create_task(
                    coroutine,
                    name="qqbot_expand_managed_panels_reconcile",
                    daemon=True,
                )
            except Exception:
                coroutine.close()
                raise
            self._tasks[task_info.task_id] = task_info
            if task_info.task is not None:
                task_info.task.add_done_callback(
                    lambda _task, task_id=task_info.task_id: self._tasks.pop(
                        task_id, None
                    )
                )
            return True

    async def close(self) -> None:
        """停止对账并等待已调度任务退出。"""
        async with self._lock:
            self._closed = True
            tasks = [info.task for info in self._tasks.values() if info.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._tasks.clear()

    async def reconcile_once(self) -> dict[str, int]:
        """读取配置与账本，对每个合法托管项执行一次安全对账。"""
        async with self._lock:
            if self._closed:
                return {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
        specs, invalid_count = self._normalize_specs()
        result = {"created": 0, "updated": 0, "unchanged": 0, "failed": invalid_count}
        if invalid_count:
            logger.error("托管面板配置存在非法声明，本轮已停止全部远端写入")
            return result
        if not specs:
            return result
        async with _PROCESS_LOCK:
            ledger = await self._load_ledger()
            if ledger is None:
                result["failed"] += len(specs)
                return result
            service = QQBotMenuPanelService(self.plugin)
            for managed_key, desired in specs.items():
                async with self._lock:
                    if self._closed:
                        break
                binding = ledger["bindings"].get(managed_key)
                if binding is None:
                    if await self._create_binding(service, managed_key, desired, ledger):
                        result["created"] += 1
                    else:
                        result["failed"] += 1
                    continue
                outcome = await self._reconcile_binding(
                    service, managed_key, desired, binding, ledger
                )
                result[outcome] += 1
        logger.info(
            "托管面板对账完成: "
            f"created={result['created']} updated={result['updated']} "
            f"unchanged={result['unchanged']} failed={result['failed']}"
        )
        return result

    def _normalize_specs(self) -> tuple[dict[str, dict[str, Any]], int]:
        """规范化配置并拒绝重复或非法 managed_key。"""
        managed = getattr(getattr(self.plugin, "config", None), "managed_panels", None)
        items = getattr(managed, "items", None) or []
        normalized: dict[str, dict[str, Any]] = {}
        failed = 0
        raw_items = [self._as_dict(item) for item in items]
        keys = [
            raw.get("managed_key", "").strip()
            if isinstance(raw.get("managed_key"), str)
            else ""
            for raw in raw_items
        ]
        duplicate_keys = {key for key in keys if key and keys.count(key) > 1}
        for raw, managed_key in zip(raw_items, keys, strict=True):
            if (
                not _MANAGED_KEY_PATTERN.fullmatch(managed_key)
                or managed_key in duplicate_keys
            ):
                logger.error("托管面板配置包含空、非法或重复的 managed_key")
                failed += 1
                continue
            panel = self._as_dict(raw.get("panel"))
            panel["items"] = [self._as_dict(value) for value in panel.get("items", [])]
            error, body = normalize_panel_create(
                raw.get("scope"),
                raw.get("target_type"),
                panel,
                raw.get("user_openids") or None,
                raw.get("group_openids") or None,
            )
            if error:
                logger.error(f"托管面板配置无效: key={managed_key.strip()} error={error}")
                failed += 1
                continue
            normalized[managed_key] = body
        return normalized, failed

    async def _load_ledger(self) -> dict[str, Any] | None:
        """加载并严格校验 ownership ledger。"""
        try:
            raw = await storage_api.load_json(_STORE_NAME, _LEDGER_NAME)
        except Exception as exc:  # noqa: BLE001 - 存储失败必须保守停止
            logger.error(f"读取托管面板账本失败，已停止远端写入: {exc}")
            return None
        if raw is None:
            return {"schema_version": _LEDGER_SCHEMA_VERSION, "bindings": {}}
        if not isinstance(raw, dict) or raw.get("schema_version") != _LEDGER_SCHEMA_VERSION:
            logger.error("托管面板账本版本不兼容，已停止远端写入")
            return None
        bindings = raw.get("bindings")
        if not isinstance(bindings, dict):
            logger.error("托管面板账本损坏，已停止远端写入")
            return None
        for key, binding in bindings.items():
            if (
                not isinstance(key, str)
                or not _MANAGED_KEY_PATTERN.fullmatch(key)
                or not isinstance(binding, dict)
                or not isinstance(binding.get("panel_id"), str)
                or not binding["panel_id"].strip()
                or not isinstance(binding.get("desired_fingerprint", ""), str)
            ):
                logger.error("托管面板账本条目损坏，已停止远端写入")
                return None
        return raw

    async def _create_binding(
        self,
        service: QQBotMenuPanelService,
        managed_key: str,
        desired: dict[str, Any],
        ledger: dict[str, Any],
    ) -> bool:
        """创建面板并仅在持久化成功后确立所有权。"""
        if self._closed:
            return False
        response = await service.create_panel(
            desired["scope"],
            desired["target_type"],
            desired["panel"],
            user_openids=desired.get("user_openids"),
            group_openids=desired.get("group_openids"),
        )
        data = response.get("data") if response.get("success") else None
        panel_id = data.get("panel_id") if isinstance(data, dict) else None
        if not isinstance(panel_id, str) or not panel_id.strip():
            logger.error(f"创建托管面板失败: key={managed_key} error={response.get('error')}")
            return False
        ledger["bindings"][managed_key] = self._binding(panel_id.strip(), desired["panel"])
        try:
            await self._save_ledger(ledger)
        except Exception as exc:  # noqa: BLE001 - 不能删除或扫描补偿
            ledger["bindings"].pop(managed_key, None)
            logger.error(
                f"托管面板已创建但所有权账本保存失败: key={managed_key}；"
                f"不会删除、扫描或自动认领该面板: {exc}"
            )
            return False
        return True

    async def _reconcile_binding(
        self,
        service: QQBotMenuPanelService,
        managed_key: str,
        desired: dict[str, Any],
        binding: dict[str, Any],
        ledger: dict[str, Any],
    ) -> str:
        """只查询和更新账本明确绑定的面板。"""
        panel_id = binding["panel_id"].strip()
        response = await service.get_panel(panel_id)
        if not response.get("success"):
            if response.get("error") == ERROR_NOT_FOUND:
                if await self._create_replacement(
                    service, managed_key, desired, ledger, panel_id
                ):
                    return "created"
            else:
                logger.warning(
                    f"查询托管面板失败: key={managed_key} panel_id={panel_id} "
                    f"error={response.get('error')}"
                )
            return "failed"
        data = response.get("data")
        remote_panel = data.get("panel") if isinstance(data, dict) else None
        error, normalized_remote = normalize_panel(remote_panel)
        if error:
            logger.error(f"托管面板远端响应无效: key={managed_key} panel_id={panel_id}")
            return "failed"
        desired_panel = desired["panel"]
        if self._fingerprint(normalized_remote) == self._fingerprint(desired_panel):
            return "unchanged"
        if self._closed:
            return "failed"
        update = await service.update_panel(panel_id, desired_panel)
        if not update.get("success"):
            logger.warning(
                f"更新托管面板失败: key={managed_key} panel_id={panel_id} "
                f"error={update.get('error')}"
            )
            return "failed"
        ledger["bindings"][managed_key] = self._binding(panel_id, desired_panel)
        try:
            await self._save_ledger(ledger)
        except Exception as exc:  # noqa: BLE001 - 远端已更新，下次查询可恢复
            logger.error(f"托管面板已更新但账本保存失败: key={managed_key} error={exc}")
            return "failed"
        return "updated"

    async def _create_replacement(
        self,
        service: QQBotMenuPanelService,
        managed_key: str,
        desired: dict[str, Any],
        ledger: dict[str, Any],
        old_panel_id: str,
    ) -> bool:
        """仅在已绑定 panel_id 明确失效时创建替代面板。"""
        old_binding = ledger["bindings"][managed_key]
        ledger["bindings"].pop(managed_key, None)
        created = await self._create_binding(service, managed_key, desired, ledger)
        if not created:
            ledger["bindings"][managed_key] = old_binding
            logger.error(
                f"托管面板失效后重建失败: key={managed_key} panel_id={old_panel_id}"
            )
        return created

    async def _save_ledger(self, ledger: dict[str, Any]) -> None:
        """保存完整 ownership ledger。"""
        await storage_api.save_json(_STORE_NAME, _LEDGER_NAME, ledger)

    @classmethod
    def _binding(cls, panel_id: str, panel: dict[str, Any]) -> dict[str, Any]:
        """构造持久化绑定记录。"""
        return {
            "panel_id": panel_id,
            "desired_fingerprint": cls._fingerprint(panel),
            "last_success_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _fingerprint(panel: dict[str, Any]) -> str:
        """计算只覆盖面板内容的稳定指纹。"""
        payload = json.dumps(panel, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        """把 Pydantic 配置对象或字典转换为普通字典。"""
        if isinstance(value, dict):
            return dict(value)
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            return dump()
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        return {}
