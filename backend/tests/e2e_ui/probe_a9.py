"""A9 探查器（verify 自用）：先摸清预览 iframe 与 PDF 的实际几何，再据此写断言。

    & $VPY backend/tests/e2e_ui/probe_a9.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_PYLIBS = os.path.join(os.path.dirname(HERE), "e2e_m2", ".pylibs")
if os.path.isdir(_PYLIBS):
    sys.path.insert(0, _PYLIBS)

from test_ui_structure import (  # noqa: E402
    ARTIFACTS,
    RealServer,
    browser_session,
    ensure_dist_fresh,
)

PREVIEW_JS = r"""
() => {
  const sheet = document.querySelector('.sheet');
  const cs = getComputedStyle(sheet);
  const sr = sheet.getBoundingClientRect();
  const ink = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = Array.from(range.getClientRects());
    if (!rects.length) return null;
    return {x0: Math.min(...rects.map(r => r.left)), x1: Math.max(...rects.map(r => r.right)),
            y0: Math.min(...rects.map(r => r.top)), y1: Math.max(...rects.map(r => r.bottom))};
  };
  const classes = {};
  for (const el of document.querySelectorAll('*')) {
    const cls = (el.className || '').toString().split(/\s+/)[0];
    if (cls) classes[cls] = (classes[cls] || 0) + 1;
  }
  const items = Array.from(document.querySelectorAll('.q-item'));
  const boxes = items.map(el => ({ink: ink(el), rect: el.getBoundingClientRect().toJSON ? null : null,
                                  t: (el.innerText || '').trim().slice(0, 22)}));
  const grid = document.querySelector('.q-grid') || items[0]?.parentElement;
  return {
    docWidth: document.documentElement.scrollWidth,
    sheetWidth: cs.width, sheetPaddingLeft: cs.paddingLeft, sheetPaddingRight: cs.paddingRight,
    sheetFontSize: cs.fontSize, sheetRect: {x: sr.x, y: sr.y, w: sr.width, h: sr.height},
    classes: Object.fromEntries(Object.entries(classes).sort((a, b) => b[1] - a[1]).slice(0, 22)),
    gridStyle: grid ? {cols: getComputedStyle(grid).gridTemplateColumns,
                       gap: getComputedStyle(grid).gap, cls: grid.className} : null,
    itemCount: items.length,
    items: boxes.slice(0, 8),
    uniqueItemHeights: [...new Set(items.map(el => Math.round(el.getBoundingClientRect().height * 100) / 100))],
  };
}
"""


def pdf_metrics(pdf_bytes: bytes) -> dict:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[0]
        width, height = page.get_size()
        textpage = page.get_textpage()
        count = textpage.count_chars()
        chars = []
        for index in range(count):
            char = textpage.get_text_range(index, 1)
            if not char.strip():
                continue
            left, bottom, right, top = textpage.get_charbox(index)
            chars.append({"ch": char, "x0": left, "x1": right, "y0": bottom, "y1": top})
        if not chars:
            return {"page_w": width, "page_h": height, "chars": 0}
        lines: dict[int, list] = {}
        for item in chars:
            key = round(item["y0"] / 3.0)
            lines.setdefault(key, []).append(item)
        rows = []
        for key in sorted(lines, reverse=True):
            row = sorted(lines[key], key=lambda c: c["x0"])
            clusters, current = [], [row[0]]
            for prev, item in zip(row, row[1:]):
                if item["x0"] - prev["x1"] > 25:
                    clusters.append(current)
                    current = [item]
                else:
                    current.append(item)
            clusters.append(current)
            rows.append({"y": min(c["y0"] for c in row),
                         "clusters": [{"x0": c[0]["x0"], "x1": max(x["x1"] for x in c),
                                       "text": "".join(x["ch"] for x in c)[:26]} for c in clusters]})
        return {"page_w": width, "page_h": height, "chars": len(chars),
                "ink_x0": min(c["x0"] for c in chars), "ink_x1": max(c["x1"] for c in chars),
                "ink_y0": min(c["y0"] for c in chars), "ink_y1": max(c["y1"] for c in chars),
                "first_rows": rows[:3]}
    finally:
        document.close()


def main() -> int:
    import httpx

    build = ensure_dist_fresh()
    print("构建：", build)
    with RealServer() as server, httpx.Client(base_url=server.base_url, timeout=60.0) as client:
        paper = client.post("/api/papers", params={"seed": 20261015, "title": "A9 探查"},
                            json={"ops": ["+", "-"], "ranges": ["R2"],
                                  "question_type": "oral", "count": 12}).json()
        pid = paper["id"]
        pdf = client.get(f"/api/papers/{pid}/export",
                         params={"format": "pdf", "mode": "question"})
        print("PDF:", pdf.status_code, len(pdf.content), "bytes")
        metrics = pdf_metrics(pdf.content)
        page_w = metrics["page_w"]
        print(f"\nPDF 页宽 {page_w:.2f}pt  高 {metrics['page_h']:.2f}pt  chars={metrics['chars']}")
        question_rows = []
        for row in metrics.get("first_rows", []):
            pass
        # 打印所有行，挑出「4 列」的题目行
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(pdf.content)
        page = document[0]
        textpage = page.get_textpage()
        chars = []
        for index in range(textpage.count_chars()):
            char = textpage.get_text_range(index, 1)
            if not char.strip():
                continue
            left, bottom, right, top = textpage.get_charbox(index)
            chars.append({"ch": char, "x0": left, "x1": right, "y0": bottom, "y1": top})
        document.close()
        bands: dict[int, list] = {}
        for item in chars:
            bands.setdefault(round(item["y0"] / 3.0), []).append(item)
        print("\n各行（y 从高到低）与列簇 x0：")
        for key in sorted(bands, reverse=True):
            row = sorted(bands[key], key=lambda c: c["x0"])
            clusters, current = [], [row[0]]
            for prev, item in zip(row, row[1:]):
                if item["x0"] - prev["x1"] > 25:
                    clusters.append(current)
                    current = [item]
                else:
                    current.append(item)
            clusters.append(current)
            xs = [round(c[0]["x0"], 2) for c in clusters]
            text = " | ".join("".join(x["ch"] for x in c)[:16] for c in clusters)
            mark = "  <== 4 列题目行" if len(clusters) == 4 else ""
            print(f"  y≈{min(c['y0'] for c in row):7.2f}  簇={len(clusters):2d}  x0={xs}{mark}   {text[:80]}")
            if len(clusters) == 4:
                question_rows.append({"y": min(c["y0"] for c in row), "xs": xs,
                                      "x1_last": max(x["x1"] for x in clusters[-1])})

        if question_rows:
            top = question_rows[0]
            print("\n=== PDF 侧比值 ===")
            print(f"  首列左边界 / 页宽 = {top['xs'][0] / page_w:.5f}")
            print(f"  最右墨迹 / 页宽   = {top['x1_last'] / page_w:.5f}")
            if len(top["xs"]) >= 2:
                pitch = top["xs"][1] - top["xs"][0]
                print(f"  列间距 / 页宽     = {pitch / page_w:.5f}  (pitch={pitch:.2f}pt)")
            print(f"  右留白 / 页宽     = {(page_w - top['x1_last']) / page_w:.5f}")

        with browser_session() as chan:      # 走产品通道（task-22；裸 sync_playwright 会撞 asyncio 守卫）
            browser = chan.browser()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"{server.base_url}/teacher/paper/{pid}", wait_until="networkidle",
                          timeout=60_000)
                page.wait_for_selector("#sheet-frame", timeout=30_000)
                frame = page.query_selector("#sheet-frame").content_frame()
                data = frame.evaluate(r"""() => {
                  const sheet = document.querySelector('.sheet');
                  const sr = sheet.getBoundingClientRect();
                  const ink = (el) => {
                    const range = document.createRange(); range.selectNodeContents(el);
                    const rects = Array.from(range.getClientRects());
                    return {x0: Math.min(...rects.map(r => r.left)), x1: Math.max(...rects.map(r => r.right)),
                            y0: Math.min(...rects.map(r => r.top)), y1: Math.max(...rects.map(r => r.bottom))};
                  };
                  const grid = document.querySelector('.q-row-grid');
                  const items = Array.from(grid.querySelectorAll('.q-item'));
                  const cols = getComputedStyle(grid).gridTemplateColumns.split(' ').map(parseFloat);
                  const boxes = items.map(el => { const b = el.getBoundingClientRect();
                    return {x0: b.x, x1: b.right, w: b.width, h: b.height, ink: ink(el)}; });
                  return {sheet: {x: sr.x, w: sr.width, h: sr.height},
                          cols, gap: parseFloat(getComputedStyle(grid).gap),
                          items: boxes, allHeights: [...new Set(items.map(el => Math.round(el.getBoundingClientRect().height*100)/100))]};
                }""")
                sheet = data["sheet"]
                first = data["items"][0]
                pitch = data["cols"][0] + data["gap"]
                print("\n=== 预览侧比值（iframe 内、未缩放坐标）===")
                print(f"  sheet 宽 = {sheet['w']:.3f}px   列宽 = {[round(c,2) for c in data['cols']]}  gap={data['gap']:.3f}")
                print(f"  首列左边界 / 纸宽 = {(first['x0'] - sheet['x']) / sheet['w']:.5f}")
                print(f"  首列墨迹左 / 纸宽 = {(first['ink']['x0'] - sheet['x']) / sheet['w']:.5f}")
                print(f"  最右列墨迹右 / 纸宽 = {(data['items'][3]['ink']['x1'] - sheet['x']) / sheet['w']:.5f}")
                print(f"  列间距 / 纸宽     = {pitch / sheet['w']:.5f}  (pitch={pitch:.3f}px)")
                print(f"  题块高度集合 = {data['allHeights']}  题块宽={first['w']:.2f}px")
                print(f"  右留白 / 纸宽     = {1 - (data['items'][3]['ink']['x1'] - sheet['x']) / sheet['w']:.5f}")
            finally:
                browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
