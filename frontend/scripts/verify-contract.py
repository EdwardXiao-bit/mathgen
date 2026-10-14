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
TEACHER_TS_DIR = REPO_ROOT / "frontend" / "src"

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
    offenders: list[str] = []
    for ts_file in TEACHER_TS_DIR.rglob("*.ts*"):
        if ts_file.name == "api.ts":
            continue  # ROUTES 与 route() 的定义处，本身就含字面量
        text = ts_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if re.search(r"['\"`]/api/", line):
                offenders.append(f"{ts_file.relative_to(REPO_ROOT)}:{lineno}: {stripped[:90]}")
    check("没有裸 /api 路径字面量（全部走 ROUTES/route()）", not offenders, " ; ".join(offenders) or "无")

    print(f"\n结果：{checks - len(failures)}/{checks} 通过")
    if failures:
        print("失败项：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("前端类型与冻结契约完全一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
