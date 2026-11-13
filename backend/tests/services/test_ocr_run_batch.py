"""整批识别与落库的判据（M18-04）：注入脚本化引擎，不依赖真实 OCR 模型。

链路：上传建批次（API）→ `run_batch()` 两遍识别 → 题行与裁图落库 → 详情接口能看到
原文/置信度/一致标记与 `crop_url`。用真实几何（`geometry_of`）给假引擎摆答案位置，
所以「框匹配」这一层是真的，只有识别文本是假的。
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:  # pragma: no cover
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

from app.contracts.sheet import SheetMode
from app.render import geometry
from app.services import answer_ocr, ocr_service
from app.services.sheet_ctx import build_sheet_context
from tests.api._support import ApiTestCase, url_of
from tests.api.test_ocr_batches import _page_bytes

__all__ = ["RunBatchTest"]


class _ScriptedEngine:
    """按「第几次调用」返回不同文本，用来模拟 P1 / P3 两遍识别。"""

    def __init__(self, centers: dict[int, tuple[float, float]], passes: list[dict[int, str]]):
        self.centers = centers
        self.passes = passes
        self.calls = 0

    def __call__(self, image, **kwargs):
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


class RunBatchTest(ApiTestCase):
    """两遍一致 → 自动通过；不一致 / 空 → 需人工；裁图与详情接口."""

    def setUp(self) -> None:  # noqa: D102
        super().setUp()
        self._tmp_root = Path(__file__).resolve().parents[2] / ".tmp-ocr-tests"
        self._tmp_root.mkdir(exist_ok=True)
        self._ocr_dir = tempfile.mkdtemp(prefix="run-", dir=str(self._tmp_root))
        self._saved_env = os.environ.get("MATHGEN_OCR_DIR")
        os.environ["MATHGEN_OCR_DIR"] = self._ocr_dir
        self.addCleanup(self._restore_env)
        self._patcher = mock.patch("app.routers.ocr.ocr_jobs.enqueue", lambda *a, **k: None)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

        paper = self.create_paper()
        self.paper_id = paper["id"]
        self.assertEqual(self.publish(self.paper_id).status_code, 200)

    def _restore_env(self) -> None:
        if self._saved_env is None:
            os.environ.pop("MATHGEN_OCR_DIR", None)
        else:
            os.environ["MATHGEN_OCR_DIR"] = self._saved_env
        shutil.rmtree(self._ocr_dir, ignore_errors=True)

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
        db = self.session()
        try:
            ctx = build_sheet_context(db, self.paper_id, SheetMode.QUESTION)
            from app.render import marking

            checksum = marking.sheet_checksum(ctx)
        finally:
            db.close()
        files = [
            ("files", (name, io.BytesIO(data), "image/jpeg"))
            for name, data in _page_bytes(checksum)
        ]
        response = self.client.post(
            self._url("ocr_batches"),
            data={
                "paper_id": str(self.paper_id),
                "student_name": "肖铭皓",
                "student_no": "20241414",
            },
            files=files,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _url(self, key: str) -> str:
        from tests.api._support import url_of

        return url_of(key)

    def _run(self, batch_id: str, engine) -> dict:
        db = self.session()
        try:
            detail = ocr_service.run_batch(db, batch_id, engine=engine)
            db.commit()
            return detail
        finally:
            db.close()

    def test_two_passes_agreeing_auto_pass_and_save_crops(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        seqs = [item["seq"] for item in detail["items"]]
        centers = self._centers()
        self.assertEqual(len(seqs), 6)

        # 前 4 题两遍都认出「99」→ 自动通过；后 2 题两遍不一样 → 需人工
        agreed = {seq: "99" for seq in seqs[:4]}
        p1 = dict(agreed)
        p1[seqs[4]] = "99"
        p1[seqs[5]] = "66"
        p3 = dict(agreed)
        p3[seqs[4]] = "98"
        p3[seqs[5]] = "6"
        engine = _ScriptedEngine(centers, [p1, p3])

        result = self._run(batch_id, engine)
        self.assertEqual(result["batch"]["status"], "reviewing")
        self.assertIn("自动通过 4 题", result["batch"]["message"])
        self.assertIn("需人工 2 题", result["batch"]["message"])

        items = {item["seq"]: item for item in result["items"]}
        for seq in seqs[:4]:
            self.assertEqual(items[seq]["agreed"], 1)
            self.assertEqual(items[seq]["reviewed"], 1)
            self.assertEqual(items[seq]["final_answer"], "99")
            self.assertEqual(items[seq]["crop_url"], f"/api/ocr/batches/{batch_id}/crops/{seq}")
        self.assertEqual(items[seqs[4]]["agreed"], 0)
        self.assertEqual(items[seqs[4]]["reviewed"], 0)
        self.assertIsNone(items[seqs[4]]["final_answer"])
        self.assertEqual(items[seqs[4]]["raw_text_p1"], "99")
        self.assertEqual(items[seqs[4]]["raw_text_p2"], "98")
        self.assertEqual(items[seqs[5]]["agreed"], 0)

        # 裁图真的落盘，且受控路由能取到 PNG
        crop = Path(self._ocr_dir) / batch_id / f"crop-{seqs[0]}.png"
        self.assertTrue(crop.is_file())
        response = self.client.get(items[seqs[0]]["crop_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")

    def test_empty_passes_are_never_silently_scored(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        seqs = [item["seq"] for item in detail["items"]]
        centers = self._centers()
        # 只有第 1 题两遍都认不出 → 全部进人工；一条都不许自动通过
        engine = _ScriptedEngine(centers, [{seqs[0]: "8"}, {seqs[0]: "8"}])

        result = self._run(batch_id, engine)
        items = {item["seq"]: item for item in result["items"]}
        self.assertEqual(items[seqs[0]]["agreed"], 1)
        for seq in seqs[1:]:
            self.assertEqual(items[seq]["agreed"], 0)
            self.assertEqual(items[seq]["reviewed"], 0)
            self.assertIsNone(items[seq]["final_answer"])

    def test_repeat_run_is_stable(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        seqs = [item["seq"] for item in detail["items"]]
        centers = self._centers()
        texts = {seq: "7" for seq in seqs}
        engine = _ScriptedEngine(centers, [texts, dict(texts)])
        first = self._run(batch_id, engine)
        second = self._run(batch_id, engine)
        self.assertEqual(
            [item["final_answer"] for item in first["items"]],
            [item["final_answer"] for item in second["items"]],
        )
        db = self.session()
        try:
            rows = db.query(ocr_service.OcrItem).filter_by(batch_id=batch_id).all()
            self.assertEqual(len(rows), len(seqs))
        finally:
            db.close()

    def test_missing_engine_marks_batch_failed(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]

        def boom():
            raise ImportError("没有 rapidocr")

        with mock.patch.object(answer_ocr, "load_engine", boom):
            db = self.session()
            try:
                result = ocr_service.run_batch(db, batch_id)
                db.commit()
            finally:
                db.close()
        self.assertEqual(result["batch"]["status"], "failed")
        self.assertIn("[ocr]", result["batch"]["message"])

    def test_committed_batch_is_not_reprocessed(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        seqs = [item["seq"] for item in detail["items"]]
        centers = self._centers()
        texts = {seq: "5" for seq in seqs}
        engine = _ScriptedEngine(centers, [texts, dict(texts)])
        self._run(batch_id, engine)
        self.assertEqual(
            self.client.post(url_of("ocr_commit", batch_id=batch_id)).status_code, 201
        )

        before = engine.calls
        result = self._run(batch_id, engine)
        self.assertEqual(result["batch"]["status"], "committed")
        self.assertEqual(engine.calls, before)


class AnswerOcrOptionalDependencyTest(unittest.TestCase):
    """J-10-08：基础安装不 import 识别引擎（lazy import 的判据）。"""

    def test_module_does_not_import_ocr_engine_at_import_time(self) -> None:
        import sys

        self.assertNotIn("rapidocr_onnxruntime", sys.modules)
        self.assertNotIn("onnxruntime", sys.modules)
