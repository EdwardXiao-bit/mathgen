"""§4.2 确定性（FR-E8，**冻结契约**）+ §4.3 单题重生成（FR-E9）。

- §4.2：同 `(config, seed)` 两次生成**逐字段相同**（含顺序）；不同 seed 结果不同（抽样，防退化实现）。
- §4.3：`regenerate_one` 只换掉指定 `seq`，其余题逐字段不变；新题不与其余题重签名；新题仍满足全部约束。

只经公开入口 `generate` / `regenerate_one` 断言，不 import 实现内部函数。
"""
import unittest

if __package__:
    from .harness import (load_env, representative_configs, snapshot, diff_report,
                          describe, call_generate, question_violations)
else:
    from harness import (load_env, representative_configs, snapshot, diff_report,
                         describe, call_generate, question_violations)

SEED = 20261014


class TestDeterminism(unittest.TestCase):
    """docs/06-测试方案.md §4.2 —— M1 门禁的核心：只要它通过，「可复现」才是真的。"""

    def test_same_config_and_seed_are_field_identical(self):
        env = load_env()
        cases = representative_configs(env)
        self.assertGreaterEqual(len(cases), 5, "§4.2 要求 ≥5 个有代表性的 config")

        for label, config in cases:
            with self.subTest(label=label, config=describe(config)):
                first = snapshot(call_generate(self, env, config, SEED))
                second = snapshot(call_generate(self, env, config, SEED))
                report = diff_report(first, second)
                self.assertEqual(
                    report, "",
                    f"[{label}] 同 (config, seed) 两次生成**不一致**（FR-E8 违反）：{report}\n"
                    f"  config = {describe(config)}\n  seed = {SEED}")
                self.assertEqual(first, second)

    def test_different_seeds_produce_different_papers(self):
        """抽样断言，防止「忽略 seed 的退化实现」也能通过确定性测试。"""
        env = load_env()
        cases = representative_configs(env)
        # 取题面空间较大的配置抽样（连 R1 20 题的空间也远大于 20!）
        sample_indexes = [0, 1, 2, 5]
        for index in sample_indexes:
            label, config = cases[index]
            with self.subTest(label=label):
                one = snapshot(call_generate(self, env, config, 1))
                two = snapshot(call_generate(self, env, config, 2))
                self.assertNotEqual(
                    one, two,
                    f"[{label}] seed=1 与 seed=2 得到完全相同的卷子 —— "
                    f"generate 可能没有真正使用 seed（退化实现）\n  config = {describe(config)}")

    def test_generate_does_not_mutate_config(self):
        """config 是值对象；容器字段（ops 是 set、ranges 是 list）不得被实现就地改动。"""
        env = load_env()
        for label, config in representative_configs(env):
            with self.subTest(label=label):
                before = (frozenset(config.ops), tuple(config.ranges), config.question_type,
                          config.max_value, config.require_carry, config.require_borrow,
                          config.exact_division, config.allow_remainder,
                          config.parentheses, config.count)
                call_generate(self, env, config, SEED)
                after = (frozenset(config.ops), tuple(config.ranges), config.question_type,
                         config.max_value, config.require_carry, config.require_borrow,
                         config.exact_division, config.allow_remainder,
                         config.parentheses, config.count)
                self.assertEqual(before, after,
                                 f"[{label}] generate 就地改动了传入的 config（契约 §2.3：纯函数）")


class TestRegenerateOne(unittest.TestCase):
    """docs/06-测试方案.md §4.3。"""

    def test_regenerate_one_only_replaces_target_seq(self):
        env = load_env()
        label, config = representative_configs(env)[0]      # R1 口算，count=20
        self.assertEqual(config.count, 20, "§4.3 以 20 题为样本")
        paper = call_generate(self, env, config, SEED)
        before = snapshot(paper)

        for seq in (0, 5, 19):
            with self.subTest(seq=seq, config=describe(config)):
                new_question = env.regenerate_one(config, SEED, seq, paper)
                self.assertIsInstance(
                    new_question, env.Question,
                    f"regenerate_one 必须返回 Question，实得 {type(new_question)!r}")

                after = list(paper)
                after[seq] = new_question
                after_snap = snapshot(after)

                # ① 仅该 seq 变化，其余题逐字段不变
                for index in range(len(paper)):
                    if index == seq:
                        continue
                    self.assertEqual(
                        before[index], after_snap[index],
                        f"regenerate_one(seq={seq}) 不应改动第 {index} 题")
                self.assertNotEqual(
                    before[seq], after_snap[seq],
                    f"regenerate_one(seq={seq}) 未替换该题（§4.3 ① 要求该题发生变化）")

                # ② 新题签名与其余题均不重复
                others = [after_snap[i]["signature"]
                          for i in range(len(after_snap)) if i != seq]
                self.assertNotIn(
                    after_snap[seq]["signature"], others,
                    f"regenerate_one(seq={seq}) 的新题与卷内其他题签名重复（FR-E7）")

                # ③ 新题仍满足 config 的全部约束
                violations = question_violations(env, config, new_question, seq)
                self.assertEqual(
                    violations, [],
                    f"[{label}] regenerate_one(seq={seq}) 的新题不满足约束：{violations}\n"
                    f"  config = {describe(config)}\n  expr = {new_question.expr!r}")

    def test_regenerate_one_is_deterministic(self):
        """同参数两次换题应得到同一道新题（契约 §2.3 确定性同样适用于 regenerate_one）。"""
        env = load_env()
        _, config = representative_configs(env)[1]          # R3 表内乘除
        paper = call_generate(self, env, config, SEED)
        one = env.regenerate_one(config, SEED, 5, paper)
        two = env.regenerate_one(config, SEED, 5, paper)
        self.assertEqual(snapshot([one]), snapshot([two]),
                         "regenerate_one 同 (config, seed, seq, existing) 两次结果不同")


if __name__ == "__main__":
    unittest.main()
