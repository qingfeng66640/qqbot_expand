"""通过真实消息 Service 和 bridge 验证 Markdown 最终 POST 契约。"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from ..services.message_service import QQBotMessageService
from ..src.builders import build_button
from ..src.constants import API_BASE_PRODUCTION, MSG_TYPE_MARKDOWN
from ..src.errors import ERROR_BAD_REQUEST
from .conftest import FakeSendHandler, make_plugin

_VARIANTS = ("keyboard_native", "keyboard_template", "template")
_PARAMS = [{"key": "title", "values": ["标题"]}]


async def _send_markdown(
    service: QQBotMessageService,
    variant: str,
    target_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """调用真实入口，仅替换发送链路最末端的 SendHandler。"""
    rows = [[build_button("确定")]]
    if variant == "keyboard_native":
        return await service.send_keyboard(
            target_type, "openid", rows, content="# 标题", **kwargs
        )
    if variant == "keyboard_template":
        return await service.send_keyboard(
            target_type, "openid", rows,
            custom_template_id="t1", params=_PARAMS, **kwargs,
        )
    return await service.send_markdown_template(
        target_type, "openid", "t1", _PARAMS, rows=rows, **kwargs
    )


@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("target_type", ["user", "group"])
@pytest.mark.parametrize("force_verify", [False, True])
async def test_final_json_nests_image_verification_in_markdown(
    patch_send_handler: FakeSendHandler,
    variant: str,
    target_type: str,
    force_verify: bool,
) -> None:
    """原生/模板与群聊/C2C 均只在 markdown 内输出 True。"""
    service = QQBotMessageService(plugin=make_plugin())
    patch_send_handler.post_result = {
        "id": "sent-id", "ext_info": {"ref_idx": " reference-index "}
    }
    result = await _send_markdown(
        service, variant, target_type,
        force_verify_image_resource=force_verify, msg_id="source-id", msg_seq=7,
    )
    markdown: dict[str, Any] = (
        {"content": "# 标题"}
        if variant == "keyboard_native"
        else {"custom_template_id": "t1", "params": _PARAMS}
    )
    if force_verify:
        markdown["force_verify_image_resource"] = True
    expected = {
        "msg_type": MSG_TYPE_MARKDOWN,
        "markdown": markdown,
        "keyboard": {"content": {"rows": [{"buttons": [build_button("确定")]}]}},
        "msg_id": "source-id",
        "msg_seq": 7,
    }
    if target_type == "group":
        expected["content"] = ""
    assert len(patch_send_handler.posts) == 1
    url, _headers, body = patch_send_handler.posts[0]
    segment = "users" if target_type == "user" else "groups"
    assert url == f"{API_BASE_PRODUCTION}/v2/{segment}/openid/messages"
    assert body == expected
    assert "force_verify_image_resource" not in body
    assert result == {
        "success": True, "message_id": "sent-id",
        "ref_idx": "reference-index", "error": None,
    }
    assert service.plugin.sent_messages.claim("sent-id", target_type, "openid")
    assert not service.plugin.sent_messages.claim(
        "reference-index", target_type, "openid"
    )


@pytest.mark.parametrize("variant", _VARIANTS)
async def test_default_omits_verification_flag(
    patch_send_handler: FakeSendHandler, variant: str
) -> None:
    """旧调用方不传新选项时维持最小请求体与默认序号。"""
    result = await _send_markdown(
        QQBotMessageService(plugin=make_plugin()), variant, "user", msg_id="source-id"
    )
    assert result["success"] is True
    body = patch_send_handler.posts[0][2]
    assert body["msg_seq"] == 1
    assert "force_verify_image_resource" not in body
    assert "force_verify_image_resource" not in body["markdown"]


@pytest.mark.parametrize("variant", _VARIANTS)
async def test_event_reference_preserved(
    patch_send_handler: FakeSendHandler, variant: str
) -> None:
    """事件被动回复不被强校验标志改变，也不补消息序号。"""
    result = await _send_markdown(
        QQBotMessageService(plugin=make_plugin()), variant, "group",
        event_id="event-id", force_verify_image_resource=True,
    )
    assert result["success"] is True
    body = patch_send_handler.posts[0][2]
    assert body["event_id"] == "event-id"
    assert "msg_id" not in body
    assert "msg_seq" not in body


@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("target_type", ["user", "group"])
@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
async def test_non_boolean_rejected_before_transport(
    patch_send_handler: FakeSendHandler,
    variant: str,
    target_type: str,
    value: object,
) -> None:
    """非法布尔参数必须先返回失败，不触发网络替身。"""
    result = await _send_markdown(
        QQBotMessageService(plugin=make_plugin()), variant, target_type,
        force_verify_image_resource=value,
    )
    assert result == {
        "success": False, "message_id": "",
        "error": "force_verify_image_resource 必须是布尔值",
    }
    assert patch_send_handler.posts == []


@pytest.mark.parametrize("variant", _VARIANTS)
async def test_http_error_remains_sanitized(
    patch_send_handler: FakeSendHandler, variant: str
) -> None:
    """错误仍经 bridge 脱敏，不登记消息或降级重发。"""
    service = QQBotMessageService(plugin=make_plugin())
    request = httpx.Request("POST", "https://example.com/private-token")
    response = httpx.Response(
        400, request=request, json={"code": 40030001, "message": "private-token"}
    )
    patch_send_handler.post_result = httpx.HTTPStatusError(
        "private-token", request=request, response=response
    )
    result = await _send_markdown(
        service, variant, "user", force_verify_image_resource=True
    )
    assert result == {
        "success": False, "message_id": "", "error": ERROR_BAD_REQUEST,
    }
    assert len(patch_send_handler.posts) == 1
    assert not service.plugin.sent_messages.claim("msg-1", "user", "openid")
