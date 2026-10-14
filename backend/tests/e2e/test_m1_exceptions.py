"""§4.4 异常路径（契约 §2.1 约束规则 + InsufficientQuestions 的**有限重试**要求）。

| 输入 | 期望 |
| --- | --- |
| `exact_division=True` 且 `allow_remainder=True` | `ConfigError` |
| `ranges=[R7]` 但 `max_value=None`（或 <10） | `ConfigError` |
| `count=0` / `101`（越界） | `ConfigError` |
| `ops` / `ranges` 为空 | `ConfigError` |
| `STEPWISE` 但只有一种运算且无括号 | `ConfigError` |
| `FILL` 且 `parentheses=True` | `ConfigError` |
| 约束组合确实无解（20 以内 + 必须进位 + 100 题） | `InsufficientQuestions`，且**耗时有上限** |

「耗时有上限」用工作线程 + 超时守护：若实现真的无限重试，用例会**失败**而不是把门禁挂死。
"""
import threading
import time
import unittest

if __package__:
    from .harness import load_env, describe
else:
    from harness import load_env, describe

_TIMEOUT_SECONDS = 10.0


def _run_with_timeout(fn, timeout):
    """返回 (返回值, 异常, 是否在超时前结束)。"""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:      # noqa: BLE001 - 需要把任何业务异常带回来
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, None, False
    return box.get("value"), box.get("error"), True


class TestConfigValidation(unittest.TestCase):
    """契约 §2.1：以下配置必须抛 ConfigError（构造期或 generate 期均可）。"""

    def _assert_config_error(self, env, label, **kwargs):
        with self.subTest(label=label):
            with self.assertRaises(
                    env.ConfigError,
                    msg=f"[{label}] 应抛 ConfigError（契约 §2.1）；"
                        f" 配置 = {kwargs}"):
                config = env.GenerateConfig(**kwargs)
                env.generate(config, 1)

    def test_exact_division_and_allow_remainder_mutually_exclusive(self):
        env = load_env()
        self._assert_config_error(
            env, "exact_division 与 allow_remainder 同时为真",
            ops={env.Op.DIV}, ranges=[env.RangeId.R4],
            question_type=env.QuestionType.ORAL,
            exact_division=True, allow_remainder=True, count=10)

    def test_r7_requires_max_value(self):
        env = load_env()
        self._assert_config_error(
            env, "含 R7 但 max_value=None",
            ops={env.Op.ADD}, ranges=[env.RangeId.R7],
            question_type=env.QuestionType.ORAL, count=10)

    def test_r7_max_value_must_be_at_least_ten(self):
        env = load_env()
        for bad in (0, 5, 9):
            self._assert_config_error(
                env, f"含 R7 但 max_value={bad}（<10）",
                ops={env.Op.ADD}, ranges=[env.RangeId.R7], max_value=bad,
                question_type=env.QuestionType.ORAL, count=10)

    def test_count_must_be_within_1_to_100(self):
        env = load_env()
        for bad in (0, 101, -1):
            self._assert_config_error(
                env, f"count={bad} 越界",
                ops={env.Op.ADD}, ranges=[env.RangeId.R2],
                question_type=env.QuestionType.ORAL, count=bad)

    def test_ops_and_ranges_must_not_be_empty(self):
        env = load_env()
        self._assert_config_error(
            env, "ops 为空", ops=set(), ranges=[env.RangeId.R2],
            question_type=env.QuestionType.ORAL, count=10)
        self._assert_config_error(
            env, "ranges 为空", ops={env.Op.ADD}, ranges=[],
            question_type=env.QuestionType.ORAL, count=10)

    def test_stepwise_needs_two_ops_or_parentheses(self):
        env = load_env()
        self._assert_config_error(
            env, "STEPWISE 只有一种运算且无括号",
            ops={env.Op.ADD}, ranges=[env.RangeId.R2],
            question_type=env.QuestionType.STEPWISE, parentheses=False, count=10)
        # 反例：加上括号后合法（不该抛 ConfigError）
        result = env.generate(env.GenerateConfig(
            ops={env.Op.ADD}, ranges=[env.RangeId.R2],
            question_type=env.QuestionType.STEPWISE, parentheses=True, count=5), 1)
        self.assertEqual(len(result), 5)

    def test_fill_rejects_parentheses(self):
        env = load_env()
        self._assert_config_error(
            env, "FILL 且 parentheses=True",
            ops={env.Op.ADD, env.Op.SUB}, ranges=[env.RangeId.R2],
            question_type=env.QuestionType.FILL, parentheses=True, count=10)


class TestInsufficientQuestions(unittest.TestCase):
    """无解组合必须抛 InsufficientQuestions，且必须在有限时间内返回。"""

    def test_unsolvable_combination_raises_within_time_limit(self):
        env = load_env()
        # docs/06 §4.4 点名的例子：20 以内 + 必须进位 + 100 题
        config = env.GenerateConfig(
            ops={env.Op.ADD}, ranges=[env.RangeId.R1],
            question_type=env.QuestionType.ORAL,
            require_carry=True, count=100)
        self.assertEqual(list(config.ranges)[0], env.RangeId.R1)

        started = time.monotonic()
        _value, error, finished = _run_with_timeout(
            lambda: env.generate(config, 20261014), _TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started

        self.assertTrue(
            finished,
            f"generate 在 {_TIMEOUT_SECONDS:.0f}s 内没有返回 —— 无解组合不得无限重试"
            f"（契约 §2.3 / docs/06 §4.4）。config = {describe(config)}")
        self.assertIsInstance(
            error, env.InsufficientQuestions,
            f"无解组合应抛 InsufficientQuestions，实得 {error!r}"
            f"（若为 ConfigError，说明把『题不足』误判成『参数非法』）\n"
            f"  config = {describe(config)}")
        self.assertLess(
            elapsed, _TIMEOUT_SECONDS,
            f"无解组合耗时 {elapsed:.2f}s，超过上限 {_TIMEOUT_SECONDS:.0f}s")


if __name__ == "__main__":
    unittest.main()
