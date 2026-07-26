"""``src/builders.py`` 的构造与校验测试。

断言点全部对齐 QQ 官方文档（msg-btn / ark / embed / markdown / text-chain）。
"""
from __future__ import annotations

import pytest

from ..src.builders import (
    at_everyone,
    at_user,
    build_ark,
    build_button,
    build_embed,
    build_keyboard,
    build_markdown,
    build_message_reference,
    cmd_enter,
    cmd_input,
    emoji,
)
from ..src.constants import (
    ACTION_TYPE_CALLBACK,
    ACTION_TYPE_LINK,
    BUTTON_STYLE_BLUE,
    COMMAND_TEXT_MAX_LENGTH,
    KEYBOARD_MAX_BUTTONS_PER_ROW,
    KEYBOARD_MAX_ROWS,
    PERMISSION_TYPE_SPECIFY_ROLE,
    PERMISSION_TYPE_SPECIFY_USER,
)


class TestBuildButton:
    """按钮构造。"""

    def test_default_shape_matches_official_schema(self) -> None:
        """默认按钮应含 render_data 与 action 两段。"""
        button = build_button("确定")
        assert button["render_data"] == {
            "label": "确定",
            "visited_label": "确定",
            "style": 0,
        }
        assert button["action"]["type"] == 2
        assert button["action"]["permission"] == {"type": 2}
        assert "id" not in button

    def test_button_id_and_visited_label(self) -> None:
        """显式传入 id 与 visited_label 时应原样带上。"""
        button = build_button("下一页", button_id="7", visited_label="已翻页")
        assert button["id"] == "7"
        assert button["render_data"]["visited_label"] == "已翻页"

    def test_link_button(self) -> None:
        """跳转按钮的 data 为 URL。"""
        button = build_button(
            "官网",
            style=BUTTON_STYLE_BLUE,
            action_type=ACTION_TYPE_LINK,
            data="https://example.com",
        )
        assert button["render_data"]["style"] == 1
        assert button["action"]["type"] == 0
        assert button["action"]["data"] == "https://example.com"

    def test_anchor_only_present_when_set(self) -> None:
        """anchor 为 0 时不应写入结构。"""
        assert "anchor" not in build_button("A")["action"]
        assert build_button("A", anchor=1)["action"]["anchor"] == 1

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"style": 9},
            {"action_type": 9},
            {"permission_type": 9},
        ],
    )
    def test_rejects_unknown_enum(self, kwargs: dict) -> None:
        """枚举值超出官方定义时拒绝。"""
        with pytest.raises(ValueError):
            build_button("A", **kwargs)

    def test_rejects_empty_label(self) -> None:
        """label 是必填字段。"""
        with pytest.raises(ValueError, match="label"):
            build_button("")

    def test_command_data_length_limit(self) -> None:
        """指令按钮 data 超过 100 字符时拒绝。"""
        with pytest.raises(ValueError, match="100"):
            build_button("A", data="x" * (COMMAND_TEXT_MAX_LENGTH + 1))

    def test_link_button_allows_long_data(self) -> None:
        """长度限制只作用于指令按钮。"""
        long_url = "https://example.com/" + "x" * 200
        button = build_button("A", action_type=ACTION_TYPE_LINK, data=long_url)
        assert button["action"]["data"] == long_url

    def test_specify_user_permission_requires_ids(self) -> None:
        """permission_type=0 必须给用户白名单。"""
        with pytest.raises(ValueError, match="specify_user_ids"):
            build_button("A", permission_type=PERMISSION_TYPE_SPECIFY_USER)

        button = build_button(
            "A", permission_type=PERMISSION_TYPE_SPECIFY_USER, specify_user_ids=["u1"]
        )
        assert button["action"]["permission"]["specify_user_ids"] == ["u1"]

    def test_specify_role_permission_requires_ids(self) -> None:
        """permission_type=3 必须给身份组白名单。"""
        with pytest.raises(ValueError, match="specify_role_ids"):
            build_button("A", permission_type=PERMISSION_TYPE_SPECIFY_ROLE)

        button = build_button(
            "A", permission_type=PERMISSION_TYPE_SPECIFY_ROLE, specify_role_ids=["r1"]
        )
        assert button["action"]["permission"]["specify_role_ids"] == ["r1"]

    def test_callback_button_carries_data(self) -> None:
        """回调按钮的 data 会在 INTERACTION_CREATE 中原样带回。"""
        button = build_button("投票", action_type=ACTION_TYPE_CALLBACK, data="vote:1")
        assert button["action"]["type"] == 1
        assert button["action"]["data"] == "vote:1"


class TestBuildKeyboard:
    """键盘构造。"""

    def test_wraps_rows(self) -> None:
        """输出结构应为 content.rows[].buttons[]。"""
        keyboard = build_keyboard([[build_button("A"), build_button("B")]])
        assert list(keyboard) == ["content"]
        assert len(keyboard["content"]["rows"]) == 1
        assert len(keyboard["content"]["rows"][0]["buttons"]) == 2

    def test_rejects_empty(self) -> None:
        """至少要有一行按钮。"""
        with pytest.raises(ValueError):
            build_keyboard([])

    def test_rejects_empty_row(self) -> None:
        """空行会被 QQ 拒绝，这里提前拦下。"""
        with pytest.raises(ValueError, match="第 1 行"):
            build_keyboard([[]])

    def test_rejects_too_many_rows(self) -> None:
        """最多 5 行。"""
        rows = [[build_button("A")] for _ in range(KEYBOARD_MAX_ROWS + 1)]
        with pytest.raises(ValueError, match=str(KEYBOARD_MAX_ROWS)):
            build_keyboard(rows)

    def test_rejects_too_many_buttons_per_row(self) -> None:
        """每行最多 5 个按钮。"""
        row = [build_button("A") for _ in range(KEYBOARD_MAX_BUTTONS_PER_ROW + 1)]
        with pytest.raises(ValueError, match=str(KEYBOARD_MAX_BUTTONS_PER_ROW)):
            build_keyboard([row])

    def test_accepts_full_grid(self) -> None:
        """5x5 是官方允许的上限。"""
        rows = [
            [build_button(f"{r}{c}") for c in range(KEYBOARD_MAX_BUTTONS_PER_ROW)]
            for r in range(KEYBOARD_MAX_ROWS)
        ]
        assert len(build_keyboard(rows)["content"]["rows"]) == KEYBOARD_MAX_ROWS


class TestBuildArk:
    """ark 构造。"""

    def test_basic(self) -> None:
        """template_id 与 kv 原样透出。"""
        ark = build_ark(23, [{"key": "#DESC#", "value": "x"}])
        assert ark == {"template_id": 23, "kv": [{"key": "#DESC#", "value": "x"}]}

    def test_accepts_obj_style_kv(self) -> None:
        """数组型变量使用 obj/obj_kv 结构。"""
        kv = [{"key": "#LIST#", "obj": [{"obj_kv": [{"key": "desc", "value": "a"}]}]}]
        assert build_ark(23, kv)["kv"] == kv

    @pytest.mark.parametrize("template_id", [0, -1, "23"])
    def test_rejects_bad_template_id(self, template_id: object) -> None:
        """template_id 必须是正整数。"""
        with pytest.raises(ValueError):
            build_ark(template_id, [{"key": "k", "value": "v"}])  # type: ignore[arg-type]

    def test_rejects_empty_kv(self) -> None:
        """kv 是必填字段。"""
        with pytest.raises(ValueError):
            build_ark(23, [])

    def test_rejects_kv_without_key(self) -> None:
        """每个 kv 项都必须有 key。"""
        with pytest.raises(ValueError, match="key"):
            build_ark(23, [{"value": "v"}])


class TestBuildEmbed:
    """embed 构造。"""

    def test_prompt_defaults_to_title(self) -> None:
        """prompt 留空时复用 title。"""
        embed = build_embed("标题")
        assert embed == {"title": "标题", "prompt": "标题"}

    def test_optional_sections(self) -> None:
        """thumbnail 与 fields 均为选填。"""
        embed = build_embed(
            "标题", prompt="通知", thumbnail_url="https://img", fields=["a", "b"]
        )
        assert embed["prompt"] == "通知"
        assert embed["thumbnail"] == {"url": "https://img"}
        assert embed["fields"] == [{"name": "a"}, {"name": "b"}]

    def test_rejects_empty_title(self) -> None:
        """title 是必填字段。"""
        with pytest.raises(ValueError):
            build_embed("")


class TestBuildMarkdown:
    """markdown 构造。"""

    def test_native_content(self) -> None:
        """原生 markdown 只带 content。"""
        assert build_markdown(content="# hi") == {"content": "# hi"}

    def test_template_with_params(self) -> None:
        """模板 markdown 使用 custom_template_id + params。"""
        params = [{"key": "title", "values": ["标题"]}]
        assert build_markdown(custom_template_id="t1", params=params) == {
            "custom_template_id": "t1",
            "params": params,
        }

    def test_template_without_params(self) -> None:
        """无变量的模板可以不带 params。"""
        assert build_markdown(custom_template_id="t1") == {"custom_template_id": "t1"}

    def test_rejects_both_modes(self) -> None:
        """两种模式互斥。"""
        with pytest.raises(ValueError, match="互斥"):
            build_markdown(content="x", custom_template_id="t1")

    def test_rejects_neither_mode(self) -> None:
        """必须提供其中一种。"""
        with pytest.raises(ValueError):
            build_markdown()

    def test_rejects_params_without_values(self) -> None:
        """官方要求 params 项为 {key, values}。"""
        with pytest.raises(ValueError, match="values"):
            build_markdown(custom_template_id="t1", params=[{"key": "title"}])

    def test_rejects_params_without_key(self) -> None:
        """params 项缺 key 时拒绝。"""
        with pytest.raises(ValueError, match="key"):
            build_markdown(custom_template_id="t1", params=[{"values": ["v"]}])


class TestBuildMessageReference:
    """引用回复构造。"""

    def test_basic(self) -> None:
        """默认不忽略拉取错误。"""
        assert build_message_reference("m1") == {
            "message_id": "m1",
            "ignore_get_message_error": False,
        }

    def test_ignore_flag(self) -> None:
        """可选择忽略拉取错误继续发送。"""
        ref = build_message_reference("m1", ignore_get_message_error=True)
        assert ref["ignore_get_message_error"] is True

    def test_rejects_empty_id(self) -> None:
        """message_id 是必填字段。"""
        with pytest.raises(ValueError):
            build_message_reference("")


class TestTextChainTags:
    """文本内嵌交互标签。"""

    def test_at_user_uses_new_protocol(self) -> None:
        """使用新版 <qqbot-at-user />，而非将弃用的 <@id>。"""
        assert at_user("openid-1") == '<qqbot-at-user id="openid-1" />'

    def test_at_user_rejects_empty(self) -> None:
        """openid 是必填字段。"""
        with pytest.raises(ValueError):
            at_user("")

    def test_at_everyone(self) -> None:
        """@全体成员标签固定不变。"""
        assert at_everyone() == "<qqbot-at-everyone />"

    def test_emoji(self) -> None:
        """系统表情标签格式为 <emoji:id>。"""
        assert emoji(4) == "<emoji:4>"

    def test_emoji_rejects_negative(self) -> None:
        """表情 id 不能为负。"""
        with pytest.raises(ValueError):
            emoji(-1)

    def test_cmd_enter_urlencodes_text(self) -> None:
        """回车指令只带 text，且需要 urlencode。"""
        assert cmd_enter("查询") == '<qqbot-cmd-enter text="%E6%9F%A5%E8%AF%A2" />'

    def test_cmd_input_defaults_show_to_text(self) -> None:
        """show 留空时复用 text。"""
        tag = cmd_input("搜索")
        assert 'text="%E6%90%9C%E7%B4%A2"' in tag
        assert 'show="%E6%90%9C%E7%B4%A2"' in tag
        assert 'reference="false"' in tag

    def test_cmd_input_reference_flag(self) -> None:
        """reference 需要序列化成小写布尔串。"""
        assert 'reference="true"' in cmd_input("a", reference=True)

    def test_cmd_input_encodes_show_separately(self) -> None:
        """show 与 text 可以不同。"""
        tag = cmd_input("/search key", show="搜索")
        assert 'text="%2Fsearch%20key"' in tag
        assert 'show="%E6%90%9C%E7%B4%A2"' in tag

    @pytest.mark.parametrize("func", [cmd_enter, cmd_input])
    def test_rejects_empty_text(self, func) -> None:
        """指令文本不能为空。"""
        with pytest.raises(ValueError):
            func("")

    @pytest.mark.parametrize("func", [cmd_enter, cmd_input])
    def test_rejects_overlong_text(self, func) -> None:
        """指令文本上限 100 字符。"""
        with pytest.raises(ValueError, match=str(COMMAND_TEXT_MAX_LENGTH)):
            func("x" * (COMMAND_TEXT_MAX_LENGTH + 1))

    def test_cmd_input_rejects_overlong_show(self) -> None:
        """show 同样受 100 字符限制。"""
        with pytest.raises(ValueError):
            cmd_input("a", show="x" * (COMMAND_TEXT_MAX_LENGTH + 1))
