# qqbot_expand — QQ Bot 扩展能力

为 [`qqbot_adapter`](../qqbot_adapter) 补齐 QQ 开放平台的消息类型空白，并统一管理开放 API
调用出口，供框架内部其他插件调用。

`qqbot_adapter` 实现了纯文本（`msg_type=0`）、原生 Markdown（`msg_type=2`）和
富媒体底层出站链路（`msg_type=7`），且唯一的 HTTP 出口 `SendHandler.post_api()`
**只支持 POST**。本插件在不修改适配器的前提下补上剩余部分：

| 能力 | qqbot_adapter | qqbot_expand |

| --- | --- | --- |
| 按钮菜单（keyboard） | 无 | `qqbot_message.send_keyboard()` |
| ark 卡片（`msg_type=3`） | 无 | `qqbot_message.send_ark()` |
| 模板 Markdown | 无 | `qqbot_message.send_markdown_template()` |
| 引用回复（message_reference） | 无 | `qqbot_message.send_reply()` |
| 文本内嵌交互标签 | 无 | `src/builders.py` 的 `at_user` / `cmd_enter` / `cmd_input` 等 |
| embed（`msg_type=4`） | 无 | `qqbot_message.send_embed()`（QQ 侧当前不支持单聊/群聊，见下） |
| 富媒体 URL 上传与发送（`msg_type=7`） | 底层出站/分片能力 | `upload_media_from_url()` / `send_media()` / `send_media_from_url()` |
| GET / PUT / PATCH / DELETE 接口 | 无 | `qqbot_raw.request()` |
| 群入群审批策略、申请审批、成员禁言 | 无 | `qqbot_group_admin` Service；受控审批/禁言 Tool |
| 当前群信息、Bot 群内状态 | 无 | `qqbot_group_info` Service；当前群只读 Tool |
| 近期消息撤回、机器人分享链接 | 无 | `qqbot_utility` Service；显式开关的受控 Tool |
| 本地 bytes 分片媒体上传 | 无 | `qqbot_chunked_media` 受信 Service |
| 群入群申请事件 | 发布 `qqbot_adapter.group_join_request` | 去重分发受信回调，不自动审批 |
| 自定义菜单、指令面板 | 无 | `qqbot_menu_panel` Service；默认关闭的受控管理 Tool |
| 互动 callback | 发布专用 EventBus 事件，不 ACK | 集中路由、权限、幂等、ACK 与 `event_id` 回复 |

## 依赖

- **必须**先安装并配置好 `qqbot_adapter`，且适配器处于已连接状态。
  本插件不建立 WebSocket 连接、不管理 token，全部经由适配器的
  `send_handler` 公共属性取用。
- Python 依赖：`httpx>=0.24.0`

## 文档

| 文档 | 内容 |
| --- | --- |
| [使用教程](docs/usage-guide.md) | **从这里开始** —— 五分钟跑通、各类消息怎么发、踩坑指南 |
| [API 参考](docs/api-reference.md) | 逐个签名的参数表、返回结构、配置项、错误值对照 |
| [QQ 平台能力与限制](docs/qq-platform-notes.md) | 字段级平台规则，实现中各种校验的依据 |
| [能力路线图](docs/roadmap.md) | 已完成能力、真实环境验证与后续强化项 |

## 架构

```
qqbot_expand
    ├── docs/       使用教程 / API 参考 / 平台规则
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
POST 一律复用它；GET / PUT / PATCH / DELETE 由本插件自己的 `httpx.AsyncClient` 发出，
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

富媒体方法在上述字段外增加 `media` 上传元数据；详见 [API 参考](docs/api-reference.md)。所有最终 `/messages` 成功结果同时返回 `message_id` 与 `ref_idx`：顶层 `message_id` 来自 QQ 响应 `id`，用于撤回；`ref_idx` 来自 `ext_info.ref_idx`，用于后续引用，二者不可互换。引用触发消息时，应使用 Adapter 写入 `Message.extra["qq_ref_idx"]` 的入站 `msg_idx`，而 `Message.message_id` 仍只用于被动回复 `msg_id`。

### `qqbot_expand:service:qqbot_group_admin`

供受信插件或管理员工作流调用，覆盖入群自动审批策略、待审批申请和成员禁言。机器人必须是目标群管理员；策略相关接口通常为 **60 QPM**，申请列表为 **30 QPM**。该 Service 不会被直接注册成 LLM Tool。

LLM 仅可在显式启用 `features.enable_group_admin_tools=true` 后使用当前群的审批与禁言 Tool，并且当前群必须在 `features.group_admin_allowed_group_openids` 白名单内；空白名单会拒绝全部操作。跨群策略创建、执行和白名单管理始终只由 Service 提供。

### 平台域名迁移

Expand 与 `qqbot_adapter` 均遵循 QQ 官方统一域名：sandbox 与 production 的 REST、Gateway、WebSocket 和 AccessToken 均使用 `api.bot.qq.com`。Expand 的 POST 请求委托 Adapter，非 POST 请求复用 Adapter 当前 `base_url`；`env` 仍表示平台运行环境，不再选择旧域名。

| 方法 | 说明 |
| --- | --- |
| `send_keyboard(target_type, target_id, rows, content="", *, custom_template_id="", params=None, ...)` | 发按钮菜单。QQ 要求 keyboard 必须挂载在 Markdown 上且 Markdown 内容必填，因此 `content` 与 `custom_template_id` 必须提供其一 |
| `send_ark(target_type, target_id, template_id, kv, ...)` | 发 ark 卡片（`msg_type=3`） |
| `send_markdown_template(target_type, target_id, custom_template_id, params=None, *, rows=None, ...)` | 发已报备的模板 Markdown，可附带按钮 |
| `send_reply(target_type, target_id, content, reference_message_id, *, ignore_get_message_error=False, ...)` | 带引用的文本回复；`reference_message_id` 必须是 QQ `msg_idx/ref_idx` 引用索引，不是被动回复或撤回消息 ID |
| `send_embed(target_type, target_id, title, ...)` | 发 embed（`msg_type=4`）。**QQ 侧单聊/群聊均不支持**，保留仅为未来兼容 |
| `upload_media_from_url(target_type, target_id, file_type, url, *, file_name="")` | 上传公网 URL，返回目标隔离且带 TTL 的 `file_info`，不直接发送 |
| `send_media(target_type, target_id, file_info, ...)` | 使用已有 `file_info` 发送富媒体（`msg_type=7`） |
| `send_media_from_url(target_type, target_id, file_type, url, ...)` | 按官方两阶段协议完成上传并发送；支持图片、视频、语音、文件 |
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
`build_message_reference` / `build_media`，以及文本内嵌标签辅助 `at_user` / `at_everyone` / `emoji` /
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

### 新增 Service 与 Tool 边界

- `qqbot_group_info.get_group_info()` / `get_bot_group_state()`：受信插件可查询任意合法群 OpenID；对应 Tool 只查询触发群，且返回 `group_openid`。
- `qqbot_utility.recall_message()`：仅撤回本插件记录的、目标匹配且未超过两分钟的消息；`qq_recall_current_message` 还要求 `confirm=True`。`generate_share_link()` 的 `callback_data` 最长 32 字符。
- `qqbot_chunked_media.upload_bytes()`：只面向受信调用方的内存 `bytes`，限制 200 MB，校验 HTTPS 预签名 URL 与连续 0-based 分片；不注册为 Tool。
- `qqbot_group_admin.register_join_request_callback()`：消费 Adapter 已发布的 `qqbot_adapter.group_join_request`，按 `join_request_id` 去重后经 TaskManager 调用受信回调，默认绝不自动审批。

群信息与实用 Tool 默认关闭，分别通过 `features.enable_group_info_tools` 和 `features.enable_utility_tools` 启用。

### `qqbot_expand:service:qqbot_interaction`

- `register_callback(namespace, action, callback, permission=None, *, replace=False)` 注册
  `namespace:action:payload` 精确路由；可选权限函数和业务 callback 均支持同步/异步。
- `unregister_callback(namespace, action, callback=None)` 注销路由；传 callback 时会校验身份。
- `ack(interaction_id, code=0)` 是唯一 ACK 出口，带 TTL 和容量上限的插件级去重，网络超时不重试。
- `needs_ack(interaction_type)` 只对 `type=11` 消息按钮和 `type=12` 单聊快捷菜单返回真。
- callback 返回 `CallbackResult(handled, ack_code, message)`；`message` 使用 `event_id` 回复，绝不将 interaction ID 当 `msg_id`。

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

## 互动 callback 链路

启用 callback 按钮前，必须手工配置 `qqbot_adapter`：

```toml
[connection]
intents = 100663296 # (1 << 25) | (1 << 26)
```

Expand 不会跨插件读取或修改该配置。完整链路为：

```text
QQ Gateway INTERACTION_CREATE
  → qqbot_adapter 标准化并发布 qqbot_adapter.interaction_create
  → QQBotInteractionEventHandler 快速认领并通过 task_manager 调度
  → namespace:action:payload 精确路由、权限与业务 callback
  → QQBotInteractionService.ack() 唯一一次 ACK
  → 可选使用 event_id 回复 user/group
```

Adapter 不 ACK，Expand 是唯一 ACK 所有者。只有事件顶层 `type=11/12` ACK；按钮结构中的
`action.type=1` 只是“callback 按钮”枚举，两者不是同一个概念。同一 interaction ID 在
Gateway 重复投递、外部 Service 调用和网络超时场景下都不会重复 PUT；超时不代表 QQ 未收到，
因此不会自动重试。活跃记录达到容量上限时，新 Interaction 会被拒绝处理/ACK，直到旧记录过期，
以保证“最多一次”优先于可用性。

`button_data` 必须使用 `namespace:action:payload`。非法或未知路由 ACK code 1，权限不足 code 4，
业务可返回 code 3 表示重复操作。耗时 callback 在 TaskManager worker 中执行；Handler 不阻塞
Gateway 的事件发布。目标 openid 无法确认或为当前不支持回复的 guild 时，只 ACK 并记录日志。

外部调用方不应对已由 EventHandler 接管的 interaction ID 再调用 `ack()`。未安装或未启用
Expand 时 callback 不会 ACK，因此不要发送 `action.type=1` 按钮。互动应答接口使用统一的
`api.bot.qq.com` 域名；若 QQ 平台仍限制其沙箱能力，应按平台能力错误处理，而不切换到旧域名。


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
| `features.enable_tools` | `true` | 是否把基础精选 Tool 注册给 LLM |
| `features.enable_group_info_service` | `true` | 是否启用只读群信息 Service |
| `features.enable_group_info_tools` | `false` | 是否注册当前群信息/机器人状态 Tool |
| `features.enable_utility_tools` | `false` | 是否注册撤回与分享链接 Tool |
| `features.enable_menu_panel_service` | `false` | 是否启用菜单与指令面板 Service |
| `features.enable_menu_panel_tools` | `false` | 是否注册菜单面板 Tool |
| `features.allow_global_menu_write` | `false` | 是否允许覆盖全局自定义菜单 |
| `features.allow_panel_create` | `false` | 是否允许创建指令面板 |
| `features.allow_panel_delete` | `false` | 是否允许删除指令面板 |
| `features.menu_panel_allowed_operator_openids` | `[]` | 菜单面板操作者 OpenID 白名单 |
| `features.menu_panel_allowed_group_openids` | `[]` | 菜单面板群 OpenID 白名单 |
| `features.menu_panel_allowed_panel_ids` | `[]` | 可操作面板 ID 白名单 |
| `features.menu_panel_profiles` | `[]` | 受信 profile，固定面板作用域与目标 |
| `features.enable_group_admin_service` | `false` | 是否启用高权限群管理 Service |
| `features.enable_group_admin_tools` | `false` | 是否注册群审批/禁言 Tool |
| `features.group_admin_allowed_group_openids` | `[]` | 群管理 Tool 可操作的群 OpenID 白名单 |
| `features.allow_raw_request` | `true` | raw 通道总开关 |
| `features.raw_allowed_methods` | `["GET","POST","PUT","PATCH","DELETE"]` | raw 通道允许的方法白名单 |
| `features.debug_log_payload` | `false` | 打印完整请求体（含敏感内容，仅调试用） |
| `interaction.enabled` | `true` | 是否消费 Adapter 发布的 Interaction 事件 |
| `interaction.callback_timeout` | `5.0` | 权限与业务 callback 的单次超时（秒） |
| `interaction.button_data_max_length` | `1024` | 可处理的 button_data 最大字符数 |
| `interaction.dedup_ttl` | `300.0` | ACK 去重记录 TTL（秒） |
| `interaction.dedup_capacity` | `4096` | ACK 去重记录容量上限 |
| `http.*` | 与 `qqbot_adapter` 一致 | 连接池、超时、重试退避参数 |

## 测试

```bash
pytest plugins/qqbot_expand/tests
```

## 许可

GPL-3.0
