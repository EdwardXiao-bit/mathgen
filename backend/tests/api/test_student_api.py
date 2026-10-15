"""学生端三条接口的 HTTP 行为（task-24 / 契约 §5.2：报到、按卷号取题、自主练习）。

判分相关（`POST /api/attempts`）在 `test_attempts.py`，白名单守卫的负对照在
`test_student_whitelist.py`，老库补列在 `test_attempt_schema.py`。

尾部 `RouterWiringTests` 是**装配护栏**：本文件所有用例都跑在真实装配的 app 上
（`_student_support` 里那座会重复 include 的「临时挂载桥」已删），所以 `main.py`
一旦回退掉学生端路由，整组用例会以 404 大声失败。
"""
from __future__ import annotations

import unittest

from sqlalchemy import text

from app.contracts.api import ROUTES
from app.services.student_service import MAX_SEED

from tests.api._support import CONFIG_ADD_SUB, make_client, url_of
from tests.api._student_support import (
    STUDENT_ITEM_KEYS,
    StudentApiTestCase,
    assert_no_answer_leak,
    assert_public_paper,
    assert_public_practice,
)


class ReportTests(StudentApiTestCase):
    """`POST /api/students`（FR-S2 / D41）。"""

    def test_first_report_creates_student_and_returns_201(self):
        response = self.report(name="张三", student_no="20240101")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"student_id": 1})

    def test_second_report_with_same_pair_is_200_and_same_id(self):
        first = self.report(name="张三", student_no="20240101")
        second = self.report(name="张三", student_no="20240101")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["student_id"], first.json()["student_id"])
        with self.session() as db:
            rows = db.execute(text("select count(*) from student")).scalar()
        self.assertEqual(rows, 1, "重复报到不该造出第二个张三")

    def test_report_trims_whitespace_so_padded_name_is_the_same_student(self):
        first = self.report(name="张三", student_no="20240101")
        padded = self.report(name="  张三  ", student_no=" 20240101 ")
        self.assertEqual(padded.status_code, 200, padded.text)
        self.assertEqual(padded.json()["student_id"], first.json()["student_id"])

    def test_same_name_different_student_no_is_two_students(self):
        first = self.report(name="张三", student_no="20240101")
        second = self.report(name="张三", student_no="20240102")
        self.assertEqual(second.status_code, 201, second.text)
        self.assertNotEqual(second.json()["student_id"], first.json()["student_id"])

    def test_blank_name_is_422_from_service_guard(self):
        response = self.report(name="   ", student_no="20240101")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("姓名", response.json()["error"]["message"])

    def test_blank_student_no_is_422(self):
        response = self.report(name="张三", student_no="   ")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("学号", response.json()["error"]["message"])

    def test_empty_string_is_422_from_request_model(self):
        """空串走的是**请求模型**（`min_length=1`）而不是服务层守卫，两条路都是 422。"""
        response = self.report(name="", student_no="20240101")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("请求参数校验失败", response.json()["error"]["message"])

    def test_missing_field_is_422(self):
        response = self.client.post(url_of("create_student"), json={"name": "张三"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


class StudentPaperTests(StudentApiTestCase):
    """`GET /api/student/paper/{code}`（FR-S4 / D74）。"""

    def test_published_paper_returns_items_without_answers(self):
        _paper_id, code, detail = self.published_paper()
        response = self.student_paper(code)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], len(payload["items"]))
        self.assertEqual(payload["total"], len(detail["items"]))
        self.assertEqual(payload["title"], detail["title"])
        assert_public_paper(self, payload)
        assert_no_answer_leak(self, payload)

    def test_item_display_is_byte_identical_to_teacher_snapshot(self):
        """D27 快照优先：学生看到的题干与老师卷面**逐字一致**。"""
        _paper_id, code, detail = self.published_paper()
        payload = self.student_paper(code).json()
        teacher_display = [item["display"] for item in detail["items"]]
        self.assertEqual([item["display"] for item in payload["items"]], teacher_display)

    def test_items_are_numbered_from_one_without_gaps(self):
        _paper_id, code, detail = self.published_paper()
        payload = self.student_paper(code).json()
        self.assertEqual([item["seq"] for item in payload["items"]], list(range(1, len(detail["items"]) + 1)))

    def test_question_type_is_the_paper_question_type(self):
        _paper_id, code, _detail = self.published_paper()
        payload = self.student_paper(code).json()
        self.assertTrue(payload["items"])
        for item in payload["items"]:
            self.assertEqual(item["question_type"], "oral")

    def test_unknown_code_is_404_code_not_found(self):
        response = self.student_paper("9999-0001")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "CODE_NOT_FOUND")

    def test_draft_paper_code_is_404_and_does_not_leak_existence(self):
        """未发布的卷没有卷号（契约 §6）：拿一个不存在的卷号必须与「未发布」同样返回 404。"""
        paper = self.create_paper()
        self.assertEqual(paper["status"], "draft")
        response = self.student_paper("2610-9999")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "CODE_NOT_FOUND")

    def test_items_reflect_snapshot_not_a_rerun_of_the_engine(self):
        """引擎被换掉后，老卷的题干不该变（D27：读 `paper_item.payload`，不重跑引擎）。"""
        _paper_id, code, detail = self.published_paper()
        with patch_generate_boom():
            payload = self.student_paper(code)
        self.assertEqual(payload.status_code, 200, payload.text)
        self.assertEqual(
            [item["display"] for item in payload.json()["items"]],
            [item["display"] for item in detail["items"]],
        )


class PracticeTests(StudentApiTestCase):
    """`POST /api/student/practice`（FR-S3 / D75）。"""

    def test_practice_returns_seed_and_items_without_answers(self):
        response = self.practice()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsInstance(payload["seed"], int)
        self.assertEqual(payload["total"], 6)
        assert_public_practice(self, payload)
        assert_no_answer_leak(self, payload)

    def test_same_seed_gives_byte_identical_items(self):
        first = self.practice(seed=2025).json()
        second = self.practice(seed=2025).json()
        self.assertEqual(first["seed"], 2025)
        self.assertEqual(first["items"], second["items"], "同 seed 必得同一套题（引擎核心保证）")

    def test_without_seed_the_server_picks_one_and_returns_it(self):
        first = self.practice().json()
        second = self.practice().json()
        self.assertNotEqual(first["seed"], second["seed"], "两次随机取到同一 seed 的概率约 5e-10")
        self.assertNotEqual(first["items"], second["items"])

    def test_seed_is_echoed_back_so_the_student_can_resubmit_it(self):
        payload = self.practice(seed=7).json()
        self.assertEqual(payload["seed"], 7)
        again = self.practice(seed=payload["seed"]).json()
        self.assertEqual(again["items"], payload["items"])

    def test_count_comes_from_the_request_slider(self):
        payload = self.practice(config={"count": 3}).json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(len(payload["items"]), 3)

    def test_large_count_is_accepted_up_to_the_contract_ceiling(self):
        payload = self.practice(config={"count": 100}, seed=1).json()
        self.assertEqual(payload["total"], 100)

    def test_seed_above_maximum_is_422(self):
        response = self.practice(seed=MAX_SEED + 1)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_negative_seed_is_422(self):
        response = self.practice(seed=-1)
        self.assertEqual(response.status_code, 422, response.text)

    def test_unknown_op_value_is_422_from_the_request_model(self):
        """不在枚举里的算符连模型都过不了 → 422（`CONFIG_INVALID` 400 留给语义违约）。"""
        response = self.practice(config={"ops": ["%"]})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_duplicate_ops_is_400_config_invalid(self):
        """模型能收（都是合法枚举），但语义违约由服务层拦下 → 400。"""
        response = self.practice(config={"ops": ["+", "+"]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "CONFIG_INVALID")

    def test_range_r7_without_max_value_is_400_config_invalid(self):
        response = self.practice(config={"ranges": ["R7"], "max_value": None})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "CONFIG_INVALID")

    def test_empty_ranges_is_422_from_model(self):
        response = self.practice(config={"ranges": []})
        self.assertEqual(response.status_code, 422, response.text)

    def test_practice_does_not_write_anything(self):
        self.practice()
        with self.session() as db:
            papers = db.execute(text("select count(*) from paper")).scalar()
            attempts = db.execute(text("select count(*) from attempt")).scalar()
        self.assertEqual((papers, attempts), (0, 0), "练习出题不该留下任何记录")


# ---- 装配护栏 -------------------------------------------------------------

#: 装配探测的路径表（路由名 + 路径参数）。**方法刻意不在表里**——由真实 app 的
#: OpenAPI 现算，见 `probe_methods_of_real_app()`：路径存在 → 405，路径不存在 → 404。
WIRING_PROBES = (
    ("create_student", {}),
    ("student_paper", {"code": "0000-0000"}),
    ("student_practice", {}),
    ("submit_attempt", {}),
)

#: 探测方法的候选池。**不含 HEAD**：starlette 的 `Route` 注册 GET 时会自动补上 HEAD，
#: 拿它探测会拿到 200，判据就失去意义。
_PROBE_METHODS = ("PUT", "PATCH", "DELETE", "GET", "POST")


def probe_methods_of_real_app() -> dict[str, str]:
    """真实 app 上每条路径的探测方法：**一个它没有注册的方法**。

    判据是「路径在 → 405，路径不在 → 404」，所以方法必须真的没注册。
    方法从 `/openapi.json`（公开产物，不是框架内部结构）**现算**，
    而不是在表里写死：谁往这些路径上加新方法，这里会自动换一个没注册的。
    写死过一次的代价就在眼前——M5/D80 给 `/api/attempts` 加了成绩列表（GET）之后，
    表里原来写的 `GET /api/attempts` 变成了**合法请求**，探测直接返回 200：
    判据没坏，是它手里的方法过期了。现算之后这条路不会再重演。
    """
    with make_client() as client:
        paths = client.get("/openapi.json").json()["paths"]
    chosen: dict[str, str] = {}
    for name, params in WIRING_PROBES:
        url = ROUTES[name].format(**params)
        declared = {method.upper() for method in paths.get(url, {})}
        method = next((m for m in _PROBE_METHODS if m not in declared), None)
        if method is None:
            raise AssertionError(
                f"{url} 把探测候选方法全占了（已注册 {sorted(declared)}）——"
                f"要么扩候选池，要么这条路径的方法多到不正常"
            )
        chosen[name] = method
    return chosen


class RouterWiringTests(unittest.TestCase):
    """真实 app（**没有桥**）必须真的在服务学生端这 4 条路径。

    这里刻意**不**去 `app.routes` 里找路径。FastAPI 0.143 / starlette 1.8 的
    `include_router` 不再把子路由摊平，而是存成 `_IncludedRouter`（`path is None`），
    所以 `{getattr(r, "path", None) for r in app.routes}` 在「装配了」与「没装配」
    两种世界里给出**完全相同**的集合——那样的断言不是「抓到了问题」，而是**恒红**：
    它会把人推去修一个不存在的问题（Lead 定为 V11：判据必须对它要区分的两种状态
    给出不同结论）。改成真实请求，顺带就不再依赖任何框架内部结构。
    """

    def test_the_four_paths_are_routed_not_missing(self):
        methods = probe_methods_of_real_app()
        with make_client() as client:
            for name, params in WIRING_PROBES:
                with self.subTest(route=name):
                    url = ROUTES[name].format(**params)
                    response = client.request(methods[name], url)
                    self.assertEqual(
                        response.status_code,
                        405,
                        f"{methods[name]} {url} 没进真实 app（期望「方法不对」的 "
                        f"405，实得 {response.status_code}）：{response.text}",
                    )
                    self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_real_app_openapi_advertises_them(self):
        """OpenAPI 是前端的权威：路径没进 schema，前端就没法生成调用代码。"""
        with make_client() as client:
            declared = set(client.get("/openapi.json").json()["paths"])
        for name in ("create_student", "student_paper", "student_practice", "submit_attempt"):
            with self.subTest(route=name):
                self.assertIn(ROUTES[name], declared)

    def test_real_app_serves_a_full_student_round_trip(self):
        """四条路径各走一次真实请求（这既是装配证据，也是「真机可用」证据）。"""
        with make_client() as client:
            report = client.post(
                ROUTES["create_student"], json={"name": "张三", "student_no": "20240101"}
            )
            self.assertEqual(report.status_code, 201, report.text)

            practice = client.post(ROUTES["student_practice"], json=dict(CONFIG_ADD_SUB))
            self.assertEqual(practice.status_code, 200, practice.text)
            practice_body = practice.json()
            self.assertEqual(practice_body["total"], CONFIG_ADD_SUB["count"])

            attempt = client.post(
                ROUTES["submit_attempt"],
                json={
                    "student_id": report.json()["student_id"],
                    "attempt_uuid": "wiring-round-trip",
                    "config": dict(CONFIG_ADD_SUB),
                    "seed": practice_body["seed"],
                    "answers": [],
                    "duration_ms": 1,
                },
            )
            self.assertEqual(attempt.status_code, 201, attempt.text)

            paper = client.post(
                ROUTES["create_paper"], json=dict(CONFIG_ADD_SUB), params={"seed": 2025}
            ).json()
            code = client.post(ROUTES["publish_paper"].format(paper_id=paper["id"])).json()["code"]
            student_paper = client.get(ROUTES["student_paper"].format(code=code))
            self.assertEqual(student_paper.status_code, 200, student_paper.text)
            payload = student_paper.json()
            self.assertEqual(payload["total"], CONFIG_ADD_SUB["count"])
            for item in payload["items"]:
                self.assertEqual(set(item), set(STUDENT_ITEM_KEYS), item)


class WiringProbeNegativeControlTests(unittest.TestCase):
    """V11 负对照：探测机制在「没装配」的世界里必须给出**不同**结论。

    用一个只挂了老师端 router 的 app 复刻「`main.py` 装配学生端之前」的状态。
    同一条探测请求在那里是 404，在真实 app 上是 405（见上一条用例）——两边不一样，
    这条护栏才有信息量。若哪天有人把判据换回「在 `app.routes` 里找 path」，这里仍会绿，
    但上一条会变成恒红，Lead 的 V11 正是为了防这个。
    """

    def test_probe_reports_404_when_the_router_is_not_mounted(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routers import papers as papers_router

        application = FastAPI()
        application.include_router(papers_router.router)  # 只有老师端，正是装配前的样子

        # 与真实 app **同一批方法**，只换世界：那边 405，这边必须 404。
        methods = probe_methods_of_real_app()
        with TestClient(application) as client:
            for name, params in WIRING_PROBES:
                with self.subTest(route=name):
                    url = ROUTES[name].format(**params)
                    self.assertEqual(
                        client.request(methods[name], url).status_code,
                        404,
                        f"{methods[name]} {url} 在没装配的 app 里也给了 405——那 405 就什么都证明不了",
                    )
            # 同一个 app 里，老师端路径换个不支持的方法就是 405：证明 404 不是「所有请求都 404」
            self.assertEqual(client.request("DELETE", ROUTES["list_papers"]).status_code, 405)


# ---- 小工具 ---------------------------------------------------------------

class patch_generate_boom:
    """把 `app.engine.generate` 换成必然抛异常的替身（D27 / D75 的「不许重跑」判据）。"""

    def __enter__(self):
        from tests.api._support import patch_engine

        def boom(*_args, **_kwargs):
            raise AssertionError("不该调用 engine.generate：这条路径应读快照或短路")

        self._ctx = patch_engine(generate=boom)
        self._ctx.__enter__()
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)
