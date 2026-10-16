"""task-51 原始证据工具：把「能红 / 能绿 / 失明也红」三态各跑一遍并落盘（只读真仓库）。

用法:
    & $VPY backend\\tests\\contract\\artifacts\\task51_evidence.py --json <path>

它只写自己造的临时树（`<仓库>/.tmp-task51-trees/<hex>`，用完即删），
真仓库那一段只读。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.dirname(HERE)
BACKEND = os.path.dirname(os.path.dirname(CONTRACT))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.contract import _text_hygiene_support as support  # noqa: E402

CRLF = b"x = 1\r\ny = 2\r\n"
BOM = b"\xef\xbb\xbfx = 1\n"
CLEAN = b"x = 1\ny = 2\n"
TREE_BASE = os.path.join(support.REPO, ".tmp-task51-trees")


def make_tree(files: dict) -> str:
    tree = os.path.join(TREE_BASE, uuid.uuid4().hex[:10])
    for rel, data in files.items():
        path = os.path.join(tree, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    return tree


def scan(tree: str, **kwargs):
    kwargs.setdefault("include_roots", ("tree",))
    problems, observation = support.text_hygiene_problems(tree, **kwargs)
    return {"problems": problems, "summary": support.summarise(observation),
            "observation": observation}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    trees: list[str] = []
    payload: dict = {"repo": support.REPO, "min_files": support.MIN_FILES}
    try:
        trees.append(make_tree({
            "tree/dirty_crlf.py": CRLF,
            "tree/dirty_bom.py": BOM,
            "tree/clean.py": CLEAN,
        }))
        payload["dirty_tree"] = scan(trees[-1], min_files=1)

        trees.append(make_tree({"tree/clean.py": CLEAN}))
        payload["clean_tree"] = scan(trees[-1], min_files=1)

        trees.append(make_tree({"tree/.keep": b"x\n"}))
        payload["empty_tree"] = scan(trees[-1], min_files=1)

        payload["nonexistent_root"] = scan(
            os.path.join(support.REPO, ".tmp-task51-does-not-exist"), min_files=1)

        real_problems, real_observation = support.text_hygiene_problems()
        payload["real_repo"] = {
            "problems": real_problems,
            "summary": support.summarise(real_observation),
            "observation": real_observation,
        }
    finally:
        for tree in trees:
            shutil.rmtree(tree, ignore_errors=True)
        if os.path.isdir(TREE_BASE) and not os.listdir(TREE_BASE):
            shutil.rmtree(TREE_BASE, ignore_errors=True)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
