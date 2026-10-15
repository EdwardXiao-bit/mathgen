"""D74 白名单守卫的**负对照**（V9）：证明守卫真的会红。

为什么这个文件必须存在：`_student_support.py` 里的守卫（`assert_public_paper` /
`assert_public_practice` / `assert_no_answer_leak`）在主用例里只会「通过」——
而「通过」有两种可能：守卫在正常工作，或者守卫什么都没查。所以这里喂**故意违规**的
数据给它，要求它必须抛 `AssertionError`；再喂干净数据，要求它必须放过（否则守卫只是
恒假的噪声）。最后一组用**真实 HTTP 响应 + 篡改生产投影**，证明守卫确实挂在学生端
响应的出口上，而不是只对本地造的假数据有效。

判据不是「测试绿」，而是「守卫能红 / 能绿」两种状态都被观察到。
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.contracts.api import PaperConfigIn
from app.services import student_service

from tests.api._student_support import (
    ANSWER_KEYS,
    PAPER_TOP_KEYS,
    PRACTICE_TOP_KEYS,
    STUDENT_ITEM_KEYS,
    StudentApiTestCase,
    assert_no_answer_leak,
    assert_public_paper,
    assert_public_practice,
    assert_keys_within,
)
from tests.api._support import CONFIG_ADD_SUB


def _clean_item(seq: int = 1) -> dict:
    return {"seq": seq, "display": "7 + 8", "question_type": "oral"}


def _clean_paper_payload() -> dict:
    return {"paper_id": 1, "title": "口算练习", "total": 1, "items": [_clean_item()]}


def _clean_practice_payload() -> dict:
    return {"seed": 2025, "total": 1, "items": [_clean_item()]}


class GuardCanFailTests(unittest.TestCase):
    """负对照：违规输入必须让守卫抛 `AssertionError`。"""

    def assert_guard_rejects(self, guard, payload, needle: str) -> None:
        with self.assertRaises(AssertionError) as ctx:
            guard(self, payload)
        self.assertIn(needle, str(ctx.exception), f"守卫红了，但理由不对：{ctx.exception}")

    def test_extra_top_level_key_is_rejected(self):
        payload = dict(_clean_paper_payload(), answer="14")
        self.assert_guard_rejects(assert_public_paper, payload, "白名单外的键")

    def test_answer_key_inside_item_is_rejected(self):
        payload = dict(_clean_paper_payload(), items=[dict(_clean_item(), answer="15")])
        self.assert_guard_rejects(assert_public_paper, payload, "items[0]")

    def test_steps_key_inside_item_is_rejected(self):
        payload = dict(_clean_paper_payload(), items=[dict(_clean_item(), steps=["7+8=15"])])
        self.assert_guard_rejects(assert_public_paper, payload, "steps")

    def test_teacher_only_tags_key_inside_item_is_rejected(self):
        payload = dict(_clean_paper_payload(), items=[dict(_clean_item(), tags=["进位"])])
        self.assert_guard_rejects(assert_public_paper, payload, "tags")

    def test_nested_container_inside_item_is_rejected(self):
        """白名单是**平的**：就算键名合法，值里也不许再嵌结构（答案可以藏在里面）。"""
        payload = dict(_clean_paper_payload(), items=[dict(_clean_item(), display={"answer": "15"})])
        self.assert_guard_rejects(assert_public_paper, payload, "嵌套结构")

    def test_deeply_nested_answer_is_rejected_by_the_leak_guard(self):
        """白名单管形状，泄题网管内容——两层深也要抓到。"""
        payload = _clean_paper_payload()
        payload["items"][0]["display"] = {"hints": [{"Answer": "15"}]}
        self.assert_guard_rejects(assert_no_answer_leak, payload, "答案性字段")

    def test_practice_without_seed_is_rejected(self):
        """D75：没有 seed，学生就无法回传，判分链路直接断掉。"""
        payload = _clean_practice_payload()
        del payload["seed"]
        self.assert_guard_rejects(assert_public_practice, payload, "seed")

    def test_practice_with_answer_in_item_is_rejected(self):
        payload = dict(_clean_practice_payload(), items=[dict(_clean_item(), answer="15")])
        self.assert_guard_rejects(assert_public_practice, payload, "answer")


class GuardCanPassTests(unittest.TestCase):
    """正对照：干净输入必须被放过——否则上面那组「必红」可能只是恒假。"""

    def test_clean_paper_payload_passes(self):
        assert_public_paper(self, _clean_paper_payload())
        assert_no_answer_leak(self, _clean_paper_payload())

    def test_clean_practice_payload_passes(self):
        assert_public_practice(self, _clean_practice_payload())

    def test_optional_paper_code_is_allowed(self):
        """§5.2 字段表没列 `paper_code`，但 D74 允许——判据是子集，不是相等。"""
        self.assertIn("paper_code", PAPER_TOP_KEYS)
        assert_public_paper(self, dict(_clean_paper_payload(), paper_code="2610-0001"))

    def test_seed_is_mandatory_in_the_practice_key_set(self):
        self.assertIn("seed", PRACTICE_TOP_KEYS)

    def test_assert_keys_within_reports_every_extra_key(self):
        with self.assertRaises(AssertionError) as ctx:
            assert_keys_within(self, dict(_clean_item(), answer="1", steps=["x"]), STUDENT_ITEM_KEYS, "item")
        message = str(ctx.exception)
        self.assertIn("answer", message)
        self.assertIn("steps", message)


class WhitelistIsIndependentlySpecifiedTests(unittest.TestCase):
    """测试侧白名单是**独立副本**：它要能发现实现悄悄改投影。"""

    def test_production_keys_equal_the_hardcoded_copy(self):
        self.assertEqual(frozenset(student_service.STUDENT_ITEM_KEYS), STUDENT_ITEM_KEYS)

    def test_leak_guard_covers_every_teacher_only_field_the_contract_mentions(self):
        for key in ("answer", "steps", "tags", "difficulty", "signature", "expr"):
            self.assertIn(key, ANSWER_KEYS, f"泄题网漏了 `{key}`")


class ProductionResponseIsGuardedTests(StudentApiTestCase):
    """把守卫接到**真实链路**上：篡改生产投影，守卫必须发现。

    这里发现的一件事值得记下来：D74 其实是**两道网**——
    ① `services/student_service.public_item` 的投影（写少了/写多了都在这里）；
    ② 路由的 `response_model=`（Pydantic 默认忽略多余键，**多写的键到不了客户端**）。
    所以篡改 ① 时 HTTP 响应反而是干净的：多出来的键被 ② 丢掉了。
    这两条性质都写成了用例：一条证明 ① 被守卫抓得住（服务层字典），
    一条证明 ② 真的在过滤（HTTP 响应）。谁哪天把 ② 的 `response_model` 去掉，
    第二条会红——这正是它存在的意义。
    """

    def test_projection_leak_fails_the_guard_at_the_service_boundary(self):
        """最关键的负对照：守卫抓的是**生产代码真的产出的字典**，不是本地假数据。"""
        _paper_id, code, _detail = self.published_paper()
        # 先抓住原函数：patch 之后 `student_service.public_item` 已经是替身，
        # 在替身里再按模块属性调用它就会无限递归（写这条时踩过一次）。
        original = student_service.public_item

        def leaky(seq, display, question_type):
            return dict(original(seq, display, question_type), steps=["先算个位"])

        with mock.patch.object(student_service, "public_item", leaky), self.session() as db:
            leaked = student_service.paper_payload(db, code)
        self.assertIn("steps", leaked["items"][0], "篡改没生效，这条负对照本身就无效")
        with self.assertRaises(AssertionError):
            assert_public_paper(self, leaked)
        with self.assertRaises(AssertionError):
            assert_no_answer_leak(self, leaked)

    def test_practice_projection_leak_fails_the_guard_at_the_service_boundary(self):
        original = student_service.public_item

        def leaky(seq, display, question_type):
            return dict(original(seq, display, question_type), answer="42")

        config = PaperConfigIn(**CONFIG_ADD_SUB)
        with mock.patch.object(student_service, "public_item", leaky):
            leaked = student_service.practice_payload(config, seed=2025)
        self.assertIn("answer", leaked["items"][0])
        with self.assertRaises(AssertionError):
            assert_public_practice(self, leaked)
        with self.assertRaises(AssertionError):
            assert_no_answer_leak(self, leaked)

    def test_response_model_is_the_second_net_that_drops_projected_extras(self):
        """第②道网真的在过滤：篡改投影后 HTTP 响应里**看不到**多出来的键。

        这不是「守卫失效」——守卫守的是服务层产出，而客户端拿到的是 response_model
        序列化后的结果。两处都要有网，这条用例把 ② 钉住。
        """
        _paper_id, code, _detail = self.published_paper()
        original = student_service.public_item

        def leaky(seq, display, question_type):
            return dict(original(seq, display, question_type), steps=["先算个位"])

        with mock.patch.object(student_service, "public_item", leaky):
            payload = self.student_paper(code).json()
        self.assertNotIn("steps", payload["items"][0], "response_model 没有过滤多余键")
        assert_public_paper(self, payload)  # 出口干净，守卫自然通过

    def test_the_real_responses_pass_the_guards_untouched(self):
        """对照组：不改投影时，真实响应必须干净——否则守卫红得没有信息量。"""
        _paper_id, code, _detail = self.published_paper()
        assert_public_paper(self, self.student_paper(code).json())
        assert_no_answer_leak(self, self.student_paper(code).json())
        assert_public_practice(self, self.practice().json())
