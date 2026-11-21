"""模式 C 端到端判据（M18-07，J-10-01/02/03/08/10 的 HTTP 侧）。

与 `tests/api`、`tests/services` 的分工：

- `tests/api/test_ocr_batches.py` 只管单条接口语义（multipart、状态码、字段）；
- `tests/services/*` 只管算法与批次执行（几何、识别归一、入档同构）；
- **这里走完整条老师端链路**：上传照片 → 后台识别（注入脚本化引擎，替换掉线程池那一跳）
  → 详情接口拿到「自动通过 / 需人工」→ 裁图路由能取到 PNG → 逐题确认 → 入档
  → 成绩列表与逐题明细与在线作答同形 → 导出里能看到这一条。

## 为什么可以替换 `enqueue`

线程池那一步在测试里是**唯一不稳定的东西**（要等线程、要独立的会话工厂）。
判据把 `artdeco.routers.ocr.ocr_jobs.enqueue` 换成「同步跑 `run_batch(engine=fake)`」，
除这一跳之外全部走 HTTP 与真实服务函数；`test_01` 末尾还断言批次状态与消息，
证明替换没有绕过状态机。真实线程池的行为由 `tests/services/test_ocr_run_batch.py` 覆盖。

## 真实样本（J-10-08）

`test_real_sample_pipeline` 只在 `MATHGEN_OCR_E2E=1`、装了 `rapidocr-onnxruntime`、
且 `.tmp-ocr-samples/` 里有照片时运行 —— 它要的是**不注入引擎**的真模型；
CI 里由单独的 job 打开该开关，本机默认跳过（`Ran ... skipped`）。
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:  # pragma: no cover - 没装 extra 的环境整模块 skip
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("需要 [ocr] extra（numpy / opencv）")

from app.contracts.api import ROUTES
from app.contracts.sheet import SheetMode
from app.models import Attempt, OcrBatch, OcrPage
from app.render import geometry, marking
from app.services import ocr_jobs, ocr_service
from app.services.sheet_ctx import build_sheet_context
from tests.api._support import ApiTestCase, url_of
from tests.api.test_ocr_batches import _page_bytes
from tests.services.test_ocr_run_batch import _ScriptedEngine

__all__ = ["OcrEndToEndTest", "OcrRealSampleTest"]

SAMPLES = Path(__file__).resolve().parents[3] / ".tmp-ocr-samples"


class OcrEndToEndTest(ApiTestCase):
    """上传 → 识别 → 校对 → 入档 → 成绩，全链路."""

    def setUp(self) -> None:  # noqa: D102
        super().setUp()
        self._tmp_root = Path(__file__).resolve().parents[2] / ".tmp-ocr-tests"
        self._tmp_root.mkdir(exist_ok=True)
        self._ocr_dir = tempfile.mkdtemp(prefix="e2e-", dir=str(self._tmp_root))
        self._saved_env = os.environ.get("MATHGEN_OCR_DIR")
        os.environ["MATHGEN_OCR_DIR"] = self._ocr_dir
        self.addCleanup(self._restore_env)

        paper = self.create_paper()
        self.paper_id = paper["id"]
        self.assertEqual(self.publish(self.paper_id).status_code, 200)
        self.paper = self.detail(self.paper_id)
        self.checksum = self._paper_checksum()
        self.centers = self._centers()
        self.answers = {item["seq"]: item["answer"] for item in self.paper["items"]}
        self.ocr_engine = _ScriptedEngine(
            self.centers,
            [
                {seq: answer for seq, answer in self.answers.items()},
                {seq: answer for seq, answer in self.answers.items()},
            ],
        )
        # 唯一的替换点：线程池 → 同步执行（见模块 docstring）
        patcher = mock.patch("app.routers.ocr.ocr_jobs.enqueue", self._enqueue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore_env(self) -> None:
        if self._saved_env is None:
            os.environ.pop("MATHGEN_OCR_DIR", None)
        else:
            os.environ["MATHGEN_OCR_DIR"] = self._saved_env
        shutil.rmtree(self._ocr_dir, ignore_errors=True)

    def _paper_checksum(self) -> str:
        db = self.session()
        try:
            return marking.sheet_checksum(build_sheet_context(db, self.paper_id, SheetMode.QUESTION))
        finally:
            db.close()

    def _centers(self) -> dict[int, tuple[float, float]]:
        db = self.session()
        try:
            boxes = geometry.geometry_of(
                build_sheet_context(db, self.paper_id, SheetMode.QUESTION)
            )["boxes"]
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

    def _enqueue(self, batch_id: str) -> None:
        db = self.session()
        try:
            ocr_service.run_batch(db, batch_id, engine=self.ocr_engine)
            db.commit()
        finally:
            db.close()

    def _upload(self, files, **form):
        data = {
            "paper_id": str(self.paper_id),
            "student_name": "肖铭皓",
            "student_no": "202414160455",
            "source": "phone",
        }
        data.update({key: str(value) for key, value in form.items()})
        return self.client.post(url_of("ocr_batches"), data=data, files=files)

    def _start_batch(self, pages: int = 1) -> dict:
        files = [
            ("files", (name, io.BytesIO(data), "image/jpeg"))
            for name, data in _page_bytes(self.checksum, pages=pages)
        ]
        response = self._upload(files)
        self.assertEqual(response.status_code, 201, response.text)
        detail = response.json()
        # 上传返回时后台已同步跑完（替换点），状态必须已经从 pending 走到 reviewing
        return self.client.get(url_of("ocr_batch", batch_id=detail["batch"]["id"])).json()

    def test_01_upload_recognize_review_commit_lands_in_attempts(self) -> None:
        """J-10-01/02：状态机走完，入档后成绩与在线作答同形."""
        detail = self._start_batch()
        batch = detail["batch"]
        self.assertEqual(batch["status"], "reviewing", batch)
        self.assertIn("自动通过", batch["message"])
        self.assertEqual(len(detail["items"]), len(self.paper["items"]))
        self.assertTrue(all(item["agreed"] for item in detail["items"]), detail["items"])

        # 裁图路由：真的返回 PNG（受控路由，不是静态目录）
        crop = self.client.get(url_of("ocr_crop", batch_id=batch["id"], seq=1))
        self.assertEqual(crop.status_code, 200, crop.text)
        self.assertEqual(crop.headers["content-type"], "image/png")
        self.assertTrue(crop.content.startswith(b"\x89PNG"))

        # 入档前的成绩列表为空（成绩只在 commit 时生成）
        self.assertEqual(self.client.get(url_of("list_attempts")).json()["total"], 0)

        commit = self.client.post(url_of("ocr_commit", batch_id=batch["id"]))
        self.assertEqual(commit.status_code, 201, commit.text)
        attempt_id = commit.json()["attempt_id"]

        attempts = self.client.get(url_of("list_attempts")).json()
        self.assertEqual(attempts["total"], 1)
        summary = attempts["attempts"][0]
        self.assertEqual(summary["id"], attempt_id)
        self.assertEqual(summary["paper_code"], self.paper["code"])
        self.assertEqual(summary["total"], len(self.paper["items"]))
        self.assertEqual(summary["correct"], len(self.paper["items"]))
        self.assertEqual(summary["score"], 100)

        attempt = self.client.get(url_of("get_attempt", attempt_id=attempt_id)).json()
        self.assertEqual(len(attempt["items"]), len(self.paper["items"]))
        for item in attempt["items"]:
            self.assertEqual(item["question_type"], self.paper["config"]["question_type"])
            self.assertTrue(item["is_correct"])
            self.assertEqual(str(item["answer"]), str(self.answers[item["seq"]]))

        export = self.client.get(url_of("export_attempts"), params={"format": "csv"})
        self.assertEqual(export.status_code, 200, export.text)
        self.assertIn("肖铭皓", export.content.decode("utf-8-sig"))

    def test_02_commit_before_review_is_409_then_idempotent(self) -> None:
        """J-10-02：有未确认题必须 409；确认后 201，重复提交 200 不新增."""
        from app.services import ocr_service as service

        files = [
            ("files", (name, io.BytesIO(data), "image/jpeg"))
            for name, data in _page_bytes(self.checksum)
        ]
        response = self._upload(files)
        batch_id = response.json()["batch"]["id"]
        # 让「两遍不一致」发生：只喂第 1 题，其余题没有文本 → 全部需人工
        db = self.session()
        try:
            service.run_batch(
                db, batch_id, engine=_ScriptedEngine(self.centers, [{1: "1"}])
            )
            db.commit()
        finally:
            db.close()

        blocked = self.client.post(url_of("ocr_commit", batch_id=batch_id))
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["error"]["code"], "OCR_ITEMS_UNREVIEWED")

        detail = self.client.get(url_of("ocr_batch", batch_id=batch_id)).json()
        for item in detail["items"]:
            patch = self.client.patch(
                url_of("ocr_item", batch_id=batch_id, seq=item["seq"]),
                json={"answer": str(self.answers[item["seq"]])},
            )
            self.assertEqual(patch.status_code, 200, patch.text)

        first = self.client.post(url_of("ocr_commit", batch_id=batch_id))
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(url_of("ocr_commit", batch_id=batch_id))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["attempt_id"], second.json()["attempt_id"])
        self.assertEqual(self.client.get(url_of("list_attempts")).json()["total"], 1)

    def test_03_wrong_sheet_is_rejected_before_any_row_is_written(self) -> None:
        """J-10-03：二维码 checksum 对不上 → 409；空白照片 → 422；都不落行."""
        page = _page_bytes(self.checksum)[0][1]

        wrong = self._upload(
            [("files", ("page-1.jpg", io.BytesIO(page), "image/jpeg"))],
            student_no="999",
        )
        self.assertEqual(wrong.status_code, 201, wrong.text)  # 同卷不同学生，正常
        # 真正错卷：checksum 换成另一张卷的
        other = self.create_paper({"ops": ["×"]})
        self.publish(other["id"])
        db = self.session()
        try:
            other_checksum = marking.sheet_checksum(
                build_sheet_context(db, other["id"], SheetMode.QUESTION)
            )
        finally:
            db.close()
        bad_page = _page_bytes(other_checksum)[0][1]
        rejected = self._upload([("files", ("page-1.jpg", io.BytesIO(bad_page), "image/jpeg"))])
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "OCR_PAGE_CONFLICT")

        blank = np.full((1400, 1000, 3), 255, dtype=np.uint8)
        ok, buffer = cv2.imencode(".jpg", blank)
        self.assertTrue(ok)
        empty = self._upload([("files", ("blank.jpg", io.BytesIO(buffer.tobytes()), "image/jpeg"))])
        self.assertEqual(empty.status_code, 422, empty.text)
        self.assertEqual(empty.json()["error"]["code"], "OCR_IMAGE_UNSUPPORTED")

    def test_04_delete_draft_batch_removes_rows_and_directory(self) -> None:
        """J-10-10：删草稿批次 = 行与目录都物理删除；已入档批次不可删."""
        detail = self._start_batch()
        batch_id = detail["batch"]["id"]
        batch_dir = Path(self._ocr_dir) / batch_id
        self.assertTrue(batch_dir.is_dir(), batch_dir)

        response = self.client.delete(url_of("ocr_batch", batch_id=batch_id))
        self.assertEqual(response.status_code, 204, response.text)
        self.assertFalse(batch_dir.exists(), "删批次后目录还在")
        db = self.session()
        try:
            self.assertIsNone(db.get(OcrBatch, batch_id))
            self.assertEqual(db.query(OcrPage).filter_by(batch_id=batch_id).count(), 0)
        finally:
            db.close()

    def test_05_stale_processing_batches_are_reset_on_startup(self) -> None:
        """J-10-01：进程崩在识别中途 → 启动时把 processing 退回 pending."""
        detail = self._start_batch()
        batch_id = detail["batch"]["id"]
        db = self.session()
        try:
            batch = db.get(OcrBatch, batch_id)
            batch.status = "processing"
            db.commit()
        finally:
            db.close()

        reset = ocr_jobs.reset_stale(self.engine)
        self.assertGreaterEqual(reset, 1)
        detail = self.client.get(url_of("ocr_batch", batch_id=batch_id)).json()
        self.assertEqual(detail["batch"]["status"], "pending")
        self.assertIn("中断", detail["batch"]["message"])


@unittest.skipUnless(
    os.environ.get("MATHGEN_OCR_E2E") == "1", "J-10-08 只在 MATHGEN_OCR_E2E=1 时真跑模型"
)
@unittest.skipUnless(
    importlib.util.find_spec("rapidocr_onnxruntime") is not None,
    "J-10-08 需要装了 rapidocr-onnxruntime",
)
class OcrRealSampleTest(unittest.TestCase):
    """真实照片 → 定位 → 真模型识别（不注入引擎）."""

    def test_real_sample_pipeline_produces_rectified_pages_and_text(self) -> None:
        samples = sorted(SAMPLES.glob("sample-0*.jpg"))
        if not samples:
            self.skipTest(f"没有真实样本：{SAMPLES}")
        from app.services import answer_ocr, page_detect

        engine = answer_ocr.load_engine()
        self.assertIsNotNone(engine)

        rectified = page_detect.rectify(samples[0].read_bytes())
        self.assertEqual(rectified.image.shape, (1754, 1240, 3))
        self.assertIn("checksum", rectified.marking)

        # 只验「能出文本」：真实手写答案的正确率由阶段 2 的 240 题样本统计，
        # 不在这条判据里定阈值（否则样本一换就红）。
        detections = engine(answer_ocr.recognize_page.__globals__["cv2"].resize(
            rectified.image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC
        ))
        texts = [row[1] for row in (detections[0] or [])]
        self.assertGreaterEqual(len([text for text in texts if text.strip()]), 12)
