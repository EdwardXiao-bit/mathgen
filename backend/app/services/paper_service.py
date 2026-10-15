"""卷服务：状态机、题号前移、快照读写、卷号分配、卷库检索（T-M2-BE + T-M3-BE 核心）。

冻结语义（docs/04-接口契约.md §6 / docs/02-技术设计.md §4）：
- `draft`（可删题 / 换题 / 改排版 / 改答案版形式）→ `published`（只读、有卷号）**单向不可逆**；
  对已发布卷的任何编辑操作 → `ApiError(PAPER_NOT_EDITABLE)` → HTTP 409
- 「另存为可编辑副本」：复制全部题目快照为新 draft（新 id、`code` 为空）
- 删题后**后续题号前移**，并在 `edit_log` 记一条 `delete, seq=N`
- 题目一律以 `Question` **快照**进入 `paper_item.payload`，读取不重跑引擎
- **M3**：`paper.answer_layout` 存答案版形式（D53③）；卷库按 `limit/offset/q/status` 检索，
  `total` 是**过滤后的总数**、不受分页影响；可按卷号（`code`）打开历史卷

事务纪律：本模块内所有写操作只 `flush()`，由路由层统一 `commit()`；
异常路径由路由层 `rollback()`（见 routers/papers.py 的 `_finish`）。
"""
from __future__ import annotations

import json
import random
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import engine
from app.contracts.api import (
    AnswerLayoutIn,
    PaperConfigIn,
    PaperDetail,
    PaperItemOut,
    PaperSummary,
)
from app.contracts.layout import LayoutParams
from app.engine.types import ConfigError, GenerateConfig, Question
from app.models import (
    EDIT_ACTION_DELETE,
    EDIT_ACTION_REGENERATE,
    ORIGIN_COPIED,
    ORIGIN_GENERATED,
    ORIGIN_REGENERATED,
    PAPER_STATUS_DRAFT,
    PAPER_STATUS_PUBLISHED,
    Attempt,
    EditLog,
    Paper,
    PaperItem,
)
from app.services import numbering
from app.services.answer_layout_svc import answer_layout_from_json, answer_layout_to_json
from app.services.config_svc import config_from_json, config_to_json, to_engine_config
from app.services.errors import ApiError
from app.services.layout_svc import layout_from_json, layout_in_from_params, layout_to_json
from app.services.paper_schema import ensure_paper_schema
from app.services.snapshot import dump_payload, load_payload
from app.services.timeutil import now, to_iso, utcnow_iso

__all__ = [
    "create_paper", "list_papers", "get_paper", "get_paper_or_404", "get_paper_by_code",
    "load_items", "load_item_pairs", "delete_item", "regenerate_item",
    "update_layout", "update_answer_layout", "publish_paper", "copy_paper", "purge_paper",
    "require_editable", "require_published", "to_detail", "to_item_out", "to_summary",
    "archive_paper", "unarchive_paper", "has_attempts",
    "ARCHIVED_EXCLUDE", "ARCHIVED_INCLUDE", "ARCHIVED_ONLY",
    "DEFAULT_LIMIT", "MAX_LIMIT", "AUTO_SEED_MAX", "SHIFT_OFFSET",
]

#: 归档筛选的三种口径（契约 `ArchivedFilter`，D78）。默认 `exclude`：
#: 默认就把已归档的卷混在列表里，等于归档没做。
ARCHIVED_EXCLUDE = "exclude"
ARCHIVED_INCLUDE = "include"
ARCHIVED_ONLY = "only"
ARCHIVED_FILTERS = (ARCHIVED_EXCLUDE, ARCHIVED_INCLUDE, ARCHIVED_ONLY)

#: 未显式给 seed 时的随机上界（真正随机：每次出题得到不同的一套题）
AUTO_SEED_MAX = 2 ** 31 - 1

#: 题号前移用的一次性偏移量：先整体抬到 OFFSET 之上，再回落到目标值，
#: 避免 `UPDATE ... SET seq = seq - 1` 逐行触发 UNIQUE(paper_id, seq) 冲突。
SHIFT_OFFSET = 1000

#: 卷库检索的分页边界（与契约 `PaperListOut` 的 `limit` 一致）
DEFAULT_LIMIT = 20
MAX_LIMIT = 100

#: LIKE 通配符的转义字符（老师搜「50%」时不应被当成通配符）
_LIKE_ESCAPE = "\\"


# --------------------------------------------------------------------------- 查询


def has_attempts(db: Session, paper_id: int) -> bool:
    """这套卷是否已有学生成绩（D78）。

    只看 `attempt.paper_id`：自主练习的 `attempt` 行 `paper_id` 为 NULL，不该算到任何卷头上。
    用 `EXISTS` 而不是 `count(*)`：这里只需要真假，不必数完。
    """
    exists = db.execute(
        select(Attempt.id).where(Attempt.paper_id == paper_id).limit(1)
    ).first()
    return exists is not None


def get_paper_or_404(db: Session, paper_id: int) -> Paper:
    ensure_paper_schema(db)
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise ApiError("PAPER_NOT_FOUND", f"卷 {paper_id} 不存在")
    return paper


def get_paper_by_code(db: Session, code: str) -> Paper:
    """按卷号取卷（FR-T14「用卷号打开历史卷」）。

    错误码用契约 §9 的 **`CODE_NOT_FOUND` | 404 | 卷号无效**：
    它与 `PAPER_NOT_FOUND`（按 **id** 找不到卷）语义不同，两者不可混用。

    只有 `published` 卷才有卷号，所以 draft 卷永远查不到 —— 这也是「卷号无效」，
    返回 404 是正确语义。
    """
    wanted = (code or "").strip()
    if not wanted:
        raise ApiError("CODE_NOT_FOUND", "卷号不能为空")
    ensure_paper_schema(db)
    paper = db.execute(select(Paper).where(Paper.code == wanted)).scalar_one_or_none()
    if paper is None:
        raise ApiError("CODE_NOT_FOUND", f"卷号 {wanted} 无效")
    return paper


def require_editable(paper: Paper) -> None:
    """已发布卷不可编辑（冻结状态机）→ 409 PAPER_NOT_EDITABLE。"""
    if paper.status == PAPER_STATUS_PUBLISHED:
        raise ApiError(
            "PAPER_NOT_EDITABLE",
            "已发布的卷不能直接修改，请先另存为可编辑副本再改",
        )
    if paper.status != PAPER_STATUS_DRAFT:  # pragma: no cover - 数据损坏路径
        raise ApiError("VALIDATION_ERROR", f"未知的卷状态：{paper.status!r}")


def require_published(paper: Paper) -> None:
    """只有已发布卷才有卷号 / 二维码 / 学生可做（契约 §9：`PAPER_NOT_PUBLISHED` → 409）。"""
    if paper.status != PAPER_STATUS_PUBLISHED or not paper.code:
        raise ApiError(
            "PAPER_NOT_PUBLISHED",
            f"卷 {paper.id} 尚未发布，请先发布后再生成二维码",
        )


def load_items(db: Session, paper_id: int) -> list[PaperItem]:
    """按 seq 升序取卷面题目行。"""
    return list(
        db.execute(
            select(PaperItem).where(PaperItem.paper_id == paper_id).order_by(PaperItem.seq)
        ).scalars()
    )


def load_item_pairs(db: Session, paper_id: int) -> list[tuple[PaperItem, Question]]:
    """按顺序取 (数据库行, 题目快照) 对。"""
    return [(row, load_payload(row.payload)) for row in load_items(db, paper_id)]


# --------------------------------------------------------------------------- DTO


def to_item_out(row: PaperItem, question: Question | None = None) -> PaperItemOut:
    """数据库行 → API 出参（契约 §5.1 `PaperItemOut`，字段名逐字对齐）。"""
    question = question if question is not None else load_payload(row.payload)
    return PaperItemOut(
        seq=row.seq,
        display=question.display,
        expr=question.expr,
        answer=question.answer,
        difficulty=question.difficulty,
        tags=list(question.tags),
        steps=list(question.steps),
        origin=row.origin,
    )


def _config_in(paper: Paper) -> PaperConfigIn:
    """从存储 JSON 还原 API 出参模型（出入参字段名同源，天然 round-trip）。"""
    return PaperConfigIn(**json.loads(paper.config_json))


def to_detail(db: Session, paper: Paper) -> PaperDetail:
    """数据库行 → API 出参（契约 §5.1 `PaperDetail`）。"""
    items = [to_item_out(row, question) for row, question in load_item_pairs(db, paper.id)]
    return PaperDetail(
        id=paper.id,
        code=paper.code,
        title=paper.title,
        status=paper.status,
        seed=paper.seed,
        config=_config_in(paper),
        layout=layout_in_from_params(layout_from_json(paper.layout_json)),
        answer_layout=answer_layout_from_json(paper.answer_layout),
        created_at=paper.created_at,
        published_at=paper.published_at,
        archived_at=paper.archived_at,
        items=items,
    )


def to_summary(
    db: Session,
    paper: Paper,
    *,
    item_count: int | None = None,
    attempts: bool | None = None,
) -> PaperSummary:
    """数据库行 → 列表项（契约 §5.1 `PaperSummary`，D78 新增两列）。

    `item_count` / `attempts` 可**预先批量算好**再传进来：`list_papers` 一页 N 行，
    逐行现查就是 2N 条 SQL（N+1）。不传时退回「按需现查」，供单卷场景使用。
    """
    if item_count is None:
        count = db.execute(
            select(func.count()).select_from(PaperItem).where(PaperItem.paper_id == paper.id)
        ).scalar_one()
        item_count = int(count)
    if attempts is None:
        attempts = has_attempts(db, paper.id)
    return PaperSummary(
        id=paper.id,
        code=paper.code,
        title=paper.title,
        status=paper.status,
        item_count=int(item_count),
        created_at=paper.created_at,
        published_at=paper.published_at,
        archived_at=paper.archived_at,
        has_attempts=bool(attempts),
    )


# --------------------------------------------------------------------------- 内部写工具


def _append_items(db: Session, paper_id: int, questions: Sequence[Question],
                  origin: str, start_seq: int = 1) -> None:
    for offset, question in enumerate(questions):
        db.add(
            PaperItem(
                paper_id=paper_id,
                seq=start_seq + offset,
                payload=dump_payload(question),
                origin=origin,
            )
        )


def _log(db: Session, paper_id: int, action: str, seq: int | None) -> None:
    db.add(EditLog(paper_id=paper_id, action=action, seq=seq, at=utcnow_iso()))


def _default_title() -> str:
    return f"口算练习 {now().strftime('%Y-%m-%d')}"


# --------------------------------------------------------------------------- 写


def create_paper(
    db: Session,
    config_in: PaperConfigIn,
    *,
    seed: int | None = None,
    title: str | None = None,
    layout: LayoutParams | None = None,
    answer_layout: AnswerLayoutIn | None = None,
) -> Paper:
    """生成一张新 draft 卷：跑一次引擎取快照后落库（此后永不重算）。

    答案版形式（M3）落显式 JSON；不传则用 D20 默认（四项全 true）。
    """
    ensure_paper_schema(db)   # 老库补 archived_at，否则下面的 INSERT 会带着未知列
    config: GenerateConfig = to_engine_config(config_in)
    layout = layout if layout is not None else LayoutParams()
    answer_layout = answer_layout if answer_layout is not None else AnswerLayoutIn()
    actual_seed = int(seed) if seed is not None else random.SystemRandom().randrange(AUTO_SEED_MAX)

    questions = engine.generate(config, actual_seed)   # 违约抛 ConfigError / InsufficientQuestions
    if len(questions) != config.count:  # pragma: no cover - 引擎契约保证
        raise ApiError(
            "INSUFFICIENT_QUESTIONS",
            f"引擎只返回 {len(questions)} 题，期望 {config.count} 题",
        )

    paper = Paper(
        code=None,
        title=(title or "").strip() or _default_title(),
        status=PAPER_STATUS_DRAFT,
        seed=actual_seed,
        config_json=config_to_json(config),
        layout_json=layout_to_json(layout),
        answer_layout=answer_layout_to_json(answer_layout),
        created_at=to_iso(now()),
        published_at=None,
        archived_at=None,
    )
    db.add(paper)
    db.flush()                                        # 拿到 paper.id
    _append_items(db, paper.id, questions, ORIGIN_GENERATED)
    db.flush()
    return paper


def delete_item(db: Session, paper_id: int, seq: int) -> list[PaperItem]:
    """删题并让**后续题号前移**（契约 §6 冻结语义）；仅 draft 允许。"""
    paper = get_paper_or_404(db, paper_id)
    require_editable(paper)

    row = db.execute(
        select(PaperItem).where(PaperItem.paper_id == paper_id, PaperItem.seq == seq)
    ).scalar_one_or_none()
    if row is None:
        raise ApiError("ITEM_NOT_FOUND", f"卷 {paper_id} 没有第 {seq} 题")

    total = int(db.execute(
        select(func.count()).select_from(PaperItem).where(PaperItem.paper_id == paper_id)
    ).scalar_one())

    db.delete(row)
    db.flush()
    if seq < total:
        # 两段式位移：先整体抬到 OFFSET 之上，再回落到「原 seq - 1」，
        # 全程不与未移动的行撞 UNIQUE(paper_id, seq)。
        db.execute(
            update(PaperItem)
            .where(PaperItem.paper_id == paper_id, PaperItem.seq > seq)
            .values(seq=PaperItem.seq + SHIFT_OFFSET)
        )
        db.execute(
            update(PaperItem)
            .where(PaperItem.paper_id == paper_id, PaperItem.seq > seq + SHIFT_OFFSET)
            .values(seq=PaperItem.seq - SHIFT_OFFSET - 1)
        )
    _log(db, paper_id, EDIT_ACTION_DELETE, seq)
    db.flush()
    return load_items(db, paper_id)


def regenerate_item(db: Session, paper_id: int, seq: int) -> PaperItem:
    """换一题（FR-E9）：只替换第 seq 题，其余题**原样不动**；仅 draft 允许。"""
    paper = get_paper_or_404(db, paper_id)
    require_editable(paper)

    rows = load_items(db, paper_id)
    row = next((item for item in rows if item.seq == seq), None)
    if row is None:
        raise ApiError("ITEM_NOT_FOUND", f"卷 {paper_id} 没有第 {seq} 题")

    config = config_from_json(paper.config_json)
    existing = [load_payload(item.payload) for item in rows]
    try:
        fresh = engine.regenerate_one(config, paper.seed, seq - 1, existing)
    except ConfigError as exc:
        raise ApiError("CONFIG_INVALID", str(exc)) from exc

    row.payload = dump_payload(fresh)
    row.origin = ORIGIN_REGENERATED
    _log(db, paper_id, EDIT_ACTION_REGENERATE, seq)
    db.flush()
    return row


def update_layout(db: Session, paper_id: int, params: LayoutParams) -> Paper:
    """改排版（FR-T4）；仅 draft 允许。"""
    paper = get_paper_or_404(db, paper_id)
    require_editable(paper)
    paper.layout_json = layout_to_json(params)
    db.flush()
    return paper


def update_answer_layout(db: Session, paper_id: int, payload: AnswerLayoutIn) -> Paper:
    """改「答案版形式」（M3，六维中的第六维）；仅 draft 允许（已发布 → 409）。"""
    paper = get_paper_or_404(db, paper_id)
    require_editable(paper)
    paper.answer_layout = answer_layout_to_json(payload)
    db.flush()
    return paper


def _publish_once(db: Session, paper: Paper) -> Paper:
    paper.code = numbering.next_code(db)
    paper.status = PAPER_STATUS_PUBLISHED
    paper.published_at = utcnow_iso()
    db.flush()
    return paper


def publish_paper(db: Session, paper_id: int) -> Paper:
    """发布：分配卷号 + 置 published（单向不可逆）。幂等：重复发布返回原卷号。"""
    paper = get_paper_or_404(db, paper_id)
    if paper.status == PAPER_STATUS_PUBLISHED:
        return paper
    if paper.status != PAPER_STATUS_DRAFT:  # pragma: no cover - 数据损坏路径
        raise ApiError("VALIDATION_ERROR", f"未知的卷状态：{paper.status!r}")

    for _attempt in range(5):
        try:
            with db.begin_nested():
                _publish_once(db, paper)
            return paper
        except IntegrityError:
            # 并发发布撞了同一个卷号：重新读最大值再试
            db.expire(paper)
    raise ApiError("VALIDATION_ERROR", "卷号分配冲突，请重试")


def copy_paper(db: Session, paper_id: int, title: str | None = None) -> Paper:
    """另存为可编辑副本（D32）：复制全部题目快照为新 draft，新 id、无卷号。"""
    source = get_paper_or_404(db, paper_id)
    clone = Paper(
        code=None,
        title=(title or "").strip() or f"{source.title}（副本）",
        status=PAPER_STATUS_DRAFT,
        seed=source.seed,
        config_json=source.config_json,
        layout_json=source.layout_json,
        answer_layout=source.answer_layout,
        created_at=utcnow_iso(),
        published_at=None,
    )
    db.add(clone)
    db.flush()
    for row in load_items(db, source.id):
        db.add(
            PaperItem(
                paper_id=clone.id,
                seq=row.seq,
                payload=row.payload,          # 快照逐字复制，不重跑引擎
                origin=ORIGIN_COPIED,
            )
        )
    db.flush()
    return clone


def _like_pattern(raw: str) -> str:
    """把老师输入里的 LIKE 通配符转义掉（搜「50%」不应匹配一切）。"""
    escaped = (
        raw.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def list_papers(
    db: Session,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    status: str | None = None,
    q: str | None = None,
    archived: str = ARCHIVED_EXCLUDE,
):
    """卷库检索（FR-T14 / M3 + 归档筛选 D78）。

    返回 `(total, papers)`，其中 **`total` 是过滤后的总数，不受 limit/offset 影响**
    （契约 `PaperListOut.total` 的明确语义），但**受 `archived` 影响**：切到 `only` 时
    total 是已归档的总数。排序：创建时间倒序，同刻按 id 倒序。

    `archived` 三口径（契约 `ArchivedFilter`）：`exclude`（默认，只看未归档）/
    `include`（全部）/ `only`（只看已归档）。非法值不在这里兜底——路由层用 `Literal`
    声明，非法值由 FastAPI 直接 422；服务层再校验一次是为了**不信任调用方**
    （服务层也可能被脚本直接调用）。
    """
    if archived not in ARCHIVED_FILTERS:
        raise ApiError(
            "VALIDATION_ERROR",
            f"archived 只能是 exclude/include/only 之一，收到 {archived!r}",
        )
    ensure_paper_schema(db)
    conditions = []
    if status:
        conditions.append(Paper.status == status)
    if q and q.strip():
        conditions.append(Paper.title.like(_like_pattern(q.strip()), escape=_LIKE_ESCAPE))
    if archived == ARCHIVED_EXCLUDE:
        conditions.append(Paper.archived_at.is_(None))
    elif archived == ARCHIVED_ONLY:
        conditions.append(Paper.archived_at.is_not(None))

    base = select(Paper)
    counter = select(func.count()).select_from(Paper)
    if conditions:
        base = base.where(*conditions)
        counter = counter.where(*conditions)

    total = int(db.execute(counter).scalar_one())
    rows = list(
        db.execute(
            base.order_by(Paper.created_at.desc(), Paper.id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    counts, attempts = _page_facts(db, [row.id for row in rows])
    return total, [
        to_summary(db, row, item_count=counts.get(row.id, 0), attempts=row.id in attempts)
        for row in rows
    ]


def _page_facts(db: Session, paper_ids: list[int]) -> tuple[dict[int, int], set[int]]:
    """一次 group-by 取回整页的题目数与「有无成绩」，把 2N 条查询压成 2 条。

    为什么必须这样：卷库默认每页 20 行，逐行 `count(*)` + `EXISTS` 就是 40 条 SQL；
    而列表页正是最常被打开的一屏。空页直接返回，避免 `IN ()` 这种空列表查询。
    """
    if not paper_ids:
        return {}, set()
    item_counts = {
        int(paper_id): int(count)
        for paper_id, count in db.execute(
            select(PaperItem.paper_id, func.count())
            .where(PaperItem.paper_id.in_(paper_ids))
            .group_by(PaperItem.paper_id)
        )
    }
    with_attempts = {
        int(paper_id)
        for (paper_id,) in db.execute(
            select(Attempt.paper_id)
            .where(Attempt.paper_id.in_(paper_ids))
            .group_by(Attempt.paper_id)
        )
    }
    return item_counts, with_attempts


def archive_paper(db: Session, paper_id: int) -> Paper:
    """归档（软删除，D78）：任何状态都能归档。

    **与 status 正交**：这里一个字都不改 `paper.status`，所以取消归档后自然回到原来的
    `draft`/`published`。**幂等**：重复归档保留**第一次**的 `archived_at` 不改写——
    那一位记录的是「它是何时被收进归档的」，重放一次请求不该把它变新。
    """
    paper = get_paper_or_404(db, paper_id)
    if paper.archived_at is None:
        # 用 `utcnow_iso()` 与同表的 `published_at` 保持同一口径（都是生命周期事件时间戳）；
        # `created_at` 用的是本地时区，属于历史遗留差异，这里不跟着它走。
        paper.archived_at = utcnow_iso()
        db.flush()
    return paper


def unarchive_paper(db: Session, paper_id: int) -> Paper:
    """取消归档（D78）：**幂等**，本来就未归档时原样返回，不报错也不写时间戳。

    注意这里恢复的是「看得见」而不是「状态」——`status` 从头到尾没被归档动过，
    所以一个归档前是 published 的卷，取消归档后仍是 published（卷号也照旧有效）。
    """
    paper = get_paper_or_404(db, paper_id)
    if paper.archived_at is not None:
        paper.archived_at = None
        db.flush()
    return paper


def get_paper(db: Session, paper_id: int) -> PaperDetail:
    return to_detail(db, get_paper_or_404(db, paper_id))


def purge_paper(db: Session, paper_id: int) -> None:
    """物理删除卷及其题目（`DELETE /api/papers/{id}`，D78）。

    **有成绩的卷一律不许物理删除**：先查 `attempt` 有没有指向它的行，有则抛
    409 `PAPER_HAS_ATTEMPTS`，让调用方去走归档（软删除）。

    这条限制**首先是数据约束、其次才是政策**：`attempt.paper_id` 外键没有 `ondelete`，
    `db.py` 又显式打开了 `PRAGMA foreign_keys=ON`，所以真删下去只会撞
    `FOREIGN KEY constraint failed` —— 那是一个**数据库内部错误**，老师看不懂、
    前端也无从解释。翻译成 409 + 一句人话 + 一条出路（归档）才是有用的行为。

    历史注记：本函数早于 D78 存在，原 docstring 写着「测试清理用；契约未开放删除卷的
    HTTP 接口」——那句从 D78 起已过期（接口已开放），此处一并订正。
    """
    paper = get_paper_or_404(db, paper_id)
    if has_attempts(db, paper.id):
        raise ApiError(
            "PAPER_HAS_ATTEMPTS",
            f"卷 {paper.id} 已有学生成绩，不能删除；可以改为归档",
        )
    db.execute(delete(PaperItem).where(PaperItem.paper_id == paper.id))
    db.delete(paper)
    db.flush()
