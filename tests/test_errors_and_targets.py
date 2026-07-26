"""``src/errors.py`` 与 ``src/targets.py`` 的测试。

错误脱敏的核心断言是"绝不透传原文"——这些返回值会流向 LLM 与用户侧。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ..src.errors import (
    ERROR_BAD_REQUEST,
    ERROR_FORBIDDEN,
    ERROR_GENERIC,
    ERROR_NETWORK,
    ERROR_NOT_FOUND,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_TOKEN,
    sanitize_error,
    sanitize_http_status,
)
from ..src.targets import resolve_target


class TestSanitizeError:
    """异常归类。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Read timed out", ERROR_TIMEOUT),
            ("ConnectTimeout", ERROR_TIMEOUT),
            ("401 Unauthorized", ERROR_TOKEN),
            ("invalid access_token", ERROR_TOKEN),
            ("429 Too Many Requests", ERROR_RATE_LIMIT),
            ("调用频率超限", ERROR_RATE_LIMIT),
            ("403 Forbidden", ERROR_FORBIDDEN),
            ("404 Not Found", ERROR_NOT_FOUND),
            ("400 Bad Request", ERROR_BAD_REQUEST),
            ("502 Bad Gateway", ERROR_SERVER),
            ("Connection refused", ERROR_NETWORK),
            ("SSL handshake failed", ERROR_NETWORK),
            ("完全无法识别的错误", ERROR_GENERIC),
        ],
    )
    def test_classification(self, raw: str, expected: str) -> None:
        """按关键字归类到白名单文案。"""
        assert sanitize_error(raw) == expected

    def test_accepts_exception_object(self) -> None:
        """异常对象与字符串走同一条路径。"""
        assert sanitize_error(TimeoutError("timed out")) == ERROR_TIMEOUT

    def test_never_leaks_secrets(self) -> None:
        """原始文本中的 token / URL 一律不得出现在返回值里。"""
        raw = "POST https://api.sgroup.qq.com/v2/users/x/messages failed: QQBot s3cr3t-token"
        sanitized = sanitize_error(raw)
        assert "s3cr3t-token" not in sanitized
        assert "api.sgroup.qq.com" not in sanitized

    def test_result_is_always_whitelisted(self) -> None:
        """任意输入的输出都必须落在白名单集合内。"""
        whitelist = {
            ERROR_TIMEOUT,
            ERROR_TOKEN,
            ERROR_RATE_LIMIT,
            ERROR_FORBIDDEN,
            ERROR_NOT_FOUND,
            ERROR_BAD_REQUEST,
            ERROR_SERVER,
            ERROR_NETWORK,
            ERROR_GENERIC,
        }
        for raw in ["", "???", "appid=12345 secret=abcdef", "\x00\x01"]:
            assert sanitize_error(raw) in whitelist


class TestSanitizeHttpStatus:
    """HTTP 状态码归类。"""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, ERROR_TOKEN),
            (403, ERROR_FORBIDDEN),
            (404, ERROR_NOT_FOUND),
            (429, ERROR_RATE_LIMIT),
            (400, ERROR_BAD_REQUEST),
            (422, ERROR_BAD_REQUEST),
            (500, ERROR_SERVER),
            (503, ERROR_SERVER),
            (200, ERROR_GENERIC),
        ],
    )
    def test_classification(self, status: int, expected: str) -> None:
        """状态码映射到白名单文案。"""
        assert sanitize_http_status(status) == expected


def _message(**kwargs: object) -> SimpleNamespace:
    """构造一个最小 Message 替身。

    Args:
        **kwargs: 覆盖默认字段。

    Returns:
        带 Message 关键字段的替身对象。
    """
    base = {
        "message_id": "m1",
        "chat_type": "private",
        "sender_id": "user-openid",
        "extra": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestResolveTarget:
    """从触发消息推导发送目标。"""

    def test_private_uses_sender_id(self) -> None:
        """单聊目标即发送者。"""
        target = resolve_target(_message())
        assert target == ("user", "user-openid", "m1")

    def test_group_uses_extra_group_id(self) -> None:
        """群聊目标取 extra['group_id']（适配器 from_group 写入）。"""
        message = _message(chat_type="group", extra={"group_id": "group-openid"})
        target = resolve_target(message)
        assert target == ("group", "group-openid", "m1")

    def test_group_without_group_id_returns_none(self) -> None:
        """群聊缺 group_id 时无法推导。"""
        assert resolve_target(_message(chat_type="group")) is None

    def test_private_without_sender_returns_none(self) -> None:
        """单聊缺发送者时无法推导。"""
        assert resolve_target(_message(sender_id="")) is None

    def test_none_message(self) -> None:
        """没有触发消息时返回 None。"""
        assert resolve_target(None) is None

    def test_missing_message_id_is_tolerated(self) -> None:
        """缺 message_id 只是拿不到被动回复凭据，不影响目标推导。"""
        target = resolve_target(_message(message_id=""))
        assert target is not None
        assert target.msg_id == ""

    def test_strips_whitespace(self) -> None:
        """openid 前后空白应被清理。"""
        target = resolve_target(_message(sender_id="  u1  ", message_id=" m1 "))
        assert target == ("user", "u1", "m1")
