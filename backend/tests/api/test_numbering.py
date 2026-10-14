"""卷号测试（契约 §6 冻结，D34）：`YYMM-NNNN`，序号按月自增。

覆盖：
- 格式与年月前缀正确
- 同月从 0001 开始逐个自增
- 跨月重新从 0001 开始（用注入的历史卷号证明「只统计同月」）
- `draft` 无卷号、`published` 才有
- 序号用尽（9999）显式报错而非溢出成 5 位
"""
from __future__ import annotations

import datetime
import unittest

from app.models import PAPER_STATUS_DRAFT, PAPER_STATUS_PUBLISHED, Paper
from app.services import numbering
from app.services.errors import ApiError
from tests.api._support import ApiTestCase, url_of


def _seed_paper(code: str, status: str = PAPER_STATUS_PUBLISHED) -> Paper:
    from app.services.timeutil import utcnow_iso

    return Paper(
        code=code,
        title=f"历史卷 {code}",
        status=status,
        seed=1,
        config_json="{}",
        layout_json="{}",
        answer_layout="{}",
        created_at=utcnow_iso(),
        published_at=utcnow_iso() if status == PAPER_STATUS_PUBLISHED else None,
    )


class TestPaperCode(ApiTestCase):
    def test_code_format_and_month_prefix(self):
        created = self.create_paper({"count": 2})
        code = self.publish(created["id"]).json()["code"]
        prefix = numbering.month_prefix()
        self.assertRegex(code, r"^\d{4}-\d{4}$")
        self.assertTrue(code.startswith(prefix + "-"), f"{code} 的年月前缀应为 {prefix}")
        self.assertEqual(code, f"{prefix}-0001")

    def test_sequence_increments_within_month(self):
        codes = []
        for _ in range(4):
            paper = self.create_paper({"count": 2})
            codes.append(self.publish(paper["id"]).json()["code"])
        prefix = numbering.month_prefix()
        self.assertEqual(codes, [f"{prefix}-{index:04d}" for index in range(1, 5)])

    def test_draft_papers_do_not_consume_numbers(self):
        """draft 无卷号，不应影响序号（否则草稿会白白吃掉编号）。"""
        for _ in range(3):
            self.create_paper({"count": 2})          # 全是 draft
        paper = self.create_paper({"count": 2})
        self.assertEqual(self.publish(paper["id"]).json()["code"], f"{numbering.month_prefix()}-0001")

    def test_sequence_counts_only_same_month(self):
        """上个月的卷号不参与本月自增（按月自增的语义）。"""
        prefix = numbering.month_prefix()
        last_month = f"{int(prefix[:2]) - 1 if int(prefix[:2]) > 0 else 99:02d}{prefix[2:]}"
        with self.session() as db:
            db.add(_seed_paper(f"{last_month}-0007"))
            db.add(_seed_paper(f"{prefix}-0003"))
            db.commit()

        paper = self.create_paper({"count": 2})
        self.assertEqual(self.publish(paper["id"]).json()["code"], f"{prefix}-0004")

    def test_next_code_helper_reads_max_of_month(self):
        prefix = numbering.month_prefix()
        with self.session() as db:
            db.add(_seed_paper(f"{prefix}-0042"))
            db.commit()
            self.assertEqual(numbering.next_code(db), f"{prefix}-0043")

    def test_next_code_raises_when_month_exhausted(self):
        prefix = numbering.month_prefix()
        with self.session() as db:
            db.add(_seed_paper(f"{prefix}-9999"))
            db.commit()
            with self.assertRaises(ApiError) as ctx:
                numbering.next_code(db)
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_month_prefix_uses_given_moment(self):
        moment = datetime.datetime(2025, 10, 3, 9, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(numbering.month_prefix(moment), "2510")

    def test_code_is_unique_across_publishes(self):
        codes = set()
        for _ in range(5):
            paper = self.create_paper({"count": 2})
            codes.add(self.publish(paper["id"]).json()["code"])
        self.assertEqual(len(codes), 5)

    def test_draft_status_and_null_code_in_db(self):
        created = self.create_paper({"count": 2})
        with self.session() as db:
            row = db.get(Paper, created["id"])
            self.assertEqual(row.status, PAPER_STATUS_DRAFT)
            self.assertIsNone(row.code)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
