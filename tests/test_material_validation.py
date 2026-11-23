"""固定 CPU worker 入口、结果预算和失败白名单测试。"""
from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from ..src.material_policy import MaterialError
from ..src.material_validation import VALIDATION_TIMEOUT, validation_job, validation_result


def result(data: Any, *, returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    """构造已捕获stdout的子进程结果，stderr不得被产品返回。"""
    return subprocess.CompletedProcess(
        [], returncode, stdout=json.dumps(data).encode(), stderr=b"private stderr secret",
    )


def valid_data(**changes: Any) -> dict[str, Any]:
    """构造CPU纯函数的完整白名单payload。"""
    return {
        "file_name": "a.png", "file_size": 10, "content_type": "image/png",
        "md5": "a" * 32, "sha1": "b" * 40, **changes,
    }


def test_job_has_only_fixed_code_and_stdin_data() -> None:
    """用户内容仅通过stdin和独立argv传递，不成为代码或shell命令。"""
    content = b"untrusted image payload"
    job = validation_job("a.png", content, "image/png")
    assert job.func is subprocess.run
    assert job.args[0][1:4] == ["-B", "-I", "-c"]
    assert job.args[0][-2:] == ["a.png", "image/png"]
    assert job.keywords["input"] is content
    assert job.keywords["timeout"] == VALIDATION_TIMEOUT
    assert job.keywords["stdout"] == job.keywords["stderr"] == subprocess.PIPE
    assert job.keywords.get("shell", False) is False


def test_worker_payload_is_preserved() -> None:
    """只接受名称、大小、类型和摘要格式全部吻合的CPU结果。"""
    payload = valid_data()
    assert validation_result(result({"ok": True, "data": payload}), "a.png", 10, "image/png") == payload


@pytest.mark.parametrize("bad", [
    None,
    subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"private secret"),
    subprocess.CompletedProcess([], 0, stdout="not bytes", stderr=b"private secret"),
    subprocess.CompletedProcess([], 0, stdout=b"x" * 8193),
])
def test_failed_or_oversized_worker_result_is_rejected(bad: Any) -> None:
    """执行失败、错误类型和超预算输出不透传私密stderr。"""
    with pytest.raises(MaterialError, match="执行器失败") as error:
        validation_result(bad, "a.png", 10, "image/png")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("body", [b"not-json", b"[" * 1200 + b"]" * 1200])
def test_invalid_worker_json_has_fixed_error(body: bytes) -> None:
    """畸形和过深JSON也保持有界的错误路径。"""
    with pytest.raises(MaterialError, match="响应不合法"):
        validation_result(subprocess.CompletedProcess([], 0, stdout=body), "a.png", 10, "image/png")


def test_nonobject_worker_envelope_is_rejected() -> None:
    """非对象结果不能被当作成功或错误正文透传。"""
    with pytest.raises(MaterialError, match="响应不合法"):
        validation_result(result([]), "a.png", 10, "image/png")


@pytest.mark.parametrize("error", ["private secret", None, [], {"secret": True}])
def test_unknown_worker_error_is_not_exposed(error: Any) -> None:
    """即使worker故障返回任意字符串/对象，对外也只有固定安全提示。"""
    with pytest.raises(MaterialError, match="^图片校验失败$"):
        validation_result(result({"ok": False, "error": error}), "a.png", 10, "image/png")


def test_allowed_policy_error_remains_actionable() -> None:
    """已知帧预算错误可向调用方解释，但不带原始图像或内部异常。"""
    message = "图片帧数超出本地安全预算（最多 1024 帧）"
    with pytest.raises(MaterialError, match="帧数"):
        validation_result(result({"ok": False, "error": message}), "a.png", 10, "image/png")


@pytest.mark.parametrize("payload", [
    None,
    {},
    valid_data(extra="unexpected"),
    valid_data(file_name="b.png"),
    valid_data(file_size=True),
    valid_data(file_size=11),
    valid_data(content_type="image/jpeg"),
    valid_data(md5=None),
    valid_data(md5="bad"),
    valid_data(sha1=None),
    valid_data(sha1="bad"),
])
def test_worker_data_is_strictly_validated(payload: Any) -> None:
    """防止损坏、类型混淆或未声明字段进入prepare请求。"""
    with pytest.raises(MaterialError, match="数据不合法"):
        validation_result(result({"ok": True, "data": payload}), "a.png", 10, "image/png")
