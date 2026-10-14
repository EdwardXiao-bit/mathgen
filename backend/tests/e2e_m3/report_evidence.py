"""M3 验收证据打印（verify 自用，**不是测试**）：把关键实测数字打成可引用的原始输出。

    & $VPY backend/tests/e2e_m3/report_evidence.py

A. 产物几何：`artifacts/m3-answer-*.docx` 的结构 + LibreOffice 转 PDF 页数
B. 快照复现：建卷 → 发布 → 按卷号取回 → 打印逐字段一致性结论
C. 单命令启动：只用 uvicorn（无 Vite）的响应码；再验静态目录缺失场景
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_m3_acceptance as T  # noqa: E402  （复用同一套测试侧读回工具）

ARTIFACTS = T.ARTIFACTS


def part_a() -> None:
    print("=== A. docx / PDF 产物几何 ===")
    import glob

    rows = []
    for path in sorted(glob.glob(os.path.join(ARTIFACTS, "m3-answer-*.docx"))):
        with open(path, "rb") as fh:
            blob = fh.read()
        structure = T.docx_structure(blob)
        text = T.docx_text(blob)
        try:
            pdf = T.docx_to_pdf(path)
            pages = T.pdf_pages(pdf)
        except AssertionError as exc:
            pages = f"转换失败({exc})"
        rows.append((os.path.basename(path), structure["paragraphs"], structure["tables"],
                     structure["page_breaks"], len(text), pages))
    print(f"{'文件':44s} {'段落':>5s} {'表格':>5s} {'分页符':>6s} {'文本字符':>8s} {'PDF页数':>7s}")
    for row in rows:
        print(f"{row[0]:44s} {row[1]:5d} {row[2]:5d} {row[3]:6d} {row[4]:8d} {str(row[5]):>7s}")


def part_b() -> None:
    print("\n=== B. 快照复现（发布 → 按卷号取回）===")
    client = T.shared_client()
    paper = client.post("/api/papers", params={"seed": 31337, "title": "证据卷"},
                        json=T.CFG_ORAL).json()
    pid = paper["id"]
    draft = client.get(f"/api/papers/{pid}").json()
    client.post(f"/api/papers/{pid}/layout",
                json={"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
                      "page_margin": 2.0, "answer_line": True, "header_bar": True})
    published = client.post(f"/api/papers/{pid}/publish").json()
    fetched = client.get(f"/api/papers/by-code/{published['code']}").json()
    same = all(a == b for a, b in zip(draft["items"], fetched["items"]))
    payload_rows = T.db_payloads(pid)
    snapshot_same = all(
        all(row["payload"][f] == item[f]
            for f in ("expr", "display", "answer", "difficulty", "tags", "steps"))
        for row, item in zip(payload_rows, fetched["items"]))
    print(f"卷号={published['code']}  id={pid}  题量={len(fetched['items'])}")
    print(f"首题: {fetched['items'][0]['display']!r} answer={fetched['items'][0]['answer']!r}")
    print(f"发布时 vs 按卷号取回：逐字段一致 = {same}")
    print(f"库存 snapshots（paper_item.payload）行数={len(payload_rows)}，与 API 逐字段一致 = {snapshot_same}")

    from app.engine import generate
    from app.engine.types import GenerateConfig, Op, QuestionType, RangeId

    cfg = draft["config"]
    rerun = generate(GenerateConfig(
        ops={Op(o) for o in cfg["ops"]}, ranges=[RangeId(r) for r in cfg["ranges"]],
        question_type=QuestionType(cfg["question_type"]), max_value=cfg["max_value"],
        require_carry=cfg["require_carry"], require_borrow=cfg["require_borrow"],
        exact_division=cfg["exact_division"], allow_remainder=cfg["allow_remainder"],
        parentheses=cfg["parentheses"], count=cfg["count"]), draft["seed"])
    rerun_same = all(q.expr == i["expr"] and q.answer == i["answer"]
                     for q, i in zip(rerun, fetched["items"]))
    print(f"重跑引擎（同 config+seed）与快照一致 = {rerun_same}")

    stale = client.get("/api/papers", params={"page": 2})
    fresh = client.get("/api/papers", params={"limit": 5, "offset": 0})
    missing = client.get("/api/papers/by-code/9999-9999")
    print(f"旧参数 ?page=2 → HTTP {stale.status_code} code={stale.json()['error']['code']}")
    print(f"新参数 ?limit=5 → HTTP {fresh.status_code} limit={fresh.json()['limit']}")
    print(f"无效卷号 → HTTP {missing.status_code} code={missing.json()['error']['code']}")


def part_c() -> None:
    print("\n=== C. 一条命令启动（只用 uvicorn，无 Vite）===")
    import httpx

    port = T.free_port()
    cmd = [T.VPY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    env = dict(os.environ)
    env["MATHGEN_DB"] = os.path.join(ARTIFACTS, f"evidence-{port}.db")
    env.pop("MATHGEN_STATIC_DIR", None)
    env["PYTHONIOENCODING"] = "utf-8"
    print("命令：" + " ".join(cmd[1:]))

    def run(extra_env, label):
        child_env = dict(env)
        child_env.update(extra_env)
        process = subprocess.Popen(cmd, cwd=T.REPO, env=child_env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        try:
            deadline = __import__("time").time() + 40
            while __import__("time").time() < deadline:
                if process.poll() is not None:
                    print(f"[{label}] 提前退出 rc={process.returncode}")
                    return
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/api/papers",
                                 timeout=2.0).status_code == 200:
                        break
                except Exception:
                    pass
                __import__("time").sleep(0.4)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0) as client:
                for path in ("/teacher", "/teacher/paper/1", "/api/papers", "/docs"):
                    response = client.get(path)
                    kind = response.headers.get("content-type", "").split(";")[0]
                    print(f"[{label}] GET {path:22s} → {response.status_code}  {kind}")
            print(f"[{label}] 服务已就绪（uvicorn 进程存活={process.poll() is None}）")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()

    run({}, "dist 存在")
    run({"MATHGEN_STATIC_DIR": os.path.join(ARTIFACTS, "no-such-dist")}, "dist 缺失")


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
