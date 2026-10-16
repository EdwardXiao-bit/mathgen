# REPORT-m6-nfr5 — NFR-5 服务端排版守卫的**独立验收**（task-52 / T-M6-04）

> 验收人：**verify-m1**（未参与 `fitting.py` / `pdf_export*.py` / `routers/exports.py` 这条线的任何实现）
> 判据：`backend/tests/e2e_m6/test_m6_nfr5_guard.py`（33 条）+ 支撑 `backend/tests/e2e_m6/_nfr5_support.py`
> 门禁：`& $VPY -m unittest discover -s backend/tests/e2e_m6 -t backend -p "test_m6_nfr5_guard.py" -v`
> 结果：**`Ran 33 tests in 70.542s` / `OK` / exit 0**（日志 `artifacts/task52-run3.log`；中间两跑 `task52-run1.log` / `task52-run2.log`）
> 证据：`backend/tests/e2e_m6/artifacts/task52-criterion-{1,2,3,4,5,6,real-reachability}.json`、`task52-over-rejection.json`、`task52-pagination-probe.json`、`task52-instrument.json`

---

## 0. 一屏（先看这里）

| # | 必验项 | 结论 | 判据位置 |
| --- | --- | --- | --- |
| 1 | 守卫不是恒 409 | **通过**：同一内容（指纹相同）默认档 200（24758 B `%PDF-`）、最坏档 409 `LAYOUT_DOES_NOT_FIT` | `test_m6_nfr5_guard.py:45` |
| 2 | 诊断「实测这样能放下」可验证 | **通过**：文案给 `请把字号从 20 号调到 17 号以下（实测这样能放下）`，原样改 `font_size=17` → **200 / 24807 B**；第二种形态（连字号 10 + 每行 2 题都放不下）只给方向、不给目标值 | `:102` |
| 3 | 应急出口四态 | **通过**：未设 409；`=1` → 200 + `X-Mathgen-Layout-Guard: skipped` + WARNING（含卷号、`仍有 2 行放不下`、`行号 [0, 1]`）；**`=0` → 409**；**`1 `（带空格）→ 409** | `:173` |
| 4 | 系统故障不伪装成 409 | **通过**：Chromium 不可用 → 503 `EXPORT_NOT_READY`；worker 非 0 退出（非排版拒绝）→ 503；排版拒绝仍 409 | `:235` |
| 5 | 模型 vs 真实分页（≥5 组） | **通过**：7 组独立复测，**「模型放得下、真实却切开」= 0 例**；并量到**反向**偏差（见 §5） | `:281` |
| 6 | Ctrl+P 路径没有第二层守卫 | **通过（含缺口量化）**：客户端 0 处等价守卫；同一份 HTML 走无守卫打印通道 → 5 页 8 处跨页 | `:371` |
| 7 | 冻结契约未动 | **通过**：`api.py` sha256 = 卡给定值；`test_layering.py` 绿（日志 `artifacts/task52-layering.log`） | `:509` |
| 8 | 写明不能发现什么 | 见 §8（10 条） | 本文 |

**两条独立发现（不是「通过/不通过」，是量出来的事实）：**

1. **模型的容量估计比真实打印大约 1.85 ×**（`synth28_worst`：模型行高 1270.5px，真实纸面上同一行只占 687.3px），所以守卫在 **897 ~ 1270px** 这一带**过度拒绝**：真实打印放得下的一行被 409 挡掉。方向与 `export/pdf.py:138-144` 注释担心的（「量出的行高会系统性**偏小** → 漏报」）**相反**。
2. **被拒世界用真实可生成内容不可达**：本任务自己扫 1008 个配置（591 成功建卷）实测**最长题面 26 字符**，在最坏合法档下模型量到 **561.2px « 833.00px** 阈值，端到端导出 **200**。所以「第二层 409」当前只对**合成**输入有辨别力。

**Ctrl+P 的分层表述（对 Lead 口径的独立确认 + 一处补充）**：三条路（打印按钮 / Ctrl+P / PDF 导出）确实是**同一份 HTML**（我实测 `/sheet` 返回的 HTML 与导出用的 HTML **逐字节相同**）；CSS `break-inside: avoid` 这条**主机制在打印媒体下确实生效**（整行放得下一页时整行下移，`synth16_worst`/`synth28_worst` 跨页数 = 0）。补充的是适用范围：**一行放不下一页时 CSS 无能为力**——同一个被 409 拒绝的输入走打印通道，真实被切成 5 页 8 处跨页（`synth40_worst`，页面文本高度 831.4px / 52.6px）。服务端 409 管的正是这一类，而这一类用真实内容不可达。

---

## 1. 由来 / 为什么由我验

`docs/06` §9.15 之前，NFR-5 这条链的证据全部来自 render-export 自己写的 `backend/tests/render/**`（`test_layout_proof.py` 的「24 组必然断行」用的是**合成 58 字符**题面）。M6 验收（T-M6-04）里「对他人交付做独立验收」这一半要求**没参与实现的人**用**自己造的输入**复核。本报告的全部输入都由本任务新建（合成题面 / 临时库 / 真 Chromium 打印通道），没有拿 render-export 的测试当结论。

## 2. 验的对象与三条真实路径

| 路径 | 入口 | 本任务怎么走它 |
| --- | --- | --- |
| **API** | `GET /api/papers/{id}/export`（`routers/exports.py:105-187`） | 临时 SQLite + `TestClient`，直接写 `paper`/`paper_item`（合成题面唯一入口），走 200 / 409 / 503 三个真实分支 |
| **打印** | 同一份卷面 HTML 交给 Chromium 自己的打印通道 | `export_pdf_report(html, out, measure=False, reject_overflow=False)` —— 就是「没有服务端判定」的打印语义；`/sheet` 的 HTML 与导出用的 HTML 逐字节相同 |
| **模型** | `measure_js()` + `detect_overflow()`（守卫真正用的判据） | `export_pdf_report(..., measure=True, reject_overflow=False)`，取 `report.measurement` 自己算溢出 |

被验文件：`backend/app/layout/fitting.py`、`backend/app/export/pdf.py`、`backend/app/services/pdf_export{,_worker}.py`、`backend/app/routers/exports.py:105-187`，以及 `backend/tests/render/REPORT-layout-proof.md` 的结论（只读其结论，不作证据）。

## 3. 八条必验清单 → 判据对照

| 清单 | 判据（`test_m6_nfr5_guard.py`） | 负对照（我自己造的世界会让它红） |
| --- | --- | --- |
| 1 | `TestTheGuardIsNotAlwaysRejecting` `:45`（4 条） | `test_the_pair_checker_can_go_red` `:92`（两个世界都 200 / 内容指纹不同 → 红） |
| 2 | `TestTheDiagnosticSuggestionIsVerifiable` `:102`（5 条） | `test_the_suggestion_parser_can_go_red` `:167`（既无目标值又无方向表述 → 红） |
| 3 | `TestTheEscapeHatchHasFourStates` `:173`（5 条） | `test_the_state_checker_can_go_red` `:225`（`=0` 被当作关闭 → 红） |
| 4 | `TestASystemFailureIs503Not409` `:235`（3 条） | 同组第 3 条是反面对照：排版拒绝必须是 409 而不是 503 |
| 5 | `TestModelVersusRealPagination` `:281`（7 条） | `:323` 喂「模型放得下 + 真实切开」→ 红；`:329` 无真实页数/空集 → 红 |
| 6 | `TestCtrlPlusPHasNoServerGuard` `:371`（4 条） | `:416` 客户端塞「先问服务端再打印」的假实现 → 红 |
| 6b | `TestTheRejectedWorldIsUnreachableWithRealContent` `:463`（3 条） | 引用本任务自己的引擎扫描证据文件，证据缺失即红（`:494`） |
| 7 | `TestFrozenContractsAreUntouched` `:509`（2 条） | sha256 与卡给定值逐字符比较；`test_layering.py` 子进程返回码必须 0 |

## 4. 原始证据

### 4.1 判据 1 / 2 / 3 / 4（`artifacts/task52-criterion-{1,2,3,4}.json`）

- **判据 1**：`default_status=200`、`default_bytes=24758`、`overflow_status=409`、`overflow_code=LAYOUT_DOES_NOT_FIT`、`content_same=true`（两个世界的题目 payload 指纹相同）。409 原文：
  > 当前排版（每行题数 6 题 / 行距倍数 2 倍 / 字号 20 号 / 页边距 3cm）在这一卷上放不下，会有 2 行题目被跨页拆开（最高的一行 **1718.42px**，超过可印区 **833.00px**）；请把字号从 20 号调到 17 号以下（实测这样能放下）。
- **判据 2**：`applied={"font_size":17.0}` → `follow_up=200 / 24807 B`（`%PDF-`）；并在库里复核 `layout_json` 真的写进了 17（不是只在文案里）。第二种形态：1200 字符/题 → `409`，`claims_direction_only=true`、`has_target=false`。
- **判据 3**：`unset → 409`；`1 → 200 + 头 skipped + 1 条 WARNING`；`0 → 409`（无头、无 WARNING）；`1 `（尾随空格）`→ 409`。WARNING 原文：
  > NFR-5 排版守卫被跳过（MATHGEN_SKIP_LAYOUT_GUARD=1）：卷 1 仍有 2 行放不下（行号 [0, 1]）；成品可能把题目跨页拆开 —— 这是应急出口，请尽快查明判据为何误报
- **判据 4**：`MATHGEN_CHROMIUM=<python.exe>` → **503 / EXPORT_NOT_READY**（文案含「浏览器/打印环境问题」）；`_run_worker` 返回 1（mock，模拟 worker 内部炸掉）→ **503 / EXPORT_NOT_READY**；排版拒绝 → **409 / LAYOUT_DOES_NOT_FIT**。

### 4.2 判据 5：模型 vs 真实（7 组，`artifacts/task52-criterion-5.json`）

`model_*` = 产品自己的 `measure_js()` + `detect_overflow()`；`real_*` = 真 Chromium 打印出的 PDF（vendored pypdfium2 逐页取文本 + 每个题块两端 `MKR_nnA/B` 标记判同页）。

| 组 | 输入 | 模型判决 | 模型最大行高 | 真实页数 | 真实跨页 | 真实每页文本高度 |
| --- | --- | --- | --- | --- | --- | --- |
| `real26_worst` | 真实题面 26 字符 / 最坏档 | accept | 561.2px | 2 | 0 | p1 569.5 / p2 405.8 |
| `real26_default` | 真实题面 / 默认档 | accept | 105.5px | 1 | 0 | p1 291.7 |
| `synth10_worst` | 合成 10 汉字 | accept | 598.6px | 2 | 0 | p1 498.7 / p2 334.9 |
| `synth16_worst` | 合成 16 汉字 | accept | 822.5px | 2 | 0 | p1 604.4 / p2 440.7 |
| `synth28_worst` | 合成 28 汉字 | **reject** | 1270.5px | 3 | **0** | p1 148.7 / p2 **687.3** / p3 687.2 |
| `synth40_worst` | 合成 40 汉字 | **reject** | 1718.4px | 5 | **8** | p1 148.7 / p2 831.4 / p3 52.6 / p4 831.3 / p5 52.6 |
| `synth350_default` | 合成 350 汉字 / 默认档 | **reject** | 1386.4px | 5 | **8** | p1 89.8 / p2 958.5 / p3 383.4 / p4 958.5 / p5 383.4 |

- **漏报方向（核心问题）**：`problems = []` —— 七组里**没有**「模型说放得下、真实却切开」的组合。加上本任务早先的 13 组扫描（`task52-pagination-probe.json`：11 个合成档 + 2 个真实档，`real_cut_rows` 只在模型 reject 的档位非空），**漏报 0 例**。
- **过度拒绝方向**：`synth28_worst` 是反例 —— 模型 1270.5px（> 833 阈值）判拒绝，真实 PDF 里那一行**完整地在一页内**（p2 文本 687.3px，无 marker 跨页）。`task52-over-rejection.json`：`ratio = 1.85`。
- **关于 render-export 自报的残留风险**（`REPORT-layout-proof.md` §7/§12 ①：`break-inside: avoid` 的「不可分块必定整体下移」是行为假设，本仓**未见到「被切开」的真实样本**）：本任务**见到了**。`synth40_worst` 与 `synth350_default` 走真实打印通道后，`MKR_nnA/B` 标记跨页各 **8 处**（页面文本高度 831.4 / 52.6 px）—— 一行确实被拦腰切开。该行为假设的**边界**是「整行放得下一页时整行下移；整行放不下一页时会被切开」，不是无条件成立。这两组模型判决 = `reject`、真实也切开，**两者一致**（不是漏报）。

### 4.3 判据 6：Ctrl+P / 打印路径（`artifacts/task52-criterion-6.json`）

- `/sheet` → **200**，其 HTML 与导出路径渲染的 HTML **逐字节相同**（`sheet_equals_export_html=true`，sha256 `621750792388b437…`），且 HTML 里含 `.q-row` + `break-inside: avoid`。
- 客户端扫描（`frontend/src`、`frontend/scripts`）：**7 个打印触发点** —— `frontend/src/teacher/pages/PaperPage.tsx:318` `iframe.contentWindow?.print();`，其余 6 处是注释/文档里的 `window.print()` 字样（`PaperPage.tsx:287`、`frontend/scripts/verify-layout-css-vars.mjs:16,327,329`、`verify-teacher-ui.mjs:239,241`）。**守卫标记 0 处**（`client_guard_problems = []`）。
- **缺口量化**：把同一份被 409 拒绝的 HTML 交给**无守卫**打印通道 → `5 页`、**8 处 marker 跨页**（行 0 与行 1），页面文本高度 `831.4 / 52.6` px，说明一行被拦腰切开。

### 4.4 判据 7

- `backend/app/contracts/api.py` sha256 = `31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded`（与卡给定值一致）。
- `test_layering.py` 子进程 `returncode=0`（日志 `artifacts/task52-layering.log`）。

## 5. 独立发现：模型的容量估计 vs 真实打印

### 5.1 量到的差

`synth28_worst`（6 列 / 行距 2.0 / 字号 20 / 页边距 3cm，8 题题面各 40 字符含 marker）：

| 量在哪 | 值 |
| --- | --- |
| 模型 `measure_js()` 的行高 | **1270.5px**（`display` 元素 33 行 × 37px 行距，y 173.98 → 1395.48） |
| 真实 PDF 第 2 页（整行 + 卷头外的部分）文本高度 | **687.3px**（约 19 行 × ~35px 行距） |
| 比值 | **1.85 ×** |
| 真实纸面可印区（margin 3cm） | 896px；阈值 `usable_first` = 833.00px |

### 5.2 机制（同一列宽下的换行阈值）

在**同一份 print 媒体页**上量同一列（`artifacts/task52-instrument.json` + `task52_probe_column.py`）：

- 模型侧：`.q-item` 宽 **50.38px**、字号 26.6667px，一个汉字的 `Range` 矩形宽 = **26.67px = 1em**；
- 真实 PDF 侧：同列相邻汉字的 x 差 ≈ **25.2px**（0.945em），**一行排得下 2 个汉字**；
- 列宽 50.38px 正好卡在 `2 × 25.2 = 50.4 ≤ 50.38` 与 `2 × 26.67 = 53.3 > 50.38` 之间 ⇒ **真实打印 2 字/行、模型 1 字/行**，行数近似翻倍（33 vs 约 19），行距量级一致（模型 37px / 真实 ~35px）。

这是**换行阈值上的窄带效应**，不是大面积系统误差：它只在「列宽恰好落在两倍字宽之间」时出现（本例 50.38px 与 50.4px 只差 0.02px）。**为什么真实 PDF 里字步进是 0.945em 而不是 1em，我没有查到根因**（候选：打印/PDF 字体子集化后的度量与布局度量不同），这点如实记录，不装作已解释。

### 5.3 可达性：这条偏差在真实内容上根本触发不到

- 本任务自己的引擎扫描（`artifacts/task52-pagination-probe.json`）：3 题型 × R1..R7 × 4 组运算符 × 6 个 `max_value` × `parentheses`，`configs_ok=591`、`configs_failed=417`，**最长题面 26 字符**（`942848 - (718286 + 148111)`；次长 24 / 23 / 22）。
- 端到端（`artifacts/task52-criterion-real-reachability.json`）：真实题面在**默认档**与**最坏合法档**下导出都是 **200**；最坏档下模型量到 **561.2px « 833.00px**。
- ⇒ 「第二层 409」的触发条件（模型行高 > 833px）用**真实可生成内容不可达**。这不是「守卫坏了」，而是「这条判据当前只对**合成**输入有辨别力」；它同时说明真实的 409 是**安全侧误差**（宁可拒绝，不会放过）。

## 6. render-export 自报的两条弱点，我复核后的结论

1. **`__all__` 判据是快照式的** —— 确认。`backend/app/layout/fitting.py:63-74` 是**手工维护的引用计数表**，仓库里**没有任何**从调用图算公共面的机器判据（我读过 `backend/tests/render/test_layout_proof.py` 的 `__all__` 用例：它列举名字 + 断言内部构件 `hasattr`，规则本身没被判据覆盖）。**对 NFR-5 结论强度的影响**：公共面 = 被模块外引用的名字这条规则**没有机器保证**，将来重命名/漏导出不会被发现。我不在本任务给它立判据（写域限制）。
2. **`LayoutTooLarge` 的引用计数是手工的** —— 独立复核：按**代码引用**（不含 docstring）数，正好是它修正后的 **4 处**：`export/pdf.py:20` import + `:171` except、`services/pdf_export_worker.py:117` import + `:128` except；另外 4 处是文档性提及（`export/pdf.py:99,173`、`services/pdf_export.py:155,279`），确实不该计入。它的修正是对的，但这类数字**每次都要人手数**。

## 7. 对 Lead「NFR-5 结论形状」的独立确认

| Lead 的说法 | 我的独立结果 |
| --- | --- |
| 真实生成器最长题面 26 字符 | **确认**（591 成功建卷的扫描，见 §5.3） |
| 真实卷在最坏合法档下 `/sheet`、导出 pdf 全 200 | **确认 pdf 与 /sheet**（`/sheet` 200、导出 200；`docx` 我这轮没跑，不在本报告结论里） |
| 三条路共用同一份 HTML、CSS 是主机制 | **确认**：`/sheet` HTML 与导出 HTML 逐字节相同；打印媒体下整行下移确实生效（无跨页） |
| 「打印路径缺的只是第二层 409」 | **确认，但要补适用范围**：CSS 只管「一行放得下一页」；一行放不下一页时打印**真的会被切开**（5 页 8 处跨页），而那正是 409 管的类别 —— 该类别用真实内容不可达 |
| 第二层当前只对合成输入有辨别力 | **确认**（§5.3） |

我**没有**量到任何推翻 CSS 主机制的证据；`docs/00` D81 / `docs/05` M6 若要改成分层表述，上面这一行补充是必须带的限定。

## 8. 这套判据**不能**发现什么

1. **不是真的按 Ctrl+P**：我用同一份 HTML 走 Chromium 的打印通道，等价于程序化打印；浏览器打印预览对话框、用户改缩放/纸张/页边距覆盖，都不在判据覆盖内（用户把缩放调小/调大就会改变结果）。
2. **真实打印机/驱动**：只量 PDF 通道；驱动自己的缩放与不可打印边距不算在内。
3. **Chromium 版本差异**：本机 148.0.7778.96（task-49 记录）；`break-inside` / `@page` 行为、以及 §5.2 那个 0.945em 步进都可能随版本变。
4. **`answer` 模式**：判据只走 `mode=question`；答案版式（`.a-item`、`break-before: page`）的容量与跨页没被独立复测（渲染链共享，但内容形状不同）。
5. **教师编辑后的内容**：只覆盖引擎生成的内容。若将来允许教师自定义/粘贴题面，§5.3 的可达性结论必须重估。
6. **字符集**：真实题面是数字与运算符；恰恰是**中文合成内容**才触发了 §5.2 的换行阈值效应。真实中文题面的行为没有实测样本。
7. **边界精度**：模型高估 ⇒ 真实侧比模型宽松，所以「真实恰好放得下/放不下」的分界我不做保证（我只有 7 组采样，没有二分）。
8. **`measure=False` + `reject_overflow=True`**：代码里这个组合会**静默跳过守卫**（`export/pdf.py:154-163` 的 `if measure:` 包住守卫）。生产路径不会这么调，但这是可被误用的组合，我没有做成判据。
9. **应急出口没人看见**：`=1` 的痕迹只到日志（无遥测、无 UI 提示）。判据只验「日志里有」，不验「有人会看到」。
10. **`detect_page_crossing` 不在生产链上**：真正的「行跨界」模型在 `app/layout/__init__.py` 导出，但**没有任何生产调用点**（只被 `backend/tests/render/**` 引用）；生产用的是「行高 > 首页可用区」这个**代理**，因而按构造就会拒绝「换页后放得下」的行。我把它作为事实记录，没有立判据（它是设计选择，不是缺陷）。

## 9. 复核命令

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:MATHGEN_PW_TMP="<REPO>\.tmp-pw"; $env:TMP=$env:MATHGEN_PW_TMP; $env:TEMP=$env:MATHGEN_PW_TMP
# 本报告全部门禁（33 条）
& "backend\.venv\Scripts\python.exe" -m unittest discover -s backend/tests/e2e_m6 -t backend -p "test_m6_nfr5_guard.py" -v
# 冻结契约分层
& "backend\.venv\Scripts\python.exe" -m unittest discover -s backend/tests/contract -t backend -p "test_layering.py" -v
# 只读探针（模型 vs 真实、仪器、单列）
& "backend\.venv\Scripts\python.exe" backend\tests\e2e_m6\artifacts\task52_probe_pagination.py
& "backend\.venv\Scripts\python.exe" backend\tests\e2e_m6\artifacts\task52_probe_instrument.py
& "backend\.venv\Scripts\python.exe" backend\tests\e2e_m6\artifacts\task52_probe_column.py backend\tests\e2e_m6\artifacts\task52-instrument-cjk028.pdf 2 110 175
```

## 10. 上报事实

1. **写域**：只新增 `backend/tests/e2e_m6/**`（判据、支撑、探针、报告）与 `docs/06-测试方案.md`；**未改** `backend/app/**`、`backend/tests/render/**`、`frontend/**`，也未改 task-49 已有的文件。
2. **`frontend` 是只读**：`client_guard_problems` 只读源码文本，不做任何写操作。
3. **依赖**：判据使用了仓库自带（vendored）的 `pypdfium2`（`backend/tests/e2e_m2/.pylibs`，task-51 的排除项里点名过它）来读 PDF 文本；没有新装依赖。
4. **临时库**：每个 `GuardClient` 在 `backend/tests/e2e_m6/artifacts/` 建一个 `nfr5-*.db`，测试结束由 `engine.dispose()` 释放（文件保留作证据）。
5. **跑时**：本判据 33 条 ≈ 71s，其中 20 次真实 Chromium 导出（每次启动导出子进程）是主要开销。
6. **未验证项（不许当成已验证）**：`format=docx` 的 NFR-5 行为、`answer` 模式的跨页、真实打印机、教师自定义内容。
