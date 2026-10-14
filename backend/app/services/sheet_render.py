"""卷面 HTML 产出（契约 §5.1 `sheet` / `export` 共用的**唯一**渲染入口）。

**C3 硬约束**：`/sheet` 与 `/export` 必须打印**同一份 HTML**。
把「组装 SheetContext → render_sheet」收拢到这一个函数，两个路由都调它，
同源就不再靠自觉，而是结构上只有一条路。

渲染包由 render-export 维护，可能尚未就绪：这里延迟导入并抛
`RenderNotReady`，由路由层翻译成 503 `RENDER_NOT_READY`（契约 §9）。
本模块**不 import FastAPI**，保持 services 层的纯净。
"""
from __future__ import annotations

from app.contracts.sheet import SheetContext

__all__ = ["RenderNotReady", "RENDER_MODULE", "render_html"]

#: 渲染入口（冻结签名：`render_sheet(ctx: SheetContext) -> str`）
RENDER_MODULE = "app.render.sheet"


class RenderNotReady(RuntimeError):
    """渲染模块不可用（未实现 / 依赖缺失）——属环境问题，映射为 503。"""

    def __init__(self, expected: str = RENDER_MODULE) -> None:
        super().__init__(f"卷面渲染模块尚未就绪（缺少 {expected}）")
        self.expected = expected


def render_html(ctx: SheetContext) -> str:
    """把 `SheetContext` 渲染成完整 HTML 文档（纯字符串，无副作用）。"""
    try:
        from app.render.sheet import render_sheet
    except ImportError as exc:
        raise RenderNotReady() from exc

    html = render_sheet(ctx)
    if not isinstance(html, str) or not html.strip():
        raise RenderNotReady(f"{RENDER_MODULE}.render_sheet 未返回非空 HTML 字符串")
    return html
