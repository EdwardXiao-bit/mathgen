"""提交作答：判分 / 落库 / D76 幂等（契约 §5.2）。

判分**只**调 :func:`app.grading.grade`——归一化（全角、`*`/`x`、余数写法）、余数、
算式边界（D45：不求值）的规则都在那里，router 与 service 都不自己比较答案。

两种模式（D75，恰好给一组，否则 422）：

- **老师发的卷**（给 `paper_id`）：从 `paper_item.payload` 读**完整题目快照**判分，
  **不重跑引擎**（D27）。卷必须是 `published`，否则 409 `PAPER_NOT_PUBLISHED`。
- **自主练习**（给 `config` + `seed`）：服务端用 `generate(config, seed)` **重新生成一遍**再判分；
  服务端**不接受**客户端回传的题目内容，因此客户端无法伪造题目（D75）。

幂等（D76）：按 `(student_id, attempt_uuid)` 去重；重复提交**不新建记录**，
返回**首次那次的响应体**——两次走的是**同一段渲染代码**（都从库里读），所以逐字相同。
HTTP 状态码则如实反映发生了什么：首次 201，重放 200（契约 §5.2 明写重放是 200）。
"""
from __future__ import annotations

import weakref
from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import engine as engine_module
from app.contracts.api import PaperConfigIn
from app.grading import grade
from app.models import PAPER_STATUS_PUBLISHED, Attempt, AttemptItem, Paper, Student
from app.services.config_svc import config_to_json, question_type_of, to_engine_config
from app.services.errors import ApiError
from app.services.paper_service import get_paper_or_404, load_item_pairs
from app.services.snapshot import dump_payload, load_payload
from app.services.timeutil import now, to_iso

__all__ = ["SubmitOutcome", "ensure_attempt_schema", "submit"]

#: D76 唯一键的列与名字（新库由模型的 `UniqueConstraint` 建，老库由本模块补索引）
UNIQUE_COLUMNS = frozenset({"student_id", "attempt_uuid"})
UNIQUE_NAME = "uq_attempt_student_uuid"

#: 已补齐的 engine（每进程每个 engine 只探测/改一次；用例各建内存库，因此按 engine 记忆）
_ENSURED: weakref.WeakSet = weakref.WeakSet()


class SubmitOutcome(NamedTuple):
    """`submit()` 的结果。

    `replayed` 是**如实反映现实**的那一位：`True` = 这次请求命中了 D76 去重（没有新建记录），
    路由层据此把状态码从 201 改成 200。为什么要显式带出来而不是让路由层「猜」：
    状态码是 HTTP 语义的一部分，重放返回 201 会谎报「刚刚创建了一个资源」。
    """

    replayed: bool
    payload: dict


def ensure_attempt_schema(db: Session) -> None:
    """把老库的 `attempt` 表补到 D76 需要的形状（幂等）。

    为什么需要这段：本项目**没有 Alembic**，`db.init_db()` 只跑 `Base.metadata.create_all()`
    ——它只为**不存在**的表建表，对已存在的表**不会**补列/补约束。
    所以这里 `inspect` 真实表结构，缺什么补什么：

    1. 缺 `attempt_uuid` 列 → `ALTER TABLE attempt ADD COLUMN attempt_uuid VARCHAR(64)`。
       允许 NULL **是有意的**：历史成绩没有 uuid，NULL 在 SQLite 的唯一索引里互不相等，
       所以老成绩不会互相撞键。
    2. 缺 `(student_id, attempt_uuid)` 唯一约束 → 建**唯一索引**（`IF NOT EXISTS`，幂等）。
       不依赖它也能靠 SELECT 去重，但**约束才是并发重发的最后一道防线**
       （两个请求同时提交同一 uuid 时，SELECT 都可能查不到）。
    """
    bind = db.get_bind()
    if bind in _ENSURED:
        return
    connection = db.connection()
    inspector = inspect(connection)
    if "attempt" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("attempt")}
        unique_sets = {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("attempt")
        }
        unique_sets |= {
            frozenset(index["column_names"])
            for index in inspector.get_indexes("attempt")
            if index.get("unique")
        }
        if "attempt_uuid" not in columns:
            connection.execute(text("ALTER TABLE attempt ADD COLUMN attempt_uuid VARCHAR(64)"))
        if UNIQUE_COLUMNS not in unique_sets:
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_NAME} "
                    "ON attempt (student_id, attempt_uuid)"
                )
            )
    _ENSURED.add(bind)


def submit(
    db: Session,
    *,
    student_id: int,
    attempt_uuid: str,
    answers: Sequence[tuple[int, str | None]],
    duration_ms: int,
    paper_id: int | None = None,
    config: PaperConfigIn | None = None,
    seed: int | None = None,
) -> SubmitOutcome:
    """提交一份作答并返回判分结果（FR-S7 / FR-S8）。

    `answers` 是 `(seq, 原始作答或 None)` 的序列；按 `seq` 匹配，**未作答按答错计**
    （契约 §5.2：给 `null` 或整体省略该项都算未作答）。
    """
    _require_one_mode(paper_id=paper_id, config=config, seed=seed)
    ensure_attempt_schema(db)
    _require_student(db, student_id)

    existing = _find_attempt(db, student_id, attempt_uuid)
    if existing is not None:
        # D76：重复提交不是错误——不新建、不改写，直接返回首次那次的结果。
        return SubmitOutcome(replayed=True, payload=_render(db, existing))

    questions, config_json = _resolve_questions(db, paper_id=paper_id, config=config, seed=seed)
    answer_map = _answer_map(answers, total=len(questions))

    attempt = Attempt(
        student_id=student_id,
        attempt_uuid=attempt_uuid,
        paper_id=paper_id,
        config_json=config_json,
        total=len(questions),
        correct=0,
        duration_ms=duration_ms,
        submitted_at=to_iso(now()),
    )
    db.add(attempt)
    try:
        # 唯一约束在 **flush** 时就会拦下并发重发（`sessionmaker(autoflush=False)`，
        # INSERT 正是这一句发出的），所以要在这里接住，不能等到路由层 commit。
        db.flush()
    except IntegrityError:
        # 另一个请求抢先写入了同一 (student_id, attempt_uuid) —— 唯一约束存在的意义。
        # 回滚本次写入，返回**先落库那一份**的结果（D76：重复不是错误）。
        db.rollback()
        existing = _find_attempt(db, student_id, attempt_uuid)
        if existing is None:
            raise
        return SubmitOutcome(replayed=True, payload=_render(db, existing))

    correct = 0
    for seq, question in enumerate(questions, start=1):
        raw = answer_map.get(seq)
        # 未作答：把 None 交给判分函数会绕过它「raw_answer: str」的签名，
        # 这里统一送空串——归一化后仍解析不出数值，因此稳定判错（不特判、不自己比较）。
        verdict = grade(question, "" if raw is None else raw)
        if verdict.is_correct:
            correct += 1
        db.add(
            AttemptItem(
                attempt_id=attempt.id,
                seq=seq,
                payload=dump_payload(question),
                student_answer=raw,
                is_correct=1 if verdict.is_correct else 0,
            )
        )
    attempt.correct = correct
    db.flush()  # 逐题明细落库，`_render` 才能从库里读回（首次与重复提交共用同一段渲染）
    return SubmitOutcome(replayed=False, payload=_render(db, attempt))


# ---------------------------------------------------------------- 内部

def _require_one_mode(
    *, paper_id: int | None, config: PaperConfigIn | None, seed: int | None
) -> None:
    """D75：`paper_id` 与（`config` + `seed`）**恰好给一组**，否则 422（不静默降级）。"""
    has_paper = paper_id is not None
    has_config = config is not None
    has_seed = seed is not None
    if has_paper and (has_config or has_seed):
        raise ApiError("VALIDATION_ERROR", "paper_id 与 config+seed 只能给一组（D75）")
    if not has_paper and not (has_config and has_seed):
        raise ApiError(
            "VALIDATION_ERROR",
            "做老师发的卷给 paper_id；自主练习给 config + seed（D75）",
        )


def _require_student(db: Session, student_id: int) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise ApiError("VALIDATION_ERROR", f"学生 {student_id} 不存在，请先报到")
    return student


def _find_attempt(db: Session, student_id: int, attempt_uuid: str) -> Attempt | None:
    return db.execute(
        select(Attempt).where(
            Attempt.student_id == student_id,
            Attempt.attempt_uuid == attempt_uuid,
        )
    ).scalars().first()


def _resolve_questions(
    db: Session, *, paper_id: int | None, config: PaperConfigIn | None, seed: int | None
) -> tuple[list, str | None]:
    """按模式取本次要判分的题目（快照 or 重新生成），顺带决定入库的 `config_json`。"""
    if paper_id is not None:
        paper: Paper = get_paper_or_404(db, paper_id)
        if paper.status != PAPER_STATUS_PUBLISHED:
            raise ApiError("PAPER_NOT_PUBLISHED", f"卷 {paper_id} 尚未发布，学生不能作答")
        # D27：读快照，不重跑引擎——否则引擎升级后老卷的判分会与卷面不符。
        questions = [question for _row, question in load_item_pairs(db, paper.id)]
        return questions, paper.config_json
    engine_config = to_engine_config(config)  # 违约 → 400 CONFIG_INVALID
    # D75：同种子完全可复现（引擎核心保证），所以服务端不需要客户端回传题目内容。
    questions = list(engine_module.generate(engine_config, seed))
    return questions, config_to_json(engine_config)


def _answer_map(answers: Sequence[tuple[int, str | None]], *, total: int) -> dict[int, str | None]:
    """`answers` → `{seq: 原始作答}`，越界与重复 seq 直接 422（报错好过静默错，D65）。"""
    result: dict[int, str | None] = {}
    for seq, answer in answers:
        if not 1 <= seq <= total:
            raise ApiError("VALIDATION_ERROR", f"seq={seq} 超出本次题目范围（1..{total}）")
        if seq in result:
            raise ApiError("VALIDATION_ERROR", f"seq={seq} 重复提交")
        result[seq] = answer
    return result


def _render(db: Session, attempt: Attempt) -> dict:
    """把一次 `attempt` 渲染成响应体（**首次与重复提交共用这一段**，因此逐字相同）。"""
    rows = db.execute(
        select(AttemptItem).where(AttemptItem.attempt_id == attempt.id).order_by(AttemptItem.seq)
    ).scalars().all()
    question_type = question_type_of(attempt.config_json)
    items = []
    for row in rows:
        question = load_payload(row.payload)  # 快照（D27），不重跑引擎
        items.append(
            {
                "seq": row.seq,
                "display": question.display,
                "question_type": question_type,
                "answer": question.answer,
                "student_answer": row.student_answer,
                "is_correct": bool(row.is_correct),
            }
        )
    return {
        "attempt_id": attempt.id,
        "total": attempt.total,
        "correct": attempt.correct,
        "items": items,
    }
