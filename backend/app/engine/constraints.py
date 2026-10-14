"""特殊约束的原语：位数、进位、退位、非负与取模构造（FR-E4）。

「进位 / 退位」的口径（契约只写「是否进位 / 是否退位」，此处显式定死并可测）：

  - **进位**：按小学竖式加法逐位计算，**任一位**上「本位数字之和 + 低位进位 ≥ 10」
    即为一次进位。因此 `45 + 60` 是进位（十位 4+6=10），`23 + 45` 不是。
    只数加法，不数乘法（乘法竖式的进位不在本期口径内，见 build 侧注释）。
  - **退位**：按竖式减法逐位计算，**任一位**上「本位被减数（含借位）< 减数本位」
    即为一次退位。

纯函数纪律：只有整数运算（契约 §8）。
"""
from __future__ import annotations

__all__ = [
    "digit_count",
    "carry_count",
    "borrow_count",
    "has_carry",
    "has_borrow",
    "ceil_div",
    "floor_div",
    "first_ge_with_mod",
]


def digit_count(value: int) -> int:
    """十进制位数（0 记 1 位）。"""
    n = abs(int(value))
    digits = 1
    while n >= 10:
        n //= 10
        digits += 1
    return digits


def ceil_div(a: int, b: int) -> int:
    """向上取整的整除（b > 0）。"""
    if b <= 0:
        raise ValueError("ceil_div: b must be positive")
    return -((-a) // b)


def floor_div(a: int, b: int) -> int:
    """向下取整的整除（b > 0）。"""
    if b <= 0:
        raise ValueError("floor_div: b must be positive")
    return a // b


def carry_count(left: int, right: int) -> int:
    """竖式加法中产生进位的位数。"""
    a, b = abs(int(left)), abs(int(right))
    count = 0
    carry = 0
    while a or b:
        total = (a % 10) + (b % 10) + carry
        if total >= 10:
            count += 1
            carry = 1
        else:
            carry = 0
        a //= 10
        b //= 10
    return count


def borrow_count(left: int, right: int) -> int:
    """竖式减法 `left - right` 中产生退位的位数（要求 left ≥ right）。"""
    a, b = abs(int(left)), abs(int(right))
    count = 0
    borrow = 0
    while a or b:
        ai = (a % 10) - borrow
        if ai < (b % 10):
            count += 1
            borrow = 1
        else:
            borrow = 0
        a //= 10
        b //= 10
    return count


def has_carry(left: int, right: int) -> bool:
    """竖式加法是否发生进位。"""
    return carry_count(left, right) > 0


def has_borrow(left: int, right: int) -> bool:
    """竖式减法是否发生退位。"""
    return borrow_count(left, right) > 0


def first_ge_with_mod(lo: int, mod: int, rem: int) -> int:
    """最小的 `value ≥ lo` 且 `value % mod == rem`；不存在上界约束。"""
    if mod <= 0:
        raise ValueError("first_ge_with_mod: mod must be positive")
    r = rem % mod
    delta = (r - lo % mod) % mod
    return lo + delta
