"""模式 C 上传/校对/入档的 API 判据（T-M18-03，docs/10 §7）。

为什么用「合成照片」而不是真照片：判据要能重复跑，也要能在 CI 里跑；
`tests/services/test_page_detect.py` 已经用 cv2 合成过与本系统打印物同几何的卷面，
这里复用它，只把二维码里的 checksum 换成**这张真卷的** `sheet_checksum()`，
于是上传路径（校验 checksum、落盘、建行）能被完整走到。

这些用例需要 `[ocr]` extra（numpy/opencv）；没装时整模块 skip，与页面定位判据一致。
"""
from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:  # pragma: no cover - 没装 extra 的环境直接跳过
    from tests.services.test_page_detect import photograph, printed_page
except unittest.SkipTest:  # 由被测模块自己抛出的 skip
    raise
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("需要 [ocr] extra（numpy / opencv）")

from app.contracts.api import ROUTES
from app.contracts.sheet import SheetMode
from app.models import Attempt, OcrBatch, OcrItem
from app.render import marking
from app.services import ocr_store
from app.services.sheet_ctx import build_sheet_context
from tests.api._support import ApiTestCase, url_of

__all__ = ["OcrBatchApiTest"]


def _page_bytes(checksum: str, *, pages: int = 1) -> list[tuple[str, bytes]]:
    """合成 1–2 页「照片」（JPEG 字节），二维码 checksum 与真卷一致."""
    page = printed_page(checksum)
    return [(f"page-{index + 1}.jpg", photograph(page, width=1500, seed=index))
            for index in range(pages)]


class OcrBatchApiTest(ApiTestCase):
    """上传 → 页/题行 → 校对 → 入档 / 删除 的整条链路."""

    def setUp(self) -> None:  # noqa: D102
        super().setUp()
        # 临时目录放在仓库内：本机 TEMP 可能不可写（沙箱 EPERM），而 mkdtemp 会悄悄落到 cwd
        self._tmp_root = Path(__file__).resolve().parents[2] / ".tmp-ocr-tests"
        self._tmp_root.mkdir(exist_ok=True)
        self._ocr_dir = tempfile.mkdtemp(prefix="ocr-", dir=str(self._tmp_root))
        import os

        self._saved_env = os.environ.get("MATHGEN_OCR_DIR")
        os.environ["MATHGEN_OCR_DIR"] = self._ocr_dir
        paper = self.create_paper()
        self.paper_id = paper["id"]
        published = self.publish(self.paper_id)
        self.assertEqual(published.status_code, 200, published.text)
        self.checksum = self._paper_checksum()
        # 后台识别线程不在 API 判据里真跑：这里只验上传/校对/入档的接口语义，
        # 识别流程由 tests/services/test_answer_ocr.py 注入假引擎覆盖。
        self._enqueue_patcher = mock.patch(
            "app.routers.ocr.ocr_jobs.enqueue", lambda *args, **kwargs: None
        )
        self._enqueue_patcher.start()
        self.addCleanup(self._enqueue_patcher.stop)

    def tearDown(self) -> None:  # noqa: D102
        import os

        if self._saved_env is None:
            os.environ.pop("MATHGEN_OCR_DIR", None)
        else:
            os.environ["MATHGEN_OCR_DIR"] = self._saved_env
        shutil.rmtree(self._ocr_dir, ignore_errors=True)
        super().tearDown()

    # ---- 工具 -----------------------------------------------------------

    def _paper_checksum(self) -> str:
        ctx = build_sheet_context(self.session(), self.paper_id, SheetMode.QUESTION)
        return marking.sheet_checksum(ctx)

    def _upload(self, files, **form):
        data = {
            "paper_id": str(self.paper_id),
            "student_name": "肖铭皓",
            "student_no": "202414160455",
            "source": "phone",
        }
        data.update({key: str(value) for key, value in form.items()})
        return self.client.post(
            url_of("ocr_batches"), data=data, files=files
        )

    def _create_batch(self, pages: int = 1) -> dict:
        files = [
            ("files", (name, io.BytesIO(data), "image/jpeg"))
            for name, data in _page_bytes(self.checksum, pages=pages)
        ]
        response = self._upload(files)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _review_all(self, batch: dict) -> None:
        for item in batch["items"]:
            response = self.client.patch(
                url_of("ocr_item", batch_id=batch["batch"]["id"], seq=item["seq"]),
                json={"answer": "0", "reviewed": True},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ---- 上传 -----------------------------------------------------------

    def test_upload_builds_pages_items_and_crops_absent_for_now(self) -> None:
        detail = self._create_batch(pages=1)
        batch = detail["batch"]
        self.assertEqual(batch["page_count"], 1)
        self.assertEqual(batch["status"], "pending")
        self.assertEqual(len(detail["pages"]), 1)
        self.assertEqual(detail["pages"][0]["status"], "ready")
        self.assertEqual(len(detail["items"]), 6)  # CONFIG_ADD_SUB 的 count=6
        self.assertTrue(all(not item["reviewed"] for item in detail["items"]))
        self.assertTrue(all(item["crop_url"] is None for item in detail["items"]))
        # 原图与校正图都落盘
        directory = Path(self._ocr_dir) / batch["id"]
        self.assertTrue((directory / "raw-1.jpg").is_file())
        self.assertTrue((directory / "page-1.jpg").is_file())

    def test_upload_two_pages(self) -> None:
        detail = self._create_batch(pages=2)
        self.assertEqual(detail["batch"]["page_count"], 2)
        self.assertEqual([page["page_no"] for page in detail["pages"]], [1, 2])

    def test_upload_too_many_pages_is_rejected(self) -> None:
        files = [
            ("files", (name, io.BytesIO(data), "image/jpeg"))
            for name, data in _page_bytes(self.checksum, pages=1)
        ] * 3
        response = self._upload(files)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_upload_other_paper_is_rejected_by_checksum(self) -> None:
        files = [
            ("files", ("not-ours.jpg", io.BytesIO(photograph(printed_page("0" * 16), width=1500)), "image/jpeg"))
        ]
        response = self._upload(files)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "OCR_PAGE_CONFLICT")

    def test_upload_non_image_is_rejected(self) -> None:
        files = [("files", ("note.txt", io.BytesIO(b"hello"), "image/jpeg"))]
        response = self._upload(files)
        self.assertEqual(response.status_code, 422, response.text)

    def test_upload_when_storage_full_is_rejected(self) -> None:
        saved = ocr_store.TOTAL_LIMIT_BYTES
        ocr_store.TOTAL_LIMIT_BYTES = 10
        try:
            response = self._upload([
                ("files", ("p.jpg", io.BytesIO(_page_bytes(self.checksum)[0][1]), "image/jpeg"))
            ])
        finally:
            ocr_store.TOTAL_LIMIT_BYTES = saved
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "OCR_STORAGE_FULL")

    # ---- 校对 -----------------------------------------------------------

    def test_patch_item_and_unreadable(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        response = self.client.patch(
            url_of("ocr_item", batch_id=batch_id, seq=2), json={"answer": " 7 "}
        )
        self.assertEqual(response.status_code, 200, response.text)
        item = next(i for i in response.json()["items"] if i["seq"] == 2)
        self.assertEqual(item["final_answer"], "7")
        self.assertTrue(item["reviewed"])

        response = self.client.patch(
            url_of("ocr_item", batch_id=batch_id, seq=3), json={"unreadable": True}
        )
        item = next(i for i in response.json()["items"] if i["seq"] == 3)
        self.assertEqual(item["final_answer"], "")
        self.assertTrue(item["reviewed"])

    def test_commit_requires_every_item_reviewed(self) -> None:
        detail = self._create_batch()
        response = self.client.post(url_of("ocr_commit", batch_id=detail["batch"]["id"]))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "OCR_ITEMS_UNREVIEWED")

    def test_commit_creates_attempt_and_is_idempotent(self) -> None:
        detail = self._create_batch()
        self._review_all(detail)
        batch_id = detail["batch"]["id"]
        first = self.client.post(url_of("ocr_commit", batch_id=batch_id))
        self.assertEqual(first.status_code, 201, first.text)
        attempt_id = first.json()["attempt_id"]
        self.assertFalse(first.json()["replayed"])

        second = self.client.post(url_of("ocr_commit", batch_id=batch_id))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["attempt_id"], attempt_id)
        self.assertTrue(second.json()["replayed"])

        session = self.session()
        attempt = session.get(Attempt, attempt_id)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.paper_id, self.paper_id)
        self.assertEqual(attempt.total, 6)
        batch = session.get(OcrBatch, batch_id)
        assert batch is not None
        self.assertEqual(batch.status, "committed")
        self.assertEqual(batch.committed_attempt_id, attempt_id)
        # 入档后不能再改题、不能删批次
        response = self.client.patch(
            url_of("ocr_item", batch_id=batch_id, seq=1), json={"answer": "1"}
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.delete(url_of("ocr_batch", batch_id=batch_id)).status_code, 409)

    # ---- 删除 / 读取 -----------------------------------------------------

    def test_delete_draft_removes_rows_and_files(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        self.assertTrue((Path(self._ocr_dir) / batch_id).is_dir())
        response = self.client.delete(url_of("ocr_batch", batch_id=batch_id))
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.client.get(url_of("ocr_batch", batch_id=batch_id)).status_code, 404)
        self.assertFalse((Path(self._ocr_dir) / batch_id).exists())
        session = self.session()
        self.assertIsNone(session.get(OcrBatch, batch_id))
        self.assertEqual(session.query(OcrItem).count(), 0)

    def test_unknown_and_malformed_batch_ids_are_404(self) -> None:
        self.assertEqual(
            self.client.get(url_of("ocr_batch", batch_id="00000000-0000-4000-8000-000000000000")).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(url_of("ocr_batch", batch_id="..%2F..%2Fetc")).status_code, 404
        )

    def test_crop_route_is_controlled(self) -> None:
        detail = self._create_batch()
        batch_id = detail["batch"]["id"]
        # 本期还不裁图：受控路由如实 404，而不是把目录挂成静态资源
        response = self.client.get(url_of("ocr_crop", batch_id=batch_id, seq=1))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "ITEM_NOT_FOUND")
        self.assertEqual(
            self.client.get(url_of("ocr_crop", batch_id=batch_id, seq=99)).status_code, 404
        )
