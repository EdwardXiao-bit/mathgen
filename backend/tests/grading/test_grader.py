"""判分器门禁测试（表驱动），对应 docs/06-测试方案.md §5。

覆盖契约 §3 归一化规则的**每一个分支**：
空白、全角、``*``/``x``/``X`` → ``×``、``/`` → ``÷``、中文括号、
余数三种写法（``7 余 2`` / ``7…2`` / ``7r2``）、``Decimal`` 等值、
漏写余数判错、脱式题只判最终答案。

另含 doc / contract 测试：依赖方向（契约 §8 冻结）与纯函数纪律。
"""
import ast
import dataclasses
import os
import unittest
from decimal import Decimal

from app.engine.types import Question, Verdict
from app.grading import grade
from app.grading.grader import normalize_answer


def make_question(answer: str, expr: str = "1 + 1", steps=None) -> Question:
    """按契约 §2.2 构造一道题；测试只关心 answer 字段。"""
    return Question(
        expr=expr,
        display=expr + " = ____",
        answer=answer,
        steps=[] if steps is None else steps,
        difficulty=1,
        tags=["测试"],
        signature=expr.replace(" ", ""),
    )


# (标签, 标准答案, 学生输入, 期望 is_correct, 期望 normalized_input)
# 覆盖契约 §3 的每一条归一化规则；标签用于断言"分支被覆盖过"。
#
# 注意：`answer` 按契约 §2.2 是**数值型标准答案**（如 "42" / "7余2" / "5"），
# 算式只出现在学生的原始输入里（或 `expr` 字段里）。06-测试方案 §5 的用例表
# 也遵循这一约定：("(12+8)÷4", "5", True) → answer="5"、raw="(12+8)÷4"。
# 判分**不做算式求值**，两栏一律按数值比较（契约 §3「不判过程，只比数值」）。
CASES = [
    # --- 基线 ---
    ("baseline_exact", "42", "42", True, "42"),
    ("baseline_wrong", "42", "43", False, "43"),

    # --- 规则 1：去除首尾与中间空白 ---
    ("rule1_space_ends", "42", " 42 ", True, "42"),
    ("rule1_space_middle", "42", " 4   2 ", True, "42"),
    ("rule1_space_fullwidth", "42", "\u300042\u3000", True, "42"),
    ("rule1_space_in_remainder", "7余2", "7 余   2", True, "7…2"),

    # --- 规则 2：全角数字 / 运算符 → 半角 ---
    ("rule2_fullwidth_digits", "42", "４２", True, "42"),
    ("rule2_fullwidth_digits_mixed", "42", "4２", True, "42"),
    ("rule2_fullwidth_operator", "18", "１８", True, "18"),
    ("rule2_fullwidth_minus", "7", "１０－３", False, "10-3"),

    # --- 规则 3：× 的两种写法（* / x / X → ×） ---
    ("rule3_mul_star", "42", "6*7", False, "6×7"),
    ("rule3_mul_lower_x", "42", "6x7", False, "6×7"),
    ("rule3_mul_upper_x", "42", "6X7", False, "6×7"),
    ("rule3_mul_plain_symbol", "42", "6×7", False, "6×7"),
    ("rule3_mul_fullwidth_star", "42", "６＊７", False, "6×7"),

    # --- 规则 3：÷ 的两种写法（/ → ÷） ---
    ("rule3_div_slash", "5", "20/4", False, "20÷4"),
    ("rule3_div_plain_symbol", "5", "20÷4", False, "20÷4"),
    ("rule3_div_fullwidth_slash", "5", "２０／４", False, "20÷4"),

    # --- 规则 3：中文括号 → 半角 ---
    ("rule3_paren_chinese", "5", "（12+8）÷4", False, "(12+8)÷4"),
    ("rule3_paren_ascii", "5", "(12+8)÷4", False, "(12+8)÷4"),

    # --- 规则 4：余数三种写法 ---
    ("rule4_remainder_hanzi", "7余2", "7 余 2", True, "7…2"),
    ("rule4_remainder_ellipsis", "7余2", "7…2", True, "7…2"),
    ("rule4_remainder_r", "7余2", "7r2", True, "7…2"),
    ("rule4_remainder_r_upper", "7余2", "7R2", True, "7…2"),
    ("rule4_remainder_missing", "7余2", "7", False, "7"),
    ("rule4_remainder_answer_ellipsis", "7…2", "7 余 2", True, "7…2"),

    # --- 规则 5：Decimal 等值（禁止浮点直接比较） ---
    ("rule5_decimal_equal", "18", "18.0", True, "18.0"),
    ("rule5_decimal_trailing_zeros", "18", "18.000", True, "18.000"),
    ("rule5_decimal_more_places", "1.5", "1.50", True, "1.50"),
    ("rule5_decimal_not_equal", "18", "18.1", False, "18.1"),
    ("rule5_decimal_remainder", "7余2", "7.0余2.0", True, "7.0…2.0"),

    # --- 脱式题只判最终答案（契约 §3「已确认不做」，06-测试方案 §5） ---
    ("stepwise_value_matches", "5", "5", True, "5"),
    ("stepwise_no_process_grading", "5", "(12+8)÷4", False, "(12+8)÷4"),

    # --- 不可判定输入：判错，且 normalized_input 仍可展示 ---
    ("blank_input", "42", "", False, ""),
    ("blank_whitespace_input", "42", "   ", False, ""),
    ("garbage_input", "42", "不知道", False, "不知道"),
]

# 契约 §3 要求必须覆盖的分支名（06-测试方案 §5「必须覆盖的归一化分支」）
REQUIRED_BRANCHES = {
    "baseline",
    "rule1",   # 空白
    "rule2",   # 全角
    "rule3",   # ×÷ 两种写法 + 中文括号
    "rule4",   # 余数三写法
    "rule5",   # Decimal 等值
    "stepwise",
}


class TestGradeTable(unittest.TestCase):
    """表驱动：一行一个契约分支。"""

    def test_table_covers_every_required_branch(self):
        """门禁：契约 §3 列出的分支必须都有用例，防止悄悄漏测。"""
        covered = {label.split("_", 1)[0] for label, _, _, _, _ in CASES}
        missing = REQUIRED_BRANCHES - covered
        self.assertEqual(missing, set(), f"缺少分支覆盖: {sorted(missing)}")

    def test_table_length(self):
        """验收标准要求用例数 ≥ 12。"""
        self.assertGreaterEqual(len(CASES), 12)

    def test_grade_table(self):
        for label, answer, raw, expected_correct, expected_norm in CASES:
            with self.subTest(case=label):
                verdict = grade(make_question(answer), raw)
                self.assertIsInstance(verdict, Verdict)
                self.assertIs(
                    verdict.is_correct, expected_correct,
                    f"{label}: answer={answer!r} raw={raw!r}",
                )
                self.assertEqual(
                    verdict.normalized_input, expected_norm,
                    f"{label}: 归一化结果不符",
                )


class TestSharedBranches(unittest.TestCase):
    """契约里被点名、但值得单独钉死的分支。"""

    def test_remainder_three_notations_are_equivalent(self):
        """规则 4：三种余数写法归一后必须完全一致。"""
        notated = ["7 余 2", "7…2", "7r2", "7 R 2"]
        normalized = [normalize_answer(x) for x in notated]
        self.assertEqual(len(set(normalized)), 1, normalized)
        for raw in notated:
            self.assertTrue(grade(make_question("7余2"), raw).is_correct, raw)

    def test_missing_remainder_is_wrong(self):
        """漏写余数必须判错：7 与 7余2 不等值。"""
        self.assertFalse(grade(make_question("7余2"), "7").is_correct)
        self.assertFalse(grade(make_question("7余2"), "7r0").is_correct)

    def test_decimal_equality_is_exact_not_float(self):
        """规则 5：必须走 Decimal 精确比较，不能浮点近似。

        `0.1 + 0.2 != 0.3` 是浮点陷阱；Decimal 下 `0.3` 与 `0.30` 精确相等。
        """
        self.assertTrue(grade(make_question("0.3"), "0.30").is_correct)
        self.assertTrue(grade(make_question("0.1"), "0.1").is_correct)
        # 若实现里误用 float，0.30000000000000004 会不等于 0.3 之外的写法；
        # 这里断言"数值上不同则必判错"，反向确认没有容差比较。
        self.assertFalse(grade(make_question("0.3"), "0.3000001").is_correct)

    def test_stepwise_only_final_answer(self):
        """脱式题只判最终答案：答案 5、输入 (12+8)÷4 应判错。"""
        question = make_question("5", expr="(12 + 8) ÷ 4",
                                 steps=["12+8=20", "20÷4=5"])
        self.assertTrue(grade(question, "5").is_correct)
        self.assertFalse(grade(question, "(12+8)÷4").is_correct)

    def test_stepwise_process_not_graded(self):
        """过程（steps / expr）不参与判分：同一最终答案必得同一结论。"""
        with_steps = make_question("5", expr="(12 + 8) ÷ 4",
                                   steps=["12+8=20", "20÷4=5"])
        other_steps = make_question("5", expr="(12 + 8) ÷ 4",
                                    steps=["先算括号", "再除以 4"])
        self.assertEqual(
            grade(with_steps, "5").is_correct,
            grade(other_steps, "5").is_correct,
        )
        self.assertTrue(grade(other_steps, "5").is_correct)

    def test_expression_is_not_evaluated(self):
        """本版不做事式求值：算式一律不可判定为数值 → 判错。"""
        self.assertFalse(grade(make_question("5"), "12-7").is_correct)
        self.assertFalse(grade(make_question("20"), "12+8").is_correct)

    def test_expression_valued_answer_falls_back_to_wrong(self):
        """契约 §3 用例 ("(12+8)÷4", "5", False)：answer 是算式时判错。

        本轮实现**不对 answer 做算式求值**，因此两栏顺序相反的同一用例
        都判错，与 06-测试方案 §5 的用例表逐行一致（见本文件 CASES 最后两行）。
        """
        self.assertFalse(grade(make_question("(12+8)÷4"), "5").is_correct)
        self.assertFalse(grade(make_question("(12+8)÷4"), "(12+8)÷4").is_correct)

    def test_non_finite_input_is_not_a_number(self):
        """健壮性：Decimal 能解析 NaN / Infinity，但它们不是合法答案。"""
        for raw in ("NaN", "nan", "Infinity", "-Infinity", "sNaN"):
            with self.subTest(raw=raw):
                self.assertFalse(grade(make_question("42"), raw).is_correct)

    def test_normalize_answer_tolerates_none(self):
        """健壮性：空值（如前端未填）归一化为空串，绝不抛异常。"""
        self.assertEqual(normalize_answer(None), "")
        self.assertEqual(normalize_answer(""), "")
        self.assertFalse(grade(make_question("42"), None).is_correct)


class TestNormalizeAnswer(unittest.TestCase):
    """归一化函数本身的顺序与幂等性。"""

    def test_order_whitespace_before_fullwidth(self):
        """第 1 步清半角空白，第 2 步清全角空白后仍不得残留空白。"""
        self.assertEqual(normalize_answer(" 7 \u3000 余 \u3000 2 "), "7…2")

    def test_fullwidth_operator_then_symbol(self):
        """＊ 先变半角 * ，再按第 3 步变 ×（证明顺序生效）。"""
        self.assertEqual(normalize_answer("６＊７"), "6×7")
        self.assertEqual(normalize_answer("２４／６"), "24÷6")

    def test_chinese_parentheses(self):
        self.assertEqual(normalize_answer("（12+8）"), "(12+8)")

    def test_multiplication_letter_variants(self):
        for raw in ("6*7", "6x7", "6X7", "6×7"):
            self.assertEqual(normalize_answer(raw), "6×7", raw)

    def test_idempotent(self):
        """归一化幂等：再归一化一次不改变结果。"""
        for raw in (" ６＊７ ", "7 余 2", "１２ ＋ ８", "(12+8)÷4"):
            once = normalize_answer(raw)
            self.assertEqual(normalize_answer(once), once, raw)

    def test_quote_it_is_a_text_level_operation(self):
        """规则 4 是文本级替换：非法余数写法不得被误判为合法等值。"""
        # "…" 只出现一次才可解析，多个分隔符不可判定 → 判错
        self.assertFalse(grade(make_question("7余2"), "7…2…1").is_correct)
        # 余数分隔符两侧必须是合法数字
        self.assertFalse(grade(make_question("7余2"), "余2").is_correct)
        self.assertFalse(grade(make_question("7余2"), "7余").is_correct)


class TestPurityAndDiscipline(unittest.TestCase):
    """纯函数纪律 + 依赖方向门禁（契约 §8，冻结）。"""

    def test_public_api_surface(self):
        """公开入口必须包含冻结签名 grade，且不夹带其它业务符号。"""
        import app.grading as grading_pkg
        self.assertIn("grade", grading_pkg.__all__)
        self.assertEqual(sorted(grading_pkg.__all__), ["grade", "normalize_answer"])
        self.assertTrue(callable(grading_pkg.grade))

    def test_grade_is_deterministic(self):
        """同输入同输出（纯函数）。"""
        question = make_question("7余2")
        first = grade(question, " 7 r 2 ")
        second = grade(question, " 7 r 2 ")
        self.assertEqual(first, second)
        self.assertEqual(dataclasses.astuple(first), dataclasses.astuple(second))

    def test_grade_does_not_mutate_question(self):
        """判分不得修改传入的题目（值对象纪律）。"""
        question = make_question("42")
        before = dataclasses.astuple(question)
        grade(question, " ４２ ")
        self.assertEqual(dataclasses.astuple(question), before)

    def test_verdict_is_frozen(self):
        verdict = grade(make_question("42"), "42")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            verdict.is_correct = False  # type: ignore[misc]

    def _grading_source_files(self):
        package_dir = os.path.dirname(
            os.path.abspath(__import__("app.grading", fromlist=["x"]).__file__)
        )
        paths = []
        for name in sorted(os.listdir(package_dir)):
            if name.endswith(".py"):
                paths.append(os.path.join(package_dir, name))
        return paths

    def test_package_has_source_files(self):
        """守卫本测试自身：确实扫到了源码文件，而不是扫了个空目录。"""
        paths = self._grading_source_files()
        self.assertGreaterEqual(len(paths), 1, paths)
        self.assertTrue(any(p.endswith("grader.py") for p in paths), paths)

    def test_no_forbidden_imports_or_io_in_grading_package(self):
        """契约 §8 冻结：grading 包内不得出现数据库 / Web / I/O。

        用 AST 检查而非字符串搜索，避免把注释与文档字符串误判为依赖。
        同时检查 `open(` / `pathlib` / `import os` 这类 I/O 通道。
        """
        forbidden_roots = {"fastapi", "sqlalchemy", "requests", "pathlib",
                           "starlette", "pydantic", "httpx", "urllib",
                           "socket", "subprocess", "sqlite3", "io"}
        violations = []
        for path in self._grading_source_files():
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            tree = ast.parse(source, filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root in forbidden_roots:
                            violations.append(f"{path}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root = node.module.split(".")[0]
                        if root in forbidden_roots:
                            violations.append(f"{path}: from {node.module}")
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "open":
                        violations.append(f"{path}: open() 调用")
        self.assertEqual(violations, [], f"违反契约 §8: {violations}")

    def test_only_decimal_types_used_for_comparison(self):
        """规则 5：源码里不得出现 float 直接比较的取整/近似函数。"""
        forbidden_calls = {"round", "float"}
        violations = []
        for path in self._grading_source_files():
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in forbidden_calls):
                    violations.append(f"{path}: {node.func.id}()")
        self.assertEqual(violations, [], f"浮点比较风险: {violations}")


if __name__ == "__main__":
    unittest.main()
