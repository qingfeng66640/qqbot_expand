"""一次性 QQ 请求不能走内部可重试的 POST 出口。"""
import httpx
import pytest

from ..src.bridge import api_request
from .conftest import FakeSendHandler, make_plugin


@pytest.mark.parametrize('scenario', ['success', 'redirect', 'timeout'])
async def test_post_once_is_not_replayed(patch_send_handler: FakeSendHandler, scenario: str) -> None:
    """通过内存 HTTP 边界区分成功、重定向和未知结果。"""
    sent: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """记录请求次数，不打开任何实际网络连接。"""
        sent.append(request.method)
        if scenario == 'timeout':
            raise httpx.ReadTimeout('fixture', request=request)
        if scenario == 'redirect':
            return httpx.Response(307, headers={'location': '/not-called'})
        return httpx.Response(200, json={'id': 'ok'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
        result = await api_request(make_plugin(http_client=client), 'POST', '/v2/fixture', {}, retry_network_errors=False)
    assert result['success'] is (scenario == 'success')
    assert sent == ['POST']
    assert not patch_send_handler.posts
