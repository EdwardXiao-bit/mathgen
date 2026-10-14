"""顶层 `generate` / `regenerate_one` 测试（FR-E7–E9，契约 §2.3）。

覆盖 docs/06-测试方案.md §4 的四类：属性测试、确定性、单题重生成、异常路径。
所有断言都用测试侧独立解析器（`_probe`）复核，不复用引擎的求值器。
"""
from __future__ import annotations

import random
import time
import unittest

from app.engine.generate import generate, regenerate_one
from app.engine.ranges import bounds_for, spec_for
from app.engine.types import (
    GenerateConfig,
    InsufficientQuestions,
    Op,
    QuestionType,
    RangeId,
)

from . import _probe

_OP_BY_VALUE = {op.value: op for op in Op}
_ALL_OPS = [Op.ADD, Op.SUB, Op.MUL, Op.DIV]
_ALL_RANGES = list(RangeId)

PROPERTY_CONFIGS = 200
PROPERTY_SEEDS = 3


def _random_config(rng: random.Random) -> GenerateConfig:
    """生成一组**合法**随机配置（`validate` 必须放行）。"""
    ops = set(rng.sample(_ALL_OPS, rng.randint(1, len(_ALL_OPS))))
    ranges = rng.sample(_ALL_RANGES, rng.randint(1, len(_ALL_RANGES)))
    question_type = rng.choice(list(QuestionType))
    parentheses = rng.random() < 0.4
    exact_division = rng.random() < 0.3
    allow_remainder = (not exact_division) and rng.random() < 0.3

    if question_type is QuestionType.STEPWISE and len(ops) < 2:
        parentheses = True
    if question_type is QuestionType.FILL:
        parentheses = False

    max_value = None
    if RangeId.R7 in ranges:
        max_value = rng.choice([10, 20, 50, 99, 200, 999])

    return GenerateConfig(
        ops=ops,
        ranges=ranges,
        question_type=question_type,
        max_value=max_value,
        require_carry=rng.random() < 0.3,
        require_borrow=rng.random() < 0.3,
        exact_division=exact_division,
        allow_remainder=allow_remainder,
        parentheses=parentheses,
        count=rng.randint(1, 20),
    )


class _Checker(unittest.TestCase):
    """把「一道题必须满足的全部约束」写成可复用的断言。"""

    def assert_question_ok(self, config, question, label=""):
        where = "%s expr=%r display=%r" % (label, question.expr, question.display)
        tree = _probe.parse(question.expr)
        specs = [spec_for(rid, config.max_value) for rid in config.ranges]
        op_values = {op.value for op in config.ops}

        # ① 运算类型必须来自 config.ops
        for char, _, _, _ in _probe.nodes(tree):
            self.assertIn(char, op_values, "出现了未选择的运算：%s（%s）" % (char, where))

        # ② 每个操作数与结果都必须落在某个已选档位的边界内
        for char, left, right, value in _probe.nodes(tree):
            op = _OP_BY_VALUE[char]
            ok = any(bounds_for(spec, op).holds(left, right, value) for spec in specs)
            self.assertTrue(ok, "越界：%d %s %d = %d（%s）" % (left, char, right, value, where))

        # ③ 特殊约束
        for char, left, right, value in _probe.nodes(tree):
            if char == "+" and config.require_carry:
                self.assertGreater(_probe.carry_count(left, right), 0,
                                   "要求进位却没有进位：%d + %d（%s）" % (left, right, where))
            if char == "-" and config.require_borrow:
                self.assertGreater(_probe.borrow_count(left, right), 0,
                                   "要求退位却没有退位：%d - %d（%s）" % (left, right, where))
            if char == "÷":
                self.assertGreater(right, 0, where)
                if config.exact_division:
                    self.assertEqual(left % right, 0, "exact_division 下未整除：%s" % where)
                if not config.allow_remainder:
                    self.assertEqual(left % right, 0,
                                     "未允许余数却有余数：%s" % where)

        # ④ 答案
        self._assert_answer(config, question, tree, where)

        # ⑤ 难度 / 标签 / 步骤容器
        self.assertGreaterEqual(question.difficulty, 1, where)
        self.assertLessEqual(question.difficulty, 5, where)
        self.assertTrue(question.tags, "tags 为空（%s）" % where)
        if config.question_type is QuestionType.STEPWISE:
            self.assertGreaterEqual(len(question.steps), 2, where)
        else:
            self.assertEqual(question.steps, [], where)

        # ⑥ 题型形态
        if config.question_type is QuestionType.FILL:
            self.assertEqual(question.display.count("□"), 1, where)
        else:
            self.assertNotIn("□", question.display, where)
        if config.question_type is QuestionType.STEPWISE:
            self.assertEqual(("(" in question.display), bool(config.parentheses), where)

    def _assert_answer(self, config, question, tree, where):
        answer = question.answer
        if config.question_type is QuestionType.ORAL:
            if "余" in answer:
                quotient, remainder = _probe.parse_remainder(answer)
                division = _probe.top_division(question.expr)
                self.assertIsNotNone(division, where)
                dividend, divisor = division
                self.assertEqual(quotient, dividend // divisor, where)
                self.assertEqual(remainder, dividend % divisor, where)
                self.assertLess(remainder, divisor, where)
            else:
                self.assertEqual(int(answer), _probe.evaluate(tree), where)
        elif config.question_type is QuestionType.STEPWISE:
            value = _probe.evaluate(tree)
            self.assertEqual(answer, str(value), where)
            self.assertEqual(question.steps[0], question.expr, where)
            self.assertEqual(question.steps[-1], str(value), where)
            for step in question.steps:
                self.assertEqual(_probe.eval_text(step), value,
                                 "脱式步骤 %r 与答案 %s 不一致（%s）" % (step, answer, where))
        else:   # FILL
            filled = question.display.replace("□", answer)
            self.assertTrue(_probe.equation_holds(filled),
                            "填空等式不成立：%r（%s）" % (filled, where))


class TestGenerateBasics(_Checker):
    def test_count_is_respected(self):
        for count in (1, 5, 20, 100):
            config = GenerateConfig(ops=set(_ALL_OPS), ranges=[RangeId.R2, RangeId.R5],
                                    question_type=QuestionType.ORAL, count=count)
            self.assertEqual(len(generate(config, 1)), count)

    def test_signatures_are_unique_within_a_paper(self):
        config = GenerateConfig(ops={Op.ADD, Op.SUB}, ranges=[RangeId.R1, RangeId.R2],
                                question_type=QuestionType.ORAL, count=60)
        questions = generate(config, 99)
        signatures = [q.signature for q in questions]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_typical_configs_are_all_valid(self):
        configs = [
            GenerateConfig(ops=set(_ALL_OPS), ranges=list(RangeId), max_value=1000,
                           question_type=QuestionType.ORAL, count=30),
            GenerateConfig(ops={Op.ADD, Op.MUL}, ranges=[RangeId.R2, RangeId.R4],
                           question_type=QuestionType.STEPWISE, count=20),
            GenerateConfig(ops={Op.ADD, Op.MUL}, ranges=[RangeId.R2], question_type=QuestionType.STEPWISE,
                           parentheses=True, count=20),
            GenerateConfig(ops={Op.SUB, Op.DIV}, ranges=[RangeId.R3, RangeId.R4],
                           question_type=QuestionType.FILL, count=20),
            GenerateConfig(ops={Op.DIV}, ranges=[RangeId.R7], max_value=50,
                           question_type=QuestionType.ORAL, allow_remainder=True, count=20),
            GenerateConfig(ops={Op.ADD}, ranges=[RangeId.R2], question_type=QuestionType.ORAL,
                           require_carry=True, count=20),
            GenerateConfig(ops={Op.SUB}, ranges=[RangeId.R6], question_type=QuestionType.ORAL,
                           require_borrow=True, count=10,
                           ),
        ]
        for index, config in enumerate(configs):
            for seed in (0, 7, 20261014):
                questions = generate(config, seed)
                self.assertEqual(len(questions), config.count)
                for seq, question in enumerate(questions):
                    self.assert_question_ok(config, question, "cfg%d seed%d seq%d" % (index, seed, seq))


class TestDeterminism(_Checker):
    """FR-E8（**冻结**）：同 (config, seed) 必得逐字段相同的结果，含顺序。"""

    CONFIGS = [
        GenerateConfig(ops=set(_ALL_OPS), ranges=list(RangeId), max_value=1000,
                       question_type=QuestionType.ORAL, count=25),
        GenerateConfig(ops={Op.ADD, Op.SUB, Op.MUL}, ranges=[RangeId.R2, RangeId.R4],
                       question_type=QuestionType.STEPWISE, count=15),
        GenerateConfig(ops={Op.MUL, Op.ADD}, ranges=[RangeId.R2], question_type=QuestionType.STEPWISE,
                       parentheses=True, count=12),
        GenerateConfig(ops={Op.ADD, Op.SUB, Op.DIV}, ranges=[RangeId.R1, RangeId.R3],
                       question_type=QuestionType.FILL, allow_remainder=True, count=18),
        GenerateConfig(ops={Op.ADD}, ranges=[RangeId.R7], max_value=30,
                       question_type=QuestionType.ORAL, require_carry=True, count=10),
    ]

    def test_same_seed_gives_identical_questions(self):
        for config in self.CONFIGS:
            for seed in (0, 1, 42, -7, 2 ** 40 + 3):
                first = generate(config, seed)
                second = generate(config, seed)
                self.assertEqual(first, second)
                self.assertEqual(
                    [(q.expr, q.display, q.answer, tuple(q.steps),
                      q.difficulty, tuple(q.tags), q.signature) for q in first],
                    [(q.expr, q.display, q.answer, tuple(q.steps),
                      q.difficulty, tuple(q.tags), q.signature) for q in second],
                )

    def test_identical_results_are_field_by_field(self):
        config = self.CONFIGS[1]
        first = generate(config, 2026)
        second = generate(config, 2026)
        self.assertEqual([q.expr for q in first], [q.expr for q in second])
        self.assertEqual([q.display for q in first], [q.display for q in second])
        self.assertEqual([q.answer for q in first], [q.answer for q in second])
        self.assertEqual([q.steps for q in first], [q.steps for q in second])
        self.assertEqual([q.tags for q in first], [q.tags for q in second])
        self.assertEqual([q.signature for q in first], [q.signature for q in second])
        self.assertEqual([q.difficulty for q in first], [q.difficulty for q in second])

    def test_different_seeds_give_different_papers(self):
        config = self.CONFIGS[0]
        papers = [tuple(q.signature for q in generate(config, seed))
                  for seed in range(6)]
        self.assertEqual(len(set(papers)), len(papers),
                         "不同 seed 应产生不同试卷（抽样 6 个）")

    def test_op_selection_order_is_independent_of_set_iteration(self):
        """`ops` 是 set，遍历顺序不稳定；实现必须先排序再使用。

        同一组 ops / ranges 用不同插入顺序构造两个 set，结果必须逐字段相同。
        """
        ranges = [RangeId.R2, RangeId.R3]
        forward = GenerateConfig(ops={Op.ADD, Op.SUB, Op.MUL, Op.DIV}, ranges=ranges,
                                 question_type=QuestionType.ORAL, count=16)
        backward = GenerateConfig(ops={Op.DIV, Op.MUL, Op.SUB, Op.ADD}, ranges=ranges,
                                  question_type=QuestionType.ORAL, count=16)
        self.assertEqual(generate(forward, 5), generate(backward, 5))


class TestRegenerateOne(_Checker):
    def _paper(self):
        config = GenerateConfig(ops={Op.ADD, Op.SUB, Op.MUL, Op.DIV},
                                ranges=[RangeId.R2, RangeId.R3, RangeId.R4],
                                question_type=QuestionType.ORAL, count=20)
        return config, generate(config, 314)

    def test_only_target_changes_and_stays_unique(self):
        config, existing = self._paper()
        for seq in (0, 5, 19):
            fresh = regenerate_one(config, 314, seq, existing)
            self.assertNotEqual(fresh.signature, existing[seq].signature,
                                "seq=%d 的新题与原题相同" % seq)
            others = {q.signature for index, q in enumerate(existing) if index != seq}
            self.assertNotIn(fresh.signature, others)
            self.assert_question_ok(config, fresh, "regenerate seq=%d" % seq)
            # 其余题逐字段不变（调用方只替换 seq 位置）
            updated = list(existing)
            updated[seq] = fresh
            self.assertEqual(updated[:seq], existing[:seq])
            self.assertEqual(updated[seq + 1:], existing[seq + 1:])

    def test_regenerate_is_deterministic(self):
        config, existing = self._paper()
        first = regenerate_one(config, 314, 3, existing)
        second = regenerate_one(config, 314, 3, existing)
        self.assertEqual(first, second)

    def test_regenerate_for_stepwise_and_fill(self):
        for config in (
            GenerateConfig(ops={Op.ADD, Op.MUL}, ranges=[RangeId.R2],
                           question_type=QuestionType.STEPWISE, count=10),
            GenerateConfig(ops={Op.ADD, Op.SUB}, ranges=[RangeId.R1, RangeId.R2],
                           question_type=QuestionType.FILL, count=10),
        ):
            existing = generate(config, 8)
            fresh = regenerate_one(config, 8, 4, existing)
            self.assertNotIn(fresh.signature, {q.signature for q in existing})
            self.assert_question_ok(config, fresh, "regenerate")

    def test_seq_out_of_range_is_rejected(self):
        config, existing = self._paper()
        for bad in (-1, len(existing), 999):
            with self.assertRaises(ValueError):
                regenerate_one(config, 314, bad, existing)
        with self.assertRaises(ValueError):
            regenerate_one(config, 314, 0, [])
        with self.assertRaises(ValueError):
            regenerate_one(config, 314, "0", existing)


class TestInsufficientQuestions(_Checker):
    def test_twenty_range_carry_hundred_items_is_insufficient(self):
        """docs/06-测试方案.md §4.4 的例子：20 以内 + 必须进位 + 100 题。"""
        config = GenerateConfig(ops={Op.ADD}, ranges=[RangeId.R1],
                                question_type=QuestionType.ORAL,
                                require_carry=True, count=100)
        started = time.perf_counter()
        with self.assertRaises(InsufficientQuestions):
            generate(config, 1)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 10.0, "无解组合耗时 %.2fs，必须尽快报错" % elapsed)

    def test_table_multiplication_space_is_exhausted_quickly(self):
        """表内乘法去重后只有 45 种，要 100 题必然不足。"""
        config = GenerateConfig(ops={Op.MUL}, ranges=[RangeId.R3],
                                question_type=QuestionType.ORAL, count=100)
        started = time.perf_counter()
        with self.assertRaises(InsufficientQuestions):
            generate(config, 5)
        self.assertLess(time.perf_counter() - started, 10.0)

    def test_seed_does_not_change_feasibility(self):
        config = GenerateConfig(ops={Op.ADD}, ranges=[RangeId.R1],
                                question_type=QuestionType.ORAL,
                                require_carry=True, count=100)
        for seed in (0, 1, 2, 3):
            with self.assertRaises(InsufficientQuestions):
                generate(config, seed)

    def test_insufficient_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            generate(GenerateConfig(ops={Op.MUL}, ranges=[RangeId.R3],
                                    question_type=QuestionType.ORAL, count=100), 5)


class TestPropertySweep(_Checker):
    """docs/06-测试方案.md §4.1：≥200 组随机合法 config × 3 个 seed。"""

    def test_random_configs_satisfy_every_constraint(self):
        rng = random.Random(20261014)
        checked_configs = 0
        skipped_configs = 0
        checked_questions = 0
        for _ in range(PROPERTY_CONFIGS):
            config = _random_config(rng)
            generated_any = False
            for seed in range(PROPERTY_SEEDS):
                try:
                    questions = generate(config, seed)
                except InsufficientQuestions:
                    continue
                generated_any = True
                self.assertEqual(len(questions), config.count)
                signatures = set()
                for seq, question in enumerate(questions):
                    self.assert_question_ok(
                        config, question,
                        "seed=%d seq=%d config=%r" % (seed, seq, config))
                    self.assertNotIn(question.signature, signatures,
                                     "单卷内签名重复：%s" % question.signature)
                    signatures.add(question.signature)
                checked_questions += len(questions)
            if generated_any:
                checked_configs += 1
            else:
                skipped_configs += 1

        self.assertGreaterEqual(checked_configs, 150,
                                "通过校验的配置过少：%d（跳过 %d）" % (checked_configs, skipped_configs))
        self.assertGreaterEqual(checked_questions, 3000)


if __name__ == "__main__":
    unittest.main()
