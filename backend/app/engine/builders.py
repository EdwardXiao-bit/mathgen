"""按约束**反向构造**算式（FR-E5 / FR-E10 / FR-E12）。

设计要点（docs/02-技术设计.md §6.2）：

  - **不做「随机生成再过滤」**。每种运算都从约束出发反推：
      加法  先定个位（需要进位时保证「个位之和 ≥ 10」）再定十位以上；
      减法  **先定减数与被减数区间，结果天然非负**；需要退位时先定「被减数个位 < 减数个位」；
      乘法  先定一个因子，另一个因子由「乘积落在结果区间」反推区间后取样；
      除法  **先定除数与商，再相乘得被除数**——整除天然成立；有余数时再加上一个小于除数的余数。
  - 脱式（STEPWISE）**先建表达式树**（`BinOp`），`steps` 由逐步归约（后序式）产出。
  - 只有在「两个操作数/两层结果耦合」这类必须联立的地方才做**有限次**重试，
    重试用尽后抛内部的 `Infeasible`，由上层（generate.py）计入尝试预算后转成
    `InsufficientQuestions`（契约 §2.3：不得无限重试）。

脱式的两种树形（都为「无歧义 + 与小学脱式规范一致」服务）：

  - **右嵌套** `左 o1 (中 o2 右)`，用于「内层优先级更高」的场合，
    渲染成 `12 + 8 × 4`，逐步过程先算乘法（先乘除后加减）——先建内层、再建外层；
  - **左嵌套** `(左 o1 中) o2 右`，用于「内层优先级不低于外层」的场合，
    渲染成 `23 + 63 - 11`，逐步过程**从左到右**（同级从左往右）。

括号渲染口径（`render`）——保证「字符串形态」与「树结构」求值一致：
  - 左子节点：优先级低于父节点时加括号；
  - 右子节点：优先级低于父节点、或（同优先级且父节点是 `-` / `÷`）、
    或（父节点是 `×` 且子节点是 `÷`）时加括号。最后一条是为了避免
    `a × (b ÷ c)` 被渲染成有歧义的 `a × b ÷ c`。

纯函数纪律：只用整数运算，无 I/O（契约 §8）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .constraints import borrow_count, carry_count, ceil_div, digit_count, first_ge_with_mod
from .ranges import Bounds, bounds_for, shape_span
from .types import GenerateConfig, Op

__all__ = [
    "Leaf",
    "BinOp",
    "SimpleOp",
    "Infeasible",
    "render",
    "evaluate",
    "op_count",
    "leaf_values",
    "has_group",
    "build_simple",
    "build_stepwise_tree",
    "make_steps",
]

_INNER_ATTEMPTS = 16    # 单次构造内的联立重试上限（硬上限，不再扩大）
_MAX_STEP_NODES = 32    # 逐步归约的保护上限（表达式树最多 2 个运算）

LEFT = "left"
RIGHT = "right"


class Infeasible(Exception):
    """内部信号：本次构造在当前随机取向下做不到，请上层换一次尝试。"""


@dataclass(frozen=True)
class Leaf:
    """叶子：一个整数；`unknown=True` 表示填空题的 □。"""

    value: int
    unknown: bool = False


@dataclass(frozen=True)
class BinOp:
    """二元运算节点；`grouped=True` 表示卷面上真的有括号。"""

    op: Op
    left: object
    right: object
    grouped: bool = False


_PREC = {Op.ADD: 1, Op.SUB: 1, Op.MUL: 2, Op.DIV: 2}


def _left_needs_parens(parent: Op, child: Op) -> bool:
    return _PREC[child] < _PREC[parent]


def _right_needs_parens(parent: Op, child: Op) -> bool:
    if _PREC[child] < _PREC[parent]:
        return True
    if _PREC[child] == _PREC[parent]:
        if parent in (Op.SUB, Op.DIV):
            return True
        if parent is Op.MUL and child is Op.DIV:
            return True
    return False


def render(node, parent: Op | None = None, right: bool = False) -> str:
    """把表达式树渲染成卷面字符串（运算符用 `+ - × ÷`）。"""
    if isinstance(node, Leaf):
        return "□" if node.unknown else str(node.value)
    need = bool(node.grouped)
    if parent is not None:
        if right and _right_needs_parens(parent, node.op):
            need = True
        if not right and _left_needs_parens(parent, node.op):
            need = True
    inner = "%s %s %s" % (render(node.left, node.op, False),
                          node.op.value,
                          render(node.right, node.op, True))
    return "(%s)" % inner if need else inner


def evaluate(node) -> int:
    """表达式树求值（除法取下整商；`□` 不可求值）。"""
    if isinstance(node, Leaf):
        if node.unknown:
            raise ValueError("evaluate: 含未知数 □ 的表达式不可求值")
        return node.value
    left = evaluate(node.left)
    right = evaluate(node.right)
    if node.op is Op.ADD:
        return left + right
    if node.op is Op.SUB:
        return left - right
    if node.op is Op.MUL:
        return left * right
    return left // right


def op_count(node) -> int:
    """表达式树中的运算个数。"""
    if isinstance(node, Leaf):
        return 0
    return 1 + op_count(node.left) + op_count(node.right)


def leaf_values(node) -> list:
    """所有叶子上的数值（忽略 □）。"""
    if isinstance(node, Leaf):
        return [] if node.unknown else [node.value]
    return leaf_values(node.left) + leaf_values(node.right)


def has_group(node) -> bool:
    """表达式树中是否含显式括号。"""
    if isinstance(node, Leaf):
        return False
    return bool(node.grouped) or has_group(node.left) or has_group(node.right)


@dataclass(frozen=True)
class SimpleOp:
    """一个二元运算的构造结果。"""

    op: Op
    left: int
    right: int
    value: int                  # 结果；除法为商
    remainder: int = 0          # 除法余数，0 表示整除
    carries: int = 0
    borrows: int = 0

    @property
    def has_remainder(self) -> bool:
        return self.remainder > 0

    def result_text(self) -> str:
        """规范化答案写法：有余数时为 `商余余数`（半角、无空格）。"""
        if self.has_remainder:
            return "%d余%d" % (self.value, self.remainder)
        return str(self.value)

    def expr_text(self) -> str:
        return "%d %s %d" % (self.left, self.op.value, self.right)


def _mk(op: Op, left: int, right: int, value: int, remainder: int = 0) -> SimpleOp:
    carries = carry_count(left, right) if op is Op.ADD else 0
    borrows = borrow_count(left, right) if op is Op.SUB else 0
    return SimpleOp(op=op, left=left, right=right, value=value,
                    remainder=remainder, carries=carries, borrows=borrows)


def _tighten(bd: Bounds, res_lo, res_hi) -> Bounds:
    lo = bd.res_lo if res_lo is None else max(bd.res_lo, int(res_lo))
    hi = bd.res_hi if res_hi is None else min(bd.res_hi, int(res_hi))
    if lo > hi:
        raise Infeasible()
    return Bounds(bd.a_lo, bd.a_hi, bd.b_lo, bd.b_hi, lo, hi, bd.shapes)


# --------------------------------------------------------------------------
# 四种运算的构造
# --------------------------------------------------------------------------

def _add_free(rng, bd: Bounds, require_carry: bool):
    if not require_carry:
        lo = max(bd.a_lo, bd.res_lo - bd.b_hi)
        hi = min(bd.a_hi, bd.res_hi - bd.b_lo)
        if lo > hi:
            raise Infeasible()
        left = rng.randint(lo, hi)
        r_lo = max(bd.b_lo, bd.res_lo - left)
        r_hi = min(bd.b_hi, bd.res_hi - left)
        if r_lo > r_hi:
            raise Infeasible()
        return left, rng.randint(r_lo, r_hi)

    # 需要进位：先定个位（保证个位之和 ≥ 10），再定十位以上
    for _ in range(_INNER_ATTEMPTS):
        ua = rng.randint(1, 9)
        ub = rng.randint(10 - ua, 9)
        ta_lo = max(0, ceil_div(bd.a_lo - ua, 10))
        ta_hi = (bd.a_hi - ua) // 10
        if ta_lo > ta_hi:
            continue
        ta = rng.randint(ta_lo, ta_hi)
        left = ta * 10 + ua
        tb_lo = max(0,
                    ceil_div(bd.b_lo - ub, 10),
                    ceil_div(bd.res_lo - left - ub, 10))
        tb_hi = min((bd.b_hi - ub) // 10,
                    (bd.res_hi - left - ub) // 10)
        if tb_lo > tb_hi:
            continue
        return left, rng.randint(tb_lo, tb_hi) * 10 + ub
    raise Infeasible()


def _add_with_known(rng, bd: Bounds, known: int, known_on_left: bool,
                    require_carry: bool):
    if known_on_left:
        lo = max(bd.b_lo, bd.res_lo - known)
        hi = min(bd.b_hi, bd.res_hi - known)
    else:
        lo = max(bd.a_lo, bd.res_lo - known)
        hi = min(bd.a_hi, bd.res_hi - known)
    if lo > hi:
        raise Infeasible()
    if not require_carry:
        other = rng.randint(lo, hi)
        return (known, other) if known_on_left else (other, known)
    unit = known % 10
    for unit_other in rng.shuffle([u for u in range(10) if u + unit >= 10]):
        first = first_ge_with_mod(lo, 10, unit_other)
        if first <= hi:
            other = first + 10 * rng.randint(0, (hi - first) // 10)
            return (known, other) if known_on_left else (other, known)
    raise Infeasible()


def _sub_free(rng, bd: Bounds, require_borrow: bool):
    if not require_borrow:
        # 先定减数，再取被减数区间 —— 结果天然非负
        r_lo = max(bd.b_lo, bd.a_lo - bd.res_hi)
        r_hi = min(bd.b_hi, bd.a_hi - bd.res_lo)
        if r_lo > r_hi:
            raise Infeasible()
        right = rng.randint(r_lo, r_hi)
        lo = max(bd.a_lo, bd.res_lo + right, right)
        hi = min(bd.a_hi, bd.res_hi + right)
        if lo > hi:
            raise Infeasible()
        return rng.randint(lo, hi), right

    # 需要退位：先定「被减数个位 < 减数个位」，再让十位以上落在结果区间
    for _ in range(_INNER_ATTEMPTS):
        ub = rng.randint(1, 9)
        ua = rng.randint(0, ub - 1)
        tb_lo = max(0, ceil_div(bd.b_lo - ub, 10))
        tb_hi = (bd.b_hi - ub) // 10
        if tb_lo > tb_hi:
            continue
        right = rng.randint(tb_lo, tb_hi) * 10 + ub
        lo = max(bd.a_lo, bd.res_lo + right, right + 1)
        hi = min(bd.a_hi, bd.res_hi + right)
        if lo > hi:
            continue
        first = first_ge_with_mod(lo, 10, ua)
        if first > hi:
            continue
        return first + 10 * rng.randint(0, (hi - first) // 10), right
    raise Infeasible()


def _sub_with_known(rng, bd: Bounds, known: int, known_on_left: bool,
                    require_borrow: bool):
    if known_on_left:      # known - right = 结果
        lo = max(bd.b_lo, known - bd.res_hi)
        hi = min(bd.b_hi, known - bd.res_lo)
    else:                  # left - known = 结果
        lo = max(bd.a_lo, bd.res_lo + known, known)
        hi = min(bd.a_hi, bd.res_hi + known)
    if lo > hi:
        raise Infeasible()
    if not require_borrow:
        other = rng.randint(lo, hi)
        return (known, other) if known_on_left else (other, known)
    unit = known % 10
    if known_on_left:
        # known - other，需要 known 的个位 < other 的个位
        choices = [u for u in range(10) if u > unit]
    else:
        # other - known，需要 other 的个位 < known 的个位
        choices = [u for u in range(unit)]
    for unit_other in rng.shuffle(choices):
        first = first_ge_with_mod(lo, 10, unit_other)
        if first <= hi:
            other = first + 10 * rng.randint(0, (hi - first) // 10)
            return (known, other) if known_on_left else (other, known)
    raise Infeasible()


def _mul_free(rng, bd: Bounds):
    """乘法：先选一个「位数组合」（D47），再在该组合内按乘积区间反推另一因子。"""
    shapes = list(bd.shapes) if bd.shapes else [(None, None)]
    for shape in rng.shuffle(shapes):
        a_lo, a_hi = shape_span(bd.a_lo, bd.a_hi, shape[0])
        b_lo, b_hi = shape_span(bd.b_lo, bd.b_hi, shape[1])
        if a_lo > a_hi or b_lo > b_hi:
            continue
        lo = max(a_lo, ceil_div(bd.res_lo, b_hi))
        hi = min(a_hi, bd.res_hi // b_lo)
        if lo > hi:
            continue
        left = rng.randint(lo, hi)
        r_lo = max(b_lo, ceil_div(bd.res_lo, left))
        r_hi = min(b_hi, bd.res_hi // left)
        if r_lo > r_hi:
            continue
        return left, rng.randint(r_lo, r_hi)
    raise Infeasible()


def _mul_with_known(rng, bd: Bounds, known: int, known_on_left: bool):
    if known <= 0:
        raise Infeasible()
    known_index = 0 if known_on_left else 1
    known_digits = digit_count(known)
    shapes = list(bd.shapes) if bd.shapes else [(None, None)]
    for shape in rng.shuffle(shapes):
        if shape[known_index] is not None and shape[known_index] != known_digits:
            continue
        other_digits = shape[1 - known_index]
        if known_on_left:
            lo, hi = shape_span(bd.b_lo, bd.b_hi, other_digits)
        else:
            lo, hi = shape_span(bd.a_lo, bd.a_hi, other_digits)
        lo = max(lo, ceil_div(bd.res_lo, known))
        hi = min(hi, bd.res_hi // known)
        if lo > hi:
            continue
        other = rng.randint(lo, hi)
        return (known, other) if known_on_left else (other, known)
    raise Infeasible()


def _div_free(rng, bd: Bounds, allow_remainder: bool):
    right = rng.randint(bd.b_lo, bd.b_hi)
    return _div_solve(rng, bd, right, allow_remainder)


def _div_solve(rng, bd: Bounds, right: int, allow_remainder: bool):
    """除数已定：先定商，再相乘（必要时加余数）得被除数。"""
    if allow_remainder:
        q_lo = max(bd.res_lo, ceil_div(bd.a_lo - (right - 1), right))
        q_hi = min(bd.res_hi, bd.a_hi // right)
    else:
        q_lo = max(bd.res_lo, ceil_div(bd.a_lo, right))
        q_hi = min(bd.res_hi, bd.a_hi // right)
    if q_lo > q_hi:
        raise Infeasible()
    quotient = rng.randint(q_lo, q_hi)
    if not allow_remainder:
        return right * quotient, right, quotient, 0
    r_lo = max(0, bd.a_lo - right * quotient)
    r_hi = min(right - 1, bd.a_hi - right * quotient)
    if r_lo > r_hi:
        raise Infeasible()
    # allow_remainder 是「允许」而非「强制」：多数题带余数，也留一部分整除题
    if r_hi > 0 and rng.randint(0, 9) < 7:
        rem = rng.randint(max(1, r_lo), r_hi)
    elif r_lo <= 0 <= r_hi:
        rem = 0
    else:
        rem = rng.randint(r_lo, r_hi)
    return right * quotient + rem, right, quotient, rem


def build_simple(rng, op: Op, bd: Bounds, *,
                 require_carry: bool = False,
                 require_borrow: bool = False,
                 allow_remainder: bool = False,
                 res_lo=None, res_hi=None,
                 fixed_right=None, fixed_left=None) -> SimpleOp:
    """构造一个满足边界的二元运算。做不到时抛 `Infeasible`。

    `fixed_left` / `fixed_right` 用于脱式的外层：其中一个操作数由内层子表达式给出。
    """
    if fixed_left is not None and fixed_right is not None:
        raise ValueError("build_simple: fixed_left 与 fixed_right 不能同时给定")
    bd = _tighten(bd, res_lo, res_hi)
    known = fixed_left if fixed_left is not None else fixed_right
    known_on_left = fixed_left is not None

    if known is None:
        if op is Op.ADD:
            left, right = _add_free(rng, bd, require_carry)
        elif op is Op.SUB:
            left, right = _sub_free(rng, bd, require_borrow)
        elif op is Op.MUL:
            left, right = _mul_free(rng, bd)
        else:
            left, right, value, rem = _div_free(rng, bd, allow_remainder)
            return _mk(op, left, right, value, rem)
    else:
        known = int(known)
        if op is Op.ADD:
            left, right = _add_with_known(rng, bd, known, known_on_left, require_carry)
        elif op is Op.SUB:
            left, right = _sub_with_known(rng, bd, known, known_on_left, require_borrow)
        elif op is Op.MUL:
            left, right = _mul_with_known(rng, bd, known, known_on_left)
        else:
            if known_on_left:
                # 「被除数已定、反推除数」需要整除搜索，本期脱式不使用（见候选树形规则）
                raise Infeasible()
            if not (bd.b_lo <= known <= bd.b_hi):
                raise Infeasible()
            left, right, value, rem = _div_solve(rng, bd, known, allow_remainder)
            return _mk(op, left, right, value, rem)

    if op is Op.ADD:
        value = left + right
    elif op is Op.SUB:
        value = left - right
    else:
        value = left * right
    return _mk(op, left, right, value)


# --------------------------------------------------------------------------
# 脱式：表达式树
# --------------------------------------------------------------------------

def second_operand_limits(op: Op, bd: Bounds):
    """外层运算的**右**操作数可行区间（该值由内层子表达式产出）。"""
    a_lo, a_hi = bd.operand_span(0)
    b_lo, b_hi = bd.operand_span(1)
    lo, hi = b_lo, b_hi
    if op is Op.ADD:
        lo = max(lo, bd.res_lo - a_hi)
        hi = min(hi, bd.res_hi - a_lo)
    elif op is Op.SUB:
        lo = max(lo, a_lo - bd.res_hi)
        hi = min(hi, a_hi - bd.res_lo)
    elif op is Op.MUL:
        lo = max(lo, ceil_div(bd.res_lo, a_hi))
        hi = min(hi, bd.res_hi // a_lo)
    else:   # DIV: 被除数 = 右操作数 × 商
        lo = max(lo, ceil_div(bd.a_lo, bd.res_hi))
        hi = min(hi, bd.a_hi // bd.res_lo)
    return lo, hi


def first_operand_limits(op: Op, bd: Bounds):
    """外层运算的**左**操作数可行区间（该值由内层子表达式产出）。"""
    a_lo, a_hi = bd.operand_span(0)
    b_lo, b_hi = bd.operand_span(1)
    lo, hi = a_lo, a_hi
    if op is Op.ADD:
        lo = max(lo, bd.res_lo - b_hi)
        hi = min(hi, bd.res_hi - b_lo)
    elif op is Op.SUB:
        lo = max(lo, bd.res_lo + b_lo)
        hi = min(hi, bd.res_hi + b_hi)
    elif op is Op.MUL:
        lo = max(lo, ceil_div(bd.res_lo, b_hi))
        hi = min(hi, bd.res_hi // b_lo)
    else:
        raise Infeasible()      # 左嵌套时外层不会是 ÷（见 _candidate_structures）
    return lo, hi


def _candidate_structures(ops, parentheses: bool):
    """枚举 (内层, 外层, 树形) 候选，保证渲染出的字符串与树结构**逐节点等价**。

    卷面是一个不带括号的字符串，它的分组由优先级 + 左结合决定，因此树形必须
    选得让「按正常规则读字符串」得到的就是这棵树（否则中间结果会对不上）：

      - `prec(内层) > prec(外层)`（如 `a + b × c`）⇒ **右嵌套**：字符串按优先级
        天然读成 `a + (b × c)`；
      - `prec(内层) == prec(外层)`（如 `a + b - c`）⇒ **左嵌套**：字符串左结合，
        读成 `(a + b) - c`；此时右嵌套会得到 `a + (b - c)`，中间结果与字符串不符；
      - `prec(内层) < prec(外层)` ⇒ 必须带括号，`parentheses=False` 时剔除。

    左嵌套要求外层支持「已知左操作数反推右操作数」，即外层不能是 `÷`。
    """
    found = []
    for inner_op in ops:
        for outer_op in ops:
            if parentheses:
                found.append((inner_op, outer_op, RIGHT))
            elif _PREC[inner_op] > _PREC[outer_op]:
                found.append((inner_op, outer_op, RIGHT))
            elif _PREC[inner_op] == _PREC[outer_op] and outer_op is not Op.DIV:
                found.append((inner_op, outer_op, LEFT))
    if not found:
        raise Infeasible()
    if parentheses:
        # 优先挑「括号真的改变了运算顺序」的组合，让含括号题有教学意义
        meaningful = [p for p in found if _right_needs_parens(p[1], p[0])]
        return meaningful or found
    return found


def build_stepwise_tree(config: GenerateConfig, rng, spec, ops):
    """构造脱式表达式树，返回 (树, 外层, 内层)。

    内层结果会作为外层的一个操作数，因此内层的**结果上限**被收窄到
    「外层该操作数的可行区间」，一次构造即可满足两层的边界。
    """
    inner_op, outer_op, structure = rng.choice(
        _candidate_structures(ops, bool(config.parentheses)))
    bd_in = bounds_for(spec, inner_op)
    bd_out = bounds_for(spec, outer_op)
    grouped = bool(config.parentheses)

    if structure is RIGHT:
        lim_lo, lim_hi = second_operand_limits(outer_op, bd_out)
        inner = build_simple(rng, inner_op, bd_in,
                             require_carry=config.require_carry,
                             require_borrow=config.require_borrow,
                             res_lo=lim_lo, res_hi=lim_hi)
        outer = build_simple(rng, outer_op, bd_out,
                             require_carry=config.require_carry,
                             require_borrow=config.require_borrow,
                             fixed_right=inner.value)
        inner_node = BinOp(inner_op, Leaf(inner.left), Leaf(inner.right), grouped=grouped)
        node = BinOp(outer_op, Leaf(outer.left), inner_node, grouped=False)
    else:
        lim_lo, lim_hi = first_operand_limits(outer_op, bd_out)
        inner = build_simple(rng, inner_op, bd_in,
                             require_carry=config.require_carry,
                             require_borrow=config.require_borrow,
                             res_lo=lim_lo, res_hi=lim_hi)
        outer = build_simple(rng, outer_op, bd_out,
                             require_carry=config.require_carry,
                             require_borrow=config.require_borrow,
                             fixed_left=inner.value)
        inner_node = BinOp(inner_op, Leaf(inner.left), Leaf(inner.right), grouped=False)
        node = BinOp(outer_op, inner_node, Leaf(outer.right), grouped=False)
    return node, outer, inner


# --------------------------------------------------------------------------
# 逐步递等过程
# --------------------------------------------------------------------------

def _find_reducible(node, path=()):
    """列出所有「两个子节点都是叶子」的节点：可归约候选。"""
    found = []
    if isinstance(node, BinOp):
        if isinstance(node.left, Leaf) and isinstance(node.right, Leaf):
            key = (0 if node.grouped else 1, -_PREC[node.op], path)
            found.append((key, path, node))
        found.extend(_find_reducible(node.left, path + (0,)))
        found.extend(_find_reducible(node.right, path + (1,)))
    return found


def _replace(node, path, new):
    if not path:
        return new
    head = path[0]
    if head == 0:
        return BinOp(node.op, _replace(node.left, path[1:], new), node.right, node.grouped)
    return BinOp(node.op, node.left, _replace(node.right, path[1:], new), node.grouped)


def make_steps(node) -> list:
    """脱式的逐步递等过程：从原式开始逐次归约，最后一步即答案。

    归约顺序：**先括号内**，同级从左到右；树形本身已保证「先乘除后加减」。
    """
    steps = [render(node)]
    current = node
    for _ in range(_MAX_STEP_NODES):
        if not isinstance(current, BinOp):
            break
        found = _find_reducible(current)
        if not found:
            break
        _, path, target = min(found, key=lambda item: item[0])
        current = _replace(current, path, Leaf(evaluate(target)))
        steps.append(render(current))
    return steps
