"""QQ 图文 card 的纯数据构造与校验，不执行 DNS 解析或网络访问。"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

__all__ = ["build_tuwen_card"]

_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_INVALID_URL_CHAR = re.compile(r'[\\<>"{}|^`]|%(?![0-9A-Fa-f]{2})')


def _validate_text(value: str, field: str, maximum: int) -> None:
    """按 Unicode 码点计数，拒绝空白文本与超长输入，不截断或改写。"""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} 必须是 1~{maximum} 字符的非空文本")


def _validate_https_url(value: str, field: str) -> None:
    """校验有界 HTTPS URL，拒绝凭据、控制字符及畸形主机或端口。"""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError(f"{field} 必须是长度不超过 1024 字符的 HTTPS URL")
    if (
        _INVALID_URL_CHAR.search(value)
        or any(char.isspace() for char in value)
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in unquote(value))
    ):
        raise ValueError(f"{field} 不能包含空白、控制字符或非法 URL 字符")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError(f"{field} URL 格式不合法") from None
    if parsed.scheme != "https" or not host:
        raise ValueError(f"{field} 必须是包含主机名的 HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} 不能包含用户名或密码")
    if parsed.netloc.endswith(":") or (port is not None and port < 1):
        raise ValueError(f"{field} URL 端口不合法")
    if parsed.netloc.startswith("["):
        # urlsplit 已校验 IPv6；另拒绝括号后的垃圾字符和 zone ID。
        if not re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::[0-9]+)?", parsed.netloc):
            raise ValueError(f"{field} URL 主机名不合法")
    else:
        try:
            ascii_host = host.encode("idna").decode("ascii").removesuffix(".")
        except UnicodeError:
            raise ValueError(f"{field} URL 主机名不合法") from None
        if len(ascii_host) > 253 or any(
            not _HOST_LABEL.fullmatch(label) for label in ascii_host.split(".")
        ):
            raise ValueError(f"{field} URL 主机名不合法")
        if ascii_host.replace(".", "").isdigit():
            try:
                ipaddress.IPv4Address(ascii_host)
            except ValueError:
                raise ValueError(f"{field} URL IP 地址不合法") from None


def build_tuwen_card(
    title: str, description: str, url: str, pic_url: str
) -> dict[str, Any]:
    """构造 ``msg_type=8`` 的 ``card`` 对象，不包含消息外层字段。

    Args:
        title: 标题，1~50 个 Unicode 码点；不采用菜单中文计二规则。
        description: 描述，1~200 个 Unicode 码点。
        url: 跳转 HTTPS URL，最多 1024 字符。
        pic_url: 图片 HTTPS URL，最多 1024 字符。

    Returns:
        只含 ``type=tuwen`` 和四个 ``content`` 字段的白名单结构；值原样保留。

    Raises:
        ValueError: 文本或 URL 不合法。错误不回显调用方输入。
    """
    _validate_text(title, "title", 50)
    _validate_text(description, "description", 200)
    _validate_https_url(url, "url")
    _validate_https_url(pic_url, "pic_url")
    return {
        "type": "tuwen",
        "content": {
            "title": title,
            "description": description,
            "url": url,
            "pic_url": pic_url,
        },
    }
