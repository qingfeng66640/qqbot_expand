"""面向 LLM Tool 的结构化输入类型。"""
from __future__ import annotations

from typing import Annotated, Literal, TypedDict

ArkStyle = Literal["card", "list"]
GroupReviewOp = Literal["approve", "decline"]
MenuItemType = Literal["switch", "send_message", "link", "menu"]
MuteOp = Literal["add", "update", "del"]
PanelItemType = Literal["command", "link"]
PanelScope = Literal["c2c", "group", "channel", "dm"]
PanelTargetOp = Literal["add", "del"]
SubMenuItemType = Literal["send_message", "link"]


class MenuSwitchInput(TypedDict):
    """开关菜单项参数。"""

    switch_id: Annotated[str, "开关唯一标识，最长 64 字符"]
    default: Annotated[bool, "开关默认状态"]


class SubMenuItemRequired(TypedDict):
    """二级菜单项必填字段。"""

    name: Annotated[str, "二级菜单显示名称，最长 14 字符"]
    type: Annotated[SubMenuItemType, "二级菜单类型，只能是 send_message 或 link"]


class SubMenuItemInput(SubMenuItemRequired, total=False):
    """二级菜单项输入。"""

    send_message: Annotated[str, "type=send_message 时必填，点击后发送的消息"]
    link: Annotated[str, "type=link 时必填，必须是 HTTPS URL"]


class MenuItemRequired(TypedDict):
    """一级菜单项必填字段。"""

    name: Annotated[str, "一级菜单显示名称，最长 10 字符"]
    type: Annotated[MenuItemType, "菜单类型"]


class MenuItemInput(MenuItemRequired, total=False):
    """一级菜单项输入。"""

    switch: Annotated[MenuSwitchInput, "type=switch 时必填"]
    send_message: Annotated[str, "type=send_message 时必填，点击后发送的消息"]
    link: Annotated[str, "type=link 时必填，必须是 HTTPS URL"]
    sub_menu_items: Annotated[
        list[SubMenuItemInput], "type=menu 时必填，包含 1~5 个二级菜单项"
    ]


class PanelItemRequired(TypedDict):
    """指令面板项必填字段。"""

    name: Annotated[str, "显示名称；command 类型填写指令名，最长 14 字符"]
    type: Annotated[PanelItemType, "项目类型，只能是 command 或 link"]


class PanelItemInput(PanelItemRequired, total=False):
    """指令面板项输入。"""

    desc: Annotated[str, "可选项目描述，最长 30 字符，默认空字符串"]
    only_admin: Annotated[bool, "是否仅管理员可见或可用，默认 false"]
    link: Annotated[str, "type=link 时必填，必须是 HTTPS URL"]


class PanelRequired(TypedDict):
    """指令面板必填字段。"""

    items: Annotated[
        list[PanelItemInput],
        "面板项目，1~20 项；使用 name/desc/type/only_admin/link，不使用 label/command/url",
    ]


class PanelInput(PanelRequired, total=False):
    """指令面板输入。"""

    remark: Annotated[str, "面板备注，最长 255 字符"]


class KeyboardButtonRequired(TypedDict):
    """按钮必填字段。"""

    label: Annotated[str, "按钮显示文字"]


class KeyboardButtonInput(KeyboardButtonRequired, total=False):
    """按钮输入。"""

    command: Annotated[str, "指令按钮内容；与 url 必须且只能提供一个"]
    url: Annotated[str, "链接按钮 URL；与 command 必须且只能提供一个"]


class ArkListItemRequired(TypedDict):
    """Ark 列表项必填字段。"""

    text: Annotated[str, "条目显示文本"]


class ArkListItemInput(ArkListItemRequired, total=False):
    """Ark 列表项输入。"""

    url: Annotated[str, "可选跳转 URL，域名需在 QQ 开放平台报备"]


class MuteMemberRequired(TypedDict):
    """群成员禁言操作必填字段。"""

    op: Annotated[MuteOp, "禁言动作：add、update 或 del"]
    member_openid: Annotated[str, "目标群成员的 member_openid"]


class MuteMemberInput(MuteMemberRequired, total=False):
    """群成员禁言操作输入。"""

    mute_expire_at: Annotated[str, "add/update 时可填的 RFC3339 解禁时间"]
