"""对外 URL 组装（契约 §5.3，D54 冻结）。

**唯一规则**：学生端 URL 必须用**请求的 `Host` 头**构造绝对地址，
使老师在局域网上用哪个地址访问，二维码就指向哪个地址——
无需网卡枚举、无需配置，也不会探测到错误的网卡（NFR-1）。

`Host` 缺失时的兜底：退回 ASGI scope 里的服务地址（`request.base_url`），
它同样来自本次连接，而不是猜测。
"""
from __future__ import annotations

from typing import Protocol

from app.contracts.api import ROUTES

__all__ = ["student_url_for", "qr_url_for", "STUDENT_PAPER_PATH"]


class _HostCarrier(Protocol):
    """只需要 `headers` 与 `base_url`（便于单测用假请求对象）。"""

    headers: object
    base_url: object


#: 学生端取卷页前缀（前端路由契约 §7：`/student` 入口 + 卷号）
STUDENT_PAPER_PATH = "/student/paper"


def student_url_for(request: _HostCarrier, code: str) -> str:
    """绝对学生端 URL：`http://{请求的 Host}{/student/paper/{code}}`。"""
    if not code:
        raise ValueError("卷号不能为空（仅 published 卷才有二维码）")
    host = (request.headers.get("host") or "").strip()  # type: ignore[attr-defined]
    if host:
        return f"http://{host}{STUDENT_PAPER_PATH}/{code}"
    base = str(request.base_url).rstrip("/")
    return f"{base}{STUDENT_PAPER_PATH}/{code}"


def qr_url_for(paper_id: int) -> str:
    """二维码图片的相对路径（前端同源直接引用，契约 §5.3）。"""
    return ROUTES["qr"].format(paper_id=paper_id)
