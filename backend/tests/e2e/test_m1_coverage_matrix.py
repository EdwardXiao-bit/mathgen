"""§11 覆盖矩阵抽查：FR-E7 / FR-E8 / FR-E10 / FR-E11 / FR-E12，各一条**具名**断言。

这些需求点在 §4.1 属性测试里已经顺带覆盖；本文件的目的是让覆盖矩阵「可指认」——
每条需求都能指到一个具名用例，而不是淹没在属性测试里。
"""
import unittest
from decimal import Decimal

if __package__:
    from .harness import (load_env, representative_configs, snapshot, diff_report,
                          describe, call_generate, commutative_duplicates,
                          answer_number, last_int_tokens)
else:
    from harness import (load_env, representative_configs, snapshot, diff_report,
                         describe, call_generate, commutative_duplicates,
                         answer_number, last_int_tokens)

SEED = 20261014
# 口算 / 脱式配置（填空题的"题干"是挖空后的 display，交换律去重语义有分歧，
# 见验收报告「契约存疑」一节，因此不纳入交换律等价检查）
_ORAL_OR_STEPWISE = (0, 1, 2, 3, 5)


def _papers(case, env, indexes):
    """产出 (label, config, paper)；paper 经 call_generate 校验过返回形态。"""
    cases = representative_configs(env)
    for index in indexes:
        label, config = cases[index]
        yield label, config, call_generate(case, env, config, SEED)


class TestCoverageMatrix(unittest.TestCase):
    def test_fr_e7_no_duplicate_signature_within_paper(self):
        """FR-E7 去重：单卷内 signature 不得重复。"""
        env = load_env()
        for label, config, paper in _papers(self, env, (0, 1, 2, 5)):
            with self.subTest(label=label, config=describe(config)):
                signatures = [q.signature for q in paper]
                duplicates = sorted({s for s in signatures if signatures.count(s) > 1})
                self.assertEqual(
                    duplicates, [],
                    f"[{label}] 单卷内 signature 重复（FR-E7）：{duplicates}")

    def test_fr_e7_commutative_equivalents_not_duplicated(self):
        """FR-E7 交换律归一：`3+5` 与 `5+3`、`2×6` 与 `6×2` 不得同卷。

        这是**不依赖实现 signature 格式**的独立检查：测试侧自己归一化题面再比对。
        """
        env = load_env()
        for label, config, paper in _papers(self, env, _ORAL_OR_STEPWISE):
            with self.subTest(label=label, config=describe(config)):
                pairs = commutative_duplicates(paper)
                self.assertEqual(
                    pairs, [],
                    f"[{label}] 卷内存在交换律等价的重复题（FR-E7 未归一交换律）："
                    f"{[(paper[i].expr, paper[j].expr) for i, j, _k in pairs]}")

    def test_fr_e8_same_seed_reproducible(self):
        """FR-E8 种子可复现：同 (config, seed) 必得逐题相同的结果（含顺序）。"""
        env = load_env()
        for label, config in representative_configs(env):
            with self.subTest(label=label):
                first = snapshot(call_generate(self, env, config, SEED))
                second = snapshot(call_generate(self, env, config, SEED))
                self.assertEqual(diff_report(first, second), "",
                                 f"[{label}] 同 (config, seed) 不可复现（FR-E8）")

    def test_fr_e10_difficulty_in_1_to_5(self):
        """FR-E10 难度：每题 difficulty ∈ 1..5。"""
        env = load_env()
        for label, _config, paper in _papers(self, env, tuple(range(6))):
            for index, question in enumerate(paper):
                with self.subTest(label=label, index=index):
                    self.assertIsInstance(
                        question.difficulty, int,
                        f"difficulty 应为整数，实得 {question.difficulty!r}")
                    self.assertTrue(
                        1 <= question.difficulty <= 5,
                        f"[{label}] 第 {index} 题 difficulty={question.difficulty} "
                        f"越界（FR-E10）")

    def test_fr_e11_tags_non_empty(self):
        """FR-E11 考点标签：每题 tags 非空，且元素为非空字符串。"""
        env = load_env()
        for label, _config, paper in _papers(self, env, tuple(range(6))):
            for index, question in enumerate(paper):
                with self.subTest(label=label, index=index):
                    self.assertTrue(
                        isinstance(question.tags, list) and question.tags,
                        f"[{label}] 第 {index} 题 tags 为空（FR-E11）：{question.tags!r}")
                    for tag in question.tags:
                        self.assertIsInstance(tag, str)
                        self.assertTrue(tag.strip(), f"tags 含空标签：{question.tags!r}")

    def test_fr_e12_stepwise_has_at_least_two_steps(self):
        """FR-E12 过程输出：脱式题 steps 长度 ≥2。"""
        env = load_env()
        label, config = representative_configs(env)[3]      # 脱式含括号
        paper = call_generate(self, env, config, SEED)
        self.assertGreater(len(paper), 0)
        for index, question in enumerate(paper):
            with self.subTest(index=index, expr=question.expr):
                self.assertGreaterEqual(
                    len(question.steps), 2,
                    f"[{label}] 第 {index} 题脱式过程不足两步（FR-E12）："
                    f"{question.steps!r}")
                self.assertTrue(all(isinstance(s, str) and s.strip()
                                    for s in question.steps),
                                f"steps 元素必须是非空字符串：{question.steps!r}")

    def test_fr_e12_last_step_consistent_with_answer(self):
        """FR-E12：脱式末步自洽——最终答案的数值出现在最后一步里（§4.1 断言⑧）。

        只做这一条**与格式无关**的检查：`["= 20 ÷ 4", "= 5"]` 与
        `["12 + 8 = 20", "20 ÷ 4 = 5"]` 两种写法都能成立。
        """
        env = load_env()
        label, config = representative_configs(env)[3]
        paper = call_generate(self, env, config, SEED)
        checked = 0
        for index, question in enumerate(paper):
            value = answer_number(question.answer)
            tokens = last_int_tokens(question.steps[-1]) if question.steps else []
            if value is None or not tokens:
                continue
            with self.subTest(index=index, expr=question.expr,
                              last_step=question.steps[-1]):
                checked += 1
                self.assertIn(
                    int(Decimal(value)), tokens,
                    f"[{label}] 第 {index} 题末步与答案不自洽：answer={question.answer!r}，"
                    f"末步={question.steps[-1]!r}（FR-E12 / §4.1 断言⑧）")
        self.assertGreater(checked, 0, "没有任何一题能做末步自洽校验，本用例是空转的")

    def test_fr_e12_non_stepwise_steps_empty(self):
        """契约 §2.2：`steps` 仅在 STEPWISE 时非空。"""
        env = load_env()
        for index in (0, 1, 2, 4):                          # 口算 / 口算 / 口算 / 填空
            label, config, paper = next(iter(_papers(self, env, (index,))))
            del config
            for qi, question in enumerate(paper):
                with self.subTest(label=label, index=qi):
                    self.assertEqual(
                        list(question.steps), [],
                        f"[{label}] 非脱式题 steps 必须为空（契约 §2.2）："
                        f"{question.steps!r}")


if __name__ == "__main__":
    unittest.main()
