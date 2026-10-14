"""数值范围档位 R1–R7（D16 档位表 / FR-E2 / FR-E3）。

本模块是**纯数据表 + 纯查表函数**：给定「档位 + 运算」，返回一组数值边界
（左操作数 / 右操作数 / 结果）。构造器（builders.py）按这组边界**反向构造**题目，
因此「操作数与结果都在档位范围内」是构造出来的、而不是筛出来的。

档位语义（D16）：
  R1 20 以内加减 / R2 100 以内加减 / R3 表内乘除 / R4 两位数×一位数、两位数÷一位数
  R5 三位数×两位数、三位数÷两位数 / R6 四位数及以上多位数加减 / R7 老师自定义上限

两个已定的实现口径（契约未细化，此处显式写死并可测）：
  1. **同档位未显式定义的运算用 `value_max` 派生边界**（见 `_derived`）。
     例：R3 是「表内乘除」，若老师同时勾了加法，则加法按「结果 ≤ 81」出题。
     这样任何 `ops × ranges` 组合都有定义，不会出现「档位与运算组合无解」的伪无解。
  2. R7 的 `value_max` 是**操作数与结果的上限**（老师填「最大不超过 N」）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .constraints import digit_count
from .types import Op, RangeId

__all__ = [
    "Bounds",
    "RangeSpec",
    "OP_NAME",
    "spec_for",
    "bounds_for",
    "tag_for",
    "supported_ops",
    "digit_span",
    "shape_span",
]

OP_NAME = {
    Op.ADD: "加法",
    Op.SUB: "减法",
    Op.MUL: "乘法",
    Op.DIV: "除法",
}


def digit_span(digits: int):
    """`digits` 位数的取值范围（闭区间）。"""
    if digits <= 1:
        return 1, 9
    return 10 ** (digits - 1), 10 ** digits - 1


def shape_span(lo: int, hi: int, digits):
    """把「值域区间」收窄到「指定位数」上；`digits=None` 表示不限制。"""
    if digits is None:
        return lo, hi
    d_lo, d_hi = digit_span(digits)
    return max(lo, d_lo), min(hi, d_hi)


@dataclass(frozen=True)
class Bounds:
    """一次二元运算的数值边界（全部闭区间）。

    `shapes` —— **位数组合**（D47，冻结）：非空时表示「只接受这些
    `(左操作数位数, 右操作数位数)` 组合」，与顺序无关的组合要分别列出。
    例：R5 的乘法是 `三位数 × 两位数`，同时接受 `两位数 × 三位数`，
    因此 `shapes=((3, 2), (2, 3))`。空元组表示只受值域约束。
    """

    a_lo: int
    a_hi: int
    b_lo: int
    b_hi: int
    res_lo: int
    res_hi: int
    shapes: tuple = ()

    def holds(self, left: int, right: int, result: int) -> bool:
        """操作数与结果是否都落在边界内（含位数组合）。"""
        if not (self.a_lo <= left <= self.a_hi
                and self.b_lo <= right <= self.b_hi
                and self.res_lo <= result <= self.res_hi):
            return False
        if self.shapes:
            if (digit_count(left), digit_count(right)) not in self.shapes:
                return False
        return True

    def operand_span(self, side: int):
        """某一侧操作数的可行区间（位数组合的**包络**，用于反推内层值域）。

        包络为空时返回「反向区间」`(1, 0)`，由调用方统一转成 `Infeasible`
        （不在这里抛 ValueError —— 引擎对「做不到」只有 InsufficientQuestions 一条出路）。
        """
        lo, hi = (self.a_lo, self.a_hi) if side == 0 else (self.b_lo, self.b_hi)
        if not self.shapes:
            return lo, hi
        spans = [shape_span(lo, hi, shape[side]) for shape in self.shapes]
        spans = [(l, h) for l, h in spans if l <= h]
        if not spans:
            return 1, 0
        return min(l for l, _ in spans), max(h for _, h in spans)


@dataclass(frozen=True)
class RangeSpec:
    """一个档位的完整定义。"""

    id: RangeId
    label: str             # 用于标签前缀，如 "20以内"
    value_max: int         # 该档位的数量级上限（派生边界的依据）
    bounds: dict           # dict[Op, Bounds]，显式定义的运算语义
    tags: dict             # dict[Op, str]，显式定义的考点标签


def _derived(op: Op, value_max: int) -> Bounds:
    """档位未显式定义某运算时的兜底边界（见模块 docstring 口径 1）。"""
    v = max(2, int(value_max))
    if op is Op.ADD:
        return Bounds(1, v - 1, 1, v - 1, 2, v)
    if op is Op.SUB:
        return Bounds(1, v, 1, v - 1, 0, v - 1)
    if op is Op.MUL:
        # 结果也受 value_max 约束，因此是「乘积不超过上限」而不是「两位数字相乘」
        return Bounds(2, v, 2, v, 2, v)
    return Bounds(2, v, 2, v, 1, v)


def _builtin(rid: RangeId, label: str, value_max: int, add=None, sub=None,
             mul=None, div=None, tags=None) -> RangeSpec:
    bounds = {}
    if add is not None:
        bounds[Op.ADD] = add
    if sub is not None:
        bounds[Op.SUB] = sub
    if mul is not None:
        bounds[Op.MUL] = mul
    if div is not None:
        bounds[Op.DIV] = div
    return RangeSpec(id=rid, label=label, value_max=value_max,
                     bounds=bounds, tags=dict(tags or {}))


_TABLE = {
    RangeId.R1: _builtin(
        RangeId.R1, "20以内", 20,
        add=Bounds(1, 19, 1, 19, 2, 20),
        sub=Bounds(1, 20, 1, 19, 0, 19),
        tags={Op.ADD: "20以内加法", Op.SUB: "20以内减法"},
    ),
    RangeId.R2: _builtin(
        RangeId.R2, "100以内", 100,
        add=Bounds(1, 99, 1, 99, 2, 100),
        sub=Bounds(1, 100, 1, 99, 0, 99),
        tags={Op.ADD: "100以内加法", Op.SUB: "100以内减法"},
    ),
    RangeId.R3: _builtin(
        RangeId.R3, "表内", 81,
        mul=Bounds(1, 9, 1, 9, 1, 81, shapes=((1, 1),)),
        div=Bounds(1, 81, 2, 9, 1, 9),
        tags={Op.MUL: "表内乘法", Op.DIV: "表内除法"},
    ),
    RangeId.R4: _builtin(
        RangeId.R4, "两位数", 99,
        # 「两位数 × 一位数」与位数组合的顺序无关（D47）：两种排列都接受
        mul=Bounds(2, 99, 2, 99, 20, 891, shapes=((2, 1), (1, 2))),
        div=Bounds(10, 99, 2, 9, 1, 49),
        tags={Op.MUL: "两位数乘一位数", Op.DIV: "两位数除以一位数"},
    ),
    RangeId.R5: _builtin(
        RangeId.R5, "三位数", 999,
        # 「三位数 × 两位数」同样与顺序无关（D47）：810 × 30 与 30 × 810 都合法
        mul=Bounds(10, 999, 10, 999, 1000, 98901, shapes=((3, 2), (2, 3))),
        div=Bounds(100, 999, 10, 99, 1, 99),
        tags={Op.MUL: "三位数乘两位数", Op.DIV: "三位数除以两位数"},
    ),
    RangeId.R6: _builtin(
        RangeId.R6, "四位数", 9999,
        add=Bounds(1000, 9999, 1000, 9999, 2000, 19998),
        sub=Bounds(1000, 9999, 1000, 9999, 0, 8999),
        tags={Op.ADD: "多位数加法", Op.SUB: "多位数减法"},
    ),
}


def _r7(max_value: int) -> RangeSpec:
    v = int(max_value)
    return RangeSpec(
        id=RangeId.R7,
        label="自定义%d以内" % v,
        value_max=v,
        bounds={
            Op.ADD: Bounds(1, v - 1, 1, v - 1, 2, v),
            Op.SUB: Bounds(1, v, 1, v - 1, 0, v - 1),
            Op.MUL: Bounds(2, v, 2, v, 2, v),
            Op.DIV: Bounds(2, v, 2, v, 1, v),
        },
        tags={
            Op.ADD: "%d以内加法" % v,
            Op.SUB: "%d以内减法" % v,
            Op.MUL: "%d以内乘法" % v,
            Op.DIV: "%d以内除法" % v,
        },
    )


def spec_for(rid: RangeId, max_value: int | None = None) -> RangeSpec:
    """取档位定义；R7 需要 `max_value`（由 config.py 保证非空且 ≥ 10）。"""
    if rid is RangeId.R7:
        if max_value is None:
            raise ValueError("R7 需要 max_value")
        return _r7(max_value)
    return _TABLE[rid]


def bounds_for(spec: RangeSpec, op: Op) -> Bounds:
    """取「档位 × 运算」的边界；未显式定义时按 value_max 派生。"""
    bd = spec.bounds.get(op)
    if bd is not None:
        return bd
    return _derived(op, spec.value_max)


def supported_ops(spec: RangeSpec) -> list:
    """该档位**显式**支持的运算（按运算符号排序，顺序稳定）。"""
    return sorted(spec.bounds.keys(), key=lambda o: o.value)


def tag_for(spec: RangeSpec, op: Op) -> str:
    """该档位该运算的考点标签（保证非空）。"""
    tag = spec.tags.get(op)
    if tag:
        return tag
    return "%s%s" % (spec.label, OP_NAME[op])
