"""从 PDF 内容流解析**文字在纸面上的绝对坐标**（不依赖第三方库）。

用于回答「`page_margin` 在导出的 PDF 里到底生效没有」——必须给几何事实，
不接受「代码里写了字面量」。

实现要点（踩过的坑都记在这里）：

1. 文字坐标是**文本矩阵**给的：`Tm` 设绝对位置，`Td` / `TD` / `T*` 相对移动，
   `Tj` / `TJ` / `'` / `"` 才真正落字。必须按顺序维护文本矩阵，只统计落字位置。
2. 内容流最外层还有 **CTM**（`cm` 变换），Chromium 打印时会用它把内容整体偏移/缩放；
   `q` / `Q` 成对压栈弹栈。只取文本矩阵会在纸张尺寸 > 默认真实版心时**溢出纸面**
   （实测 1cm 页边距时解析出 237mm，而纸宽 210mm），所以必须一并维护 CTM。
"""
from __future__ import annotations

import re
import zlib

PT2MM = 25.4 / 72.0

_TOKEN_RE = re.compile(rb"\[[^\]]*\]|/[^\s/\[\]()<>]+|\([^)]*\)|<[0-9A-Fa-f\s]*>|[-\d.]+|[A-Za-z*'\"]+")
_TEXT_OPS = {b"Tj", b"TJ", b"'", b'"'}


def content_streams(pdf_bytes: bytes):
    for match in re.finditer(rb"stream\r?\n", pdf_bytes):
        start = match.end()
        end = pdf_bytes.find(b"endstream", start)
        if end < 0:
            continue
        raw = pdf_bytes[start:end]
        try:
            yield zlib.decompress(raw)
        except Exception:
            yield raw


def _apply(a, b, c, d, e, f, x, y):
    """把 `(x, y)` 用矩阵 `[a b c d e f]` 变换（PDF 用行向量约定）。"""
    return (a * x + c * y + e, b * x + d * y + f)


def _mul(m1, m2):
    """矩阵相乘 `m1 x m2`（PDF 约定）。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _num(token: bytes):
    try:
        return float(token)
    except ValueError:
        return None


def text_positions_mm(pdf_bytes: bytes) -> list[tuple[float, float]]:
    """所有**落字**位置的纸面绝对坐标（mm，原点在纸张左下角）。"""
    positions: list[tuple[float, float]] = []
    for stream in content_streams(pdf_bytes):
        positions.extend(_positions_in_stream(stream))
    return positions


def text_positions_by_page(pdf_bytes: bytes) -> list[list[tuple[float, float]]]:
    """按页分组的落字坐标（每页一项，坐标同 `text_positions_mm`）。

    不能直接把 `content_streams()` 的每一项当一页：一份 PDF 里还有字体、图像等
    其它流（实测 1 页的 PDF 有 9 条流、2 页的有 11 条）。判据是**含 `BT`/`ET`
    文本块**的流才是页内容流，且它们在文件里的顺序就是页序（实测 1 页 → 1 条、
    2 页 → 2 条，与 `count_pdf_pages()` 逐一对上）。

    `count_pdf_pages()` 走的是另一条路径（数 `/Type /Page`），两条通道互相独立——
    M6 用这个交叉核对「模型预测的分页」与「真实 PDF 的分页」。
    """
    return [
        _positions_in_stream(stream)
        for stream in content_streams(pdf_bytes)
        if b"BT" in stream and b"ET" in stream
    ]


def _positions_in_stream(stream: bytes) -> list[tuple[float, float]]:
    """解析单个内容流里的落字坐标（mm）。"""
    positions: list[tuple[float, float]] = []
    stack: list[bytes] = []
    ctm: tuple = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm_stack: list[tuple] = []
    tm: tuple = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    leading = 12.0
    in_text = False

    for token in _TOKEN_RE.findall(stream):
        if token == b"q":
            ctm_stack.append(ctm)
            stack.clear()
            continue
        if token == b"Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
            stack.clear()
            continue
        if token == b"cm" and len(stack) >= 6:
            values = [_num(t) for t in stack[-6:]]
            if all(v is not None for v in values):
                ctm = _mul(tuple(values), ctm)  # type: ignore[arg-type]
            stack.clear()
            continue
        if token == b"BT":
            in_text, tm = True, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            stack.clear()
            continue
        if token == b"ET":
            in_text = False
            stack.clear()
            continue
        if token == b"TL" and stack:
            value = _num(stack[-1])
            if value is not None:
                leading = value
            stack.clear()
            continue
        if token == b"Tm" and len(stack) >= 6:
            values = [_num(t) for t in stack[-6:]]
            if all(v is not None for v in values):
                tm = tuple(values)  # type: ignore[assignment]
            stack.clear()
            continue
        if token in (b"Td", b"TD") and len(stack) >= 2:
            dx, dy = _num(stack[-2]), _num(stack[-1])
            if dx is not None and dy is not None:
                tm = _mul((1.0, 0.0, 0.0, 1.0, dx, dy), tm)
            if token == b"TD" and dy is not None:
                leading = -dy
            stack.clear()
            continue
        if token == b"T*":
            tm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tm)
            stack.clear()
            continue
        if token in _TEXT_OPS:
            if in_text:
                absolute = _mul(tm, ctm)
                positions.append(
                    (absolute[4] * PT2MM, absolute[5] * PT2MM)
                )
            if token in (b"'", b'"'):
                tm = _mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tm)
            stack.clear()
            continue
        stack.append(token)
        if len(stack) > 12:
            del stack[:-12]
    return positions


def page_text_bbox_mm(pdf_bytes: bytes):
    """落字位置的包围盒 `(x_min, y_min, x_max, y_max)`（mm）。"""
    points = text_positions_mm(pdf_bytes)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def text_line_levels(pdf_bytes: bytes, tolerance_mm: float = 1.5) -> int:
    """纸面上**排了几行字**：把落字 y 坐标按 `tolerance_mm` 归并成层。

    M6 用来核准 `measure_js()` 的测量视口：DOM 量到的行数（行高 ÷ 行盒高）
    必须等于纸面上的文字层数，否则说明测量视口与真实打印版心不等宽，
    量到的行高与纸上不一致（NFR-5 判据链的假阴性来源）。
    """
    ys = sorted(p[1] for p in text_positions_mm(pdf_bytes))
    if not ys:
        return 0
    levels = 1
    anchor = ys[0]
    for value in ys[1:]:
        if value - anchor > tolerance_mm:
            levels += 1
            anchor = value
    return levels
