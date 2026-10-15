"""task-25 主判据：D74 学生端白名单（逐层递归）+ 老师端 `answer` 反向断言。

每条断言都直接引契约 §5.2 第 253-260 行的原话；数据全部由本文件自己经真实 HTTP 造成。
"""

from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path

from tests.security._harness import (
    ARTIFACTS,
    SecurityHarness,
    create_paper,
    dump_evidence,
    practice_body,
    publish_paper,
    record,
    student_paper_body,
    teacher_detail,
)
from tests.security._whitelist import (
    STUDENT_ITEM_KEY_SET,
    STUDENT_PAPER_TOP_KEYS,
    STUDENT_PRACTICE_TOP_KEYS,
    WhitelistViolation,
    assert_guard_rejects,
    assert_student_payload,
    assert_teacher_detail_has_answers,
    assert_top_keys,
    collect_item_objects,
    naive_top_level_only_check,
    teacher_answers,
)

HERE = Path(__file__).resolve().parent
#: 能被「伪造成合法」的键名扫描（原始 JSON 文本层面的一次独立视角）
RAW_TEXT_FORBIDDEN = ('"answer"', '"steps"', '"is_correct"', '"student_answer"', '"expr"')


class TestStudentPaperWhitelist(SecurityHarness):
    """`GET /api/student/paper/{code}` —— 契约 §5.2 字段表 + D74 白名单。"""

    COUNT = 6

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.paper_id = create_paper(cls.client, count=cls.COUNT, seed=20261015)
        cls.code = publish_paper(cls.client, cls.paper_id)
        cls.body = student_paper_body(cls.client, cls.code)
        cls.detail = teacher_detail(cls.client, cls.paper_id)
        cls.raw_body = json.dumps(cls.body, ensure_ascii=False)
        record(
            "student_paper_response",
            where="GET /api/student/paper/{code}",
            code=cls.code,
            paper_id=cls.paper_id,
            top_keys=sorted(cls.body),
            total=cls.body.get("total"),
            item_keys=[sorted(item) for item in cls.body.get("items", [])],
            raw=cls.body,
        )

    def test_01_top_level_matches_the_documented_field_table(self) -> None:
        """§5.2 字段表：`paper_id`, `title`, `total`, `items[]`（多键即失败）。"""
        assert_top_keys(self.body, STUDENT_PAPER_TOP_KEYS, where="GET /api/student/paper/{code}")
        self.assertEqual(self.body["total"], self.COUNT)
        self.assertEqual(len(self.body["items"]), self.COUNT)

    def test_02_item_objects_are_whitelisted_recursively(self) -> None:
        """D74 主判据：题目对象**只允许** `seq`/`display`/`question_type`，逐层递归。"""
        items = assert_student_payload(
            self.body,
            where="GET /api/student/paper/{code}",
            expected_items=self.COUNT,
        )
        self.assertEqual(len(items), self.COUNT)
        for item in items:
            self.assertEqual(set(item), set(STUDENT_ITEM_KEY_SET))
        record(
            "d74_recursive_walk",
            where="GET /api/student/paper/{code}",
            item_paths=sorted(collect_item_objects(self.body)),
            all_item_keys=[sorted(item) for item in items],
            result="pass",
        )

    def test_03_raw_json_text_has_no_answer_bearing_field(self) -> None:
        """独立视角：不看结构，直接在**原始 JSON 文本**里找答案性字段名。"""
        for token in RAW_TEXT_FORBIDDEN:
            with self.subTest(token=token):
                self.assertNotIn(token, self.raw_body, f"学生端响应原文里出现了 {token}")

    def test_04_student_display_is_the_same_question_as_the_teacher_paper(self) -> None:
        """投影必须是**同一道题**（不是被截断/清空后「看起来合规」的壳）。

        题面同源是 D27 快照的语义：学生看到的题干与老师卷面逐字一致。
        """
        student = {item["seq"]: item["display"] for item in self.body["items"]}
        teacher = {item["seq"]: item["display"] for item in self.detail["items"]}
        self.assertEqual(student, teacher)
        for seq, display in student.items():
            with self.subTest(seq=seq):
                self.assertTrue(display.strip(), "题面不能是空白")
                self.assertRegex(display, r"\d", "题面里应当有数字（题干不是答案串）")

    def test_05_guard_has_teeth_on_this_real_response(self) -> None:
        """负对照（就地最小化）：把**真实响应**的副本改坏一处，守卫必须立刻红。

        三种改法覆盖三类越权：题目层多键、题目层少键、嵌套层藏答案。
        """
        cases: dict[str, dict] = {}
        tampered = copy.deepcopy(self.body)
        tampered["items"][0]["tags"] = ["加法"]
        cases["items[0] 多一个 tags"] = tampered
        tampered = copy.deepcopy(self.body)
        del tampered["items"][1]["display"]
        cases["items[1] 少一个 display"] = tampered
        tampered = copy.deepcopy(self.body)
        tampered["items"][2]["extra"] = {"answer": "42"}
        cases["items[2].extra.answer 深层藏答案"] = tampered
        tampered = copy.deepcopy(self.body)
        del tampered["items"][3]
        cases["items 少一题（数量与 total 对不上）"] = tampered

        for label, payload in cases.items():
            with self.subTest(tampered=label):
                reason = assert_guard_rejects(
                    payload,
                    where=f"真实响应副本（{label}）",
                    expected_items=self.COUNT,
                )
                self.assertTrue(reason)
                record("mutated_real_response", tampered=label, violation=reason)

    def test_06_teacher_detail_contains_answer_for_every_item(self) -> None:
        """反向断言（契约 §5.2 末句）：老师端 `GET /api/papers/{id}` 必须**含** `answer`。"""
        answers = assert_teacher_detail_has_answers(
            self.detail, where="GET /api/papers/{id}", expected_items=self.COUNT
        )
        self.assertEqual(set(answers), set(range(1, self.COUNT + 1)))
        for seq, answer in answers.items():
            with self.subTest(seq=seq):
                self.assertTrue(answer.strip(), "标准答案不能是空白")
        record(
            "teacher_reverse_assertion",
            where="GET /api/papers/{id}",
            answers={str(k): v for k, v in sorted(answers.items())},
            result="pass",
        )

    def test_07_reverse_assertion_discriminates(self) -> None:
        """反向断言本身也要有区分力：学生端响应喂进去必须失败、老师端删掉 answer 必须失败。"""
        with self.assertRaises(WhitelistViolation):
            assert_teacher_detail_has_answers(
                self.body, where="学生端响应（应当没有 answer）", expected_items=self.COUNT
            )
        stripped = copy.deepcopy(self.detail)
        for item in stripped["items"]:
            item.pop("answer", None)
        with self.assertRaises(WhitelistViolation):
            assert_teacher_detail_has_answers(
                stripped, where="老师端（人为删掉 answer）", expected_items=self.COUNT
            )
        self.assertEqual(len(teacher_answers(self.detail)), self.COUNT)


class TestStudentPracticeWhitelist(SecurityHarness):
    """`POST /api/student/practice` —— 自主练习同一条 D74 白名单。"""

    COUNT = 5

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.body = practice_body(cls.client, count=cls.COUNT, seed=7)
        cls.raw_body = json.dumps(cls.body, ensure_ascii=False)
        record(
            "student_practice_response",
            where="POST /api/student/practice",
            top_keys=sorted(cls.body),
            total=cls.body.get("total"),
            seed=cls.body.get("seed"),
            item_keys=[sorted(item) for item in cls.body.get("items", [])],
            raw=cls.body,
        )

    def test_01_top_level_matches_the_documented_field_table(self) -> None:
        assert_top_keys(self.body, STUDENT_PRACTICE_TOP_KEYS, where="POST /api/student/practice")
        self.assertEqual(self.body["total"], self.COUNT)

    def test_02_item_objects_are_whitelisted_recursively(self) -> None:
        items = assert_student_payload(
            self.body, where="POST /api/student/practice", expected_items=self.COUNT
        )
        self.assertEqual(len(items), self.COUNT)
        record(
            "d74_recursive_walk",
            where="POST /api/student/practice",
            item_paths=sorted(collect_item_objects(self.body)),
            all_item_keys=[sorted(item) for item in items],
            result="pass",
        )

    def test_03_raw_json_text_has_no_answer_bearing_field(self) -> None:
        for token in RAW_TEXT_FORBIDDEN:
            with self.subTest(token=token):
                self.assertNotIn(token, self.raw_body, f"练习响应原文里出现了 {token}")

    def test_04_seed_is_reported_and_reproduces_the_same_questions(self) -> None:
        """D75 的立足点：`seed` 必须回传，且同 `(config, seed)` 必得同一套题。"""
        again = practice_body(self.client, count=self.COUNT, seed=self.body["seed"])
        self.assertEqual(again["items"], self.body["items"])
        record(
            "practice_seed_reproduction",
            seed=self.body["seed"],
            identical=again["items"] == self.body["items"],
        )

    def test_05_guard_has_teeth_on_this_real_response(self) -> None:
        tampered = copy.deepcopy(self.body)
        tampered["items"][0]["difficulty"] = 2
        reason = assert_guard_rejects(
            tampered, where="练习响应副本（items[0] 多一个 difficulty）", expected_items=self.COUNT
        )
        self.assertIn("difficulty", reason)
        tampered = copy.deepcopy(self.body)
        tampered["data"] = {"steps": [{"answer": "1"}]}
        reason = assert_guard_rejects(tampered, where="练习响应副本（data.steps[0].answer）")
        self.assertIn("answer", reason)


class TestTopLevelOnlyIsNotEnough(unittest.TestCase):
    """契约原话「**只查顶层是不够的**」的正对照：证明只查一层的实现会放行嵌套越权。"""

    NESTED_LEAK = {
        "paper_id": 1,
        "title": "t",
        "total": 1,
        "items": [{"seq": 1, "display": "12+7", "question_type": "oral"}],
        "meta": {"questions": [{"seq": 1, "display": "12+7", "question_type": "oral", "answer": "19"}]},
    }

    def test_naive_top_level_checker_passes_the_nested_leak(self) -> None:
        self.assertEqual(naive_top_level_only_check(self.NESTED_LEAK), [], "坏检查器应当放行")

    def test_recursive_guard_rejects_the_same_body(self) -> None:
        reason = assert_guard_rejects(self.NESTED_LEAK, where="嵌套泄漏（meta.questions[0].answer）")
        self.assertIn("answer", reason)
        self.assertIn("meta", reason)
        record("nested_leak_contrast", naive_result="放行", recursive_result=reason)


class TestSuiteIndependence(unittest.TestCase):
    """任务书第 3 条：不得 import `backend/tests/api/**`（用 AST 静态检查守住）。"""

    FORBIDDEN_MODULES = ("tests.api", "tests.api._student_support", "app.tests")

    def test_no_import_of_the_api_test_package(self) -> None:
        sources = sorted(HERE.glob("*.py"))
        self.assertTrue(sources, "至少要有本包的源文件")
        offenders: list[str] = []
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for module in self.FORBIDDEN_MODULES:
                if re.search(rf"^\s*(from|import)\s+{re.escape(module)}\b", text, re.M):
                    offenders.append(f"{path.name} → {module}")
        self.assertEqual(offenders, [], f"不得 import 既有 api 测试包：{offenders}")
        record("suite_independence", scanned=[p.name for p in sources], offenders=offenders)


def tearDownModule() -> None:
    path = dump_evidence(ARTIFACTS / "evidence-tests.json")
    print(f"\n[task-25] 证据落盘：{path}")
