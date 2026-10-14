"""M2 端到端验收运行器（verify 自用，**不是 unittest 用例**）。

对**已启动的后端**发真实 HTTP 请求，走完 M2 里程碑流程：
    生成卷 → 取详情 → 换一题 → 删一题 → 改排版 → 发布 → 取卷面 HTML → 导出 PDF
外加状态机独立复核：发布后编辑 409 / 另存副本 / 删题后题号连续。

用法（仓库根目录，先自行起服务）：

    $VPY = 'C:\\Users\\Ed\\dsh_workspace\\mathgen\\backend\\.venv\\Scripts\\python.exe'
    $env:MATHGEN_DB = '<repo>\\backend\\tests\\e2e_m2\\artifacts\\verify-m2.db'
    & $VPY -m uvicorn app.main:app --host 127.0.0.1 --port 8137 --app-dir backend

    & $VPY backend/tests/e2e_m2/m2_e2e.py --base-url http://127.0.0.1:8137

脚本只做 GET/POST/PATCH/DELETE 与读响应，**不修改任何实现文件**。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.dirname(HERE) if os.path.basename(HERE) == "e2e_m2" else None
BACKEND = os.path.dirname(os.path.dirname(HERE))  # <repo>/backend
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

ARTIFACTS = os.path.join(HERE, "artifacts")

CODE_RE = re.compile(r"^\d{4}-\d{4}$")

RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


def jdump(payload, limit: int = 260) -> str:
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    return text if len(text) <= limit else text[:limit] + f"...(共 {len(text)} 字符)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8137")
    args = parser.parse_args()
    os.makedirs(ARTIFACTS, exist_ok=True)
    client = httpx.Client(base_url=args.base_url, timeout=90.0)

    stamp = int(time.time())
    cfg = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral", "count": 40}

    # ---------------- 1. 生成卷 ----------------
    resp = client.post("/api/papers", params={"seed": 20261014 + stamp % 1000,
                                              "title": "M2 验收卷"},
                       json=cfg)
    step("1 生成卷 POST /api/papers", resp.status_code == 201,
         f"HTTP {resp.status_code} body={jdump(resp.text, 200)}")
    if resp.status_code != 201:
        return finish()
    paper = resp.json()
    pid = paper["id"]
    print(f"      id={pid} status={paper['status']} code={paper['code']} "
          f"items={len(paper['items'])} seed={paper['seed']} layout={jdump(paper['layout'], 120)}")
    print(f"      首题={jdump(paper['items'][0], 150)}")

    # ---------------- 2. 取详情 ----------------
    resp = client.get(f"/api/papers/{pid}")
    detail = resp.json()
    step("2 取详情 GET /api/papers/{id}", resp.status_code == 200 and len(detail["items"]) == 40,
         f"HTTP {resp.status_code} items={len(detail['items'])} status={detail['status']}")
    before = {item["seq"]: item for item in detail["items"]}
    seqs_before = sorted(before)

    # ---------------- 3. 换一题 ----------------
    resp = client.post(f"/api/papers/{pid}/items/2/regenerate")
    regen = resp.json() if resp.status_code == 200 else {}
    detail2 = client.get(f"/api/papers/{pid}").json()
    after_regen = {item["seq"]: item for item in detail2["items"]}
    changed = [s for s in seqs_before if after_regen[s] != before[s]]
    step("3 换一题 POST .../items/2/regenerate",
         resp.status_code == 200 and changed == [2],
         f"HTTP {resp.status_code} 变化题号={changed} 新题={jdump(regen.get('expr'))} "
         f"新答案={jdump(regen.get('answer'))}")

    # ---------------- 4. 删一题 ----------------
    resp = client.delete(f"/api/papers/{pid}/items/3")
    items_after_delete = resp.json() if resp.status_code == 200 else []
    seqs_after = [item["seq"] for item in items_after_delete]
    contiguous = seqs_after == list(range(1, len(seqs_after) + 1))
    step("4 删一题 DELETE .../items/3",
         resp.status_code == 200 and len(seqs_after) == 39 and contiguous,
         f"HTTP {resp.status_code} 剩余={len(seqs_after)} seq={seqs_after[:6]}...{seqs_after[-3:]} "
         f"连续={contiguous}")

    # ---------------- 5. 改排版 ----------------
    layout = {"cols_per_row": 3, "row_gap": 1.4, "font_size": 16,
              "page_margin": 1.5, "answer_line": True, "header_bar": True}
    resp = client.patch(f"/api/papers/{pid}/layout", json=layout)
    step("5 改排版 PATCH .../layout",
         resp.status_code == 200 and resp.json() == layout,
         f"HTTP {resp.status_code} 回显={jdump(resp.text, 160)}")

    # ---------------- 6. 发布 ----------------
    resp = client.post(f"/api/papers/{pid}/publish")
    pub = resp.json() if resp.status_code == 200 else {}
    step("6 发布 POST .../publish",
         resp.status_code == 200 and CODE_RE.match(pub.get("code", "")) is not None,
         f"HTTP {resp.status_code} code={pub.get('code')} qr_url={pub.get('qr_url')} "
         f"student_url={pub.get('student_url')}")

    # ---------------- 7. 取卷面 HTML ----------------
    resp_q = client.get(f"/api/papers/{pid}/sheet", params={"mode": "question"})
    html_q = resp_q.text
    resp_a = client.get(f"/api/papers/{pid}/sheet", params={"mode": "answer"})
    html_a = resp_a.text
    open(os.path.join(ARTIFACTS, "sheet-question.html"), "w", encoding="utf-8").write(html_q)
    open(os.path.join(ARTIFACTS, "sheet-answer.html"), "w", encoding="utf-8").write(html_a)
    answers = [item["answer"] for item in before.values()]
    leaked = [a for a in answers if f">{a}<" in html_q]
    step("7a 取卷面 HTML（题目版/答案版）",
         resp_q.status_code == 200 and resp_a.status_code == 200
         and len(html_q) > 2000 and len(html_a) > 2000,
         f"HTTP {resp_q.status_code}/{resp_a.status_code} question={len(html_q)}B "
         f"answer={len(html_a)}B")
    step("7b 题目版不泄露 answer", not leaked, f"命中答案文本的答案={leaked[:3]}")
    var_ok = "--cols-per-row:3" in html_q.replace(" ", "")
    step("7c 卷面 HTML 内联后端排版变量", var_ok,
         "命中 --cols-per-row:3" if var_ok else f"未命中；style 片段={jdump(html_q[:400])}")

    # ---------------- 7d. 导出 PDF（契约 §5.1 的路由） ----------------
    resp_pdf = client.get(f"/api/papers/{pid}/export", params={"format": "pdf", "mode": "question"})
    route_ok = resp_pdf.status_code == 200 and resp_pdf.content[:4] == b"%PDF"
    step("7d 导出 PDF GET /api/papers/{id}/export?format=pdf", route_ok,
         f"HTTP {resp_pdf.status_code} content-type={resp_pdf.headers.get('content-type')} "
         f"body={jdump(resp_pdf.text[:200])}")

    # ---------------- 状态机独立复核 ----------------
    r_edit = client.patch(f"/api/papers/{pid}/layout", json=layout)
    r_del = client.delete(f"/api/papers/{pid}/items/4")
    r_reg = client.post(f"/api/papers/{pid}/items/4/regenerate")
    codes = (r_edit.status_code, r_del.status_code, r_reg.status_code)
    bodies = [r.json().get("error", {}).get("code") for r in (r_edit, r_del, r_reg)]
    step("S1 发布后再编辑被拒（PATCH/DELETE/regenerate → 409）",
         codes == (409, 409, 409) and set(bodies) == {"PAPER_NOT_EDITABLE"},
         f"HTTP={codes} error.code={bodies}")

    r_copy = client.post(f"/api/papers/{pid}/copy")
    cp = r_copy.json() if r_copy.status_code == 201 else {}
    step("S2 另存副本得到新 id 且无 code",
         r_copy.status_code == 201 and cp.get("id") != pid and cp.get("code") is None
         and cp.get("status") == "draft" and len(cp.get("items", [])) == 39,
         f"HTTP {r_copy.status_code} new_id={cp.get('id')} code={cp.get('code')} "
         f"status={cp.get('status')} items={len(cp.get('items', []))}")
    cid = cp.get("id")

    if cid is not None:
        r_del2 = client.delete(f"/api/papers/{cid}/items/3")
        seqs2 = [i["seq"] for i in (r_del2.json() if r_del2.status_code == 200 else [])]
        step("S3 副本删题后题号连续无空洞",
             r_del2.status_code == 200 and seqs2 == list(range(1, len(seqs2) + 1)),
             f"HTTP {r_del2.status_code} n={len(seqs2)} seq头={seqs2[:5]} 尾={seqs2[-2:]}")

    # 发布后可再编辑副本（draft）
    if cid is not None:
        r_l2 = client.patch(f"/api/papers/{cid}/layout",
                            json={**layout, "cols_per_row": 4})
        step("S4 副本（draft）可编辑", r_l2.status_code == 200,
             f"HTTP {r_l2.status_code} body={jdump(r_l2.text, 100)}")

    r_qr = client.get(f"/api/papers/{pid}/qr")
    step("S5 二维码可用 GET .../qr",
         r_qr.status_code == 200 and r_qr.content[:4] == b"\x89PNG",
         f"HTTP {r_qr.status_code} type={r_qr.headers.get('content-type')} "
         f"bytes={len(r_qr.content)}")

    # D64：列表参数为 limit / offset（page/size 已是 deprecated 别名，M4 起移除）
    r_list = client.get("/api/papers", params={"limit": 5, "offset": 0})
    step("S6 卷库列表 GET /api/papers", r_list.status_code == 200,
         f"HTTP {r_list.status_code} total={r_list.json().get('total')} "
         f"limit={r_list.json().get('limit')} offset={r_list.json().get('offset')}")

    # 供后续脚本用：把关键 id/HTML 落盘
    meta = {"paper_id": pid, "copy_id": cid, "code": pub.get("code"),
            "base_url": args.base_url, "cfg": cfg, "layout": layout}
    with open(os.path.join(ARTIFACTS, "run-meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return finish()


def finish() -> int:
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n===== 汇总 =====")
    print(f"通过 {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    if failed:
        print("未通过：" + "; ".join(failed))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
