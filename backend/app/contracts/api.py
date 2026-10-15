"""老师端 REST 出入参模型与路径常量 —— **冻结**（docs/04-接口契约.md §5.1）。

**为什么需要它**：卷 API（生产方）与老师端界面（消费方）由不同智能体实现。
把 JSON 形状冻结成 Pydantic 模型后，API 用 `response_model=` 强制约束，
前端按同一份字段名写，集成时不会因为 `paperId` / `paper_id` 这类分歧返工。

约定：
- JSON 字段一律 **snake_case**（与数据库、Python 一致），前端自行映射。
- 时间一律 **ISO 8601 带时区** 字符串。
- 错误响应统一 `{"error": {"code": ..., "message": ...}}`。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "ROUTES", "PaperConfigIn", "LayoutIn", "AnswerLayoutIn", "PaperItemOut",
    "PaperDetail", "PaperSummary", "PaperListOut", "PublishResult",
    "AttemptItemOut", "AttemptSummary", "AttemptListOut", "AttemptDetailOut",
    "score_of",
    "ErrorBody", "ErrorResponse", "ArchivedFilter",
]

#: 路径常量（**冻结**）：前端与后端都必须引用这里，禁止各写字符串字面量
ROUTES = {
    "create_paper": "/api/papers",
    "list_papers": "/api/papers",
    "get_paper": "/api/papers/{paper_id}",
    "get_paper_by_code": "/api/papers/by-code/{code}",
    "delete_item": "/api/papers/{paper_id}/items/{seq}",
    "regenerate_item": "/api/papers/{paper_id}/items/{seq}/regenerate",
    "update_layout": "/api/papers/{paper_id}/layout",
    "update_answer_layout": "/api/papers/{paper_id}/answer-layout",
    "publish_paper": "/api/papers/{paper_id}/publish",
    # ---- 卷库收纳（D78）----
    "archive_paper": "/api/papers/{paper_id}/archive",
    "unarchive_paper": "/api/papers/{paper_id}/unarchive",
    "delete_paper": "/api/papers/{paper_id}",
    "copy_paper": "/api/papers/{paper_id}/copy",
    "sheet": "/api/papers/{paper_id}/sheet",
    "export": "/api/papers/{paper_id}/export",
    "qr": "/api/papers/{paper_id}/qr",
    # ---- M4 学生端（D74 / D75 / D76）----
    "create_student": "/api/students",
    "student_paper": "/api/student/paper/{code}",
    "student_practice": "/api/student/practice",
    "submit_attempt": "/api/attempts",
    # ---- M5 成绩（FR-T11 / FR-T12 / FR-T13）----
    # 注意 `export_attempts` 必须**先于** `get_attempt` 注册：`/api/attempts/export`
    # 与 `/api/attempts/{attempt_id}` 只差一段，而 `attempt_id: int` 解析不了 "export"
    # （会 422 而不是继续匹配），所以字面量路由永远排在参数路由前面。
    # 这与既有 `get_paper_by_code` 排在 `get_paper` 之前是同一条规矩。
    "export_attempts": "/api/attempts/export",
    "list_attempts": "/api/attempts",
    "get_attempt": "/api/attempts/{attempt_id}",
}

PaperStatus = Literal["draft", "published"]
#: D78 卷库列表的归档筛选：`exclude` 只看未归档（默认）/ `include` 全部 / `only` 只看已归档。
ArchivedFilter = Literal["exclude", "include", "only"]
QuestionTypeName = Literal["oral", "stepwise", "fill"]
RangeName = Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
OpName = Literal["+", "-", "×", "÷"]


class PaperConfigIn(BaseModel):
    """出题参数（对应 `app.engine.types.GenerateConfig`）。"""

    ops: list[OpName] = Field(min_length=1)
    ranges: list[RangeName] = Field(min_length=1)
    question_type: QuestionTypeName
    max_value: int | None = None
    require_carry: bool = False
    require_borrow: bool = False
    exact_division: bool = False
    allow_remainder: bool = False
    parentheses: bool = False
    count: int = Field(default=20, ge=1, le=100)


class LayoutIn(BaseModel):
    """排版参数（对应 `app.contracts.layout.LayoutParams`）。"""

    cols_per_row: int = Field(default=4, ge=2, le=6)
    row_gap: float = Field(default=1.0, ge=0.8, le=2.0)
    font_size: int = Field(default=14, ge=10, le=20)
    page_margin: float = Field(default=2.0, ge=1.0, le=3.0)
    answer_line: bool = True
    header_bar: bool = True


class AnswerLayoutIn(BaseModel):
    """答案版形式（**D53③ 由 M3 定义**，存 `paper.answer_layout`）。

    六维出题配置中的第六维。M2 期间固定为 D20 默认、不落库；M3 起正式接入。
    """

    separate_page: bool = True        # 答案单独成页（D20 默认 true）
    include_steps: bool = True        # 脱式题是否带逐步过程
    include_difficulty: bool = True   # 是否标难度
    include_tags: bool = True         # 是否标考点标签


class PaperItemOut(BaseModel):
    """卷面题目快照。老师端**包含** answer / steps（学生端必须剔除，契约 §9）。"""

    seq: int
    display: str
    expr: str
    answer: str
    difficulty: int
    tags: list[str]
    steps: list[str]
    origin: str = "generated"


class PaperDetail(BaseModel):
    id: int
    code: str | None = None
    title: str
    status: PaperStatus
    seed: int
    config: PaperConfigIn
    layout: LayoutIn
    answer_layout: AnswerLayoutIn
    created_at: str
    published_at: str | None = None
    #: D78 归档时间（ISO 8601 带时区）；`None` = 未归档。
    #: **与 `status` 正交**：归档只决定「老师在卷库里看不看得见它」，
    #: `status` 仍决定「能不能发卷 / 卷号是否有效」。
    archived_at: str | None = None
    items: list[PaperItemOut]


class PaperSummary(BaseModel):
    id: int
    code: str | None = None
    title: str
    status: PaperStatus
    item_count: int
    created_at: str
    published_at: str | None = None
    #: 见 `PaperDetail.archived_at`（D78）。
    archived_at: str | None = None
    #: 这套卷是否已有学生成绩（D78）。有成绩时**不允许物理删除**，只能归档。
    #: 列表页据此决定「删除」按钮是否可用，并在禁用时给出人话理由。
    has_attempts: bool = False


class PaperListOut(BaseModel):
    """卷库列表（M3 起支持分页与搜索；M4.x 起支持归档筛选，D78）。

    查询参数：`limit`（1..100，默认 20）、`offset`（≥0）、`q`（按标题模糊匹配）、
    `status`（`draft` / `published`，留空为全部）、
    `archived`（`exclude`（默认，只看未归档）/ `include` / `only`）。

    `total` 与 `papers` 都受 `archived` 影响——切到「只看已归档」时，
    `total` 是**已归档的**总数，不是全部总数。
    """

    total: int          # 符合条件的总数（不受 limit/offset 影响，但受 archived 影响）
    limit: int
    offset: int
    papers: list[PaperSummary]


class PublishResult(BaseModel):
    id: int
    code: str
    qr_url: str
    student_url: str


# ---------------------------------------------------------------- 成绩（M5 / FR-T11–T13）

def score_of(total: int, correct: int) -> int:
    """把 `(total, correct)` 换算成 0..100 的整数分 —— **全系统唯一的得分口径（D80）**。

    规则：
    1. 先把 `correct` 夹进 `[0, total]`（数据若不自洽，宁可显示 0/100，也不显示 120 分）；
    2. `total == 0` → 0（不出 0/0）；
    3. 否则**精确四舍五入（half-up）取整、不显示小数**：精确值落在 `.5` 时**向上进位**
       （小学生看得懂的才是有效的反馈；半分给低分是不必要的伤害）。

    为什么是一个纯整数公式，而不是 `round(correct * 100 / total)`：
    - Python 的 `round()` 是**银行家舍入**，`round(12.5) == 12`——正是本项目会踩的坑；
    - 而 `floor(correct / total * 100 + 0.5)`（= 旧学生端 JS 的 `Math.round`）会受
      **浮点表示误差**影响：`23 / 40 * 100` 在 IEEE754 里是 `57.49999999999999`，
      于是同样「正好一半」的 57.5 被算成 57。

    纯整数写法 `(clamped * 200 + total) // (2 * total)` 既没有银行家舍入、也不经过浮点，
    是精确的 half-up。**与旧 JS 口径的差异已被枚举清楚**（探针
    `.tmp-probe/probe_score_divergence.py`，`total ≤ 200` 范围共 **8** 个点，
    全部是「精确值正好 .5、浮点略低于 .5」；落在真实题量上限 `count ≤ 100` 内的只有
    **2** 个：`40/23` 与 `80/46`，旧口径 57、新口径 58）。M5 起得分**只由这里产出**，
    学生端结果页、教师端成绩列表、CSV/Excel 导出都直接用它，不再各自计算（D80）。

    一个函数放在**冻结包**里而不是服务层，是因为它解释的是 `AttemptSummary.score`
    这个冻结字段的语义；放在这里，契约测试才能对「规则本身」下判据，
    而不是只对「某个实现算出来的数」下判据。
    """
    if total <= 0:
        return 0
    clamped = max(0, min(correct, total))
    return (clamped * 200 + total) // (2 * total)


class AttemptItemOut(BaseModel):
    """一次作答里的**单题明细**（FR-T12）。

    **含正确答案** `answer`——这是教师端 / 提交判分的出口，与 `StudentItemOut` 的
    三键白名单（D74）不是一回事：白名单管的是**学生在答题时**能拿到什么，
    而这里的前提是「这次练习已经结束、成绩已经落库」。

    M5 起从 `app/routers/student_routes.py` 上移到冻结包：学生端结果页与教师端明细页
    要渲染的是**同一个形状**，两处各写一份模型就会各自漂移。
    """

    seq: int
    display: str
    question_type: str
    answer: str
    student_answer: str | None = None
    is_correct: bool


class AttemptSummary(BaseModel):
    """成绩列表里的一行（FR-T11：姓名、时间、题数、得分、用时）。"""

    id: int
    student_id: int
    student_name: str
    student_no: str
    #: 自主练习（D75）没有卷，`paper_id` / `paper_code` / `paper_title` 全为 `None`。
    #: **接口不返回「自主练习」这四个字**：显示文案属于界面层（`docs/07` 的用词约定在那里执行），
    #: 接口只负责说清「这行有没有挂卷」。
    paper_id: int | None = None
    paper_code: str | None = None
    paper_title: str | None = None
    total: int
    correct: int
    #: 得分（0..100 整数）。**唯一来源是 `score_of()`**，界面与导出都直接用它，
    #: 不得各自再算一遍（D80）。
    score: int
    duration_ms: int
    submitted_at: str


class AttemptListOut(BaseModel):
    """成绩列表（FR-T11）。

    查询参数：`limit`（1..100，默认 20）、`offset`（≥0）、
    `paper_id`（只看某套卷的成绩）、`q`（按**姓名或学号**模糊匹配，空 = 全部）。
    默认排序：`submitted_at` 倒序（新的在前），同一时刻按 `id` 倒序保证稳定分页。
    """

    total: int          # 符合条件的总数（不受 limit/offset 影响）
    limit: int
    offset: int
    attempts: list[AttemptSummary]


class AttemptDetailOut(AttemptSummary):
    """一次练习的完整明细（FR-T12）：概要在前，逐题在 `items`。

    `items` 按 `seq` 升序，与卷面顺序一致；`total` 与 `len(items)` 必须相等——
    这条由判据守着（分数与明细对不上时，成绩就不值得信）。
    """

    items: list[AttemptItemOut]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
