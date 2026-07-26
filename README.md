# qqbot_expand — QQ Bot 扩展能力

为 [`qqbot_adapter`](../qqbot_adapter) 补齐 QQ 开放平台的消息类型空白，并统一管理开放 API
调用出口，供框架内部其他插件调用。

`qqbot_adapter` 只实现了纯文本（`msg_type=0`）、原生 Markdown（`msg_type=2`）、
富媒体（`msg_type=7`）三种出站消息，且唯一的 HTTP 出口 `SendHandler.post_api()`
**只支持 POST**。本插件在不修改适配器的前提下补上剩余部分：

| 能力 | qqbot_adapter | qqbot_expand |
| --- | --- | --- |
| 按钮菜单（keyboard） | 无 | `qqbot_message.send_keyboard()` |
| ark 卡片（`msg_type=3`） | 无 | `qqbot_message.send_ark()` |
| 模板 Markdown | 无 | `qqbot_message.send_markdown_template()` |
| 引用回复（message_reference） | 无 | `qqbot_message.send_reply()` |
| 文本内嵌交互标签 | 无 | `src/builders.py` 的 `at_user` / `cmd_enter` / `cmd_input` 等 |
| embed（`msg_type=4`） | 无 | `qqbot_message.send_embed()`（QQ 侧当前不支持单聊/群聊，见下） |
| GET / PUT / DELETE 接口 | 无 | `qqbot_raw.request()` |
| 互动回调应答 | 自动 `code=0` | `qqbot_interaction.ack()`（见下方限制） |

## 依赖

- **必须**先安装并配置好 `qqbot_adapter`，且适配器处于已连接状态。
  本插件不建立 WebSocket 连接、不管理 token，全部经由适配器的
  `send_handler` 公共属性取用。
- Python 依赖：`httpx>=0.24.0`

## 架构

```
qqbot_expand
    ├── services/   对外 Service（面向其他插件）
    ├── tools/      精选 Tool（面向 LLM，仅 QQ 平台激活）
    ├── tests/      单元测试
    └── src/        内部实现
         ├── bridge.py     适配器桥接 + 统一请求出口
         ├── builders.py   消息结构体构造与校验（纯函数）
         ├── constants.py  QQ 开放平台枚举
         ├── errors.py     错误白名单脱敏
         └── targets.py    从触发消息推导发送目标
```

**POST 走适配器，其余自持客户端**：`SendHandler.post_api()` 内置了 401 重试与错误处理，
POST 一律复用它；GET / PUT / DELETE 由本插件自己的 `httpx.AsyncClient` 发出，
token 仍向适配器索取。该客户端挂在插件实例上（`BaseService` 每次 `get_service()`
都是新实例，不能缓存长生命周期资源），由 `on_plugin_loaded` / `on_plugin_unloaded` 管理。

## Service

通过 `service_api.get_service(...)` 获取。

### `qqbot_expand:service:qqbot_message`

所有方法的公共参数：

- `target_type`：`"user"`（C2C 单聊）或 `"group"`（群聊）
- `target_id`：对应的 openid
- `msg_id` / `event_id`：填了即为**被动回复**；都不填则走主动推送
- `msg_seq`：与 `msg_id` 联合使用的回复序号，相同 `msg_id + msg_seq` 重复发送会失败；
  带 `msg_id` 时缺省填 1

返回体统一为 `{"success": bool, "message_id": str, "error": str | None}`。

| 方法 | 说明 |
| --- | --- |
| `send_keyboard(target_type, target_id, rows, content="", *, custom_template_id="", params=None, ...)` | 发按钮菜单。QQ 要求 keyboard 必须挂载在 Markdown 上且 Markdown 内容必填，因此 `content` 与 `custom_template_id` 必须提供其一 |
| `send_ark(target_type, target_id, template_id, kv, ...)` | 发 ark 卡片（`msg_type=3`） |
| `send_markdown_template(target_type, target_id, custom_template_id, params=None, *, rows=None, ...)` | 发已报备的模板 Markdown，可附带按钮 |
| `send_reply(target_type, target_id, content, reference_message_id, *, ignore_get_message_error=False, ...)` | 带引用的文本回复 |
| `send_embed(target_type, target_id, title, ...)` | 发 embed（`msg_type=4`）。**QQ 侧单聊/群聊均不支持**，保留仅为未来兼容 |
| `send_raw_message(target_type, target_id, payload)` | 直接投递完整消息体，用于尚未包装的形态 |

```python
from src.app.plugin_system.api import service_api
from plugins.qqbot_expand.src.builders import build_button
from plugins.qqbot_expand.src.constants import ACTION_TYPE_COMMAND, ACTION_TYPE_LINK

msg = service_api.get_service("qqbot_expand:service:qqbot_message")

rows = [[
    build_button("查看详情", action_type=ACTION_TYPE_COMMAND, data="/detail"),
    build_button("官网", style=1, action_type=ACTION_TYPE_LINK, data="https://example.com"),
]]
result = await msg.send_keyboard("group", group_openid, rows, content="请选择操作：", msg_id=msg_id)
```

`src/builders.py` 里的构造函数可以自由复用，它们只做数据拼装与校验，不发请求：
`build_button` / `build_keyboard` / `build_ark` / `build_embed` / `build_markdown` /
`build_message_reference`，以及文本内嵌标签辅助 `at_user` / `at_everyone` / `emoji` /
`cmd_enter` / `cmd_input`。

### `qqbot_expand:service:qqbot_raw`

```python
raw = service_api.get_service("qqbot_expand:service:qqbot_raw")

await raw.get_status()                          # 探测桥接链路
await raw.request("GET", "/users/@me")          # 调用任意 openapi
```

- `request(method, path, body=None, query=None, *, force_production=False)`
  返回 `{"success": bool, "data": dict | None, "error": str | None}`
- `get_status()` 转发 `qqbot_adapter:service:qqbot` 的状态，并附加
  `http_client_ready` / `raw_enabled`

### `qqbot_expand:service:qqbot_interaction`

- `ack(interaction_id, code=0)` → `PUT /interactions/{id}`（限频 50 QPS）
- `code` 取值：0 操作成功 / 1 操作失败 / 2 操作频繁 / 3 重复操作 / 4 没有权限 / 5 仅管理员操作
- `needs_ack(interaction_type)` 判断某个互动类型是否需要应答（仅 `type=11` 消息按钮、
  `type=12` 单聊快捷菜单需要）

## Tool

三个精选 Tool 会注册给 LLM，全部限定 `associated_platforms = ["qq"]`，
只在 QQ 官方 Bot 会话中激活。发送目标由触发消息自动推导，LLM 不需要（也无法）指定 openid。

| Tool | 用途 |
| --- | --- |
| `qq_send_keyboard` | 发按钮菜单，按钮支持指令与链接两种 |
| `qq_send_ark` | 发 ark 卡片，`style='list'`（模板 23）/ `style='card'`（模板 24） |
| `qq_send_reply` | 发引用回复 |

不想把 QQ 专属能力暴露给 LLM，可在配置里关闭 `features.enable_tools`，
此时只注册 Service。

## QQ 平台能力限制速查

这些限制来自 QQ 官方文档，不是本插件的实现取舍，请在设计交互时提前考虑：

| 能力 | 单聊 | 群聊 | 说明 |
| --- | --- | --- | --- |
| 自定义 Markdown / 按钮 | 支持 | 支持 | 2026-04-23 起全量开放，无需单独申请模板 |
| ark（主动消息） | 支持 | 支持 | 模板 23 / 24 / 37 默认可用 |
| ark（被动消息） | 需申请 | 需申请 | 达到准入条件后向平台运营申请 |
| embed | **不支持** | **不支持** | 仅文字子频道与频道私信可用 |
| `action.enter`（点击即发送） | 支持 | **不支持** | `qq_send_keyboard` 已按会话类型自动处理 |
| `<qqbot-cmd-enter />` | 支持 | **不支持** | 仅 Markdown 内生效 |
| `<qqbot-at-everyone />` | 不支持 | 不支持 | 仅文字子频道可用 |
| `<emoji:id>` | 不支持 | 不支持 | 仅频道可用，且仅 `type=1` 系统表情 |

发送频次（官方规则，超限会发送失败）：

- 单聊：主动消息每月 4 条；被动回复有效期 60 分钟，每条消息最多回复 4 次
- 群聊：主动消息每月 4 条；被动回复有效期 5 分钟，每条消息最多回复 5 次
- 消息内含 URL 需先在 `q.qq.com` 后台「开发设置 - 消息 URL 配置」报备，否则发送失败

## 互动回调（按钮点击）的已知限制

**首版只做"发出去"和"主动应答"，收不到回调事件。** 原因如下，务必先读完再决定是否依赖按钮交互：

1. **默认不订阅互动事件**。`qqbot_adapter` 的 `connection.intents` 默认为 `33554432`
   （`1<<25`，群聊@ + 单聊），**不含** `1<<26` 的 INTERACTION 位。想收到按钮回调需要
   **你自己**把适配器配置里的 `intents` 改成 `100663296`（`1<<25 | 1<<26`）——
   本插件不会、也不应该代改 `qqbot_adapter` 的配置。

2. **即便开启，回调 payload 也到不了本插件**。适配器的 `MessageHandler` 收到
   `INTERACTION_CREATE` 后走 `_handle_interaction_create()` 然后 `return None`，
   事件不会转成 `MessageEnvelope` 进入核心。而 `gateway.set_dispatch_callback`
   是单槽回调，已被适配器占用，本插件无法旁挂监听。

3. **自定义 ACK 会与适配器竞争**。适配器在收到互动事件时会自动应答 `{"code": 0}`，
   而按官方文档同一个 `interaction_id` **只能应答一次**且过期后不可再答。因此
   `qqbot_interaction.ack()` 传自定义 code 时很可能失败（晚到的一方被拒）。该 Service
   适用于"通过其他途径拿到 interaction_id"的场景，不能当作完整的互动链路使用。

4. **沙箱不支持**。互动应答接口只在正式域名 `api.sgroup.qq.com` 上可用，
   本插件对该接口强制走正式域名。

结论：按钮可以正常发出、正常显示。**指令按钮**（`action.type=2`）与**链接按钮**
（`action.type=0`）均可正常工作 —— 指令按钮点击后由用户端发出一条消息，走正常消息链路，
不依赖回调。但**回调按钮**（`action.type=1`）的后端回调目前不可用。

## 安全说明

- **错误脱敏**：QQ 的原始错误可能带 token / appid / 完整 URL，而本插件的返回值会流向
  LLM 与用户侧。`src/errors.py` 做白名单式归类，只输出预定义短语，绝不透传原文。
- **路径校验**：`raw.request()` 的 `path` 必须是以 `/` 开头的相对路径，桥接层会拒绝
  绝对 URL、协议相对 URL 与 `..`，防止请求被打到 QQ 域名之外的主机。
- **raw 通道不暴露给 LLM**：`qqbot_raw` 只注册为 Service，不注册为 Tool。
- **双重开关**：`features.allow_raw_request` 关闭后 `request()` 一律拒绝；
  `features.raw_allowed_methods` 可进一步收窄允许的 HTTP 方法。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `plugin.enabled` | `true` | 插件总开关 |
| `features.enable_tools` | `true` | 是否把三个精选 Tool 注册给 LLM |
| `features.allow_raw_request` | `true` | raw 通道总开关 |
| `features.raw_allowed_methods` | `["GET","POST","PUT","DELETE"]` | raw 通道允许的方法白名单 |
| `features.debug_log_payload` | `false` | 打印完整请求体（含敏感内容，仅调试用） |
| `http.*` | 与 `qqbot_adapter` 一致 | 连接池、超时、重试退避参数 |

## 测试

```bash
pytest plugins/qqbot_expand/tests
```

## 许可

GPL-3.0
