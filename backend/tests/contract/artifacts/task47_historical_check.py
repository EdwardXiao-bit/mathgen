"""task-47 历史实证（**只手动跑一次，不做成常驻测试**）。

为什么单独一个脚本、不放进 `unittest` 套件：常驻测试要读 `git show 1202e15:…`，
就依赖仓库的 git 历史深度（浅克隆 / 导出源码包会让它变红或变哑），而它要证明的事实
是**一次性的**——「M4 收口时的 §7 与**同一时刻的**两个 `App.tsx` 对不上，而当时没有任何
东西会因此变红」。判据本身（`tests.contract.test_route_table_reconciliation`）只读
**当前**工作区，不碰 git 历史，它才是门禁。

对比要同源：拿历史文档去比**当前**代码只能说明「文档旧了」（那是废话），所以本脚本取
`git show <rev>:<path>` 的**四个对象**——`docs/04`、老师端 `App.tsx`、学生端 `App.tsx`、
学生端 `main.tsx` 的 basename——两侧都在 `<rev>` 上，再跑判据的纯函数。

用法（仓库根）：`python backend/tests/contract/artifacts/task47_historical_check.py`
本脚本**只判断据能不能红**，逐条点名缺哪几条路由；不修改任何文件。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT_DIR = HERE.parent
REPO_ROOT = CONTRACT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tests.contract.test_route_table_reconciliation import (  # noqa: E402
    STUDENT_BASENAME,
    compare_routes,
    doc_routes,
    parse_app,
    real_doc_text,
    real_student_text,
    real_teacher_text,
)

HISTORICAL_REV = "1202e15"
TEACHER_PATH = "frontend/src/App.tsx"
STUDENT_PATH = "frontend/src/student/App.tsx"
STUDENT_MAIN_PATH = "frontend/src/student/main.tsx"
DOC_PATH = "docs/04-接口契约.md"


def show(rev: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"取不到 git 历史（{rev}:{path}）→ 历史实证不成立，如实报告而不是跳过：\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


def basename_at(rev: str) -> str:
    """从历史 `main.tsx` 里读 basename；读不到就大声退出，不猜。"""
    text = show(rev, STUDENT_MAIN_PATH)
    match = re.search(r'basename="([^"]*)"', text)
    if match is None:
        raise SystemExit(f"{rev}:{STUDENT_MAIN_PATH} 里读不到 `basename=\"…\"`，判据不猜。")
    return match.group(1)


def compare(doc_text: str, teacher_text: str, student_text: str, base: str) -> tuple[dict, dict, list]:
    doc = doc_routes(doc_text)
    teacher = parse_app(teacher_text, label="老师端", source=TEACHER_PATH)
    student = parse_app(student_text, label="学生端", source=STUDENT_PATH, basename=base)
    code = {**teacher.pages, **student.pages}
    return doc, code, compare_routes(doc, code)


def report(label: str, doc: dict, code: dict, problems: list) -> None:
    print(f"== {label} ==")
    print(f"  §7 表行 = {len(doc)} 条：{sorted(doc)}")
    print(f"  代码页面路由 = {len(code)} 条：{sorted(code)}")
    if problems:
        print(f"  判据输出 **{len(problems)} 条问题**：")
        for line in problems:
            print(f"    - {line}")
    else:
        print("  判据输出：一致（0 条问题）")
    print()


def main() -> int:
    print(f"仓库根：{REPO_ROOT}")
    print(f"历史提交：{HISTORICAL_REV}\n")

    old_base = basename_at(HISTORICAL_REV)
    print(f"[{HISTORICAL_REV}] 学生端 basename = {old_base!r}；当前 = {STUDENT_BASENAME!r}\n")

    old_doc, old_code, old_problems = compare(
        show(HISTORICAL_REV, DOC_PATH),
        show(HISTORICAL_REV, TEACHER_PATH),
        show(HISTORICAL_REV, STUDENT_PATH),
        old_base,
    )
    cur_doc, cur_code, cur_problems = compare(
        real_doc_text(), real_teacher_text(), real_student_text(), STUDENT_BASENAME
    )

    report(f"历史同源对比：{HISTORICAL_REV} 的 §7 ↔ {HISTORICAL_REV} 的两个 App.tsx",
           old_doc, old_code, old_problems)
    report("当前工作区对比（判据作为门禁现场跑）", cur_doc, cur_code, cur_problems)

    missing = sorted(set(old_code) - set(old_doc))
    extra = sorted(set(old_doc) - set(old_code))
    print("== 结论 ==")
    if not old_problems:
        print(f"{HISTORICAL_REV} 当时两侧就一致 —— 本次历史实证没有红，按派单要求如实报告，"
              "不要调判据。")
    else:
        print(f"{HISTORICAL_REV} 当时 §7 与 App.tsx 差 **{len(old_problems)} 条**，"
              "当时代码里有、文档没登记的路由是：")
        for route in missing:
            print(f"    - {route}")
        if extra:
            print(f"  （反向：文档有、代码没有 = {extra}）")
    if cur_problems:
        print(f"**当前工作区也没过判据**（{len(cur_problems)} 条）—— 门禁是红的，先修。")
        return 3
    print("当前工作区：判据绿（两侧各 11 条，一致）。")
    print("\n注：「当时没有东西会因此变红」由判据自身的性质给出：它是 task-47 才新增的，"
          f"{HISTORICAL_REV} 时的 `backend/tests/contract/` 里没有任何一条读 `docs/04` §7。")
    return 0 if old_problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
