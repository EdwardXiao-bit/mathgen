"""学生端接口的 **wire 模型**与路径常量（契约 §5.2；M4 期间不进冻结包）。

为什么这些模型不住在 `app/contracts/api.py`：冻结是给「已经谈定、不该再动」的接口用的，
而学生端接口在 M4 期间还在长（`docs/04` §8 已写明这条显式例外）。
它们的权威是 **§5.2 的字段表 + `backend/tests/api/_student_support.py` 的白名单守卫**，
不是 `contracts/api.py`。M4 收尾时再由 Lead 评估是否合并进契约包（合并要重算契约哈希）。

路径常量一律取 `ROUTES`，**不写字符串字面量、也不留本地兜底表**：
两处事实来源会让「今天的安全网」变成「明天的真实来源」（`ROUTES.get(name, LOCAL[name])`
这种写法尤其危险——它让分歧变成合法状态）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.contracts.api import ROUTES, PaperConfigIn

__all__ = [
    "ROUTE_CREATE_STUDENT",
    "ROUTE_STUDENT_PAPER",
    "ROUTE_STUDENT_PRACTICE",
    "ROUTE_SUBMIT_ATTEMPT",
    "StudentIn",
    "StudentOut",
    "StudentItemOut",
    "StudentPaperOut",
    "PracticeOut",
    "AnswerIn",
    "AttemptIn",
    "AttemptItemOut",
    "AttemptResultOut",
]

ROUTE_CREATE_STUDENT = ROUTES["create_student"]
ROUTE_STUDENT_PAPER = ROUTES["student_paper"]
ROUTE_STUDENT_PRACTICE = ROUTES["student_practice"]
ROUTE_SUBMIT_ATTEMPT = ROUTES["submit_attempt"]


# ---------------------------------------------------------------- 报到

class StudentIn(BaseModel):
    """报到入参：姓名与学号**均必填**（D41）。"""

    name: str = Field(min_length=1, max_length=50)
    student_no: str = Field(min_length=1, max_length=50)


class StudentOut(BaseModel):
    student_id: int


# ---------------------------------------------------------------- 取题 / 练习

class StudentItemOut(BaseModel):
    """学生端题目对象。

    **只允许** `seq` / `display` / `question_type` 三个键（契约 §5.2 / D74 白名单）。
    这里不设 `model_config = ConfigDict(extra="forbid")`：HTTP 出口由
    `response_model=` 过滤（多出来的键会被丢掉而不是泄漏给客户端），
    而「投影有没有多写键」要靠 `services/student_service.public_item` 的守卫用例去抓
    （见 `tests/api/_student_support.py` 的递归白名单判据 + V9 负对照）。
    """

    seq: int
    display: str
    question_type: str


class StudentPaperOut(BaseModel):
    """按卷号取题（FR-S4）：**不含** `answer` / `steps`。"""

    paper_id: int
    title: str
    total: int
    items: list[StudentItemOut]


class PracticeOut(BaseModel):
    """自主练习出题（FR-S3）：`seed` **必须返回**，学生提交时原样带回（D75）。"""

    seed: int
    total: int
    items: list[StudentItemOut]


# ---------------------------------------------------------------- 提交作答

class AnswerIn(BaseModel):
    """逐题作答：`seq` 按 1 起编号；未作答给 `null` 或整体省略该项，均按**答错**计。"""

    seq: int = Field(ge=1)
    answer: str | None = None


class AttemptIn(BaseModel):
    """提交作答（契约 §5.2）：`paper_id` 与（`config` + `seed`）**恰好给一组**（D75）。

    除「恰好一组」外，`attempt_uuid` **必填**（D76）：客户端每次提交新生成一个。
    """

    student_id: int = Field(ge=1)
    attempt_uuid: str = Field(min_length=1, max_length=64)
    answers: list[AnswerIn]
    duration_ms: int = Field(ge=0)
    paper_id: int | None = Field(default=None, ge=1)
    config: PaperConfigIn | None = None
    seed: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> "AttemptIn":
        """两种模式二选一 —— 只是同一份判断的第二道闸。

        服务层 `attempt_service._require_one_mode()` 才是权威（直接调服务层也拦得住），
        这里做**请求体层面**的校验，是为了让错误在 422 而不是走到服务层才发现。
        """
        has_paper = self.paper_id is not None
        has_config = self.config is not None
        has_seed = self.seed is not None
        if has_paper and (has_config or has_seed):
            raise ValueError("paper_id 与 config+seed 只能给一组（D75）")
        if not has_paper and not (has_config and has_seed):
            raise ValueError("做老师发的卷给 paper_id；自主练习给 config + seed（D75）")
        return self


class AttemptItemOut(BaseModel):
    """判分明细：含**正确答案**与逐题对错（FR-S8；这里给 answer 是契约要求的）。"""

    seq: int
    display: str
    question_type: str
    answer: str
    student_answer: str | None
    is_correct: bool


class AttemptResultOut(BaseModel):
    attempt_id: int
    total: int
    correct: int
    items: list[AttemptItemOut]
