"""路由层共用的小工具（非路由模块，不注册到 app）。

`render_not_ready()` 之所以单独拿出来：`/sheet` 与 `/export` 两条路由
都需要在渲染模块未就绪时返回**同一个** 503 结构（契约 §9 `RENDER_NOT_READY`）。

`finish()` 同理：M4 的写接口从 2 个涨到 5 个（建卷/删除/重生成/布局/报到/提交），
于是**事务收尾**这件事有了 6 份复制品。它是「服务层不 commit」这条分层的补集——
服务层只管算与写，**提交/回滚由路由层收口**（见 `services/paper_service.py` 模块注释），
所以这个收口动作必须只有一处实现，否则「哪些接口真的提交了」会变成逐处都不同的暗规则。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.services.sheet_render import RENDER_MODULE

__all__ = ["json_error", "render_not_ready", "finish"]


def json_error(status: int, code: str, message: str) -> JSONResponse:
    """统一错误响应结构（契约 §5.4）：`{"error": {"code", "message"}}`。"""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def render_not_ready(expected: str = RENDER_MODULE) -> JSONResponse:
    """渲染模块未就绪的标准 503 响应（结构与其他错误一致）。"""
    return json_error(
        503,
        "RENDER_NOT_READY",
        f"卷面渲染模块尚未就绪（缺少 {expected}），请稍后重试",
    )


@contextmanager
def finish(db: Session) -> Iterator[Session]:
    """写接口的事务收口：成功 `commit()`，异常 `rollback()` 后原样抛出。

    异常原样抛出而不是吞掉转成 JSON——`ApiError` 由 `main.py` 的异常处理器统一成
    契约错误信封；这里是**最后一道** rollback，保证失败请求不会留下半截写入。
    """
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    else:
        db.commit()

