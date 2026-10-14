"""M2 实物验收：生成 PDF 并用**两条独立路径**实测页边距 / 分页（verify 自用）。

与 render-export 的自测不同：
  · 它的路径 = 手写 PDF 内容流解析（文本矩阵 + CTM）；
  · 本脚本路径 ① = **pdfium 自己的字符盒**（`pypdfium2` 的 `get_charbox`）；
  · 本脚本路径 ② = **把 PDF 光栅化成图，量 ink（非白像素）边界**（Pillow `getbbox`）。
两条路径都不读它解析出的任何数字。

排版改动**走真实 HTTP**（PATCH /layout + GET /sheet）；只有最后一步
「HTML → PDF 字节」用产品库 `app.export.export_pdf`（因为契约 §5.1 的
`GET /api/papers/{id}/export` 路由当前不存在，见端到端报告 7d）。

用法：
    & $VPY backend/tests/e2e_m2/make_and_inspect_pdf.py --base-url http://127.0.0.1:8137
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))          # <repo>/backend/tests/e2e_m2
BACKEND = os.path.dirname(os.path.dirname(HERE))           # <repo>/backend
REPO = os.path.dirname(BACKEND)                            # <repo>
sys.path.insert(0, os.path.join(HERE, ".pylibs"))          # pypdfium2（verify 自装，仅本目录）
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import pypdfium2 as pdfium                            # noqa: E402
from app.export import export_pdf                     # noqa: E402

ARTIFACTS = os.path.join(HERE, "artifacts")
PT_PER_MM = 72.0 / 25.4
A4_PT = (595.276, 841.89)


def mm(pt: float) -> float:
    return pt / PT_PER_MM


def pdf_text_xmin_pt(page) -> float | None:
    """路径 ①：pdfium 解析出的**字符盒**最小 x（pt）。"""
    textpage = page.get_textpage()
    try:
        count = textpage.count_chars()
        xs = []
        for index in range(count):
            try:
                left, _bottom, _right, _top = textpage.get_charbox(index, loose=True)
            except Exception:                      # 空白字符可能没有盒子
                continue
            if left is not None:
                xs.append(left)
        return min(xs) if xs else None
    finally:
        textpage.close()


def raster_ink_bbox(png_path: str, threshold: int = 245):
    """路径 ②：光栅 ink 边界（像素）。返回 (bbox, size)。"""
    image = Image.open(png_path).convert("L")
    ink = image.point(lambda p: 255 if p < threshold else 0)
    return ink.getbbox(), image.size


def render_page(pdf_path: str, page_index: int, scale: float, out_png: str) -> str:
    document = pdfium.PdfDocument(pdf_path)
    try:
        page = document[page_index]
        bitmap = page.render(scale=scale)
        bitmap.to_pil().save(out_png)
        return out_png
    finally:
        document.close()


def inspect_pdf(pdf_path: str, scale: float, tag: str, keep_png: bool = True):
    """返回每页的实测几何（mm）。"""
    document = pdfium.PdfDocument(pdf_path)
    pages = []
    try:
        count = len(document)
        for index in range(count):
            page = document[index]
            width, height = page.get_size()
            xmin_pt = pdf_text_xmin_pt(page)
            png = os.path.join(ARTIFACTS, f"{tag}-p{index + 1}.png")
            bitmap = page.render(scale=scale)
            bitmap.to_pil().save(png)
            bbox, size = raster_ink_bbox(png)
            if not keep_png:
                os.remove(png)
            ink_mm = None
            if bbox:
                x0, y0, x1, y1 = bbox
                ink_mm = {
                    "left": x0 / scale / PT_PER_MM,
                    "top": y0 / scale / PT_PER_MM,
                    "right": (size[0] - x1) / scale / PT_PER_MM,
                    "bottom": (size[1] - y1) / scale / PT_PER_MM,
                }
            pages.append({
                "page": index + 1,
                "size_pt": (round(width, 2), round(height, 2)),
                "text_xmin_mm": round(mm(xmin_pt), 2) if xmin_pt is not None else None,
                "ink_mm": {k: round(v, 2) for k, v in ink_mm.items()} if ink_mm else None,
            })
    finally:
        document.close()
    return pages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8137")
    parser.add_argument("--draft-id", type=int, default=None)
    args = parser.parse_args()
    os.makedirs(ARTIFACTS, exist_ok=True)
    client = httpx.Client(base_url=args.base_url, timeout=120.0)

    meta_path = os.path.join(ARTIFACTS, "run-meta.json")
    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
    draft_id = args.draft_id or meta.get("copy_id")
    published_id = meta.get("paper_id")

    report: dict = {"margin_experiment": [], "documents": {}}

    # ---------------- 实验 1：page_margin 1/2/3 cm 是否真的落到纸上 ----------------
    if draft_id:
        for margin in (1.0, 2.0, 3.0):
            layout = {"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
                      "page_margin": margin, "answer_line": True, "header_bar": False}
            r = client.patch(f"/api/papers/{draft_id}/layout", json=layout)
            html = client.get(f"/api/papers/{draft_id}/sheet",
                              params={"mode": "question"}).text
            pdf_path = os.path.join(ARTIFACTS, f"margin-{margin:g}.pdf")
            export_pdf(html, pdf_path)
            pages = inspect_pdf(pdf_path, scale=4.0, tag=f"margin-{margin:g}-p",
                                keep_png=False)
            first = pages[0]
            nominal_mm = margin * 10.0
            report["margin_experiment"].append({
                "page_margin_cm": margin,
                "nominal_mm": nominal_mm,
                "pdf": pdf_path,
                "pages": len(pages),
                "page1_size_pt": first["size_pt"],
                "path1_pdfium_text_xmin_mm": first["text_xmin_mm"],
                "path2_raster_ink_left_mm": first["ink_mm"]["left"] if first["ink_mm"] else None,
                "path2_raster_ink_right_mm": first["ink_mm"]["right"] if first["ink_mm"] else None,
                "patch_status": r.status_code,
            })
            print(f"[margin {margin:g}cm] 名义={nominal_mm:.2f}mm  "
                  f"路径①pdfium字符盒 x_min={first['text_xmin_mm']}mm  "
                  f"路径②光栅 ink x_min={first['ink_mm']['left'] if first['ink_mm'] else None}mm  "
                  f"页面={first['size_pt']}pt  页数={len(pages)}")

    # ---------------- 实验 2：真卷面（题目版 / 答案版）的 PDF 与分页 ----------------
    for mode in ("question", "answer"):
        source = published_id if published_id else draft_id
        html = client.get(f"/api/papers/{source}/sheet", params={"mode": mode}).text
        pdf_path = os.path.join(ARTIFACTS, f"paper-{mode}.pdf")
        export_pdf(html, pdf_path)
        pages = inspect_pdf(pdf_path, scale=1.6, tag=f"paper-{mode}")
        report["documents"][mode] = {"pdf": pdf_path, "pages": pages}
        print(f"\n[paper-{mode}] {pdf_path}  页数={len(pages)}")
        for page in pages:
            ink = page["ink_mm"]
            print(f"    第{page['page']}页 尺寸={page['size_pt']}pt  "
                  f"文本x_min={page['text_xmin_mm']}mm  "
                  f"ink 左{ink['left']} 上{ink['top']} 右{ink['right']} 下{ink['bottom']} mm")

    # A4 校验
    a4_ok = all(
        abs(page["size_pt"][0] - A4_PT[0]) < 1.5 and abs(page["size_pt"][1] - A4_PT[1]) < 1.5
        for doc in report["documents"].values() for page in doc["pages"])
    report["a4_ok"] = a4_ok
    print(f"\nA4 媒体盒校验（595.28×841.89pt ±1.5）: {'OK' if a4_ok else '不通过'}")

    q_pages = len(report["documents"]["question"]["pages"])
    a_pages = len(report["documents"]["answer"]["pages"])
    report["answer_mode_pages"] = a_pages
    report["question_mode_pages"] = q_pages
    print(f"题目版 {q_pages} 页 / 答案版 {a_pages} 页（答案版单独成页 → 页数不少于题目版）")

    with open(os.path.join(ARTIFACTS, "pdf-geometry-report.json"), "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\n几何报告 -> backend/tests/e2e_m2/artifacts/pdf-geometry-report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
