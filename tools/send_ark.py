"""ark 卡片 Tool。

ark 是 QQ 侧预置的富文本卡片模板，比纯文本更适合展示结构化信息。
本 Tool 只暴露官方默认开放、无需申请的两种模板：

- 23 链接 + 文本列表：条目可带链接（链接需提前在开放平台报备）
- 24 文本 + 缩略图：标题 + 详情描述 + 缩略图 + 跳转链接

模板变量名（``#DESC#`` / ``#LIST#`` 之类）由本 Tool 内部填充，
LLM 只需给出语义化的标题、条目、图片等，不必接触原始 kv 结构。
"""
from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.base import BaseTool

from ..services.message_service import QQBotMessageService
from ..src.constants import ARK_TEMPLATE_LIST, ARK_TEMPLATE_THUMBNAIL
from ..src.targets import resolve_target
from .schema_types import ArkListItemInput, ArkStyle

__all__ = ["QQSendArkTool"]

_MAX_LIST_ITEMS = 10


class QQSendArkTool(BaseTool):
    """向当前会话发送 ark 卡片消息。"""

    tool_name = "qq_send_ark"
    tool_description = (
        "在 QQ 会话中发送一张 ark 富文本卡片。style='list' 展示条目列表（每条可带链接），"
        "style='card' 展示带标题、详情和缩略图的卡片。"
        "比纯文本更适合呈现搜索结果、榜单、推荐内容等结构化信息。"
    )
    associated_platforms = ["qq"]

    async def execute(
        self,
        style: Annotated[ArkStyle, "卡片样式：list 条目列表或 card 缩略图卡片"],
        title: Annotated[str, "卡片标题，不能为空"],
        items: Annotated[
            list[ArkListItemInput] | None,
            "style='list' 时的条目列表，最多 10 条。每项形如 "
            '{"text": "条目文字"} 或 {"text": "条目文字", "url": "https://..."}；'
            "url 需为已在开放平台报备的域名，未报备则会发送失败",
        ] = None,
        description: Annotated[str, "style='card' 时的详情描述文本"] = "",
        image_url: Annotated[
            str, "style='card' 时的缩略图 URL，需为公网可访问地址"
        ] = "",
        link_url: Annotated[str, "style='card' 时点击卡片跳转的 URL，需提前报备域名"] = "",
    ) -> tuple[bool, str | dict]:
        """发送 ark 卡片。

        Args:
            style: ``"list"`` 或 ``"card"``。
            title: 卡片标题。
            items: 列表条目，每项含 ``text``，可选 ``url``。
            description: 卡片详情描述。
            image_url: 卡片缩略图 URL。
            link_url: 卡片跳转 URL。

        Returns:
            ``(是否成功, 结果描述)``。
        """
        normalized_style = str(style or "").strip().lower()
        if normalized_style not in {"list", "card"}:
            return False, "style 只能是 'list' 或 'card'"
        if not title or not title.strip():
            return False, "title 不能为空"

        target = resolve_target(self.trigger_message)
        if target is None:
            return False, "无法从当前会话推导 QQ 发送目标"

        try:
            if normalized_style == "list":
                template_id = ARK_TEMPLATE_LIST
                kv = self._build_list_kv(title, items)
            else:
                template_id = ARK_TEMPLATE_THUMBNAIL
                kv = self._build_thumbnail_kv(title, description, image_url, link_url)
        except ValueError as exc:
            return False, str(exc)

        service = QQBotMessageService(self.plugin)
        result = await service.send_ark(
            target.target_type,
            target.target_id,
            template_id,
            kv,
            msg_id=target.msg_id,
        )
        if not result["success"]:
            return False, f"ark 卡片发送失败: {result['error']}"
        return True, {
            "message_id": result["message_id"],
            "ref_idx": result["ref_idx"],
            "template_id": template_id,
        }

    @staticmethod
    def _build_list_kv(
        title: str, items: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        """构造 23 号（链接 + 文本列表）模板的 kv 参数。

        Args:
            title: 列表标题，同时用作 ``#DESC#`` 与 ``#PROMPT#``。
            items: 条目列表，每项含 ``text``，可选 ``url``。

        Returns:
            ark kv 参数列表。

        Raises:
            ValueError: 条目为空、超限或缺少 text。
        """
        if not items:
            raise ValueError("style='list' 时 items 不能为空")
        if len(items) > _MAX_LIST_ITEMS:
            raise ValueError(f"items 最多 {_MAX_LIST_ITEMS} 条")

        list_objects: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 个条目必须是字典")
            text = str(item.get("text", "") or "").strip()
            if not text:
                raise ValueError(f"第 {index + 1} 个条目缺少 text")
            obj_kv: list[dict[str, str]] = [{"key": "desc", "value": text}]
            url = str(item.get("url", "") or "").strip()
            if url:
                obj_kv.append({"key": "link", "value": url})
            list_objects.append({"obj_kv": obj_kv})

        return [
            {"key": "#DESC#", "value": title},
            {"key": "#PROMPT#", "value": title},
            {"key": "#LIST#", "obj": list_objects},
        ]

    @staticmethod
    def _build_thumbnail_kv(
        title: str, description: str, image_url: str, link_url: str
    ) -> list[dict[str, Any]]:
        """构造 24 号（文本 + 缩略图）模板的 kv 参数。

        Args:
            title: 卡片标题。
            description: 详情描述。
            image_url: 缩略图 URL。
            link_url: 跳转 URL。

        Returns:
            ark kv 参数列表。

        Raises:
            ValueError: image_url 为空。
        """
        if not image_url or not image_url.strip():
            raise ValueError("style='card' 时 image_url 不能为空")

        kv: list[dict[str, Any]] = [
            {"key": "#DESC#", "value": description or title},
            {"key": "#PROMPT#", "value": title},
            {"key": "#TITLE#", "value": title},
            {"key": "#METADESC#", "value": description or title},
            {"key": "#IMG#", "value": image_url.strip()},
        ]
        if link_url and link_url.strip():
            kv.append({"key": "#LINK#", "value": link_url.strip()})
        return kv
