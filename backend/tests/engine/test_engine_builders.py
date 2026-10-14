"""表达式树测试：渲染括号、求值、逐步递等过程（FR-E12）。

渲染与求值必须**自洽**：卷面字符串按正常优先级算出来的结果，
必须等于表达式树本身求出来的结果。这里用测试侧独立的解析器（`_probe`）交叉验证。
"""
from __future__ import annotations

import unittest

from app.engine.builders import (
    BinOp,
    Leaf,
    evaluate,
    has_group,
    leaf_values,
    make_steps,
    op_count,
    render,
)
from app.engine.types import Op

from . import _probe


def leaf(value: int) -> Leaf:
    return Leaf(value)


class TestRenderAndEvaluate(unittest.TestCase):
    def test_single_operation(self):
        node = BinOp(Op.ADD, leaf(37), leaf(45))
        self.assertEqual(render(node), "37 + 45")
        self.assertEqual(evaluate(node), 82)

    def test_higher_precedence_on_right_needs_no_parens(self):
        node = BinOp(Op.ADD, leaf(12), BinOp(Op.MUL, leaf(8), leaf(4)))
        self.assertEqual(render(node), "12 + 8 × 4")
        self.assertEqual(_probe.eval_text(render(node)), evaluate(node))

    def test_lower_precedence_child_gets_parens(self):
        node = BinOp(Op.MUL, BinOp(Op.ADD, leaf(12), leaf(8)), leaf(4))
        self.assertEqual(render(node), "(12 + 8) × 4")
        self.assertEqual(evaluate(node), 80)
        self.assertEqual(_probe.eval_text(render(node)), 80)

    def test_right_child_of_subtraction_gets_parens(self):
        node = BinOp(Op.SUB, leaf(20), BinOp(Op.SUB, leaf(9), leaf(3)))
        self.assertEqual(render(node), "20 - (9 - 3)")
        self.assertEqual(_probe.eval_text(render(node)), evaluate(node))

    def test_right_child_of_division_gets_parens(self):
        node = BinOp(Op.DIV, leaf(36), BinOp(Op.DIV, leaf(12), leaf(2)))
        self.assertEqual(render(node), "36 ÷ (12 ÷ 2)")
        self.assertEqual(_probe.eval_text(render(node)), evaluate(node))

    def test_mul_with_div_on_the_right_gets_parens(self):
        node = BinOp(Op.MUL, leaf(6), BinOp(Op.DIV, leaf(12), leaf(3)))
        self.assertEqual(render(node), "6 × (12 ÷ 3)")
        self.assertEqual(_probe.eval_text(render(node)), evaluate(node))

    def test_grouped_flag_is_preserved(self):
        node = BinOp(Op.ADD, leaf(1), BinOp(Op.ADD, leaf(2), leaf(3), grouped=True))
        self.assertEqual(render(node), "1 + (2 + 3)")
        self.assertTrue(has_group(node))

    def test_left_nested_equal_precedence_has_no_parens(self):
        node = BinOp(Op.SUB, BinOp(Op.ADD, leaf(23), leaf(63)), leaf(11))
        self.assertEqual(render(node), "23 + 63 - 11")
        self.assertEqual(_probe.eval_text(render(node)), evaluate(node))

    def test_tree_helpers(self):
        node = BinOp(Op.ADD, leaf(12), BinOp(Op.MUL, leaf(8), leaf(4)))
        self.assertEqual(op_count(node), 2)
        self.assertEqual(sorted(leaf_values(node)), [4, 8, 12])
        self.assertFalse(has_group(node))

    def test_unknown_leaf_is_not_evaluable(self):
        node = BinOp(Op.ADD, Leaf(0, unknown=True), leaf(7))
        self.assertEqual(render(node), "□ + 7")
        with self.assertRaises(ValueError):
            evaluate(node)
        self.assertEqual(leaf_values(node), [7])


class TestSteps(unittest.TestCase):
    def test_steps_for_mixed_precedence(self):
        node = BinOp(Op.ADD, leaf(12), BinOp(Op.MUL, leaf(8), leaf(4)))
        steps = make_steps(node)
        self.assertEqual(steps[0], "12 + 8 × 4")
        self.assertEqual(steps[-1], "44")
        self.assertGreaterEqual(len(steps), 2)

    def test_steps_reduce_parenthesised_group_first(self):
        node = BinOp(Op.MUL, BinOp(Op.ADD, leaf(12), leaf(8)), leaf(4))
        steps = make_steps(node)
        self.assertEqual(steps, ["(12 + 8) × 4", "20 × 4", "80"])

    def test_steps_for_left_nested_go_left_to_right(self):
        node = BinOp(Op.SUB, BinOp(Op.ADD, leaf(23), leaf(63)), leaf(11))
        steps = make_steps(node)
        self.assertEqual(steps, ["23 + 63 - 11", "86 - 11", "75"])

    def test_every_step_keeps_the_same_value(self):
        samples = [
            BinOp(Op.ADD, leaf(12), BinOp(Op.MUL, leaf(8), leaf(4))),
            BinOp(Op.DIV, BinOp(Op.MUL, leaf(6), leaf(12)), leaf(3)),
            BinOp(Op.SUB, BinOp(Op.ADD, leaf(95), leaf(5)), leaf(37)),
        ]
        for node in samples:
            steps = make_steps(node)
            final = evaluate(node)
            for step in steps:
                self.assertEqual(_probe.eval_text(step), final,
                                 "步骤 %r 的值与最终答案不一致" % step)
            self.assertEqual(steps[-1], str(final))

    def test_single_operation_steps(self):
        node = BinOp(Op.ADD, leaf(3), leaf(5))
        self.assertEqual(make_steps(node), ["3 + 5", "8"])


if __name__ == "__main__":
    unittest.main()
