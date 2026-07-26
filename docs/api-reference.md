# qqbot_expand API 参考

按签名逐项列出对外接口。上手教程见 [使用教程](usage-guide.md)，
平台规则见 [QQ 平台能力与限制对照](qq-platform-notes.md)。

约定：

- 所有 Service 通过 `service_api.get_service("qqbot_expand:service:<name>")` 获取
- 所有异步方法**不抛异常**，错误经白名单脱敏后放进返回值的 `error` 字段
- `src/` 下的纯函数**会抛 `ValueError`**，由调用方或 Service 捕获

---

## Service: `qqbot_expand:service:qqbot_message`

发送 `qqbot_adapter` 未覆盖的消息类型。

### 公共参数

以下参数在多数方法中出现，语义一致：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `target_type` | `str` | 必填 | `"user"`（C2C 单聊）或 `"group"`（群聊） |
| `target_id` | `str` | 必填 | 对应的 openid |
| `msg_id` | `str` | `""` | 前置用户消息 ID。**填了即被动回复** |
| `event_id` | `str` | `""` | 前置事件 ID，同样触发被动回复 |
| `msg_seq` | `int \| None` | `None` | 回复序号，取值 1~65536。带 `msg_id` 时缺省为 1 |

### 公共返回

```python
{"success": bool, "message_id": str, "error": str | None}
```

成功时 `message_id` 为 QQ 返回的消息 ID，`error` 为 `None`；失败时 `message_id` 为 `""`。

---

### `send_keyboard`

```python
async def send_keyboard(
    target_type: str,
    target_id: str,
    rows: list[list[dict[str, Any]]],
    content: str = "",
    *,
    custom_template_id: str = "",
    params: list[dict[str, Any]] | None = None,
    msg_id: str = "",
    event_id: str = "",
    msg_seq: int | None = None,
) -> dict[str, Any]
```

发送带按钮菜单的消息（`msg_type=2`）。

| 参数 | 说明 |
|---|---|
| `rows` | 二维按钮列表，用 `build_button()` 构造。最多 5 行 × 5 列 |
| `content` | 原生 Markdown 正文 |
| `custom_template_id` | 已报备的 Markdown 模板 ID |
| `params` | 模板参数，`[{"key": "title", "values": ["..."]}]` |

**`content` 与 `custom_template_id` 必须提供且只能提供其一** —— QQ 不接受无正文的
纯按钮消息，两者同时给会抛 `ValueError`（已被捕获，转为 `error` 返回）。

---

### `send_ark`

```python
async def send_ark(
    target_type: str,
    target_id: str,
    template_id: int,
    kv: list[dict[str, Any]],
    *,
    msg_id: str = "",
    event_id: str = "",
    msg_seq: int | None = None,
) -> dict[str, Any]
```

发送 ark 卡片（`msg_type=3`）。

| 参数 | 说明 |
|---|---|
| `template_id` | 模板 ID。默认开放：23 链接+列表、24 文本+缩略图、37 大图 |
| `kv` | 模板变量，`[{"key": "#DESC#", "value": "..."}]`；数组型用 `{"key": "#LIST#", "obj": [...]}` |

> 主动 ark 默认可用；**被动 ark**（带 `msg_id`/`event_id`）需向平台运营申请权限。

---

### `send_reply`

```python
async def send_reply(
    target_type: str,
    target_id: str,
    content: str,
    reference_message_id: str,
    *,
    ignore_get_message_error: bool = False,
    msg_id: str = "",
    event_id: str = "",
    msg_seq: int | None = None,
) -> dict[str, Any]
```

发送带引用的文本消息（`msg_type=0` + `message_reference`）。

| 参数 | 说明 |
|---|---|
| `content` | 文本内容，不能为空 |
| `reference_message_id` | 被引用消息的 ID |
| `ignore_get_message_error` | 原消息拉取失败时是否继续发送。**建议置 `True`** |

---

### `send_markdown_template`

```python
async def send_markdown_template(
    target_type: str,
    target_id: str,
    custom_template_id: str,
    params: list[dict[str, Any]] | None = None,
    *,
    rows: list[list[dict[str, Any]]] | None = None,
    msg_id: str = "",
    event_id: str = "",
    msg_seq: int | None = None,
) -> dict[str, Any]
```

发送已报备的模板 Markdown（`msg_type=2`），可选附加按钮。与 `qqbot_adapter` 的原生
Markdown 不同，模板方式可绕开原生 Markdown 的内容白名单限制。

---

### `send_embed`

```python
async def send_embed(
    target_type: str,
    target_id: str,
    title: str,
    *,
    prompt: str = "",
    thumbnail_url: str = "",
    fields: list[str] | None = None,
    msg_id: str = "",
    event_id: str = "",
    msg_seq: int | None = None,
) -> dict[str, Any]
```

> **⚠️ QQ 单聊与群聊均不支持 embed**，仅文字子频道与频道私信可用。本插件只覆盖单聊/群聊
> 接口，因此该方法**实际调用必定失败**，保留仅为未来平台开放时的兼容性。

| 参数 | 说明 |
|---|---|
| `title` | 标题，不能为空 |
| `prompt` | 消息列表外显文字，留空复用 `title` |
| `thumbnail_url` | 缩略图 URL |
| `fields` | 正文条目，每项渲染一行 |

---

### `send_raw_message`

```python
async def send_raw_message(
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]
```

直接投递完整消息体，用于本 Service 未包装的形态。`payload` 必须是非空字典且含
`msg_type` 字段，其余结构由调用方自行保证。

---

## Service: `qqbot_expand:service:qqbot_raw`

统一的 QQ 开放 API 调用出口。**不注册为 Tool**，不暴露给 LLM。

### `request`

```python
async def request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    *,
    force_production: bool = False,
) -> dict[str, Any]
```

| 参数 | 说明 |
|---|---|
| `method` | `GET` / `POST` / `PUT` / `DELETE`，大小写不敏感 |
| `path` | **必须以 `/` 开头的相对路径**，如 `/users/@me` |
| `body` | 请求体，用于 POST / PUT |
| `query` | 查询参数，用于 GET / DELETE |
| `force_production` | 强制正式域名。沙箱不支持的接口（如互动应答）需置 `True` |

返回 `{"success": bool, "data": dict | None, "error": str | None}`。

**路径安全校验**：拒绝空串、不以 `/` 开头、含 `//`、含 `://`、含 `..` 的路径，防止
请求被打到 QQ 域名之外的主机。

**双重开关**：受 `features.allow_raw_request` 总开关与 `features.raw_allowed_methods`
方法白名单共同管控，任一不通过直接返回失败，不发起请求。

### `get_status`

```python
async def get_status() -> dict[str, Any]
```

转发 `qqbot_adapter:service:qqbot` 的状态，并附加本插件自身的就绪信息：

| 字段 | 来源 | 说明 |
|---|---|---|
| `http_client_ready` | 本插件 | httpx 客户端是否已创建 |
| `raw_enabled` | 本插件 | raw 通道是否启用 |
| `connected` | 适配器 | **WebSocket 是否已连接** |
| `bot_id` / `bot_name` | 适配器 | 机器人身份 |
| `platform` / `env` | 适配器 | 平台标识与环境（sandbox/production） |

适配器不可用时返回 `{"connected": False, "error": "qqbot_adapter 服务不可用", ...}`，
不抛异常。

---

## Service: `qqbot_expand:service:qqbot_interaction`

互动回调应答。**使用前务必阅读 [使用教程第 6 节](usage-guide.md#6-按钮回调读之前先看这里)** ——
当前版本存在链路限制。

### `ack`

```python
async def ack(interaction_id: str, code: int = 0) -> dict[str, Any]
```

应答一次互动回调（`PUT /interactions/{id}`，限频 50 QPS，强制走正式域名）。

| `code` | 客户端提示 |
|---|---|
| 0 | 操作成功 |
| 1 | 操作失败 |
| 2 | 操作频繁 |
| 3 | 重复操作 |
| 4 | 没有权限 |
| 5 | 仅管理员操作 |

返回 `{"success": bool, "code": int, "description": str, "error": str | None}`。

> 同一 `interaction_id` **只能应答一次**且超时后失效。适配器会自动应答 `code=0`，
> 你的自定义 code 会与之竞争。

### `needs_ack`

```python
@staticmethod
def needs_ack(interaction_type: int) -> bool
```

判断某互动类型是否需要应答。官方只要求 `type=11`（消息按钮）与 `type=12`
（单聊快捷菜单）应答，其余类型（消息反馈、清空会话、授权变更等）无需应答。

### `describe_code`

```python
@staticmethod
def describe_code(code: int) -> str
```

查询应答码对应的客户端提示文案，未知 code 返回空串。

---

## 构造函数：`src/builders.py`

**纯函数，不发请求，校验失败抛 `ValueError`。** 可被 Service、Tool、测试自由复用。

### `build_button`

```python
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
) -> dict[str, Any]
```

| 参数 | 说明 |
|---|---|
| `label` | 按钮文字，不能为空 |
| `visited_label` | 点击后文字，留空复用 `label` |
| `style` | `0` 灰色线框 / `1` 蓝色线框 |
| `action_type` | `0` 跳转 / `1` 回调 / `2` 指令 |
| `permission_type` | `0` 指定用户 / `1` 仅管理员 / `2` 所有人 / `3` 指定身份组 |
| `data` | 跳转填 URL，回调填回调数据，指令填指令文本（≤ 100 字符） |
| `reply` | 指令按钮是否带引用回复 |
| `enter` | 指令按钮点击后是否自动发送。**仅单聊生效** |
| `anchor` | 设为 1 唤起选图器，设置后忽略 `enter` |

校验规则：`permission_type=0` 必须给 `specify_user_ids`；`permission_type=3` 必须给
`specify_role_ids`；指令按钮 `data` 超 100 字符报错。已弃用的 `click_limit` 与
`at_bot_show_channel_list` 不生成。

### `build_keyboard`

```python
def build_keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]
```

包装成 `{"content": {"rows": [{"buttons": [...]}]}}`。校验最多 5 行、每行最多 5 个、
不允许空行。

### `build_ark`

```python
def build_ark(template_id: int, kv: list[dict[str, Any]]) -> dict[str, Any]
```

校验 `template_id` 为正整数、`kv` 非空且每项含 `key`。

### `build_embed`

```python
def build_embed(
    title: str,
    *,
    prompt: str = "",
    thumbnail_url: str = "",
    fields: list[str] | None = None,
) -> dict[str, Any]
```

### `build_markdown`

```python
def build_markdown(
    *,
    content: str = "",
    custom_template_id: str = "",
    params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]
```

原生模式（`content`）与模板模式（`custom_template_id` + `params`）**互斥**，
必须且只能提供其一。模板参数每项须含 `key` 与 `values`。

### `build_message_reference`

```python
def build_message_reference(
    message_id: str, *, ignore_get_message_error: bool = False
) -> dict[str, Any]
```

### 文本内嵌标签

```python
def at_user(openid: str) -> str                    # <qqbot-at-user id="..." />
def at_everyone() -> str                           # <qqbot-at-everyone />
def emoji(emoji_id: int) -> str                    # <emoji:4>
def cmd_enter(text: str) -> str                    # <qqbot-cmd-enter text="..." />
def cmd_input(text: str, *, show: str = "", reference: bool = False) -> str
```

`cmd_enter` / `cmd_input` 内部完成 urlencode，传原文即可；`text` 与 `show` 均限 100 字符。

作用域限制（超范围使用不报错，但客户端不渲染）：

| 函数 | 单聊 | 群聊 | 子频道 | 载体要求 |
|---|---|---|---|---|
| `at_user` | — | ✅ | ✅ | 含文本的消息 |
| `at_everyone` | ❌ | ❌ | ✅ | 需 @全体成员 权限 |
| `emoji` | ❌ | ❌ | ✅ | 仅 `type=1` 系统表情 |
| `cmd_enter` | ✅ | ❌ | ❌ | **仅 Markdown** |
| `cmd_input` | ✅ | ✅ | ✅ | **仅 Markdown** |

---

## 常量：`src/constants.py`

```python
# 消息类型
MSG_TYPE_TEXT = 0;  MSG_TYPE_MARKDOWN = 2;  MSG_TYPE_ARK = 3
MSG_TYPE_EMBED = 4; MSG_TYPE_MEDIA = 7
MSG_SEQ_MAX = 65536

# 发送目标
TARGET_TYPE_USER = "user";  TARGET_TYPE_GROUP = "group"

# 按钮
KEYBOARD_MAX_ROWS = 5;  KEYBOARD_MAX_BUTTONS_PER_ROW = 5
BUTTON_STYLE_GREY = 0;  BUTTON_STYLE_BLUE = 1
ACTION_TYPE_LINK = 0;   ACTION_TYPE_CALLBACK = 1;  ACTION_TYPE_COMMAND = 2
PERMISSION_TYPE_SPECIFY_USER = 0;  PERMISSION_TYPE_ADMIN = 1
PERMISSION_TYPE_EVERYONE = 2;      PERMISSION_TYPE_SPECIFY_ROLE = 3
COMMAND_TEXT_MAX_LENGTH = 100

# ark 预置模板
ARK_TEMPLATE_LIST = 23;  ARK_TEMPLATE_THUMBNAIL = 24;  ARK_TEMPLATE_BIG_IMAGE = 37

# 互动回调
INTERACTION_TYPE_BUTTON = 11;  INTERACTION_TYPE_MENU = 12
INTENT_INTERACTION = 1 << 26
INTERACTION_CODES = frozenset({0, 1, 2, 3, 4, 5})

# raw 通道
RAW_SUPPORTED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})
```

---

## Tool（面向 LLM）

三个 Tool 均限定 `associated_platforms = ["qq"]`，发送目标由 `resolve_target()`
从 `trigger_message` 自动推导。返回 `(bool, str | dict)`。

### `qq_send_keyboard`

| 参数 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 按钮上方的 Markdown 正文 |
| `buttons` | `list[dict]` | 最多 25 个。`{"label": "...", "command": "..."}` 或 `{"label": "...", "url": "..."}` |
| `per_row` | `int` | 每行按钮数，1~5，默认 2 |

`command` 与 `url` 必须二选一。群聊场景自动禁用 `enter`（QQ 侧仅单聊生效）。

### `qq_send_ark`

| 参数 | 类型 | 说明 |
|---|---|---|
| `style` | `str` | `"list"`（模板 23）或 `"card"`（模板 24） |
| `title` | `str` | 卡片标题 |
| `items` | `list[dict]` | `style="list"` 时的条目，最多 10 条，`{"text": "...", "url": "..."}` |
| `description` | `str` | `style="card"` 时的详情描述 |
| `image_url` | `str` | `style="card"` 时的缩略图，必填 |
| `link_url` | `str` | `style="card"` 时的跳转链接 |

### `qq_send_reply`

| 参数 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 回复文本 |
| `reference_message_id` | `str` | 被引用消息 ID，留空则引用触发消息 |

---

## 错误值对照

`error` 字段只会是以下固定短语之一（白名单脱敏，原始错误绝不透传）：

| 常量 | 值 | 触发条件 |
|---|---|---|
| `ERROR_TOKEN` | `token 获取失败或已失效` | 401 / token 相关关键词 |
| `ERROR_FORBIDDEN` | `无权限调用该接口` | 403 / forbidden / permission |
| `ERROR_NOT_FOUND` | `接口不存在或目标已失效` | 404 / not found |
| `ERROR_RATE_LIMIT` | `QQ API 限频` | 429 / rate limit / 频率 |
| `ERROR_BAD_REQUEST` | `请求参数不被 QQ API 接受` | 400 / bad request |
| `ERROR_SERVER` | `QQ 服务端错误` | 500 / 502 / 503 / 504 |
| `ERROR_TIMEOUT` | `请求超时` | timeout / timed out |
| `ERROR_NETWORK` | `网络错误` | connection / dns / ssl / proxy |
| `ERROR_GENERIC` | `调用失败` | 未匹配任何规则 |

参数校验失败返回的是具体的中文提示（如 `target_type 必须为 ['group', 'user'] 之一`），
因为这类信息不含敏感内容且对调用方有诊断价值。

---

## 配置项

配置文件：`config/plugins/qqbot_expand/config.toml`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `plugin.enabled` | bool | `true` | 插件总开关 |
| `plugin.config_version` | str | `"1.0.0"` | 配置版本（只读） |
| `features.enable_tools` | bool | `true` | 是否向 LLM 注册 3 个 Tool |
| `features.allow_raw_request` | bool | `true` | raw 通道总开关 |
| `features.raw_allowed_methods` | list | `["GET","POST","PUT","DELETE"]` | 方法白名单 |
| `features.debug_log_payload` | bool | `false` | 打印完整请求体（含敏感内容） |
| `http.max_keepalive_connections` | int | `20` | 空闲连接上限 |
| `http.max_connections` | int | `50` | 总连接上限 |
| `http.keepalive_expiry` | float | `30.0` | 空闲连接保活秒数 |
| `http.connect_timeout` | float | `10.0` | 连接超时 |
| `http.request_timeout` | float | `30.0` | 请求超时 |
| `http.http2` | bool | `true` | 启用 HTTP/2 |
| `http.retry_max_attempts` | int | `3` | 非 POST 请求重试次数 |
| `http.retry_backoff_base` | float | `1.0` | 退避基准（`base * 2^attempt`） |
| `http.retry_backoff_max` | float | `10.0` | 退避上限 |
| `http.retry_jitter` | float | `0.3` | 抖动系数 |

> 重试**仅针对网络错误**，HTTP 状态码错误不重试 —— 避免对已限频的接口雪上加霜。

---

## 组件签名一览

```
qqbot_expand:service:qqbot_message        QQBotMessageService
qqbot_expand:service:qqbot_interaction    QQBotInteractionService
qqbot_expand:service:qqbot_raw            QQBotRawService
qqbot_expand:tool:qq_send_keyboard        QQSendKeyboardTool
qqbot_expand:tool:qq_send_ark             QQSendArkTool
qqbot_expand:tool:qq_send_reply           QQSendReplyTool
```
