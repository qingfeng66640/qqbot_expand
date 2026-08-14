"""三个精选 Tool 的行为测试。

Tool 的参数由 LLM 填写，因此重点验证：非法参数不会变成非法 API 调用、
发送目标只能从触发消息推导（LLM 无法指定 openid）、失败一律以
``(False, 原因)`` 返回而不是抛异常。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ..src.constants import (
    ACTION_TYPE_COMMAND,
    ACTION_TYPE_LINK,
    ARK_TEMPLATE_LIST,
    ARK_TEMPLATE_THUMBNAIL,
    KEYBOARD_MAX_BUTTONS_PER_ROW,
)
from ..tools import ALL_TOOLS
from ..tools.send_ark import QQSendArkTool
from ..tools.send_keyboard import QQSendKeyboardTool
from ..tools.send_reply import QQSendReplyTool
from .conftest import FakeHttpClient, make_plugin


def make_message(
    *,
    chat_type: str = "group",
    group_id: str = "g1",
    sender_id: str = "u1",
    ref_idx: str = "ref-0",
) -> SimpleNamespace:
    """构造触发消息替身。

    Args:
        chat_type: ``"group"`` 或 ``"private"``。
        group_id: 群 openid，写入 ``extra``。
        sender_id: 发送者 openid。

    Returns:
        带 Message 关键字段的替身对象。
    """
    extra = (
        {"group_id": group_id, "qq_ref_idx": ref_idx}
        if chat_type == "group"
        else {"qq_ref_idx": ref_idx}
    )
    return SimpleNamespace(
        message_id="m0",
        chat_type=chat_type,
        sender_id=sender_id,
        extra=extra,
    )


def bind(tool_cls, message: SimpleNamespace | None):
    """实例化 Tool 并绑定运行时上下文。

    Args:
        tool_cls: Tool 类。
        message: 触发消息替身。

    Returns:
        已绑定上下文的 Tool 实例。
    """
    tool = tool_cls(make_plugin(http_client=FakeHttpClient()))
    tool._bind_runtime_context(stream_id="s1", message=message)
    return tool


class TestToolRegistration:
    """组件注册元数据。"""

    def test_names_and_platform(self) -> None:
        """Tool 名与 manifest 一致，且只在 QQ 平台激活。"""
        assert [tool.tool_name for tool in ALL_TOOLS] == [
            "qq_send_keyboard",
            "qq_send_ark",
            "qq_send_reply",
        ]
        for tool in ALL_TOOLS:
            assert tool.associated_platforms == ["qq"]
            assert tool.tool_description

    def test_schema_is_generated(self) -> None:
        """每个 Tool 都能生成合法的 function schema。"""
        for tool in ALL_TOOLS:
            schema = tool.to_schema()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == f"tool-{tool.tool_name}"
            assert schema["function"]["parameters"]["properties"]

    def test_openid_is_not_an_llm_parameter(self) -> None:
        """LLM 不应能指定发送目标，避免越权发消息。"""
        for tool in ALL_TOOLS:
            properties = tool.to_schema()["function"]["parameters"]["properties"]
            assert "target_id" not in properties
            assert "openid" not in properties
            assert "target_type" not in properties

    def test_keyboard_and_ark_nested_schemas(self) -> None:
        """按钮与 Ark 列表向模型暴露准确字段和枚举。"""
        keyboard = QQSendKeyboardTool.to_schema()["function"]["parameters"][
            "properties"
        ]["buttons"]["items"]
        assert set(keyboard["properties"]) == {"label", "command", "url"}
        assert keyboard["required"] == ["label"]

        ark = QQSendArkTool.to_schema()["function"]["parameters"]["properties"]
        assert ark["style"]["type"] == "string"
        assert set(ark["style"]["enum"]) == {"list", "card"}
        ark_item = ark["items"]["items"]
        assert set(ark_item["properties"]) == {"text", "url"}
        assert ark_item["required"] == ["text"]


class TestSendKeyboardTool:
    """按钮 Tool。"""

    async def test_sends_command_and_link_buttons(self, patch_send_handler) -> None:
        """指令按钮与链接按钮分别映射到 action.type 2 / 0。"""
        tool = bind(QQSendKeyboardTool, make_message())

        ok, result = await tool.execute(
            "请选择：",
            [
                {"label": "详情", "command": "/detail"},
                {"label": "官网", "url": "https://example.com"},
            ],
        )

        assert ok is True
        assert result["button_count"] == 2
        body = patch_send_handler.posts[-1][2]
        buttons = body["keyboard"]["content"]["rows"][0]["buttons"]
        assert buttons[0]["action"]["type"] == ACTION_TYPE_COMMAND
        assert buttons[0]["action"]["data"] == "/detail"
        assert buttons[1]["action"]["type"] == ACTION_TYPE_LINK
        assert buttons[1]["action"]["data"] == "https://example.com"

    async def test_target_derived_from_group_message(self, patch_send_handler) -> None:
        """群聊消息推导出群 openid 并带上被动回复 msg_id。"""
        tool = bind(QQSendKeyboardTool, make_message(group_id="gx"))

        await tool.execute("x", [{"label": "A", "command": "/a"}])

        assert patch_send_handler.posts[-1][0].endswith("/v2/groups/gx/messages")
        assert patch_send_handler.posts[-1][2]["msg_id"] == "m0"

    async def test_target_derived_from_private_message(self, patch_send_handler) -> None:
        """单聊消息推导出用户 openid。"""
        tool = bind(QQSendKeyboardTool, make_message(chat_type="private", sender_id="ux"))

        await tool.execute("x", [{"label": "A", "command": "/a"}])

        assert patch_send_handler.posts[-1][0].endswith("/v2/users/ux/messages")

    async def test_enter_only_enabled_in_private(self, patch_send_handler) -> None:
        """action.enter 仅单聊生效，群聊必须为 False。"""
        private = bind(QQSendKeyboardTool, make_message(chat_type="private"))
        await private.execute("x", [{"label": "A", "command": "/a"}])
        private_button = patch_send_handler.posts[-1][2]["keyboard"]["content"]["rows"][0][
            "buttons"
        ][0]
        assert private_button["action"]["enter"] is True

        group = bind(QQSendKeyboardTool, make_message())
        await group.execute("x", [{"label": "A", "command": "/a"}])
        group_button = patch_send_handler.posts[-1][2]["keyboard"]["content"]["rows"][0][
            "buttons"
        ][0]
        assert group_button["action"]["enter"] is False

    async def test_wraps_rows_by_per_row(self, patch_send_handler) -> None:
        """按 per_row 自动折行。"""
        tool = bind(QQSendKeyboardTool, make_message())
        buttons = [{"label": f"B{i}", "command": f"/{i}"} for i in range(5)]

        await tool.execute("x", buttons, per_row=2)

        rows = patch_send_handler.posts[-1][2]["keyboard"]["content"]["rows"]
        assert [len(row["buttons"]) for row in rows] == [2, 2, 1]

    async def test_no_target(self) -> None:
        """无触发消息时不发请求。"""
        ok, result = await bind(QQSendKeyboardTool, None).execute(
            "x", [{"label": "A", "command": "/a"}]
        )
        assert ok is False
        assert "发送目标" in result

    @pytest.mark.parametrize(
        ("content", "buttons", "per_row"),
        [
            ("", [{"label": "A", "command": "/a"}], 2),
            ("   ", [{"label": "A", "command": "/a"}], 2),
            ("x", [], 2),
            ("x", [{"label": "A", "command": "/a"}], 0),
            ("x", [{"label": "A", "command": "/a"}], KEYBOARD_MAX_BUTTONS_PER_ROW + 1),
        ],
    )
    async def test_rejects_bad_arguments(
        self, patch_send_handler, content: str, buttons: list, per_row: int
    ) -> None:
        """参数非法时在发请求前拒绝。"""
        tool = bind(QQSendKeyboardTool, make_message())

        ok, _ = await tool.execute(content, buttons, per_row=per_row)

        assert ok is False
        assert patch_send_handler.posts == []

    async def test_rejects_too_many_buttons(self, patch_send_handler) -> None:
        """总数超过 5x5 时拒绝。"""
        tool = bind(QQSendKeyboardTool, make_message())
        buttons = [{"label": f"B{i}", "command": f"/{i}"} for i in range(26)]

        ok, result = await tool.execute("x", buttons)

        assert ok is False
        assert "25" in result

    @pytest.mark.parametrize(
        "button",
        [
            {"command": "/a"},
            {"label": "A"},
            {"label": "A", "command": "/a", "url": "https://x.com"},
            "notdict",
        ],
    )
    async def test_rejects_malformed_button(
        self, patch_send_handler, button: object
    ) -> None:
        """按钮缺 label 或 command/url 未二选一时拒绝。"""
        tool = bind(QQSendKeyboardTool, make_message())

        ok, _ = await tool.execute("x", [button])  # type: ignore[list-item]

        assert ok is False
        assert patch_send_handler.posts == []

    async def test_reports_send_failure(self, patch_send_handler) -> None:
        """底层失败时返回结构化原因。"""
        patch_send_handler.post_result = {"code": 22009, "message": "msg limit exceed"}
        tool = bind(QQSendKeyboardTool, make_message())

        ok, result = await tool.execute("x", [{"label": "A", "command": "/a"}])

        assert ok is False
        assert "msg limit exceed" not in result


class TestSendArkTool:
    """ark Tool。"""

    async def test_list_style_uses_template_23(self, patch_send_handler) -> None:
        """style='list' 走模板 23，条目落到 #LIST# 的 obj_kv。"""
        tool = bind(QQSendArkTool, make_message())

        ok, result = await tool.execute(
            "list",
            "搜索结果",
            items=[{"text": "第一条"}, {"text": "第二条", "url": "https://example.com"}],
        )

        assert ok is True
        assert result["template_id"] == ARK_TEMPLATE_LIST
        kv = patch_send_handler.posts[-1][2]["ark"]["kv"]
        by_key = {item["key"]: item for item in kv}
        assert by_key["#DESC#"]["value"] == "搜索结果"
        assert by_key["#PROMPT#"]["value"] == "搜索结果"
        objects = by_key["#LIST#"]["obj"]
        assert objects[0]["obj_kv"] == [{"key": "desc", "value": "第一条"}]
        assert objects[1]["obj_kv"][1] == {"key": "link", "value": "https://example.com"}

    async def test_card_style_uses_template_24(self, patch_send_handler) -> None:
        """style='card' 走模板 24，字段名对齐官方模板变量。"""
        tool = bind(QQSendArkTool, make_message())

        ok, result = await tool.execute(
            "card",
            "标题",
            description="简介",
            image_url="https://img.example.com/a.png",
            link_url="https://example.com",
        )

        assert ok is True
        assert result["template_id"] == ARK_TEMPLATE_THUMBNAIL
        kv = {item["key"]: item["value"] for item in patch_send_handler.posts[-1][2]["ark"]["kv"]}
        assert kv["#TITLE#"] == "标题"
        assert kv["#METADESC#"] == "简介"
        assert kv["#IMG#"] == "https://img.example.com/a.png"
        assert kv["#LINK#"] == "https://example.com"

    async def test_card_link_is_optional(self, patch_send_handler) -> None:
        """未提供跳转链接时不写入 #LINK#。"""
        tool = bind(QQSendArkTool, make_message())

        await tool.execute("card", "标题", image_url="https://img")

        keys = {item["key"] for item in patch_send_handler.posts[-1][2]["ark"]["kv"]}
        assert "#LINK#" not in keys

    @pytest.mark.parametrize(
        ("style", "title", "kwargs"),
        [
            ("unknown", "标题", {}),
            ("list", "", {"items": [{"text": "a"}]}),
            ("list", "标题", {}),
            ("list", "标题", {"items": [{"text": ""}]}),
            ("list", "标题", {"items": ["notdict"]}),
            ("list", "标题", {"items": [{"text": "a"}] * 11}),
            ("card", "标题", {}),
        ],
    )
    async def test_rejects_bad_arguments(
        self, patch_send_handler, style: str, title: str, kwargs: dict
    ) -> None:
        """参数非法时在发请求前拒绝。"""
        tool = bind(QQSendArkTool, make_message())

        ok, _ = await tool.execute(style, title, **kwargs)

        assert ok is False
        assert patch_send_handler.posts == []

    async def test_no_target(self) -> None:
        """无触发消息时不发请求。"""
        ok, _ = await bind(QQSendArkTool, None).execute(
            "list", "标题", items=[{"text": "a"}]
        )
        assert ok is False

    async def test_reports_send_failure(self, patch_send_handler) -> None:
        """底层失败时返回结构化原因，且不泄漏原始错误。"""
        patch_send_handler.post_result = {"code": 22009, "message": "msg limit exceed"}
        tool = bind(QQSendArkTool, make_message())

        ok, result = await tool.execute("list", "标题", items=[{"text": "a"}])

        assert ok is False
        assert "msg limit exceed" not in result


class TestSendReplyTool:
    """引用回复 Tool。"""

    async def test_defaults_to_trigger_message(self, patch_send_handler) -> None:
        """不指定被引用索引时默认引用触发消息携带的 ref_idx。"""
        tool = bind(QQSendReplyTool, make_message())

        ok, result = await tool.execute("收到")

        assert ok is True
        assert result["reference_message_id"] == "ref-0"
        body = patch_send_handler.posts[-1][2]
        assert body["content"] == "收到"
        assert body["message_reference"]["message_id"] == "ref-0"
        assert body["message_reference"]["ignore_get_message_error"] is True

    async def test_explicit_reference(self, patch_send_handler) -> None:
        """可以引用上下文中的其他消息。"""
        tool = bind(QQSendReplyTool, make_message())

        ok, result = await tool.execute("收到", "other-msg")

        assert ok is True
        assert result["reference_message_id"] == "other-msg"

    @pytest.mark.parametrize("content", ["", "   "])
    async def test_rejects_empty_content(
        self, patch_send_handler, content: str
    ) -> None:
        """回复内容不能为空。"""
        ok, _ = await bind(QQSendReplyTool, make_message()).execute(content)
        assert ok is False
        assert patch_send_handler.posts == []

    async def test_rejects_when_no_reference_available(
        self, patch_send_handler
    ) -> None:
        """触发消息缺 id 且未显式指定时无法引用。"""
        message = make_message(ref_idx="")
        tool = bind(QQSendReplyTool, message)

        ok, result = await tool.execute("收到")

        assert ok is False
        assert "引用" in result

    async def test_no_target(self) -> None:
        """无触发消息时不发请求。"""
        ok, _ = await bind(QQSendReplyTool, None).execute("收到")
        assert ok is False

    async def test_reports_send_failure(self, patch_send_handler) -> None:
        """底层失败时返回结构化原因，且不泄漏原始错误。"""
        patch_send_handler.post_result = {"code": 22009, "message": "msg limit exceed"}
        tool = bind(QQSendReplyTool, make_message())

        ok, result = await tool.execute("收到")

        assert ok is False
        assert "msg limit exceed" not in result
