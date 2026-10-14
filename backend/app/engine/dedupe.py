"""去重签名（FR-E7 / 契约 §2.2）。

签名 = 表达式树的**规范化字符串**：去空格、统一运算符（`×` `÷`）、
**交换律归一**（`3+5` 与 `5+3`、`2×6` 与 `6×2` 视为同一签名）。

归一做法是**结构化的**：先把每个可交换节点（`+` / `×`）的两个子节点按
「各自规范渲染串」排序，再用同一套括号规则渲染，最后去掉空格。
这样交换律、以及交换律与括号的叠加都能归一，而不是靠字符串技巧。

填空题额外把 □ 的位置编码进签名（占位符就是 `□` 本身），
因此 `7 + □ = 15` 与 `□ + 7 = 15` 同签名，而 `7 + □ = 15` 与 `□ + 8 = 15` 不同签名
——两者本就是不同的题（挖掉的是不同的数）。

纯函数纪律：只有字符串与整数运算（契约 §8）。
"""
from __future__ import annotations

from .builders import BinOp, Leaf, render
from .types import Op

__all__ = ["normalize", "canonical_string", "signature_of"]

_COMMUTATIVE = (Op.ADD, Op.MUL)


def _key(node) -> str:
    return render(node)


def normalize(node):
    """递归地把可交换节点的子节点排序，返回新的表达式树。"""
    if isinstance(node, Leaf):
        return node
    left = normalize(node.left)
    right = normalize(node.right)
    if node.op in _COMMUTATIVE and _key(right) < _key(left):
        left, right = right, left
    return BinOp(node.op, left, right, node.grouped)


def canonical_string(node) -> str:
    """规范化字符串（无空格）。"""
    return render(normalize(node)).replace(" ", "")


def signature_of(node, result_blank: bool = False) -> str:
    """题目签名。

    `result_blank=True` 用于「把结果挖空」的填空题：题面其余部分完全确定，
    用 `=□` 标记被挖空的结果。
    """
    text = canonical_string(node)
    return text + "=□" if result_blank else text
