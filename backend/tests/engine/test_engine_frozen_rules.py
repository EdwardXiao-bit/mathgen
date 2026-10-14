"""三条**冻结**新增规则的专项测试（契约 §2.1 / §2.3）。

  - **D46** 脱式（递等式）必须左结合；且每一步中间值都要满足档位约束，
    越界的题不得生成；
  - **D47** 档位位数组合与顺序无关（`三位数 × 两位数` 同时接受 `两位数 × 三位数`），
    R5 + 无括号混合乘除脱式必须能稳定出题；
  - **D48** 公开入口必须可从包根导入（`from app.engine import generate`）。

写在这里而不是散落到各文件，是为了让这三条规则**可指认**（每条一个具名用例）。
"""
from __future__ import annotations

import importlib
import unittest

import app.engine as engine_package
from app.engine.generate import generate
from app.engine.ranges import bounds_for, spec_for
from app.engine.types import (
    GenerateConfig,
    Op,
    QuestionType,
    RangeId,
)

from . import _probe

SEEDS = (0, 1, 7, 20261014)


class TestD46LeftAssociativeSteps(unittest.TestCase):
    """D46①：脱式必须**左结合**（从左到右）依次化简。"""

    def _cfg(self, **kw):
        base = dict(ops={Op.ADD, Op.SUB}, ranges=[RangeId.R2],
                    question_type=QuestionType.STEPWISE)
        base.update(kw)
        return GenerateConfig(**base)

    def test_equal_precedence_reduces_left_to_right(self):
        """`78 - 56 - 18` 的第一步必须是 `78 - 56 = 22`（而不是 `56 - 18`）。"""
        config = self._cfg(count=15)
        checked = 0
        for seed in SEEDS:
            for question in generate(config, seed):
                tokens = _probe.tokenize(question.expr)
                if len(tokens) != 5:      # 只针对「a op b op c」这一形状
                    continue
                checked += 1
                self.assertEqual(len(question.steps), 3,
                                 "两步脱式应有 3 步过程：%r" % (question.steps,))
                first_block = _probe.eval_text(" ".join(tokens[:3]))
                middle = _probe.tokenize(question.steps[1])
                self.assertEqual(
                    [middle[0], middle[1], middle[2]],
                    [str(first_block), tokens[3], tokens[4]],
                    "第一步不是从左往右化简：expr=%r steps=%r"
                    % (question.expr, question.steps))
                self.assertEqual(question.steps[2], question.answer)
        self.assertGreater(checked, 20, "本用例没有检查到足够的题（%d）" % checked)

    def test_every_printed_step_value_within_numeric_cap(self):
        """D46②：R1 / R2 / R7 的答案版上不得出现越界数字。"""
        cases = [
            (self._cfg(count=15), 100),                                   # R2 加减
            (self._cfg(ops={Op.ADD, Op.SUB}, ranges=[RangeId.R1], count=12), 20),  # R1 加减
            (self._cfg(ops={Op.ADD, Op.MUL}, ranges=[RangeId.R2], count=12), 100),  # R2 混合
            (self._cfg(ops={Op.ADD}, ranges=[RangeId.R1],
                       parentheses=True, count=12), 20),                   # R1（单运算需括号）
            (GenerateConfig(ops={Op.MUL, Op.ADD}, ranges=[RangeId.R7], max_value=50,
                            question_type=QuestionType.STEPWISE, count=12), 50),
        ]
        checked = 0
        for config, cap in cases:
            for seed in SEEDS:
                for question in generate(config, seed):
                    for step in question.steps:
                        for value in _probe.int_tokens(step):
                            checked += 1
                            self.assertGreaterEqual(value, 0)
                            self.assertLessEqual(
                                value, cap,
                                "steps 里出现越界数字 %d（上限 %d）：%r"
                                % (value, cap, question.steps))
        self.assertGreater(checked, 100)

    def test_out_of_range_question_is_never_generated(self):
        """D46③：`53 + 52 - 17` 在 R2 下第一步就越界，该题不得被生成。"""
        config = self._cfg(count=20)
        for seed in SEEDS:
            for question in generate(config, seed):
                for value in _probe.left_to_right_values(question.expr):
                    self.assertGreaterEqual(value, 0)
                    self.assertLessEqual(
                        value, 100,
                        "生成了从左到右展开会越界的题：expr=%r 中间值=%r"
                        % (question.expr, _probe.left_to_right_values(question.expr)))
                self.assertEqual(
                    _probe.left_to_right_values(question.expr)[-1],
                    int(question.answer))

    def test_printed_string_reads_back_to_the_answer(self):
        """卷面字符串按**标准优先级**独立求值必须等于答案。

        同级运算（纯加减）时，左到右读法与标准读法一致 —— 这时才追加左结合断言；
        混合优先级（`12 + 8 × 4`）必须按「先乘除后加减」读，不算左结合。
        """
        config = self._cfg(ops={Op.ADD, Op.SUB, Op.MUL}, count=15)
        checked_flat = 0
        for seed in SEEDS:
            for question in generate(config, seed):
                self.assertNotIn("(", question.expr)
                value = _probe.evaluate(_probe.parse(question.expr))
                self.assertEqual(str(value), question.answer, question.expr)
                if "×" not in question.expr and "÷" not in question.expr:
                    checked_flat += 1
                    self.assertEqual(
                        _probe.left_to_right_values(question.expr)[-1], value,
                        "同级运算应等价于左结合：%r" % question.expr)
        self.assertGreater(checked_flat, 0)


class TestD47DigitShapeOrderIndependence(unittest.TestCase):
    """D47：位数组合与顺序无关；R5 无括号混合乘除脱式必须能出题。"""

    def test_r5_multiplication_accepts_both_orders(self):
        bd = bounds_for(spec_for(RangeId.R5), Op.MUL)
        self.assertTrue(bd.holds(891, 27, 24057), "三位数 × 两位数 应合法")
        self.assertTrue(bd.holds(27, 891, 24057), "两位数 × 三位数 也应合法（D47）")
        self.assertFalse(bd.holds(329, 151, 49679), "三位数 × 三位数 不在档位内")

    def test_r4_multiplication_accepts_both_orders(self):
        bd = bounds_for(spec_for(RangeId.R4), Op.MUL)
        self.assertTrue(bd.holds(67, 3, 201))
        self.assertTrue(bd.holds(3, 67, 201))
        self.assertFalse(bd.holds(67, 13, 871), "两位数 × 两位数 不在档位内")

    def test_r5_mul_div_stepwise_is_feasible(self):
        """D47 点名的组合：R5 + STEPWISE + {×,÷} + 无括号，必须稳定出题。"""
        for parentheses in (False, True):
            config = GenerateConfig(
                ops={Op.MUL, Op.DIV}, ranges=[RangeId.R5],
                question_type=QuestionType.STEPWISE,
                parentheses=parentheses, count=20)
            for seed in SEEDS:
                paper = generate(config, seed)      # 不得抛 InsufficientQuestions
                self.assertEqual(len(paper), 20)
                self.assertEqual(len({q.signature for q in paper}), 20)
                for question in paper:
                    self.assertEqual(("(" in question.expr), parentheses)
                    self.assertGreaterEqual(len(question.steps), 2)

    def test_r5_stepwise_actually_uses_two_by_three_shape(self):
        """把 D47 的例子落到实处：必须真的出现「÷ 得两位数，再乘三位数」这种形态。"""
        config = GenerateConfig(ops={Op.MUL, Op.DIV}, ranges=[RangeId.R5],
                                question_type=QuestionType.STEPWISE, count=20)
        found = None
        for seed in SEEDS:
            for question in generate(config, seed):
                for char, left, right, _value in _probe.nodes(_probe.parse(question.expr)):
                    if char == "×" and (len(str(left)), len(str(right))) == (2, 3):
                        found = question
                        break
                if found:
                    break
            if found:
                break
        self.assertIsNotNone(
            found,
            "R5 脱式从未出现「两位数 × 三位数」（D47：位数组合与顺序无关）")

    def test_r5_div_still_requires_three_by_two(self):
        """除法不是交换律运算：被除数仍须三位数、除数两位数（D47「除法同理」）。"""
        config = GenerateConfig(ops={Op.DIV}, ranges=[RangeId.R5],
                                question_type=QuestionType.ORAL, count=15)
        for seed in SEEDS:
            for question in generate(config, seed):
                dividend, divisor = _probe.top_division(question.expr)
                self.assertEqual(len(str(dividend)), 3, question.expr)
                self.assertEqual(len(str(divisor)), 2, question.expr)


class TestD48PackageEntrypoints(unittest.TestCase):
    """D48：`app.engine` 必须 re-export `generate` / `regenerate_one`。"""

    def test_from_package_root_is_callable(self):
        from app.engine import generate as pkg_generate
        from app.engine import regenerate_one as pkg_regenerate
        self.assertTrue(callable(pkg_generate))
        self.assertTrue(callable(pkg_regenerate))

    def test_reexport_is_the_same_object_as_module_function(self):
        # 注意：`app.engine.generate` 这个**属性**已被 re-export 覆盖成函数，
        # 因此这里必须用 importlib 拿真正的子模块（D48 的副作用，属预期）。
        generate_module = importlib.import_module("app.engine.generate")
        self.assertIs(engine_package.generate, generate_module.generate)
        self.assertIs(engine_package.regenerate_one, generate_module.regenerate_one)

    def test_all_declares_the_public_entrypoints(self):
        self.assertEqual(sorted(engine_package.__all__),
                         ["generate", "regenerate_one"])

    def test_package_root_entrypoint_actually_runs(self):
        config = GenerateConfig(ops={Op.ADD, Op.SUB}, ranges=[RangeId.R2],
                                question_type=QuestionType.ORAL, count=5)
        paper = engine_package.generate(config, 3)
        self.assertEqual(len(paper), 5)
        fresh = engine_package.regenerate_one(config, 3, 0, paper)
        self.assertIsNotNone(fresh.signature)


if __name__ == "__main__":
    unittest.main()
