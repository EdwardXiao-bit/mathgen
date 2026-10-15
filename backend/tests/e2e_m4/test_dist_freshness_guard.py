"""task-43：dist 新鲜度门禁的**负对照**（留仓库，V9.1），并自证它真的制造了失败条件（V9.2）。

背景：`_harness.assert_dist_fresh` 旧签名是 `assert_dist_fresh(case)`，
体内写 `case.assertTrue(fresh, 消息)`，而六个调用点传的是**类**。
`TestCase.assertTrue` 于是被当普通函数调用：`self=fresh`(bool)、`expr=消息`(非空)，
`if not expr:` 恒假 → **真不新鲜也永远不抛**。六个「产物必须比源码新」的守卫一条都没在守。

本模块做两件事：
1. 用真实的 `TestCase` 复刻**修复前的调用形态**，在同一个「陈旧世界」里证明它**不抛**；
2. 在**同一个世界**里证明修复后的无参 `assert_dist_fresh()` **抛且具名两个 mtime**。

两条断言必须成对出现：只证明「新的会抛」不能说明旧的是坏的，
只证明「旧的不抛」也不能说明新的修好了 —— 判别力来自**同一个世界里的对照**。
"""

from __future__ import annotations

import inspect
import json
import os
import unittest
from unittest import mock

from tests.e2e_m4 import _harness as harness

#: 陈旧世界：产物 mtime 1000 < 源码 mtime 2000
STALE = (False, 2000.0, 1000.0)
#: 新鲜世界：产物 mtime 2000 >= 源码 mtime 1000
FRESH = (True, 1000.0, 2000.0)

_EVIDENCE: dict[str, object] = {}
_EVIDENCE_PATH = os.path.join(harness.ARTIFACTS, "task43-guard-evidence.json")


class HistoricalCallSite(unittest.TestCase):
    """复刻修复前的六个调用点所传的那个实参 —— 它是**类**，不是实例。"""


def old_style_assert_dist_fresh(case) -> dict:
    """逐字复刻修复前的 `_harness.py:133-141`（留着，否则「修复前」无法复现）。"""
    fresh, src_m, dist_m = harness.dist_freshness()
    case.assertTrue(
        fresh,
        "前端产物早于源码 → 本套件判据无效。请在 `frontend/**` 写域里"
        f"先跑 `pnpm --dir frontend build`（src={src_m:.0f} dist={dist_m:.0f}）。",
    )
    return {"dist_student_html_mtime": dist_m, "src_newest_mtime": src_m}


def stale_world():
    return mock.patch.object(harness, "dist_freshness", lambda: STALE)


def fresh_world():
    return mock.patch.object(harness, "dist_freshness", lambda: FRESH)


def tearDownModule():  # noqa: N802 - unittest 协议
    with open(_EVIDENCE_PATH, "w", encoding="utf-8") as handle:
        json.dump(_EVIDENCE, handle, ensure_ascii=False, indent=2)


class TestTheOldCallPatternWasVacuous(unittest.TestCase):
    """缺陷本体：修复前的写法在陈旧世界里**不抛**（这条判据留在仓库里防回归）。"""

    def test_the_stale_world_really_is_stale_under_the_patch(self):
        """先自证夹具：被 mock 的世界确实是「产物比源码旧」，否则下面的对照没有意义。"""
        with stale_world():
            fresh, src_m, dist_m = harness.dist_freshness()
        self.assertFalse(fresh)
        self.assertLess(dist_m, src_m)
        _EVIDENCE["stale_world"] = {"fresh": fresh, "src_mtime": src_m, "dist_mtime": dist_m}

    def test_the_historical_pattern_stays_green_while_the_guard_now_raises(self):
        """**同一个陈旧世界**里的判别对照：旧的静默通过，新的当场抛。

        旧写法静默通过的原因不是「它算出来是新鲜的」，而是
        `TestCase.assertTrue(fresh, 消息)` 把 `fresh` 当成了 `self`、把非空消息当成了 `expr`：
        `if not expr:` 恒假。为此这里额外直证该机制本身。
        """
        with stale_world():
            # 机制直证：非空消息占住 expr 位 → assertTrue 什么都不检查
            self.assertIsNone(unittest.TestCase.assertTrue(False, "非空消息"))

            old_result = old_style_assert_dist_fresh(HistoricalCallSite)  # 不抛
            self.assertEqual({"dist_student_html_mtime": 1000.0,
                              "src_newest_mtime": 2000.0}, old_result)

            with self.assertRaises(AssertionError) as ctx:
                harness.assert_dist_fresh()

        message = str(ctx.exception)
        for token in ("2000", "1000", "pnpm --dir frontend build"):
            self.assertIn(token, message, f"守卫的消息没点名 {token!r}：{message}")

        _EVIDENCE["discriminating_pair"] = {
            "old_pattern_raised": False,
            "old_pattern_returned": old_result,
            "new_guard_raised": True,
            "new_guard_message": message,
        }

    def test_the_guard_signature_forbids_a_case_argument(self):
        """签名层面封死回归：再写 `assert_dist_fresh(cls)` 会当场 `TypeError`。"""
        params = inspect.signature(harness.assert_dist_fresh).parameters
        self.assertEqual([], list(params), "守卫又接参数了 —— 旧缺陷会原样回来")
        with self.assertRaises(TypeError):
            harness.assert_dist_fresh(HistoricalCallSite)
        _EVIDENCE["signature"] = {"parameters": list(params), "extra_arg_raises": "TypeError"}


class TestTheGuardRaisesInBothDirections(unittest.TestCase):
    """两个方向都要有具体输入：陈旧世界必须抛、新鲜世界必须不抛。"""

    def test_a_fresh_world_does_not_raise_and_returns_the_mtimes(self):
        with fresh_world():
            result = harness.assert_dist_fresh()
        self.assertEqual({"dist_student_html_mtime": 2000.0, "src_newest_mtime": 1000.0},
                         result)

    def test_a_missing_product_is_stale_via_the_real_predicate(self):
        """不打桩谓词本身：把 `STUDENT_HTML` 指向一个不存在的文件，真 `dist_freshness` 也要判旧。"""
        missing = os.path.join(harness.DIST, "__task43_missing_student.html")
        self.assertFalse(os.path.exists(missing))
        with mock.patch.object(harness, "STUDENT_HTML", missing):
            fresh, _src_m, dist_m = harness.dist_freshness()
            self.assertFalse(fresh)
            self.assertEqual(0.0, dist_m)
            with self.assertRaises(AssertionError):
                harness.assert_dist_fresh()

    def test_the_real_world_is_fresh_right_now(self):
        """真实磁盘上的结论：产物确实比源码新 —— 这是 e2e_m4 那六条守卫有效的前提。"""
        fresh, src_m, dist_m = harness.dist_freshness()
        self.assertTrue(
            fresh,
            f"真实世界产物早于源码（src={src_m:.0f} dist={dist_m:.0f}）："
            f"请先在 `frontend/**` 写域跑 `pnpm --dir frontend build`",
        )
        self.assertEqual({"dist_student_html_mtime": dist_m, "src_newest_mtime": src_m},
                         harness.assert_dist_fresh())
        _EVIDENCE["real_world"] = {"fresh": fresh, "src_mtime": src_m, "dist_mtime": dist_m}
