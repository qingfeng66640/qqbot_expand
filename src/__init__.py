"""qqbot_expand 内部实现包。

包含：
- constants: QQ 开放平台的枚举与常量
- errors: 对外错误信息脱敏
- builders: 消息结构体（keyboard / ark / embed / markdown 等）构造与校验
- bridge: 与 qqbot_adapter 的桥接及统一 HTTP 请求出口
"""
from __future__ import annotations
