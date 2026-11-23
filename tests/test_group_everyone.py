"""离线验证群 @全体的显式授权、管理员预检和一次性发送契约。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.app.plugin_system.api import adapter_api
from src.kernel.concurrency import TaskManager

from ..services.message_service import QQBotMessageService
from ..src.bridge import ADAPTER_SIGNATURE
from ..src.builders import build_button
from ..src.constants import MSG_SEQ_MAX
from ..src.everyone import contains_everyone, payload_mentions_everyone
from .conftest import make_plugin

TAG = "<qqbot-at-everyone />"
_INTENTS = (
    TAG,
    "<qqbot-at-everyone/>",
    '<qqbot-at-everyone ignored="x" />',
    "<QQBOT-AT-EVERYONE >",
    "<qqbot-at-everyone",
    "@everyone",
    "@everyone你好",
    "通知@everyone请查收",
    "<@all>",
)
_VARIANTS = ("text", "reply", "keyboard_native", "keyboard_template", "template")


class OfflineAPI:
    """仅替换公开 Adapter 边界和 HTTP 传输，保留真实业务与 bridge。"""

    def __init__(self) -> None:
        """预置合成管理员响应，并记录所有 HTTP 与 token 调用。"""
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.adapter_posts = 0
        self.state: Any = {"member_role": "admin"}
        self.state_status = 200
        self.post: Any = {"id": "sent-id", "ext_info": {"ref_idx": " reference-index "}}
        self.post_status = 200
        self.base_url = "https://api.sgroup.qq.com"
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.plugin = make_plugin(http_client=self.client)
        self.plugin.config.http.retry_max_attempts = 3
        self.service = QQBotMessageService(plugin=self.plugin)

    async def get_token(self) -> str:
        """模拟 Adapter 公开 token 接口，不读取真实凭据。"""
        self.token_calls += 1
        return "offline-token"

    async def post_api(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any]:
        """普通发送保留 Adapter 出口，仅把末端 HTTP 转到 MockTransport。"""
        self.adapter_posts += 1
        response = await self.client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.json()

    def get_adapter(self, signature: str) -> SimpleNamespace:
        """通过公开 Adapter 查询接口提供最小替身，禁止替换 bridge。"""
        assert signature == ADAPTER_SIGNATURE
        return SimpleNamespace(send_handler=self)

    def respond(self, request: httpx.Request) -> httpx.Response:
        """响应固定 GET/POST 边界，并允许模拟平台错误、取消与未知网络结果。"""
        self.requests.append(request)
        if request.method == "GET":
            assert request.url.raw_path.endswith(b"/bot_state")
            outcome, status = self.state, self.state_status
        else:
            assert request.method == "POST"
            assert request.url.raw_path.endswith(b"/messages")
            outcome, status = self.post, self.post_status
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        return httpx.Response(status, json=outcome)


@pytest.fixture
async def offline_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[OfflineAPI]:
    """安装网络末端替身；即使断言失败也关闭共享客户端。"""
    api = OfflineAPI()
    monkeypatch.setattr(adapter_api, "get_adapter", api.get_adapter)
    try:
        yield api
    finally:
        await api.client.aclose()


@pytest.fixture
async def everyone_tasks() -> AsyncIterator[TaskManager]:
    """使用真实且独立的任务管理器，测试失败时也取消并收回在途发送。"""
    manager = TaskManager()
    try:
        yield manager
    finally:
        for info in manager.get_active_tasks():
            info.cancel()
        async with asyncio.timeout(3):
            await manager.wait_all_tasks()
        manager.shutdown_process_pool(wait=True, cancel_futures=True)


def _transport_barrier(
    api: OfflineAPI, monkeypatch: pytest.MonkeyPatch, stage: str
) -> tuple[asyncio.Event, asyncio.Event]:
    """仅在公开认证或 Adapter POST 边界挂起，不替换业务构造和策略。"""
    entered, release = asyncio.Event(), asyncio.Event()
    get_token, post_api = api.get_token, api.post_api

    async def delayed_token() -> str:
        """让调用方有机会在真实鉴权 await 期间修改原始载荷。"""
        entered.set()
        await release.wait()
        return await get_token()

    async def delayed_post(
        url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any]:
        """证明即使 token 已返回，下一层 await 也不能重开检查竞态。"""
        entered.set()
        await release.wait()
        return await post_api(url, headers, body)

    if stage == "token":
        monkeypatch.setattr(api, "get_token", delayed_token)
    else:
        monkeypatch.setattr(api, "post_api", delayed_post)
    return entered, release


@pytest.mark.parametrize("variant", ["raw_text", "raw_template", "template", "keyboard_template"])
@pytest.mark.parametrize("stage", ["token", "post"])
@pytest.mark.parametrize("target_type", ["group", "user"])
@pytest.mark.parametrize("intent", [TAG, "通知@everyone请查收", "<@all>"])
async def test_mutation_during_auth_or_transport_uses_checked_snapshot(
    offline_api: OfflineAPI,
    everyone_tasks: TaskManager,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    stage: str,
    target_type: str,
    intent: str,
) -> None:
    """真实任务在两个 await 边界修改调用方原对象，请求必须保持检查时的 JSON。"""
    entered, release = _transport_barrier(offline_api, monkeypatch, stage)
    text = '<qqbot-at-user id="member-id" /> 普通通知'
    params = [{"key": "title", "values": [text]}]
    rows = [[build_button("确定")]]
    payload: dict[str, Any] = {
        "msg_type": 2,
        "markdown": {
            "custom_template_id": "t1", "params": params,
            "force_verify_image_resource": True,
        },
        "msg_id": "source-id", "msg_seq": 7,
    }
    if variant == "raw_text":
        payload.pop("markdown")
        payload.update({"msg_type": 0, "content": text})
    if variant.startswith("raw_"):
        payload["message_reference"] = {
            "message_id": "reference-id", "ignore_get_message_error": False,
        }
        pending = offline_api.service.send_raw_message(target_type, "g1", payload)
    elif variant == "template":
        pending = offline_api.service.send_markdown_template(
            target_type, "g1", "t1", params, force_verify_image_resource=True,
            msg_id="source-id", msg_seq=7,
        )
    else:
        payload["keyboard"] = {"content": {"rows": [{"buttons": rows[0]}]}}
        pending = offline_api.service.send_keyboard(
            target_type, "g1", rows, custom_template_id="t1", params=params,
            force_verify_image_resource=True, msg_id="source-id", msg_seq=7,
        )
    expected = json.loads(json.dumps(payload))
    if target_type == "group":
        expected.setdefault("content", "")
    task = everyone_tasks.create_task(pending, name="everyone-payload-snapshot").task
    assert task is not None
    async with asyncio.timeout(3):
        await entered.wait()
        assert offline_api.requests == []
        if variant == "raw_text":
            payload["content"] = intent
        else:
            params[0]["values"][0] = intent
            params[0]["values"].append("late-added")
        if variant.startswith("raw_"):
            payload["msg_id"] = "late-source"
            payload["message_reference"]["message_id"] = "late-reference"
        rows[0][0]["render_data"]["label"] = "late-label"
        release.set()
        result = await task
    assert result == {
        "success": True, "message_id": "sent-id", "ref_idx": "reference-index", "error": None,
    }
    assert [request.method for request in offline_api.requests] == ["POST"]
    assert json.loads(offline_api.requests[0].content) == expected
    assert intent not in offline_api.requests[0].content.decode("utf-8")
    assert offline_api.adapter_posts == 1
    assert everyone_tasks.get_active_task_count() == 0


@pytest.mark.parametrize("variant", ["raw", "template"])
@pytest.mark.parametrize("stage", ["token", "post"])
async def test_snapshot_send_cancellation_propagates_without_background_work(
    offline_api: OfflineAPI,
    everyone_tasks: TaskManager,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    stage: str,
) -> None:
    """快照建立后，认证或 POST 边界的取消仍传播，不发送、不登记、不留任务。"""
    entered, release = _transport_barrier(offline_api, monkeypatch, stage)
    payload = {"msg_type": 0, "content": "普通通知"}
    params = [{"key": "title", "values": ["普通通知"]}]
    pending = (
        offline_api.service.send_raw_message("group", "g1", payload)
        if variant == "raw" else
        offline_api.service.send_markdown_template("group", "g1", "t1", params)
    )
    task = everyone_tasks.create_task(pending, name="everyone-snapshot-cancel").task
    assert task is not None
    async with asyncio.timeout(3):
        await entered.wait()
        payload["content"] = TAG
        params[0]["values"][0] = TAG
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    release.set()
    assert offline_api.requests == []
    assert offline_api.adapter_posts == 0
    assert not offline_api.plugin.sent_messages.claim("sent-id", "group", "g1")
    assert everyone_tasks.get_active_task_count() == 0


def _invalid_snapshot_value(kind: str) -> Any:
    """构造真实不可发送 JSON 值，不 mock JSON 编解码或快照实现。"""
    if kind == "cycle_list":
        cycle_list: list[Any] = []
        cycle_list.append(cycle_list)
        return cycle_list
    if kind == "cycle_dict":
        cycle_dict: dict[str, Any] = {}
        cycle_dict["private-detail"] = cycle_dict
        return cycle_dict
    if kind in {"deep_list", "deep_dict"}:
        deep: Any = "private-detail"
        for _ in range(2000):
            deep = [deep] if kind == "deep_list" else {"nested": deep}
        return deep
    if kind == "opaque":
        class Opaque:
            """不透明值不得通过字符串化或自定义深复制变成消息。"""

            def __str__(self) -> str:
                """错误地转换不透明对象时立即暴露测试缺陷。"""
                raise AssertionError("private-detail must not be stringified")

            def __repr__(self) -> str:
                """错误地记录原对象时立即暴露测试缺陷。"""
                raise AssertionError("private-detail must not be logged")

            def __deepcopy__(self, memo: dict[int, Any]) -> Any:
                """快照不得执行自定义复制钩子。"""
                raise AssertionError("private-detail must not be copied")

        return Opaque()
    return {
        "bytes": b"private-detail", "set": {"private-detail"},
        "nan": float("nan"), "inf": float("inf"), "negative_inf": float("-inf"),
        "key": {("private-detail",): "unsupported-key"},
    }[kind]


@pytest.mark.parametrize("variant", ["raw", "template"])
@pytest.mark.parametrize(
    "kind",
    ["cycle_list", "cycle_dict", "deep_list", "deep_dict", "opaque",
     "bytes", "set", "nan", "inf", "negative_inf", "key"],
)
async def test_invalid_snapshot_rejected_safely_before_auth(
    offline_api: OfflineAPI, variant: str, kind: str
) -> None:
    """循环、超深与非 JSON 值必须在 token 之前安全失败，不泄漏异常内容。"""
    value = _invalid_snapshot_value(kind)
    if variant == "raw":
        result = await offline_api.service.send_raw_message(
            "group", "g1", {"msg_type": 0, "content": "普通通知", "unknown": value}
        )
    else:
        result = await offline_api.service.send_markdown_template(
            "group", "g1", "t1", [{"key": "title", "values": [value]}]
        )
    assert result == {
        "success": False, "message_id": "",
        "error": "消息体必须为可序列化 JSON，不能包含循环引用、过深结构或非 JSON 值",
    }
    assert "private-detail" not in str(result)
    assert offline_api.token_calls == 0
    assert offline_api.requests == [] and offline_api.adapter_posts == 0
    assert not offline_api.plugin.sent_messages.claim("sent-id", "group", "g1")


@pytest.mark.parametrize("target_type", ["user", "group"])
async def test_snapshot_preserves_json_semantics_without_mutating_raw_input(
    offline_api: OfflineAPI, target_type: str
) -> None:
    """合法嵌套 JSON 和元组仍按 HTTP 语义发送，群占位字段不再改写调用方对象。"""
    shared = {"values": [None, True, 3, 1.25, "中文"]}
    payload = {
        "msg_type": 2,
        "markdown": {"content": '<qqbot-at-user id="member-id" />',
                     "force_verify_image_resource": True},
        "unknown": (shared, shared),
    }
    expected = json.loads(json.dumps(payload))
    if target_type == "group":
        expected["content"] = ""
    result = await offline_api.service.send_raw_message(target_type, "g1", payload)
    assert result["success"] is True
    assert len(offline_api.requests) == 1
    assert json.loads(offline_api.requests[0].content) == expected
    assert "content" not in payload
    assert payload["unknown"] == (shared, shared)
    assert payload["unknown"][0] is shared


async def _ordinary_send(
    api: OfflineAPI, variant: str, target_type: str, text: str, **kwargs: Any
) -> dict[str, Any]:
    """选择普通公开发送入口，不替换参数构造或安全策略。"""
    if variant == "text":
        return await api.service.send_text(target_type, "openid", text, **kwargs)
    if variant == "reply":
        return await api.service.send_reply(
            target_type, "openid", text, "reference-id", **kwargs
        )
    rows = [[build_button("确定")]]
    if variant == "keyboard_native":
        return await api.service.send_keyboard(
            target_type, "openid", rows, content=text, **kwargs
        )
    params = [{"key": "title", "values": ["无害前缀", text]}]
    if variant == "keyboard_template":
        return await api.service.send_keyboard(
            target_type, "openid", rows, custom_template_id="t1", params=params,
            **kwargs,
        )
    assert variant == "template"
    return await api.service.send_markdown_template(
        target_type, "openid", "t1", params, rows=rows, **kwargs
    )


@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("target_type", ["group", "user"])
@pytest.mark.parametrize("intent", _INTENTS)
async def test_ordinary_entries_reject_everyone_before_network(
    offline_api: OfflineAPI, variant: str, target_type: str, intent: str
) -> None:
    """所有普通文本入口明确拒绝全体意图，而非升级协议或降级目标。"""
    result = await _ordinary_send(offline_api, variant, target_type, f"通知 {intent}")
    assert result["success"] is False
    assert result["message_id"] == ""
    assert "send_group_mention_everyone" in result["error"]
    assert offline_api.requests == []
    assert offline_api.token_calls == 0
    assert offline_api.adapter_posts == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"content": TAG},
        {"content": TAG, "markdown": {"content": TAG}},
        {"markdown": {"content": TAG}},
        {"markdown": {"params": [{"key": "title", "values": ["safe", TAG]}]}},
        {"markdown": {"params": [{"key": "title", "values": TAG}]}},
        {"markdown": {"params": [{"key": "title", "values": (TAG,)}]}},
    ],
)
async def test_raw_message_cannot_bypass_central_text_policy(
    offline_api: OfflineAPI, payload: dict[str, Any]
) -> None:
    """原始消息出口也必须拒绝全体意图，不能利用宽松模板参数构造绕过。"""
    result = await offline_api.service.send_raw_message("group", "g1", {"msg_type": 2, **payload})
    assert result["success"] is False
    assert "send_group_mention_everyone" in result["error"]
    assert offline_api.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {}, {"markdown": None}, {"markdown": []}, {"markdown": {}},
        {"markdown": {"params": {"values": [TAG]}}},
        {"markdown": {"params": [None, "ignored", {}, {"values": []}, {"values": 3}]}},
        {"markdown": {"params": [{"key": TAG, "values": ["safe"]}]}},
        {"media": {"file_info": TAG}}, {"unknown": {"content": TAG}},
    ],
)
def test_inspection_only_visits_known_text_fields(payload: dict[str, Any]) -> None:
    """固定层级检查忽略未知、不透明或非文本字段，不扩大为通用内容审查。"""
    assert not payload_mentions_everyone(payload)


def test_inspection_ignores_cycles_and_does_not_coerce_objects() -> None:
    """循环模板容器和不能字符串化的不透明数据不会导致递归或误判。"""
    class Opaque:
        """一旦错误地隐式字符串化即令测试失败。"""

        def __str__(self) -> str:
            """禁止策略扫描不透明对象。"""
            raise AssertionError("must not stringify opaque values")

    values: list[Any] = []
    values.append(values)
    params: list[Any] = []
    params.extend([params, {"key": "title", "values": values}, {"values": [Opaque()]}])
    payload: dict[str, Any] = {"markdown": {"params": params}, "content": Opaque()}
    payload["unknown"] = payload
    assert not payload_mentions_everyone(payload)
    assert not contains_everyone(Opaque())


async def test_keyboard_callback_data_is_not_mention_text(offline_api: OfflineAPI) -> None:
    """按钮不透明回调数据不是全体消息正文，维持已有按钮语义。"""
    result = await offline_api.service.send_keyboard(
        "group", "g1", [[build_button("确定", action_type=1, data=TAG)]], content="普通菜单"
    )
    assert result["success"] is True
    assert len(offline_api.requests) == 1
    assert json.loads(offline_api.requests[0].content)["keyboard"]["content"]["rows"][0]["buttons"][0]["action"]["data"] == TAG


@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("target_type", ["group", "user"])
@pytest.mark.parametrize("text", ['<qqbot-at-user id="member-id" />', "<@member-id>", "普通消息"])
async def test_single_mentions_and_ordinary_content_keep_existing_behavior(
    offline_api: OfflineAPI, variant: str, target_type: str, text: str
) -> None:
    """单人 @、引用、强校验选项和原有 Adapter 发送通道保持不变。"""
    options: dict[str, Any] = {"msg_id": "source-id", "msg_seq": 7}
    if variant not in {"text", "reply"}:
        options["force_verify_image_resource"] = True
    result = await _ordinary_send(offline_api, variant, target_type, text, **options)
    assert result == {
        "success": True, "message_id": "sent-id", "ref_idx": "reference-index", "error": None,
    }
    assert offline_api.adapter_posts == 1
    assert len(offline_api.requests) == 1
    request = offline_api.requests[0]
    assert request.method == "POST"
    segment = "groups" if target_type == "group" else "users"
    assert request.url.path == f"/v2/{segment}/openid/messages"
    body = json.loads(request.content)
    assert body["msg_id"] == "source-id" and body["msg_seq"] == 7
    if variant in {"text", "reply"}:
        assert body["content"] == text
    else:
        assert body["markdown"]["force_verify_image_resource"] is True
        assert "force_verify_image_resource" not in body
        if variant == "keyboard_native":
            assert body["markdown"]["content"] == text
        else:
            assert body["markdown"]["params"][0]["values"] == ["无害前缀", text]
    if variant == "reply":
        assert body["message_reference"] == {
            "message_id": "reference-id", "ignore_get_message_error": False,
        }


@pytest.mark.parametrize("markdown", [False, True])
@pytest.mark.parametrize(
    ("options", "passive"),
    [
        ({}, {}),
        ({"msg_id": "source-id"}, {"msg_id": "source-id", "msg_seq": 1}),
        ({"event_id": "event-id"}, {"event_id": "event-id"}),
        ({"msg_id": "source-id", "msg_seq": MSG_SEQ_MAX},
         {"msg_id": "source-id", "msg_seq": MSG_SEQ_MAX}),
        ({"msg_seq": 2}, {"msg_seq": 2}),
    ],
)
async def test_confirmed_admin_sends_one_new_tag_with_real_bridge(
    offline_api: OfflineAPI, markdown: bool, options: dict[str, Any], passive: dict[str, Any]
) -> None:
    """校验 GET 管理员状态到 POST 的真实 URL、请求头、载荷和引用登记。"""
    content = '公告 <qqbot-at-user id="member-id" />'
    result = await offline_api.service.send_group_mention_everyone(
        "g/1?next=user", content, confirm=True, markdown=markdown, **options
    )
    assert result == {
        "success": True, "message_id": "sent-id", "ref_idx": "reference-index", "error": None,
    }
    assert offline_api.adapter_posts == 0
    assert offline_api.token_calls == 2
    assert [request.method for request in offline_api.requests] == ["GET", "POST"]
    for request, suffix in zip(offline_api.requests, ("bot_state", "messages"), strict=True):
        assert str(request.url) == (
            f"https://api.sgroup.qq.com/v2/groups/g%2F1%3Fnext%3Duser/{suffix}"
        )
        assert request.headers["authorization"] == "QQBot offline-token"
        assert request.headers["content-type"] == "application/json"
    assert offline_api.requests[0].content == b""
    body = json.loads(offline_api.requests[1].content)
    text = f"{TAG} {content}"
    expected = (
        {"msg_type": 2, "markdown": {"content": text}, "content": ""}
        if markdown else {"msg_type": 0, "content": text}
    )
    assert body == {**expected, **passive}
    assert json.dumps(body).count(TAG) == 1
    assert offline_api.plugin.sent_messages.claim("sent-id", "group", "g/1?next=user")
    assert not offline_api.plugin.sent_messages.claim("reference-index", "group", "g/1?next=user")


@pytest.mark.parametrize("confirm", [False, None, 0, 1, "true", [], {}])
async def test_confirmation_must_be_literal_true(offline_api: OfflineAPI, confirm: Any) -> None:
    """真值对象不等于明确授权，所有失败均早于状态查询。"""
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=confirm)
    assert result["success"] is False and "confirm" in result["error"]
    assert offline_api.requests == [] and offline_api.token_calls == 0


async def test_confirmation_is_opt_in(offline_api: OfflineAPI) -> None:
    """省略 confirm 必须拒绝，不能默认向 LLM 暴露高副作用能力。"""
    result = await offline_api.service.send_group_mention_everyone("g1", "通知")
    assert result["success"] is False and "confirm" in result["error"]
    assert offline_api.requests == []


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"group_openid": ""}, "target_id"),
        ({"group_openid": "  "}, "target_id"),
        ({"group_openid": None}, "target_id"),
        ({"group_openid": 1}, "target_id"),
        ({"group_openid": "../users/u1"}, ".."),
        ({"content": ""}, "content"),
        ({"content": "  "}, "content"),
        ({"content": None}, "content"),
        ({"content": 123}, "content"),
        ({"markdown": 1}, "markdown"),
        ({"markdown": None}, "markdown"),
        ({"markdown": "true"}, "markdown"),
        ({"msg_seq": 0}, "msg_seq"),
        ({"msg_seq": True}, "msg_seq"),
        ({"msg_seq": MSG_SEQ_MAX + 1}, "msg_seq"),
        ({"msg_id": "m", "event_id": "e"}, "互斥"),
        ({"msg_id": 1}, "msg_id"),
        ({"event_id": []}, "event_id"),
    ],
)
async def test_invalid_arguments_do_not_query_or_send(
    offline_api: OfflineAPI, overrides: dict[str, Any], error: str
) -> None:
    """仅专用入口收紧必需参数，不允许无效路由或引用进入网络。"""
    kwargs = {"group_openid": "g1", "content": "通知", "confirm": True, **overrides}
    result = await offline_api.service.send_group_mention_everyone(**kwargs)
    assert result["success"] is False and error in result["error"]
    assert offline_api.requests == []


@pytest.mark.parametrize("intent", [*_INTENTS, f"{TAG} {TAG}"])
@pytest.mark.parametrize("markdown", [False, True])
async def test_special_entry_rejects_preexisting_everyone(
    offline_api: OfflineAPI, intent: str, markdown: bool
) -> None:
    """专用入口自己插入唯一新标签，不接受混入或多个全体标签。"""
    result = await offline_api.service.send_group_mention_everyone(
        "g1", f"通知 {intent}", confirm=True, markdown=markdown
    )
    assert result["success"] is False and "全体" in result["error"]
    assert offline_api.requests == []


@pytest.mark.parametrize(
    "state",
    [
        {}, None, [], "admin", {"member_role": "member"}, {"member_role": "owner"},
        {"member_role": "ADMIN"}, {"member_role": " admin "}, {"member_role": True},
        {"member_role": ["admin"]}, {"data": {"member_role": "admin"}},
        {"code": 11253, "message": "private-detail"},
        {"member_role": "admin", "code": 11253, "message": "private-detail"},
        {"member_role": "admin", "err_code": 11253},
        httpx.Response(200, text="not-json private-detail"),
        httpx.Response(204),
    ],
)
async def test_non_admin_malformed_or_business_error_state_never_posts(
    offline_api: OfflineAPI, state: Any
) -> None:
    """只接受合法 admin 状态；不能从 HTTP 成功或嵌套文本猜测权限。"""
    offline_api.state = state
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False
    assert "管理员" in result["error"]
    assert "private-detail" not in str(result)
    assert [request.method for request in offline_api.requests] == ["GET"]
    assert offline_api.adapter_posts == 0


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500, 503])
async def test_state_query_failure_stops_send(offline_api: OfflineAPI, status: int) -> None:
    """白名单权限、限频、重定向及服务故障均不跳过状态查询结果。"""
    offline_api.state_status = status
    offline_api.state = {"code": 11253, "message": "private-detail"}
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False
    assert "private-detail" not in str(result)
    assert [request.method for request in offline_api.requests] == ["GET"]


async def test_state_network_failure_never_reaches_post(offline_api: OfflineAPI) -> None:
    """只读 GET 可沿用已有重试，但所有查询失败时绝不能发送。"""
    offline_api.state = httpx.ReadTimeout("private-detail")
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False and "private-detail" not in str(result)
    assert [request.method for request in offline_api.requests] == ["GET"] * 4
    assert offline_api.adapter_posts == 0


@pytest.mark.parametrize("fail_at", [1, 2])
async def test_token_failure_is_not_replayed(
    offline_api: OfflineAPI, monkeypatch: pytest.MonkeyPatch, fail_at: int
) -> None:
    """只替换公开 token 边界；预检或发送取 token 失败不能重放。"""
    async def get_token() -> str:
        """在指定次获取凭据时模拟脱敏错误。"""
        offline_api.token_calls += 1
        if offline_api.token_calls == fail_at:
            raise RuntimeError("token private-detail")
        return "offline-token"

    monkeypatch.setattr(offline_api, "get_token", get_token)
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False and "private-detail" not in str(result)
    assert offline_api.token_calls == fail_at
    expected = [] if fail_at == 1 else ["GET"]
    assert [request.method for request in offline_api.requests] == expected
    assert offline_api.adapter_posts == 0


@pytest.mark.parametrize("missing", ["adapter", "handler", "client", "disabled"])
async def test_unavailable_dependencies_never_send(
    offline_api: OfflineAPI, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Adapter、共享客户端或群信息 Service 不可用时直接失败。"""
    if missing == "adapter":
        monkeypatch.setattr(adapter_api, "get_adapter", lambda _signature: None)
    elif missing == "handler":
        monkeypatch.setattr(adapter_api, "get_adapter", lambda _signature: SimpleNamespace())
    elif missing == "client":
        offline_api.plugin.http_client = None
    else:
        offline_api.plugin.config.features.enable_group_info_service = False
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False
    assert offline_api.requests == [] and offline_api.adapter_posts == 0


@pytest.mark.parametrize("status", [302, 400, 403, 429, 500, 503])
async def test_post_error_is_single_attempt_without_fallback(
    offline_api: OfflineAPI, status: int
) -> None:
    """QQ 平台限额或服务端错误只产生一次 POST，不能转普通消息或私聊。"""
    offline_api.post_status = status
    offline_api.post = {"code": 429, "message": "private-detail", "id": "unconfirmed-id"}
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False and "private-detail" not in str(result)
    assert [request.method for request in offline_api.requests] == ["GET", "POST"]
    assert offline_api.adapter_posts == 0
    assert not offline_api.plugin.sent_messages.claim("unconfirmed-id", "group", "g1")


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("private-detail"), httpx.ConnectError("private-detail")])
async def test_unknown_post_outcome_is_never_replayed(
    offline_api: OfflineAPI, failure: BaseException
) -> None:
    """客户端设置允许重试也不能重放 @全体；未知结果保留为失败。"""
    offline_api.post = failure
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False and "private-detail" not in str(result)
    assert [request.method for request in offline_api.requests] == ["GET", "POST"]
    assert offline_api.adapter_posts == 0 and offline_api.token_calls == 2


@pytest.mark.parametrize("stage", ["state", "post"])
async def test_cancellation_propagates_without_replay(offline_api: OfflineAPI, stage: str) -> None:
    """查询或发送期间取消均传播取消，不转普通发送、不后台重试。"""
    setattr(offline_api, stage, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    expected = ["GET"] if stage == "state" else ["GET", "POST"]
    assert [request.method for request in offline_api.requests] == expected
    assert offline_api.adapter_posts == 0


@pytest.mark.parametrize(
    "post",
    [
        {}, {"id": 123}, {"id": "  "},
        {"id": "unconfirmed-id", "code": 429, "message": "private-detail"},
        {"id": "unconfirmed-id", "err_code": 11253},
        httpx.Response(200, text="not-json private-detail"),
    ],
)
async def test_unconfirmed_post_response_is_failure_without_replay(
    offline_api: OfflineAPI, post: Any
) -> None:
    """业务错误或缺少消息 ID 不能伪装成功，也不能自动再发。"""
    offline_api.post = post
    result = await offline_api.service.send_group_mention_everyone("g1", "通知", confirm=True)
    assert result["success"] is False and "private-detail" not in str(result)
    assert [request.method for request in offline_api.requests] == ["GET", "POST"]
    assert not offline_api.plugin.sent_messages.claim("unconfirmed-id", "group", "g1")


async def test_each_explicit_call_rechecks_platform_without_local_quota(offline_api: OfflineAPI) -> None:
    """平台是自然日额度唯一权威，成功记录不作为本地额度或管理员缓存。"""
    assert (await offline_api.service.send_group_mention_everyone("g1", "首次", confirm=True))["success"]
    offline_api.post_status = 429
    offline_api.post = {"code": 429, "message": "quota exhausted"}
    second = QQBotMessageService(plugin=offline_api.plugin)
    assert not (await second.send_group_mention_everyone("g1", "再次", confirm=True))["success"]
    assert [request.method for request in offline_api.requests] == ["GET", "POST", "GET", "POST"]
    assert offline_api.adapter_posts == 0
