"""D74「学生端不得下发答案」白名单守卫（task-25，**独立实现**）。

判据原文：`docs/04-接口契约.md` §5.2 第 253-260 行的冻结安全契约（C2 / 契约 #9）：

    > **学生端题目对象只允许这三个键**：`seq`、`display`、`question_type`——**逐层递归检查**，
    > 出现任何其它键即失败。尤其不得出现 `answer`、`steps`（含任何嵌套形式，如
    > `items[].answer`、`data.steps`）。任一层出现白名单外的键即失败——**只查顶层是不够的**，
    > 字段一旦被包进 `item` 里就漏了。**反向断言**：老师端 `GET /api/papers/{id}` 必须**含** `answer`。

本模块按上述原文实现，且**刻意**做三件事让判据不能空过：

1. **题目对象**靠两种特征识别：任何 `items` 容器里的元素，以及**任何含白名单键之一**的 dict。
   后者专门对付「题目被包进别的结构里」——只要一个 dict 长得像题目，就没有理由再多带键。
2. 键集必须**恰好相等**（不是子集）：少键也失败——被截断的题面一样是「学生看到的题不对」。
3. 题目数必须等于响应自己声明的 `total`：否则只探测到空壳的实现也能「通过」，等于没查。

只读模块：不 import `backend/tests/api/**`，也不碰 `backend/app/**`。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

__all__ = [
    "ANSWER_BEARING_KEYS",
    "STUDENT_ITEM_KEYS",
    "STUDENT_ITEM_KEY_SET",
    "STUDENT_PAPER_TOP_KEYS",
    "STUDENT_PRACTICE_TOP_KEYS",
    "WhitelistViolation",
    "assert_guard_rejects",
    "assert_student_payload",
    "assert_teacher_detail_has_answers",
    "assert_top_keys",
    "collect_item_objects",
    "naive_top_level_only_check",
    "teacher_answers",
    "walk",
]

#: 契约 §5.2 / D74：学生端题目对象**只允许**这三个键。
STUDENT_ITEM_KEYS: tuple[str, ...] = ("seq", "display", "question_type")
STUDENT_ITEM_KEY_SET: frozenset[str] = frozenset(STUDENT_ITEM_KEYS)

#: §5.2 字段表给出的顶层字段（`GET /api/student/paper/{code}`）。
STUDENT_PAPER_TOP_KEYS: frozenset[str] = frozenset({"paper_id", "title", "total", "items"})
#: §5.2 字段表给出的顶层字段（`POST /api/student/practice`）。
STUDENT_PRACTICE_TOP_KEYS: frozenset[str] = frozenset({"seed", "total", "items"})

#: **补充**（不是判据）：D71 的教训是枚举式黑名单永远不完整，所以这里只当第二道网，
#: 用来抓「非题目对象的那一层也出现了答案性字段」——白名单判据本身只针对题目对象。
ANSWER_BEARING_KEYS: frozenset[str] = frozenset(
    {
        "answer",
        "steps",
        "expr",
        "expression",
        "origin",
        "signature",
        "difficulty",
        "tags",
        "is_correct",
        "correct",
        "student_answer",
        "payload",
    }
)

#: 题目数组的键名（契约里就叫 `items`）。
ITEM_CONTAINER_KEY = "items"


class WhitelistViolation(AssertionError):
    """白名单守卫失败。

    继承 `AssertionError`：负对照里既可以用 `assertRaises(WhitelistViolation)` 精确断言，
    也可以被 `unittest` 自然当作断言失败看待。
    """


def walk(node: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    """深度优先遍历，产出**每一个** dict / list 节点及其 JSON 风格路径。"""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


def collect_item_objects(body: Any) -> dict[str, dict]:
    """挑出响应里**所有**题目对象，返回 `路径 → 对象`（路径同时用于报错定位）。

    识别规则（并集，见模块 docstring 第 1 条）：
      * `items` 键下的列表元素——必须是 dict，否则直接判失败；
      * 任何含白名单键之一的 dict。
    """
    found: dict[str, dict] = {}
    for path, node in walk(body):
        if not isinstance(node, dict):
            continue
        if STUDENT_ITEM_KEY_SET & set(node):
            found.setdefault(path, node)
        container = node.get(ITEM_CONTAINER_KEY)
        if isinstance(container, list):
            for index, element in enumerate(container):
                element_path = f"{path}.{ITEM_CONTAINER_KEY}[{index}]"
                if not isinstance(element, dict):
                    raise WhitelistViolation(
                        f"{element_path} 不是对象，而是 {type(element).__name__}：{element!r}"
                    )
                found.setdefault(element_path, element)
    return found


def assert_top_keys(body: Any, allowed: frozenset[str], *, where: str) -> None:
    """顶层键必须是 §5.2 字段表的子集（字段表没列的键不该凭空出现）。"""
    if not isinstance(body, dict):
        raise WhitelistViolation(f"{where}: 响应不是 JSON 对象，而是 {type(body).__name__}")
    extra = set(body) - set(allowed)
    if extra:
        raise WhitelistViolation(
            f"{where}: 顶层出现字段表之外的键 {sorted(extra)}（字段表只有 {sorted(allowed)}）"
        )


def assert_student_payload(
    body: Any,
    *,
    where: str,
    expected_items: int | None = None,
) -> list[dict]:
    """D74 主判据：逐层递归校验学生端响应，返回合格题目对象列表。

    `expected_items` 给出时必须与探测到的题目数相等——这是**防止判据空过**的关键一条。
    """
    items = collect_item_objects(body)

    # 主判据：题目对象的键集**恰好**等于白名单（递归到任意层）。
    if not items:
        raise WhitelistViolation(
            f"{where}: 没探测到任何题目对象——判据会空过，因此算失败（见模块 docstring）"
        )
    for path, item in sorted(items.items()):
        extra = set(item) - STUDENT_ITEM_KEY_SET
        missing = STUDENT_ITEM_KEY_SET - set(item)
        if extra or missing:
            raise WhitelistViolation(
                f"{where}: {path} 键集={sorted(item)}，多出={sorted(extra)}、"
                f"缺少={sorted(missing)}（白名单恰好 {sorted(STUDENT_ITEM_KEY_SET)}）"
            )
        if not isinstance(item["seq"], int) or isinstance(item["seq"], bool):
            raise WhitelistViolation(f"{where}: {path}.seq 必须是整数，实际 {item['seq']!r}")
        if not isinstance(item["display"], str) or not item["display"].strip():
            raise WhitelistViolation(f"{where}: {path}.display 必须是非空字符串，实际 {item['display']!r}")
        if not isinstance(item["question_type"], str) or not item["question_type"]:
            raise WhitelistViolation(
                f"{where}: {path}.question_type 必须是非空字符串，实际 {item['question_type']!r}"
            )

    # 补充判据：任何一层都不许出现答案性字段（黑名单只当第二道网，D71）。
    for path, node in walk(body):
        if isinstance(node, dict):
            hit = ANSWER_BEARING_KEYS & set(node)
            if hit:
                raise WhitelistViolation(
                    f"{where}: {path} 出现答案性字段 {sorted(hit)}"
                    f"（逐层递归检查；白名单判据只允许题目对象带 {sorted(STUDENT_ITEM_KEY_SET)}）"
                )

    # 完整性：题目数必须与响应声明的 total 一致。
    declared = body.get("total") if isinstance(body, dict) else None
    for label, value in (("expected_items", expected_items), ("body['total']", declared)):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise WhitelistViolation(f"{where}: {label} 不是整数，实际 {value!r}")
        if value != len(items):
            raise WhitelistViolation(
                f"{where}: {label}={value}，但只探测到 {len(items)} 个题目对象"
                "（数量对不上说明投影缺题或判据漏看）"
            )
    return list(items.values())


def teacher_answers(detail: Any) -> dict[int, str]:
    """递归收集老师端详情里的 `seq → answer`（老师端**应当**有 answer）。"""
    found: dict[int, str] = {}
    for path, node in walk(detail):
        if not isinstance(node, dict) or "answer" not in node:
            continue
        if "seq" not in node:
            continue
        seq, answer = node["seq"], node["answer"]
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise WhitelistViolation(f"{path}: seq 不是整数，实际 {seq!r}")
        if not isinstance(answer, str) or not answer.strip():
            raise WhitelistViolation(f"{path}: answer 必须是非空字符串，实际 {answer!r}")
        found[seq] = answer
    return found


def assert_teacher_detail_has_answers(
    detail: Any,
    *,
    where: str,
    expected_items: int | None = None,
) -> dict[int, str]:
    """反向断言（契约 §5.2 末句）：老师端必须**含** `answer`。

    防的是「一刀切删字段把老师端也删了」——那种改法下学生端守卫会全绿，只有这条会红。
    """
    answers = teacher_answers(detail)
    if not answers:
        raise WhitelistViolation(f"{where}: 一个 answer 都没有——反向断言失败（老师端被一刀切了？）")
    if expected_items is not None and len(answers) != expected_items:
        raise WhitelistViolation(
            f"{where}: 只收集到 {len(answers)} 个 answer，期望 {expected_items} 个"
        )
    return answers


def assert_guard_rejects(body: Any, *, where: str, expected_items: int | None = None) -> str:
    """负对照助手：断言守卫**确实**拒绝 `body`，返回违规原因（便于在报告里引用原文）。"""
    try:
        assert_student_payload(body, where=where, expected_items=expected_items)
    except WhitelistViolation as exc:
        return str(exc)
    raise AssertionError(f"{where}: 守卫**没抓到**这个越权响应——判据失效：{body!r}")


def naive_top_level_only_check(body: Any) -> list[str]:
    """**故意写坏的**检查器：只看 `body["items"][i]` 这一层的键。

    存在的唯一目的是当正对照：用它证明「只查顶层」的实现会放行嵌套越权
    （契约原话：**只查顶层是不够的**）。返回它认为违规的路径列表。
    """
    problems: list[str] = []
    if not isinstance(body, dict):
        return ["body 不是对象"]
    items = body.get(ITEM_CONTAINER_KEY)
    if not isinstance(items, list):
        return ["items 不是列表"]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"$.items[{index}] 不是对象")
            continue
        extra = set(item) - STUDENT_ITEM_KEY_SET
        if extra:
            problems.append(f"$.items[{index}] 多出 {sorted(extra)}")
    return problems
