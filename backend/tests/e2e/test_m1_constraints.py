"""§4.1 约束完备（FR-E1–E6、E7、E10、E11、E12）：随机合法 config × 固定种子。

对**随机组合**的合法 `GenerateConfig` 批量断言：

  ① 题量恰为 config.count（FR-E6）
  ② 每道题的运算类型 ∈ config.ops（FR-E1）
  ③ 每个操作数与结果都落在选中档位 / max_value 范围内（FR-E2/E3）
  ④ 特殊约束：require_carry（加法个位和 ≥10）、require_borrow（减法个位不够减）、
     exact_division（整除）、parentheses（含括号）（FR-E4）
  ⑤ 单卷内 signature 无重复（FR-E7）
  ⑥ difficulty ∈ 1..5（FR-E10）
  ⑦ tags 非空（FR-E11）
  ⑧ STEPWISE 时 steps 长度 ≥2；非 STEPWISE 时 steps 为空（FR-E12）
  ⑨ FILL 时 display 含 □ 且 expr 不含 □

检查器是测试侧独立实现（harness.ExprInfo），不复用实现方内部函数。
"""
import unittest

if __package__:
    from .harness import (load_env, random_configs, representative_configs,
                          describe, call_generate, paper_violations_all,
                          step_value_violations)
else:
    from harness import (load_env, random_configs, representative_configs,
                         describe, call_generate, paper_violations_all,
                         step_value_violations)

SEED = 20261014


class TestConstraintCompleteness(unittest.TestCase):
    def test_representative_configs_all_questions_conform(self):
        """6 个代表性配置（含 R5 带进位 / 脱式含括号 / 填空 / R7）逐题全约束。"""
        env = load_env()
        for label, config in representative_configs(env):
            with self.subTest(label=label, config=describe(config)):
                paper = call_generate(self, env, config, SEED)
                violations = paper_violations_all(env, config, paper)
                self.assertEqual(
                    violations, [],
                    f"[{label}] 生成结果不满足契约约束（docs/06 §4.1）：\n  "
                    + "\n  ".join(violations[:12])
                    + f"\n  config = {describe(config)}")

    def test_random_legal_configs_all_questions_conform(self):
        """≥50 组随机合法 config × 2 个 seed，逐题全约束（docs/06 §4.1）。"""
        env = load_env()
        configs = random_configs(env, count=60, seed=20261014)
        self.assertGreaterEqual(len(configs), 50, "任务要求 ≥50 组随机合法 config")

        checked_questions = 0
        for index, config in enumerate(configs):
            for seed in (11, SEED):
                with self.subTest(config_index=index, seed=seed, config=describe(config)):
                    try:
                        paper = call_generate(self, env, config, seed)
                    except env.InsufficientQuestions as exc:
                        self.fail(
                            "合法且题面空间充足的随机配置抛出了 InsufficientQuestions；"
                            "要么是回溯上限设得太小，要么是配置空间估计过保守。\n"
                            f"  config = {describe(config)}\n  seed = {seed}\n  异常 = {exc!r}")
                    violations = paper_violations_all(env, config, paper)
                    checked_questions += len(paper)
                    self.assertEqual(
                        violations, [],
                        f"随机配置生成的题目违反契约约束：\n  "
                        + "\n  ".join(violations[:12])
                        + f"\n  config = {describe(config)}\n  seed = {seed}")

        self.assertGreaterEqual(
            checked_questions, 200,
            f"本用例实际只检查了 {checked_questions} 道题，覆盖量不足（P1：门禁不能空转）")

    def test_returned_count_equals_config_count(self):
        """FR-E6：题量必须恰好等于 config.count，不多不少。"""
        env = load_env()
        cases = representative_configs(env)
        for label, config in cases:
            with self.subTest(label=label):
                paper = call_generate(self, env, config, SEED)
                self.assertEqual(
                    len(paper), config.count,
                    f"[{label}] 题量应为 {config.count}，实得 {len(paper)}")

        # 边界：count=1 与 count=100（用题面空间充足的 R2，避免误触"题不足"）
        base = dict(ops={env.Op.ADD, env.Op.SUB}, ranges=[env.RangeId.R2],
                    question_type=env.QuestionType.ORAL)
        for count in (1, 100):
            with self.subTest(count=count):
                config = env.GenerateConfig(count=count, **base)
                paper = call_generate(self, env, config, SEED)
                self.assertEqual(len(paper), count)

    def test_stepwise_step_values_within_range(self):
        """脱式题 steps 里打印出来的每一个数值都必须在档位上限内。

        「`20 以内` 的脱式，答案版上不得出现 `= 26 - 12` 这种越界数字」——
        这一条与实现选择的求值顺序无关，因此可以作为硬断言。

        （曾经写过一版「对 expr 从左到右求值、要求中间值也 ≤ 上限」，
        实测是**假阳性**：引擎会把 `53 + 52 - 17` 写成 `= 53 + 35`，
        它自己的中间值并未越界。见验收报告 F-2。）
        """
        env = load_env()
        for label, config in representative_configs(env):
            paper = call_generate(self, env, config, SEED)
            violations = step_value_violations(env, config, paper)
            with self.subTest(label=label, config=describe(config)):
                self.assertEqual(violations, [], f"[{label}] " + "；".join(violations))
        for index, config in enumerate(random_configs(env, count=60, seed=20261014)[:20]):
            try:
                paper = call_generate(self, env, config, SEED)
            except env.InsufficientQuestions:
                continue          # 由 test_random_legal_configs_all_questions_conform 报告
            violations = step_value_violations(env, config, paper)
            with self.subTest(config_index=index, config=describe(config)):
                self.assertEqual(violations, [], f"随机配置 #{index} " + "；".join(violations))

    def test_only_selected_operations_appear(self):
        """FR-E1：只出现 config.ops 里勾选的运算。"""
        env = load_env()
        config = env.GenerateConfig(
            ops={env.Op.ADD}, ranges=[env.RangeId.R2],
            question_type=env.QuestionType.ORAL, count=20)
        paper = call_generate(self, env, config, SEED)
        for index, question in enumerate(paper):
            with self.subTest(index=index, expr=question.expr):
                self.assertNotIn("×", question.expr)
                self.assertNotIn("÷", question.expr)
                self.assertNotIn("-", question.expr)


if __name__ == "__main__":
    unittest.main()
