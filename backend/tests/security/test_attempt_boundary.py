"""task-25 的**判据边界**：D74 白名单只管「取题」，**不适用于判分结果**。

契约 §5.2 第 253-260 行的白名单管的是**取题**接口——「学生端不能拿到答案」。
而 `POST /api/attempts` 是**判分结果**（FR-S7/S8），按契约它返回的题目对象
**必须**带上正确答案 `answer` 与逐题对错 `is_correct`，否则学生交完卷看不到任何反馈。

危险在于：将来有人图省事，把白名单「统一」应用到所有学生端响应上——
判分功能会被一起打掉，而**当时的判据（白名单全绿）不会有任何一条变红**。
所以这里把边界钉成断言（本文件是这个套件里唯一「期望出现 `answer`」的地方）：

* ``test_01``：判分结果**必须**含 `answer`，且逐题答案与老师端逐字一致；
* ``test_02``：判分结果**不得**含 `explanation`/`steps` 这类过程性字段（连原始 JSON 文本一起扫）；
* ``test_03``：白名单**故意**判它红——这条红是「边界被画在原地」的证据，不是缺陷；
* ``test_04``：自主练习模式（`config` + `seed`）同样必须含答案。

另有一条与 D76 有关的**行为**判据（``test_05``）：重复提交返回 **200 且正常响应体**，
而不是「HTTP 200 + 错误信封」——后者正是被删掉的 `ATTEMPT_DUPLICATE` 的写法。
"""

from __future__ import annotations

import json
import unittest
import uuid

from tests.security._harness import (
    ARTIFACTS,
    CONFIG_ADD_SUB,
    SecurityHarness,
    create_paper,
    dump_evidence,
    publish_paper,
    record,
    report_student,
    submit_attempt,
    teacher_detail,
)
from tests.security._whitelist import WhitelistViolation, assert_student_payload, walk

#: 判分结果的题目对象键集（`AttemptItemOut`，契约 §5.2 字段表）
ATTEMPT_ITEM_KEYS = frozenset(
    {"seq", "display", "question_type", "answer", "student_answer", "is_correct"}
)
#: 判分结果的顶层键集（`AttemptResultOut`）。M5 起多一个 `score`（D80）：
#: 得分改为**后端下发**（`contracts.api.score_of`），学生端结果页不再自己算，
#: 教师端成绩列表与导出也用同一个数——否则同一份数据会有三处口径。
ATTEMPT_TOP_KEYS = frozenset({"attempt_id", "total", "correct", "score", "items"})
#: 判分结果里**不该出现**的过程性/内部字段（学生端拿答案是为了看对错，不是看解法）
FORBIDDEN_PROCESS_KEYS = (
    "explanation",
    "steps",
    "tags",
    "difficulty",
    "signature",
    "origin",
    "expr",
    "payload",
)


class TestAttemptResultBoundary(SecurityHarness):
    """「白名单到此为止」——判分结果必须含答案。"""

    COUNT = 3
    SEED = 20261015

    def _published_with_truth(self) -> tuple[int, str, dict[int, str], int]:
        """建卷 → 发布 → 取老师端真值答案 → 学生报到。返回 (paper_id, code, truth, student_id)。"""
        paper_id = create_paper(self.client, count=self.COUNT, seed=self.SEED)
        code = publish_paper(self.client, paper_id)
        detail = teacher_detail(self.client, paper_id)
        truth = {int(item["seq"]): str(item["answer"]) for item in detail["items"]}
        student_id = report_student(self.client)
        return paper_id, code, truth, student_id

    def _mixed_answers(self, truth: dict[int, str]) -> tuple[list[dict], list[int]]:
        """构造三种作答：第 1 题答对、第 2 题答错、第 3 题**整体省略**（契约：按答错计）。"""
        seqs = sorted(truth)
        answers = [
            {"seq": seqs[0], "answer": truth[seqs[0]]},
            {"seq": seqs[1], "answer": "绝对不对的答案-0xDEAD"},
        ]
        return answers, seqs

    def test_01_grading_result_contains_the_correct_answer(self) -> None:
        paper_id, _code, truth, student_id = self._published_with_truth()
        answers, seqs = self._mixed_answers(truth)
        status, body = submit_attempt(
            self.client,
            student_id=student_id,
            attempt_uuid=uuid.uuid4().hex,
            answers=answers,
            paper_id=paper_id,
        )
        self.assertEqual(status, 201, f"首次提交应 201：{json.dumps(body, ensure_ascii=False)}")
        self.assertEqual(set(body), set(ATTEMPT_TOP_KEYS), "判分结果顶层字段表（契约 §5.2）")
        self.assertEqual(body["total"], self.COUNT)
        items = body["items"]
        self.assertEqual(len(items), self.COUNT)
        by_seq = {int(item["seq"]): item for item in items}
        self.assertEqual(sorted(by_seq), seqs)

        for seq, item in sorted(by_seq.items()):
            with self.subTest(seq=seq):
                self.assertEqual(
                    set(item), set(ATTEMPT_ITEM_KEYS), "判分结果的题目对象必须含 answer（FR-S8）"
                )
                self.assertIsInstance(item["answer"], str)
                self.assertNotEqual(item["answer"].strip(), "", "答案不能是空串")
                self.assertEqual(
                    item["answer"], truth[seq], "判分结果里的答案必须与老师端逐字一致"
                )
                self.assertIsInstance(item["is_correct"], bool)

        # 逐题对错：答对 / 答错 / 未作答（student_answer 为 None 且判错）
        self.assertTrue(by_seq[seqs[0]]["is_correct"])
        self.assertEqual(by_seq[seqs[0]]["student_answer"], truth[seqs[0]])
        self.assertFalse(by_seq[seqs[1]]["is_correct"])
        self.assertEqual(by_seq[seqs[1]]["student_answer"], "绝对不对的答案-0xDEAD")
        self.assertIsNone(by_seq[seqs[2]]["student_answer"])
        self.assertFalse(by_seq[seqs[2]]["is_correct"])
        self.assertEqual(body["correct"], 1)
        self.assertEqual(body["correct"], sum(1 for item in items if item["is_correct"]))

        record(
            "attempt_result_boundary",
            where="POST /api/attempts（判分结果）",
            top_keys=sorted(body),
            item_keys=sorted(set(by_seq[seqs[0]])),
            answers_in_response={str(seq): by_seq[seq]["answer"] for seq in seqs},
            teacher_answers={str(seq): truth[seq] for seq in seqs},
            answers_match_teacher=all(by_seq[seq]["answer"] == truth[seq] for seq in seqs),
            correct=body["correct"],
            result="pass（判分结果含答案，白名单不适用于此）",
        )

    def test_02_grading_result_has_no_process_fields(self) -> None:
        """含答案 ≠ 什么都给：解法/难度/标签/内部 payload 都不该出现在判分结果里。"""
        paper_id, _code, truth, student_id = self._published_with_truth()
        answers, _seqs = self._mixed_answers(truth)
        status, body = submit_attempt(
            self.client,
            student_id=student_id,
            attempt_uuid=uuid.uuid4().hex,
            answers=answers,
            paper_id=paper_id,
        )
        self.assertEqual(status, 201)
        raw = json.dumps(body, ensure_ascii=False)
        offenders = [key for key in FORBIDDEN_PROCESS_KEYS if f'"{key}"' in raw]
        self.assertEqual(offenders, [], f"判分结果里出现了过程性字段：{offenders}")
        record(
            "attempt_result_forbidden_keys",
            where="POST /api/attempts",
            scanned=list(FORBIDDEN_PROCESS_KEYS),
            offenders=offenders,
            result="pass" if not offenders else "fail",
        )

    def test_03_whitelist_is_deliberately_not_applied_here(self) -> None:
        """把边界钉死：白名单套到判分结果上**必须**报错——报错的是判据，不是产品。

        如果哪天有人「统一」了学生端白名单，上面两条会红、这条会绿，
        报告里能立刻看出是**判据越界**而不是产品漏答案。
        """
        paper_id, _code, truth, student_id = self._published_with_truth()
        answers, _seqs = self._mixed_answers(truth)
        status, body = submit_attempt(
            self.client,
            student_id=student_id,
            attempt_uuid=uuid.uuid4().hex,
            answers=answers,
            paper_id=paper_id,
        )
        self.assertEqual(status, 201)

        with self.assertRaises(WhitelistViolation) as ctx:
            assert_student_payload(body, where="POST /api/attempts（判分结果，白名单不适用）")
        reason = str(ctx.exception)
        self.assertIn("answer", reason, "白名单拒绝的理由必须点到 answer")

        # 答案确实深在 `items[].answer`（不是顶层漏看）：证明拒绝原因就是契约要求的键
        answer_paths = [
            path
            for path, node in walk(body)
            if isinstance(node, dict) and "answer" in node and path.startswith("$.items[")
        ]
        self.assertEqual(len(answer_paths), self.COUNT)
        record(
            "attempt_boundary_whitelist_not_applicable",
            where="POST /api/attempts",
            whitelist_verdict="拒绝（预期如此）",
            whitelist_reason=reason,
            answer_paths=answer_paths,
            result="pass（白名单边界画在取题接口，未越界到判分接口）",
        )

    def test_04_practice_mode_grading_result_also_contains_answers(self) -> None:
        """自主练习（`config` + `seed`，D75）走同一条判分路径，同样必须给答案。"""
        _paper_id, _code, _truth, student_id = self._published_with_truth()
        config = {**CONFIG_ADD_SUB, "count": 4}
        status, body = submit_attempt(
            self.client,
            student_id=student_id,
            attempt_uuid=uuid.uuid4().hex,
            answers=[],
            config=config,
            seed=11,
        )
        self.assertEqual(status, 201, f"练习模式提交应 201：{json.dumps(body, ensure_ascii=False)}")
        self.assertEqual(body["total"], 4)
        self.assertEqual(body["correct"], 0, "全未作答必须判错")
        for item in body["items"]:
            with self.subTest(seq=item["seq"]):
                self.assertEqual(set(item), set(ATTEMPT_ITEM_KEYS))
                self.assertNotEqual(str(item["answer"]).strip(), "")
                self.assertIsNone(item["student_answer"])
                self.assertFalse(item["is_correct"])
        record(
            "attempt_practice_mode_boundary",
            where="POST /api/attempts（config+seed）",
            total=body["total"],
            item_keys=sorted(set(body["items"][0])),
            result="pass",
        )

    def test_05_duplicate_submission_replays_with_200_not_an_error_envelope(self) -> None:
        """D76 行为判据：重复提交是 **200 + 正常响应体**，不是「200 + 错误信封」。

        被删掉的 `ATTEMPT_DUPLICATE` 原写作「HTTP 200 + 错误码」，自相矛盾；
        这条用真实 HTTP 把它钉在地上（首次 201 / 重放 200 / 两次响应体逐字相同 /
        响应体里没有 `error` 信封）。
        """
        paper_id, _code, truth, student_id = self._published_with_truth()
        answers, _seqs = self._mixed_answers(truth)
        attempt_uuid = uuid.uuid4().hex

        first_status, first = submit_attempt(
            self.client,
            student_id=student_id,
            attempt_uuid=attempt_uuid,
            answers=answers,
            paper_id=paper_id,
        )
        second_status, second = submit_attempt(
            self.client,
            student_id=student_id,
            attempt_uuid=attempt_uuid,
            answers=answers,
            paper_id=paper_id,
        )
        self.assertEqual(first_status, 201, "首次提交 = 真的新建了记录")
        self.assertEqual(second_status, 200, "重放 = 没有新建记录，状态码必须如实反映")
        self.assertEqual(second, first, "重放必须返回首次那一份结果（逐字相同）")
        self.assertNotIn("error", second, "重复提交不是错误，不能出现错误信封")
        record(
            "attempt_duplicate_replay",
            first_status=first_status,
            second_status=second_status,
            bodies_identical=(second == first),
            error_envelope_present=("error" in second),
            result="pass（重放 200 + 正常响应体）",
        )


def tearDownModule() -> None:
    path = dump_evidence(ARTIFACTS / "evidence-tests.json")
    print(f"\n[task-25] 证据落盘：{path}")
