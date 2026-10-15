"""M5 成绩验收（task-42 / T-M5-04）的公共设施：得分口径、两条服务路径、判据函数。

设计口径（四条，都为「判据要能自证」服务）：

1. **得分只有一个来源**（D80）。本模块自带一份**独立复述**的契约公式
   （`score_by_contract`）与一份「M5 之前学生端算法」的逐字复刻（`score_by_old_js`），
   分叉点由 `divergence_points()` 现算。判据断言的是「产品 == 我的独立复述」，
   不是「产品 == 文档里抄来的那个数字」。
2. **两条服务路径分工明确**：只读取证、真实 HTTP、以及「产品真能起来」走真实
   uvicorn + 磁盘库（`M5Server`）；需要注入 / 直接改库 / 构造「同一时刻多条记录」
   这类**构造性实验**走进程内（`InProcessApp`，TestClient + 内存库）。
3. **判据都是纯函数**（`*_problems(...) -> list[str]`，空列表 = 通过）：输入是
   **世界**（字典、字节、库行），不是断言。于是「让这条判据变红」可以用合成的坏世界
   直接演示（V9.1 / V9.2 / V11），不依赖产品真的坏掉。
4. **夹具自检**：造世界的助手（`answers_with` / `submit_graded`）会断言自己造出的
   世界真的符合预期（例如「恰好 23 题答对」），避免判据建在一个我没意识到的世界上（V14）。

复用 `tests.e2e_m4` 的公共设施：`ArchiveServer`（真实 uvicorn + httpx + 真实库文件）、
只读取证助手、`browser_session`（产品自己的浏览器通道 —— task-22 起禁止裸用
Playwright 的 sync API）。这些都是同一个写者（verify-m1）的域，不复制第二份会漂移的实现。

写域纪律：本模块与同目录用例只新建/修改 `backend/tests/e2e_m5/**`，不改
`backend/app/**`、`backend/tests/api/**`、`frontend/**`；唯一的跨域写盘是
`docs/06-测试方案.md` §9.11（任务书指派）。
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
DIST = os.path.join(REPO, "frontend", "dist")
os.makedirs(ARTIFACTS, exist_ok=True)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.e2e_m4._archive_support import (  # noqa: E402
    CONFIG,
    ArchiveServer,
    readonly_count,
    readonly_rows,
    readonly_tables,
    submit_attempt,
)
from tests.e2e_m4._harness import browser_session  # noqa: E402,F401

__all__ = [
    "ARTIFACTS", "BOM", "COLUMN_LABELS", "CONFIG", "DIST", "FORBIDDEN_RENDER", "PAGE_LIMIT",
    "AttemptsStubServer", "InProcessApp", "M5Server", "answers_with", "browser_session",
    "csv_problems", "csv_records", "csv_row_for_student", "db_attempt_ids", "db_attempt_row",
    "db_cross_check_problems", "desktop_page", "direct_write", "divergence_points",
    "export_coverage_problems", "list_detail_problems", "paging_problems",
    "practice_contract_problems", "rendered_text_problems", "score_by_contract",
    "score_by_old_js", "score_render_problems", "score_source_problems", "stub_attempt",
    "submit_attempt", "wildcard_problems", "write_evidence",
]

#: 服务端列表默认页大小（`docs/04-接口契约.md` §5.5：limit 默认 20）
PAGE_LIMIT = 20

#: Excel 认 UTF-8 的唯一信号（`docs/04` §5.5 要求 utf-8-sig）
BOM = b"\xef\xbb\xbf"

#: 导出列顺序（契约固定顺序，写在这里是为了让判据**独立于实现**地复述它）
COLUMN_LABELS = (
    "卷号", "卷名", "姓名", "学号", "得分", "答对题数", "总题数", "用时", "提交时间",
)

#: 界面上不该出现的渲染结果（A10：`null` 要渲染成人能读的东西）
FORBIDDEN_RENDER = ("undefined", "null", "NaN")


# ---------------------------------------------------------------------------
# 1. 得分口径：独立复述 + 分叉点枚举
# ---------------------------------------------------------------------------

def score_by_contract(total: int, correct: int) -> int:
    """独立复述 D80 的整数公式（**不调用产品代码**）。

    `docs/00-决策记录.md:92`：先把 `correct` 夹进 `[0, total]`，`total == 0` 给 0，
    否则精确 half-up：`(clamped * 200 + total) // (2 * total)`。
    """
    if not isinstance(total, int) or not isinstance(correct, int):
        raise TypeError(f"score_by_contract 需要两个 int，拿到 {total!r} / {correct!r}")
    if total <= 0:
        return 0
    clamped = max(0, min(correct, total))
    return (clamped * 200 + total) // (2 * total)


def score_by_old_js(total: int, correct: int) -> int:
    """M5 之前学生端结果页的算法（D80 要消灭的那份口径），逐字复刻浮点语义。

    原码是 `Math.round(correct / total * 100)` 的等价写法；JS 的 `/` `*` 是 IEEE-754
    double，`Math.floor(x + 0.5)` 对正数等价于 `Math.round`（`.5` 向上）。复刻成
    Python 就是下面这一行 —— **关键是不要让 Python 的 `round()` 混进来**：那是
    银行家舍入，不是 JS 的行为（`round(57.5) == 58`，而 `floor(57.5 + 0.5) == 58`，
    但浮点算出来的 `23 / 40 * 100` 是 `57.49999999999999`，两者都会给 57，
    所以真正的分叉来自浮点误差，见 `divergence_points()`）。
    """
    if total <= 0:
        return 0
    return math.floor((correct / total * 100) + 0.5)


def divergence_points(max_total: int = 200, *, limit: int | None = None) -> list[dict]:
    """枚举旧口径与契约口径分叉的 `(total, correct)`。

    返回每项：`{total, correct, float, old_js, contract, exact_half}`。
    `limit` 用来只看「真实题量上限内」的点（题量上限 `count <= 100`）。
    """
    points = []
    for total in range(1, max_total + 1):
        if limit is not None and total > limit:
            break
        for correct in range(0, total + 1):
            old = score_by_old_js(total, correct)
            new = score_by_contract(total, correct)
            if old != new:
                points.append({
                    "total": total,
                    "correct": correct,
                    "float": correct / total * 100,
                    "old_js": old,
                    "contract": new,
                    # 「精确值正好是 x.5」的整数判据：`correct*100/total` 的小数部分恰为 1/2。
                    "exact_half": (correct * 200) % (2 * total) == total,
                })
    return points


def score_source_problems(*, total, correct, observed: dict) -> list[str]:
    """「得分只有一个来源」：所有观测点都必须等于契约口径算出的那一个数。

    `observed` = `{来源标签: 观测到的 score}`。空字典也算问题 —— 那说明判据
    根本没在守护任何东西（V9：空集对空集最容易假绿）。
    """
    problems: list[str] = []
    if not observed:
        problems.append("没有任何 score 观测点 —— 这条判据没在守护任何东西")
        return problems
    if not isinstance(total, int) or not isinstance(correct, int):
        return [f"观测缺了 total/correct（拿到 {total!r} / {correct!r}），无法核对得分口径"]

    want = score_by_contract(total, correct)
    old = score_by_old_js(total, correct)
    for label, value in sorted(observed.items()):
        if value is None:
            problems.append(f"{label} 没拿到 score（观测缺失）")
        elif int(value) != want:
            problems.append(
                f"{label} 的 score={value}，按 D80 独立复述应为 {want}"
                f"（total={total} correct={correct}；M5 之前的旧算法在这里给 {old}）")
    return problems


def score_render_problems(*, rendered: str, payload: dict) -> list[str]:
    """判据 1 的**界面侧**：界面渲染出的分数必须与后端 JSON 里的 `score` 逐字相同（D80）。

    这里**不重算**分数去当判据的期望值 —— 重算就把「同一份成绩两个来源」升级成
    「两个来源都被判据认可」。重算只用来**点名**：界面显示的数字恰好等于哪种本地算法，
    说明界面在自己算分（`frontend/src/teacher/pages/AttemptsPage.tsx:391` 只该渲染
    `String(attempt.score)`）。
    """
    problems: list[str] = []
    expected = payload.get("score")
    if expected is None:
        problems.append("后端 JSON 里没有 score：界面无从呈现，这条判据在空转")
        return problems

    shown = (rendered or "").strip()
    if shown != str(expected):
        problems.append(f"界面显示 {shown!r}，后端给 {str(expected)!r}")
        # 只有「显示值 ≠ 后端给的值、却恰好等于某种本地算法」才能断言界面在自己算分：
        # 后端值本来就等于契约口径时，显示 58 是**正确**的，不是本地算分的证据。
        total, correct = payload.get("total"), payload.get("correct")
        if isinstance(total, int) and isinstance(correct, int) and total > 0:
            for label, value in (("M5 之前的旧浮点口径", score_by_old_js(total, correct)),
                                 ("契约 D80 口径", score_by_contract(total, correct))):
                if shown == str(value):
                    problems.append(
                        f"界面显示的数字正是{label}自己算出来的 {value}"
                        f"（后端给的是 {expected}）—— 界面在本地算分")
    return problems


# ---------------------------------------------------------------------------
# 2. 判据：列表 ↔ 明细 ↔ 库
# ---------------------------------------------------------------------------

def list_detail_problems(*, summary: dict, detail: dict) -> list[str]:
    """同一条作答记录在列表行与明细里必须互相印证（V13：不是「各自看起来都对」）。"""
    problems: list[str] = []
    items = detail.get("items")
    if items is None:
        return ["明细响应里没有 items 字段"]

    if detail.get("id") != summary.get("id"):
        problems.append(f"明细 id={detail.get('id')!r} 与列表 id={summary.get('id')!r} 不是同一条")
    if len(items) != detail.get("total"):
        problems.append(f"明细 len(items)={len(items)} ≠ detail.total={detail.get('total')!r}")
    if len(items) != summary.get("total"):
        problems.append(f"明细 len(items)={len(items)} ≠ 列表 total={summary.get('total')!r}")

    correct_in_items = sum(1 for item in items if item.get("is_correct"))
    if correct_in_items != summary.get("correct"):
        problems.append(
            f"明细里 is_correct 为真的条数={correct_in_items} ≠ 列表 correct={summary.get('correct')!r}")

    seqs = [item.get("seq") for item in items]
    if seqs != sorted(seqs, key=lambda value: (value is None, value)):
        problems.append(f"明细 items 没有按 seq 升序：{seqs}")

    for item in items:
        missing = [key for key in
                   ("seq", "display", "question_type", "answer", "student_answer", "is_correct")
                   if key not in item]
        if missing:
            problems.append(f"明细第 {item.get('seq')!r} 题缺字段 {missing}")
            break

    if not items:
        problems.append("明细 items 为空 —— 要么夹具没造出题，要么接口吞了数据")

    problems.extend(score_source_problems(
        total=summary.get("total"), correct=summary.get("correct"),
        observed={"列表 score": summary.get("score"), "明细 score": detail.get("score")}))
    return problems


def db_cross_check_problems(*, summary: dict, row: dict | None, item_count: int,
                            paper_row: dict | None = None) -> list[str]:
    """接口返回的那一行必须与库里那一行逐字段对得上（不是「都像对的」）。"""
    if row is None:
        return [f"库里找不到 attempt id={summary.get('id')!r} —— 接口返回了一条库里没有的记录"]

    problems: list[str] = []
    for column in ("total", "correct", "duration_ms", "student_id", "submitted_at"):
        api, db = summary.get(column), row.get(column)
        if api != db:
            problems.append(f"{column}：接口 {api!r} ≠ 库 {db!r}")
    if summary.get("paper_id") != row.get("paper_id"):
        problems.append(f"paper_id：接口 {summary.get('paper_id')!r} ≠ 库 {row.get('paper_id')!r}")
    if int(item_count) != int(row.get("total") or -1):
        problems.append(
            f"库里 attempt_item 行数={item_count} ≠ attempt.total={row.get('total')!r}"
            "（明细是被截断的还是 total 是编的？）")
    if "score" in row:
        problems.append("attempt 表里存了 score 列 —— 得分有了第二个来源（D80 要求它只由 score_of 算）")
    if paper_row is not None:
        if summary.get("paper_code") != paper_row.get("code"):
            problems.append(f"paper_code：接口 {summary.get('paper_code')!r} ≠ 库 {paper_row.get('code')!r}")
        if summary.get("paper_title") != paper_row.get("title"):
            problems.append(
                f"paper_title：接口 {summary.get('paper_title')!r} ≠ 库 {paper_row.get('title')!r}")
    return problems


def paging_problems(*, pages: list[dict], expected_ids: list[int],
                    expected_total: int) -> list[str]:
    """翻页「不重不漏」：逐页取回的 id 串起来必须等于**全序**，且每页 total 相同。"""
    problems: list[str] = []
    seen = [int(row["id"]) for page in pages for row in page.get("attempts", [])]
    duplicates = sorted({value for value in seen if seen.count(value) > 1})
    if duplicates:
        problems.append(f"翻页出现重复行 id={duplicates}")
    if seen != [int(value) for value in expected_ids]:
        missing = [value for value in expected_ids if value not in seen]
        extra = [value for value in seen if value not in expected_ids]
        problems.append(
            f"翻页拼起来的顺序/内容与「submitted_at desc, id desc 全序」不一致："
            f"实际 {seen}，期望 {[int(v) for v in expected_ids]}（缺 {missing}，多 {extra}）")
    for page in pages:
        if page.get("total") != expected_total:
            problems.append(
                f"每页的 total 都必须是 {expected_total}，第 {pages.index(page) + 1} 页给的是 "
                f"{page.get('total')!r}（分页不该改变总数）")
            break
    if not pages:
        problems.append("没有任何一页 —— 判据没有输入")
    return problems


# ---------------------------------------------------------------------------
# 3. 判据：导出（CSV / XLSX）
# ---------------------------------------------------------------------------

def csv_records(data: bytes) -> tuple[list[str], list[list[str]]]:
    """CSV 字节 → `(表头, 数据行)`；按 `utf-8-sig` 解码（BOM 透明）。"""
    text = data.decode("utf-8-sig")
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def csv_problems(*, data: bytes, expected_rows: int,
                 labels: tuple = COLUMN_LABELS) -> tuple[list[str], list[list[str]]]:
    """CSV 的形状判据：BOM + 表头顺序 + 行数 + 列数。返回 `(问题清单, 数据行)`。

    为什么 BOM 是判据而不是「显示问题」：Windows Excel 打开无 BOM 的 UTF-8 CSV 会按
    本地码页（中文机器是 GBK）解码，表头直接变乱码 —— 用户看到的就是「导出坏了」。
    """
    problems: list[str] = []
    if data[:3] != BOM:
        problems.append(
            f"CSV 前 3 字节是 {data[:3]!r}，不是 EF BB BF —— 缺 UTF-8 BOM，"
            "Windows Excel 会按本地码页解码，中文表头/姓名会乱码")
    try:
        header, rows = csv_records(data)
    except UnicodeDecodeError as exc:
        return ([f"CSV 不是合法 UTF-8：{exc}"], [])
    if header != list(labels):
        problems.append(f"表头不是契约顺序：{header!r} ≠ {list(labels)!r}")
    if len(rows) != expected_rows:
        problems.append(f"CSV 数据行数={len(rows)} ≠ 期望 {expected_rows}")
    for index, row in enumerate(rows, start=1):
        if len(row) != len(labels):
            problems.append(f"CSV 第 {index} 行有 {len(row)} 列 ≠ {len(labels)} 列：{row!r}")
            break
    if expected_rows > 0 and not rows:
        problems.append("CSV 一行数据都没有 —— 夹具或实现吞了数据")
    return problems, rows


def csv_row_for_student(rows: list[list[str]], student_no: str) -> list[str] | None:
    """按学号（第 4 列）取一行，方便核对「得分」列。"""
    index = list(COLUMN_LABELS).index("学号")
    for row in rows:
        if len(row) > index and row[index] == student_no:
            return row
    return None


def export_coverage_problems(*, csv_rows: int, list_total: int, limit: int) -> list[str]:
    """导出必须覆盖**当前筛选下的全部**记录，一个都不能少。

    `limit` / `list_total` 都取自**响应本身**（不写死 20）：契约要求限流参数由服务端回显。
    另外这里会主动检查夹具是否真的造出了「总数 > 一页」的世界 —— 否则这条判据
    看起来在守护，实际上在那个场景里恒真。
    """
    problems: list[str] = []
    if not isinstance(list_total, int) or not isinstance(limit, int):
        return [f"覆盖面判据缺输入：list_total={list_total!r} limit={limit!r}"]
    if list_total <= limit:
        problems.append(
            f"夹具失效：列表 total={list_total} 没有超过一页 limit={limit}，"
            "这个场景证明不了「导出不受分页限制」")
    if csv_rows != list_total:
        problems.append(
            f"导出只给了 {csv_rows} 行，而当前筛选下 total={list_total} 条 —— 导出偷偷套用了分页")
    return problems


# ---------------------------------------------------------------------------
# 4. 判据：自主练习（D75）与界面渲染（A10）
# ---------------------------------------------------------------------------

#: 契约要求**接口不返回**的显示文案（由界面自己决定怎么显示）
PRACTICE_WORDS = ("自主练习",)


def practice_contract_problems(*, summary: dict, payload_text: str | None = None) -> list[str]:
    """自主练习：三个卷字段必须是 `null`，且响应体里不能出现显示文案。"""
    problems: list[str] = []
    for key in ("paper_id", "paper_code", "paper_title"):
        if summary.get(key) is not None:
            problems.append(f"自主练习的 {key} 必须是 null，实际是 {summary.get(key)!r}")
    if payload_text is not None:
        for word in PRACTICE_WORDS:
            if word in payload_text:
                problems.append(
                    f"响应体里出现了显示文案「{word}」—— 契约要求接口只给 null，"
                    "显示文案由界面决定（否则改文案要动后端）")
    if not summary.get("total"):
        problems.append("自主练习的 total 为 0 —— 夹具没有真的出题")
    return problems


def rendered_text_problems(*, text: str | None, where: str) -> list[str]:
    """界面上的可见文本不能出现 `undefined` / `null` / `NaN`（A10）。"""
    if text is None:
        return [f"{where} 没有渲染出来（选择器没命中）"]
    hits = [token for token in FORBIDDEN_RENDER if token in text]
    if hits:
        return [f"{where} 的可见文本里出现了 {hits}：{text!r}"]
    return []


# ---------------------------------------------------------------------------
# 5. 判据：`q` 的 LIKE 通配符必须按字面量
# ---------------------------------------------------------------------------

def wildcard_problems(*, char: str, students: list[dict], returned_ids: list[int]) -> list[str]:
    """`q` 里的 `%` / `_` 必须当**普通字符**，不能当 LIKE 通配符。

    `students` = `[{"student_id":…, "name":…, "student_no":…}]`（调用方的夹具事实）。
    期望集合由**字面量包含**现算：任何人如果实现忘转义，`q='%'` 就会把全部学生都匹配上。
    """
    expected = sorted(
        int(row["student_id"]) for row in students
        if char in (row.get("name") or "") or char in (row.get("student_no") or ""))
    got = sorted({int(value) for value in returned_ids})
    if got == expected:
        return []
    return [
        f"q={char!r} 按字面量应匹配 {expected}，实际返回 {got}"
        f"（若返回全部 {len(students)} 个学生，说明 {char!r} 被当成 LIKE 通配符了；"
        "若返回空集，说明实现把字面量也转义掉了）",
    ]


# ---------------------------------------------------------------------------
# 6. 只读 / 直接写库的取数口
# ---------------------------------------------------------------------------

def db_attempt_row(db_path: str, attempt_id: int) -> dict | None:
    rows = readonly_rows(db_path, "SELECT * FROM attempt WHERE id=?", (attempt_id,))
    return rows[0] if rows else None


def db_attempt_ids(db_path: str) -> list[int]:
    """库里全部 attempt 的 id，按契约排序（`submitted_at desc, id desc`）。"""
    return [int(row["id"]) for row in readonly_rows(
        db_path, "SELECT id FROM attempt ORDER BY submitted_at DESC, id DESC")]


def db_paper_row(db_path: str, paper_id: int) -> dict | None:
    rows = readonly_rows(db_path, "SELECT * FROM paper WHERE id=?", (paper_id,))
    return rows[0] if rows else None


def direct_write(db_path: str, sql: str, params: tuple = ()) -> int:
    """**直接写**库：只用来构造「判据必须能发现的坏世界」（真删一行等）。

    这是夹具动作，不是产品缺陷注入：删的是本用例自己造的临时库文件。
    """
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(sql, params)
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 7. 造世界：作答答案
# ---------------------------------------------------------------------------

def answers_with(questions: dict, *, correct_count: int,
                 wrong_answer: str | None = None) -> list[dict]:
    """造一份「**恰好** `correct_count` 题答对」的作答。

    答错的题给 `null`（契约 §5.2：未作答按答错计），所以不需要猜一个「保证错」的答案。
    """
    seqs = sorted(int(key) for key in questions)
    if not 0 <= correct_count <= len(seqs):
        raise ValueError(f"correct_count={correct_count} 超出 0..{len(seqs)}")
    correct = set(seqs[:correct_count])
    return [{"seq": seq, "answer": questions[seq] if seq in correct else wrong_answer}
            for seq in seqs]


# ---------------------------------------------------------------------------
# 8. 真实服务路径
# ---------------------------------------------------------------------------

class M5Server(ArchiveServer):
    """真实 `uvicorn app.main:app` + 磁盘库 —— M5 判据的取数口子。

    继承 task-36 的 `ArchiveServer`（同写者的域，不复制第二份）：它有 `request()`、
    `new_paper()`、`student_id()`、`submit()` 这些通用件；这里只补 M5 的
    `/api/attempts` 系列与「从库里读题目快照」。
    """

    # ---- /api/attempts 系列 -------------------------------------------
    def list_attempts_response(self, *, expect: int | None = 200, **params):
        return self.request("GET", "/api/attempts", params=params or None, expect=expect)

    def list_attempts(self, **params) -> dict:
        return self.list_attempts_response(**params).json()

    def get_attempt_response(self, attempt_id, *, expect: int | None = None):
        return self.request("GET", f"/api/attempts/{attempt_id}", expect=expect)

    def get_attempt(self, attempt_id) -> dict:
        return self.get_attempt_response(attempt_id).json()

    def export_response(self, *, expect: int | None = 200, **params):
        return self.request("GET", "/api/attempts/export", params=params or None, expect=expect)

    def export_bytes(self, **params) -> tuple[bytes, dict]:
        response = self.export_response(**params)
        return response.content, dict(response.headers)

    # ---- 库里那套题的标准答案（不重跑引擎，直接读快照） ---------------
    def questions(self, paper_id: int) -> dict:
        rows = readonly_rows(
            self.db_path,
            "SELECT seq, payload FROM paper_item WHERE paper_id=? ORDER BY seq",
            (paper_id,))
        return {int(row["seq"]): json.loads(row["payload"])["answer"] for row in rows}

    def submit_graded(self, *, student_id: int, paper_id: int, uuid_hex: str,
                      correct_count: int, duration_ms: int = 1234):
        """提交一次作答，并**自检**夹具真的造出了「恰好 correct_count 题答对」的世界。"""
        questions = self.questions(paper_id)
        answers = answers_with(questions, correct_count=correct_count)
        response = self.submit(student_id=student_id, paper_id=paper_id,
                               attempt_uuid=uuid_hex, answers=answers,
                               duration_ms=duration_ms)
        assert response.status_code in (200, 201), response.text
        body = response.json()
        if body.get("total") != len(questions) or body.get("correct") != correct_count:
            raise AssertionError(
                "夹具没造出预期的世界：期望 "
                f"total={len(questions)} correct={correct_count}，"
                f"实际 total={body.get('total')!r} correct={body.get('correct')!r}"
                f"（接口原样返回 {body!r}）")
        return response


class InProcessApp:
    """进程内 `TestClient` + 内存库：用于注入 / 直接改库 / 构造同刻多条记录。

    自己建库、自己覆盖 `get_db`，不 import 别人的测试包（`backend/tests/api/**` 是
    be-paper 的域）。`db_path` 给定时用磁盘库（需要跨连接直接改库时用）。
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self.engine = None
        self.client = None
        self.factory = None

    def __enter__(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from app.db import enable_sqlite_foreign_keys, get_db, init_db, session_factory
        from app.main import create_app

        if self.db_path:
            url = f"sqlite+pysqlite:///{self.db_path}"
            pool_kwargs: dict = {}
        else:
            url = "sqlite+pysqlite:///:memory:"
            pool_kwargs = {"poolclass": StaticPool}
        self.engine = enable_sqlite_foreign_keys(create_engine(
            url, connect_args={"check_same_thread": False}, future=True, **pool_kwargs))
        init_db(self.engine)
        self.factory = session_factory(self.engine)
        app = create_app(engine=self.engine, static_dir=False)

        def _override():
            session = self.factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        return self

    def __exit__(self, *exc):
        if self.engine is not None:
            self.engine.dispose()
        return False

    def session(self):
        return self.factory()

    def make_paper(self, *, count: int = 3, seed: int = 20261015,
                   title: str = "M5 成绩验收卷") -> int:
        created = self.client.post("/api/papers", params={"seed": seed, "title": title},
                                   json=dict(CONFIG, count=count))
        assert created.status_code == 201, created.text
        return int(created.json()["id"])

    def publish(self, paper_id: int) -> dict:
        response = self.client.post(f"/api/papers/{paper_id}/publish")
        assert response.status_code == 200, response.text
        return response.json()

    def publish_and_get(self, paper_id: int) -> tuple[str, dict]:
        body = self.publish(paper_id)
        return body["code"], body


# ---------------------------------------------------------------------------
# 8.5 撒谎的桩：只伪造 `/api/attempts` 的 JSON（真实 dist + 真实浏览器）
# ---------------------------------------------------------------------------

def stub_attempt(attempt_id: int = 1, *, student_name: str = "桩学生",
                 student_no: str = "2026STUB01", total: int = 40, correct: int = 23,
                 score: int | None = None, paper_id: int | None = 7,
                 paper_code: str | None = "2610-0007",
                 paper_title: str | None = "桩卷", duration_ms: int = 83000,
                 submitted_at: str = "2026-10-15T10:00:00+08:00") -> dict:
    """一条 `AttemptSummary` 形状的假数据（字段与 `frontend/src/shared/api.ts:283-296` 对齐）。

    `score` 缺省时按**契约**算；但界面负对照要的恰恰是**任何本机算法都推不出来**的值：
    `(total=40, correct=23)` 给 `score=91`。
    """
    return {
        "id": attempt_id,
        "student_id": 1,
        "student_name": student_name,
        "student_no": student_no,
        "paper_id": paper_id,
        "paper_code": paper_code,
        "paper_title": paper_title,
        "total": total,
        "correct": correct,
        "score": score_by_contract(total, correct) if score is None else score,
        "duration_ms": duration_ms,
        "submitted_at": submitted_at,
    }


class AttemptsStubServer:
    """真实构建产物 + **只伪造 `/api/attempts`** 的桩 —— 界面判据的负对照世界。

    为什么需要它：`frontend/**` 不在本任务写域内，不能给页面注入任何东西；而
    `_harness.degraded_dist()` 的注入只作用于 `student.html` 的 CSS，动不了教师端 JS。
    于是换方向：**前端源码一个字不改**，只把列表数据换成一个界面自己算不出来的分数，
    看渲染结果是否如判据所料（`test_paper_archive_ui.py` 的 `LibraryStubServer` 是同一手法）。

    端口用 `bind(("127.0.0.1", 0))` 让内核分配（不预探测端口：那是 TOCTOU）。
    所有 `/api/attempts` 请求都记进 `self.requests`，供判据直接核对查询参数。
    """

    def __init__(self, attempts: list[dict]):
        self.attempts = list(attempts)
        self.requests: list[dict] = []
        #: 所有请求的原文（含静态资源）—— 诊断「页面为什么不渲染」用
        self.all_requests: list[str] = []
        self.httpd = None
        self.thread = None
        self.port = None

    # ---- 请求处理 --------------------------------------------------------
    def _attempts_payload(self, query: dict) -> dict:
        limit = int((query.get("limit") or [str(PAGE_LIMIT)])[0])
        offset = int((query.get("offset") or ["0"])[0])
        q = ((query.get("q") or [""])[0]).strip().lower()
        paper_id = (query.get("paper_id") or [None])[0]

        rows = []
        for row in self.attempts:
            if q and q not in row["student_name"].lower() \
                    and q not in str(row["student_no"]).lower():
                continue
            if paper_id is not None and str(row["paper_id"]) != str(paper_id):
                continue
            rows.append(row)
        return {"total": len(rows), "limit": limit, "offset": offset,
                "attempts": rows[offset:offset + limit]}

    def _send_json(self, handler, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def __enter__(self):
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from urllib.parse import parse_qs, urlparse

        stub = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=DIST, **kwargs)

            def do_GET(self):                # noqa: N802
                parsed = urlparse(self.path)
                stub.all_requests.append(f"GET {self.path}")
                if parsed.path == "/api/attempts":
                    query = parse_qs(parsed.query)
                    stub.requests.append(query)
                    return stub._send_json(self, 200, stub._attempts_payload(query))
                if parsed.path.startswith("/api/"):
                    return stub._send_json(self, 404, {"error": {
                        "code": "STUB_UNIMPLEMENTED",
                        "message": f"验收桩只实现 /api/attempts：{parsed.path}"}})
                if not os.path.exists(self.translate_path(self.path)) and "." not in self.path:
                    self.path = "/index.html"      # SPA 回落
                return super().do_GET()

            def log_message(self, *args):          # 静音
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ---------------------------------------------------------------------------
# 9. 浏览器（走产品自己的通道：task-22 起禁止裸用 Playwright sync API）
# ---------------------------------------------------------------------------

def desktop_page(chan, viewport: tuple = (1440, 900)):
    """开一个桌面页面。`chan` 来自 `with browser_session() as chan:`。

    不许 `sync_playwright()`：产品的 `app/export/browser.py` 有进程级常驻会话
    （`_SESSION`），Playwright 的 sync API 用 greenlet 调度，之后裸新建会撞
    「Sync API inside the asyncio loop」守卫（task-22 实测 12 条 errors）。
    """
    browser = chan.browser()
    return browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})


# ---------------------------------------------------------------------------
# 10. 证据落盘
# ---------------------------------------------------------------------------

def write_evidence(name: str, payload) -> str:
    """把一次判据的**原始观测**写成 JSON（UTF-8 无 BOM、LF），返回路径。"""
    path = os.path.join(ARTIFACTS, name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    return path


def tables_in(db_path: str) -> list[str]:
    return readonly_tables(db_path)
