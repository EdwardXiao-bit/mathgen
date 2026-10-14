"""M1 验收套件共用的「独立测量仪器」：装载器 + 独立求值器 + 约束检查器。

本模块**不是**测试模块（文件名不以 `test_` 开头，unittest 不会收集它）。

三段职责
--------
1. ``load_types()`` / ``load_env()`` —— 解析冻结契约类型与三个公开入口
   ``generate`` / ``regenerate_one`` / ``grade``。**导入期不 import 实现**：
   实现缺失时不会产生 ImportError / 收集错误，而是抛 ``AssertionError``
   （在 unittest 里记为 FAIL，即"断言失败"，满足任务卡的失败类型要求）。
2. ``ExprInfo`` —— 测试侧**独立实现**的表达式解析与求值（只用标准库）。
   刻意不复用实现方的求值代码，否则"约束完备"就成了自证。
3. ``question_violations()`` / ``random_configs()`` —— 把 ``GenerateConfig`` 的约束
   逐条翻译成对 ``Question`` 的检查，返回可读的违规描述列表。

每条规则的文档出处都写在注释里，便于 Lead 逐条审计。
"""
from __future__ import annotations

import importlib
import random
import re
from decimal import Decimal

# ---------------------------------------------------------------------------
# 契约中的 Question 字段顺序（docs/04-接口契约.md §2.2）
# ---------------------------------------------------------------------------
FIELDS = ("expr", "display", "answer", "steps", "difficulty", "tags", "signature")

# ---------------------------------------------------------------------------
# 1. 公开入口装载
#
# 契约只冻结了**函数签名**（§2.3 / §3），没有冻结模块路径；types.py 的 docstring
# 提到实现分别在 `engine.generate` / `grading.grader`。因此按优先级给出候选，
# 并在断言失败信息里公布实际命中的路径，避免"猜错路径"被误判成"实现没写"。
# ---------------------------------------------------------------------------
_ENTRY_CANDIDATES = {
    "generate": [("app.engine", "generate"), ("app.engine.generate", "generate")],
    "regenerate_one": [("app.engine", "regenerate_one"),
                       ("app.engine.generate", "regenerate_one")],
    "grade": [("app.grading", "grade"), ("app.grading.grader", "grade")],
}
_TYPES_MODULE = "app.engine.types"
_REQUIRED_TYPES = ("Op", "RangeId", "QuestionType", "GenerateConfig", "Question",
                   "Verdict", "ConfigError", "InsufficientQuestions")

_TYPES = None
_TYPES_ERROR = None
_ENV = None
_ENV_ERROR = None


def _try_import(module_name):
    try:
        return importlib.import_module(module_name), None
    except Exception as exc:          # 任何导入失败都转成断言失败
        return None, f"{module_name}: {type(exc).__name__}: {exc}"


def load_types():
    """只装载冻结契约类型（**不要求**实现入口就绪）。

    套件自检（test_m1_harness_selfcheck）用它——那份自检在实现缺位时也应全绿，
    用来证明"套件本身是对的"。
    """
    global _TYPES, _TYPES_ERROR
    if _TYPES is not None:
        return _TYPES
    if _TYPES_ERROR is not None:
        raise _TYPES_ERROR
    mod, err = _try_import(_TYPES_MODULE)
    if err:
        _TYPES_ERROR = AssertionError(
            f"冻结契约模块无法导入（这是断言失败，不是收集错误）：{err}\n"
            "依据：docs/04-接口契约.md §2 / §3，实现见 backend/app/engine/types.py")
        raise _TYPES_ERROR
    missing = [name for name in _REQUIRED_TYPES if getattr(mod, name, None) is None]
    if missing:
        _TYPES_ERROR = AssertionError(
            f"冻结契约缺少类型：{', '.join(missing)}（{_TYPES_MODULE}）\n"
            "types.py 是冻结文件，不得删字段；见 docs/04-接口契约.md §2。")
        raise _TYPES_ERROR
    _TYPES = mod
    return _TYPES


class ContractEnv:
    """解析结果：冻结类型 + 三个公开入口 + 每个入口实际来自哪个模块。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def config(self, **kw):
        """构造 GenerateConfig 的便捷入口（只是转发 kwargs，不改变契约）。"""
        return self.GenerateConfig(**kw)
def _resolve_entry(entry, diagnostics):
    for module_name, attr in _ENTRY_CANDIDATES[entry]:
        mod, err = _try_import(module_name)
        if err:
            diagnostics.append(f"    - {err}")
            continue
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn, f"{module_name}.{attr}"
        diagnostics.append(f"    - {module_name}.{attr}: 不存在或不可调用")
    return None, None


def load_env():
    """解析三个公开入口；任何缺失都以 AssertionError（= 断言失败）抛出。"""
    global _ENV, _ENV_ERROR
    if _ENV is not None:
        return _ENV
    if _ENV_ERROR is not None:
        raise _ENV_ERROR

    types_mod = load_types()
    ns = {name: getattr(types_mod, name) for name in _REQUIRED_TYPES}

    diagnostics = ["  · 已尝试的公开入口："]
    sources = {}
    for entry in _ENTRY_CANDIDATES:
        fn, src = _resolve_entry(entry, diagnostics)
        ns[entry] = fn
        sources[entry] = src

    missing = [e for e in _ENTRY_CANDIDATES if ns[e] is None]
    if missing:
        _ENV_ERROR = AssertionError(
            "M1 公开入口尚未就绪，无法执行验收。\n"
            f"  缺失：{', '.join(missing)}\n" + "\n".join(diagnostics) + "\n"
            "  依据：docs/04-接口契约.md §2.3 / §3（generate / regenerate_one / grade）。\n"
            "  注意：这是**断言失败**，不是 ImportError —— 套件本身可正常导入并运行。")
        raise _ENV_ERROR

    _ENV = ContractEnv(sources=sources, **ns)
    return _ENV


# ---------------------------------------------------------------------------
# 2. 测试侧独立表达式解析 / 求值
# ---------------------------------------------------------------------------

class ExprParseError(AssertionError):
    """测试侧解析器无法解析 ``expr``。

    刻意继承 AssertionError：逃逸出去时在 unittest 里记为**断言失败**，
    而不是 ERROR —— 与任务卡的失败类型约定一致。
    """


_TRANSLATE = str.maketrans({"（": "(", "）": ")", "*": "×", "/": "÷", "x": "×", "X": "×"})
_OPS = "+-×÷"
_OP_TO_ENUM = {"+": "ADD", "-": "SUB", "×": "MUL", "÷": "DIV"}


def tokenize(expr):
    """把算式切成 token 列表：``("num", int)`` 或 ``(运算符, 运算符)``。"""
    if not isinstance(expr, str):
        raise ExprParseError(f"expr 不是字符串：{expr!r}")
    s = expr.translate(_TRANSLATE).replace(" ", "").replace("\u3000", "")
    if not s:
        raise ExprParseError("expr 为空")
    tokens = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            tokens.append(("num", int(s[i:j])))
            i = j
        elif c in _OPS or c in "()":
            tokens.append((c, c))
            i += 1
        else:
            raise ExprParseError(f"expr 含测试侧解析器不认识的字符 {c!r}：{expr!r}")
    return tokens


class _Parser:
    """递归下降：expr := term (('+'|'-') term)* ; term := factor (('×'|'÷') factor)*"""

    def __init__(self, tokens, expr):
        self.tokens = tokens
        self.expr = expr
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def parse(self):
        node = self.expr_()
        if self.pos != len(self.tokens):
            raise ExprParseError(f"expr 有无法解析的残余：{self.expr!r}")
        return node

    def expr_(self):
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.tokens[self.pos][0]
            self.pos += 1
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.factor()
        while self.peek() in ("×", "÷"):
            op = self.tokens[self.pos][0]
            self.pos += 1
            node = ("bin", op, node, self.factor())
        return node

    def factor(self):
        tok = self.peek()
        if tok == "(":
            self.pos += 1
            node = self.expr_()
            if self.peek() != ")":
                raise ExprParseError(f"expr 括号不配对：{self.expr!r}")
            self.pos += 1
            return node
        if tok == "num":
            value = self.tokens[self.pos][1]
            self.pos += 1
            return ("num", value)
        raise ExprParseError(f"expr 语法错误，位置 {self.pos}：{self.expr!r}")


class ExprInfo:
    """一道题 ``expr`` 的结构化信息（全部由测试侧解析得到）。

    属性：
        ast       语法树
        operands  所有叶子操作数
        ops       出现过的运算符字符集合
        nodes     ``(op, 左值, 右值)`` 列表，按后序
        values    每个子表达式的值（除法取商），用于值域检查
        divisions ``(被除数, 除数, 商, 余数)`` 列表
        value     整式最终值（除法取商）
    """

    def __init__(self, expr):
        self.expr = expr
        self.ast = _Parser(tokenize(expr), expr).parse()
        self.operands = []
        self.ops = set()
        self.nodes = []
        self.values = []
        self.divisions = []
        self.node_values = []        # (深度, 值)；根节点深度 0
        self.value = self._walk(self.ast, 0)

    def _walk(self, node, depth):
        if node[0] == "num":
            self.operands.append(node[1])
            self.values.append(node[1])
            return node[1]
        _, op, left, right = node
        lv = self._walk(left, depth + 1)
        rv = self._walk(right, depth + 1)
        self.ops.add(op)
        self.nodes.append((op, lv, rv))
        if op == "+":
            value = lv + rv
        elif op == "-":
            value = lv - rv
        elif op == "×":
            value = lv * rv
        else:
            if rv == 0:
                raise ExprParseError(f"除数为 0：{self.expr!r}")
            quotient, remainder = divmod(lv, rv)
            self.divisions.append((lv, rv, quotient, remainder))
            value = quotient
        self.node_values.append((depth, value))
        self.values.append(value)
        return value

    # -- 派生判定 ----------------------------------------------------------
    @property
    def intermediate_values(self):
        """非根内部子表达式的值 —— 也就是脱式的「中间步骤结果」。"""
        return [v for depth, v in self.node_values if depth > 0]

    @property
    def has_remainder(self):
        return any(rem != 0 for _a, _b, _q, rem in self.divisions)

    @property
    def has_parentheses(self):
        return "(" in self.expr and ")" in self.expr

    def commutative_key(self):
        """交换律归一的题面指纹（FR-E7：``3+5`` 与 ``5+3`` 视为同一题）。"""
        return _comm_key(self.ast)


def _comm_key(node):
    if node[0] == "num":
        return str(node[1])
    _, op, left, right = node
    a, b = _comm_key(left), _comm_key(right)
    if op in "+×":
        a, b = sorted((a, b))
    return f"({a}{op}{b})"


def last_int_tokens(text):
    """取出字符串里所有整数 token（用于脱式末步与 answer 的自洽校验）。"""
    return [int(t) for t in re.findall(r"\d+", text or "")]


# ---------------------------------------------------------------------------
# 2b. 递等式折叠重演器（D46 独立复核用）
#
# 刻意**不**复用 ExprInfo 的 AST：用另一套 token 级算法重演「有括号先算、
# 先乘除后加减、同级从左到右」，两条独立路径互证。
# ---------------------------------------------------------------------------

_PRECEDENCE = {"+": 1, "-": 1, "×": 2, "÷": 2}


def _tokenize_for_fold(expr):
    s = expr.translate(_TRANSLATE).replace(" ", "").replace("\u3000", "")
    tokens = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            tokens.append(s[i:j])
            i = j
        elif c in _OPS or c in "()":
            tokens.append(c)
            i += 1
        else:
            raise ExprParseError(f"折叠器无法识别字符 {c!r}：{expr!r}")
    return tokens


def _next_fold_index(tokens):
    """下一个可折叠算符的下标：两侧都是数字，取优先级最高者，同级取最左。"""
    best = None
    for i in range(1, len(tokens) - 1):
        if (tokens[i] in _PRECEDENCE and tokens[i - 1].isdigit()
                and tokens[i + 1].isdigit()):
            if best is None or _PRECEDENCE[tokens[i]] > _PRECEDENCE[tokens[best]]:
                best = i
    return best


def _fold_apply(op, left, right):
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "×":
        return left * right
    if right == 0:
        raise ExprParseError(f"折叠时遇到除数为 0：{left} ÷ {right}")
    return left // right          # 商（余数不参与「值」）


def fold_sequence(expr):
    """重演递等式的折叠序列，返回 ``[(折叠值, 折叠后剩余式子的整数列表), ...]``。

    每一步对应递等式的一行：把**当前该算**的那个运算折成一个数。
    用途：独立判定 D46「`steps` 必须左结合（从左到右）」，完全不看 `steps` 本身。
    """
    tokens = _tokenize_for_fold(expr)
    steps = []
    while len(tokens) > 1:
        index = _next_fold_index(tokens)
        if index is None:
            raise ExprParseError(f"无法继续折叠：{expr!r} → {tokens!r}")
        value = _fold_apply(tokens[index], int(tokens[index - 1]), int(tokens[index + 1]))
        tokens = tokens[:index - 1] + [str(value)] + tokens[index + 2:]
        # 折叠后把已经变成单值的括号收起来： "( 20 )" -> "20"
        changed = True
        while changed:
            changed = False
            for i in range(len(tokens) - 2):
                if tokens[i] == "(" and tokens[i + 2] == ")" and tokens[i + 1].isdigit():
                    tokens = tokens[:i] + [tokens[i + 1]] + tokens[i + 3:]
                    changed = True
                    break
        steps.append((value, sorted(int(t) for t in tokens if t.isdigit())))
    return steps


def step_order_violations(expr, steps):
    """按 D46 判定 `steps` 是否严格左结合；返回违规描述（空 = 合规）。

    判定只用**每行出现的整数集合**，因此与书写格式（空格、是否带 `=`）无关：
    第 k 行应当恰好是「第 k 次折叠后的式子」，即整数集合为
    ``{本次折叠值} ∪ {尚未参与运算的操作数}``。
    """
    steps = list(steps or [])
    if not steps:
        return [f"steps 为空（expr={expr!r}）"]
    outs = []
    if steps[0].replace(" ", "") != expr.replace(" ", ""):
        outs.append(f"steps 首行应为原式：{steps[0]!r} != {expr!r}")
    try:
        folds = fold_sequence(expr)
    except ExprParseError as exc:
        return [f"折叠器无法重演 {expr!r}：{exc}"]
    if len(steps) != len(folds) + 1:
        outs.append(f"steps 行数应为 {len(folds) + 1}（原式 + {len(folds)} 次折叠），"
                    f"实为 {len(steps)}：{steps!r}")
        return outs
    for k, (value, remaining) in enumerate(folds):
        actual = sorted(last_int_tokens(steps[k + 1]))
        if actual != remaining:
            outs.append(
                f"第 {k + 1} 行不是「左结合折叠第 {k + 1} 步」：期望整数集合 {remaining}"
                f"（本次应折出 {value}），实际 {actual}；steps={steps!r}")
    return outs


# ---------------------------------------------------------------------------
# 3. 判分联动的扰动器
# ---------------------------------------------------------------------------

_REMAINDER_RE = re.compile(r"^\s*(-?\d+)\s*(?:余|…|\.{3,}|r|R)\s*(-?\d+)\s*$")
_PLAIN_INT_RE = re.compile(r"^\s*(-?\d+)\s*$")
_DECIMAL_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")


def perturb_answer(answer):
    """对标准答案做**一处数值扰动**（商/结果 +1），用于"判分必须判错"的联动断言。"""
    m = _REMAINDER_RE.match(answer)
    if m:
        return f"{int(m.group(1)) + 1}余{int(m.group(2))}"
    m = _PLAIN_INT_RE.match(answer)
    if m:
        return str(int(m.group(1)) + 1)
    if _DECIMAL_RE.match(answer):
        return str(Decimal(answer.strip()) + 1)
    raise AssertionError(f"测试侧无法对 answer 做数值扰动：{answer!r}")


def answer_number(answer):
    """取 answer 的数值部分（余数题取商）；无法解析返回 None。"""
    m = _REMAINDER_RE.match(answer)
    if m:
        return Decimal(m.group(1))
    if _DECIMAL_RE.match(answer):
        return Decimal(answer.strip())
    return None


# ---------------------------------------------------------------------------
# 4. 约束检查
# ---------------------------------------------------------------------------

def _digits(value):
    return len(str(abs(int(value))))


def _plural(values, lo, hi):
    return [v for v in values if not (lo <= v <= hi)]


def _bounded_values(info):
    """**硬检查**只看：所有叶子操作数 + 整式最终值。

    为什么不查 `expr` 的「从左到右中间值」：实现可以自行选择求值顺序
    （引擎实测把 `53 + 52 - 17` 写成 `= 53 + 35`），那种查法是假阳性。
    答案版真正打印出来的数值由 `step_value_violations` 负责。
    """
    return list(info.operands) + [info.value]


def range_violations(range_id, info, max_value):
    """返回该档位下被违反的规则描述（空列表 = 该档位符合）。

    档位定义出处：docs/00-决策记录.md《数值范围档位表（D16）》。
    """
    rng = str(getattr(range_id, "value", range_id))
    outs = []
    values = _bounded_values(info)
    if rng == "R1":      # 20 以内加减法
        bad = _plural(values, 0, 20)
        if bad:
            outs.append(f"R1（20 以内）出现越界值：{bad}")
    elif rng == "R2":    # 100 以内加减法
        bad = _plural(values, 0, 100)
        if bad:
            outs.append(f"R2（100 以内）出现越界值：{bad}")
    elif rng == "R3":    # 表内乘除法（九九表范围）
        for op, lv, rv in info.nodes:
            if op == "×" and not (0 <= lv <= 9 and 0 <= rv <= 9):
                outs.append(f"R3（九九表）乘法因数越界：{lv} × {rv}")
            if op == "÷":
                if not 0 <= rv <= 9:
                    outs.append(f"R3（九九表）除数越界：{lv} ÷ {rv}")
                if not 0 <= lv <= 81:
                    outs.append(f"R3（九九表）被除数越界：{lv} ÷ {rv}")
        for a, b, q, _rem in info.divisions:
            if not 0 <= q <= 9:
                outs.append(f"R3（九九表）商越界：{a} ÷ {b} = {q}")
    elif rng == "R4":    # 两位数 × 一位数 / 两位数 ÷ 一位数
        for op, lv, rv in info.nodes:
            if op == "×" and sorted([_digits(lv), _digits(rv)]) != [1, 2]:
                outs.append(f"R4 要求 两位数×一位数，实得 {lv} × {rv}")
            if op == "÷" and not (_digits(lv) == 2 and _digits(rv) == 1):
                outs.append(f"R4 要求 两位数÷一位数，实得 {lv} ÷ {rv}")
    elif rng == "R5":    # 三位数 × 两位数 / 三位数 ÷ 两位数
        for op, lv, rv in info.nodes:
            if op == "×" and sorted([_digits(lv), _digits(rv)]) != [2, 3]:
                outs.append(f"R5 要求 三位数×两位数，实得 {lv} × {rv}")
            if op == "÷" and not (_digits(lv) == 3 and _digits(rv) == 2):
                outs.append(f"R5 要求 三位数÷两位数，实得 {lv} ÷ {rv}")
    elif rng == "R6":    # 四位数及以上多位数加减
        for v in info.operands:
            if _digits(v) < 4:
                outs.append(f"R6 要求四位数及以上加减，实得操作数 {v}")
    elif rng == "R7":    # 老师自定义上限
        if max_value is None:
            outs.append("R7 需要 max_value，但未提供")
        else:
            bad = _plural(values, 0, int(max_value))
            if bad:
                outs.append(f"R7（≤{max_value}）出现越界值：{bad}")
    else:
        outs.append(f"未知档位 {rng!r}")
    return outs


def question_violations(env, config, question, index):
    """独立检查一道题是否满足 config 的全部约束，返回违规描述列表（空 = 符合）。"""
    outs = []
    prefix = f"第 {index} 题"

    if not isinstance(question.expr, str) or not question.expr.strip():
        return [f"{prefix} expr 为空"]

    try:
        info = ExprInfo(question.expr)
    except ExprParseError as exc:
        return [f"{prefix} expr 无法解析（expr={question.expr!r}）：{exc}"]

    # --- 运算类型 ∈ config.ops（FR-E1）-------------------------------------
    allowed = {str(getattr(op, "value", op)) for op in config.ops}
    for char in sorted(info.ops):
        if char not in allowed:
            outs.append(f"{prefix} 出现未选中的运算 {char!r}（expr={question.expr!r}）")

    # --- 值域：落在**至少一个**选中档位内（FR-E2，多选即并集语义）----------
    per_range = []
    ok_any = False
    for rng in config.ranges:
        violations = range_violations(rng, info, config.max_value)
        per_range.append((rng, violations))
        if not violations:
            ok_any = True
    if not ok_any and per_range:
        detail = "；".join(
            f"{getattr(r, 'value', r)}: {'; '.join(v) if v else '符合'}" for r, v in per_range)
        outs.append(f"{prefix} 不满足任何选中档位（expr={question.expr!r}）→ {detail}")
    if not per_range:
        outs.append(f"{prefix} config.ranges 为空")

    # --- 括号（FR-E4）------------------------------------------------------
    if config.parentheses and not info.has_parentheses:
        outs.append(f"{prefix} 要求含括号，但 expr={question.expr!r}")

    # --- 进位 / 退位（FR-E4；docs/06 §4.1 ④：进位指加法个位和 ≥10）----------
    if config.require_carry:
        for op, lv, rv in info.nodes:
            if op == "+" and (lv % 10) + (rv % 10) < 10:
                outs.append(f"{prefix} 要求进位，但 {lv} + {rv} 未进位")
    if config.require_borrow:
        for op, lv, rv in info.nodes:
            if op == "-" and lv % 10 >= rv % 10:
                outs.append(f"{prefix} 要求退位，但 {lv} - {rv} 未退位")

    # --- 除法整除 / 余数（FR-E4）------------------------------------------
    if config.exact_division:
        for a, b, q, rem in info.divisions:
            if rem != 0:
                outs.append(f"{prefix} 要求整除，但 {a} ÷ {b} = {q} 余 {rem}")

    # --- 题型（FR-E5 / FR-E12）--------------------------------------------
    qtype = str(getattr(config.question_type, "value", config.question_type))
    steps = list(question.steps) if isinstance(question.steps, (list, tuple)) else None
    if steps is None:
        outs.append(f"{prefix} steps 不是列表：{question.steps!r}")
    elif qtype == "stepwise":
        if len(steps) < 2:
            outs.append(f"{prefix} 脱式题 steps 长度应 ≥2，实为 {len(steps)}：{steps!r}")
    else:
        if len(steps) != 0:
            outs.append(f"{prefix} 非脱式题 steps 应为空（契约 §2.2），实为 {steps!r}")

    if qtype == "fill":
        if "□" not in (question.display or ""):
            outs.append(f"{prefix} 填空题 display 必须含 □，实为 {question.display!r}")
        if "□" in (question.expr or ""):
            outs.append(f"{prefix} 填空题 expr 不应含 □（契约 §2.2：□ 只出现在 display）："
                        f"{question.expr!r}")

    # --- 难度 / 标签 / 签名（FR-E10 / E11 / E7）----------------------------
    if not isinstance(question.difficulty, int) or not 1 <= question.difficulty <= 5:
        outs.append(f"{prefix} difficulty 应为 1..5 整数，实为 {question.difficulty!r}")
    if not isinstance(question.tags, (list, tuple)) or not question.tags:
        outs.append(f"{prefix} tags 非空（FR-E11），实为 {question.tags!r}")
    elif not all(isinstance(t, str) and t.strip() for t in question.tags):
        outs.append(f"{prefix} tags 含空标签：{question.tags!r}")
    if not isinstance(question.signature, str) or not question.signature.strip():
        outs.append(f"{prefix} signature 为空（FR-E7）")

    return outs


#: 施加「数值上限」的档位（R1/R2/R7）。R3–R6 的约束是**结构性的**
#: （位数模式 / 因数范围），由 range_violations 逐节点检查，不在此列。
_NUMERIC_CAP = {"R1": lambda config: 20, "R2": lambda config: 100,
                "R7": lambda config: config.max_value}


def step_value_violations(env, config, questions):
    """脱式题 **steps 里实际出现的数值** 必须落在有数值上限的档位内（R1/R2/R7）。

    为什么**不**对 `expr` 做「从左到右求值」再查中间值 —— 见验收报告 F-2：
    实现可以自行选择求值顺序（引擎实测会把 `53 + 52 - 17` 写成 `= 53 + 35`），
    从左到右只是其中一种读法。拿 `expr` 的左到右中间值当缺陷是**假阳性**。
    这里改为检查**答案版真正打印出来的每一步数值**，既尊重实现自由，
    又能抓住真正越界的数字（例如 `= 161 - 91`）。
    """
    outs = []
    for index, question in enumerate(questions):
        steps = list(question.steps or [])
        if not steps:
            continue
        values = []
        for step in steps:
            values.extend(last_int_tokens(step))
        if not values:
            continue
        acceptable = False
        details = []
        for rng in config.ranges:
            rid = str(getattr(rng, "value", rng))
            cap = _NUMERIC_CAP.get(rid)
            if cap is None:
                acceptable = True         # 该档位不施加数值上限
                break
            limit = cap(config)
            if limit is None:
                continue
            bad = sorted({v for v in values if not 0 <= v <= limit})
            if not bad:
                acceptable = True
                break
            details.append(f"{rid}（上限 {limit}）steps 里出现越界数值：{bad}")
        if not acceptable and details:
            outs.append(f"第 {index} 题 expr={question.expr!r} steps={steps!r} → "
                        + "；".join(details))
    return outs


def paper_violations(env, config, questions):
    """整卷层面的检查：题量、签名去重（FR-E6 / FR-E7）。"""
    outs = []
    if len(questions) != config.count:
        outs.append(f"题量应为 config.count={config.count}，实得 {len(questions)}")

    signatures = [getattr(q, "signature", None) for q in questions]
    duplicates = {}
    for i, sig in enumerate(signatures):
        duplicates.setdefault(sig, []).append(i)
    for sig, indexes in duplicates.items():
        if len(indexes) > 1:
            outs.append(f"单卷内 signature 重复：{sig!r} 出现在第 {indexes} 题")
    return outs


def commutative_duplicates(questions):
    """返回交换律等价的题目对（FR-E7：``3+5`` 与 ``5+3`` 不得同卷）。

    这是**不依赖实现 signature 格式**的独立检查：test 侧自己归一化题面。
    只对 expr 可解析的题（口算 / 脱式）有效。
    """
    seen = {}
    pairs = []
    for i, q in enumerate(questions):
        try:
            key = ExprInfo(q.expr).commutative_key()
        except ExprParseError:
            continue
        if key in seen:
            pairs.append((seen[key], i, key))
        else:
            seen[key] = i
    return pairs


# ---------------------------------------------------------------------------
# 5. 代表性配置 & 随机合法配置
# ---------------------------------------------------------------------------

# 档位 → 允许的运算（严格按 D16 档位表配对，避免产生"档位与运算不匹配"的伪配置）
_POOL_SPECS = [
    ("R1", ["ADD", "SUB"]),
    ("R2", ["ADD", "SUB"]),
    ("R3", ["MUL", "DIV"]),
    ("R4", ["MUL", "DIV"]),
    ("R5", ["MUL", "DIV"]),
    ("R6", ["ADD", "SUB"]),
    ("R7", ["ADD", "SUB", "MUL", "DIV"]),
]
# 同组内可用于多选（FR-E2 档位可多选）的配对：并集语义，检查取"任一档位接受即通过"
_MULTI_RANGE_PAIRS = {("R1", "R2"), ("R3", "R4"), ("R4", "R5")}


def representative_configs(env):
    """≥5 个有代表性的配置（docs/06-测试方案.md §4.2 点名的那几类）。"""
    Op, RangeId, QuestionType = env.Op, env.RangeId, env.QuestionType
    return [
        ("R1 口算（20 以内加减）", env.GenerateConfig(
            ops={Op.ADD, Op.SUB}, ranges=[RangeId.R1],
            question_type=QuestionType.ORAL, count=20)),
        ("R3 表内乘除（九九表）", env.GenerateConfig(
            ops={Op.MUL, Op.DIV}, ranges=[RangeId.R3],
            question_type=QuestionType.ORAL, exact_division=True, count=20)),
        ("R5 三位数×两位数带进位", env.GenerateConfig(
            ops={Op.MUL}, ranges=[RangeId.R5],
            question_type=QuestionType.ORAL, require_carry=True, count=20)),
        ("脱式含括号", env.GenerateConfig(
            ops={Op.ADD, Op.MUL}, ranges=[RangeId.R2],
            question_type=QuestionType.STEPWISE, parentheses=True, count=15)),
        ("填空求未知数", env.GenerateConfig(
            ops={Op.ADD, Op.SUB}, ranges=[RangeId.R2],
            question_type=QuestionType.FILL, count=20)),
        ("R7 自定义上限", env.GenerateConfig(
            ops={Op.MUL, Op.ADD}, ranges=[RangeId.R7], max_value=999,
            question_type=QuestionType.ORAL, count=15)),
        ("R4 有余数除法（判分余数写法联动）", env.GenerateConfig(
            ops={Op.DIV}, ranges=[RangeId.R4],
            question_type=QuestionType.ORAL, allow_remainder=True, count=12)),
    ]


def random_configs(env, count=60, seed=20261014):
    """生成 ``count`` 组合法的随机 GenerateConfig。

    只生成**契约允许**的组合（docs/04-接口契约.md §2.1 的五条约束规则都不会被触发）：
      - exact_division 与 allow_remainder 不同时为真
      - 含 R7 必带 max_value 且 ≥10
      - STEPWISE 至少两种运算或含括号
      - FILL 不带括号
      - count ∈ 1..100
    档位与运算按 D16 配对；进位/退位只在单一运算下开启，避开
    「混合运算里 require_carry 指哪一次加法」的语义分歧（见验收报告"契约存疑"一节）。
    """
    rng = random.Random(seed)
    Op, RangeId, QuestionType = env.Op, env.RangeId, env.QuestionType
    out = []
    for i in range(count):
        pool_name, op_names = _POOL_SPECS[i % len(_POOL_SPECS)]
        k = rng.randint(1, len(op_names))
        chosen = rng.sample(op_names, k)
        ops = {getattr(Op, name) for name in chosen}

        qtype = rng.choice([QuestionType.ORAL, QuestionType.ORAL,
                            QuestionType.STEPWISE, QuestionType.FILL])
        if qtype is QuestionType.STEPWISE:
            # 规则：STEPWISE 至少两种运算或含括号
            if len(chosen) < 2:
                chosen = rng.sample(op_names, 2)
                ops = {getattr(Op, name) for name in chosen}
            parentheses = rng.random() < 0.5
        else:
            parentheses = False

        exact_division = allow_remainder = False
        if "DIV" in chosen:
            if pool_name == "R3":       # 表内除法按九九表语义，强制整除
                exact_division = True
            else:
                roll = rng.random()
                if roll < 0.4:
                    exact_division = True
                elif roll < 0.8:
                    allow_remainder = True

        require_carry = "ADD" in chosen and len(chosen) == 1 and rng.random() < 0.5
        require_borrow = "SUB" in chosen and len(chosen) == 1 and rng.random() < 0.5

        ranges = [getattr(RangeId, pool_name)]
        if rng.random() < 0.25:
            partner = {"R1": "R2", "R2": "R1", "R3": "R4", "R4": "R5",
                       "R5": "R4", "R6": "R2"}.get(pool_name)
            if partner:
                ranges = [getattr(RangeId, pool_name), getattr(RangeId, partner)]

        max_value = None
        if pool_name == "R7":
            max_value = rng.choice([50, 100, 999])

        tight = require_carry or require_borrow
        if tight:
            cnt = rng.randint(5, 12)
        elif pool_name == "R7":
            cnt = rng.randint(5, 15)
        else:
            cnt = rng.randint(5, 20)

        out.append(env.GenerateConfig(
            ops=ops, ranges=ranges, question_type=qtype,
            max_value=max_value, require_carry=require_carry,
            require_borrow=require_borrow, exact_division=exact_division,
            allow_remainder=allow_remainder, parentheses=parentheses, count=cnt))
    return out


# ---------------------------------------------------------------------------
# 6. 快照与差异报告
# ---------------------------------------------------------------------------

def snapshot(questions):
    """把题目列表变成可比较的普通结构（逐字段，含列表内容）。"""
    return [{
        "expr": q.expr, "display": q.display, "answer": q.answer,
        "steps": list(q.steps), "difficulty": q.difficulty,
        "tags": list(q.tags), "signature": q.signature,
    } for q in questions]


def diff_report(left, right):
    """返回两卷第一个不同点的可读描述；完全相同返回空串。"""
    if len(left) != len(right):
        return f"题量不同：{len(left)} vs {len(right)}"
    for i, (a, b) in enumerate(zip(left, right)):
        for key in FIELDS:
            if a[key] != b[key]:
                return f"第 {i} 题字段 {key} 不同：{a[key]!r} vs {b[key]!r}"
    return ""


def describe(config):
    """配置的紧凑可读描述，用于失败信息。"""
    def name(x):
        return str(getattr(x, "value", x))
    ops = ",".join(sorted(name(o) for o in config.ops))
    ranges = ",".join(name(r) for r in config.ranges)
    return (f"GenerateConfig(ops={{{ops}}}, ranges=[{ranges}], "
            f"type={name(config.question_type)}, max_value={config.max_value}, "
            f"carry={config.require_carry}, borrow={config.require_borrow}, "
            f"exact={config.exact_division}, rem={config.allow_remainder}, "
            f"paren={config.parentheses}, count={config.count})")


# ---------------------------------------------------------------------------
# 7. 公开入口调用包装：把"返回类型不对"变成**断言失败**而不是 AttributeError
# ---------------------------------------------------------------------------

def call_generate(case, env, config, seed):
    """调用 generate 并校验返回形态；返回 list[Question]。"""
    result = env.generate(config, seed)
    case.assertIsInstance(
        result, list,
        f"generate 必须返回 list[Question]，实得 {type(result).__name__}"
        f"（config={describe(config)}, seed={seed}）")
    for i, question in enumerate(result):
        case.assertIsInstance(
            question, env.Question,
            f"generate 返回的第 {i} 个元素不是 Question，实得 {type(question)!r}")
    return result


def call_grade(case, env, question, raw_answer):
    """调用 grade 并校验 Verdict 形态（契约 §3：is_correct + normalized_input）。"""
    verdict = env.grade(question, raw_answer)
    case.assertIsInstance(
        verdict, env.Verdict,
        f"grade 必须返回 Verdict，实得 {type(verdict).__name__}")
    case.assertIsInstance(
        getattr(verdict, "is_correct", None), bool,
        f"Verdict.is_correct 必须是 bool，实得 {verdict.is_correct!r}")
    case.assertIsInstance(
        getattr(verdict, "normalized_input", None), str,
        f"Verdict.normalized_input 必须是 str，实得 {verdict.normalized_input!r}")
    return verdict


def paper_violations_all(env, config, questions):
    """整卷 + 逐题的违规汇总。"""
    outs = paper_violations(env, config, questions)
    for index, question in enumerate(questions):
        outs.extend(question_violations(env, config, question, index))
    return outs
