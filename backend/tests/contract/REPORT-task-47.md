# task-47（T-M6-07）`docs/04` §7 前端路由表 ↔ 两个 `App.tsx` 逐行对账 —— 独立验收报告

**结论：通过。** 新增判据 **15 条**（`backend/tests/contract/test_route_table_reconciliation.py`），
契约域 **89 → 104** 全绿；判据双向可红、两侧有下限、解析不出内容时大声红而不是静默变绿。
**历史同源实证红出 2 条**（1 条是派单点名的「从 M4 起漏记」，另 1 条是此前没人报过的**反向漂移**）；
与派单预期的「应红出那两条老师端路由」**不同** —— 差异与理由见 §5，**按要求如实报告，未调判据**。

---

## 0. 一屏结论

| 项 | 结果 |
| --- | --- |
| 判据文件 | `backend/tests/contract/test_route_table_reconciliation.py`（514 行） |
| 判据条数 | **15**（11 条负对照 + 4 条正对照） |
| 契约域门禁 | `discover -s backend/tests/contract -t backend` → `Ran 104 tests in 1.116s` / **OK** / exit 0（89 → 104） |
| 全量后端门禁 | `Ran 1119 tests in 454.331s` / **FAILED (errors=6)** —— 6 条**全部**来自别人正在改的 `frontend/**`（dist 旧 + `tsc -b` 不过），与 task-47 无关；见 §4.5。**本任务的域内判据 104/104 绿** |
| 历史实证（同源） | 1202e15 的 §7 ↔ 1202e15 的两个 `App.tsx` → **2 条问题**（点名到路由 + 出处） |
| 「当年没有任何东西会因此变红」的直证 | 1202e15 的 `backend/tests/contract/` 只有 8 个文件，逐个 `git show` 后 grep，无一条读 §7 —— 见 §5.3 |
| 改动文件 | 新增判据 1 个 + 历史脚本 1 个 + 日志；`docs/04` §7（2 处）+ `docs/06` §9.12（新增） |
| 未动 | `backend/app/**`、`frontend/**`（零改动） |
| 它不能发现什么 | **两边同时漏同一条**、`element` 绑了哪个组件、其它路由注册方式 —— 见 §7 |

---

## 1. 任务与由来

`docs/04-接口契约.md` §7 是一张**手工维护**的前端路由表，路由的真相在
`frontend/src/App.tsx`（老师端）与 `frontend/src/student/App.tsx`（学生端）。
M5 收口时核对发现两条漏记：task-40 新增的两条老师端成绩路由没登记，
**而 `/student/paper/:code`（`frontend/src/student/App.tsx:22`）从 M4 起就在跑，
却从来没进过 §7，一直没人发现**。

根因与 D79 / `docs/06` §9.9 同族（V13）：**靠人记得同步的约定迟早漂移，而且漂移是静默的** ——
没有任何东西会因为文档少一行而变红。§7 表下原本就写着「**这张表目前没有机器判据**，
所以『改了 `App.tsx` 忘了改这里』仍然发现不了」。task-47 把这句话变成判据，并订正那句话。

---

## 2. 判据读什么（两侧都不猜格式）

| 侧 | 形态 | 处理 |
| --- | --- | --- |
| 文档 | §7 表行 `| 老师\|学生 | \`路由\` | 页面 |`（`docs/04-接口契约.md:388-400`，11 行） | 只认这种形态；解析出空集时**大声红** |
| 代码 | `<Route path="字面量">` | **花括号深度**感知扫标签 + 标签栈还原嵌套 |

* **为什么必须花括号深度**：`element={…}` 里嵌了 JSX（老师端的兜底
  `path="*"` 正是这种写法，`element` 里有 `<div className="fatal-error">`），
  朴素正则 `<Route[^>]*>` 会在那里的 `>` 处截断，从而**漏掉整条路由**。
* **嵌套**：老师端 `/teacher` 是父路由，相对子路由拼成 `/teacher/paper/:id`；
  学生端按 `frontend/src/student/main.tsx:26` 的 `basename="/student"` 拼前缀。
* **不算页面路由**：`path="*"` 兜底；`element={<Navigate …/>}` 重定向。
  重定向**不要求**登记成表行，但它的**目标**必须在表里 —— 否则「表里少一条真页面」
  会被重定向悄悄盖住。
* **参数名不比**：`/teacher/paper/:paperId`（代码）与 `/teacher/paper/:id`（文档）
  视作同一条，两侧都归一成 `:id`。比参数名只会制造假红。
* **下限（防「空集对空集」全绿）**：文档 ≥8 行、代码 ≥8 条，另按端各一条
  （老师 ≥5、学生 ≥3）。
* **形态坏了要大声红**：解析不出任何表行、解析不出任何 `<Route`、标签没有闭合的 `>`、
  文件读不到 —— 一律抛 `AssertionError` 并说明原因。

---

## 3. 判据条数与位置

`backend/tests/contract/test_route_table_reconciliation.py`（514 行）：

* 纯函数（判据本体，可被脚本与负对照直接调用）：`normalise_route:78`、`read_text:86`、
  `doc_routes:97`、`route_tokens:119`、`AppRoutes:157`、`parse_app:169`、`code_routes:226`、
  `compare_routes:240`、`assert_reconciled:251`。
* `class TestNegativeControls:351` —— **11 条**（`:370,375,380,385,394,404,415,421,432,439,446`）：
  01 删学生端表行 → 红且点名方向；02 删老师端表行 → 同向红；03 插幽灵行 → 红且点名「文档有、代码没有」；
  04 文档把 `:code` 改名 `:paperCode` → **仍绿**；05 空文本 / 只有标题 / 表格换成围栏 → 报「找不到任何」；
  06 表行缩到 2 行 → 报「文档侧表行 2 < 下限 8」；07 `App.tsx` 无 `<Route` → 报「解析不出任何」；
  08 学生端截到 1 条 → 报「学生端页面路由」下限；09 重定向目标改成表里没有的路径 → 红且点名；
  10 标签没有闭合的 `>` → 报「没有闭合的 `>`」；11 路径读不到 → 报「读不到」。
* `class TestPositiveControls:459` —— **4 条**（`:468,488,496,503`）：
  01 解析器**真的读到**真文件（老师 ≥5、学生 ≥3，且文本锚点存在、嵌套已还原、`/*` 与重定向都没被当页面）；
  02 真文档 + 真代码过门禁且两侧各 **11** 条、键集合相等；03 由代码合成的「一致」markdown →
  `compare_routes` 返回 `[]`；04 合成文档也过门禁（防恒红）。

---

## 4. 原始输出

### 4.1 契约域（含新增 15 条）

```
> & .\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests\contract -t backend
Ran 104 tests in 1.116s

OK
DOMAIN3_EXIT=0
```

日志：`backend/tests/contract/artifacts/task47-contract-domain3.log`
（`task47-contract-domain.log` 为修复过程中的红：`Ran 104 tests / FAILED (failures=3)`）。

### 4.2 判据自身修复过程（3 条假红，都是判据的错，不是产品的错）

1. `_drop_doc_row` 按「整行含 `` `route` ``」删行 → `docs/04:402` 的**说明段落**也引用了
   `` `/student/paper/:code` ``，于是删了两行，夹具自检直接抛「负对照没删掉恰好一行」。
   改为**只删表行**（`_DOC_ROW_RE.match(line)`），并断言「恰好删掉 1 行」。
2. 负对照 10 用 `teacher.replace("/>", ">", 1)` 造未闭合标签 → 第一个 `/>` 之后的 `>` 仍能收尾，
   判据**没有红**（`AssertionError not raised`）。改成显式合成
   `'<Routes><Route path="/teacher" element={<div className="fatal-error">'`，判据报
   「没有闭合的 `>`」。
3. 模块 docstring 里写了 `` \` ``（在非 raw 字符串里是非法转义）→ `SyntaxWarning: invalid escape sequence`。
   去掉多余反斜杠。

### 4.3 全量后端门禁（第一次：被别人的半写状态打断；第二次见 §4.4）

```
> & .\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -t backend
Ran 566 tests in 238.177s
FAILED (errors=31)
ALL_EXIT=1
```

日志：`backend/tests/contract/artifacts/task47-backend-all.log`。**31 条全部是
`unittest.loader._FailedTest`（导入期就会死），根因同一个**：

```
ERROR: tests.security.test_student_whitelist (unittest.loader._FailedTest.…)
Traceback (most recent call last):
  …
  File "C:\…\backend\tests\security\test_student_whitelist.py", line 14, in <module>
    from tests.security._harness import (
  …
  File "C:\…\backend\app\export\pdf.py", line 333
    key = (cache_key if cache_key is not None else margin, margin, max_rows)
IndentationError: unexpected indent
```

→ 这与本任务的判据无关：`backend/app/export/pdf.py` 当时正被别人改到一半（见 §4.4）。
`Ran 566` 也说明**测试数不能当绿灯看** —— 收集阶段就中止了，后面 500 多条根本没跑。

### 4.4 环境事实：窗口内有别的写者（如实登记，与本任务结论无关）

跑契约域时**撞到过一次来自别人写域的瞬态语法错**：

```
ERROR: test_export_is_assembled_before_the_id_route (tests.contract.test_contracts_package…)
  File "C:\…\backend\app\main.py", line 28, in <module>
    from app.routers import exports as exports_router
  …
  File "C:\…\backend\app\export\pdf.py", line 333
IndentationError: unexpected indent
Ran 104 tests in 0.886s / FAILED (errors=1)
```

同一文件几秒后 `py_compile.compile(…, doraise=True)` → `COMPILE OK`，复跑即 `104 / OK`。
`git status --short` 当时显示工作树里另有 `M backend/app/export/pdf.py`、
`M backend/app/layout/pagination.py`、`M frontend/src/teacher/ConfigForm.tsx`、
`M frontend/src/teacher/pages/LibraryPage.tsx`、`M frontend/scripts/verify-ui-voice.mjs`
（**均不在本任务写域**，我一行未动）。→ 结论：那是一次**半写状态的瞬时读**，
不是产品缺陷，也不是本判据的问题；但它说明「窗口里跑全量门禁」在别的写者落盘时会被打断，
而且**打断的样子是 31 条 loader 错 + 只跑 566 条**，不是「少一条」。

**复跑（树恢复可编译之后）**：见 §4.5。

### 4.5 全量后端门禁第二次：`Ran 1119 / FAILED (errors=6)` —— 6 条全部来自**别人正在改的前端**，不是本任务

```
> & .\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -t backend
Ran 1119 tests in 454.331s
FAILED (errors=6)
ALL2_EXIT=1
```

日志：`backend/tests/contract/artifacts/task47-backend-all2.log`。6 条全是 `setUpClass`，
分两类，**都不在 `backend/tests/contract/**`，也都不是 task-47 引入的**：

1. `tests.e2e_m5.test_m5_ui_acceptance.TestScoreUiWithRealBackend`（task-42 的判据，
   我这次一行未动）：

   ```
   File "…\backend\tests\e2e_m5\test_m5_ui_acceptance.py", line 123, in setUpClass
       cls.freshness = _require_fresh_dist(cls)
   AssertionError: 前端产物早于源码 → 本套件判据无效。请在 `frontend/**` 写域里先跑
     `pnpm --dir frontend build`（src=1792110585 dist=1792110141）。
   ```

   → **守卫按设计工作**：`frontend/src/**` 刚被改过（`git status` 里的 `M
   frontend/src/teacher/ConfigForm.tsx`、`M frontend/src/teacher/pages/LibraryPage.tsx`），
   而 `frontend/dist/**` 还是旧的（src 1792110585 > dist 1792110141）。

2. `tests.e2e_ui.{test_ui_a9.TestA9FocusRingClosed, TestA9NoAbnormalWrapping,
   TestA9PreviewGeometry, TestA9ReverseComparisonWithPdf, test_ui_structure.TestA3Keyboard}`
   —— 5 条，这些套件在 `setUpClass` 里**自己触发构建**，而构建当场失败：

   ```
   File "…\backend\tests\e2e_ui\test_ui_structure.py", line 74, in ensure_dist_fresh
       raise RuntimeError(f"前端构建失败：{result.stderr[-500:]}")
   RuntimeError: 前端构建失败：$ tsc -b && vite build
   [ELIFECYCLE] Command failed with exit code 2.
   ```

   → `frontend/**` 工作树处于**改到一半**的状态（`tsc -b` 不过），与后端判据无关。

**→ 诚实结论：task-47 的两条交付路径都绿**（契约域 104/104；新增 15/15 在本域内通过），
**但「全量后端门禁」此刻不是绿的，且原因在别人的写域里**（`frontend/**` 改到一半 + dist 未重建）。
上一次全量绿（1111 条，见 `tests/e2e_m5/REPORT-m5.md` §5.2）发生在这些前端改动之前。
我不在 `frontend/**` 写域，**没有**替别人跑构建（构建会写 `frontend/dist/**`）；
这条由 Lead 决定何时重建并在干净窗口里重跑权威验收。

---

## 5. 历史实证（同源对比）—— 与派单预期的差异，如实报告

脚本：`backend/tests/contract/artifacts/task47_historical_check.py`
（**只手动跑一次，不做成常驻测试**：常驻就要读 `git show`，浅克隆 / 导出源码包会让它变红或变哑）。
日志：`backend/tests/contract/artifacts/task47-historical-1202e15.log`。

### 5.1 参照物选「同一提交」，不是「历史文档 ↔ 当前代码」

拿 M4 的文档去比**当前**代码只能说明「文档旧了」（废话）；要证明「当年漏没漏」，
两侧必须都在 `1202e15` 上。脚本因此取四个对象：`1202e15:docs/04-接口契约.md`、
`1202e15:frontend/src/App.tsx`、`1202e15:frontend/src/student/App.tsx`、
`1202e15:frontend/src/student/main.tsx`（读 basename）。

### 5.2 实测结果

```
== 历史同源对比：1202e15 的 §7 ↔ 1202e15 的两个 App.tsx ==
  §7 表行 = 8 条：['/student', '/student/practice', '/student/result', '/teacher',
                  '/teacher/library', '/teacher/paper/:id', '/teacher/paper/:id/attempts',
                  '/teacher/paper/:id/publish']
  代码页面路由 = 8 条：['/student', '/student/paper/:id', '/student/practice', '/student/result',
                       '/teacher', '/teacher/library', '/teacher/paper/:id',
                       '/teacher/paper/:id/publish']
  判据输出 **2 条问题**：
    - 代码有、文档没有：`/student/paper/:id`（学生端 frontend/src/student/App.tsx:22）
    - 文档有、代码没有：`/teacher/paper/:id/attempts`（docs/04-接口契约.md:394，老师）

== 当前工作区对比（判据作为门禁现场跑） ==
  §7 表行 = 11 条 / 代码页面路由 = 11 条 / 判据输出：一致（0 条问题）
```

即：**M4 收口时两侧差 2 条**，其中

* `/student/paper/:code`（代码有、文档没有）—— 正是派单点名的「从 M4 起就漏记、一直没人发现」；
* `/teacher/paper/:id/attempts`（**文档有、代码没有**）—— **反向漂移**，此前没有人报过。
  该提交的老师端 `App.tsx` 只有 4 条页面路由（`/teacher` index、`paper/:id`、
  `paper/:id/publish`、`library`）+ 兜底 `*`，而 §7 已经写了「某套卷的成绩列表」这一页 ——
  **文档承诺了一个当时还不存在的页面**，而兜底页的 `fatal-error` 文案里也把
  `/teacher/paper/:id/attempts` 排除了。

### 5.3 「当时没有任何东西会因此变红」的直证

```
> git ls-tree -r --name-only 1202e15 -- backend/tests/contract
backend/tests/contract/REPORT-task-37.md
backend/tests/contract/__init__.py
backend/tests/contract/test_contract_types.py
backend/tests/contract/test_contracts_package.py
backend/tests/contract/test_doc_schema_reconciliation.py
backend/tests/contract/test_layering.py
backend/tests/contract/test_layout_vars_consumed.py
backend/tests/contract/test_write_domains.py

> 逐个 git show 后 grep '04-接口契约|前端路由|Route path|App\.tsx'
命中 3 处，全在 docstring 且引的是 §4 / §5.1 / §8 —— 没有任何一条读 §7
```

### 5.4 与派单预期的差异（不调判据）

派单写的是「`git show 1202e15:docs/04-接口契约.md` 应当红出**那两条**」
（= M5 新增的两条老师端路由）。实测：

* **同源对比下那两条不会红** —— `1202e15` 的代码里还没有 `/teacher/attempts` 与
  `/teacher/attempts/:attemptId`（它们是 task-40/M5 才加的），拿 M4 的文档比 M4 的代码
  谈不上漂移；
* 只有把 M4 的文档喂给**当前**代码才会红出它们，而那只是在陈述「文档旧了」；
* 同源对比实际红出的 2 条见 §5.2 —— 其中一条正是派单自己点名的那个更早的漏记。

→ **按要求如实报告，没有为了让「那两条」出现而改判据、也没有放宽口径。**

---

## 6. 改动清单

| 文件 | 改动 |
| --- | --- |
| `backend/tests/contract/test_route_table_reconciliation.py` | **新增**（514 行，15 条判据） |
| `backend/tests/contract/artifacts/task47_historical_check.py` | **新增**（历史实证脚本，手动跑） |
| `backend/tests/contract/artifacts/task47-historical-1202e15.log` | 历史实证原始输出 |
| `backend/tests/contract/artifacts/task47-contract-domain{,-domain2,-domain3}.log` | 契约域原始输出 |
| `backend/tests/contract/artifacts/task47-run{1,2}.log` | 判据自身修复过程 |
| `docs/04-接口契约.md:25` | 汇总行第 7 条改「M5 已订正；**task-47 起有机器判据**」 |
| `docs/04-接口契约.md:402-416` | §7 表下说明：删掉「这张表目前没有机器判据」，改为写清判据、双向、下限、边界，并补历史同源实证的 2 条发现 |
| `docs/06-测试方案.md:1092-1170`（§9.12，新增，插在 §9.11 与 `## 10. 性能测试（NFR-4）`(`:1171`) 之间） | 判据的为什么存在 / 读什么 / 历史实证（含与派单预期的差异）/ 命令与实测 / 负对照 / 已知边界 |
| **未动** | `backend/app/**`、`frontend/**`（零改动） |

---

## 7. 它不能发现什么（诚实边界）

* **两边同时漏同一条**（文档和 `App.tsx` 一起少一条路由）它看不见 —— 它比的是「两侧一致」，
  不是「路由齐不齐」；下限只能挡整体缩水，**不是覆盖率证明**。
* 只比**路径结构**：不比 `element` 绑了哪个组件、不比嵌套怎么写的（`index` 还是 `path=""`）、
  不比参数名、不比 `Route` 的书写顺序、不覆盖 `docs/04` 其它章节。
* 只认「§7 用表格 + 内联代码」与「`App.tsx` 用 `<Route path="字面量">`」两个形态；
  形态变了会**大声红**（不会静默变绿），但别处再注册路由（`createBrowserRouter`、
  手写 `navigate()`）它看不到。
* 判据本体**不读 git 历史**；历史实证是一次性脚本，浅克隆下不适用，也不进常驻套件。
* 它证明的是「文档与代码两侧一致」，**不证明路由本身设计得对**（多一条少一条是否该有，
  仍是人的判断）。

---

## 8. 复核命令

```
# 1) 判据本体（含 15 条新增）
python -m unittest discover -s backend/tests/contract -t backend -v

# 2) 全量后端门禁（104 条契约判据在其中）
python -m unittest discover -s backend/tests -t backend

# 3) 历史同源实证（只跑一次，不入套件）
python backend/tests/contract/artifacts/task47_historical_check.py
```

---

## 9. 要上报 Lead 的事实

1. **历史实证的实际结果与派单预期不同**（§5.4）：同源对比红出 2 条，**不是**「那两条老师端路由」；
   其中 `/teacher/paper/:id/attempts` 是**反向漂移**（M4 文档写了、M4 代码没有），此前没人报过 ——
   按约定如实报告，未调判据。
2. **窗口内另有写者，且此刻挡住的是全量门禁**（§4.3–§4.5）：
   * `backend/app/export/pdf.py` 在我跑契约域时处于半写状态（`IndentationError: unexpected indent`，
     几秒后即恢复），全量门禁那次因此报 `Ran 566 / FAILED (errors=31)`（31 条全是 loader 错，
     **收集阶段就中止，后面 500 多条根本没跑** —— 记数不能只看 `Ran N`）；
   * 树恢复后复跑：`Ran 1119 / FAILED (errors=6)`，6 条全是 `setUpClass`，
     根因是 `frontend/**` 改到一半：`frontend/dist` 旧于 `frontend/src`
     （src=1792110585 > dist=1792110141，task-42 的 M5 UI 守卫按其设计拒跑）
     **并且** `tsc -b && vite build` 当场失败（exit 2，`e2e_ui` 的 5 条自建构建套件）。
   * 我留在 `frontend/**` 写域之外，**没有**替别人跑构建（构建会写 `frontend/dist/**`）——
     重建与干净窗口里的权威验收由 Lead 定。
3. `docs/05-任务板.md:381` 的 T-M6-07 行与本次交付对应（我未改 `docs/05`，不在写域）。
