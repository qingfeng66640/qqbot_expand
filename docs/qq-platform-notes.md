# QQ 平台能力与限制对照

本文档汇总 `qqbot_expand` 依赖的 QQ 开放平台规则，来源为官方文档
（`docs/qqbot_official/develop/api-v2/`）。实现中的各种校验与降级都以此为依据。

## 消息类型支持矩阵

| msg_type | 名称 | 单聊 | 群聊 | 文字子频道 | 频道私信 | 本插件 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 文本 | 支持 | 支持 | 支持 | 支持 | `send_reply` |
| 2 | Markdown | 支持 | 支持 | 支持 | 支持 | `send_keyboard` / `send_markdown_template` |
| 3 | ark | 支持 | 支持 | 支持 | 支持 | `send_ark` |
| 4 | embed | **不支持** | **不支持** | 支持 | 支持 | `send_embed`（保留，实际不可用） |
| 7 | media | 支持 | 支持 | — | — | `upload_media_from_url` / `send_media` / `send_media_from_url` |

`qqbot_expand` 只覆盖 `/v2/users/{openid}/messages` 与
`/v2/groups/{group_openid}/messages` 两个接口，即单聊与群聊场景。频道相关接口
未纳入本插件范围。

## 发送接口字段

两个接口的请求参数基本一致：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `msg_type` | int | 必填 |
| `content` | string | 单聊选填；**群聊必填**（本插件对非文本消息自动补空串占位） |
| `markdown` | object | Markdown 对象 |
| `keyboard` | object | Keyboard 对象，必须与 markdown 同时下发 |
| `ark` | object | Ark 对象 |
| `media` | object | 富媒体 file_info |
| `message_reference` | object | 消息引用 |
| `msg_id` | string | 前置收到的用户消息 ID，用于被动回复 |
| `event_id` | string | 前置事件 ID，支持 `INTERACTION_CREATE`、`C2C_MSG_RECEIVE`、`GROUP_MSG_RECEIVE` 等 |
| `msg_seq` | int | 与 `msg_id` 联合使用，相同组合重复发送会失败，不填默认 1 |

`force_verify_image_resource` 是 Markdown 消息新增的可选 boolean。默认 `false` 时，图片转存失败可能被平台静默忽略而消息继续发送；传 `true` 时，任何图片资源转存失败都会中断整条消息并返回错误。`send_keyboard()` 与 `send_markdown_template()` 仅在调用方显式传 `True` 时写入该字段。

返回：`{"id": "消息唯一ID", "timestamp": 发送时间}`。

常见错误码：`22009` 消息发送超频、`304082`/`304083` 富媒体资源拉取失败。

## 富媒体（msg_type=7）

官方协议分两步：

1. `POST /v2/users/{openid}/files` 或 `/v2/groups/{openid}/files`，提交
   `file_type`、公网 `url`、可选 `file_name` 与 `srv_send_msg=false`；
2. 原样取出返回的 `file_info`，再向相同场景的 `/messages` 发送
   `{"msg_type": 7, "media": {"file_info": "..."}}`。

| file_type | 类型 | 文档软限制 | 硬限制 |
| --- | --- | --- | --- |
| 1 | 图片 | 20 MB | 200 MB |
| 2 | 视频 | 30 MB | 200 MB |
| 3 | 语音 | 20 MB | 200 MB |
| 4 | 文件 | 200 MB | 200 MB |

超过软限制时平台可能降级为文件；格式支持以平台实际响应为准。`file_info` 是不透明数据，
单聊上传只能用于相同单聊目标，群聊上传只能用于相同群聊目标；`ttl=0` 表示长期有效，
否则过期后必须重新上传。URL 上传接口限频 50 QPS，消息接口限频 100 QPS。

本插件固定使用 `srv_send_msg=false`，避免上传动作直接消耗主动消息额度，并允许第二阶段
携带 `msg_id`、`event_id` 和 `msg_seq`。URL 会在调用前检查协议和全部 DNS 解析结果，
拒绝非公网地址；QQ 平台下载时发生的重定向仍由平台负责。

常见上传/发送错误：`850019` 格式不支持、`850026` 下载 URL 失败、`850031` 超尺寸、
`40093002` 当日累计上传超限、`304080` file_info 无效、`40034004` 富媒体转存失败。

本地分片上传预留为后续能力，当前不可调用；需先确认适配器分片索引实现与最新官方
0-based `parts[].index` 协议一致。

## 群管理（2026-08-10）

机器人必须是群管理员才可使用入群审批、成员禁言和接收 `GROUP_JOIN_REQUEST`。自动审批策略最多 20 个，每个策略最多关联 100 个群；策略、审批和禁言接口通常限 60 QPM，入群申请列表限 30 QPM。

`GROUP_JOIN_REQUEST` 使用已有 `GROUP_AND_C2C_EVENT (1<<25)`，不新增 intent。Expand 当前只提供 Service/API 封装，不消费事件或自动审批；Adapter 应发布 `qqbot_adapter.group_join_request` 后，再由后续受信工作流接入。

官方已将 REST、Gateway discovery、fallback WSS 和 AccessToken 域名统一为 `api.bot.qq.com`。sandbox 与 production 使用同一网络主机，但仍代表不同的平台运行环境和权限范围。Expand 的 POST 请求委托 Adapter，非 POST 请求复用 Adapter 当前 `base_url`。

## 频控规则

| 场景 | 主动消息 | 被动回复 |
| --- | --- | --- |
| 单聊 | 每月 4 条 | 有效期 60 分钟，每条消息最多回复 4 次 |
| 群聊 | 每月 4 条 | 有效期 5 分钟，每条消息最多回复 5 次 |

群主动推送另有账号维度（企业认证 60 qpm / 个人认证 60 qpm / 未认证 30 qpm）与
单群维度（20 qpm）的限频。沙箱环境不受频控影响。

消息内含 URL 需先在 `q.qq.com` 后台「开发设置 - 消息 URL 配置」报备，否则发送失败。
调用发消息接口的 timeout 官方建议不低于 5 秒。

## 按钮（keyboard）

- 结构：`{"keyboard": {"content": {"rows": [{"buttons": [...]}]}}}`
- 上限：最多 5 行，每行最多 5 个按钮
- keyboard **必须挂载在 Markdown 消息上**，且 Markdown 内容必填，不支持仅下发键盘

单个 button 的字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 否 | 在一个 keyboard 内唯一，回调时原样带回 |
| `render_data.label` | 是 | 按钮文字 |
| `render_data.visited_label` | 是 | 点击后的按钮文字 |
| `render_data.style` | 是 | 0 灰色线框，1 蓝色线框 |
| `action.type` | 是 | 0 跳转（http / 小程序 scheme），1 回调后台，2 指令（自动在输入框插入 @bot data） |
| `action.permission.type` | 是 | 0 指定用户，1 仅管理者，2 所有人，3 指定身份组（仅频道） |
| `action.permission.specify_user_ids` | 否 | `type=0` 时必填 |
| `action.permission.specify_role_ids` | 否 | `type=3` 时必填，仅频道 |
| `action.data` | 是 | 操作相关数据 |
| `action.reply` | 否 | 指令按钮可用，是否带引用回复本消息 |
| `action.enter` | 否 | 指令按钮可用，点击后直接发送 data，**仅单聊可用** |
| `action.anchor` | 否 | 设为 1 时点击唤起选图器，设置后忽略 `enter`；仅手机端 8983+ 单聊 |
| `action.unsupport_tips` | 是 | 客户端不支持时的 toast 文案 |

`action.click_limit` 与 `action.at_bot_show_channel_list` 已被官方标记为弃用，
本插件不生成这两个字段。

## 文本交互标签

仅在含文本的消息（文本、图文、Markdown）中生效。

| 标签 | 作用域 | 说明 |
| --- | --- | --- |
| `<qqbot-at-user id="" />` | 群聊、文字子频道 | 旧协议 `<@userid>` 即将弃用 |
| `<qqbot-at-everyone />` | **仅文字子频道** | 需要 @全体成员 权限 |
| `<qqbot-cmd-enter text="" />` | **仅单聊**，仅 Markdown | 点击后直接发送，text ≤ 100 字符且需 urlencode |
| `<qqbot-cmd-input text="" show="" reference="" />` | 仅 Markdown | 点击后插入输入框，text / show ≤ 100 字符且需 urlencode |
| `<#channel_id>` | 仅频道 | 跳转子频道 |
| `<emoji:id>` | 仅频道 | 仅 `type=1` 系统表情；`type=2` 的 emoji 直接写字符即可 |

## ark 预置模板

默认开放、无需申请的三个模板：

### 23 链接 + 文本列表

| 变量 | 类型 | 说明 |
| --- | --- | --- |
| `#DESC#` | string | 描述 |
| `#PROMPT#` | string | 外显提示 |
| `#LIST#` | array | 条目数组，每项 `obj_kv` 含 `desc`（文本）与可选 `link`（需报备域名） |

### 24 文本 + 缩略图

| 变量 | 说明 |
| --- | --- |
| `#DESC#` / `#PROMPT#` | 描述 / 外显提示 |
| `#TITLE#` | 标题 |
| `#METADESC#` | 详情描述 |
| `#IMG#` | 缩略图链接 |
| `#LINK#` | 跳转链接 |
| `#SUBTITLE#` | 来源 |

### 37 大图

| 变量 | 说明 |
| --- | --- |
| `#PROMPT#` | 外显提示 |
| `#METATITLE#` / `#METASUBTITLE#` | 标题 / 子标题 |
| `#METACOVER#` | 大图，尺寸 975×540 |
| `#METAURL#` | 跳转链接 |

数组型变量的 kv 结构：
`{"key": "#LIST#", "obj": [{"obj_kv": [{"key": "desc", "value": "..."}]}]}`

权限：**主动** ark 默认开放；**被动** ark 需达到准入条件后向平台运营申请。

## Markdown

- 原生：`{"markdown": {"content": "# 标题\n正文"}}`
- 模板：`{"markdown": {"custom_template_id": "xxx", "params": [{"key": "title", "values": ["标题"]}]}}`
- 图片需使用公网可访问的 URL，平台会下载转存
- 2026-04-23 起单聊与群聊的自定义 Markdown 已全量开放，无需申请模板；频道场景仍需内邀

## 互动回调（INTERACTION_CREATE）

- intents：`1<<26`；与群聊/C2C 的 `1<<25` 组合为 `100663296`，必须在 Adapter 配置中手工设置
- Adapter 收到事件后发布 `qqbot_adapter.interaction_create`，不生成普通消息，也不 ACK
- 事件字段：`id`、`type`、`scene`（c2c/group/guild）、`chat_type`（0 频道/1 群聊/2 单聊）、
  `user_openid`、`group_openid`、`group_member_openid`、`data.resolved.*`
- `type` 取值：11 消息按钮、12 单聊快捷菜单、13 消息反馈、14 清空会话、
  15 进出故事集、16 切换模型、18 用户授权、19 群授权、20 群授权状态变更
- **只有 type 11 与 12 需要应答**

应答接口：

- `PUT /interactions/{interaction_id}`，限频 50 QPS
- 请求体 `{"code": int}`，取值 0 成功 / 1 操作失败 / 2 操作频繁 / 3 重复操作 /
  4 没有权限 / 5 仅管理员操作
- 成功返回空
- **每个 interaction_id 只能应答一次**，超时后失效；不应答客户端会一直 loading
- Expand 在请求前以 TTL/容量有界表去重；网络 timeout 也不自动重试
- 业务消息回复使用 `event_id=interaction_id`，不得使用 `msg_id` 或同时传两者
- 按钮 `action.type=1` 与事件顶层 `type=11/12` 是两套不同枚举
- 该接口使用统一域名 `api.bot.qq.com`；若平台限制沙箱能力，应按接口返回的能力错误处理，不切换到旧域名

错误码：630001 参数非法、630002~630006 appid / header 相关、630007 数据过大、
630008 互动预处理失败。

具体使用、路由注册和 ACK 所有权见 [README](../README.md#互动-callback-链路) 与 [使用教程](usage-guide.md#6-按钮-callback)。
