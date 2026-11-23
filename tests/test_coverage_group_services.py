"""群管理、成员与群信息 Service 的拒绝路径和协议边界回归。"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from ..services.group_admin_service import QQBotGroupAdminService
from .conftest import FakeHttpClient, FakeResponse, FakeSendHandler, make_plugin


@pytest.mark.parametrize(
    ("method", "maximum"),
    [("list_join_approval_strategies", 50), ("list_join_requests", 50)],
)
@pytest.mark.parametrize("invalid", [0, -1, True, "20", None, "overflow"])
async def test_admin_pagination_rejects_invalid_limit(
    method: str,
    maximum: int,
    invalid: Any,
    patch_send_handler: FakeSendHandler,
) -> None:
    """审批策略和入群申请均执行 50/51 以及类型边界。"""
    client = FakeHttpClient()
    service = QQBotGroupAdminService(make_plugin(http_client=client))
    kwargs: dict[str, Any] = {"limit": maximum + 1 if invalid == "overflow" else invalid}
    if method == "list_join_requests":
        kwargs["group_openid"] = "g1"

    result = await getattr(service, method)(**kwargs)

    assert result == {"success": False, "data": None, "error": f"limit 必须在 1~{maximum} 之间"}
    assert client.calls == []
    assert patch_send_handler.posts == []


@pytest.mark.parametrize(
    ("method", "path", "limit"),
    [
        ("list_join_approval_strategies", "/v2/groups/join_approval_strategy", 50),
        ("list_join_requests", "/v2/groups/g%2F1/join_request_list", 50),
    ],
)
async def test_admin_pagination_accepts_upper_bound(
    method: str,
    path: str,
    limit: int,
    patch_send_handler: FakeSendHandler,
) -> None:
    """有效上界只发一次 GET，并完整编码游标和群标识。"""
    payload = {"records": [], "next_cursor": "next"}
    client = FakeHttpClient([FakeResponse(200, payload)])
    service = QQBotGroupAdminService(make_plugin(http_client=client))
    kwargs: dict[str, Any] = {"cursor": "a&b", "limit": limit}
    if method == "list_join_requests":
        kwargs["group_openid"] = "g/1"

    result = await getattr(service, method)(**kwargs)

    assert result == {"success": True, "data": payload, "error": None}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["method"] == "GET"
    assert urlsplit(call["url"]).path == path
    assert parse_qs(urlsplit(call["url"]).query) == {"cursor": ["a&b"], "limit": [str(limit)]}
    assert patch_send_handler.posts == []
