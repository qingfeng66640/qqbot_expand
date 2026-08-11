"""``src/bridge.py`` 的路径校验、URL 拼接与请求分流测试。

路径校验是 SSRF 防线：``qqbot_raw.request()`` 的 path 可能来自其他插件甚至
间接来自 LLM，必须保证请求只能打到 QQ 域名下。
"""
from __future__ import annotations

import httpx
import pytest

from ..src.bridge import (
    api_request,
    build_url,
    failure,
    success,
    validate_path,
)
from ..src.constants import API_BASE_PRODUCTION
from ..src.errors import (
    ERROR_FORBIDDEN,
    ERROR_NETWORK,
    ERROR_TOKEN,
)

from .conftest import FakeHttpClient, FakeResponse, make_plugin


class TestResultHelpers:
    """统一返回结构。"""

    def test_success_defaults_to_empty_data(self) -> None:
        """不传 data 时应给出空字典而非 None。"""
        assert success() == {"success": True, "data": {}, "error": None}

    def test_success_with_data(self) -> None:
        """成功时携带响应体。"""
        assert success({"id": "1"})["data"] == {"id": "1"}

    def test_failure(self) -> None:
        """失败时 data 为 None。"""
        assert failure("boom") == {"success": False, "data": None, "error": "boom"}


class TestValidatePath:
    """路径白名单校验（SSRF 防线）。"""

    @pytest.mark.parametrize(
        "path",
        [
            "/users/@me",
            "/v2/users/abc/messages",
            "/interactions/xyz",
            "/v2/groups/g1/messages",
        ],
    )
    def test_accepts_relative_paths(self, path: str) -> None:
        """合法相对路径放行。"""
        assert validate_path(path) is None

    @pytest.mark.parametrize(
        "path",
        [
            "https://evil.com/steal",
            "http://evil.com",
            "//evil.com/steal",
            "/v2/../../../etc/passwd",
            "/path/..%2f",
            "users/@me",
            "",
        ],
    )
    def test_rejects_dangerous_paths(self, path: str) -> None:
        """绝对 URL、协议相对 URL、路径穿越、相对路径一律拒绝。"""
        assert validate_path(path) is not None

    def test_rejects_non_string(self) -> None:
        """非字符串输入也要拒绝而不是抛异常。"""
        assert validate_path(None) is not None  # type: ignore[arg-type]

    def test_rejects_embedded_scheme(self) -> None:
        """路径中间夹带 scheme 同样拒绝。"""
        assert validate_path("/redirect?to=https://evil.com") is not None


class TestBuildUrl:
    """URL 拼接。"""

    def test_basic(self) -> None:
        """基础地址与路径直接拼接。"""
        assert build_url("https://api.bot.qq.com", "/users/@me") == (
            "https://api.bot.qq.com/users/@me"
        )

    def test_strips_trailing_slash(self) -> None:
        """基础地址尾部斜杠不应产生双斜杠。"""
        assert build_url("https://api.bot.qq.com/", "/x") == (
            "https://api.bot.qq.com/x"
        )

    def test_query_is_encoded(self) -> None:
        """查询参数需要 urlencode。"""
        url = build_url("https://a.com", "/x", {"q": "你好", "n": 1})
        assert url.startswith("https://a.com/x?")
        assert "q=%E4%BD%A0%E5%A5%BD" in url
        assert "n=1" in url

    def test_none_values_are_dropped(self) -> None:
        """值为 None 的参数不应出现在 URL 中。"""
        assert build_url("https://a.com", "/x", {"a": None}) == "https://a.com/x"

    def test_empty_query_ignored(self) -> None:
        """空查询字典不加问号。"""
        assert build_url("https://a.com", "/x", {}) == "https://a.com/x"


class TestApiRequestRouting:
    """请求分流：POST 走适配器，其余走自持客户端。"""

    async def test_post_uses_adapter(self, patch_send_handler) -> None:
        """POST 应复用 SendHandler.post_api()，不碰 httpx 客户端。"""
        client = FakeHttpClient()
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "POST", "/v2/users/u1/messages", {"a": 1})

        assert result["success"] is True
        assert result["data"] == {"id": "msg-1", "timestamp": 1}
        assert client.calls == []
        url, headers, body = patch_send_handler.posts[0]
        assert url == "https://api.bot.qq.com/v2/users/u1/messages"
        assert headers["Authorization"] == "QQBot fake-token"
        assert headers["Content-Type"] == "application/json"
        assert body == {"a": 1}

    async def test_get_uses_http_client(self, patch_send_handler) -> None:
        """GET 走自持客户端，post_api 不应被调用。"""
        client = FakeHttpClient([FakeResponse(200, {"id": "bot"})])
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "GET", "/users/@me")

        assert result == {"success": True, "data": {"id": "bot"}, "error": None}
        assert patch_send_handler.posts == []
        assert client.calls[0]["method"] == "GET"
        assert client.calls[0]["headers"]["Authorization"] == "QQBot fake-token"

    async def test_method_is_normalized(self, patch_send_handler) -> None:
        """方法名大小写不敏感。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)

        await api_request(plugin, "  put ", "/interactions/i1", {"code": 0})

        assert client.calls[0]["method"] == "PUT"

    async def test_force_production_overrides_base_url(self, patch_send_handler) -> None:
        """互动应答等接口沙箱不可用，需强制正式域名。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)

        await api_request(
            plugin, "PUT", "/interactions/i1", {"code": 0}, force_production=True
        )

        assert client.calls[0]["url"] == f"{API_BASE_PRODUCTION}/interactions/i1"

    async def test_query_is_forwarded(self, patch_send_handler) -> None:
        """查询参数应体现在最终 URL 上。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)

        await api_request(plugin, "GET", "/x", query={"page": 2})

        assert client.calls[0]["url"].endswith("/x?page=2")


class TestApiRequestGuards:
    """前置校验与失败处理。"""

    async def test_rejects_empty_method(self, patch_send_handler) -> None:
        """method 不能为空。"""
        result = await api_request(make_plugin(), "", "/x")
        assert result["success"] is False

    async def test_rejects_bad_path_before_network(self, patch_send_handler) -> None:
        """路径非法时不应发起任何请求。"""
        client = FakeHttpClient()
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "GET", "https://evil.com")

        assert result["success"] is False
        assert client.calls == []
        assert patch_send_handler.posts == []

    async def test_reports_adapter_not_ready(self, monkeypatch) -> None:
        """适配器未就绪时给出明确提示。"""
        from ..src import bridge

        monkeypatch.setattr(bridge, "resolve_send_handler", lambda: None)
        result = await api_request(make_plugin(), "GET", "/x")

        assert result["success"] is False
        assert "qqbot_adapter" in result["error"]

    async def test_reports_missing_http_client(self, patch_send_handler) -> None:
        """非 POST 请求依赖插件的共享客户端。"""
        result = await api_request(make_plugin(http_client=None), "GET", "/x")
        assert result["success"] is False
        assert "HTTP 客户端" in result["error"]

    async def test_http_error_status_is_sanitized(self, patch_send_handler) -> None:
        """HTTP 错误状态码归类成白名单文案。"""
        client = FakeHttpClient([FakeResponse(403, {"code": 11251})])
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "GET", "/x")

        assert result == {"success": False, "data": None, "error": ERROR_FORBIDDEN}

    async def test_network_error_is_retried_then_sanitized(
        self, patch_send_handler
    ) -> None:
        """网络错误按配置重试，耗尽后返回脱敏文案。"""
        client = FakeHttpClient(
            [httpx.ConnectError("connection refused"), httpx.ConnectError("connection refused")]
        )
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "GET", "/x")

        # retry_max_attempts=1 → 首次 + 1 次重试
        assert len(client.calls) == 2
        assert result["error"] == ERROR_NETWORK

    async def test_network_error_can_disable_retry(self, patch_send_handler) -> None:
        """一次性操作关闭网络重试后只发送一次。"""
        client = FakeHttpClient(
            [httpx.ConnectError("connection refused"), FakeResponse(200, {"ok": 1})]
        )
        plugin = make_plugin(http_client=client)

        result = await api_request(
            plugin, "PUT", "/interactions/i1", {"code": 0}, retry_network_errors=False
        )

        assert len(client.calls) == 1
        assert result["error"] == ERROR_NETWORK

    async def test_network_error_recovers_on_retry(self, patch_send_handler) -> None:
        """首次失败、重试成功时应返回成功。"""
        client = FakeHttpClient(
            [httpx.ConnectError("connection refused"), FakeResponse(200, {"ok": 1})]
        )
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "GET", "/x")

        assert result["success"] is True
        assert result["data"] == {"ok": 1}

    async def test_token_failure_is_sanitized(self, patch_send_handler) -> None:
        """取 token 失败不应透传原始异常。"""

        async def boom() -> str:
            raise RuntimeError("401 unauthorized appid=123 secret=abc")

        patch_send_handler.get_token = boom
        plugin = make_plugin(http_client=FakeHttpClient())

        result = await api_request(plugin, "POST", "/x", {})

        assert result["error"] == ERROR_TOKEN
        assert "secret" not in result["error"]

    async def test_token_failure_on_non_post_is_sanitized(
        self, patch_send_handler
    ) -> None:
        """非 POST 路径取 token 失败同样脱敏，且不发请求。"""

        async def boom() -> str:
            raise RuntimeError("401 unauthorized secret=abc")

        patch_send_handler.get_token = boom
        client = FakeHttpClient()
        plugin = make_plugin(http_client=client)

        result = await api_request(plugin, "GET", "/x")

        assert result["error"] == ERROR_TOKEN
        assert client.calls == []

    async def test_adapter_business_error_is_sanitized(self, patch_send_handler) -> None:
        """QQ 业务错误体（code+message）应被归类而非透传。"""
        patch_send_handler.post_result = {"code": 403, "message": "no permission"}
        plugin = make_plugin(http_client=FakeHttpClient())

        result = await api_request(plugin, "POST", "/x", {})

        assert result["success"] is False
        assert "no permission" not in result["error"]

    async def test_adapter_non_dict_response(self, patch_send_handler) -> None:
        """post_api 返回非字典时按空成功处理。"""
        patch_send_handler.post_result = None
        plugin = make_plugin(http_client=FakeHttpClient())

        assert await api_request(plugin, "POST", "/x", {}) == {
            "success": True,
            "data": {},
            "error": None,
        }

    async def test_adapter_exception_is_sanitized(self, patch_send_handler) -> None:
        """post_api 抛异常时返回脱敏文案。"""
        patch_send_handler.post_result = httpx.ConnectError("connection refused")
        plugin = make_plugin(http_client=FakeHttpClient())

        result = await api_request(plugin, "POST", "/x", {})

        assert result["error"] == ERROR_NETWORK


class TestResponseParsing:
    """响应体解析。"""

    async def test_empty_body(self, patch_send_handler) -> None:
        """204 之类的空响应解析成空字典。"""
        client = FakeHttpClient([FakeResponse(204)])
        plugin = make_plugin(http_client=client)

        assert (await api_request(plugin, "PUT", "/x", {}))["data"] == {}

    async def test_non_json_body(self, patch_send_handler) -> None:
        """非 JSON 响应回落成 raw 文本。"""
        client = FakeHttpClient([FakeResponse(200, None, text="plain")])
        plugin = make_plugin(http_client=client)

        assert (await api_request(plugin, "GET", "/x"))["data"] == {"raw": "plain"}

    async def test_json_array_body(self, patch_send_handler) -> None:
        """顶层为数组时包一层 data。"""
        client = FakeHttpClient([FakeResponse(200, [1, 2])])
        plugin = make_plugin(http_client=client)

        assert (await api_request(plugin, "GET", "/x"))["data"] == {"data": [1, 2]}


class TestDebugLogging:
    """调试开关。"""

    async def test_payload_logging_can_be_enabled(
        self, patch_send_handler, capsys
    ) -> None:
        """开启 debug_log_payload 后应打印请求 URL 与请求体。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client, debug_log_payload=True)

        await api_request(plugin, "GET", "/users/@me")

        assert "/users/@me" in capsys.readouterr().err

    async def test_payload_logging_off_by_default(
        self, patch_send_handler, capsys
    ) -> None:
        """默认不打印请求体，避免敏感内容进日志。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)

        await api_request(plugin, "GET", "/users/@me")

        assert "/users/@me" not in capsys.readouterr().err
