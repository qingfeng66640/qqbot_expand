"""显式 HTTPS 默认端口兼容性离线回归。"""
import httpx
import pytest

from ..services.chunked_media_service import _plan_parts
from ..src.cos_transport import validate_presigned_url
from .test_coverage_domain_chunked_media import _COS_ORIGIN, _part


def test_plan_accepts_only_equivalent_default_port_normalization() -> None:
    """显式 HTTPS :443 等价于默认端口，但签名 path/query 必须原样保留。"""
    signed_url = f"{_COS_ORIGIN}:443/first%2Fpart?partNumber=1&signature=a%2Bb&partNumber=2"

    assert validate_presigned_url(signed_url) == signed_url
    assert str(httpx.URL(signed_url)) != signed_url
    plan = _plan_parts([_part(1, "7", signed_url)], len(b"payload"))
    assert plan == [(1, signed_url, 0, len(b"payload"))]
    assert plan[0][1].partition("/")[2] == signed_url.partition("/")[2]
    assert "partNumber=1&signature=a%2Bb&partNumber=2" in plan[0][1]


def test_plan_rejects_duplicate_canonical_destination() -> None:
    """显式默认端口与隐式默认端口不能给同一地址重复写入。"""
    target = '/object?part=1&signature=fixture'
    with pytest.raises(ValueError, match='无效分片'):
        _plan_parts([_part(1, '1', _COS_ORIGIN + target), _part(2, '1', _COS_ORIGIN + ':443' + target)], 2)
