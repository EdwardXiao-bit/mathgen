# task-48 报告：并发提交压测（T-M6-02 / NFR-4 后半句）

## 0. 一屏结论

| 问题 | 答案 |
| --- | --- |
| 任务 | T-M6-02：NFR-4 后半句「局域网内**数十名学生并发提交不阻塞**」 |
| 结论 | **通过（并有两条必须上报的发现，见 §5）** |
| 判据条数 | `backend/tests/perf/test_concurrent_submit.py` **10 条**（正向 7 + 注入/负对照 3） |
| 最近一次运行 | `Ran 10 tests in 37.539s` / `OK` / exit 0（`artifacts/task48-run5.log`，**50 并发口径**） |
| 50 条同时提交 | 50/50 落库、`201` 全数、**零 5xx、零传输失败**、p50 1119.12ms、p95 1810.09ms、max 1895.02ms、整批墙钟 **2167.54ms** |
| 同一批串行对照 | p50 27.13ms、p95 49.26ms、墙钟 1630.54ms；**并发/串行墙钟比 1.329** |
| 测量窗口是否有效 | **有效**：`backend/app/**` 前后指纹逐字节相同（60 个文件、`ebc13c1398f27cad`、newest_mtime 1792110568.94679） |
| 判据能红吗 | 能，三条独立直证见 §4（写锁注入 4×500；改坏库 7 组观测全红；指纹负对照 3 条） |
| 它不能发现什么 | 见 §7（单突发 / 同机回环 / 无 SLO 阈值 / 不覆盖前端 / 不是容量结论） |

## 1. 由来

- 派单：Lead `team-message`（`m04836`）第 ② 项，board 卡 **task-48**（`T-M6-02 并发提交压测（数十名学生，NFR-4）`），写域 `backend/tests/perf/**`。
- 派单原话里的方向盘：**「你的任务是测出真相，不是把它压绿」**；`database is locked` 真遇到就如实报（日志 + 复现 + 曲线），**不要**重试到过为止把它藏起来；且**必须证明判据能红**。
- 追加要求（Lead `m05063`）三条，§3/§4/§5 逐条对应：
  1. 测量窗口内 `backend/app/**` 必须稳定：开测前后各拍一次聚合哈希与最新 mtime，不同就把这次测量**标为无效并重做**。
  2. 「先证明判据能红」同样适用：除正向数字外必须给能红的对照。
  3. `database is locked` 原样上报，不重试抹平。

## 2. 判据读什么

文件（均在写域 `backend/tests/perf/**`）：

| 文件 | 行数 | 作用 |
| --- | --- | --- |
| `__init__.py` | 5 | 包说明 |
| `_perf_support.py` | 673 | 判据设施：真并发驱动 + 真机取数口子 + 纯函数判据 + 负对照工具 |
| `test_concurrent_submit.py` | 435 | 10 条用例 |
| `artifacts/task48_lock_probe{,2,3}.py` | 199 / 202 / 163 | 写锁机制的三个探针（可重跑，留仓库） |
| `artifacts/task48_schema_dump.py` | 14 | 打印 `attempt`/`attempt_item` 的 DDL 与索引 |

两个类：

- `TestConcurrentSubmissions`（7 条，正向）：`test_01_fifty_concurrent_submissions_never_five_xx`、`test_02_every_submission_landed_exactly_one_row`、`test_03_a_replayed_uuid_under_concurrency_still_lands_one_row`、`test_04_every_attempt_has_exactly_its_items`、`test_05_the_api_and_the_database_tell_the_same_story`、`test_06_the_serial_baseline_says_the_same_thing`、`test_zz_the_product_source_did_not_change_during_the_measurement`。
- `TestWhatTheInjectionProves`（3 条，能红直证）：`test_01_an_exclusive_write_lock_makes_the_same_criterion_red`、`test_02_a_doctored_database_is_caught_by_the_same_checkers`、`test_03_the_measurement_window_guard_notices_a_change`。

判据本身是**纯函数**，因此能被喂一份改坏的库/伪造的指纹（§4）——这是能红的前提：

- `submit_batch_problems(batch, *, expect=(200, 201))`：传输失败点名、`>= 500` 点名、状态不在 `expect` 点名。**写锁注入用的就是这一条**，不是另写一套。
- `duplicate_attempt_problems(rows)`：D76 的 `(student_id, attempt_uuid)` 恰好一条。
- `item_integrity_problems(attempts, items)`：`total == 明细行数`、`correct == is_correct=1 行数`、`correct/total` 成话、payload 非空、`is_correct ∈ {0,1}`、无重复 `(attempt_id, seq)`、无孤儿行。
- `fingerprint_diff(before, after)`：文件数 / 聚合哈希 / 最新 mtime 三处。

## 3. 数字（原始输出见 §3.1 文件）

负载形态：**50 名学生各提交一次**（`docs/06-测试方案.md` §10 写的验收目标就是「50 名学生并发提交」；50 题卷，每线程一条独立 `httpx.Client`、`threading.Barrier` 对齐起跑），对照是同样 50 条**串行**发。

| 组 | n | 成功 | 状态 | p50 | p95 | max | 墙钟 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 50 并发 | 50 | 50 | 201×50 | 1119.12ms | 1810.09ms | 1895.02ms | **2167.54ms** |
| 50 串行 | 50 | 50 | 201×50 | 27.13ms | 49.26ms | 53.38ms | 1630.54ms |
| 8 线程重放同一 uuid | 8 | 8 | 201×1 + 200×7 | 241.95ms | 362.82ms | 362.82ms | 457.42ms |

- **并发/串行墙钟比 1.329**：50 条同时打到 SQLite 单文件上，整批只比串行多 0.54 秒，没有出现指数级退化或阻塞。
- **重放语义**（D76）：同一个 `(student_id, attempt_uuid)` 的 8 条并发重放 → 恰好 1 条落库、1 个 `201` + 7 个 `200`、8 条响应里的 `attempt_id` 全等且等于库里那一条。
- **落库完整性**：发出 50 条 → 库里 50 行、`missing: []`、重复问题 `[]`；51 条成绩 / 2040 条明细全部 `total == 明细行数`（51×40 = 2040）。
- **API ↔ 库对账**：抽 5 条取 `GET /api/attempts/{id}`，`detail_total/detail_correct/detail_score` 与库一致、明细条数一致。
- **故意不设阈值**：契约里没有「单次提交 ≤ X 毫秒」的 SLO，所以本任务**不自己编一个阈值**假装门禁；判据只断「零 5xx + 恰好一次落库 + 账目自洽」，延迟数字进报告由人判读。
- **口径变更留痕**：run1–run4 是 40 并发（当时的 `CONCURRENT_STUDENTS = 40`），为使证据与 `docs/06` §10 的「50 名学生」对齐改成 50 后复跑（run5）。40 并发那一轮的数字（p50 965.6ms / p95 1566.82ms / 墙钟 1996.38ms / 比 1.316）也全绿，与 50 并发同向。

### 3.1 原始输出文件

`artifacts/task48-concurrent-batch.json`、`task48-serial-baseline.json`、`task48-landed-rows.json`、`task48-replay-batch.json`、`task48-item-integrity.json`、`task48-api-vs-database.json`、`task48-measurement-window.json`、`task48-measurement-window-check.json`。

## 4. 能红的直证（V9：先证明判据会红）

### 4.1 写锁注入：同一条判据变红

另一个连接 `BEGIN EXCLUSIVE` 拿住写锁（自证：注入期间别人确实写不进去，`lock_self_proof = {"held": true, "error": "OperationalError: database is locked"}`），锁内打 4 条并发提交：

- `stats = {n: 4, ok: 0, status_counts: {"500": 4}, p50: 7546.08ms, max: 7552.08ms, total_wall_ms: 7658.6}`
- `problems = ["第 1 条返回 500（5xx）…", "第 2 条…", "第 3 条…", "第 4 条…"]`
- `rows_landed_while_locked = 0`
- 服务端日志 **34788 字节**，命中 `database is locked` / `OperationalError` / `ensure_attempt_schema`（`artifacts/task48-lock-server.log`）
- 证据 `artifacts/task48-injected-write-lock.json`

→ 判据在一个真实被阻塞的世界里**红了**，而且红的是「零 5xx」这一条，不是别的偶然原因。

### 4.2 改坏库：同一批判据逐项点名

把主世界的库拷一份，用 `witnesses` 先证明前提、再逐项改坏，喂同一批纯函数判据（证据 `artifacts/task48-doctored-database.json`）：

- `witnesses.real_db_refused_duplicate` = `IntegrityError: UNIQUE constraint failed: attempt.student_id, attempt.attempt_uuid`（2.3ms）——**D76 在库里有一道真约束**，手写 INSERT 被它顶回。
- `witnesses.constraint_removed` = `{"outcome": "ok", "before_had_constraint": true, "after_has_constraint": false}`——把表级 `UNIQUE` 拆掉后才插得进重复行。
- 七组观测全部非空并具名：`duplicate_uuid`（点名那对键与两个 id）、`dropped_item`、`blank_payload`（「payload 为空（半写）」）、`bad_is_correct`（「is_correct=7（只能是 0/1）」）、`wrong_correct`（「correct=999 / total=40 不成话」）、`orphan_item`（「孤儿行」）、`batch_5xx`。

### 4.3 指纹负对照

伪造一份「窗口内被改过」的指纹，`fingerprint_diff` 报出 3 条：文件数 60→61、聚合哈希 `ebc13c1398f27cad → 0000000000000000`、最新 mtime 变化；同时断言「同一份指纹比自己」返回 `[]`（否则判据恒红）。证据 `artifacts/task48-window-guard-negative-control.json`。

## 5. 过程中查清的两个真相（必须上报）

### 5.1 真相一：`database is locked` 下的「卡死 60 秒 + 日志为空」是**测试设施的假象**

run2 的现场：客户端只看到 `ReadTimeout`（60019 / 60024 / 67446ms），服务端日志 **0 字节**，看起来像「产品在锁下无限等待」。

三个探针逐层排除后（引擎层、超时参数、数据库层都已排除），`task48_lock_probe3.py` 自己起 uvicorn 并**把 stdout/stderr 落成文件**（不是 `PIPE`）跑出决定性结果：

- 请求在 **7461.5ms** 拿到 **HTTP 500 / `Internal Server Error`**，traceback 落在文件里（**8697 字节**）；
- 时间线：`t=3s/6s` 时客户端还活着、日志 0 字节；`t=9s` 时客户端已结束、日志 8697 字节。

真因：`backend/tests/_uvicorn_boot.py` 用 `Popen(..., stdout=PIPE, stderr=STDOUT)` 起 uvicorn，而 `UiServer`（`backend/tests/e2e_m4/_harness.py:357-436`）**从不读这个管道**。Windows 匿名管道默认缓冲约 4KB，而一条 uvicorn 未处理异常的 traceback 是 8697 字节 → 服务进程**阻塞在写日志上**，请求永远不返回；`terminate()` 之后管道里那点数据被系统丢掉，`drain_server_log()` 读到 0 字节。

**影响范围跨任务**：任何用 `UiServer`/`_uvicorn_boot` 的套件，只要服务端产生一条足够大的异常输出，「服务端报错」在观察者眼里就变成「客户端超时 + 日志为空」——M5 的 404/422 用例没踩到是因为那些响应由 FastAPI 正常返回、不产生 traceback。本任务在压测设施里用 `ServerLogDrain`（守护线程持续 `readline()`）绕开，但**根因在别人的写域**（`backend/tests/_uvicorn_boot.py`、`backend/tests/e2e_m4/_harness.py`），按派单「不得碰 `backend/app/**` 与 `frontend/**`」以外的原因我也没有动它——**留给你决定是否立案**。

### 5.2 真相二：长写锁下产品在 ≈7.4~7.5 秒返回 500

产品侧可复现行为（与 NFR-4 直接相关）：

- `backend/app/db.py:53-65` 建的引擎**没有 WAL、没有显式 `busy_timeout`** → 写锁等待上限就是 pysqlite 默认 5.0 秒（实测忙等回调粒度让它变成 **7.4~7.5 秒**）。
- 到点后提交路径抛 `OperationalError: database is locked` → **HTTP 500**，**不排队等待、不用重试掩盖**，0 行落库。
- 提交路径上**第一个撞锁的语句是表反射**：`backend/app/routers/attempts.py:53 submit_attempt` → `backend/app/services/attempt_service.py:141 ensure_attempt_schema(db)` → `:101 inspector.get_table_names()`（SQLAlchemy `get_table_names`）。`attempt_service.py:67 _ENSURED: weakref.WeakSet` 只在**本进程首条提交成功之后**才把 engine 记进去，所以新起的服务第一条提交必走 `inspect()`。
- 实测（探针 1）：`timeout=0.5` 时 1109.7ms / 1125.9ms 报错；`timeout=5.0` 时 7445.8ms / 7539.1ms；产品引擎 `SELECT` 7436.4ms 报错；锁一释放同一条请求 46.2ms 返回 `201` 并落库 1 行。

判读（写进报告供你决策，不属于本任务范围）：40 名学生**同一瞬间**提交时写锁只被占住几十毫秒（§3 的 40 并发全绿即是证据），所以这个 7.5 秒不是日常路径；它是「有一个长期持锁的写者」时的降级行为——表现为**快速失败（500）而不是排队**，学生端会看到提交失败。

## 6. 判据自身的缺陷（自查清单）

1. **run1 全红**：`httpx_client(timeout)` 忘了传 `base_url` → 那一轮 40 条全算传输失败（`UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol.`）。判据报得没错，是客户端造错了。
2. **run2/run3 的改坏库用例**：`DROP INDEX IF EXISTS uq_attempt_student_uuid` 对**表级 UNIQUE 约束**无效（SQLite 实现成 `sqlite_autoindex_attempt_1`，自动索引拆不掉；`IF EXISTS` 还静默成功）。改用 `remove_unique_constraint()`（按原 DDL 重建表）。
3. **探针 1 第 4 段的设计错误**：`release_later` 线程并没有释放锁（锁由外层 `with` 持有到 `fire_concurrent` 返回），那一段「锁释放后请求是否补完」**没有结论**，不能当证据——探针 3 重做了这件事。
4. **探针脚本 docstring 里的 `\.`** 触发 `SyntaxWarning: invalid escape sequence` → 含路径示例的 docstring 用 `r"""`。

## 7. 方法边界（它不能发现什么）

- **单突发、单进程、单文件**：50 条同时到达；不测持续压力、不测多 worker/多进程部署、不测长时间运行后的行为。
- **产品级降级行为（要一直记着）**：50 名**同刻突发不会触发** `database is locked`（§3 全绿即是证据，写锁只被占住几十毫秒）；**但一个长期持锁的写者会让提交以 HTTP 500 失败而不是排队**（≈7.4~7.5 秒到点，§5.2）。这是真实存在的产品级降级行为，不是只活在本报告里的细节。
- **同机回环**：客户端与服务端同机 `127.0.0.1`，**不含局域网真实延迟/丢包**；数字是功能证据，不是容量结论。
- **没有阈值**：契约没有 SLO，所以「p95 = 1566ms」本身不判红/绿，只有「零 5xx + 恰好一次落库 + 账目自洽」是门禁。
- **不覆盖前端**：没有浏览器，学生端在失败时显示什么由 task-42/task-49 一类任务负责。
- **注入是人工构造**：写锁注入只有 4 条请求（故意小：一条 traceback 就有 8697 字节，超过管道缓冲会重演 5.1 的假象），它证明的是「判据会红」，不是「真实 40 人突发会阻塞多久」。
- **不覆盖 D76 的语义面**：重放只测了「同 uuid 恰好一行」，没有测不同学生同 uuid、或 uuid 长度边界。
- **环境相关**：7.5 秒这个数字是 `pysqlite` 默认 busy timeout 的产物；换 WAL、换显式 `busy_timeout`、换非 Windows，数字会变。

## 8. 复跑命令

```powershell
# 判据（10 条，约 38 秒）
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests/perf -t backend -v

# 三个机制探针（可重跑，各自写 artifacts/*.json 与 *.log）
.\backend\.venv\Scripts\python.exe backend\tests\perf\artifacts\task48_lock_probe.py
.\backend\.venv\Scripts\python.exe backend\tests\perf\artifacts\task48_lock_probe2.py
.\backend\.venv\Scripts\python.exe backend\tests\perf\artifacts\task48_lock_probe3.py
```

## 9. 上报 Lead 的事实

1. **测试设施缺陷（跨任务）**：`backend/tests/_uvicorn_boot.py`（`Popen(stdout=PIPE)`）+ `backend/tests/e2e_m4/_harness.py:357-436`（`UiServer` 从不 `read()`）会让「服务端 500」在观察者眼里变成「客户端超时 + 日志为空」。8697 字节的 traceback > Windows 管道约 4KB 缓冲。建议单独立案（不在本任务写域）。
2. **产品行为**：长写锁下提交在 ≈7.4~7.5 秒返回 HTTP 500（`database is locked`），快速失败而非排队；第一道撞锁语句是 `ensure_attempt_schema()` 的表反射。
3. **正面发现**：D76 不只在 SELECT 层去重，库里有真约束 `uq_attempt_student_uuid`（表级 UNIQUE），手写 INSERT 会被顶回——这是比「代码里有判断」更硬的守护。
4. **测量窗口**：40 并发数字取自 `backend/app/**` 指纹前后一致的窗口（`ebc13c1398f27cad` / 60 文件），**有效**；同一窗口里 `frontend/**` 有人在半写（dist 早于 src），与本任务的数字无关（`PerfServer` 不起静态托管、不含浏览器）。
