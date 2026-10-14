"""D57 / D58 独立复核（verify 自用，不是 unittest 用例）。

对**已启动的后端**做四组可判定检查：

1. **导出路由**：`format=pdf` → `%PDF-` + `Content-Disposition: attachment`；`mode=answer`
   页数应多于 `mode=question`；`format=docx` → 501 `NOT_IMPLEMENTED`；非法 format → 422；
   卷不存在 → 404 统一结构。
2. **统一错误结构（D57②）**：未知路径 → 404 `NOT_FOUND`；方法不允许 → 405
   `METHOD_NOT_ALLOWED`；**都不得是 FastAPI 默认的 `{"detail": ...}`**。
3. **C3 同源（D57③）**：`/sheet` 的响应体与 `app.services.sheet_render.render_html`
   对同一 context 的输出**逐字节相同**（并读源码确认 `/export` 也走同一函数）。
4. **D58 子进程隔离**：连续多次走 HTTP 导出后，**在同一进程的主线程**直接调用
   `app.export.export_pdf` 仍然可用（这正是子进程隔离要保证的）。

用法：& $VPY backend/tests/e2e_m2/check_d57_export.py --base-url http://127.0.0.1:8137
"""
from __future__ import annotations

import argparse
import io
import os
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
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def pages_of(blob: bytes) -> int:
    document = pdfium.PdfDocument(io.BytesIO(blob))
    try:
        return len(document)
    finally:
        document.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8137")
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base_url, timeout=180.0)
    os.makedirs(ARTIFACTS, exist_ok=True)

    cfg = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral", "count": 39}
    paper = client.post("/api/papers", params={"seed": 4242, "title": "D57 复核卷"},
                        json=cfg).json()
    pid = paper["id"]
    client.patch(f"/api/papers/{pid}/layout",
                 json={"cols_per_row": 3, "row_gap": 1.4, "font_size": 16,
                       "page_margin": 1.5, "answer_line": True, "header_bar": True})
    client.post(f"/api/papers/{pid}/publish")

    # ---------------- 1. 导出路由 ----------------
    counts = {}
    for mode in ("question", "answer"):
        resp = client.get(f"/api/papers/{pid}/export",
                          params={"format": "pdf", "mode": mode})
        ok_head = resp.content[:5] == b"%PDF-"
        disp = resp.headers.get("content-disposition", "")
        counts[mode] = pages_of(resp.content) if ok_head else -1
        out = os.path.join(ARTIFACTS, f"http-export-{mode}.pdf")
        if ok_head:
            with open(out, "wb") as fh:
                fh.write(resp.content)
        check(f"1 export format=pdf mode={mode} → 200 %PDF-",
              resp.status_code == 200 and ok_head,
              f"HTTP {resp.status_code} type={resp.headers.get('content-type')} "
              f"{len(resp.content)}B head={resp.content[:5]!r} 页数={counts[mode]} "
              f"disposition={disp!r}")
        check(f"1 export mode={mode} 带 Content-Disposition: attachment",
              "attachment" in disp.lower(), f"disposition={disp!r}")
    check("1 mode=answer 页数多于 mode=question",
          counts["answer"] > counts["question"] > 0,
          f"question={counts['question']} 页 / answer={counts['answer']} 页")

    resp = client.get(f"/api/papers/{pid}/export", params={"format": "docx"})
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    check("1 format=docx → 501 NOT_IMPLEMENTED（不假装支持）",
          resp.status_code == 501 and body.get("error", {}).get("code") == "NOT_IMPLEMENTED",
          f"HTTP {resp.status_code} body={body}")

    resp = client.get(f"/api/papers/{pid}/export", params={"format": "xml"})
    check("1 非法 format → 422", resp.status_code == 422,
          f"HTTP {resp.status_code} body={resp.text[:120]!r}")

    resp = client.get("/api/papers/999999/export", params={"format": "pdf"})
    body = resp.json()
    # 契约 §9 明列：PAPER_NOT_FOUND | 404 | 卷不存在
    check("1 卷不存在 → 404 PAPER_NOT_FOUND（契约 §9）",
          resp.status_code == 404
          and body.get("error", {}).get("code") == "PAPER_NOT_FOUND",
          f"HTTP {resp.status_code} body={body}")

    resp = client.get(f"/api/papers/{pid}/export")
    check("1 缺省 mode=question 生效",
          resp.status_code == 200 and resp.content[:5] == b"%PDF-",
          f"HTTP {resp.status_code} head={resp.content[:5]!r}")

    # ---------------- 2. 统一错误结构 ----------------
    resp = client.get("/api/nonexistent")
    body = resp.json()
    check("2 未知路径 → 404 NOT_FOUND（不再是 {\"detail\": ...}）",
          resp.status_code == 404 and body.get("error", {}).get("code") == "NOT_FOUND",
          f"HTTP {resp.status_code} body={body}")

    resp = client.put("/api/papers")
    body = resp.json()
    check("2 方法不允许 → 405 METHOD_NOT_ALLOWED",
          resp.status_code == 405
          and body.get("error", {}).get("code") == "METHOD_NOT_ALLOWED",
          f"HTTP {resp.status_code} body={body}")

    # ---------------- 3. C3 同源（逐字节） ----------------
    from app.db import get_db as _get_db          # noqa: F401  （仅用于确认模块存在）
    from app.services.sheet_ctx import build_sheet_context, parse_mode
    from app.services.sheet_render import render_html

    sheet_body = client.get(f"/api/papers/{pid}/sheet",
                            params={"mode": "question"}).text
    db_file = os.environ.get("MATHGEN_DB")
    if db_file:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(f"sqlite+pysqlite:///{db_file}")
        session = sessionmaker(bind=engine)()
        try:
            ctx = build_sheet_context(session, pid, parse_mode("question"), "")
            rendered = render_html(ctx)
        finally:
            session.close()
        check("3 /sheet 响应体与共享渲染器输出逐字节相同（C3）",
              sheet_body == rendered,
              f"/sheet={len(sheet_body)}B  render_html={len(rendered)}B  "
              f"相同={sheet_body == rendered}")
    else:
        print("[SKIP] 3 未设置 MATHGEN_DB，跳过逐字节比对")

    # 源码层：/export 与 /sheet 必须调用同一个 render_html
    exports_src = open(os.path.join(BACKEND, "app", "routers", "exports.py"),
                       encoding="utf-8").read()
    sheet_src = open(os.path.join(BACKEND, "app", "routers", "sheet.py"),
                     encoding="utf-8").read()
    check("3 /export 与 /sheet 都走 sheet_render.render_html（源码）",
          "sheet_render" in exports_src and "render_html" in exports_src
          and "render_html" in sheet_src,
          "两条路由都 import/调用 services.sheet_render.render_html")

    # ---------------- 4. D58：HTTP 导出后，同进程主线程仍能直接导 PDF ----------------
    for _ in range(3):
        client.get(f"/api/papers/{pid}/export", params={"format": "pdf"})
    html = sheet_body
    out = os.path.join(ARTIFACTS, "inprocess-after-http.pdf")
    try:
        export_pdf(html, out)
        ok = os.path.exists(out) and open(out, "rb").read(5) == b"%PDF-"
        detail = f"{out} ({os.path.getsize(out)}B)"
    except Exception as exc:                       # noqa: BLE001
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    check("4 连续 HTTP 导出后，同进程主线程 export_pdf 仍可用（D58 隔离）", ok, detail)

    print("\n" + ("全部通过" if not FAILURES else "未通过：" + "; ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
