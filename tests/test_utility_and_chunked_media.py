"""撤回、分享、分片上传与入群申请事件测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..handlers.group_join_request_event_handler import QQBotGroupJoinRequestEventHandler
from ..services.chunked_media_service import QQBotChunkedMediaService
from ..services.utility_service import QQBotUtilityService
from ..src.sent_messages import SentMessageRegistry
from ..tools.utility import QQRecallCurrentMessageTool
from .conftest import FakeHttpClient, FakeResponse, make_plugin


class TestUtilityService:
    """验证消息撤回与分享链接边界。"""

    async def test_recalls_recorded_message(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        plugin = make_plugin(http_client=client)
        plugin.sent_messages.record("m1", "group", "g1")
        result = await QQBotUtilityService(plugin).recall_message("group", "g1", "m1")
        assert result["success"] is True
        assert client.calls[0]["method"] == "DELETE"
        assert client.calls[0]["url"].endswith("/v2/groups/g1/messages/m1")

    async def test_rejects_unrecorded_message(self) -> None:
        result = await QQBotUtilityService(make_plugin()).recall_message("group", "g1", "m1")
        assert result["success"] is False
        assert "本插件" in result["error"]

    async def test_generates_share_link(self, patch_send_handler) -> None:
        service = QQBotUtilityService(make_plugin(http_client=FakeHttpClient()))
        result = await service.generate_share_link(callback_data="source")
        assert result["success"] is True
        assert patch_send_handler.posts[-1][0].endswith("/v2/generate_url_link")
        assert patch_send_handler.posts[-1][2] == {"callback_data": "source"}

    async def test_rejects_long_callback_data(self) -> None:
        result = await QQBotUtilityService(make_plugin()).generate_share_link(callback_data="x" * 33)
        assert result["success"] is False


class TestChunkedMediaService:
    """验证受信 bytes 上传的官方阶段顺序。"""

    async def test_uploads_zero_based_parts_then_merges(self, patch_send_handler) -> None:
        client = FakeHttpClient([FakeResponse(200, {})])
        client.put = AsyncMock(return_value=FakeResponse(200, {}))
        plugin = make_plugin(http_client=client)
        patch_send_handler.post_results = [
            {"upload_id": "u1", "parts": [{"index": 0, "block_size": "3", "presigned_url": "https://cos.test/0"}]},
            {},
            {"file_info": "f1"},
        ]
        result = await QQBotChunkedMediaService(plugin).upload_bytes("group", "g1", 4, "a.txt", b"abc")
        assert result["success"] is True
        assert client.put.await_args.args[0] == "https://cos.test/0"
        assert patch_send_handler.posts[1][0].endswith("/upload_part_finish")
        assert patch_send_handler.posts[2][2]["upload_id"] == "u1"

    async def test_rejects_non_contiguous_part_indexes(self, patch_send_handler) -> None:
        plugin = make_plugin(http_client=FakeHttpClient())
        patch_send_handler.post_result = {"upload_id": "u1", "parts": [{"index": 1}]}
        result = await QQBotChunkedMediaService(plugin).upload_bytes("group", "g1", 4, "a.txt", b"abc")
        assert result["success"] is False
        assert "索引" in result["error"]

    async def test_rejects_invalid_part_size(self, patch_send_handler) -> None:
        plugin = make_plugin(http_client=FakeHttpClient())
        patch_send_handler.post_result = {
            "upload_id": "u1",
            "parts": [{"index": 0, "block_size": "invalid", "presigned_url": "https://cos.test/0"}],
        }
        result = await QQBotChunkedMediaService(plugin).upload_bytes("group", "g1", 4, "a.txt", b"abc")
        assert result["success"] is False
        assert "无效分片" in result["error"]


class TestJoinRequestEventHandler:
    """验证事件分发不会自动审批。"""

    async def test_deduplicates_and_dispatches_callback(self, monkeypatch) -> None:
        plugin = make_plugin()
        from ..src.join_requests import JoinRequestRuntime

        plugin.join_request_runtime = JoinRequestRuntime(plugin)
        received: list[dict] = []

        async def callback(params: dict) -> None:
            received.append(params)

        assert await plugin.join_request_runtime.register("test", callback)
        handler = QQBotGroupJoinRequestEventHandler(plugin)
        created = []

        class FakeTaskManager:
            def create_task(self, coroutine, **kwargs):
                created.append((coroutine, kwargs))
                return SimpleNamespace()

        from ..handlers import group_join_request_event_handler as handler_module

        monkeypatch.setattr(handler_module, "get_task_manager", lambda: FakeTaskManager())
        params = {"join_request_id": "r1", "group_openid": "g1", "raw_event": {}}
        await handler.execute("qqbot_adapter.group_join_request", params)
        await handler.execute("qqbot_adapter.group_join_request", params)
        assert len(created) == 1
        await created[0][0]
        assert received == [params]


class TestSentMessageRegistry:
    """验证发送归属记录只能使用一次。"""

    def test_claim_consumes_record(self) -> None:
        registry = SentMessageRegistry()
        registry.record("m1", "user", "u1")
        assert registry.claim("m1", "user", "u1") is True
        assert registry.claim("m1", "user", "u1") is False


class TestRecallTool:
    """验证 Tool 必须明确确认。"""

    async def test_requires_confirmation(self) -> None:
        tool = QQRecallCurrentMessageTool(make_plugin())
        tool.trigger_message = SimpleNamespace(chat_type="group", extra={"group_id": "g1"})
        success, message = await tool.execute("m1", False)
        assert success is False
        assert "confirm" in message
