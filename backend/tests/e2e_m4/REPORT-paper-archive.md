# task-36（T-M4-16）独立验收报告：卷库收纳（D78）

- 验收人：verify-m1（独立验收，不是实现者）
- 时间：2026-10-15（Asia/Shanghai）
- 写域：只新建/修改 `backend/tests/e2e_m4/**`（3 个文件 + `artifacts/**`）
- 一行结论：**通过** —— 9 条验收判据全部达成，逐条证据见 §5；本轮新增判据 33 条（后端 26 + 教师端 UI 7）。

---

## §0 一屏结论

| 项 | 结果 | 原始证据 |
| --- | --- | --- |
| 后端判据（判据 1–7） | `Ran 26 tests in 15.826s` / `OK` / `exit 0` | `artifacts/task36-backend-acceptance.log` |
| 教师端 UI 判据（判据 8） | `Ran 7 tests in 12.744s` / `OK` / `exit 0` | `artifacts/task36-ui.log` + `artifacts/task36-ui-evidence.json` |
| 全量后端门禁（判据 9a） | `Ran 980 tests in 307.184s` / `OK` | `artifacts/task36-full-backend.log` |
| 前端门禁（判据 9b） | `pnpm --dir frontend verify` exit 0，12 步 ①–⑫ 全 PASS，末行「全部验收通过」 | `artifacts/task36-frontend-verify.log` |
| V8 哈希夹逼（判据 9c） | pre == post，六组逐字节相同 | `artifacts/hash-task36-{pre,post}.json` |
| `backend/app/**` 是否被本任务改动 | **没有**（夹逼 `backend_app` pre == post） | 同上 |

Lead 点名的三个关注点，逐条回应：

1. **`status` 归档往返后逐字不变**：`TestArchiveRoundTrip.test_draft_and_published_round_trip_keeps_status` 对「草稿 / 已发布」两个 subTest 各做一次归档→取消归档，`status_before` 与往返后的 `status` **单独断言**，且同时核对 `archived=exclude/only/include` **三口径**的列表归属；已发布那条另加「`code` 仍有效 + `GET /api/papers/{id}/qr` 200」——归档不是撤回发布。**不是**只断言 `archived_at` 非空。
2. **`has_attempts` 非恒真/非恒假（V11）**：`TestHasAttemptsTruth` 用两个具体世界给出真、假两个输入（有成绩卷 / 干净卷），另加两个负对照：**恒真的世界**必须红、**恒假的世界**必须红、**互换取值**只允许红 2 条且**不得**出现「恒值字段」那条（互换 ≠ 恒值，见 §6 第 3 条）。
3. **「归档不影响学生端」有负对照**：`test_negative_control_injected_archived_check_turns_the_same_criterion_red` 把 archived 检查**注入**到 `student_service.get_paper_by_code` 与 `attempt_service.get_paper_or_404`（学生读卷 / 交卷两条路径），**同一条判据**随即变红并具名。所以这条判据不是「学生端接口没坏」的替代品。

---

## §1 任务与由来

D78 把「卷库收纳」定成三件套，缺一件界面就会说谎：

1. `paper.archived_at` —— 归档位，**与 `status` 正交**（草稿和已发布都能归档，归档不改 `status`）；
2. 列表三口径 `archived=exclude|only|include` —— 「在用 / 已归档」两个分段档必须由后端口径保证，不能靠前端过滤；
3. `PaperSummary.has_attempts` —— 「有成绩的卷禁用删除」的**唯一数据来源**；它恒真等于所有卷都删不掉，恒假等于所有卷都能删。

D78 之前 `paper.archived_at` 与 `attempt.attempt_uuid` 漏写在 `docs/02-技术设计.md`（task-37 已把「docs/02 建表语句 ↔ `models.py` 逐列逐索引对账」做成门禁）。本轮是同一条决策的**独立验收**：实现由 task-34（be-paper 后端）/ task-35（fe-teacher 前端）交付，验收判据由本任务新写。

**只读域**（本任务一行未动）：`backend/app/**`、`backend/app/contracts/**`、`backend/tests/api/**`、`frontend/**`。

---

## §2 现场形态（实现侧锚点，grep 复核过行号）

| 能力 | 位置 |
| --- | --- |
| `has_attempts(db, paper_id) -> bool` | `backend/app/services/paper_service.py:92-101`（`select(Attempt.id).where(paper_id==…).limit(1)` 的 EXISTS 语义） |
| 列表三口径 | `backend/app/services/paper_service.py:454-508`（`archived` 取值不在 `ARCHIVED_FILTERS` → `VALIDATION_ERROR`；`exclude` → `Paper.archived_at.is_(None)`；`only` → `is_not(None)`） |
| 批量取数 `_page_facts(db, paper_ids)` | `backend/app/services/paper_service.py:511-535`（两条 `IN (…)` group-by，把 2N 压成 2，见判据 7） |
| `archive_paper` | `backend/app/services/paper_service.py:538-551`（`if paper.archived_at is None: paper.archived_at = utcnow_iso()`；幂等、不碰 `status`） |
| `unarchive_paper` | `backend/app/services/paper_service.py:554-564`（置 `None`） |
| `purge_paper`（删除边界） | `backend/app/services/paper_service.py:571-593`（先 `has_attempts` → `ApiError("PAPER_HAS_ATTEMPTS", "卷 {id} 已有学生成绩，不能删除；可以归档")`；否则先删 `paper_item` 再删 `paper`） |
| 老库护栏 `ensure_paper_schema` | `backend/app/services/paper_schema.py:33-48`（`weakref.WeakSet` 记「本引擎已查过列」，缺列才 `ALTER TABLE paper ADD COLUMN archived_at`） |
| 路由 | `backend/app/routers/papers.py:72 list_papers` / `:178 archive_paper`（`POST`） / `:190 unarchive_paper` / `:196-209 delete_paper`（204 + `Response`） |
| 契约字段 | `backend/app/contracts/api.py`：`PaperDetail.archived_at`(123)、`PaperSummary.archived_at`(136) / `has_attempts`(139)、`PaperListOut.total/limit/offset/papers`(142-156)、`ROUTES`(36-38) |
| 学生读卷 | `backend/app/services/student_service.py:94 paper_payload`（只查 `status != published` → `CODE_NOT_FOUND`，**没有任何 archived 检查**） |
| 学生交卷 | `backend/app/services/attempt_service.py:209 _resolve_questions`（paper_id 分支在 214 `get_paper_or_404`） |

---

## §3 判据与用例映射

| # | 判据 | 用例 | 走了什么 |
| --- | --- | --- | --- |
| 1 | 冻结契约未被改动；D78 的路由与字段真的存在 | `TestContractSurface:60`（2 条） | 进程内，读契约哈希 + `ROUTES` |
| 2 | 归档往返后 `status` 逐字不变 + 三口径归属 | `TestArchiveRoundTrip:97` | 真机 uvicorn + 真 HTTP |
| 3 | 归档幂等、保留第一次时间戳；取消归档清空 | `:129` | 真机 uvicorn |
| | 非法 `archived` 值必须 422（不静默忽略）；缺卷 404 | `:143` / `:149` | 真机 uvicorn |
| 4 | 删除边界：无成绩真删（204 + 级联清空）；有成绩 409 且**什么都没变** | `TestDeleteBoundary:222` / `:238` / `:276` | 真机 uvicorn + **只读 SQLite** 直查库文件 |
| 5 | `has_attempts` 真 / 假两个世界 | `TestHasAttemptsTruth:310` | 真机 uvicorn |
| 6 | 已归档的已发布卷：学生照常打开 + 交卷 | `TestArchivedPaperStaysAnswerable:359` | 真机 uvicorn（真 HTTP 打学生端两个接口） |
| 7 | 老库没有 `archived_at` 列时护栏能起、能归档、数据不丢 | `TestOldDatabaseGuard:457` | 磁盘库文件 + **冷引擎**（见 §6 第 4 条） |
| 8 | `has_attempts` 是批量取数（不是每行一条查询） | `TestHasAttemptsIsBatched:546` | 进程内 + SQLAlchemy 语句计数 |
| 9 | 教师端界面：有成绩卷删除按钮事先禁用 + 可见理由；切档默认 `exclude`；切档回第一页；撒谎的双向负对照 | `test_paper_archive_ui.py`（7 条） | 真实 `frontend/dist` + playwright |

负对照：后端 **13 条**（用例名带 `negative_control` 的）+ UI 侧 **3 类**（纯函数逐种错法、撒谎桩双向、页序请求日志；另有一条「好世界必须绿」防止负对照本身空转）。全部要求「制造失败条件后必须红且具名」，原文见 §5.3 / §5.2。

**判据 1 的完整证据**：`TestContractSurface` 内断言 `contracts/api.py` 哈希 == 冻结值 `31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded`，并核对 D78 的路由键与 `archived_at`/`has_attempts` 字段真的存在；**契约域全绿**由 §5.4 的全量后端门禁覆盖（980 / OK，含 `backend/tests/contract/**`）；**ROUTES 后端/前端键集合一致**由前端门禁的门禁② 覆盖（12 步全 PASS）。

> **订正（task-43，2026-10-16）**：上面这段原先写的冻结值是 `359efa5323376c66e2abb04768f6a3f74b49fa8ef3510436aaaee16526c7c435` —— 那是 **M4 当时的旧值**；M5 起 `contracts/api.py` 合法地重新冻结为 `31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded`（两处机器守卫 `backend/tests/contract/test_layering.py:31` 与 `backend/tests/e2e_m4/test_paper_archive_acceptance.py:30` 都已同步，测试一直是绿的）。**只改了这一段已确认过期的陈述，本报告其余结论、数字与证据一字未动。** 顺带记下一条教训：**报告会持续变陈，而没有任何东西会因此变红** —— 测试锁的是代码里的冻结值，锁不住报告正文里誊写的那一份；这次是人工发现才订正的（task-43 顺手做的，不属于原 M4 交付）。

---

## §4 改动清单（全部在写域内）

| 文件 | 性质 |
| --- | --- |
| `backend/tests/e2e_m4/_archive_support.py` | 新建。`ArchiveServer(UiServer)` + 真机 HTTP 助手 + **只读 SQLite** 取证（`mode=ro`）+ 纯判据函数（`archive_round_trip_problems` / `has_attempts_problems` / `student_flow_problems`）+ 注入桩 `archived_blocks_students()` + N+1 计数 `statement_log`/`attempt_selects` + 老库构造 `build_old_paper_db` + **教师端桩服务器** `LibraryStubServer`（`Task 8` 的撒谎世界） |
| `backend/tests/e2e_m4/test_paper_archive_acceptance.py` | 新建。26 条（判据 1–7） |
| `backend/tests/e2e_m4/test_paper_archive_ui.py` | 新建。7 条（判据 8） |
| `backend/tests/e2e_m4/artifacts/task36-*.log`、`hash-task36-*.json`、`task36-ui-evidence.json`、`probe_ui_stub.py` | 取证产物（`probe_ui_stub.py` 是定位 UI 假红的可重跑探针，保留） |
| 本报告 | 新建 |

**为什么 UI 判据不用改前端做负对照**：写域不含 `frontend/**`，且 `_harness.degraded_dist()` 只往 `student.html` 注入 CSS，不能伪造教师端 JS 的数据。做法是**自建桩 HTTP 服务器**（照 `backend/tests/e2e_ui/test_ui_structure.py:134-191` 的 `ApiDownServer` 模板）：静态文件照真 dist，只伪造 `/api/papers` 的 JSON，于是「桩撒谎」的两种世界都能在**真实界面**上验证。

---

## §5 验证命令与原始输出

### 5.1 后端判据（判据 1–7）

```
& $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend -p "test_paper_archive_acceptance.py" -v
```

→ `Ran 26 tests in 15.826s` / `OK` / `exit 0`（`artifacts/task36-backend-acceptance.log`）
类分布：`TestContractSurface` 2 + `TestArchiveRoundTrip` 7 + `TestDeleteBoundary` 6 + `TestHasAttemptsTruth` 3 + `TestArchivedPaperStaysAnswerable` 3 + `TestOldDatabaseGuard` 2 + `TestHasAttemptsIsBatched` 3 = 26。

判据 4 的独立佐证：删除路径的断言**不只看 HTTP 状态码**，还用 `sqlite3.connect("file:…?mode=ro", uri=True)` 直读库文件，核对 `attempt`/`attempt_item`/`paper`/`paper_item` 行数与值——409 那条要求「成绩一个字都没变」，204 那条要求级联清空。

### 5.2 教师端 UI 判据（判据 8）

```
& $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend -p "test_paper_archive_ui.py" -v
```

→ `Ran 7 tests in 12.744s` / `OK` / `exit 0`（`artifacts/task36-ui.log`）

`artifacts/task36-ui-evidence.json` 关键读数：

```json
{"rows": {"有成绩卷": {"delete_disabled": true, "guard": "blocked",
   "guard_title": "这套卷已有学生成绩，不能删除；可以归档。",
   "reason_text": "已有学生成绩，不能删除", "present": true},
  "没成绩卷": {"delete_disabled": false, "guard": "allowed", "guard_title": null, "reason_text": null}},
 "clean_delete_is_operable": {"confirm_text": "删掉《UI 没成绩卷》就找不回来了。\n确认删除\n取消"},
 "archive_tabs": {"default_state": "exclude", "summary": "第 1–1 / 共 1 套（已归档）"},
 "paging": {"page_info": "每页 20 套 · 第 1 页",
   "requests": ["…archived=exclude&limit=20&offset=20", "…archived=only&limit=20&offset=20", "…archived=only&limit=20&offset=0"]}}
```

- 有成绩卷：`disabled` + `data-delete-guard='blocked'` + `title` 是人话理由 + 行内可见理由「已有学生成绩，不能删除」；
- 干净卷：可点，确认条文案正确；
- **撒谎的桩**（101 谎称 `has_attempts=false`、102 谎称 `true`）：判据红 **7 条**，两个方向都点名（`有成绩的卷：删除按钮**没有事先禁用**…`、`没成绩的卷：删除按钮也被禁用了 —— 恒禁用等于所有卷都删不掉`）；
- 切档回第一页：请求日志显示 `offset=20`（第 2 页）→ 切「已归档」→ 过渡请求 `offset=20`（前端先带旧 offset 发了一次，见 §7 边界）→ 稳定在 `offset=0`，页面显示「第 1 页」。

### 5.3 负对照原文（节选，全文见日志）

- 判据 2：`status` 被当成第三种归档态 → 红；归档其实没生效 → 红；取消归档没清时间戳 → 红。
- 判据 4：静默删掉有成绩的卷 → 红；被拒的删除改动了成绩 → 红；错误码写错（`PAPER_HAS_ATTEMPTS` 之外）→ 红。
- 判据 5：恒真世界 → 红；恒假世界 → 红；互换取值 → 红 2 条且无「恒值」条。
- 判据 6：注入 archived 检查 → 同一条判据红并具名；学生流静默失败（打开 200 但内容空 / 交卷没落库）→ 红。
- 判据 7：把护栏换成「查别的列」→ 红。
- 判据 8：每行一条查询的世界 → 被计数抓住（红）；第二页不乘倍 → 绿。

### 5.4 三类门禁 + V8 夹逼（一个运行窗口内一次跑完）

后台 `pwsh-685`：`HASH_PRE_EXIT=0` / `FRONTEND_EXIT=0` / `BACKEND_EXIT=0` / `HASH_POST_EXIT=0`。

- 全量后端：`discover -s backend/tests -t backend` → **`Ran 980 tests in 307.184s` / `OK`**（`task36-full-backend.log`）。上一轮基准 947 → **947 + 33 = 980**，正好等于本轮新增的 26 + 7。
- 前端：`pnpm --dir frontend verify` → exit 0，12 步 ①–⑫ 全 `PASS`（⑫ = mock↔真实后端逐字段比对），末行「全部验收通过」；该门禁自己的源码夹逼 `65 个文件 / 6D16D9CCD8F570916D28D96A86D1A996FE1B879BAEECD19D2487F1A215AEAE99`，`T1 == T3`（`task36-frontend-verify.log`，904 行）。
- V8 夹逼（`backend/tests/e2e_m4/artifacts/agg_hash.py`，跑前后各一份）：

| 组 | pre | post | 判定 |
| --- | --- | --- | --- |
| `backend_app` | `4b82f0472ea589e5` (59 文件) | 同 | 不变 ✅ |
| `backend_tests_all` | `4be733a6deee8302` (97) | 同 | 不变 ✅ |
| `backend_tests_others` | `f84d4072ce9bb3e1` (87) | 同 | 不变 ✅ |
| `e2e_m4_scope` | `f939ac39e6c22a13` (11) | 同 | 不变 ✅（本任务写的三文件已在 pre 快照内，运行期间未再改盘） |
| `frontend_src` | `82bfc27102235477` (49) | 同 | 不变 ✅ |
| `frontend_dist` | `22707d639b7582f2` (8) | 同 | 不变 ✅ |

写盘后复核（`hash-task36-post2.json`）：除 `e2e_m4_scope` 由 **11 → 12 文件**外，其余五组与 post 逐字节相同。多出来的那一个文件**就是本报告自身**（`d953763fb63b76f2`）——即门禁跑完之后**没有任何代码被改动**，唯一变化是报告落盘这个自指项（与 task-37 同一形态，不掩盖）。

落盘后域内再跑一次：`discover -s backend/tests/e2e_m4 -t backend` → **`Ran 62 tests in 56.386s` / `OK` / `exit 0`**（`artifacts/task36-e2e-m4-final.log`；62 = 原 29 + 本任务 26 + 7），证明最终树在写域内全绿。

---

## §6 判据自身缺陷（写判据时抓到的，V9 家族）

测试第一次跑就绿的新判据不可信，所以**先把假红/假绿都抓出来**。本轮抓到 6 条：

1. **形参名不一致**：`_archive_support.ArchiveServer.request()` 的形参是 `json_body`，助手里写成 `json=` → 6 条 `TypeError: request() got an unexpected keyword argument 'json'`。只在运行时炸，静态检查看不到。
2. **凭猜测用返回值**：`_harness.UiServer.publish()` 返回的是**解析好的 dict**，我按 `Response` 又调了 `.json()` → 2 条 `AttributeError: 'dict' object has no attribute 'json'`。教训：助手的行为照实现读，不照印象猜。
3. **互换 ≠ 恒值**：纯函数负对照喂 `{"with_attempts": False, "without_attempts": True}` 时，只应红 2 条方向性错误；我原断言 3 条（把「两值不等」也算进去）是错的。**期望条数要按逻辑推**，否则负对照本身也在说谎。
4. **老库构造必须用冷引擎**：`ensure_paper_schema` 用 `weakref.WeakSet` 记住「本引擎查过列」。我在**同一个引擎**上先建列再 `DROP`，护栏缓存是热的 → 直接被短路，报 `sqlite3.OperationalError: no such column: paper.archived_at`。**这不是产品缺陷，是我构造出来的假象**——真实的「新进程打开旧库」必须换一个引擎对象（改用磁盘库文件 + 新 `create_engine`）。
5. **桩服务器少了 `import json` → 前端静默降级**：`LibraryStubServer._send_json` 抛 `NameError: name 'json' is not defined`，前端 `frontend/src/shared/apiClient.ts:219-237` 的 `detectPaginationMode()` 探测失败后缓存 `'legacy'`，页面改用 `size`/`page` 参数 → `library-error` 显示 `NETWORK_UNREACHABLE：连接不上后端：/api/papers?size=20&page=1&archived=exclude`，等待 20 秒超时。**故障点离原因三层远**，靠 `artifacts/probe_ui_stub.py`（读页面正文 + 桩收到的请求）才定位。修法：桩补 `import json`；桩记录 `all_requests`；`_open()` 超时时打印页面正文与桩请求日志，把「静默超时」改成「自解释失败」。
6. **等待里的 JS 读了 `null.textContent`**：重渲染瞬间 `library-page-info` 会短暂不存在 → `Cannot read properties of null (reading 'textContent')`。修法：`(document.querySelector(...)?.textContent ?? '').includes(...)`；切档的状态读取同样 `?.dataset.state ?? ''`。

---

## §7 方法边界（哪些结论**不**成立）

- **判据 1、8 走进程内**（`backend/tests/api/_support.make_client`，TestClient + 内存 SQLite）；**判据 2–7 走真机 uvicorn**（磁盘库文件 + 真 HTTP），其中删除/归档的库侧结论用**只读 SQLite 直查**独立佐证。两类互补，不能互相替代。
- 「归档不影响学生端」的**负对照是注入**，它证明的是「判据抓得住产品某天真的加了归档拦截」；**不**证明生产路径上不存在别的拦截（例如将来新增的中间件）。这是判据形态的边界，不是漏测。
- UI 判据跑真实 `frontend/dist` 产物 + 真后端；撒谎世界用**自建桩**（只伪造 `/api/papers` 的 JSON，不伪造 dist）。因此**不覆盖**：前端构建产物与源码不同步的情形（由前端门禁的 freshness 负责）。
- **切档回第一页**的判据允许一次**过渡请求**：`archived=only&limit=20&offset=20` 会先发出去（前端先按旧 offset 渲染/请求，随后 `useEffect` 复位到 `offset=0`）。判据只断言**稳定后**的 `offset=0` 与「第 1 页」文案；把「一个过渡请求都不许有」当判据会误红。
- **`has_attempts` 批量取数**用 SQLAlchemy `before_cursor_execute` 语句计数自证（不采信实现者的汇报）。覆盖**列表页第一页与翻页**两种形态，不覆盖其它端点/其它查询路径。
- 前端分页存在 `legacy`（`size`/`page`）回退：桩必须回显 `limit`，否则前端静默走 legacy。这条已固化成桩的实现约束，并把「日志里出现 `size=`」当成降级的证据。
- **未覆盖**：真实手机/真机浏览器（task-30、task-33 的范畴）、并发归档/删除同一卷、Excel/docx 导出路径。
- 本任务是**验收**，不是修复：`backend/app/**` 一行未动（夹逼 `backend_app` pre == post 佐证）；发现的问题只登记不代改。

---

## §8 运行窗口内写者情况（V7）

- `git status --short`：我的三个新文件均为未跟踪（`??`）：`backend/tests/e2e_m4/_archive_support.py`、`test_paper_archive_acceptance.py`、`test_paper_archive_ui.py`。
- 窗口内**其他写者**：fe-teacher 的前端改动已存在于工作树（`frontend/scripts/{run-all-verifications,verify-negative-controls,verify-server,verify-ui-voice}.mjs`、`frontend/scripts/dist-freshness.mjs`(新增)、`frontend/src/shared/{api.ts,apiClient.ts,queryKeys.ts}`、`frontend/src/teacher/pages/LibraryPage.{tsx,css}`），但**在我的运行窗口内没有写盘**。
- 判定依据：`frontend/scripts/**` 的最新 mtime 是 **2026/10/15 22:28:29**（`run-all-verifications.mjs`），**早于**我的 pre 快照（约 22:41）。`frontend/scripts/**` 不在任何哈希组内，所以这里用 mtime 而不是哈希判定。
- 结论：**task-38 未在我的窗口内改动 `frontend/scripts/**`**；前端门禁那一步的结论按 V7 成立（`frontend_src` 夹逼 pre == post 也独立支持这一点）。

---

## §9 复跑命令

```powershell
$VPY = "backend\.venv\Scripts\python.exe"   # 实际路径以仓库内 venv 为准
cd mathgen

# 后端判据（判据 1–7）
& $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend -p "test_paper_archive_acceptance.py" -v

# 教师端 UI 判据（判据 8；需要 frontend/dist 是新的）
& $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend -p "test_paper_archive_ui.py" -v

# UI 假红排查探针（可重跑）
& $VPY backend/tests/e2e_m4/artifacts/probe_ui_stub.py

# 全量后端门禁（判据 9a）
& $VPY -m unittest discover -s backend/tests -t backend

# 前端门禁（判据 9b）
pnpm --dir frontend verify

# V8 夹逼
& $VPY backend/tests/e2e_m4/artifacts/agg_hash.py task36-pre
# …跑门禁…
& $VPY backend/tests/e2e_m4/artifacts/agg_hash.py task36-post
```

---

## §10 写盘清单（自报，供 Lead 对账）

新建（写域内）：

- `backend/tests/e2e_m4/_archive_support.py`
- `backend/tests/e2e_m4/test_paper_archive_acceptance.py`
- `backend/tests/e2e_m4/test_paper_archive_ui.py`
- `backend/tests/e2e_m4/REPORT-paper-archive.md`（本文件）
- `backend/tests/e2e_m4/artifacts/`：`task36-backend-acceptance.log`、`task36-ui.log`、`task36-ui-evidence.json`、`task36-ui-stub-probe.log`、`probe_ui_stub.py`、`task36-full-backend.log`、`task36-frontend-verify.log`、`task36-hash-pre.log`、`task36-hash-post.log`、`hash-task36-pre.json`、`hash-task36-post.json`、老库用的临时 `.db`（测试自带 `tearDown` 删除，残留会被清）

**未动**：`backend/app/**`、`backend/app/contracts/**`、`backend/tests/api/**`、`backend/tests/contract/**`、`backend/tests/security/**`、`backend/tests/e2e_ui/**`、`frontend/**`、`docs/**`。

注：全量后端门禁会重写 `backend/tests/security/artifacts/evidence-tests.json`（task-25 用例自身的产物，不是我的写盘）。
