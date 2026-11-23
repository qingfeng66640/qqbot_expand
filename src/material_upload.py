"""图片素材的上传流程、ETag 校验与插件级限时资源。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .cos_transport import (
    COS_CONCURRENCY as MATERIAL_CONCURRENCY,
    CosTransportError,
    _HTTP_LOGGERS,
    _PRIVATE_HTTP,
    _PrivateHTTPFilter,
    create_cos_client as create_cos_client,
    private_cos_diagnostics as private_cos_diagnostics,
)
from .material_policy import MaterialError, PreparedUpload, normalize_etag, validate_presigned_url


class MaterialUploadClient:
    """插件持有的专用客户端；Service 实例不持有连接、任务或长期缓存。"""

    def __init__(self, *, request_timeout: float, connect_timeout: float) -> None:
        """建立无鉴权的客户端和操作门限，尚不发出网络请求。"""
        self._request_timeout = request_timeout
        self._client = create_cos_client(request_timeout=request_timeout, connect_timeout=connect_timeout)
        self._semaphore = asyncio.Semaphore(MATERIAL_CONCURRENCY)
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._drained = asyncio.Event()
        self._drained.set()
        self.is_closed = False
        self._log_filter = _PrivateHTTPFilter()
        for name in _HTTP_LOGGERS:
            logging.getLogger(name).addFilter(self._log_filter)

    @asynccontextmanager
    async def operation(self) -> AsyncIterator[None]:
        """对排队和完整三阶段流程设置总时限；取消传播并归还共享名额。"""
        task = asyncio.current_task()
        if self.is_closed or task is None:
            raise MaterialError("素材上传资源未初始化或已关闭")
        self._active_tasks.add(task)
        self._drained.clear()
        token = _PRIVATE_HTTP.set(True)
        try:
            async with asyncio.timeout(3 * self._request_timeout):
                async with self._semaphore:
                    if self.is_closed:
                        raise MaterialError("素材上传资源已关闭")
                    yield
        finally:
            _PRIVATE_HTTP.reset(token)
            self._active_tasks.discard(task)
            if not self._active_tasks:
                self._drained.set()

    async def put_prepared(
        self, prepared: PreparedUpload, content: bytes, content_type: str, md5: str,
    ) -> str:
        """仅消费受信 prepare 票据；上传无任何鉴权/默认 cookie，校验 ETag。"""
        if self.is_closed:
            raise MaterialError("素材上传资源已关闭")
        url = validate_presigned_url(prepared.presigned_url)
        # 显式 Request 不合并 AsyncClient 的默认 headers/cookies/auth。
        request = httpx.Request("PUT", url, headers={"Content-Type": content_type}, content=content)
        token = _PRIVATE_HTTP.set(True)
        try:
            async with asyncio.timeout(min(self._request_timeout, prepared.expires_in)):
                response = await self._client.send(request, auth=None, follow_redirects=False, stream=True)
                try:
                    if not 200 <= response.status_code < 300:
                        raise MaterialError("图片 PUT 失败或被重定向；未调用 complete")
                    return normalize_etag(response.headers.get("etag", ""), md5)
                finally:
                    await response.aclose()
        except CosTransportError as exc:
            raise MaterialError(str(exc)) from None
        finally:
            self._client.cookies.clear()
            _PRIVATE_HTTP.reset(token)

    async def aclose(self) -> None:
        """卸载时取消在途/排队操作，等待操作退出后关闭连接；不创建后台任务。"""
        if self.is_closed:
            return
        self.is_closed = True
        for task in tuple(self._active_tasks):
            task.cancel()
        try:
            async with asyncio.timeout(self._request_timeout):
                await self._drained.wait()
        finally:
            token = _PRIVATE_HTTP.set(True)
            try:
                await self._client.aclose()
            finally:
                _PRIVATE_HTTP.reset(token)
                for name in _HTTP_LOGGERS:
                    logging.getLogger(name).removeFilter(self._log_filter)
