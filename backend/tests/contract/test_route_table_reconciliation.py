"""`docs/04-接口契约.md` §7 前端路由表 ↔ 两个 `App.tsx` 的 `Route path=` 逐行对账
（task-47 / T-M6-07 / 与 D79 同族）。

## 为什么存在

§7 是一张**手工维护**的前端路由表，而路由的真相在 `frontend/src/App.tsx`（老师端）与
`frontend/src/student/App.tsx`（学生端）。M5 收口时核对发现两条漏记：

* task-40 新增的两条老师端成绩路由（`/teacher/attempts`、`/teacher/attempts/:attemptId`）没登记；
* **更早的那条漏记一直没人发现** —— `/student/paper/:code` 从 M4 起就在跑，却从来没进过 §7。

根因与 D79 完全同族：**靠人记得同步的约定迟早漂移，而且漂移是静默的** ——
没有任何东西会因为文档少一行而变红。所以这里有机器判据，而不是「以后记得同步」。

## 判据读什么（两侧都不猜格式）

* **文档侧**：§7 表格行 `| 老师 | /teacher/library | 卷库 |`（路由那一格是内联代码），只认这种形态——
  第一个单元格是 `老师`/`学生`，第二个单元格是**单行内联代码**里的路由字面量。
  行首不是这两个端名的行、或第二个单元格不是内联代码的行，都不算表行。
* **代码侧**：`<Route path="…">` 的**字面量**。用花括号感知的扫描取标签（`element={…}` 里
  可能嵌 JSX，出现 `>` 不能当标签结束），再用标签栈还原嵌套：
  `/teacher` 的子路由（`paper/:id` 这类相对路径）拼成 `/teacher/paper/:id`；
  学生端按 `frontend/src/student/main.tsx` 的 `basename="/student"` 拼前缀。
* **不算页面路由的两类**：`path="*"` 兜底路由；`element={<Navigate …/>}` 重定向
  （例如老师端的 `/` → `/teacher`）。重定向**不要求**登记成表行，但它的**目标**必须在表里
  ——否则「表里少一条真页面」会被重定向悄悄盖住。
* **参数名不比**：`/teacher/paper/:paperId`（代码）与 `/teacher/paper/:id`（文档）视作同一条，
  两边都归一成 `:id`。比参数名只会制造假红（改名不改结构，改的是实现口味不是契约）。

## 下限（两侧都要有，防「空集对空集」全绿）

`MIN_DOC_ROWS = 8`、`MIN_CODE_ROUTES = 8`，另按端各一条下限
（`MIN_TEACHER_ROUTES = 5`、`MIN_STUDENT_ROUTES = 3`）。任何一侧解析不出东西、
或整体缩水到下限以下，都**大声红**并说明原因；读不到文件也大声红。

## 诚实边界（本判据做不到什么）

* **两边同时漏同一条**（文档和 `App.tsx` 一起少一条路由）它看不见 —— 它比的是「两侧一致」，
  不是「路由齐不齐」。下限只能挡整体缩水，不是覆盖率证明。
* 只比**路径结构**：不比 `element` 绑了哪个组件、不比嵌套层级怎么写的、不比参数名、
  不比 `Route` 的书写顺序、不比 `docs/04` 其它章节。
* 依赖「§7 用表格 + 内联代码」「`App.tsx` 用 `<Route path="字面量">`」这两个形态。
  形态变了会**大声红**（未解析行 / 下限），不会静默变绿。
* `docs/04` §7 是**唯一的**文档侧来源；`frontend/src/**/*.tsx` 里别处若再注册路由
  （例如 `createBrowserRouter` 或手写 `navigate()`），它看不到。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTE_DOC = REPO_ROOT / "docs" / "04-接口契约.md"
TEACHER_APP = REPO_ROOT / "frontend" / "src" / "App.tsx"
STUDENT_APP = REPO_ROOT / "frontend" / "src" / "student" / "App.tsx"

MIN_DOC_ROWS = 8
MIN_CODE_ROUTES = 8
MIN_TEACHER_ROUTES = 5
MIN_STUDENT_ROUTES = 3

# 学生端路由挂在 BrowserRouter 的 basename 下（frontend/src/student/main.tsx:26）
STUDENT_BASENAME = "/student"

_DOC_ROW_RE = re.compile(r"^\|\s*(老师|学生)\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
_PARAM_RE = re.compile(r":[A-Za-z_]\w*")
_TAG_SCAN_RE = re.compile(r"<Route\b|</Route>")
_PATH_ATTR_RE = re.compile(r"\bpath=\"([^\"]*)\"")
_TO_ATTR_RE = re.compile(r"\bto=\"([^\"]*)\"")
_NAVIGATE_RE = re.compile(r"<Navigate\b")


# --------------------------------------------------------------------------- 公共


def normalise_route(route: str) -> str:
    """路径归一：去尾斜杠、参数名统一成 `:id`。参数名不是契约的一部分。"""
    text = route.strip()
    if text != "/":
        text = text.rstrip("/") or "/"
    return _PARAM_RE.sub(":id", text)


def read_text(path: Path, what: str) -> str:
    """读不到就大声红（不是 `skipTest`，也不是空串）。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 正常路径下不会走到
        raise AssertionError(f"读不到{what}：{path}（{exc}）") from exc


# --------------------------------------------------------------------------- 文档侧


def doc_routes(text: str) -> dict[str, tuple[int, str]]:
    """§7 表行 → `{归一化路由: (行号, 页面说明)}`。

    只认 `| 老师|学生 | \\`路由\\` | … |` 形态；一条都解析不出来时大声红
    （而不是返回空字典，让「空集对空集」蒙混过关）。
    """
    found: dict[str, tuple[int, str]] = {}
    for match in _DOC_ROW_RE.finditer(text):
        line_no = text.count("\n", 0, match.start()) + 1
        route = normalise_route(match.group(2))
        found[route] = (line_no, match.group(1))
    if not found:
        raise AssertionError(
            "在 `docs/04-接口契约.md` 里找不到任何 `| 老师|学生 | `…` | … |` 形式的表行 —— "
            "§7 的表格形态变了（或文件拿错了），判据拒绝在「解析出空集」的情况下给结论。"
        )
    return found


# --------------------------------------------------------------------------- 代码侧


def route_tokens(text: str) -> list[tuple[str, str, int]]:
    """把文本切成 `("open", 标签原文, 行号)` / `("close", "", 行号)` 序列。

    `<Route` 到它的 `>` 之间用**花括号深度**计数：`element={…}` 里可能嵌 JSX 并出现 `>`，
    拿 `<Route[^>]*>` 这种朴素正则会在那里截断，从而漏掉整条路由。
    """
    tokens: list[tuple[str, str, int]] = []
    pos = 0
    while True:
        match = _TAG_SCAN_RE.search(text, pos)
        if match is None:
            break
        if match.group(0) == "</Route>":
            tokens.append(("close", "", text.count("\n", 0, match.start()) + 1))
            pos = match.end()
            continue
        cursor = match.end()
        depth = 0
        while cursor < len(text):
            char = text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
            elif char == ">" and depth == 0:
                break
            cursor += 1
        if cursor >= len(text):
            raise AssertionError(
                f"`<Route` 标签没有闭合的 `>`（第 {text.count(chr(10), 0, match.start()) + 1} 行起）"
                "—— 文件形态变了，判据拒绝猜。"
            )
        tokens.append(("open", text[match.start():cursor + 1],
                       text.count("\n", 0, match.start()) + 1))
        pos = cursor + 1
    return tokens


class AppRoutes:
    """一个 `App.tsx` 解析出来的结果。"""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}      # 归一化路径 → 出处（`文件:行`）
        self.redirects: dict[str, str] = {}  # 归一化路径 → 重定向目标
        self.skipped: list[str] = []         # 兜底路由 / 无 path 无 index 的标签

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"AppRoutes(pages={sorted(self.pages)}, redirects={self.redirects})"


def parse_app(text: str, *, label: str, source: str, basename: str = "") -> AppRoutes:
    """解析一个 `App.tsx`：还原嵌套、按 `basename` 拼前缀、区分页面与重定向。"""
    tokens = route_tokens(text)
    if not tokens:
        raise AssertionError(
            f"{label}（{source}）里解析不出任何 `<Route` 标签 —— 路由表形态变了"
            "（或文件拿错了），判据拒绝在「解析出空集」的情况下给结论。"
        )
    result = AppRoutes()
    stack: list[str] = []
    for kind, tag, line in tokens:
        if kind == "close":
            if stack:
                stack.pop()
            continue
        self_closing = tag.rstrip().endswith("/>")
        path_match = _PATH_ATTR_RE.search(tag)
        is_index = re.search(r"\bindex\b", tag) is not None
        parent = stack[-1] if stack else None

        if path_match is None:
            if not is_index:
                result.skipped.append(f"{source}:{line} 既没有 `path=` 也没有 `index`")
                continue
            if parent is None:
                result.skipped.append(f"{source}:{line} 是最外层 `index`，没有父路由")
                continue
            full = parent
        else:
            raw = path_match.group(1)
            if raw.startswith("/"):
                full = raw
            elif parent is not None:
                full = parent.rstrip("/") + "/" + raw.lstrip("/")
            else:
                full = "/" + raw.lstrip("/")

        if basename:
            full = (basename + full).rstrip("/") or "/"
        full = normalise_route(full)

        if path_match is not None and path_match.group(1) == "*":
            result.skipped.append(f"{source}:{line} 兜底路由 `*`（不是页面）")
            if not self_closing:
                stack.append(full)
            continue
        if _NAVIGATE_RE.search(tag):
            target = _TO_ATTR_RE.search(tag)
            result.redirects[full] = normalise_route(target.group(1)) if target else ""
        else:
            result.pages[full] = f"{label} {source}:{line}"

        if not self_closing:
            stack.append(full)
    return result


def code_routes(teacher_text: str, student_text: str) -> tuple[AppRoutes, AppRoutes]:
    teacher = parse_app(teacher_text, label="老师端", source="frontend/src/App.tsx")
    student = parse_app(
        student_text,
        label="学生端",
        source="frontend/src/student/App.tsx",
        basename=STUDENT_BASENAME,
    )
    return teacher, student


# --------------------------------------------------------------------------- 对账


def compare_routes(doc: dict[str, tuple[int, str]], code: dict[str, str]) -> list[str]:
    """纯比较：返回问题列表（空 = 一致）。不改任何东西，便于正/负对照直接调用。"""
    problems: list[str] = []
    for route in sorted(set(code) - set(doc)):
        problems.append(f"代码有、文档没有：`{route}`（{code[route]}）")
    for route in sorted(set(doc) - set(code)):
        line_no, side = doc[route]
        problems.append(f"文档有、代码没有：`{route}`（docs/04-接口契约.md:{line_no}，{side}）")
    return problems


def assert_reconciled(*, doc_text: str, teacher_text: str, student_text: str) -> dict:
    """判据主体：下限 → 解析 → 对账 → 重定向目标也在表里。失败时抛 AssertionError。"""
    doc = doc_routes(doc_text)
    teacher, student = code_routes(teacher_text, student_text)
    code = {**teacher.pages, **student.pages}

    floors = (
        ("文档侧表行", len(doc), MIN_DOC_ROWS),
        ("老师端页面路由", len(teacher.pages), MIN_TEACHER_ROUTES),
        ("学生端页面路由", len(student.pages), MIN_STUDENT_ROUTES),
        ("代码侧页面路由合计", len(code), MIN_CODE_ROUTES),
    )
    below = [f"{name} = {actual} < 下限 {floor}" for name, actual, floor in floors
             if actual < floor]
    if below:
        raise AssertionError(
            "`docs/04` §7 与两个 `App.tsx` 的对账在**下限**上就过不去（解析不出足够内容，"
            "拿空集对空集不算通过）：\n  - " + "\n  - ".join(below)
            + f"\n  文档解析到：{sorted(doc)}\n  代码解析到：{sorted(code)}"
        )

    problems = compare_routes(doc, code)
    for route, target in {**teacher.redirects, **student.redirects}.items():
        if not target:
            problems.append(f"重定向 `{route}` 没有字面量 `to=` 目标（判据不猜）")
        elif target not in doc:
            problems.append(
                f"重定向 `{route}` → `{target}`，但 §7 表里没有 `{target}`"
                "（被重定向指向的页面也是契约的一部分）"
            )
    if problems:
        raise AssertionError(
            "`docs/04` §7 前端路由表与两个 `App.tsx` 不一致 —— 改了 `App.tsx` 忘了改文档"
            "（或反之）：\n  - " + "\n  - ".join(problems)
        )
    return {"doc": doc, "code": code, "redirects": {**teacher.redirects, **student.redirects}}


# --------------------------------------------------------------------------- 夹具


def real_doc_text() -> str:
    return read_text(ROUTE_DOC, "`docs/04-接口契约.md`")


def real_teacher_text() -> str:
    return read_text(TEACHER_APP, "老师端 `frontend/src/App.tsx`")


def real_student_text() -> str:
    return read_text(STUDENT_APP, "学生端 `frontend/src/student/App.tsx`")


def _drop_doc_row(text: str, route: str) -> str:
    """删掉 §7 里某一条**表行**（用于「文档少一行」的负对照）。

    只动表行：路由串在表下的说明段落里也会出现（`docs/04-接口契约.md:402` 就引用了
    `/student/paper/:code`），把普通段落一起删掉就不是「文档少一行」了。
    """
    lines = text.splitlines(keepends=True)
    kept = [
        line for line in lines
        if not (_DOC_ROW_RE.match(line) and f"`{route}`" in line)
    ]
    assert len(kept) == len(lines) - 1, (
        f"负对照没删掉恰好一行：{route}（删了 {len(lines) - len(kept)} 行）"
    )
    return "".join(kept)


def _add_doc_row(text: str, route: str, page: str = "幽灵页") -> str:
    """在 §7 表尾追加一行（用于「文档多一行」的负对照）。"""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("| 学生 | `/student/result`"):
            lines.insert(index + 1, f"| 老师 | `{route}` | {page} |\n")
            return "".join(lines)
    raise AssertionError("负对照找不到插入点（§7 表尾）")


def _replace_doc_row(text: str, route: str, replacement: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if f"`{route}`" in line:
            lines[index] = replacement
            return "".join(lines)
    raise AssertionError(f"负对照找不到要替换的行：{route}")


def _synthetic_doc_from(paths: dict[str, str]) -> str:
    """由代码侧解析结果合成一份「一致」的 markdown（正向对照，防恒红）。"""
    rows = "".join(
        f"| 老师 | `{route}` | 合成 |\n" for route in sorted(paths)
    )
    return "## 7. 前端路由\n\n| 端 | 路由 | 页面 |\n| --- | --- | --- |\n" + rows


# --------------------------------------------------------------------------- 负对照（必须能红）


class TestNegativeControls(unittest.TestCase):
    """每一条都要求：制造失败条件后**必红且点名**。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = real_doc_text()
        cls.teacher = real_teacher_text()
        cls.student = real_student_text()

    def _assert_red(self, *, doc: str, teacher: str | None = None,
                    student: str | None = None) -> str:
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(
                doc_text=doc,
                teacher_text=self.teacher if teacher is None else teacher,
                student_text=self.student if student is None else student,
            )
        return str(ctx.exception)

    def test_01_document_missing_a_student_route_is_red_and_named(self):
        message = self._assert_red(doc=_drop_doc_row(self.doc, "/student/paper/:code"))
        self.assertIn("`/student/paper/:id`", message)
        self.assertIn("代码有、文档没有", message)

    def test_02_document_missing_a_teacher_route_is_red_and_named(self):
        message = self._assert_red(doc=_drop_doc_row(self.doc, "/teacher/attempts"))
        self.assertIn("`/teacher/attempts`", message)
        self.assertIn("代码有、文档没有", message)

    def test_03_document_with_an_extra_route_is_red_and_named(self):
        message = self._assert_red(doc=_add_doc_row(self.doc, "/teacher/ghost"))
        self.assertIn("`/teacher/ghost`", message)
        self.assertIn("文档有、代码没有", message)

    def test_04_document_with_a_renamed_param_is_still_green(self):
        """参数名不是契约（`:code` → `:paperCode` 不该红）——顺手钉住这个边界。"""
        same = _replace_doc_row(
            self.doc,
            "/student/paper/:code",
            "| 学生 | `/student/paper/:paperCode` | 输卷号 / 扫码进入的卷子作答页 |\n",
        )
        assert_reconciled(doc_text=same, teacher_text=self.teacher, student_text=self.student)

    def test_05_emptied_document_is_loud_not_green(self):
        for label, bad in (
            ("空文本", ""),
            ("只有标题", "## 7. 前端路由\n\n这一节没有表。\n"),
            ("表格换成围栏", "## 7. 前端路由\n\n```\n/teacher\n/student\n```\n"),
        ):
            with self.subTest(case=label):
                message = self._assert_red(doc=bad)
                self.assertIn("找不到任何", message)

    def test_06_truncated_document_trips_the_row_floor(self):
        """文档还剩几行、但整体缩水到下限以下 → 必须踩下限（不是「一致就绿」）。"""
        rows = [line for line in self.doc.splitlines(keepends=True)
                if _DOC_ROW_RE.match(line)]
        self.assertEqual(11, len(rows), "夹具变了：§7 表行数不再是 11")
        two = "## 7. 前端路由\n\n| 端 | 路由 | 页面 |\n| --- | --- | --- |\n" + "".join(rows[:2])
        message = self._assert_red(doc=two)
        self.assertIn("下限", message)
        self.assertIn("文档侧表行", message)
        self.assertIn("2 < 下限 8", message)

    def test_07_app_without_route_tags_is_loud_not_green(self):
        plain = "export const x = 1;\n"
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(doc_text=self.doc, teacher_text=plain, student_text=self.student)
        self.assertIn("解析不出任何 `<Route`", str(ctx.exception))

    def test_08_truncated_student_app_trips_the_route_floor(self):
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(
                doc_text=_drop_doc_row(self.doc, "/student/paper/:code"),
                teacher_text=self.teacher,
                student_text="<Routes><Route path=\"/\" element={<EntryPage />} /></Routes>\n",
            )
        message = str(ctx.exception)
        self.assertIn("学生端页面路由", message)
        self.assertIn("下限", message)

    def test_09_redirect_target_missing_from_the_table_is_red_and_named(self):
        broken = self.teacher.replace('to="/teacher"', 'to="/teacher/ghost"')
        self.assertNotEqual(broken, self.teacher, "负对照没改到重定向目标")
        message = self._assert_red(doc=self.doc, teacher=broken)
        self.assertIn("/teacher/ghost", message)
        self.assertIn("重定向", message)

    def test_10_unclosed_route_tag_is_loud(self):
        """标签没有闭合的 `>`（例如 `element={…}` 里的 JSX 之后忘了收尾）必须大声红。"""
        broken = '<Routes><Route path="/teacher" element={<div className="fatal-error">'
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(doc_text=self.doc, teacher_text=broken, student_text=self.student)
        self.assertIn("没有闭合的 `>`", str(ctx.exception))

    def test_11_missing_files_are_loud(self):
        with self.assertRaises(AssertionError) as ctx:
            read_text(Path(__file__).with_name("no-such-route-doc.md"), "`docs/04-接口契约.md`")
        message = str(ctx.exception)
        self.assertIn("读不到", message)
        self.assertIn("no-such-route-doc.md", message)
        # 对照：能读到时必须把内容真的读出来（不是恒抛）
        self.assertIn("REPO_ROOT", read_text(Path(__file__), "本判据自身"))


# --------------------------------------------------------------------------- 正对照（防恒红）+ 真门禁


class TestPositiveControls(unittest.TestCase):
    """防「恒红」：解析器真的读到了两个 `App.tsx`，一致的输入必须绿。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = real_doc_text()
        cls.teacher_text = real_teacher_text()
        cls.student_text = real_student_text()

    def test_01_parsers_really_read_both_real_app_files(self):
        teacher, student = code_routes(self.teacher_text, self.student_text)
        # 不是空集对空集：两侧各自越过下限，且内容可核对
        self.assertGreaterEqual(len(teacher.pages), MIN_TEACHER_ROUTES)
        self.assertGreaterEqual(len(student.pages), MIN_STUDENT_ROUTES)
        self.assertGreaterEqual(len(teacher.pages) + len(student.pages), MIN_CODE_ROUTES)
        # 解析器读的确实是真文件（锚点在源码文本里）
        self.assertIn('<Route path="/paper/:code"', self.student_text)
        self.assertIn('<Route path="attempts/:attemptId"', self.teacher_text)
        self.assertIn("/teacher/attempts/:id", teacher.pages)
        self.assertIn("/student/paper/:id", student.pages)
        self.assertIn("/teacher", teacher.pages)
        # 嵌套还原：相对子路由拼到了父路由下面
        self.assertIn("/teacher/paper/:id", teacher.pages)
        self.assertIn("/teacher/library", teacher.pages)
        # 兜底路由与重定向都没被当成页面
        self.assertNotIn("/*", teacher.pages)
        self.assertIn("/", teacher.redirects)
        self.assertEqual("/teacher", teacher.redirects["/"])

    def test_02_real_document_and_real_apps_pass_the_gate(self):
        result = assert_reconciled(
            doc_text=self.doc, teacher_text=self.teacher_text, student_text=self.student_text
        )
        self.assertEqual(11, len(result["doc"]))
        self.assertEqual(11, len(result["code"]))
        self.assertEqual(result["doc"].keys(), result["code"].keys())

    def test_03_synthetic_consistent_document_is_green(self):
        """同一份代码，合成一份「一致」的表 → compare_routes 必须返回空。"""
        teacher, student = code_routes(self.teacher_text, self.student_text)
        code = {**teacher.pages, **student.pages}
        synthetic = doc_routes(_synthetic_doc_from(code))
        self.assertEqual([], compare_routes(synthetic, code))

    def test_04_synthetic_document_with_the_same_content_is_green_on_the_gate(self):
        teacher, student = code_routes(self.teacher_text, self.student_text)
        code = {**teacher.pages, **student.pages}
        assert_reconciled(
            doc_text=_synthetic_doc_from(code),
            teacher_text=self.teacher_text,
            student_text=self.student_text,
        )


if __name__ == "__main__":
    unittest.main()
