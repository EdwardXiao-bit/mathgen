"""排版容量守卫：导出前判定「这一卷在这个排版下放得下吗」（NFR-5 的服务端出口）。

## 为什么需要这个模块

NFR-5 的原话是「**浏览器打印**不能把一道题跨页拆开」，而服务端的出口只有
`GET /api/papers/{id}/export`。老师直接在浏览器里 Ctrl+P 时，服务端**一次都不会执行**——
这条路径覆盖不到。所以本模块**只**负责服务端那一半：把「已经确定放不下」的卷面
在导出前**拒绝**掉，并给出能照着做的诊断。

## 为什么放在 `app/layout/` 而不是 `app/services/`

判定本身是**纯计算**（读一份实测排布 → 判行高是否超阈值 → 生成文案），不碰数据库、
不碰 FastAPI、不碰 I/O。而它的调用方是 `app/export/pdf.py`：`app/services/__init__.py`
明确要求「本包**不得**被 `engine` / `grading` / `render` / `export` 反向依赖」，
所以守卫不能住在 `app/services`——否则 `app.export` 一 import 就破了分层纪律。
`app/layout` 是 `app.export` 本来就依赖的层（`content_width_px`、`measure_js` 都来自这里）。

## 判据链（谁量、量什么）

1. 渲染出来的 HTML 在**真实打印媒体**下量出行排布（`app.export.pdf.export_pdf_report`
   在 `emulate_media("print")` 之后调 `measure_js()`）；
2. 本模块用 `detect_overflow()` 判「有没有哪一行连一整页都放不下」——
   阈值取**实测标定的分页线**，不是 A4 名义版心（`app.layout.pagination` 的注释里有
   两轮踩坑记录：名义值判定会得到与真实 PDF 相反的结论）；
3. 有 → 抛 `LayoutTooLarge`，由导出通道翻成 `LAYOUT_DOES_NOT_FIT`（409）。

**测量视口必须等于真实版心宽**：`export_pdf_report` 已经这么做（M6 修正），
否则折行行为与纸上不同、行高系统性偏小，这里就会漏报。

## 建议从哪来：`find_fitting_layout`，**不是** `reflow()`

诊断里那句「实测这样能放下」出自 `find_fitting_layout`：它在**同一份 HTML** 上
逐档改排版参数并**重新测量**（`measure(rewrite_layout_vars(html, candidate), candidate)`），
命中即报。目标值因此是**量过的**，不是推算的。

**`app.layout.pagination.reflow()` 不在本链路里**，导出链路上一次都不会跑。
（历史教训：本模块早先写过一层 `try_reflow_layout` 包装 + `check_layout_fits` 的
`render_with`/`measure` 分支，但生产调用点从不传这两个回调 → 那条分支**恒真**
（`not overflows or render_with is None or measure is None`），包装函数生产死、测试死。
两个做同一件事的搜索器只有一个在跑，另一个必然静默漂移且无人发现。已整体删除。
`reflow()` 本身留在 `app/layout/pagination.py`，是布局模块自己的公共面。）

**无论用哪个搜索器，结果都只进文案，绝不自动应用到交付物上**：老师设的字号是 20，
悄悄按 14 导出，等于交付物与老师设的不是同一个东西——那比报错更坏。

## 写域约束

`app/contracts/**` 是冻结契约（`backend/tests/contract/test_layering.py` 有 sha256 守卫），
本模块一个字都不改；`LAYOUT_DOES_NOT_FIT` 这个码加在 `app/services/errors.py`
（错误词汇表本来就住那儿），契约文档由 Lead 落笔。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from typing import Any, Callable, Mapping

from app.contracts.layout import BOUNDS, LayoutParams, css_variables
from app.layout.pagination import Overflow, detect_overflow

#: 公共面 = **被模块外引用**的名字（Lead m05153 §二(1) 的判据：按「是否被模块外引用」
#: 收缩，不按「是否有定义」保留）。逐项来源（实测 grep，见报告 §10.7）：
#:   * `LayoutTooLarge`        —— 外部生产 4 处（`export/pdf.py:20` import + `:171` except；
#:     `services/pdf_export_worker.py:117` import + `:128` except）+ 测试 3 处
#:     （`test_layout_proof.py:47,1200,1224`）。**注意**：`services/pdf_export.py:155,279`
#:     两处只是 docstring 里的交叉引用，**不是**生产使用——本账按代码引用算。
#:   * `guard_layout_of_html`  —— 外部生产 2 处（`export/pdf.py:20` import + `:159` 唯一调用点）
#:     + 测试 5 处。
#:   * `describe_layout`       —— 外部生产 1 处（`pdf_export_worker.py:72` import，`:81` 调用）
#:     + 测试 0 处（生产要用就够）。
#: 其余五个（`check_layout_fits` / `format_diagnostic` / `find_fitting_layout` /
#: `rewrite_layout_vars` / `LayoutFit`）外部引用全 0，只是模块内部链的一环：
#: `guard_layout_of_html` → `check_layout_fits` / `find_fitting_layout` → `rewrite_layout_vars`，
#: `LayoutTooLarge.__init__` → `format_diagnostic`。**函数一个都没删**，只是不再宣称它们是公共面。
__all__ = [
    "LayoutTooLarge",
    "describe_layout",
    "guard_layout_of_html",
]

#: HTML 里注入的六个排版参数（`app.contracts.layout.css_variables()` 的产物）。
#: 值里不含 `;` / `"` / 换行，所以这三者可以安全地当地界。
_LAYOUT_VAR_RE = re.compile(
    r"(--(?:cols-per-row|row-gap|font-size|page-margin|answer-line-display"
    r"|header-bar-display)\s*:\s*)([^;\"\n]+)"
)

#: `@page { margin: 3.0cm }` 里的字面量（`app.layout.styles.page_rule`）。
#: 为什么它也要换：`@page` 不解析自定义属性（styles.py 有实测记录），
#: 页边距进纸面的那条通道走的是这个字面量，漏了它候选版就只换了一半。
_AT_PAGE_MARGIN_RE = re.compile(r"(margin:\s*)([0-9.]+)cm")


def rewrite_layout_vars(html: str, layout: LayoutParams) -> str:
    """把 HTML 里注入的六个排版参数换成 `layout` 的取值（**只换参数，不换内容**）。

    为什么需要它：导出链路手里只有 `render_sheet(ctx)` 的产物，没有 `ctx` 本身，
    所以没法调 `render_sheet(ctx_template, layout)` 来「换排版重排」。而在同一份 HTML 上
    改掉这六个 CSS 变量，行的正是「同一张卷子换排版」这件事——`find_fitting_layout`
    的候选版 HTML 因此可以就地构造，不必改冻结入口的签名、也不必把 context 塞进 HTML。

    **不改变题目内容**（题干、题号、答案都不动），所以「换成什么样」这件事
    仍然完全由老师定：这里只回答「如果按这个排版，放得下吗」。
    """
    variables = css_variables(layout)

    def _replace_var(match: re.Match[str]) -> str:
        name = match.group(1).split(":", 1)[0].strip()
        value = variables.get(name)
        return match.group(0) if value is None else f"{match.group(1)}{value}"

    swapped = _LAYOUT_VAR_RE.sub(_replace_var, html)
    return _AT_PAGE_MARGIN_RE.sub(
        lambda m: f"{m.group(1)}{layout.page_margin:g}cm", swapped
    )


class LayoutTooLarge(Exception):
    """这一卷在这个排版下**必然**会有题目跨页拆开（NFR-5）——导出必须拒绝。

    携带原始读数与诊断，好让上层给出可照做的提示：

    * `overflows` —— `detect_overflow()` 的结果（行号 + 行高 + 阈值）；
    * `layout` —— 判定时用的排版参数；
    * `measurement` —— 浏览器侧实测排布（可选，供更细的审计）。
    """

    def __init__(
        self,
        overflows: tuple[Overflow, ...],
        layout: LayoutParams,
        measurement: Mapping[str, Any] | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.overflows = overflows
        self.layout = layout
        self.measurement = measurement
        self.suggestion = suggestion
        super().__init__(format_diagnostic(overflows, layout, suggestion))

    @property
    def rows_over(self) -> tuple[int, ...]:
        """超高行的行号（0 基，与切行顺序一致）。"""
        return tuple(o.row_index for o in self.overflows)

    def to_payload(self) -> dict[str, Any]:
        """可 JSON 序列化的诊断（**跨进程**用：导出走一次性子进程）。

        只有 `overflow_px` 是派生量，其余三个字段都原样带上——诊断要能自己说清
        「哪一行、多高、超过多少」，到了父进程还得能重新拼出提示文案。
        """
        return {
            "message": str(self),
            "layout": describe_layout(self.layout),
            "layout_fields": {
                name: getattr(self.layout, name)
                for name in _FIELD_LABEL
            },
            "suggestion": self.suggestion,
            "overflows": [
                {
                    "row_index": o.row_index,
                    "height_px": round(o.height_px, 4),
                    "content_height_px": round(o.content_height_px, 4),
                }
                for o in self.overflows
            ],
        }


#: 诊断里字段 → 人话（**必须**带上老师能看懂的单位）
_FIELD_LABEL = {
    "cols_per_row": "每行题数",
    "row_gap": "行距倍数",
    "font_size": "字号",
    "page_margin": "页边距",
}

#: 调整建议里字段 → 单位后缀
_FIELD_UNIT = {
    "cols_per_row": " 题",
    "font_size": " 号",
    "row_gap": " 倍",
    "page_margin": "cm",
}


@dataclass(frozen=True)
class LayoutFit:
    """一次容量判定的结果：**只有读数，没有建议**（`fits=True` 表示放得下）。

    刻意不带 `suggestion` 字段：建议要拿这份 HTML 逐档重测才能算出，
    那是浏览器侧的活（`guard_layout_of_html(..., measure=...)`）。
    原先这里有一个恒为 `None` 的 `suggestion` 字段——同一类「写在类型里的机制
    没人跑」的假象，随 `try_reflow_layout` 一并删除（见模块 docstring）。
    """

    overflows: tuple[Overflow, ...]
    layout: LayoutParams

    @property
    def fits(self) -> bool:
        return not self.overflows


def _format_value(value: Any) -> str:
    """整数原样、小数去掉多余的 0（20.0 → 20，1.5 → 1.5）。"""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def describe_layout(layout: LayoutParams) -> str:
    """把排版参数读成人话：`每行 6 题 / 字号 20 / 页边距 3cm`。

    只列**数值项**：两个布尔开关（答题线、卷头栏）不影响行高，列进来只会让诊断变长。
    """
    parts: list[str] = []
    for field in fields(layout):
        name = field.name
        if name not in _FIELD_LABEL:
            continue
        value = getattr(layout, name)
        parts.append(
            f"{_FIELD_LABEL[name]} {_format_value(value)}{_FIELD_UNIT[name]}"
        )
    return " / ".join(parts)


def format_diagnostic(
    overflows: tuple[Overflow, ...],
    layout: LayoutParams,
    suggestion: str | None = None,
) -> str:
    """面向老师的诊断：**参数实际值** + **具体动作**，不是「排版不合适」。

    形态（`suggestion` 由 `find_fitting_layout` 在同一份 HTML 上逐档重测得出）：

        当前排版（每行 6 题 / 字号 20 / 页边距 3cm）在这一卷上放不下，
        会有 4 行题目被跨页拆开（最高的一行 1382.47px，超过可印区 833.00px）；
        请把字号从 20 号调到 19 号以下（实测这样能放下）。

    `suggestion` 为 `None` 时退化为不含具体目标值的通用建议（搜索没命中，或调用方
    没有浏览器可重测）——仍然给出**方向**，不让老师自己去猜。
    """
    if not overflows:
        # 防御：调用方只应在非空时报错。空集说明判据输入被误用，不能静默输出
        # 一条「放不下」的错误信息（V13：先核对判据输入）。
        raise ValueError("format_diagnostic 需要至少一条溢出读数；空集说明判据输入被误用")

    count = len(overflows)
    worst = max(overflows, key=lambda o: o.overflow_px)
    base = (
        f"当前排版（{describe_layout(layout)}）在这一卷上放不下，"
        f"会有 {count} 行题目被跨页拆开"
        f"（最高的一行 {worst.height_px:.2f}px，超过可印区 {worst.content_height_px:.2f}px）"
    )
    if suggestion:
        return f"{base}；{suggestion}。"
    return (
        f"{base}；请把字号调小，或把每行题数减少——"
        f"这两项才会改变行高，行距与页边距在极端参数下救不回来。"
    )


def _suggestion_between(before: LayoutParams, after: LayoutParams) -> str | None:
    """把「从 `before` 到 `after` 的改动」翻成一句可照做的建议；没有改动时返回 `None`。"""
    changed = [
        (field.name, getattr(before, field.name), getattr(after, field.name))
        for field in fields(before)
        if getattr(before, field.name) != getattr(after, field.name)
    ]
    if not changed:
        return None
    moves = "，且".join(
        f"把{_FIELD_LABEL[name]}从 {_format_value(old)}{_FIELD_UNIT[name]}"
        f"调到 {_format_value(new)}{_FIELD_UNIT[name]}以下"
        for name, old, new in changed
    )
    return f"请{moves}（实测这样能放下）"


#: 建议搜索的**参数优先序**：只动「真的改变行高」的两项（与降级文案说的一致），
#: 且字号在前——一档一档降字号比直接砍掉一列题温和，老师的排版意图保留得更多。
_SEARCH_FIELDS: tuple[str, ...] = ("font_size", "cols_per_row")


def find_fitting_layout(
    html: str,
    measure: Callable[[str, LayoutParams], Mapping | Any],
    layout: LayoutParams,
    *,
    max_probes: int = 24,
) -> LayoutParams | None:
    """在**同一份内容**上找一个真的放得下的参数组合；找不到返回 `None`。

    为什么不用 `app.layout.pagination.reflow()`（它每轮只把**一个**参数动**一步**，
    适合「已经接近」的情形）：这里的起点是判出溢出的极端组合（实测最坏一行比可印区
    高 66%），单步走法永远走不到。所以改成**沿单个参数逐档下探**：
    `font_size` 20 → 19 → … → 10，再 `cols_per_row` 6 → 5 → … → 2，命中即返回。
    **本模块只此一个建议搜索器**，不存在第二个在跑的同类实现。

    返回的组合是**测量过的**，不是推算的——诊断里写得出「实测这样能放下」。
    它的用途**只有写进诊断**，绝不自动应用（见模块 docstring）。
    """
    probes = 0
    for name in _SEARCH_FIELDS:
        for value in shrink_steps(layout, name):
            if probes >= max_probes:
                return None
            probes += 1
            # `shrink_steps` 给的是**数值**，这里才拼成候选排版
            candidate = replace(layout, **{name: value})
            reading = measure(rewrite_layout_vars(html, candidate), candidate)
            if not detect_overflow(reading, candidate):
                return candidate
    return None


def check_layout_fits(
    measurement: Mapping | Any,
    layout: LayoutParams,
) -> LayoutFit:
    """判「这一卷在这个排版下放得下吗」；返回 `LayoutFit`（`fits=True` 表示放得下）。

    本函数**不抛异常**——抛不抛是调用方的策略决定（导出路径抛 `LayoutTooLarge`）。
    这样同一个判定既能用于「拒绝导出」，也能用于「只报警告」。

    纯判定，**不算建议**：`suggestion` 恒为 `None`。算建议要拿这份 HTML 逐档重测，
    那是浏览器侧的活（`guard_layout_of_html(..., measure=...)` → `find_fitting_layout`），
    判定本身不该反向依赖测量回调。
    """
    return LayoutFit(overflows=detect_overflow(measurement, layout), layout=layout)


def guard_layout_of_html(
    html: str,
    measurement: Mapping | Any,
    *,
    layout: LayoutParams | None = None,
    measure: Callable[[str, LayoutParams], Mapping | Any] | None = None,
    max_probes: int = 24,
) -> LayoutFit:
    """导出链路的**判定 + 拒绝**：放不下就抛 `LayoutTooLarge`。

    `layout` 缺省时从 HTML 自带的 CSS 变量里读回六个参数（`app.export.pdf.layout_of`）。
    调用方（`app.export.pdf.export_pdf_report`）负责提供 `measurement`——它必须是
    **打印媒体 + 真实版心视口**下量出来的那一份，否则行高系统性偏小、这里会漏报。

    `measure` 提供时才会去算**含具体目标值**的建议（`find_fitting_layout`：
    在同一份 HTML 上逐档换参数重测，命中即报「实测把字号调到 19 号以下」）。
    省略它则退化为给方向的通用建议——导出链路之外（例如只做判定的调用方）
    没有浏览器可用来重测，此时编一个具体数字反而是假信息。
    """
    from app.export.pdf import layout_of  # 局部导入：避免 layout→export 的循环

    fitted = layout if layout is not None else layout_of(html)
    fit = check_layout_fits(measurement, fitted)
    if fit.fits:
        return fit

    suggestion: str | None = None
    if measure is not None:
        target = find_fitting_layout(html, measure, fitted, max_probes=max_probes)
        if target is not None:
            suggestion = _suggestion_between(fitted, target)
    raise LayoutTooLarge(fit.overflows, fit.layout, measurement, suggestion)


def shrink_steps(layout: LayoutParams, name: str) -> list[float]:
    """`name` 从当前值往**更省空间**方向逐档可取的值（供诊断给具体目标）。

    只用于文案：`font_size` 20 → `[19, 18, …, 10]`，`cols_per_row` 6 → `[5, 4, …, 2]`。
    """
    if name not in BOUNDS:
        raise ValueError(f"未知排版参数 {name!r}")
    lo, _hi = BOUNDS[name]
    step = 1.0 if name in ("cols_per_row", "font_size") else 0.1
    values: list[float] = []
    cursor = getattr(layout, name) - step
    while cursor >= lo - 1e-9:
        values.append(round(cursor, 2))
        cursor -= step
    return values
