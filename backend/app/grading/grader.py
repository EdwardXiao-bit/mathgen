"""判分器实现（FR-S7）。

契约：`docs/04-接口契约.md` §3（**冻结**）。

冻结签名：

    def grade(question: Question, raw_answer: str) -> Verdict: ...

**归一化规则顺序固定**（契约 §3），实现与测试都必须按此顺序：

    1. 去除首尾与中间空白
    2. 全角数字 / 运算符 → 半角
    3. ``*`` ``/`` ``x`` ``X`` → ``×`` ``÷``；中文括号 ``（）`` → 半角
    4. 余数写法统一：``7 余 2``、``7…2``、``7r2`` → 统一内部形式
    5. 数值比较用 ``decimal.Decimal``（**禁止浮点直接比较**）

**已确认不做**：脱式题（``QuestionType.STEPWISE``）只判**最终答案**，过程不计分——
过程规范性无法可靠自动判定。因此本题型与口算题走完全相同的判定路径，
答案 ``5`` 与学生输入 ``(12+8)÷4`` 判**错**（不判过程，只比数值）。

**纪律**（契约 §8，冻结）：本模块不得 import 数据库 / FastAPI / requests / 任何 I/O；
``grade`` 是**纯函数**——同输入必得同输出，不读写文件、网络或全局状态。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.engine.types import Question, Verdict

__all__ = ["grade", "normalize_answer"]


# ---------------------------------------------------------------- 归一化

# 第 2 步：全角 → 半角。用显式映射而非 ``chr(ord(c) - 0xFEE0)``，
# 语义一目了然，且不会误伤该区间内的非数字符号。
_FULLWIDTH_MAP = {
    # 全角数字 ０-９
    "\uff10": "0", "\uff11": "1", "\uff12": "2", "\uff13": "3", "\uff14": "4",
    "\uff15": "5", "\uff16": "6", "\uff17": "7", "\uff18": "8", "\uff19": "9",
    # 全角运算符与括号
    "\uff0b": "+",   # ＋
    "\uff0d": "-",   # －
    "\uff0a": "*",   # ＊（第 3 步再归一为 ×）
    "\uff0f": "/",   # ／（第 3 步再归一为 ÷）
    "\uff08": "(",   # （
    "\uff09": ")",   # ）
    "\uff1d": "=",   # ＝
    "\uff0e": ".",   # ．
    "\uff0c": ",",   # ，
    # 空白类：全角空格 / 不换行空格，统一成半角空格
    "\u3000": " ",
    "\u00a0": " ",
}

# 第 3 步：运算符归一。``x`` / ``X`` 按契约与 ``*`` 等价。
_MUL_AS_WORD = str.maketrans({"*": "×", "x": "×", "X": "×"})
_DIV_AS_WORD = str.maketrans({"/": "÷"})

# 第 4 步：余数分隔符 → 统一内部形式 ``…``（契约 §3 的写法之一，语义无歧义）。
_REMAINDER_SEPARATORS = ("余", "…", "r", "R")


def normalize_answer(raw_answer: str) -> str:
    """把学生答案（或标准答案）归一化为内部规范写法。

    规则与顺序严格对齐契约 §3 的 1–4 条；第 5 条（``Decimal`` 等值）在
    :func:`grade` 的比较阶段完成，不属于本函数的职责。

    归一化是**幂等**的：``normalize_answer(normalize_answer(x)) == normalize_answer(x)``。
    """
    if raw_answer is None:
        return ""
    text = str(raw_answer)

    # 第 1 步：去除中间空白（含制表/换行），再交第 2 步处理全角空白
    text = "".join(text.split())

    # 第 2 步：全角数字 / 运算符 → 半角
    text = "".join(_FULLWIDTH_MAP.get(ch, ch) for ch in text)

    # 全角空白是在第 2 步才变成半角空格的，此处再清一次，保证第 2 步之后无空白。
    # （契约第 1 步要求"去除中间空白"，顺序上属于第 1 步的收尾清理。）
    text = "".join(text.split())

    # 第 3 步：* x X → ×，/ → ÷；中文括号在第 2 步已随全角映射变成半角
    text = text.translate(_MUL_AS_WORD).translate(_DIV_AS_WORD)

    # 第 4 步：余数写法统一为 a…b
    for sep in _REMAINDER_SEPARATORS:
        text = text.replace(sep, "…")

    return text


# ---------------------------------------------------------------- 取值

def _extract_value(text: str) -> tuple[Decimal, Decimal] | None:
    """把归一化后的字符串解析成可比较的两元组。

    - 纯数值 ``"18.0"``        → ``(18.0, 0)``
    - 余数式 ``"7…2"``         → ``(7, 2)``
    - 其它（含算式、空串、非法数字）→ ``None``，即"不可判定为数值"

    解析后的比较用 :class:`~decimal.Decimal` 精确等值，不做四舍五入、不用浮点。
    """
    if text == "":
        return None

    if "…" in text:
        quotient_text, sep, remainder_text = text.partition("…")
        if not sep or "…" in remainder_text:
            # 出现多个余数分隔符（如 "7…2…1"）不是合法写法
            return None
        quotient = _parse_decimal(quotient_text)
        remainder = _parse_decimal(remainder_text)
        if quotient is None or remainder is None:
            return None
        return (quotient, remainder)

    value = _parse_decimal(text)
    if value is None:
        return None
    return (value, Decimal(0))


def _parse_decimal(text: str) -> Decimal | None:
    """严格解析十进制数；非法输入、``NaN``、``Infinity`` 一律视为不可判定。"""
    if text == "":
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    if not value.is_finite():
        return None
    return value


# ---------------------------------------------------------------- 判分

def grade(question: Question, raw_answer: str) -> Verdict:
    """判定学生答案是否正确，返回 :class:`Verdict`（FR-S7）。

    - 学生输入先经 :func:`normalize_answer` 归一化，结果放进
      ``Verdict.normalized_input``（老师端展示与排错用）。
    - 标准答案 ``question.answer`` 同样走一遍归一化，避免卷面快照的写法差异
      影响判定。
    - 只有**两侧都能解析成数值**时才可能判对；一侧是算式（如 ``(12+8)÷4``）
      时判错——契约 §3 明确"不判过程，只比数值"，本版**不做算式求值**。

    纯函数：不读全局状态、不修改传入的 ``question``、同输入必得同输出。
    """
    normalized = normalize_answer(raw_answer)

    student_value = _extract_value(normalized)
    if student_value is None:
        return Verdict(is_correct=False, normalized_input=normalized)

    expected_value = _extract_value(normalize_answer(question.answer))
    if expected_value is None:
        return Verdict(is_correct=False, normalized_input=normalized)

    return Verdict(
        is_correct=student_value == expected_value,
        normalized_input=normalized,
    )
