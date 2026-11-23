"""受信插件使用的 QQ Markdown 图片素材管理 Service（默认关闭，无 LLM Tool）。"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from src.app.plugin_system.base import BaseService
from src.kernel.concurrency import get_task_manager

from ..src.bridge import api_request, failure, success
from ..src.material_policy import (
    MaterialError,
    completed_data,
    listed_data,
    parse_prepared,
    validate_cursor,
    validate_image_id,
    validate_image_input,
)
from ..src.material_upload import MaterialUploadClient
from ..src.material_validation import VALIDATION_TIMEOUT, validation_job, validation_result

__all__ = ["QQBotMaterialService"]


class QQBotMaterialService(BaseService):
    """管理图片素材引用；不发送消息、不持久化预览链接、不自动清理库存。"""

    name = "qqbot_material"
    description = "上传、分页查询和显式删除 QQ Markdown 图片素材"

    def _feature(self, name: str) -> bool:
        """只接受显式开启的布尔配置，缺失默认拒绝。"""
        features = getattr(getattr(self.plugin, "config", None), "features", None)
        return getattr(features, name, False) is True

    def _runtime(self) -> MaterialUploadClient:
        """获取插件实例共享的有限并发客户端，不在 Service 上创建资源。"""
        if not self._feature("enable_material_service"):
            raise MaterialError("图片素材 Service 未启用")
        runtime = getattr(self.plugin, "material_upload", None)
        if runtime is None or runtime.is_closed:
            raise MaterialError("图片素材资源未初始化；启用配置后请重新加载插件")
        return runtime

    async def _control(
        self, method: str, path: str, body: dict[str, Any] | None = None,
        *, query: dict[str, Any] | None = None,
    ) -> Any:
        """复用 Adapter token 与 bridge，不添加 prepare/complete/删除重试。"""
        result = await api_request(
            self.plugin, method, path, body, query=query, retry_network_errors=False,
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise MaterialError("素材控制面请求失败")
        return result.get("data")

    async def upload_image(
        self, file_name: str, content: bytes, content_type: str,
    ) -> dict[str, Any]:
        """校验至多 10 MiB 的图片，完整执行 prepare/PUT/complete。

        返回 ``success/data/error``；data 仅含 image_id、素材 url 和滑动窗口 ttl。
        超时或未知 complete 结果不会触发自动重传、补偿删除或后台重试。
        """
        stage = "validation"
        try:
            runtime = self._runtime()
            async with runtime.operation():
                # 先在当前进程限制提交体积，再由框架执行器解码；不能阻塞事件循环。
                name = validate_image_input(file_name, content, content_type)
                checked = await get_task_manager().to_process(
                    validation_job(name, content, content_type), timeout=VALIDATION_TIMEOUT + 5,
                )
                payload = validation_result(checked, name, len(content), content_type)
                stage = "prepare"
                data = await self._control("POST", "/v2/media-images/prepare", payload)
                stage = "prepare_response"
                prepared = parse_prepared(data)
                stage = "PUT"
                md5 = await runtime.put_prepared(prepared, content, content_type, payload["md5"])
                stage = "complete"
                data = await self._control("POST", "/v2/media-images/complete", {
                    "upload_id": prepared.upload_id, "md5": md5,
                })
                return success(completed_data(data))
        except MaterialError as exc:
            if stage in {"prepare", "complete"}:
                return failure(f"{stage} 失败，远端结果未知；未自动重试或删除，请先查询库存")
            return failure(str(exc))
        except Exception:
            # 不返回异常文本、响应正文或任何 presigned_url/preview_url。
            return failure(f"素材 {stage} 失败或超时，远端结果未知；未自动重试或删除")

    async def list_images(self, *, cursor: str = "") -> dict[str, Any]:
        """只查询一页素材，由调用方决定是否继续 next_cursor；无长期缓存。"""
        try:
            runtime = self._runtime()
            cursor = validate_cursor(cursor)
            async with runtime.operation():
                data = await self._control(
                    "GET", "/v2/media-images", query={"cursor": cursor} if cursor else None,
                )
                return success(listed_data(data))
        except MaterialError as exc:
            return failure(str(exc))
        except Exception:
            return failure("查询图片素材失败或超时")

    async def delete_image(self, image_id: str, *, confirm: bool = False) -> dict[str, Any]:
        """仅在删除开关与 confirm=True 均满足时删除一次，绝不自动重放。"""
        requested = False
        try:
            runtime = self._runtime()
            if not self._feature("allow_material_delete") or confirm is not True:
                raise MaterialError("删除素材需启用 allow_material_delete 且 confirm=True")
            image_id = validate_image_id(image_id)
            async with runtime.operation():
                requested = True
                data = await self._control("DELETE", f"/v2/media-images/{quote(image_id, safe='')}/delete")
                if data != {}:
                    raise MaterialError("删除响应不合法")
                return success()
        except MaterialError as exc:
            if not requested:
                return failure(str(exc))
            return failure("删除素材失败，远端结果未知；未自动重试，请先查询库存")
        except Exception:
            return failure("删除素材失败或超时，远端结果未知；未自动重试，请先查询库存")
