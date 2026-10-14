"""测试侧**独立**的表达式解析与求值（不复用引擎的求值器）。

存在的意义：属性测试不能拿「被测实现自己的求值器」去验自己。
这里重新写一份词法 + 递归下降语法，独立地把卷面字符串变成求值树，
并暴露「每个子表达式的（左、右、结果）」供边界断言使用。
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"\d+|[+\-×÷()]")
_NUM = re.compile(r"^\d+$")


class ProbeError(AssertionError):
    """测试侧解析失败（说明卷面字符串不合规）。"""


def tokenize(text: str) -> list:
    compact = text.replace(" ", "")
    tokens = _TOKEN.findall(compact)
    if "".join(tokens) != compact:
        raise ProbeError("表达式含无法识别的字符: %r" % text)
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self, expected=None):
        token = self._peek()
        if token is None:
            raise ProbeError("表达式意外结束")
        if expected is not None and token != expected:
            raise ProbeError("期望 %r，实际 %r" % (expected, token))
        self.pos += 1
        return token

    def parse(self):
        node = self._expr()
        if self.pos != len(self.tokens):
            raise ProbeError("表达式有剩余记号: %r" % self.tokens[self.pos:])
        return node

    def _expr(self):
        node = self._term()
        while self._peek() in ("+", "-"):
            op = self._take()
            node = ("op", op, node, self._term())
        return node

    def _term(self):
        node = self._atom()
        while self._peek() in ("×", "÷"):
            op = self._take()
            node = ("op", op, node, self._atom())
        return node

    def _atom(self):
        token = self._peek()
        if token == "(":
            self._take("(")
            node = self._expr()
            self._take(")")
            return node
        if token is not None and _NUM.match(token):
            self._take()
            return ("num", int(token))
        raise ProbeError("意外的记号 %r" % (token,))


def parse(text: str):
    """把卷面表达式串解析成 ('num', v) / ('op', op, left, right) 树。"""
    return _Parser(tokenize(text)).parse()


def evaluate(tree) -> int:
    """独立求值（除法取下整商，与小学「商」一致）。"""
    if tree[0] == "num":
        return tree[1]
    _, op, left, right = tree
    a = evaluate(left)
    b = evaluate(right)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "×":
        return a * b
    return a // b


def eval_text(text: str) -> int:
    return evaluate(parse(text))


def leaves(tree) -> list:
    """所有数值叶子。"""
    if tree[0] == "num":
        return [tree[1]]
    return leaves(tree[2]) + leaves(tree[3])


def nodes(tree) -> list:
    """所有运算节点：(op, 左值, 右值, 结果)。"""
    if tree[0] == "num":
        return []
    _, op, left, right = tree
    left_value = evaluate(left)
    right_value = evaluate(right)
    value = evaluate(tree)
    return nodes(left) + nodes(right) + [(op, left_value, right_value, value)]


def root_op(tree):
    return None if tree[0] == "num" else tree[1]


def carry_count(left: int, right: int) -> int:
    """竖式加法产生进位的位数（独立实现）。"""
    count = 0
    carry = 0
    while left or right:
        total = (left % 10) + (right % 10) + carry
        carry = 1 if total >= 10 else 0
        count += carry
        left //= 10
        right //= 10
    return count


def borrow_count(left: int, right: int) -> int:
    """竖式减法产生退位的位数（独立实现）。"""
    count = 0
    borrow = 0
    while left or right:
        if (left % 10) - borrow < right % 10:
            count += 1
            borrow = 1
        else:
            borrow = 0
        left //= 10
        right //= 10
    return count


def top_division(text: str):
    """若 `text` 是一步除法「a ÷ b」则返回 (a, b)，否则 None。"""
    tree = parse(text)
    if tree[0] == "op" and tree[1] == "÷" and tree[2][0] == "num" and tree[3][0] == "num":
        return tree[2][1], tree[3][1]
    return None


def int_tokens(text: str) -> list:
    """取出字符串里的所有整数（用于检查答案版上打印出来的数值）。"""
    return [int(token) for token in re.findall(r"\d+", text or "")]


def _apply(op: str, left: int, right: int) -> int:
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "×":
        return left * right
    return left // right


def left_to_right_values(text: str) -> list:
    """**按从左到右**（不考虑优先级）逐步求值，返回每一步的结果。

    只支持无括号的平坦算式（`a op b op c`）；用于验证 D46②：
    「按左到右展开的每一步中间值都必须落在档位上限内」。
    """
    tokens = tokenize(text)
    if any(token in ("(", ")") for token in tokens):
        raise ProbeError("left_to_right_values 只支持无括号表达式: %r" % text)
    if len(tokens) % 2 == 0:
        raise ProbeError("表达式记号数应为奇数: %r" % text)
    values = [int(tokens[0])]
    index = 1
    while index < len(tokens):
        values.append(_apply(tokens[index], values[-1], int(tokens[index + 1])))
        index += 2
    return values



def parse_remainder(text: str):
    """把 `7余2` 解析成 (7, 2)；无余数则返回 (值, 0)。"""
    compact = text.replace(" ", "")
    if "余" in compact:
        quotient, remainder = compact.split("余")
        return int(quotient), int(remainder)
    return int(compact), 0


def equation_holds(filled: str):
    """校验「把 □ 填成答案」后的等式是否真的成立。

    支持两种写法：`a - b = c` 与 `a ÷ b = 商余余数`。
    """
    if filled.count("=") != 1:
        raise ProbeError("等式应恰好有一个等号: %r" % filled)
    left_text, right_text = filled.split("=")
    left_text, right_text = left_text.strip(), right_text.strip()
    if "余" in right_text:
        quotient, remainder = parse_remainder(right_text)
        division = top_division(left_text)
        if division is None:
            raise ProbeError("有余数的等式左侧必须是一步除法: %r" % filled)
        dividend, divisor = division
        return (quotient == dividend // divisor
                and remainder == dividend % divisor
                and 0 <= remainder < divisor)
    return eval_text(left_text) == eval_text(right_text)
