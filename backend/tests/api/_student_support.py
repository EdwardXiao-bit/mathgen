"""学生端用例的共用脚手架（task-24 / M4）。

三段，各自的「为什么」都写在下面：

1. **白名单守卫**（`assert_public_paper` / `assert_public_practice`）——契约 §5.2 的 D74 白名单
   在测试侧**独立硬编码**一份，**不 import 服务层的常量**。复制是有意的：守卫要能在
   「有人往投影里加字段」时报错，如果它跟着实现一起漂移，就永远报不出错。
   守卫本身也不是凭空的——`test_student_whitelist.py` 用**故意多一个键**的假响应
   证明守卫会失败（V9 负对照），否则「守卫通过」可能只是因为它什么都没查。

2. **没有挂载桥**（原设计有，已删）——曾经这里在 `setUp` 里补挂学生端 router，好让
   `main.py` 尚未装配时也能写用例，并号称「Lead 挂上以后自动变成空操作」。**这个说法在
   FastAPI 0.143 上是假的**：`include_router` 不再把子路由摊平进 `app.routes`，而是存成
   `_IncludedRouter`（`path` 是 `None`），所以按 `route.path` 做的「在不在」检测永远为假，
   两个 router 会在每个用例里被重复 include（先注册者胜，所以测试还绿——**绿得没有理由**）。
   桥已删除：`main.py` 装配好了，将来若回退，学生端用例会**全部 404 大声失败**，
   这比一个偷偷补挂、把真机 404 藏起来的桥安全得多。

3. `StudentApiTestCase`：报到 / 取题 / 练习 / 提交的小助手（都只调 HTTP，不碰服务层）。
"""
from __future__ import annotations

import uuid

from app.contracts.api import ROUTES

from tests.api._support import CONFIG_ADD_SUB, ApiTestCase, url_of

__all__ = [
    "STUDENT_ITEM_KEYS",
    "PAPER_TOP_KEYS",
    "PRACTICE_TOP_KEYS",
    "ATTEMPT_TOP_KEYS",
    "ATTEMPT_ITEM_KEYS",
    "ANSWER_KEYS",
    "assert_keys_within",
    "assert_public_paper",
    "assert_public_practice",
    "assert_no_answer_leak",
    "StudentApiTestCase",
    "new_uuid",
]

# ---- D74 白名单（硬编码副本：见模块注释第 1 段） --------------------------

#: 学生端题目对象只允许这三个键（§5.2）。
STUDENT_ITEM_KEYS = frozenset({"seq", "display", "question_type"})

#: 按卷号取题的顶层键。`paper_code` 在 D74 里是允许项（§5.2 的字段表没列它），
#: 所以判据是**子集**而不是相等：实现可以不给，给多了不行。
PAPER_TOP_KEYS = frozenset({"paper_id", "paper_code", "title", "total", "items"})

#: 自主练习的顶层键：`seed` **必须**在（D75，学生要原样回传）。
PRACTICE_TOP_KEYS = frozenset({"seed", "total", "items"})

#: 提交作答的顶层键（§5.2 / Lead 裁决①）。`score` 是 M5/D80 按契约加上的
#: （`AttemptResultOut.score`，由冻结包 `score_of()` 算出）——判据是**相等**，
#: 所以它必须出现在这里，否则「契约多给了一个字段」会悄悄漏过去。
ATTEMPT_TOP_KEYS = frozenset({"attempt_id", "total", "correct", "score", "items"})

#: 判分结果里每题允许的键——**不含 `steps`**（Lead 裁决①：FR-S8 只要求得分与对错）。
ATTEMPT_ITEM_KEYS = STUDENT_ITEM_KEYS | {"answer", "student_answer", "is_correct"}

#: 答案性键名：学生端取题/练习的响应里出现任何一个都是**泄题**（§5.2 / D74）。
ANSWER_KEYS = frozenset({"answer", "steps", "tags", "difficulty", "signature", "expr", "origin"})


def new_uuid() -> str:
    """新的 `attempt_uuid`（D76：客户端每次提交生成一个）。"""
    return uuid.uuid4().hex


# ---- 守卫 ----------------------------------------------------------------

def assert_keys_within(tc, node, allowed: frozenset[str], where: str) -> None:
    """断言 `node` 的键是 `allowed` 的**子集**；多出来的键要连键名一起报出来。"""
    tc.assertIsInstance(node, dict, f"{where} 不是对象：{type(node).__name__}")
    extra = set(node) - allowed
    tc.assertFalse(
        extra,
        f"{where} 出现白名单外的键 {sorted(extra)}；D74 白名单 = {sorted(allowed)}",
    )


def _assert_items(tc, items, allowed: frozenset[str], where: str = "items") -> None:
    """逐项查键，并禁止题目对象里再嵌 dict/list（白名单是**平的**）。"""
    tc.assertIsInstance(items, list, f"{where} 不是数组")
    for index, item in enumerate(items):
        path = f"{where}[{index}]"
        assert_keys_within(tc, item, allowed, path)
        for key, value in item.items():
            tc.assertNotIsInstance(
                value, (dict, list), f"{path}.{key} 是嵌套结构，白名单只允许平铺标量"
            )


def assert_public_paper(tc, payload: dict, where: str = "响应") -> None:
    """按卷号取题的响应守卫（顶层键 + 题目对象键 + 不得泄题）。"""
    assert_keys_within(tc, payload, PAPER_TOP_KEYS, f"{where}顶层")
    _assert_items(tc, payload["items"], STUDENT_ITEM_KEYS)


def assert_public_practice(tc, payload: dict, where: str = "响应") -> None:
    """自主练习的响应守卫：`seed` 必须在（D75 学生要原样回传）。"""
    assert_keys_within(tc, payload, PRACTICE_TOP_KEYS, f"{where}顶层")
    tc.assertIn("seed", payload, f"{where}缺少 seed：学生无法回传，D75 就断了")
    _assert_items(tc, payload["items"], STUDENT_ITEM_KEYS)


def assert_no_answer_leak(tc, node, where: str = "响应", *, depth: int = 0) -> None:
    """递归断言任何层级都没有答案性键名（键名大小写不敏感）。

    比逐键白名单更宽的一道网：白名单管「形状对不对」，这道网管「有没有泄题」，
    即使将来顶层结构变了它也照样能拦住 `answer` 出现在响应里。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            tc.assertNotIn(
                str(key).lower(),
                ANSWER_KEYS,
                f"{where} 出现答案性字段 `{key}`——学生端不能拿到它（§5.2 / D74）",
            )
            assert_no_answer_leak(tc, value, f"{where}.{key}", depth=depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            assert_no_answer_leak(tc, value, f"{where}[{index}]", depth=depth + 1)


# ---- 用例基类 -------------------------------------------------------------

class StudentApiTestCase(ApiTestCase):
    """学生端用例基类：跑在**真实装配**的 app 上（没有挂载桥，见模块注释第 2 段）。"""

    # ---- 常用动作 -------------------------------------------------------

    def report(self, name: str = "张三", student_no: str = "20240101", **extra):
        """`POST /api/students`（不判状态码，调用方自己断言 201 / 200）。"""
        body = {"name": name, "student_no": student_no}
        body.update(extra)
        return self.client.post(url_of("create_student"), json=body)

    def student_id(self, name: str = "张三", student_no: str = "20240101") -> int:
        response = self.report(name, student_no)
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()["student_id"]

    def practice(self, config: dict | None = None, **query):
        """`POST /api/student/practice`：`config` 是覆盖项。"""
        body = dict(CONFIG_ADD_SUB)
        if config:
            body.update(config)
        return self.client.post(url_of("student_practice"), json=body, params=query or None)

    def student_paper(self, code: str):
        return self.client.get(url_of("student_paper", code=code))

    def submit(self, **body):
        """`POST /api/attempts`。未给的键用一份最小合法体补齐。"""
        payload = {
            "student_id": body.pop("student_id", 0),
            "attempt_uuid": body.pop("attempt_uuid", new_uuid()),
            "answers": body.pop("answers", []),
            "duration_ms": body.pop("duration_ms", 12345),
        }
        payload.update(body)
        return self.client.post(url_of("submit_attempt"), json=payload)

    def published_paper(self) -> tuple[int, str, dict]:
        """建卷 → 发布 → 取详情，返回 `(paper_id, code, detail)`（detail 含答案）。"""
        paper = self.create_paper()
        paper_id = paper["id"]
        response = self.publish(paper_id)
        self.assertEqual(response.status_code, 200, response.text)
        detail = self.detail(paper_id)
        return paper_id, response.json()["code"], detail

    @staticmethod
    def answers_of(detail: dict, *, correct: bool = True) -> list[dict]:
        """按老师卷详情造一份作答（正确 = 照抄答案；错误 = 故意给个错的）。"""
        answers = []
        for item in detail["items"]:
            value = item["answer"] if correct else _wrong_answer(item["answer"])
            answers.append({"seq": item["seq"], "answer": value})
        return answers


def _wrong_answer(answer: str) -> str:
    """构造一个**必然判错**的作答：非数字串（归一化后解析不出数值）。"""
    return "不是数字" if answer else "0"
