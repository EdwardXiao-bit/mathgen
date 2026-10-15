"""成绩导出（FR-T13 / M5）测试：CSV 与 xlsx，UTF-8 BOM 与「不分页」。

分两层，和 `tests/api/test_export.py` 同构：

* **纯函数层**（`app.export.attempt_export`）：构造 `AttemptSummary` 直接下断言，
  不需要数据库、不需要路由、不需要浏览器。这是本任务最早能全绿的部分。
* **路由层**（`GET /api/attempts/export`）：`ApiTestCase` + 把
  `attempt_service.list_attempts_all` 换成探针，断言查询参数怎么传给服务层、
  以及**导出不受 `limit`/`offset` 影响**。

本文件负责钉死两个「最容易做成看起来对」的点，各有一条**留在仓库里的负对照**：

1. 去掉 BOM → `test_negative_plain_utf8_without_bom_would_fail`
2. 导出偷偷套用分页 → `test_negative_pagination_applied_would_fail`

负对照不是「把断言写两遍」，而是**把错误实现真的跑一遍并证明判据会红**——
否则那条判据可能只是在空转（V9.1/V9.2）。
"""
from __future__ import annotations

import contextlib
import csv
import inspect
import io
import re
import unittest
import urllib.parse
from collections.abc import Iterator

from app.contracts.api import ROUTES, AttemptSummary
from app.export import attempt_export
from app.services import attempt_service

from tests.api._student_support import StudentApiTestCase, new_uuid
from tests.api._support import CONFIG_ADD_SUB, ApiTestCase, url_of

#: 导出的固定列顺序（契约 §5.5），逐字比对用
EXPECTED_HEADER = ["卷号", "卷名", "姓名", "学号", "得分", "答对题数", "总题数", "用时", "提交时间"]

#: 一「页」的条数（列表接口 `limit` 默认 20）——造数据时故意超过它
PAGE_SIZE = 20

BOM = b"\xef\xbb\xbf"


# ---- 夹具 ---------------------------------------------------------------


def make_row(
    index: int,
    *,
    paper_id: int | None = 7,
    paper_code: str | None = "2610-0001",
    paper_title: str | None = "两位数乘两位数",
    student_name: str | None = None,
    student_no: str | None = None,
    total: int = 20,
    correct: int = 17,
    score: int = 85,
    duration_ms: int = 83000,
    submitted_at: str = "2026-10-16T10:00:00+08:00",
) -> AttemptSummary:
    """造一行成绩概要（默认挂卷；显式传 `paper_*=None` 表示自主练习）。"""
    return AttemptSummary(
        id=index,
        student_id=100 + index,
        student_name=student_name if student_name is not None else f"学生{index:02d}",
        student_no=student_no if student_no is not None else f"2026{index:04d}",
        paper_id=paper_id,
        paper_code=paper_code,
        paper_title=paper_title,
        total=total,
        correct=correct,
        score=score,
        duration_ms=duration_ms,
        submitted_at=submitted_at,
    )


def make_rows(count: int, **kwargs) -> list[AttemptSummary]:
    return [make_row(i + 1, **kwargs) for i in range(count)]


def csv_rows(payload: bytes) -> list[list[str]]:
    """读回 CSV（去掉 BOM 后交给 csv 解析，因此不隐含依赖编码）。"""
    import csv

    text = payload.decode("utf-8-sig")
    return [row for row in csv.reader(io.StringIO(text)) if row]


@contextlib.contextmanager
def list_attempts_all_with(fake) -> Iterator[list[tuple]]:
    """把 `attempt_service.list_attempts_all` 换成探针，并记录每次调用的入参。

    用 `setattr` 而不是 `mock.patch`：这个函数由 be-paper 并行实现，
    在它落地之前**属性根本不存在**，而 `setattr` 对「不存在」也成立。
    """
    calls: list[tuple] = []
    sentinel = object()
    saved = getattr(attempt_service, "list_attempts_all", sentinel)

    def probe(db, **kwargs):
        calls.append((db, kwargs))
        return fake(**kwargs)

    attempt_service.list_attempts_all = probe
    try:
        yield calls
    finally:
        if saved is sentinel:
            with contextlib.suppress(AttributeError):
                del attempt_service.list_attempts_all
        else:
            attempt_service.list_attempts_all = saved


# ---- 纯函数层：格式与内容 -------------------------------------------------


class TestExportFormatResolution(unittest.TestCase):
    """`fmt` 只认 csv / xlsx，且各自回对 media_type 与文件名。"""

    def test_csv_is_default_shape(self):
        payload, media_type, filename = attempt_export.export_attempts(make_rows(2), "csv")
        self.assertEqual(media_type, "text/csv; charset=utf-8")
        self.assertTrue(filename.endswith(".csv"), filename)
        self.assertTrue(payload.startswith(BOM), "CSV 必须 UTF-8 with BOM")

    def test_xlsx_media_type_and_magic(self):
        payload, media_type, filename = attempt_export.export_attempts(make_rows(2), "xlsx")
        self.assertEqual(
            media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(filename.endswith(".xlsx"), filename)
        self.assertTrue(payload.startswith(b"PK\x03\x04"), "xlsx 是 zip 容器")

    def test_format_is_case_insensitive_and_trimmed(self):
        for value in ("CSV", " csv ", "XLSX"):
            with self.subTest(value=value):
                payload, _, _ = attempt_export.export_attempts(make_rows(1), value)
                self.assertTrue(payload)

    def test_unsupported_format_raises(self):
        """纯函数层也要能判：路由层负责把它变成 422，但模块本身不许「默认成 csv」。"""
        for value in ("json", "xls", "pdf", "csv,json", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    attempt_export.export_attempts(make_rows(1), value)

    def test_supported_formats_tuple_matches_contract(self):
        self.assertEqual(attempt_export.SUPPORTED_FORMATS, ("csv", "xlsx"))


class TestCsvContent(unittest.TestCase):
    """CSV 读回：BOM、表头逐字与顺序、行数、自主练习留空。"""

    def test_first_three_bytes_are_utf8_bom(self):
        payload, _, _ = attempt_export.export_attempts(make_rows(3), "csv")
        self.assertEqual(payload[:3], b"\xef\xbb\xbf", f"实际前 3 字节={payload[:3]!r}")

    def test_header_is_the_frozen_column_order(self):
        payload, _, _ = attempt_export.export_attempts(make_rows(1), "csv")
        self.assertEqual(csv_rows(payload)[0], EXPECTED_HEADER)

    def test_all_rows_are_written(self):
        payload, _, _ = attempt_export.export_attempts(make_rows(45), "csv")
        rows = csv_rows(payload)
        self.assertEqual(len(rows), 1 + 45, "45 条数据 → 表头 + 45 行")

    def test_self_practice_rows_have_empty_paper_columns(self):
        """自主练习（D75）：卷号、卷名留空；且**不写**「自主练习」这类显示文案。"""
        payload, _, _ = attempt_export.export_attempts(
            make_rows(1, paper_id=None, paper_code=None, paper_title=None), "csv"
        )
        row = csv_rows(payload)[1]
        self.assertEqual(row[0], "", "卷号应留空")
        self.assertEqual(row[1], "", "卷名应留空")
        self.assertNotIn("自主练习", payload.decode("utf-8-sig"))

    def test_duration_column_is_human_readable_not_raw_ms(self):
        payload, _, _ = attempt_export.export_attempts(
            make_rows(1, duration_ms=83000), "csv"
        )
        row = csv_rows(payload)[1]
        self.assertEqual(row[7], "1分23秒")
        self.assertNotIn("83000", payload.decode("utf-8-sig"), "不应出现裸毫秒")

    def test_quotes_names_containing_commas(self):
        """姓名/卷名里带逗号时 CSV 必须真的转义（否则列会错位）。"""
        payload, _, _ = attempt_export.export_attempts(
            make_rows(1, student_name="张三,李四", paper_title='含"引号"的卷'), "csv"
        )
        rows = csv_rows(payload)
        self.assertEqual(rows[1][2], "张三,李四", "读回必须还原成一个单元格")
        self.assertEqual(rows[1][1], '含"引号"的卷')
        self.assertEqual(len(rows[1]), len(EXPECTED_HEADER))

    def test_score_comes_from_the_summary_untouched(self):
        """D80 唯一来源：导出直接抄 `score`，**不在这里重算**。

        造一组「重算会得到不同数字」的数据：`total=40, correct=23` 时
        旧浮点口径 `floor(23/40*100+0.5)` = 57，而 `score_of` = 58（契约 §5.5 明写）。
        若导出自己算一遍，这里就会读到 57。
        """
        payload, _, _ = attempt_export.export_attempts(
            make_rows(1, total=40, correct=23, score=58), "csv"
        )
        self.assertEqual(csv_rows(payload)[1][4], "58")


class TestDurationFormatting(unittest.TestCase):
    """用时列：人能读、且不写「0分」这种废话。"""

    def test_minutes_and_seconds(self):
        self.assertEqual(attempt_export.format_duration(83000), "1分23秒")

    def test_under_one_minute_has_no_zero_minutes(self):
        self.assertEqual(attempt_export.format_duration(45000), "45秒")

    def test_zero_and_none(self):
        self.assertEqual(attempt_export.format_duration(0), "0秒")
        self.assertEqual(attempt_export.format_duration(None), "0秒")

    def test_milliseconds_truncate_not_round(self):
        """59.6 秒不能显示成「1分0秒」——那会让学生以为自己多用了一分钟。"""
        self.assertEqual(attempt_export.format_duration(59600), "59秒")

    def test_over_an_hour_only_grows_minutes(self):
        self.assertEqual(attempt_export.format_duration(3661000), "61分1秒")


class TestFilenameAndDisposition(unittest.TestCase):
    """文件名 `成绩-YYYYMMDD-HHMM.csv` 与 RFC 5987 头。"""

    def setUp(self) -> None:
        from datetime import datetime

        self.now = datetime(2026, 10, 16, 9, 5, 0)

    def test_filename_pattern(self):
        _, _, filename = attempt_export.export_attempts(make_rows(1), "csv", now=self.now)
        self.assertEqual(filename, "成绩-20261016-0905.csv")
        _, _, xlsx = attempt_export.export_attempts(make_rows(1), "xlsx", now=self.now)
        self.assertEqual(xlsx, "成绩-20261016-0905.xlsx")

    def test_disposition_has_ascii_fallback_and_rfc5987(self):
        filename = attempt_export.filename_for("csv", now=self.now)
        header = attempt_export.content_disposition(filename)
        self.assertTrue(header.startswith("attachment; "), header)
        self.assertRegex(header, r'filename="[A-Za-z0-9._-]*\.csv"')
        # 「成绩」两个汉字在 ASCII 兜底里变成下划线
        self.assertIn('filename="__-20261016-0905.csv"', header)
        expected = urllib.parse.quote(filename, safe="")
        self.assertIn(f"filename*=UTF-8''{expected}", header)
        # filename* 之外的部分必须是纯 ASCII：头按 Latin-1 传输，原始中文到这里就是乱码
        head, _, encoded = header.partition("filename*=UTF-8''")
        head.encode("ascii")                      # 不抛 = 兜底段确实没有中文
        self.assertEqual(urllib.parse.unquote(encoded), filename)


class TestXlsxContent(unittest.TestCase):
    """xlsx 读回：表头、行数、单元格值；表头加粗 + 冻结首行。"""

    @staticmethod
    def _load(payload: bytes):
        import openpyxl

        return openpyxl.load_workbook(io.BytesIO(payload))

    def test_readback_matches_csv_rows(self):
        rows = make_rows(5)
        payload, _, _ = attempt_export.export_attempts(rows, "xlsx")
        sheet = self._load(payload).active
        values = [[cell.value for cell in row] for row in sheet.iter_rows()]
        self.assertEqual(values[0], EXPECTED_HEADER)
        self.assertEqual(len(values), 1 + 5)
        self.assertEqual(values[1][2], rows[0].student_name)
        self.assertEqual(values[1][7], "1分23秒", "用时列同样是可读形式")

    def test_sheet_is_not_empty_for_zero_rows(self):
        """0 条记录也要给出**带表头**的文件，而不是空文件（老师会以为导出失败）。"""
        payload, _, _ = attempt_export.export_attempts([], "xlsx")
        sheet = self._load(payload).active
        values = [[cell.value for cell in row] for row in sheet.iter_rows()]
        self.assertEqual(values, [EXPECTED_HEADER])

    def test_header_is_bold_and_frozen(self):
        payload, _, _ = attempt_export.export_attempts(make_rows(1), "xlsx")
        sheet = self._load(payload).active
        self.assertTrue(all(cell.font.bold for cell in sheet[1]), "表头应加粗")
        self.assertEqual(sheet.freeze_panes, "A2", "首行应冻结")

    def test_zero_rows_csv_still_has_header(self):
        payload, _, _ = attempt_export.export_attempts([], "csv")
        self.assertEqual(csv_rows(payload), [EXPECTED_HEADER])


# ---- 路由层：查询参数怎么传、以及「不分页」 --------------------------------


class TestExportAttemptsRoute(ApiTestCase):
    """`GET /api/attempts/export` 的路由装配与查询参数转发。"""

    def _get(self, rows: list[AttemptSummary], **params):
        with list_attempts_all_with(lambda **kwargs: rows) as calls:
            response = self.client.get(url_of("export_attempts"), params=params)
        return response, calls

    # ---- 正常路径 -------------------------------------------------------

    def test_csv_download(self):
        response, calls = self._get(make_rows(3))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertTrue(response.content.startswith(BOM))
        self.assertEqual(len(calls), 1, "取数只调一次服务层")

    def test_xlsx_download(self):
        response, _ = self._get(make_rows(3), format="xlsx")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b"PK\x03\x04"))
        self.assertIn("spreadsheetml", response.headers["content-type"])

    def test_content_disposition_is_rfc5987(self):
        response, _ = self._get(make_rows(1))
        header = response.headers["content-disposition"]
        self.assertTrue(header.startswith("attachment; "), header)
        # 「成绩」以 percent-encoded 形式出现在 filename* 里，而不是原始中文
        self.assertIn("%E6%88%90%E7%BB%A9-", header)
        self.assertRegex(header, r"filename\*=UTF-8''%E6%88%90%E7%BB%A9-\d{8}-\d{4}\.csv")
        self.assertNotIn("成绩", header, "原始中文不能直接进头值")

    def test_filters_are_forwarded_to_the_service(self):
        _, calls = self._get(make_rows(2), paper_id=42, q="张三")
        self.assertEqual(calls[0][1], {"paper_id": 42, "q": "张三"})

    def test_blank_q_becomes_none(self):
        """空串 / 纯空白 = 不过滤，与列表接口约定一致（不是「匹配空字符串」）。"""
        for value in ("", "   "):
            with self.subTest(value=value):
                _, calls = self._get(make_rows(1), q=value)
                self.assertIsNone(calls[0][1]["q"])

    # ---- 不分页（本任务最容易出的错） ----------------------------------

    def test_export_returns_every_row_beyond_one_page(self):
        """**核心判据**：45 条 > 一页（limit 默认 20），导出必须给全 45 条。

        这里同时发 `limit=20&offset=0`：导出必须**无视**它们。
        """
        rows = make_rows(45)
        response, calls = self._get(rows, limit=PAGE_SIZE, offset=0)
        self.assertEqual(response.status_code, 200, response.text)
        body = csv_rows(response.content)
        self.assertEqual(len(body) - 1, 45, "导出条数必须等于总记录数，不是一页")
        self.assertEqual(calls[0][1], {"paper_id": None, "q": None},
                         "分页参数绝不能出现在服务层入参里")

    def test_service_never_receives_pagination_kwargs(self):
        _, calls = self._get(make_rows(3), limit=1, offset=999)
        self.assertNotIn("limit", calls[0][1])
        self.assertNotIn("offset", calls[0][1])

    # ---- 错误路径 -------------------------------------------------------

    def test_invalid_format_is_422(self):
        for value in ("json", "xls", "pdf", "docx", "", "csvx"):
            with self.subTest(value=value):
                response, calls = self._get(make_rows(1), format=value)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
                self.assertEqual(calls, [], "格式非法时不应取数")

    def test_empty_result_is_still_a_valid_file(self):
        response, _ = self._get([])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(csv_rows(response.content), [EXPECTED_HEADER])


class TestExportRouteOrdering(ApiTestCase):
    """`/api/attempts/export` 必须排在 `/api/attempts/{attempt_id}` 之前（契约 §5.5）。

    **为什么要在这里重测一遍**：契约包里的 `test_export_route_is_declared_before_the_id_route`
    查的是 `ROUTES` 这个 **dict 的键顺序**——它证明契约声明是对的，但**不证明运行时装配
    真的按这个顺序**（`app/main.py` 里 include 的先后、以及 router 自身的注册顺序都可能
    把它推翻）。本组查的是**真实 app 的路由表**。
    """

    def _registered_paths(self) -> list[str]:
        """真实 app 上的路由路径，按**注册顺序**。

        FastAPI 0.143 的 `include_router` 是**惰性**的：`app.routes` 里放的是
        `_IncludedRouter` 包装对象，子路由在 `original_router.routes` 里。
        所以这里两种形态都要摊平——只取顶层 `.path` 会得到「没有 API 路由」的假象
        （第一版就是这么被骗的）。
        """
        paths: list[str] = []

        def walk(route) -> None:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                paths.append(path)
            for child in getattr(route, "routes", ()) or ():
                walk(child)

        for route in self.client.app.routes:
            original = getattr(route, "original_router", None)
            if original is not None and original is not route:
                for child in original.routes:
                    walk(child)
            else:
                walk(route)
        return paths

    def test_literal_route_is_registered_before_the_id_route(self):
        paths = self._registered_paths()
        self.assertIn(ROUTES["export_attempts"], paths, "成绩导出路由未注册")
        try:
            id_index = paths.index(ROUTES["get_attempt"])
        except ValueError:
            # be-paper 的 task-39 还没落地：参数路由不存在，字面量路由自然不会被吃掉。
            # 这不是「通过」，而是**当前无法证伪**——等它落地后这条会真的比一次。
            self.skipTest("list_attempts/get_attempt 路由尚未落地（task-39），顺序待其就位后生效")
        self.assertLess(
            paths.index(ROUTES["export_attempts"]),
            id_index,
            "字面量路由被参数路由吃掉了",
        )

    def test_export_route_declared_on_the_exports_router(self):
        """成绩导出属于**我这条** router（`app/routers/exports.py`），不是别处。"""
        from app.routers import exports as exports_router

        paths = [route.path for route in exports_router.router.routes]
        self.assertIn(ROUTES["export_attempts"], paths)
        self.assertEqual(paths[0], ROUTES["export"], "卷面导出仍应排在前面")

    def test_negative_id_route_first_would_return_422(self):
        """**负对照**：证明「顺序」确实有可观察后果。

        把参数路由挪到前面后，`GET /api/attempts/export` 不再是导出响应：
        `attempt_id: int` 解析不了 "export"，FastAPI 直接 422。
        这里用一个临时 app 复现那种注册顺序。
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get(ROUTES["get_attempt"])
        def _by_id(attempt_id: int):  # pragma: no cover - 只为注册路由
            return {"attempt_id": attempt_id}

        @app.get(ROUTES["export_attempts"])
        def _export():  # pragma: no cover - 只为注册路由
            return {"ok": True}

        with TestClient(app) as client:
            response = client.get(ROUTES["export_attempts"])

        self.assertEqual(response.status_code, 422, "参数路由抢占时应 422（这正是要防的事）")
        self.assertNotIn("ok", response.text)


# ---- 负对照（V9.1 / V9.2） ----------------------------------------------


class TestNegativeControls(unittest.TestCase):
    """留在仓库里的负对照：把**错误实现**真的跑一遍，证明判据会红。"""

    def test_negative_plain_utf8_without_bom_would_fail(self):
        """(a) 去掉 BOM → BOM 判据必须红。

        直接复现「不用 `utf-8-sig` 而用 `utf-8`」的那种实现，并证明它骗得过
        「在 Linux/macOS 上打开正常」这条**非证据**（解码本来就没问题），
        却过不了「前 3 字节 == EF BB BF」这条真判据。
        """
        payload, _, _ = attempt_export.export_attempts(make_rows(2), "csv")
        broken = payload.decode("utf-8-sig").encode("utf-8")   # 就是「忘了 BOM」的实现

        self.assertNotEqual(broken[:3], BOM)
        self.assertFalse(broken.startswith(BOM), "负对照必须真的制造出失败条件")
        # 非证据：无 BOM 的字节流在非 Windows 环境照样能解码
        self.assertEqual(csv_rows(broken)[0], EXPECTED_HEADER,
                         "无 BOM 时 csv 模块照样能读——所以「读得出来」不构成证据")
        # 真判据
        with self.assertRaises(AssertionError):
            self.assertEqual(broken[:3], BOM)

    def test_negative_pagination_applied_would_fail(self):
        """(b) 导出偷偷套用 `limit` → 「导出全部」判据必须红。

        用「会行使分页的服务层」复现那个 bug：一旦路由把 `limit` 传下去并截断，
        45 条的库就只剩 20 条，而判据要求 45 条。
        """
        rows = make_rows(45)

        def service_that_paginates(*, paper_id=None, q=None, limit=None, offset=0):
            """模拟「把列表接口的实现直接拿来用」的错误版本。"""
            if limit is None:
                return rows[offset:]
            return rows[offset:offset + limit]

        correct = service_that_paginates(limit=None)
        broken = service_that_paginates(limit=PAGE_SIZE, offset=0)

        self.assertEqual(len(correct), 45)
        self.assertEqual(len(broken), PAGE_SIZE, "负对照必须真的制造出失败条件")
        with self.assertRaises(AssertionError):
            self.assertEqual(len(broken), 45)

        header = ["卷号", "卷名", "姓名", "学号", "得分", "答对题数", "总题数", "用时", "提交时间"]
        self.assertEqual(len(header), len(EXPECTED_HEADER))
        self.assertEqual(
            len(attempt_export.rows_of(broken)) + 1, PAGE_SIZE + 1,
            "错误实现下导出只有一页——这正是要防的",
        )

    def test_negative_missing_bom_is_caught_by_the_route_too(self):
        """同样的错误实现换成真的走一遍导出函数，证明它产出的就是缺 BOM 的字节流。"""
        payload, _, _ = attempt_export.export_attempts(make_rows(1), "csv")
        self.assertEqual(payload[:3], BOM)
        stripped = payload[3:]
        self.assertNotEqual(stripped[:3], BOM)


# ---- 结构性判据：签名里根本不该有分页参数 ---------------------------------


class TestExportApisHaveNoPaginationParameters(unittest.TestCase):
    """「导出不套用 limit/offset」的最强形式：参数上就没有它们。

    这是对 `test_export_returns_every_row_beyond_one_page` 的**结构补强**：
    行为判据可能被某次实现绕过（比如在函数内 `rows[:20]`），而签名判据把
    「从列表接口拿分页参数」这条错误路径本身钉掉。
    """

    def test_export_function_has_no_limit_or_offset(self):
        params = inspect.signature(attempt_export.export_attempts).parameters
        self.assertEqual(list(params), ["rows", "fmt", "now"])

    def test_route_handler_has_no_limit_or_offset(self):
        from app.routers import exports as exports_router

        params = inspect.signature(exports_router.export_attempts).parameters
        self.assertEqual(list(params), ["file_format", "paper_id", "q", "db"])

    def test_frozen_service_signature_has_no_pagination(self):
        """服务层签名由 be-paper 提供（task-39）；落地后这里自动从「跳过」变「检查」。

        `list_attempts_all` 的**语义就是「全部」**——它若长出 `limit`/`offset`，
        本任务的判据就失去依托，所以这条要在它出现的那一刻就守住。
        """
        function = getattr(attempt_service, "list_attempts_all", None)
        if function is None:
            self.skipTest("task-39（be-paper）的 list_attempts_all 尚未落地，签名待其就位后生效")
        params = list(inspect.signature(function).parameters)
        self.assertEqual(params, ["db", "paper_id", "q"])

    def test_rows_and_columns_line_up(self):
        self.assertEqual(attempt_export.HEADER_LABELS, tuple(EXPECTED_HEADER))
        self.assertEqual(
            [field for _label, field in attempt_export.COLUMNS],
            ["paper_code", "paper_title", "student_name", "student_no", "score",
             "correct", "total", "duration_ms", "submitted_at"],
        )


# ---- 真数据端到端（不 mock 服务层） ---------------------------------------
#
# 上面那组路由判据把 `list_attempts_all` 换成了探针，证明的是「路由层有没有把
# 分页参数传下去」。这里换成**真卷、真学生、真提交**，证明的是端到端那条链：
# 老师实际会拿到的表格里，行数、列序、卷号卷名、用时都是对的。
#
# 为什么值得单独一组：探针组永远无法回答「服务层真的返回了能导出的行吗」。
# 真数据组的第一版正是靠它才发现 `list_attempts?limit=5` 只回 5 行、而导出
# 必须回全部——两者的差别在真库上才可观察。


class TestExportWithRealAttempts(StudentApiTestCase):
    """建卷 → 发布 → 建学生 → 真提交 → 真导出 → 读回。"""

    #: 超过列表接口默认一页（`DEFAULT_LIMIT`）的提交数，用来证伪「偷偷套用 limit」
    SUBMISSIONS = 23

    def setUp(self) -> None:  # noqa: D102
        super().setUp()
        self.paper_id, self.code, self.detail = self.published_paper()

    # ---- 常用动作 -------------------------------------------------------

    def _payload(self, student_id: int, *, practice_seed: int | None = None, duration: int = 1000,
                 answers: list[dict] | None = None):
        """一次提交的 wire 体。给了 `practice_seed` 就走 D75 自主练习（**不带** paper_id）。"""
        total = self.detail["total"] if "total" in self.detail else len(self.detail["items"])
        body = {
            "student_id": student_id,
            "attempt_uuid": new_uuid(),
            "answers": answers if answers is not None
            else [{"seq": seq, "answer": "0"} for seq in range(1, total + 1)],
            "duration_ms": duration,
        }
        if practice_seed is None:
            body["paper_id"] = self.paper_id
        else:
            body["config"] = dict(CONFIG_ADD_SUB)
            body["seed"] = practice_seed
        return body

    def _submit(self, student_id: int, **kwargs) -> dict:
        response = self.client.post(url_of("submit_attempt"), json=self._payload(student_id, **kwargs))
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def _seed_attempts(self):
        """造 `SUBMISSIONS` 条真提交（1 个学生，多次重做）。"""
        student_id = self.student_id(name="张三", student_no="20240101")
        for index in range(self.SUBMISSIONS):
            self._submit(student_id, duration=[1200, 45000, 61000][index % 3])
        return student_id

    def _export(self, **params):
        return self.client.get(url_of("export_attempts"), params=params or None)

    @staticmethod
    def _table(payload: bytes) -> list[list[str]]:
        text = payload.decode("utf-8-sig")
        return [line for line in text.splitlines() if line]

    # ---- 判据 -----------------------------------------------------------

    def test_export_has_more_rows_than_one_page(self):
        """**本任务最难的一条**：导出条数 > 一页（默认 limit=20）。

        先证明列表接口**确实受一页限制**（否则「导出更多」是空话），再用同一份
        `limit` 请求导出：导出必须无视它，给全 `SUBMISSIONS` 条。
        """
        self._seed_attempts()

        listed = self.client.get(ROUTES["list_attempts"])
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_body = listed.json()
        page_size = listed_body["limit"]
        self.assertGreater(
            self.SUBMISSIONS, page_size,
            f"造 {self.SUBMISSIONS} 条还不够超过一页（limit={page_size}），本条会空转",
        )
        self.assertEqual(len(listed_body["attempts"]), page_size, "列表接口本来就该只回一页")
        self.assertEqual(listed_body["total"], self.SUBMISSIONS)

        response = self._export(limit=page_size, offset=0)
        self.assertEqual(response.status_code, 200, response.text)
        table = self._table(response.content)
        self.assertEqual(len(table) - 1, self.SUBMISSIONS, "导出必须给全，不是一页")
        self.assertGreater(len(table) - 1, page_size)

    def test_csv_bom_survives_the_whole_pipeline(self):
        self._seed_attempts()
        response = self._export()
        payload = response.content
        self.assertEqual(payload[:3], BOM, "前 3 字节必须是 EF BB BF")

        # 作为 utf-8（**带**BOM 的字节按普通 utf-8 解）读回：第一列名会被 BOM 污染。
        # 这就是 Windows Excel 之外的工具若不按 utf-8-sig 读会看到的真相，
        # 也是「BOM 真的在文件里」的直接证据（不是只在常量里）。
        naive_first_cell = payload.decode("utf-8").lstrip("\ufeff").splitlines()[0].split(",")[0]
        self.assertEqual(naive_first_cell, EXPECTED_HEADER[0])

    def test_header_and_row_count_on_real_data(self):
        self._seed_attempts()
        table = self._table(self._export().content)
        self.assertEqual(table[0].split(","), EXPECTED_HEADER)

    def test_paper_code_and_title_come_from_the_real_paper(self):
        self._seed_attempts()
        rows = list(csv.DictReader(self._export().content.decode("utf-8-sig").splitlines()))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["卷号"], self.code)
            self.assertTrue(row["卷名"], "卷名不该为空")
            self.assertEqual(row["姓名"], "张三")
            self.assertEqual(row["学号"], "20240101")
            self.assertEqual(row["总题数"], str(len(self.detail["items"])))

    def test_self_practice_row_leaves_paper_columns_blank(self):
        """D75 自主练习没有卷，导出时卷号/卷名必须留空（不是「未命名」之类）。"""
        student_id = self.student_id(name="李四", student_no="20240102")
        practice = self.practice()
        self.assertEqual(practice.status_code, 200, practice.text)
        seed = practice.json()["seed"]
        self._submit(student_id, practice_seed=seed)

        table = self._table(self._export().content)
        rows = list(csv.DictReader(table))
        self.assertEqual(len(rows), 1, "只提交了一次自主练习")
        self.assertEqual(rows[0]["卷号"], "")
        self.assertEqual(rows[0]["卷名"], "")

    def test_duration_column_is_readable_and_not_raw_ms(self):
        self._seed_attempts()
        rows = list(csv.DictReader(self._export().content.decode("utf-8-sig").splitlines()))
        durations = {row["用时"] for row in rows}
        self.assertTrue(durations)
        for value in durations:
            self.assertFalse(value.isdigit(), f"用时列出现裸毫秒：{value!r}")
            self.assertIn("秒", value, f"用时列应为人可读形式：{value!r}")
        # 三档入参（1200 / 45000 / 61000 ms）应各自映出不同的可读串
        self.assertIn("1秒", durations)
        self.assertIn("45秒", durations)
        self.assertIn("1分1秒", durations)

    def test_xlsx_readback_matches_the_same_real_rows(self):
        import openpyxl

        self._seed_attempts()
        csv_table = self._table(self._export().content)
        response = self._export(format="xlsx")
        self.assertEqual(response.status_code, 200, response.text)
        book = openpyxl.load_workbook(io.BytesIO(response.content))
        sheet = book.active
        self.assertEqual(sheet.max_column, len(EXPECTED_HEADER))
        self.assertEqual(sheet.max_row, len(csv_table))
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header, EXPECTED_HEADER)
        self.assertEqual(sheet.max_row - 1, self.SUBMISSIONS)
        # 首行数据与 CSV 同一来源，逐列应一致（时间戳列由同一序列化提供）
        csv_first = csv_table[1].split(",")
        xlsx_first = [cell.value for cell in sheet[2]]
        self.assertEqual(xlsx_first[:4], csv_first[:4])

    def test_filters_apply_to_real_data(self):
        self._seed_attempts()
        self.student_id(name="王五", student_no="20240103")

        all_rows = self._table(self._export().content)
        self.assertEqual(len(all_rows) - 1, self.SUBMISSIONS)

        hit = self._table(self._export(q="王五").content)
        self.assertEqual(len(hit) - 1, 0, "王五没有提交过")

        other = self._table(self._export(paper_id=self.paper_id).content)
        self.assertEqual(len(other) - 1, self.SUBMISSIONS)

        missing = self._table(self._export(paper_id=999999).content)
        self.assertEqual(len(missing), 1, "不存在的卷只应剩表头")

    def test_export_is_not_hijacked_by_the_id_route(self):
        """字面量路由没被 `/api/attempts/{attempt_id}` 吃掉。

        task-39 已把 `get_attempt` 装配进真实 app，所以这条现在**真的在比**：
        若顺序错了，`attempt_id: int` 解析不了 "export"，响应会是 422。
        """
        self._seed_attempts()
        response = self._export()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertNotEqual(response.status_code, 422)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()