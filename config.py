"""QQ Bot Expand 插件配置定义。

镜像 qqbot_adapter 的 HTTP 连接池默认值，保证两侧行为一致；
另外提供功能开关，控制精选 Tool 的注册与 raw 通道的访问范围。
"""
from __future__ import annotations

from typing import ClassVar, Literal

from typing_extensions import TypedDict

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class ManagedPanelItemConfigRequired(TypedDict):
    """声明式托管面板项目的必填字段。"""

    name: str
    type: Literal["command", "link"]


class ManagedPanelItemConfig(ManagedPanelItemConfigRequired, total=False):
    """声明式托管面板中的单个项目。"""

    desc: str
    only_admin: bool
    link: str


class ManagedPanelContentConfigRequired(TypedDict):
    """声明式托管面板内容的必填字段。"""

    items: list[ManagedPanelItemConfig]


class ManagedPanelContentConfig(ManagedPanelContentConfigRequired, total=False):
    """声明式托管面板的展示内容。"""

    remark: str


class ManagedPanelConfigRequired(TypedDict):
    """声明式托管面板的必填字段。"""

    managed_key: str
    scope: Literal["c2c", "group", "channel", "dm"]
    target_type: Literal["all", "specific"]
    panel: ManagedPanelContentConfig


class ManagedPanelConfig(ManagedPanelConfigRequired, total=False):
    """一项由本插件持有所有权的声明式面板。"""

    user_openids: list[str]
    group_openids: list[str]


class QQBotExpandConfig(BaseConfig):
    """QQ Bot 扩展能力插件配置"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "QQ Bot 扩展能力插件配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件基本配置"""

        enabled: bool = Field(
            default=True,
            description="是否启用 QQ Bot 扩展能力插件",
            label="启用插件",
            tag="plugin",
        )
        config_version: str = Field(
            default="1.0.0",
            description="配置文件版本",
            label="配置版本",
            disabled=True,
            tag="general",
        )

    @config_section("features", title="功能特性", tag="general")
    class FeaturesSection(SectionBase):
        """功能开关配置"""

        enable_tools: bool = Field(
            default=True,
            description="是否向 LLM 注册精选 Tool（发按钮菜单 / ark 卡片 / 引用回复）",
            label="启用 LLM 工具",
            tag="general",
        )
        allow_raw_request: bool = Field(
            default=True,
            description="是否允许通过 qqbot_raw 服务直接调用任意 QQ 开放 API",
            label="启用 raw 通道",
            tag="security",
            hint="关闭后 qqbot_raw.request() 一律拒绝，仅保留 get_status()",
        )
        raw_allowed_methods: list[str | int] = Field(
            default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE"],
            description="raw 通道允许使用的 HTTP 方法",
            label="raw 允许方法",
            input_type="list",
            item_type="str",
            tag="security",
            depends_on="allow_raw_request",
            depends_value=True,
        )
        enable_utility_tools: bool = Field(
            default=False,
            description="是否向 LLM 注册消息撤回与机器人分享链接工具",
            label="启用实用 LLM 工具",
            tag="security",
            depends_on="enable_tools",
            depends_value=True,
        )
        enable_group_info_service: bool = Field(
            default=True,
            description="是否允许调用只读群信息与机器人群内状态 Service",
            label="启用群信息 Service",
            tag="general",
        )
        enable_group_info_tools: bool = Field(
            default=False,
            description="是否向 LLM 注册当前群信息与机器人状态查询工具",
            label="启用群信息 LLM 工具",
            tag="general",
            depends_on="enable_tools",
            depends_value=True,
        )
        enable_menu_panel_service: bool = Field(
            default=False,
            description="是否允许调用自定义菜单与指令面板 Service",
            label="启用菜单面板 Service",
            tag="security",
        )
        enable_menu_panel_tools: bool = Field(
            default=False,
            description="是否向 LLM 注册菜单与指令面板管理工具",
            label="启用菜单面板 LLM 工具",
            tag="security",
            depends_on="enable_tools",
            depends_value=True,
        )
        allow_global_menu_write: bool = Field(
            default=False,
            description="是否允许覆盖 Bot 对所有用户生效的自定义菜单",
            label="允许全局菜单写入",
            tag="security",
        )
        allow_panel_create: bool = Field(
            default=False,
            description="是否允许创建指令面板",
            label="允许创建指令面板",
            tag="security",
        )
        allow_panel_delete: bool = Field(
            default=False,
            description="是否允许删除指令面板",
            label="允许删除指令面板",
            tag="security",
        )
        menu_panel_allowed_operator_openids: list[str] = Field(
            default_factory=list,
            description="允许 LLM 管理菜单面板的操作者 OpenID 白名单",
            label="菜单面板操作者白名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        menu_panel_allowed_group_openids: list[str] = Field(
            default_factory=list,
            description="允许 LLM 管理菜单面板的群 OpenID 白名单",
            label="菜单面板群白名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        menu_panel_allowed_panel_ids: list[str] = Field(
            default_factory=list,
            description="允许 LLM 操作的指令面板 ID 白名单",
            label="菜单面板 ID 白名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        menu_panel_profiles: list[dict[str, object]] = Field(
            default_factory=list,
            description="受信菜单面板配置档案，供 Tool 选择已授权目标",
            label="菜单面板配置档案",
            input_type="list",
            item_type="dict",
            tag="security",
        )
        enable_group_admin_service: bool = Field(
            default=False,
            description="是否允许受信插件调用高权限群管理 Service",
            label="启用群管理 Service",
            tag="security",
        )
        enable_group_admin_tools: bool = Field(
            default=False,
            description="是否向 LLM 注册群入群审批与成员禁言工具",
            label="启用群管理 LLM 工具",
            tag="security",
            depends_on="enable_tools",
            depends_value=True,
        )
        group_admin_allowed_group_openids: list[str] = Field(
            default_factory=list,
            description="允许 LLM 群管理工具操作的群 OpenID 白名单；留空时拒绝所有群",
            label="群管理允许群 OpenID",
            input_type="list",
            item_type="str",
            tag="security",
            depends_on="enable_group_admin_tools",
            depends_value=True,
        )
        debug_log_payload: bool = Field(
            default=False,
            description="开启后将发往 QQ API 的完整请求体打印到日志（可能包含完整回复内容）",
            label="调试：打印请求体",
            tag="debug",
        )

    @config_section("managed_panels", title="声明式托管面板", tag="managed_panels")
    class ManagedPanelsSection(SectionBase):
        """由本地配置声明并在插件加载时自动对账的固定面板。"""

        enabled: bool = Field(
            default=False,
            description="是否在插件加载或 reload 后自动对账托管面板",
            label="启用托管面板",
            tag="managed_panels",
        )
        items: list[ManagedPanelConfig] = Field(
            default_factory=list,
            description="声明式托管面板列表；managed_key 必须稳定且唯一",
            label="托管面板",
            input_type="list",
            item_type="dict",
            tag="managed_panels",
        )

    @config_section("interaction", title="互动回调", tag="interaction")
    class InteractionSection(SectionBase):
        """按钮互动路由、回调超时与去重配置。"""

        enabled: bool = Field(
            default=True,
            description="是否消费 qqbot_adapter 发布的互动事件",
            label="启用互动回调",
            tag="interaction",
        )
        callback_timeout: float = Field(
            default=5.0,
            description="权限与业务回调的单次超时时间（秒）",
            label="回调超时",
            ge=0.1,
            le=30.0,
            step=0.1,
            tag="interaction",
        )
        button_data_max_length: int = Field(
            default=1024,
            description="允许处理的 button_data 最大字符数",
            label="按钮数据长度",
            ge=3,
            le=4096,
            tag="interaction",
        )
        dedup_ttl: float = Field(
            default=300.0,
            description="ACK 消费记录保留时间（秒）",
            label="去重有效期",
            ge=1.0,
            le=3600.0,
            step=1.0,
            tag="interaction",
        )
        dedup_capacity: int = Field(
            default=4096,
            description="ACK 消费记录的最大容量",
            label="去重容量",
            ge=1,
            le=100000,
            tag="interaction",
        )

    @config_section("http", title="HTTP 客户端", tag="network")
    class HttpSection(SectionBase):
        """HTTP 客户端连接池配置。

        本插件持有独立的 httpx.AsyncClient，用于 qqbot_adapter 的
        ``SendHandler.post_api()`` 无法覆盖的 GET / PUT / DELETE 请求。
        默认值与 qqbot_adapter 的 HttpSection 保持一致。
        """

        max_keepalive_connections: int = Field(
            default=20,
            description="保持的空闲连接数上限",
            label="最大空闲连接",
            ge=1,
            le=100,
            tag="network",
        )
        max_connections: int = Field(
            default=50,
            description="总连接数上限（含活跃与空闲）",
            label="最大连接数",
            ge=1,
            le=200,
            tag="network",
        )
        keepalive_expiry: float = Field(
            default=30.0,
            description="空闲连接保活时间（秒）",
            label="保活时间",
            ge=1.0,
            le=300.0,
            step=1.0,
            tag="network",
        )
        connect_timeout: float = Field(
            default=10.0,
            description="连接建立超时（秒）",
            label="连接超时",
            ge=1.0,
            le=60.0,
            step=1.0,
            tag="network",
        )
        request_timeout: float = Field(
            default=30.0,
            description="默认请求超时（秒）",
            label="请求超时",
            ge=1.0,
            le=300.0,
            step=1.0,
            tag="network",
        )
        http2: bool = Field(
            default=True,
            description="启用 HTTP/2（服务端不支持时自动降级到 HTTP/1.1）",
            label="启用 HTTP/2",
            tag="network",
        )
        retry_max_attempts: int = Field(
            default=3,
            description="非 POST 请求失败重试次数（仅对网络错误重试，HTTP 状态码错误不重试）",
            label="重试次数",
            ge=0,
            le=10,
            tag="network",
        )
        retry_backoff_base: float = Field(
            default=1.0,
            description="重试退避基准时间（秒），实际等待 = base * 2^attempt（指数退避）",
            label="退避基准",
            ge=0.1,
            le=10.0,
            step=0.1,
            tag="network",
        )
        retry_backoff_max: float = Field(
            default=10.0,
            description="重试最大等待时间（秒），指数退避上限",
            label="退避上限",
            ge=1.0,
            le=60.0,
            step=1.0,
            tag="network",
        )
        retry_jitter: float = Field(
            default=0.3,
            description="重试抖动系数（0~1），在退避时间上加随机抖动避免雪崩",
            label="抖动系数",
            ge=0.0,
            le=1.0,
            step=0.1,
            tag="network",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    managed_panels: ManagedPanelsSection = Field(default_factory=ManagedPanelsSection)
    interaction: InteractionSection = Field(default_factory=InteractionSection)
    http: HttpSection = Field(default_factory=HttpSection)
