"""通过公开 Service 查询用户资料；仅返回数据，不发送消息或下载头像。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api import service_api


async def query_user_profile(user_openid: str) -> dict[str, Any]:
    """在插件已加载的框架中查询当前 bot 的用户 OpenID。

    查询不自动重试，失败时 data 为 None；调用方可读取稳定的 error_type 决策。
    allow_proactive_msg=True 只是偏好元数据，不代表拥有发送许可。
    avatar/union_openid 不应自动下载、用于路由或触发跨应用身份合并。
    请勿打印包含个人资料的结果；本示例不读取凭据或启动网络生命周期。
    """
    service = service_api.get_service("qqbot_expand:service:qqbot_user_info")
    if service is None:
        raise RuntimeError("用户信息 Service 尚未加载")
    return await service.get_user_info(user_openid)
