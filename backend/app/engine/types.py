"""出题引擎与判分的数据契约 —— **冻结**。

本文件由 Lead 维护，是 `docs/04-接口契约.md` §2 / §3 的代码化实现。
**engine 与 grading 的实现方不得直接修改本文件**；如需变更字段，先在
`docs/00-决策记录.md` 新增决策，再由 Lead 更新本文件与契约文档。

冻结的函数签名（实现分别在 engine.generate / grading.grader）：

    def generate(config: GenerateConfig, seed: int) -> list[Question]: ...
    def regenerate_one(config: GenerateConfig, seed: int, seq: int,
                       existing: list[Question]) -> Question: ...
    def grade(question: Question, raw_answer: str) -> Verdict: ...

纪律（docs/04-接口契约.md §8，冻结）：
  - 两个实现包都不得 import 数据库、FastAPI、requests 或任何 I/O 模块
  - `generate` 必须是**纯函数**且**确定性**：同 (config, seed) 必得逐题相同的结果

容器可变性说明：为与契约文档逐字一致，`ops` 用 `set`、`ranges` 用 `list`。
调用方**不得在调用之间修改其内容**，否则确定性无法保证。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Op",
    "RangeId",
    "QuestionType",
    "GenerateConfig",
    "Question",
    "Verdict",
    "ConfigError",
    "InsufficientQuestions",
]


class Op(str, Enum):
    """运算类型（FR-E1）。值即卷面使用的运算符。"""

    ADD = "+"
    SUB = "-"
    MUL = "×"
    DIV = "÷"


class RangeId(str, Enum):
    """数值范围档位（D16 档位表，FR-E2）。

    R1 20 以内加减 / R2 100 以内加减 / R3 表内乘除 /
    R4 两位数×一位数、两位数÷一位数 / R5 三位数×两位数、三位数÷两位数 /
    R6 四位数及以上多位数加减 / R7 老师自定义上限
    """

    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"
    R6 = "R6"
    R7 = "R7"


class QuestionType(str, Enum):
    """题型（D17，FR-E5）。"""

    ORAL = "oral"          # 口算横式，如 37 + 45 = ____
    STEPWISE = "stepwise"  # 脱式 / 递等式，需逐步过程
    FILL = "fill"          # 填空求未知数，如 7 + □ = 15


@dataclass(frozen=True)
class GenerateConfig:
    """出题参数（值对象，不可变）。

    字段顺序即契约顺序。注意 `question_type` 是无默认值的必需字段，
    因此必须排在所有带默认值的字段之前（Python 语法要求）。
    """

    ops: set[Op]
    ranges: list[RangeId]
    question_type: QuestionType
    max_value: int | None = None      # 仅当 ranges 含 R7 时必填（FR-E3）
    require_carry: bool = False       # 必须进位
    require_borrow: bool = False      # 必须退位
    exact_division: bool = False      # 除法必须整除
    allow_remainder: bool = False     # 允许有余数
    parentheses: bool = False         # 含括号（仅脱式题型有意义）
    count: int = 20                   # 题量 1..100


@dataclass(frozen=True)
class Question:
    """一道题（值对象，不可变）。卷面与成绩都保存它的完整快照（D27）。"""

    expr: str            # "37 + 45" / "(12 + 8) ÷ 4"，统一用 ÷ 与 ×
    display: str         # 卷面展示形态；填空题含 □，如 "7 + □ = 15"
    answer: str          # 标准答案（规范化写法）
    steps: list[str]     # 脱式逐步过程；非脱式题为 []
    difficulty: int      # 1..5
    tags: list[str]      # 考点标签，如 ["三位数乘两位数", "进位"]
    signature: str       # 去重签名；交换律等价已归一（FR-E7）


@dataclass(frozen=True)
class Verdict:
    """判分结果（FR-S7）。"""

    is_correct: bool
    normalized_input: str  # 归一化后的学生答案，便于老师端展示与排错


class ConfigError(ValueError):
    """出题参数违约（契约 §2.1 约束规则）。对应错误码 CONFIG_INVALID。"""


class InsufficientQuestions(RuntimeError):
    """当前约束组合可出的题不足。

    引擎**必须**在有限次尝试后抛出本异常，不得无限重试。
    对应错误码 INSUFFICIENT_QUESTIONS。
    """
