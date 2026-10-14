"""参数校验测试（契约 §2.1，FR-E1–E6）。"""
from __future__ import annotations

import unittest

from app.engine.config import validate
from app.engine.types import (
    ConfigError,
    GenerateConfig,
    Op,
    QuestionType,
    RangeId,
)


def cfg(**overrides) -> GenerateConfig:
    base = dict(ops={Op.ADD}, ranges=[RangeId.R1], question_type=QuestionType.ORAL)
    base.update(overrides)
    return GenerateConfig(**base)


class TestValidConfigs(unittest.TestCase):
    def test_minimal_config_is_valid(self):
        validate(cfg())                      # 不抛异常即通过

    def test_all_ops_all_ranges_is_valid(self):
        validate(cfg(ops=set(Op), ranges=list(RangeId), max_value=100,
                     question_type=QuestionType.ORAL))

    def test_r7_with_max_value_is_valid(self):
        validate(cfg(ranges=[RangeId.R7], max_value=10))
        validate(cfg(ranges=[RangeId.R7], max_value=1000))

    def test_count_boundaries_are_valid(self):
        validate(cfg(count=1))
        validate(cfg(count=100))

    def test_exact_division_alone_is_valid(self):
        validate(cfg(ops={Op.DIV}, exact_division=True))

    def test_allow_remainder_alone_is_valid(self):
        validate(cfg(ops={Op.DIV}, allow_remainder=True))

    def test_stepwise_with_two_ops_is_valid(self):
        validate(cfg(ops={Op.ADD, Op.MUL}, question_type=QuestionType.STEPWISE))

    def test_stepwise_single_op_with_parentheses_is_valid(self):
        validate(cfg(ops={Op.ADD}, question_type=QuestionType.STEPWISE, parentheses=True))

    def test_fill_without_parentheses_is_valid(self):
        validate(cfg(question_type=QuestionType.FILL))

    def test_oral_with_parentheses_is_tolerated(self):
        """口径：ORAL 时 parentheses 无意义但仍被忽略（不报错）。"""
        validate(cfg(question_type=QuestionType.ORAL, parentheses=True))

    def test_max_value_ignored_when_no_r7(self):
        """口径：不含 R7 时 max_value 被忽略（契约只要求「含 R7 时必填」）。"""
        validate(cfg(ranges=[RangeId.R1], max_value=3))
        validate(cfg(ranges=[RangeId.R1], max_value=None))


class TestInvalidConfigs(unittest.TestCase):
    def test_exact_division_and_allow_remainder_conflict(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ops={Op.DIV}, exact_division=True, allow_remainder=True))

    def test_r7_without_max_value(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ranges=[RangeId.R7]))

    def test_r7_max_value_below_ten(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ranges=[RangeId.R7], max_value=9))
        with self.assertRaises(ConfigError):
            validate(cfg(ranges=[RangeId.R7], max_value=-5))
        with self.assertRaises(ConfigError):
            validate(cfg(ranges=[RangeId.R7], max_value=True))

    def test_count_out_of_range(self):
        for bad in (0, 101, -1, 1000):
            with self.assertRaises(ConfigError, msg="count=%r" % bad):
                validate(cfg(count=bad))

    def test_count_must_be_int(self):
        with self.assertRaises(ConfigError):
            validate(cfg(count="20"))
        with self.assertRaises(ConfigError):
            validate(cfg(count=None))

    def test_empty_ops(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ops=set()))

    def test_empty_ranges(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ranges=[]))

    def test_bad_op_and_range_types(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ops={"+", "-"}))
        with self.assertRaises(ConfigError):
            validate(cfg(ranges=["R1"]))

    def test_bad_question_type(self):
        with self.assertRaises(ConfigError):
            validate(cfg(question_type="oral"))

    def test_stepwise_single_op_without_parentheses(self):
        with self.assertRaises(ConfigError):
            validate(cfg(ops={Op.ADD}, question_type=QuestionType.STEPWISE))

    def test_fill_with_parentheses(self):
        with self.assertRaises(ConfigError):
            validate(cfg(question_type=QuestionType.FILL, parentheses=True))

    def test_config_error_is_value_error(self):
        """契约：错误码 CONFIG_INVALID 走 ValueError 语义。"""
        with self.assertRaises(ValueError):
            validate(cfg(count=0))


if __name__ == "__main__":
    unittest.main()
