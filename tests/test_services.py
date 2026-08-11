"""三个 Service 的行为测试。

重点验证：消息体拼装是否符合 QQ 官方字段定义、被动回复字段的填充规则、
raw 通道的双重开关，以及所有失败路径都返回结构化错误而非抛异常。
"""

from __future__ import annotations

import pytest

from ..services import message_service as message_service_module
from ..services.interaction_service import QQBotInteractionService
from ..services.message_service import QQBotMessageService
from ..services.raw_service import QQBotRawService
from ..src.builders import build_button
from ..src.constants import (
    API_BASE_PRODUCTION,
    MSG_SEQ_MAX,
    MSG_TYPE_ARK,
    MSG_TYPE_EMBED,
    MSG_TYPE_MARKDOWN,
    MSG_TYPE_MEDIA,
    MSG_TYPE_TEXT,
)
from .conftest import FakeHttpClient, FakeResponse, make_plugin


@pytest.fixture
def message_service(patch_send_handler) -> QQBotMessageService:
    """构造已接好桥接替身的消息 Service。

    Args:
        patch_send_handler: 被打补丁的 SendHandler 替身。

    Returns:
        QQBotMessageService 实例。
    """
    return QQBotMessageService(make_plugin(http_client=FakeHttpClient()))


def sent_body(handler) -> dict:
    """取出最近一次 POST 的请求体。

    Args:
        handler: SendHandler 替身。

    Returns:
        请求体字典。
    """
    return handler.posts[-1][2]


def sent_url(handler) -> str:
    """取出最近一次 POST 的 URL。

    Args:
        handler: SendHandler 替身。

    Returns:
        完整 URL。
    """
    return handler.posts[-1][0]


class TestMessageRouting:
    """发送目标解析。"""

    async def test_user_path(self, message_service, patch_send_handler) -> None:
        """单聊走 /v2/users/{openid}/messages。"""
        await message_service.send_reply("user", "u1", "hi", "m0")
        assert sent_url(patch_send_handler).endswith("/v2/users/u1/messages")

    async def test_group_path(self, message_service, patch_send_handler) -> None:
        """群聊走 /v2/groups/{openid}/messages。"""
        await message_service.send_reply("group", "g1", "hi", "m0")
        assert sent_url(patch_send_handler).endswith("/v2/groups/g1/messages")

    async def test_group_payload_always_has_content(
        self, message_service, patch_send_handler
    ) -> None:
        """群聊接口把 content 标为必填，非文本消息也需占位。"""
        await message_service.send_ark("group", "g1", 23, [{"key": "k", "value": "v"}])
        assert sent_body(patch_send_handler)["content"] == ""

    async def test_returns_message_id(self, message_service) -> None:
        """成功时透出 QQ 返回的消息 id。"""
        result = await message_service.send_reply("user", "u1", "hi", "m0")
        assert result == {"success": True, "message_id": "msg-1", "error": None}

    @pytest.mark.parametrize(
        ("target_type", "target_id"),
        [("channel", "x"), ("", "x"), ("user", ""), ("user", "   ")],
    )
    async def test_rejects_bad_target(
        self, message_service, target_type: str, target_id: str
    ) -> None:
        """非法目标在发请求前就被拦下。"""
        result = await message_service.send_reply(target_type, target_id, "hi", "m0")
        assert result["success"] is False
        assert result["message_id"] == ""


class TestPassiveFields:
    """被动回复字段。"""

    async def test_msg_id_defaults_msg_seq_to_one(
        self, message_service, patch_send_handler
    ) -> None:
        """带 msg_id 时 msg_seq 缺省为 1。"""
        await message_service.send_reply("user", "u1", "hi", "m0", msg_id="mid")
        body = sent_body(patch_send_handler)
        assert body["msg_id"] == "mid"
        assert body["msg_seq"] == 1

    async def test_explicit_msg_seq(self, message_service, patch_send_handler) -> None:
        """显式 msg_seq 优先。"""
        await message_service.send_reply(
            "user", "u1", "hi", "m0", msg_id="mid", msg_seq=7
        )
        assert sent_body(patch_send_handler)["msg_seq"] == 7

    async def test_event_id(self, message_service, patch_send_handler) -> None:
        """event_id 用于 INTERACTION_CREATE 等事件的被动回复。"""
        await message_service.send_reply("user", "u1", "hi", "m0", event_id="ev1")
        assert sent_body(patch_send_handler)["event_id"] == "ev1"

    async def test_proactive_has_no_passive_fields(
        self, message_service, patch_send_handler
    ) -> None:
        """不带 msg_id/event_id 时为主动推送，不应混入被动字段。"""
        await message_service.send_reply("user", "u1", "hi", "m0")
        body = sent_body(patch_send_handler)
        assert "msg_id" not in body
        assert "event_id" not in body
        assert "msg_seq" not in body

    @pytest.mark.parametrize("msg_seq", [0, -1, MSG_SEQ_MAX + 1, "1", True])
    async def test_rejects_bad_msg_seq(self, message_service, msg_seq: object) -> None:
        """msg_seq 越界或类型错误时拒绝。"""
        result = await message_service.send_reply(
            "user",
            "u1",
            "hi",
            "m0",
            msg_seq=msg_seq,  # type: ignore[arg-type]
        )
        assert result["success"] is False


class TestSendKeyboard:
    """按钮消息。"""

    async def test_builds_markdown_plus_keyboard(
        self, message_service, patch_send_handler
    ) -> None:
        """keyboard 必须挂载在 markdown 上（msg_type=2）。"""
        rows = [[build_button("A"), build_button("B")]]
        result = await message_service.send_keyboard(
            "group", "g1", rows, content="选择："
        )

        assert result["success"] is True
        body = sent_body(patch_send_handler)
        assert body["msg_type"] == MSG_TYPE_MARKDOWN
        assert body["markdown"] == {"content": "选择："}
        assert len(body["keyboard"]["content"]["rows"][0]["buttons"]) == 2

    async def test_supports_template_markdown(
        self, message_service, patch_send_handler
    ) -> None:
        """也可以用已报备的模板承载按钮。"""
        await message_service.send_keyboard(
            "user",
            "u1",
            [[build_button("A")]],
            custom_template_id="t1",
            params=[{"key": "title", "values": ["标题"]}],
        )
        assert sent_body(patch_send_handler)["markdown"]["custom_template_id"] == "t1"

    async def test_requires_markdown_carrier(self, message_service) -> None:
        """content 与 custom_template_id 都不给时拒绝。"""
        result = await message_service.send_keyboard(
            "user", "u1", [[build_button("A")]]
        )
        assert result["success"] is False

    async def test_propagates_builder_error(self, message_service) -> None:
        """构造层的校验错误应转成结构化返回而非抛出。"""
        result = await message_service.send_keyboard("user", "u1", [], content="x")
        assert result["success"] is False
        assert result["error"]


class TestSendArk:
    """ark 消息。"""

    async def test_payload(self, message_service, patch_send_handler) -> None:
        """msg_type=3，ark 字段承载模板与 kv。"""
        kv = [{"key": "#DESC#", "value": "x"}]
        await message_service.send_ark("user", "u1", 23, kv)

        body = sent_body(patch_send_handler)
        assert body["msg_type"] == MSG_TYPE_ARK
        assert body["ark"] == {"template_id": 23, "kv": kv}

    async def test_rejects_empty_kv(self, message_service) -> None:
        """kv 为空时拒绝。"""
        assert (await message_service.send_ark("user", "u1", 23, []))[
            "success"
        ] is False


class TestSendEmbedAndMarkdown:
    """embed 与模板 Markdown。"""

    async def test_embed_payload(self, message_service, patch_send_handler) -> None:
        """msg_type=4，embed 字段承载标题与条目。"""
        await message_service.send_embed(
            "user", "u1", "标题", thumbnail_url="https://img", fields=["a"]
        )
        body = sent_body(patch_send_handler)
        assert body["msg_type"] == MSG_TYPE_EMBED
        assert body["embed"]["title"] == "标题"
        assert body["embed"]["fields"] == [{"name": "a"}]

    async def test_embed_rejects_empty_title(self, message_service) -> None:
        """title 为空时拒绝。"""
        assert (await message_service.send_embed("user", "u1", ""))["success"] is False

    async def test_markdown_template_payload(
        self, message_service, patch_send_handler
    ) -> None:
        """模板 Markdown 走 msg_type=2 + custom_template_id。"""
        params = [{"key": "title", "values": ["标题"]}]
        await message_service.send_markdown_template("user", "u1", "t1", params)

        body = sent_body(patch_send_handler)
        assert body["msg_type"] == MSG_TYPE_MARKDOWN
        assert body["markdown"] == {"custom_template_id": "t1", "params": params}
        assert "keyboard" not in body

    async def test_markdown_template_with_buttons(
        self, message_service, patch_send_handler
    ) -> None:
        """模板消息可以附带按钮。"""
        await message_service.send_markdown_template(
            "user", "u1", "t1", rows=[[build_button("A")]]
        )
        assert "keyboard" in sent_body(patch_send_handler)


class TestSendReply:
    """引用回复。"""

    async def test_event_id_only_text_reply(
        self, message_service, patch_send_handler
    ) -> None:
        """普通文本回复互动事件时只携带 event_id。"""
        result = await message_service.send_text("user", "u1", "完成", event_id="e1")
        body = sent_body(patch_send_handler)
        assert result["success"] is True
        assert body == {"msg_type": MSG_TYPE_TEXT, "content": "完成", "event_id": "e1"}
        assert "msg_id" not in body

    async def test_text_rejects_empty_and_conflicting_sources(
        self, message_service, patch_send_handler
    ) -> None:
        """文本为空或同时传 msg_id/event_id 时不发请求。"""
        assert (await message_service.send_text("user", "u1", ""))["success"] is False
        assert (
            await message_service.send_text(
                "user", "u1", "x", msg_id="m1", event_id="e1"
            )
        )["success"] is False
        assert patch_send_handler.posts == []

    async def test_payload(self, message_service, patch_send_handler) -> None:
        """msg_type=0 + message_reference。"""
        await message_service.send_reply("user", "u1", "回复内容", "m0")
        body = sent_body(patch_send_handler)
        assert body["msg_type"] == MSG_TYPE_TEXT
        assert body["content"] == "回复内容"
        assert body["message_reference"] == {
            "message_id": "m0",
            "ignore_get_message_error": False,
        }

    async def test_ignore_flag(self, message_service, patch_send_handler) -> None:
        """可选择忽略拉取被引用消息的错误。"""
        await message_service.send_reply(
            "user", "u1", "x", "m0", ignore_get_message_error=True
        )
        ref = sent_body(patch_send_handler)["message_reference"]
        assert ref["ignore_get_message_error"] is True

    @pytest.mark.parametrize(("content", "ref"), [("", "m0"), ("   ", "m0"), ("x", "")])
    async def test_rejects_empty_fields(
        self, message_service, content: str, ref: str
    ) -> None:
        """内容与被引用 id 均为必填。"""
        result = await message_service.send_reply("user", "u1", content, ref)
        assert result["success"] is False


class TestSendMedia:
    """富媒体 URL 上传与发送。"""

    @pytest.fixture(autouse=True)
    def public_dns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认把测试域名解析到公网地址。"""

        async def validate(url: str) -> tuple[str | None, str]:
            """为非安全专项测试提供确定的公网 URL 结果。"""
            return None, url.strip()

        monkeypatch.setattr(
            message_service_module, "_validate_public_media_url", validate
        )

    async def test_upload_user_media(self, message_service, patch_send_handler) -> None:
        """单聊上传使用 /files 且不直接发送。"""
        patch_send_handler.post_result = {
            "file_uuid": "f1",
            "file_info": "opaque",
            "ttl": 300,
            "raw_url": "should-not-leak",
        }
        result = await message_service.upload_media_from_url(
            "user", "u1", 1, "https://cdn.example/a.png", file_name="a.png"
        )

        assert sent_url(patch_send_handler).endswith("/v2/users/u1/files")
        assert sent_body(patch_send_handler) == {
            "file_type": 1,
            "url": "https://cdn.example/a.png",
            "srv_send_msg": False,
            "file_name": "a.png",
        }
        assert result == {
            "success": True,
            "message_id": "",
            "media": {"file_uuid": "f1", "file_info": "opaque", "ttl": 300},
            "error": None,
        }

    async def test_upload_group_media(
        self, message_service, patch_send_handler
    ) -> None:
        """群聊上传使用群文件端点。"""
        patch_send_handler.post_result = {"file_info": "opaque"}
        await message_service.upload_media_from_url(
            "group", "g1", 4, "https://cdn.example/a.bin"
        )
        assert sent_url(patch_send_handler).endswith("/v2/groups/g1/files")
        assert "file_name" not in sent_body(patch_send_handler)

    @pytest.mark.parametrize("file_type", [1, 2, 3, 4])
    async def test_accepts_all_file_types(
        self, message_service, patch_send_handler, file_type: int
    ) -> None:
        """官方定义的四种媒体类型均允许上传。"""
        patch_send_handler.post_result = {"file_info": "opaque"}
        result = await message_service.upload_media_from_url(
            "user", "u1", file_type, "https://cdn.example/media"
        )
        assert result["success"] is True

    @pytest.mark.parametrize("file_type", [0, 5, True, "1"])
    async def test_rejects_bad_file_type(
        self, message_service, patch_send_handler, file_type: object
    ) -> None:
        """未知或类型错误的 file_type 在请求前拒绝。"""
        result = await message_service.upload_media_from_url(
            "user",
            "u1",
            file_type,
            "https://cdn.example/a",  # type: ignore[arg-type]
        )
        assert result["success"] is False
        assert patch_send_handler.posts == []

    async def test_requires_upload_file_info(
        self, message_service, patch_send_handler
    ) -> None:
        """上传响应不含 file_info 时不得伪装成功。"""
        patch_send_handler.post_result = {"file_uuid": "f1"}
        result = await message_service.upload_media_from_url(
            "user", "u1", 1, "https://cdn.example/a.png"
        )
        assert result["success"] is False
        assert "file_info" in result["error"]

    async def test_send_existing_file_info(
        self, message_service, patch_send_handler
    ) -> None:
        """已有 file_info 可跳过上传直接发送。"""
        result = await message_service.send_media("group", "g1", "opaque", msg_id="m1")
        body = sent_body(patch_send_handler)
        assert body["msg_type"] == MSG_TYPE_MEDIA
        assert body["media"] == {"file_info": "opaque"}
        assert body["content"] == ""
        assert body["msg_id"] == "m1"
        assert body["msg_seq"] == 1
        assert result["media"] is None

    async def test_rejects_passive_source_conflict(
        self, message_service, patch_send_handler
    ) -> None:
        """官方要求 msg_id 与 event_id 二选一。"""
        result = await message_service.send_media(
            "user", "u1", "opaque", msg_id="m1", event_id="e1"
        )
        assert result["success"] is False
        assert patch_send_handler.posts == []

    async def test_upload_then_send(self, message_service, patch_send_handler) -> None:
        """一站式入口严格先上传再发送。"""
        patch_send_handler.post_results = [
            {"file_uuid": "f1", "file_info": "opaque", "ttl": 60},
            {"id": "msg-media"},
        ]
        result = await message_service.send_media_from_url(
            "user",
            "u1",
            2,
            "https://cdn.example/a.mp4",
            event_id="e1",
        )

        assert len(patch_send_handler.posts) == 2
        assert patch_send_handler.posts[0][0].endswith("/v2/users/u1/files")
        assert patch_send_handler.posts[1][0].endswith("/v2/users/u1/messages")
        assert patch_send_handler.posts[1][2]["event_id"] == "e1"
        assert result["message_id"] == "msg-media"
        assert result["media"]["file_info"] == "opaque"

    async def test_upload_failure_stops_send(
        self, message_service, patch_send_handler
    ) -> None:
        """上传失败时不进入消息发送阶段。"""
        patch_send_handler.post_result = {"code": 850026, "message": "download fail"}
        result = await message_service.send_media_from_url(
            "user", "u1", 1, "https://cdn.example/a.png"
        )
        assert result["success"] is False
        assert len(patch_send_handler.posts) == 1

    async def test_send_failure_keeps_media(
        self, message_service, patch_send_handler
    ) -> None:
        """上传成功但发送失败时保留 file_info 供重试。"""
        patch_send_handler.post_results = [
            {"file_uuid": "f1", "file_info": "opaque", "ttl": 60},
            {"code": 304080, "message": "invalid media"},
        ]
        result = await message_service.send_media_from_url(
            "user", "u1", 1, "https://cdn.example/a.png"
        )
        assert result["success"] is False
        assert result["media"]["file_info"] == "opaque"

    async def test_normalizes_invalid_ttl(
        self, message_service, patch_send_handler
    ) -> None:
        """非整数 ttl 按未知有效期归一为 0。"""
        patch_send_handler.post_result = {
            "file_uuid": "f1",
            "file_info": "opaque",
            "ttl": "300",
        }
        result = await message_service.upload_media_from_url(
            "user", "u1", 1, "https://cdn.example/a.png"
        )
        assert result["media"]["ttl"] == 0

    async def test_rejects_non_string_file_name(
        self, message_service, patch_send_handler
    ) -> None:
        """file_name 类型错误时不发请求。"""
        result = await message_service.upload_media_from_url(
            "user",
            "u1",
            1,
            "https://cdn.example/a.png",
            file_name=1,  # type: ignore[arg-type]
        )
        assert result["success"] is False
        assert patch_send_handler.posts == []

    async def test_rejects_url_validator_error(
        self,
        message_service,
        patch_send_handler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """URL 安全校验失败时不调用 QQ API。"""

        async def reject(url: str) -> tuple[str | None, str]:
            """返回预置安全错误。"""
            return "url 只能指向公网地址", ""

        monkeypatch.setattr(
            message_service_module, "_validate_public_media_url", reject
        )
        result = await message_service.upload_media_from_url(
            "user", "u1", 1, "https://private.example/a.png"
        )
        assert result["success"] is False
        assert patch_send_handler.posts == []

    async def test_rejects_empty_file_info(
        self, message_service, patch_send_handler
    ) -> None:
        """直接发送时 file_info 不能为空。"""
        result = await message_service.send_media("user", "u1", "   ")
        assert result["success"] is False
        assert patch_send_handler.posts == []

    async def test_rejects_bad_msg_seq_before_upload(
        self, message_service, patch_send_handler
    ) -> None:
        """一站式发送应在上传前校验回复序号。"""
        result = await message_service.send_media_from_url(
            "user", "u1", 1, "https://cdn.example/a.png", msg_seq=0
        )
        assert result["success"] is False
        assert patch_send_handler.posts == []

    async def test_rejects_passive_conflict_before_upload(
        self, message_service, patch_send_handler
    ) -> None:
        """一站式发送应在上传前校验被动来源互斥。"""
        result = await message_service.send_media_from_url(
            "user",
            "u1",
            1,
            "https://cdn.example/a.png",
            msg_id="m1",
            event_id="e1",
        )
        assert result["success"] is False
        assert patch_send_handler.posts == []


class TestMediaUrlSecurity:
    """媒体 URL SSRF 防护。"""

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "ftp://example.com/a",
            "https:///a",
            "https://user:pass@example.com/a",
            "https://example.com:99999/a",
            "https://127.0.0.1/a",
            "https://10.0.0.1/a",
            "https://169.254.1.1/a",
            "https://[::1]/a",
        ],
    )
    async def test_rejects_unsafe_url(self, url: str) -> None:
        """非 HTTP(S) 或非公网地址必须拒绝。"""
        error, _ = await message_service_module._validate_public_media_url(url)
        assert error

    async def test_accepts_public_ip(self) -> None:
        """公网 IP 地址允许交给 QQ 下载。"""
        error, normalized = await message_service_module._validate_public_media_url(
            " https://8.8.8.8/a.png "
        )
        assert error is None
        assert normalized == "https://8.8.8.8/a.png"

    async def test_rejects_dns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """域名解析失败时默认拒绝。"""
        loop = __import__("asyncio").get_running_loop()

        async def fail(*args, **kwargs):
            """模拟 DNS 失败。"""
            raise OSError("dns failed")

        monkeypatch.setattr(loop, "getaddrinfo", fail)
        error, _ = await message_service_module._validate_public_media_url(
            "https://missing.example/a"
        )
        assert error

    async def test_rejects_empty_dns_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """域名没有任何解析结果时拒绝。"""
        loop = __import__("asyncio").get_running_loop()

        async def empty(*args, **kwargs):
            """返回空解析结果。"""
            return []

        monkeypatch.setattr(loop, "getaddrinfo", empty)
        error, _ = await message_service_module._validate_public_media_url(
            "https://empty.example/a"
        )
        assert error

    async def test_rejects_invalid_dns_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """解析器返回非法 IP 时按 DNS 失败拒绝。"""
        loop = __import__("asyncio").get_running_loop()

        async def invalid(*args, **kwargs):
            """返回非法地址。"""
            return [(2, 1, 6, "", ("not-an-ip", 443))]

        monkeypatch.setattr(loop, "getaddrinfo", invalid)
        error, _ = await message_service_module._validate_public_media_url(
            "https://invalid.example/a"
        )
        assert error

    async def test_rejects_mixed_dns_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """域名同时解析到公网和私网时也必须拒绝。"""
        loop = __import__("asyncio").get_running_loop()

        async def mixed(*args, **kwargs):
            """返回公私混合解析结果。"""
            return [
                (2, 1, 6, "", ("8.8.8.8", 443)),
                (2, 1, 6, "", ("10.0.0.1", 443)),
            ]

        monkeypatch.setattr(loop, "getaddrinfo", mixed)
        error, _ = await message_service_module._validate_public_media_url(
            "https://mixed.example/a"
        )
        assert error

    """直投完整消息体。"""

    async def test_passthrough(self, message_service, patch_send_handler) -> None:
        """payload 原样投递。"""
        payload = {"msg_type": 7, "media": {"file_info": "abc"}}
        result = await message_service.send_raw_message("user", "u1", payload)

        assert result["success"] is True
        assert sent_body(patch_send_handler)["media"] == {"file_info": "abc"}

    @pytest.mark.parametrize("payload", [{}, None, "notdict"])
    async def test_rejects_invalid_payload(
        self, message_service, payload: object
    ) -> None:
        """payload 必须是非空字典。"""
        result = await message_service.send_raw_message("user", "u1", payload)  # type: ignore[arg-type]
        assert result["success"] is False

    async def test_rejects_payload_without_msg_type(self, message_service) -> None:
        """msg_type 是 QQ 侧必填字段。"""
        result = await message_service.send_raw_message("user", "u1", {"content": "x"})
        assert result["success"] is False
        assert "msg_type" in result["error"]

    async def test_rejects_bad_target_before_payload_check(
        self, message_service, patch_send_handler
    ) -> None:
        """目标非法时不发请求。"""
        result = await message_service.send_raw_message("bad", "u1", {"msg_type": 0})
        assert result["success"] is False
        assert patch_send_handler.posts == []


class TestInteractionService:
    """互动回调应答。"""

    @pytest.fixture
    def service(self, patch_send_handler) -> QQBotInteractionService:
        """构造互动 Service。

        Args:
            patch_send_handler: 被打补丁的 SendHandler 替身。

        Returns:
            QQBotInteractionService 实例。
        """
        client = FakeHttpClient([FakeResponse(200, {})])
        self.client = client
        return QQBotInteractionService(make_plugin(http_client=client))

    async def test_forces_production_domain(self, service) -> None:
        """应答接口沙箱不可用，必须打正式域名。"""
        result = await service.ack("i1", 2)

        assert result == {
            "success": True,
            "code": 2,
            "description": "操作频繁",
            "error": None,
            "duplicate": False,
        }
        call = self.client.calls[0]
        assert call["method"] == "PUT"
        assert call["url"] == f"{API_BASE_PRODUCTION}/interactions/i1"
        assert call["json"] == {"code": 2}

    async def test_default_code_is_zero(self, service) -> None:
        """默认应答"操作成功"。"""
        await service.ack("i1")
        assert self.client.calls[0]["json"] == {"code": 0}

    @pytest.mark.parametrize("code", [-1, 6, 99, "0", True])
    async def test_rejects_unknown_code(self, service, code: object) -> None:
        """code 必须在官方定义的 0~5 之内。"""
        result = await service.ack("i1", code)  # type: ignore[arg-type]
        assert result["success"] is False
        assert result["duplicate"] is False
        assert self.client.calls == []

    @pytest.mark.parametrize("interaction_id", ["", "   ", None])
    async def test_rejects_empty_id(self, service, interaction_id: object) -> None:
        """interaction_id 是必填字段。"""
        result = await service.ack(interaction_id)  # type: ignore[arg-type]
        assert result["success"] is False

    async def test_http_failure_is_reported(self, patch_send_handler) -> None:
        """HTTP 失败时返回结构化错误。"""
        client = FakeHttpClient([FakeResponse(404, {})])
        service = QQBotInteractionService(make_plugin(http_client=client))

        result = await service.ack("i1")

        assert result["success"] is False
        assert result["error"]

    def test_register_and_unregister_return_structured_results(self, service) -> None:
        """Service 屏蔽常规冲突并支持替换及 callback 身份保护。"""

        def original(_context, _payload):
            """返回成功应答码。"""
            return 0

        def replacement(_context, _payload):
            """返回失败应答码。"""
            return 1

        assert service.register_callback("demo", "run", original) == {
            "success": True,
            "registered": True,
            "error": None,
        }
        conflict = service.register_callback("demo", "run", replacement)
        assert conflict["success"] is False
        assert conflict["registered"] is False
        assert conflict["error"]
        assert service.register_callback("demo", "run", replacement, replace=True) == {
            "success": True,
            "registered": True,
            "error": None,
        }
        mismatch = service.unregister_callback("demo", "run", original)
        assert mismatch["success"] is False
        assert mismatch["removed"] is False
        assert mismatch["error"]
        assert service.unregister_callback("demo", "run", replacement) == {
            "success": True,
            "removed": True,
            "error": None,
        }

    async def test_register_after_close_returns_structured_error(self, service) -> None:
        """运行时关闭后注册失败也不泄漏异常。"""
        await service.plugin.interaction_runtime.close()

        result = service.register_callback("demo", "run", lambda _ctx, _payload: 0)

        assert result["success"] is False
        assert result["registered"] is False
        assert result["error"]

    def test_describe_code(self) -> None:
        """应答码文案查询。"""
        assert QQBotInteractionService.describe_code(4) == "没有权限"
        assert QQBotInteractionService.describe_code(99) == ""

    @pytest.mark.parametrize(
        ("interaction_type", "expected"),
        [(11, True), (12, True), (13, False), (18, False)],
    )
    def test_needs_ack(self, interaction_type: int, expected: bool) -> None:
        """只有消息按钮与单聊快捷菜单需要应答。"""
        assert QQBotInteractionService.needs_ack(interaction_type) is expected


class TestRawService:
    """通用 API 通道。"""

    async def test_forwards_request(self, patch_send_handler) -> None:
        """合法请求透传到桥接层。"""
        client = FakeHttpClient([FakeResponse(200, {"id": "bot"})])
        service = QQBotRawService(make_plugin(http_client=client))

        result = await service.request("get", "/users/@me")

        assert result["success"] is True
        assert client.calls[0]["method"] == "GET"

    async def test_blocked_by_master_switch(self, patch_send_handler) -> None:
        """allow_raw_request=False 时一律拒绝。"""
        client = FakeHttpClient()
        service = QQBotRawService(
            make_plugin(http_client=client, allow_raw_request=False)
        )

        result = await service.request("GET", "/users/@me")

        assert result["success"] is False
        assert "allow_raw_request" in result["error"]
        assert client.calls == []

    async def test_blocked_by_method_whitelist(self, patch_send_handler) -> None:
        """方法不在白名单内时拒绝。"""
        client = FakeHttpClient()
        service = QQBotRawService(
            make_plugin(http_client=client, raw_allowed_methods=["GET"])
        )

        result = await service.request("DELETE", "/x")

        assert result["success"] is False
        assert "raw_allowed_methods" in result["error"]
        assert client.calls == []

    async def test_whitelist_is_case_insensitive(self, patch_send_handler) -> None:
        """白名单配置大小写不敏感。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        service = QQBotRawService(
            make_plugin(http_client=client, raw_allowed_methods=["get"])
        )

        assert (await service.request("GET", "/x"))["success"] is True

    async def test_empty_whitelist_falls_back_to_all(self, patch_send_handler) -> None:
        """白名单为空视为未配置，回落到全部受支持方法。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        service = QQBotRawService(
            make_plugin(http_client=client, raw_allowed_methods=[])
        )

        assert (await service.request("PUT", "/x", {}))["success"] is True

    async def test_supports_patch(self, patch_send_handler) -> None:
        """PATCH 走本插件 HTTP 客户端。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        service = QQBotRawService(make_plugin(http_client=client))

        assert (await service.request("PATCH", "/x", {"enabled": True}))["success"] is True
        assert client.calls[0]["method"] == "PATCH"

    @pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "TRACE", ""])
    async def test_rejects_unsupported_method(
        self, patch_send_handler, method: str
    ) -> None:
        """只支持 GET/POST/PUT/PATCH/DELETE。"""
        client = FakeHttpClient()
        service = QQBotRawService(make_plugin(http_client=client))

        result = await service.request(method, "/x")

        assert result["success"] is False
        assert client.calls == []

    async def test_rejects_absolute_url(self, patch_send_handler) -> None:
        """SSRF 防线：绝对 URL 不放行。"""
        client = FakeHttpClient()
        service = QQBotRawService(make_plugin(http_client=client))

        result = await service.request("GET", "https://evil.com/steal")

        assert result["success"] is False
        assert client.calls == []

    async def test_force_production_is_forwarded(self, patch_send_handler) -> None:
        """force_production 需要传到桥接层。"""
        client = FakeHttpClient([FakeResponse(200, {})])
        service = QQBotRawService(make_plugin(http_client=client))

        await service.request(
            "PUT", "/interactions/i1", {"code": 0}, force_production=True
        )

        assert client.calls[0]["url"].startswith(API_BASE_PRODUCTION)

    async def test_status_without_adapter(self, monkeypatch) -> None:
        """适配器服务不可用时给出 connected=False。"""
        service = QQBotRawService(make_plugin(http_client=FakeHttpClient()))

        status = await service.get_status()

        assert status["connected"] is False
        assert status["http_client_ready"] is True
        assert status["raw_enabled"] is True

    async def test_status_survives_lookup_failure(self, monkeypatch) -> None:
        """服务注册表查询本身抛异常时也不能崩。"""
        from src.app.plugin_system.api import service_api

        def boom(_sig: str) -> object:
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr(service_api, "get_service", boom)
        service = QQBotRawService(make_plugin(http_client=FakeHttpClient()))

        status = await service.get_status()

        assert status["connected"] is False
        assert status["error"]

    async def test_status_ignores_non_dict_adapter_state(self, monkeypatch) -> None:
        """适配器返回非字典时忽略，不污染状态结构。"""
        from src.app.plugin_system.api import service_api

        class WeirdAdapterService:
            async def get_status(self) -> str:
                return "not-a-dict"

        monkeypatch.setattr(
            service_api, "get_service", lambda _sig: WeirdAdapterService()
        )
        service = QQBotRawService(make_plugin(http_client=FakeHttpClient()))

        status = await service.get_status()

        assert status["http_client_ready"] is True
        assert "connected" not in status

    async def test_status_merges_adapter_state(self, monkeypatch) -> None:
        """适配器可用时合并其状态字段。"""
        from src.app.plugin_system.api import service_api

        class FakeAdapterService:
            async def get_status(self) -> dict:
                return {"connected": True, "bot_id": "b1", "env": "sandbox"}

        monkeypatch.setattr(
            service_api, "get_service", lambda _sig: FakeAdapterService()
        )
        service = QQBotRawService(make_plugin(http_client=FakeHttpClient()))

        status = await service.get_status()

        assert status["connected"] is True
        assert status["bot_id"] == "b1"
        assert status["http_client_ready"] is True

    async def test_status_reports_missing_http_client(self) -> None:
        """未加载完成时 http_client_ready 为 False。"""
        service = QQBotRawService(make_plugin(http_client=None))
        assert (await service.get_status())["http_client_ready"] is False
