"""判据 A1 的**独立**扫描器（verify 自写，不复用 `frontend/scripts/verify-tokens.mjs`）。

与实现方那套的差别（刻意做成第二条路径，便于互证）：
  * 用 Python 自己走目录，**不限定扩展名白名单** —— 只要文件能按 UTF-8 解码就扫，
    这样 `.svg` / `.json` 之类漏网文件也会被覆盖（实现方只扫 6 种扩展名）；
  * 颜色字面量的模式另外写一套（含 `#rgb/#rrggbb/#rrggbbaa`、`rgb(`/`rgba(`、`hsl(`/`hsla(`）；
  * 输出「文件:行:内容」，便于人工判读。

用法：
    & $VPY backend/tests/e2e_ui/scan_tokens.py            # 扫描并打印结论；有命中则退出码 1
    & $VPY backend/tests/e2e_ui/scan_tokens.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SRC = os.path.join(REPO, "frontend", "src")
TOKENS = os.path.join(SRC, "design", "tokens.css")

#: 颜色字面量：十六进制（3/4/6/8 位）与函数式颜色
COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{4}\b|#[0-9a-fA-F]{3}\b"
    r"|\b(?:rgb|rgba|hsl|hsla)\s*\(")


def walk(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", "__pycache__"}]
        for name in sorted(filenames):
            yield os.path.join(dirpath, name)


def scan() -> dict:
    hits = []
    scanned = 0
    skipped_binary = 0
    for path in walk(SRC):
        if os.path.abspath(path) == os.path.abspath(TOKENS):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except (UnicodeDecodeError, OSError):
            skipped_binary += 1
            continue
        scanned += 1
        for index, line in enumerate(lines, 1):
            found = COLOR_RE.findall(line)
            if found:
                hits.append({
                    "file": os.path.relpath(path, REPO).replace("\\", "/"),
                    "line": index,
                    "literals": sorted(set(found)),
                    "text": line.strip()[:140],
                })
    return {"scanned_files": scanned, "skipped_binary": skipped_binary, "hits": hits}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = scan()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("判据 A1 独立扫描（verify 自写）：frontend/src/** 除 design/tokens.css 外的颜色字面量")
        print(f"扫描文件数 = {result['scanned_files']}（按 UTF-8 可读即扫，不设扩展名白名单；"
              f"跳过二进制 {result['skipped_binary']}）")
        print(f"命中行数 = {len(result['hits'])}")
        for hit in result["hits"][:40]:
            print(f"  [HIT] {hit['file']}:{hit['line']}  {' '.join(hit['literals'])}")
            print(f"        {hit['text']}")
        if len(result["hits"]) > 40:
            print(f"  …（共 {len(result['hits'])} 行）")
        print("结论：" + ("A1 通过（计数 = 0）" if not result["hits"] else "A1 不通过"))
    return 1 if result["hits"] else 0


if __name__ == "__main__":
    sys.exit(main())
