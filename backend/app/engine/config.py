"""出题参数校验（契约 §2.1，FR-E1–E6）。

违约一律抛 `ConfigError`（对应错误码 `CONFIG_INVALID`）。
校验规则**逐条**对应契约 §2.1 的表格，不做契约之外的加码：

  1. `ops` / `ranges` 非空且元素类型正确
  2. `exact_division` 与 `allow_remainder` 互斥
  3. `ranges` 含 R7 时 `max_value` 必填且 ≥ 10
  4. `question_type=STEPWISE` 要求 `ops` 至少两种运算，或 `parentheses=True`
  5. `question_type=FILL` 不允许 `parentheses=True`
  6. `count` 必须在 1..100

两个显式口径（契约未规定，此处写死并可测）：
  - 不含 R7 时传入的 `max_value` **被忽略**，不报错（契约只说「含 R7 时必填」）；
  - `question_type=ORAL` 时 `parentheses` 被忽略（契约只说「仅脱式题型有意义」）。

纯函数纪律：无 I/O（契约 §8）。
"""
from __future__ import annotations

from .types import ConfigError, GenerateConfig, Op, QuestionType, RangeId

__all__ = ["validate", "MIN_COUNT", "MAX_COUNT", "MIN_MAX_VALUE"]

MIN_COUNT = 1
MAX_COUNT = 100
MIN_MAX_VALUE = 10


def _fail(message: str):
    raise ConfigError(message)


def validate(config: GenerateConfig) -> None:
    """校验出题参数；合法则返回 None，违约抛 ConfigError。"""
    if not isinstance(config, GenerateConfig):
        _fail("config 必须是 GenerateConfig，实际为 %s" % type(config).__name__)

    ops = _check_ops(config.ops)
    _check_ranges(config.ranges)

    if not isinstance(config.question_type, QuestionType):
        _fail("question_type 必须是 QuestionType，实际为 %r" % (config.question_type,))

    if config.count is None or isinstance(config.count, bool) or not isinstance(config.count, int):
        _fail("count 必须是整数，实际为 %r" % (config.count,))
    if not (MIN_COUNT <= config.count <= MAX_COUNT):
        _fail("count 必须在 %d..%d 之间，实际为 %d" % (MIN_COUNT, MAX_COUNT, config.count))

    if config.exact_division and config.allow_remainder:
        _fail("exact_division 与 allow_remainder 互斥，不能同时为真")

    if RangeId.R7 in config.ranges:
        max_value = config.max_value
        if max_value is None:
            _fail("ranges 含 R7 时 max_value 必填")
        if isinstance(max_value, bool) or not isinstance(max_value, int):
            _fail("max_value 必须是整数，实际为 %r" % (max_value,))
        if max_value < MIN_MAX_VALUE:
            _fail("max_value 必须 ≥ %d，实际为 %d" % (MIN_MAX_VALUE, max_value))

    if config.question_type is QuestionType.STEPWISE:
        if len(ops) < 2 and not config.parentheses:
            _fail("STEPWISE 要求 ops 至少含两种运算，或 parentheses=True")
    elif config.question_type is QuestionType.FILL:
        if config.parentheses:
            _fail("FILL 不允许 parentheses=True（V1 简化）")


def _check_ops(ops) -> list:
    if not isinstance(ops, (set, frozenset, list, tuple)) or len(ops) == 0:
        _fail("ops 必须是非空集合")
    result = list(ops)
    for op in result:
        if not isinstance(op, Op):
            _fail("ops 元素必须是 Op，实际为 %r" % (op,))
    return result


def _check_ranges(ranges) -> list:
    if not isinstance(ranges, (list, tuple)) or len(ranges) == 0:
        _fail("ranges 必须是非空列表")
    for rid in ranges:
        if not isinstance(rid, RangeId):
            _fail("ranges 元素必须是 RangeId，实际为 %r" % (rid,))
    return list(ranges)
