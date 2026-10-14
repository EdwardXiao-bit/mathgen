"""套件自检：证明「测量仪器」本身是对的 —— **不需要实现就位**。

一个只会全红的验收套件没有价值。本文件用冻结契约的**手工 Question 对象**
验证 harness 的三件仪器真的能用：

  1. 独立求值器 / 交换律归一 / 扰动器（接受合法、拒绝非法）
  2. 约束检查器 `question_violations` / `paper_violations`
  3. 配置生成器 `random_configs` / `representative_configs` 只产出**合法** config

因此即便 `generate` / `grade` 尚未实现，本文件也应全绿；它是「套件本身正确」
这一结论的直接证据。
"""
import unittest
from types import SimpleNamespace

if __package__:
    from . import harness as H
    from .harness import (  # noqa: F401
        ExprInfo, ExprParseError, perturb_answer, answer_number, last_int_tokens,
        question_violations, paper_violations, paper_violations_all,
        commutative_duplicates, call_generate, call_grade, step_value_violations,
        random_configs, representative_configs, load_types, describe)
else:  # 允许直接执行本文件
    import harness as H
    from harness import (  # noqa: F401
        ExprInfo, ExprParseError, perturb_answer, answer_number, last_int_tokens,
        question_violations, paper_violations, paper_violations_all,
        commutative_duplicates, call_generate, call_grade, step_value_violations,
        random_configs, representative_configs, load_types, describe)


def _q(expr, answer, display=None, steps=(), difficulty=1,
       tags=("口算",), signature=None):
    T = load_types()
    return T.Question(
        expr=expr, display=display if display is not None else expr, answer=answer,
        steps=list(steps), difficulty=difficulty, tags=list(tags),
        signature=signature if signature is not None else expr)


def _cfg(**kw):
    T = load_types()
    base = dict(ops={T.Op.ADD}, ranges=[T.RangeId.R1],
                question_type=T.QuestionType.ORAL, count=1)
    base.update(kw)
    return T.GenerateConfig(**base)


def config_rule_violations(config):
    """测试侧独立复核「config 本身是否合法」（契约 §2.1 五条规则）。"""
    T = load_types()
    outs = []
    if not config.ops:
        outs.append("ops 非空（§2.1）")
    if not config.ranges:
        outs.append("ranges 非空（§2.1）")
    if config.exact_division and config.allow_remainder:
        outs.append("exact_division 与 allow_remainder 互斥（§2.1）")
    if T.RangeId.R7 in config.ranges:
        if config.max_value is None or config.max_value < 10:
            outs.append("含 R7 时 max_value 必填且 ≥10（§2.1）")
    if (config.question_type == T.QuestionType.STEPWISE
            and len(config.ops) < 2 and not config.parentheses):
        outs.append("STEPWISE 要求至少两种运算或 parentheses=True（§2.1）")
    if config.question_type == T.QuestionType.FILL and config.parentheses:
        outs.append("FILL 不允许 parentheses=True（§2.1）")
    if not 1 <= config.count <= 100:
        outs.append("count 必须 1..100（§2.1）")
    return outs


class TestInstrumentEvaluator(unittest.TestCase):
    """求值器 / 交换律归一 / 扰动器。"""

    def test_evaluates_contract_examples(self):
        cases = [("37 + 45", 82), ("(12 + 8) ÷ 4", 5), ("9 × 8", 72),
                 ("100 - 37", 63), ("81 ÷ 9", 9)]
        for expr, expected in cases:
            with self.subTest(expr=expr):
                self.assertEqual(ExprInfo(expr).value, expected)

    def test_records_division_remainder(self):
        info = ExprInfo("7 ÷ 2")
        self.assertEqual(info.value, 3)
        self.assertEqual(info.divisions, [(7, 2, 3, 1)])
        self.assertTrue(info.has_remainder)
        self.assertFalse(ExprInfo("8 ÷ 2").has_remainder)

    def test_detects_parentheses(self):
        self.assertTrue(ExprInfo("(12 + 8) ÷ 4").has_parentheses)
        self.assertFalse(ExprInfo("12 + 8").has_parentheses)

    def test_rejects_unparseable_expr(self):
        for bad in ("7 + □", "3 & 4", "3 +", "(3 + 4", "", "abc"):
            with self.subTest(expr=bad):
                with self.assertRaises(ExprParseError):
                    ExprInfo(bad)

    def test_commutative_key_normalizes_order(self):
        self.assertEqual(ExprInfo("3 + 5").commutative_key(),
                         ExprInfo("5 + 3").commutative_key())
        self.assertEqual(ExprInfo("2 × 6").commutative_key(),
                         ExprInfo("6 × 2").commutative_key())
        self.assertNotEqual(ExprInfo("3 + 4").commutative_key(),
                            ExprInfo("3 + 5").commutative_key())
        # 减法**不**满足交换律
        self.assertNotEqual(ExprInfo("9 - 4").commutative_key(),
                            ExprInfo("4 - 9").commutative_key())

    def test_perturb_answer_changes_value(self):
        self.assertEqual(perturb_answer("42"), "43")
        self.assertEqual(perturb_answer("7余2"), "8余2")
        self.assertEqual(answer_number(perturb_answer("7余2")), 8)
        self.assertNotEqual(answer_number("42"), answer_number(perturb_answer("42")))
        self.assertNotEqual(answer_number("18.0"), answer_number(perturb_answer("18.0")))

    def test_last_int_tokens(self):
        self.assertEqual(last_int_tokens("= 20 ÷ 4"), [20, 4])
        self.assertEqual(last_int_tokens("= 5"), [5])


class TestInstrumentConstraintChecker(unittest.TestCase):
    """约束检查器必须**接受合法题、拒绝违规题**。"""

    def test_accepts_valid_r1_oral(self):
        config = _cfg(ops={load_types().Op.ADD, load_types().Op.SUB})
        question = _q("9 + 8", "17", difficulty=2, tags=["20以内加法", "进位"])
        self.assertEqual(question_violations(load_types(), config, question, 0), [])

    def test_accepts_valid_r3_and_stepwise_and_fill(self):
        T = load_types()
        r3 = _cfg(ops={T.Op.MUL}, ranges=[T.RangeId.R3])
        self.assertEqual(question_violations(T, r3, _q("9 × 8", "72"), 0), [])

        step = _cfg(ops={T.Op.ADD, T.Op.DIV}, ranges=[T.RangeId.R2],
                    question_type=T.QuestionType.STEPWISE, parentheses=True)
        self.assertEqual(question_violations(
            T, step, _q("(12 + 8) ÷ 4", "5", steps=["= 20 ÷ 4", "= 5"]), 0), [])

        fill = _cfg(question_type=T.QuestionType.FILL)
        self.assertEqual(question_violations(
            T, fill, _q("7 + 8", "8", display="7 + □ = 15"), 0), [])

    def test_rejects_out_of_range(self):
        T = load_types()
        config = _cfg(ops={T.Op.ADD})
        self.assertTrue(question_violations(T, config, _q("15 + 9", "24"), 0),
                        "R1（20 以内）不应接受 15 + 9")

    def test_rejects_wrong_operator(self):
        T = load_types()
        config = _cfg(ops={T.Op.ADD})
        self.assertTrue(question_violations(T, config, _q("9 × 8", "72"), 0),
                        "config 只选加法，乘法题应被拒")

    def test_rejects_missing_carry(self):
        T = load_types()
        config = _cfg(ops={T.Op.ADD}, require_carry=True)
        # 12 + 3 = 15：个位 2+3=5，不进位 → 应违规
        self.assertTrue(question_violations(T, config, _q("12 + 3", "15"), 0))
        # 9 + 8 = 17：个位 9+8=17 ≥10，进位 → 合法
        self.assertEqual(question_violations(T, config, _q("9 + 8", "17"), 0), [])

    def test_rejects_missing_borrow_and_non_exact_division(self):
        T = load_types()
        borrow = _cfg(ops={T.Op.SUB}, ranges=[T.RangeId.R2], require_borrow=True)
        self.assertTrue(question_violations(T, borrow, _q("25 - 13", "12"), 0),
                        "25-13 个位 5≥3，不退位")
        self.assertEqual(question_violations(T, borrow, _q("25 - 17", "8"), 0), [])

        exact = _cfg(ops={T.Op.DIV}, ranges=[T.RangeId.R4], exact_division=True)
        self.assertTrue(question_violations(T, exact, _q("15 ÷ 2", "7余1"), 0),
                        "exact_division=True 时有余数应违规")

    def test_rejects_bad_metadata(self):
        T = load_types()
        config = _cfg(ops={T.Op.ADD, T.Op.SUB})
        bad_difficulty = _q("9 + 8", "17", difficulty=7)
        self.assertTrue(question_violations(T, config, bad_difficulty, 0))
        empty_tags = _q("9 + 8", "17", tags=[])
        self.assertTrue(question_violations(T, config, empty_tags, 0))
        blank_sig = _q("9 + 8", "17", signature="")
        self.assertTrue(question_violations(T, config, blank_sig, 0))
        wrong_steps = _q("9 + 8", "17", steps=["= 17"])
        self.assertTrue(question_violations(T, config, wrong_steps, 0),
                        "非脱式题 steps 必须为空（契约 §2.2）")

    def test_rejects_fill_without_box_and_stepwise_with_one_step(self):
        T = load_types()
        fill = _cfg(question_type=T.QuestionType.FILL)
        self.assertTrue(question_violations(T, fill, _q("7 + 8", "8"), 0),
                        "填空题 display 必须含 □")
        self.assertTrue(question_violations(
            T, fill, _q("7 + □", "8", display="7 + □ = 15"), 0),
            "填空题 expr 不应含 □（契约 §2.2）")
        step = _cfg(ops={T.Op.ADD, T.Op.MUL}, question_type=T.QuestionType.STEPWISE)
        self.assertTrue(question_violations(T, step, _q("8 + 2 × 3", "14", steps=["= 14"]), 0),
                        "脱式 steps 长度应 ≥2（FR-E12）")

    def test_paper_level_checks(self):
        T = load_types()
        config = _cfg(ops={T.Op.ADD}, count=3)
        questions = [_q("9 + 8", "17"), _q("7 + 5", "12"), _q("9 + 8", "17")]
        violations = paper_violations(T, config, questions)
        self.assertTrue(any("signature 重复" in v for v in violations), violations)

        config2 = _cfg(ops={T.Op.ADD}, count=2)
        self.assertTrue(any("题量" in v
                            for v in paper_violations(T, config2, questions)))

    def test_commutative_duplicates_detected(self):
        pairs = commutative_duplicates([_q("3 + 5", "8"), _q("5 + 3", "8")])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(commutative_duplicates([_q("3 + 5", "8"), _q("3 + 6", "9")]), [])


class TestInstrumentConfigGenerator(unittest.TestCase):
    """配置生成器只产出合法 config（否则随机属性测试自身就是伪配置）。"""

    def test_random_configs_are_legal_and_cover_all_pools(self):
        configs = random_configs(load_types(), count=60, seed=20261014)
        self.assertEqual(len(configs), 60)
        seen = set()
        for index, config in enumerate(configs):
            with self.subTest(index=index, config=describe(config)):
                self.assertEqual(config_rule_violations(config), [])
            seen.update(str(getattr(r, "value", r)) for r in config.ranges)
        self.assertEqual(seen, {"R1", "R2", "R3", "R4", "R5", "R6", "R7"},
                         "随机配置应覆盖全部 7 个档位")

    def test_random_configs_are_reproducible(self):
        first = random_configs(load_types(), count=10, seed=7)
        second = random_configs(load_types(), count=10, seed=7)
        self.assertEqual([describe(c) for c in first], [describe(c) for c in second],
                         "配置生成器必须固定种子可复现（P2）")

    def test_representative_configs_are_legal(self):
        configs = representative_configs(load_types())
        self.assertGreaterEqual(len(configs), 5, "§4.2 要求 ≥5 个代表性配置")
        for label, config in configs:
            with self.subTest(label=label):
                self.assertEqual(config_rule_violations(config), [])


class TestSuiteSelfValidation(unittest.TestCase):
    """用**手工构造的合规卷**验证检查器与公开入口调用包装 —— 完全不依赖实现。

    意义：验收套件最大的风险不是"太松"（会放过缺陷），而是"太严"（会误报缺陷、
    让实现方白改）。本类用一份"实现方理应产出的样子"喂给检查器，证明它不会误报。
    """

    def _paper(self, rows):
        """rows: (expr, answer, display, steps, difficulty, tags, signature)。"""
        T = load_types()
        return [T.Question(
            expr=expr, display=display if display is not None else expr, answer=answer,
            steps=list(steps), difficulty=difficulty, tags=list(tags), signature=sig)
            for expr, answer, display, steps, difficulty, tags, sig in rows]

    def _conformant_cases(self):
        T = load_types()
        return [
            (_cfg(ops={T.Op.ADD, T.Op.SUB}, ranges=[T.RangeId.R1], count=3), [
                ("9 + 8", "17", None, (), 2, ["20以内加法", "进位"], "9+8"),
                ("15 - 7", "8", None, (), 2, ["20以内减法", "退位"], "15-7"),
                ("6 + 5", "11", None, (), 1, ["20以内加法", "进位"], "6+5"),
            ]),
            (_cfg(ops={T.Op.MUL}, ranges=[T.RangeId.R3], exact_division=True, count=3), [
                ("9 × 8", "72", None, (), 2, ["表内乘法"], "9×8"),
                ("7 × 6", "42", None, (), 2, ["表内乘法"], "7×6"),
                ("4 × 5", "20", None, (), 1, ["表内乘法"], "4×5"),
            ]),
            (_cfg(ops={T.Op.MUL}, ranges=[T.RangeId.R5], require_carry=True, count=3), [
                ("123 × 45", "5535", None, (), 4, ["三位数乘两位数", "进位"], "123×45"),
                ("456 × 78", "35568", None, (), 4, ["三位数乘两位数", "进位"], "456×78"),
                ("234 × 56", "13104", None, (), 4, ["三位数乘两位数", "进位"], "234×56"),
            ]),
            # R4 无括号脱式：引擎当前产不出这一**合法**类别（见验收报告 F-1），
            # 检查器接受它 ⇒ 说明这是实现缺口，不是检查器过严。
            (_cfg(ops={T.Op.MUL, T.Op.DIV}, ranges=[T.RangeId.R4],
                  question_type=T.QuestionType.STEPWISE, count=1), [
                ("12 × 3 ÷ 4", "9", None, ("= 36 ÷ 4", "= 9"), 2, ["两位数乘除法"], "12×3÷4"),
            ]),
            (_cfg(ops={T.Op.ADD, T.Op.MUL}, ranges=[T.RangeId.R2],
                  question_type=T.QuestionType.STEPWISE, parentheses=True, count=2), [
                ("(12 + 8) × 3", "60", None, ("= 20 × 3", "= 60"), 3, ["混合运算", "含括号"], "(12+8)×3"),
                ("(5 + 15) × 4", "80", None, ("= 20 × 4", "= 80"), 3, ["混合运算", "含括号"], "(5+15)×4"),
            ]),
            (_cfg(ops={T.Op.ADD, T.Op.SUB}, ranges=[T.RangeId.R2],
                  question_type=T.QuestionType.FILL, count=3), [
                ("7 + 8", "8", "7 + □ = 15", (), 2, ["填空", "加法"], "7+8"),
                ("15 - 7", "7", "15 - □ = 8", (), 2, ["填空", "减法"], "15-7"),
                ("6 + 5", "5", "6 + □ = 11", (), 1, ["填空", "加法"], "6+5"),
            ]),
            (_cfg(ops={T.Op.MUL, T.Op.ADD}, ranges=[T.RangeId.R7], max_value=999, count=3), [
                ("12 × 34", "408", None, (), 3, ["两位数乘两位数"], "12×34"),
                ("100 + 250", "350", None, (), 2, ["整百加法"], "100+250"),
                ("9 × 100", "900", None, (), 2, ["整百乘法"], "9×100"),
            ]),
        ]

    def test_checker_accepts_conformant_papers(self):
        T = load_types()
        for config, rows in self._conformant_cases():
            with self.subTest(config=describe(config)):
                violations = paper_violations_all(T, config, self._paper(rows))
                self.assertEqual(
                    violations, [],
                    "检查器对**合规**的卷误报缺陷（会造成假阳性、浪费实现方时间）：\n  "
                    + "\n  ".join(violations))

    def test_checker_catches_injected_defects(self):
        T = load_types()
        config = _cfg(ops={T.Op.ADD, T.Op.SUB}, ranges=[T.RangeId.R1], count=3)
        ok = [("9 + 8", "17", None, (), 2, ["t"], "9+8"),
              ("15 - 7", "8", None, (), 2, ["t"], "15-7"),
              ("6 + 5", "11", None, (), 1, ["t"], "6+5")]
        variants = {
            "signature 重复": [ok[0], ("6 + 5", "11", None, (), 1, ["t"], "6+5"),
                               ("6 + 5", "11", None, (), 1, ["t"], "6+5")],
            "难度越界（6）": [(ok[0][0], "17", None, (), 6, ["t"], "9+8"), ok[1], ok[2]],
            "tags 为空": [("9 + 8", "17", None, (), 2, [], "9+8"), ok[1], ok[2]],
            "超出 R1 范围": [("15 + 9", "24", None, (), 2, ["t"], "15+9"), ok[1], ok[2]],
            "非脱式却带 steps": [("9 + 8", "17", None, ("= 17",), 2, ["t"], "9+8"), ok[1], ok[2]],
            "题量不足": [ok[0]],
        }
        for label, rows in variants.items():
            with self.subTest(defect=label):
                violations = paper_violations_all(T, config, self._paper(rows))
                self.assertTrue(violations, f"检查器漏检了注入缺陷「{label}」")

        # 脱式 / 填空题的专属缺陷
        step_cfg = _cfg(ops={T.Op.ADD, T.Op.MUL}, ranges=[T.RangeId.R2],
                        question_type=T.QuestionType.STEPWISE, parentheses=True, count=1)
        for label, row in {
            "脱式只有一步": ("(12 + 8) × 3", "60", None, ("= 60",), 3, ["t"], "(12+8)×3"),
            "脱式缺少括号": ("12 + 8 × 3", "36", None, ("= 20 × 3", "= 60"), 3, ["t"], "12+8×3"),
        }.items():
            with self.subTest(defect=label):
                self.assertTrue(paper_violations_all(T, step_cfg, self._paper([row])), label)

        fill_cfg = _cfg(question_type=T.QuestionType.FILL, count=1)
        with self.subTest(defect="填空 display 缺 □"):
            row = ("7 + 8", "8", "7 + 8 = 15", (), 2, ["t"], "7+8")
            self.assertTrue(paper_violations_all(T, fill_cfg, self._paper([row])))

    def test_missing_entry_points_fail_as_assertion_not_import_error(self):
        """**任务卡验收标准的直接编码**：实现缺位时必须是断言失败，不是 ImportError。

        做法：把入口候选换成不存在的模块，再调用 load_env()，断言抛出的异常
        类型是 AssertionError 且**不是** ImportError 的子类。
        """
        saved = (H._ENTRY_CANDIDATES, H._ENV, H._ENV_ERROR)
        try:
            H._ENTRY_CANDIDATES = {
                key: [(f"app.engine.__missing_{key}__", key)] for key in saved[0]}
            H._ENV = None
            H._ENV_ERROR = None
            with self.assertRaises(AssertionError) as ctx:
                H.load_env()
            self.assertNotIsInstance(ctx.exception, ImportError)
            self.assertIs(type(ctx.exception), AssertionError,
                          "必须是 AssertionError 本身（unittest 记为 FAIL，而非 ERROR）")
            self.assertIn("断言失败", str(ctx.exception))
        finally:
            H._ENTRY_CANDIDATES, H._ENV, H._ENV_ERROR = saved

    def test_step_value_violations_catches_out_of_range_step(self):
        """注入一条 `= 161 - 91` 的脱式步骤，检查器必须抓到。"""
        T = load_types()
        config = _cfg(ops={T.Op.ADD, T.Op.SUB}, ranges=[T.RangeId.R2],
                      question_type=T.QuestionType.STEPWISE, count=1)
        bad = self._paper([("65 + 96 - 91", "70", None, ("= 161 - 91", "= 70"), 3,
                            ["混合运算"], "65+96-91")])
        self.assertTrue(step_value_violations(T, config, bad),
                        "steps 里出现 161（>100）应被 step_value_violations 抓到")

    def test_link_wrappers_accept_conformant_and_reject_malformed(self):
        """call_generate / call_grade 必须把"返回形态不对"变成断言失败。"""
        T = load_types()
        config = _cfg(ops={T.Op.ADD, T.Op.SUB}, ranges=[T.RangeId.R1], count=3)
        paper = self._paper([("9 + 8", "17", None, (), 2, ["t"], "9+8"),
                             ("15 - 7", "8", None, (), 2, ["t"], "15-7"),
                             ("6 + 5", "11", None, (), 1, ["t"], "6+5")])
        env = SimpleNamespace(Question=T.Question, Verdict=T.Verdict,
                              generate=lambda c, s: paper,
                              grade=lambda q, raw: T.Verdict(True, str(raw)))

        self.assertIs(call_generate(self, env, config, 1), paper)
        self.assertEqual(call_grade(self, env, paper[0], "17").normalized_input, "17")

        for label, bad in {
            "generate 返回非 list": SimpleNamespace(
                **{**env.__dict__, "generate": lambda c, s: ("not", "a", "list")}),
            "generate 返回非 Question": SimpleNamespace(
                **{**env.__dict__, "generate": lambda c, s: [object()]}),
        }.items():
            with self.subTest(case=label):
                with self.assertRaises(AssertionError):
                    call_generate(self, bad, config, 1)

        bad_verdict = SimpleNamespace(**{**env.__dict__, "grade": lambda q, raw: "17"})
        with self.assertRaises(AssertionError):
            call_grade(self, bad_verdict, paper[0], "17")


if __name__ == "__main__":
    unittest.main()
