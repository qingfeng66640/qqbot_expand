# 更新日志

## 0.6.0

- 新增声明式托管指令面板：通过配置声明固定命令和 HTTPS 链接，插件加载或 reload 后自动创建、比对和更新。
- 新增本地 ownership ledger，自动对账只操作明确绑定的面板，不扫描、认领、覆盖或删除 LLM 自主创建的面板；配置移除只停止管理。
- 修复嵌套 `managed_panels` 配置在框架自动回写后被字符串化并清洗为空列表的问题。
- 修复托管面板对账早于 `qqbot_adapter` 启动导致首次创建永久失败的问题；Adapter 未就绪时进行有限、可取消的后台重试。
- 完善 `managed_panels`、`menu_panel_profiles` 和菜单面板 Tool 的配置字段、TOML 示例、安全边界及故障说明。

## 0.5.0

- 新增 `qqbot_menu_panel` Service，封装 QQ 自定义菜单与指令面板的查询、创建、更新、删除和关联对象管理。
- 新增 7 个受控菜单/面板 Tool；默认不注册，写操作需独立开关、白名单和 `confirm=true`。创建面板默认使用当前会话目标，跨目标/批量操作使用受信 profile。
- 菜单、面板、按钮、Ark 和群管理 Tool 改用结构化输入 Schema，向模型明确展示嵌套字段和枚举，避免混淆面板的 `name/link` 与键盘的 `label/url`。
- 菜单和面板输入按 QQ 平台限制白名单重建，拒绝未知字段、非 HTTPS 链接、非法 scope/目标组合和越界数量。
- 菜单点击的 `feature_id` 可从原始 Interaction 事件安全提取，不修改 Adapter 事件契约。
- 消息发送结果新增 `ref_idx`，引用回复改用 QQ `msg_idx/ref_idx`，并与撤回使用的 `message_id` 区分。
