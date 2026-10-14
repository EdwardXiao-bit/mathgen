"""题目快照的序列化（契约 §6 冻结语义：payload 存 Question 完整快照，读取不重跑引擎）。

为什么必须能完整往返：卷库打开、成绩明细还原都直接读快照（D27）。
一旦引擎升级，历史卷的字段（expr/display/answer/steps/difficulty/tags/signature）
必须原样还在——所以这里**逐字段**序列化，不依赖 dataclasses.asdict 的隐式行为，
并且解析时对缺失字段给出显式默认，保证老快照仍可读。
"""
from __future__ import annotations

import json

from app.engine.types import Question

__all__ = ["question_to_dict", "question_from_dict", "dump_payload", "load_payload", "PAYLOAD_FIELDS"]

#: 快照字段（含 signature）：signature 也存，便于将来审计去重口径
PAYLOAD_FIELDS = ("expr", "display", "answer", "steps", "difficulty", "tags", "signature")


def question_to_dict(question: Question) -> dict:
    return {
        "expr": question.expr,
        "display": question.display,
        "answer": question.answer,
        "steps": list(question.steps),
        "difficulty": question.difficulty,
        "tags": list(question.tags),
        "signature": question.signature,
    }


def question_from_dict(data: dict) -> Question:
    return Question(
        expr=data["expr"],
        display=data["display"],
        answer=data["answer"],
        steps=list(data.get("steps") or []),
        difficulty=int(data.get("difficulty") or 0),
        tags=list(data.get("tags") or []),
        signature=data.get("signature", ""),
    )


def dump_payload(question: Question) -> str:
    """Question → 入库文本（中文不转义，保证快照可读、可 diff）。"""
    return json.dumps(question_to_dict(question), ensure_ascii=False, sort_keys=True)


def load_payload(raw: str) -> Question:
    """入库文本 → Question（**不重跑引擎**）。"""
    return question_from_dict(json.loads(raw))
