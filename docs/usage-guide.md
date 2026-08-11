# qqbot_expand 使用教程

面向**插件开发者**的实操指南。读完你能：给 QQ 用户发按钮菜单、ark 卡片、引用回复，
并在需要时直接调用任意 QQ 开放 API。

只讲怎么用。字段级的平台规则见 [QQ 平台能力与限制对照](qq-platform-notes.md)，
能力总览与安全说明见 [README](../README.md)。

---

## 0. 五分钟跑通

### 前置条件

1. `qqbot_adapter` 已安装、已填好 `app_id` / `app_secret`，且**已连接**
2. 本插件已放进 `plugins/qqbot_expand/`
3. 重启 Bot 或 `/reload qqbot_expand`

### 确认链路通了

在你自己的插件里：

```python
from src.app.plugin_system.api import service_api

raw = service_api.get_service("qqbot_expand:service:qqbot_raw")
status = await raw.get_status()
print(status)
```

期望输出：

```python
{
    "http_client_ready": True,     # 本插件的 httpx 客户端已就绪
    "raw_enabled": True,           # raw 通道未被配置关闭
    "connected": True,             # ← 关键：适配器 WebSocket 已连接
    "bot_id": "1234567890",
    "bot_name": "你的机器人",
    "platform": "qqbot",
    "env": "production",           # 或 sandbox
}
```

`connected` 为 `False` 就别往下走了，先修 `qqbot_adapter`。

---

## 0.1 群管理与 Adapter 交接

`qqbot_group_admin` 提供入群自动审批策略、申请审批和成员禁言。机器人必须是群管理员；策略及审批接口由 QQ 平台执行最终权限校验。

LLM Tool 默认关闭。只有配置同时满足：

```toml
[features]
enable_group_admin_service = true # 允许受信插件调用高权限 Service
enable_tools = true
enable_group_admin_tools = true
group_admin_allowed_group_openids = ["目标群 OpenID"]
```

才会注册当前群审批/禁言 Tool；目标群始终从触发会话推导，LLM 不能指定其他群。策略创建、执行和白名单管理不暴露给 LLM，只能通过 `qqbot_group_admin` Service 由受信插件调用。

本轮 Expand 不消费 `GROUP_JOIN_REQUEST`。Adapter 请自行完成以下交接：识别该 Dispatch，不生成 `MessageEnvelope`、不自动审批，以深拷贝 `raw_event` 发布 `qqbot_adapter.group_join_request`，并标准化 `group_openid`、`join_request_id`、`member_openid`、申请来源、验证信息和自动审批信息。该事件仍使用 `GROUP_AND_C2C_EVENT (1<<25)`，只有机器人为群管理员时可收到。

官方已将 REST、Gateway discovery、fallback WSS 和 AccessToken 端点统一为 `api.bot.qq.com`；请同步迁移 Adapter。Expand 的非 POST 请求已使用新域名。

---

## 1. 发一排按钮

最常见的需求：回复末尾挂几个按钮，让用户点一下就能继续。

```python
from src.app.plugin_system.api import service_api
from plugins.qqbot_expand.src.builders import build_button
from plugins.qqbot_expand.src.constants import ACTION_TYPE_COMMAND, ACTION_TYPE_LINK

msg = service_api.get_service("qqbot_expand:service:qqbot_message")

rows = [
    [
        build_button("下一页", action_type=ACTION_TYPE_COMMAND, data="/list 2"),
        build_button("刷新", action_type=ACTION_TYPE_COMMAND, data="/list 1"),
    ],
    [
        build_button("查看详情", style=1, action_type=ACTION_TYPE_LINK,
                     data="https://example.com/detail"),
    ],
]

result = await msg.send_keyboard(
    "group", group_openid, rows,
    content="共 32 条结果，当前第 1 页",
    msg_id=trigger_msg_id,          # 带上就是被动回复，强烈建议带
)

if result["success"]:
    print("消息 ID:", result["message_id"])
else:
    print("失败:", result["error"])
```

### 三类按钮怎么选

| 你想要的效果 | `action_type` | `data` 填什么 |
|---|---|---|
| 用户点了自动发一条消息给机器人 | `ACTION_TYPE_COMMAND`(2) | 指令文本，如 `/list 2` |
| 用户点了跳转网页 / 小程序 | `ACTION_TYPE_LINK`(0) | URL（**域名需先报备**） |
| 用户点了回调你的后端 | `ACTION_TYPE_CALLBACK`(1) | 任意回调数据 |

**指令按钮适合让用户发送一条真实消息；callback 按钮适合不进入普通聊天链的结构化业务动作。** callback 按钮的完整配置和注册方式见第 6 节。

### 硬性约束

- 最多 **5 行**，每行最多 **5 个**，超了 `build_button` / `build_keyboard` 直接抛 `ValueError`
- **`content` 或 `custom_template_id` 必须给一个**。QQ 不接受"光有按钮没有正文"的消息
- 指令按钮的 `data` ≤ 100 字符

### 只有管理员能点

```python
from plugins.qqbot_expand.src.constants import PERMISSION_TYPE_ADMIN, PERMISSION_TYPE_SPECIFY_USER

# 仅管理员
build_button("解散", action_type=ACTION_TYPE_COMMAND, data="/dismiss",
             permission_type=PERMISSION_TYPE_ADMIN)

# 仅指定的几个人
build_button("确认", action_type=ACTION_TYPE_COMMAND, data="/confirm",
             permission_type=PERMISSION_TYPE_SPECIFY_USER,
             specify_user_ids=[user_openid_a, user_openid_b])
```

`permission_type=0` 时不给 `specify_user_ids` 会抛 `ValueError` —— 这是刻意的，
避免你发出一个谁都点不了的按钮。

---

## 2. 发 ark 卡片

比纯文本好看，适合搜索结果、榜单、推荐位。

### 模板 23：链接 + 文本列表

```python
from plugins.qqbot_expand.src.constants import ARK_TEMPLATE_LIST

kv = [
    {"key": "#DESC#", "value": "今日热搜"},
    {"key": "#PROMPT#", "value": "今日热搜"},      # 消息列表里的外显文字
    {"key": "#LIST#", "obj": [
        {"obj_kv": [{"key": "desc", "value": "1. 某某某"},
                    {"key": "link", "value": "https://example.com/1"}]},
        {"obj_kv": [{"key": "desc", "value": "2. 另一条"}]},   # link 可省略
    ]},
]

await msg.send_ark("group", group_openid, ARK_TEMPLATE_LIST, kv, msg_id=trigger_msg_id)
```

### 模板 24：文本 + 缩略图

```python
from plugins.qqbot_expand.src.constants import ARK_TEMPLATE_THUMBNAIL

kv = [
    {"key": "#PROMPT#", "value": "文章推荐"},
    {"key": "#TITLE#", "value": "标题"},
    {"key": "#METADESC#", "value": "一段摘要"},
    {"key": "#IMG#", "value": "https://example.com/cover.png"},
    {"key": "#LINK#", "value": "https://example.com/article"},
]

await msg.send_ark("user", user_openid, ARK_TEMPLATE_THUMBNAIL, kv)
```

模板 37（大图）的变量表见 [平台文档](qq-platform-notes.md#ark-预置模板)。

> **权限坑**：主动 ark 默认能发；**被动 ark**（即带了 `msg_id` / `event_id`）
> 需要向平台运营单独申请。如果你带 `msg_id` 发 ark 收到权限错误，就是这个原因。

---

## 3. 引用回复

群里人多时，明确回应某条消息：

```python
await msg.send_reply(
    "group", group_openid,
    content="你说的这个我查到了",
    reference_message_id=某条消息的_id,
    ignore_get_message_error=True,   # 原消息被撤回时也照常发出
    msg_id=trigger_msg_id,
)
```

`ignore_get_message_error=True` 建议默认开。否则被引用的消息一旦被撤回或过期，
整条消息会发送失败。

---

## 4. 文本内嵌交互标签

在文本 / Markdown 里插入可点击元素：

```python
from plugins.qqbot_expand.src.builders import at_user, cmd_enter, cmd_input

# @某人（群聊、子频道可用）
content = f"{at_user(member_openid)} 你的任务完成了"

# 点击后直接发送（仅单聊 + 仅 Markdown）
content = f"要继续吗？{cmd_enter('/continue')}"

# 点击后填进输入框，用户自己改（仅 Markdown）
content = f"试试 {cmd_input('/search 关键词', show='搜索')}"
```

作用域限制很严，写之前先对一下表：

| 标签 | 单聊 | 群聊 | 子频道 |
|---|---|---|---|
| `at_user()` | — | ✅ | ✅ |
| `at_everyone()` | ❌ | ❌ | ✅ |
| `cmd_enter()` | ✅ | ❌ | ❌ |
| `cmd_input()` | ✅ | ✅ | ✅ |
| `emoji()` | ❌ | ❌ | ✅ |

`cmd_enter` / `cmd_input` 只在 **Markdown 消息**里生效，塞进纯文本没有效果。
文本长度上限 100 字符，超了抛 `ValueError`；urlencode 由函数内部完成，你直接传原文。

---

## 5. 调用任意 QQ 开放 API

本插件没包装的接口，用 raw 通道：

```python
raw = service_api.get_service("qqbot_expand:service:qqbot_raw")

# GET
result = await raw.request("GET", "/users/@me")

# 带查询参数
result = await raw.request("GET", "/users/@me/guilds", query={"limit": 10})

# POST / PUT 带请求体
result = await raw.request("PUT", "/some/endpoint", body={"key": "value"})

if result["success"]:
    data = result["data"]
else:
    print(result["error"])
```

统一返回 `{"success": bool, "data": dict | None, "error": str | None}`。

**路径必须是以 `/` 开头的相对路径。** 绝对 URL、`//`、`..` 都会被桥接层拒绝 ——
这是防 SSRF 的，不是 bug。域名由插件根据适配器的沙箱/正式环境自动选择。

沙箱不支持的接口加 `force_production=True` 强制走正式域名。

---

## 6. 按钮 callback

### 启用 Interaction intent

`qqbot_adapter` 默认只订阅群聊/C2C 消息。callback 按钮还需要手工配置：

```toml
[connection]
intents = 100663296 # GROUP_AND_C2C_EVENT | INTERACTION
```

本插件只在加载日志中提示，不会读取或修改 Adapter 配置。Adapter 收到事件后只发布
`qqbot_adapter.interaction_create`，不 ACK，也不创建普通消息；Expand 是唯一 ACK 所有者。

### 注册路由并发送 callback 按钮

```python
from src.app.plugin_system.api import service_api
from plugins.qqbot_expand.src.builders import build_button
from plugins.qqbot_expand.src.constants import ACTION_TYPE_CALLBACK
from plugins.qqbot_expand.src.interaction import CallbackResult

interaction = service_api.get_service("qqbot_expand:service:qqbot_interaction")

async def approve(context, payload):
    # context.operator_openid 可用于业务权限校验；payload 是第三段原文
    return CallbackResult(
        handled=True,
        ack_code=0,
        message=f"已处理任务 {payload}",
    )

registration = interaction.register_callback("todo", "approve", approve)
assert registration["success"]

button = build_button(
    "批准",
    action_type=ACTION_TYPE_CALLBACK,
    data="todo:approve:task-42",
)
await msg.send_keyboard(
    "group",
    group_openid,
    [[button]],
    content="是否批准？",
    msg_id=trigger_msg_id,
)
```

路由固定为 `namespace:action:payload`，只按 `(namespace, action)` 精确查找；payload 可以为空。
namespace/action 只能包含字母、数字、下划线和连字符。禁止把 button_data 当 Python、模块路径或
命令执行。

可选 `permission(context, payload)` 可同步或异步；返回 false 时 ACK code 4，业务 callback
不会执行。callback 可返回 `CallbackResult(handled, ack_code, message)` 或简写为 0～5 的整数。
未知/非法路由与异常/超时使用 code 1；业务可用 code 3 表示重复操作。

`message` 只在可确认的 user/group 目标上发送，并且只携带 `event_id=context.event_id`，绝不
把 interaction ID 作为 `msg_id`。C2C 流式业务应自行通过
`service_api.get_service("qqbot_adapter:service:qqbot")` 调用
`start_streaming(..., event_id=context.event_id)`，不得同时传 msg_id。

只有事件顶层 `type=11/12` 需要 ACK。按钮的 `action.type=1` 与 Interaction 顶层 type 是两套
不同枚举。ACK 请求前会写入插件级 TTL/容量有界去重表；网络超时也不会自动重试。外部调用方
不应再次 ACK 已由 EventHandler 接管的 ID。未安装或关闭 Expand 时 callback 不会 ACK。

注销时可校验 callback 身份：

```python
interaction.unregister_callback("todo", "approve", approve)
```

---

## 7. 给 LLM 用的 Tool

三个 Tool 自动注册给 LLM，只在 QQ 会话激活，**发送目标由触发消息自动推导**，
LLM 不需要也无法指定 openid：

| Tool | LLM 什么时候会用 |
|---|---|
| `qq_send_keyboard` | 需要给用户提供后续操作选项时 |
| `qq_send_ark` | 需要展示结构化的列表或卡片时 |
| `qq_send_reply` | 群聊中需要明确回应某条消息时 |

不想让 LLM 碰这些，配置里关掉：

```toml
[features]
enable_tools = false
```

关掉后 Service 照常可用，只是不再注册 Tool。

---

## 8. 富媒体：图片、视频、语音和文件

QQ 富媒体采用两阶段流程：先上传 URL 得到 `file_info`，再用 `msg_type=7` 发送。
最常用的是一站式方法：

```python
from plugins.qqbot_expand.src.constants import FILE_TYPE_IMAGE

result = await msg.send_media_from_url(
    "group",
    group_openid,
    FILE_TYPE_IMAGE,
    "https://cdn.example.com/report.png",
    file_name="report.png",
    msg_id=trigger_msg_id,
)
```

需要复用上传结果时，可拆成两步：

```python
uploaded = await msg.upload_media_from_url(
    "user", user_openid, 2, "https://cdn.example.com/demo.mp4"
)
if uploaded["success"]:
    await msg.send_media(
        "user",
        user_openid,
        uploaded["media"]["file_info"],
        msg_id=trigger_msg_id,
    )
```

`file_type` 为 1 图片、2 视频、3 语音、4 文件。`file_info` 不可解析或修改，也不能在
单聊、群聊或不同目标之间复用；`ttl` 非 0 时必须在到期前发送。上传固定使用
`srv_send_msg=false`，因此被动回复字段只在第二阶段使用，不会把上传动作误算成一次主动消息。

URL 必须是公网 HTTP(S) 地址。本插件会拒绝私网、环回、链路本地及公私混合 DNS 结果；
实际下载和重定向行为仍由 QQ 平台控制。

本地文件分片上传暂未开放。未来预留 `send_media_from_local_file`，待适配器分片协议按
最新官方文档验证后再实现。

---

## 9. 被动回复 vs 主动推送

**这是最容易踩的坑。**

```python
# 被动回复 —— 带 msg_id，几乎不受限
await msg.send_keyboard("group", gid, rows, content="...", msg_id=trigger_msg_id)

# 主动推送 —— 不带，每月只有 4 条额度！
await msg.send_keyboard("group", gid, rows, content="...")
```

| | 被动回复 | 主动推送 |
|---|---|---|
| 触发条件 | 带 `msg_id` 或 `event_id` | 都不带 |
| 单聊限制 | 60 分钟内每条消息可回 4 次 | **每月 4 条** |
| 群聊限制 | 5 分钟内每条消息可回 5 次 | **每月 4 条** |

**只要是在响应用户消息，就一定要带 `msg_id`。** 忘了带会悄悄消耗主动推送额度，
用完之后所有主动消息都发不出去。

`msg_seq` 用于同一条 `msg_id` 回复多次的场景：

```python
await msg.send_reply(..., msg_id=mid, msg_seq=1)
await msg.send_reply(..., msg_id=mid, msg_seq=2)   # 同一 msg_id 的第二次回复
```

不填默认为 1。相同 `msg_id + msg_seq` 重复发送会失败。

---

## 10. 错误处理

所有方法都不抛异常，统一返回结构，`error` 已脱敏：

```python
result = await msg.send_keyboard(...)
if not result["success"]:
    logger.warning(f"发送失败: {result['error']}")
```

你会看到的错误只有这几种固定短语：

| `error` | 含义 | 怎么办 |
|---|---|---|
| `token 获取失败或已失效` | 适配器 token 问题 | 检查 `app_secret` |
| `无权限调用该接口` | 能力未开通 | 如被动 ark 需申请 |
| `QQ API 限频` | 超频 | 退避重试，或检查是否误用主动推送 |
| `请求参数不被 QQ API 接受` | 参数不合法 | 对照平台文档检查消息体 |
| `接口不存在或目标已失效` | 路径错或 openid 失效 | — |
| `QQ 服务端错误` / `请求超时` / `网络错误` | 传输层问题 | 已自动重试过，可再试 |

**原始错误绝不透传** —— QQ 的错误信息可能带 token、appid、完整 URL，而这些返回值
会流向 LLM 和用户侧。要看原文开 `features.debug_log_payload = true` 查日志。

---

## 11. 常见问题

**Q: callback 按钮发出去了但点击没反应**
A: 检查 Adapter `intents=100663296`、Expand 是否启用、加载日志是否提示 Interaction，以及
`button_data` 是否匹配已注册的 `namespace:action:payload` 路由。未启用 Expand 时不会 ACK。

**Q: `无法从当前会话推导 QQ 发送目标`**
A: Tool 在非 QQ 平台会话被调用了，或触发消息缺少 openid。Tool 已限定
`associated_platforms=["qq"]`，正常不会发生。

**Q: 发 embed 一直失败**
A: QQ 单聊和群聊**都不支持 embed**，只有子频道能用。`send_embed` 保留纯为未来兼容。

**Q: 链接按钮点了没跳转 / 消息发不出去**
A: URL 域名需先在 `q.qq.com` 后台「开发设置 - 消息 URL 配置」报备。

**Q: 消息发送成功但客户端没显示**
A: 检查是不是超了主动推送的每月 4 条额度。

**Q: 沙箱环境某接口 404**
A: 部分接口沙箱不提供，用 `force_production=True`。

---

## 相关文档

- [README](../README.md) —— 能力总览、配置项、安全说明
- [QQ 平台能力与限制对照](qq-platform-notes.md) —— 字段级平台规则
- [QQ 官方文档](https://bot.q.qq.com/wiki/develop/api-v2/)
