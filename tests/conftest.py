"""qqbot_expand 测试公共装置。

把仓库根目录与 ``plugins/`` 目录加入 ``sys.path``，使测试能以
``plugins.qqbot_expand.xxx`` 的形式导入插件模块（与 qqbot_adapter 一致）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
for path in (REPO_ROOT, PLUGIN_ROOT.parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class FakeResponse:
    """最小化的 httpx 响应替身。"""

    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str = ""
    ) -> None:
        """初始化响应替身。

        Args:
            status_code: HTTP 状态码。
            payload: ``json()`` 的返回值；None 表示无响应体。
            text: 非 JSON 场景下的原始文本。
        """
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"x" if payload is not None or text else b""

    def json(self) -> Any:
        """返回预置的 JSON 负载。

        Returns:
            预置负载。

        Raises:
            ValueError: 未预置负载时模拟解析失败。
        """
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttpClient:
    """记录调用参数的 httpx.AsyncClient 替身。"""

    def __init__(self, responses: list[Any] | None = None) -> None:
        """初始化客户端替身。

        Args:
            responses: 依次返回的响应；元素为异常时抛出。
        """
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], json: Any = None
    ) -> FakeResponse:
        """记录并返回预置响应。

        Args:
            method: HTTP 方法。
            url: 完整 URL。
            headers: 请求头。
            json: 请求体。

        Returns:
            预置响应。

        Raises:
            BaseException: 预置项为异常时原样抛出。
        """
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if not self.responses:
            return FakeResponse(200, {})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeSendHandler:
    """qqbot_adapter SendHandler 的替身。"""

    def __init__(self, base_url: str = "https://api.bot.qq.com") -> None:
        """初始化 SendHandler 替身。

        Args:
            base_url: 适配器当前使用的 API 域名。
        """
        self.base_url = base_url
        self.posts: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.post_result: Any = {"id": "msg-1", "timestamp": 1}
        self.post_results: list[Any] = []

    async def get_token(self) -> str:
        """返回固定 token。

        Returns:
            access_token。
        """
        return "fake-token"

    async def post_api(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> Any:
        """记录 POST 调用并返回预置结果。

        Args:
            url: 完整 URL。
            headers: 请求头。
            body: 请求体。

        Returns:
            预置结果。

        Raises:
            BaseException: 预置结果为异常时原样抛出。
        """
        self.posts.append((url, headers, body))
        result = self.post_results.pop(0) if self.post_results else self.post_result
        if isinstance(result, BaseException):
            raise result
        return result


def make_plugin(
    *,
    http_client: Any = None,
    allow_raw_request: bool = True,
    raw_allowed_methods: list[str] | None = None,
    debug_log_payload: bool = False,
    enable_tools: bool = True,
    enable_group_admin_service: bool = True,
    enable_group_admin_tools: bool = True,
    group_admin_allowed_group_openids: list[str] | None = None,
    callback_timeout: float = 5.0,
    button_data_max_length: int = 1024,
    dedup_ttl: float = 300.0,
    dedup_capacity: int = 4096,
) -> SimpleNamespace:
    """构造一个满足 Service 依赖的插件替身。

    Args:
        http_client: 注入的 HTTP 客户端。
        allow_raw_request: raw 通道总开关。
        raw_allowed_methods: raw 通道方法白名单。
        debug_log_payload: 是否打印请求体。
        enable_tools: 是否注册 Tool。

    Returns:
        带 ``config`` 与 ``http_client`` 属性的替身对象。
    """
    features = SimpleNamespace(
        enable_tools=enable_tools,
        allow_raw_request=allow_raw_request,
        raw_allowed_methods=raw_allowed_methods
        if raw_allowed_methods is not None
        else ["GET", "POST", "PUT", "PATCH", "DELETE"],
        enable_group_admin_service=enable_group_admin_service,
        enable_group_admin_tools=enable_group_admin_tools,
        group_admin_allowed_group_openids=group_admin_allowed_group_openids or [],
        debug_log_payload=debug_log_payload,
    )
    http = SimpleNamespace(
        retry_max_attempts=1,
        retry_backoff_base=0.0,
        retry_backoff_max=0.0,
        retry_jitter=0.0,
    )
    plugin = SimpleNamespace(
        config=SimpleNamespace(
            features=features,
            http=http,
            interaction=SimpleNamespace(
                enabled=True,
                callback_timeout=callback_timeout,
                button_data_max_length=button_data_max_length,
                dedup_ttl=dedup_ttl,
                dedup_capacity=dedup_capacity,
            ),
        ),
        http_client=http_client,
    )
    from ..src.interaction import InteractionRuntime

    plugin.interaction_runtime = InteractionRuntime(plugin)
    return plugin


@pytest.fixture
def send_handler() -> FakeSendHandler:
    """提供 SendHandler 替身。

    Returns:
        FakeSendHandler 实例。
    """
    return FakeSendHandler()


@pytest.fixture
def patch_send_handler(monkeypatch: pytest.MonkeyPatch, send_handler: FakeSendHandler):
    """把 bridge 的 ``resolve_send_handler`` 替换成替身。

    Args:
        monkeypatch: pytest 提供的补丁工具。
        send_handler: SendHandler 替身。

    Returns:
        被注入的 SendHandler 替身。
    """
    from ..src import bridge

    monkeypatch.setattr(bridge, "resolve_send_handler", lambda: send_handler)
    return send_handler
