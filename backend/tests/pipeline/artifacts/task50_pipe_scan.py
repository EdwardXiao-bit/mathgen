"""task-50 交付要求⑤：反模式扫描 —— 谁把子进程输出接进了管道，谁在读。

用法（仓库根）：

    backend\\.venv\\Scripts\\python.exe backend\\tests\\pipeline\\artifacts\\task50_pipe_scan.py

对 `backend/tests/**` 下每个 `.py` 做 **AST** 扫描（不看注释/字符串），列出
`Popen/run/call/check_call/check_output` 里 `stdout=`/`stderr=` 为 `PIPE` 的调用点，
并给出同文件里的读数动作、是否牵涉 uvicorn、以及**这次判据能不能抓到它**。
结果落 `artifacts/task50-pipe-scan.json`。
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.dirname(HERE)
TESTS = os.path.dirname(PIPELINE)
BACKEND = os.path.dirname(TESTS)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.pipeline import _pipeline_support as support                  # noqa: E402

READ_HINT = re.compile(r"\.stdout|\.stderr|communicate\(|iter\(")


def classify(rel: str, source: str, sites: list) -> dict:
    mentions_server = "uvicorn" in source.lower()
    reads = bool(READ_HINT.search(source))
    return {
        "file": rel,
        "sites": sites,
        "mentions_uvicorn": mentions_server,
        "reads_child_output_somewhere_in_file": reads,
        "in_deliberate_negative_control": rel.replace("/", "\\").startswith(
            os.path.join("tests", "pipeline")),
        "caught_by_guard": bool(mentions_server and not rel.replace("/", "\\").startswith(
            os.path.join("tests", "pipeline"))),
    }


def main() -> int:
    rows = []
    unparsed = []
    for root, dirs, files in os.walk(TESTS):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, BACKEND)
            with open(path, encoding="utf-8-sig", errors="replace") as handle:
                source = handle.read()
            try:
                sites = support.pipe_popen_sites(source, filename=rel)
            except SyntaxError as exc:
                unparsed.append(f"{rel}: {exc}")
                continue
            if sites:
                rows.append(classify(rel, source, sites))

    payload = {"sites": rows, "unparsed": unparsed,
               "scanned_root": os.path.relpath(TESTS, BACKEND),
               "guard_rule": "mentions_uvicorn && !in tests/pipeline/ → 必须为空"}
    out = support.write_evidence("task50-pipe-scan", payload)

    for row in rows:
        print(f"{row['file']}  sites={row['sites']}  "
              f"uvicorn={row['mentions_uvicorn']}  "
              f"reads={row['reads_child_output_somewhere_in_file']}  "
              f"negative_control={row['in_deliberate_negative_control']}")
    print(f"[scan] {len(rows)} 个调用点文件，{len(unparsed)} 个没解析成功，证据 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
