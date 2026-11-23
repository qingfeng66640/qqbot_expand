"""面板创建失败诊断的端到端离线回归，不访问真实 QQ API。"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from ..services.menu_panel_service import QQBotMenuPanelService
from ..src import bridge, errors
from .conftest import FakeHttpClient, FakeSendHandler, make_plugin


PANEL = {"items": [{"name": "/help", "desc": "简要说明", "type": "command"}]}


def _response(status: int, payload: Any) -> httpx.Response:
    """构造带有不可泄露字段的纯内存 HTTP 响应。"""
    return httpx.Response(
        status, json=payload,
        request=httpx.Request("POST", "https://api.bot.qq.com/v2/panels?secret-url=yes"),
    )


def _http_error(response: httpx.Response) -> httpx.HTTPStatusError:
    """构造真实异常类型，但不执行 HTTP 请求。"""
    return httpx.HTTPStatusError(
        "secret-exception", request=response.request, response=response
    )


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (40030001, "参数错误"),
        (40030008, "链接格式"),
        (40030009, "并发冲突"),
        (40030011, "scope"),
        (40030012, "target_type"),
        (40030013, "数量限制"),
        (40030015, "元素类型"),
        (40030016, "必填字段"),
        (40030018, "当前场景"),
        (40030020, "安全校验"),
        (40030021, "关联对象"),
    ],
)
async def test_service_distinguishes_official_panel_codes(
    code: int, reason: str, patch_send_handler: FakeSendHandler,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建失败保留业务码、官方归因和操作标识，不重放 HTTP 400。"""
    patch_send_handler.post_result = _http_error(_response(
        400, {"code": 30001, "err_code": code, "message": "secret-server-body"}
    ))
    logs: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", logs.append)
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("group", "all", PANEL)

    assert set(result) == {"success", "data", "error"}
    assert result["success"] is False and result["data"] is None
    assert reason in result["error"]
    assert "HTTP 400" in result["error"]
    assert f"err_code={code}" in result["error"]
    assert len(patch_send_handler.posts) == 1
    assert len(logs) == 1
    for field in ["operation=panel_create", "status=400", "scope=group",
                  "target_type=all", "item_count=1", "name_units_max=5",
                  "desc_units_max=8", f"err_code={code}"]:
        assert field in logs[0]
    assert "secret" not in logs[0] + result["error"]


@pytest.mark.parametrize("code", [40039999, "40039999", None, True, -1, 1.5, "secret-code"])
async def test_unknown_or_malformed_code_does_not_invent_cause(
    code: Any, patch_send_handler: FakeSendHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未知或畸形业务码不能被一概解释为参数、权限或限频。"""
    patch_send_handler.post_result = _http_error(_response(
        400, {"err_code": code, "message": "secret-server-body", "trace_id": "a" * 32}
    ))
    logs: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", logs.append)
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("c2c", "all", PANEL)
    assert result["success"] is False
    assert "原因未确认" in result["error"]
    assert "HTTP 400" in result["error"]
    assert "参数" not in result["error"]
    text = " ".join(logs) + result["error"]
    assert all(secret not in text for secret in ["secret", "trace_id", "a" * 32])
    assert ("err_code=40039999" in text) is (code in [40039999, "40039999"])


@pytest.mark.parametrize("payload", [None, [], {"message": "secret-body"}])
async def test_nonobject_error_body_keeps_panel_failure_context(
    payload: Any, patch_send_handler: FakeSendHandler,
) -> None:
    """不要求失败正文一定可读，不回显原始正文。"""
    response = _response(400, payload)
    if payload is None:
        response = httpx.Response(400, text="secret-html", request=response.request)
    patch_send_handler.post_result = _http_error(response)
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("group", "all", PANEL)
    assert result["success"] is False
    assert "HTTP 400" in result["error"] and "原因未确认" in result["error"]
    assert "secret" not in result["error"]


@pytest.mark.parametrize("payload", [
    {"err_code": 40030009},
    {"code": 40030020},
    {"code": 0, "err_code": "40030013"},
])
async def test_decoded_business_failure_without_message_is_not_success(
    payload: dict[str, Any], patch_send_handler: FakeSendHandler,
) -> None:
    """Adapter 解码结果只有非零业务码时仍失败，不能伪造其 HTTP 状态。"""
    patch_send_handler.post_result = payload
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("group", "all", PANEL)
    assert result["success"] is False and result["data"] is None
    assert "HTTP 状态未记录" in result["error"]
    assert "HTTP 200" not in result["error"]


@pytest.mark.parametrize(("status", "expected"), [
    (401, errors.ERROR_TOKEN), (403, errors.ERROR_FORBIDDEN),
    (429, errors.ERROR_RATE_LIMIT), (503, errors.ERROR_SERVER),
])
async def test_authoritative_http_status_is_not_overridden_by_body(
    status: int, expected: str, patch_send_handler: FakeSendHandler,
) -> None:
    """鉴权、权限、限频和服务端状态优先，不误用正文里的面板码。"""
    patch_send_handler.post_result = _http_error(_response(status, {"err_code": 40030020}))
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("group", "all", PANEL)
    assert expected in result["error"]
    assert f"HTTP {status}" in result["error"]


async def test_panel_debug_summary_never_logs_body_or_target_identifiers(
    patch_send_handler: FakeSendHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使开启完整负载调试，面板专用路径也只输出白名单计数。"""
    patch_send_handler.post_result = _http_error(_response(400, {"err_code": 40030009}))
    plugin = make_plugin(enable_menu_panel_service=True)
    plugin.config.features.debug_log_payload = True
    logs: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", logs.append)
    monkeypatch.setattr(bridge.logger, "info", logs.append)
    panel = {"items": [{"name": "/secret", "type": "link",
                        "link": "https://private.invalid/?token=secret-link"}],
             "remark": "secret-remark"}
    result = await QQBotMenuPanelService(plugin).create_panel(
        "group", "specific", panel, group_openids=["secret-group-openid"]
    )
    text = " ".join(logs) + result["error"]
    assert "group_count=1" in text and "link_count=1" in text
    assert all(secret not in text for secret in [
        "/secret", "secret-link", "secret-remark", "secret-group-openid",
        "private.invalid", "fake-token", "secret-url",
    ])
    assert len(patch_send_handler.posts) == 1


@pytest.mark.parametrize("status", [400, 200])
async def test_single_attempt_transport_has_same_panel_diagnostics(
    status: int, patch_send_handler: FakeSendHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式一次性 POST 分支也识别面板错误，不影响既有重试开关。"""
    client = FakeHttpClient([_response(status, {"err_code": 40030009})])
    logs: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", logs.append)
    result = await bridge.api_request(
        make_plugin(http_client=client), "POST", "/v2/panels", PANEL,
        retry_network_errors=False, operation="panel_create",
    )
    assert result["success"] is False
    assert "并发冲突" in result["error"] and f"HTTP {status}" in result["error"]
    assert len(client.calls) == 1 and patch_send_handler.posts == []
    assert "operation=panel_create" in logs[0]


@pytest.mark.parametrize("body", [None, {}, {"scope": [], "target_type": {}, "panel": []},
                                     {"scope": "secret-scope", "panel": {"items": [None, "secret-item"]}}])
async def test_diagnostic_summary_is_total_for_malformed_input(
    body: Any, patch_send_handler: FakeSendHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直调桥接层的畸形输入不会让诊断抛异常或输出任意字符串。"""
    patch_send_handler.post_result = _http_error(_response(400, {"err_code": 40030009}))
    logs: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", logs.append)
    result = await bridge.api_request(
        make_plugin(), "POST", "/v2/panels", body, operation="panel_create"
    )
    assert result["success"] is False and "并发冲突" in result["error"]
    assert "operation=panel_create" in logs[0] and "secret" not in logs[0]


async def test_success_contract_and_other_operations_are_unchanged(
    patch_send_handler: FakeSendHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功与非面板接口仍沿用原返回结构和错误文案。"""
    patch_send_handler.post_result = {"panel_id": "panel-fixture", "code": 0}
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("group", "all", PANEL)
    assert result == {"success": True, "data": {"panel_id": "panel-fixture", "code": 0}, "error": None}
    logs: list[str] = []
    monkeypatch.setattr(bridge.logger, "warning", logs.append)
    patch_send_handler.post_result = _http_error(_response(400, {"err_code": 40030009}))
    result = await bridge.api_request(make_plugin(), "POST", "/v2/panels", PANEL)
    assert result["error"] == errors.ERROR_BAD_REQUEST
    assert "operation=panel_create" not in logs[0]
    logs.clear()
    result = await bridge.api_request(
        make_plugin(), "POST", "/other", PANEL, operation="panel_create"
    )
    assert result["error"] == errors.ERROR_BAD_REQUEST
    assert "operation=panel_create" not in logs[0]


@pytest.mark.parametrize("code", [0, "0", "0000000000"])
async def test_zero_business_code_with_message_keeps_success(
    code: int | str, patch_send_handler: FakeSendHandler,
) -> None:
    """合法零码不能因字符串 truthiness 与 message 而误判失败。"""
    payload = {"panel_id": "panel-fixture", "code": code, "err_code": "0", "message": "ok"}
    patch_send_handler.post_result = payload
    result = await QQBotMenuPanelService(
        make_plugin(enable_menu_panel_service=True)
    ).create_panel("group", "all", PANEL)
    assert result == {"success": True, "data": payload, "error": None}


def test_summary_saturates_untrusted_lengths_without_mutating_input() -> None:
    """摘要工作量有界，非字符串字段不抛异常且不会改变原始负载。"""
    from ..src.panel_diagnostics import panel_create_summary

    item = {"name": "中" * 1000, "desc": "中" * 1000, "type": "command"}
    body = {"scope": "group", "target_type": "specific",
            "panel": {"items": [item] * 100, "remark": "中" * 1000},
            "group_openids": ["secret-id"] * 100}
    summary = panel_create_summary(body)
    for field in ["item_count=21", "command_count=20", "name_units_max=15",
                  "desc_units_max=31", "remark_chars=256", "group_count=21"]:
        assert field in summary
    assert "secret-id" not in summary and "中" not in summary
    assert len(body["panel"]["items"]) == 100 and len(item["name"]) == 1000
    body["panel"]["items"] = [{"name": [], "desc": None, "type": {}}]
    assert "name_units_max=0" in panel_create_summary(body)


@pytest.mark.parametrize("code", [True, -1, 1.5, "1" * 11, {"secret": "value"}, 10**50])
def test_malformed_codes_have_no_numeric_business_failure(code: Any) -> None:
    """不以畸形非数值字段推测平台业务失败，不输出任意字符串。"""
    from ..src.panel_diagnostics import panel_create_failure, panel_has_business_error

    assert panel_has_business_error({"err_code": code}) is False
    assert panel_has_business_error([]) is False
    error, details = panel_create_failure(400, {"err_code": code})
    assert "原因未确认" in error and "err_code=" not in details
