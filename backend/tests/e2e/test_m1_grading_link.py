"""引擎 × 判分**联动**（M1 里程碑门禁的第二条：判分正确）。

断言：
  ① 生成的每一道题，用它的标准答案 `answer` 判分必须 `is_correct=True`
  ② 对 `answer` 做**一处数值扰动**（商/结果 +1）后判分必须 `is_correct=False`
  ③ `grade` 是确定性的（同输入同输出），且 `Verdict.normalized_input` 始终是字符串

这是跨模块验证：写引擎的与写判分的是两个智能体，单元测试各自全绿也可能在接口处断裂
（例如余数写法、脱式只判最终答案）。
"""
import unittest

if __package__:
    from .harness import (load_env, representative_configs, describe,
                          call_generate, call_grade, perturb_answer)
else:
    from harness import (load_env, representative_configs, describe,
                         call_generate, call_grade, perturb_answer)

SEED = 20261014


def _papers(env):
    """返回 [(label, config, paper)]：7 个代表性配置（含脱式、填空、有余数除法）。

    这里**只用代表性配置**，不用随机配置：随机配置的生成失败属于引擎约束完备性问题，
    由 `test_m1_constraints` 报告；本文件只负责「引擎 × 判分」的接口联动。
    """
    out = []
    for label, config in representative_configs(env):
        try:
            paper = call_generate_for(env, config, SEED)
        except env.InsufficientQuestions as exc:
            raise AssertionError(
                f"[{label}] 引擎无法产出该**合法**配置的题目，判分联动无法完成。\n"
                f"  config = {describe(config)}\n  异常 = {exc!r}\n"
                "  （同一根因由 test_m1_constraints 报告）") from exc
        out.append((label, config, paper))
    return out


def call_generate_for(env, config, seed):
    """不依赖 TestCase 的 generate 包装（用于构造样本）。"""
    result = env.generate(config, seed)
    if not isinstance(result, list):
        raise AssertionError(f"generate 未返回 list，实得 {type(result).__name__}")
    return result


class TestEngineGraderLink(unittest.TestCase):
    def test_generated_answer_grades_correct(self):
        env = load_env()
        checked = 0
        for label, config, paper in _papers(env):
            for index, question in enumerate(paper):
                with self.subTest(label=label, index=index,
                                  expr=question.expr, answer=question.answer):
                    verdict = call_grade(self, env, question, question.answer)
                    checked += 1
                    self.assertTrue(
                        verdict.is_correct,
                        "标准答案被判为错误（引擎与判分在答案写法上不一致）：\n"
                        f"  config = {describe(config)}\n  expr    = {question.expr!r}\n"
                        f"  display = {question.display!r}\n  answer  = {question.answer!r}\n"
                        f"  normalized_input = {verdict.normalized_input!r}")
        self.assertGreaterEqual(checked, 100,
                                f"联动用例只检查了 {checked} 道题，覆盖量不足")

    def test_perturbed_answer_grades_incorrect(self):
        env = load_env()
        checked = 0
        for label, config, paper in _papers(env):
            for index, question in enumerate(paper):
                perturbed = perturb_answer(question.answer)
                with self.subTest(label=label, index=index,
                                  expr=question.expr, answer=question.answer,
                                  perturbed=perturbed):
                    verdict = call_grade(self, env, question, perturbed)
                    checked += 1
                    self.assertFalse(
                        verdict.is_correct,
                        "扰动后的答案仍被判为正确（判分过宽）：\n"
                        f"  config = {describe(config)}\n  expr    = {question.expr!r}\n"
                        f"  answer  = {question.answer!r}\n  perturbed = {perturbed!r}\n"
                        f"  normalized_input = {verdict.normalized_input!r}")
        self.assertGreaterEqual(checked, 100,
                                f"联动用例只检查了 {checked} 道题，覆盖量不足")

    def test_grade_is_deterministic(self):
        env = load_env()
        for label, _config, paper in _papers(env):
            for question in paper:
                with self.subTest(label=label, expr=question.expr):
                    first = call_grade(self, env, question, question.answer)
                    second = call_grade(self, env, question, question.answer)
                    self.assertEqual(first.is_correct, second.is_correct)
                    self.assertEqual(first.normalized_input, second.normalized_input)

    def test_stepwise_grades_final_answer_only(self):
        """契约 §3：脱式题只判最终答案，`steps` 完全不参与判分（测试方案 §5 明确要求守住）。"""
        env = load_env()
        config = env.GenerateConfig(
            ops={env.Op.ADD, env.Op.MUL}, ranges=[env.RangeId.R2],
            question_type=env.QuestionType.STEPWISE, parentheses=True, count=10)
        paper = call_generate(self, env, config, SEED)
        for question in paper:
            with self.subTest(expr=question.expr, answer=question.answer):
                self.assertGreaterEqual(len(question.steps), 2)
                self.assertTrue(call_grade(self, env, question, question.answer).is_correct)

                # 只把 steps 清空、其余字段逐字复制：判分结果必须完全不变
                cloned = env.Question(
                    expr=question.expr, display=question.display, answer=question.answer,
                    steps=[], difficulty=question.difficulty, tags=list(question.tags),
                    signature=question.signature)
                with_steps = call_grade(self, env, question, question.answer)
                without_steps = call_grade(self, env, cloned, question.answer)
                self.assertEqual(
                    (with_steps.is_correct, with_steps.normalized_input),
                    (without_steps.is_correct, without_steps.normalized_input),
                    "清空 steps 后判分结果变了 —— 说明判分把过程也当成了计分依据"
                    "（契约 §3：脱式只判最终答案）")


if __name__ == "__main__":
    unittest.main()
