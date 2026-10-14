"""排版开关的可判定复核（verify 自用）：cols_per_row / header_bar / answer_line。

只用 HTTP + 两种独立读取方式，不依赖实现方测试：
  · `cols_per_row`：从 `/sheet` HTML 数每个 `data-row-index` 块里的 `.q-item` 个数；
  · `header_bar`：导出的 PDF **文本**里是否出现「班级:」——用 pdfium 取文本（与渲染无关的第二条路径）；
  · `answer_line`：HTML 里是否出现答题线元素 `q-blank`。

用法：& $VPY backend/tests/e2e_m2/check_layout_toggles.py --base-url http://127.0.0.1:8137
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, ".pylibs"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import pypdfium2 as pdfium          # noqa: E402
from app.export import export_pdf   # noqa: E402

ARTIFACTS = os.path.join(HERE, "artifacts")
ROW_RE = re.compile(r'<div class="q-row"[^>]*>(.*?)</div></div>', re.S)
ITEM_RE = re.compile(r'class="q-item"')
ROW_OPEN_RE = re.compile(r'<div class="q-row" data-row-index="(\d+)">')


def rows_of(html: str) -> dict[int, int]:
    """返回 {row_index: 该行题目数}。用 data-row-index 切块，避免误数其他 div。"""
    result = {}
    matches = list(ROW_OPEN_RE.finditer(html))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        block = html[start:end]
        result[int(match.group(1))] = len(ITEM_RE.findall(block))
    return result


def pdf_text(pdf_path: str) -> str:
    document = pdfium.PdfDocument(pdf_path)
    try:
        chunks = []
        for page in document:
            textpage = page.get_textpage()
            try:
                chunks.append(textpage.get_text_range())
            finally:
                textpage.close()
        return "\n".join(chunks)
    finally:
        document.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8137")
    parser.add_argument("--draft-id", type=int, required=True)
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base_url, timeout=120.0)
    failures = []

    def check(name, ok, detail):
        print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")
        if not ok:
            failures.append(name)

    base = {"row_gap": 1.0, "font_size": 14, "page_margin": 2.0,
            "answer_line": True, "header_bar": True}

    # --- cols_per_row：2 / 3 / 4 / 6 每行题数必须恰好等于参数 ---
    for cols in (2, 3, 4, 6):
        client.patch(f"/api/papers/{args.draft_id}/layout", json={**base, "cols_per_row": cols})
        html = client.get(f"/api/papers/{args.draft_id}/sheet",
                          params={"mode": "question"}).text
        distribution = rows_of(html)
        per_row = sorted(set(distribution.values()))
        # 最后一行可能不满，其余必须恰好 cols
        full_rows = [n for n in list(distribution.values())[:-1]]
        check(f"cols_per_row={cols} 每行题数", set(full_rows) == {cols},
              f"各行题数={list(distribution.values())}（末行可不满）")

    # --- answer_line（D59：元素**始终输出**，显隐由 display 变量决定）---
    page_counts = {}
    for flag in (True, False):
        client.patch(f"/api/papers/{args.draft_id}/layout",
                     json={**base, "cols_per_row": 3, "answer_line": flag})
        html = client.get(f"/api/papers/{args.draft_id}/sheet",
                          params={"mode": "question"}).text
        var = re.search(r"--answer-line-display:\s*([a-z]+)", html)
        expected = "block" if flag else "none"
        check(f"answer_line={flag}（D59）元素始终输出且变量为 {expected}",
              'class="q-blank"' in html and bool(var) and var.group(1) == expected,
              f"元素存在={'class=\"q-blank\"' in html} "
              f"--answer-line-display={var.group(1) if var else None}")
        pdf_path = os.path.join(ARTIFACTS, f"answerline-{'on' if flag else 'off'}.pdf")
        export_pdf(html, pdf_path)
        document = pdfium.PdfDocument(pdf_path)
        page_counts[flag] = len(document)
        document.close()
    check("D59 根因：三个「死变量」必须被样式表消费",
          "display: var(--answer-line-display, block)" in html
          and "display: var(--header-bar-display, grid)" in html
          and "var(--page-margin)" in html,
          "样式表含 display:var(--answer-line-display…)/display:var(--header-bar-display…)/var(--page-margin)")
    # 独立交叉验证：有答题线时每行更高 ⇒ 页数不会更少
    check("answer_line 对分页的影响（PDF 页数交叉验证）",
          page_counts[True] >= page_counts[False],
          f"有答题线 {page_counts[True]} 页 / 无答题线 {page_counts[False]} 页")

    # --- header_bar：用 PDF 文本这一独立路径判定 ---
    for flag in (True, False):
        client.patch(f"/api/papers/{args.draft_id}/layout",
                     json={**base, "cols_per_row": 3, "header_bar": flag})
        html = client.get(f"/api/papers/{args.draft_id}/sheet",
                          params={"mode": "question"}).text
        pdf_path = os.path.join(ARTIFACTS, f"header-{'on' if flag else 'off'}.pdf")
        export_pdf(html, pdf_path)
        text = pdf_text(pdf_path)
        found = "班级" in text and "姓名" in text
        check(f"header_bar={flag} 卷头信息栏", found == flag,
              f"PDF 文本含班级/姓名={found}（pdf={os.path.basename(pdf_path)}）")

    print("\n" + ("全部通过" if not failures else "未通过：" + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
