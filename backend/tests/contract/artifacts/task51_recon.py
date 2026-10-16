"""task-51 recon (read-only): 数一数候选文本文件、找出 CR / BOM 的现行犯。

用法: & $VPY backend\\tests\\contract\\artifacts\\task51_recon.py --json <path>
不改任何文件；只为给判据定下限与排除项提供实测数字。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.dirname(HERE)
TESTS = os.path.dirname(CONTRACT)
BACKEND = os.path.dirname(TESTS)
REPO = os.path.dirname(BACKEND)

INCLUDE_ROOTS = ("backend/app", "backend/tests", "frontend/src", "frontend/scripts", "docs")
EXCLUDE_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "artifacts",
    ".tmp-uvicorn-logs",
    ".tmp-playwright",
    ".tmp-pw",
    "site-packages",
}
TEXT_SUFFIXES = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss",
    ".html", ".htm", ".md", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".txt", ".sh", ".ps1", ".sql", ".svg", ".csv", ".gitattributes", ".gitignore",
)


def excluded(part: str) -> bool:
    low = part.lower()
    return low in EXCLUDE_PARTS or low.startswith(".tmp-") or low.startswith("tmp-")


def candidates(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not excluded(d)]
        for name in filenames:
            if not name.lower().endswith(TEXT_SUFFIXES):
                continue
            yield os.path.join(dirpath, name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    files = 0
    cr_files = []
    bom_files = []
    undecodable = []
    binary = []
    for root_rel in INCLUDE_ROOTS:
        root = os.path.join(REPO, root_rel.replace("/", os.sep))
        if not os.path.isdir(root):
            print("MISSING ROOT", root)
            continue
        for path in candidates(root):
            files += 1
            with open(path, "rb") as handle:
                data = handle.read()
            if b"\x00" in data[:8192]:
                binary.append(os.path.relpath(path, REPO))
                continue
            if data.startswith(b"\xef\xbb\xbf"):
                bom_files.append(os.path.relpath(path, REPO))
            cr = data.count(b"\r")
            if cr:
                cr_files.append([os.path.relpath(path, REPO), cr, data.count(b"\n")])
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                undecodable.append([os.path.relpath(path, REPO), str(exc)])

    payload = {
        "repo": REPO,
        "files": files,
        "cr_files": sorted(cr_files),
        "bom_files": sorted(bom_files),
        "undecodable": undecodable,
        "binary_skipped": len(binary),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
