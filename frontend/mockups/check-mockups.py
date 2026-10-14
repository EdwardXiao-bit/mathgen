"""样张自检 —— 零外部资源 + 内容一致性（可反复运行，纯标准库，不需要浏览器）。

用法：
    & backend\\.venv\\Scripts\\python.exe frontend\\mockups\\check-mockups.py

检查两条验收标准：
  1. **零外部资源**：所有 HTML 里不出现 http(s)://、//cdn、@import url(...)、<link>、<img>、<script src>
     （`file://` 直开要求；也是项目红线 6 / NFR-9）
  2. **同一份内容**：都含顶部导航、后端连接状态、40 行题目（含「换一题」「删除」）、
     卷面卷头 + 4 列题目 + 答题横线 + 卷号 2610-0001、六项排版控件、两条状态文案

覆盖：3 个方向样张（A/B/C）+ 2 个 C 的细化版（C1 工作台 / C2 聚焦）。
浏览器侧的几何验收（A4 比例、10 行 40 格、不横向溢出、浮动层不互相压住）需要 Playwright，
见 README 的「自检」一节。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).parent
FILES = [
    "option-a-minimal.html",
    "option-b-friendly.html",
    "option-c-canvas.html",
    "option-c1-workbench.html",
    "option-c2-focus.html",
]
#: 需要按「A4 比例 + 40 格 = 4 列 × 10 行」校验的：C 的两个细化版。
#: C 原版（option-c-canvas.html）保留为历史对照，只画了 24 格，不参与这条断言。
SHEET_FILES = ["option-c1-workbench.html", "option-c2-focus.html"]

#: 任何一项命中都算「引用了外部资源」
FORBIDDEN = [
    (r"https?://", "http(s):// 绝对地址"),
    (r"//cdn", "协议相对的 CDN 地址"),
    (r"@import\s+url\(", "CSS @import url(...)"),
    (r"<link\b", "<link> 外链"),
    (r"<img\b", "<img> 外链图片"),
    (r"<script\s+[^>]*src=", "<script src=> 外链脚本"),
    (r"url\(\s*['\"]?\s*(?:https?:)?//", "CSS 里 url(//...) 外链"),
]

#: 三版都必须出现的内容（同一份内容，否则没法公平比较）
REQUIRED = [
    ("产品名 mathgen", r"mathgen"),
    ("导航 出题配置", r"出题配置"),
    ("导航 卷库", r"卷库"),
    ("后端连接状态", r"后端已连接"),
    ("页面标题（卷名）", r"100 以内加减法\s*随堂练"),
    ("状态 草稿（可改题）", r"草稿（可改题）"),
    ("种子", r"20261014"),
    ("卷号", r"2610-0001"),
    ("操作 新建一套", r"新建一套"),
    ("操作 去发卷", r"去发卷"),
    ("操作 打印题目版", r"打印题目版"),
    ("操作 打印答案版", r"打印答案版"),
    ("改题操作 换一题", r"换一题"),
    ("改题操作 删除", r"删除"),
    ("卷头 班级", r"班级："),
    ("卷头 姓名", r"姓名："),
    ("卷头 得分", r"得分："),
    ("排版六项 每行题数", r"每行题数"),
    ("排版六项 行间距", r"行间距"),
    ("排版六项 字号", r"字号"),
    ("排版六项 页边距", r"页边距"),
    ("排版六项 答题横线", r"答题横线"),
    ("排版六项 卷头栏", r"卷头栏"),
    ("状态文案 保留", r"保留"),
    ("状态文案 显示", r"显示"),
    ("反馈 已保存，预览已刷新", r"已保存，预览已刷新"),
    ("视口声明", r'name="viewport"'),
]

checks: list[tuple[str, bool, str]] = []

#: 「改题入口」在 C2 里刻意换成了页面缩略图 + 页码（见 README 的两版差别），
#: 所以对 C2 用另一组「操作入口」断言，而不是强行要求它也有列表与按钮。
EXTRA_REQUIRED = {
    "option-c2-focus.html": [("页面缩略图", r"页面缩略图"), ("页码 1", r">1<"), ("共 1 页", r"共 1 页")],
}


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}")


print("=== ① 零外部资源（file:// 直开的前提）===")
for name in FILES:
    text = (HERE / name).read_text(encoding="utf-8")
    hits: list[str] = []
    for pattern, label in FORBIDDEN:
        for m in re.finditer(pattern, text, re.I):
            snippet = text[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
            hits.append(f"{label}: …{snippet}…")
    check(f"{name}: 零外部资源", not hits, " ; ".join(hits[:3]) if hits else "0 命中")

print("\n=== ② 同一份内容（所有样张必须都可比较）===")
texts = {n: (HERE / n).read_text(encoding="utf-8") for n in FILES}
#: 列表相关的断言对 C2 不适用（它把改题入口换成缩略图 + 页码）
LIST_PATTERNS = {"改题操作 换一题", "改题操作 删除", "操作 新建一套", "操作 去发卷"}
for name, text in texts.items():
    required = REQUIRED
    if name == "option-c2-focus.html":
        required = [r for r in REQUIRED if r[0] not in LIST_PATTERNS] + EXTRA_REQUIRED[name]
    missing = [label for label, pattern in required if not re.search(pattern, text)]
    check(f"{name}: 必含内容齐全", not missing, f"缺少 {missing}" if missing else f"{len(required)} 项全含")

print("\n=== ③ 规模一致性（列表 40 行 / 卷面 4 列 / 答题横线）===")
for name, text in texts.items():
    cells = len(re.findall(r'class="q"[ >]', text))
    check(f"{name}: 卷面网格是 4 列", "repeat(4, 1fr)" in text, "grid-template-columns: repeat(4, 1fr)")
    check(f"{name}: 留有答题横线元素", 'class="q-blank"' in text)
    # C1/C2 必须 40 格 = 4 列 x 10 行（铺满一页）；C 原版与 A/B 维持 24 格
    if name in SHEET_FILES:
        check(f"{name}: 卷面 40 格（4 列 × 10 行，铺满一页）", cells == 40, f"cells={cells}")
    else:
        check(f"{name}: 卷面有题格", cells >= 24, f"cells={cells}")
    # 题目列表：C2 刻意没有列表（缩略图轨道代替）
    if name != "option-c2-focus.html":
        rows = len(re.findall(r'class="row"', text))
        check(f"{name}: 题目列表 40 行", rows == 40, f"rows={rows}")

failed = [c for c in checks if not c[1]]
print(f"\n结果：{len(checks) - len(failed)}/{len(checks)} 通过")
if failed:
    for name, _ok, detail in failed:
        print(f"  - {name} ({detail})")
    raise SystemExit(1)
print("样张自检通过：零外部资源，内容一致。")
