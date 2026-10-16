# task-50 报告：测试设施把 uvicorn 的输出去哪了（T-M6-08 / 管道缺陷）

> 状态：**完成**，判据与负对照已留仓库。日期 2026-10-16。写域 `backend/tests/**`（未碰
> `backend/app/**`、`frontend/**`、`docs/06` 以外的文档）。
> 一句话：**旧设施把服务端输出接进一条没人读的管道**，于是「服务真的卡住了」在观察者眼里
> 长得和「环境有问题」一模一样；现在输出落**文件**，同一条判据能在旧行为上红、在新行为上绿。

---

## 0. 一屏结论

* **修的是什么**：`backend/tests/_uvicorn_boot.py` 起 uvicorn 时把 `stdout`/`stderr` 从
  `subprocess.PIPE` 改成**一个文件**（`log_path` 参数；默认
  `<仓库>/.tmp-uvicorn-logs/uvicorn-<port>-<attempt>.log`）。新增 `read_server_log(log_path)`
  读它；`boot_uvicorn` 的 `attempts` 条目多了 `log_path`。
* **消费侧**：`backend/tests/e2e_m4/_harness.py:380-405` 的 `UiServer` 每次起服务用独立日志
  文件（`ARTIFACTS/server-<hex>.log`）并暴露 `read_server_log()`；
  `backend/tests/perf/_perf_support.py:604-660` 的 `ServerLogDrain` / `drain_server_log()`
  改成读那个文件（没有日志文件的旧调用方仍走原路径）。
* **判据**：`backend/tests/pipeline/test_server_log_pipeline.py`（**6 条**），
  实测 `Ran 6 tests in 52.093s` / `OK` / exit 0（`artifacts/task50-criteria-run3.log`）。
* **双向（V9/V11）**：旧行为世界 —— `/boom`、`/explode` 双双 `ReadTimeout`（6.078 s / 6.110 s）、
  日志 **0 字节**、判据 3 条全红；新行为世界 —— **200 / 500** 真状态码、日志 **38933 字节**、
  判据 0 条红。
* **回归**：`e2e_m3` 15 OK / `e2e_m4` 68 OK / `e2e_m5` 50 OK / `perf` 10 OK /
  `e2e_ui` 27 OK（首轮那 1 条红是同伴半成品文件，见 §7）。

---

## 1. 为什么有这张卡：故障被伪装成环境问题

`docs/06` §9.13 记过这次悬案（原文）：

> `backend/tests/_uvicorn_boot.py` 用 `Popen(stdout=PIPE)` 起 uvicorn，而 `UiServer` **从不读
> 这个管道**…… 服务进程**阻塞在写日志上**，请求永不返回、客户端只看到 `ReadTimeout`，
> `terminate()` 后管道数据被丢弃、日志读到 0 字节 ——「产品卡死 60 秒且日志为空」的假象就是
> 这么来的。

这段话把**观察者看到的**说对了，但机制还差一步：**不是「写满 4KB 就卡死」**。本任务用可控
fixture 把它钉死（`backend/tests/pipeline/fixture_app.py`，合成 ASGI 应用，不碰产品代码）：

| 实验 | 结果 | 说明 |
| --- | --- | --- |
| `small-2000-nodrain` | `/boom` **200 @0.079 s**，管道里 2056 字节 | 一波输出没超过缓冲 → 不阻塞 |
| `big-30000-nodrain` | `/boom` **ReadTimeout 8.094 s**，管道里 **30055 字节**（整行已写完） | 超过缓冲 → 卡 |
| 分步探针（三段 STAGE 标记） | `/boom` 超时 → **把管道读干（30157 字节）之后，下一个请求 200 @0.078 s** | **事件循环真的被 `write()` 堵住**；读走即恢复 |
| 第二跑（记 `first_read_bytes`） | `first_read_bytes 30054`、`drained 30105`，有 `AFTER-PRINT`、无 `AFTER-RESPONSE` | 卡点在「写完大段之后的那次写」，不是响应前 |

**结论（不变量）**：**没有人读** + **一波输出大于管道缓冲** → uvicorn 的事件循环线程阻塞在
`write()`，已排好的响应刷不出去、新连接也不再被 accept。本机这级缓冲装到 **~30KB**
（不是 4KB；4KB 是平台/负载相关的数字）。这也解释了为什么写锁注入只有 4 条请求就能复现：
一条未处理异常的 traceback 是 **8697 字节**，4 条叠起来远超缓冲。

**为什么必须修**：它让**真实故障看起来像环境问题** —— 超时 + 空日志恰好把人指向错误方向
（§9.13 那 7.5 秒 / HTTP 500 的真响应，是靠把 stderr 落成文件才看见的）。

---

## 2. 先复现，再修

修复前（`artifacts/task50-prefix-world-pipe.json`，旧形状由探针自己 `Popen(stdout=PIPE)` 复刻）：

* 就绪 200 ✓（服务真的起来了）；
* `/pid` 200 @0.094 s → 应用自报 `pid=34728 / ppid=46496`，而 `Popen.pid=40760`；
* `/boom` → `ReadTimeout` **12.093 s**；`/explode` → `ReadTimeout` **12.094 s**；
* 收尾读管道 **0 字节**，marker / traceback 全无。

⇒ 卡片的原始症状**逐字复现**（客户端超时 + 日志为空）。留仓库的负对照
（`_pipeline_support.old_pipe_no_reader`）在判据里每次都会再验一遍，最近的数字见 §5。

---

## 3. 修法：为什么是「文件」而不是「加个读者线程」

| 方案 | 为什么没选 / 选 |
| --- | --- |
| **读者线程持续 `readline()`** | 只**搬运**、不留存：没人调用 `drain` 的套件照样把日志丢了；`terminate()` 时线程里没落盘的字节同样会丢；还要处理线程退出与竞态。§9.13 当时就是用这个绕过去的（只够让压测跑起来） |
| **重定向到文件**（选它） | 文件**不会满**、`write()` 不阻塞；父进程随后**任何时候**都能读（进程死了也读得到）；不依赖谁记得调 drain；`attempts` 里带 `log_path`，失败现场天然可追溯 |
| 代价 | 占磁盘。日志落在 `.tmp-uvicorn-logs/`（默认）或调用方指定的路径（`UiServer` 用 `ARTIFACTS/`）。**没有轮转/清理**，见 §8 |

落地细节（`backend/tests/_uvicorn_boot.py`）：

* `:61` `import os`；`:67` `REPO_ROOT`；`:70` `DEFAULT_LOG_DIR = <仓库>/.tmp-uvicorn-logs`；
  `:72` `__all__` 增 `REPO_ROOT` / `DEFAULT_LOG_DIR` / `read_server_log`。
* `:164` `read_server_log(log_path) -> str`：读不到就返回 `""`（**不抛**，判据靠它判「空日志」）。
* `:175` `_resolve_log_path(log_path, log_dir, port, index)`：给了 `log_path` 就用它
  （重试写 `<stem>.attemptN<ext>`，不覆盖上一次的现场）；没给就落到 `log_dir` 或
  `DEFAULT_LOG_DIR` 下 `uvicorn-<port>-<index+1>.log`。
* `:210` `_one_attempt(...)`：`with open(log_path, "w", …) as handle: popen(…, stdout=handle,
  stderr=subprocess.STDOUT)` —— 父进程侧句柄**启动后立刻关闭**，子进程拿自己那份继续写；
  `early-exit` / `timeout` 分支改为读文件（原来读管道）。
* `:245` `boot_uvicorn(..., log_path=None, log_dir=None, ...)`：返回签名
  `(process, port, attempts)` 不变；`attempts` 条目 `{"port","outcome","output","log_path"}`。

**兼容性**：所有既有调用方（`e2e_m3`、`e2e_ui`、`e2e_m4`、`e2e_m5`）不改也能用（走默认目录）；
`UiServer` 显式传 `log_path`，`perf` 侧改为读文件。`ServerLogDrain` 的**外部行为**（`snapshot()`/
`stop()`）不变，只是内容来自文件。

---

## 4. 判据（6 条，`backend/tests/pipeline/test_server_log_pipeline.py`）

判据本体是**纯函数** `_pipeline_support.server_log_pipeline_problems(observation)`：只有两件事，
**必须同时成立**：

1. 客户端**拿得到响应**（`/boom` 200、`/explode` 500 —— 有真实状态码，不是超时）；
2. 日志**非空**，且确实是**这一次**那波输出（带本次唯一 marker 的 `BOOM-LINE` + 这次异常的
   traceback）——「非空」不能是别的来源凑的。

| 类 | 行 | 断言什么 |
| --- | --- | --- |
| `TestTheOldFacilityIsRed` | `:32/:35` | 旧形状（`Popen(stdout=PIPE)` 无人读）下判据必须**红**；两个请求都超时；`log_source == "pipe-after-kill"`；写日志的是自报 pid 那个进程（不是 `Popen.pid`） |
| `TestTheFixedFacilityIsGreen` | `:68/:71` | 新形状下 `200`/`500` 真状态码、日志 >4096 字节、marker + traceback 都在、判据 0 条、`log_source == "file"`、文件在盘上 |
| `TestTheSameCriterionGivesOppositeVerdicts` | `:95/:98` | **同一条判据、同一个测试里**，旧世界红 / 新世界绿（V11 对照） |
| `TestTheLogBelongsToThatVeryProcess` | `:121/:124` | 两次跑用不同 marker：新世界日志**只**含本次 marker（旧的 marker 不在里面），且进程死了文件还在 |
| `TestTheProductServerNoLongerUsesAPipe` | `:157/:160` | 真设施：`UiServer` 的 `process.stdout is None`、`server_log_path == attempts[-1]["log_path"]`、启动横幅进文件、**`terminate()` 之后 `perf.drain_server_log()` 仍读得到同一段**（管道在这里必然已丢） |
| `TestNoOtherTestFacilityHandsOutAPipe` | `:202/:213` | 性质守卫：`backend/tests/**` 里**起 uvicorn 的地方**不得再有 PIPE 调用点（AST 口径，docstring 里提到不算）；并给出守卫自己的**能红直证**（喂修复前的形状必须被抓到） |

运行（可复现）：

```powershell
$VPY = "C:\Users\Ed\dsh_workspace\mathgen\backend\.venv\Scripts\python.exe"
Set-Location C:\Users\Ed\dsh_workspace\mathgen
$env:PYTHONIOENCODING = "utf-8"
& $VPY -m unittest discover -s backend/tests/pipeline -t backend -v
# Ran 6 tests in 52.093s / OK / exit 0 → artifacts/task50-criteria-run3.log
```

---

## 5. 修复后的原始证据

**修复前 / 后（同一条命令，探针 `--world pipe|boot`）**

| | 修复前（`task50-prefix-world-pipe.json`） | 修复后（`task50-fixed-world-boot.json`） |
| --- | --- | --- |
| `/boom`（写 30KB 再回 200） | `ReadTimeout` 12.093 s | **200 @0.093 s** |
| `/explode`（真异常） | `ReadTimeout` 12.094 s | **500 @0.094 s** |
| 日志 | **0 字节**，无 marker / traceback | **39025 字节**（文件），marker + traceback 都在 |
| `attempts[-1]` | — | `{"port":64680,"outcome":"ready","log_path":"…task50-boot-server.log"}` |

**判据两态（`task50-criterion-*.json`）**

| 观测 | 旧世界 | 新世界 |
| --- | --- | --- |
| `/boom` | `ReadTimeout` 6.078 s | **200 @0.079 s** |
| `/explode` | `ReadTimeout` 6.110 s | **500 @0.078 s** |
| 日志 | **0 字节**（`pipe-after-kill`） | **38933 字节**（`file`）、marker ✓ traceback ✓ |
| 判据 | **3 条红**（2×「客户端没拿到响应」+「日志为空」） | **0 条** |

**「谁在写、谁在读」按句柄/自报判断（不是按 `Popen.pid`）**

| 世界 | `Popen.pid` | 自报 `pid` | 自报 `ppid` |
| --- | --- | --- | --- |
| 旧 | 36964 | **27700** | 36964 |
| 新 | 31732 | **28196** | 31732 |

本机 venv 的 `python.exe` 是 **launcher**，真正干活的是它的子进程：所以「写日志的进程」与
`Popen.pid` **不是同一个**。收尾也因此必须 `taskkill /F /T`（杀整棵树），否则会留下那个可能
正卡在 `write()` 上的子进程（`_pipeline_support.kill_tree`）。

**真设施（`task50-product-server-log.json`）**：`stdout_is_pipe=false`；启动横幅（`--log-level
info` 的 `Uvicorn running on …`）在文件里 `live_bytes=323`，**进程结束之后仍是 323 字节**。

---

## 6. 同类反模式扫描（交付要求⑤）

扫描器：`backend/tests/pipeline/artifacts/task50_pipe_scan.py`（**AST**，不看注释/字符串），
结果 `artifacts/task50-pipe-scan.json`。全仓 `backend/tests/**` 共 **5 个文件**有「把子进程输出
接进管道」的调用点：

| 文件 | 行 | 同类风险 | 结论 |
| --- | --- | --- | --- |
| `tests/e2e_m4/artifacts/tier1_dryrun.py` | 35 | 有读者（`proc.stdout.readline()` 循环等学生端 URL），**找到 URL 后 `break`**，之后只 `proc.wait(180)` | **不改，登记为残留隐患**：读者离场后不再排空 → 若采集器后续输出超过缓冲仍可能卡住。不属必修：一次性干跑脚本、90 秒窗口、实测输出量小；改它需要真机采集场景回归（不在本卡范围） |
| `tests/pipeline/_pipeline_support.py` | 99 | 旧形状**故意**的负对照（无人读，读数有界） | 安全（本任务的一部分） |
| `tests/pipeline/artifacts/task50_pipeline_probe.py` | 178 | 同上（`--world pipe` 那一半） | 安全 |
| `tests/pipeline/artifacts/task50_pipe_matrix.py` | 133 | 同上（判别矩阵的 `nodrain` 组） | 安全 |
| `tests/pipeline/artifacts/task50_stage_probe.py` | 94 | 同上（分步探针的 `nodrain` 组） | 安全 |

其余曾经担心的位置其实**不是**同类：`tests/e2e_m4/collect_device_evidence.py:176,179,422` 与
`tests/perf/artifacts/task48_lock_probe3.py:67` 用的是 `stdout=handle`（**文件**）；
`contract/artifacts/task47_historical_check.py:49`、`e2e_m6/_m6_support.py:149`、`e2e_m4/_harness.py:181`
等用的是 `subprocess.run(..., capture_output=True)` —— `run()` 内部**并发读走**管道，不会堵。

**守卫口径**（`TestNoOtherTestFacilityHandsOutAPipe`）：只守「**起 uvicorn 的文件**里不许有 PIPE
调用点」这一类，不做「任何地方都不许有 PIPE」的一刀切（一次性子进程是另一类风险）。守卫自带
**能红直证**：把修复前的形状字符串喂进去必须被抓到。**这个口径是启发式的**（见 §8）。

---

## 7. 五域回归

命令（逐域，串行跑，日志在各域 `artifacts/task50-regression-*.log`；耗时含全套 Playwright/浏览器）：

| 域 | 命令 | 结果 | 用时 | 结论 |
| --- | --- | --- | --- | --- |
| `e2e_ui` | `unittest discover -s backend/tests/e2e_ui -t backend` | `Ran 27 tests in 120.432s` / **OK** / exit 0 | 122.1 s | 无新红 |
| `e2e_m3` | 同上 `-s backend/tests/e2e_m3` | `Ran 15 tests in 26.639s` / **OK** | 29.7 s | 无新红 |
| `e2e_m4` | 同上 `-s backend/tests/e2e_m4` | `Ran 68 tests in 71.004s` / **OK** | 72.9 s | 无新红 |
| `e2e_m5` | 同上 `-s backend/tests/e2e_m5` | `Ran 50 tests in 40.265s` / **OK** | 41.8 s | 无新红 |
| `perf` | 同上 `-s backend/tests/perf` | `Ran 10 tests in 32.990s` / **OK** | 34.1 s | 无新红（含写锁注入那条） |

**首轮 e2e_ui 的那 1 条红与本次修复无关**（原始输出）：

```
FAIL: test_criteria_go_red_on_degraded_build (tests.e2e_ui.test_ui_a9.TestA9NegativeControlOnRealBuild)
  File "backend/tests/e2e_ui/test_ui_structure.py", line 116, in __enter__
    self.process, self.port, self.attempts = boot_uvicorn(
tests._uvicorn_boot.UvicornBootError: uvicorn 因**非端口原因**提前退出，不重试。
  File "backend/app/export/pdf.py", line 20, in <module>
    from app.layout.fitting import LayoutTooLarge
  File "backend/app/layout/fitting.py", line 288
    只用于文案：`font_size` 20 → `[19, 18, …, 10]`，`cols_per_row` 6 → `[5, 4, …, 2]`。
SyntaxError: invalid character '：' (U+FF1A)
```

那是同伴（task-44 写域 `backend/app/layout/**`）**半成品文件**造成的：`fitting.py` 的 mtime
09:47:49 改好，`py_compile` 现在 exit 0。我按同一命令**重跑整域**（09:53 起）= 上表那一行
（`Ran 27 … / OK`）。顺带说明：`boot_uvicorn` 在这里表现是正确的 —— 它把**非端口原因**的提前
退出**如实报出来**（含原始输出）而不是换端口重试，这次红正好证明这条判据有区分力。

**perf 数字与 task-48（run5）对比**（同一套判据，判据本身**不设延迟阈值**）：

| 项 | task-48 run5 | 本轮 | 备注 |
| --- | --- | --- | --- |
| 50 并发 | 50/50 `201`、零 5xx | 50/50 `201`、零 5xx | 同结论 |
| p50 | 1119.12 ms | 1222.76 ms | 同量级；本轮机器上有同伴在跑构建/测试，负载未受控 |
| p95 | 1810.09 ms | 2227.17 ms | 同上 |
| 墙钟 | 2167.54 ms | 2784.88 ms | 同上 |
| 并发/串行比 | 1.329 | 1.526 | 同上 |
| 写锁注入 | 4×500、p50 7546.08 ms、日志 34788 字节 | 4×500、p50 7502.22 ms、日志 **34788 字节** | 日志**字节数完全一致** ⇒ 文件把原来线程读到的内容一字不少地留下来了 |

---

## 8. 这套设施仍不能发现什么（已知边界）

* **磁盘**：日志文件没有轮转/清理，`.tmp-uvicorn-logs/` 会一直攒（谁跑谁攒）。写满磁盘的极端
  情形没测：`open()` 失败会让引导**直接失败**（可接受，但未验证），写到一半磁盘满则可能丢尾部。
* **只解决「无人读 + 一波输出大于缓冲」这一类阻塞**：别的丢字节情形照旧（`taskkill /F` 杀掉时
  文件缓冲里未 flush 的字节也会丢；区别是文件按行 flush，且父进程任何时刻都能读）。**不覆盖**
  「输出量巨大到写不完/来不及看」的极端场景。
* **进程归属**：判据按「自报 pid / 唯一 marker」判，不按 `Popen.pid`；但一个**连自报都没有**的
  子进程（例如不经过我方代码的第三方 launcher）仍然只能按 marker 归因。
* **守卫口径是启发式**：只扫 `backend/tests/**`、只守「文件里出现 `uvicorn` 且有 PIPE 调用点」。
  一个**不写 uvicorn 字样却起长驻服务**的地方会漏（例如把命令名藏在变量/配置里）；`backend/app/**`
  与仓库外完全不扫。
* **不覆盖** stdout 被别的框架接管/重定向的形态（`pytest` 的 capture、CI 的日志收集器）。
* **产品服务那条只证到「横幅进文件 + 收尾仍读得到」**：没有在产品里造一次 8KB traceback
  （那需要注入，属 `perf` 域，由 §7 的写锁注入覆盖：34788 字节日志一字不少）。
* **判据的运行成本**：旧世界那两条必然各等 6 秒（这就是「客户端超时」的定义），整域约 52 秒；
  这是**故意**的，不要为了快把它降到 1 秒内 —— 那会削弱「响应真的没来」的判定。

---

## 9. 复核命令与证据清单

```powershell
$VPY = "C:\Users\Ed\dsh_workspace\mathgen\backend\.venv\Scripts\python.exe"
Set-Location C:\Users\Ed\dsh_workspace\mathgen
$env:PYTHONIOENCODING = "utf-8"

# 1) 判据（6 条，含留仓库的旧世界负对照）
& $VPY -m unittest discover -s backend/tests/pipeline -t backend -v

# 2) 修复前/后同一条探针（--world pipe 是旧形状的忠实复刻）
& $VPY backend\tests\pipeline\artifacts\task50_pipeline_probe.py --world pipe --json backend\tests\pipeline\artifacts\task50-prefix-world-pipe.json
& $VPY backend\tests\pipeline\artifacts\task50_pipeline_probe.py --world boot --json backend\tests\pipeline\artifacts\task50-fixed-world-boot.json

# 3) 反模式扫描（AST）
& $VPY backend\tests\pipeline\artifacts\task50_pipe_scan.py

# 4) 分步探针（卡在哪一步 + 读走即恢复）
& $VPY backend\tests\pipeline\artifacts\task50_stage_probe.py --big-chars 30000 --timeout 8.0 --json backend\tests\pipeline\artifacts\task50-stage-nodrain.json
```

| 证据 | 内容 |
| --- | --- |
| `artifacts/task50-criteria-run3.log` | 判据 6 条 `Ran 6 tests in 52.093s / OK / exit 0` |
| `artifacts/task50-criterion-old-world.json` | 旧世界：超时 + 0 字节日志 + 3 条问题 |
| `artifacts/task50-criterion-new-world.json` | 新世界：200/500 + 38933 字节日志 + 0 条问题 |
| `artifacts/task50-criterion-opposite-verdicts.json` | 同一条判据两态对照 |
| `artifacts/task50-criterion-attribution.json` | 唯一 marker 归因（两次跑互不污染） |
| `artifacts/task50-product-server-log.json` | 真设施：无管道、收尾仍读得到 |
| `artifacts/task50-prefix-world-pipe.json` / `task50-fixed-world-boot.json` | 修复前后同一条探针 |
| `artifacts/task50-pipe-matrix.json` / `task50-stage-nodrain.json` | 机制：缓冲 / 卡点 / 读走即恢复 |
| `artifacts/task50-pipe-scan.json` | 反模式扫描逐处结论 |
| `backend/tests/{e2e_ui,e2e_m3,e2e_m4,e2e_m5,perf}/artifacts/task50-regression-*.log` | 五域回归原始输出 |
| `artifacts/task50-regression-summary.txt` | 五域 exit / 用时一览 |

**改动的文件**（`git diff --stat` 口径）：`backend/tests/_uvicorn_boot.py`、
`backend/tests/e2e_m4/_harness.py`、`backend/tests/perf/_perf_support.py`；新增
`backend/tests/pipeline/**`（`_pipeline_support.py`、`fixture_app.py`、`test_server_log_pipeline.py`、
`artifacts/**`）。**没有**修改 `backend/app/**`、`frontend/**`，也没碰 task-44 的
`backend/tests/render/**`。

---

## 10. 上报事实（不粉饰）

1. **「约 4KB」的说法被本任务修正为「~30KB」**：`docs/06` §9.13 的原话按当时观察写的是 4KB；
   本任务实测这级缓冲装到 30054 字节。不变量是「**没人读** + 一波输出大于缓冲」，数字随平台/负载浮动。
2. **首轮 e2e_ui 有 1 条红，不是本修复造成的**，是同伴 `backend/app/layout/fitting.py:288` 的
   半成品 `SyntaxError`（09:47:49 修好）；我重跑整域得 OK。这条红也顺带证明 `boot_uvicorn`
   对「非端口原因的提前退出」是**如实报错**的。
3. **`tier1_dryrun.py:35` 有同类残留隐患**（读者提前离场），我**没有**改它，理由与代价写在 §6。
4. **perf 延迟数字本轮比 task-48 慢**（p50 1222.76 vs 1119.12 ms 等）：判据不设阈值，结论
   （零 5xx、恰好一次落库）不变；差异来源是机器负载未受控，**不是**这次修复 —— 修复在压测路径上
   是**少**了一个守护线程、换成读文件，而写锁注入的日志字节数与 task-48 **完全相同（34788）**。
5. **没有轮转/清理机制**：`.tmp-uvicorn-logs/` 与 `ARTIFACTS/server-*.log` 会累积（§8 第 1 条）。
