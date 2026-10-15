"""`POST /api/attempts`：判分 / 两种模式 / 入参校验 / D76 幂等（task-24）。

判分**不由本层决定**：这里断言的是「接口确实把服务层的判分结果如实返回」，
其中「只经判分归一化才可能判对」的用例（全角、空格）才是「真的调了
`app.grading.grade()`，而不是在本层自己比字符串」的证据。
"""
from __future__ import annotations

from typing import NamedTuple
from unittest import mock

from sqlalchemy import text

from app.services import attempt_service

from tests.api._support import url_of
from tests.api._student_support import (
    ATTEMPT_ITEM_KEYS,
    ATTEMPT_TOP_KEYS,
    StudentApiTestCase,
    assert_keys_within,
    new_uuid,
)

from tests.api.test_student_api import patch_generate_boom


class PaperModeTests(StudentApiTestCase):
    """老师发的卷：按 `paper_item.payload` 快照判分（D27）。"""

    def test_all_correct_scores_full(self):
        paper_id, _code, detail = self.published_paper()
        student_id = self.student_id()
        response = self.submit(
            student_id=student_id,
            paper_id=paper_id,
            answers=self.answers_of(detail),
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], len(detail["items"]))
        self.assertEqual(payload["correct"], payload["total"])
        for item in payload["items"]:
            self.assertTrue(item["is_correct"], item)
            self.assertEqual(item["student_answer"], item["answer"])

    def test_one_wrong_answer_is_counted_and_marked(self):
        paper_id, _code, detail = self.published_paper()
        answers = self.answers_of(detail)
        answers[0]["answer"] = "不是数字"
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=answers)
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["correct"], payload["total"] - 1)
        self.assertFalse(payload["items"][0]["is_correct"])
        self.assertEqual(payload["items"][0]["student_answer"], "不是数字")
        self.assertTrue(all(item["is_correct"] for item in payload["items"][1:]))

    def test_omitted_answers_are_all_wrong(self):
        paper_id, _code, detail = self.published_paper()
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=[])
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], len(detail["items"]))
        self.assertEqual(payload["correct"], 0)
        for item in payload["items"]:
            self.assertFalse(item["is_correct"])
            self.assertIsNone(item["student_answer"], "没作答 → student_answer 为 null")

    def test_explicit_null_answer_is_wrong_not_an_error(self):
        paper_id, _code, detail = self.published_paper()
        answers = self.answers_of(detail)
        answers[0]["answer"] = None
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=answers)
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["correct"], payload["total"] - 1)
        self.assertIsNone(payload["items"][0]["student_answer"])

    def test_partial_answer_list_is_allowed(self):
        paper_id, _code, detail = self.published_paper()
        response = self.submit(
            student_id=self.student_id(),
            paper_id=paper_id,
            answers=self.answers_of(detail)[:2],
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["correct"], 2)

    def test_fullwidth_digits_and_spaces_are_graded_correct(self):
        """只有走判分归一化才可能判对——证明本层没有自己比较字符串。"""
        paper_id, _code, detail = self.published_paper()
        answers = self.answers_of(detail)
        answers[0]["answer"] = _to_fullwidth(answers[0]["answer"])
        answers[1]["answer"] = f"  {answers[1]['answer']} "
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=answers)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["correct"], len(detail["items"]))

    def test_naive_string_comparison_would_score_the_same_answer_wrong(self):
        """本项目最关键的判分负对照（V9）：把判分换成朴素字符串比较，全角那条必须**变红**。

        上面 `test_fullwidth_digits_and_spaces_are_graded_correct` 绿，有两种可能：
        真的走了 `app.grading.grade` 的归一化，或者试卷里恰好没有全角/空格。
        这里把判分换成「strip 后逐字比较」，要求同一份作答**判错**——
        于是那条用例的绿就有了辨别力（它确实在测「判分被调用了」，不是巧合）。
        """
        with mock.patch.object(attempt_service, "grade", _naive_grade):
            paper_id, _code, detail = self.published_paper()
            answers = self.answers_of(detail)
            answers[0]["answer"] = _to_fullwidth(answers[0]["answer"])
            response = self.submit(
                student_id=self.student_id(), paper_id=paper_id, answers=answers
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            response.json()["correct"],
            len(detail["items"]) - 1,
            "朴素比较居然也判对了全角作答——那条判分用例就没有辨别力了",
        )

    def test_student_answer_is_recorded_verbatim(self):
        paper_id, _code, detail = self.published_paper()
        answers = self.answers_of(detail)
        answers[0]["answer"] = "  007  "
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=answers)
        self.assertEqual(response.json()["items"][0]["student_answer"], "  007  ")

    def test_response_shape_is_exactly_the_contract_shape(self):
        paper_id, _code, detail = self.published_paper()
        payload = self.submit(
            student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail)
        ).json()
        assert_keys_within(self, payload, ATTEMPT_TOP_KEYS, "响应顶层")
        for index, item in enumerate(payload["items"]):
            assert_keys_within(self, item, ATTEMPT_ITEM_KEYS, f"items[{index}]")
            self.assertNotIn("steps", item, "FR-S8 只要求得分与对错，steps 不在契约里")

    def test_attempt_records_the_paper_link_and_duration(self):
        paper_id, _code, detail = self.published_paper()
        student_id = self.student_id()
        self.submit(
            student_id=student_id,
            paper_id=paper_id,
            answers=self.answers_of(detail),
            duration_ms=4321,
        )
        with self.session() as db:
            row = db.execute(
                text("select student_id, paper_id, duration_ms, total from attempt")
            ).one()
        self.assertEqual(tuple(row), (student_id, paper_id, 4321, len(detail["items"])))

    def test_items_are_stored_as_snapshots(self):
        paper_id, _code, detail = self.published_paper()
        self.submit(
            student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail)
        )
        with self.session() as db:
            payloads = [row[0] for row in db.execute(text("select payload from attempt_item"))]
        self.assertEqual(len(payloads), len(detail["items"]))
        for raw in payloads:
            self.assertIn('"answer"', raw, "快照里必须有答案，否则老师端成绩明细会缺答案")

    def test_unknown_paper_is_404(self):
        response = self.submit(student_id=self.student_id(), paper_id=999, answers=[])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_draft_paper_is_409_not_published(self):
        paper = self.create_paper()
        response = self.submit(student_id=self.student_id(), paper_id=paper["id"], answers=[])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_PUBLISHED")
        with self.session() as db:
            self.assertEqual(db.execute(text("select count(*) from attempt")).scalar(), 0)

    def test_published_paper_keeps_working_after_the_engine_changes(self):
        """D27：判分读快照，引擎挂了也照判——否则引擎升级会让老卷判分与卷面不符。"""
        paper_id, _code, detail = self.published_paper()
        with patch_generate_boom():
            response = self.submit(
                student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail)
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["correct"], len(detail["items"]))

    def test_editing_the_paper_after_publish_is_not_possible_so_snapshot_cannot_drift(self):
        """发布后不可编辑（D32）→ 学生看到的题干与判分用的快照必然同源。"""
        paper_id, _code, detail = self.published_paper()
        attempt = self.submit(
            student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail)
        )
        self.assertEqual(attempt.status_code, 201, attempt.text)
        # 发布卷上任何编辑都是 409（回归护栏，不是本任务的功能）
        response = self.client.delete(url_of("delete_item", paper_id=paper_id, seq=1))
        self.assertEqual(response.status_code, 409, response.text)


class PracticeModeTests(StudentApiTestCase):
    """自主练习：给 `config` + `seed`，服务端**重新生成一遍**再判分（D75）。"""

    def _oracle_answers(self, seed: int, config: dict | None = None) -> list[dict]:
        """用老师端**同配置同 seed** 的卷当答案源（`generate` 确定性 ⇒ 同一套题）。"""
        paper = self.create_paper(config=config, seed=seed)
        return self.answers_of(self.detail(paper["id"]))

    def test_practice_answers_are_graded_against_the_regenerated_set(self):
        payload = self.practice(seed=2025).json()
        self.assertEqual(len(payload["items"]), 6)
        response = self.submit(
            student_id=self.student_id(),
            config=payload_config(),
            seed=2025,
            answers=self._oracle_answers(2025),
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["total"], 6)
        self.assertEqual(body["correct"], 6, body)
        self.assertIsNone(
            # 练习模式没有卷：paper_id 必须是 null，否则成绩单会挂到错误的卷上
            self._attempt_row()["paper_id"]
        )

    def test_practice_with_wrong_answers_scores_zero(self):
        response = self.submit(
            student_id=self.student_id(),
            config=payload_config(),
            seed=2025,
            answers=[{"seq": seq, "answer": "不是数字"} for seq in range(1, 7)],
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["correct"], 0)

    def test_practice_mode_needs_no_paper_row(self):
        response = self.submit(
            student_id=self.student_id(),
            config=payload_config(),
            seed=1,
            answers=[],
        )
        self.assertEqual(response.status_code, 201, response.text)
        with self.session() as db:
            self.assertEqual(db.execute(text("select count(*) from paper")).scalar(), 0)

    def test_config_without_seed_is_422(self):
        response = self.submit(student_id=self.student_id(), config=payload_config(), answers=[])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_seed_without_config_is_422(self):
        response = self.submit(student_id=self.student_id(), seed=2025, answers=[])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_both_modes_at_once_is_422(self):
        paper_id, _code, _detail = self.published_paper()
        response = self.submit(
            student_id=self.student_id(),
            paper_id=paper_id,
            config=payload_config(),
            seed=2025,
            answers=[],
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_neither_mode_is_422(self):
        response = self.submit(student_id=self.student_id(), answers=[])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("paper_id", response.json()["error"]["message"])

    def _attempt_row(self) -> dict:
        with self.session() as db:
            row = db.execute(
                text("select paper_id, total, correct, config_json from attempt")
            ).mappings().one()
        return dict(row)


class ValidationTests(StudentApiTestCase):
    """入参校验：报错好过静默错（D65）。"""

    def test_unknown_student_is_422(self):
        paper_id, _code, detail = self.published_paper()
        response = self.submit(
            student_id=999, paper_id=paper_id, answers=self.answers_of(detail)
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("报到", response.json()["error"]["message"])

    def test_seq_out_of_range_is_422(self):
        paper_id, _code, detail = self.published_paper()
        answers = self.answers_of(detail) + [{"seq": len(detail["items"]) + 1, "answer": "1"}]
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=answers)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("超出", response.json()["error"]["message"])

    def test_zero_seq_is_422_from_the_request_model(self):
        response = self.submit(
            student_id=self.student_id(),
            paper_id=1,
            answers=[{"seq": 0, "answer": "1"}],
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_duplicate_seq_is_422(self):
        paper_id, _code, detail = self.published_paper()
        answers = self.answers_of(detail)
        answers.append(dict(answers[0]))
        response = self.submit(student_id=self.student_id(), paper_id=paper_id, answers=answers)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("重复", response.json()["error"]["message"])

    def test_missing_attempt_uuid_is_422(self):
        paper_id, _code, detail = self.published_paper()
        response = self.client.post(
            url_of("submit_attempt"),
            json={
                "student_id": self.student_id(),
                "paper_id": paper_id,
                "answers": [],
                "duration_ms": 1,
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_over_long_attempt_uuid_is_422(self):
        response = self.submit(
            student_id=self.student_id(),
            attempt_uuid="u" * 65,
            paper_id=1,
            answers=[],
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_missing_duration_is_422(self):
        response = self.client.post(
            url_of("submit_attempt"),
            json={
                "student_id": self.student_id(),
                "attempt_uuid": new_uuid(),
                "paper_id": 1,
                "answers": [],
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_negative_duration_is_422(self):
        response = self.submit(
            student_id=self.student_id(), paper_id=1, answers=[], duration_ms=-1
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_failed_submission_leaves_no_row_behind(self):
        """校验失败发生在写库之前（或异常路径 rollback 干净）——不留下半截记录。"""
        paper_id, _code, detail = self.published_paper()
        bad = self.answers_of(detail) + [{"seq": 999, "answer": "1"}]
        self.assertEqual(
            self.submit(student_id=self.student_id(), paper_id=paper_id, answers=bad).status_code,
            422,
        )
        with self.session() as db:
            attempts = db.execute(text("select count(*) from attempt")).scalar()
            items = db.execute(text("select count(*) from attempt_item")).scalar()
        self.assertEqual((attempts, items), (0, 0))


class IdempotencyTests(StudentApiTestCase):
    """D76：按 `(student_id, attempt_uuid)` 去重；重放返回首次结果（200）。"""

    def _submit_once(self, **body) -> tuple[object, dict]:
        paper_id, _code, detail = self.published_paper()
        student_id = self.student_id()
        body.setdefault("paper_id", paper_id)
        body.setdefault("answers", self.answers_of(detail))
        response = self.submit(student_id=student_id, **body)
        return response, {"paper_id": paper_id, "student_id": student_id, "detail": detail}

    def test_replay_returns_200_with_the_first_result(self):
        first, info = self._submit_once(attempt_uuid="fixed-uuid")
        second = self.submit(
            student_id=info["student_id"],
            attempt_uuid="fixed-uuid",
            paper_id=info["paper_id"],
            answers=self.answers_of(info["detail"]),
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())

    def test_replay_body_is_byte_identical(self):
        first, info = self._submit_once(attempt_uuid="fixed-uuid")
        second = self.submit(
            student_id=info["student_id"],
            attempt_uuid="fixed-uuid",
            paper_id=info["paper_id"],
            answers=self.answers_of(info["detail"]),
        )
        self.assertEqual(first.content, second.content, "D76：两次响应体逐字相同")

    def test_replay_does_not_add_a_second_row(self):
        first, info = self._submit_once(attempt_uuid="fixed-uuid")
        self.submit(
            student_id=info["student_id"],
            attempt_uuid="fixed-uuid",
            paper_id=info["paper_id"],
            answers=self.answers_of(info["detail"]),
        )
        with self.session() as db:
            attempts = db.execute(text("select count(*) from attempt")).scalar()
            items = db.execute(text("select count(*) from attempt_item")).scalar()
        self.assertEqual(attempts, 1, "同 uuid 提交两次只记一条成绩（E7）")
        self.assertEqual(items, len(info["detail"]["items"]))

    def test_replay_does_not_overwrite_the_first_result(self):
        """重放时带着**不同的作答**也必须返回首次那一份（不改写）。"""
        first, info = self._submit_once(attempt_uuid="fixed-uuid")
        second = self.submit(
            student_id=info["student_id"],
            attempt_uuid="fixed-uuid",
            paper_id=info["paper_id"],
            answers=[],
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(second.json()["correct"], first.json()["correct"])
        with self.session() as db:
            answers = [row[0] for row in db.execute(text("select student_answer from attempt_item"))]
        self.assertNotIn(None, answers, "重放不该把首次的作答改写成未作答")

    def test_replay_short_circuits_before_regenerating_in_practice_mode(self):
        """重放路径在读库之后就返回：连 `generate` 都不该被调用（引擎挂了也照样答）。"""
        answers = [{"seq": seq, "answer": "不是数字"} for seq in range(1, 7)]
        first = self.submit(
            student_id=self.student_id(),
            attempt_uuid="practice-uuid",
            config=payload_config(),
            seed=2025,
            answers=answers,
        )
        self.assertEqual(first.status_code, 201, first.text)
        with patch_generate_boom():
            second = self.submit(
                student_id=self.student_id(),
                attempt_uuid="practice-uuid",
                config=payload_config(),
                seed=2025,
                answers=answers,
            )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.content, first.content)

    def test_same_uuid_for_a_different_student_is_a_separate_attempt(self):
        paper_id, _code, detail = self.published_paper()
        first = self.submit(
            student_id=self.student_id(name="张三", student_no="1"),
            attempt_uuid="shared-uuid",
            paper_id=paper_id,
            answers=self.answers_of(detail),
        )
        second = self.submit(
            student_id=self.student_id(name="李四", student_no="2"),
            attempt_uuid="shared-uuid",
            paper_id=paper_id,
            answers=[],
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["correct"], 0)
        self.assertNotEqual(first.json()["attempt_id"], second.json()["attempt_id"])
        with self.session() as db:
            self.assertEqual(db.execute(text("select count(*) from attempt")).scalar(), 2)

    def test_different_uuid_is_a_new_attempt(self):
        paper_id, _code, detail = self.published_paper()
        student_id = self.student_id()
        first = self.submit(
            student_id=student_id,
            attempt_uuid="uuid-a",
            paper_id=paper_id,
            answers=self.answers_of(detail),
        )
        second = self.submit(
            student_id=student_id,
            attempt_uuid="uuid-b",
            paper_id=paper_id,
            answers=[],
        )
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.json()["attempt_id"], second.json()["attempt_id"])


# ---- 小工具 ---------------------------------------------------------------

def payload_config() -> dict:
    """自主练习/练习判分用的出题参数（与 `practice` 助手默认值同源）。"""
    from tests.api._support import CONFIG_ADD_SUB

    return dict(CONFIG_ADD_SUB)


def _to_fullwidth(text: str) -> str:
    return "".join(chr(ord(ch) + 0xFEE0) if "0" <= ch <= "9" else ch for ch in text)


class _Verdict(NamedTuple):
    """`app.grading.grade` 的返回形状（负对照里替代真判分）。"""

    is_correct: bool
    normalized_input: str


def _naive_grade(question, raw_answer: str) -> _Verdict:
    """朴素比较器：不归一化，`strip` 后逐字比——全角/空格作答必然判错。"""
    return _Verdict(str(raw_answer).strip() == question.answer, str(raw_answer))
