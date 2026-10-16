"""task-48：并发提交压测（T-M6-02 / NFR-4 后半句「数十名学生并发提交不阻塞」）。

## 这些判据在读什么

真机 `uvicorn app.main:app` + 磁盘 SQLite（`MATHGEN_DB` 指到本目录 `artifacts/`，
产品库 `backend/app/data/mathgen.db` 不参与）：

1. **零 5xx**：50 条**同时出发**的 `POST /api/attempts` 必须全部 200/201。
2. **D76 在并发下不破**：每个 `(student_id, attempt_uuid)` 恰好一条 `attempt`；
   同一个 uuid 的 8 线程并发重放仍只有一条（期望 1×201 + 7×200，响应里的
   `attempt_id` 必须都指向库里那一条）。
3. **落库成套**：`attempt.total == attempt_item 行数`、`attempt.correct ==
   明细里 is_correct=1 的行数`、没有空 payload / 非法 `is_correct` / 重复 seq / 孤儿行。
4. **测量窗口有效**：开测前后 `backend/app/**` 的聚合哈希与最新 mtime 必须一致 ——
   否则这次测量的数字**无效**（Lead 的硬要求：不要在别人半写状态下出数字）。

`TestWhatTheInjectionProves` 是 V9：同一套判据在**人为破坏**下必须变红
（排它写锁 → 5xx；改坏的库 → 唯一性/成套性判据红；伪造指纹 → 窗口判据红）。

## 它不能发现什么（同时写进 `REPORT-task-48.md`）

* 它测「**会不会阻塞**」，不测「够不够快」：契约没有为单次提交定 SLO，所以
  p50/p95/max 只作为**观测量**落进证据，这里**不设**一个自己编的阈值假装门禁。
* 数字来自**本机回环 + 单文件 SQLite**，不是教室 WiFi 多机；跨机网络排队不在覆盖内。
* 它**不重试**：`database is locked` 真出现就红，不会被重试洗成「不阻塞」。
"""
from __future__ import annotations

import os
import shutil
import unittest
from collections import Counter

from tests.perf import _perf_support as perf

#: 注入写锁时打出去的并发数。**故意小一点**：uvicorn 的异常栈写在 PIPE 里
#: （`UiServer` 从不读它），栈太大有把管道写满、把服务卡住的风险。
INJECTION_REQUESTS = 4

WORLD: dict = {}


def _numbers(stats: dict) -> str:
    return (f"共 {stats['n']} 条 / 成功 {stats['ok']} / 成功率 {stats['success_rate']:.0%} / "
            f"wall {stats.get('total_wall_ms')}ms / p50 {stats.get('p50_ms')}ms / "
            f"p95 {stats.get('p95_ms')}ms / max {stats.get('max_ms')}ms / "
            f"状态分布 {stats['status_counts']}")


def _specs(*, student_ids: list[int], paper_id: int, questions: dict) -> list[dict]:
    """每个学生一条提交：答对题数按序号轮转，写放量全部一样（每题一行明细）。"""
    return [perf.submit_spec(student_id=student_id, paper_id=paper_id,
                             attempt_uuid=perf.new_uuid(),
                             answers=perf.answers_for(
                                 questions, correct_count=index % (len(questions) + 1)))
            for index, student_id in enumerate(student_ids)]


def setUpModule() -> None:
    os.makedirs(perf.ARTIFACTS, exist_ok=True)
    db_path = os.path.join(perf.ARTIFACTS, "task48-world.db")
    for suffix in ("", "-journal", "-wal", "-shm"):
        stale = db_path + suffix
        if os.path.exists(stale):
            os.remove(stale)
    server = perf.PerfServer(db_path=db_path)
    server.__enter__()
    before = perf.source_fingerprint()
    paper_id, questions = server.published_paper(
        title="task-48 并发压测卷", seed=20261016, count=perf.PAPER_QUESTIONS)
    WORLD.update({
        "server": server,
        "db_path": db_path,
        "before": before,
        "paper_id": paper_id,
        "questions": questions,
        "concurrent_students": server.make_students(
            name_prefix="并发学生", no_prefix="2026PERFC", count=perf.CONCURRENT_STUDENTS),
        "serial_students": server.make_students(
            name_prefix="串行学生", no_prefix="2026PERFS", count=perf.SERIAL_STUDENTS),
        "replay_students": server.make_students(
            name_prefix="重放学生", no_prefix="2026PERFR", count=1),
    })


def tearDownModule() -> None:
    after = perf.source_fingerprint()
    window = {
        "kind": "测量窗口有效性：开测前 / 模块结束后各拍一次 backend/app/** 指纹",
        "before": WORLD.get("before"),
        "after": after,
        "problems": perf.fingerprint_diff(WORLD.get("before", after), after),
    }
    WORLD["window"] = window
    perf.write_evidence("task48-measurement-window", window)
    server = WORLD.pop("server", None)
    if server is not None:
        server.__exit__(None, None, None)


class TestConcurrentSubmissions(unittest.TestCase):
    """真机服务 + 真库文件上的一次 50 并发提交（模块级世界，与注入对照隔离）。"""

    def setUp(self) -> None:
        self.server = WORLD["server"]

    def test_01_fifty_concurrent_submissions_never_five_xx(self):
        specs = _specs(student_ids=WORLD["concurrent_students"],
                       paper_id=WORLD["paper_id"], questions=WORLD["questions"])
        batch = perf.fire_concurrent(self.server.base_url, specs)
        problems = perf.submit_batch_problems(batch)
        stats = perf.summarise_batch(batch)
        WORLD["concurrent"] = {"specs": specs, "batch": batch, "problems": problems,
                               "stats": stats}
        perf.write_evidence("task48-concurrent-batch", {
            "kind": "50 条同时出发的 POST /api/attempts"
                    "（每线程独立 httpx.Client，threading.Barrier 对齐起跑）",
            "stats": stats,
            "problems": problems,
            "responses": [{"index": item.get("index"), "status": item.get("status"),
                           "elapsed_ms": round(float(item.get("elapsed_ms", 0.0)), 2),
                           "body": (item.get("body") or item.get("error") or "")[:300]}
                          for item in batch["results"]],
        })
        self.assertEqual([], problems,
                         f"并发提交有失败（{len(problems)} 条）：{problems[:5]}\n{_numbers(stats)}")
        self.assertEqual(perf.CONCURRENT_STUDENTS, stats["ok"], _numbers(stats))
        self.assertEqual(1.0, stats["success_rate"], _numbers(stats))

    def test_02_every_submission_landed_exactly_one_row(self):
        specs = WORLD["concurrent"]["specs"]
        rows = perf.attempt_rows(WORLD["db_path"])
        sent = {spec["json"]["attempt_uuid"] for spec in specs}
        self.assertEqual(perf.CONCURRENT_STUDENTS, len(sent), "夹具的 uuid 自己就不唯一")
        landed = {row["attempt_uuid"] for row in rows}
        missing = sorted(sent - landed)
        self.assertEqual([], missing, f"有 {len(missing)} 条提交没落库：{missing[:5]}")
        duplicates = perf.duplicate_attempt_problems(rows)
        perf.write_evidence("task48-landed-rows", {
            "sent": len(sent), "rows_in_db": len(rows), "missing": missing,
            "duplicate_problems": duplicates,
        })
        self.assertEqual([], duplicates, f"D76 被并发破了：{duplicates[:5]}")
        self.assertEqual(perf.CONCURRENT_STUDENTS, len(rows),
                         f"库里的 attempt 行数不对：{len(rows)}")

    def test_03_a_replayed_uuid_under_concurrency_still_lands_one_row(self):
        student_id = WORLD["replay_students"][0]
        attempt_uuid = perf.new_uuid()
        answers = perf.answers_for(WORLD["questions"], correct_count=17)
        specs = [perf.submit_spec(student_id=student_id, paper_id=WORLD["paper_id"],
                                  attempt_uuid=attempt_uuid, answers=answers)
                 for _ in range(perf.REPLAY_THREADS)]
        batch = perf.fire_concurrent(self.server.base_url, specs)
        problems = perf.submit_batch_problems(batch)
        statuses = Counter(item["status"] for item in batch["results"])
        attempt_ids = {perf.body_field(item.get("body"), "attempt_id")
                       for item in batch["results"]}
        rows = [row for row in perf.attempt_rows(WORLD["db_path"])
                if row["attempt_uuid"] == attempt_uuid]
        perf.write_evidence("task48-replay-batch", {
            "kind": f"同一个 (student_id, attempt_uuid) 的 {perf.REPLAY_THREADS} 线程并发重放",
            "status_counts": {str(key): value for key, value in sorted(statuses.items())},
            "rows_in_db": len(rows),
            "attempt_ids_in_responses": sorted(str(item) for item in attempt_ids),
            "stats": perf.summarise_batch(batch),
            "problems": problems,
        })
        self.assertEqual([], problems, f"并发重放出现失败：{problems[:5]}")
        self.assertEqual(1, len(rows), f"同一 uuid 落了 {len(rows)} 条（D76 要求恰好 1 条）")
        self.assertEqual(1, statuses.get(201, 0),
                         f"应当恰好一次新建（201），实际 {dict(statuses)}")
        self.assertEqual(1, len(attempt_ids), f"响应里的 attempt_id 不一致：{attempt_ids}")
        self.assertEqual({rows[0]["id"]}, attempt_ids,
                         "响应里的 attempt_id 与库里那条不是同一个")

    def test_04_every_attempt_has_exactly_its_items(self):
        attempts = perf.attempt_rows(WORLD["db_path"])
        items = perf.item_rows(WORLD["db_path"])
        problems = perf.item_integrity_problems(attempts, items)
        perf.write_evidence("task48-item-integrity", {
            "attempts": len(attempts), "attempt_items": len(items),
            "expected_items": sum(int(row["total"]) for row in attempts),
            "problems": problems,
        })
        self.assertEqual([], problems, f"落库不成套（{len(problems)} 条）：{problems[:5]}")
        self.assertGreaterEqual(len(attempts), perf.CONCURRENT_STUDENTS + 1,
                                "夹具没造够 attempt（并发批 + 重放那条）")
        self.assertEqual(sum(int(row["total"]) for row in attempts), len(items),
                         "明细总行数与 total 之和不一致")

    def test_05_the_api_and_the_database_tell_the_same_story(self):
        rows = {row["student_id"]: row for row in perf.attempt_rows(WORLD["db_path"])}
        sample = [rows[student_id] for student_id in WORLD["concurrent_students"][:5]]
        problems: list[str] = []
        observed = []
        for row in sample:
            detail = self.server.get_attempt(row["id"])
            items = detail.get("items") or []
            observed.append({"attempt_id": row["id"], "detail_total": detail.get("total"),
                             "detail_correct": detail.get("correct"),
                             "detail_score": detail.get("score"), "items": len(items)})
            if detail.get("total") != len(items):
                problems.append(f"attempt {row['id']}：detail.total={detail.get('total')} "
                                f"≠ len(items)={len(items)}")
            if detail.get("total") != row["total"]:
                problems.append(f"attempt {row['id']}：detail.total={detail.get('total')} "
                                f"≠ 库里 total={row['total']}")
            if detail.get("correct") != row["correct"]:
                problems.append(f"attempt {row['id']}：detail.correct={detail.get('correct')} "
                                f"≠ 库里 correct={row['correct']}")
        perf.write_evidence("task48-api-vs-database", {"sample": observed, "problems": problems})
        self.assertEqual([], problems, f"接口与库对不上：{problems[:5]}")

    def test_06_the_serial_baseline_says_the_same_thing(self):
        specs = _specs(student_ids=WORLD["serial_students"],
                       paper_id=WORLD["paper_id"], questions=WORLD["questions"])
        batch = perf.fire_serial(self.server.base_url, specs)
        problems = perf.submit_batch_problems(batch)
        stats = perf.summarise_batch(batch)
        stats["workers"] = 1
        concurrent = WORLD["concurrent"]["stats"]
        WORLD["serial"] = {"stats": stats, "problems": problems}
        perf.write_evidence("task48-serial-baseline", {
            "kind": "同样的 50 条请求，一条一条发（对照，不是并发证据）",
            "stats": stats,
            "problems": problems,
            "concurrent_stats": concurrent,
            "wall_ratio_concurrent_over_serial": (
                round(concurrent.get("total_wall_ms", 0.0) / batch["wall_ms"], 3)
                if batch["wall_ms"] else None),
            "note": "只回答「并发有没有比串行更糟」；乘积没有阈值，数字进报告由人判读",
        })
        self.assertEqual([], problems,
                         f"串行对照也失败（说明问题不在并发）：{problems[:5]}")

    def test_zz_the_product_source_did_not_change_during_the_measurement(self):
        before = WORLD["before"]
        now = perf.source_fingerprint()
        problems = perf.fingerprint_diff(before, now)
        perf.write_evidence("task48-measurement-window-check", {
            "before": before, "now": now, "problems": problems,
            "verdict": "有效" if not problems else "无效，必须重做",
        })
        self.assertEqual([], problems,
                         "测量窗口内 backend/app/** 被改过 → 本次数字无效，必须重做：" + str(problems))


class TestWhatTheInjectionProves(unittest.TestCase):
    """V9：同一套判据必须能被**人为破坏**弄红。用**自己的**服务与库，不污染主世界。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = os.path.join(perf.ARTIFACTS, "task48-injection.db")
        for suffix in ("", "-journal", "-wal", "-shm"):
            stale = cls.db_path + suffix
            if os.path.exists(stale):
                os.remove(stale)
        cls.server = perf.PerfServer(db_path=cls.db_path)
        cls.server.__enter__()
        cls.paper_id, cls.questions = cls.server.published_paper(
            title="task-48 注入对照卷", seed=20261017, count=perf.PAPER_QUESTIONS)
        cls.students = cls.server.make_students(
            name_prefix="注入学生", no_prefix="2026PERFI", count=INJECTION_REQUESTS)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__(None, None, None)

    def test_01_an_exclusive_write_lock_makes_the_same_criterion_red(self):
        specs = [perf.submit_spec(student_id=student_id, paper_id=self.paper_id,
                                  attempt_uuid=perf.new_uuid(),
                                  answers=perf.answers_for(self.questions, correct_count=3))
                 for student_id in self.students]
        # 必须先把服务端日志抽走：uvicorn 的 traceback（实测 8697 字节）写进没人读的
        # `stdout=PIPE`（Windows 默认 4KB 缓冲）会把服务**堵在写日志上**，那时客户端
        # 只看到 ReadTimeout —— 第一版就是被这个假象骗了（见 `ServerLogDrain` docstring）。
        drain = perf.ServerLogDrain(self.server)
        with perf.exclusive_write_lock(self.db_path):
            held = perf.lock_is_held(self.db_path)
            self.assertTrue(held["held"],
                            f"注入失败：锁没拿住（{held}）—— 这个实验什么都证明不了")
            batch = perf.fire_concurrent(self.server.base_url, specs, timeout=60.0)
        log = drain.stop()
        # 锁一释放就能读库了（`EXCLUSIVE` 连读都不放，锁内读库只会再等 7.4 秒）。
        # 此刻所有请求都已经拿到 500，所以这个数字就是「锁下没有任何提交落库」。
        rows_landed = perf.attempt_rows(self.db_path)
        problems = perf.submit_batch_problems(batch)
        stats = perf.summarise_batch(batch)
        log_path = os.path.join(perf.ARTIFACTS, "task48-lock-server.log")
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(log)
        perf.write_evidence("task48-injected-write-lock", {
            "kind": "另一个连接 `BEGIN EXCLUSIVE` 拿住写锁，然后打 "
                    f"{INJECTION_REQUESTS} 条并发提交（同一条 submit_batch_problems 判据）",
            "lock_self_proof": held,
            "stats": stats,
            "problems": problems,
            "rows_landed_while_locked": len(rows_landed),
            "server_log_bytes": len(log.encode("utf-8")),
            "server_log_mentions": [token for token in ("database is locked", "OperationalError",
                                                        "ensure_attempt_schema")
                                    if token in log],
            "server_log_path": log_path,
            "note": "产线没有 WAL、也没有显式 busy_timeout（backend/app/db.py:53-65）"
                    "→ 写锁等待上限就是 pysqlite 默认的 5.0 秒（实测忙等回调粒度让它"
                    "变成 7.4~7.5 秒），到点后 submit 路径抛 `database is locked` → 500。",
        })
        self.assertNotEqual([], problems,
                            "注入写锁之后判据竟然还是绿的 —— 判据没压到点上")
        self.assertTrue(any("5xx" in problem for problem in problems),
                        f"变红了，但红的不是「零 5xx」这一条：{problems[:3]}")
        self.assertEqual([], rows_landed,
                         "锁被持有期间竟然有提交落库了 —— 这个实验的「阻塞」前提不成立")
        self.assertIn("database is locked", log,
                      "服务端日志里没有 `database is locked` → 失败原因不是写锁，"
                      f"证据不足（日志 {len(log)} 字节，命中 "
                      f"{[t for t in ('database is locked', 'OperationalError') if t in log]}）")

    def test_02_a_doctored_database_is_caught_by_the_same_checkers(self):
        source = WORLD.get("db_path")
        self.assertIsNotNone(source, "主世界没起来，改坏世界的前提不成立")
        doctored = os.path.join(perf.ARTIFACTS, "task48-doctored.db")
        for suffix in ("", "-journal", "-wal", "-shm"):
            stale = doctored + suffix
            if os.path.exists(stale):
                os.remove(stale)
        shutil.copyfile(source, doctored)

        attempts = perf.attempt_rows(doctored)
        items = perf.item_rows(doctored)
        victim = attempts[0]
        its_items = [row for row in items if row["attempt_id"] == victim["id"]]
        self.assertGreaterEqual(len(its_items), 4,
                                "夹具前提：victim 那条 attempt 至少要有 4 行明细可供分别改坏")
        dropped, blanked, badflag = its_items[0], its_items[1], its_items[2]
        observations: dict[str, list[str]] = {}
        witnesses: dict = {}

        # 真实库里 `uq_attempt_student_uuid` 是**表级 UNIQUE 约束**（SQLite 实现成
        # `sqlite_autoindex_attempt_1`，`DROP INDEX` 拆不掉）：直接插重复行会被它用
        # `UNIQUE constraint failed: attempt.student_id, attempt.attempt_uuid` 顶回来
        # —— 第一次跑就是这么红的。**这本身是正面发现**：D76 不只靠 SELECT 去重，
        # 库里还有一道真约束。所以先在真形状上证明它拒绝，再把约束拆掉（按原 DDL
        # 重建表）模拟「约束丢了」的世界，把判据逼红。
        witnesses["real_db_refused_duplicate"] = perf.timed_db_write(
            doctored,
            "INSERT INTO attempt (student_id, attempt_uuid, paper_id, total, correct, "
            "duration_ms, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (victim["student_id"], victim["attempt_uuid"], victim["paper_id"],
             victim["total"], victim["correct"], victim["duration_ms"],
             victim["submitted_at"]))
        witnesses["constraint_removed"] = perf.remove_unique_constraint(
            doctored, "attempt", "uq_attempt_student_uuid")
        perf.db_write(
            doctored,
            "INSERT INTO attempt (student_id, attempt_uuid, paper_id, total, correct, "
            "duration_ms, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (victim["student_id"], victim["attempt_uuid"], victim["paper_id"],
             victim["total"], victim["correct"], victim["duration_ms"],
             victim["submitted_at"]))
        observations["duplicate_uuid"] = perf.duplicate_attempt_problems(
            perf.attempt_rows(doctored))

        perf.db_write(doctored, "DELETE FROM attempt_item WHERE id=?", (dropped["id"],))
        observations["dropped_item"] = perf.item_integrity_problems(
            perf.attempt_rows(doctored), perf.item_rows(doctored))

        perf.db_write(doctored, "UPDATE attempt_item SET payload='' WHERE id=?",
                      (blanked["id"],))
        observations["blank_payload"] = perf.item_integrity_problems(
            perf.attempt_rows(doctored), perf.item_rows(doctored))

        perf.db_write(doctored, "UPDATE attempt_item SET is_correct=7 WHERE id=?",
                      (badflag["id"],))
        observations["bad_is_correct"] = perf.item_integrity_problems(
            perf.attempt_rows(doctored), perf.item_rows(doctored))

        perf.db_write(doctored, "UPDATE attempt SET correct=999 WHERE id=?", (victim["id"],))
        observations["wrong_correct"] = perf.item_integrity_problems(
            perf.attempt_rows(doctored), perf.item_rows(doctored))

        perf.db_write(
            doctored,
            "INSERT INTO attempt_item (attempt_id, seq, payload, student_answer, is_correct) "
            "VALUES (999999, 1, '{\"answer\": \"1\"}', '1', 0)")
        observations["orphan_item"] = perf.item_integrity_problems(
            perf.attempt_rows(doctored), perf.item_rows(doctored))

        observations["batch_5xx"] = perf.submit_batch_problems({"results": [
            {"index": 3, "status": 500, "elapsed_ms": 12.0, "body": "Internal Server Error"}]})

        perf.write_evidence("task48-doctored-database", {
            "kind": "把主世界的库拷一份出来逐项改坏，喂同一批判据",
            "doctored_db": doctored,
            "witnesses": witnesses,
            "observations": observations,
        })

        self.assertIn("UNIQUE constraint failed",
                      witnesses["real_db_refused_duplicate"]["outcome"],
                      "真库竟然接受了重复的 (student_id, attempt_uuid) —— "
                      "D76 在库里没有约束，这条负对照的前提就变了")
        self.assertFalse(witnesses["constraint_removed"]["after_has_constraint"],
                         f"约束没拆掉，后面的重复行插不进去：{witnesses['constraint_removed']}")
        for name, problems in observations.items():
            self.assertNotEqual([], problems, f"把世界改坏之后判据 `{name}` 没红 —— 它是恒绿的")
        self.assertTrue(any(victim["attempt_uuid"] in problem
                            for problem in observations["duplicate_uuid"]),
                        f"重复判据没点名那对键：{observations['duplicate_uuid'][:3]}")
        self.assertTrue(any("payload 为空" in problem for problem in observations["blank_payload"]),
                        f"半写 payload 没被点名：{observations['blank_payload'][:3]}")
        self.assertTrue(any("is_correct=7" in problem for problem in observations["bad_is_correct"]),
                        f"非法 is_correct 没被点名：{observations['bad_is_correct'][:3]}")
        self.assertTrue(any("孤儿行" in problem for problem in observations["orphan_item"]),
                        f"孤儿明细行没被点名：{observations['orphan_item'][:3]}")
        self.assertTrue(any("5xx" in problem for problem in observations["batch_5xx"]),
                        f"5xx 判据没红：{observations['batch_5xx']}")

    def test_03_the_measurement_window_guard_notices_a_change(self):
        before = WORLD["before"]
        changed = dict(before)
        changed["sha256"] = "0" * 64
        changed["sha256_16"] = "0000000000000000"
        changed["files"] = int(before["files"]) + 1
        changed["newest_mtime"] = float(before["newest_mtime"]) + 60.0
        problems = perf.fingerprint_diff(before, changed)
        perf.write_evidence("task48-window-guard-negative-control", {
            "kind": "伪造一份「窗口内被改过」的指纹，喂指纹判据",
            "before": before, "changed": changed, "problems": problems,
        })
        self.assertEqual([], perf.fingerprint_diff(before, dict(before)),
                         "同一份指纹不该报差异（否则判据恒红）")
        self.assertEqual(3, len(problems),
                         f"指纹判据没把三处变化都报出来：{problems}")
