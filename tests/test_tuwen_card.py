"""图文卡片纯构造与真实 Service→bridge→传输替身的离线契约测试。"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from ..services.message_service import QQBotMessageService
from ..src.card_builder import build_tuwen_card
from ..src.constants import API_BASE_PRODUCTION, MSG_SEQ_MAX, MSG_TYPE_CARD
from ..src.errors import ERROR_BAD_REQUEST, ERROR_GENERIC, ERROR_TIMEOUT
from .conftest import FakeSendHandler, make_plugin

_VALID = {
    "title": "更新通知",
    "description": "查看本次更新内容",
    "url": "https://example.com/detail?ref=1#info",
    "pic_url": "https://example.com/cover.png",
}
_INVALID_URLS = [
    None, 1, True, b"https://example.com", "",
    "http://example.com", "ftp://example.com", "//example.com",
    "https:example.com/path", "https:///missing", "https://",
    "https://user@example.com", "https://user:secret@example.com",
    "https://@example.com", "https://example.com:0",
    "https://example.com:-1", "https://example.com:65536",
    "https://example.com:invalid", "https://example.com:",
    "https://example.com:443:80", "https://[::1", "https://[invalid]",
    "https://[2001:db8::1]suffix/path", "https://[fe80::1%25eth0]/",
    "https://exa mple.com", " https://example.com", "https://example.com/a b",
    "https://example.com/a\nb", "\nhttps://example.com", "https://example.com/\t",
    "https://example.com/\r", "https://example.com/\x00",
    "https://example.com/\x7f", "https://example.com/\x85",
    "https://example.com/\ud800", "https://example.com\\evil",
    "https://exa_mple.com", "https://-example.com", "https://example-.com",
    "https://example..com", "https://%65xample.com", "https://999.999.999.999",
    "https://example.com/%", "https://example.com/%GG",
    "https://example.com/%0a", "https://example.com/%00",
    "https://example.com/%7f", "https://example.com/%c2%85",
    "https://example.com/<script>", "https://example.com/\"quote",
    "https://exa\u200dmple.com", "https://exa／mple.com",
    "https://" + "a" * 64 + ".com",
    "https://" + ".".join(["a" * 63] * 4),
    "https://example.com/" + "x" * 1024,
]
_INVALID_FIELDS = [
    (field, value)
    for field, maximum in (("title", 50), ("description", 200))
    for value in ("", "   ", None, 0, True, [], "中" * (maximum + 1))
] + [
    (field, value)
    for field in ("url", "pic_url")
    for value in _INVALID_URLS
]


def test_card_contains_only_official_fields() -> None:
    """新类型为 8，card 只有 type=tuwen 与四个 content 字段。"""
    assert MSG_TYPE_CARD == 8
    assert build_tuwen_card(**_VALID) == {"type": "tuwen", "content": _VALID}


@pytest.mark.parametrize(("field", "maximum"), [("title", 50), ("description", 200)])
@pytest.mark.parametrize("at_maximum", [False, True])
@pytest.mark.parametrize("pattern", ["A", "中", "😀", "中a😀e\u0301"])
def test_text_boundaries_count_unicode_code_points(
    field: str, maximum: int, at_maximum: bool, pattern: str
) -> None:
    """len 按 Unicode 码点计数，中文/emoji 不使用菜单中文计二算法。"""
    size = maximum if at_maximum else 1
    value = (pattern * size)[:size]
    fields = {**_VALID, field: value}
    assert build_tuwen_card(**fields)["content"][field] == value


def test_text_is_not_silently_trimmed() -> None:
    """合法文本保留空格和组合字符，不做归一化或静默截断。"""
    fields = {**_VALID, "title": " 标题 ", "description": "e\u0301 中😀 "}
    assert build_tuwen_card(**fields)["content"] == fields


@pytest.mark.parametrize("field", ["url", "pic_url"])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com", "HTTPS://Example.COM:443/a?b=1#c",
        "https://example.com:1/a", "https://example.com:65535/a",
        "https://example.com./a", "https://192.0.2.1/a",
        "https://[2001:db8::1]:8443/a", "https://例子.测试/详情",
        "https://example.com/a%20b?q=%E4%B8%AD",
    ],
)
def test_accepts_valid_https_urls_without_rewriting(field: str, url: str) -> None:
    """接受合法 HTTPS 域名/IP/端口和编码路径，不改写 URL。"""
    assert build_tuwen_card(**{**_VALID, field: url})["content"][field] == url


@pytest.mark.parametrize("field", ["url", "pic_url"])
@pytest.mark.parametrize("length", [1024, 1025])
def test_url_length_boundary(field: str, length: int) -> None:
    """两个 URL 上限均为 1024，超长时拒绝而不是截短。"""
    prefix = "https://example.com/"
    value = prefix + "x" * (length - len(prefix))
    fields = {**_VALID, field: value}
    if length == 1024:
        assert build_tuwen_card(**fields)["content"][field] == value
    else:
        with pytest.raises(ValueError, match=field):
            build_tuwen_card(**fields)


@pytest.mark.parametrize(("field", "value"), _INVALID_FIELDS)
def test_builder_rejects_invalid_fields(field: str, value: Any) -> None:
    """非法文本及 URL 统一抛 ValueError，错误只说明字段不泄露输入。"""
    with pytest.raises(ValueError, match=field):
        build_tuwen_card(**{**_VALID, field: value})


@pytest.mark.parametrize("target_type", ["user", "group"])
async def test_service_final_json_and_reference_registration(
    patch_send_handler: FakeSendHandler, target_type: str
) -> None:
    """真实链路保留路径、空群 content、撤回 ID、引用索引及归属登记。"""
    service = QQBotMessageService(plugin=make_plugin())
    patch_send_handler.post_result = {
        "id": "card-message-id", "ext_info": {"ref_idx": " card-ref-index "}
    }
    result = await service.send_tuwen_card(target_type, "id/with?chars", **_VALID)
    expected: dict[str, Any] = {
        "msg_type": 8, "card": {"type": "tuwen", "content": _VALID},
    }
    if target_type == "group":
        expected["content"] = ""
    assert len(patch_send_handler.posts) == 1
    url, _headers, payload = patch_send_handler.posts[0]
    segment = "users" if target_type == "user" else "groups"
    assert url == f"{API_BASE_PRODUCTION}/v2/{segment}/id%2Fwith%3Fchars/messages"
    assert payload == expected
    assert not {"ark", "embed", "template_id"}.intersection(payload)
    assert result == {
        "success": True, "message_id": "card-message-id",
        "ref_idx": "card-ref-index", "error": None,
    }
    assert not service.plugin.sent_messages.claim(
        "card-message-id", target_type, "other-target"
    )
    assert service.plugin.sent_messages.claim(
        "card-message-id", target_type, "id/with?chars"
    )
    assert not service.plugin.sent_messages.claim(
        "card-ref-index", target_type, "id/with?chars"
    )


@pytest.mark.parametrize(("field", "value"), _INVALID_FIELDS)
async def test_service_rejects_invalid_fields_before_transport(
    patch_send_handler: FakeSendHandler, field: str, value: Any
) -> None:
    """非法字段经真实 guard 转为标准失败，绝不发起 POST。"""
    result = await QQBotMessageService(plugin=make_plugin()).send_tuwen_card(
        "user", "openid", **{**_VALID, field: value}
    )
    assert result["success"] is False
    assert result["message_id"] == ""
    assert field in result["error"]
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("target_type", ["user", "group"])
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, {}),
        ({"msg_id": "source-id"}, {"msg_id": "source-id", "msg_seq": 1}),
        ({"msg_id": "source-id", "msg_seq": 7}, {"msg_id": "source-id", "msg_seq": 7}),
        ({"event_id": "event-id"}, {"event_id": "event-id"}),
        ({"event_id": "event-id", "msg_seq": MSG_SEQ_MAX},
         {"event_id": "event-id", "msg_seq": MSG_SEQ_MAX}),
        ({"msg_seq": 1}, {"msg_seq": 1}),
    ],
)
async def test_passive_fields_follow_existing_rules(
    patch_send_handler: FakeSendHandler,
    target_type: str,
    kwargs: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """主动/消息被动/事件被动的缺省和显式序号复用现有规则。"""
    result = await QQBotMessageService(plugin=make_plugin()).send_tuwen_card(
        target_type, "openid", **_VALID, **kwargs
    )
    assert result["success"] is True
    body = patch_send_handler.posts[0][2]
    assert {key: body[key] for key in ("msg_id", "event_id", "msg_seq") if key in body} == expected


@pytest.mark.parametrize("target_type", ["user", "group"])
async def test_rejects_conflicting_passive_sources(
    patch_send_handler: FakeSendHandler, target_type: str
) -> None:
    """消息与事件引用互斥，不能发送两者都有的卡片。"""
    result = await QQBotMessageService(plugin=make_plugin()).send_tuwen_card(
        target_type, "openid", **_VALID, msg_id="source-id", event_id="event-id"
    )
    assert result["success"] is False
    assert "互斥" in result["error"]
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("value", [0, -1, MSG_SEQ_MAX + 1, True, "1", 1.5])
async def test_invalid_sequence_rejected_before_transport(
    patch_send_handler: FakeSendHandler, value: Any
) -> None:
    """序号错误在网络前失败，不被 builder 或传输隐式修正。"""
    result = await QQBotMessageService(plugin=make_plugin()).send_tuwen_card(
        "group", "openid", **_VALID, msg_seq=value
    )
    assert result["success"] is False
    assert "msg_seq" in result["error"]
    assert patch_send_handler.posts == []


@pytest.mark.parametrize(
    ("target_type", "target_id"),
    [("channel", "id"), (None, "id"), ("user", ""), ("group", "  "), ("user", None)],
)
async def test_invalid_target_rejected_before_transport(
    patch_send_handler: FakeSendHandler, target_type: Any, target_id: Any
) -> None:
    """目标校验沿用 Service，不发起无效目标请求。"""
    result = await QQBotMessageService(plugin=make_plugin()).send_tuwen_card(
        target_type, target_id, **_VALID
    )
    assert result["success"] is False
    assert "target_" in result["error"]
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("failure", ["http", "business", "timeout", "exception"])
async def test_errors_are_sanitized_without_fallback_or_registration(
    patch_send_handler: FakeSendHandler, failure: str
) -> None:
    """平台/网络异常保留失败结构，不降级 Ark/文本，不登记失败消息。"""
    service = QQBotMessageService(plugin=make_plugin())
    secret = "sensitive-detail"
    expected_error = ERROR_BAD_REQUEST
    if failure == "http":
        request = httpx.Request("POST", f"https://example.com/{secret}")
        response = httpx.Response(
            400, request=request, json={"code": 40030001, "message": secret}
        )
        patch_send_handler.post_result = httpx.HTTPStatusError(
            secret, request=request, response=response
        )
    elif failure == "business":
        patch_send_handler.post_result = {"code": 40030001, "message": secret, "id": "bad-id"}
    elif failure == "timeout":
        patch_send_handler.post_result = TimeoutError(f"timeout {secret}")
        expected_error = ERROR_TIMEOUT
    else:
        patch_send_handler.post_result = RuntimeError(secret)
        expected_error = ERROR_GENERIC
    result = await service.send_tuwen_card("user", "openid", **_VALID)
    assert result == {"success": False, "message_id": "", "error": expected_error}
    assert secret not in str(result)
    assert len(patch_send_handler.posts) == 1
    assert patch_send_handler.posts[0][2]["msg_type"] == 8
    assert not service.plugin.sent_messages.claim("bad-id", "user", "openid")


@pytest.mark.parametrize("ext_info", [None, {}, {"ref_idx": 7}, {"ref_idx": "  "}])
async def test_missing_reference_index_uses_existing_empty_value(
    patch_send_handler: FakeSendHandler, ext_info: Any
) -> None:
    """卡片没有有效引用索引时仍按旧发送结果返回空字符串。"""
    patch_send_handler.post_result = {"id": "card-id", "ext_info": ext_info}
    result = await QQBotMessageService(plugin=make_plugin()).send_tuwen_card(
        "user", "openid", **_VALID
    )
    assert result == {
        "success": True, "message_id": "card-id", "ref_idx": "", "error": None,
    }
