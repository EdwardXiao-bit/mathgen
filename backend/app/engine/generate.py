"""出题引擎入口：`generate` / `regenerate_one`（契约 §2.3）。

确定性（FR-E8，冻结）的实现方式：每一道题的随机数流由
`(seed, 题号 index, 第几次尝试 attempt)` 三元组派生，
因此**同 (config, seed) 必得逐字段相同的结果（含顺序）**，
不受进程、调用顺序或全局状态影响。

无解组合的处理（契约 §2.3）：单题尝试次数有上限，总尝试次数也有上限，
用尽即抛 `InsufficientQuestions`，**不会无限重试**。

纯函数纪律：不读文件、不查库、不联网、无全局可变状态（契约 §8 / C5）。
"""
from __future__ import annotations

from .assemble import difficulty_of, tags_of
from .builders import (
    BinOp,
    Infeasible,
    Leaf,
    build_simple,
    build_stepwise_tree,
    evaluate,
    has_group,
    make_steps,
    render,
)
from .config import validate
from .dedupe import signature_of
from .ranges import bounds_for, spec_for
from .rng import Rng, mix_seed
from .types import (
    ConfigError,
    GenerateConfig,
    InsufficientQuestions,
    Question,
    QuestionType,
    RangeId,
)

__all__ = [
    "generate",
    "regenerate_one",
    "MAX_ATTEMPTS_PER_QUESTION",
    "MAX_TOTAL_ATTEMPTS",
]

MAX_ATTEMPTS_PER_QUESTION = 300    # 单题尝试上限（远超实际所需，杜绝误报无解）
MAX_TOTAL_ATTEMPTS = 20000         # 整卷尝试上限（硬性终止保证）
_BLANK = "□"
_SALT = 0x5EED


def _sorted_ops(config: GenerateConfig) -> list:
    """`config.ops` 是 set，遍历顺序不稳定；一律按运算符号排序后使用。"""
    return sorted(config.ops, key=lambda op: op.value)


def _spec_for_index(config: GenerateConfig, index: int):
    rid = config.ranges[index % len(config.ranges)]
    if rid is RangeId.R7:
        return spec_for(rid, config.max_value)
    return spec_for(rid)


def _make_question(config: GenerateConfig, seed: int, index: int, attempt: int):
    """构造第 `index` 题（第 `attempt` 次尝试）。不可行时返回 None。"""
    rng = Rng(mix_seed(seed, index, attempt, _SALT))
    spec = _spec_for_index(config, index)
    ops = _sorted_ops(config)
    try:
        if config.question_type is QuestionType.STEPWISE:
            node, outer, inner = build_stepwise_tree(config, rng, spec, ops)
            return _finish_stepwise(config, spec, node, outer, inner)
        op = ops[index % len(ops)]
        record = build_simple(rng, op, bounds_for(spec, op),
                              require_carry=config.require_carry,
                              require_borrow=config.require_borrow,
                              allow_remainder=config.allow_remainder)
        if config.question_type is QuestionType.FILL:
            return _finish_fill(config, spec, record, rng)
        return _finish_oral(config, spec, record)
    except Infeasible:
        return None


def _finish_oral(config: GenerateConfig, spec, record) -> Question:
    node = BinOp(record.op, Leaf(record.left), Leaf(record.right))
    expr = render(node)
    return Question(
        expr=expr,
        display="%s = ____" % expr,
        answer=record.result_text(),
        steps=[],
        difficulty=difficulty_of(node, record.has_remainder),
        tags=tags_of(spec, [record.op],
                     carries=record.carries, borrows=record.borrows,
                     remainder=record.has_remainder,
                     question_type=QuestionType.ORAL),
        signature=signature_of(node),
    )


def _finish_stepwise(config: GenerateConfig, spec, node, outer, inner) -> Question:
    expr = render(node)
    return Question(
        expr=expr,
        display=expr,
        answer=str(evaluate(node)),
        steps=make_steps(node),
        difficulty=difficulty_of(node, False),
        tags=tags_of(spec, [inner.op, outer.op],
                     carries=inner.carries + outer.carries,
                     borrows=inner.borrows + outer.borrows,
                     parentheses=has_group(node),
                     question_type=QuestionType.STEPWISE),
        signature=signature_of(node),
    )


def _finish_fill(config: GenerateConfig, spec, record, rng) -> Question:
    """填空：生成基础题后按位置挖空（结果 / 左操作数 / 右操作数）。"""
    base = BinOp(record.op, Leaf(record.left), Leaf(record.right))
    mode = rng.randint(0, 3)          # 0 → 挖结果；1 → 挖左操作数；其余 → 挖右操作数
    if mode == 0:
        node = base
        display = "%s = %s" % (record.expr_text(), _BLANK)
        answer = record.result_text()
        signature = signature_of(base, result_blank=True)
    elif mode == 1:
        node = BinOp(record.op, Leaf(0, unknown=True), Leaf(record.right))
        display = "%s %s %d = %s" % (_BLANK, record.op.value, record.right,
                                     record.result_text())
        answer = str(record.left)
        signature = signature_of(node)
    else:
        node = BinOp(record.op, Leaf(record.left), Leaf(0, unknown=True))
        display = "%d %s %s = %s" % (record.left, record.op.value, _BLANK,
                                     record.result_text())
        answer = str(record.right)
        signature = signature_of(node)
    return Question(
        expr=record.expr_text(),
        display=display,
        answer=answer,
        steps=[],
        difficulty=difficulty_of(base, record.has_remainder),
        tags=tags_of(spec, [record.op],
                     carries=record.carries, borrows=record.borrows,
                     remainder=record.has_remainder,
                     question_type=QuestionType.FILL),
        signature=signature,
    )


def _insufficient(config: GenerateConfig, index: int) -> InsufficientQuestions:
    ops = "/".join(op.value for op in _sorted_ops(config))
    ranges = "/".join(rid.value for rid in config.ranges)
    return InsufficientQuestions(
        "当前约束组合可出的题不足：已生成 %d 题，第 %d 题在 %d 次尝试内无法产生"
        "不重复且满足全部约束的题目（ops=%s, ranges=%s, type=%s, "
        "require_carry=%s, require_borrow=%s, exact_division=%s, "
        "allow_remainder=%s, parentheses=%s, max_value=%s）"
        % (index, index + 1, MAX_ATTEMPTS_PER_QUESTION, ops, ranges,
           config.question_type.value, config.require_carry, config.require_borrow,
           config.exact_division, config.allow_remainder, config.parentheses,
           config.max_value)
    )


def generate(config: GenerateConfig, seed: int) -> list:
    """按配置生成 `config.count` 道**互不重复**的题（纯函数、确定性）。"""
    validate(config)
    chosen = []
    used = set()
    total = 0
    for index in range(config.count):
        question = None
        for attempt in range(MAX_ATTEMPTS_PER_QUESTION):
            total += 1
            if total > MAX_TOTAL_ATTEMPTS:
                raise _insufficient(config, index)
            candidate = _make_question(config, seed, index, attempt)
            if candidate is not None and candidate.signature not in used:
                question = candidate
                break
        if question is None:
            raise _insufficient(config, index)
        used.add(question.signature)
        chosen.append(question)
    return chosen


def regenerate_one(config: GenerateConfig, seed: int, seq: int,
                   existing: list) -> Question:
    """只替换第 `seq` 题，返回新题（调用方自行保证其余题不变），FR-E9。

    新题保证：满足 config 全部约束、且签名与 `existing` 中**任何**一题都不同
    （含被替换的那一题本身）。`seq` 越界抛 `ConfigError`。
    """
    validate(config)
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ConfigError("seq 必须是整数，实际为 %r" % (seq,))
    if not (0 <= seq < len(existing)):
        raise ConfigError("seq=%d 越界（existing 共 %d 题）" % (seq, len(existing)))

    used = {question.signature for question in existing}
    total = 0
    for salt in range(1, MAX_ATTEMPTS_PER_QUESTION):
        total += 1
        if total > MAX_TOTAL_ATTEMPTS:
            break
        candidate = _make_question(config, seed, seq, salt)
        if candidate is not None and candidate.signature not in used:
            return candidate
    raise InsufficientQuestions(
        "无法为 seq=%d 生成与现有 %d 题都不重复的新题（尝试上限 %d 次）"
        % (seq, len(existing), MAX_ATTEMPTS_PER_QUESTION)
    )
