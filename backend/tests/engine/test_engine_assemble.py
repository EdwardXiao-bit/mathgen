"""难度评分与考点标签测试（FR-E10 / FR-E11，规则见 docs/02-技术设计.md §6.4）。"""
from __future__ import annotations

import unittest

from app.engine.assemble import carry_borrow_total, difficulty_of, tags_of
from app.engine.builders import BinOp, Leaf
from app.engine.ranges import spec_for
from app.engine.types import Op, QuestionType, RangeId


def leaf(value: int) -> Leaf:
    return Leaf(value)


class TestDifficulty(unittest.TestCase):
    def test_single_digit_question_is_easiest(self):
        node = BinOp(Op.ADD, leaf(3), leaf(4))
        self.assertEqual(difficulty_of(node), 1)

    def test_two_digit_operands_raise_difficulty(self):
        node = BinOp(Op.ADD, leaf(23), leaf(45))
        self.assertEqual(difficulty_of(node), 2)

    def test_carry_adds_one(self):
        without = BinOp(Op.ADD, leaf(23), leaf(45))     # 无进位
        with_carry = BinOp(Op.ADD, leaf(45), leaf(60))  # 十位进位
        self.assertEqual(difficulty_of(with_carry), difficulty_of(without) + 1)

    def test_borrow_adds_one(self):
        without = BinOp(Op.SUB, leaf(52), leaf(11))
        with_borrow = BinOp(Op.SUB, leaf(52), leaf(17))
        self.assertEqual(difficulty_of(with_borrow), difficulty_of(without) + 1)

    def test_extra_operation_adds_one(self):
        single = BinOp(Op.ADD, leaf(12), leaf(34))
        double = BinOp(Op.ADD, leaf(12), BinOp(Op.MUL, leaf(1), leaf(34)))
        self.assertEqual(difficulty_of(double), difficulty_of(single) + 1)

    def test_parentheses_add_one(self):
        plain = BinOp(Op.ADD, leaf(12), BinOp(Op.ADD, leaf(3), leaf(4)))
        grouped = BinOp(Op.ADD, leaf(12), BinOp(Op.ADD, leaf(3), leaf(4), grouped=True))
        self.assertEqual(difficulty_of(grouped), difficulty_of(plain) + 1)

    def test_remainder_adds_one(self):
        node = BinOp(Op.DIV, leaf(23), leaf(5))
        self.assertEqual(difficulty_of(node, has_remainder=True),
                         difficulty_of(node, has_remainder=False) + 1)

    def test_always_clamped_into_one_to_five(self):
        huge = BinOp(Op.ADD, leaf(9999), BinOp(Op.MUL, leaf(8888), leaf(7777), grouped=True))
        self.assertGreaterEqual(difficulty_of(huge, True), 1)
        self.assertLessEqual(difficulty_of(huge, True), 5)

    def test_carry_borrow_total_counts_every_node(self):
        # 内层 45+60 进位 1 次；52-17 退位 1 次；外层 105+35 进位 1 次 ⇒ 共 3 次
        node = BinOp(Op.ADD, BinOp(Op.ADD, leaf(45), leaf(60)),
                     BinOp(Op.SUB, leaf(52), leaf(17)))
        self.assertEqual(carry_borrow_total(node), 3)


class TestTags(unittest.TestCase):
    def test_tags_are_never_empty(self):
        for rid in RangeId:
            spec = spec_for(rid, 100)
            for op in Op:
                tags = tags_of(spec, [op])
                self.assertTrue(tags, "%s/%s 标签为空" % (rid, op))

    def test_carry_and_borrow_tags(self):
        spec = spec_for(RangeId.R2)
        self.assertIn("100以内加法", tags_of(spec, [Op.ADD], carries=1))
        self.assertIn("进位", tags_of(spec, [Op.ADD], carries=1))
        self.assertNotIn("进位", tags_of(spec, [Op.ADD], carries=0))
        self.assertIn("退位", tags_of(spec, [Op.SUB], borrows=1))

    def test_question_type_tags(self):
        spec = spec_for(RangeId.R1)
        self.assertIn("脱式", tags_of(spec, [Op.ADD], question_type=QuestionType.STEPWISE))
        self.assertIn("求未知数", tags_of(spec, [Op.ADD], question_type=QuestionType.FILL))
        self.assertNotIn("脱式", tags_of(spec, [Op.ADD], question_type=QuestionType.ORAL))

    def test_constraint_tags(self):
        spec = spec_for(RangeId.R5)
        tags = tags_of(spec, [Op.MUL], parentheses=True, remainder=True)
        self.assertIn("含括号", tags)
        self.assertIn("有余数", tags)

    def test_range_specific_labels(self):
        self.assertIn("表内乘法", tags_of(spec_for(RangeId.R3), [Op.MUL]))
        self.assertIn("两位数乘一位数", tags_of(spec_for(RangeId.R4), [Op.MUL]))
        self.assertIn("三位数乘两位数", tags_of(spec_for(RangeId.R5), [Op.MUL]))
        self.assertIn("多位数减法", tags_of(spec_for(RangeId.R6), [Op.SUB]))
        self.assertIn("200以内除法", tags_of(spec_for(RangeId.R7, 200), [Op.DIV]))


if __name__ == "__main__":
    unittest.main()
