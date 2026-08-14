# 更新日志

## 0.5.0

- 新增 `qqbot_menu_panel` Service，封装 QQ 自定义菜单与指令面板的查询、创建、更新、删除和关联对象管理。
- 新增 7 个受控菜单/面板 Tool；默认不注册，写操作需独立开关、白名单和 `confirm=true`。创建面板默认使用当前会话目标，跨目标/批量操作使用受信 profile。
- 菜单、面板、按钮、Ark 和群管理 Tool 改用结构化输入 Schema，向模型明确展示嵌套字段和枚举，避免混淆面板的 `name/link` 与键盘的 `label/url`。
- 菜单和面板输入按 QQ 平台限制白名单重建，拒绝未知字段、非 HTTPS 链接、非法 scope/目标组合和越界数量。
- 菜单点击的 `feature_id` 可从原始 Interaction 事件安全提取，不修改 Adapter 事件契约。
- 消息发送结果新增 `ref_idx`，引用回复改用 QQ `msg_idx/ref_idx`，并与撤回使用的 `message_id` 区分。
