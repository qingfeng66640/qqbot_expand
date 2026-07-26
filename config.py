"""QQ Bot Expand 插件配置定义。

镜像 qqbot_adapter 的 HTTP 连接池默认值，保证两侧行为一致；
另外提供功能开关，控制精选 Tool 的注册与 raw 通道的访问范围。
"""
from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


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
            default_factory=lambda: ["GET", "POST", "PUT", "DELETE"],
            description="raw 通道允许使用的 HTTP 方法",
            label="raw 允许方法",
            input_type="list",
            item_type="str",
            tag="security",
            depends_on="allow_raw_request",
            depends_value=True,
        )
        debug_log_payload: bool = Field(
            default=False,
            description="开启后将发往 QQ API 的完整请求体打印到日志（可能包含完整回复内容）",
            label="调试：打印请求体",
            tag="debug",
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
    http: HttpSection = Field(default_factory=HttpSection)
