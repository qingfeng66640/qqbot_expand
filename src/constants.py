"""QQ 开放平台常量与枚举。

集中定义消息类型、按钮动作类型、权限类型、互动应答码等，
避免在各 Service / Tool 中散落魔法数字。
"""
from __future__ import annotations

# ============ REST API 基础 URL ============

API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"
API_BASE_PRODUCTION = "https://api.sgroup.qq.com"

# ============ 消息类型（msg_type） ============

MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2
MSG_TYPE_ARK = 3
MSG_TYPE_EMBED = 4
MSG_TYPE_MEDIA = 7

# msg_seq 取值上限（QQ 侧要求同一 msg_id 下的回复序号唯一）
MSG_SEQ_MAX = 65536

# ============ 发送目标 ============

TARGET_TYPE_USER = "user"
TARGET_TYPE_GROUP = "group"
TARGET_TYPES: frozenset[str] = frozenset({TARGET_TYPE_USER, TARGET_TYPE_GROUP})

# 消息发送路径模板
PATH_USER_MESSAGES = "/v2/users/{openid}/messages"
PATH_GROUP_MESSAGES = "/v2/groups/{openid}/messages"

# 互动回调应答路径
PATH_INTERACTION_ACK = "/interactions/{interaction_id}"

# ============ 按钮（keyboard） ============

# 每个 keyboard 最多 5 行，每行最多 5 个按钮
KEYBOARD_MAX_ROWS = 5
KEYBOARD_MAX_BUTTONS_PER_ROW = 5

# 按钮样式：0 灰色线框，1 蓝色线框
BUTTON_STYLE_GREY = 0
BUTTON_STYLE_BLUE = 1
BUTTON_STYLES: frozenset[int] = frozenset({BUTTON_STYLE_GREY, BUTTON_STYLE_BLUE})

# 按钮动作类型：0 http/小程序跳转，1 回调后台接口，2 拉起用户输入指令
ACTION_TYPE_LINK = 0
ACTION_TYPE_CALLBACK = 1
ACTION_TYPE_COMMAND = 2
ACTION_TYPES: frozenset[int] = frozenset(
    {ACTION_TYPE_LINK, ACTION_TYPE_CALLBACK, ACTION_TYPE_COMMAND}
)

# 按钮权限类型：0 指定用户，1 仅管理员，2 所有人，3 指定身份组
PERMISSION_TYPE_SPECIFY_USER = 0
PERMISSION_TYPE_ADMIN = 1
PERMISSION_TYPE_EVERYONE = 2
PERMISSION_TYPE_SPECIFY_ROLE = 3
PERMISSION_TYPES: frozenset[int] = frozenset(
    {
        PERMISSION_TYPE_SPECIFY_USER,
        PERMISSION_TYPE_ADMIN,
        PERMISSION_TYPE_EVERYONE,
        PERMISSION_TYPE_SPECIFY_ROLE,
    }
)

# ============ ark 预置模板 ============

# QQ 默认开放、无需申请的三个 ark 模板
ARK_TEMPLATE_LIST = 23  # 链接 + 文本列表
ARK_TEMPLATE_THUMBNAIL = 24  # 文本 + 缩略图
ARK_TEMPLATE_BIG_IMAGE = 37  # 大图（封面尺寸 975*540）
ARK_BUILTIN_TEMPLATES: frozenset[int] = frozenset(
    {ARK_TEMPLATE_LIST, ARK_TEMPLATE_THUMBNAIL, ARK_TEMPLATE_BIG_IMAGE}
)

# ============ 互动回调应答码 ============

# 只有 type=11（消息按钮）与 type=12（单聊快捷菜单）需要调用应答接口
INTERACTION_TYPE_BUTTON = 11
INTERACTION_TYPE_MENU = 12
INTERACTION_ACK_REQUIRED_TYPES: frozenset[int] = frozenset(
    {INTERACTION_TYPE_BUTTON, INTERACTION_TYPE_MENU}
)

# INTERACTION_CREATE 事件所需的 intents 位（1<<26）
INTENT_INTERACTION = 1 << 26

# 0 操作成功 / 1 操作失败 / 2 操作频繁 / 3 重复操作 / 4 没有权限 / 5 仅管理员操作
INTERACTION_CODES: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5})
INTERACTION_CODE_DESCRIPTIONS: dict[int, str] = {
    0: "操作成功",
    1: "操作失败",
    2: "操作频繁",
    3: "重复操作",
    4: "没有权限",
    5: "仅管理员操作",
}

# ============ raw 通道 ============

RAW_SUPPORTED_METHODS: frozenset[str] = frozenset({"GET", "POST", "PUT", "DELETE"})

# 指令按钮拉起的输入内容长度上限
COMMAND_TEXT_MAX_LENGTH = 100
