"""通过框架 CPU 池运行可独立导入的图片校验，不依赖动态插件包名。

框架目录加载器可能只在父进程注册 ``qqbot_expand`` 模块别名；直接提交插件
函数会令 spawn 子进程反序列化失败。这里提交标准库 subprocess.run 的 partial，
以固定 bootstrap 从本插件实际文件路径加载纯校验模块。图片仅通过 stdin 传递，
不创建临时文件、不执行 shell，也不把用户输入解释为 Python 代码。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import Any

from . import material_policy
from .material_policy import MaterialError

VALIDATION_TIMEOUT = 15.0
_RESULT_LIMIT = 8192
_VALIDATION_ERRORS = frozenset({
    "图片内容无效或解码大小超限",
    "真实图片类型与扩展名/Content-Type 不一致",
    "图片累计解码像素过大",
    "图片帧数超出本地安全预算（最多 1024 帧）",
})

# 这是固定、受信的插件代码；文件名/MIME 是独立 argv，图片是 stdin bytes。
_WORKER_SCRIPT = '''
import importlib.util
import json
import sys
spec = importlib.util.spec_from_file_location("_qqbot_material_cpu_policy", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
    payload = module.image_payload(sys.argv[2], sys.stdin.buffer.read(), sys.argv[3])
except Exception as exc:
    message = str(exc) if isinstance(exc, getattr(module, "MaterialError", ())) else ""
    print(json.dumps(dict(ok=False, error=message), ensure_ascii=True))
else:
    print(json.dumps(dict(ok=True, data=payload), ensure_ascii=True))
'''


def validation_job(
    file_name: str, content: bytes, content_type: str,
) -> partial[subprocess.CompletedProcess[bytes]]:
    """构造只包含标准库 callable 与可序列化参数的有界 CPU 作业。"""
    policy_path = str(Path(material_policy.__file__).resolve())
    return partial(
        subprocess.run,
        [sys.executable, "-B", "-I", "-c", _WORKER_SCRIPT, policy_path, file_name, content_type],
        input=content, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=VALIDATION_TIMEOUT, check=False,
    )


def validation_result(
    result: subprocess.CompletedProcess[bytes], file_name: str, size: int, content_type: str,
) -> dict[str, Any]:
    """严格读取工作进程的有界白名单结果，不回显 stderr 或任意错误文本。"""
    if (not isinstance(result, subprocess.CompletedProcess) or result.returncode != 0
            or not isinstance(result.stdout, bytes) or len(result.stdout) > _RESULT_LIMIT):
        raise MaterialError("图片校验执行器失败")
    try:
        envelope = json.loads(result.stdout)
    except (ValueError, RecursionError):
        raise MaterialError("图片校验执行器响应不合法") from None
    if not isinstance(envelope, dict):
        raise MaterialError("图片校验执行器响应不合法")
    if envelope.get("ok") is not True:
        error = envelope.get("error")
        if isinstance(error, str) and error in _VALIDATION_ERRORS:
            raise MaterialError(error)
        raise MaterialError("图片校验失败")
    data = envelope.get("data")
    if (not isinstance(data, dict)
            or set(data) != {"file_name", "file_size", "content_type", "md5", "sha1"}
            or data.get("file_name") != file_name or type(data.get("file_size")) is not int
            or data["file_size"] != size or data.get("content_type") != content_type
            or not isinstance(data.get("md5"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", data["md5"])
            or not isinstance(data.get("sha1"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", data["sha1"])):
        raise MaterialError("图片校验执行器数据不合法")
    return data
