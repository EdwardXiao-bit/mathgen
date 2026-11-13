"""模式 C 的卷面标记：布局版本、卷面指纹（checksum）与页脚二维码。

依据 `docs/09-模式C立项.md`（D92）与 `docs/10-M18实施工作流.md` §8：

* **二维码**：每页页脚同一枚 20×20mm 二维码（纠错级别 M）。二维码内容**不含页号**——
  页号由上传顺序确定、再用该页首题编号校验（见 §17.4 与 §8 的「页号来源」注记）。
* **checksum**：对「渲染输入规范化 JSON」取 `sha256` 前 16 位（16 个十六进制字符）。
  渲染器与识别服务**共用本模块同一个函数**，避免两处实现漂移。
* **layout 版本**：版面算法变化时必须改动 `LAYOUT_VERSION`；识别服务发现版本不一致
  直接拒绝（409），不猜。

本模块是**纯函数**（无 I/O、无浏览器、不 import render/export），可被渲染、导出与
识别三处同时引用。二维码 PNG 由 `qrcode`（项目已有依赖，见 `backend/pyproject.toml`）
生成后内联成 `data:` URI——渲染契约第 1 条要求「不依赖任何外部资源」。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import fields
from typing import Any, Mapping

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext

__all__ = [
    "LAYOUT_VERSION",
    "QR_FORMAT",
    "canonical_payload",
    "canonical_json",
    "sheet_checksum",
    "qr_payload",
    "qr_png_bytes",
    "qr_data_uri",
    "qr_svg",
]

#: 版面几何/标记布局的版本。任何会改变「同一份输入打印出不同版面」的改动都要 +1。
LAYOUT_VERSION = "1"

#: 二维码内容格式版本（二维码 JSON 里的 `v` 字段），与 `LAYOUT_VERSION` 独立演进。
QR_FORMAT = 1

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_DATA_URI_PREFIX = "data:image/png;base64,"


def _layout_signature(layout: LayoutParams) -> dict[str, Any]:
    """把排版参数展开成可 JSON 化的 dict（字段名排序，值原样保留）。

    不用 `dataclasses.asdict` 是为了避免把嵌套对象悄悄展开成另一套结构——
    这里只接受标量，出现非标量直接抛错，宁可红也不要算出一个「看起来一样」的 checksum。
    """
    out: dict[str, Any] = {}
    for field in sorted(fields(layout), key=lambda f: f.name):
        value = getattr(layout, field.name)
        if isinstance(value, (bool, int, float, str)) or value is None:
            out[field.name] = value
        else:  # pragma: no cover - 防御：参数表当前全是标量
            raise TypeError(f"排版参数 {field.name} 不是标量，无法参与 checksum")
    return out


def canonical_payload(ctx: SheetContext, layout: LayoutParams | None = None) -> dict[str, Any]:
    """渲染输入的规范化表示——**checksum 的唯一输入**。

    * 只收「影响版面或题目本身」的字段：标题、卷号、卷头说明、题号与题干、排版参数。
    * **绝不收答案**：题目版不得因答案变化而产生不同指纹（契约 §9 的安全边界）。
      答案版的答案区不在本函数范围里——模式 C 只识别题目版卷面。
    """
    if not isinstance(ctx, SheetContext):
        raise TypeError("canonical_payload 只接受 SheetContext")
    if not isinstance(ctx.items, tuple):
        raise TypeError("SheetContext.items 必须是 tuple（冻结契约）")

    effective_layout = layout if layout is not None else ctx.layout
    return {
        "layout_version": LAYOUT_VERSION,
        "mode": ctx.mode.value,
        "title": ctx.title,
        "code": ctx.code or "",
        "header_note": ctx.header_note,
        "items": [[int(item.seq), item.display] for item in ctx.items],
        "layout": _layout_signature(effective_layout),
    }


def canonical_json(payload: Mapping[str, Any]) -> str:
    """规范化 JSON 文本：键排序、无空白、保留中文原字（不做 `\\u` 转义）。

    `ensure_ascii=False` 是有意的：checksum 只在本机流转，中文题目按原字参与哈希，
    比转义形式更短、也更容易在排查时人工比对。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sheet_checksum(ctx: SheetContext, layout: LayoutParams | None = None) -> str:
    """卷面指纹：`sha256(规范化 JSON).hexdigest()[:16]`。

    同一份输入（含排版参数）必然得到同一个值；改题、改标题、改字号、改页边距都会变。
    它不证明「打印出来的纸一定对」——那由二维码/定位块的实际识别链路证明。
    """
    text = canonical_json(canonical_payload(ctx, layout))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def qr_payload(
    paper: int | str,
    checksum: str,
    *,
    layout: str = LAYOUT_VERSION,
    pages: int | None = None,
) -> str:
    """二维码内容：`{"v":1,"paper":…,"checksum":…,"layout":…}`。

    * `paper`：卷的身份证（一期传 `paper_id`；没有 id 的旧路径传卷号 `code`）。
    * `pages`：整卷页数；一页卷可以省略，两页卷起由上传端用它做「缺页」预检。
    * **不含页号**：页号来自上传顺序（一页一条文件、按 1..N 顺传），再用该页
      首题编号与几何对照校验——同一枚二维码在每页重复出现才能用 CSS `fixed` 打印。
    """
    if not isinstance(checksum, str) or len(checksum) != 16:
        raise ValueError("checksum 必须是 16 个十六进制字符")
    if any(ch not in "0123456789abcdef" for ch in checksum):
        raise ValueError("checksum 只能包含小写十六进制字符")
    payload: dict[str, Any] = {
        "v": QR_FORMAT,
        "paper": paper,
        "checksum": checksum,
        "layout": layout,
    }
    if pages is not None:
        payload["pages"] = int(pages)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def qr_png_bytes(text: str, *, box_size: int = 3, border: int = 1) -> bytes:
    """把文本编成 PNG 字节（黑白、纠错级别 M、无外部依赖）。

    `box_size`/`border` 取小值：页脚打印 20×20mm，缩放由 CSS 负责，
    位图只要够清晰即可（二维码是矢量感的方块，放大不糊）。
    """
    import qrcode  # 局部导入：模块导入本身不拉 Pillow，判据可以只测纯函数

    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=int(box_size),
        border=int(border),
    )
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    raw = buf.getvalue()
    if not raw.startswith(_PNG_MAGIC):  # pragma: no cover - 防御
        raise RuntimeError("qrcode 输出的不是 PNG")
    return raw


def qr_data_uri(text: str, *, box_size: int = 3, border: int = 1) -> str:
    """二维码的 `data:` URI（直接放进 `<img src>`，满足「无外部资源」契约）。"""
    encoded = base64.b64encode(qr_png_bytes(text, box_size=box_size, border=border)).decode("ascii")
    return _DATA_URI_PREFIX + encoded


def _qr_matrix(text: str, border: int = 4) -> list[list[bool]]:
    """二维码模块矩阵（含静区 `border` 个模块），True = 黑。"""
    import qrcode

    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=int(border),
    )
    code.add_data(text)
    code.make(fit=True)
    return code.get_matrix()


def qr_svg(text: str, *, size_mm: float = 20.0, border: int = 4) -> str:
    """把二维码画成**内联 SVG**（不产生 `<img src>`，因此不触发「外部资源」判据）。

    为什么不用 `<img src="data:...">`：渲染契约第 1 条与门禁都按 `[src]`/`[href]`
    扫描外部资源，`data:` URI 也会被这条扫描命中；纯内联 SVG 没有资源位，干净。
    每个黑模块画成一个 `rect`，坐标按模块整数比，缩放不糊。
    """
    matrix = _qr_matrix(text, border=border)
    size = len(matrix)
    path_parts: list[str] = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                path_parts.append(f"M{x} {y}h1v1h-1z")
    path_d = "".join(path_parts)
    # `shape-rendering="crispEdges"` 让 20mm 打印时不出现灰边（抗锯齿会把模块糊成灰）
    return (
        # 内联进 HTML 的 SVG 不需要 xmlns（HTML 解析器按 SVG 命名空间处理）；
        # 留着它会让「无外部引用」判据（`"http://" not in html`）假红。
        f'<svg class="sheet-qr" '
        f'width="{size_mm}mm" height="{size_mm}mm" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img" aria-label="卷面二维码">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path d="{path_d}" fill="#000"/>'
        f"</svg>"
    )
