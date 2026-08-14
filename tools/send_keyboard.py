"""按钮菜单 Tool。

让 LLM 在回复里附带一排可点击按钮，把"接下来能做什么"直接摆给用户，
免去用户手动敲指令。

按钮的 openid 目标由触发消息反推，LLM 不需要（也无法）指定发给谁。
"""
from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.base import BaseTool

from ..services.message_service import QQBotMessageService
from ..src.builders import build_button
from ..src.constants import (
    ACTION_TYPE_COMMAND,
    ACTION_TYPE_LINK,
    BUTTON_STYLE_BLUE,
    KEYBOARD_MAX_BUTTONS_PER_ROW,
    KEYBOARD_MAX_ROWS,
    TARGET_TYPE_USER,
)
from ..src.targets import resolve_target
from .schema_types import KeyboardButtonInput

__all__ = ["QQSendKeyboardTool"]

_MAX_BUTTONS = KEYBOARD_MAX_ROWS * KEYBOARD_MAX_BUTTONS_PER_ROW


class QQSendKeyboardTool(BaseTool):
    """向当前会话发送带按钮菜单的 Markdown 消息。"""

    tool_name = "qq_send_keyboard"
    tool_description = (
        "在 QQ 会话中发送一条带可点击按钮的消息。按钮可以是指令按钮"
        "（用户点击后自动发出一句话）或链接按钮（点击后跳转网页）。"
        "适合给出后续操作选项，避免让用户手动输入指令。"
    )
    associated_platforms = ["qq"]

    async def execute(
        self,
        content: Annotated[str, "按钮上方显示的 Markdown 正文，不能为空"],
        buttons: Annotated[
            list[KeyboardButtonInput],
            "按钮列表，最多 25 个。每项形如 "
            '{"label": "按钮文字", "command": "点击后发送的指令"} 或 '
            '{"label": "按钮文字", "url": "https://..."}；'
            "command 与 url 二选一，label 必填",
        ],
        per_row: Annotated[int, "每行放几个按钮，1~5，默认 2"] = 2,
    ) -> tuple[bool, str | dict]:
        """发送按钮菜单。

        Args:
            content: 按钮上方的 Markdown 正文。
            buttons: 扁平按钮列表，由本方法按 ``per_row`` 自动折行。
            per_row: 每行按钮数量。

        Returns:
            ``(是否成功, 结果描述)``。
        """
        if not content or not content.strip():
            return False, "content 不能为空"
        if not buttons:
            return False, "buttons 不能为空"
        if len(buttons) > _MAX_BUTTONS:
            return False, f"按钮总数不能超过 {_MAX_BUTTONS} 个"
        if not isinstance(per_row, int) or not 1 <= per_row <= KEYBOARD_MAX_BUTTONS_PER_ROW:
            return False, f"per_row 必须在 1~{KEYBOARD_MAX_BUTTONS_PER_ROW} 之间"

        target = resolve_target(self.trigger_message)
        if target is None:
            return False, "无法从当前会话推导 QQ 发送目标"

        try:
            # action.enter（点击后自动发送）仅单聊可用，群聊只能填充输入框
            rows = self._build_rows(
                buttons, per_row, auto_enter=target.target_type == TARGET_TYPE_USER
            )
        except ValueError as exc:
            return False, str(exc)

        service = QQBotMessageService(self.plugin)
        result = await service.send_keyboard(
            target.target_type,
            target.target_id,
            rows,
            content=content,
            msg_id=target.msg_id,
        )
        if not result["success"]:
            return False, f"按钮消息发送失败: {result['error']}"
        return True, {
            "message_id": result["message_id"],
            "ref_idx": result["ref_idx"],
            "button_count": len(buttons),
        }

    @staticmethod
    def _build_rows(
        buttons: list[dict[str, Any]], per_row: int, *, auto_enter: bool
    ) -> list[list[dict[str, Any]]]:
        """把扁平按钮列表折成二维行结构。

        Args:
            buttons: LLM 给出的按钮描述列表。
            per_row: 每行按钮数量。
            auto_enter: 指令按钮是否点击后自动发送。QQ 侧该行为仅单聊生效，
                群聊传 True 无效，因此这里由调用方按场景传入。

        Returns:
            可直接交给 ``build_keyboard()`` 的二维列表。

        Raises:
            ValueError: 某个按钮缺少 label，或 command/url 未二选一。
        """
        built: list[dict[str, Any]] = []
        for index, item in enumerate(buttons):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 个按钮必须是字典")
            label = str(item.get("label", "") or "").strip()
            command = str(item.get("command", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            if not label:
                raise ValueError(f"第 {index + 1} 个按钮缺少 label")
            if bool(command) == bool(url):
                raise ValueError(f"第 {index + 1} 个按钮必须且只能提供 command 或 url 之一")

            if url:
                built.append(
                    build_button(
                        label,
                        style=BUTTON_STYLE_BLUE,
                        action_type=ACTION_TYPE_LINK,
                        data=url,
                    )
                )
            else:
                built.append(
                    build_button(
                        label,
                        action_type=ACTION_TYPE_COMMAND,
                        data=command,
                        enter=auto_enter,
                    )
                )

        return [built[i : i + per_row] for i in range(0, len(built), per_row)]
