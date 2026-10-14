"""卷面常量与样式构建 —— `render` 的排版实现（写域：`backend/app/layout/**`）。

本包**只做排版**：把 `app.contracts.layout.LayoutParams` 翻译成 CSS。
它不生成题目数据、不读文件、不碰网络，因此 `render` 与 `export` 可以共用同一份样式，
满足 C3「预览 = 打印 = PDF 三者同源」。

设计要点（每条都对应一条验收标准，见 docs/02-技术设计.md §9）：

| 要点 | 落地位置 | 依据 |
| --- | --- | --- |
| A4 页面与页边距 | `page_rule()` 的 `@page` | NFR-5 / 契约 §4 |
| 每行题数 | `.q-row { grid-template-columns: repeat(var(--cols-per-row), 1fr) }` | FR-T4 |
| 行间距 | `.q-row { row-gap: calc(var(--row-gap) * 1em) }` | FR-T4 |
| 题号字号 | `.q-item { font-size: var(--font-size) }` | FR-T4 |
| 答题空位开关 | `--answer-line` + `.q-item[data-answer-line="0"] .q-blank` | FR-T4 |
| 卷头信息栏开关 | `--header-bar` + `.sheet[data-header-bar="0"] .header-bar` | FR-T4 |
| 题块/行容器不跨页 | `.q-row` / `.q-item` / `.a-item` 的 `break-inside: avoid` | NFR-5 |
| 答案版单独成页 | `.answer-section { break-before: page }` | D20 |
"""
from __future__ import annotations

from app.contracts.layout import CSS_VAR_NAMES, LayoutParams, css_variables

__all__ = [
    "A4_WIDTH_MM",
    "A4_HEIGHT_MM",
    "DIRECTIONS",
    "page_rule",
    "style_text",
    "root_style_attr",
    "layout_fingerprint",
    "measure_js",
]

#: A4 物理尺寸（docs/02-技术设计.md §9.2）
A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

#: 排版参数方向：`reflow` 用它的下标表示「往哪个方向调」（D30 六项中的四个可微调项）
#: 每项是 (参数名, 调整步长)。步长取各自取值范围的一个细分，保证调整后仍在界内。
DIRECTIONS: tuple[tuple[str, float], ...] = (
    ("row_gap", -0.1),
    ("font_size", -1),
    ("cols_per_row", 1),
    ("page_margin", -0.2),
)


def page_rule(layout: LayoutParams) -> str:
    """`@page` 规则文本（打印 / PDF 通道的页面几何）。

    **实测结论（本机 Chromium 1223 / Playwright 1.64，2026-10-14）**：
    `@page { margin: var(--page-margin) }` 里的自定义属性**不会被解析**，
    Chromium 会直接忽略整条声明（对照实验见 T-M2-RENDER 证据）。
    所以这里写**具体长度**，同时 `:root` 里仍然注入 `--page-margin`：
    两条通道的数值都来自同一个 `LayoutParams`，不存在第二套排版。
    """
    return (
        "@page {\n"
        "      size: A4;\n"
        f"      margin: {layout.page_margin:g}cm;\n"
        "    }"
    )


def root_style_attr(layout: LayoutParams) -> str:
    """把六个参数渲染成 `style="..."` 属性内容（值全部来自 `css_variables()`）。"""
    variables = css_variables(layout)
    return "; ".join(f"{name}: {value}" for name, value in variables.items())


def layout_fingerprint(layout: LayoutParams) -> str:
    """排版指纹：用于「同一份排版产出同一份结果」的缓存键与断言。"""
    variables = css_variables(layout)
    return "|".join(f"{name}={variables[name]}" for name in sorted(variables))


def style_text(layout: LayoutParams) -> str:
    """完整样式表（`<style>` 的内容）。

    变量名一律取自冻结的 `CSS_VAR_NAMES`，不写字面量，避免与前端漂移。
    """
    var_cols = CSS_VAR_NAMES["cols_per_row"]
    var_gap = CSS_VAR_NAMES["row_gap"]
    var_font = CSS_VAR_NAMES["font_size"]
    var_line = CSS_VAR_NAMES["answer_line"]
    var_header = CSS_VAR_NAMES["header_bar"]

    return f"""
    {page_rule(layout)}

    :root {{
      {root_style_attr(layout)};
    }}

    * {{ box-sizing: border-box; }}

    html, body {{
      margin: 0;
      padding: 0;
      background: #fff;
      color: #000;
      /* 只用系统自带字体，不引外部字体（NFR-9 离线） */
      font-family: "SimSun", "Songti SC", "Times New Roman", serif;
      font-size: var(--font-size);
      line-height: 1.4;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}

    .sheet {{
      width: 100%;
    }}

    /* ---- 卷头 ---- */
    .sheet-title {{
      margin: 0 0 calc(var(--row-gap) * 0.5em) 0;
      font-size: calc(var(--font-size) * 1.5);
      font-weight: 700;
      text-align: center;
      letter-spacing: 0.1em;
    }}
    .sheet-meta {{
      display: flex;
      justify-content: center;
      gap: 1.5em;
      margin-bottom: calc(var(--row-gap) * 0.5em);
      font-size: calc(var(--font-size) * 0.85);
    }}
    .header-bar {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 1em;
      padding: calc(var(--row-gap) * 0.4em) 0;
      margin-bottom: var(--row-gap);
      border-top: 1px solid #000;
      border-bottom: 1px solid #000;
      font-size: calc(var(--font-size) * 0.9);
    }}
    /* 卷头信息栏开关：选择器读的是同一个 CSS 变量的值（由 layout.root_style_attr 注入） */
    .sheet[data-header-bar="0"] .header-bar {{
      display: none;
    }}
    .header-bar span {{ border-bottom: 1px solid #000; display: inline-block; min-width: 5em; }}

    /* ---- 题目网格：每行题数由 --cols-per-row 驱动 ----
       分两层是**实测逼出来的**（本机 Chromium 1223，2026-10-14）：
       `break-inside: avoid` 直接写在 grid 容器上**不生效**——60 行、每行 4 题时
       测到最后一行的底边 (1159.9px) 越过第 1 页内容区下沿 (1114.9px) 被切开。
       把「防截断」放在**块级外层** `.q-row`、网格放进内层 `.q-row-grid` 后，
       Chromium 会把整行整体推到下一页（复测：跨界 0）。
    */
    .q-row {{
      /* NFR-5：行容器（块级）不得被跨页截断 */
      break-inside: avoid;
      page-break-inside: avoid;
    }}

    .q-row-grid {{
      display: grid;
      grid-template-columns: repeat(var({var_cols}), 1fr);
      row-gap: calc(var({var_gap}) * 1em);
      column-gap: calc(var({var_gap}) * 1em);
      margin-bottom: calc(var({var_gap}) * 1em);
    }}

    .q-item {{
      font-size: var({var_font});
      /* NFR-5：题块不得被跨页截断 */
      break-inside: avoid;
      page-break-inside: avoid;
      min-height: calc(var({var_font}) * 1.6);
    }}
    .q-seq {{ font-weight: 700; margin-right: 0.35em; }}
    .q-display {{ white-space: pre-wrap; }}
    /* 填空题的方框排得下就行 */
    .q-display {{ word-break: break-word; }}

    /* 答题空位 / 横线开关：同一个 CSS 变量值驱动的第二处选择器 */
    .q-blank {{
      display: block;
      height: calc(var({var_font}) * 1.3);
      margin-top: 0.15em;
      border-bottom: 1px solid #999;
    }}
    .q-item[data-answer-line="0"] .q-blank {{
      display: none;
    }}

    /* ---- 答案版 ---- */
    /* D20：答案默认单独成页 */
    .answer-section {{
      break-before: page;
      page-break-before: always;
    }}
    .answer-heading {{
      margin: 0 0 calc(var({var_gap}) * 0.6em) 0;
      font-size: calc(var({var_font}) * 1.2);
      font-weight: 700;
      border-bottom: 1px solid #000;
      padding-bottom: 0.2em;
    }}
    .a-item {{
      font-size: calc(var({var_font}) * 0.95);
      margin-bottom: calc(var({var_gap}) * 1em);
      break-inside: avoid;
      page-break-inside: avoid;
    }}
    .a-head {{ font-weight: 700; }}
    .a-answer {{ margin-left: 0.5em; }}
    .a-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.6em;
      font-size: calc(var({var_font}) * 0.8);
      color: #333;
    }}
    .a-tag {{
      border: 1px solid #666;
      border-radius: 2px;
      padding: 0 0.3em;
    }}
    .a-steps {{
      margin: 0.2em 0 0 1.6em;
      padding: 0;
    }}
    .a-steps li {{ font-family: "Consolas", "Courier New", monospace; }}

    .q-display, .a-answer, .a-steps li {{
      /* 题目与答案里的算式若含空格（如「12 × 3 = □」）不能被折叠 */
      white-space: pre-wrap;
    }}
    """


#: 注入页面、量出「每一行题块的排布位置」的脚本。
#:
#: 这是 NFR-5 的**测量仪器**：Chromium 打印成 PDF 后我们看不到内容流，
#: 所以改为在打印前于同一渲染管线里读出每个行容器的 top/height。
#: `export` 用它判断某一行是否会被分页线切开，`layout.reflow` 据此调整参数。
MEASURE_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('.q-row'));
  const items = Array.from(document.querySelectorAll('.q-item'));
  const pxPerCm = 96 / 2.54;
  return {
    pxPerCm,
    docHeight: document.documentElement.scrollHeight,
    bodyWidth: document.body.getBoundingClientRect().width,
    rows: rows.map((el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top + window.scrollY, height: r.height, cols: getComputedStyle(el.querySelector('.q-row-grid') || el).gridTemplateColumns.split(' ').length };
    }),    items: items.map((el) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        top: r.top + window.scrollY,
        left: r.left,
        width: r.width,
        height: r.height,
        fontSize: cs.fontSize,
      };
    }),
    styles: {
      cols: getComputedStyle(document.documentElement).getPropertyValue('--cols-per-row').trim(),
      rowGap: getComputedStyle(document.documentElement).getPropertyValue('--row-gap').trim(),
      fontSize: getComputedStyle(document.documentElement).getPropertyValue('--font-size').trim(),
      pageMargin: getComputedStyle(document.documentElement).getPropertyValue('--page-margin').trim(),
      answerLine: getComputedStyle(document.documentElement).getPropertyValue('--answer-line').trim(),
      headerBar: getComputedStyle(document.documentElement).getPropertyValue('--header-bar').trim(),
    },
    headerVisible: (() => {
      const el = document.querySelector('.header-bar');
      if (!el) return null;
      return getComputedStyle(el).display !== 'none';
    })(),
    blankVisible: (() => {
      const el = document.querySelector('.q-item .q-blank');
      if (!el) return null;
      return getComputedStyle(el).display !== 'none';
    })(),
    answerStartsNewPage: (() => {
      const el = document.querySelector('.answer-section');
      if (!el) return null;
      return getComputedStyle(el).breakBefore || getComputedStyle(el).pageBreakBefore;
    })(),
    externalRefs: Array.from(document.querySelectorAll('[src], [href]')).map((e) => e.getAttribute('src') || e.getAttribute('href')),
    // 分页标定用：绝对定位的 1px 探测块若被分页挤出，浏览器会把它收成 0x0
    probeBlocked: (() => {
      const el = document.getElementById('mathgen-pagination-probe');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return r.width === 0 || r.height === 0;
    })(),
  };
}
"""


def measure_js() -> str:
    """返回测量脚本（函数形式，供 `page.evaluate` 直接调用）。"""
    return MEASURE_JS
