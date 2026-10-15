"""学生端服务：报到 / 按卷号取题 / 自主练习（契约 §5.2，D74 / D75）。

两条贯穿全模块的约束：

1. **白名单（D74）**：学生端题目对象**只允许** `seq` / `display` / `question_type` 三个键——
   是白名单不是黑名单（D71：枚举式黑名单永远不完整）。投影一律走 :func:`public_item`，
   **不允许**在别处手写字典字面量；要加字段必须先改契约 §5.2。
   自动化守卫在 `backend/tests/api/_student_support.py`（**独立硬编码**白名单 + 递归遍历）。
2. **快照优先（D27）**：老师发的卷从 `paper_item.payload` 读题干，**不重跑引擎**；
   题干展示形态因此与老师打印出来的卷面逐字一致（引擎升级后老卷也不变）。
"""
from __future__ import annotations

import random
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import engine as engine_module
from app.contracts.api import PaperConfigIn
from app.models import PAPER_STATUS_PUBLISHED, Student
from app.services.config_svc import question_type_of, to_engine_config
from app.services.errors import ApiError
from app.services.paper_service import get_paper_by_code, load_item_pairs
from app.services.timeutil import now, to_iso

__all__ = [
    "STUDENT_ITEM_KEYS",
    "MAX_SEED",
    "ReportOutcome",
    "public_item",
    "report_student",
    "paper_payload",
    "practice_payload",
]

#: 学生端题目对象允许的键（契约 §5.2 / D74）。**加字段前先改契约**。
STUDENT_ITEM_KEYS = ("seq", "display", "question_type")

#: `seed` 上限（与老师端建卷的 `Query(ge=0)` 同量级，保证 JSON 里是可读整数）
MAX_SEED = 2**31 - 1


class ReportOutcome(NamedTuple):
    """报到结果：`created=True` 表示这次真的新建了学生行。

    路由层据此决定 201 / 200——「取或建」对已存在的学生返回 201 会谎报「刚创建了资源」，
    与 D76 重放返 200 是同一条理由（状态码要如实反映发生了什么）。
    """

    created: bool
    student_id: int


def public_item(seq: int, display: str, question_type: str) -> dict:
    """学生端题目对象——**唯一**的投影入口（D74 白名单）。"""
    return {"seq": int(seq), "display": display, "question_type": question_type}


def report_student(db: Session, *, name: str, student_no: str) -> ReportOutcome:
    """学生报到（FR-S2）：按 `(name, student_no)` **取或建**，返回同一个 `student_id`。

    为什么不是每次插入新行：`student` 表有 `UNIQUE(name, student_no)`（D35 / D41），
    同名靠学号区分。学生换手机、清缓存后重复报到不该造出第二个「张三」，
    否则成绩列表会裂成两份（老师看到的才是唯一的成绩单）。
    """
    clean_name = (name or "").strip()
    clean_no = (student_no or "").strip()
    if not clean_name:
        raise ApiError("VALIDATION_ERROR", "姓名不能为空")
    if not clean_no:
        raise ApiError("VALIDATION_ERROR", "学号不能为空")

    existing = _find_student(db, clean_name, clean_no)
    if existing is not None:
        return ReportOutcome(created=False, student_id=existing.id)

    row = Student(name=clean_name, student_no=clean_no, created_at=to_iso(now()))
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # 并发报到（两个请求同时首次报到）：唯一约束先落一方，这里读回已存在那行。
        db.rollback()
        existing = _find_student(db, clean_name, clean_no)
        if existing is None:
            raise
        return ReportOutcome(created=False, student_id=existing.id)
    return ReportOutcome(created=True, student_id=row.id)


def paper_payload(db: Session, code: str) -> dict:
    """按卷号取题（FR-S4）：只返回题干，**不含答案**（D74）。

    未发布 → `CODE_NOT_FOUND`(404)：`paper.code` 只在 `published` 时非空（契约 §6），
    所以「一个未发布的卷的卷号」本就不该存在；用 404 而不是 409，是为了**不泄露**
    「这个卷号存在但还没发布」这一信息（D65 的语义分工：按卷号查不到 = CODE_NOT_FOUND）。
    """
    paper = get_paper_by_code(db, code)
    if paper.status != PAPER_STATUS_PUBLISHED:
        raise ApiError("CODE_NOT_FOUND", f"卷号 {code} 无效")
    question_type = question_type_of(paper.config_json)
    items = [
        public_item(row.seq, question.display, question_type)
        for row, question in load_item_pairs(db, paper.id)
    ]
    return {"paper_id": paper.id, "title": paper.title, "total": len(items), "items": items}


def practice_payload(payload: PaperConfigIn, *, seed: int | None = None) -> dict:
    """自主练习出题（FR-S3）：服务端随机取 `seed` 并**在响应里返回**（D75）。

    `seed` 必须返回：学生提交时要原样回传，服务端凭 `(config, seed)` 重新生成同一套题再判分。
    所以「服务端分不清客户端回传的是哪一次生成的 seed」**不是漏洞**——服务端本来就不信客户端
    回传的题目内容，任何能生成出题的 `(config, seed)` 都是合法的练习，D75 的安全性质正在于此。
    """
    config = to_engine_config(payload)  # 违约 → 400 CONFIG_INVALID
    if seed is None:
        seed = random.SystemRandom().randrange(0, MAX_SEED + 1)
    questions = list(engine_module.generate(config, seed))
    question_type = config.question_type.value
    items = [
        public_item(index, question.display, question_type)
        for index, question in enumerate(questions, start=1)
    ]
    return {"seed": seed, "total": len(items), "items": items}


# ---------------------------------------------------------------- 内部

def _find_student(db: Session, name: str, student_no: str) -> Student | None:
    return db.execute(
        select(Student).where(Student.name == name, Student.student_no == student_no)
    ).scalars().first()
