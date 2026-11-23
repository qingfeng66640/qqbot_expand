"""用户基本信息 Service 的真实离线 HTTP、类型与隐私契约测试。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpcore._trace import Trace

from src.app.plugin_system.api import adapter_api, service_api
from src.core import managers
from src.core.components.registry import ComponentRegistry
from src.core.managers import service_manager
from src.kernel.concurrency import TaskManager

from ..config import QQBotExpandConfig
from ..examples.user_info import query_user_profile
from ..plugin import QQBotExpandPlugin
from ..services.user_info_service import QQBotUserInfoService
from ..src import bridge, user_info
from ..src.bridge import ADAPTER_SIGNATURE


class PublicSendHandler:
    """仅提供 Adapter 公开认证边界，任何发送调用均直接失败。"""

    def __init__(self) -> None:
        """初始化离线凭据与调用记录。"""
        self.base_url = "https://sandbox.api.sgroup.qq.com"
        self.token = "offline-token-canary"
        self.token_calls = 0
        self.token_error: BaseException | None = None
        self.token_entered: asyncio.Event | None = None
        self.token_release: asyncio.Event | None = None

    async def get_token(self) -> str:
        """记录公共认证调用，不创建 TokenManager 或连接平台。"""
        self.token_calls += 1
        if self.token_entered is not None:
            self.token_entered.set()
        if self.token_release is not None:
            await self.token_release.wait()
        if self.token_error is not None:
            raise self.token_error
        return self.token

    async def post_api(self, *args: Any, **kwargs: Any) -> None:
        """只读查询绝不进入消息发送出口。"""
        pytest.fail("用户查询不得调用 post_api")


def profile(*, proactive: bool = True) -> dict[str, Any]:
    """提供包含敏感元数据但无需任何后续请求的合法响应。"""
    return {
        "username": "离线用户😊",
        "avatar": "https://avatar.invalid/private?key=avatar-canary",
        "union_openid": "union-metadata-canary",
        "allow_proactive_msg": proactive,
    }


@pytest.fixture
async def user_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    """真实构造插件和 Service，只替换组件发现与 HTTP 最末端。"""
    handler = PublicSendHandler()
    requests: list[httpx.Request] = []
    env = SimpleNamespace(
        handler=handler,
        requests=requests,
        response=httpx.Response(200, json=profile()),
        transport_error=None,
        http_entered=None,
        http_release=None,
        times=[],
    )

    def get_adapter(signature: str) -> SimpleNamespace:
        """按正式签名返回只含公共 send_handler 的 Adapter。"""
        assert signature == ADAPTER_SIGNATURE
        return SimpleNamespace(send_handler=handler)

    async def transport(request: httpx.Request) -> httpx.Response:
        """捕获物理请求；不替换构造、限流、响应分类或公共认证方法。"""
        requests.append(request)
        env.times.append(user_info.monotonic())
        # MockTransport 不经过 httpcore；使用其真实 Trace 补上 DEBUG 诊断边界。
        async with Trace(
            "receive_response_headers",
            logging.getLogger("httpcore.http11"),
            kwargs={"request": request, "headers": request.headers},
        ) as trace:
            if env.http_entered is not None:
                env.http_entered.set()
            if env.http_release is not None:
                await env.http_release.wait()
            if env.transport_error is not None:
                raise env.transport_error
            trace.return_value = profile()["avatar"]
            return env.response

    monkeypatch.setattr(adapter_api, "get_adapter", get_adapter)
    config = QQBotExpandConfig()
    config.features.debug_log_payload = True
    config.http.retry_max_attempts = 5
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        trust_env=False,
        follow_redirects=True,
    ) as client:
        plugin = QQBotExpandPlugin(config)
        plugin.http_client = client
        env.plugin = plugin
        env.service = QQBotUserInfoService(plugin=plugin)
        yield env


@pytest.mark.parametrize("proactive", [False, True])
async def test_public_service_get_is_read_only_and_preserves_metadata(
    user_env: SimpleNamespace,
    proactive: bool,
) -> None:
    """准确发送 GET 和认证头，不下载头像、不合并身份、不授权主动发送。"""
    expected = profile(proactive=proactive)
    user_env.response = httpx.Response(200, json=expected)
    result = await user_env.service.get_user_info("not-fixed-hex_openid")
    assert result == {
        "success": True,
        "data": expected,
        "error": None,
        "error_type": None,
    }
    assert user_env.service.name == "qqbot_user_info"
    assert user_env.service.description
    assert user_env.service.plugin is user_env.plugin
    assert user_env.handler.token_calls == 1
    assert len(user_env.requests) == 1
    request = user_env.requests[0]
    assert request.method == "GET"
    assert str(request.url) == (
        "https://sandbox.api.sgroup.qq.com/v2/users/not-fixed-hex_openid/info"
    )
    assert request.headers["Authorization"] == "QQBot offline-token-canary"
    assert not request.content and not request.url.query
    assert not user_env.plugin.sent_messages.claim(
        expected["union_openid"],
        "user",
        "not-fixed-hex_openid",
    )


@pytest.mark.parametrize(
    "openid",
    [
        None,
        17,
        "",
        " ",
        ".",
        "..",
        "../other",
        "a/b",
        "a\\b",
        "a?b",
        "a#b",
        "a%2fb",
        "a\nb",
    ],
)
async def test_invalid_openid_rejected_before_auth_or_http(
    user_env: SimpleNamespace,
    openid: Any,
) -> None:
    """拒绝危险路径参数而不是猜测平台没有规定的固定十六进制长度。"""
    result = await user_env.service.get_user_info(openid)
    assert result["success"] is False and result["data"] is None
    assert result["error_type"] == "invalid_user_openid"
    assert user_env.handler.token_calls == 0 and not user_env.requests


@pytest.mark.parametrize("key", ["err_code", "code"])
@pytest.mark.parametrize("status", [200, 400, 401, 403])
async def test_no_friend_relationship_is_not_a_false_opt_out(
    user_env: SimpleNamespace,
    key: str,
    status: int,
) -> None:
    """特定业务错误优先于通用 HTTP 错误，不将查询失败转为推送关闭。"""
    user_env.response = httpx.Response(status, json={key: 40184001})
    result = await user_env.service.get_user_info("user")
    assert result["success"] is False and result["data"] is None
    assert result["error_type"] == "no_friend_relationship"
    assert "allow_proactive_msg" not in result
    assert len(user_env.requests) == 1


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, "authentication_failed"),
        (429, "rate_limited"),
        (500, "server_error"),
        (503, "server_error"),
        (302, "http_error"),
    ],
)
async def test_http_failures_are_classified_without_retry_or_redirect(
    user_env: SimpleNamespace,
    status: int,
    error_type: str,
) -> None:
    """保留 HTTP 分类，不因畸形正文丢失状态，也不跟随重定向增加物理尝试。"""
    user_env.response = httpx.Response(
        status,
        content=b"private-response-canary",
        headers={"Location": "https://redirect.invalid/secret"},
    )
    result = await user_env.service.get_user_info("user")
    assert result["success"] is False and result["data"] is None
    assert result["error_type"] == error_type
    assert "canary" not in str(result) and "https://" not in str(result)
    assert len(user_env.requests) == 1 and user_env.handler.token_calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not-an-object",
        {},
        {"username": "user", "avatar": "url"},
        {"username": "user", "avatar": "url", "allow_proactive_msg": 0},
    ],
)
async def test_invalid_payload_never_defaults_permission_to_false(
    user_env: SimpleNamespace,
    payload: Any,
) -> None:
    """只接受字段类型正确的用户对象，缺失开关不是 false。"""
    user_env.response = httpx.Response(200, json=payload)
    result = await user_env.service.get_user_info("user")
    assert result["success"] is False and result["data"] is None
    assert result["error_type"] == "invalid_response"


class ControlledClock:
    """只替换时间/休眠边界，通过队列和 Event 驱动真实限流循环。"""

    def __init__(self) -> None:
        """从可精确表示的时间起点开始，避免真实等待和浮点竞态。"""
        self.now = 100.0
        self.waits: asyncio.Queue[tuple[float, asyncio.Event]] = asyncio.Queue()

    def monotonic(self) -> float:
        """返回只会由测试主动前进的单调时间。"""
        return self.now

    async def sleep(self, delay: float) -> None:
        """报告休眠边界；测试显式释放前不前进时钟。"""
        release = asyncio.Event()
        self.waits.put_nowait((delay, release))
        await release.wait()

    async def next_wait(self) -> tuple[float, asyncio.Event]:
        """等待真实限流器进入休眠而非用固定 sleep 猜测进度。"""
        return await asyncio.wait_for(self.waits.get(), timeout=3)


@pytest.fixture
async def manager() -> AsyncIterator[TaskManager]:
    """并发测试由真实 TaskManager 拥有任务，并确保失败时也清理。"""
    task_manager = TaskManager()
    yield task_manager
    for info in task_manager.get_active_tasks():
        info.cancel()
    await task_manager.wait_all_tasks()
    task_manager.cleanup_tasks()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> ControlledClock:
    """仅替换单调时间和 sleep，不 mock 限流器或 acquire。"""
    controlled = ControlledClock()
    monkeypatch.setattr(user_info, "monotonic", controlled.monotonic)
    monkeypatch.setattr(user_info, "sleep", controlled.sleep)
    return controlled


def start(manager: TaskManager, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """通过框架任务入口并行调用被测公共 Service。"""
    task = manager.create_task(coro, name="offline-user-info").task
    assert task is not None
    return task


async def test_rate_limit_shared_across_services_and_isolated_between_plugins(
    user_env: SimpleNamespace,
    clock: ControlledClock,
    manager: TaskManager,
) -> None:
    """第 11 个并发物理尝试等待一秒，新 Service 不重置、不同插件不串扰。"""
    tasks = [
        start(
            manager, QQBotUserInfoService(plugin=user_env.plugin).get_user_info("user")
        )
        for _ in range(11)
    ]
    delay, release = await clock.next_wait()
    assert delay == 1.0 and len(user_env.requests) == 10
    assert sum(task.done() for task in tasks) == 10
    other = QQBotExpandPlugin(QQBotExpandConfig())
    other.http_client = user_env.plugin.http_client
    independent = await QQBotUserInfoService(plugin=other).get_user_info("other")
    assert independent["success"] is True and len(user_env.requests) == 11
    assert other.user_info_rate_limiter is not user_env.plugin.user_info_rate_limiter

    # 提前唤醒不能绕过窗口；没有真实等待和固定 sleep。
    clock.now += 0.5
    release.set()
    remainder, release = await clock.next_wait()
    assert remainder == 0.5 and len(user_env.requests) == 11
    clock.now += 0.5
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    assert all(result["success"] for result in results)
    assert user_env.times == [100.0] * 11 + [101.0]


async def test_failed_physical_attempts_consume_shared_rate_budget(
    user_env: SimpleNamespace,
    clock: ControlledClock,
    manager: TaskManager,
) -> None:
    """超时也占用额度，调用方自行重试不能通过新 Service 绕过 10 QPS。"""
    user_env.transport_error = httpx.ReadTimeout("private-token-and-url-canary")
    for _ in range(10):
        result = await QQBotUserInfoService(plugin=user_env.plugin).get_user_info(
            "user"
        )
        assert result["error_type"] == "timeout"
    task = start(
        manager, QQBotUserInfoService(plugin=user_env.plugin).get_user_info("user")
    )
    delay, release = await clock.next_wait()
    assert len(user_env.requests) == 10 and delay == 1.0
    clock.now += delay
    release.set()
    assert (await asyncio.wait_for(task, timeout=3))["error_type"] == "timeout"
    assert len(user_env.requests) == 11


async def test_cancel_rate_waiter_releases_lock_and_does_not_reserve_future_budget(
    user_env: SimpleNamespace,
    clock: ControlledClock,
    manager: TaskManager,
) -> None:
    """取消等待直接传播，不启动 HTTP，也不留下占位或锁死后续查询。"""
    for _ in range(10):
        assert (await user_env.service.get_user_info("user"))["success"]
    task = start(manager, user_env.service.get_user_info("cancelled"))
    await clock.next_wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(user_env.requests) == 10
    clock.now += 1.0
    for _ in range(10):
        assert (await user_env.service.get_user_info("user"))["success"]
    assert len(user_env.requests) == 20


@pytest.mark.parametrize("phase", ["auth", "http"])
async def test_caller_cancellation_propagates_at_external_boundaries(
    user_env: SimpleNamespace,
    manager: TaskManager,
    phase: str,
) -> None:
    """同步屏障精确取消认证/HTTP 中的调用，不替换被测函数也不制造重试。"""
    entered, release = asyncio.Event(), asyncio.Event()
    if phase == "auth":
        user_env.handler.token_entered = entered
        user_env.handler.token_release = release
    else:
        user_env.http_entered = entered
        user_env.http_release = release
    task = start(manager, user_env.service.get_user_info("user"))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(user_env.requests) == (0 if phase == "auth" else 1)
    release.set()
    assert (await user_env.service.get_user_info("user"))["success"]
    assert len(user_env.requests) == (1 if phase == "auth" else 2)


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (httpx.ReadTimeout("private-response-canary"), "timeout"),
        (httpx.ConnectError("private-response-canary"), "network_error"),
        (RuntimeError("private-response-canary"), "request_failed"),
    ],
)
async def test_transport_failures_are_sanitized_and_never_retried(
    user_env: SimpleNamespace,
    error: Exception,
    error_type: str,
) -> None:
    """真实 MockTransport 抛出异常，分类不得回显文本或调用通用重试。"""
    user_env.transport_error = error
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == error_type and result["data"] is None
    assert "canary" not in str(result)
    assert len(user_env.requests) == 1 and user_env.handler.token_calls == 1


@pytest.mark.parametrize(
    "token", [None, 42, "", "a b", "a\nb", "a\x00b", "a\x7fb", "令牌"]
)
async def test_invalid_auth_token_is_not_sent(
    user_env: SimpleNamespace, token: Any
) -> None:
    """不合法 token 不得进入 HTTP 头，也不通过字符串化泄露调用方对象。"""
    user_env.handler.token = token
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "authentication_failed" and result["data"] is None
    assert not user_env.requests


async def test_token_exception_is_sanitized_before_http(
    user_env: SimpleNamespace,
) -> None:
    """认证边界失败不记录异常正文、不消耗用户查询额度。"""
    user_env.handler.token_error = ValueError(
        "token=secret-canary https://private.invalid"
    )
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "authentication_failed" and result["data"] is None
    assert "canary" not in str(result) and "https://" not in str(result)
    assert not user_env.requests


@pytest.mark.parametrize("closed", [False, True])
async def test_client_must_be_ready_before_auth(
    user_env: SimpleNamespace, closed: bool
) -> None:
    """共享客户端缺失或关闭时不另建客户端，也不请求认证。"""
    if closed:
        await user_env.plugin.http_client.aclose()
    else:
        user_env.plugin.http_client = None
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "client_not_ready"
    assert user_env.handler.token_calls == 0 and not user_env.requests


@pytest.mark.parametrize(
    "adapter", [None, SimpleNamespace(), SimpleNamespace(send_handler=None)]
)
async def test_adapter_must_expose_a_ready_public_handler(
    user_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    adapter: Any,
) -> None:
    """仅发现公开 Adapter 对象，不探测私有属性或另建 Gateway。"""
    monkeypatch.setattr(adapter_api, "get_adapter", lambda signature: adapter)
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "adapter_not_ready"
    assert not user_env.requests


async def test_broken_public_handler_property_is_sanitized(
    user_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公共属性访问失败同样不把敏感异常抛给调用方。"""

    class BrokenAdapter:
        """模拟 Adapter 公共属性在卸载中失效。"""

        @property
        def send_handler(self) -> Any:
            """抛出含 canary 的边界异常。"""
            raise RuntimeError("private-response-canary https://private.invalid")

    monkeypatch.setattr(adapter_api, "get_adapter", lambda signature: BrokenAdapter())
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "adapter_not_ready" and "canary" not in str(result)
    assert not user_env.requests


@pytest.mark.parametrize(
    "openid", ["a", "用户_α-1", "a:b@c+d", "a.b", "a..b", "a" * 257]
)
async def test_valid_variable_length_openid_is_encoded_as_single_segment(
    user_env: SimpleNamespace,
    openid: str,
) -> None:
    """接受非 hex、Unicode 和不同长度，保留原值且不改变 host/query/路由段数。"""
    result = await user_env.service.get_user_info(openid)
    assert result["success"]
    url = user_env.requests[0].url
    assert url.host == "sandbox.api.sgroup.qq.com"
    assert url.path == f"/v2/users/{openid}/info" and not url.query
    assert len(url.path.split("/")) == 5


@pytest.mark.parametrize(
    "content", [b"", b"{not-json", b"\xff", b"[" * 2000 + b"]" * 2000]
)
async def test_malformed_empty_or_deep_json_is_invalid_response(
    user_env: SimpleNamespace,
    content: bytes,
) -> None:
    """空、畸形编码和过深 JSON 都必须作为失败返回，不能兜底 raw 正文。"""
    user_env.response = httpx.Response(200, content=content)
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "invalid_response" and result["data"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("username", 12),
        ("username", None),
        ("avatar", []),
        ("avatar", None),
        ("allow_proactive_msg", "false"),
        ("allow_proactive_msg", 1),
        ("allow_proactive_msg", None),
        ("union_openid", None),
        ("union_openid", 123),
    ],
)
async def test_response_field_types_are_strict(
    user_env: SimpleNamespace,
    key: str,
    value: Any,
) -> None:
    """拒绝真值/字符串转换，特别是 bool 不得接受 0/1 或缺失值。"""
    payload = profile()
    payload[key] = value
    user_env.response = httpx.Response(200, json=payload)
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == "invalid_response" and result["data"] is None


async def test_optional_union_and_extra_fields_are_not_persisted_or_merged(
    user_env: SimpleNamespace,
) -> None:
    """未绑定第三方时可无 union；未知字段白名单剔除，不建立个人信息缓存。"""
    payload = profile()
    del payload["union_openid"]
    user_env.response = httpx.Response(
        200, json={**payload, "secret": "private-canary"}
    )
    before = set(vars(user_env.plugin))
    first = await user_env.service.get_user_info("user")
    assert first["data"] == payload
    user_env.response = httpx.Response(200, json=profile(proactive=False))
    second = await QQBotUserInfoService(plugin=user_env.plugin).get_user_info("user")
    assert second["data"]["allow_proactive_msg"] is False
    assert first["data"]["allow_proactive_msg"] is True and len(user_env.requests) == 2
    assert set(vars(user_env.plugin)) == before
    assert "canary" not in repr(vars(user_env.plugin.user_info_rate_limiter))


@pytest.mark.parametrize(
    ("codes", "error_type"),
    [
        ({"code": "40184001"}, "no_friend_relationship"),
        ({"err_code": 0, "code": 40184001}, "no_friend_relationship"),
        ({"code": 0, "err_code": "40184001"}, "no_friend_relationship"),
        ({"err_code": 11243}, "authentication_failed"),
        ({"code": "11241"}, "authentication_failed"),
        ({"err_code": 87654321}, "api_error"),
        ({"code": "999"}, "api_error"),
        ({"err_code": None}, "invalid_response"),
        ({"err_code": False}, "invalid_response"),
        ({"code": []}, "invalid_response"),
        ({"code": 1.0}, "invalid_response"),
        ({"code": "secret-canary"}, "invalid_response"),
        ({"code": ""}, "invalid_response"),
        ({"err_code": "-1"}, "invalid_response"),
        ({"err_code": "1" * 11}, "invalid_response"),
    ],
)
async def test_business_codes_are_independent_of_message_text(
    user_env: SimpleNamespace,
    codes: dict[str, Any],
    error_type: str,
) -> None:
    """2xx 也检查官方及历史 code 形状，不需要 message，更不匹配其文本。"""
    user_env.response = httpx.Response(200, json={**profile(), **codes})
    result = await user_env.service.get_user_info("user")
    assert result["error_type"] == error_type and result["data"] is None
    assert "canary" not in str(result)


async def test_zero_business_codes_and_irrelevant_message_are_not_errors(
    user_env: SimpleNamespace,
) -> None:
    """显式成功码可以共存；message 即使像错误也不会改变业务码判定。"""
    user_env.response = httpx.Response(
        200,
        json={
            **profile(),
            "err_code": 0,
            "code": "0",
            "message": "token failed private-canary",
        },
    )
    result = await user_env.service.get_user_info("user")
    assert result["success"] and result["data"] == profile()


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_debug_mode_does_not_log_profile_url_or_exception_details(
    user_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    level: int,
) -> None:
    """显式 INFO/DEBUG 捕获依赖日志，不能用默认 WARNING 掩盖隐私泄露。"""
    caplog.set_level(level)
    logs: list[str] = []
    monkeypatch.setattr(
        bridge,
        "logger",
        SimpleNamespace(
            info=logs.append,
            warning=logs.append,
            error=logs.append,
        ),
    )
    assert (await user_env.service.get_user_info("openid-private-canary"))["success"]
    user_env.response = httpx.Response(401, json={"message": "token=private-canary"})
    assert not (await user_env.service.get_user_info("openid-private-canary"))[
        "success"
    ]
    user_env.transport_error = RuntimeError("private-canary https://private.invalid")
    assert not (await user_env.service.get_user_info("openid-private-canary"))[
        "success"
    ]
    captured = capsys.readouterr()
    text = str(logs) + caplog.text + captured.out + captured.err
    for marker in ("canary", "https://", "离线用户", "union-metadata", "Authorization"):
        assert marker not in text


async def test_service_manager_constructs_new_instances_and_example_uses_signature(
    user_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使用真实注册表、ServiceManager 与公开 API，不 mock Service 构造。"""
    signature = "qqbot_expand:service:qqbot_user_info"
    registry = ComponentRegistry()
    registry.register(QQBotUserInfoService, signature)
    service_mgr = service_manager.ServiceManager()

    def get_plugin(name: str) -> QQBotExpandPlugin:
        """仅替换已加载插件发现边界，返回真实构造的插件。"""
        assert name == "qqbot_expand"
        return user_env.plugin

    monkeypatch.setattr(service_manager, "get_global_registry", lambda: registry)
    monkeypatch.setattr(service_manager, "get_service_manager", lambda: service_mgr)
    monkeypatch.setattr(
        managers, "get_plugin_manager", lambda: SimpleNamespace(get_plugin=get_plugin)
    )
    first, second = (
        service_api.get_service(signature),
        service_api.get_service(signature),
    )
    assert isinstance(first, QQBotUserInfoService) and isinstance(
        second, QQBotUserInfoService
    )
    assert first is not second and first.plugin is second.plugin is user_env.plugin
    assert (await first.get_user_info("user"))["success"]
    limiter = user_env.plugin.user_info_rate_limiter
    assert (await second.get_user_info("user"))["success"]
    assert user_env.plugin.user_info_rate_limiter is limiter
    assert (await query_user_profile("user"))["data"] == profile()
    assert len(user_env.requests) == 3


async def test_example_missing_service_does_not_start_http(
    user_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """示例在组件未注册时明确失败，不绕过 Service 入口自行认证。"""
    monkeypatch.setattr(service_api, "get_service", lambda signature: None)
    with pytest.raises(RuntimeError, match="尚未加载"):
        await query_user_profile("user")
    assert not user_env.requests


async def test_rolling_window_does_not_reset_at_wall_clock_second(
    user_env: SimpleNamespace,
    clock: ControlledClock,
    manager: TaskManager,
) -> None:
    """错开五次请求后再并发六次，必须分两批释放，不能整秒清空计数。"""
    for _ in range(5):
        assert (await user_env.service.get_user_info("user"))["success"]
    clock.now += 0.5
    for _ in range(5):
        assert (await user_env.service.get_user_info("user"))["success"]
    tasks = [start(manager, user_env.service.get_user_info("user")) for _ in range(6)]
    delay, release = await clock.next_wait()
    assert delay == 0.5 and len(user_env.requests) == 10
    clock.now += delay
    release.set()
    delay, release = await clock.next_wait()
    assert delay == 0.5 and len(user_env.requests) == 15
    clock.now += delay
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    assert all(result["success"] for result in results)
    assert user_env.times == [100.0] * 5 + [100.5] * 5 + [101.0] * 5 + [101.5]


_DIAGNOSTIC_LOGGERS = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
    "hpack.hpack",
    "hpack.table",
)


def diagnostic_logging_state() -> dict[str, tuple[Any, ...]]:
    """记录明确的依赖日志器状态，证明过滤器/级别/处理器均无永久修改。"""
    return {
        name: (
            tuple(logger.filters),
            logger.level,
            logger.disabled,
            tuple(logger.handlers),
            logger.propagate,
        )
        for name in _DIAGNOSTIC_LOGGERS
        for logger in (logging.getLogger(name),)
    }


def assert_private_diagnostics_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """摘要必须可见；不只检查格式化文本，也检查 LogRecord 保存的参数与异常。"""
    records = [
        record
        for record in caplog.records
        if record.name in _DIAGNOSTIC_LOGGERS
        or record.name == "qqbot_expand.user_info.http"
    ]
    assert records
    assert any("QQ user info HTTP" in record.getMessage() for record in records)
    for record in records:
        for marker in ("canary", "avatar.invalid", "private.invalid", "离线用户"):
            assert marker not in str(vars(record))


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
@pytest.mark.parametrize("outcome", [200, 401, 429, "timeout", "exception", "cancel"])
async def test_http_diagnostics_scope_restores_all_resources(
    user_env: SimpleNamespace,
    manager: TaskManager,
    caplog: pytest.LogCaptureFixture,
    level: int,
    outcome: int | str,
) -> None:
    """成功、HTTP 失败、超时、恶意异常和取消均需摘要可见并恢复所有资源。"""
    caplog.set_level(level)
    before = diagnostic_logging_state()
    client = user_env.plugin.http_client
    headers_before = dict(client.headers)
    hooks_before = {key: tuple(value) for key, value in client.event_hooks.items()}
    if isinstance(outcome, int):
        user_env.response = httpx.Response(outcome, json=profile())
    elif outcome == "timeout":
        user_env.transport_error = httpx.ReadTimeout(
            "timeout-canary https://private.invalid"
        )
    elif outcome == "exception":
        user_env.transport_error = RuntimeError(
            "exception-canary https://private.invalid"
        )
    else:
        user_env.http_entered, user_env.http_release = asyncio.Event(), asyncio.Event()

    task = start(manager, user_env.service.get_user_info("openid-private-canary"))
    if outcome == "cancel":
        await asyncio.wait_for(user_env.http_entered.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await asyncio.wait_for(task, timeout=3)
        assert result["success"] is (outcome == 200)
    assert len(user_env.requests) == 1
    assert_private_diagnostics_redacted(caplog)
    assert diagnostic_logging_state() == before
    assert dict(client.headers) == headers_before
    assert {
        key: tuple(value) for key, value in client.event_hooks.items()
    } == hooks_before

    caplog.clear()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
        trust_env=False,
    ) as unrelated:
        await unrelated.get("https://unrelated.invalid/after-query")
    assert "HTTP Request: GET https://unrelated.invalid/after-query" in caplog.text


@pytest.mark.parametrize("first_outcome", ["success", "failure", "cancel"])
async def test_concurrent_queries_do_not_hide_unrelated_http_logs(
    user_env: SimpleNamespace,
    manager: TaskManager,
    caplog: pytest.LogCaptureFixture,
    first_outcome: str,
) -> None:
    """两个真实查询共享一组过滤器；先结束/取消其中一个不得移除另一个的保护。"""
    caplog.set_level(logging.DEBUG)
    before = diagnostic_logging_state()
    entered: asyncio.Queue[str] = asyncio.Queue()
    gates = {name: asyncio.Event() for name in ("first-canary", "second-canary")}
    seen: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        """仅控制 HTTP 边界，用真实 Trace 模拟依赖诊断，不替换 Service 逻辑。"""
        seen.append(request)
        if request.url.host == "unrelated.invalid":
            return httpx.Response(204)
        name = request.url.path.split("/")[-2]
        async with Trace(
            "receive_response_headers",
            logging.getLogger("httpcore.http11"),
            kwargs={"url": request.url, "token": request.headers["Authorization"]},
        ) as trace:
            entered.put_nowait(name)
            await gates[name].wait()
            if name == "first-canary" and first_outcome == "failure":
                raise httpx.ReadTimeout("exception-canary")
            trace.return_value = profile()["avatar"]
            return httpx.Response(200, json=profile())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        trust_env=False,
    ) as client:
        user_env.plugin.http_client = client
        first = start(manager, user_env.service.get_user_info("first-canary"))
        second = start(
            manager,
            QQBotUserInfoService(plugin=user_env.plugin).get_user_info("second-canary"),
        )
        for _ in range(2):
            await asyncio.wait_for(entered.get(), timeout=3)
        active = diagnostic_logging_state()
        for name, state in active.items():
            assert len(state[0]) == len(before[name][0]) + 1
            assert state[1:] == before[name][1:]
        await client.get("https://unrelated.invalid/during-two-queries")
        if first_outcome == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            gates["first-canary"].set()
            result = await asyncio.wait_for(first, timeout=3)
            assert result["success"] is (first_outcome == "success")
        assert diagnostic_logging_state() == active and not second.done()
        await client.get("https://unrelated.invalid/during-one-query")
        gates["second-canary"].set()
        assert (await asyncio.wait_for(second, timeout=3))["success"]
        assert diagnostic_logging_state() == before
        await client.get("https://unrelated.invalid/after-two-queries")
    assert_private_diagnostics_redacted(caplog)
    for path in ("during-two-queries", "during-one-query", "after-two-queries"):
        assert f"HTTP Request: GET https://unrelated.invalid/{path}" in caplog.text
    query_requests = [
        request for request in seen if request.url.host != "unrelated.invalid"
    ]
    assert len(query_requests) == 2
    assert all(
        request.headers["Authorization"] == "QQBot offline-token-canary"
        for request in query_requests
    )
