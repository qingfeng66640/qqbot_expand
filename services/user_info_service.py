"""供插件间调用的 QQ 用户基本信息专用只读 Service。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService

from ..src.user_info import query_user_info

__all__ = ["QQBotUserInfoService"]


class QQBotUserInfoService(BaseService):
    """查询用户资料，不发送消息、不下载头像、不合并跨应用用户身份。"""

    name = "qqbot_user_info"
    description = "只读查询 QQ 用户基本信息与主动推送偏好元数据"

    async def get_user_info(self, user_openid: str) -> dict[str, Any]:
        """获取当前 bot 用户信息，供 qqbot_expand:service:qqbot_user_info 调用。

        Args:
            user_openid: 当前 bot 的用户 OpenID，非 union_openid 或 member_openid。

        Returns:
            success/data/error 兼容结构，额外提供 error_type 分类（成功为 None）。
            allow_proactive_msg 仅为查询到的偏好，true 不授予任何发送权限。
            缺失或失败不会变成 false；union_openid/avatar 只作元数据原样返回。
            不缓存个人信息，调用方取消直接传播，网络失败不会自动重试。
        """
        return await query_user_info(self.plugin, user_openid)
