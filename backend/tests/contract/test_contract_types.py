"""契约测试：守卫冻结的数据结构（对应 docs/06-测试方案.md §6.1）。

这些断言的作用是「改字段名就立刻失败」——防止实现方顺手改动契约。
本文件由 Lead 维护，随 docs/04-接口契约.md 一起变更。
"""
import dataclasses
import unittest

from app.engine.types import (
    ConfigError,
    GenerateConfig,
    InsufficientQuestions,
    Op,
    Question,
    QuestionType,
    RangeId,
    Verdict,
)


class TestGenerateConfigContract(unittest.TestCase):
    def test_field_order_and_names(self):
        """字段名与顺序必须与契约 §2.1 完全一致。"""
        self.assertEqual(
            [f.name for f in dataclasses.fields(GenerateConfig)],
            ["ops", "ranges", "question_type", "max_value", "require_carry",
             "require_borrow", "exact_division", "allow_remainder",
             "parentheses", "count"],
        )

    def test_field_types(self):
        types = {f.name: f.type for f in dataclasses.fields(GenerateConfig)}
        self.assertEqual(types["ops"], "set[Op]")
        self.assertEqual(types["ranges"], "list[RangeId]")
        self.assertEqual(types["question_type"], "QuestionType")
        self.assertEqual(types["max_value"], "int | None")

    def test_required_fields_have_no_default(self):
        """无默认值字段必须排在带默认值字段之前，否则 Python 直接语法错误。"""
        fields = dataclasses.fields(GenerateConfig)
        seen_default = False
        for f in fields:
            has_default = (f.default is not dataclasses.MISSING
                           or f.default_factory is not dataclasses.MISSING)
            if has_default:
                seen_default = True
            else:
                self.assertFalse(
                    seen_default,
                    f"必需字段 {f.name} 出现在带默认值字段之后",
                )

    def test_defaults(self):
        cfg = GenerateConfig(ops={Op.ADD}, ranges=[RangeId.R1],
                             question_type=QuestionType.ORAL)
        self.assertIsNone(cfg.max_value)
        for name in ("require_carry", "require_borrow", "exact_division",
                     "allow_remainder", "parentheses"):
            self.assertIs(getattr(cfg, name), False, name)
        self.assertEqual(cfg.count, 20)

    def test_is_frozen(self):
        cfg = GenerateConfig(ops={Op.ADD}, ranges=[RangeId.R1],
                             question_type=QuestionType.ORAL)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.count = 5  # type: ignore[misc]


class TestQuestionContract(unittest.TestCase):
    def test_field_order_and_names(self):
        self.assertEqual(
            [f.name for f in dataclasses.fields(Question)],
            ["expr", "display", "answer", "steps", "difficulty", "tags", "signature"],
        )

    def test_is_frozen(self):
        q = Question(expr="1 + 1", display="1 + 1 = ____", answer="2",
                     steps=[], difficulty=1, tags=["20以内加法"], signature="1+1")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            q.answer = "3"  # type: ignore[misc]


class TestVerdictContract(unittest.TestCase):
    def test_field_order_and_names(self):
        self.assertEqual([f.name for f in dataclasses.fields(Verdict)],
                         ["is_correct", "normalized_input"])


class TestEnumContract(unittest.TestCase):
    def test_op_values_are_display_operators(self):
        self.assertEqual(Op.ADD.value, "+")
        self.assertEqual(Op.SUB.value, "-")
        self.assertEqual(Op.MUL.value, "×")
        self.assertEqual(Op.DIV.value, "÷")

    def test_range_ids_are_r1_to_r7(self):
        self.assertEqual([r.value for r in RangeId],
                         ["R1", "R2", "R3", "R4", "R5", "R6", "R7"])

    def test_question_types(self):
        self.assertEqual([t.value for t in QuestionType], ["oral", "stepwise", "fill"])


class TestExceptionContract(unittest.TestCase):
    def test_config_error_is_value_error(self):
        self.assertTrue(issubclass(ConfigError, ValueError))

    def test_insufficient_questions_is_runtime_error(self):
        self.assertTrue(issubclass(InsufficientQuestions, RuntimeError))


if __name__ == "__main__":
    unittest.main()
