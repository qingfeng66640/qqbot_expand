"""QQ 开放平台消息结构体构造与校验。

本模块只做**纯数据构造**：把调用方给的 Python 参数拼成 QQ API 要求的
dict 结构，并在拼装过程中完成字段合法性校验。所有函数都不发起网络请求，
因此可以被 Service / Tool / 测试自由复用。

校验失败一律抛 ``ValueError``，由上层 Service 捕获后转成
``{"success": False, "error": ...}`` 结构返回。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .constants import (
    ACTION_TYPE_COMMAND,
    ACTION_TYPES,
    BUTTON_STYLE_GREY,
    BUTTON_STYLES,
    COMMAND_TEXT_MAX_LENGTH,
    KEYBOARD_MAX_BUTTONS_PER_ROW,
    KEYBOARD_MAX_ROWS,
    PERMISSION_TYPE_EVERYONE,
    PERMISSION_TYPES,
)

__all__ = [
    "at_everyone",
    "at_user",
    "build_ark",
    "build_button",
    "build_embed",
    "build_keyboard",
    "build_markdown",
    "build_message_reference",
    "cmd_enter",
    "cmd_input",
    "emoji",
]


# ============ 按钮 / 键盘 ============


def build_button(
    label: str,
    *,
    button_id: str = "",
    visited_label: str = "",
    style: int = BUTTON_STYLE_GREY,
    action_type: int = ACTION_TYPE_COMMAND,
    permission_type: int = PERMISSION_TYPE_EVERYONE,
    data: str = "",
    specify_user_ids: list[str] | None = None,
    specify_role_ids: list[str] | None = None,
    reply: bool = False,
    enter: bool = False,
    anchor: int = 0,
    unsupport_tips: str = "当前客户端版本不支持该按钮",
) -> dict[str, Any]:
    """构造单个按钮。

    Args:
        label: 按钮上显示的文字。
        button_id: 按钮唯一标识，回调时原样带回；留空则由 QQ 侧自动生成。
        visited_label: 点击后按钮显示的文字，留空则复用 ``label``。
        style: 按钮样式，0 灰色线框 / 1 蓝色线框。
        action_type: 0 跳转链接 / 1 回调后台 / 2 拉起用户输入指令。
        permission_type: 0 指定用户 / 1 仅管理员 / 2 所有人 / 3 指定身份组。
        data: 操作数据。跳转按钮填 URL，回调按钮填回调数据，指令按钮填指令文本。
        specify_user_ids: ``permission_type=0`` 时允许点击的用户 openid 列表。
        specify_role_ids: ``permission_type=3`` 时允许点击的身份组 id 列表。
        reply: 指令按钮是否以回复形式发出。
        enter: 指令按钮是否自动发送（False 时只填充输入框）。
        anchor: 指令按钮的唤起方式，1 表示唤起选图器。
        unsupport_tips: 客户端不支持该按钮时的提示文案。

    Returns:
        QQ API 要求的按钮结构。

    Raises:
        ValueError: 任一字段不合法。
    """
    if not label:
        raise ValueError("按钮 label 不能为空")
    if style not in BUTTON_STYLES:
        raise ValueError(f"按钮 style 只能是 {sorted(BUTTON_STYLES)}，收到 {style}")
    if action_type not in ACTION_TYPES:
        raise ValueError(f"按钮 action.type 只能是 {sorted(ACTION_TYPES)}，收到 {action_type}")
    if permission_type not in PERMISSION_TYPES:
        raise ValueError(
            f"按钮 action.permission.type 只能是 {sorted(PERMISSION_TYPES)}，收到 {permission_type}"
        )
    if action_type == ACTION_TYPE_COMMAND and len(data) > COMMAND_TEXT_MAX_LENGTH:
        raise ValueError(f"指令按钮的 data 长度不能超过 {COMMAND_TEXT_MAX_LENGTH} 字符")

    permission: dict[str, Any] = {"type": permission_type}
    if permission_type == 0:
        if not specify_user_ids:
            raise ValueError("permission_type=0（指定用户）时必须提供 specify_user_ids")
        permission["specify_user_ids"] = list(specify_user_ids)
    if permission_type == 3:
        if not specify_role_ids:
            raise ValueError("permission_type=3（指定身份组）时必须提供 specify_role_ids")
        permission["specify_role_ids"] = list(specify_role_ids)

    action: dict[str, Any] = {
        "type": action_type,
        "permission": permission,
        "data": data,
        "reply": reply,
        "enter": enter,
        "unsupport_tips": unsupport_tips,
    }
    if anchor:
        action["anchor"] = anchor

    button: dict[str, Any] = {
        "render_data": {
            "label": label,
            "visited_label": visited_label or label,
            "style": style,
        },
        "action": action,
    }
    if button_id:
        button["id"] = button_id
    return button


def build_keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """把二维按钮列表包装成 keyboard 结构。

    Args:
        rows: 二维列表，外层是行，内层是该行的按钮（``build_button()`` 的返回值）。

    Returns:
        ``{"content": {"rows": [{"buttons": [...]}, ...]}}``。

    Raises:
        ValueError: 行数或每行按钮数超限，或存在空行。
    """
    if not rows:
        raise ValueError("keyboard 至少需要一行按钮")
    if len(rows) > KEYBOARD_MAX_ROWS:
        raise ValueError(f"keyboard 最多 {KEYBOARD_MAX_ROWS} 行，收到 {len(rows)} 行")

    built_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not row:
            raise ValueError(f"keyboard 第 {index + 1} 行没有按钮")
        if len(row) > KEYBOARD_MAX_BUTTONS_PER_ROW:
            raise ValueError(
                f"keyboard 每行最多 {KEYBOARD_MAX_BUTTONS_PER_ROW} 个按钮，"
                f"第 {index + 1} 行收到 {len(row)} 个"
            )
        built_rows.append({"buttons": list(row)})
    return {"content": {"rows": built_rows}}


# ============ ark / embed / markdown ============


def build_ark(template_id: int, kv: list[dict[str, Any]]) -> dict[str, Any]:
    """构造 ark 卡片结构。

    Args:
        template_id: QQ 侧预置的 ark 模板 id（如 23 列表模板、24 大图模板、37 大图模板）。
        kv: 模板参数列表，元素形如 ``{"key": "#DESC#", "value": "..."}``；
            列表型参数用 ``{"key": "#LIST#", "obj": [{"obj_kv": [...]}]}``。

    Returns:
        QQ API 要求的 ark 结构。

    Raises:
        ValueError: template_id 非正整数，或 kv 为空 / 元素缺少 key。
    """
    if not isinstance(template_id, int) or template_id <= 0:
        raise ValueError("ark template_id 必须是正整数")
    if not kv:
        raise ValueError("ark kv 不能为空")
    for item in kv:
        if not isinstance(item, dict) or not item.get("key"):
            raise ValueError("ark kv 的每一项都必须是含 key 字段的字典")
    return {"template_id": template_id, "kv": list(kv)}


def build_embed(
    title: str,
    *,
    prompt: str = "",
    thumbnail_url: str = "",
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """构造 embed 结构。

    Args:
        title: 标题。
        prompt: 消息列表中的外显提示文本，留空则复用 ``title``。
        thumbnail_url: 缩略图 URL。
        fields: 正文条目，每一项渲染成一行。

    Returns:
        QQ API 要求的 embed 结构。

    Raises:
        ValueError: title 为空。
    """
    if not title:
        raise ValueError("embed title 不能为空")
    embed: dict[str, Any] = {"title": title, "prompt": prompt or title}
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    if fields:
        embed["fields"] = [{"name": str(field)} for field in fields]
    return embed


def build_markdown(
    *,
    content: str = "",
    custom_template_id: str = "",
    params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造 markdown 结构。

    原生 markdown（``content``）与模板 markdown（``custom_template_id`` + ``params``）
    互斥，必须且只能提供其中一种。

    Args:
        content: 原生 markdown 文本。
        custom_template_id: 已在 QQ 后台报备的 markdown 模板 id。
        params: 模板参数，元素形如 ``{"key": "title", "values": ["..."]}``。

    Returns:
        QQ API 要求的 markdown 结构。

    Raises:
        ValueError: 两种模式同时提供或都未提供，或模板参数格式不对。
    """
    if content and custom_template_id:
        raise ValueError("content 与 custom_template_id 互斥，只能提供其中一个")
    if not content and not custom_template_id:
        raise ValueError("必须提供 content 或 custom_template_id 之一")

    if content:
        return {"content": content}

    markdown: dict[str, Any] = {"custom_template_id": custom_template_id}
    if params:
        for item in params:
            if not isinstance(item, dict) or not item.get("key"):
                raise ValueError("markdown params 的每一项都必须是含 key 字段的字典")
            if "values" not in item:
                raise ValueError("markdown params 的每一项都必须包含 values 字段")
        markdown["params"] = list(params)
    return markdown


def build_message_reference(
    message_id: str, *, ignore_get_message_error: bool = False
) -> dict[str, Any]:
    """构造引用回复结构。

    Args:
        message_id: 被引用消息的 id。
        ignore_get_message_error: 拉取被引用消息失败时是否忽略错误继续发送。

    Returns:
        QQ API 要求的 message_reference 结构。

    Raises:
        ValueError: message_id 为空。
    """
    if not message_id:
        raise ValueError("引用回复的 message_id 不能为空")
    return {
        "message_id": message_id,
        "ignore_get_message_error": ignore_get_message_error,
    }


# ============ 文本内嵌标签辅助 ============


def at_user(openid: str) -> str:
    """生成 @某人 的内嵌标签。

    可用于文本、图文、markdown 消息。旧协议 ``<@userid>`` 即将弃用，统一使用本格式。

    Args:
        openid: 目标用户的 openid。

    Returns:
        形如 ``<qqbot-at-user id="xxx" />`` 的标签串。

    Raises:
        ValueError: openid 为空。
    """
    if not openid:
        raise ValueError("at_user 的 openid 不能为空")
    return f'<qqbot-at-user id="{openid}" />'


def at_everyone() -> str:
    """生成 @全体成员 的内嵌标签。

    **仅文字子频道可用**，群聊与单聊不支持；且需要机器人拥有 @全体成员 权限。

    Returns:
        ``<qqbot-at-everyone />``。
    """
    return "<qqbot-at-everyone />"


def emoji(emoji_id: int) -> str:
    """生成系统表情的内嵌标签。

    **仅频道可用**，且只支持 ``type=1`` 的系统表情；``type=2`` 的 emoji
    直接按字符串写进文本即可，无需本函数。

    Args:
        emoji_id: QQ 系统表情 id。

    Returns:
        形如 ``<emoji:4>`` 的标签串。

    Raises:
        ValueError: emoji_id 为负数。
    """
    if emoji_id < 0:
        raise ValueError("emoji id 不能为负数")
    return f"<emoji:{emoji_id}>"


def cmd_enter(text: str) -> str:
    """生成"点击后直接发送"的指令内嵌标签。

    仅在 markdown 消息中生效，且**仅单聊可用**（群聊与文字子频道不支持）。

    Args:
        text: 指令文本，长度不超过 100 字符。

    Returns:
        形如 ``<qqbot-cmd-enter text="..." />`` 的标签串。

    Raises:
        ValueError: 文本为空或超长。
    """
    _validate_command_text(text)
    return f'<qqbot-cmd-enter text="{quote(text, safe="")}" />'


def cmd_input(text: str, *, show: str = "", reference: bool = False) -> str:
    """生成"点击后填充输入框"的指令内嵌标签。

    仅在 markdown 消息中生效。用户点击后文本进入输入框，由用户自行编辑发送。

    Args:
        text: 插入输入框的指令文本，长度不超过 100 字符。
        show: 展示给用户看的文字，留空则复用 ``text``，长度同样不超过 100 字符。
        reference: 插入输入框时是否带上对本条消息的引用回复。

    Returns:
        形如 ``<qqbot-cmd-input text="..." show="..." reference="false" />`` 的标签串。

    Raises:
        ValueError: 文本为空或超过 100 字符。
    """
    _validate_command_text(text)
    if show:
        _validate_command_text(show)
    return (
        f'<qqbot-cmd-input text="{quote(text, safe="")}" '
        f'show="{quote(show or text, safe="")}" '
        f'reference="{str(reference).lower()}" />'
    )


def _validate_command_text(text: str) -> None:
    """校验指令标签的文本长度。

    Args:
        text: 待校验文本。

    Raises:
        ValueError: 文本为空或超过 100 字符。
    """
    if not text:
        raise ValueError("指令文本不能为空")
    if len(text) > COMMAND_TEXT_MAX_LENGTH:
        raise ValueError(f"指令文本长度不能超过 {COMMAND_TEXT_MAX_LENGTH} 字符")
