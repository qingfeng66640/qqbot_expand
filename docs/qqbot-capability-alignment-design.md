# QQ 插件能力补齐清单与修复设计

## 2026-11-17 归档复核与责任矩阵

本节以当前工作树和本地官方归档为准，后续章节保留 2026-11-04 的设计历史，不能把其中旧版本、拟定契约或旧“已有支持”描述当成当前验收结果。当前工作树版本为 Adapter 1.8.1 / Expand 0.6.2，本轮不升版、不暂存、不提交、不打包、不发布。

事实来源为 `docs/qqbot_official/develop/api-v2/changelog.md` 及对应详细页：归档抓取时间为 2026-11-17 18:26:32–34 +08:00，changelog 的最后语义更新日期为 2026-11-16，来源为 `https://bot.q.qq.com/wiki/develop/api-v2/changelog.html`。本轮只读本地归档，未联网核验官网最新状态，也未修改归档原件。下表官方路径相对于 `docs/qqbot_official/develop/api-v2/`，实现与测试路径相对于各自插件。

| 官方变化 / 详细页 | 实际差距与当前状态 | 归属及实现 | 真实链路测试 / 本轮处理 |
| --- | --- | --- | --- |
| 用户信息 `autogen/api/v2_users_user_openid_info.get.md` | 原无专用能力；已新增受信 Service，不是以 raw 透传冒充 | Expand：`services/user_info_service.py`、`src/user_info.py`；公共 Adapter 认证，插件实例共享滚动 10 QPS，单次物理请求，不缓存资料 | `tests/test_user_info_service.py`、`test_user_info_discovery_logs.py`；正常、缺字段/错误类型、无好友、401/429/5xx、超时、取消和共享额度；`test_http_diagnostics.py` / `test_http_inflight_diagnostics.py` 验证请求、客户端及在途/流式响应租约的日志隐私，最终审查结论见本轮交付报告 |
| C2C 昵称/头像/union：`autogen/event/c2c_message_create.md` | 原仅有路由 ID；已补齐新旧事件兼容 | Adapter：`src/handlers/inbound.py`、`src/known_users.py`；本地别名优先，平台资料放 `extra.qq_user`，头像元数据不下载 | `tests/test_profile_metadata.py`；缺失/空/错误类型、Unicode、有效缓存保留、禁止 C2C 依赖群昵称 fallback |
| 群 @ / 普通消息：`autogen/event/group_at_message_create.md`、`group_message_create.md` | 已补齐两入口资料及群关联标识；不能只覆盖 @机器人 | Adapter：同一入站转换；`member_openid` 与 `user_openid` 分场景，群内昵称留在群作用域 extra，不写全局 Person/cardname | 同上及 `tests/test_inbound.py`；跨群隔离、原路由不变、`extra.qq_group.union_group_openid` 可选 |
| 进退群/成员增减：`autogen/event/group_add_robot.md`、`group_del_robot.md`、`group_member_add.md`、`group_member_remove.md` | 机器人进退群原无公开出口；现四类均保留 union 与 raw 深拷贝 | Adapter：公开 `qqbot_adapter.group_add_robot/group_del_robot/group_member_add/group_member_remove` EventBus，不生成聊天或管理动作 | `test_profile_metadata.py`、`test_coverage_handlers.py`；真实公开事件与旧事件兼容 |
| 群基本信息 `autogen/api/v2_groups_group_openid_info.get.md` | 已有返回透传；补真实消费链证据，不重复开发 | Expand：`services/group_info_service.py` → `tools/group_info.py`；可选 `union_group_openid` 不替代目标 | `tests/test_group_union_contract.py`：Service→Tool→HTTPX MockTransport，缺省/空/有效 |
| 群 @全体：`autogen/api/v2_groups_group_openid_messages.post.md` | 原只有标签 builder，缺完整授权业务；已增显式 Service，普通出口拒绝隐式触发 | Expand：`send_group_mention_everyone(confirm=True)`；每次真实 admin 预检，QQ 每群自然日一次，禁自动重试、不自动降级，无默认 Tool；Adapter 只做普通传输防护 | `tests/test_group_everyone.py`、Adapter `test_everyone_transport.py`；含中文紧邻旧标签、未知结果、取消及载荷并发改写；`src/everyone.py` 首次 await 前 JSON 快照确保校验与发送一致 |
| 图文卡片 / 入站 `ark_data`：群与 C2C messages 详细页 | 已完整实现且有测试，本轮保留并回归，不重复开发 | Expand `src/card_builder.py`、`send_tuwen_card()`；Adapter `src/messaging/ark_payload.py` 仅协议转换 | Expand `tests/test_tuwen_card.py`；Adapter `tests/test_ark_payload.py` |
| Markdown 图片强校验：messages 的 `MessageMarkdown` | 已实现在 `markdown` 内而非根级；本轮保留并回归 | Expand `src/builders.py`、`services/message_service.py` | `tests/test_markdown_request_contract.py`；原生/模板、群/C2C 的最终请求体 |
| 素材 prepare/PUT/complete/list/delete 详细页 | 已有专用能力及安全约束；不是仅 raw，本轮不重复开发 | Expand `material_service.py`、`src/material_*`；独立 COS 传输，不复制 TokenManager | `tests/test_material_*.py`；SSRF、图片校验、取消、一次性写入和资源释放；官方 10 MB 与实现 10 MiB 单位差异保留为歧义 |
| 批量移除 `autogen/api/v2_groups_group_openid_batch_remove_members.post.md` | 新归档仅列 member_openids；旧移除+拉黑组合与新文档不符，已按用户决定分离 | Expand `group_members_service.py`、`tools/group_members.py`；移除与黑名单独立，旧 True 显式失败且不部分执行；40103011/40103012 分类为操作权限关闭 | `tests/test_latest_document_contracts.py`；真实 Service/Tool 请求与权限码，不把旧参数继续发往 QQ |
| 分享链接 `autogen/api/v2_generate_url_link.post.md` | 新旧页均未列 url_link；仅 callback_data ≤32，已修复 | Expand `utility_service.py`、`tools/utility.py`；旧非空 url_link 清晰报错且不请求；保留官方 data.url 嵌套 | 同上，覆盖兼容拒绝、参数构造及实际响应形状 |
| API 错误约定 `dev-prepare/api-call-guide.md` | 原共享 bridge 会把部分 2xx 非零 err_code 当成功；已修复，不以 message 为判错条件 | Expand `src/errors.py`、`src/bridge.py`；Adapter 委托和插件 HTTP 两路统一安全失败，合法零码/成功嵌套不变 | `tests/test_business_error_contract.py`；Service/Tool、201/202、两种码、有/无 message、隐私 canary；红测后转绿 |
| 自动审批策略列表 `autogen/api/v2_groups_join_approval_strategy.get.md` | 原上限 100 与详细页 50 不符，已修复 | Expand `group_admin_service.py`：默认 20，上限 50；不误改创建关联 100 群、黑名单单页 100 | `tests/test_coverage_group_services.py`；51/非法类型认证与 HTTP 前拒绝，50 正确 GET；红测后转绿 |
| 菜单、指令面板、按钮、流式、引用 | 已有专用实现及测试；权限/平台类型条件仍适用，不等于全部默认开放 | Expand 菜单面板与 Interaction 业务；Adapter Gateway、输入状态/流式、引用和传输 | 既有菜单面板/Interaction/streaming/ref_idx 回归；不新增竞争发送链 |
| C2C `is_wakeup` / 互动召回 | 只有 raw API 通道，尚无专用召回策略；非 11/16 新增项 | Expand 业务边界，保留 E4 延期，不自动生成召回或将偏好解释为许可 | 不宣称正式封装完成；用户偏好 allow_proactive_msg 不代表自动发送授权 |
| C2C 被动窗口 / 群 content / Markdown 弃用标记 | 文档有歧义或兼容语义，不能猜测线上契约 | C2C 概览 60 分钟、msg_id 仍 5 分钟：保留既有行为并明示；群 content 当前选填，Markdown 时为空，保留兼容空串；模板字段弃用不直接删接口 | 见 `qq-platform-notes.md`；主动频控改按当前 Bot/单关系规则，不再写普通主动“每月 4 条” |

身份边界：所有 union 标识仅作元数据，不能路由、改会话主键、改撤回/回复目标或自动合并跨应用身份。跨插件只用公开 Service/协议，不导入对方源码、不读私有字段；新查询和 @全体业务不默认开放给 LLM。框架原有并发 stop/start 竞态不在本轮范围，未修改框架。

本节记录实现与责任，完整验收结果以本轮最终报告中的实际命令和覆盖率清单为准；不能由表中“已有实现”推导真实 QQ 权限已开通或已验证线上可用。

---

## 2026-11-04 历史设计（以下保留原文）

日期：2026-11-04。状态：E1/E2/A1/E3 已实施并完成离线回归及定向审查，D1 已定向同步；D2 由用户另派、未改动，E4 延期。尚未提交、发布或执行真实 QQ 生产验证。

## 1. 目标、依据与边界

目标：对齐近期 QQ 官方消息能力，同时保持 `qqbot_adapter` 与 `qqbot_expand` 职责分离。

- Adapter：连接、鉴权、公共传输、平台事件接收、协议字段与框架消息的转换。
- Expand：QQ 特有的扩展能力、字段校验、发送编排、素材生命周期、受控业务接口。
- 不修改 `src/kernel/`、`src/core/`、`src/app/`；不为 QQ 专有能力新增框架全局消息类型。
- 不把新图文卡片发送、素材管理或召回策略塞进 Adapter；不在 Expand 复制 token 获取与刷新逻辑。
- 不用“raw 接口能够透传”替代正式封装、参数验证和测试。

已核对基线：Adapter 1.8.0；Expand 工作树 0.6.1；官方归档生成于 2026-10-27。
官网变更记录已增加 20261028 的群聊/单聊图文卡片。素材管理、输入状态、互动召回等属于旧能力覆盖补齐，不应记为本次全部新增。

本设计中的新接口、配置名和文件名是拟定契约，实施前仍需确认；代码引用为制定设计时的位置。
生产请求体与真实账号权限尚不可用，不能把本设计当作原 `40030001` 故障的精确根因证明。

## 2. 补齐清单

| 编号 | 优先级 | 归属 | 工作项 | 完成标准 |
| --- | --- | --- | --- | --- |
| E1 | P1 | Expand | 修正 `force_verify_image_resource` 的 JSON 层级 | 标志只位于 `markdown` 内；两个现有发送入口都有真实请求体断言 |
| E2 | P1 | Expand | 新图文卡片 `msg_type=8/card` | builder、发送 Service、校验、返回结果与离线测试齐全 |
| E3 | P2 | Expand | Markdown 图片素材管理 | 完整 prepare/PUT/complete、分页查询、显式删除与安全边界 |
| A1 | P2 | Adapter | 入站 `ark_data` 结构化转换 | C2C/群消息保留有界元数据，不改变已有正文和附件行为 |
| D1 | P1，随 E1/E2 | 官方归档及插件文档 | 定向补充新卡片及接口契约说明 | 保留来源、真实抓取时间；不能把局部刷新标记成全站刷新 |
| E4 | 可选，延期 | Expand | C2C `is_wakeup` 正式封装 | 单独确认召回策略、互斥规则和权限后设计，不在首批实现 |
| D2 | 可选，独立维护 | 官方归档脚本 | 修正校验失败退出码、缺页漏检和历史条件耦合 | 另行立项，不混入两个插件的功能提交 |

已有能力不重复开发：@全体标签、按钮 style=3/4/group_id/modal 的 builder、群成员及黑名单管理、禁言最多20人、入群申请分页最多50、入群申请事件与审批、统一域名、流式 text/Markdown、引用索引。

输入状态已有 Adapter 流式底层实现，不新增一套竞争的默认发送链路。简化 Tool 未开放全部 builder 参数，不自动认定为缺陷。

## 3. E1：图片转存强校验修复

### 问题与证据

群聊、单聊接口的 `MessageMarkdown` 均包含 `force_verify_image_resource`。
当前 `services/message_service.py:456`、`:621` 把该字段写在请求顶层，与文档结构不一致。
这是独立的既有契约差异，不据此声称它导致了此前的面板创建错误。

### 修复方式

1. 保留 `send_keyboard()`、`send_markdown_template()` 的公共参数及默认值 `False`。
2. 为 `build_markdown()` 增加 keyword-only 参数 `force_verify_image_resource: bool = False`。
3. 在 builder 内校验布尔类型；为 True 时写入返回的 Markdown 对象，为 False 时保持原有最小请求体。
4. 两个 Service 入口把参数传给 builder，删除根级写入。原生 Markdown 和模板 Markdown 均使用这一条组包规则。
5. 不修改 raw 调用者自行构造的 payload，不新增 Adapter 配置或自动重试。

目标请求形状：

```json
{
  "msg_type": 2,
  "markdown": {
    "content": "![示例](https://example.com/image.png)",
    "force_verify_image_resource": true
  }
}
```

### 验收

- 原生/模板 Markdown、群聊/C2C 的发送请求均断言嵌套位置，且根级字段不存在。
- False 时省略字段；字符串、整数、None 等非法参数在网络调用前失败。
- keyboard、被动回复字段、msg_seq、错误脱敏、message_id/ref_idx 行为不变。
- 不只测 builder，必须检查经真实 Service 组装后传给传输替身的最终 JSON。

预计文件：`src/builders.py`、`services/message_service.py`、`tests/test_builders.py`、新增 `tests/test_markdown_request_contract.py`，以及对应使用/API文档。

## 4. E2：新图文卡片

### 对外契约

复用现有 `qqbot_expand:service:qqbot_message`，拟新增：

```python
async def send_tuwen_card(
    self,
    target_type: str,
    target_id: str,
    title: str,
    description: str,
    url: str,
    pic_url: str,
    *,
    msg_id: str = "",
    event_id: str = "",
    msg_seq: int | None = None,
) -> dict[str, Any]: ...
```

`target_type` 延续现有 `user/group`，不是官方 scope 的 `c2c/group`。
返回值复用消息 Service 的 `success/message_id/ref_idx/error` 语义，不能误用 bridge 的 `success/data/error` 作为发送结果。

首版只提供受信 Service，不默认注册新 LLM Tool。未来若需要 Tool，另行设计当前会话目标约束、开关和权限，不允许模型随意指定跨群/跨用户 OpenID。

### 组包与实现

- 新增 `MSG_TYPE_CARD = 8`；不要重用 Ark 的 `MSG_TYPE_ARK = 3` 或 embed 的 4。
- 新增纯函数模块 `src/card_builder.py`，负责白名单字段及卡片校验。
- `card` 结构为 `{"type": "tuwen", "content": {"title", "description", "url", "pic_url"}}`。
- 标题1～50字符，描述1～200字符；URL、图片URL均须合法 HTTPS，长度不超过1024字符，拒绝userinfo、控制字符及非法URL。
- 卡片文档没有声明菜单式“中文计2”算法；不复用菜单展示字符计数。先按该接口自身契约实现并记录Unicode边界，发现官网说明冲突时再处理，不擅自截短。
- 经 `_guard_and_send()`、`_apply_passive_fields()`、`_send()` 复用现有目标校验、序号、传输、近期发送登记和引用索引提取。
- 原有群消息发送链路补充空 `content` 的行为继续沿用；不把卡片再转成普通文本。
- Adapter 不新增 `send_tuwen_card`，继续作为公共 token/POST 传输提供方。
- 旧 Ark 模板23/24继续保留，不把已有 `style="card"` 静默改成新协议。

目标业务结构：

```json
{
  "msg_type": 8,
  "card": {
    "type": "tuwen",
    "content": {
      "title": "更新通知",
      "description": "查看本次更新内容",
      "url": "https://example.com/detail",
      "pic_url": "https://example.com/cover.png"
    }
  }
}
```

### 验收

- 群聊/C2C路径及 msg_type/card 层级正确，不能出现 Ark模板字段。
- 文本空值、上限/上限+1、混合Unicode、URL协议、userinfo和长度边界均有测试。
- 非法参数不调用传输；合法发送保留 message_id、ref_idx 和近期消息登记。
- 现有 msg_id/event_id 互斥与msg_seq规则保持不变；HTTP错误不自动降级为Ark或文本。
- 旧Ark、embed、Markdown、media发送的现有测试不受影响。

预计文件：`src/constants.py`、新增 `src/card_builder.py`、`services/message_service.py`、新增 `tests/test_tuwen_card.py`、插件README/API/使用说明。

## 5. E3：图片素材管理

### 组件与接口

拟新增 `QQBotMaterialService`：

- 签名：`qqbot_expand:service:qqbot_material`。
- 使用标准 `name` / `description`，继承框架 Service 构造契约，不覆写不兼容的 `__init__`。
- 由插件注册组件、在 manifest.include 同步元数据；不通过源码import另一个插件。

拟定方法：

```python
async def upload_image(
    self, file_name: str, content: bytes, content_type: str
) -> dict[str, Any]: ...

async def list_images(self, *, cursor: str = "") -> dict[str, Any]: ...

async def delete_image(
    self, image_id: str, *, confirm: bool = False
) -> dict[str, Any]: ...
```

返回值使用 `success/data/error`。素材引用与消息发送结果分开，不把 image_id 当成 message_id。
首版接收调用方提供的 bytes，不接受本地路径、任意下载URL或模型提供的预签名URL；不新增素材上传/删除 LLM Tool。

### 流程与职责

1. Expand 校验内容，计算 MD5/SHA1，使用现有 `api_request()` 发起 prepare。
2. Expand 验证 prepare 返回的 upload_id、expires_in、presigned_url；预签名URL仅在调用内存活。
3. Expand 的专用上传helper将 bytes 直传对象存储，不携带QQ Authorization、cookie或默认鉴权头。
4. 从PUT响应取得并规范化ETag，与预期MD5核对；不匹配或缺失时停止，不继续complete。
5. Expand调用complete，验证 image_id 和 `qqbot-media://image/v1/` 引用，返回白名单结果。
6. 查询按游标单页返回，调用者决定是否继续分页；删除需显式开关及confirm，不自动扫库删除。

JSON控制面继续复用Adapter公开token。默认POST仍走其公共出口；由于该出口内部会重试网络异常，素材prepare/complete显式禁重试时走Expand现有客户端的一次性请求路径，HTTP401也不重放。素材生命周期及平台专属流程留在Expand。
二进制PUT沿用当前Expand已有对象存储直传的职责位置，但使用独立安全helper，不借机重构Adapter为素材业务服务。未来若多处确需统一二进制传输，可单独下沉纯技术能力，不在本轮打包改造。

### 限制与安全

- 依据官方：单图不超过10MB，png/jpeg/gif/webp，上传任务4小时，单机器人100张，引用90天滑动有效期。大小阈值集中成常量并记录采用的字节口径，不能套用现有200MB分片上传上限。
- 文件名只接受basename；扩展名、Content-Type和可识别的图片格式一致。拒绝目录穿越、非法内容、伪装类型；优先复用现有图片依赖，不引入新系统软件。
- 必须区分此流程与已有 `qqbot_chunked_media` 的 `upload_prepare/upload_part_finish/files`。
- presigned_url仅接受受信prepare响应；检查HTTPS、标准端口、禁止userinfo、受控COS主机及非私网地址，禁用重定向。实现时要核实合法COS域名规则，不能简单允许任意HTTPS，也不能只检查一次DNS后忽略连接目标。
- 现有分片服务仅做HTTPS前缀检查，不把它当作安全校验已充分的依据；新增helper不自动改写旧分片路径。
- 上传客户端禁用继承QQ鉴权；清理默认header、cookie、代理继承等泄漏途径，不记录或向Tool返回presigned_url、preview_url。
- 不持久化预览URL，不把“90天有效”做成从首次上传算起的固定TTL，不后台自动刷新或清理素材。
- 首版不做本地长期素材缓存；运行资源及并发限制挂插件实例，不挂每次新建的Service实例。
- 顺序await完成操作，不额外创建后台任务；取消要传播并释放资源。插件卸载关闭新增客户端。
- 图片廉价输入校验先在本进程执行，真实解码通过框架CPU池调度标准库固定worker入口，以兼容动态目录加载器的spawn导入。worker从可信插件实际路径加载纯模块，bytes走stdin，无shell/eval/临时文件或全局sys.path修改；最长15秒，取消后不进入HTTP，已运行纯计算可有界收尾。
- 本地增加1024帧与89,478,485累计解码像素预算；不把这些本地工作量限制冒充QQ协议上限。
- prepare/complete不添加盲目重试。超时导致远端结果未知时明确返回失败/结果未知，不假定未创建、不自动重传或补偿删除。
- 不自动重放带副作用的删除；提供有界超时、有限并发及取消测试。

拟定配置（均为新增且默认安全）：`features.enable_material_service=false`、`features.allow_material_delete=false`。
其他HTTP超时优先复用现有配置；上传并发采用命名常量，确有运维需求再开放配置。

### 验收

- prepare/PUT/complete正常流程、ETag校验、响应缺字段、非JSON、大小/格式/文件名边界均有离线测试。
- prepare失败不PUT，PUT失败不complete，complete结果未知不自动重建或删除。
- 私网/非HTTPS/恶意主机、重定向、跨host默认鉴权泄漏和日志泄漏均有反例测试。
- list只请求一页；delete需显式启用及confirm，目标路径编码正确；超限不自动删他人素材。
- 客户端使用插件生命周期，取消及卸载不遗留任务或连接。

预计文件：新增 `services/material_service.py`、`src/material_upload.py`、`tests/test_material_service.py`、`tests/test_material_upload.py`；更新 `services/__init__.py`、`plugin.py`、`config.py`、`manifest.json` 和插件文档。

## 6. A1：Adapter 入站卡片结构化转换

### 目标与数据契约

当前C2C/群消息通过content与attachments构造段，未专门消费ark_data：
`src/handlers/inbound.py:321`、`:408`、`:444`。
已有摘要文本仍可能可读，本项不是断言所有卡片均完全丢失。

拟新增纯规范化模块 `src/messaging/ark_payload.py`，由入站handler调用。
采用已有 `message_info.extra` 透传，不新增全局消息段类型，不改核心转换器。

拟定元数据key为 `qq_ark`：

```json
{
  "schema_version": 1,
  "ark_type": "picture",
  "ark_name": "图片",
  "prompt": "图片摘要",
  "fields": {"title": "标题", "preview": "https://example.com/image.png"}
}
```

- 只保留文档明确的有界字段；约束对象层级、字段数量、字符串长度和整体体积，不保存完整网关payload。
- 未知ark_type作为不透明类型保留，不能把“未知”误判为普通图片或丢弃整条消息。
- content/attachments/mentions/时间戳/被动回复ID/qq_ref_idx保持原有行为。
- 仅当没有可用正文时生成简短卡片摘要，避免重复平台已给出的content。
- picture已有attachments时仍走现有附件解析，不重复下载；只有preview时先保留元数据，不自动抓取任意URL，也不虚报已获得可供视觉识别的图像。
- 该结构是非可信用户内容，不能当作系统指令、可执行命令或已审核URL。
- 不把发送端的msg_type=8编号直接套到入站message_type解释，两者按各自官方契约处理。
- Expand首版无需消费此元数据；两个插件可以独立升级。

### 验收

- C2C、GROUP_AT_MESSAGE_CREATE、GROUP_MESSAGE_CREATE都保留qq_ark。
- tuwen/feed/picture/miniapp及未知类型均有fixture，错误对象/超大字段不导致整条接收失败。
- 既有content不重复、attachment不重复下载，空正文时才补摘要。
- 通过框架真实converter检查extra最终仍可读，原qq_ref_idx与消息ID不变。
- 核心库与Expand不新增入站协议解析代码。

预计文件：新增 `src/messaging/ark_payload.py`、`tests/test_ark_payload.py`，更新 `src/handlers/inbound.py`、`tests/test_inbound.py` 及Adapter相关说明。

## 7. D1/D2：文档与校验工具

### 本轮需要的文档对齐

定向更新官方归档的以下原始页面：

- `develop/api-v2/changelog.md`：补充20261028。
- `develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.md`。
- `develop/api-v2/autogen/api/v2_users_user_openid_messages.post.md`。

只按官网实际内容更新，保留来源和真实单页抓取时间；必要时更新对应元数据，不能更改整体生成时间冒充全站重抓，也不运行重建日志脚本制造新的执行证据。

插件README、API/使用说明、CHANGELOG应准确区分：新图文card与旧Ark；素材URI与file_info；类型化Service与raw透传；Adapter底层与Expand扩展。没有新增Tool时，不把Service能力写成LLM已可直接调用。

### 独立维护项D2

已有只读审计指出 `_verify.py` 报告失败后仍退出0，成功记录对应文件缺失时可能被跳过；固定日期、数量及历史日志条件不适合作为可复用门禁。
建议独立修复：失败返回非零、成功记录逐项检查落盘、通用检查与历史验收分离。它不属于Adapter或Expand的新功能，不与功能提交混在一起。

## 8. E4：延期的互动召回

`is_wakeup` 在旧归档中已经存在，不是10月28日新增。首批不实现自动召回任务。
若后续需要：由Expand提供C2C专用显式Service参数，校验与msg_id/event_id互斥，说明平台召回周期和额度；不允许默认主动发送，不在Adapter中引入运营策略，不让LLM绕过权限任意召回用户。

## 9. 实施顺序与提交隔离

1. 保留当前Expand的14个暂存文件及0.6.1版本准备状态。本设计文档不自动暂存；实施前核对HEAD、index和工作树，确定既有修复的提交边界，不擅自撤销或混入新功能。
2. 第一批E1：独立修复Markdown组包契约及测试，低风险先完成。
3. 第二批E2：图文卡片Service与文档；不引入素材管理依赖，不注册默认LLM Tool。
4. 第三批A1：在Adapter独立仓库实施入站转换，单独测试与审查；可与E2并行，但文件所有权分离。
5. 第四批E3：完整素材管理及二进制上传安全评审，避免只实现prepare而没有complete/查询/删除的半成品。
6. D1随对应实现提交；D2和E4单独确认后再进入实施。

拟定提交标题（仅建议）：

- `fix(qqbot_expand): 修正 Markdown 图片强校验字段层级`
- `feat(qqbot_expand): 支持群聊和单聊图文卡片`
- `fix(qqbot_adapter): 保留入站卡片结构化信息`
- `feat(qqbot_expand): 添加 Markdown 图片素材管理`

版本仅提出建议、不执行：已有0.6.1补丁准备不重复升级；新增Service能力适合后续Expand minor版本，Adapter兼容性解析修复适合patch。实际版本及commit、build、tag、push、发布需分别授权。

## 10. 总体验收门槛与不做事项

- 每批先补失败回归，再实现；覆盖新增/修改分支，不用替身绕开被测Service和真实组包路径。
- Ruff、各插件全量单元测试、跨插件回归、diff检查；失败项和跳过项逐条保留，不能把缺依赖跳过当作通过。
- 用真实PluginManager检查目录插件注册/卸载；默认抑制业务生命周期和网络副作用。新Service需按框架 `service_cls(plugin=...)` 调用方式验证。
- 新增组件在实际get_components与manifest中一致，默认关闭行为有测试；客户端等资源不依赖Service单例。
- 离线测试禁止真实QQ写请求；需要生产验证时，先由用户确认账号、目标会话及具体操作，再执行，不以生产试错替代payload契约测试。
- 不修改框架、现有菜单所有权账本、用户生产配置；不自动删远端素材或面板；不静默回退消息类型。
- 本设计不执行代码修复、版本修改、暂存、commit、打包、推送或发布。原面板400的具体非法字段仍需生产请求体确认。

## 11. 官方依据

- 变更记录（20261028图文卡片）：https://bot.q.qq.com/wiki/develop/api-v2/changelog.html
- 群聊发送、MessageCard与MessageMarkdown：https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html
- 单聊发送、输入状态及召回：https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html
- 图片素材上传、查询、删除与约束：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/material-management.html

核对范围是变更记录、接口索引/站点图及相关关键页面，不是全站所有页面的逐字段审计；文档可变，实施前应复核相关接口的实际最新版本。
