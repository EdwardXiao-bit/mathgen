"""特殊约束原语测试：位数、进位、退位、取整与「已知一个操作数反推另一个」。

进位 / 退位的口径见 `app/engine/constraints.py` 模块 docstring：
按竖式逐位计算，任一位满足条件即计数一次。
"""
from __future__ import annotations

import unittest

from app.engine.builders import Infeasible, build_simple
from app.engine.constraints import (
    borrow_count,
    carry_count,
    ceil_div,
    digit_count,
    first_ge_with_mod,
    has_borrow,
    has_carry,
)
from app.engine.ranges import Bounds, bounds_for, spec_for
from app.engine.rng import Rng
from app.engine.types import Op, RangeId


class TestDigitAndRounding(unittest.TestCase):
    def test_digit_count(self):
        self.assertEqual(digit_count(0), 1)
        self.assertEqual(digit_count(7), 1)
        self.assertEqual(digit_count(10), 2)
        self.assertEqual(digit_count(99), 2)
        self.assertEqual(digit_count(100), 3)
        self.assertEqual(digit_count(9999), 4)

    def test_ceil_div(self):
        self.assertEqual(ceil_div(10, 3), 4)
        self.assertEqual(ceil_div(12, 3), 4)
        self.assertEqual(ceil_div(1, 3), 1)
        self.assertEqual(ceil_div(-3, 10), 0)
        self.assertEqual(ceil_div(0, 5), 0)

    def test_first_ge_with_mod(self):
        self.assertEqual(first_ge_with_mod(13, 10, 5), 15)
        self.assertEqual(first_ge_with_mod(15, 10, 5), 15)
        self.assertEqual(first_ge_with_mod(16, 10, 5), 25)
        self.assertEqual(first_ge_with_mod(0, 10, 0), 0)
        self.assertEqual(first_ge_with_mod(1, 10, 0), 10)


class TestCarryBorrow(unittest.TestCase):
    def test_carry_detected_at_tens(self):
        # 45 + 60：个位 5+0=5 不进位，十位 4+6=10 进位
        self.assertEqual(carry_count(45, 60), 1)
        self.assertTrue(has_carry(45, 60))

    def test_carry_detected_at_units(self):
        self.assertEqual(carry_count(23, 45), 0)
        self.assertFalse(has_carry(23, 45))
        self.assertEqual(carry_count(8, 7), 1)

    def test_carry_can_be_counted_twice(self):
        # 95 + 5 = 100：个位 5+5 进位，十位 9+0+1 再进位
        self.assertEqual(carry_count(95, 5), 2)

    def test_borrow_detected(self):
        self.assertEqual(borrow_count(52, 17), 1)
        self.assertTrue(has_borrow(52, 17))
        self.assertEqual(borrow_count(52, 11), 0)
        self.assertFalse(has_borrow(52, 11))

    def test_borrow_chains(self):
        # 100 - 1 = 99：个位与十位各退位一次
        self.assertEqual(borrow_count(100, 1), 2)


class TestConstructiveBuilders(unittest.TestCase):
    """构造器必须在**不过滤**的前提下满足边界与特殊约束。"""

    def _spec(self, rid, max_value=None):
        return spec_for(rid, max_value)

    def test_addition_stays_in_bounds(self):
        rng = Rng(1)
        bd = bounds_for(self._spec(RangeId.R2), Op.ADD)
        for _ in range(300):
            record = build_simple(rng, Op.ADD, bd)
            self.assertTrue(bd.holds(record.left, record.right, record.value))
            self.assertEqual(record.value, record.left + record.right)

    def test_subtraction_never_negative(self):
        rng = Rng(2)
        for rid in (RangeId.R1, RangeId.R2, RangeId.R6, RangeId.R7):
            bd = bounds_for(self._spec(rid, 50), Op.SUB)
            for _ in range(200):
                record = build_simple(rng, Op.SUB, bd)
                self.assertGreaterEqual(record.value, 0)
                self.assertTrue(bd.holds(record.left, record.right, record.value))

    def test_division_always_exact_without_remainder_permission(self):
        rng = Rng(3)
        for rid in (RangeId.R3, RangeId.R4, RangeId.R5, RangeId.R7):
            bd = bounds_for(self._spec(rid, 100), Op.DIV)
            for _ in range(200):
                record = build_simple(rng, Op.DIV, bd, allow_remainder=False)
                self.assertEqual(record.left % record.right, 0)
                self.assertEqual(record.remainder, 0)
                self.assertEqual(record.left, record.right * record.value)
                self.assertTrue(bd.holds(record.left, record.right, record.value))

    def test_remainder_is_smaller_than_divisor_only_when_allowed(self):
        rng = Rng(4)
        bd = bounds_for(self._spec(RangeId.R2), Op.DIV)
        with_remainder = 0
        for _ in range(400):
            record = build_simple(rng, Op.DIV, bd, allow_remainder=True)
            self.assertTrue(bd.holds(record.left, record.right, record.value))
            if record.has_remainder:
                with_remainder += 1
                self.assertLess(record.remainder, record.right)
                self.assertEqual(record.left,
                                 record.right * record.value + record.remainder)
        self.assertGreater(with_remainder, 0, "allow_remainder 应能产出有余数的题")

    def test_require_carry_is_constructed_not_filtered(self):
        rng = Rng(5)
        for rid in (RangeId.R1, RangeId.R2, RangeId.R6):
            bd = bounds_for(self._spec(rid), Op.ADD)
            for _ in range(200):
                record = build_simple(rng, Op.ADD, bd, require_carry=True)
                self.assertGreater(carry_count(record.left, record.right), 0,
                                   "%s: %d + %d 未进位" % (rid, record.left, record.right))

    def test_require_borrow_is_constructed_not_filtered(self):
        rng = Rng(6)
        for rid in (RangeId.R1, RangeId.R2, RangeId.R6):
            bd = bounds_for(self._spec(rid), Op.SUB)
            for _ in range(200):
                record = build_simple(rng, Op.SUB, bd, require_borrow=True)
                self.assertGreater(borrow_count(record.left, record.right), 0,
                                   "%s: %d - %d 未退位" % (rid, record.left, record.right))

    def test_fixed_operand_respects_bounds(self):
        """已知一个操作数时也必须落在边界内（脱式内层结果作为外层操作数）。"""
        rng = Rng(7)
        bd = bounds_for(self._spec(RangeId.R2), Op.ADD)
        for known in range(bd.b_lo, bd.b_hi + 1):
            try:
                record = build_simple(rng, Op.ADD, bd, fixed_right=known)
            except Infeasible:
                continue
            self.assertTrue(bd.holds(record.left, record.right, record.value))

    def test_impossible_tightened_bounds_raise_infeasible(self):
        bd = Bounds(1, 9, 1, 9, 1, 9)
        with self.assertRaises(Infeasible):
            build_simple(Rng(8), Op.ADD, bd, res_lo=100, res_hi=200)

    def test_both_sides_fixed_is_rejected(self):
        bd = bounds_for(self._spec(RangeId.R1), Op.ADD)
        with self.assertRaises(ValueError):
            build_simple(Rng(9), Op.ADD, bd, fixed_left=3, fixed_right=4)

    def test_div_with_fixed_left_is_not_supported(self):
        """口径：左嵌套的外层不会是 ÷，因此「已知被除数」不实现。"""
        bd = bounds_for(self._spec(RangeId.R2), Op.DIV)
        with self.assertRaises(Infeasible):
            build_simple(Rng(10), Op.DIV, bd, fixed_left=24)


class TestRngDeterminism(unittest.TestCase):
    def test_same_seed_same_sequence(self):
        a = [Rng(123).next_u64() for _ in range(1)]
        b = [Rng(123).next_u64() for _ in range(1)]
        self.assertEqual(a, b)

    def test_different_seed_different_sequence(self):
        first = [Rng(1).next_u64() for _ in range(1)]
        second = [Rng(2).next_u64() for _ in range(1)]
        self.assertNotEqual(first, second)

    def test_negative_and_huge_seeds_are_accepted(self):
        first = Rng(-1).next_u64()
        second = Rng((1 << 70) + 5).next_u64()
        self.assertIsInstance(first, int)
        self.assertIsInstance(second, int)

    def test_shuffle_is_pure(self):
        rng = Rng(11)
        source = [1, 2, 3, 4, 5]
        shuffled = rng.shuffle(source)
        self.assertEqual(source, [1, 2, 3, 4, 5])
        self.assertEqual(sorted(shuffled), [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
