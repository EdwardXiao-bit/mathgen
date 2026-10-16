# task-44 六要素报告：把 NFR-5（防断题）从默认排版推广到整个合法参数空间

> 角色：M6 实现者（T-M6-01） · 日期：2026-10-16
> 写域：`backend/app/layout/**`、`backend/tests/render/**`（**未触碰 `backend/app/contracts/**` 与 `frontend/`**）
> 落盘位置说明：本报告写在自己的写域内；`docs/05-任务板.md` 按 `AGENTS.md`「共享文件写者」表**仅 Lead 可改**，
> 故在 §9 给出**可直接粘贴**的段落。
>
> 判据主体：`backend/tests/render/test_layout_proof.py`（**10 个测试类 / 26 条用例 / 1154 行**，
> 另有 1 个非测试辅助类 `_MeasuredPage`）
> 契约：`backend/tests/render/test_export_pdf.py::TestPageBoxMeasurement::test_measured_capacity_matches_contract_table`（未改）
> 文件格式：本批改动**全部纯 LF、无 BOM**（逐字节量，见 §6.1）

---

## 1. 任务 ID

**task-44**（团队任务板 id；`docs/05-任务板.md` 第 375 行记为 `T-M6-01`）。
标题：**排版样张定稿（六参数实物对照，NFR-5 推广到整个合法参数空间）**。
依赖 `T-M5-04`，下游 `T-M6-04`（M6 里程碑验收）。

### 交派时的问题（Lead 的原始表述，m02345）

NFR-5 的证据链此前只覆盖**默认排版**：`detect_page_crossing()` 与 `detect_overflow()`
的判据都是在默认参数（4 列 / 14 号字 / 2cm 页边距）下量出来的。而合法参数空间是
**6 维**的：`cols_per_row ∈ {2,3,4,5,6}` × `row_gap ∈ {0.8,1.0,1.4,2.0}` ×
`font_size ∈ {10,14,20}` × `page_margin ∈ {1.0,2.0,3.0}` × `answer_lines ∈ {T,F}` ×
`header_bar ∈ {T,F}`。**「默认档安全」不等于「整个空间安全」**——这正是本任务要回答的。

### 结论（一句话）

| 问题 | 结论 |
| --- | --- |
| 合法参数空间里，有没有一行会被印到两页上（**断题**）？ | **没有**。角点扫描 + 真实 PDF 双向对照都支持；且**能红判据**证明这不是「恒真判据」 |
| NFR-5 的证据链有没有洞？ | **有，已堵**：① 两处单位 bug（本就存在，会造成**漏报**）；② 测量视口 ≠ 真实版心宽（会让行高被系统性低估近 1.5 倍）；③ 分页标定表方向记反、值偏小 |
| 交付物（PDF/DOCX 字节）有没有被我改动？ | **没有**。`page.pdf()` 与屏幕视口无关（实测字节 sha256 相同），M6 修的全是**测量与判据** |

---

## 2. 改动文件清单

| # | 文件 | 改动 | 关键位置 |
| --- | --- | --- | --- |
| 1 | `backend/app/layout/pagination.py` | 新增 `_MM_PER_CM = 10.0` 与 `_margin_mm()`，修两处「cm 当 mm」单位 bug | `pagination.py:141`、`pagination.py:144` |
| | | `content_width_px()` 改用 `_margin_mm()`；补「测量视口必须=版心宽」的实测说明 | `pagination.py:157`、`pagination.py:184` |
| | | `MEASURED_BREAK_CEILING_PX` **整表重写**（方向 + 数值都修） | `pagination.py:270` |
| | | `usable_geometry()` / `detect_page_crossing()` 走 `_margin_mm()` | `pagination.py:294`、`pagination.py:309` |
| 2 | `backend/tests/render/test_layout_proof.py` | **新增整个文件**：12 个判据类 / 40 条用例（本任务的判据主体）。其中 14 条纯计算（不需浏览器，<0.1s），26 条走真实 Chromium | `test_layout_proof.py:1-1462` |
| | | 索引：`TestContentWidthHelper:224`、`TestMeasurementViewport:278`、`TestRowHeightUpperBound:367`、`TestPageCapacityCalibration:515`、`TestRowSplitBoundaryIsReachable:613`、`TestCrossingCriterionCanFail:785`、`TestRealPdfNoTruncation:848`、`TestModelMatchesRealPdfPagination:909`、`TestMarginUnitBugCannotRegress:1091`、`TestLayoutContractStillIntact:1136`、`TestLayoutGuardRejectsExport:1155`、`TestExportRefusesWhenItCannotFit:1374` | 同上 |
| 3 | `backend/tests/render/pdfprobe.py` | 新增「不依赖 PDF 库」的 PDF 内容流探针（见 §4.4） | `pdfprobe.py:19`、`pdfprobe.py:25`、`pdfprobe.py:64`、`pdfprobe.py:164` |
| | | **行尾修正**：原为 CRLF（CR=190/LF=190），已改纯 LF（CR=0/LF=190，7037→6847 B） | 见 §6.1 |
| 4 | `backend/.tmp-pw/m6_*.py` `.out` | 探针脚本 21 个 + 输出（`.tmp-*` 已 gitignore，**不入库**） | — |
| 5 | `backend/.tmp-render-artifacts/m6-*.pdf` | 六参数实物样张 8 份（**不入库**，见 §3.4） | — |
| 6 | `backend/tests/render/REPORT-layout-proof.md` | 本报告（新增） | — |
| 7 | `backend/app/layout/fitting.py` | **新增**：导出前的容量守卫（NFR-5 服务端出口）。公共面收缩为 3 个名字（见 §10.7） | `fitting.py:75`(`__all__`)、`fitting.py:118`(`LayoutTooLarge`)、`fitting.py:215`(`describe_layout`)、`fitting.py:232`(`format_diagnostic`)、`fitting.py:290`(`find_fitting_layout`)、`fitting.py:322`(`check_layout_fits`)、`fitting.py:338`(`guard_layout_of_html`)、`fitting.py:94`(`rewrite_layout_vars`)、`fitting.py:189`(`LayoutFit`) |
| 8 | `backend/app/export/pdf.py` | `layout_of()` 读全六参数（原只读 `page_margin`）；`export_pdf_report(..., reject_overflow=False)`；`_candidate_measure()` | `pdf.py:284`、`pdf.py:85`、`pdf.py:89`、`pdf.py:59`、`pdf.py:162` |
| 9 | `backend/app/services/pdf_export.py` | `LAYOUT_GUARD_ENV` / `ExportedPdf` / `ExportLayoutRejected` / 伴随文件（见 §10.3） | `pdf_export.py:83`、`pdf_export.py:101`、`pdf_export.py:152`、`pdf_export.py:187` |
| 10 | `backend/app/services/pdf_export_worker.py` | 子进程把 `measurement` 与溢出事实写 `<pdf>.json`；退出码 3 = 排版拒绝 | `pdf_export_worker.py:59`、`pdf_export_worker.py:107` |
| 11 | `backend/app/routers/exports.py` | `LAYOUT_DOES_NOT_FIT` → 409；跳过开关 → 响应头 + WARNING | `exports.py:145`、`exports.py:161` |
| 12 | `backend/app/services/errors.py` | 注册 `"LAYOUT_DOES_NOT_FIT": 409` | `errors.py:73` |
| 13 | `backend/tests/api/test_export_layout_guard.py` | **新增**：路由层三世界端到端判据（4 用例） | `test_export_layout_guard.py:1` |
| 14 | `backend/tests/api/test_errors_and_sheet.py` | 契约码副本加 `LAYOUT_DOES_NOT_FIT`（按 Lead 裁决一步到位） | `test_errors_and_sheet.py:40` |
| 15 | `backend/tests/api/test_error_code_sources.py` | `EXPECTED_DOC_ROWS` 15 → 16 | `test_error_code_sources.py:51` |
| 16 | `docs/04-接口契约.md` | §9 表加 `LAYOUT_DOES_NOT_FIT`（**由 Lead 落笔**，不在我的写域） | §9 表 + D81 块 |
| 17 | `backend/app/render/sheet.py` | **只改 `render_with_layout` 的 docstring**（`:53-69`）：如实说明「有真实调用者，但没有任何东西在用 `reflow` 自动微调；NFR-5 的保证来自导出前的拒绝判据」（Lead m05153 §二(2) 授权扩写域）。**签名、函数体与其它行未动** | `sheet.py:52` |

**只读复用的现有资产**（未改）：`app/export/measure_page_box()`、`test_export_pdf.py::_renderer()`、
`app/layout/styles.py`、`app/contracts/layout.py`。

> **`backend/tests/render/test_export_pdf.py` 本轮为零改动**（Lead 曾问及「那 2 处改动」）：
> `git status --porcelain -- backend/tests/render/test_export_pdf.py` 无输出，即与 HEAD
> 逐字节一致，最后改动是 `839eb9e`（M3 收尾）。Lead 记忆里的「2 处」实际落在
> `test_layout_proof.py:242` 与 `test_layout_proof.py:591`，都在本轮**新增**的文件里，
> 所以 `git status` 显示的是 `?? test_layout_proof.py` 一个新文件，而不是两处修改。
> 该文件里两条相关断言原本就是：`test_measured_capacity_decreases_with_margin`
> （`test_export_pdf.py:405`，本来就是「1.0 > 3.0」递减方向）与
> `test_measured_capacity_matches_contract_table`（`test_export_pdf.py:421`，容差 = 一行步距
> 的**区间式**断言，原本如此）。**没有把精确相等放松成区间**这件事发生——因为该文件没被碰过。

---

## 3. 验证命令 + 原始输出

环境（不设则必假红，见 `AGENTS.md` 坑 2）：
`PLAYWRIGHT_BROWSERS_PATH=<repo>\.playwright`、`TEMP=TMP=<repo>\.tmp-pw`、`PYTHONIOENCODING=utf-8`；
`$VPY = <repo>\backend\.venv\Scripts\python.exe`；工作目录 `<repo>`。

### (1) render 套件（新增判据所在套件）

```
& $VPY -m unittest discover -s backend/tests/render -t backend
```
```
Ran 252 tests in 180.507s
OK
```
日志：`backend/.tmp-pw/m6_gate_render3.log`。**行尾修正之后又复跑一次**（§6.1）：
`Ran 252 tests in 172.327s / OK`，日志 `backend/.tmp-pw/m6_gate_render4.log`。

### (2) contract 套件（确认没碰冻结契约）

```
& $VPY -m unittest discover -s backend/tests/contract -t backend
```
```
Ran 104 tests in 0.998s
OK
```

### (3) 全量门禁（最终）

```
& $VPY -m unittest discover -s backend/tests -t backend
```
```
Ran 1180 tests in 575.620s
OK
```
日志：`backend/.tmp-pw/m6_gate_full_final.log`。

### (3b) 附编 (ii) 落地后的门禁读数（**最新**）

守卫接入导出通道后逐套复跑（命令同 (1)(2)，另加 API 套）。下表是
**Lead m05153（`__all__` 收缩 + `sheet.py` docstring 订正）落地后的最终一次读数**
（源文件冻结后再跑，三个域一次跑完，日志同一份）：

| 套件 | 命令 | 读数 | 退出码 | 日志 |
| --- | --- | --- | ---: | --- |
| render | `discover -s tests/render -t .` | `Ran 266 tests in 195.781s / OK` | 0 | `.tmp-pw/gates-final2.log` |
| api | `discover -s tests/api -t .` | `Ran 365 tests in 57.960s / OK` | 0 | `.tmp-pw/gates-final2.log` |
| contract | `discover -s tests/contract -t .` | `Ran 121 tests in 1.704s / OK` | 0 | `.tmp-pw/gates-final2.log` |

三套件**全部绿**（`render=0 api=0 contract=0`）。API 套件 361 → **365**（+4 = 新增的
`test_export_layout_guard.py`），且此前那 3 条契约对账红已随 `docs/04` §9 补行自愈。

render 套件从 252 → **266**，差额来自本轮新增的判据类：`TestLayoutGuardRejectsExport`（9，
含 (A) 与「公共面收缩」那条双向判据）、`TestExportRefusesWhenItCannotFit`（5）。
（m05153 那轮只改判据内部断言与 docstring，**用例数不变**：266 → 266。）
contract 从 104 → 121 是并行任务（task-47/51 的路由表与文本卫生判据）带来的，与本节改动无关。

**「先证明变红、再补绿」的实现（Lead 要求④）**：加 `LAYOUT_DOES_NOT_FIT` 之前，
API 套件确实红过三条，且都指向同一处**缺失的文档行**：

1. `test_error_code_sources.py::DocumentedErrorCodesAgreeTests::test_document_copy_and_implementation_agree`
   → `AssertionError: Lists differ: ["[实现映射表] 有、[文档 §9] 没有：['LAYOUT_DOES_NOT_FIT']"] != []`
2. `test_errors_and_sheet.py::ErrorCodeReconciliationNegativeControls::test_there_is_no_implementation_only_code_left`
3. `test_errors_and_sheet.py::TestErrorShape::test_error_codes_cover_contract`

这三条红**不是缺陷**，正是判据在正常工作：D81 的「实现先于契约」被双向对账抓住，
与 D77 记录的同族。补 `docs/04` §9 那行（Lead 落笔）后即绿。

### (4) 实物样张（六参数真实打印通道，Chromium → PDF）

```
& $VPY backend/.tmp-pw/m6_samples.py
```

| 样张 | 参数（cols × gap × font × margin） | 用途 |
| --- | --- | --- |
| `m6-corner-6x2.0x20x3.0.pdf` | 6 × 2.0 × 20 × 3.0 | 行高**最大**的角点（实测 1382.47px） |
| `m6-corner-2x2.0x20x3.0.pdf` | 2 × 2.0 × 20 × 3.0 | 第二个角点 |
| `m6-corner-6x2.0x20x1.0.pdf` | 6 × 2.0 × 20 × 1.0 | 「列最多 + 页边距最小」 |
| `m6-corner-4x1.0x14x2.0.pdf` | 4 × 1.0 × 14 × 2.0 | 默认档对照 |
| `m6-margin-1.0 / 2.0 / 3.0.pdf` | 同一卷面、只改页边距 | 标定表逐档核对 |
| `m6-crossing-control.pdf` | 人为做出跨页行 | 判据「能红」的实物对照 |

自证：`8 份 PDF + 26 条判据全部从同一个 `backend/app/` 产生`；样张与判据之间**没有手抄数字**。

---

## 4. 根因（实测取证，非推测）

### 4.1 单位 bug ①：`content_width_px()` 把 cm 当 mm → 版心宽算错

原式：`content_mm = A4_WIDTH_MM - 2.0 * layout.page_margin`。`page_margin` 的单位是 **cm**，
这里当 **mm** 用，**让出量少了 10 倍**（`pagination.py:144` 的 `_margin_mm()` 就是修它）。
3.0cm 时版心宽算成 771px，正确值 567px（差 204px，**多算 36%**）。
后果：测量视口取宽了 ⇒ 窄列不折行 ⇒ 行高被低估 ⇒ `detect_overflow()` **漏报**。

### 4.2 单位 bug ②：`usable_geometry()` 的「第 2 页起让出两个页边距」算少了 10 倍

原式把 `page_margin / MM_PER_PX`（cm 值除以 mm/px）当换算，于是第 2 页起只让出
**11.34px**，应为 **113.39px**（3.0cm 档）。这条最危险：`page_of()` 会用错误的
分页线把「其实越过了」的行算成没越过 —— **正是 NFR-5 要防的漏报**。
证据（`m6_red_demo2.log` 第 16 行的真实断言输出）：
`AssertionError: 11.338582677165277 != 113.38582677165354 within 0.5 delta`。

### 4.3 分页标定表 `MEASURED_BREAK_CEILING_PX` 原先**方向记反、值偏小**

原表：1.0cm → 649.4px、3.0cm → 1008.7px，即「页边距越大、可用高度越大」——物理上不可能。
成因：原表在**高并发**下扫描，每次 `pdf()` 量到的分页线互相干扰，把最宽松的一组记到了
最紧的页边距上。M6 用**生产代码自己的标定函数** `measure_page_box()` 串行重测
（口径与 `test_export_pdf.py::_renderer()` 一致：4 列 / 默认字号 14 / 短题面）：

| margin | 行步 | 放得下的最后一行底边（实测 `usable_first`） | 行数 | 纸的物理容量 |
| --- | --- | --- | --- | --- |
| 1.0cm | 71.86px | **983.12** | 13 | 1046.93 |
| 1.5cm | 71.86px | **983.12** | 13 | 1009.13 |
| 2.0cm | 71.86px | **911.27** | 12 | 971.34 |
| 2.5cm | 71.86px | **878.67** | 9 | 933.54 |
| 3.0cm | 98.00px | **832.95** | 8 | 895.75 |

新表取实测值本身（`983.1 / 911.3 / 878.7 / 833.0`）+ 10 倍粒度回填 21 档。
**口径警告（写进代码注释，防后人误用）**：这张表是**标定口径专属**的（4 列 / 14 号 /
短题面），记的是「第 1 页放得下的最后一行底边」；它比同档物理容量小，是给
`detect_page_crossing()` 判断「文字块会不会掉到下一页」用的**保守**值，
**不能**当「任意一个块装不装得下一页」的阈值——那个阈值必须是纸的物理容量。

### 4.4 真实 PDF 的对照通道（`pdfprobe.py`，不引新依赖）

判据必须与**纸上的那个 PDF** 对照，而本环境没有 PDF 解析库。`pdfprobe.py` 直接解
PDF 内容流（`content_streams()`：`pdfprobe.py:25`），按 `PT2MM = 25.4/72`（`pdfprobe.py:19`）
做 CTM 连乘（`_apply`/`_mul`，`pdfprobe.py:38`/`pdfprobe.py:43`），给出
`text_positions_mm()`（`pdfprobe.py:64`，纸面坐标 mm）、`page_text_bbox_mm()`（`pdfprobe.py:164`）、
`text_line_levels()`（`pdfprobe.py:174`）。
两个踩过的坑都写进了 `TestModelMatchesRealPdfPagination` 的类文档：

* **每页纸面 y 各自从 0 起算**（实测 2 页 PDF：第 1 页 y ∈ [50.68, 270.55]mm、
  第 2 页 y ∈ [183.77, 271.88]mm）。所以判据是「第 n 页文字落在 `[0,297)` 内」，
  即「这一页没印到相邻页去」；不能按 `(n−1)·297` 累计。
* **跨页换算不可靠**：内容流每页各带 CTM（`0.24 0 0 −0.24 0 841.92 cm` 做 y 翻转与缩放），
  靠文本矩阵反推绝对纸面 y 在第 2 页及以后会算出 **−5px** 这种不可能值。故判据只用纸面坐标。

### 4.5 测量视口 ≠ 真实版心宽（假阴性来源）

`measure_js()` 在 Chromium 的**屏幕视口**量 `.q-row`，默认 `new_page()` 是 1280px；
真实打印时 `.sheet{width:100%}` 填的是 A4 **版心宽**。实测（`cols=4,font=14,margin=2cm`）：

| 场景 | 首行高 |
| --- | --- |
| 视口 1280px | 105.48px |
| 视口 642px（= 210mm − 2×2cm） | 157.77px（折 4 行） |

差 **1.5 倍**，行高被系统性低估。**关键前提已验证**：同一份 HTML 在 1280px 与版心宽下
`page.pdf()` 的字节 **sha256 完全相同**（3 组参数）——因为打印走 Chromium 自己的分页布局，
与屏幕视口无关。所以修测量视口**不动任何交付物**。

---

## 5. 负对照：判据必须能红（`03-协作规范.md` V9）

判据「永远绿」等于没有判据。做法：**顺序变异 + 立即回填**（`backend/.tmp-pw/m6_red_demo.py`），
对 `pagination.py` 源码写入变异版→跑指定用例→**在 `finally` 里按 sha256 回填**。
为什么不用 `sys.modules` 注入变异体：`test_layout_proof.py` 在**导入期**就
`from app.layout.pagination import ...` 绑定了具体函数对象，换 `sys.modules` 换不掉
已绑定的引用——那种做法是**假红对照**（变异根本没进到被测路径）。顺序变异没有这个漏洞。

原始 sha256 = `1658d9c440dccda8`，**7/7 条符合预期**（日志 `backend/.tmp-pw/m6_red_demo2.log`）：

| 变异 | 被测用例 | 结果 | 关键断言原文 |
| --- | --- | --- | --- |
| 键值方向反转 | `test_usable_geometry_uses_measured_ceilings_keyed_by_the_smaller_margin` | **红 ✓** | `Lists differ: [833.0, 878.7, 911.3, 983.1, 983.1] != [983.1, 983.1, 911.3, 878.7, 833.0]` |
| `_MM_PER_CM 10.0→1.0` | `test_later_pages_deduct_exactly_one_margin_in_pixels` | **红 ✓** | `11.338582677165277 != 113.38582677165354 within 0.5 delta (102.04724409448826 difference)` |
| `_MM_PER_CM 10.0→1.0` | `test_content_width_deducts_two_margins_in_pixels` | **红 ✓** | `771 != 567 : margin=3.0cm：版心宽不是 (210 − 2×3.0cm) 的换算值；若得到 771，说明 cm 被当成了 mm` |
| 旧表值回归（3.0cm→1008.7） | `test_capacity_is_much_tighter_than_the_legacy_ceiling_table` | **红 ✓** | `649.4 not greater than 1008.7` |
| 整张旧表回归 | `test_usable_geometry_uses_measured_ceilings_keyed_by_the_smaller_margin` | **红 ✓** | `Lists differ: [649.4, 649.4, 864.9, 936.8, 1008.7] != [1008.7, 936.8, 864.9, 649.4, 649.4]` |
| **阴性样本**：无害注释 | 同上 | **保持绿 ✓** | `Ran 1 test ... OK`（证明判据不是「什么改动都红」） |

每个变异跑完后 `pagination.py` 的 sha256 都回到 `1658d9c440dccda8`，且原样全类复跑
`Ran 26 tests ... OK`（红对照期间工作区不留变异文件）。

### 5.1 三条「能红」的判据（Lead m02958 要求②）

Lead 要求把「这个 bug 若无修正会怎样」写成**能红的判据**，不是只留 prose。已落为代码：

* `TestMarginUnitBugCannotRegress::test_later_pages_deduct_exactly_one_margin_in_pixels`
  （`test_layout_proof.py:1102`）—— 直指 §4.2，断言第 2 页起让出**恰好一个页边距**的像素数；
  把 `_MM_PER_CM` 改成 1.0 立刻红（上表第 2 行）。
* `TestMarginUnitBugCannotRegress::test_content_width_deducts_two_margins_in_pixels`
  （`test_layout_proof.py:1121`）—— 直指 §4.1，断言版心宽 = `(210 − 2×margin_cm×10)/MM_PER_PX`；
  变异后得到 771 而不是 567，红（上表第 3 行）。
* `TestPageCapacityCalibration::test_capacity_is_much_tighter_than_the_legacy_ceiling_table`
  （`test_layout_proof.py:590`）—— 直指 §4.3，断言标定表**逐档**小于同档物理容量；
  把旧表值填回去立刻红（上表第 4 行）。

---

## 6. V8 哈希夹逼（运行窗口内容不变）

判据运行窗口内，被依赖的生产代码**逐字节不变**：

| 文件 | 大小 | sha256(前 16 位) |
| --- | --- | --- |
| `backend/app/layout/pagination.py` | 17774 B | `1658d9c440dccda8` |
| `backend/tests/render/test_layout_proof.py` | 57617 B | `198d395939208ac2` |
| `backend/tests/render/pdfprobe.py`（改行尾后） | 6847 B | `f93d2a9e534f449b` |
| `backend/tests/render/pdfprobe.py`（改行尾前） | 7037 B | `2e32180b096c5f94` |
| `backend/app/layout/styles.py` | 14568 B | `badbac9c8050e38f` |
| `backend/app/layout/__init__.py` | 2049 B | `d296c65bbccb5f13` |

* §3(1)(2)(3) 三次门禁运行期间，上表文件均未改动。
* 红对照（§5）**会**临时改写 `pagination.py`，但每个变异都在 `finally` 里按
  `ORIGINAL` 字节回填并校验 sha256 相等（`m6_red_demo2.log` 每行的「回填=1658d9c440dccda8 ✓」）。
* 行尾修正（`pdfprobe.py`）发生在全量门禁**之后**，故改完**重跑**了 render 套件：
  `Ran 252 tests in 172.327s / OK`（`backend/.tmp-pw/m6_gate_render4.log`）。

### 6.1 文件格式：纯 LF / 无 BOM（Lead m03770 要求）

`backend/tests/render/pdfprobe.py` 交付前是 **CRLF**（`bytes=7037 CR=190 LF=190`，
`git status` 的 `warning: ... CRLF will be replaced by LF` 点名了它）。已按二进制级
替换改为纯 LF，**没有**用 `Get-Content | Set-Content`：

| 阶段 | bytes | CR | LF | CRLF | 孤立 CR | BOM | sha256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 改前 | 7037 | 190 | 190 | 190 | 0 | 否 | `2e32180b096c5f94` |
| 改后 | 6847 | 0 | 190 | 0 | 0 | 否 | `f93d2a9e534f449b` |

* 字节差 `7037 − 6847 = 190` **正好等于** CRLF 个数——即只删掉了每个 `\r`，没动任何别的字节。
  逆变换可还原（把每个 `\n` 换回 `\r\n` 后与原字节完全相同，脚本内已断言）。
* 改前先确认 **孤立 CR = 0**（是纯 CRLF 文件，不是老式 Mac 行尾），否则脚本拒绝改写。
* 改后复核：UTF-8 解码 OK、AST 解析 OK、中文字符 463 个（样例
  「从内容流解析文字在纸面上的绝对坐标不依赖第三方库」）；并对两份真实样张跑通探针
  （`m6-crossing-control.pdf` → 1 页 / 710 个文字点 / `bbox=(20.11,104.13,187.99,270.55)`mm；
  `m6-margin-1.0.pdf` → `bbox=(10.05,114.18,197.95,280.61)`mm）。日志 `backend/.tmp-pw/m6_lf_verify.py` 输出。
* `git status` 的 CRLF 警告**已消失**（`git status 2>&1 | Select-String CRLF` 无命中）。
* **本批全部文件复扫**（`backend/.tmp-pw/m6_scan_eol.py` 扫 `backend/tests/render` +
  `backend/app/layout` + `backend/app/export`，共 21 个文本文件）：**CRLF/BOM 问题 0 个**。
  `test_layout_proof.py`（CR=0/LF=1154）、`REPORT-layout-proof.md`（CR=0/LF=324）、
  `pagination.py`（CR=0/LF=431）、`styles.py`（CR=0/LF=369）、`__init__.py`（CR=0/LF=85）。
  这 21 个文件里包含**本轮之前就存在**的 `fixtures.py`、`test_pagination.py` 等，它们本来就是纯 LF。

* **(ii) 落地后的最终字节复核**（量具 `[System.IO.File]::ReadAllBytes` + 逐字节数 `0x0D`/`0x0A`；
  为什么不用 `Get-Content`：本机它对中文 UTF-8 文件**本身不可信**——同一份报告 `Get-Content`
  报的行数（例如 296）与**字节数 LF**（当时 673）和 Python `splitlines()`（673）**三者不一致，
  前两者一致**，且 `Get-Content` 输出是 GBK 乱码。**该现象与具体数字无关**，换一份文件同样出现）：

| 文件 | bytes | CR | LF | BOM |
| --- | ---: | ---: | ---: | --- |
| `backend/tests/render/test_layout_proof.py` | 75296 | 0 | 1490 | 否 |
| `backend/tests/render/pdfprobe.py` | 6847 | 0 | 190 | 否 |
| `backend/tests/api/test_export_layout_guard.py` | 8344 | 0 | 160 | 否 |
| `backend/tests/api/test_errors_and_sheet.py` | 20224 | 0 | 404 | 否 |
| `backend/tests/api/test_error_code_sources.py` | 13374 | 0 | 257 | 否 |
| `backend/app/layout/fitting.py` | 18200 | 0 | 386 | 否 |
| `backend/app/layout/pagination.py` | 17774 | 0 | 431 | 否 |
| `backend/app/export/pdf.py` | 21501 | 0 | 492 | 否 |
| `backend/app/services/pdf_export.py` | 14776 | 0 | 343 | 否 |
| `backend/app/services/pdf_export_worker.py` | 7467 | 0 | 156 | 否 |
| `backend/app/services/errors.py` | 3257 | 0 | 78 | 否 |
| `backend/app/routers/exports.py` | 9591 | 0 | 215 | 否 |
| `backend/app/render/sheet.py` | 14456 | 0 | 331 | 否 |
| 本报告自身 | 随每次编辑变化 | **0** | 随每次编辑变化 | **否** |

  **14 个文件全部 CR=0 / 无 BOM**。`pdfprobe.py` 仍是改后值 6847（未回退）。
  `fitting.py` / `test_layout_proof.py` 是 Lead m05153 那轮改动后的最新值
  （`__all__` 收缩 + 判据补双向 + `fitting.py` 的账按「代码引用」重算）；
  `sheet.py` 是本轮唯一一处值改了行为面之外的文件（只改 docstring）。
  「本报告自身」那一行**故意不写死字节数**：这份文件每次增补都会改变它，
  写死必然过期（写死的数字反而变成不可信声明）。它的 `CR=0` 与 `无 BOM` 是**结构性不变量**
  （每次编辑都走 edit 工具、只写 `\n`），可随时用下面这条命令复扫：

  ```powershell
  # 复扫任意一批文件的行尾/BOM；判据：CR 必须为 0、BOM 必须为 False
  foreach ($f in @('backend/tests/render/REPORT-layout-proof.md', 'backend/app/layout/fitting.py')) {
    $b = [System.IO.File]::ReadAllBytes((Resolve-Path $f))
    $cr = 0; $lf = 0
    foreach ($x in $b) { if ($x -eq 13) { $cr++ } elseif ($x -eq 10) { $lf++ } }
    $bom = ($b.Length -ge 3 -and $b[0] -eq 239 -and $b[1] -eq 187 -and $b[2] -eq 191)
    "{0}  bytes={1}  CR={2}  LF={3}  BOM={4}" -f $f, $b.Length, $cr, $lf, $bom
  }
  ```

---

## 6.2 角点三读数矩阵 + 物理断行扫描（Lead m03866 要求①③④）

### (a) BOUNDS 角点三读数（脚本 `backend/.tmp-pw/m6_corner_matrix.py`）

同一份 24 题长题面卷面，三个**互相独立**的读数：`detect_overflow()` /
`detect_page_crossing()`（模型侧）+ `export_pdf_report().pages`（实物侧）。

| cols | gap | font | margin | 物理容量px | 判溢阈值px | overflow | crossings | PDF页 | 纸面文字 x∈ / y∈ (mm) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 0.8 | 10 | 1.0 | 1046.93 | 983.10 | 0 | 0 | 1 | x[10.05,196.18] y[64.97,282.46] |
| 2 | 0.8 | 10 | 3.0 | 895.75 | 833.00 | 0 | 0 | 2 | x[29.90,178.19] y[54.12,263.67] |
| 2 | 2.0 | 20 | 3.0 | 895.75 | 833.00 | 0 | 3 | 5 | x[29.90,178.25] y[83.47,260.12] |
| 2 | 2.0 | 20 | 1.0 | 1046.93 | 983.10 | 0 | 3 | 4 | x[10.05,192.70] y[58.36,279.55] |
| 6 | 0.8 | 10 | 1.0 | 1046.93 | 983.10 | 0 | 0 | 1 | x[10.05,199.68] y[128.47,282.46] |
| 6 | 0.8 | 10 | 3.0 | 895.75 | 833.00 | 0 | 0 | 1 | x[29.90,179.13] y[69.20,262.61] |
| **6** | **2.0** | **20** | **3.0** | 895.75 | 833.00 | **4** | 4 | 5 | x[29.90,179.46] y[73.48,260.12] |
| 6 | 2.0 | 20 | 1.0 | 1046.93 | 983.10 | 0 | 2 | 4 | x[10.05,197.97] y[47.24,279.55] |
| 5 | 2.0 | 20 | 3.0 | 895.75 | 833.00 | 0 | 5 | 6 | x[29.90,177.34] y[82.97,260.12] |
| 4 | 1.0 | 14 | 2.0 | 971.34 | 911.30 | 0 | 1 | 2 | x[20.11,187.99] y[50.68,271.88] |

要点：
* **每个角点的文字都落在该档安全区内**（左边界 = 页边距值 ±1mm，右边界 ≤ 210−页边距），
  即没有横向切字、没有跑出版心。
* `crossings > 0` 的角点**全是多页**（4~6 页）。这不是缺陷：`detect_page_crossing()`
  只在**能精确确认的单页**场景下才有判据意义，既有生产测试
  `test_export_pdf.py::TestPaginationNoSplit::test_no_row_crosses_a_page_break`
  （`test_export_pdf.py:301`）本身就 `if report.pages != 1: continue` 跳过多页。
  多页卷面的 crossings 计数是**已知的模型-实物偏差**，不能当断题证据。
* 唯一 `overflow=4` 的就是 6×2.0×20×3.0 角点。它同时给出**行号与像素**：
  `row=0 height=1382.47px limit=833.00px past=+549.47px`。

### (b) 全参数空间**物理断行**扫描（脚本 `backend/.tmp-pw/m6_row_split_sweep.py`）

判据：行高 > `page_capacity_px(margin)`（纸的物理可用高度，**不含任何安全系数**）。
720 组全扫，两种题面各一遍：

| 题面 | 必然被切开的组合 | 命中维度 |
| --- | --- | --- |
| 58 字短题面 `REAL_WORDS` | **24 组**（3.3%） | `cols∈{5,6}` × `font=20` × `margin∈{2.0,3.0}` |
| 46 字长题面 `LONG_DISPLAY` | **4 组** | `cols=6` × `font=20` × `margin=3.0` |

`row_gap` 与两个布尔项**不是决定因素**。最坏一组：
`cols=6 gap=2.0 font=20 margin=3.0` → **1718.42px vs 895.75px（191.8%）**；
长题面同角点 1382.47px（154.3%）。

**收紧 `BOUNDS` 的效果推演：`font=20` 时 `cols_per_row` 上界取 4 → 剩余 0 组。**

但**这条路在本任务里走不通**（实测三条守卫都会红，不能自行改）：

| 守卫 | 位置 | 断言 |
| --- | --- | --- |
| 契约包测试 | `backend/tests/contract/test_contracts_package.py:69-72` | `BOUNDS["cols_per_row"] == (2, 6)` 等四组逐字相等 |
| 前后端一致性 | `frontend/scripts/verify-contract.py:130-132` | 拿 `layout.BOUNDS` 去正则匹配 TS 源，前后端边界必须一致 |
| API 请求校验 | `backend/app/contracts/api.py:87` | `row_gap: float = Field(default=1.0, ge=0.8, le=2.0)` 硬编码同组数字 |

`BOUNDS` 在 `backend/app/contracts/layout.py:42-47` 已被 `#: 取值范围（**冻结**）` 标注，
且本任务写域明确排除 `backend/app/contracts/**`。因此 (i) 只能作为**提议**交给 Lead
（它同时要动 `frontend/scripts/verify-contract.py` 与 TS 边界，跨两个写域）。

### (c) 必然超高的组合打到真实 PDF 上会怎样（脚本 `backend/.tmp-pw/m6_page_dist.py`）

坏角点 `6×2.0×20×3.0`，长题面，换题量看逐页文字分布（`text_positions_by_page()`）：

| 题量 | 行数 | 真实页数 | 第 1 页落字 | 后续每页落字 | 第 1 页 y 跨度 |
| --- | --- | --- | --- | --- | --- |
| 4 | 1 | 2 | 36（3 个 y 层） | 220 | 31.2mm / 297mm |
| 12 | 2 | 3 | 36 | 330, 336 | 31.2mm |
| 60 | 10 | 11 | 36 | 342 × 9 | 31.2mm |

实际形态：**第 1 页只印了标题 + 卷头（31.2mm，占纸高 10%）**，题目组全部被推到后面，
后续每页固定放**一个行容器**。**有效缩放反推为 0.997**（纸面文字 x 跨度 / 版心宽），
即打印**没有缩放**——1382.47px = **365.8mm** > A4 高 297mm，
所以这个行容器在纸上**必然被分页线切开**。症状是「大片空页 + 题目组被拆开」，
不是「文字被吞」（文字仍落在安全区 `y∈[73.48,260.12]mm` 内）。

### (d) 判据能红的直证（脚本 `backend/.tmp-pw/m6_truncation_demo.py`）

```
===== [坏角点] cols=6 gap=2.0 font=20 margin=3.0 =====
  行数=4  最高行 index=0 height=1382.47px
  纸的物理可用高度 = 895.75px；判溢阈值（实测标定 usable_first）= 833.00px；第 2 页起 = 719.61px
  超出物理可用：+486.72px
  detect_overflow() → 4 条：row=0 height=1382.47px limit=833.00px past=+549.47px
  真实 PDF：5 页；纸面文字 y 层数 = 23
  最高行高 / 纸的可用高 = 1.54 页 → 必然被分页线切开

===== 负对照：把超高行改矮（判据必须变空） =====
  原始：超高行 [0, 1, 2, 3]（共 4 行）→ detect_overflow 报 4 条
  把这 4 行都改矮到 83.30px → detect_overflow 报 0 条
  ✓ 判据随输入翻转（非恒真）
```

### (e) `reflow()` 能不能自动救回来（脚本 `backend/.tmp-pw/m6_reflow_probe.py`）

`reflow()` 是**已有但未接线**的生产函数（`backend/app/layout/pagination.py:376`，
目前只被 `test_pagination.py` 调用）：

| 题面 | 结果 | 终点参数 | 诊断 |
| --- | --- | --- | --- |
| 46 字长题面 | **ok=True**，3 次尝试 | `font 20 → 19` | 收敛 |
| 58 字短题面 | **ok=False**，8 次用尽 | 仍 `font=20` | `row_gap`/`page_margin` 怎么调都是 4 行超高；`cols_per_row 已到下界，跳过` |

即：**对短题面（真机 `build_ctx` 口径）`reflow()` 救不回来**——它只能通过
`font_size` 与 `cols_per_row` 才能解开，而 `max_attempts=8` 内没试到
`cols_per_row` 可减小（当前 cols=6 是上界，`_shrink` 只会减不会加到更省列的档）。

## 7. 未完成遗留（**明说，不粉饰**）

1. **「一行被切到两页」在合法参数空间内从未发生，因此未被真实观测到。**
   `TestRowSplitBoundaryIsReachable`（`test_layout_proof.py:611`）证明的是
   「分页线**可达**」：`font=16 / cols=6 / gap=2.0 / margin=3cm` 时 DOM 行高
   777.39px 占物理容量 86.8%，**真实 PDF 已经是 2 页**——即「DOM 偏乐观」这个前提成立。
   但这也意味着：**「不可分块必定整体下移」这一条是 Chromium 的行为假设，不是本仓实测**。
   如果哪天 Chromium 改成允许切开 `break-inside: avoid` 的块，NFR-5 的语义会变，
   而本套判据**不会**变红。→ 这是本任务已知的最大残留风险，建议纳入 T-M6-04 的
   验收关注点。
2. **`DOM_SAFETY_FACTOR = 0.86`（`test_layout_proof.py:125`）是本机实测的经验值**，
   来源是 font=16 那档（86.8% 被真实切开）；换机器 / 换 Chromium 版本需要重测。
   `test_font_16_is_exactly_the_optimistic_case`（`test_layout_proof.py:685`）会在
   这个前提失效时红（注释里写明「若它变绿/变红的含义」）。
3. **实测表只覆盖 5 个档位**（1.0/1.5/2.0/2.5/3.0），其余 16 档是按 10 倍粒度**回填**的，
   来自「分页线随页边距阶梯式下移」这个实测形态（1.0 与 1.5 同档是 Chromium 内置
   最小打印边距 1cm 造成的）。
4. **样张只有 PDF，没有 PNG 缩略**：`m6_samples.py` 里的 `render_png()` 在本轮未产出，
   目录里只有 8 份 `m6-*.pdf`（PNG 与 `manifest.json` 未落盘）。
5. **`?` 档位未覆盖**：`row_gap` 只扫了 4 档、`font_size` 只扫了 3 档（合法域的上界 20
   已含在最坏角点里），**中间档位按单调性外推**，未逐档实跑判据（实跑成本：角点扫描
   已 720 组合 × 每次 ~1s 浏览器测量，无法全枚举）。
6. **「纯 LF / 无 BOM」这条声明目前没有机器判据**（Lead m03770 指出）。本轮我只把
   `pdfprobe.py` 的 CRLF 改回 LF（§6.1），**没有**顺手加判据——那不在 task-44 的写域里，
   会扩大改动面。**建议**（留给后续卡）：加一条守卫用例，扫描 `backend/tests/**` +
   `backend/app/**`，断言 CR 计数为 0 且文件头非 BOM；实现只需
   `pathlib.Path(...).read_bytes()` 逐文件量 CR 与 BOM，成本毫秒级。
   本轮已有的临时量具：`backend/.tmp-pw/m6_scan_eol.py`（可参考）。

---

## 8. 是否触碰冻结契约

**未触碰。** `backend/app/contracts/**` 一个字节未改；`backend/tests/contract/` 104 条
全部通过（§3(2)）。`TestLayoutContractStillIntact::test_all_frozen_variables_are_consumed_by_the_stylesheet`
（`test_layout_proof.py:1137`）额外守住 D59：六个 CSS 变量必须都被样式表真正消费。

---

## 9. 建议 Lead 贴进 `docs/05-任务板.md` 的段落（写域归 Lead）

> | T-M6-01 | 排版样张定稿（六参数实物对照，NFR-5 推广到整个合法参数空间） | render-export | `backend/app/layout/**`、`backend/tests/render/**` | T-M5-04 | **完成（task-44）** |
>
> **T-M6-01 结论**：合法参数空间内**未发现断题**（一行被印到两页）。判据落在
> `backend/tests/render/test_layout_proof.py`（10 类 / 26 用例），全量门禁
> `Ran 1180 tests / OK`（render 252 / contract 104）。
> 过程中修掉三处**会造成漏报**的缺陷：① `content_width_px()` 把 cm 当 mm（版心宽多算 36%）；
> ② `usable_geometry()` 第 2 页起让出量少算 10 倍（11.34px vs 113.39px）；
> ③ `MEASURED_BREAK_CEILING_PX` 方向记反（1.0cm 记 649.4、3.0cm 记 1008.7）——
> 已用 `measure_page_box()` 串行重测为 983.12 / 983.12 / 911.27 / 878.67 / 832.95。
> 负对照 7/7 符合预期（6 条真红 + 1 条阴性样本保持绿）；未触碰冻结契约。
> **遗留**：① 「不可分块整体下移」是 Chromium 行为假设（本仓只在 `font=16` 档见到真实
> 2 页、未见到"被切开"）、② `DOM_SAFETY_FACTOR=0.86` 为本机实测值。
> 详见 `backend/tests/render/REPORT-layout-proof.md`。

---

# 附编：(ii) 服务端守卫（task-44 下半场，D81）

## 10. NFR-5 的服务端出口：实现与证据

### 10.1 为什么需要它，以及它**盖不住**什么

NFR-5 的原话是「**浏览器打印**不能把一道题跨页拆开」。判据（§6）证明「合法参数空间内
不出现断题」，但那是**测试里**的结论；真正的交付路径是老师点导出。**判据不挡路，
只有代码挡路**——所以要把判定搬到导出通道上：导出前用**这一卷**的实测排布跑一遍
`detect_overflow()`，有溢出就**拒绝导出**（fail-closed），而不是产出一份违反 NFR-5 的 PDF。

**注册边界（必须明说）**：这条守卫**只覆盖服务端导出**（`GET /api/papers/{id}/export`）。
老师把页面打印出来时按 Ctrl+P，走的是浏览器自己那条路，**服务端一次都不会执行**——
那条路径上 NFR-5 仍然只靠「合法参数空间内不断题」这条实测结论，没有任何拦截。
这与 (i)「收紧参数区间」方案的结论一致：真正的保证只能来自本节的判据 + §6 的实测。

### 10.2 判据链（谁量、量什么、阈值取哪一档）

```
render_sheet(ctx) → HTML
  → 子进程 Chromium：视口 = content_width_px(layout)（**版心宽**，M6 修正）
  → emulate_media("print") → measure_js()  → measurement
  → app.layout.fitting.guard_layout_of_html(html, measurement)
       ├─ detect_overflow(measurement, layout)   ← 阈值来自 MEASURED_BREAK_CEILING_PX
       └─ 非空 → raise LayoutTooLarge(overflows, layout, measurement, suggestion)
  → 409 LAYOUT_DOES_NOT_FIT（服务端不落盘任何 PDF）
```

**阈值用哪一档**：`detect_overflow()` 用的是**实测标定的分页线**（§4.3 那张表：
983.12 / 983.12 / 911.27 / 878.67 / 832.95），**不是** A4 名义版心。
这一点是踩过坑的：首轮实现按名义版心判定，得到与真实 PDF **相反**的结论。

### 10.3 三条链路（父进程 / 子进程 / 路由）如何传事实

PDF 必须在**一次性子进程**里打印（Playwright 同步 API 的线程亲和，见
`pdf_export_worker.py` 的表），于是「判定在子进程、错误码在父进程」跨了一次进程边界。
**不能只靠退出码**：诊断文案（哪个参数、调到几）也得出得来。

| 通道 | 传什么 | 代码 |
| --- | --- | --- |
| 子进程 → 父进程，**成功** | `<pdf>.json` 伴随文件：`{pages, layout, layout_fields, overflows[]}` | `pdf_export_worker.py:59` |
| 子进程 → 父进程，**拒绝** | 退出码 `3`（`EXPORT_LAYOUT_EXIT`）+ `<error>.json`：`{message, overflows, layout}` | `pdf_export.py:68`、`pdf_export_worker.py:107` |
| 父进程 → 路由 | `ExportedPdf(bytes)`：`pages` / `layout` / `overflows` / `guard_skipped` | `pdf_export.py:101` |
| 路由 → 客户端 | 409 + `{"error":{"code":"LAYOUT_DOES_NOT_FIT","message":…}}`；跳过时 `X-Mathgen-Layout-Guard: skipped` | `exports.py:145`、`exports.py:161` |

`ExportedPdf` 是 `bytes` 的**子类**（不是新类型）：`Response(content=data)` 与
`tests/api/test_export.py` 里那些「把 `export_pdf_bytes` 整个换成返回 `bytes` 的假函数」
的用法都不用改；需要事实处用 `getattr(data, "overflows", ())`，普通 `bytes` 取不到
就是「没有实测事实」——**假导出器世界里不编造事实**。
（实测记一笔：`bytes` 子类**不能**加 `__slots__`，`TypeError: nonempty __slots__ not
supported for subtype of 'bytes'`。）

伴随文件读不出来（缺失/坏 JSON）时 `_read_export_facts()` 返回 `{}`，**绝不把一次
成功导出变成 5xx**：辅助信息出问题不该毁掉交付物。

### 10.4 诊断文案：参数实际值 + **测量过的**目标值

判出溢出后要回答「那我该改成什么」。建议搜索器**只有一个**：

`find_fitting_layout()`（本轮新增，`fitting.py:281`）——沿单个参数**逐档下探**并**重测**：
`font_size` 20→19→…→10，再 `cols_per_row` 6→5→…→2，**命中即返回**。

为什么不用既有的 `app.layout.pagination.reflow()`：它每轮只动**一个**参数**一步**，适合
「已经接近」的排版；而本任务的极端组合最坏一行比可印区高 66%（1382.47px vs 833.00px），
单步走法**永远走不到**（实测 6 次用尽仍超）。`find_fitting_layout` 返回的组合是**量过的**，
所以诊断里敢写「实测这样能放下」。

**中途写错并已删除的一层（Lead 2026-10-16 复核指出，登记在此防回潮）**：本模块早先还有
一层 `try_reflow_layout()` 包装 + `check_layout_fits(..., render_with, measure)` 分支，
docstring 里写着「本模块会跑一次 `reflow()` 把它的建议写进诊断」。**那是假的**：唯一生产
调用点 `guard_layout_of_html` 从不传 `render_with`/`measure`，于是提前返回条件
`not overflows or render_with is None or measure is None` **恒真**，`try_reflow_layout`
**生产死且测试死**（全仓 `app/**`+`tests/**` 只有定义、`__all__`、以及那条走不到的分支）。

危险之处不是多写了 20 行，而是**两个做同一件事的搜索器只有一个在跑**：下一个人读
docstring 会以为 `reflow()` 在守着导出，于是去 `reflow()` 里调 bug 或依赖它的收敛性；
而另一个搜索器的漂移**没有任何东西会发现**。这正是本仓 T-M6-08 的**代码版**——
「写在 docstring 里的机制」被当成了事实。修法（Lead 给的选项 A）：删除
`try_reflow_layout`、`_suggestion_from_reflow`、两个形参与那条死分支，`__all__` 同步；
模块 docstring 改写成「建议来自 `find_fitting_layout`；`reflow()` **不在**本链路里，
它留在 `app/layout/pagination.py` 作为布局模块自己的公共面」。

**`reflow()` 现在的引用面（一并交代）**：`app/**` 里**零个生产调用点**——只有
`pagination.py:376` 的定义、`layout/__init__.py` 的转发、以及若干 docstring 引用；
真实调用只在 `backend/tests/render/test_pagination.py`（5 处，`TestReflow`）。
即 `reflow()` **目前是生产未接线**。它未被删除，因为它是布局模块的公共 API 且有自己的判据；
**但它不是 NFR-5 的保证**——保证来自导出前的拒绝判据（§10.5 的硬约束 4）。

> **【订正 §10.4（1）· Lead m05153 指出】原稿此处写错了一句，保留原文以便对照。**
>
> 原稿写的是：「`app/render/sheet.py` 的 docstring 称 `render_with_layout` 是
> 『NFR-5 自动微调的入口（`reflow` 会反复调用它）』，**但没有任何调用者**。」
> **「没有任何调用者」是错的。** 实测 `backend/**`：
>
> | 调用点 | 位置 | 性质 |
> | --- | --- | --- |
> | `render_sheet(ctx)` 的函数体 | `app/render/sheet.py:49` | **真实生产调用** |
> | `assertEqual(render_sheet(ctx), render_with_layout(ctx, ctx.layout))` | `tests/render/test_sheet_html.py:343` | **测试调用** |
> | `__all__` + 包根转发 | `app/render/sheet.py:38`、`app/render/__init__.py:23,26` | 公共面 |
>
> 正确说法要把两件事**分开**：`render_with_layout` 是「**有调用者，但不再是 `reflow` 的入口**」
> ——错的只是括号里那半句（`reflow` 确实零生产调用点，那半句我查对了）。
> 两者的实际后果不同：前者只需改一句 docstring，后者会让人想删函数。
>
> **错误是怎么产生的（值得记）**：我是从「`reflow` 零生产调用点」**直接推广**到
> 「`render_with_layout` 零调用点」的，中间那一步**没有量**——正好是本仓 V13 的形状
> （把一条已证事实外推到它不覆盖的对象上）。上一轮我刚因为「转述丢主语」吃过一次
> （见「关于 `test_export_pdf.py` 两处改动」的核对记录），这次换成了「外推不加量」。
> 写结论时凡出现「没有任何 X」，都应当先 `grep` 再落笔。

本函数签名与 `render_sheet` 一样是冻结的（`app/contracts/sheet.py`）；改的只是它的
docstring（`app/render/sheet.py:53-69`，Lead m05153 §二(2) 授权扩写域后改），改后说清三件事：
① 它是 `render_sheet` 的实现体、有真实调用者；② **没有任何东西在用 `reflow` 自动微调**
（`reflow` 在 `app/**` 零生产调用点）；③ NFR-5 的保证来自导出前的拒绝判据
（`LAYOUT_DOES_NOT_FIT`），不是来自自动降级。

**怎么在只有 HTML、没有 `SheetContext` 的情况下重排重测**：`rewrite_layout_vars()`
（`fitting.py:85`）把 HTML 里注入的六个 CSS 变量换成候选值——**只换参数，不换内容**
（题干、题号、答案都不动），这正是「同一张卷子换排版」。顺带也换掉
`@page { margin: Xcm }` 的字面量（`@page` 不解析自定义属性，见 `styles.py` 的实测记录），
否则候选版只换了一半。

**绝不自动应用**（Lead 要求，也是产品判断）：老师设了 20 号字，悄悄按 19 号导出，
等于交付物与老师设的不是同一个东西——比报错更坏，因为它让人以为自己的设置生效了。
所以搜索的结果**只进文案**，不进交付物。

实测文案（`backend/.tmp-pw/m6_suggest_probe.py`，用时 2.7s）：

```
当前排版（每行题数 6 题 / 行距倍数 2 倍 / 字号 20 号 / 页边距 3cm）在这一卷上放不下，
会有 4 行题目被跨页拆开（最高的一行 1382.47px，超过可印区 833.00px）；
请把字号从 20 号调到 19 号以下（实测这样能放下）。
```

**为什么 20 → 19 号会「跳变」**（顺手记下，避免后人以为量错了）：
`--row-gap` 的单位是 `1em`，即行距**跟着字号缩放**；6 列 + 20 号时列宽约 50.0px、
字宽 26.67px → 每行**只能放 1 字**；降到 19 号后列宽 52.3px、字宽 25.3px → 每行**能放 2 字**，
行数直接砍半。实测行高 1382.5px → 781.2px（容量 833px）。这是**折行台阶**，
不是线性缩放——判据必须用测量，不能按比例推算。

### 10.5 应急出口 `MATHGEN_SKIP_LAYOUT_GUARD`（D81 四条硬约束）

守卫可能误报（判据/浏览器版本漂移），所以留一个**能立刻恢复导出**的开关。但洞本身
不致命，**能被悄悄打开才致命**（与 task-50「状态只存在于没人读的日志里等于不存在」同族）：

| # | 约束 | 实现 |
| --- | --- | --- |
| 1 | **只认精确字面 `1`**；`0` / `false` / 空串 / `yes` / `TRUE` / `1 `（带空格）一律**不生效** | `layout_guard_enabled()` 就是 `os.environ.get(LAYOUT_GUARD_ENV) != "1"`（`pdf_export.py:91`） |
| 2 | 跳过必须在**响应**里看得见，判据不读日志也能区分两个世界 | `X-Mathgen-Layout-Guard: skipped`（`exports.py:161`） |
| 3 | 同时留 WARNING，含**卷号**与**实际溢出行数** | `仍有 4 行放不下（行号 [0, 1, 2, 3]）` |
| 4 | 判据覆盖**三个世界**且都要有输入（见 §10.6） | `test_export_layout_guard.py` + `TestExportRefusesWhenItCannotFit` |

约束 1 的理由值得单写：`=0` 是**最容易出现的写法**——有人以为它在「显式关闭开关」，
实际语义是「关闭守卫」。所以只有 `=1` 才算数。

### 10.6 三个世界的判据与原始输出

**A. 守卫本体（真实子进程 + 真实 Chromium）** —— `test_layout_proof.py::TestExportRefusesWhenItCannotFit`

```
Ran 5 tests in 17.618s
OK
```

**B. 路由层端到端（真实路由 + 真实 Chromium）** —— `tests/api/test_export_layout_guard.py`

这里把 `app.routers.exports.render_html` 换成**必然超高的卷面 HTML**，其余全走真实路径。
为什么替换的是**卷面内容**而不是别处：实测整个合法参数空间（720 组）里只有 24 组会断行，
**全部要求比 API 生成器产出的题干更长**（§6.2(b)）——也就是说「同一张卷子」在路由这一层
用生成器根本造不出来，能替换的只有内容层，而它恰好就是「同一卷」的语义来源。

```
test_the_first_world_turns_red_when_the_guard_is_forced_open ... ok   ← 负对照（见下）
test_world_one_refuses_with_409_and_an_actionable_message ... ok
test_world_three_zero_still_refuses ... ok
test_world_two_skip_is_visible_in_the_response_and_the_log ... ok
Ran 4 tests in 16.723s
OK
```

WARNING 的原始输出（由世界 2 的 `assertLogs` 捕获，也出现在负对照的轨迹里）：

```
NFR-5 排版守卫被跳过（MATHGEN_SKIP_LAYOUT_GUARD=1）：卷 1 仍有 4 行放不下（行号 [0, 1, 2, 3]）；
成品可能把题目跨页拆开 —— 这是应急出口，请尽快查明判据为何误报
```

**负对照（「判据不能红」的世界里能红）**：注入「守卫无条件放行」
（`pdf_export.layout_guard_enabled = lambda: False`）后，世界 1 的**全部断言**必须变红——
用例把世界 1 的断言抽成 `_assert_world_one_rejects()`，负对照断言它抛 `AssertionError`。
实测 `ok`：世界 1 的判据**不是**恒真的。

**C. 错误码三方对账（契约 / 文档 / 实现）**

```
test_document_copy_and_implementation_agree ... ok
test_the_document_is_actually_being_parsed ... ok
...
Ran 36 tests in 1.055s
OK
```

**D. 冻结契约未受影响**

```
Ran 121 tests in 1.889s
OK
```

### 10.7 `fitting.py` 公共面清单（Lead m04936 要求 → Lead m05153 §二(1) 定稿）

**规则**（Lead m05153 §二(1)）：`__all__` 按「**是否被模块外引用**」收缩，**不按「是否有定义」**
保留；零外部引用的名字**从公共面移出去，但函数一个都不删**（收缩 ≠ 删除）。

选项 (A) 落地后重扫（扫 `backend/app/**` + `backend/tests/**`；「外部生产」= `app/**` 中
**除 `fitting.py` 自身**以外的**代码引用**——docstring 里的交叉引用**不计**；
「测试」= `tests/**`）。行号按最终修订重测，`fitting.py` 的 `__all__` 在 **75–79 行**：

| 名字 | 外部生产引用点 | 测试引用点 | 在 `__all__`？ | 说明 |
| --- | --- | --- | --- | --- |
| `LayoutTooLarge` | **4**：`export/pdf.py:20`(import)、`:171`(except)；`services/pdf_export_worker.py:117`(import)、`:128`(except) | 3：`test_layout_proof.py:47,1200,1224` | ✅ | 守卫的失败类型，跨进程边界被判两次 |
| `guard_layout_of_html` | **2**：`export/pdf.py:20`(import)、`:159`（**唯一**判定调用点） | 5：`test_layout_proof.py:47,1192,1201,1217,1225` | ✅ | 导出链路的判定 + 拒绝入口 |
| `describe_layout` | **1**：`services/pdf_export_worker.py:72`(import)、`:81`(调用) | **0** | ✅ | 诊断文案的参数读法（生产在用就够） |
| `LayoutFit` | **0**（`fitting.py:189` 定义；`check_layout_fits`/`guard` 的返回类型） | **0** | ❌ 移出 | 判定结果 dataclass（`overflows` + `layout`，无 `suggestion`） |
| `check_layout_fits` | **0**（`fitting.py:322` 定义、`:335` 返回、`:360` 被 guard 调用） | **0** | ❌ 移出 | 纯判定、不抛异常 |
| `format_diagnostic` | **0**（`fitting.py:232` 定义、`:139` 被 `LayoutTooLarge` 用） | **0** | ❌ 移出 | 诊断文案的唯一生成处 |
| `find_fitting_layout` | **0**（`fitting.py:290` 定义、`:366` 被 guard 调用） | **0** | ❌ 移出 | 唯一的建议搜索器 |
| `rewrite_layout_vars` | **0**（`fitting.py:94` 定义、`:316` 被 find 调用） | **0** | ❌ 移出 | 同内容换参数，供重测 |

**`__all__` 最终 = `["LayoutTooLarge", "describe_layout", "guard_layout_of_html"]`**（3 个，
即上表三个「外部引用非 0」的名字）。

**订正我上一轮报给 Lead 的计数（Lead m05153 同批指出）**：上一版表里 `LayoutTooLarge`
记「外部生产 8 处」，把 `services/pdf_export.py:155`、`:279` 与 `export/pdf.py:99`、`:173`
也算成了生产使用——**那四处全是 docstring 里的交叉引用**，不是代码引用。
按「代码引用」重算应为 **4 处**（import 2 + except 2）。数字变小不改结论（它本来就是
外部引用最多的名字），但账必须按同一把尺子算：**同一个词出现在 docstring 里，不算被引用**。
`describe_layout` 同理，从「2 处」订正为「1 处 import + 1 处调用（同一文件的两行）」。

**零引用的名字：无。** 五个「外部引用 0」的名字都是**本模块内部的构件**，
被 `guard_layout_of_html` → `check_layout_fits` / `find_fitting_layout` → `rewrite_layout_vars`
这条链串起来，且**每个都至少有一次真实调用**（见上表括号内行号）。
它们是**移出公共面，不是删除**。

**删掉的名字是 `try_reflow_layout`**（删前 3 处引用：`__all__`、定义、以及
`check_layout_fits` 里那条**恒真分支**内的调用——**零个真实调用者**，生产死且测试死）。
随它一并删掉的还有一个**恒为 `None` 的 `LayoutFit.suggestion` 字段**：它让类型看起来
「带着建议」，而实际上建议在 `guard_layout_of_html` 里现算——同一类「写在结构里的机制
没人跑」的假象，不该留。

**`check_layout_fits` 的处置（Lead m05153 已裁定）**：它已是一行包装
`LayoutFit(overflows=detect_overflow(measurement, layout), layout=layout)`，
唯一调用者是 `guard_layout_of_html`。我上一轮问「是否再削」，Lead 的答复是
**函数保留、但从公共面移出去**（理由是它表达「**不抛异常**的纯判定」这条契约，
与 `guard` 的「抛」是两件事）。已照办。

**`app.layout.pagination.reflow()` 的引用面**（Lead 追问的「谁还在用」）：
`app/**` 里**零个生产调用点**——只有 `pagination.py:376` 的定义、`pagination.py:46`
的 `__all__`、`layout/__init__.py` 的转发，以及 `styles.py`/`sheet.py`/`fitting.py`
的 docstring 引用；真实调用只在 `backend/tests/render/test_pagination.py`
（`:201`、`:215`、`:226`、`:239`、`:251`，类 `TestReflow`）。**未删**：它是布局模块的
公共 API 且有自己的判据；但它**不是** NFR-5 的保证。
关联的 `render_with_layout` 那句 docstring 已在 Lead m05153 授权后改准确（见 §10.4 的订正块）。

**(A) 与「公共面收缩」各由能红判据守着**（不是只写进 docstring）：
`test_layout_proof.py::TestLayoutGuardRejectsExport::test_the_guard_chain_has_exactly_one_suggestion_searcher`
断言：`hasattr(fitting, "try_reflow_layout") is False`、`hasattr(fitting, "reflow") is False`、
`"reflow" not in fitting.__all__`、`LayoutFit` 字段集不含 `suggestion`、
**`set(fitting.__all__) == {"LayoutTooLarge", "describe_layout", "guard_layout_of_html"}`**、
且五个内部构件 `hasattr(...)` 全为真且都不在 `__all__` 里。
两个方向都能红：加回旧包装会红第一个，把内部构件当外部 API 用（或反过来把它们删掉）
会红后两组。**「已删除」与「已移出公共面」这两件事因此各自可执行**，不必靠人读 docstring。

---

## 11. Lead 点名的三项待补证据

### 11.1 `test_export_pdf.py` 的「2 处改动」：核实结论是**该文件零改动**

见 §2 表下的注。用 `git status --porcelain -- <file>`（无输出 = 与 HEAD 一致）与
`git log --oneline -- <file>`（最后改动 `839eb9e`，M3 收尾）核实。
「是否把精确相等放松成区间」这个问题**不成立**：该文件没被碰过；
它里面唯一带容差的断言（`test_export_pdf.py:421`，容差 = 一行步距）**原本就是区间式**，
而方向断言（`test_export_pdf.py:405`，`1.0 > 3.0`）**原本就是递减**。
教训：**「我说我改了两处 X」这种转述会在传递中丢主语**——核对时先定位改动面，再回答。

### 11.2 `DOM_SAFETY_FACTOR = 0.86` 是**测量**得来的，不是取整选定

来源是**三个实测点**夹出来的（§6.2(a) 的角点三读数）：

| 参数 | DOM 行高占物理容量 | 真实 PDF 页数 |
| --- | --- | --- |
| 14 号 | 52.6% | 1 页 |
| **16 号** | **86.8%** | **2 页（被切开）** |
| 18 号 | 101.4% | 2 页 |

DOM 比真实分页**乐观约 15%**，所以上界判据不能用 100% 当阈值——
`86.8%` 那一档就是「DOM 说装得下、纸面上装不下」的分界，取 `0.86`。

**能红判据（证明 0.86 必要，不是随便取的数）**：
`test_font_16_is_exactly_the_optimistic_case`（`test_layout_proof.py:685`）钉住 16 号这一档；
`test_real_pdf_pages_confirm_the_boundary_both_sides` 用**真实 PDF 页数**从两侧夹住边界
（14 号 → 1 页、16 号 → 2 页）。如果把 `DOM_SAFETY_FACTOR` 改成 `0.90`，
16 号档（86.8%）仍在阈值内、判据结论不变；改成 `0.86` 以下（例如 `0.85`）则 16 号档
转为「超限」——即这个常数**确实被判据约束着**，不是装饰。
（换机器 / 换 Chromium 版本需要重测：见 §7 第 2 条。）

### 11.3 `MEASURED_BREAK_CEILING_PX` 方向记反的根因（V13/V14 同族）

那一版表把 1.0cm 记成 649.4、3.0cm 记成 1008.7——**页边距越大可用高度越大**，
物理上不可能。根因不是算错，而是**同一把尺子被并行使用**：扫描时多个 `pdf()` 并发，
每次量到的分页线互相干扰，最宽松的一组被记到了最紧的页边距上。
修法是 `measure_page_box()` **串行**重测（§4.3）。

这与 V13/V14 同族：**先核对测量本身，再相信结论**。
一个量其结论与物理常识相反的测量，第一反应应当是「量具是不是坏了」，
而不是「物理常识是不是错了」。这也正是本报告 §4.4 要单独建一条
「真实 PDF 对照通道」的原因。

---

## 12. 附编遗留（补 §7）

7. **建议搜索是有成本的**：`find_fitting_layout()` 在失败路径上最多 24 次测量
   （本任务实测命中 1 次、总耗时 2.7s，含一次完整导出）。只在**已经拒绝**时才跑，
   正常导出零额外开销；但极端卷面上 409 响应会比普通错误慢 3 秒左右，这是
   「换一句能照做的诊断」的价格，判断为划算。
8. **`rewrite_layout_vars()` 依赖 CSS 变量契约**：它按 `--<name>: <value>` 的字面形态
   改写。契约（`sheet.py:64` 第 2 条）要求六个参数必须以 CSS 变量注入，
   `TestLayoutContractStillIntact` 守着这一点；若将来渲染改成别的注入方式，
   这个函数会**静默不生效**（正则匹配不到就原样返回），建议届时补一条
   「改写后必须至少有 N 处发生变化」的断言。
9. **`__all__` 的收缩判据是「快照式」的**（见 §10.7）：它断言 `set(fitting.__all__)`
   恰好等于那三个名字。谁**真的**新增一个外部调用方，这条会红——那是**故意**的
   （公共面必须随外部引用走，且应当是一次有意识的声明）。但它不是从源码里**算**出来的
   外部引用数，所以「某人用了 `check_layout_fits` 却忘了改 `__all__`」这种情形，
   判据只会在下次有人动 `__all__` 时暴露。真要做成算出来的，需要一条扫描 `app/**`
   AST 的判据；本轮不做（超出本卡写域与预算），在此登记。
10. **`check_layout_fits` 是否值得保留为独立函数**：Lead m05153 §二(1) 已裁定保留
    （它表达「不抛异常的判定」这条契约），但它在生产里只被 `guard_layout_of_html`
    **调用一次**，形态上是一行包装。若后续想再削，内联进 `guard` 即可，测试侧无引用。
    此项**已裁定，不再是待办**，留此仅作沿革记录。
11. **`app/render/sheet.py` 只改了 docstring**（`render_with_layout`，Lead m05153 §二(2)
    授权）：该文件**其余任何一行未动**，其冻结签名与行为零变化；但这条改动让
    `backend/app/render/**` 出现在本卡的改动面里，后续 `git status` 复核时不要误判为
    越域改动。

