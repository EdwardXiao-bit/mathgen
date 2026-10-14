"""task-4 独立复核：D46 / D47 / D48 / D49（契约 §2.1 / §2.3 新增冻结条款）。

验收对象：git `7e0f29b263982442c15561832b71a2bbd2781ee9`（main，已推送）。

**本文件不复用实现方的任何测试**：D46 用测试侧自写的「token 级左结合折叠重演器」
（harness.fold_sequence / step_order_violations）独立判定，与 ExprInfo 的 AST 求值是
两条互不相干的路径。

| 条款 | 复核内容 |
| --- | --- |
| D46 | steps 严格左结合；R1/R2 每一步中间值与最终结果都不超上界；越界题不得生成 |
| D47 | `R5 + STEPWISE + {×,÷} + 无括号` 多 seed 稳定出满 count；乘法位数组合顺序无关 |
| D48 | `from app.engine import generate` 得到函数（包根 re-export） |
| D49 | 除法不做顺序交换；`regenerate_one` 的 seq 越界抛 `ConfigError` |
"""
import inspect
import types
import unittest

if __package__:
    from .harness import (load_env, describe, call_generate, call_grade, ExprInfo,
                          question_violations, representative_configs,
                          fold_sequence, step_order_violations, last_int_tokens,
                          answer_number)
else:
    from harness import (load_env, describe, call_generate, call_grade, ExprInfo,
                         question_violations, representative_configs,
                         fold_sequence, step_order_violations, last_int_tokens,
                         answer_number)

SEED = 20261014
SEEDS = (1, 2, 3, 5, 7, 11, 20261014, 987654321)

#: D50：有数值上界的档位（R7 的界来自 config.max_value）。R6/R3/R4/R5 **无上界**。
CAPPED_RANGES = {"R1": 20, "R2": 100}


def _mk(env, ops, ranges, count, parentheses=False, question_type=None, max_value=None):
    return env.GenerateConfig(
        ops=set(ops), ranges=list(ranges),
        question_type=question_type if question_type is not None
        else env.QuestionType.STEPWISE,
        parentheses=parentheses, count=count, max_value=max_value)


def _upper_bound(config):
    """按 D50 返回该 config 的（单一）数值上界；无上界返回 None。

    D50：R1 = 20，R2 = 100，R7 = `max_value`；**R6 / R3 / R4 / R5 不设数值上界**。
    多选档位是并集语义，不能用 `min` 当上界 —— 直接返回 None（不做上界断言）。
    """
    if len(config.ranges) != 1:
        return None
    rid = str(getattr(config.ranges[0], "value", config.ranges[0]))
    if rid == "R7":
        return config.max_value
    return CAPPED_RANGES.get(rid)


class TestD46ReducerSelfCheck(unittest.TestCase):
    """先证明重演器本身是对的（否则"通过"没有意义）。"""

    def test_reducer_folds_left_to_right_with_precedence(self):
        cases = {
            "8 + 11 - 10": [(19, [10, 19]), (9, [9])],
            "1 + 49 × 2": [(98, [1, 98]), (99, [99])],
            "18 × 2 × 2": [(36, [2, 36]), (72, [72])],
            "(12 + 8) × 3": [(20, [3, 20]), (60, [60])],
        }
        for expr, expected in cases.items():
            with self.subTest(expr=expr):
                self.assertEqual(fold_sequence(expr), expected)

    def test_reducer_rejects_wrong_association(self):
        # 右结合的写法（把 52 - 17 先算）与左结合折叠不符，必须被抓到
        self.assertTrue(step_order_violations("53 + 52 - 17", ["53 + 52 - 17", "53 + 35", "88"]))
        self.assertEqual(step_order_violations("53 + 52 - 17", ["53 + 52 - 17", "105 - 17", "88"]), [])


class TestD46StepsStrictlyLeftToRight(unittest.TestCase):
    """D46 ①：`steps` 必须左结合（从左到右）。"""

    def _configs(self, env):
        return [
            ("R1 加减 count=8", _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R1], 8)),
            ("R1 加减 count=15", _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R1], 15)),
            ("R2 加减 count=12", _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R2], 12)),
            ("R2 加减 count=20", _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R2], 20)),
            ("R1 加+括号 count=6", _mk(env, (env.Op.ADD,), [env.RangeId.R1], 6,
                                      parentheses=True)),
            ("R2 加减 count=18", _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R2], 18)),
            # 混合优先级：先乘除后加减 + 同级从左到右（两条规则必须同时成立）
            ("R2 加减乘 count=8", _mk(env, (env.Op.ADD, env.Op.MUL), [env.RangeId.R2], 8)),
            # R6 无上界（D50），但仍必须左结合、操作数 ≥ 4 位
            ("R6 加减 count=10", _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R6], 10)),
        ]

    def test_steps_expand_strictly_left_to_right(self):
        env = load_env()
        checked = 0
        for label, config in self._configs(env):
            for seed in SEEDS:
                paper = call_generate(self, env, config, seed)
                with self.subTest(label=label, seed=seed, config=describe(config)):
                    self.assertEqual(len(paper), config.count)
                for index, question in enumerate(paper):
                    violations = step_order_violations(question.expr, question.steps)
                    checked += 1
                    self.assertEqual(
                        violations, [],
                        f"[{label}] seed={seed} 第 {index} 题 steps 不是左结合递等式"
                        f"（D46 违反）：\n  expr={question.expr!r} steps={question.steps!r}\n  "
                        + "\n  ".join(violations))
        self.assertGreaterEqual(checked, 60, f"只检查了 {checked} 道题，覆盖量不足")

    def test_every_fold_value_within_dang_wei_bound(self):
        """D46 + D50：**有上界**的档位（R1=20 / R2=100）每一步中间值与最终结果都不超上界。

        R6 与 R3/R4/R5 按 D50 **无上界**，本用例不做上界断言（由 R6 专属用例覆盖）。
        """
        env = load_env()
        checked = 0
        capped_labels = []
        for label, config in self._configs(env):
            limit = _upper_bound(config)
            if limit is None:
                continue
            capped_labels.append(label)
            for seed in SEEDS:
                paper = call_generate(self, env, config, seed)
                for index, question in enumerate(paper):
                    folds = fold_sequence(question.expr)
                    values = [value for value, _remaining in folds]
                    checked += 1
                    bad = [v for v in values if not 0 <= v <= limit]
                    self.assertEqual(
                        bad, [],
                        f"[{label}] seed={seed} 第 {index} 题按左到右折叠出现越界中间值 "
                        f"{bad}（上界 {limit}）—— 这类题按 D46 ③ 不得生成：\n"
                        f"  expr={question.expr!r} steps={question.steps!r} folds={values}")
                    self.assertEqual(
                        values[-1], int(answer_number(question.answer)),
                        f"[{label}] 第 {index} 题折叠终点与 answer 不一致："
                        f"{values[-1]} vs {question.answer!r}")
        self.assertIn("R1 加减 count=8", capped_labels)
        self.assertIn("R2 加减 count=20", capped_labels)
        self.assertGreaterEqual(checked, 60, f"只检查了 {checked} 道题，覆盖量不足")

    def test_r7_fold_values_within_max_value(self):
        """D50：R7 的上界就是老师填的 `max_value`，中间值与最终结果都不得超。"""
        env = load_env()
        checked = 0
        for max_value in (20, 50, 999):
            config = _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R7], 10,
                         max_value=max_value)
            for seed in SEEDS:
                paper = call_generate(self, env, config, seed)
                self.assertEqual(len(paper), config.count)
                for index, question in enumerate(paper):
                    values = [v for v, _r in fold_sequence(question.expr)]
                    checked += 1
                    bad = [v for v in values if not 0 <= v <= max_value]
                    self.assertEqual(
                        bad, [],
                        f"R7(max_value={max_value}) seed={seed} 第 {index} 题越界 {bad}："
                        f"expr={question.expr!r} steps={question.steps!r}")
        self.assertGreaterEqual(checked, 60, f"只检查了 {checked} 道题，覆盖量不足")

    def test_r6_has_no_upper_bound_only_operand_floor(self):
        """D50：R6 **无上界** —— 只断言「操作数 ≥ 4 位」+ 左结合，不断言结果大小。"""
        env = load_env()
        config = _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R6], 10)
        self.assertIsNone(_upper_bound(config), "R6 不应有数值上界（D50）")
        checked = 0
        for seed in SEEDS:
            paper = call_generate(self, env, config, seed)
            self.assertEqual(len(paper), config.count)
            for index, question in enumerate(paper):
                checked += 1
                self.assertEqual(
                    step_order_violations(question.expr, question.steps), [],
                    f"R6 seed={seed} 第 {index} 题 steps 不是左结合：{question.steps!r}")
                info = ExprInfo(question.expr)
                for operand in info.operands:
                    self.assertGreaterEqual(
                        operand, 1000,
                        f"R6 只要求操作数 ≥ 四位数（D50）：expr={question.expr!r}")
        self.assertGreaterEqual(checked, 60, f"只检查了 {checked} 道题，覆盖量不足")

    def test_r6_five_digit_result_is_legal_under_d50(self):
        """D50：R6 无上界 —— `1053 + 5408 + 9112 = 15573` 必须被判为**合法**。"""
        env = load_env()
        config = _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R6], 1)
        question = env.Question(
            expr="1053 + 5408 + 9112", display="1053 + 5408 + 9112", answer="15573",
            steps=["1053 + 5408 + 9112", "6461 + 9112", "15573"],
            difficulty=2, tags=["四位数加法"], signature="1053+5408+9112")
        self.assertEqual(
            question_violations(env, config, question, 0), [],
            "R6 的五位数结果（15573）按 D50 是合法的，检查器不得报越界")
        self.assertEqual(step_order_violations(question.expr, question.steps), [],
                         "该题的 steps 是标准左结合，不得被判为右结合")
        self.assertTrue(any(v > 9999 for v, _r in fold_sequence(question.expr)),
                        "本用例锚点失效：该题应存在 >9999 的折叠值")

    def test_regenerate_one_also_produces_left_associative_steps(self):
        """FR-E9 换题路径不得绕过 D46（新题同样要左结合、且每步不越界）。"""
        env = load_env()
        config = _mk(env, (env.Op.ADD, env.Op.SUB), [env.RangeId.R2], 15)
        paper = call_generate(self, env, config, SEED)
        for seq in (0, 7, 14):
            with self.subTest(seq=seq):
                question = env.regenerate_one(config, SEED, seq, paper)
                self.assertEqual(step_order_violations(question.expr, question.steps), [],
                                 f"regenerate_one(seq={seq}) 产出的 steps 不是左结合："
                                 f"{question.expr!r} {question.steps!r}")
                folds = fold_sequence(question.expr)
                bad = [v for v, _r in folds if not 0 <= v <= 100]
                self.assertEqual(bad, [], f"regenerate_one(seq={seq}) 产出越界中间值 {bad}")

    def test_no_right_associative_evasion_across_seeds(self):
        """专门守住 D46 的"不允许改右结合规避"：全量扫描里不得出现右结合 steps。"""
        env = load_env()
        offenders = []
        for label, config in self._configs(env):
            for seed in SEEDS:
                paper = call_generate(self, env, config, seed)
                for index, question in enumerate(paper):
                    if step_order_violations(question.expr, question.steps):
                        offenders.append((label, seed, index, question.expr, question.steps))
        self.assertEqual(offenders, [], f"发现右结合（或行数不符）的 steps：{offenders[:3]}")


class TestD47RangeOrderIndependence(unittest.TestCase):
    """D47（经 D49 修正）：乘法位数组合顺序无关；R5 无括号混合乘除脱式必须能出题。"""

    def test_r5_stepwise_muldiv_without_parentheses_generates_full_count(self):
        env = load_env()
        config = _mk(env, (env.Op.MUL, env.Op.DIV), [env.RangeId.R5], 12)
        total = 0
        div_then_times_3digit = 0
        for seed in SEEDS:
            with self.subTest(seed=seed, config=describe(config)):
                paper = call_generate(self, env, config, seed)     # 不得抛 InsufficientQuestions
                self.assertEqual(len(paper), config.count)
                for index, question in enumerate(paper):
                    violations = question_violations(env, config, question, index)
                    self.assertEqual(violations, [], f"seed={seed} " + "；".join(violations))
                    info = ExprInfo(question.expr)
                    # 形态一：「÷ 得数再乘三位数」——三位数÷两位数 的商再乘三位数
                    if any(len(str(q)) == 2 for _a, _b, q, _r in info.divisions):
                        for op, lv, rv in info.nodes:
                            if op == "×" and len(str(rv)) == 3:
                                div_then_times_3digit += 1
                                break
                total += len(paper)
        self.assertEqual(total, 12 * len(SEEDS),
                         "R5 无括号混合乘除脱式未稳定出满 count（D47 要求可出题）")
        self.assertGreater(div_then_times_3digit, 0,
                           "未观察到「÷ 得数再乘三位数」的形态，D47 的判定可能落空")

    def test_multiplication_digit_combo_is_order_insensitive(self):
        """`两位数 × 三位数` 与 `三位数 × 两位数` 都必须被档位接受（D47）。"""
        env = load_env()
        T = load_env()
        # 检查器层面：手工构造两种顺序，都应通过
        config = _mk(env, (env.Op.MUL,), [env.RangeId.R5], 1,
                     question_type=env.QuestionType.ORAL)
        for expr, answer in (("123 × 45", "5535"), ("45 × 123", "5535")):
            with self.subTest(expr=expr):
                question = T.Question(expr=expr, display=expr, answer=answer, steps=[],
                                      difficulty=3, tags=["三位数乘两位数"], signature=expr)
                self.assertEqual(question_violations(T, config, question, 0), [])

        # 实现层面：多 seed 实际产出必须全部满足「位数多重集 = {2,3}」
        for seed in SEEDS:
            paper = call_generate(self, env, config, seed)
            for index, question in enumerate(paper):
                with self.subTest(seed=seed, index=index, expr=question.expr):
                    info = ExprInfo(question.expr)
                    for op, lv, rv in info.nodes:
                        if op == "×":
                            self.assertEqual(
                                sorted([len(str(lv)), len(str(rv))]), [2, 3],
                                f"R5 乘法位数组合应为 {{2,3}}（顺序无关），"
                                f"实得 {lv} × {rv}")

    def test_r4_multiplication_accepts_one_by_two_digits(self):
        env = load_env()
        T = env
        config = _mk(env, (env.Op.MUL,), [env.RangeId.R4], 1,
                     question_type=env.QuestionType.ORAL)
        for expr in ("12 × 8", "8 × 12"):
            with self.subTest(expr=expr):
                question = T.Question(expr=expr, display=expr, answer="96", steps=[],
                                      difficulty=2, tags=["两位数乘一位数"], signature=expr)
                self.assertEqual(question_violations(T, config, question, 0), [])

    def test_division_does_not_swap_operands(self):
        """D49①：除法不接受顺序交换 —— `两位数 ÷ 三位数` 应被档位拒绝。"""
        env = load_env()
        config = _mk(env, (env.Op.DIV,), [env.RangeId.R4], 1,
                     question_type=env.QuestionType.ORAL)
        swapped = env.Question(expr="12 ÷ 345", display="12 ÷ 345", answer="0",
                               steps=[], difficulty=1, tags=["t"], signature="12÷345")
        self.assertTrue(question_violations(env, config, swapped, 0),
                        "两位数 ÷ 三位数 不应被 R4 接受（D49①：除法不做顺序交换）")


class TestD48PackageRootExports(unittest.TestCase):
    """D48：`from app.engine import generate` 必须得到**函数**。"""

    def test_engine_package_reexports_functions(self):
        import importlib
        import app.engine as engine_pkg
        # 注意：`import app.engine.generate` 会绑定到**函数**（D48 生效后包属性被覆盖），
        # 这里显式用 importlib 取子模块对象。
        gen_module = importlib.import_module("app.engine.generate")

        for name in ("generate", "regenerate_one"):
            with self.subTest(name=name):
                obj = getattr(engine_pkg, name, None)
                self.assertTrue(callable(obj),
                                f"app.engine.{name} 必须可调用，实得 {type(obj).__name__}")
                self.assertNotIsInstance(obj, types.ModuleType,
                                         f"app.engine.{name} 仍是模块，未 re-export 函数")
                self.assertTrue(inspect.isfunction(obj),
                                f"app.engine.{name} 应为函数，实得 {type(obj).__name__}")
                # 与实现模块里的同名对象必须一致
                self.assertIs(obj, getattr(gen_module, name))

        from app.engine import generate, regenerate_one   # noqa: F401  —— 直接导入形态
        self.assertTrue(callable(generate))
        self.assertTrue(callable(regenerate_one))

    def test_grading_package_reexports_grade(self):
        import app.grading as grading_pkg
        self.assertTrue(callable(getattr(grading_pkg, "grade", None)))
        self.assertNotIsInstance(getattr(grading_pkg, "grade"), types.ModuleType)


class TestD49RegenerateSeqOutOfRange(unittest.TestCase):
    """D49②：`regenerate_one` 的 `seq` 越界抛 `ConfigError`，不是 `IndexError`。"""

    def test_out_of_range_seq_raises_config_error(self):
        env = load_env()
        _label, config = representative_configs(env)[0]
        paper = call_generate(self, env, config, SEED)
        size = len(paper)
        for seq in (-1, -100, size, size + 1, size + 10, 10 ** 6):
            with self.subTest(seq=seq, size=size):
                with self.assertRaises(env.ConfigError) as ctx:
                    env.regenerate_one(config, SEED, seq, paper)
                self.assertNotIsInstance(ctx.exception, IndexError,
                                         "seq 越界必须抛 ConfigError 而不是 IndexError（D49②）")

    def test_empty_existing_list_is_out_of_range(self):
        env = load_env()
        _label, config = representative_configs(env)[0]
        with self.assertRaises(env.ConfigError):
            env.regenerate_one(config, SEED, 0, [])

    def test_in_range_seq_still_works(self):
        env = load_env()
        _label, config = representative_configs(env)[0]
        paper = call_generate(self, env, config, SEED)
        for seq in (0, len(paper) - 1):
            with self.subTest(seq=seq):
                question = env.regenerate_one(config, SEED, seq, paper)
                self.assertIsInstance(question, env.Question)


if __name__ == "__main__":
    unittest.main()
