"""二维码编码（契约 §5.3 / D54）。

纯编码层：给一个字符串，返回 PNG 字节。**不含**任何 URL 组装逻辑
（URL 由 `app/routers/urls.py` 依据请求 Host 构造）。

为什么不自己写二维码编码：`qrcode` 已在本项目 venv 中（契约要求的离线依赖），
自研编码器既无必要也不可验证。
"""
from __future__ import annotations

import io

import qrcode

__all__ = ["PNG_MAGIC", "png_bytes"]

#: PNG 文件头魔数（测试用它确认「返回的确实是 PNG」而不是别的东西）
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_bytes(data: str) -> bytes:
    """把 `data` 编码为二维码 PNG。

    使用 `qrcode` 库默认参数（box_size=10 / border=4），使同一内容
    每次得到**逐字节相同**的 PNG（可断言、可复现）。
    """
    if not data:
        raise ValueError("二维码内容不能为空")
    buffer = io.BytesIO()
    qrcode.make(data).save(buffer, format="PNG")
    return buffer.getvalue()
