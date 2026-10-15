"""`GET /api/attempts`（成绩列表）与 `GET /api/attempts/{attempt_id}`（明细）——M5 / D80。

三条判据是这个模块的重心，写之前先想清楚每一条**要区分的两种状态**（V11）：

1. **`total` 是筛选后的总数，不是本页条数**：`limit=1` 时 `total` 必须还是 3。
   若把 `total` 写成 `len(page)`，界面上的「共 N 条」就会随每页条数变。
2. **同一时刻的两条记录必须能稳定翻页**：只按 `submitted_at` 排序时，同一秒内的
   记录在不同页请求里可能互换位置 —— 学生名单会**又漏又重**。所以排序是
   `submitted_at` 倒序、同刻按 `id` 倒序（契约 `AttemptListOut` 的原话）。
3. **`total == len(items)`**：契约把这条写成「由判据守着」。它由**写路径**保证
   （判分时每题写一条 `attempt_item`，与 `attempt` 同一事务），读路径**故意不修**它：
   明细真被截断时接口要如实报出来。本模块最后一块负对照（留在仓库里）用**同一个检查器**
   证明它会在截断的记录上变红。
"""
from __future__ import annotations

from sqlalchemy import text

from app.contracts.api import score_of

from tests.api._support import CONFIG_ADD_SUB, url_of
from tests.api._student_support import ATTEMPT_ITEM_KEYS, StudentApiTestCase, new_uuid

#: 成绩行的顶层键（`AttemptSummary`，**刻意硬编码的第二事实来源**：契约加字段要改这里，
#: 改它的动作本身就是「我在改契约」的声明）。
ATTEMPT_SUMMARY_KEYS = frozenset({
    "id", "student_id", "student_name", "student_no",
    "paper_id", "paper_code", "paper_title",
    "total", "correct", "score", "duration_ms", "submitted_at",
})

#: 列表响应的顶层键（`AttemptListOut`）。
ATTEMPT_LIST_TOP_KEYS = frozenset({"total", "limit", "offset", "attempts"})

#: 默认页大小（契约：1..100，默认 20）。界面第一页的条数靠它，所以钉住。
DEFAULT_LIMIT = 20

#: 一份必然判错的作答（判分归一化后解析不出数值）。
WRONG = "不是数字"


def assert_total_matches_items(tc, detail: dict, where: str = "成绩明细") -> None:
    """契约 `AttemptDetailOut` 的那条等式：`total == len(items)`。

    这是**生产的判据**，正例与负对照用的是同一个函数 —— 负对照证明它真的会红，
    正例才不是在空过。
    """
    tc.assertEqual(
        len(detail["items"]),
        detail["total"],
        f"{where}：total={detail['total']} 而逐题只有 {len(detail['items'])} 条 —— "
        f"分数与明细对不上（明细表被截断？老库迁移？），成绩不可信",
    )


class AttemptReadCase(StudentApiTestCase):
    """成绩读取用例的共用动作：造学生、造卷、造作答。"""

    def student_pair(self) -> tuple[int, int]:
        """两个学生（张三 / 李四），返回 `(张三 id, 李四 id)`。"""
        return (
            self.student_id("张三", "20240101"),
            self.student_id("李四", "20240102"),
        )

    def attempt_on_paper(
        self,
        student_id: int,
        paper_id: int,
        detail: dict,
        *,
        correct_count: int | None = None,
    ) -> int:
        """在老师发的卷上提交一次作答，返回 `attempt_id`。

        `correct_count=None` → 全对；给整数 → 前 N 题对、其余必错。
        """
        answers = []
        for index, item in enumerate(detail["items"]):
            if correct_count is None or index < correct_count:
                answers.append({"seq": item["seq"], "answer": item["answer"]})
            else:
                answers.append({"seq": item["seq"], "answer": WRONG})
        response = self.submit(
            student_id=student_id, paper_id=paper_id, answers=answers, attempt_uuid=new_uuid()
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["attempt_id"]

    def practice_attempt(self, student_id: int) -> int:
        """自主练习（D75：没有卷）提交一次（**全错**），返回 `attempt_id`。"""
        config = dict(CONFIG_ADD_SUB)
        practice = self.practice(config)
        self.assertEqual(practice.status_code, 200, practice.text)
        seed = practice.json()["seed"]
        total = practice.json()["total"]
        answers = [{"seq": seq, "answer": WRONG} for seq in range(1, total + 1)]
        response = self.submit(
            student_id=student_id,
            config=config,
            seed=seed,
            answers=answers,
            attempt_uuid=new_uuid(),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["attempt_id"]

    # ---- 读取 ----------------------------------------------------------

    def listing(self, **params) -> dict:
        response = self.client.get(url_of("list_attempts"), params=params or None)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def detail_of(self, attempt_id: int) -> dict:
        response = self.client.get(url_of("get_attempt", attempt_id=attempt_id))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class AttemptListTests(AttemptReadCase):
    """列表：分页 / 筛选 / 搜索 / 排序稳定性。"""

    def setUp(self) -> None:
        super().setUp()
        self.paper_id, _code, self.paper_detail = self.published_paper()
        self.zhang, self.li = self.student_pair()

    def test_empty_database_returns_an_empty_first_page(self):
        body = self.listing()
        self.assertEqual(
            body,
            {"total": 0, "limit": DEFAULT_LIMIT, "offset": 0, "attempts": []},
            "空库也要给出完整的分页信封，界面才能直接渲染",
        )

    def test_every_row_has_exactly_the_contract_keys(self):
        self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail)
        body = self.listing()
        self.assertEqual(set(body), ATTEMPT_LIST_TOP_KEYS, body)
        for row in body["attempts"]:
            self.assertEqual(set(row), ATTEMPT_SUMMARY_KEYS, row)

    def test_lists_newest_first(self):
        ids = [
            self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail),
            self.attempt_on_paper(self.li, self.paper_id, self.paper_detail),
            self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail),
        ]
        body = self.listing()
        self.assertEqual(body["total"], 3)
        self.assertEqual(
            [row["id"] for row in body["attempts"]],
            list(reversed(ids)),
            "新的必须排在最前面（submitted_at 倒序）",
        )

    def test_total_is_the_filtered_count_not_the_page_size(self):
        ids = [
            self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail),
            self.attempt_on_paper(self.li, self.paper_id, self.paper_detail),
            self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail),
        ]
        first = self.listing(limit=2, offset=0)
        second = self.listing(limit=2, offset=2)

        for page in (first, second):
            self.assertEqual(page["total"], 3, "total 是筛选后的总数，与 limit/offset 无关")
            self.assertEqual(page["attempts"], page["attempts"][: page["limit"]])
        self.assertEqual([row["id"] for row in first["attempts"]], list(reversed(ids))[:2])
        self.assertEqual([row["id"] for row in second["attempts"]], list(reversed(ids))[2:])

        seen = [row["id"] for row in first["attempts"] + second["attempts"]]
        self.assertEqual(len(seen), len(set(seen)), "翻页不能重复")
        self.assertEqual(set(seen), set(ids), "翻页不能漏")

    def test_paging_is_stable_when_timestamps_tie(self):
        """同一时刻的多条记录必须按 `id` 倒序 —— 否则翻页会又漏又重。"""
        ids = [
            self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail),
            self.attempt_on_paper(self.li, self.paper_id, self.paper_detail),
            self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail),
        ]
        with self.session() as db:
            db.execute(text("update attempt set submitted_at = '2026-01-01T00:00:00'"))
            db.commit()

        paged = []
        for offset in range(len(ids)):
            page = self.listing(limit=1, offset=offset)
            self.assertEqual(page["total"], 3)
            paged.append(page["attempts"][0]["id"])

        self.assertEqual(len(set(paged)), 3, f"同一时刻翻页出现重复：{paged}")
        self.assertEqual(paged, sorted(ids, reverse=True), "同一时刻必须按 id 倒序，翻页才稳定")

    def test_filter_by_paper_id(self):
        other_paper, _code, other_detail = self.published_paper()
        mine = self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail)
        self.attempt_on_paper(self.li, other_paper, other_detail)
        self.practice_attempt(self.zhang)

        body = self.listing(paper_id=self.paper_id)
        self.assertEqual(body["total"], 1, "只看这套卷的成绩")
        self.assertEqual([row["id"] for row in body["attempts"]], [mine])
        self.assertEqual(body["attempts"][0]["paper_id"], self.paper_id)

        self.assertEqual(self.listing(paper_id=other_paper)["total"], 1)
        self.assertEqual(
            self.listing(paper_id=999999)["total"], 0, "没有成绩的卷返回空页而不是 404"
        )

    def test_search_matches_the_student_name(self):
        self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail)
        self.attempt_on_paper(self.li, self.paper_id, self.paper_detail)
        third = self.student_id("张小明", "20240103")
        self.attempt_on_paper(third, self.paper_id, self.paper_detail)

        body = self.listing(q="张")
        self.assertEqual(body["total"], 2, body["attempts"])
        self.assertEqual({row["student_name"] for row in body["attempts"]}, {"张三", "张小明"})

    def test_search_matches_the_student_no(self):
        self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail)
        self.attempt_on_paper(self.li, self.paper_id, self.paper_detail)

        body = self.listing(q="240102")
        self.assertEqual(body["total"], 1, body["attempts"])
        self.assertEqual(body["attempts"][0]["student_no"], "20240102")

    def test_search_combines_with_the_paper_filter(self):
        other_paper, _code, other_detail = self.published_paper()
        self.attempt_on_paper(self.li, self.paper_id, self.paper_detail)
        self.attempt_on_paper(self.li, other_paper, other_detail)

        body = self.listing(paper_id=other_paper, q="李")
        self.assertEqual(body["total"], 1, body["attempts"])
        self.assertEqual(body["attempts"][0]["paper_id"], other_paper)

    def test_search_wildcards_are_literal(self):
        """`%` / `_` 是**字符**，不是通配符：搜 `%` 不该匹配所有人。"""
        self.attempt_on_paper(self.zhang, self.paper_id, self.paper_detail)
        self.attempt_on_paper(self.li, self.paper_id, self.paper_detail)

        self.assertEqual(self.listing(q="%")["total"], 0, "LIKE 通配符没被转义")
        self.assertEqual(self.listing(q="_")["total"], 0, "LIKE 单字通配符没被转义")
        self.assertEqual(self.listing(q="")["total"], 2, "空 q = 不筛选（清空搜索框的常用请求）")
        self.assertEqual(self.listing(q="   ")["total"], 2, "纯空白 q 同样当作不筛选")

    def test_practice_rows_have_no_paper(self):
        attempt_id = self.practice_attempt(self.zhang)
        row = self.listing()["attempts"][0]
        self.assertEqual(row["id"], attempt_id)
        self.assertIsNone(row["paper_id"], "自主练习没有卷")
        self.assertIsNone(row["paper_code"])
        self.assertIsNone(row["paper_title"], "接口只说「有没有挂卷」，显示文案属于界面层")
        self.assertEqual(self.listing(paper_id=self.paper_id)["total"], 0, "练习不该被卷过滤命中")

    def test_invalid_paging_and_filters_are_422(self):
        for params in (
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"paper_id": 0},
            {"q": "字" * 201},
        ):
            with self.subTest(params=params):
                response = self.client.get(url_of("list_attempts"), params=params)
                self.assertEqual(response.status_code, 422, f"{params} → {response.text}")
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


class AttemptDetailTests(AttemptReadCase):
    """明细：逐题顺序 / 与列表一致 / 404 / 得分口径。"""

    def setUp(self) -> None:
        super().setUp()
        self.paper_id, _code, self.paper_detail = self.published_paper()
        self.student_id_value = self.student_id("张三", "20240101")

    def test_items_are_in_seq_order_and_total_matches(self):
        attempt_id = self.attempt_on_paper(self.student_id_value, self.paper_id, self.paper_detail)
        detail = self.detail_of(attempt_id)

        self.assertEqual(detail["total"], len(self.paper_detail["items"]))
        self.assertEqual(
            [item["seq"] for item in detail["items"]],
            list(range(1, detail["total"] + 1)),
            "明细必须按 seq 升序（与卷面顺序一致）",
        )
        for item in detail["items"]:
            self.assertEqual(set(item), ATTEMPT_ITEM_KEYS, item)
        assert_total_matches_items(self, detail)

    def test_detail_agrees_with_the_list_row(self):
        attempt_id = self.attempt_on_paper(
            self.student_id_value, self.paper_id, self.paper_detail, correct_count=3
        )
        row = next(r for r in self.listing()["attempts"] if r["id"] == attempt_id)
        detail = self.detail_of(attempt_id)

        self.assertEqual(set(detail), ATTEMPT_SUMMARY_KEYS | {"items"})
        for key in ATTEMPT_SUMMARY_KEYS:
            self.assertEqual(detail[key], row[key], f"{key} 在列表与明细里不一致")

    def test_missing_attempt_is_404_attempt_not_found(self):
        response = self.client.get(url_of("get_attempt", attempt_id=999999))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "ATTEMPT_NOT_FOUND")
        self.assertIn("999999", response.json()["error"]["message"])

    def test_score_comes_from_the_contract_helper(self):
        """得分**唯一**来自冻结包 `score_of`（D80），服务层不再写第二份算法。

        这里既跟 `score_of` 对账，也钉住三个手算过的锚点（6 题：全对 100、全错 0、
        对 3 题 50）—— 只跟 `score_of` 对账的话，两边一起漂也没人拦得住。
        """
        all_correct = self.attempt_on_paper(self.student_id_value, self.paper_id, self.paper_detail)
        self.assertEqual(self.detail_of(all_correct)["score"], 100)

        third = self.student_id("李四", "20240102")
        half = self.attempt_on_paper(third, self.paper_id, self.paper_detail, correct_count=3)
        self.assertEqual(self.detail_of(half)["total"], 6)
        self.assertEqual(self.detail_of(half)["correct"], 3)
        self.assertEqual(self.detail_of(half)["score"], 50)

        fourth = self.student_id("王五", "20240103")
        none = self.attempt_on_paper(fourth, self.paper_id, self.paper_detail, correct_count=0)
        self.assertEqual(self.detail_of(none)["score"], 0)

        for attempt_id in (all_correct, half, none):
            detail = self.detail_of(attempt_id)
            self.assertEqual(
                detail["score"],
                score_of(detail["total"], detail["correct"]),
                "响应里的 score 必须等于冻结包算出来的值",
            )

    def test_submit_response_carries_the_same_score(self):
        """提交响应里的 `score` 与明细页一致 —— 学生交完卷当场看到的分数，不能跟老师看到的不一样。"""
        answers = [
            {"seq": item["seq"], "answer": item["answer"] if index < 3 else WRONG}
            for index, item in enumerate(self.paper_detail["items"])
        ]
        response = self.submit(
            student_id=self.student_id_value,
            paper_id=self.paper_id,
            answers=answers,
            attempt_uuid=new_uuid(),
        )
        self.assertEqual(response.status_code, 201, response.text)
        submitted = response.json()
        detail = self.detail_of(submitted["attempt_id"])

        self.assertEqual(submitted["correct"], 3)
        self.assertEqual(submitted["score"], 50)
        self.assertEqual(submitted["score"], detail["score"])
        self.assertEqual(detail["score"], score_of(detail["total"], detail["correct"]))

    def test_practice_detail_has_no_paper_fields(self):
        attempt_id = self.practice_attempt(self.student_id_value)
        detail = self.detail_of(attempt_id)
        self.assertIsNone(detail["paper_id"])
        self.assertIsNone(detail["paper_code"])
        self.assertIsNone(detail["paper_title"])
        assert_total_matches_items(self, detail)


class TotalMatchesItemsNegativeControls(AttemptReadCase):
    """V11 负对照（**留在仓库里**，谁都能重跑）：`total == len(items)` 的检查器真的会红。

    这条等式由**写路径**保证，读路径故意不拿 `len(items)` 去修 `total`
    （修了就永远绿，明细被截断时接口会安静地说谎）。所以只能这样证明它：
    把写好的一条记录**真的弄坏**，再用**同一个检查器**跑一遍，看它是不是红、
    而且报出两个数。
    """

    def setUp(self) -> None:
        super().setUp()
        self.paper_id, _code, self.paper_detail = self.published_paper()
        self.student_id_value = self.student_id("张三", "20240101")

    def test_checker_accepts_a_healthy_attempt(self):
        attempt_id = self.attempt_on_paper(self.student_id_value, self.paper_id, self.paper_detail)
        detail = self.detail_of(attempt_id)
        self.assertEqual(detail["total"], 6)
        assert_total_matches_items(self, detail)  # 必须绿

    def test_checker_goes_red_when_the_item_table_is_truncated(self):
        attempt_id = self.attempt_on_paper(self.student_id_value, self.paper_id, self.paper_detail)
        with self.session() as db:
            db.execute(
                text("delete from attempt_item where attempt_id = :a and seq = 6"),
                {"a": attempt_id},
            )
            db.commit()

        detail = self.detail_of(attempt_id)
        self.assertEqual(detail["total"], 6, "record 里的 total 没变")
        self.assertEqual(len(detail["items"]), 5, "明细真的少了一条")

        with self.assertRaises(AssertionError) as ctx:
            assert_total_matches_items(self, detail)
        message = str(ctx.exception)
        self.assertIn("6", message, f"报错要点名 total：{message}")
        self.assertIn("5", message, f"报错要点名实际条数：{message}")
