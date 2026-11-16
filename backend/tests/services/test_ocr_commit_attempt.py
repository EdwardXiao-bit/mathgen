"""入档后的成绩与在线作答同构（M18-06）。

链路：上传建批次 → 注入脚本化引擎跑两遍识别 → 老师逐题确认 → `commit` 生成
`attempt(source=ocr)` → 成绩列表 / 明细 / 导出三处读回。

这里**不重测识别**（M18-04 已覆盖），只钉三件事：

1. 入档后的成绩与在线作答**同一个形状**：`total`/`correct`/`score`、明细的
   `question_type`、`student_answer`、`is_correct` 都来自共用渲染，OCR 不另开一条路；
2. 三种状态可区分：答对（`is_correct=true`）、答错（`false` + 有答案）、
   未作答（`false` + `student_answer=null`）——「看不清」绝不能静默判 0；
3. 未确认完不许入档、重复入档幂等、入档前不出现在成绩列表里（D91 / `docs/10` §3）。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:  # pragma: no cover
    import cv2
    import numpy as np  # noqa: F401
except ImportError:  # pragma: no cover
    cv2 = None

from app.contracts.sheet import SheetMode
from app.render import geometry, marking
from app.services import ocr_service
from app.services.sheet_ctx import build_sheet_context
from tests.api._support import ApiTestCase, url_of
from tests.api.test_ocr_batches import _page_bytes

__all__ = ["OcrCommitAttemptTest"]


class _ScriptedEngine:
    """按「第几次调用」返回不同文本；位置用真实答案框中心（与 M18-04 判据同一套）。"""

    def __init__(self, centers: dict[int, tuple[float, float]], passes: list[dict[int, str]]):
        self.centers = centers
        self.passes = passes
        self.calls = 0

    def __call__(self, image, **_kwargs):
        if cv2 is None:  # pragma: no cover
            raise RuntimeError("cv2 缺失")
        height, width = image.shape[:2]
        texts = self.passes[self.calls % len(self.passes)]
        self.calls += 1
        detections = []
        for seq, text in texts.items():
            center = self.centers.get(seq)
            if center is None:
                continue
            cx = center[0] * (width / 1240.0)
            cy = center[1] * (height / 1754.0)
            detections.append(
                [
                    [[cx - 9, cy - 4], [cx + 9, cy - 4], [cx + 9, cy + 4], [cx - 9, cy + 4]],
                    text,
                    0.95,
                ]
            )
        return detections, 0.01


class OcrCommitAttemptTest(ApiTestCase):
    """入档 → 成绩列表 / 明细 / 导出三处读回。"

    """

    def setUp(self) -> None:  # noqa: D102
        super().setUp()
        tmp_root = Path(__file__).resolve().parents[2] / ".tmp-ocr-tests"
        tmp_root.mkdir(exist_ok=True)
        self._ocr_dir = tempfile.mkdtemp(prefix="commit-", dir=str(tmp_root))
        self._saved_env = os.environ.get("MATHGEN_OCR_DIR")
        os.environ["MATHGEN_OCR_DIR"] = self._ocr_dir
        self.addCleanup(self._restore_env)
        self._patcher = mock.patch("app.routers.ocr.ocr_jobs.enqueue", lambda *a, **k: None)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

        self.paper = self.create_paper()
        self.paper_id = self.paper["id"]
        self.assertEqual(self.publish(self.paper_id).status_code, 200)

    def _restore_env(self) -> None:
        if self._saved_env is None:
            os.environ.pop("MATHGEN_OCR_DIR", None)
        else:
            os.environ["MATHGEN_OCR_DIR"] = self._saved_env
        shutil.rmtree(self._ocr_dir, ignore_errors=True)

    # ---- 脚手架 ---------------------------------------------------------

    def _checksum(self) -> str:
        db = self.session()
        try:
            ctx = build_sheet_context(db, self.paper_id, SheetMode.QUESTION)
            return marking.sheet_checksum(ctx)
        finally:
            db.close()

    def _centers(self) -> dict[int, tuple[float, float]]:
        db = self.session()
        try:
            ctx = build_sheet_context(db, self.paper_id, SheetMode.QUESTION)
            boxes = geometry.geometry_of(ctx)["boxes"]
        finally:
            db.close()
        return {
            int(box["seq"]): (
                (float(box["x"]) + float(box["w"]) / 2) * 1240.0,
                (float(box["y"]) + float(box["h"]) / 2) * 1754.0,
            )
            for box in boxes
            if int(box["page"]) == 1
        }

    def _create_batch(self) -> dict:
        files = [
            ("files", (name, io.BytesIO(data), "image/jpeg"))
            for name, data in _page_bytes(self._checksum())
        ]
        response = self.client.post(
            url_of("ocr_batches"),
            data={
                "paper_id": str(self.paper_id),
                "student_name": "肖铭皓",
                "student_no": "20241414",
            },
            files=files,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _run(self, batch_id: str, engine) -> dict:
        db = self.session()
        try:
            detail = ocr_service.run_batch(db, batch_id, engine=engine)
            db.commit()
            return detail
        finally:
            db.close()

    def _patch_item(self, batch_id: str, seq: int, **body) -> dict:
        response = self.client.patch(url_of("ocr_item", batch_id=batch_id, seq=seq), json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _commit(self, batch_id: str):
        return self.client.post(url_of("ocr_commit", batch_id=batch_id))

    def _attempts(self, **params):
        response = self.client.get(url_of("list_attempts"), params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _detail(self, attempt_id: int) -> dict:
        response = self.client.get(url_of("get_attempt", attempt_id=attempt_id))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _correct_answers(self) -> dict[int, str]:
        detail = self.detail(self.paper_id)
        return {item["seq"]: item["answer"] for item in detail["items"]}

    # ---- 判据 -----------------------------------------------------------

    def test_commit_builds_an_attempt_with_the_same_shape_as_online_work(self) -> None:
        """全部答对 → 100 分；明细的题型/答案/判对与在线作答同一条渲染路径。"""
        batch = self._create_batch()
        batch_id = batch["batch"]["id"]
        centers = self._centers()
        answers = self._correct_answers()
        seqs = sorted(answers)
        self.assertEqual(len(seqs), 6)

        # 两遍都读成正确答案 → 全部自动通过
        engine = _ScriptedEngine(centers, [dict(answers), dict(answers)])
        self._run(batch_id, engine)

        before = self._attempts(paper_id=self.paper_id)
        self.assertEqual(before["total"], 0, "入档前不该出现在成绩列表里")

        response = self._commit(batch_id)
        self.assertEqual(response.status_code, 201, response.text)
        attempt_id = response.json()["attempt_id"]

        listing = self._attempts(paper_id=self.paper_id)
        self.assertEqual(listing["total"], 1)
        row = listing["attempts"][0]
        self.assertEqual(row["id"], attempt_id)
        self.assertEqual(row["student_name"], "肖铭皓")
        self.assertEqual(row["student_no"], "20241414")
        self.assertEqual(row["paper_id"], self.paper_id)
        self.assertEqual(row["paper_code"], self.detail(self.paper_id)["code"])
        self.assertEqual(row["total"], len(seqs))
        self.assertEqual(row["correct"], len(seqs))
        self.assertEqual(row["score"], 100)
        self.assertEqual(row["duration_ms"], 0)

        detail = self._detail(attempt_id)
        self.assertEqual(detail["total"], len(detail["items"]))
        paper_detail = self.detail(self.paper_id)
        question_type = paper_detail["config"]["question_type"]
        for item in detail["items"]:
            self.assertEqual(item["question_type"], question_type)
            self.assertEqual(item["student_answer"], answers[item["seq"]])
            self.assertTrue(item["is_correct"])
            self.assertEqual(item["answer"], answers[item["seq"]])

        # 导出：OCR 入档的行与在线作答走同一个导出器
        exported = self.client.get(url_of("export_attempts"), params={"format": "csv"})
        self.assertEqual(exported.status_code, 200)
        body = exported.content.decode("utf-8-sig")
        self.assertIn("肖铭皓", body)
        self.assertIn("20241414", body)

    def test_wrong_and_unreadable_answers_stay_three_state(self) -> None:
        """答对 / 答错 / 未作答三态：看不清的题是 NULL，不是 0 分答案。"""
        batch = self._create_batch()
        batch_id = batch["batch"]["id"]
        centers = self._centers()
        answers = self._correct_answers()
        seqs = sorted(answers)
        wrong = {seq: "999" for seq in seqs}

        engine = _ScriptedEngine(centers, [dict(wrong), dict(wrong)])
        self._run(batch_id, engine)

        # 第 1 题改成正确答案；第 2 题标「看不清」；其余保持错误答案
        self._patch_item(batch_id, seqs[0], answer=answers[seqs[0]], reviewed=True)
        self._patch_item(batch_id, seqs[1], unreadable=True)

        response = self._commit(batch_id)
        self.assertEqual(response.status_code, 201, response.text)
        attempt_id = response.json()["attempt_id"]

        detail = self._detail(attempt_id)
        items = {item["seq"]: item for item in detail["items"]}
        self.assertTrue(items[seqs[0]]["is_correct"])
        self.assertEqual(items[seqs[0]]["student_answer"], answers[seqs[0]])

        # 未作答：接口给 null（界面按三态显示「未作答」），绝不能被写成 "0" 判错
        self.assertIsNone(items[seqs[1]]["student_answer"])
        self.assertFalse(items[seqs[1]]["is_correct"])

        self.assertEqual(items[seqs[2]]["student_answer"], "999")
        self.assertFalse(items[seqs[2]]["is_correct"])

        self.assertEqual(detail["correct"], 1)
        self.assertEqual(detail["score"], int(round(1 / len(seqs) * 100)))

    def test_commit_is_refused_until_reviewed_and_is_idempotent(self) -> None:
        """未确认完 409；确认完 201；重复入档 200 且不新增成绩。"""
        batch = self._create_batch()
        batch_id = batch["batch"]["id"]
        centers = self._centers()
        answers = self._correct_answers()
        seqs = sorted(answers)

        # 只有第 1 题两遍一致 → 其余 5 题需人工
        engine = _ScriptedEngine(
            centers,
            [{seqs[0]: answers[seqs[0]]}, {seqs[0]: answers[seqs[0]]}],
        )
        self._run(batch_id, engine)

        refused = self._commit(batch_id)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["error"]["code"], "OCR_ITEMS_UNREVIEWED")
        self.assertEqual(self._attempts(paper_id=self.paper_id)["total"], 0)

        for seq in seqs[1:]:
            self._patch_item(batch_id, seq, answer=answers[seq], reviewed=True)

        first = self._commit(batch_id)
        self.assertEqual(first.status_code, 201, first.text)
        attempt_id = first.json()["attempt_id"]

        second = self._commit(batch_id)
        self.assertEqual(second.status_code, 200, second.text)
        payload = second.json()
        self.assertTrue(payload["replayed"])
        self.assertEqual(payload["attempt_id"], attempt_id)

        listing = self._attempts(paper_id=self.paper_id)
        self.assertEqual(listing["total"], 1, "重复入档不能多出一条成绩")

    def test_ocr_attempt_config_keeps_source_and_question_type(self) -> None:
        """`config_json` 同时带 source 与题型：既溯源，也让明细有题型可显示。"""
        batch = self._create_batch()
        batch_id = batch["batch"]["id"]
        centers = self._centers()
        answers = self._correct_answers()
        engine = _ScriptedEngine(centers, [dict(answers), dict(answers)])
        self._run(batch_id, engine)
        attempt_id = self._commit(batch_id).json()["attempt_id"]

        db = self.session()
        try:
            from app.models import Attempt

            attempt = db.get(Attempt, attempt_id)
            self.assertIsNotNone(attempt)
            config = json.loads(attempt.config_json)
        finally:
            db.close()
        self.assertEqual(config["source"], "ocr")
        self.assertEqual(config["batch_id"], batch_id)
        self.assertEqual(config["question_type"], self.detail(self.paper_id)["config"]["question_type"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
