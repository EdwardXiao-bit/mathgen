"""答案版内容计划 —— **HTML 与 DOCX 共用的唯一判据**（D53③ / D59 / 契约第 6 条）。

## 为什么需要这一层

契约第 6 条要求「`export_docx` 的卷面结构必须与 HTML/PDF 一致」。
如果 HTML 与 Word 各自解释 `answer_layout`，两边迟早漂移，而且漂移只在
「关掉某个开关」时才显形，很容易漏。

所以把「答案版该输出什么」抽成**一个纯函数** `build_answer_plan(ctx)`：

* `app/render/sheet.py` 的 HTML 渲染按它决定输出哪些元素；
* `app/export/docx.py` 的 Word 导出按**同一份** plan 生成段落。

两边都不可能各写一套判断，结构一致性就变成了结构性事实而不是约定。

本模块是纯计算：不读文件、不依赖 docx / playwright。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.contracts.sheet import SheetContext, SheetItem, SheetMode

__all__ = [
    "AnswerEntry",
    "AnswerPlan",
    "build_answer_plan",
    "HEADER_FIELD_LABELS",
    "TITLE_SCALE",
    "TITLE_LONG_TEXT_STRATEGY",
]

#: 卷头栏字段（D41 + D70）。
#:
#: D41 定学生身份为「**姓名 + 学号**」两者必填（`student_no NOT NULL`），
#: 所以卷面卷头栏必须是四项——M3 之前只有「班级 / 姓名 / 得分」，与 D41 不一致，D70 补齐。
#:
#: 放在 `layout` 而不是 `render`：**HTML 与 DOCX 都从这里取**，
#: 两边不可能各写一份字段列表（契约第 6 条的结构一致性因此是结构性的）。
HEADER_FIELD_LABELS: tuple[str, ...] = ("班级", "姓名", "学号", "得分")

#: 标题字号相对**题目字号**（`--font-size`）的放大倍数（D70）。
#:
#: 用户反馈「卷面文字相对纸面偏大」。`--font-size` 是冻结的**题目字号**（10..20pt，默认 14），
#: 语义与边界都不能动，所以只收紧标题倍数：1.5 → 1.35
#: （默认 14pt 时标题 21pt → 18.9pt，正文仍是 14pt）。
#:
#: 放在 `layout` 的理由同 `HEADER_FIELD_LABELS`：**HTML 与 DOCX 共用**，不会一边改一边漏。
TITLE_SCALE = 1.35

#: 标题长文本策略（D70）：**折行 + 允许断词**，而不是按字数缩字号。
#:
#: 理由：A4 版心固定，若按字数缩字号，「多长算长」会变成一个魔数阈值，
#: 且不同字数得到不同字号、观感跳变；折行对任意长度都稳定，也不影响其它版面参数。
#: 这里只记录策略常量（供测试与文档引用），实际 CSS 见 `app.layout.styles`。
TITLE_LONG_TEXT_STRATEGY = "wrap"


@dataclass(frozen=True)
class AnswerEntry:
    """答案版里的一道题：只保留**被允许输出**的字段。"""

    seq: int
    answer: str
    difficulty: int = 0
    tags: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerPlan:
    """答案版内容计划（由 `SheetContext.answer_layout` 决定）。"""

    #: 是否渲染答案区（`mode=QUESTION` 恒为 False —— 安全边界，契约第 3 条）
    enabled: bool
    #: 答案是否单独成页（D20 默认 true）
    separate_page: bool
    #: 是否带逐步递等过程
    include_steps: bool
    #: 是否标难度
    include_difficulty: bool
    #: 是否标考点标签
    include_tags: bool
    #: 逐题内容（已按开关裁剪过——关掉的项**根本不在数据里**）
    entries: tuple[AnswerEntry, ...]

    @property
    def is_empty(self) -> bool:
        """答案区没有任何可输出的题（空卷）。"""
        return not self.entries


def build_answer_plan(ctx: SheetContext) -> AnswerPlan:
    """把 `SheetContext` 翻译成「答案版该输出什么」。

    **题目版恒返回 `enabled=False`**：answer / steps 一个字段都不读，
    与 HTML 的安全边界（契约第 3 条）保持同一条实现。
    """
    if ctx.mode is not SheetMode.ANSWER:
        return AnswerPlan(
            enabled=False,
            separate_page=False,
            include_steps=False,
            include_difficulty=False,
            include_tags=False,
            entries=(),
        )

    answer_layout = ctx.answer_layout
    entries = tuple(
        _entry_for(item, answer_layout) for item in ctx.items
    )
    return AnswerPlan(
        enabled=True,
        separate_page=bool(answer_layout.separate_page),
        include_steps=bool(answer_layout.include_steps),
        include_difficulty=bool(answer_layout.include_difficulty),
        include_tags=bool(answer_layout.include_tags),
        entries=entries,
    )


def _entry_for(item: SheetItem, answer_layout) -> AnswerEntry:
    """按开关裁剪一道题的答案字段。

    关掉的项**在数据层就被丢掉**，而不是「生成了再隐藏」——
    这样 HTML 与 Word 都不可能把该藏的内容写出去。
    """
    return AnswerEntry(
        seq=int(item.seq),
        answer=item.answer,
        difficulty=int(item.difficulty) if answer_layout.include_difficulty else 0,
        tags=tuple(item.tags) if answer_layout.include_tags else (),
        steps=tuple(item.steps) if answer_layout.include_steps else (),
    )
