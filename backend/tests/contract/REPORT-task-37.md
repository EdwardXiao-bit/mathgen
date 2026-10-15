# task-37 / T-M4-17：`docs/02` §5.2 建表语句 ↔ `models.py` **逐列 + 逐索引对账门禁**

* 判据文件：`backend/tests/contract/test_doc_schema_reconciliation.py`（新增，26 条用例）
* 证据目录：`backend/tests/contract/artifacts/`
* 报告日期：2026-10-15（验收窗口，Asia/Shanghai）
* 写域：`backend/tests/contract/**` + `docs/06-测试方案.md`（登记见该文件 §9.9）
* 只读未动：`backend/app/models.py`、`backend/app/contracts/**`、`docs/02-技术设计.md`

---

## 0. 结论（一屏）

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 新增门禁 | 26 条（6 门禁本体 + 1 两种列写法 + 3 索引同一层级 + 13 负对照 + 3 正对照） | §5.1 |
| 契约域整包 | `Ran 79 tests in 0.086s / OK / exit 0`（本目录 53 → 79） | `artifacts/task37-contract-suite.log` |
| **现在就实测的真实对账** | 6 张表 / 40 列 / **4 个索引** 两侧全等（双向差集为空、可空性无差异、索引四项属性无差异）→ **绿** | §3、§5.2 |
| 这个「绿」为什么可信 | ① 列的两处已被 D79 补过（`archived_at` 219 行、`attempt_uuid` 254 行），索引的两条已被 Lead 补进 §5.2（277–278 行），首跑绿是**预期**；② 判据自证不靠绿，靠 13 条负对照 + 历史真文档实证 | §5.3、§5.4、§6 |
| 负对照 | 13 条，全部红且**点名表/列/索引**；制造失败条件的改写本身带断言（改不到就报错，防「空转的负对照」） | §5.3、§6 |
| 历史真文档实证 | 用 D79 补文档**之前**的 `git b794ccd:docs/02-技术设计.md` → **一次红出 6 条、三类遗漏**：`paper.archived_at`、`attempt.attempt_uuid`、`idx_paper_status`、`idx_edit_log_paper`（另加两条下限） | `artifacts/task37-historical-doc-check.log` |
| 索引发现（已闭环） | `models.py` 4 个索引 vs §5.2 只有 2 条 `CREATE INDEX` → 上报 Lead → **Lead 补文档**（277–278 行，不改模型）→ **纳入本判据** | §9 |
| 冻结契约 | `backend/app/**` 一字未改；未加白名单、未放宽判据、无 skip | §7 |

---

## 1. 任务与由来

D78 给 `paper` 加了 `archived_at`、D76 给 `attempt` 加了 `attempt_uuid`，两列**都漏写在
`docs/02-技术设计.md` 的 `CREATE TABLE` 里**，直到 D79 人工审计才发现（`attempt.attempt_uuid`
是 M4 期间就漏的）。根因与 V13 同族：**靠人记得同步的约定迟早漂移，而且这种漂移是静默的** ——
没有任何东西会因为文档少一列而变红。D79 立了「功能增删必须同步的文档清单」（`docs/03` §6.1），
本任务把清单里**唯一能机器验的一条**做成门禁。

第一版按任务原话只做列；同一次审计顺手发现 `models.py` 有 4 个显式索引而 §5.2 只写了 2 条
`CREATE INDEX`，作为发现上报。Lead 核对后**批准扩面把索引也纳入对账**（并亲自把缺的 2 条
`CREATE INDEX` 补进 `docs/02`，**补文档、不改模型**），同时点名三个容易「看起来对而实际错」的坑：
两侧必须同一层级、`CREATE INDEX` 与 `CREATE TABLE` 严格分开、下限同样施加。本报告即扩面后的终态。

## 2. `docs/02` §5.2 的真实形态（写解析器前先看清）

Lead 提醒「别照着我卡里的 CREATE TABLE 猜格式」——照做后的实测形态：

* `## 5. 数据模型` 在 191 行，`### 5.1 实体关系` 193 行，`### 5.2 表结构` 204 行。
* 建表语句**不是表格、不是缩进块**，而是 **206–279 行的单个 ```sql 围栏**，内含 **6 条标准
  `CREATE TABLE <表> (\n … \n);`**，每条以 `);` 收尾，**另有 4 条 `CREATE INDEX`**（275–278 行）。
* 列定义行带**行尾 `--` 中文注释**（如 `archived_at   TEXT                   -- D78 …`），
  表级约束是**独立行**：`UNIQUE(paper_id, seq)`（229）、`UNIQUE(name, student_no)`（247）、
  `UNIQUE(student_id, attempt_uuid)`（261）、`UNIQUE(attempt_id, seq)`（272）。
* 4 条索引：`idx_attempt_paper`(275) `ON attempt(paper_id)`、`idx_attempt_student`(276)
  `ON attempt(student_id)`、`idx_paper_status`(277) `ON paper(status)`、
  `idx_edit_log_paper`(278) `ON edit_log(paper_id)` —— **都在建表语句之外、同一个围栏里**。
* 列数实测：`paper` 11、`attempt` 9、`attempt_item` 6、`edit_log` 5、`paper_item` 5、`student` 4 = **40**。

解析规则据此写成：取 ```sql 围栏 → **先摘掉所有 `CREATE INDEX` 行** → 按
`CREATE\s+TABLE\s+(\w+)\s*\(` 切分 → 表体截到 `);`（并先切掉后续 `CREATE …` 行）→
逐行去 `--` 注释与行尾逗号 → 跳过表级约束行 → 行首标识符为列名、该行含 `NOT NULL`
或 `PRIMARY KEY` 即必填。索引行另走一条正则解析。

## 3. 判据怎么读两侧

### 3.1 列

* **文档侧**：上面的解析器。**无法解析的非空行会被收集起来并报红**（形态变了 → 大声红），
  而不是「少解析出几列」静默变绿。
* **模型侧**：`app.models.Base.metadata` 的 `Table.columns`，**不读源码正则** ——
  `models.py` 全部写作 `Mapped[...] = mapped_column(...)`，正则漏一种写法就会假绿（Lead 点名）。
  两条旁证：
  1. 把同一 metadata `create_all` 到内存 SQLite 后 `inspect().get_columns()` 读回来，必须与 ORM 侧逐列一致
     （证明读到的不是「永远不落地的符号」）；
  2. 合成一张 `Column(...)` 写法的表，断言它同样被读到 —— **两种写法都覆盖**作为一条断言验出来。
* **比对**：表的有无；列名的**双向差集**并点名方向（「文档有、模型没有」/「模型有、文档没有」）；
  两边都有但可空性不同。

### 3.2 索引（扩面时 Lead 指出的第一个坑：两侧必须同一层级）

| 维度 | 文档侧 | 模型侧 | 为什么不混别的 |
| --- | --- | --- | --- |
| 索引来源 | 只认 `CREATE INDEX [UNIQUE] <名> ON <表>(<列>)` 行 | 只取 `Table.indexes`（显式 `Index(...)`） | 表级 `UNIQUE(...)`／列 `unique=True` 在模型侧落在 **`UniqueConstraint`**，不在 `Table.indexes`；`paper.code` 的 `unique=True` 就是实例。混进来会让判据在**正确的**文档上变红，然后人就会想去加白名单 —— 最坏方向 |
| 比什么 | 名字 + 表 + 列 + 是否 `UNIQUE` | 同名四项 | 两边都有这四个属性；比 `name=` 之外的构造细节会制造假红 |

* 实测旁证：`paper.indexes = {idx_paper_status}`，`paper.constraints` 含
  `PrimaryKeyConstraint` + `UniqueConstraint`（`code unique=True` 在这里）；`student.indexes` 为空
  而 `student.constraints` 含 `UniqueConstraint:uq_student_name_no`。
* **结构上也不混**：`_split_fence_body()` 在解析表之前就把 `CREATE INDEX` 行摘出去，
  另有一条独立断言保证「索引名绝不出现在任何表的列集合里」，**不靠切表体的实现细节兜底**；
  还有一条对抗形态用例（索引行紧贴最后一张表、索引名像列名）专门压这条断言。
* 同族形态（写坏、漏解析）走 `broken` 清单 → 报红并**贴出该行原文**。

### 3.3 下限与入口

* **下限**（防「空集对空集」全绿，V9）：表 ≥ 6、列 ≥ 40、**索引 ≥ 4**，**文档侧与模型侧都施加**；
  另加「某表解析出 0 列」也报红。
* **入口**：`assert_reconciled(text, *, min_tables, min_columns, min_indexes, model=None,
  indexes=None, source=...)` 抛 `AssertionError`，消息是多行可读清单。

## 4. 改动清单

| 文件 | 动作 |
| --- | --- |
| `backend/tests/contract/test_doc_schema_reconciliation.py` | **新增**（判据本体 26 条，含索引对账） |
| `backend/tests/contract/artifacts/task37-contract-suite.log` | 新增（契约域整包原始输出） |
| `backend/tests/contract/artifacts/task37-negative-controls.log` | 新增（13 条负对照红出来的原文） |
| `backend/tests/contract/artifacts/task37-historical-doc-check.log` | 新增（历史真文档实证原文） |
| `docs/06-测试方案.md` | 新增 **§9.9** 登记该判据（唯一跨域一笔，属 Lead 指派写域） |

未改：`backend/app/models.py`、`backend/app/contracts/**`、`docs/02-技术设计.md`、`backend/tests/contract/`
其余既有文件、`test_layering.py` 的 5 个冻结 sha256。

## 5. 验证命令与原始输出

### 5.1 契约域整包

```
python -m unittest discover -s backend/tests/contract -t backend -v
```

→ `Ran 79 tests in 0.086s / OK`，`exit 0`（新增 26 条：本目录 53 → 79）。
全量后端门禁 `discover -s backend/tests -t backend` 会**自动带上**本判据，无需显式调用。

### 5.2 真实对账读数

```
表 = ['attempt','attempt_item','edit_log','paper','paper_item','student']
每表列数 = {'attempt':9,'attempt_item':6,'edit_log':5,'paper':11,'paper_item':5,'student':4}   合计 40
未解析行 = []            坏索引行 = []
文档侧索引 = {'idx_attempt_paper':('attempt',('paper_id',),False),
              'idx_attempt_student':('attempt',('student_id',),False),
              'idx_paper_status':('paper',('status',),False),
              'idx_edit_log_paper':('edit_log',('paper_id',),False)}
模型侧（Table.indexes）= 同上四项，逐项相等
compare_schemas(文档, 模型) = []      compare_indexes(文档, 模型) = []
```

### 5.3 负对照（红的原文，节选；全文见日志）

```
删 paper.archived_at 行 →
  文档侧只解析出 39 列（下限 40）—— 下限用来挡住空集对空集的全绿
  paper.archived_at：**模型有、文档没有**（文档漏更新，D78/D76 同款）

删 attempt.attempt_uuid 行 →
  attempt.attempt_uuid：**模型有、文档没有**（文档漏更新，D78/D76 同款）

给 student 插一列 ghost_column →
  student.ghost_column：**文档有、模型没有**（文档漏删，或代码少建一列）

把 paper.title 的 NOT NULL 抹掉 →
  paper.title：文档写 可空，模型是 NOT NULL

空文本 / 只有标题 / 围栏里没有 CREATE TABLE →
  文档侧只解析出 0 张表（下限 6）… 文档侧只解析出 0 列（下限 40）…

只留一张表的截断文档 →
  文档侧只解析出 1 张表（下限 6）… 2 列（下限 40）…

把 attempt_item 一列换成 `*** 这里本该是列定义 ***` →
  文档里有无法解析成列定义的非空行：attempt_item: *** 这里本该是列定义 ***

删 idx_paper_status 行 →
  文档侧只解析出 3 个索引（下限 4）—— 下限用来挡住空集对空集的全绿
  索引 idx_paper_status：**模型有、文档没有**（模型是 `paper(status)`）

凭空多一条 idx_ghost →
  索引 idx_ghost：**文档有、模型没有**（文档写了 `paper(status)`，但 `app/models.py` 里没有这个索引）

把 idx_edit_log_paper 改指 paper(status) →
  索引 idx_edit_log_paper：文档写 `paper(status)`，模型是 `edit_log(paper_id)`

删光所有索引行 →
  文档侧只解析出 0 个索引（下限 4）… + 四条索引逐条「模型有、文档没有」

把索引行写坏（CREATE INDEX idx_attempt_student breakdown;）→
  文档里有无法解析成 `CREATE INDEX <名> ON <表>(<列>)` 的索引行：
      CREATE INDEX idx_attempt_student breakdown;
```

### 5.4 历史真文档实证（关键：证明它能抓住这次的遗漏）

用 D79 补文档**之前**的版本（`git b794ccd:docs/02-技术设计.md`）跑同一条门禁：

```
文档侧解析：6 张表 / 38 列 / 2 个索引 / 未解析行 0 / 坏索引行 0
下限：MIN_TABLES=6 MIN_COLUMNS=40 MIN_INDEXES=4

assert_reconciled → 抛 AssertionError（6 条）：
  - 文档侧只解析出 38 列（下限 40）—— 下限用来挡住空集对空集的全绿
  - 文档侧只解析出 2 个索引（下限 4）—— 下限用来挡住空集对空集的全绿
  - attempt.attempt_uuid：**模型有、文档没有**（文档漏更新，D78/D76 同款）
  - paper.archived_at：**模型有、文档没有**（文档漏更新，D78/D76 同款）
  - 索引 idx_edit_log_paper：**模型有、文档没有**（模型是 `edit_log(paper_id)`）
  - 索引 idx_paper_status：**模型有、文档没有**（模型是 `paper(status)`）
```

即：**同一条判据一次抓住三类遗漏**（D76 的列、D78 的列、索引漏写文档），
这条判据**本可以**替代 D79 那次人工审计。该检查按 git 版本手动跑，
**不做成常驻测试**（不依赖 git 历史深度的假设）。

## 6. 负对照逐条

| # | 制造的失败条件 | 期望 | 实测 |
| --- | --- | --- | --- |
| 1 | 删 `paper.archived_at` 列行 | 红 + 点名 `paper.archived_at` | ✅（并附带列数下限红） |
| 2 | 删 `attempt.attempt_uuid` 列行 | 红 + 点名 `attempt.attempt_uuid` | ✅ |
| 3 | `student` 多一列 `ghost_column` | 红 + 方向「文档有、模型没有」 | ✅ |
| 4 | `paper.title` 去掉 `NOT NULL` | 红 + 点名可空性不一致 | ✅ |
| 5 | 空文本 / 只有标题 / 无 `CREATE TABLE` | 红 + 表数/列数下限 | ✅（3 种输入） |
| 6 | 只留一张表的截断文档 | 红 + 表数下限 | ✅ |
| 7 | 某列行改成不可解析的乱码 | 红 + 报出该行原文 | ✅ |
| 8 | 文档不存在 / 指向没有 `CREATE TABLE` 的文件 | 大声红（不是 skip） | ✅（两种路径） |
| 9 | 删 `idx_paper_status` 索引行 | 红 + 点名 + 索引下限 | ✅ |
| 10 | 凭空加 `idx_ghost` | 红 + 方向「文档有、模型没有」 | ✅ |
| 11 | `idx_edit_log_paper` 改指 `paper(status)` | 红 + 同时写出文档侧与模型侧 | ✅ |
| 12 | 删光所有索引行 | 红 + 索引下限 | ✅ |
| 13 | 索引行写坏（缺 `ON <表>(<列>)`） | 红 + 报出该行原文 | ✅ |

正对照 3 条（防「恒红」）：原文必须通过；完全相同的合成两侧 → `compare_schemas` / `compare_indexes`
返回 `[]`；另一条「索引两侧同一层级」用例证明 `UNIQUE(...)` 与 `unique=True` 都不会被算成索引。
另：所有负对照的改写函数自带断言（**没真的改到就报错**），避免「负对照空转却因为别的原因红了」。

## 7. 冻结契约与写域

* `backend/app/**` **一字未动**（`models.py` 只被 import 读取，未写）。
* `docs/02-技术设计.md` 未动（缺列/缺索引一律报告，不自行补文档）—— 索引那 2 条由 **Lead** 补。
* 未加任何白名单、未放宽判据、无 `skipTest`、无降级。
* 判据只读 `docs/02` + `app.models`，未触碰 `app/contracts/**`、未改 `test_layering.py` 的冻结哈希。

## 8. 方法边界（做不到什么）

* **两边同时错**（文档与模型一起漏掉同一列/同一索引）它看不见；`MIN_*` 只挡「整体缩水」，
  不是覆盖率证明。
* 列只比**列名 + 可空性**；索引只比**名字 + 所在表 + 列 + 是否 `UNIQUE`**。
  类型细节（`TEXT` vs `String(40)`）、默认值、外键 `ondelete`、唯一约束名（文档写无名的
  `UNIQUE(…)`，模型是 `uq_*`）、索引的 `WHERE` 部分一律不比。
* 依赖「每张表一条 `CREATE TABLE … );`、每条索引一行 `CREATE INDEX …;`」的形态；
  形态变了会大声红（未解析行/坏索引行/下限），需要人看一眼再改解析器 —— 这是**有意的**，
  不是静默兼容。
* `EXPECTED_TABLES` / `EXPECTED_INDEXES` 是冻结清单：合法新增表或索引要同时改文档与本文件。
* 「历史真文档实证」按 git 版本手动跑，不是常驻测试 —— 常驻部分不依赖 git 历史深度。

## 9. 索引差异：发现 → Lead 补文档 → 已纳入判据（闭环）

第一版（只对列）发现的不一致，如实上报 Lead 后的闭环过程：

| 索引 | models.py | docs/02 §5.2（发现时） | 现在 |
| --- | --- | --- | --- |
| `idx_attempt_paper` | 133 行 | 275 行 ✅ | 275 行 ✅ 纳入判据 |
| `idx_attempt_student` | 134 行 | 276 行 ✅ | 276 行 ✅ 纳入判据 |
| `idx_paper_status` | 65 行 | **缺** | **Lead 补 277 行** ✅ 纳入判据 |
| `idx_edit_log_paper` | 96 行 | **缺** | **Lead 补 278 行** ✅ 纳入判据 |

* **Lead 补文档、不改模型**（与我的上报结论一致）；本任务未写 `docs/02`。
* 扩面后两侧都是 4 条，`compare_indexes = []`；历史版本（2 条）会红出索引两条（§5.4）。
* 未加白名单、未放宽任何判据；索引下限（≥4）与三条索引负对照随判据进仓库。

## 10. 复跑命令

```powershell
cd mathgen
$VPY = "backend\.venv\Scripts\python.exe"
& $VPY -m unittest discover -s backend/tests/contract -t backend -v     # 契约域
& $VPY -m unittest discover -s backend/tests -t backend                 # 全量后端门禁
```

历史实证（手动，需要 git 里的 `b794ccd`）：
`git show b794ccd:docs/02-技术设计.md` 之后用 `assert_reconciled` 跑一遍，
参照 `artifacts/task37-historical-doc-check.log` 的原文。

## 11. 写盘清单（自报，供对账）

* 新增：`backend/tests/contract/test_doc_schema_reconciliation.py`（26 条）
* 新增（证据）：`backend/tests/contract/artifacts/task37-{contract-suite,negative-controls,historical-doc-check}.log`
* 修改：`docs/06-测试方案.md`（§9.9，位于 882 行起；文件 **1023 行 / 69183 字节**，纯 LF（CR=0）、
  UTF-8 无 BOM）—— 跨域一笔，属 Lead 指派写域
* 临时文件已删：`.tmp-old-doc.md`、`.tmp-task37-historical-check.py`、`.tmp-task37-controls.py`、
  `.tmp-task37-evidence.py`
