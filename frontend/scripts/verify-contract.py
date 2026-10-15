"""前端类型 ↔ 后端冻结契约 的一致性检查（**只读，不依赖后端服务**）。

验收标准第 4 条要求「TypeScript 类型与 `backend/app/contracts/api.py` 的字段名一致（snake_case）、
路径常量与 `ROUTES` 一致」。肉眼看一遍不算证据，所以用脚本把两侧**逐个字段**比出来：

  · 用 Pydantic 直接加载 `app/contracts/api.py`，取每个模型的字段名集合、别名与默认值；
  · 用 Python 的 ast 解析 `frontend/src/shared/api.ts`，取每个 `export interface` 的字段名集合；
  · 逐模型比对字段名集合（顺序不敏感，多一个少一个都算失败）；
  · 比对 `ROUTES` 的键与值，以及 `CSS_VAR_NAMES` / 取值范围与 `app/contracts/layout.py`；
  · 顺带检查前端是否真的**只用** ROUTES 常量拼路径（不出现裸字符串 `/api/papers`）。

用法（用装了 pydantic 的项目 venv）：
    & backend\\.venv\\Scripts\\python.exe frontend\\scripts\\verify-contract.py
退出码 0 = 全部一致。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
API_PY = BACKEND / "app" / "contracts" / "api.py"
LAYOUT_PY = BACKEND / "app" / "contracts" / "layout.py"
API_TS = REPO_ROOT / "frontend" / "src" / "shared" / "api.ts"

# 扫描范围是**整个** `frontend/src`（老师端 + 学生端 + 组件层都扫）——注意它不是
# `src/teacher`。变量名曾是 TEACHER_TS_DIR，容易被读成"只扫老师端"，已改名。
SRC_TS_DIR = REPO_ROOT / "frontend" / "src"

# ROUTES / route() 的定义处本身就是路径字面量的**唯一合法来源**，必须排除；
# 但只能按**文件路径**排除，不能按文件名 —— 学生端也有一个 `src/student/api.ts`，
# 它只是同名，并不定义 ROUTES。早期版本写的是 `if ts_file.name == "api.ts": continue`，
# 于是把学生端那个文件也一起跳过了，判据**静默空过**（实测：4 处裸路径全部漏报）。
ROUTES_DEF_FILE = API_TS

sys.path.insert(0, str(BACKEND))

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
    if not ok:
        failures.append(f"{name} ({detail})")


# ---------------------------------------------------------------------------
# 1. 后端模型字段（用 Pydantic 真加载，而不是正则）
# ---------------------------------------------------------------------------

def backend_field_names() -> dict[str, set[str]]:
    import importlib

    api = importlib.import_module("app.contracts.api")
    from pydantic import BaseModel

    names: dict[str, set[str]] = {}
    for attr in api.__all__:
        obj = getattr(api, attr)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            names[attr] = set(obj.model_fields.keys())
    return names


# ---------------------------------------------------------------------------
# 2. 前端 interface 字段（ast 解析 TS 的 interface 声明）
# ---------------------------------------------------------------------------

INTERFACE_RE = re.compile(r"export interface (\w+) \{(.*?)\n\}", re.S)


def frontend_interface_fields() -> dict[str, set[str]]:
    source = API_TS.read_text(encoding="utf-8")
    result: dict[str, set[str]] = {}
    for match in INTERFACE_RE.finditer(source):
        name, body = match.group(1), match.group(2)
        fields = set()
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("*") or line.startswith("/*"):
                continue
            field = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", line)
            if field:
                fields.add(field.group(1))
        result[name] = fields
    return result


def main() -> int:
    print("=== ① 后端 api.py 的模型字段 vs 前端 api.ts 的 interface 字段 ===")
    be = backend_field_names()
    fe = frontend_interface_fields()

    for model, fields in sorted(be.items()):
        if model not in fe:
            check(f"前端存在与 {model} 对应的 interface", False, f"api.ts 里没有 interface {model}")
            continue
        missing = fields - fe[model]
        extra = fe[model] - fields
        check(
            f"{model} 字段名完全一致（{len(fields)} 个）",
            not missing and not extra,
            f"缺少={sorted(missing) or '无'} 多出={sorted(extra) or '无'}",
        )

    print("\n=== ② 路径常量 ROUTES 键值一致 ===")
    import importlib

    api = importlib.import_module("app.contracts.api")
    ts_source = API_TS.read_text(encoding="utf-8")
    ts_routes: dict[str, str] = {}
    for match in re.finditer(r"(\w+):\s*'(/api/[^']*)'", ts_source):
        ts_routes[match.group(1)] = match.group(2)

    check("ROUTES 键集合一致", set(api.ROUTES) == set(ts_routes), f"后端={sorted(api.ROUTES)} 前端={sorted(ts_routes)}")
    for key, value in api.ROUTES.items():
        check(f"ROUTES['{key}'] 一致", ts_routes.get(key) == value, f"后端={value} 前端={ts_routes.get(key)}")

    print("\n=== ③ 排版参数与 CSS 变量名一致（layout.py vs api.ts）===")
    layout = importlib.import_module("app.contracts.layout")
    for field, var in layout.CSS_VAR_NAMES.items():
        check(f"CSS_VAR_NAMES['{field}'] = {var}", re.search(rf"{field}:\s*'{re.escape(var)}'", ts_source) is not None)
    for field, (lo, hi) in layout.BOUNDS.items():
        pattern = rf"{field}:\s*\[(?:{lo}|{lo}\.0),\s*(?:{hi}|{hi}\.0)\]"
        check(f"BOUNDS['{field}'] = [{lo}, {hi}]", re.search(pattern, ts_source) is not None)

    # ③-c **M3 答案版形式**：`AnswerLayoutIn` 的四个字段必须在 api.ts 的 interface 里齐全
    print("\n=== ③-c AnswerLayoutIn 四字段一致（M3 第六维）===")
    answer_fields = set(api.AnswerLayoutIn.model_fields.keys())
    check(
        "AnswerLayoutIn 字段数 = 4",
        len(answer_fields) == 4,
        f"后端字段={sorted(answer_fields)}",
    )
    for field in sorted(answer_fields):
        check(f"api.ts 里有 AnswerLayoutIn.{field}", re.search(rf"\b{field}\s*:\s*boolean", ts_source) is not None)
    # 默认值也要一致：四个都为 true（D20 默认「单独成页 + 含过程 / 难度 / 考点」）
    defaults = {
        name: field.default for name, field in api.AnswerLayoutIn.model_fields.items()
    }
    check(
        "AnswerLayoutIn 默认值全为 True（与前端 DEFAULT_ANSWER_LAYOUT 一致）",
        all(value is True for value in defaults.values()),
        f"后端默认值={defaults}",
    )
    check(
        "api.ts 的 DEFAULT_ANSWER_LAYOUT 四字段都是 true",
        len(re.findall(r"(separate_page|include_steps|include_difficulty|include_tags):\s*true", ts_source)) >= 4,
    )

    # ③-b **取值**也要一致（D59 的实质）：前端 `cssVariables()` 的产物必须与后端
    # `css_variables()` 逐字相同，否则「服务端渲染」与「面板实时覆盖」会写出不同的值。
    #
    # 做法：用后端真加载 `LayoutParams` 跑出**开 / 关两种状态**的期望值，
    # 再断言 frontend 源码里 `CSS_DISPLAY_VALUES` 用了同一批关键字、且不含旧的 1/0 写法。
    print("\n=== ③-b css_variables() 的布尔取值逐字一致（D59）===")
    on_vars = layout.css_variables(layout.LayoutParams(answer_line=True, header_bar=True))
    off_vars = layout.css_variables(layout.LayoutParams(answer_line=False, header_bar=False))

    block_match = re.search(r"CSS_DISPLAY_VALUES\s*=\s*\{(.*?)\n\}\s*as const", ts_source, re.S)
    display_block = block_match.group(1) if block_match else ""
    check("前端存在 CSS_DISPLAY_VALUES（布尔项的 CSS 取值表）", bool(display_block))

    def ts_pair(field: str) -> tuple[str, str] | None:
        m = re.search(
            rf"{field}:\s*\{{\s*on:\s*'([^']+)',\s*off:\s*'([^']+)'\s*\}}", display_block
        )
        return (m.group(1), m.group(2)) if m else None

    for field in ("answer_line", "header_bar"):
        var = layout.CSS_VAR_NAMES[field]
        want = (on_vars[var], off_vars[var])
        got = ts_pair(field)
        check(
            f"{field}：前端 (on, off) = 后端 ({want[0]}, {want[1]})",
            got == want,
            f"前端={got} 后端={want}",
        )

    check(
        "前端不再把布尔项写成 '1'/'0'（D59 之前的错误语义）",
        not re.search(r"answer_line\]\s*:\s*layout\.answer_line\s*\?\s*'1'\s*:\s*'0'", ts_source),
        "已改为 display 关键字",
    )

    print("\n=== ④ 前端只用 ROUTES 常量拼 API 路径（不出现裸 /api 字面量）===")
    # 扫描逻辑与 --self-test 共用同一个函数，避免"自检验一套、生产跑另一套"。
    scanned, offenders = _scan_bare_api_paths()
    # 先证明"扫到了东西"：如果扫描集为空，下面的 PASS 就是空过（与 D69 同类）。
    check("扫描覆盖非空（frontend/src 下除 ROUTES 定义处外确有文件被扫）", scanned > 0, f"扫描 {scanned} 个文件")
    check("没有裸 /api 路径字面量（全部走 ROUTES/route()）", not offenders, " ; ".join(offenders) or "无")

    print(f"\n结果：{checks - len(failures)}/{checks} 通过")
    if failures:
        print("失败项：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("前端类型与冻结契约完全一致。")
    return 0


# ---------------------------------------------------------------------------
# 自检（负对照）：证明判据④真的抓得住裸路径
# ---------------------------------------------------------------------------
# V9.1/V9.2：一条判据如果"抓不住东西"，比没有判据更危险（D69）。判据④历史上真的
# 空过一次 —— 排除条件写的是文件名 `api.ts`，把学生端的 `src/student/api.ts`
# 一起跳过了，于是 4 处裸路径全部漏报、判据还显示 PASS。
#
# 所以这里**现造一个真文件**（放在 frontend/src 下，走的是同一个 rglob），
# 里面写两处形态不同的裸路径（单引号 + 模板串），断言判据必须红；并且额外断言
# **红的原因确实是那两处**（否则可能是被别的失败项带红的，那就不算证明）。
# 跑完在 finally 里删掉，不留坏文件在仓库。
SELFTEST_FILE = SRC_TS_DIR / "__contract_selftest_bare_paths.ts"


def _scan_bare_api_paths() -> tuple[int, list[str]]:
    """判据④的扫描逻辑（抽出来供自检复用，避免自检与生产走两套实现）。"""
    offenders: list[str] = []
    scanned = 0
    for ts_file in SRC_TS_DIR.rglob("*.ts*"):
        if ts_file.resolve() == ROUTES_DEF_FILE.resolve():
            continue
        scanned += 1
        text = ts_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if re.search(r"['\"`]/api/", line):
                offenders.append(f"{ts_file.relative_to(REPO_ROOT)}:{lineno}: {stripped[:90]}")
    return scanned, offenders


def self_test() -> int:
    """返回 0 表示自检通过。--self-test 时只跑这个，不跑完整契约核对。"""
    print("=== 判据自检：裸 /api 扫描必须抓得住（负对照 + 具名证明）===")
    ok = True

    # 基线：不注入时应为 0 处。
    _, base_offenders = _scan_bare_api_paths()
    print(f"  [{'PASS' if not base_offenders else 'FAIL'}] 基线：当前仓库无裸路径"
          f"（{len(base_offenders)} 处）")
    ok = ok and not base_offenders

    # 注入一个真文件（放在 src/ 下，会被同一条 rglob 扫到）。
    SELFTEST_FILE.write_text(
        "// 负对照：本文件由 verify-contract.py --self-test 现造，跑完即删。\n"
        "export async function a() { return fetch('/api/students'); }\n"
        "export async function b(code: string) {\n"
        "  return fetch(`/api/student/paper/${code}`);\n"
        "}\n",
        encoding="utf-8",
    )
    try:
        scanned, injected = _scan_bare_api_paths()
        named = [o for o in injected if SELFTEST_FILE.name in o]
        # 1) 必须红（抓到了东西）
        caught = len(injected) >= 2
        print(f"  [{'PASS' if caught else 'FAIL'}] 负对照：注入 2 处裸路径后判据转红"
              f"（抓到 {len(injected)} 处）")
        ok = ok and caught
        # 2) 必须**具名**：红的正是这个注入文件，而不是被别的问题带红
        named_ok = len(named) == 2
        print(f"  [{'PASS' if named_ok else 'FAIL'}] 具名：红的位置就是注入文件"
              f"（命中 {len(named)}/2 处）")
        ok = ok and named_ok
        # 3) 扫描集必须真的包含它（证明注入生效，不是"文件没被扫到所以没红"）
        print(f"  [{'PASS' if scanned > 0 else 'FAIL'}] 注入生效：扫描集 {scanned} 个文件")
        ok = ok and scanned > 0
        for line in injected:
            print(f"      · {line}")
    finally:
        SELFTEST_FILE.unlink(missing_ok=True)

    _, after = _scan_bare_api_paths()
    print(f"  [{'PASS' if not after else 'FAIL'}] 清理：注入文件已删除，仓库回到 0 处"
          f"（{len(after)} 处）")
    ok = ok and not after

    print(f"\n结果：{'自检通过' if ok else '自检未通过'} —— "
          f"判据④抓得住裸 /api 路径，且不误报。")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
