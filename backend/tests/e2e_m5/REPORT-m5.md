# task-42（T-M5-04）独立验收报告：成绩列表 / 逐题明细 / 导出（FR-T11 / FR-T12 / FR-T13）

- 验收人：verify-m1（独立验收，不是实现者）
- 时间：2026-10-16（Asia/Shanghai）
- 对象：HEAD `9f533f7`（M5 实现已合入）+ 写域内新建的判据
- 写域：只新建/修改 `backend/tests/e2e_m5/**` 与 `docs/06-测试方案.md` §9.11
- 一行结论：**通过** —— Lead 派的 8 条验收要求逐条达成（§3），board 卡 8 条逐条对表也全中（§3.3），
  四道闸门一个窗口内全绿（§5），V8 夹逼六组逐字节相同（§5.4）。
  新增判据 **50 条**（后端 42 + 教师端界面 8），其中负对照 20 条（另加 1 条注入式负对照）。

---

## §0 一屏结论

| 项 | 结果 | 原始证据 |
| --- | --- | --- |
| 判据 0 契约面（哈希两副本一致 + 三路由） | `TestContractSurface` 2 条 OK | `artifacts/task42-gate1-e2e-m5.log` |
| 判据 1 后端（得分单一来源 + 注入式负对照） | `TestScoreSourceIsSingle` 6 + `TestAnInjectedDeviationIsCaught` 1 条 OK | `artifacts/task42-gate1-e2e-m5.log`、`artifacts/task42-score-one-source.json`、`artifacts/task42-injected-deviation.json` |
| 判据 2–5、7、8 后端 | `TestListDetailReconciliation` 7 + `TestPagingAtTheSameInstant` 4 + `TestAttemptBoundaries` 6 + `TestExportAcceptance` 13 + `TestSelfPractice` 3 = **33 条 OK**（连同第 17/18 行 2 + 7，后端合计 **42**） | 同上 + `artifacts/task42-*.json` |
| 判据 1/5/6 界面侧 | `test_m5_ui_acceptance.py` 8 条 OK（真 dist + 真浏览器 4 + 撒谎桩 2 + 空态 2） | `artifacts/task42-ui-evidence.json` |
| 闸门① e2e_m5 discovery | `Ran 50 tests in 40.618s` / `OK` / `exit 0` | `artifacts/task42-gate1-e2e-m5.log` |
| 闸门② 全量后端 | `Ran 1111 tests in 476.887s` / `OK` / `exit 0`（1061 + 50） | `artifacts/task42-gate2-backend-all.log` |
| 闸门③ 前端门禁 | `pnpm --dir frontend verify` `exit 0`，12 步 ①–⑫ 全 PASS，末行「全部验收通过」 | `artifacts/task42-gate3-frontend-verify.log` |
| 闸门④ V8 哈希夹逼 | T1 == T3 == T4，六组逐字节相同（含 `frontend_dist`） | `artifacts/hash-T1.json`、`hash-T3.json`、`hash-T4.json`、`artifacts/task42-hash-T{1,3,4}.log` |
| `backend/app/**` 与 `frontend/src/**` 是否被本任务改动 | **没有**（夹逼 `backend_app` / `frontend_src` pre == post） | 同上 |

**得分口径的实证锚点**（判据 1 的核心）：同一份成绩 `total=40, correct=23`，
提交响应 / 列表 / 明细 / CSV **四处都是 58**；而 M5 之前的旧浮点口径
（`Math.round(correct / total * 100)`）在这个点上是 **57**（精确值 `57.49999999999999`）。
证据原文：`artifacts/task42-score-one-source.json`（`observed` 四项全 58、`old_js_would_say: 57`、`problems: []`）。

---

## §1 任务与由来

M5 交付三条需求，D80 给了它们**唯一**的得分口径：

1. **FR-T11 成绩列表** —— 教师端「全部成绩」：分页、按卷号收窄、按姓名/学号搜索；
2. **FR-T12 逐题明细** —— 每条成绩可展开到逐题对错；
3. **FR-T13 导出** —— 当前筛选下的**全部**成绩，CSV（带 BOM，Excel 双击不乱码）或 xlsx。

D80（`docs/00-决策记录.md:91`）把「得分」定成 `score_of(total, correct)` **一个函数**
（`backend/app/contracts/api.py:178-207`）。风险在于：M5 之前**结果页自己算分**
（旧 `Math.round(correct / total * 100)`），两套口径在总分非 100 的卷上真的分叉
（`(40,23)` 旧 57 / 新 58；`(80,46)` 同理）。若界面上还留着第二处算法，
「一处改成后端、一处没改」会让同一份成绩在列表与结果页显示两个数字。

本任务是**独立验收**：实现由 M5 实现方交付（`docs/05-任务板.md` T-M5 段），
判据由本任务新写，**不采信实现方汇报**——每一条都用真机 HTTP / 真库 / 真浏览器复现。

**只读域**（本任务一行未动）：`backend/app/**`、`frontend/**`、`docs/00`、`docs/04`、`docs/05`。

---

## §2 现场形态（实现侧锚点，行号已 grep 复核）

| 能力 | 位置 |
| --- | --- |
| 唯一算分函数 | `backend/app/contracts/api.py:178-207`（`if total <= 0: return 0`；`clamped = max(0, min(correct, total))`；`return (clamped * 200 + total) // (2 * total)` = 四舍五入到整数） |
| 列表契约 | `api.py:229-248 AttemptSummary`（含 `score`）、`:251-262 AttemptListOut(total, limit, offset, attempts)`、`:265-272 AttemptDetailOut` + `items` |
| 路由表 | `api.py:55-57`（`export_attempts` **在** `get_attempt` 之前注册） |
| 列表取数 | `backend/app/services/attempt_service.py:200-218 list_attempts(db, *, paper_id, q, limit=DEFAULT_LIMIT, offset=0)`；计数在分页**之前**（`:291-308`）；`order_by(submitted_at.desc(), id.desc())` |
| 导出取全量 | `attempt_service.py:221-234 list_attempts_all(db, *, paper_id, q)`（`limit=None`）→ `_query_attempts` 只在 `limit is not None` 时套 `LIMIT/OFFSET`（`:291-308`） |
| 明细取数 | `attempt_service.py:237-257 get_attempt_detail(db, attempt_id)`；`total` 取 `attempt.total`，**故意不取 `len(items)`** —— 这样「明细被截断」才可能被抓住 |
| 通配符字面量 | `attempt_service.py:311-325 _conditions` → `:328-339 _like_pattern`（转义 `\`、`%`、`_`，`escape=_LIKE_ESCAPE`） |
| limit/offset 边界 | `backend/app/routers/attempts.py:68-94 list_attempts`（`Query(ge=1, le=MAX_LIMIT)` / `offset ge=0` / `paper_id ge=1` / `q max_length=200`） |
| 明细 404 | `backend/app/routers/attempts.py:97-108` → `ATTEMPT_NOT_FOUND` |
| 导出路由 | `backend/app/routers/exports.py:150-175 export_attempts(file_format alias format, paper_id, q, db)` —— **签名里没有 `limit`/`offset`**；`:77-92 parse_attempts_format`（非法值 → `VALIDATION_ERROR`） |
| CSV 字节级 | `backend/app/export/attempt_export.py:161-171 _export_csv`（`csv.writer(lineterminator="\r\n")` + `encode("utf-8-sig")` = BOM）；列序 `:51-61 COLUMNS`；`filename_for:137-140`；`content_disposition:143-155` |
| 界面行 | `frontend/src/teacher/pages/AttemptsPage.tsx:362-403`（`data-score`(371)、`attempts-score`(391) 全是 `String(attempt.score)`）；请求 `:137-140` 只发 `limit/offset`（+ 可选 `paper_id`/`q`） |
| 学生端结果页 | `frontend/src/student/pages/ResultPage.tsx:54-58`（`const score = result.score;`，注释「D80：不再用 `correct / total` 复算」） |

**判据 1 的全仓 grep（算分代码的第二处）**：`frontend/src/**/*.{ts,tsx}` 里
`Math.round|/ total|correct|.score` 的 46 处命中，**没有**任何一处把 `correct/total` 算成分数：

* 渲染分数字段的只有三处，且都是直取后端字段：`AttemptsPage.tsx:371,391`（`String(attempt.score)`）、
  `AttemptDetailPage.tsx:98`（`{detail.score}`）、`ResultPage.tsx:58`（`result.score`）；
  `frontend/src/shared/apiClient.ts:304` 只做 `typeof row.score !== 'number'` 的**类型校验**。
* 剩下的 `correct`/`total` 算术都**不是分数**：`ResultPage.tsx:54`（把 `correct` 夹到 `[0, total]`
  用于「答对 N 题」文案）、`AttemptDetailPage.tsx:53`（`total - correct` = 答错**题数**）、
  `AttemptsPage.tsx:392`（`${correct} / ${total}` 的显示串）、
  `frontend/src/student/QuestionRunner.tsx:67`（`Math.round(((index + 1) / total) * 100)` = **进度百分比**）。

---

## §3 八条验收要求 ↔ 用例映射

| # | 要求（Lead 派单） | 用例 | 走了什么 |
| --- | --- | --- | --- |
| 1 | D80 得分单一来源 | `TestScoreSourceIsSingle`（6）+ UI `TestScoreUiWithRealBackend.test_01`、`TestScoreUiAgainstADishonestStub`（2）+ `artifacts/score_enumeration.py` | 独立复述枚举（**不 import** 产品函数）+ 真机 uvicorn + 真 `frontend/dist`/真 playwright + 撒谎桩 |
| 2 | 导出导「当前筛选下全部」 | `TestExportAcceptance::test_export_covers_everything_under_the_filter`、`test_export_filters_match_the_list_filters`、`test_negative_control_an_export_that_respected_the_page_is_caught`、`test_negative_control_a_fixture_smaller_than_one_page_is_caught` | 真机 uvicorn；夹具 25 条 > `limit=20`；覆盖面断言用响应**回显的** `limit=20` 证明「分页真的生效了」 |
| 3 | CSV 前 3 字节 `EF BB BF` | `test_csv_starts_with_a_utf8_bom`、`test_negative_control_a_csv_without_the_bom_is_caught` | 真机 + 字节级断言 |
| 4 | `total == len(items)` 对账 + **真删一行**能红 | `TestListDetailReconciliation`（7，含 `test_deleting_a_real_attempt_item_makes_the_same_checker_red`） | 真机 + **只读 SQLite** 直查库文件 + 真删一行再**原样插回** |
| 5 | 同刻翻页不重不漏 | `TestPagingAtTheSameInstant`（4） | 真机；7 条成绩固定同一 `submitted_at` |
| 6 | 自主练习三字段 null + 文案不来自后端 + 界面不出现 `undefined/null/NaN` | `TestSelfPractice`（3）+ UI `test_03`、`test_04` | 真机 + 真浏览器（读行内文本与 DOM 属性）+ 纯函数负对照 |
| 7 | 404 `ATTEMPT_NOT_FOUND` / `limit` 越界 422 / 非法 `format` 422 | `TestAttemptBoundaries`（6）+ `TestExportAcceptance::test_invalid_format_is_422` | 真机 |
| 8 | `q='%'`、`q='_'` 按字面量 | `TestAttemptBoundaries::test_q_wildcards_are_literal`、`test_paper_filter_is_exactly_that_paper`、`test_negative_control_an_unescaped_wildcard_world_is_caught` | 真机（学生名/学号里真的含 `%` 与 `_`） |

### 3.1 判据 1 的完整证据链（三条腿，缺一条都不算）

* **腿 A：分叉点独立枚举。** `artifacts/score_enumeration.py` 用**自己写的**两个实现
  （契约整数式 vs 旧 JS 浮点式）在 `total ∈ [1, 200] × correct ∈ [-2, total+2]` 全网格上扫：
  `total<=200` 恰 **8** 个分叉点、`total<=100` 恰 **2** 个 = `(40,23)` 与 `(80,46)`；
  8 个点**全部**满足「精确值正好 `.5` 且浮点略低」。再用 `app.contracts.api.score_of`
  与自己的复述逐点比对，**0 处分歧**（`test_product_score_of_agrees_with_my_independent_restatement`）。
  日志 `artifacts/task42-score-enumeration.log`。
* **腿 B：四处同数。** `test_score_is_one_number_across_submit_list_detail_and_csv`（`(40,23)` → 58）
  与 `test_the_second_real_world_point_is_also_58_on_the_real_backend`（`(80,46)` → 58）；
  证据 `artifacts/task42-score-one-source.json`。
* **腿 C：界面不可能自己算。** 两个世界：
  (a) 真后端 `(40,23)`，行上 `data-score` 与 `attempts-score` 单元格都必须是 `"58"`
  —— 界面若自己算会是 `"57"`；(b) **撒谎的桩**只换 `/api/attempts` 的 JSON：
  `score=91`（`(40,23)` 下**任何本机算法都推不出来**）→ 页面必须显示 91，
  且断言桩确实收到了 `/api/attempts` 且首条请求是 `limit=20&offset=0`
  （证明判据打在真页面上，不是空转）。证据 `artifacts/task42-ui-evidence.json`。

### 3.2 负对照 20 条（V9 / V9.1 / V9.2 的落地）

后端 16 条（`test_m5_attempts_acceptance.py`）：本地算分、无观测、缺输入、
互换 `correct`、截断明细、行内本地算分、漏一页、重复一行、页码 `total` 不符、
未转义通配符、后端下发「自主练习」文案、导出套用分页、夹具不足一页、去 BOM、
表头乱序、截断 CSV；界面 4 条（`test_m5_ui_acceptance.py`）：本地算 57/58、
渲染出 `undefined`、空态里出现开发者术语。（计数口径：用例名含 `negative` 的机器判据，
用 AST 数出来，不靠人眼。）

### 3.3 board 卡 8 条逐条对表（卡里的措辞比派单更硬，这里如实对照）

| # | 卡里的要求 | 落在哪 | 结果 |
| --- | --- | --- | --- |
| 1 | `backend/tests/contract/**` 全绿 + 哈希 `31202c98…` **两处副本必须一致** + ROUTES 三键与前端门禁②一致 | 闸门②（`tests/contract/**` 在其内）+ **新增** `TestContractSurface::test_the_frozen_hash_copies_agree_with_each_other_and_the_file`、`test_the_three_m5_routes_exist_and_the_export_route_is_its_own_path`；前端侧一致由闸门③ 第 ② 步 PASS 作证 | 通过 |
| 2 | 对账必须造出「不一致就会红」的世界 | `TestListDetailReconciliation`（真删一行 → 4 条具名不符，§3.2 引的那段原文） | 通过 |
| 3 | 三处同分 + `.5` 边界（`(40,23)`→58、`(80,46)`→58） | `TestScoreSourceIsSingle`（6）+ `artifacts/score_enumeration.py` | 通过 |
| 4 | CSV 前 3 字节 BOM、列序逐字符合 §5.5、自主练习行卷号卷名为空、总数 > 一页时导出条数 > `limit` | `TestExportAcceptance`（13） | 通过 |
| 5 | 没有成绩的卷在成绩页上**空态可读**，不是白屏或开发者术语 | **新增** `TestEmptyStateIsReadable`（2）：真 `frontend/dist` + 空列表桩，断言 `data-state="empty"`、文案含「还没有学生做过这套卷」、≥1 个可点的下一步、无 `[data-attempt-id]`、**不是**错误态、正文无 9 个开发者词 | 通过 |
| 6 | 注入式负对照（让 `score` 偏离 `score_of()`，判据要红并具名，贴两态原文） | **新增** `TestAnInjectedDeviationIsCaught`：`mock.patch.object(attempt_service, "score_of", …)` 让**产品代码**给 999 分 → 三条具名问题；两态原文见 §3.4 与 `artifacts/task42-injected-deviation.json` | 通过 |
| 7 | 至少一部分判据走真机 uvicorn + 磁盘库，并说明谁走真机谁走 TestClient | **真机 uvicorn + 磁盘 SQLite 43 条**（后端 35 + 界面 8，全走 `M5Server`/`UiServer`）；**进程内 TestClient 5 条**（翻页 4 条：要把多条 `submitted_at` 改成**同一个瞬间**，只能直接 `UPDATE` 库；注入 1 条：`mock.patch` 打不进别的进程）；**纯静态 2 条**（`TestContractSurface`：读守卫文件 + 读契约） | 通过 |
| 8 | 全量门禁 + 前端 verify + V8 夹逼，说明谁跑的、窗口内有无写者 | §5、§8（窗口内另有 Lead 一次 `docs/02` 写入，不在任何哈希组里） | 通过 |

### 3.4 注入式负对照的两态原文（卡第 6 条）

判据 `TestAnInjectedDeviationIsCaught` 在**同一个世界**上跑两遍，中间把产品代码里的算分函数
换掉（`app.services.attempt_service.score_of` → 「永远给 999」）。原文
`artifacts/task42-injected-deviation.json`：

```
# 注入前（未打桩，score_of(4,2) = 50）
observed = {"提交响应": 50, "列表": 50, "明细": 50}      problems = []

# 注入后（服务层下发 999）
observed = {"提交响应": 999, "列表": 999, "明细": 999}
problems = [
  "列表 的 score=999，按 D80 独立复述应为 50（total=4 correct=2；M5 之前的旧算法在这里给 50）",
  "提交响应 的 score=999，按 D80 独立复述应为 50（…）",
  "明细 的 score=999，按 D80 独立复述应为 50（…）",
]
```

判据断言的是「注入后**恰好 3 条**具名问题」——只报 1 条（说明有观测点没被守护）或 0 条
（空转）都不算通过。

**另有一条「真注入」负对照**（不是喂错误输入）：`test_deleting_a_real_attempt_item_makes_the_same_checker_red`
在共享世界里**真的**删掉 `attempt 32` 的一行 `attempt_item`，同一条对账判据随即变红并具名：

```
明细 len(items)=4 ≠ detail.total=5        （明细被截断）
明细 len(items)=4 ≠ 列表 total=5          （列表与明细打架）
明细里 is_correct 为真的条数=2 ≠ 列表 correct=3
库里 attempt_item 行数=4 ≠ attempt.total=5 （判据直查库文件）
```

（原文 `artifacts/task42-real-deletion-negative-control.json`；`finally` 里把该行**原样插回**，
插回后再跑一次同一判据必须绿，否则报「删行用例把世界搞脏了」。）

---

## §4 改动清单（全部在写域内）

| 文件 | 性质 |
| --- | --- |
| `backend/tests/e2e_m5/__init__.py` | 新建。缺它时 `discover -s backend/tests/e2e_m5 -t backend` 直接 `ImportError: Start directory is not importable` |
| `backend/tests/e2e_m5/_m5_support.py` | 新建。判据设施：真机 HTTP 助手 `M5Server(UiServer)` + **只读 SQLite** 取证 + 纯判据函数（`score_source_problems` / `list_detail_problems` / `db_cross_check_problems` / `paging_problems` / `csv_problems` / `export_coverage_problems` / `practice_contract_problems` / `rendered_text_problems` / `wildcard_problems` / `score_render_problems`）+ 撒谎桩 `AttemptsStubServer` + 进程内 `InProcessApp` |
| `backend/tests/e2e_m5/test_m5_attempts_acceptance.py` | 新建。42 条（判据 0–5、7、8 的后端侧；含静态契约面 2 条与注入式负对照 1 条） |
| `backend/tests/e2e_m5/test_m5_ui_acceptance.py` | 新建。8 条（判据 1 / 5 / 6 的界面侧：真后端 4 + 撒谎桩 2 + 空态 2） |
| `backend/tests/e2e_m5/artifacts/score_enumeration.py` | 新建。分叉点独立枚举（可重跑） |
| `backend/tests/e2e_m5/artifacts/agg_hash.py` | 新建。V8 夹逼工具（可重跑，`--diff T1 T3`） |
| `backend/tests/e2e_m5/artifacts/task42-*.log`、`task42-*.json`、`hash-T{1,3}.json` | 取证产物 |
| `docs/06-测试方案.md` §9.11 | 新建一节（插在 §9.10 之后、`## 10. 性能测试（NFR-4）` 之前） |
| 本报告 | 新建 |

**为什么界面判据要自建桩**：写域不含 `frontend/**`，且 `_harness.degraded_dist()` 只往
`student.html` 注入 CSS，不能伪造教师端 JS 拿到的数据。做法是照
`backend/tests/e2e_m4/_archive_support.py:428-522 LibraryStubServer` 的模板自建
「真 dist + 只伪造 `/api/attempts`」的桩服务器（真 dist 一个字不改）。

---

## §5 验证命令与原始输出

### 5.1 闸门①：e2e_m5 discovery（50 条）

```
> python -m unittest discover -s backend/tests/e2e_m5 -t backend -v
Ran 50 tests in 40.618s
OK                                          [exit 0]
```

（`-t backend` 必须在位置参数之前；日志 `artifacts/task42-gate1-e2e-m5.log`）

### 5.2 闸门②：全量后端（1111 条）

```
> python -m unittest discover -s backend/tests -t backend -v
Ran 1111 tests in 476.887s
OK                                          [exit 0]
```

1061（M5 前）+ 50（本任务新增）= 1111，正好等于新增数。
仅剩 `ResourceWarning: unclosed file`（`_harness` 里 uvicorn 日志句柄，非失败）。
日志 `artifacts/task42-gate2-backend-all.log`。

### 5.3 闸门③：前端门禁（先 `build` 后 `verify`）

```
> pnpm --dir frontend build      # 必须在夹逼窗口之前（frontend_dist 是哈希组之一）
✓ 129 modules transformed / ✓ built in 1.99s      [exit 0]
> pnpm --dir frontend verify
  PASS ①…⑫ 十二步
  PASS  源码在门禁运行前后未被改写（T1 == T3）
  全部验收通过。                                  [exit 0]
```

日志 `artifacts/task42-frontend-build.log`、`artifacts/task42-gate3-frontend-verify.log`。

### 5.4 闸门④：V8 哈希夹逼（pre == post）

```
backend_app            7484893D110D1AF3  (60 files)    SAME
backend_tests_ex_m5    F2CFAD4560E4573B  (99 files)    SAME
backend_tests_all      F3D9FE1CC60075DF  (103 files)   SAME
e2e_m5_scope           4ED65F66C06C8DE8  (4 files)     SAME
frontend_src           1A36AE341A173870  (52 files)    SAME
frontend_dist          F0FD0B90C5A46790  (8 files)     SAME
T1 -> T3: 全部一致 —— 窗口内没有写入被验对象
```

口径见 `artifacts/agg_hash.py` 头注释；`artifacts/`（运行期证据）已排除，
否则窗口内写的证据 JSON 会让夹逼**假红**。快照 `hash-T1.json` / `hash-T3.json`，
日志 `artifacts/task42-hash-T{1,3}.log`、`task42-hash-diff-T1-T3.log`。

**补拍一张 T4（本报告与 `docs/06` 写完后再拍）**：`T3 -> T4: 全部一致`，六组仍逐字节相同
（`artifacts/hash-T4.json`、`artifacts/task42-hash-T4.log`）—— 即「窗口后我写的报告与文档
一个字都没碰被验对象」，T1==T3 的结论不是靠时序侥幸得来的。

### 5.5 现场读数（判据写死前逐条核对过，不是猜的）

| 事实 | 读数 |
| --- | --- |
| 提交响应形状 | `AttemptResultOut{attempt_id, total, correct, score, items}` —— **没有** `id`、没有 `duration_ms`/`submitted_at` |
| 错误响应 | **信封**：`{"error": {"code": …, "message": …}}`（契约 `docs/04-接口契约.md:300`） |
| 404 原文 | `{'error': {'code': 'ATTEMPT_NOT_FOUND', 'message': '作答记录 999999 不存在'}}` |
| 422 原文 | `{'error': {'code': 'VALIDATION_ERROR', 'message': '请求参数校验失败：query.limit: Input should be greater than or equal to 1'}}` |
| `limit` 边界 | `{0, -1, 101, 1000, offset=-1} → 422`；`{1, 100} → 200`（`artifacts/task42-boundary-limits.json`） |
| 同刻翻页 | 7 条成绩同一 `submitted_at`，期望序 `[7…1]`，实际每页 `[[7,6],[5,4],[3,2],[1]]`，无重无漏（`artifacts/task42-same-instant-paging.json`） |
| 导出覆盖面 | 列表 `total=25`、回显 `limit=20`、页内 20 行，而 CSV **25** 行（`artifacts/task42-export-coverage.json`） |
| 学生端请求形状 | `frontend/src/shared/apiClient.ts:706-715 listAttempts` 固定发 `limit/offset`，**不做** pagination-mode 探测（只有 `listPapers:549` 用 `detectPaginationMode()`） |

---

## §6 判据自身的缺陷（写判据时抓到的，V9 家族）

1. **判据自己的公式先出错**：`score_enumeration.py` 的 `exact_half` 第一版写成
   `(correct*2)%total==0 and (correct*200)%(2*total)==total` → 8 个分叉点全判 False。
   改成 `(correct*200)%(2*total)==total` 才对。教训：宣布「预期 N 个点」之前先核对自己的公式。
2. **归因逻辑写错方向**（`score_render_problems` 第一版）：把「本地算法比对」放在
   `shown != str(expected)` 之外，导致**真实世界**（后端本来就给契约值 58）显示 58 也被判红。
   现在只有「显示值 ≠ 后端值」才继续做「界面在本地算分」的归因 —— 这正是判据 1 的语义。
3. **错误响应按扁平体读**：3 条用例写 `body.get("code")` / `"detail" in body`，
   而实际是信封 → 三条假红。契约里写得很清楚，是我没核对。
4. **助手写死 `expect=200`**：验 422 的用例在 `M5Server.list_attempts_response` 内部先炸，
   报错信息指向 helper 而不是被测行为。已给取数口子加 `expect` 参数。
5. **`BOM` 是 `bytes`**：`m5.BOM + "\n".join(...)` → `TypeError: can't concat str to bytes`。
6. **夹具互相污染**：删行用例真删了共享世界的一行且**没恢复**，按字母序跑在它之后的
   全量对账判据随之变红（4 条具体不符）。已改成 `finally` 原样插回 + 插回后自检。
7. **证据写进了别人的写域**：`tearDownModule` 用了从 `tests.e2e_m4._harness` 导入的
   `ARTIFACTS` → 证据落到 `backend/tests/e2e_m4/artifacts/task42-ui-evidence.json`。
   已改用 `m5.ARTIFACTS` 并删掉越界文件。
8. **空成绩卷的合法空导出会被误判**：`csv_problems` 原来无条件要求「至少一行」，
   零成绩卷（表头 + 无数据行是**正确**行为）会假红。已加 `expected_rows > 0` 前置。
9. **对表时说错了一句话**：§3.3 卡第 7 条我第一版写成「本套 49 条中没有一个依赖 TestClient」，
   实际 `TestPagingAtTheSameInstant` 4 条就是走进程内（要改 `submitted_at`）。已按 AST 实际
   结构改成「真机 43 / 进程内 5 / 静态 2」。教训：写「全部 / 没有一条」这类全称句之前，
   先 grep 一遍自己写过的代码，别凭印象。

---

## §7 方法边界（哪些结论**不**成立）

* **判据 1 的界面侧只证明「界面把后端的 `score` 原样呈现」**，不证明后端 `score_of` 本身正确
  —— 后者由分叉点枚举 + 全网格比对 + 全量后端门禁里的判分测试覆盖。
* **撒谎桩是注入**：它证明「界面若自己算，这条判据必红」，**不**证明生产代码里没有别的
  算分路径。后者由 §2 的全仓 grep 覆盖（人工判读，非机器门禁）。
* **注入式负对照（`TestAnInjectedDeviationIsCaught`）走进程内 TestClient，不走真机**：
  uvicorn 是另一个进程，`unittest.mock.patch.object` 打不进去。它证明的是「判据对**服务层
  下发的偏差分数**会红并具名」，**不**证明真机部署下服务层不会算出偏差 —— 后者靠闸门②
  里产品自己的判分测试与 D80 的契约哈希冻结。
* **导出「全部」的口径是「当前筛选下、后端 `list_attempts_all` 能取到的全部」**：
  夹具 25 条 > 一页 20 条，证明导出没套分页；**不**覆盖「十万行时的内存/耗时」
  （NFR-4 性能测试是另一节）。
* **xlsx 只验「是真工作簿 + 数据行数/列序对」**，不验视觉格式（字体/列宽属实现自由）。
* **已知未覆盖**：真机手机浏览器上的成绩页（属 task-30/33 范畴）、并发导出与并发提交、
  前端在 `legacy`（`size`/`page`）分页模式下的成绩页回退 —— 本任务只断言**当前**
  `limit/offset` 合同（`apiClient.ts:706-715` 不做模式探测）。
* **`q` 的 `max_length=200`、`paper_id ge=1`** 只做了 422 存在性覆盖，不验超长/越界后端的性能。

---

## §8 运行窗口内写者情况（V7）

窗口内**除我之外还有一次写入**，是 Lead 自己的，如实登记：

| 提交 | 时间 | 改了什么 | 为什么不影响本报告 |
| --- | --- | --- | --- |
| `d1bbf9c` `docs(02): 同步 M5 —— §7.1 API 路径订正、§8.1 路由补全、§3.2 目录树、§14 覆盖矩阵` | 本窗口内 | **只改 `docs/02-技术设计.md`** | 它不在本任务夹逼的任何一组里（`backend_app` / `backend_tests_*` / `e2e_m5_scope` / `frontend_src` / `frontend_dist`），也不碰 `docs/06`；V8 六组 pre == post 已佐证窗口内被验对象零改动 |

除此之外窗口内只有**我**的写入，且全部落在 `backend/tests/e2e_m5/**`（§10）。
窗口内**没有**任何 `backend/app/**`、`frontend/**`、`docs/04`、`docs/00` 的写入 ——
V8 夹逼的 `backend_app` 与 `frontend_src` pre == post 是机器证据。

---

## §9 复跑命令

```powershell
$VPY = "backend\.venv\Scripts\python.exe"

# 后端判据（判据 1–5、7、8）
& $VPY -m unittest discover -s backend/tests/e2e_m5 -t backend -p "test_m5_attempts_acceptance.py" -v

# 界面判据（判据 1 / 6 界面侧；需要 frontend/dist 比 src 新）
& $VPY -m unittest discover -s backend/tests/e2e_m5 -t backend -p "test_m5_ui_acceptance.py" -v

# 分叉点独立枚举（判据 1 的腿 A，可重跑）
& $VPY backend/tests/e2e_m5/artifacts/score_enumeration.py

# 四道闸门（一个窗口内）
& $VPY -m unittest discover -s backend/tests/e2e_m5 -t backend -v
& $VPY -m unittest discover -s backend/tests -t backend -v
node <pnpm.mjs> --dir frontend build
node <pnpm.mjs> --dir frontend verify

# V8 夹逼
& $VPY backend/tests/e2e_m5/artifacts/agg_hash.py T1     # 窗口前
# …跑闸门…
& $VPY backend/tests/e2e_m5/artifacts/agg_hash.py T3
& $VPY backend/tests/e2e_m5/artifacts/agg_hash.py --diff T1 T3
```

---

## §10 写盘清单（自报，供 Lead 对账）

新增（全部在 `backend/tests/e2e_m5/**`，除最后一行的 `docs/06`）：

| 路径 | 说明 |
| --- | --- |
| `backend/tests/e2e_m5/__init__.py` | 包标记（discovery 必需） |
| `backend/tests/e2e_m5/_m5_support.py` | 判据设施（真机助手 / 只读库 / 纯判据 / 桩 / 进程内 app） |
| `backend/tests/e2e_m5/test_m5_attempts_acceptance.py` | 42 条后端判据 |
| `backend/tests/e2e_m5/test_m5_ui_acceptance.py` | 8 条界面判据 |
| `backend/tests/e2e_m5/REPORT-m5.md` | 本报告 |
| `backend/tests/e2e_m5/artifacts/score_enumeration.py` | 独立枚举（可重跑） |
| `backend/tests/e2e_m5/artifacts/agg_hash.py` | V8 夹逼工具（可重跑） |
| `backend/tests/e2e_m5/artifacts/task42-boundary-404.json` | 404 原文 |
| `backend/tests/e2e_m5/artifacts/task42-boundary-limits.json` | `limit/offset` 边界矩阵 |
| `backend/tests/e2e_m5/artifacts/task42-e2e-m5-run1.log` | 首轮红（6 处，见 §6） |
| `backend/tests/e2e_m5/artifacts/task42-e2e-m5-run2.log` | 后端 39 绿 |
| `backend/tests/e2e_m5/artifacts/task42-e2e-m5-run3.log` | 全套件 50 绿（补完契约面 / 空态 / 注入后） |
| `backend/tests/e2e_m5/artifacts/task42-injected-deviation.json` | 注入式负对照两态原文（§3.4） |
| `backend/tests/e2e_m5/artifacts/task42-export-coverage.json` | 列表 25 / 回显 limit 20 / CSV 25 |
| `backend/tests/e2e_m5/artifacts/task42-export-empty.json` | 零成绩卷的空导出 |
| `backend/tests/e2e_m5/artifacts/task42-export-invalid-format.json` | 非法 `format` 422 |
| `backend/tests/e2e_m5/artifacts/task42-export-rows.json` | CSV 前若干行事实 |
| `backend/tests/e2e_m5/artifacts/task42-export-xlsx.json` | xlsx 工作簿事实 |
| `backend/tests/e2e_m5/artifacts/task42-frontend-build.log` | 窗口前的 `frontend build` |
| `backend/tests/e2e_m5/artifacts/task42-gate1-e2e-m5.log` | 闸门① 50 / OK |
| `backend/tests/e2e_m5/artifacts/task42-gate2-backend-all.log` | 闸门② 1111 / OK |
| `backend/tests/e2e_m5/artifacts/task42-gate3-frontend-verify.log` | 闸门③ 12 步全 PASS |
| `backend/tests/e2e_m5/artifacts/task42-hash-T1.log`、`task42-hash-T3.log` | 夹逼两端的文字输出 |
| `backend/tests/e2e_m5/artifacts/task42-hash-T4.log` | 报告/文档写完后补拍的第三张（T3 == T4） |
| `backend/tests/e2e_m5/artifacts/hash-T1.json`、`hash-T3.json`、`hash-T4.json` | 夹逼快照（六组逐文件 sha256） |
| `backend/tests/e2e_m5/artifacts/task42-list-detail-reconciliation.json` | 全量对账事实 |
| `backend/tests/e2e_m5/artifacts/task42-q-wildcards.json` | `%` / `_` 字面量 |
| `backend/tests/e2e_m5/artifacts/task42-real-deletion-negative-control.json` | 真删一行 → 4 条具名不符 |
| `backend/tests/e2e_m5/artifacts/task42-same-instant-paging.json` | 同刻翻页 |
| `backend/tests/e2e_m5/artifacts/task42-score-enumeration.log` | 枚举结论（8 / 2 个分叉点） |
| `backend/tests/e2e_m5/artifacts/task42-score-one-source.json` | 四处同数 58 / 旧算法 57 |
| `backend/tests/e2e_m5/artifacts/task42-self-practice.json` | 自主练习三字段 null |
| `backend/tests/e2e_m5/artifacts/task42-ui-evidence.json` | 界面证据（真后端 + 撒谎桩 + 桩收到的请求） |
| `backend/tests/e2e_m5/artifacts/task42-ui-run1.log` | UI 首轮（2 处假红，见 §6 第 2、7 条） |
| `backend/tests/e2e_m5/artifacts/task42-ui-run2.log` | UI 6 绿 |
| `docs/06-测试方案.md` | **修改**：新增 §9.11（插在 §9.10 后、`## 10. 性能测试（NFR-4）` 前），并把 §11 需求覆盖矩阵里 `FR-T11–T13 成绩` 一行补上指向 §9.11 与 `backend/tests/e2e_m5/**` |

删除：`backend/tests/e2e_m4/artifacts/task42-ui-evidence.json`
（我误写进别人写域的证据文件，已删除；见 §6 第 7 条）。

---

## §11 要上报 Lead 的既有事实（**都不在本任务写域内**，只报不改）

1. **`backend/tests/e2e_m4/REPORT-paper-archive.md:78` 里的「冻结值」是旧值**：
   文中写契约哈希 == `359efa5323376c66e2abb04768f6a3f74b49fa8ef3510436aaaee16526c7c435`，
   而 M5 之后 `backend/app/contracts/api.py` 的真值是
   `31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded`
   （两处机器守卫**已同步**：`backend/tests/contract/test_layering.py:31`、
   `backend/tests/e2e_m4/test_paper_archive_acceptance.py:30`）。
   即**测试是绿的、报告里那一行是陈的**。
   （补充：这条「两处副本一致」本身**已有机器判据** ——
   `backend/tests/e2e_m4/test_paper_archive_acceptance.py:63-74`
   `TestContractSurface.test_contract_hash_matches_the_frozen_value` 会把
   `FROZEN_API_SHA` 与 `test_layering.py` 的文本对账，闸门② 1106→1111 绿里包含它。
   我在 M5 侧又独立誊了一份等价判据（`test_m5_attempts_acceptance.py:150 TestContractSurface`），
   差别是**不硬编码冻结值**：它断言「两处副本 == 被冻结文件本身」，M6 合法重冻结时不会假红。）
2. **`backend/tests/e2e_m4/_harness.py:133-141 assert_dist_fresh(case)` 是死断言**：
   内部是 `case.assertTrue(...)`，而 6 个调用点全都传**类**而不是实例
   （`test_mobile_layout.py:190,321`、`test_paper_archive_ui.py:88,174,211`、
   `test_storage_disabled_path.py:41` 的 `assert_dist_fresh(cls)`）→ `self` 位被占成
   `fresh`、`expr` 位变成非空消息串 → `if not expr` 恒假 → **永不抛**。
   后果：那 6 处「产物必须比源码新」的门禁**恒绿**（真不新鲜时也不会红）。
   本任务的界面判据因此改用自己写的 `_require_fresh_dist()`。
3. **`docs/04-接口契约.md` §7 的前端路由表仍无机器判据**（已登记为 T-M6-07），
   本任务只对 `/teacher/attempts` 与 `/teacher/attempts/:attemptId` 做了运行时触达
   （真浏览器打开页面），没有把路由表本身做成门禁。
