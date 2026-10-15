"""V8 哈希夹逼工具（task-25 专用，可重跑）：把「我验的东西」与「我写的东西」分别聚合。

口径：
* `backend_app`：`backend/app/**/*.py`（**被验对象**，我一行都不许动）
* `backend_tests_ex_security`：`backend/tests/**/*.py` **排除** `backend/tests/security/**`
  （task-24 的既有测试也在被验范围内，但我不能改它们 → 必须排除我自己的写域）
* `backend_tests_all`：`backend/tests/**/*.py`（含我的写域，便于看出「变了什么」）
* `security`：`backend/tests/security/**/*.py`（**我的写域**，允许变）

跳过 `__pycache__` / `artifacts` / `.pylibs` / `.venv`。

用法：`python agg_hash.py T1` → 写 `artifacts/hash-T1.json`；再跑 `T3` 后比对。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECURITY = HERE.parent
TESTS = SECURITY.parent
BACKEND = TESTS.parent
REPO = BACKEND.parent

SKIP_DIRS = {"__pycache__", "artifacts", ".pylibs", ".venv", ".tmp-pw"}
MY_SCOPE = SECURITY.resolve()


def digest(paths: list[Path]) -> dict[str, object]:
    entries: list[tuple[str, str]] = []
    for path in sorted(paths):
        entries.append((path.relative_to(REPO).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    blob = "\n".join(f"{name}:{sha}" for name, sha in entries).encode("utf-8")
    return {
        "aggregate_sha256_16": hashlib.sha256(blob).hexdigest()[:16].upper(),
        "file_count": len(entries),
        "files": dict(entries),
    }


def py_files(root: Path, *, exclude: Path | None = None) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if exclude is not None and exclude in path.resolve().parents:
            continue
        found.append(path)
    return found


def collect() -> dict[str, object]:
    return {
        "backend_app": digest(py_files(BACKEND / "app")),
        "backend_tests_ex_security": digest(py_files(BACKEND / "tests", exclude=MY_SCOPE)),
        "backend_tests_all": digest(py_files(BACKEND / "tests")),
        "security_scope": digest(py_files(MY_SCOPE)),
    }


def main(argv: list[str]) -> int:
    label = argv[1] if len(argv) > 1 else "current"
    report = {"label": label, **collect()}
    target = HERE / f"hash-{label}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for key in ("backend_app", "backend_tests_ex_security", "backend_tests_all", "security_scope"):
        block = report[key]
        print(f"{key:28s} {block['aggregate_sha256_16']}  ({block['file_count']} files)")
    print(f"[hash] 快照落入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
