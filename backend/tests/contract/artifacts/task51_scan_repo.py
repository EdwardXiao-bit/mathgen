"""task-51 证据工具：对真实仓库跑一遍判据并落盘（只读）。

用法: & $VPY backend\\tests\\contract\\artifacts\\task51_scan_repo.py --json <path>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.dirname(HERE)
BACKEND = os.path.dirname(os.path.dirname(CONTRACT))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.contract import _text_hygiene_support as support  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    parser.add_argument("--root", default=support.REPO)
    args = parser.parse_args()

    problems, observation = support.text_hygiene_problems(args.root)
    payload = {
        "summary": support.summarise(observation),
        "problems": problems,
        "observation": observation,
        "min_files": support.MIN_FILES,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
