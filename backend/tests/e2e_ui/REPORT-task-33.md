# task-33（T-M4-13）验收报告：把「端口竞争」从偶发失败里救回来

**结论：修复完成，四个方向的证据都拿到了。** 共享引导模块 `backend/tests/_uvicorn_boot.py`
替换掉四处套件各自的 `free_port()` TOCTOU 探测；新负对照模块用**真实 uvicorn + 真实监听套接字**
证明「重试真的会发生」与「重试不会吞掉真故障」；`e2e_ui` 连跑 3 次全绿；运行窗口前后
`backend/tests` 聚合哈希逐字节不变。

* 任务：task-33 / T-M4-13（Lead 指派，`team_task_get` 全文）
* 现象来源：Lead 全树门禁 `discover -s backend/tests -t backend` 第一次
  `Ran 869 tests in 263.885s / FAILED (errors=1)`；立刻复跑 `Ran 872 tests in 278.784s / OK`
* 报告日期锚点：2026-10-15（Asia/Shanghai）

---

## 0. 一句话结论表

| 问题 | 状态 | 证据 |
| --- | --- | --- |
| 门禁里的 `WinError 10013` 是端口竞争伪装成权限错误 | 已定性（Lead 受控实验 + 本次复现） | §2 |
| 四处 `free_port()` TOCTOU | 已统一替换为 `boot_uvicorn` | §3、§4 |
| 只对有 bind 失败证据的退出重试，其它失败一次都不重试 | 已实现并有**双向**负对照 | §6 |
| 错误信息能拦住「去查防火墙」 | 已改（点明真实含义 + 怎么查占用者） | §6.2 |
| `e2e_ui` 连跑 3 次 | 3/3 全绿，无 10013 | §5.1 |
| 运行窗口内 `backend/tests` 是否稳定 | 全组哈希 pre == post | §5.3 |
| 是否触碰冻结契约 / `backend/app/**` | **没有**，只改 `backend/tests/**` | §7 |

---

## 1. 现象原文（Lead 门禁实测，逐字节引用）

```
ERROR: setUpClass (tests.e2e_ui.test_ui_a9.TestA9FocusRingClosed)
...
  File ".../backend/tests/e2e_ui/test_ui_a9.py", line 160, in setUpClass
    cls.server.__enter__()
  File ".../backend/tests/e2e_ui/test_ui_structure.py", line 116, in __enter__
    self._wait()
  File ".../backend/tests/e2e_ui/test_ui_structure.py", line 125, in _wait
AssertionError: uvicorn 提前退出：ERROR: [Errno 13] error while attempting to bind on
address ('127.0.0.1', 64695): [WinError 10013] 以一种访问权限不允许的方式做了一个
访问套接字的尝试。
```

`869 = 872 - 4 + 1`：四条用例被这个 `setUpClass` 顶掉。复跑 10013 出现 **0** 次 ——
典型的「偶发、但一次就足以让门禁红」。

## 2. 机理（Lead 受控实验，本报告只复述不扩写）

原先四处套件各自写的 `free_port()`：

```python
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    return probe.getsockname()[1]
```

它只是问内核要一个**此刻**空闲的端口就立刻关掉 —— 到 uvicorn 真正 `bind` 之间有一段窗口，
窗口里任何东西（同进程里别的 server、Chromium 的出站连接、httpx 客户端……）都可能把这个
端口当临时端口拿走。本机 TIME_WAIT 的 `127.0.0.1` 端口常年在**百位数**，池子复用激烈。

三组对照钉死了 10013 的真实含义：

| 场景 | 结果 |
| --- | --- |
| `[A]` 端口处于 TIME_WAIT + 默认 bind | OK |
| `[B]` 端口处于 TIME_WAIT + `SO_REUSEADDR` | OK |
| `[C]` 端口**已有活监听者** + `SO_REUSEADDR` | `errno=13 winerror=10013` |

即：**TIME_WAIT 无害；10013 在这里就是「端口已被别人占着」**。之所以不报 10048（地址已在使用），
是因为 **uvicorn 0.54.0 的 `Config.bind_socket()` 无条件设了 `SO_REUSEADDR`**；Windows 的
`SO_REUSEADDR` 没有 Unix 那种共享语义，于是「已被占用」被翻译成了「访问权限不允许」。

> 这条踩坑记录一并写进了 `_uvicorn_boot.BIND_FAILURE_HINT`：下一个人看到 10013 时，
> 错误信息里直接写着「**不是防火墙、也不是权限问题**」。

## 3. 改动文件清单

新建：

| 文件 | 作用 |
| --- | --- |
| `backend/tests/_uvicorn_boot.py` | 共享引导模块（234 行）。`boot_uvicorn(...) -> (process, port, attempts)`；`looks_like_bind_failure()`；`describe_bind_failure()`；`http_ready()`；`UvicornBootError`；`probe_free_port()`/`free_port` 兼容别名。文件名不以 `test` 开头，unittest discover 不收集 |
| `backend/tests/e2e_ui/test_ui_port_race.py` | task-33 负对照（5 条，两个方向 + 判据双向单测） |

改动（**全部在 `backend/tests/**`**）：

| 文件 | 改动 |
| --- | --- |
| `backend/tests/e2e_ui/test_ui_structure.py` | 删本地 `free_port()`、`import socket`/`time`；`RealServer` 改为 `boot_uvicorn`（`_command(port)` + `attempts`），删 `_wait()`；`MATHGEN_DB` 改名用随机后缀（端口起来后才知道） |
| `backend/tests/e2e_m4/_harness.py` | 删本地 `free_port()`，改从 `tests._uvicorn_boot` 导入（`free_port` 仍在 `__all__`，向后兼容）；`UiServer` 同上改为 `boot_uvicorn` + `attempts`，删 `_wait` |
| `backend/tests/e2e_m3/test_m3_acceptance.py` | 删本地 `free_port()`；新增模块级 `startup_command(port)`（测试与 `report_evidence` 同源，避免证据里贴的命令漂移）；`TestSingleCommandStartup` 的 `_start`/`_wait_ready` → `_boot()` + `boot_uvicorn` |
| `backend/tests/e2e_m3/report_evidence.py` | `part_c()` 改用 `T.boot_uvicorn(T.startup_command, ...)`；启动失败**不再静默**（打印 `启动失败：…`）；换过端口时打印 `端口竞争：换过端口 p1 -> p2` |

刻意**没动**：

* `backend/tests/e2e_ui/test_ui_structure.py` 的 `ApiDownServer` 仍用 `free_port()` 给
  `ThreadingHTTPServer` —— 它在**同进程内**紧接着 `bind`，窗口极小；且它失败时会以 `OSError`
  立刻炸出来（不会伪装），属另一种形态，本次不扩面。
* `backend/tests/e2e_m4/collect_device_evidence.py` 的 `start_backend(port, ...)` —— 它是
  **固定端口 + `port_in_use()` 预检**，不是 TOCTOU 探测，本次不动。

## 4. 关键实现约定（为什么重试是安全的）

1. **只有证据才重试**：`looks_like_bind_failure(output)` 只认
   `WinError 10013` / `10013` / `error while attempting to bind` / `Errno 13` / `address already
   in use` / `[Errno 48]` / `[Errno 98]` / `WinError 10048` / `10048`。宁可窄：多认一个词，
   就多一分吞掉真 bug 的风险。
2. **三种结局分开处理**：`ready` 返回；`bind-failure` 换端口重试（上限 4 次 = `MAX_BOOT_ATTEMPTS`）；
   `early-exit` / `timeout` **一次都不重试**并原样透出 uvicorn 输出。
   （超时且进程存活 ⇒ bind 失败会立刻退出 ⇒ 这**不是**端口竞争，所以不重试。）
3. **不泄漏进程**：失败尝试的 uvicorn 一律先收掉再返回/抛出（超时且存活的先 `kill` 再 drain）。
4. **不可降级**：`UvicornBootError` 是 `AssertionError` 的子类 —— 在 `setUpClass` 里仍是
   error、仍是红，**没有**任何 skip 路径把「环境坏了」美化成「跑过了」。

## 5. 验证命令与原始输出

### 5.1 `e2e_ui` 连跑 3 次（Lead 要求的四步之一）

```powershell
& $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -v
```

| 轮次 | 原始输出 | exit |
| --- | --- | --- |
| run1 | `Ran 27 tests in 74.214s` / `OK` | 0 |
| run2 | `Ran 27 tests in 71.290s` / `OK` | 0 |
| run3 | `Ran 27 tests in 71.863s` / `OK` | 0 |

原始日志：`backend/tests/e2e_ui/artifacts/task33-e2e-ui-run{1,2,3}.log`
（27 条 = 本域原有 22 条 + 本次新增 5 条负对照；10013 出现 **0** 次。）

### 5.2 其它两处被改域的套件 + 证据脚本

```powershell
& $VPY -m unittest discover -s backend/tests/e2e_m3 -t backend -v   # Ran 15 tests in 13.106s  OK  exit=0
& $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend -v   # Ran 29 tests in 21.450s  OK  exit=0
& $VPY backend/tests/e2e_m3/report_evidence.py                      # exit=0
```

`report_evidence.py` 的 C 段原文（节选，两个场景各自拿到**本次真实端口**）：

```
=== C. 一条命令启动（只用 uvicorn，无 Vite）===
命令：-m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 58830 --log-level warning
[dist 存在] GET /teacher               → 200  text/html
[dist 存在] GET /teacher/paper/1       → 200  text/html
[dist 存在] GET /api/papers            → 200  application/json
[dist 存在] GET /docs                  → 200  text/html
[dist 存在] 服务已就绪（uvicorn 进程存活=True）
命令：-m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 56546 --log-level warning
[dist 缺失] GET /teacher               → 404  application/json
[dist 缺失] GET /teacher/paper/1       → 404  application/json
[dist 缺失] GET /api/papers            → 200  application/json
[dist 缺失] GET /docs                  → 200  text/html
[dist 缺失] 服务已就绪（uvicorn 进程存活=True）
```

日志：`backend/tests/e2e_m3/artifacts/task33-{e2e-m3,report-evidence}.log`、
`backend/tests/e2e_m4/artifacts/task33-e2e-m4.log`

### 5.3 V8 夹逼（运行窗口前后 `backend/tests` 聚合哈希）

```powershell
& $VPY backend/tests/e2e_m4/artifacts/agg_hash.py task33-pre    # 3 次连跑之前
& $VPY backend/tests/e2e_m4/artifacts/agg_hash.py task33-post   # 3 次连跑之后
```

| 组 | pre | post | 结论 |
| --- | --- | --- | --- |
| `backend_app` | `402fb1b0c13930e9` (58) | `402fb1b0c13930e9` (58) | **SAME**（产品代码一行未动） |
| `backend_tests_all` | `30dc591c6985c8b9` (90) | `30dc591c6985c8b9` (90) | **SAME**（窗口内稳定） |
| `backend_tests_others` | `af0f1f8de185a199` (83) | `af0f1f8de185a199` (83) | **SAME** |
| `e2e_m4_scope` | `0912dd55491dc725` (8) | `0912dd55491dc725` (8) | **SAME** |
| `frontend_src` | `83908cdbd015acf2` (48) | `83908cdbd015acf2` (48) | **SAME** |
| `frontend_dist` | `c3f075e77f3b03d4` (8) | `c3f075e77f3b03d4` (8) | **SAME** |

快照：`backend/tests/e2e_m4/artifacts/hash-task33-{pre,post}.json`。
（`backend_tests_all` 相对 T1 的 86 → 90 是本任务域内新增文件的**预期**变化：`_uvicorn_boot.py`、
`test_ui_port_race.py` 等。）

## 6. 负对照：两个方向都要有结论

```powershell
& $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -p "test_ui_port_race.py" -v
# Ran 5 tests in 10.122s / OK / exit=0
```

日志：`backend/tests/e2e_ui/artifacts/task33-port-race.log`

### 6.1 方向一：重试**真的**会发生（两态对照，真实 uvicorn + 真实监听套接字）

| 测试 | 世界 | 断言 | 结果 |
| --- | --- | --- | --- |
| `test_retry_moves_to_another_port_and_serves` | `_OccupiedPort` 用 `listen()` 占住端口 P，`first_port=P` | `len(attempts) >= 2`；`attempts[0].port == P` 且 `outcome == "bind-failure"`；`looks_like_bind_failure(attempts[0].output)` 为真；最终端口 ≠ P；`http_ready(最终端口)` 为真 | ok |
| `test_without_the_retry_the_same_race_is_a_hard_failure` | 同一场景 + `max_attempts=1` | 抛 `UvicornBootError`；`port_race is True`；`len(attempts) == 1`；消息含「疑似端口竞争」+ `BIND_FAILURE_HINT` + 「不是防火墙」+ 端口号 + `netstat` | ok |

第二态就是门禁里那一次的形态：**没有重试时它必红**；第一态证明**有了重试就起绿**。
（`first_port` 这个参数是专为负对照加的缝：强制第一次尝试指向已知被占端口，复现竞态。）

### 6.2 方向二：重试**不会**吞掉非端口原因的真故障

| 测试 | 世界 | 断言 | 结果 |
| --- | --- | --- | --- |
| `test_broken_app_target_is_reported_once_without_retrying` | 真实 uvicorn + 写坏的 app 目标（`app.does_not_exist:app`） | 抛 `UvicornBootError`；`port_race is False`；**`len(attempts) == 1`（一次都不重试）**；`outcome == "early-exit"`；原始输出含 `does_not_exist`；消息含「非端口原因」、**不含**「疑似端口竞争」、**不含** `10013` | ok |

### 6.3 判据本身的双向单测

| 测试 | 输入 | 期望 | 结果 |
| --- | --- | --- | --- |
| `test_recognises_the_bind_failure_forms` | 10013 原文 / `winerror=10013` / `[Errno 98] Address already in use` / `WinError 10048 地址已在使用` | 全 True | ok |
| `test_does_not_call_a_real_bug_a_port_problem` | `ModuleNotFoundError: No module named ...` / `AssertionError: 判据失败：期望 200，实际 500` / `""` / `None` | 全 False | ok |

## 7. 是否触碰冻结契约

**没有。** 具体核对：

| 检查项 | 结论 |
| --- | --- |
| `backend/app/**`（产品实现） | 一行未动（`backend_app` 哈希 pre/post 与 T1 完全相同的 `402fb1b0c13930e9`） |
| 错误码契约 / 接口契约（`backend/tests/contract/**`、`api/**`） | 未动 |
| 请求/响应结构、DB 结构 | 未动（只换了「谁来起服务、起在哪个端口」） |
| 测试是否被降级成 skip | 未新增任何 skip；`UvicornBootError` 继承 `AssertionError`，失败仍是 error/red |
| `frontend/**` | 未动（`frontend_src` / `frontend_dist` 哈希不变） |

## 8. 方法边界（能自证到什么程度）

1. **「偶发」不可 100% 自证已消灭**：TOCTOU 的窗口在原理上仍存在，本修复把它从「一次就红」变成
   「有证据才换端口、最多 4 次」。三次连跑 + 受控复现（负对照方向一）是「机制上不再暴露」的证据，
   不是「数学上不可能再发生」的证明。
2. **负对照用的是真实 uvicorn、真实监听套接字**，不是桩：`_OccupiedPort` 保持 `listen()` 直到
   `__exit__`，因此它占的是**真的**端口，而不是「探测后关掉」。
3. **`first_port` 是测试缝**，只给负对照用；生产路径（四处调用点）一律不传它。
4. **`reap` 保证不泄漏**：失败尝试的 uvicorn 在返回/抛出前已被收掉；负对照跑完后
   8000/8010 之外没有留下孤儿进程（三次连跑无端口争用迹象，10013 出现 0 次）。
5. **本域仍有一处 `free_port()`**（`ApiDownServer` 的 `ThreadingHTTPServer`，同进程内立即 bind）：
   属另一种形态（失败会以 `OSError` 立刻炸出、且不伪装），本次**有意不扩面**，在此登记。

## 9. 复跑命令（一次贴全）

```powershell
$VPY = "backend\.venv\Scripts\python.exe"
& $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -v                       # 27 / OK
& $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -p "test_ui_port_race.py" -v  # 5 / OK
& $VPY -m unittest discover -s backend/tests/e2e_m3 -t backend -v                       # 15 / OK
& $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend -v                       # 29 / OK
& $VPY backend/tests/e2e_m3/report_evidence.py                                          # exit 0
& $VPY backend/tests/e2e_m4/artifacts/agg_hash.py task33-post                            # 与 pre 逐组相同
```

> 注意：`discover` 的选项要写在位置参数**之前**，且必须在仓库根目录执行
> （`workdir=mathgen`）；否则会报 `ImportError: Start directory is not importable: 'backend/tests/e2e_ui'`。

## 10. 本次写盘清单（自报，供 Lead 对账）

| 文件 | 类型 |
| --- | --- |
| `backend/tests/_uvicorn_boot.py` | 新建 |
| `backend/tests/e2e_ui/test_ui_port_race.py` | 新建（负对照） |
| `backend/tests/e2e_ui/test_ui_structure.py` | 改 |
| `backend/tests/e2e_m4/_harness.py` | 改 |
| `backend/tests/e2e_m3/test_m3_acceptance.py` | 改 |
| `backend/tests/e2e_m3/report_evidence.py` | 改 |
| `backend/tests/e2e_ui/REPORT-task-33.md` | 新建（本文件） |
| `backend/tests/e2e_ui/artifacts/task33-*.log`、`backend/tests/e2e_m4/artifacts/hash-task33-*.json` 等 | 产物 |

未动域：`backend/app/**`、`backend/tests/api/**`、`backend/tests/security/**`、
`backend/tests/contract/**`、`frontend/**`、`docs/**`。
