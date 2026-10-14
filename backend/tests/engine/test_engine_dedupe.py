"""去重签名测试（FR-E7，契约 §2.2）。

重点：**交换律归一** —— `3+5` 与 `5+3`、`2×6` 与 `6×2` 必须同签名。
"""
from __future__ import annotations

import unittest

from app.engine.builders import BinOp, Leaf, evaluate
from app.engine.dedupe import canonical_string, normalize, signature_of
from app.engine.types import Op


def add(a, b):
    return BinOp(Op.ADD, Leaf(a), Leaf(b))


def mul(a, b):
    return BinOp(Op.MUL, Leaf(a), Leaf(b))


def sub(a, b):
    return BinOp(Op.SUB, Leaf(a), Leaf(b))


def div(a, b):
    return BinOp(Op.DIV, Leaf(a), Leaf(b))


class TestCommutativeNormalisation(unittest.TestCase):
    def test_addition_is_commutative(self):
        self.assertEqual(signature_of(add(3, 5)), signature_of(add(5, 3)))

    def test_multiplication_is_commutative(self):
        self.assertEqual(signature_of(mul(2, 6)), signature_of(mul(6, 2)))

    def test_subtraction_is_not_commutative(self):
        self.assertNotEqual(signature_of(sub(7, 3)), signature_of(sub(3, 7)))

    def test_division_is_not_commutative(self):
        self.assertNotEqual(signature_of(div(12, 3)), signature_of(div(3, 12)))

    def test_nested_commutative_group(self):
        left = BinOp(Op.MUL, BinOp(Op.ADD, Leaf(12), Leaf(8)), Leaf(4))
        right = BinOp(Op.MUL, Leaf(4), BinOp(Op.ADD, Leaf(8), Leaf(12)))
        self.assertEqual(signature_of(left), signature_of(right))

    def test_different_expressions_differ(self):
        self.assertNotEqual(signature_of(add(3, 5)), signature_of(add(3, 6)))
        self.assertNotEqual(signature_of(mul(3, 5)), signature_of(add(3, 5)))

    def test_signature_has_no_spaces(self):
        self.assertEqual(signature_of(add(37, 45)), "37+45")
        self.assertNotIn(" ", signature_of(BinOp(Op.MUL, BinOp(Op.ADD, Leaf(1), Leaf(2)), Leaf(3))))

    def test_canonical_string_of_group(self):
        node = BinOp(Op.MUL, BinOp(Op.ADD, Leaf(12), Leaf(8)), Leaf(4))
        self.assertEqual(canonical_string(node), "(12+8)×4")

    def test_normalize_preserves_value_and_is_idempotent(self):
        node = BinOp(Op.ADD, Leaf(5), BinOp(Op.MUL, Leaf(6), Leaf(2)))
        normalised = normalize(node)
        self.assertEqual(evaluate(normalised), evaluate(node))
        self.assertEqual(normalize(normalised), normalised)
        # 交换律归一后，两种写法的渲染结果一致
        swapped = BinOp(Op.ADD, BinOp(Op.MUL, Leaf(2), Leaf(6)), Leaf(5))
        self.assertEqual(signature_of(swapped), signature_of(node))


class TestFillSignatures(unittest.TestCase):
    def test_blank_position_survives_commutative_normalisation(self):
        left_blank = BinOp(Op.ADD, Leaf(0, unknown=True), Leaf(7))
        right_blank = BinOp(Op.ADD, Leaf(7), Leaf(0, unknown=True))
        self.assertEqual(signature_of(left_blank), signature_of(right_blank))

    def test_different_unknowns_get_different_signatures(self):
        blank_seven = BinOp(Op.ADD, Leaf(0, unknown=True), Leaf(8))
        blank_eight = BinOp(Op.ADD, Leaf(7), Leaf(0, unknown=True))
        self.assertNotEqual(signature_of(blank_seven), signature_of(blank_eight))

    def test_result_blank_differs_from_operand_blank(self):
        base = add(7, 8)
        operand_blank = BinOp(Op.ADD, Leaf(7), Leaf(0, unknown=True))
        self.assertNotEqual(signature_of(base, result_blank=True),
                            signature_of(operand_blank))
        self.assertIn("□", signature_of(base, result_blank=True))

    def test_signature_of_plain_expression_has_no_blank(self):
        self.assertNotIn("□", signature_of(add(7, 8)))


if __name__ == "__main__":
    unittest.main()
