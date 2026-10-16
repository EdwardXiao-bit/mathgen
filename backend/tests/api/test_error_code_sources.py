"""`docs/04` §9 ↔ 测试侧副本 ↔ 实现映射：**三方**对账门禁（task-30 / T-M4-10）。

**为什么存在**：task-28 给错误码加了双向判据，但它的两个输入
（`CONTRACT_ERROR_CODES` 硬编码副本、`ERROR_HTTP_STATUS` 映射表）**都在机器侧**——
`docs/04-接口契约.md` §9 那 14 行（契约，权威）**不在任何判据的输入里**。
于是出现一个没有任何东西在守的缝隙：**只改文档不会让任何测试变红**。
be-paper 是靠一个临时脚本才确认「文档 14 = 副本 14 = 实现 14」的，脚本一删，
证据就蒸发了（D69 / V9.1：**证据随临时目录蒸发**）。本文件把那次一次性对账
固化成常驻判据，并**直接解析 `.md`**（先例：`backend/tests/contract/test_write_domains.py`）。

**本文件与 task-28 那条判据的分工**（两件事不要混为一谈）：
- `test_errors_and_sheet.assert_error_codes_agree`：副本 ↔ 实现（机器侧双向），继续由它守；
- 本文件：文档 ↔ 副本、文档 ↔ 实现（**新**），并复用上面那条做副本 ↔ 实现。

**为什么下界是非零常量**（V9/V11，判据不能恒真）：三方对账的输入之一是**解析器的产物**。
若 §9 的表被换一种写法（或文件被移动、被清空），解析器可能只抓到 0 行；此时
「文档空集 vs 实现非空」虽然仍会红，但报错会指向**契约漂移**——**方向是错的**，
下一个人会去查谁删了码，而不是查解析器坏了。`MIN_DOC_ROWS` 把这种情况纠正为
「解析器看不见表了」，并让「表格格式一变就静默变绿」这条退化路径不复存在。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app.services.errors import ERROR_HTTP_STATUS
from tests.api.test_errors_and_sheet import (
    CONTRACT_ERROR_CODES,
    assert_error_codes_agree,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DOC = REPO_ROOT / "docs" / "04-接口契约.md"

SECTION_TITLE = "## 9. 错误码"
NEXT_SECTION_RE = re.compile(r"^## \d", re.MULTILINE)

#: 解析器**防失明**的非零下界（V9/V11）：低于它就说明解析器看不见那张表了
#: （表格被改写、文件被移动、章节被删），此时**不许**把「空集对空集」放过去。
#:
#: 为什么它**故意低于**当前真实行数（14）：下界是**失明**探测器，不是行数锁。
#: 真删掉 §9 一行时，正确的报错应当是三方对账**点名那个码**（「[测试副本] 有、
#: [文档 §9] 没有：['PAPER_HAS_ATTEMPTS']」），而不是一句「解析出 13 行、低于 14」——
#: 后者会把「契约少了码」误报成「解析器坏了」，方向就是错的（V13：先核对判据输入）。
#: 行数本身由 `EXPECTED_DOC_ROWS` 在正例里锁死（契约加码要**刻意**上调它，同改哈希）。
MIN_DOC_ROWS = 10

#: 当前 §9 的真实行数（M4：D77 补 `EXPORT_NOT_READY`、D78 补 `PAPER_HAS_ATTEMPTS` → 14；
#: M5/D80 补 `ATTEMPT_NOT_FOUND` → 15）。
EXPECTED_DOC_ROWS = 16

_ROW_RE = re.compile(
    r"^\|\s*`(?P<code>[A-Z][A-Z0-9_]*)`\s*\|\s*(?P<status>\d{3})\s*\|(?P<desc>.*)\|\s*$"
)
_TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
_SEPARATOR_RE = re.compile(r"^\|[\s|:-]+\|\s*$")


def parse_error_code_table(text: str) -> dict[str, int]:
    """从契约文档全文里解析 §9 的错误码表 → `{code: http_status}`。

    解析失败**一律大声红**（不返回部分结果、不静默跳过）：
    - 找不到 `## 9. 错误码` 标题；
    - 表头行不是「码 / HTTP / 含义」；
    - 表里出现无法解析的数据行（形态变了不许静默变绿）；
    - 同一个码重复出现；
    - 行数低于 `MIN_DOC_ROWS`。
    """
    if SECTION_TITLE not in text:
        raise AssertionError(f"契约文档里找不到章节标题 {SECTION_TITLE!r}——解析器失明，不许放行")

    section = text.split(SECTION_TITLE, 1)[1]
    match = NEXT_SECTION_RE.search(section)
    if match:
        section = section[: match.start()]

    table: dict[str, int] = {}
    seen_header = False
    for raw in section.splitlines():
        line = raw.strip()
        if not _TABLE_ROW_RE.match(line) or _SEPARATOR_RE.match(line):
            continue
        if _ROW_RE.match(line):
            code = _ROW_RE.match(line).group("code")
            status = int(_ROW_RE.match(line).group("status"))
            if code in table:
                raise AssertionError(f"§9 表里 {code} 出现了两次——表格形态异常，判据拒绝解读")
            table[code] = status
            continue
        if not seen_header:
            if "码" not in line or "HTTP" not in line:
                raise AssertionError(f"§9 表头行形态不认识：{line!r}（解析器失明，不许放行）")
            seen_header = True
            continue
        raise AssertionError(
            f"§9 表里有无法解析的数据行：{line!r}——表格写法变了，"
            f"请同步本文件的 `_ROW_RE`，不要让它静默变绿"
        )

    if len(table) < MIN_DOC_ROWS:
        raise AssertionError(
            f"只从 §9 解析出 {len(table)} 行（下限 {MIN_DOC_ROWS}）——"
            f"大概率是表格被改写/文件被移动，而不是契约真的删了码：不要放宽下界，"
            f"先确认解析器还看得见那张表"
        )
    return table


def diff_lines(left_name: str, left: dict[str, int],
               right_name: str, right: dict[str, int]) -> list[str]:
    """可读的差异清单（V13：报错要直接点名，而不是只抛一个集合不相等）。"""
    problems: list[str] = []
    only_left = sorted(set(left) - set(right))
    if only_left:
        problems.append(f"[{left_name}] 有、[{right_name}] 没有：{only_left}")
    only_right = sorted(set(right) - set(left))
    if only_right:
        problems.append(f"[{right_name}] 有、[{left_name}] 没有：{only_right}")
    drift = sorted(
        f"{code}（{left_name}={left[code]}，{right_name}={right[code]}）"
        for code in set(left) & set(right)
        if left[code] != right[code]
    )
    if drift:
        problems.append(f"[{left_name}] 与 [{right_name}] 的状态码不一致：{drift}")
    return problems


def assert_three_sources_agree(tc, *, doc: dict[str, int], copy: dict[str, int],
                               impl: dict[str, int]) -> None:
    """文档 §9 ↔ 测试副本 ↔ 实现映射，三方逐项相等（码集合与状态码都比）。

    副本 ↔ 实现复用 task-28 的 `assert_error_codes_agree`（不在这里重写一份，
    否则又多一份要同步的判据）；文档参与的两条边在这里比，并输出点名差异。
    """
    doc_vs_copy = diff_lines("文档 §9", doc, "测试副本", copy)
    tc.assertEqual(doc_vs_copy, [], "文档 §9 与测试副本对不上：" + "；".join(doc_vs_copy))

    doc_vs_impl = diff_lines("文档 §9", doc, "实现映射表", impl)
    tc.assertEqual(doc_vs_impl, [], "文档 §9 与实现映射表对不上：" + "；".join(doc_vs_impl))

    assert_error_codes_agree(tc, impl, copy)


class DocumentedErrorCodesAgreeTests(unittest.TestCase):
    """真判据：跑在**仓库里那份真实文档**上。"""

    @classmethod
    def setUpClass(cls):
        cls.text = CONTRACT_DOC.read_text(encoding="utf-8")
        cls.doc = parse_error_code_table(cls.text)

    def test_the_document_is_actually_being_parsed(self):
        """防退化（V11）：先证明判据**真的读到了表**，否则后面的相等都是空过。

        这里的 `EXPECTED_DOC_ROWS` 是行数锁（15）：它是**刻意**常量，契约加/减码必须
        同步改它——改它的动作本身就是「我在改契约」的显式声明（同改契约哈希）。
        """
        self.assertEqual(len(self.doc), EXPECTED_DOC_ROWS)
        self.assertGreaterEqual(len(self.doc), MIN_DOC_ROWS)
        for code in ("CONFIG_INVALID", "PAPER_NOT_FOUND", "RENDER_NOT_READY",
                     "EXPORT_NOT_READY", "PAPER_HAS_ATTEMPTS", "ATTEMPT_NOT_FOUND",
                     "VALIDATION_ERROR"):
            self.assertIn(code, self.doc, f"{code} 应该出现在 §9 表里")

    def test_document_copy_and_implementation_agree(self):
        """三方逐项相等：文档 §9 是权威，副本与实现都必须跟得上它，反之亦然。"""
        assert_three_sources_agree(
            self, doc=self.doc, copy=CONTRACT_ERROR_CODES, impl=ERROR_HTTP_STATUS
        )


class ErrorCodeSourceNegativeControls(unittest.TestCase):
    """V9.1/V9.2：这些用例**留在仓库里**，任何人都能重跑并看到判据的辨别力。

    每条都拿一份「坏掉的 §9 文档」喂给**与真判据同一个解析器 + 同一个对账函数**，
    断言它必须红、且**点名**出问题的码；不是「我删了一条坏断言」。
    """

    @classmethod
    def setUpClass(cls):
        cls.text = CONTRACT_DOC.read_text(encoding="utf-8")

    def _row(self, code: str) -> str:
        for line in self.text.splitlines():
            if line.strip().startswith(f"| `{code}`"):
                return line
        raise AssertionError(f"测试自身出错：文档里找不到 {code} 那一行")

    def _expect_red(self, text: str, *, names: str | None = None) -> str:
        doc = parse_error_code_table(text)
        with self.assertRaises(AssertionError) as ctx:
            assert_three_sources_agree(
                self, doc=doc, copy=CONTRACT_ERROR_CODES, impl=ERROR_HTTP_STATUS
            )
        message = str(ctx.exception)
        if names is not None:
            self.assertIn(names, message, f"报错必须点名 {names}，实际是：{message}")
        return message

    def test_red_when_the_document_is_missing_a_code(self):
        """文档删一行（这里是 `PAPER_HAS_ATTEMPTS`）→ 红，并点名该码。"""
        line = self._row("PAPER_HAS_ATTEMPTS")
        message = self._expect_red(self.text.replace(line + "\n", ""),
                                  names="PAPER_HAS_ATTEMPTS")
        self.assertIn("[测试副本] 有、[文档 §9] 没有", message)

    def test_red_when_the_document_invents_a_code(self):
        """文档凭空多一行 → 红，并点名那个码（「多出来的一头没人看」不再成立）。"""
        line = self._row("VALIDATION_ERROR")
        message = self._expect_red(self.text.replace(line, line + "\n| `MADE_UP_CODE` | 418 | 合成的假码 |"),
                                  names="MADE_UP_CODE")
        self.assertIn("[文档 §9] 有、[测试副本] 没有", message)

    def test_red_when_a_documented_status_disagrees(self):
        """码都在、文档写的状态码不对（`EXPORT_NOT_READY` 写成 500）→ 红并给出两侧数值。"""
        line = self._row("EXPORT_NOT_READY")
        broken = line.replace("| 503 |", "| 500 |", 1)
        self.assertNotEqual(broken, line, "测试自身出错：没能改掉状态码")
        message = self._expect_red(self.text.replace(line, broken),
                                  names="EXPORT_NOT_READY")
        self.assertIn("文档 §9=500", message)
        self.assertIn("测试副本=503", message)

    def test_red_when_the_parser_can_no_longer_see_the_table(self):
        """整张表被换成散文 → 解析出 0 行 → 必须红在**解析器**这一层（下界），不红成契约漂移。

        为什么必须红在这一层：0 行时三方对账也会红（实现里有、文档里没有），但报错会指向
        「契约少了码」——方向是错的，下一个人会去查是谁删了码，而真凶是解析器失明。
        下界把这条诊断纠正过来。
        """
        prose = re.sub(r"^\| `[A-Z][A-Z0-9_]*` \|[^\n]*$", "错误码见 `backend/app/services/errors.py`。",
                       self.text, flags=re.MULTILINE)
        self.assertNotIn("| `CONFIG_INVALID` |", prose, "测试自身出错：表格没被换掉")
        with self.assertRaises(AssertionError) as ctx:
            parse_error_code_table(prose)
        message = str(ctx.exception)
        self.assertIn("下限", message)
        self.assertIn("解析", message)

    def test_red_when_a_single_row_becomes_unreadable_prose(self):
        """只有一行被改写成散文（其余 13 行还在）→ 下界拦不住，必须由**三方对账点名**。"""
        line = self._row("CONFIG_INVALID")
        prose = self.text.replace(line, "错误码见 `backend/app/services/errors.py`。")
        message = self._expect_red(prose, names="CONFIG_INVALID")
        self.assertIn("[测试副本] 有、[文档 §9] 没有", message)

    def test_red_when_the_table_shape_changes_to_an_unparsable_row(self):
        """表格形态变了（码不再加反引号）→ 解析器大声红，并把那一行原样贴出来。"""
        line = self._row("CODE_NOT_FOUND")
        broken = line.replace("`CODE_NOT_FOUND`", "CODE_NOT_FOUND")
        with self.assertRaises(AssertionError) as ctx:
            parse_error_code_table(self.text.replace(line, broken))
        message = str(ctx.exception)
        self.assertIn("无法解析的数据行", message)
        self.assertIn("CODE_NOT_FOUND", message)
