"""题目快照测试（契约 §6 冻结语义，D27）：读取不重跑引擎。

覆盖：
- 已存卷的所有读取路径（详情 / 列表 / 卷面上下文）在引擎被替换成「必炸」后仍可用
- 删题 / 换题 / 发布 / 另存副本都不回头调用 `generate`
- 换题走的是 `regenerate_one`，读到的是它的返回快照（证明没有重算）
- 快照字段完整（含 signature），解析可完整往返
- `edit_log` 审计记录齐全（delete / regenerate 各一条）
"""
from __future__ import annotations

import json
import unittest

from sqlalchemy import select

from app.contracts.sheet import SheetMode
from app.engine.types import Question
from app.models import EditLog, PaperItem
from app.services.sheet_ctx import build_sheet_context
from app.services.snapshot import (
    PAYLOAD_FIELDS,
    dump_payload,
    load_payload,
    question_from_dict,
    question_to_dict,
)
from tests.api._support import ApiTestCase, patch_engine, url_of


def _boom(*_args, **_kwargs):
    raise AssertionError("已存卷的读取路径不应重新调用引擎 generate()")


def _boom_regenerate(*_args, **_kwargs):
    raise AssertionError("删题 / 发布 / 另存副本不应调用 regenerate_one()")


class TestSnapshotIsNotRecomputed(ApiTestCase):
    def test_detail_read_does_not_call_engine(self):
        created = self.create_paper({"count": 5})
        with patch_engine(generate=_boom):
            fetched = self.detail(created["id"])
        self.assertEqual(fetched["items"], created["items"])

    def test_list_read_does_not_call_engine(self):
        created = self.create_paper({"count": 3})
        with patch_engine(generate=_boom):
            response = self.client.get(url_of("list_papers"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

    def test_delete_and_layout_and_publish_do_not_call_engine(self):
        created = self.create_paper({"count": 4})
        with patch_engine(generate=_boom, regenerate_one=_boom_regenerate):
            self.assertEqual(
                self.client.delete(url_of("delete_item", paper_id=created["id"], seq=1)).status_code,
                200,
            )
            self.assertEqual(
                self.client.patch(
                    url_of("update_layout", paper_id=created["id"]),
                    json={"cols_per_row": 2, "row_gap": 0.8, "font_size": 10,
                          "page_margin": 1.0, "answer_line": False, "header_bar": False},
                ).status_code,
                200,
            )
            self.assertEqual(self.publish(created["id"]).status_code, 200)
            clone = self.client.post(f"/api/papers/{created['id']}/copy")
        self.assertEqual(clone.status_code, 201)
        self.assertEqual(len(clone.json()["items"]), 3)

    def test_regenerate_uses_engine_result_verbatim(self):
        """换题后 payload 必须**逐字**等于 `regenerate_one` 的返回（含 signature）。"""
        created = self.create_paper({"count": 4})
        fixed = Question(
            expr="11 + 22",
            display="11 + 22 = ____",
            answer="33",
            steps=[],
            difficulty=1,
            tags=["注入的快照"],
            signature="injected-signature",
        )
        with patch_engine(regenerate_one=lambda *a, **k: fixed):
            response = self.client.post(url_of("regenerate_item", paper_id=created["id"], seq=2))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {"seq": 2, "display": "11 + 22 = ____", "expr": "11 + 22", "answer": "33",
             "difficulty": 1, "tags": ["注入的快照"], "steps": [], "origin": "regenerated"},
        )

        with self.session() as db:
            row = db.execute(
                select(PaperItem).where(PaperItem.paper_id == created["id"], PaperItem.seq == 2)
            ).scalar_one()
            payload = json.loads(row.payload)
        self.assertEqual(payload["signature"], "injected-signature")
        self.assertEqual(payload["tags"], ["注入的快照"])
        # 再次读取（引擎已恢复正常）仍是注入的那份快照
        self.assertEqual(self.detail(created["id"])["items"][1]["expr"], "11 + 22")

    def test_snapshot_payload_contains_all_contract_fields(self):
        created = self.create_paper({"count": 2})
        with self.session() as db:
            rows = db.execute(
                select(PaperItem).where(PaperItem.paper_id == created["id"])
            ).scalars().all()
        self.assertEqual(len(rows), 2)
        for row in rows:
            payload = json.loads(row.payload)
            self.assertEqual(set(payload), set(PAYLOAD_FIELDS))
            self.assertTrue(row.payload.startswith("{"))
            self.assertEqual(row.origin, "generated")

    def test_snapshot_serialization_round_trip(self):
        question = Question(
            expr="(12 + 8) ÷ 4", display="(12 + 8) ÷ 4", answer="5",
            steps=["12 + 8 = 20", "20 ÷ 4 = 5"], difficulty=3,
            tags=["含括号", "两位数"], signature="sig-1",
        )
        restored = load_payload(dump_payload(question))
        self.assertEqual(restored, question)
        self.assertEqual(question_from_dict(question_to_dict(question)), question)

    def test_snapshot_tolerates_legacy_missing_fields(self):
        """老快照缺字段时给显式默认，保证历史卷仍可打开（不抛 KeyError）。"""
        legacy = question_from_dict({"expr": "1 + 1", "display": "1 + 1 = ____", "answer": "2"})
        self.assertEqual(legacy.steps, [])
        self.assertEqual(legacy.tags, [])
        self.assertEqual(legacy.difficulty, 0)
        self.assertEqual(legacy.signature, "")

    def test_engine_swap_does_not_change_stored_paper(self):
        """模拟引擎升级：换掉 generate 的结果，已存卷必须一字不变。"""
        created = self.create_paper({"count": 3})
        upgraded = Question(
            expr="999 + 1", display="999 + 1 = ____", answer="1000",
            steps=[], difficulty=5, tags=["升级后"], signature="upgraded",
        )
        with patch_engine(generate=lambda *a, **k: [upgraded, upgraded, upgraded]):
            new_paper = self.create_paper({"count": 3})
            stored = self.detail(created["id"])

        self.assertEqual(new_paper["items"][0]["expr"], "999 + 1")
        self.assertEqual(stored["items"], created["items"])
        self.assertNotEqual(stored["items"][0]["expr"], "999 + 1")

    def test_sheet_context_is_built_from_snapshot(self):
        created = self.create_paper({"count": 3})
        with patch_engine(generate=_boom):
            with self.session() as db:
                ctx = build_sheet_context(db, created["id"], SheetMode.QUESTION)
        self.assertEqual([item.seq for item in ctx.items], [1, 2, 3])
        self.assertEqual(ctx.items[0].display, created["items"][0]["display"])
        self.assertEqual(ctx.title, created["title"])
        self.assertIsNone(ctx.code)


class TestEditLog(ApiTestCase):
    def test_delete_logs_deleted_seq(self):
        created = self.create_paper({"count": 4})
        self.client.delete(url_of("delete_item", paper_id=created["id"], seq=2))

        with self.session() as db:
            logs = db.execute(
                select(EditLog).where(EditLog.paper_id == created["id"]).order_by(EditLog.id)
            ).scalars().all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].action, "delete")
        self.assertEqual(logs[0].seq, 2)
        self.assertRegex(logs[0].at, r"^\d{4}-\d{2}-\d{2}T")

    def test_regenerate_logs_seq(self):
        created = self.create_paper({"count": 3})
        self.client.post(url_of("regenerate_item", paper_id=created["id"], seq=3))

        with self.session() as db:
            logs = db.execute(
                select(EditLog).where(EditLog.paper_id == created["id"])
            ).scalars().all()
        self.assertEqual([(log.action, log.seq) for log in logs], [("regenerate", 3)])

    def test_log_records_original_seq_of_deleted_item(self):
        """删第 1 题后，原第 2 题前移到 seq=1；日志必须记被删那一刻的 seq。"""
        created = self.create_paper({"count": 3})
        self.client.delete(url_of("delete_item", paper_id=created["id"], seq=1))
        self.client.delete(url_of("delete_item", paper_id=created["id"], seq=1))

        with self.session() as db:
            logs = db.execute(
                select(EditLog).where(EditLog.paper_id == created["id"]).order_by(EditLog.id)
            ).scalars().all()
        self.assertEqual([(log.action, log.seq) for log in logs],
                         [("delete", 1), ("delete", 1)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
