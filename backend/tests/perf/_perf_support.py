"""并发提交压测的判据设施（task-48 / T-M6-02 / NFR-4 后半句）。

## 它回答哪个问题

NFR-4 后半句是「局域网内**数十名学生并发提交不阻塞**」。本模块提供三样东西：

1. **真并发驱动** `fire_concurrent()`：每条请求一个线程 + 独立 `httpx.Client`，
   起跑用 `threading.Barrier` 对齐。**不是** `for` 循环串行发的。
2. **真机取数口子** `PerfServer`：真 `uvicorn app.main:app` + 磁盘 SQLite 文件，
   库落在本目录 `artifacts/` 下（`MATHGEN_DB` 指过去），**不碰产品库**
   `backend/app/data/mathgen.db`。
3. **一组纯函数判据**：零 5xx、D76 唯一性、`total == len(items)`、没有半写 item。
   纯函数的好处是**能被喂一份改坏的库**，因此证明它真的会红（V9）。

## 三条诚实边界（报告里也写一遍）

1. 本套件测的是「**不阻塞 / 不 5xx / 落库完整**」，不是 NFR-4 前半句
   「20–50 题在 1 秒内返回」（生成侧由 task-46 判据覆盖）。时延数字只是观测量：
   契约没有为单次提交定 SLO，所以这里**不设**一个自己编的阈值把它伪装成门禁。
2. 数字来自**本机回环 + 单文件 SQLite**，不是教室 WiFi 下的多机。它能回答
   「会不会阻塞」，回答不了「在局域网下够不够快」。
3. `database is locked` 是**要测的真相**，不是要抹掉的噪声：`fire_concurrent()`
   不做任何重试，`submit_batch_problems()` 会把它原样报出来。重试恰好会把
   「阻塞」洗成「不阻塞」。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import Counter

from tests._uvicorn_boot import read_server_log
from tests.e2e_m4._archive_support import readonly_rows
from tests.e2e_m5._m5_support import M5Server

__all__ = [
    "APP_DIR", "ARTIFACTS", "BACKEND", "CONCURRENT_STUDENTS", "HERE", "PAPER_QUESTIONS",
    "PRODUCT_DB", "REPLAY_THREADS", "REPO", "SERIAL_STUDENTS", "BATCH_TIMEOUT",
    "PerfServer", "ServerLogDrain", "answers_for", "attempt_rows", "body_field", "db_rows",
    "db_script", "db_write", "drain_server_log", "duplicate_attempt_problems",
    "exclusive_write_lock", "fingerprint_diff", "fire_concurrent", "fire_serial",
    "item_integrity_problems", "item_rows", "latency_stats", "lock_is_held", "new_uuid",
    "percentile", "remove_unique_constraint", "source_fingerprint",
    "submit_batch_problems", "submit_spec", "summarise_batch", "timed_db_write",
    "write_evidence",
]

HERE = os.path.dirname(os.path.abspath(__file__))              # backend/tests/perf
BACKEND = os.path.dirname(os.path.dirname(HERE))               # backend/
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
APP_DIR = os.path.join(BACKEND, "app")
PRODUCT_DB = os.path.join(APP_DIR, "data", "mathgen.db")

#: 并发批的规模：`docs/06-测试方案.md` §10 写的是「50 名学生并发提交」，所以这里取 50。
#: 50 名学生 × 40 题 = 2000 行 `attempt_item`，写放大都集中在这条路径上。
CONCURRENT_STUDENTS = 50
#: 串行对照的规模：与并发批同量，用来回答「并发有没有比串行更糟」。
SERIAL_STUDENTS = 50
#: 同一 `attempt_uuid` 的并发重放线程数（D76 在并发下的那一条）。
REPLAY_THREADS = 8
#: 一张卷的题数（提交时每题一行 `attempt_item`，写放大量在这里）。
PAPER_QUESTIONS = 40
#: 单请求客户端超时。给得宽：pysqlite 的 `timeout` 缺省 5.0 秒，而实测忙等回调
#: 的粒度让「锁被长期持有」这条请求在 **7.4~7.5 秒**才拿到 500（见
#: `artifacts/task48_lock_probe{,2,3}.py`），仍远小于这个值。
BATCH_TIMEOUT = 90.0


# ---------------------------------------------------------------------------
# 1. 测量窗口有效性：产品源码的聚合指纹
# ---------------------------------------------------------------------------

def source_fingerprint(root: str = APP_DIR) -> dict:
    """`root/**/*.py` 的聚合哈希 + 文件数 + 最新 mtime。

    Lead 的硬要求：测量窗口内 `backend/app/**` 必须稳定。两次指纹不同，这次测量
    就**无效**——「在别人半写状态下量出来的数字比没有数字更坏，因为它看起来是证据」。
    """
    files: list[str] = []
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            files.append(path)
            try:
                newest = max(newest, os.path.getmtime(path))
            except OSError:
                pass
    digest = hashlib.sha256()
    for path in sorted(files):
        rel = os.path.relpath(path, root).replace("\\", "/")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            with open(path, "rb") as handle:
                digest.update(handle.read())
        except OSError as exc:
            digest.update(f"<unreadable: {exc}>".encode("utf-8"))
        digest.update(b"\0")
    return {
        "root": root,
        "files": len(files),
        "sha256_16": digest.hexdigest()[:16],
        "sha256": digest.hexdigest(),
        "newest_mtime": newest,
    }


def fingerprint_diff(before: dict, after: dict) -> list[str]:
    """两份指纹不一致的地方（空列表 = 窗口内没被改过）。"""
    problems: list[str] = []
    if before.get("files") != after.get("files"):
        problems.append(f"文件数变了：{before.get('files')} → {after.get('files')}")
    if before.get("sha256") != after.get("sha256"):
        problems.append(
            f"聚合哈希变了：{before.get('sha256_16')} → {after.get('sha256_16')}")
    if before.get("newest_mtime") != after.get("newest_mtime"):
        problems.append(
            f"最新 mtime 变了：{before.get('newest_mtime')} → {after.get('newest_mtime')}")
    return problems


# ---------------------------------------------------------------------------
# 2. 真机服务：复用 task-36/task-42 的取数口子，不复制第二份
# ---------------------------------------------------------------------------

class PerfServer(M5Server):
    """真机 uvicorn + 磁盘库。只加「批量建学生」与「建卷并拿标准答案」。

    `M5Server`（task-42）已经有 `request()` / `new_paper()` / `student_id()` /
    `submit()` / `questions()` / `db_path`；`db_path` 由构造参数落到本目录
    `artifacts/`，所以产品库不会被动到。
    """

    def make_students(self, *, name_prefix: str, no_prefix: str, count: int) -> list[int]:
        return [self.student_id(f"{name_prefix}{index:02d}", f"{no_prefix}{index:03d}")
                for index in range(1, count + 1)]

    def published_paper(self, *, title: str, seed: int, count: int) -> tuple[int, dict]:
        """建卷 + 发布，返回 `(paper_id, {seq: 标准答案})`。"""
        paper_id = self.new_paper(title=title, seed=seed, count=count)
        self.request("POST", f"/api/papers/{paper_id}/publish", expect=200)
        return paper_id, self.questions(paper_id)


def answers_for(questions: dict, *, correct_count: int) -> list[dict]:
    """造一份「**恰好**答对 `correct_count` 题」的作答。

    答错的题送 `null`（契约 §5.2：未作答按答错计），所以不必猜一个「保证错」的答案。
    这里保留一份本地实现（与 `_m5_support.answers_with` 同口径），让压测包不必为了
    一个 8 行函数去 import M5 的判据模块。
    """
    seqs = sorted(int(key) for key in questions)
    if not 0 <= correct_count <= len(seqs):
        raise ValueError(f"correct_count={correct_count} 超出 0..{len(seqs)}")
    correct = set(seqs[:correct_count])
    return [{"seq": seq, "answer": questions[seq] if seq in correct else None}
            for seq in seqs]


def submit_spec(*, student_id: int, paper_id: int, attempt_uuid: str,
                answers: list | None = None, duration_ms: int = 1234) -> dict:
    """一条 `POST /api/attempts` 的请求体（`fire_concurrent` 的输入单位）。"""
    return {"json": {
        "student_id": student_id,
        "attempt_uuid": attempt_uuid,
        "answers": answers or [],
        "duration_ms": duration_ms,
        "paper_id": paper_id,
    }}


# ---------------------------------------------------------------------------
# 3. 真并发驱动（不重试、不串行化）
# ---------------------------------------------------------------------------

def fire_concurrent(base_url: str, specs: list[dict], *, path: str = "/api/attempts",
                    timeout: float = BATCH_TIMEOUT, barrier_timeout: float = 30.0) -> dict:
    """把 `specs` 里的请求**同时**打出去，返回 `{"results", "wall_ms", "workers"}`。

    每条请求一个线程 + 独立 `httpx.Client`（`httpx.Client` 不是线程安全的共享对象），
    全部线程先在 `threading.Barrier` 上对齐再一起出发 —— 否则线程创建本身就把请求
    错开了，量到的是「分批」而不是「并发」。

    单条失败（超时 / 连接错误 / 非 2xx）都**如实记进结果**，绝不重试：本任务要测的
    就是阻塞本身。
    """
    results: list[dict | None] = [None] * len(specs)
    barrier = threading.Barrier(len(specs)) if specs else None

    def worker(index: int, spec: dict) -> None:
        client = httpx_client(base_url, timeout)
        started = time.perf_counter()
        try:
            assert barrier is not None
            try:
                barrier.wait(timeout=barrier_timeout)
            except threading.BrokenBarrierError as exc:
                results[index] = {"index": index, "status": None, "elapsed_ms": 0.0,
                                  "error": f"起跑对齐失败：{type(exc).__name__}: {exc}"}
                return
            try:
                response = client.post(path, json=spec.get("json"), params=spec.get("params"))
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                results[index] = {
                    "index": index,
                    "status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "body": response.text[:600],
                }
            except Exception as exc:                       # noqa: BLE001 - 传输层任何失败都算证据
                results[index] = {
                    "index": index,
                    "status": None,
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        finally:
            client.close()

    threads = [threading.Thread(target=worker, args=(index, spec), name=f"perf-{index}")
               for index, spec in enumerate(specs)]
    wall_started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    return {"results": [item for item in results if item is not None],
            "wall_ms": wall_ms, "workers": len(specs)}


def httpx_client(base_url: str, timeout: float):
    """每条请求一个独立客户端（`httpx.Client` 不是线程安全的共享对象）。

    注意 `base_url` 必须显式传：不传的话 `client.post("/api/attempts")` 会以
    「缺少协议」直接抛 `UnsupportedProtocol`（第一次跑就是这么红的，
    40 条全部算「传输失败」——判据报得没错，是我的客户端造错了）。
    """
    import httpx

    return httpx.Client(base_url=base_url, timeout=timeout)


def fire_serial(base_url: str, specs: list[dict], *, path: str = "/api/attempts",
                timeout: float = BATCH_TIMEOUT) -> dict:
    """串行对照：同样的请求一条一条发（**只做对照**，不用它当并发证据）。"""
    results = []
    wall_started = time.perf_counter()
    with httpx_client(base_url, timeout) as client:
        for index, spec in enumerate(specs):
            started = time.perf_counter()
            try:
                response = client.post(path, json=spec.get("json"), params=spec.get("params"))
                results.append({"index": index, "status": response.status_code,
                                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                                "body": response.text[:600]})
            except Exception as exc:                       # noqa: BLE001
                results.append({"index": index, "status": None,
                                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                                "error": f"{type(exc).__name__}: {exc}"})
    return {"results": results, "wall_ms": (time.perf_counter() - wall_started) * 1000.0,
            "workers": 1}


# ---------------------------------------------------------------------------
# 4. 观测：时延统计（观测量，不是门禁）
# ---------------------------------------------------------------------------

def percentile(values: list[float], q: float) -> float:
    """最近秩法（nearest-rank）：`q=0.95` 取第 `ceil(0.95*n)` 小的值。

    不插值：样本只有几十个，插值会造出一个**没有任何一次请求真的经历过**的数字。
    """
    if not values:
        raise ValueError("percentile() 需要至少一个样本")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(q * len(ordered))))
    return ordered[rank - 1]


def latency_stats(results: list[dict], *, wall_ms: float | None = None) -> dict:
    """成功率 + status 分布 + p50/p95/max/min/mean（毫秒）。

    `ok` 只数 200/201；传输层失败的样本仍计入 `n`，但**不进时延分位数**（它没有
    服务端时延可言），这一点必须写在报告里，免得 p95 看起来比实际好。
    """
    arrived = [item for item in results if item.get("status") is not None]
    latencies = [float(item["elapsed_ms"]) for item in arrived]
    statuses = Counter(item["status"] for item in arrived)
    ok = sum(count for status, count in statuses.items() if status in (200, 201))
    stats = {
        "n": len(results),
        "arrived": len(arrived),
        "ok": ok,
        "success_rate": (ok / len(results)) if results else 0.0,
        "status_counts": {str(key): value for key, value in sorted(statuses.items())},
        "transport_errors": len(results) - len(arrived),
        "workers": None,
    }
    if latencies:
        stats.update({
            "min_ms": round(min(latencies), 2),
            "p50_ms": round(percentile(latencies, 0.50), 2),
            "p95_ms": round(percentile(latencies, 0.95), 2),
            "max_ms": round(max(latencies), 2),
            "mean_ms": round(sum(latencies) / len(latencies), 2),
        })
    else:
        stats.update({"min_ms": None, "p50_ms": None, "p95_ms": None,
                      "max_ms": None, "mean_ms": None})
    if wall_ms is not None:
        stats["total_wall_ms"] = round(wall_ms, 2)
    return stats


def summarise_batch(batch: dict) -> dict:
    """`fire_concurrent` 的返回 → 报告可直接引用的数字（含时延分位数）。"""
    stats = latency_stats(batch["results"], wall_ms=batch["wall_ms"])
    stats["workers"] = batch.get("workers")
    return stats


# ---------------------------------------------------------------------------
# 5. 纯函数判据（可以被喂改坏的库 → 证明会红）
# ---------------------------------------------------------------------------

def _excerpt(text: str | None, limit: int = 160) -> str:
    single = " ".join((text or "").split())
    return single[:limit] + ("…" if len(single) > limit else "")


def body_field(body: str | None, field: str) -> int | None:
    """从（可能被截断的）响应体里取一个**前置标量**字段。

    `AttemptResultOut` 的字段顺序是 `attempt_id, total, correct, score, items`，而
    `items` 有 40 条 —— `fire_concurrent` 把 body 截到 600 字符，所以整段
    `json.loads` 会失败。这里从最前面正则取那几个标量，够判据用。
    """
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(-?\d+)', body or "")
    return int(match.group(1)) if match else None


def submit_batch_problems(batch: dict, *, expect: tuple[int, ...] = (200, 201)) -> list[str]:
    """**这一批提交**的判据：零 5xx、零传输失败、状态码在契约允许集内。

    这就是 task-48 要求④里「同一条判据」——注入写锁之后它必须变红。
    """
    problems: list[str] = []
    for item in batch["results"]:
        index = int(item.get("index", 0)) + 1
        status = item.get("status")
        if status is None:
            problems.append(
                f"第 {index} 条请求没有拿到响应（{round(float(item.get('elapsed_ms', 0)))}ms）："
                f"{_excerpt(item.get('error'))}")
            continue
        if status >= 500:
            problems.append(f"第 {index} 条返回 {status}（5xx）：{_excerpt(item.get('body'))}")
        elif status not in expect:
            problems.append(
                f"第 {index} 条返回 {status}（期望 {expect}）：{_excerpt(item.get('body'))}")
    return problems


def duplicate_attempt_problems(rows: list[dict]) -> list[str]:
    """D76：`(student_id, attempt_uuid)` 必须恰好一条 —— 并发重放也不许破。"""
    problems: list[str] = []
    seen: dict[tuple, list[int]] = {}
    for row in rows:
        student_id = row.get("student_id")
        attempt_uuid = row.get("attempt_uuid")
        if not attempt_uuid:
            problems.append(f"attempt id={row.get('id')} 的 attempt_uuid 为空（{attempt_uuid!r}）")
            continue
        seen.setdefault((student_id, attempt_uuid), []).append(row.get("id"))
    for (student_id, attempt_uuid), ids in sorted(seen.items(), key=lambda item: str(item[0])):
        if len(ids) > 1:
            problems.append(
                f"(student_id={student_id}, attempt_uuid={attempt_uuid}) 落了 {len(ids)} 条"
                f"（D76 要求恰好 1 条）：attempt id={ids}")
    return problems


def item_integrity_problems(attempts: list[dict], items: list[dict]) -> list[str]:
    """并发下 `attempt` 与 `attempt_item` 必须成套：`total == 行数`，且没有半写行。"""
    problems: list[str] = []
    counts = Counter(row.get("attempt_id") for row in items)
    correct_seen = Counter(row.get("attempt_id") for row in items
                           if row.get("is_correct") == 1)
    known = {row.get("id") for row in attempts}
    for row in attempts:
        got = counts.get(row.get("id"), 0)
        if row.get("total") != got:
            problems.append(
                f"attempt id={row.get('id')}（uuid={row.get('attempt_uuid')}）："
                f"total={row.get('total')} ≠ attempt_item 行数={got}")
        if row.get("correct") != correct_seen.get(row.get("id"), 0):
            problems.append(
                f"attempt id={row.get('id')}：correct={row.get('correct')} ≠ "
                f"明细里 is_correct=1 的行数={correct_seen.get(row.get('id'), 0)}")
        correct = row.get("correct")
        total = row.get("total")
        if not isinstance(correct, int) or not isinstance(total, int) \
                or not 0 <= correct <= total:
            problems.append(
                f"attempt id={row.get('id')}：correct={correct!r} / total={total!r} 不成话")
    seqs = Counter()
    for row in items:
        seqs[(row.get("attempt_id"), row.get("seq"))] += 1
        if row.get("attempt_id") not in known:
            problems.append(
                f"attempt_item id={row.get('id')} 指向不存在的 attempt "
                f"{row.get('attempt_id')}（孤儿行）")
        payload = row.get("payload")
        if not payload:
            problems.append(f"attempt_item id={row.get('id')} 的 payload 为空（半写）")
        if row.get("is_correct") not in (0, 1):
            problems.append(
                f"attempt_item id={row.get('id')} 的 is_correct={row.get('is_correct')!r}"
                "（只能是 0/1）")
    for (attempt_id, seq), count in sorted(seqs.items(), key=lambda item: str(item[0])):
        if count > 1:
            problems.append(f"attempt {attempt_id} 的 seq={seq} 有 {count} 行（应恰好 1 行）")
    return problems


# ---------------------------------------------------------------------------
# 6. 直接读库（只读）/ 直接改库（只给负对照用）
# ---------------------------------------------------------------------------

def db_rows(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """只读打开（`mode=ro`）真库。压测判据**不写**产品侧的库。"""
    return readonly_rows(db_path, sql, params)


def db_write(db_path: str, sql: str, params: tuple = ()) -> int:
    """直接写库：**只给负对照用**（把世界改坏，证明判据会红）。"""
    connection = sqlite3.connect(db_path, timeout=10.0)
    try:
        cursor = connection.execute(sql, params)
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


def timed_db_write(db_path: str, sql: str, params: tuple = ()) -> dict:
    """`db_write` 的「如实记结果」版：写不进去（例如撞唯一索引）也返回而不是抛。

    负对照里经常要**故意**写一条本来就该被拒绝的记录，这时「被拒绝」本身就是证据
    （例：D76 的唯一索引 `uq_attempt_student_uuid` 会顶回重复成绩），不能让它把
    整条用例炸掉。
    """
    started = time.perf_counter()
    try:
        rowcount = db_write(db_path, sql, params)
        return {"outcome": "ok", "rowcount": rowcount,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1)}
    except sqlite3.Error as exc:
        return {"outcome": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1)}


def db_script(db_path: str, script: str) -> None:
    """跑一段多语句 SQL（**只给负对照用**；`execute` 一次只接一条语句）。"""
    connection = sqlite3.connect(db_path, timeout=10.0)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()


def remove_unique_constraint(db_path: str, table: str, constraint: str) -> dict:
    """拆掉一张表的**表级 UNIQUE 约束**（模拟「约束丢了」的世界）。

    为什么不能用 `DROP INDEX`：SQLAlchemy 建的是
    `CONSTRAINT uq_attempt_student_uuid UNIQUE (student_id, attempt_uuid)`，
    SQLite 把它实现成 `sqlite_autoindex_attempt_1` —— 自动索引 `DROP INDEX` 拆不掉
    （`IF EXISTS` 还会静默成功，于是负对照会以为拆了、其实没拆）。只能按原 DDL
    重建一张不带该子句的表，再把数据搬过去。
    """
    rows = db_rows(db_path, "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                   (table,))
    if not rows:
        return {"outcome": f"表 {table} 不存在"}
    ddl = rows[0]["sql"]
    if f"CONSTRAINT {constraint} UNIQUE" not in ddl:
        return {"outcome": f"DDL 里没有 CONSTRAINT {constraint} UNIQUE", "ddl": ddl}
    stripped = re.sub(r",\s*CONSTRAINT\s+" + re.escape(constraint) + r"\s+UNIQUE\s*\([^)]*\)",
                      "", ddl, count=1)
    temporary = f"{table}__task48_no_unique"
    replacement = stripped.replace(f"CREATE TABLE {table}",
                                   f"CREATE TABLE {temporary}", 1)
    db_script(db_path, "\n".join([
        "PRAGMA foreign_keys=OFF;",
        replacement + ";",
        f"INSERT INTO {temporary} SELECT * FROM {table};",
        f"DROP TABLE {table};",
        f"ALTER TABLE {temporary} RENAME TO {table};",
    ]))
    after = db_rows(db_path, "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,))[0]["sql"]
    return {"outcome": "ok", "before_had_constraint": True,
            "after_has_constraint": f"CONSTRAINT {constraint} UNIQUE" in after}


def attempt_rows(db_path: str) -> list[dict]:
    return db_rows(db_path, "SELECT id, student_id, attempt_uuid, paper_id, total, correct, "
                            "duration_ms, submitted_at FROM attempt ORDER BY id")


def item_rows(db_path: str) -> list[dict]:
    return db_rows(db_path, "SELECT id, attempt_id, seq, payload, is_correct "
                            "FROM attempt_item ORDER BY attempt_id, seq, id")


# ---------------------------------------------------------------------------
# 7. 注入：排它写锁（证明判据能红）+ 它自己的自证
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def exclusive_write_lock(db_path: str, *, timeout: float = 1.0):
    """在**另一个连接**上拿住 SQLite 排它写锁，模拟「有长事务占着写锁」。

    为什么这一招有效：产品没有 WAL、也没有显式 `busy_timeout`
    （`backend/app/db.py:53-65` 只有 `check_same_thread=False`），所以写锁等待上限
    就是 pysqlite 默认的 **5.0 秒**；排它锁一拿住，并发的写（以及回滚日志模式下
    连读）都会在 5 秒后 `database is locked`。

    进入时用一次真实写入确认锁已经拿住（`BEGIN EXCLUSIVE` 在回滚日志模式下是
    立刻加锁的，但不写一下就只是「声称」）。
    """
    connection = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("CREATE TABLE IF NOT EXISTS __task48_lock_probe (id INTEGER)")
        connection.execute("INSERT INTO __task48_lock_probe (id) VALUES (1)")
        yield connection
    finally:
        with contextlib.suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        connection.close()


def lock_is_held(db_path: str, *, timeout: float = 0.3) -> dict:
    """自证：此刻**别人**写不进去。返回 `{"held", "error"}`。

    没有这一步，「注入写锁」就只是一句声称：万一锁根本没拿住，那个「判据变红了」
    的实验其实什么都没证明（V9.2：负对照必须自证真的制造了失败条件）。
    """
    connection = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS __task48_lock_probe_2 (id INTEGER)")
        connection.execute("INSERT INTO __task48_lock_probe_2 (id) VALUES (1)")
        return {"held": False, "error": None}
    except sqlite3.Error as exc:
        return {"held": True, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        with contextlib.suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        connection.close()


# ---------------------------------------------------------------------------
# 8. 服务端日志（注入写锁时 `database is locked` 的原始出处）
# ---------------------------------------------------------------------------

#: Windows 匿名管道默认缓冲的量级（`subprocess.PIPE` 就是这个规模附近，实测会随负载浮动）；
#: 实测一条 uvicorn 的未处理异常 traceback 是 8697 字节 —— 比它大，所以**必须有人读**。
PIPE_BUFFER_HINT = 4096


class ServerLogDrain:
    """读走服务端输出，避免它**阻塞在写日志上**。

    task-50 之前：`UiServer` 用 `stdout=PIPE` 起 uvicorn 且从不读；uvicorn 把未处理异常的
    traceback（实测 8697 字节）写进那段管道，超过管道缓冲后服务进程就卡在 `write()` 里 ——
    请求永远不返回，客户端只看到 `ReadTimeout`，而 `terminate()` 之后管道里剩的那点数据
    会被系统丢掉、读到 0 字节。task-48 第一版注入判据就是这么被骗的：看起来「产品在锁下
    卡死 60 秒」，实际是**测试设施没收日志**。

    task-50 起根因已修：子进程输出**直接重定向到文件**（`_uvicorn_boot`）。文件不会满、
    `write()` 不会阻塞，`terminate()` 之后内容仍在。所以这个类现在是**按需读文件**；
    只有调用方仍然自己用管道起进程（`process.stdout` 不是 None）时，才退回原来的守护线程读法。
    """

    def __init__(self, server, *, name: str = "perf-log-drain"):
        process = getattr(server, "process", server)
        self._path = getattr(server, "server_log_path", None)
        self._stream = None if self._path else getattr(process, "stdout", None)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.text = ""
        self._thread = None
        if self._stream is not None:
            self._thread = threading.Thread(target=self._pump, name=name, daemon=True)
            self._thread.start()

    def _pump(self) -> None:
        if self._stream is None:
            return
        while not self._stop.is_set():
            try:
                line = self._stream.readline()
            except (OSError, ValueError):
                return
            if not line:
                return
            with self._lock:
                self.text += line

    def snapshot(self) -> str:
        if self._path is not None:
            return read_server_log(self._path)
        with self._lock:
            return self.text

    def stop(self, *, timeout: float = 5.0) -> str:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return self.snapshot()


def drain_server_log(server) -> str:
    """收掉服务进程并读回它的 stdout/stderr（uvicorn 的异常栈在这里）。

    task-50 起设施把子进程输出**直接写文件**：进程退出不会带走日志，所以这里是
    「先收进程、再读文件」。仍然用管道起进程的调用方退回原来的读法（那份数据只到进程
    退出为止，读不到就是真的没有 —— 这本身也是「没人读的管道会丢证据」的一部分）。
    """
    process = getattr(server, "process", None)
    path = getattr(server, "server_log_path", None)
    if process is None and path is None:
        return ""
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
    if path is not None:
        return read_server_log(path)
    stream = getattr(process, "stdout", None)
    if stream is None:
        return ""
    try:
        return stream.read() or ""
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# 9. 取证落盘
# ---------------------------------------------------------------------------

def write_evidence(name: str, payload) -> str:
    """写 `artifacts/<name>.json`（UTF-8 无 BOM，中文原样）。返回路径。"""
    os.makedirs(ARTIFACTS, exist_ok=True)
    path = os.path.join(ARTIFACTS, f"{name}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
    return path


def new_uuid() -> str:
    return uuid.uuid4().hex
