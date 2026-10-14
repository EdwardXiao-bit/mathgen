"""难度评分与考点标签（FR-E10 / FR-E11，规则见 docs/02-技术设计.md §6.4）。

难度公式（规则打分，可解释、可测）：

    difficulty = 1
      + 位数贡献（操作数最大位数 - 1）
      + 进位 / 退位次数（竖式逐位计数，只数加法进位与减法退位）
      + 运算步数（超出 1 步的部分）
      + 含括号 +1
      + 有余数除法 +1
    最后规整到 1..5

纯函数纪律：只有整数与字符串运算（契约 §8）。
"""
from __future__ import annotations

from .builders import BinOp, Leaf, evaluate, has_group, leaf_values, op_count
from .constraints import borrow_count, carry_count, digit_count
from .ranges import RangeSpec, tag_for
from .types import Op, QuestionType

__all__ = ["difficulty_of", "tags_of", "carry_borrow_total"]

_TAG_CARRY = "进位"
_TAG_BORROW = "退位"
_TAG_PARENS = "含括号"
_TAG_REMAINDER = "有余数"
_TAG_STEPWISE = "脱式"
_TAG_FILL = "求未知数"


def carry_borrow_total(node) -> int:
    """表达式树中所有加法进位 + 减法退位的总次数。"""
    if isinstance(node, Leaf):
        return 0
    total = carry_borrow_total(node.left) + carry_borrow_total(node.right)
    left = evaluate(node.left)
    right = evaluate(node.right)
    if node.op is Op.ADD:
        total += carry_count(left, right)
    elif node.op is Op.SUB:
        total += borrow_count(left, right)
    return total


def difficulty_of(node, has_remainder: bool = False) -> int:
    """按 §6.4 规则打分，规整到 1..5。"""
    values = leaf_values(node)
    if not values:
        raise ValueError("difficulty_of: 表达式没有可求值的操作数")
    score = 1
    score += max(digit_count(v) for v in values) - 1
    score += carry_borrow_total(node)
    score += max(0, op_count(node) - 1)
    if has_group(node):
        score += 1
    if has_remainder:
        score += 1
    return max(1, min(5, score))


def tags_of(spec: RangeSpec, ops, *, carries: int = 0, borrows: int = 0,
            parentheses: bool = False, remainder: bool = False,
            question_type: QuestionType | None = None) -> list:
    """考点标签：档位×运算标签 + 特殊约束标签（保证非空）。"""
    tags = []
    for op in ops:
        tag = tag_for(spec, op)
        if tag not in tags:
            tags.append(tag)
    if carries:
        tags.append(_TAG_CARRY)
    if borrows:
        tags.append(_TAG_BORROW)
    if parentheses:
        tags.append(_TAG_PARENS)
    if remainder:
        tags.append(_TAG_REMAINDER)
    if question_type is QuestionType.STEPWISE:
        tags.append(_TAG_STEPWISE)
    elif question_type is QuestionType.FILL:
        tags.append(_TAG_FILL)
    return tags
