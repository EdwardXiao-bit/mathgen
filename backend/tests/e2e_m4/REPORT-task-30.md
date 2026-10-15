# T-M4-06 M4 里程碑验收报告：真机手机浏览器可用

- **日期**：2026-10-15（Asia/Shanghai）
- **验收方**：verify-m1。判据由我写，**产品侧数据由我独立只读 SQLite 复核**（不用采集器代码查业务数据）；Lead 对 `04a83041` 的 DB 另做了一次独立只读复核，结论与我一致；用户核对过手机上填写的姓名/学号。
- **写域**：`backend/tests/e2e_m4/**`（本报告与全部证据都在此域内）
- **对应任务**：`docs/05-任务板.md` 的 **T-M4-06**。文件名取 `REPORT-task-30.md`（沿用 `backend/tests/<域>/REPORT-task-NN.md` 的既有命名，编号排在 task-29 之后；编号若与 Lead 的记录不同，改文件名即可，内容不受影响）。

---

## 0. 结论（三档并列，不合并成一个「通过」）

| 档 | 验收对象 | 结论 | 决定性证据 |
| --- | --- | --- | --- |
| **Tier 0** | 链路无关判据（HTTP Host 敏感性 / 存储禁用路径 / 移动端排版与负对照） | **全绿 · 21/21** | `artifacts/tier0-all-run3.log`：`Ran 21 tests in 22.016s / OK` |
| **Tier 1a** | **真机手机浏览器**（系统 Chrome）打开 → 报到 → 取卷 → 交卷 | **全绿 · 6/6** | `artifacts/device-session-04a83041.json`：手机请求 10 条、`t1_1..t1_5` 全 true、`t1_3=false`（非微信，1b 的账） |
| **Tier 1b** | **微信内置浏览器**（XWEB 内核）打开 → 报到 → 取卷 → 交卷 | **全绿 · 2/2** | `artifacts/device-session-5b025aba.json`：UA 含 `MicroMessenger/8.0.79.3200`、走到 `POST /api/attempts 201` |
| **Tier 2** | 渲染细节（`dvh` 地址栏伸缩、iOS 键盘遮挡、微信缓存策略、X5 内核差异） | **UNVERIFIED（未验证）** | 本机没有 iOS 设备；微信缓存策略与 X5 内核**没有**可复现的观测手段。**不越界表述** |

**一句话**：M4 的验收目标「扫码 → 手机可用」在**真机、两条浏览器通道（系统 Chrome + 微信内置）**上都走完了完整流程；Tier 2 的渲染细节**没有**被验证，按未验证处理。

---

## 1. 验收轮次总表（每一轮都有独立 nonce 与独立产物）

链路变化过一次（校园网 → 笔记本移动热点），监听进程换过一次（venv python → 有防火墙入站规则的 anaconda python）。**这些变化都发生在验收中，不是验收后补的**，所以逐轮留档：

| # | nonce | 链路 / 服务地址 | 监听解释器 | 手机请求数 | 结果与原因 |
| --- | --- | --- | --- | --- | --- |
| 1 | `bb3ed26e` | 校园网 `10.134.45.36:8000` | venv python | **0** | 手机打不开 → 现场事实判定为**校园网客户端隔离**（详见 §5.1） |
| 2 | `eb577a8c` | 热点 `192.168.137.1:8000` | venv python | 0（干跑：本机回环 3 条） | **干跑绿**：Host 改写、二维码、全流程自连 201/200/201 |
| 3 | `3ef19e10` | 热点 `192.168.137.1:8000` | venv python | **0**（只有我自己的 `curl` 存活校验 1 条） | 手机**连接挂起**（进度条卡 50%、空白、无错误页）→ **防火墙静默丢弃（drop，而非 reject）**；见 §5.3 |
| 4 | `04a83041` | 热点 `192.168.137.1:8000` | **`D:\anaconda3\python.exe`** | **10** | **Tier 1a 全绿**（系统 Chrome，交卷成功） |
| 5 | `5b025aba` | 热点 `192.168.137.1:8000` | `D:\anaconda3\python.exe` | **10** | **Tier 1b 全绿**（微信内置浏览器，交卷成功） |

> 第 3 轮与第 4 轮之间**只改了一个变量**：谁在 `0.0.0.0:8000` 上 listen（`--proxy-python`）。uvicorn 与浏览器驱动**始终**是 venv python，代理只做 Host 改写与转发。这是一个干净对照，见 §6.5。

---

## 2. Tier 0：链路无关判据（21 条，全绿）

Tier 0 的定义：**不需要手机**就能跑、且结论不依赖哪条链路的判据。三个批次：

| 批次 | 文件 | 条数 | 实测 |
| --- | --- | --- | --- |
| 二维码 / Host 敏感性 | `test_qr_host_sensitivity.py` | 6 | `artifacts/canary-qr.log`：`Ran 6 tests in 1.303s / OK` |
| 存储禁用路径 | `test_storage_disabled_path.py` | 5 | `artifacts/tier0-2-run1.log`：`Ran 5 tests in 8.703s / OK` |
| 移动端排版 + 负对照 | `test_mobile_layout.py` | 10 | `artifacts/tier0-3-run2.log`：`Ran 10 tests in 14.434s / OK` |
| **整包** | `backend/tests/e2e_m4` | **21** | `artifacts/tier0-all-run2.log` / `tier0-all-run3.log`：`Ran 21 tests / OK (skipped=1)` |

`skipped=1` 是**当时尚未采集真机证据**的 Tier 1 类，skip 理由写在类上：`尚未采集真机证据：请先运行 python backend/tests/e2e_m4/collect_device_evidence.py ...`。**带理由的 skip 比一个假的绿诚实**——它把「没测」和「测了通过」分开。Tier 1 采集完成后这个 skip 自动消失（整包变 29 条 0 skip，见 §9）。

判据要点（各自的「牙齿」都在同文件里有负对照）：
- 二维码判据不是「生成了 PNG」，而是**像素级比对**：`test_02` 断言二维码像素与 LAN URL 渲染结果一致；`test_03` 断言换 Host 必须换二维码。**用 localhost 生成的二维码会被 `test_04` 判红**（手机不可能访问 `127.0.0.1`）。
- 排版判据带 5 条负对照（注入一份「坏 CSS」→ 各判据**必须**变红），否则判据可能因为元素找不到而恒绿。

### 2.1 这一批里抓到并修掉的三个问题（都是判据/仪表自身的缺陷）

| # | 现象（原始输出） | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `tier0-all-run1.log`：`FAIL: test_03_positive_control_storage_enabled_also_flows` → `AssertionError: 'SecurityError' == 'SecurityError' : 没注入存储禁用，却已经不可写 —— 探针或环境有问题` | 探针在 `about:blank`（不透明源）上读 `localStorage`，**必然**抛 `SecurityError`，于是「正对照」成了死代码 | 探针先 `goto` 学生端页面、等 `#root` 出现后再探存储 |
| 2 | `tier0-3-run1.log`：`FAILED (failures=1, errors=2)` → `FAIL: test_02_tap_targets_on_answering_screen` / `AssertionError: '点击目标 >= 44' unexpectedly found in {'点击目标 >= 44': ['退出 32x44']}`；两条 `Page.click: Timeout 30000ms exceeded`（`test_05` 与负对照的 `setUpClass`） | 「退出」按钮实测 32×44，判据阈值 44 触发；另两条是等待条件写错导致 30s 超时 | 修判据的对象（点真实按钮而不是子串）/ 等条件；`tier0-3-run2.log` 10 条 `OK` |
| 3 | 采集器把「查不到防火墙规则」印成「没有规则」 | PowerShell 脚本在被拒时仍输出合法 JSON `[]` → `query_ok=true` + `python_rules=0` | 脚本自报 `ok`/`error`/`rules`，`query_ok=false` 时 `covered=null`（无法判定）。复验见 `artifacts/probe_firewall3.py` |

---

## 3. Tier 1a：真机手机浏览器（系统 Chrome，全流程）

**产物**：`artifacts/device-04a83041-requests.jsonl`、`device-04a83041.db`、`device-04a83041-qr.png`、`device-session-04a83041.json`
**窗口**：2026-10-15 19:46:43 → 19:49:52；服务地址 `http://192.168.137.1:8000`；学生卷 `http://192.168.137.1:8000/student/paper/2610-0001`

### 3.1 手机侧请求序列（代理侧原文，`client_ip = 192.168.137.173`）

```
19:46:43 POST /api/papers?seed=20261015&title=真机验收卷-04a83041   201  127.0.0.1        ← 采集器自己建卷/发布/取二维码
19:46:43 POST /api/papers/1/publish                                200  127.0.0.1
19:46:44 GET  /api/papers/1/qr                                     200  127.0.0.1
19:48:59 GET  /student/paper/2610-0001                             200  192.168.137.173  ← 以下全部来自手机
19:49:00 GET  /assets/student-nkw1JC7a.js                          200  192.168.137.173
19:49:02 GET  /assets/tokens-C2jUAzgv.css                          200  192.168.137.173
19:49:02 GET  /assets/student-CNcKrH17.css                         200  192.168.137.173
19:49:02 GET  /assets/tokens-BNjZ03QE.js                           200  192.168.137.173
19:49:02 GET  /favicon.ico                                         404  192.168.137.173
19:49:02 GET  /favicon.ico                                         404  192.168.137.173
19:49:36 POST /api/students                                        201  192.168.137.173  ← 报到（姓名 + 学号）
19:49:36 GET  /api/student/paper/2610-0001                         200  192.168.137.173  ← 取卷
19:49:51 POST /api/attempts                                        201  192.168.137.173  ← 交卷
```

UA（系统 Chrome，**不含** `MicroMessenger`）：
`Mozilla/5.0 (Linux; Android 16; V2364A Build/BP2A.250605.031.A3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.181 Mobile Safari/537.36`

### 3.2 产品侧落库（我独立只读 SQLite 查，非采集器代码）

```
paper   : {id: 1, code: '2610-0001', title: '真机验收卷-04a83041', status: 'published'}
student : {id: 1, name: '肖铭皓', student_no: '2024141460455'}
attempt : {id: 1, student_id: 1, paper_id: 1, attempt_uuid: 'attempt-1792064943263-175923399',
           total: 3, correct: 3, duration_ms: 14734,
           submitted_at: '2026-10-15T19:49:51.413743+08:00'}
```

- 卷标题带本轮 nonce → 证明这条 attempt **属于本轮**，不是历史数据；
- `student_no` 非空 → D41（学号必填）在真机上被真实走通；
- `submitted_at` 落在采集窗口内（判据 `test_04`）；
- 用户口头核对：`肖铭皓 / 2024141460455` **确系本人**在手机上填写的。

### 3.3 判据（`test_tier1_device_evidence.py::TestTier1aRealDeviceFlow`，6 条）

| 判据 | 断言的是什么 | 结果 |
| --- | --- | --- |
| `test_01_client_ip_comes_from_a_second_device` | 存在**非回环**客户端 IP，且请求不是 `curl` 之类 | PASS |
| `test_02_the_client_address_is_a_real_neighbour_on_that_link` | 该 IP 在同一时刻的 `arp -a` 邻居表里（**把「手机真在这个热点上」做成判据**，读 `artifacts/lan-neighbors.txt`） | PASS |
| `test_03_user_agent_is_recorded_and_looks_like_a_browser` | UA 被记录且形态是浏览器；非回环请求里**不许**出现 `curl/` | PASS |
| `test_04_attempt_row_is_the_product_side_proof` | attempt 行**属本轮 nonce 卷**、时间落窗口内、且学生**不是**干跑固定学生 `验收学生/20260406001` | PASS |
| `test_05_client_ip_evidence_comes_from_our_proxy_not_from_the_product` | **方法边界**：记录 IP/UA 的是本项目采集代理，产品 uvicorn 只看见 `127.0.0.1`（`uvicorn_phone_lines` 必然为空） | PASS（并把界限固定在判据里） |
| `test_06_environment_facts_are_recorded` | 环境事实（监听解释器、候选网卡、防火墙读数）被记录 | PASS |

---

## 4. Tier 1b：微信内置浏览器（XWEB 内核，全流程）

**产物**：`artifacts/device-5b025aba-requests.jsonl`、`device-5b025aba.db`、`device-session-5b025aba.json`
**窗口**：19:52:17 → 19:54:39（`--minutes 45`）

请求序列与 1a 同形：`19:53:45 GET /student/paper/2610-0001 200` → 4 个静态资源 200 → 2× `favicon.ico` 404 → `19:53:59 POST /api/students 201` → `GET /api/student/paper/2610-0001 200` → **`19:54:39 POST /api/attempts 201`**（走到交卷，不只是「能打开」）。

UA 原文（**真·微信内置浏览器**）：
```
Mozilla/5.0 (Linux; Android 16; V2364A Build/BP2A.250605.031.A3; wv) AppleWebKit/537.36 (KHTML, like Gecko)
Version/4.0 Chrome/150.0.7871.189 Mobile Safari/537.36 XWEB/1500145 MMWEBSDK/20260902 MMWEBID/8459
REV/ad1165dcc9fc199afcf096f0d7ca0c872f981881 MicroMessenger/8.0.79.3200(0x28004F30) WeChat/arm64
Weixin NetType/WIFI Language/zh_CN ABI/arm64
```

产品侧落库：
```
paper   : {id: 1, code: '2610-0001', title: '真机验收卷-5b025aba'}
student : {id: 1, name: '肖铭皓', student_no: '2024141460455'}
attempt : {id: 1, attempt_uuid: 'attempt-1792065231047-299214026', paper_id: 1,
           total: 3, correct: 0, duration_ms: 38785,
           submitted_at: '2026-10-15T19:54:39.459981+08:00'}
```

**如实记录**：微信这轮的 `correct = 0`（交卷了但**没有答对/未作答**）。它不影响本档判据（本档验收的是「微信内置浏览器里流程可用」），但**不粉饰**——不把它写成「全对」，也不因为它是 0 就把它算成失败。

判据（`TestTier1bWeChatInnerBrowser`）：
- `test_01_wechat_ua_is_recorded_with_a_version`：UA 必须匹配 `MicroMessenger/\d+\.\d+` → PASS；
- `test_02_wechat_run_level_is_opened_only_or_full_flow`：走到 `POST /api/attempts` 记 `full_flow`，只打开页面记 `opened_only` 并 **skip**，理由写进测试输出 —— **不用「能打开」冒充「全流程可用」** → 本轮 `full_flow`，PASS。

---

## 5. 环境限制（**现场事实**，不是推测）

### 5.1 校园网侧：手机到不了 `10.134.45.36:8000`（已实测）

第 1 轮（nonce `bb3ed26e`，`--minutes 30`）在校园网地址上采集：二维码与地址都正确、采集器自连正常，**手机侧请求数 0**，用户现场反馈手机打不开。按现场事实记为**校园网客户端隔离 / 二层隔离**（**不写「可能被隔离」**）。

换链路后的选择（Lead 定案）：**笔记本开 Windows 移动热点，手机连它**——不受园区网隔离影响，也不依赖 Windows ICS 驱动。

### 5.2 热点侧拓扑（本机读数）

- 热点 AP 侧地址 `192.168.137.1/24` 落在适配器 **`本地连接* 2`**（Local Area Connection\* 2，ifIndex 7）；**该接口没有网络类别**（`Get-NetConnectionProfile` 里只有 WLAN 一条，类别 `Public`）；
- 手机拿到 `192.168.137.173`；`arp -a` 邻居表：`192.168.137.173  76-52-86-72-46-2d  static`（本地管理位 = 随机化 MAC，手机特征）；反向 `ping` → `Reply … TTL=64` ×2、`Sent = 2, Received = 2, Lost = 0 (0% loss)`；
- 原始输出留存：`artifacts/lan-neighbors.txt`。

### 5.3 防火墙：**手机请求被静默丢弃**，换监听解释器后到达

- 第 3 轮（venv python 在 listen）手机表现为**连接挂起**：进度条卡在约 50%、页面空白、**没有**「无法访问」错误页。`reject` 会立刻变成 connection refused 并显示错误页，**hang + 服务器零请求** 对应 **drop**；
- 本机防火墙读数（`Get-NetFirewallRule` / `Get-NetFirewallProfile`，三个配置文件 `Domain`/`Private`/`Public` 均 `Enabled=True`、`DefaultInboundAction=NotConfigured`）；
- **存在的 python 入站允许规则 6 条，全部 `Profile=Public`**，程序为：
  - `D:\anaconda3\envs\cpu_1\python.exe`（×2）
  - `D:\anaconda3\python.exe`（×2）
  - `C:\users\ed\appdata\local\python\bin\python3.exe`（×2）
  - **venv python（`backend\.venv\Scripts\python.exe`）没有规则**；
- 非管理员**加不了**最小权限端口规则（原始拒绝必须留档）：
  ```
  netsh advfirewall firewall add rule name="mathgen-e2e-m4-port8000" dir=in action=allow protocol=TCP localport=8000 profile=any
  → The requested operation requires elevation (Run as administrator).    NETSH_EXIT=1
  ```
- 于是**只换监听进程**（`--proxy-only` + `--proxy-python D:\anaconda3\python.exe`），uvicorn 与驱动仍是 venv python → 手机请求到达（第 4、5 轮）。

### 5.4 一条读数差异（**只记读数，不编机制**）

同一个用户、同样 `admin=False`、同样 `Medium Mandatory Level`：

| 发起方 | `Get-NetFirewallApplicationFilter` |
| --- | --- |
| 采集器（venv python 的子进程 powershell） | `Access is denied.` / `FullyQualifiedErrorId : Windows System Error 5`；`Get-CimInstance` 走 `MSFT_NetFirewallApplicationFilter` → `无效类` / `HRESULT 0x80041010` |
| DSH 的 pwsh 工具（同一时刻） | `ALL_FILTERS = 909` / `PYTHON_FILTERS = 6`（可读） |

已排除的简单解释：**不是 WOW64**（两边都是 64 位；`struct.calcsize('P')*8 = 64`、`[Environment]::Is64BitProcess = True`）。**机制未定，本报告不声称知道原因**，只把它记为环境事实；因此采集器的 `covered` 在查询被拒时是 `null`（**无法判定**），而不是 false。

另记一条**读数**（不据此推断因果）：存在入站 Block 规则 `codex_sandbox_offline_block_inbound`，`profile=Any`、`dir=Inbound`、`action=Block`、`enabled=True`、`LocalAddress=Any`、`RemoteAddress={0.0.0.0-126.255.255.255, 128.0.0.0-255.255.255.255, ::, ::2-…}`，ActiveStore 与 PersistentStore 都在。**手机请求实测能到达**，所以本报告**不**声称「就是它挡的」，也不声称「它没挡」。

---

## 6. 方法边界（这份报告**能**证明什么、**不能**证明什么）

1. **「笔记本自连 `192.168.137.1` 拿到 200」不证明手机可达** —— 这一点已被实测证实：第 3 轮窗口里唯一的非采集器请求是**我自己**的 `curl/8.21.0 GET /api/papers 200`（`client_ip=192.168.137.1`）。如果把它当成「客户端非 loopback」的证据，就会得到一条**看起来通过、其实没测手机**的判据。真正的判据只能是：**服务器日志里出现非 loopback、且不是我自己 UA 的客户端请求**。
2. **非回环 IP/UA 的证据来自本项目采集代理**（`LoggingProxy`），**不是产品 uvicorn**：uvicorn 只见 `127.0.0.1`，所以 `uvicorn_phone_lines` 必然为空。**这不是缺陷**，是链路结构（代理在前）。这条界限写进了判据 `tier1a/test_05`，不让它随报告消失。
3. **邻居表对账**（`arp -a` + `ping`）把「这台手机真的在这个热点上」变成了判据，而不是靠我口头断言。
4. **产品侧数据由我独立只读 SQLite 复核**，没有使用采集器自己的判定代码去证明采集器自己的结论（避免「用被测物证明被测物」）。
5. **监听解释器对照是干净对照**：唯一变量是「谁在 `0.0.0.0:8000` listen」，uvicorn 与浏览器驱动全程 venv python。
6. **`correct=0` 的 1b attempt 如实记录**（§4）。
7. **Tier 2 未验证就不说验证了**：本轮的微信真机请求顺带证明了「XWEB 内核确实把学生端页面与静态资源都取走了」（可达性与资源加载层面），**不等于** Tier 2 的渲染行为（`dvh`/键盘遮挡/缓存策略）已被验证。

---

## 7. V8 哈希夹逼（证明「运行窗口前后没改过不属于我的东西」）

工具：`artifacts/agg_hash.py`（可重跑）。口径：`backend_app` 必须与 T1 相同（产品代码没动）、`backend_tests_others` 必须与 T1 相同（别人的测试域没动）。

| 组 | T1（19:13:27） | T3（收尾） | 结论 |
| --- | --- | --- | --- |
| `backend_app` | `402fb1b0c13930e9` (58) | `402fb1b0c13930e9` (58) | **不变** ✅ 产品代码没动 |
| `frontend_src` | `83908cdbd015acf2` (48) | `83908cdbd015acf2` (48) | **不变** ✅ |
| `frontend_dist` | `c3f075e77f3b03d4` (8) | `c3f075e77f3b03d4` (8) | **不变** ✅ 没重建产物 |
| `backend_tests_all` | `11dde6e074e3d0ef` (86) | `4d0b98db37f7115c` (88) | 变，**允许**：我在自己域内新增 2 个文件（`test_tier1_device_evidence.py`、`collect_device_evidence.py`） |
| `e2e_m4_scope` | `e2df8d204d5634f0` (5) | `5a8f494e99f5125b` (8) | 变，**预期**（判据模块、采集器、报告、日志都在此域内） |
| `backend_tests_others` | `9716809c15884165` (81) | `3e5a3f4fc8bbb64d` (81) | **变，已定位到具体文件，不是我的写盘** ⚠️ 见下 |

**`backend_tests_others` 的偏移：夹逼起了作用，它点名了那个文件。**

- 该组文件数不变（81），内容哈希变了 → 逐个查 mtime：**唯一一个晚于 T1 快照的文件**是
  `backend/tests/api/test_errors_and_sheet.py`（mtime **19:15:51**，T1 快照 19:13:27，晚 2.5 分钟）；
  同组其余 80 个文件 mtime 均 ≤ 19:05:58。
- 该文件不属于我的写域（我的写域是 `backend/tests/e2e_m4/**`），其内容和形态与 **task-28（T-M4-08）的交付物一致**：
  `git diff` 显示新增 `assert_error_codes_agree(...)`、`ErrorCodeReconciliationNegativeControls`（6 条负对照）
  与 `test_there_is_no_implementation_only_code_left` —— 即「错误码双向对账 + 常驻负对照」。
- 结论：**偏移由另一位写者（`backend/tests/api/**` 域）造成，发生在我 T1 快照之后**；我今天的写盘清单全部落在
  `backend/tests/e2e_m4/**` 内（另加 `docs/06-测试方案.md` 的 §9.7 一节，见 §11）。夹逼的价值恰在此：
  它没有被我解释掉，而是被定位到一个具体文件、一个具体时间、一份具体内容形态。

> `e2e_m4_scope` 是**自指**的：本报告自己就在域内，所以任何「最终快照」都会在写盘后再次偏移。因此夹逼口径只认
> `backend_app` / `frontend_src` / `frontend_dist` 三组「必须完全不变」，以及 `backend_tests_others` 的**逐文件归因**。
>
> 写盘完成后再采一次快照（`artifacts/hash-T3b.json`）：`backend_app`、`backend_tests_all`、`backend_tests_others`、
> `frontend_src`、`frontend_dist` 五组**与 `hash-T3.json` 逐字节相同** —— 即我在这之后的写盘（本报告、§9.7、日志）
> **没有**触及任何一组。唯一变化的是 `e2e_m4_scope`（本报告自己在该域内）。

---

## 8. 复跑命令（任何人可以重来一遍）

```powershell
# 整包（Tier 0 + Tier 1a + Tier 1b 判据；注意 discover 形式，-t 不能放在位置参数后面）
& backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests/e2e_m4 -t backend -v

# 真机采集（热点链路；--proxy-python 换成有防火墙入站允许规则的解释器）
& backend\.venv\Scripts\python.exe backend\tests\e2e_m4\collect_device_evidence.py `
    --minutes 45 --label <本轮标签> --proxy-python D:\anaconda3\python.exe

# 从原始产物重建某一轮的 session（不冒充原采集，标 reconstructed=true）
& backend\.venv\Scripts\python.exe backend\tests\e2e_m4\collect_device_evidence.py `
    --rebuild-session <nonce> --lan-ip 192.168.137.1 --port 8000

# 哈希夹逼
& backend\.venv\Scripts\python.exe backend\tests\e2e_m4\artifacts\agg_hash.py T3

# 防火墙读数探针（可重跑，用于复现 §5.4）
& backend\.venv\Scripts\python.exe backend\tests\e2e_m4\artifacts\probe_firewall3.py
```

---

## 9. 未覆盖 / UNVERIFIED 清单（诚实边界）

| 项 | 状态 | 原因 / 需要什么才能覆盖 |
| --- | --- | --- |
| Tier 2 渲染细节：`dvh` 地址栏伸缩、iOS 键盘遮挡、微信缓存策略、X5 内核差异 | **UNVERIFIED** | 无 iOS 设备；微信缓存策略与 X5 内核没有可复现的观测手段。`test_04_dvh_and_safe_area_are_shipped_and_effective`（Tier 0）验的是**产物里确实发了 `dvh`/`env(safe-area-inset-*)` 且计算值生效**，**不是**真机地址栏伸缩行为 |
| 微信「打开页面」与「全流程」的分级 | 已分级（`full_flow` / `opened_only`） | 本轮 `full_flow`；`opened_only` 会成为**带理由的 skip**，不会被算成通过 |
| 多台手机 / 多浏览器矩阵 | 未覆盖 | 只有一台 Android 16 设备（vivo V2364A） |
| `/student/practice` 练习流程真机 | 未覆盖 | 真机只走了「老师发的卷」这条流程 |
| 并发 / 弱网下的真机行为 | 未覆盖 | 属 NFR-4 性能测试范围（`docs/06` §10） |

---

## 10. 附：本报告引用的产物清单

| 文件 | 内容 |
| --- | --- |
| `artifacts/device-session-04a83041.json` | Tier 1a 汇总（手机请求 10、判据逐条 true/false、哈希、环境事实） |
| `artifacts/device-session-5b025aba.json` | Tier 1b 汇总（同上） |
| `artifacts/device-session-3ef19e10.json` | 第 3 轮：只有我自己 `curl`，手机零请求（防火墙 drop 的现场） |
| `artifacts/device-<nonce>-requests.jsonl` | 代理侧逐条请求原文（时间/方法/路径/状态/客户端 IP/UA） |
| `artifacts/device-<nonce>.db` | 该轮的产品数据库（供独立只读复核） |
| `artifacts/lan-neighbors.txt` | `arp -a` 与 `ping 192.168.137.173` 原文 |
| `artifacts/probe_firewall.py` / `probe_firewall2.py` / `probe_firewall3.py` | 防火墙读数探针（含「查询被拒」的原始报错） |
| `artifacts/tier0-all-run{1,2,3}.log` 等 | Tier 0 各批次原始输出（含那次失败的原文） |
| `artifacts/hash-T1.json` / `hash-T3.json` | 哈希夹逼快照 |

---

## 11. 本次写盘清单（自报，供 Lead 对账）

**我改过/新建过的文件**：

| 文件 | 性质 |
| --- | --- |
| `backend/tests/e2e_m4/_harness.py` | 网卡候选与选择重写（ICS 优先）、代理子进程、端口预检、`allow_reuse_address=False` |
| `backend/tests/e2e_m4/collect_device_evidence.py` | 采集器：候选清单+理由、防火墙预检、`--proxy-only` / `--proxy-python`、按 nonce 留档、`--rebuild-session` |
| `backend/tests/e2e_m4/test_tier1_device_evidence.py` | 新建：Tier 1a（6 条）+ Tier 1b（2 条）判据 |
| `backend/tests/e2e_m4/test_qr_host_sensitivity.py` / `test_storage_disabled_path.py` / `test_mobile_layout.py` | 判据加固与假绿修复（§2.1） |
| `backend/tests/e2e_m4/REPORT-task-30.md` | 本报告 |
| `backend/tests/e2e_m4/artifacts/**` | 证据、日志、哈希快照（新增） |
| `docs/06-测试方案.md` | **跨域一笔**：新增 §9.7（task-29 覆盖面登记）。`docs/06` 属 fe-teacher 写域，此笔是 **Lead 指派我写**的；它不在任何哈希组内，与 V8 夹逼无关 |

**明确没有动**：`backend/app/**`、`backend/tests/api/**`、`backend/tests/security/**`、`backend/tests/contract/**`、`frontend/**`、`docs/00`/`03`/`04`/`05`。

**一处刻意没改**：`frontend/scripts/compare-mock-and-real.mjs` 头注释 18–20 行仍写着旧命令名 `verify:mock-vs-real`。
它属 `frontend/scripts/**`（fe-teacher 写域），我**没有**代改，只在 `docs/06` §9.7 里点名该漂移并写明「以 `package.json` 为准」。
