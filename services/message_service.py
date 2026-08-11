"""QQ 消息发送 Service。

补齐 ``qqbot_adapter`` 未覆盖的消息类型：keyboard（按钮）、ark（卡片）、
embed、模板 Markdown、引用回复。

所有方法遵循统一签名风格::

    send_xxx(target_type, target_id, ..., msg_id="", event_id="", msg_seq=None)

其中 ``target_type`` 为 ``"user"``（C2C 私聊）或 ``"group"``（群聊），
``target_id`` 为对应的 openid。

被动回复 vs 主动推送：带 ``msg_id`` / ``event_id`` 即为被动回复（不消耗主动
推送额度）；两者都不带则走主动推送通道，受 QQ 侧频次限制。

返回体统一为 ``{"success": bool, "message_id": str, "error": str | None}``，
错误信息经 ``src/errors.py`` 白名单脱敏后才对外暴露。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from src.app.plugin_system.base import BaseService

from ..src.bridge import api_request, encode_path_segment
from ..src.builders import (
    build_ark,
    build_embed,
    build_keyboard,
    build_markdown,
    build_media,
    build_message_reference,
)
from ..src.constants import (
    MEDIA_FILE_TYPES,
    MSG_SEQ_MAX,
    MSG_TYPE_ARK,
    MSG_TYPE_EMBED,
    MSG_TYPE_MARKDOWN,
    MSG_TYPE_MEDIA,
    MSG_TYPE_TEXT,
    PATH_GROUP_FILES,
    PATH_GROUP_MESSAGES,
    PATH_USER_FILES,
    PATH_USER_MESSAGES,
    TARGET_TYPE_GROUP,
    TARGET_TYPE_USER,
    TARGET_TYPES,
)

__all__ = ["QQBotMessageService"]


def _validate_target(target_type: str, target_id: str) -> str | None:
    """校验发送目标。

    Args:
        target_type: ``"user"`` 或 ``"group"``。
        target_id: 目标 openid。

    Returns:
        错误描述；合法时返回 None。
    """
    if not isinstance(target_type, str) or target_type not in TARGET_TYPES:
        return f"target_type 必须为 {sorted(TARGET_TYPES)} 之一"
    if not isinstance(target_id, str) or not target_id.strip():
        return "target_id 不能为空"
    return None


def _validate_msg_seq(msg_seq: int | None) -> str | None:
    """校验 msg_seq 取值范围。

    Args:
        msg_seq: 回复序号，None 表示由本 Service 填默认值。

    Returns:
        错误描述；合法时返回 None。
    """
    if msg_seq is None:
        return None
    if not isinstance(msg_seq, int) or isinstance(msg_seq, bool):
        return "msg_seq 必须是整数"
    if msg_seq < 1 or msg_seq > MSG_SEQ_MAX:
        return f"msg_seq 必须在 1~{MSG_SEQ_MAX} 之间"
    return None


def _failure(error: str) -> dict[str, Any]:
    """构造发送失败返回体。

    Args:
        error: 已脱敏的错误描述。

    Returns:
        统一返回结构。
    """
    return {"success": False, "message_id": "", "error": error}


def _media_failure(
    error: str, media: dict[str, Any] | None = None
) -> dict[str, Any]:
    """构造富媒体操作失败返回体。

    Args:
        error: 已脱敏的错误描述。
        media: 已完成上传时可供重试的媒体元数据。

    Returns:
        富媒体统一返回结构。
    """
    return {
        "success": False,
        "message_id": "",
        "media": media,
        "error": error,
    }


def _normalize_media_metadata(data: dict[str, Any]) -> dict[str, Any] | None:
    """提取允许对外暴露的上传元数据。

    Args:
        data: QQ 上传接口响应。

    Returns:
        规范化媒体元数据；缺少 file_info 时返回 None。
    """
    file_info = data.get("file_info")
    if not isinstance(file_info, str) or not file_info.strip():
        return None
    ttl = data.get("ttl", 0)
    if not isinstance(ttl, int) or isinstance(ttl, bool):
        ttl = 0
    return {
        "file_uuid": str(data.get("file_uuid", "")),
        "file_info": file_info,
        "ttl": ttl,
    }


def _validate_media_file_type(file_type: int) -> str | None:
    """校验 QQ 富媒体文件类型。

    Args:
        file_type: 1 图片、2 视频、3 语音、4 文件。

    Returns:
        错误描述；合法时返回 None。
    """
    if (
        not isinstance(file_type, int)
        or isinstance(file_type, bool)
        or file_type not in MEDIA_FILE_TYPES
    ):
        return f"file_type 必须为 {sorted(MEDIA_FILE_TYPES)} 之一"
    return None


def _validate_passive_source(msg_id: str, event_id: str) -> str | None:
    """校验被动回复来源字段互斥。

    Args:
        msg_id: 原始消息 id。
        event_id: 原始事件 id。

    Returns:
        错误描述；合法时返回 None。
    """
    if msg_id and event_id:
        return "msg_id 与 event_id 互斥，只能提供其中一个"
    return None


def _validate_force_verify_image_resource(value: bool) -> str | None:
    """校验图片资源强校验开关。"""
    if not isinstance(value, bool):
        return "force_verify_image_resource 必须是布尔值"
    return None


async def _validate_public_media_url(url: str) -> tuple[str | None, str]:
    """校验媒体 URL 只指向公网 HTTP(S) 地址。

    Args:
        url: 待交给 QQ 平台下载的媒体 URL。

    Returns:
        ``(错误描述, 规范化 URL)``；合法时错误描述为 None。
    """
    if not isinstance(url, str) or not url.strip():
        return "url 不能为空", ""
    normalized = url.strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        return "url 格式不合法", ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return "url 只支持 http 或 https 协议", ""
    if not parsed.hostname:
        return "url 必须包含主机名", ""
    if parsed.username is not None or parsed.password is not None:
        return "url 不能包含用户名或密码", ""

    host = parsed.hostname
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(
                host,
                port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
        except (OSError, ValueError):
            return "url 主机名解析失败", ""

    if not addresses:
        return "url 主机名没有可用地址", ""
    if any(not address.is_global for address in addresses):
        return "url 只能指向公网地址", ""
    return None, normalized


class QQBotMessageService(BaseService):
    """QQ 扩展消息发送服务。

    只负责拼装消息体并交给 ``src.bridge.api_request()`` 发送，
    token、重试、错误脱敏均在桥接层完成。
    """

    service_name = "qqbot_message"
    service_description = "发送 QQ 按钮 / ark / embed / 模板 Markdown / 引用回复 / 富媒体消息"
    version = "0.2.0"

    # ============ 内部工具 ============

    @staticmethod
    def _resolve_path(target_type: str, target_id: str) -> str:
        """把发送目标解析成 API 路径。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。

        Returns:
            以 ``/`` 开头的相对路径。
        """
        template = (
            PATH_USER_MESSAGES
            if target_type == TARGET_TYPE_USER
            else PATH_GROUP_MESSAGES
        )
        _error, openid = encode_path_segment(target_id, "target_id")
        return template.format(openid=openid)

    @staticmethod
    def _resolve_upload_path(target_type: str, target_id: str) -> str:
        """把发送目标解析成富媒体上传路径。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。

        Returns:
            以 ``/`` 开头的相对路径。
        """
        template = PATH_USER_FILES if target_type == TARGET_TYPE_USER else PATH_GROUP_FILES
        _error, openid = encode_path_segment(target_id, "target_id")
        return template.format(openid=openid)

    @staticmethod
    def _apply_passive_fields(
        payload: dict[str, Any],
        msg_id: str,
        event_id: str,
        msg_seq: int | None,
    ) -> None:
        """把被动回复相关字段写入消息体。

        Args:
            payload: 待补全的消息体。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id。
            msg_seq: 回复序号；带 ``msg_id`` 时缺省填 1。
        """
        if msg_id:
            payload["msg_id"] = msg_id
        if event_id:
            payload["event_id"] = event_id
        if msg_seq is not None:
            payload["msg_seq"] = msg_seq
        elif msg_id:
            payload["msg_seq"] = 1

    async def _send(
        self,
        target_type: str,
        target_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """发送已拼装好的消息体。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            payload: 完整消息体。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """
        # 群聊接口把 content 标为必填，非文本消息也需带上空串占位
        if target_type == TARGET_TYPE_GROUP:
            payload.setdefault("content", "")
        path = self._resolve_path(target_type, target_id)
        result = await api_request(self.plugin, "POST", path, payload)
        if not result["success"]:
            return _failure(result["error"])
        data = result["data"] or {}
        message_id = str(data.get("id", ""))
        registry = getattr(self.plugin, "sent_messages", None)
        if registry is not None:
            registry.record(message_id, target_type, target_id)
        return {
            "success": True,
            "message_id": message_id,
            "error": None,
        }

    async def _guard_and_send(
        self,
        target_type: str,
        target_id: str,
        msg_seq: int | None,
        builder: Any,
    ) -> dict[str, Any]:
        """统一执行"参数校验 -> 构造消息体 -> 发送"。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            msg_seq: 回复序号。
            builder: 无参可调用对象，返回完整消息体；构造失败应抛 ``ValueError``。

        Returns:
            统一返回结构。
        """
        error = _validate_target(target_type, target_id) or _validate_msg_seq(msg_seq)
        if error:
            return _failure(error)
        try:
            payload = builder()
        except ValueError as exc:
            return _failure(str(exc))
        return await self._send(target_type, target_id, payload)

    # ============ 对外方法 ============

    async def send_text(
        self,
        target_type: str,
        target_id: str,
        content: str,
        *,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
    ) -> dict[str, Any]:
        """发送普通文本，支持以 event_id 关联互动事件。"""

        def builder() -> dict[str, Any]:
            """构造文本消息体。"""
            if not isinstance(content, str) or not content.strip():
                raise ValueError("content 不能为空")
            if msg_id and event_id:
                raise ValueError("msg_id 与 event_id 互斥，只能提供其中一个")
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_TEXT,
                "content": content,
            }
            self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
            return payload

        return await self._guard_and_send(target_type, target_id, msg_seq, builder)

    async def send_keyboard(
        self,
        target_type: str,
        target_id: str,
        rows: list[list[dict[str, Any]]],
        content: str = "",
        *,
        custom_template_id: str = "",
        params: list[dict[str, Any]] | None = None,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
        force_verify_image_resource: bool = False,
    ) -> dict[str, Any]:
        """发送带按钮菜单的消息。

        QQ 侧要求 keyboard 必须搭载在 Markdown 消息（``msg_type=2``）上，
        因此必须提供 ``content``（原生 Markdown）或 ``custom_template_id``
        （已报备的模板）之一。

        Args:
            target_type: ``"user"``（C2C）或 ``"group"``（群聊）。
            target_id: 目标 openid。
            rows: 二维按钮列表，可用 ``src.builders.build_button()`` 构造；
                最多 5 行、每行最多 5 个按钮。
            content: 原生 Markdown 文本。
            custom_template_id: 已在 QQ 后台报备的 Markdown 模板 id。
            params: 模板参数，形如 ``[{"key": "title", "values": ["..."]}]``。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id。
            msg_seq: 回复序号，同一 ``msg_id`` 下必须唯一。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """

        def builder() -> dict[str, Any]:
            """拼装 Markdown + keyboard 消息体。

            Returns:
                完整消息体。

            Raises:
                ValueError: Markdown 载体、图片资源校验参数或按钮结构非法。
            """
            error = _validate_force_verify_image_resource(force_verify_image_resource)
            if error:
                raise ValueError(error)
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_MARKDOWN,
                "markdown": build_markdown(
                    content=content,
                    custom_template_id=custom_template_id,
                    params=params,
                ),
                "keyboard": build_keyboard(rows),
            }
            if force_verify_image_resource:
                payload["force_verify_image_resource"] = True
            self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
            return payload

        return await self._guard_and_send(target_type, target_id, msg_seq, builder)

    async def send_ark(
        self,
        target_type: str,
        target_id: str,
        template_id: int,
        kv: list[dict[str, Any]],
        *,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
    ) -> dict[str, Any]:
        """发送 ark 卡片消息（``msg_type=3``）。

        权限提示：**主动** ark 默认开放；**被动**（带 ``msg_id`` / ``event_id``）
        ark 需要达到准入条件并向平台运营申请后才可用，否则会返回权限错误。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            template_id: ark 模板 id。默认开放的有 23（链接+文本列表）、
                24（文本+缩略图）、37（大图），其余需管理端申请。
            kv: 模板参数列表，形如 ``[{"key": "#DESC#", "value": "..."}]``；
                数组型变量用 ``{"key": "#LIST#", "obj": [{"obj_kv": [...]}]}``。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id。
            msg_seq: 回复序号。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """

        def builder() -> dict[str, Any]:
            """拼装 ark 消息体。

            Returns:
                完整消息体。

            Raises:
                ValueError: 模板 id 或 kv 结构非法。
            """
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_ARK,
                "ark": build_ark(template_id, kv),
            }
            self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
            return payload

        return await self._guard_and_send(target_type, target_id, msg_seq, builder)

    async def send_embed(
        self,
        target_type: str,
        target_id: str,
        title: str,
        *,
        prompt: str = "",
        thumbnail_url: str = "",
        fields: list[str] | None = None,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
    ) -> dict[str, Any]:
        """发送 embed 消息（``msg_type=4``）。

        .. warning::
           官方文档明确标注 embed **单聊与群聊均不支持**，仅文字子频道与频道私信
           可用。而本 Service 只覆盖 ``/v2/users`` 与 ``/v2/groups`` 两个接口，
           因此本方法在实际环境中大概率返回权限/参数错误。保留它是为了在 QQ 侧
           放开该能力后无需改动调用方；**当前请优先使用 ark 或 Markdown**。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            title: 标题。
            prompt: 消息列表中的外显提示文本，留空则复用 ``title``。
            thumbnail_url: 缩略图 URL。
            fields: 正文条目，每一项渲染成一行。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id。
            msg_seq: 回复序号。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """

        def builder() -> dict[str, Any]:
            """拼装 embed 消息体。

            Returns:
                完整消息体。

            Raises:
                ValueError: title 为空。
            """
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_EMBED,
                "embed": build_embed(
                    title,
                    prompt=prompt,
                    thumbnail_url=thumbnail_url,
                    fields=fields,
                ),
            }
            self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
            return payload

        return await self._guard_and_send(target_type, target_id, msg_seq, builder)

    async def send_markdown_template(
        self,
        target_type: str,
        target_id: str,
        custom_template_id: str,
        params: list[dict[str, Any]] | None = None,
        *,
        rows: list[list[dict[str, Any]]] | None = None,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
        force_verify_image_resource: bool = False,
    ) -> dict[str, Any]:
        """发送模板 Markdown 消息（``msg_type=2``）。

        与 ``qqbot_adapter`` 已有的原生 Markdown 不同，这里走的是 QQ 后台
        报备过的模板，可以绕开原生 Markdown 的白名单限制。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            custom_template_id: 已报备的 Markdown 模板 id。
            params: 模板参数，形如 ``[{"key": "title", "values": ["..."]}]``。
            rows: 可选的按钮，附加在模板消息下方。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id。
            msg_seq: 回复序号。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """

        def builder() -> dict[str, Any]:
            """拼装模板 Markdown 消息体，按需附加按钮。

            Returns:
                完整消息体。

            Raises:
                ValueError: 模板参数、图片资源校验参数或按钮结构非法。
            """
            error = _validate_force_verify_image_resource(force_verify_image_resource)
            if error:
                raise ValueError(error)
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_MARKDOWN,
                "markdown": build_markdown(
                    custom_template_id=custom_template_id, params=params
                ),
            }
            if force_verify_image_resource:
                payload["force_verify_image_resource"] = True
            if rows:
                payload["keyboard"] = build_keyboard(rows)
            self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
            return payload

        return await self._guard_and_send(target_type, target_id, msg_seq, builder)

    async def send_reply(
        self,
        target_type: str,
        target_id: str,
        content: str,
        reference_message_id: str,
        *,
        ignore_get_message_error: bool = False,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
    ) -> dict[str, Any]:
        """发送带引用回复的文本消息。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            content: 文本内容。
            reference_message_id: 被引用消息的 id。
            ignore_get_message_error: 拉取被引用消息失败时是否忽略错误继续发送。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id。
            msg_seq: 回复序号。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """

        def builder() -> dict[str, Any]:
            """拼装带引用回复的文本消息体。

            Returns:
                完整消息体。

            Raises:
                ValueError: 内容为空或被引用消息 id 缺失。
            """
            if not content or not content.strip():
                raise ValueError("content 不能为空")
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_TEXT,
                "content": content,
                "message_reference": build_message_reference(
                    reference_message_id,
                    ignore_get_message_error=ignore_get_message_error,
                ),
            }
            self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
            return payload

        return await self._guard_and_send(target_type, target_id, msg_seq, builder)

    async def upload_media_from_url(
        self,
        target_type: str,
        target_id: str,
        file_type: int,
        url: str,
        *,
        file_name: str = "",
    ) -> dict[str, Any]:
        """从公网 URL 上传图片、视频、语音或文件。

        本方法只执行上传阶段，并固定使用 ``srv_send_msg=false``。取得的
        ``file_info`` 只能用于相同目标场景，并应在 ``ttl`` 到期前使用。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            file_type: 1 图片、2 视频、3 语音、4 文件。
            url: QQ 平台可访问的公网 HTTP(S) URL。
            file_name: 可选文件名。

        Returns:
            富媒体统一返回结构；成功时 ``media`` 包含上传元数据。
        """
        error = _validate_target(target_type, target_id) or _validate_media_file_type(
            file_type
        )
        if error:
            return _media_failure(error)
        if not isinstance(file_name, str):
            return _media_failure("file_name 必须是字符串")
        url_error, normalized_url = await _validate_public_media_url(url)
        if url_error:
            return _media_failure(url_error)

        payload: dict[str, Any] = {
            "file_type": file_type,
            "url": normalized_url,
            "srv_send_msg": False,
        }
        if file_name.strip():
            payload["file_name"] = file_name.strip()
        result = await api_request(
            self.plugin,
            "POST",
            self._resolve_upload_path(target_type, target_id),
            payload,
        )
        if not result["success"]:
            return _media_failure(result["error"])
        media = _normalize_media_metadata(result["data"] or {})
        if media is None:
            return _media_failure("QQ API 上传响应缺少 file_info")
        return {
            "success": True,
            "message_id": "",
            "media": media,
            "error": None,
        }

    async def send_media(
        self,
        target_type: str,
        target_id: str,
        file_info: str,
        *,
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
    ) -> dict[str, Any]:
        """发送已经上传完成的富媒体消息（``msg_type=7``）。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid，必须与上传 ``file_info`` 时的场景一致。
            file_info: QQ ``/files`` 接口返回的不透明数据。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id，与 ``msg_id`` 互斥。
            msg_seq: 回复序号。

        Returns:
            富媒体统一返回结构；本方法不上传，因此 ``media`` 为 None。
        """
        error = (
            _validate_target(target_type, target_id)
            or _validate_msg_seq(msg_seq)
            or _validate_passive_source(msg_id, event_id)
        )
        if error:
            return _media_failure(error)
        try:
            payload: dict[str, Any] = {
                "msg_type": MSG_TYPE_MEDIA,
                "media": build_media(file_info),
            }
        except ValueError as exc:
            return _media_failure(str(exc))
        self._apply_passive_fields(payload, msg_id, event_id, msg_seq)
        result = await self._send(target_type, target_id, payload)
        return {
            "success": result["success"],
            "message_id": result["message_id"],
            "media": None,
            "error": result["error"],
        }

    async def send_media_from_url(
        self,
        target_type: str,
        target_id: str,
        file_type: int,
        url: str,
        *,
        file_name: str = "",
        msg_id: str = "",
        event_id: str = "",
        msg_seq: int | None = None,
    ) -> dict[str, Any]:
        """从公网 URL 上传并发送一条富媒体消息。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            file_type: 1 图片、2 视频、3 语音、4 文件。
            url: QQ 平台可访问的公网 HTTP(S) URL。
            file_name: 可选文件名。
            msg_id: 被动回复关联的原始消息 id。
            event_id: 被动回复关联的事件 id，与 ``msg_id`` 互斥。
            msg_seq: 回复序号。

        Returns:
            富媒体统一返回结构。发送失败但上传成功时仍保留 ``media``，调用方
            可在 TTL 内用其中的 ``file_info`` 重试 ``send_media()``。
        """
        error = _validate_msg_seq(msg_seq) or _validate_passive_source(msg_id, event_id)
        if error:
            return _media_failure(error)
        uploaded = await self.upload_media_from_url(
            target_type,
            target_id,
            file_type,
            url,
            file_name=file_name,
        )
        if not uploaded["success"]:
            return uploaded
        media = uploaded["media"]
        sent = await self.send_media(
            target_type,
            target_id,
            media["file_info"],
            msg_id=msg_id,
            event_id=event_id,
            msg_seq=msg_seq,
        )
        sent["media"] = media
        return sent

    async def send_raw_message(
        self,
        target_type: str,
        target_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """直接投递一个完整消息体。

        用于本 Service 尚未包装的消息形态，调用方需自行保证 ``payload``
        符合 QQ 开放平台的消息结构。

        Args:
            target_type: ``"user"`` 或 ``"group"``。
            target_id: 目标 openid。
            payload: 完整消息体，至少包含 ``msg_type``。

        Returns:
            ``{"success": bool, "message_id": str, "error": str | None}``。
        """
        error = _validate_target(target_type, target_id)
        if error:
            return _failure(error)
        if not isinstance(payload, dict) or not payload:
            return _failure("payload 必须是非空字典")
        if "msg_type" not in payload:
            return _failure("payload 必须包含 msg_type 字段")
        return await self._send(target_type, target_id, payload)
