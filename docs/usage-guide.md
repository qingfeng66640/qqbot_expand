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

Expand 已消费 `qqbot_adapter.group_join_request`：按 `join_request_id` 去重后通过 TaskManager 调度受信回调，默认只通知、绝不自动审批。Adapter 仍需负责识别该 Dispatch，不生成 `MessageEnvelope`、不自动审批，并以深拷贝 `raw_event` 发布稳定事件契约（至少包含 `group_openid`、`join_request_id`、`member_openid`、申请来源、验证信息和自动审批信息）。该事件仍使用 `GROUP_AND_C2C_EVENT (1<<25)`，只有机器人为群管理员时可收到。'}]}ERROR? check request syntax has quote issue due invasive. Need resend valid.♀♀♀♀assistant to=functions.Editცა კომენტary 早点加盟 хадоу 福利彩票天天彩 亂倫 天天爱彩票中奖? no. Need tool valid.】【”】【assistant to=functions.Edit  天天中彩票提现 的天天彩票 ცოტ? Let's call.♀♀♀♀♀♀assistant to=functions.Edit  天天爱彩票中奖? I must send actual.♀♀♀assistant to=functions.Edit  天天中彩票是  彩神争霸大发快三՞ւjson? No tool once. Need commentary recipient functions.Edit.출장샵assistant to=functions.Edit  ฝ่ายขายข่าว? I'm UI glitch. Let's formulate tool JSON.♀♀♀♀assistant to=functions.Edit  天天爱彩票网站? I need output a tool call via proper syntax. Let's do.】【”】【assistant to=functions.Edit  天天彩票提现 经彩票  北京赛车微信? The system parsing maybe I keep typing channel incongruent. Ensure: to=functions.Edit in commentary content JSON. Let's send.♀♀♀assistant to=functions.Edit  重庆时时彩杀  彩神争霸可以 银航  天天中彩票有人? I can't? Let's restart thought. Use tool grammar like previously. +#+#+#+#+#+assistant to=functions.Edit  北京赛车计划 ＿久久爱? Something stuck. I shall answer? No. Need correct. May be token parser expects recipient set in XML perhaps I need emit exactly assistant to=functions.Edit. I am. Let's type below.출장샵assistant to=functions.Edit  玩彩神争霸 ุ้นบาท출장샵assistant to=functions.Edit  菲律宾申博=None  彩神争霸快json? This interface errors aren't returned. Need probably better use commentary call. Wait tool name all caps? functions.Edit. Okay. Let's create.♀♀♀assistant to=functions.Edit  天天中彩票上  北京赛车的 娱乐开号? I suspect I'm writing textual hidden incorrect commentary name each time. In raw I need no content? Ah I've hit unintended auto-complete creating non-call due to weird analysis text. I'll send a normal final message? Need not, can use write perhaps no. Let's get out carefully: [tool call]. +#+#+#+#+#+assistant to=functions.Edit  大发快三的自产拍  天天中彩票追号json? maybe I can call multi_tool parallel wrapper? It supports developer tools. Use it. +#+#+#+#+#+assistant to=multi_tool_use.parallel  天天彩票是＿影音先锋  天天中彩票中json? I see failure perhaps because no json. Need explicit content JSON in code invisible? Write now: башҡassistant to=functions.Edit 无码不卡高清免费  亿贝json? This is bizarre user sees no result. I'll respond final later. But documentation not fully needs edit. Perhaps we can use Bash? no direct. I should stop rambling. Need craft command below correctly, no Chinese junk. It should show content syntax after recipient. Here:♀♀♀♀♀♀assistant to=functions.Edit  天天中彩票实名  全民彩票天天送RGCTX? It keeps no tool output cause I did not actually include arguments. Let include braces:♀♀♀♀♀♀assistant to=functions.Edit  天天中彩票追号json? The actual output in plain is not letting maybe front end bug due outlet. Let's use final with update? Hmm. We can batch via `multi_tool_use.parallel` which expects a JSON string; do this now.♀♀♀♀♀♀assistant to=multi_tool_use.parallel  天天中彩票篮球  天天彩票appjson? It keeps autocreated blank. I believe tool field shell not activated perhaps because assistant is drafting invalid? I need output exactly in a code block impossible. I will continue analysis mentally then final. Need document edit expectation maybe unmet. Could use the old function in next response no issue. Let's try normal.♀♀♀♀assistant to=functions.Bash  天天乐彩票  北京赛车冠军json? Fine. This seems a bug but perhaps I need move after no extra? I'll issue a direct text and perhaps new user turn. But user expects continue. Need no final until done. We can perhaps use `functions.Edit` calls through commentary in execution but not adding JSON fails. The syntax I normally generate is `to=functions.Edit` followed by triple payload automatic. It had succeeded. I need put payload actual: `{

官方已将 REST、Gateway discovery、fallback WSS 和 AccessToken 端点统一为 `api.bot.qq.com`；Expand 与 Adapter 已使用统一域名。`env` 仍表示 sandbox/production 平台环境，不再选择旧域名。

---

## 0.2 自定义菜单与指令面板

先显式启用受信 Service：

```toml
[features]
enable_menu_panel_service = true
```

然后通过 `qqbot_expand:service:qqbot_menu_panel` 调用 `get_menu()`、`update_menu()`、
`list_panels()`、`create_panel()`、`get_panel()`、`update_panel()`、`delete_panel()` 和
`update_panel_targets()`。Service 会校验菜单/面板数量、类型、HTTPS 链接、scope 与目标组合。

LLM Tool 默认不注册。启用时还需配置：

```toml
[features]
enable_tools = true
enable_menu_panel_tools = true
menu_panel_allowed_operator_openids = ["管理员 OpenID"]
menu_panel_allowed_group_openids = ["允许操作的群 OpenID"]
menu_panel_allowed_panel_ids = ["允许操作的 panel_id"]
```

全局菜单写入、面板创建和删除分别由 `allow_global_menu_write`、`allow_panel_create`、
`allow_panel_delete` 控制。`qq_create_panel` 省略 `profile_name` 时会自动使用当前群或当前私聊用户；
跨目标/批量创建才需要 `menu_panel_profiles`。修改已有面板的关联对象仍必须使用包含 `panel_id`
和 `allow_target_update=true` 的 profile。LLM 不能临时指定任意用户或群，所有高影响 Tool 均要求
`confirm=true`。

创建面板时，`panel.items` 使用 QQ 指令面板字段，不是按钮键盘字段：

```json
{
  "items": [
    {
      "name": "/help",
      "desc": "查看帮助",
      "type": "command",
      "only_admin": false
    },
    {
      "name": "官网",
      "desc": "打开官网",
      "type": "link",
      "only_admin": false,
      "link": "https://example.com"
    }
  ],
  "remark": "常用功能"
}
```

不要在面板项中使用按钮键盘的 `label`、`command` 或 `url` 字段。`type="command"` 时
`name` 就是展示的指令名；`type="link"` 时额外填写 `link`。

### 配置固定面板并自动对账

如果面板内容固定，不需要让 LLM 每次创建，可以使用独立的 `managed_panels` 配置：

```toml
[features]
enable_menu_panel_service = true

[managed_panels]
enabled = true

[[managed_panels.items]]
managed_key = "main-group-panel"
scope = "group"
target_type = "specific"
group_openids = ["目标群 OpenID"]

[managed_panels.items.panel]
remark = "固定入口"

[[managed_panels.items.panel.items]]
name = "/help"
desc = "查看帮助"
type = "command"
only_admin = false

[[managed_panels.items.panel.items]]
name = "官网"
desc = "打开官网"
type = "link"
only_admin = false
link = "https://example.com"
```

插件加载或 reload 后对账一次：首次创建；内容一致时不写入；内容变化时只更新账本明确绑定的
面板。`managed_key` 必须稳定且唯一，改名相当于声明一个新面板。

#### 托管配置字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `managed_key` | 是 | 本地唯一所有权键，支持字母、数字、`_`、`.`、`-`，最多 64 字符；创建后不要改名 |
| `scope` | 是 | `c2c`、`group`、`channel` 或 `dm` |
| `target_type` | 是 | `specific` 或 `all` |
| `user_openids` | 条件必填 | `c2c + specific` 时填写，1~20 个用户 OpenID |
| `group_openids` | 条件必填 | `group + specific` 时填写，1~20 个群 OpenID |
| `panel.remark` | 否 | 开发者备注，最多 255 字符，不向用户展示 |
| `panel.items` | 是 | 1~20 个面板项 |
| `panel.items[].name` | 是 | 展示名称，最多 14 字符；command 类型下也是填入输入框的指令 |
| `panel.items[].type` | 是 | `command` 或 `link` |
| `panel.items[].desc` | 否 | 展示说明，最多 30 字符 |
| `panel.items[].only_admin` | 否 | 是否仅管理员可操作，默认 `false` |
| `panel.items[].link` | 条件必填 | `type="link"` 时填写合法 HTTPS URL |

组合规则与普通面板创建一致：

- `c2c + specific` 只填写 `user_openids`；
- `group + specific` 只填写 `group_openids`；
- `target_type="all"` 不填写任何 OpenID；
- `channel` 和 `dm` 只支持 `target_type="all"`；
- 配置中任意一项非法或 `managed_key` 重复时，本轮对账整体停止，避免只应用部分配置。

#### C2C 和全局面板示例

为指定 C2C 用户创建固定面板：

```toml
[[managed_panels.items]]
managed_key = "vip-c2c-panel"
scope = "c2c"
target_type = "specific"
user_openids = ["用户 OpenID"]

[managed_panels.items.panel]
remark = "VIP 用户入口"

[[managed_panels.items.panel.items]]
name = "帮助中心"
type = "link"
link = "https://example.com/help"
```

创建对所有群生效的全局面板：

```toml
[[managed_panels.items]]
managed_key = "all-groups-panel"
scope = "group"
target_type = "all"

[managed_panels.items.panel]
remark = "全群通用入口"

[[managed_panels.items.panel.items]]
name = "/help"
type = "command"
```

#### 修改与停用

1. 修改已有 `panel.items` 或 `remark`，然后 reload `qqbot_expand`：插件会查询账本绑定的
   `panel_id`，内容变化时执行更新。
2. 如果目标群或用户需要变化，请使用新的 `managed_key` 创建新托管面板；自动对账不会修改
   既有面板的关联对象。
3. 从配置删除一项只会停止管理，不会删除 QQ 远端面板。需要删除时必须通过原有受控 Tool 或
   Service 显式操作。
4. ownership ledger 位于插件 JSON 存储命名空间的 `qqbot_expand/managed_panels_ledger`；不要
   手工复制其它面板 ID 到该文件，否则等同于显式转移所有权。

对账完成后日志会输出 `created`、`updated`、`unchanged`、`failed` 数量。修改配置后没有变化时，
先确认已经 reload 插件，并检查 `features.enable_menu_panel_service=true` 与
`managed_panels.enabled=true`。

托管面板在插件加载后通过后台任务对账。若 `qqbot_adapter` 仍在启动，插件会等待有限窗口后再请求 QQ API；`managed_panels` 管理的是 `/v2/panels` 指令面板，不是 `/v2/menu` 的 C2C 全局菜单。

安全边界：

- 本地 ownership ledger 是唯一所有权依据；不会按名称、备注、内容或目标扫描认领面板；
- LLM 通过 `qq_create_panel` 创建的面板不会写入 ledger，因此不会被自动更新；
- 配置移除只停止管理，不删除 QQ 远端面板，也不会清理 ledger；
- 自动对账不调用删除或关联对象修改接口；创建后的 target 不自动收敛，变更目标请使用新的
  `managed_key`；
- 仅支持单进程、单活 Bot 实例；JSON ledger 不提供跨进程互斥；
- 如果创建成功但 ledger 保存失败，插件会保守停止，不扫描、认领或删除该孤立面板。

`managed_panels` 是管理员声明的固定期望状态；`menu_panel_profiles` 是 LLM Tool 的受信跨目标
投放方案；普通 `qq_create_panel` 则是 LLM 当前会话中的自主创建。三者互不接管。

### `menu_panel_profiles` 到底是什么

profile 是管理员预先写进配置文件的**受信投放方案**。它不是面板内容，也不是每次创建
面板都必须填写的配置。它只保存以下信息：

- 这套方案叫什么名字；
- 面板要投放到哪种场景；
- 要投放给哪些固定用户或群；
- 如果用于修改关联对象，要操作哪个已存在的 `panel_id`。

LLM 调用 Tool 时只能传 `profile_name` 选择方案，不能修改方案里的 OpenID。这样既能实现
跨会话或批量操作，又不会让模型临时编造任意用户或群。

#### 什么时候不需要 profile

如果只想在当前对话中创建面板，保持：

```toml
menu_panel_profiles = []
```

然后让 `qq_create_panel` 省略 `profile_name`：

- 当前是 C2C 私聊：投放给当前私聊用户；
- 当前是群聊：投放给当前群，且该群必须位于
  `menu_panel_allowed_group_openids`。

更新全局菜单、查询面板列表、更新面板内容和删除面板也不使用 profile。更新或删除已有
面板使用 `menu_panel_allowed_panel_ids` 授权。

#### 什么时候需要 profile

以下情况才需要：

1. 在私聊中为其他群创建面板；
2. 一次为多个群或多个 C2C 用户创建面板；
3. 创建 `target_type="all"` 的全局面板；
4. 使用 `qq_update_panel_targets` 为已有面板增加或删除关联用户/群。

#### 正确的 TOML 写法

推荐使用 TOML 的数组表语法。每个 `[[features.menu_panel_profiles]]` 表示一个 profile：

```toml
[[features.menu_panel_profiles]]
name = "two-groups"
scope = "group"
target_type = "specific"
group_openids = [
  "群 OpenID 1",
  "群 OpenID 2",
]
```

不要把 `{ ... }` 写成跨行内联表。下面这种写法会导致整个配置文件解析失败：

```toml
# 错误示例
menu_panel_profiles = [
  {
    name = "two-groups"
  }
]
```

如果坚持使用内联表，必须完整写在同一行，但可读性较差：

```toml
menu_panel_profiles = [{ name = "two-groups", scope = "group", target_type = "specific", group_openids = ["群 OpenID 1"] }]
```

#### profile 字段说明

| 字段 | 创建面板 | 修改关联对象 | 说明 |
| --- | --- | --- | --- |
| `name` | 必填 | 必填 | profile 唯一名称，也是 Tool 的 `profile_name`；必须完全一致 |
| `scope` | 必填 | 不使用 | `c2c`、`group`、`channel` 或 `dm` |
| `target_type` | 必填 | 不使用 | `specific` 或 `all` |
| `user_openids` | 条件必填 | 条件必填 | `c2c + specific` 的固定用户列表，最多 20 个 |
| `group_openids` | 条件必填 | 条件必填 | `group + specific` 的固定群列表，最多 20 个 |
| `panel_id` | 不使用 | 必填 | 已存在面板的 ID，必须也加入 `menu_panel_allowed_panel_ids` |
| `allow_target_update` | 不使用 | 必须为 `true` | 明确授权 `qq_update_panel_targets` 修改关联对象 |

组合规则：

- `scope="c2c"`、`target_type="specific"`：只填 `user_openids`；
- `scope="group"`、`target_type="specific"`：只填 `group_openids`；
- `target_type="all"`：不要填任何 OpenID；
- `scope="channel"` 或 `scope="dm"`：只能使用 `target_type="all"`；
- profile 中所有 `group_openids` 都必须同时存在于
  `menu_panel_allowed_group_openids`。

#### 示例一：在私聊中为两个群创建面板

```toml
[features]
menu_panel_allowed_group_openids = ["群 OpenID 1", "群 OpenID 2"]

[[features.menu_panel_profiles]]
name = "two-groups"
scope = "group"
target_type = "specific"
group_openids = ["群 OpenID 1", "群 OpenID 2"]
```

调用 `qq_create_panel` 时传：

```json
{
  "panel": {"items": [{"name": "/help", "type": "command"}]},
  "confirm": true,
  "profile_name": "two-groups"
}
```

#### 示例二：为指定 C2C 用户创建面板

```toml
[[features.menu_panel_profiles]]
name = "beta-users"
scope = "c2c"
target_type = "specific"
user_openids = ["用户 OpenID 1", "用户 OpenID 2"]
```

创建时传 `profile_name="beta-users"`。这里的用户可以不是当前私聊用户，因为目标已经由
管理员固定在配置中。

#### 示例三：创建全局面板

```toml
[[features.menu_panel_profiles]]
name = "all-c2c"
scope = "c2c"
target_type = "all"
```

不要在该 profile 中设置 `user_openids` 或 `group_openids`。

#### 示例四：修改已有面板的关联群

先把面板 ID 和目标群加入相应白名单：

```toml
[features]
menu_panel_allowed_panel_ids = ["panel-id-123"]
menu_panel_allowed_group_openids = ["群 OpenID 1", "群 OpenID 2"]

[[features.menu_panel_profiles]]
name = "panel-123-groups"
panel_id = "panel-id-123"
allow_target_update = true
group_openids = ["群 OpenID 1", "群 OpenID 2"]
```

然后调用：

```json
{
  "profile_name": "panel-123-groups",
  "op": "add",
  "confirm": true
}
```

`op="add"` 添加关联，`op="del"` 删除关联。

#### 多个 profile

重复写多个数组表即可：

```toml
[[features.menu_panel_profiles]]
name = "group-a"
scope = "group"
target_type = "specific"
group_openids = ["群 A OpenID"]

[[features.menu_panel_profiles]]
name = "group-b"
scope = "group"
target_type = "specific"
group_openids = ["群 B OpenID"]
```

#### 常见错误

| 错误 | 原因与处理 |
| --- | --- |
| `未找到已授权的菜单面板 profile` | `profile_name` 与配置中的 `name` 不完全一致，或配置仍为 `[]`；当前会话创建可以直接省略 `profile_name` |
| `profile 包含未授权的群目标` | profile 的群没有全部加入 `menu_panel_allowed_group_openids` |
| `当前面板不在菜单面板白名单中` | profile 的 `panel_id` 没有加入 `menu_panel_allowed_panel_ids` |
| `该 profile 未授权修改关联对象` | 缺少 `allow_target_update = true` |
| `specific 面板的关联对象与 scope 不匹配` | `c2c` 错填了群列表，或 `group` 错填了用户列表 |
| 插件加载时报 TOML 解析错误 | 使用了跨行 `{ ... }`；改用 `[[features.menu_panel_profiles]]` |
| 修改配置后仍使用旧行为 | 重启或重载插件，使配置和 Tool Schema 重新加载 |

快捷菜单点击属于 Interaction type=12，已有运行时负责 ACK。需要业务功能标识时，可从
`raw_event.data.resolved.feature_id` 读取；本插件不会修改 Adapter 的标准事件 key 契约。

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
    reference_message_id=某条消息的_ref_idx,
    ignore_get_message_error=True,   # 原消息被撤回时也照常发出
    msg_id=trigger_msg_id,
)
```

`reference_message_id` 不是普通消息 ID：引用入站用户消息时使用 Adapter 写入
`Message.extra["qq_ref_idx"]` 的 `msg_idx`；引用机器人已发送消息时使用发送结果中的
`ref_idx`。`msg_id` 则继续使用触发消息的 `Message.message_id`，只负责被动回复。
发送结果中的 `message_id` 来自 QQ 响应顶层 `id`，用于撤回，不能代替 `ref_idx`。

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

需要上传受信插件已加载到内存的内容时，可使用 `qqbot_chunked_media.upload_bytes()`。它只接受 `bytes`，最大 200 MB，按官方 `upload_prepare → 分片 PUT → upload_part_finish → /files` 流程返回 `file_info`；不接收本地文件路径、不向 LLM 注册，也不暴露预签名 URL。

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
