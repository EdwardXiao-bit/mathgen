"""S7-① 判据：Word 导出与 PDF 导出的排版守卫**口径不同**，且这是有意的。

`backend/app/routers/exports.py:122-123` 写着「Word 链路不经过 Chromium，因此**没有**
NFR-5 服务端守卫（判据口径不同）」。文档里（`docs/08` §8.2 第 3 条）曾把这句记成
「未验证」，本轮把它改成「已知口径差异」并在这里钉住：同一张必然超版的卷子，
PDF 路径 409、Word 路径 200，且 Word 响应**不带**守卫跳过证据头。

为什么 Word 可以没有守卫：Word 由文字处理器自己重排分页，不存在「一行被拦腰切在
两张纸上」这种东西；NFR-5 防的是 Chromium 打印通道的截断，二者不是同一条通路。

判据取向（V9）：本模块的三条断言合起来才构成一个可红的世界——若哪天有人把 PDF 分支的
守卫顺手也套到 Word 上，第 2 条立刻红；若有人把守卫从 PDF 上摘掉，第 1 条立刻红。
"""
from __future__ import annotations

import unittest

from tests.e2e_m6 import _nfr5_support as sup

#: 与 task-52 判据同一组合成题面：40 字符题面 + 最坏合法排版 ⇒ PDF 必然 409
SYNTH_OVERFLOW_CHARS = 40


class TestWordExportSkipsTheLayoutGuardOnPurpose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = sup.GuardClient(chars=SYNTH_OVERFLOW_CHARS, layout=sup.WORST).__enter__()
        cls.pdf_response = cls.client.export()
        cls.docx_response = cls.client.export(file_format="docx")
        cls.observation = {
            "content_chars": SYNTH_OVERFLOW_CHARS,
            "layout": sup.asdict(sup.WORST),
            "pdf_status": cls.pdf_response.status_code,
            "pdf_code": sup.error_code(cls.pdf_response),
            "docx_status": cls.docx_response.status_code,
            "docx_media_type": cls.docx_response.headers.get("content-type", ""),
            "docx_bytes": len(cls.docx_response.content),
            "docx_skip_headers": sorted(k for k in cls.docx_response.headers if "layout" in k.lower()),
        }
        sup.write_evidence("s7-01-docx-no-guard", cls.observation)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_the_same_sheet_is_rejected_on_the_pdf_path(self):
        """对照世界：同一份内容走 PDF 必须 409 —— 否则下面那条「Word 200」说明不了任何事。"""
        self.assertEqual(self.pdf_response.status_code, 409, self.pdf_response.text[:400])
        self.assertEqual(sup.error_code(self.pdf_response), "LAYOUT_DOES_NOT_FIT")

    def test_the_word_path_is_200_and_a_real_docx(self):
        response = self.docx_response
        self.assertEqual(response.status_code, 200, response.text[:400])
        self.assertIn("wordprocessingml", response.headers.get("content-type", ""))
        self.assertTrue(response.content.startswith(b"PK"), "Word 产物不是 zip 容器")
        self.assertGreater(len(response.content), 5000, "Word 产物小得不像一份卷子")

    def test_the_word_path_never_carries_the_guard_skip_header(self):
        """守卫跳过证据头只属于 PDF 分支；Word 分支出现它，说明有人的口径混了。"""
        headers = [k for k in self.docx_response.headers if k.lower().startswith("x-mathgen-")]
        self.assertEqual(headers, [], f"Word 响应带了守卫相关证据头：{headers}")
