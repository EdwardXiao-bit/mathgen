"""数据库表结构 —— 契约 docs/04-接口契约.md §6（6 张表）+ docs/02-技术设计.md §5.2。

冻结语义（契约 §6）：
- `paper_item.payload` / `attempt_item.payload` 存 **Question 完整快照**，读取不重跑引擎（D27）
- `paper.code` 唯一，**仅 published 时非空**；`status ∈ {draft, published}`
- `UNIQUE(paper_id, seq)` / `UNIQUE(attempt_id, seq)`
- `edit_log.action ∈ {delete, regenerate}`，删题记 `deleted seq=N`
- `attempt.paper_id` 可空（空 = 自主练习）
- 时间列存 **ISO 8601 带时区** 字符串（契约 §5 约定）

纪律：本模块只描述表结构，不含业务状态机（见 app/services/paper_service.py）。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base", "Paper", "PaperItem", "EditLog", "Student", "Attempt", "AttemptItem",
    "PAPER_STATUS_DRAFT", "PAPER_STATUS_PUBLISHED",
    "EDIT_ACTION_DELETE", "EDIT_ACTION_REGENERATE",
    "ORIGIN_GENERATED", "ORIGIN_REGENERATED", "ORIGIN_COPIED",
    "OcrBatch", "OcrPage", "OcrItem",
    "OCR_STATUS_PENDING", "OCR_STATUS_PROCESSING", "OCR_STATUS_REVIEWING",
    "OCR_STATUS_COMMITTED", "OCR_STATUS_FAILED",
]

#: 模式 C（OCR）批次状态（docs/10 §3）：pending → processing → reviewing → committed，失败进 failed
OCR_STATUS_PENDING = "pending"
OCR_STATUS_PROCESSING = "processing"
OCR_STATUS_REVIEWING = "reviewing"
OCR_STATUS_COMMITTED = "committed"
OCR_STATUS_FAILED = "failed"

PAPER_STATUS_DRAFT = "draft"
PAPER_STATUS_PUBLISHED = "published"

EDIT_ACTION_DELETE = "delete"
EDIT_ACTION_REGENERATE = "regenerate"

#: `paper_item.origin` 取值（契约 §6 只要求 generated | regenerated | ...）
ORIGIN_GENERATED = "generated"
ORIGIN_REGENERATED = "regenerated"
ORIGIN_COPIED = "copied"


class Base(DeclarativeBase):
    """全部 ORM 模型的基类（一个 metadata，供 create_all 建 6 张表）。"""


class Paper(Base):
    """卷（draft 可编辑 / published 冻结）。"""

    __tablename__ = "paper"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 卷号 YYMM-NNNN（D34）；draft 阶段为 NULL，发布时分配
    code: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    layout_json: Mapped[str] = mapped_column(Text, nullable=False)
    #: 答案版分页 / 是否含过程（D20）；M2 存占位 JSON，由渲染导出方按需扩展
    answer_layout: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    published_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: D78 归档时间（ISO 8601 带时区）；NULL = 未归档。
    #: **与 status 正交**：status 决定「能不能发卷 / 卷号是否有效」，
    #: archived_at 只决定「老师在卷库里看不看得见它」。归档**不改** status，
    #: 因为一旦把它做成第三种 status，取消归档时就无法知道原来该回到 draft 还是 published。
    #: 老库没有这一列，靠 `services/paper_schema.ensure_paper_schema()` 幂等补上（无 Alembic）。
    archived_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (Index("idx_paper_status", "status"),)


class PaperItem(Base):
    """卷面题目（**快照**，不重算）。删题后由服务层前移 seq。"""

    __tablename__ = "paper_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("paper.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Question 完整快照（JSON 文本）
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (UniqueConstraint("paper_id", "seq", name="uq_paper_item_seq"),)


class EditLog(Base):
    """编辑日志（审计与排错）：delete | regenerate。"""

    __tablename__ = "edit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("paper.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (Index("idx_edit_log_paper", "paper_id"),)


class Student(Base):
    """学生：姓名 + 学号（D35 / D41），同名靠学号区分。"""

    __tablename__ = "student"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    student_no: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (UniqueConstraint("name", "student_no", name="uq_student_name_no"),)


class Attempt(Base):
    """一次练习；`paper_id` 为空表示自主练习（FR-S3）。"""

    __tablename__ = "attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    #: D76 幂等键：客户端每次提交生成的新 UUID，按 (student_id, attempt_uuid) 去重。
    #: 列在**模型层可空**是有意的：老库升级时 `create_all()` 不会补列，
    #: 由 `services/attempt_service.ensure_attempt_schema()` 幂等补上（历史行为 NULL，
    #: SQLite 里 NULL 互不相等，所以老成绩不会撞唯一键）。**API 层仍强制必填**。
    attempt_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("paper.id"), nullable=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    correct: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (
        UniqueConstraint("student_id", "attempt_uuid", name="uq_attempt_student_uuid"),
        Index("idx_attempt_paper", "paper_id"),
        Index("idx_attempt_student", "student_id"),
    )


class AttemptItem(Base):
    """逐题作答（FR-T12 / FR-S8）：冗余保存题目快照，历史成绩可长期还原。"""

    __tablename__ = "attempt_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempt.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 题目快照（含正确答案与过程）
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    student_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("attempt_id", "seq", name="uq_attempt_item_seq"),)


class OcrBatch(Base):
    """模式 C：一次「拍照/扫描 → 识别 → 老师校对 → 入档」的批次（docs/10 §5）。

    `attempt` 只在 `commit` 时生成（`committed_attempt_id` 回指），所以校对前的
    草稿永远不会出现在成绩列表里。`student_name` / `student_no` 分开存：入档时
    按 `UNIQUE(student.name, student_no)` 取或建，避免同名不同学号被合并。
    """

    __tablename__ = "ocr_batch"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("paper.id"), nullable=False)
    student_name: Mapped[str] = mapped_column(Text, nullable=False)
    student_no: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    committed_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("attempt.id"), nullable=True
    )

    __table_args__ = (Index("idx_ocr_batch_paper", "paper_id"),)


class OcrPage(Base):
    """批次里的一页：原图 + 校正图 + 状态（reason 给拒绝码/失败原因）。"""

    __tablename__ = "ocr_page"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("ocr_batch.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    rectified_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("batch_id", "page_no", name="uq_ocr_page_no"),)


class OcrItem(Base):
    """逐题识别结果（两遍 raw_text_p1/p2 + 一致标记 + 老师确认的 final_answer）。"""

    __tablename__ = "ocr_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("ocr_batch.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text_p1: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text_p2: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_p1: Mapped[float | None] = mapped_column(nullable=True)
    confidence_p2: Mapped[float | None] = mapped_column(nullable=True)
    agreed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("batch_id", "seq", name="uq_ocr_item_seq"),)
