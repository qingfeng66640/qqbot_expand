# 后续能力路线图

本文记录 QQ 官方 Bot API 的已识别扩展能力及实施顺序。除标明“已完成”的项目外，均为待处理事项，**不代表当前版本可用**。

## 已完成

- 按钮、ARK、Embed、模板 Markdown、引用回复、URL 富媒体上传与发送；
- Interaction callback 的 EventBus 消费、ACK 去重与 `event_id` 回复；
- 入群自动审批策略、人工入群审批和成员禁言 Service；
- 受群白名单限制的入群审批与禁言 LLM Tool；
- 当前群信息、Bot 群内状态 Service 与仅从当前会话推导目标的查询 Tool；
- 仅可撤回本插件两分钟内发送消息的 Service/确认 Tool，以及机器人分享链接 Service/受控 Tool；
- 受信调用方提供 bytes 的官方分片媒体上传 Service；
- `qqbot_adapter.group_join_request` 的去重消费和受信回调基础设施（默认不自动审批）。

## 后续强化项

- [ ] 在具备 QQ 平台凭据的集成环境验证群信息/Bot 状态接口的白名单权限；
- [ ] 在真实大文件环境验证分片上传的预签名 URL、超时与平台限额；
- [ ] 在真实 Gateway 环境验证 `GROUP_JOIN_REQUEST` 事件投递、管理员权限与回调编排。

## 每阶段验收

每个阶段均需覆盖 Service/Tool 安全边界、路径编码、官方限制和失败情形；完成后运行完整测试、Ruff、编译、MPDT 检查与差异格式检查。阶段验收通过后才递增版本、提交和发布。
