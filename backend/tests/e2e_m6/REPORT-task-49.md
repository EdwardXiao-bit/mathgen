# task-49 验收报告：断网下的出题与打印（NFR-9 模式 A）

**结论：通过。** 12 条机器判据全绿（`Ran 12 tests in 28.645s` / `OK` / exit 0），
「断网」由进程级守卫造成并**自证**（外部访问以守卫自己的 `OfflineBlocked` 失败、守卫日志留痕），
断网态下「生成卷 → 预览 → 导出 PDF」与产品自己那份 Chromium 的「预览 → 打印」全部可用，
且渲染期间**零外部请求**。

本会话**非管理员**：没有用过系统级断网，也没有假装用过（原始证据见 §2.3）。

---

## §0 一屏结论

| 派单要求 | 判据 | 原始证据 |
| --- | --- | --- |
| 真断网下出题→预览→打印全程可用 | 4 条（`TestTheProductFlowWorksOffline`） | 卷面 200 / 7591 B / `markup_urls []`；服务端 PDF 200 / 18507 B / `%PDF-` |
| 「断网」必须自证真的断了 | 4 条（`TestTheGuardActuallyCutsTheNetwork`） | 外部 DNS/连接/字面 IP/HTTPS 全部 `OfflineBlocked`、`marker=true`；守卫日志有 `install` + 拦截记录 |
| 产品自己的 Chromium 也要断网 | 4 条（`TestTheBrowserIsOfflineWhileItPrints`） | 强制 8 个启动参数；预览 200；打印 18511 B `%PDF-`；渲染期间 0 请求；外网 `ERR_PROXY_CONNECTION_FAILED` |
| 既有基础不重做 | —— | 只跑本次新增的 `backend/tests/e2e_m6/**`；`frontend/dist` 零外部 CDN（门禁①）与 §8.1 的常驻断言未改动 |
| 判据能红（不是恒绿计时器） | 3 条留仓库负对照 + 首轮真红 2 条 | §5 |
| 非管理员不许谎报断网 | 权限原始输出 | `net session` → `System error 5 ... Access is denied`；`Mandatory Label ... S-1-16-8192`（Medium） |
| 交付物 | 本报告 + 判据代码 + `artifacts/task49-*.json` | §8 |

---

## §1 由来

* 任务卡：`T-M6-03 离线验证：断网下出题打印可用（NFR-9 模式 A）`，写域 `backend/tests/e2e_m6/**`（新建）
  与 `docs/06-测试方案.md`。
* 约束：判据进程与被测进程**要能对照**——只有先证明「不装守卫时外网本来是通的」，
  断网态里的失败才能归因给守卫（V9）。
* 不做的事：不重做门禁①（dist 零外部 CDN）与 `docs/06` §8.1（运行时零外部请求）；
  不碰 `backend/app/**` 与 `frontend/**`。

## §2 断网怎么做的

### 2.1 三层结构

| 层 | 怎么断 | 为什么这样 |
| --- | --- | --- |
| 服务进程（真机 uvicorn） | 子进程 `PYTHONPATH` 里放 `backend/tests/e2e_m6/offline`，解释器启动时自动导入 `sitecustomize.py` | 产品唯一的浏览器启动点在服务进程内；给进程加环境变量即可，不动产品代码 |
| 服务进程里的 Chromium | 守卫给 `playwright.sync_api._generated.BrowserType.launch` 打补丁，强制追加 8 个参数 | Python 的 socket 补丁**管不到** Chromium 的网络栈，必须在启动参数上断 |
| 判据进程 | **不装** | 保留「外网此刻本来是通的」这条对照 |

守卫的拦截口径：**非环回即拒绝**（`localhost` / `*.localhost` / `127.0.0.0/8` / `::1` 放行），
补丁打在 `socket.getaddrinfo` / `socket.socket.connect` / `connect_ex` / `create_connection` / `sendto` 上，
抛出的异常是守卫自己的 `OfflineBlocked(OSError)`（带 `operation`/`target`，消息前缀 `[offline-guard] 拒绝 …`）。
`MATHGEN_OFFLINE_GUARD=0` 可关。

### 2.2 Chromium 的 8 个参数是试出来的

取证脚本 `artifacts/task49_chromium_args_probe.py`（A/B/C/D 四组对照）：

| 组 | 参数 | 环回 | 外网 |
| --- | --- | --- | --- |
| A | `MAP localhost 127.0.0.1, MAP * 0.0.0.0` + 死代理 + bypass | `ERR_ADDRESS_INVALID` | 失败 |
| B | `MAP * ~NOTFOUND , EXCLUDE localhost` | `ERR_NAME_NOT_RESOLVED` | 失败 |
| C | 只有死代理 | 200 | `ERR_PROXY_CONNECTION_FAILED` |
| **D（采用）** | `--host-resolver-rules=MAP localhost 127.0.0.1, MAP 127.0.0.1 127.0.0.1, MAP * 0.0.0.0` + `--proxy-server=http://127.0.0.1:9` + `--proxy-bypass-list=localhost;127.0.0.1;<local>` | **200** | **失败** |

两个结论：
1. `MAP` 规则**连字面 IP 也改写**，所以必须把 `127.0.0.1` 再映射回 `127.0.0.1`，否则环回的预览打不开；
2. **不能用 Playwright 的 `proxy=` 参数**：它会自动追加 `--proxy-bypass-list=<-loopback>`，
   等于取消 Chromium 默认的「环回不走代理」，把环回也送进死代理。必须从 `args` 直接给
   `--proxy-server` + 显式 bypass 列表。

### 2.3 为什么没有用系统级断网

本会话权限的原始输出：

```
> net session
net : System error 5 has occurred.
Access is denied.

> whoami /groups | findstr "Mandatory Label"
Mandatory Label\Medium Mandatory Level   Label   S-1-16-8192
```

`netsh` / `Disable-NetAdapter` / `route add` 都需要提权，**没有尝试，也没有把「尝试失败」写成「已断网」**。
本套件断的是**进程**（守卫 + Chromium 参数），不是机器。

## §3 判据读什么（12 条）

`backend/tests/e2e_m6/test_offline_acceptance.py`：

| 类 | 条数 | 行号 | 读什么 |
| --- | --- | --- | --- |
| `TestTheGuardActuallyCutsTheNetwork` | 4 | `:81,:103,:124,:139` | 对照态外网可达；断网态外部访问必须 `OfflineBlocked`；守卫日志有 `install` + 拦截记录；负对照 |
| `TestTheProductFlowWorksOffline` | 4 | `:161,:179,:196,:217` | 卷面 200 且标记里零外部 URL；服务端导出真 PDF；服务进程守卫日志显示 Chromium 参数被改写；负对照 |
| `TestTheBrowserIsOfflineWhileItPrints` | 4 | `:243,:259,:282,:295` | 对照浏览器能上网；断网浏览器预览+打印可用且渲染期零外部请求；探针守卫日志有强制参数；负对照 |

判据设施 `backend/tests/e2e_m6/_m6_support.py`（纯函数判据与真机取数分离：
`socket_probe_problems:248` / `browser_probe_problems:299` / `guard_log_problems:375` /
`pdf_problems:414` / `pdf_bytes_problems:436` / `markup_external_problems:502` /
`OfflineServer:93` / `run_probe:124`）。
两个探针 `offline/socket_probe.py`、`offline/browser_probe.py` 一律**写 JSON 文件**再读，
不用 stdout 通信（task-48 的管道教训）。

## §4 原始输出

### 4.1 V9 对照（不装守卫）

* socket 探针：`external_https` → **200**，`loopback_dns/loopback_http` ok，`problems []`
  （`artifacts/task49-socket-control-evidence.json`）。
* 浏览器探针（显式走本机代理 `http://127.0.0.1:7890`）：环回 200，外网 **200**，
  并且真的加载了 `https://example.com/s.js`（`artifacts/task49-browser-control-evidence.json` 的 `requests`）。
* 开场基线：直连 `https://example.com` 200（0.71 s）、经本机代理 200（2.43 s）、
  DNS `example.com → 104.20.23.154`。

### 4.2 断网自证

`artifacts/task49-socket-hooked-evidence.json` 的 `observations`：

| 观测 | 结果 |
| --- | --- |
| `external_dns` | `blocked` / `OfflineBlocked` / `marker=true` |
| `external_connect`（域名） | `blocked` / `OfflineBlocked` / `marker=true` |
| `external_connect_raw_ip`（字面 IP） | `blocked` / `OfflineBlocked` / `marker=true` |
| `external_https` | 被 `urllib` 包成 `URLError`，但异常链里认得出 `OfflineBlocked` / `marker=true` |
| `loopback_dns` | ok（20.8 ms） |
| `loopback_connect(127.0.0.1:9)` | `TimeoutError`（没有服务在听，**不是**被守卫拦） |
| `loopback_http` | **200**（2048 B，67.5 ms，指向真机服务） |

守卫日志（`artifacts/task49-guard-probe.json.<pid>.json`）里有 `install` 与
`getaddrinfo example.com:443` / `create_connection ('example.com', 443)` /
`create_connection ('104.20.23.154', 443)` 三条拦截记录 —— 「我拦住了」有第三个来源佐证。

### 4.3 产品全程（服务端，断网态）

| 观测 | 结果 | 文件 |
| --- | --- | --- |
| `GET /api/papers/{id}/sheet` | 200 / 7591 B / **`markup_urls []`**（HTML 里没有任何 `src=`/`href=`/`url(...)` 目标） | `task49-sheet-markup.json` |
| `GET /api/papers/{id}/export?format=pdf` | 200 / **18507 B** / 前 5 字节 `%PDF-` | `task49-server-pdf.json` |

### 4.4 产品那份 Chromium（断网态）

`artifacts/task49-browser-hooked-evidence.json`：

* 预览真机卷面 URL：200，正文 89 字符；
* 打印：**18511 B**，前 5 字节 `%PDF-`，**打印期间 0 个请求**；
* 故意打外网：失败 `Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://example.com/`；
* `requests_before_external = 1`，渲染期间唯一的请求是**环回的卷面 URL**；
* `forced_chromium_args` 8 个（host-resolver-rules / proxy-server / proxy-bypass-list / 4 个
  `--disable-*` 与 `--no-*`）。

### 4.5 服务进程守卫日志

`artifacts/task49-server-guard-log.json`：`problems []`，日志里同时有
`install` 与 `chromium-launch`，后者的 `detail.args` 就是那 8 个参数、
`detail.proxy = http://127.0.0.1:9`。

## §5 能红的直证

### 5.1 留仓库的负对照（3 条）

| 负对照 | 喂什么 | 必须红在哪 |
| --- | --- | --- |
| `test_04`（`:139`） | 一份「外网可达」的 socket 观测 | 消息含 `没有被守卫拦住` |
| `test_04`（`:217`） | `b"<html>not a pdf</html>"` | 消息含 `%PDF-` |
| `test_04`（`:295`） | `https://cdn.example.com/app.js` | 消息含 `cdn.example.com` |

### 5.2 判别性设计（不止「能红」）

* 只断言「Chromium 打不开外网」是**不够**的：它可能本来就打不开。所以额外要求
  守卫日志里有 `chromium-launch` **且**参数里含 `--host-resolver-rules=` /
  `--proxy-server=` / `--proxy-bypass-list=`（`guard_log_problems:375`）。
* 只断言「外网请求失败」也是不够的：必须失败在守卫自己的 `OfflineBlocked` 上
  （`marker=true`），否则分不出「我拦住了」与「网本来就不通」。

### 5.3 首轮真红（判据侧，已修）

首轮 `Ran 12 tests in 30.838s` / `FAILED (failures=2)`，两条都在判据侧（详见 §6.2、§6.3）：

```
探针进程守卫日志：守卫日志里只有 0 条 blocked 记录，期望至少 1 条 —— 「断网」在进程内没有留下证据
服务进程守卫日志：没有找到任何守卫日志（守卫没生效，或日志路径没交给子进程）
```

## §6 判据自身的缺陷（本文档最该被复用的部分）

1. **一条守卫日志被多进程覆盖**：诊断用的 `python -c` 也继承了 `PYTHONPATH` 而加载守卫，
   把探针刚写的 `chromium-launch` 记录整份冲掉，害得我以为 Playwright 补丁没生效。
   → 日志文件名必须带 pid：`<MATHGEN_OFFLINE_LOG>.<pid>.json`。
2. **拦截记录的 `kind` 不是字面量 `blocked`**：实际是 `getaddrinfo`/`connect`/`connect_ex`/
   `create_connection`/`sendto`。按 `kind == "blocked"` 计数恒为 0 → 判据报「断网没有留下进程内证据」，
   而证据其实就在同一个文件里（`BLOCK_KINDS:42`）。
3. **`UiServer.process.pid` 不是「导入守卫那个进程」的 pid**：本机 venv 的 `python.exe`
   会把真正的解释器当**子进程**拉起来（实测 launcher 33688 / 真正干活 23880；
   服务进程 launcher 49872 / 守卫日志里是 44440 与 6336）。按 pid 筛守卫日志必然筛空。
   → 改成按**日志基名**筛（本套件独有，足以隔离服务进程），并把这条坑写进
   `guard_logs_for_pid:469` 的 docstring。
4. **「零外部请求」判据会把探针自己故意打的那条算成问题**：探针第 3 步就是去打开
   `https://example.com/`，它必然出现在 `requests` 里。→ 探针记下
   `requests_before_external`，判据只切前面那一段（`browser_probe_problems:299`）。
5. **守卫日志跨轮残留会让判据假绿**：上一轮的 `chromium-launch` 留在盘上，
   这一轮就算补丁没生效也能过。→ `setUpModule` 先清 `task49-guard*.json*`。

## §7 本方法不能发现什么（诚实边界）

* **进程内守卫挡不住别的进程**：同机其它程序、系统级后台流量照旧；本套件不测它们，
  也不声称整机离线。
* **Chromium 参数挡的是浏览器网络栈**：操作系统级 DNS/连接能力没有被改变。真断网时
  另有一类失败（例如系统代理自动发现、企业策略、时间同步），本套件看不到。
* **显式配了本机代理的客户端仍能出去**：守卫口径是「非环回即拒绝」，装钩子时
  `external_https_via_local_proxy` 实测仍是 **200**。所以「离线可用」这句话的准确范围是
  **产品自己不主动走外部、且不配置代理**的情形。
* 只覆盖**模式 A**（出题/打印）；离线下的作答、成绩、导出成绩不在本卡范围。
* `file://` / `data:` / 已建立的连接不在拦截口径内（守卫拦的是新的解析与连接）。

## §8 复核命令

```powershell
cd C:\Users\Ed\dsh_workspace\mathgen
backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests/e2e_m6 -t backend -v
# 关键证据：backend/tests/e2e_m6/artifacts/task49-{run2.log,socket-hooked-evidence.json,browser-hooked-evidence.json,server-guard-log.json,server-pdf.json,sheet-markup.json}
```

## §9 上报事实

1. **非管理员**：`net session` → `Access is denied`（System error 5），完整性级别 Medium
   （`S-1-16-8192`）。系统级断网没做，也没谎报。
2. **显式本机代理仍能出网**：这是守卫口径的正常结果（「非环回即拒绝」），
   已作为边界写进 §7，不当作缺陷。
3. **venv 的 `python.exe` 会起子进程**（launcher ≠ 真正解释器）：既解释了 §6.3，
   也提醒 task-50：按 pid 找进程会找错，管道/日志的归属要按**句柄**而不是 pid 判断。
4. **收尾清理**：冒烟用的 2 个 `python -m http.server 8765` 已结束；跑完本套件后
   `uvicorn` 残留进程 0 个（`Get-CimInstance` 复核）。
