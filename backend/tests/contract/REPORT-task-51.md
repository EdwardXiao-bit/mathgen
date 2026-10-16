# task-51（T-M6-08）报告：把「纯 LF / 无 BOM」从声明变成机器判据

> 判据：`backend/tests/contract/test_text_hygiene.py`（17 条）+ 支撑 `backend/tests/contract/_text_hygiene_support.py`
> 证据：`backend/tests/contract/artifacts/task51-{repo-scan,evidence}.json`、`task51-contract.log`
> 写域：`backend/tests/contract/**` + `docs/06-测试方案.md`（本任务**只读扫描**，没有为了让判据变绿去改任何既有文件）

## 0. 一屏结论

| 问题 | 答案 |
| --- | --- |
| 判据能红吗？ | 能。脏树红并**点名文件 + 给 CR 数 / 是否 BOM**；净树绿；空的/不存在的扫描根也红（`task51-evidence.json`） |
| 范围是什么？ | `backend/app/**`、`backend/tests/**`、`frontend/src/**`、`frontend/scripts/**`、`docs/**`（代码里写死，见 §4） |
| 扫了多少？ | 真仓库 **271** 个候选文本文件，`cr_files=0`、`bom_files=0`、`binary_skipped=0`、`undecodable=0`、`missing_roots=[]`（`task51-repo-scan.json`） |
| 现在红吗？ | 不红。**但它的红有分辨力**：把本判据自己的支撑文件改成 CRLF（render-export 那次的形态）立刻点名，还原后字节数/哈希回到原值（§6） |
| 扫出真实问题了吗？ | 本仓库维护的源文件里**没有**。唯一在树内的 CRLF 是 vendored 的 `backend/tests/e2e_m2/.pylibs/**`（第三方 wheel，按排除项跳过，§4） |
| 运行 | `& $VPY -m unittest discover -s backend/tests/contract -t backend -p test_text_hygiene.py -v` → `Ran 17 tests in 0.724s` / `OK` / exit 0 |

## 1. 由来：那句话一直是声明

2026-10-16 M6 期间，render-export 把 `backend/tests/render/pdfprobe.py` 写成了 **CRLF**
（`CR=190 / LF=190`，7037 B）。当时只有 `git status` 给了个警告：

```
warning: in the working copy of 'backend/tests/render/pdfprobe.py', CRLF will be replaced by LF the next time Git touches it
```

**没有任何判据因此变红**，而仓库里 12+ 份报告的证据段都写着「纯 LF / 无 BOM」。
同一次事件里手工抽查还发现：`test_layout_proof.py` / `REPORT-layout-proof.md` /
`pagination.py` / `pdf.py` 的 `CR=0`，只有那一个文件是 CRLF ——
也就是说「按文件手工抽查」不会自动发现漂移，一个字节级的事实被反复写进证据段却从未被检查。

同类但角度不同的教训（V9/V13/V14 管「判据有没有分辨力」）：
这一条管**「报告里写下的量测结论有没有机器判据撑着」**。

## 2. 规则来自仓库自己，不是口味

`.gitattributes:2`：

```
* text=auto eol=lf
```

同一目录还实测了一个容易误判的事实：**`git config core.autocrlf = true`**。
`eol=lf` 在 `.gitattributes` 里，它对**被跟踪的文本文件**生效，于是入库/检出会归一化成 LF；
但**工作区里的一次普通编辑器保存可以重新引入 CRLF**（pdfprobe.py 就是这么来的），
而这正是「`git status` 警告 ≠ 判据变红」的缺口。判据检查的是**工作区的字节**，不是 git 的归一化结果。

复核命令：

```
git config --get core.autocrlf        # true（本机）
Select-String -Path .gitattributes -Pattern 'eol=lf'
```

## 3. 判据本体

`backend/tests/contract/_text_hygiene_support.py`

| 位置 | 作用 |
| --- | --- |
| `MIN_FILES = 220`（`:32`） | 文件数下限：走到更少（尤其 0）必须红（V11） |
| `INCLUDE_ROOTS`（`:36`） | 覆盖范围，写死在代码里 |
| `TEXT_SUFFIXES`（`:45`） | 只查这些后缀；二进制/派生产物靠后缀 + 内容 NUL 双保险 |
| `EXCLUDED_DIRS`（`:54`） | 目录名 → **排除理由**（每条都写下来，见 §4） |
| `inspect_bytes(data)`（`:96`） | 字节级事实：CR 次数 / LF 次数 / 是否 `EF BB BF` 开头 / 是否像二进制 |
| `text_hygiene_problems(root, *, min_files, include_roots)`（`:118`） | 返回 `(problems, observation)`；problems 每条**点名文件或点名失明原因** |

失败信息长这样（原文来自 `task51-evidence.json`）：

```
tree/dirty_crlf.py: CR 出现 2 次（LF 2）—— 不是纯 LF
tree/dirty_bom.py: 以 UTF-8 BOM 开头（EF BB BF）
扫描到 0 个文件：空集不许通过（include_roots=['tree']）
扫描根不存在或不是目录: ...\.tmp-task51-does-not-exist（解析器失明）
应在范围内的目录不存在，判据扫不到它: no_such_dir
只扫到 1 个文件，少于下限 5（范围被改小/被吞）
```

`observation` 是给证据用的：`files / cr_files / bom_files / binary_skipped / undecodable /
missing_roots / excluded_dir_reasons` ——「扫了 271 个文件都是干净的」这句话必须能自证。

## 4. 范围与排除项（逐条理由）

覆盖：`backend/app/**`、`backend/tests/**`、`frontend/src/**`、`frontend/scripts/**`、`docs/**`。

| 排除 | 理由 |
| --- | --- |
| `.git` | 版本库内部对象，不是源文件 |
| `.venv` / `venv` / `site-packages` | 安装进来的第三方包，不属于本仓库维护的源文件 |
| `node_modules` | 前端依赖树，第三方 |
| `dist` / `build` | 构建产物（如 `frontend/dist`） |
| `artifacts` | 测试证据/原始输出（json、日志、截图），不是源文件 |
| `__pycache__` / `.pytest_cache` / `.mypy_cache` / `.ruff_cache` | 工具缓存 |
| `.pylibs` | vendored 第三方 wheel：**pypdfium2 的源码本身就是 CRLF**，扫它只会制造噪音（实测 56 个文件、CRLF 一大片） |
| `.playwright` / `.idea` / `.vscode` | 工具缓存 / IDE 配置 |
| 隐藏目录（`.` 开头）、`tmp-*` | 工具缓存 / vendored / 一次性实验（`.tmp-*`） |
| 后缀不在白名单 | 二进制/派生产物（`.docx`、`.pdf`、`.png`…） |
| 内容前 8KB 有 NUL | 二进制兜底（后缀骗人时用） |

排除项不是空口清单：`TestExclusionsAreJustifiedAndLoadBearing` 断言
（a）每条排除都有非空理由；（b）扫描结果里**不出现** `node_modules`/`.venv`/`artifacts`/`.pylibs`/`dist`/`__pycache__`；
（c）`.pylibs/pypdfium2/_helpers/document.py` 在磁盘上**确实是 CRLF**、却**不在**扫描集合里 —— 排除项正在挡真东西。

## 5. 三态证据（`task51-evidence.json`，原始输出）

| 场景 | 构造 | 判据输出 |
| --- | --- | --- |
| 脏树 | `dirty_crlf.py`(CRLF) + `dirty_bom.py`(BOM) + `clean.py`(LF) | **2 条红**，点名两个脏文件；`clean.py` 没被点名 |
| 净树 | `clean.py` + 含中文的 `clean.md` | `[]`（绿），`files=2` |
| 空目录 | 只放一个 `.keep`（非白名单后缀） | **红**：「扫描到 0 个文件：空集不许通过」 |
| 不存在的根 | `<仓库>/.tmp-task51-does-not-exist` | **红**：「扫描根不存在或不是目录（解析器失明）」 |
| 范围内目录缺失 | `include_roots=("tree","no_such_dir")` | **红**：点名 `no_such_dir` |
| 少于下限 | 1 个干净文件、`min_files=5` | **红**：「只扫到 1 个文件，少于下限 5」 |
| BOM 与 CRLF 分离 | 只有 BOM 的文件 | **红 1 条**，且该条里没有「CR 出现」（两个独立事实不许合并） |
| 真仓库 | — | `files=271 cr_files=0 bom_files=0 binary_skipped=0 undecodable=0 missing_roots=[]` → `[]` |

## 6. 真文件注入 → 点名 → 还原（render-export 那次的形态）

`TestTheCriterionGoesRedOnARealFileThenRestoresIt` 每一跑都做一次端到端直证：

1. 取本判据自己的支撑文件 `backend/tests/contract/_text_hygiene_support.py`（写域内），前置断言 `CR=0`；
2. 把它的每个 `\n` 换成 `\r\n` 写回；
3. 对**真仓库**跑判据 → 该文件被点名，且 `observation["cr_files"]` 非空；
4. `finally` 还原原始字节；
5. 复核**字节数与 sha256 都回到原值**、`CR` 回到 0、判据重新变绿。

> 只碰自己的支撑文件，不碰 `pdfprobe.py`（那是 render-export 的写域）。
> 本任务**没有**为了判据变绿批量改写任何既有文件。

## 7. 能红的直证与判断力

- 脏树 / 净树**同一函数**给出相反结论（`TestTheCriterionCanGoRedOnADirtyTree`），不是两套代码。
- 三种「失明」互不替代：空目录、根不存在、范围内目录缺失（`TestParserBlindnessIsRed`）。
- 文件数下限两侧都有断言：`min_files=5` 时 1 个红、5 个绿（`TestNoEmptySetPasses`）。
- 二进制跳过会把集合变空，于是**仍然红**（证明「跳过」不等于「通过」）。
- 真仓库那一跑断言**真的读到了文件**：扫描集合里必须含本判据文件、支撑文件与 `docs/06-测试方案.md`。
- 规则与 `.gitattributes` 对齐（`TestTheRuleComesFromTheRepositoryPolicy`），并断言 `.gitattributes` 自己也是 LF。

## 8. 回归

- 本判据模块：`Ran 17 tests in 0.724s` / `OK` / exit 0。
- 整个 contract 域：`Ran 121 tests in 1.772s` / `OK` / exit 0（`task51-contract-final.log`）。
- 首跑（`task51-contract.log`）曾有 1 条 error，**不是本任务引入**：
  `tests.contract.test_contracts_package.TestAttemptRoutesAreAssembledInOrder.test_export_is_assembled_before_the_id_route`
  在 `backend/app/services/pdf_export.py` 抛
  `TypeError: nonempty __slots__ not supported for subtype of 'bytes'`（同伴 render-export 的 task-44 改动进行中；
  该文件现在写着 `class ExportedPdf(bytes)` + 一句「**不能**加 `__slots__`」的注释）。
  本判据不 import `app.*`，与该错误无关；同伴落地后重跑即全绿 —— 这也是「红要能归因到谁」的一次实例。

## 9. 这套设施**不能**发现什么

1. **行尾风格混用**：一个文件里既有 `CRLF` 又有 `LF`，只要 CR 存在就会红 —— 但报的是「有 CR」，不会告诉你哪几行是 CRLF。
2. **编码不是 UTF-8 但不带 BOM**（mojibake）：CR/BOM 是字节级事实，一个 GBK 文件只要行尾合法就会通过。判据只在 `observation["undecodable"]` 里**记一笔**，不做编码判定。
3. **尾随空白 / 制表符与空格混用 / 行末多空行**：不在本判据范围内。
4. **生成物的内部格式**：`docs/word/*.docx`、`*.pdf`、`*.png` 被排除（二进制），改坏了它也照样绿。
5. **「内容被改但行尾合法」**：判据只看字节里有没有 CR/BOM，内容正确性由别的判据负责。
6. **未跟踪/被排除目录里的漂移**：`artifacts/`、`.tmp-*`、`node_modules`、`.pylibs` 里的 CRLF 不报警（那些是排除项，理由见 §4）。
7. **大小写敏感的后缀漂移**：白名单按小写后缀匹配（`.py` 与 `.PY` 都能进），但不在白名单里的文本格式（如 `.env`、`.template`、无后缀的可执行脚本）不会被扫。

## 10. 复核命令与证据清单

```
cd C:\Users\Ed\dsh_workspace\mathgen
$VPY = backend\.venv\Scripts\python.exe          # 本仓库自带的 venv

# 1) 判据本体（17 条，含脏/净/失明/真文件注入+还原）
& $VPY -m unittest discover -s backend\tests\contract -t backend -p test_text_hygiene.py -v

# 2) 整个 contract 域
& $VPY -m unittest discover -s backend\tests\contract -t backend -v

# 3) 只读扫描真仓库并落盘（exit 0 = 没有 CR/BOM；exit 1 = 有，且会打印文件名）
& $VPY backend\tests\contract\artifacts\task51_scan_repo.py --json backend\tests\contract\artifacts\task51-repo-scan.json

# 4) 三态原始证据（脏/净/空/不存在 + 真仓库）
& $VPY backend\tests\contract\artifacts\task51_evidence.py --json backend\tests\contract\artifacts\task51-evidence.json

# 5) 现场复现 CRLF 漂移（不改真文件）
& $VPY backend\tests\contract\artifacts\task51_recon.py --json backend\tests\contract\artifacts\task51-recon.json
```

| 证据 | 内容 |
| --- | --- |
| `task51-repo-scan.json` | 真仓库那一跑：271 文件、0 CR、0 BOM、0 二进制跳过、0 非 UTF-8 |
| `task51-evidence.json` | 脏树/净树/空目录/不存在的根 + 真仓库的 problems 原文 |
| `task51-recon.json` | 全仓候选文件里 CR/BOM 的分布（唯一命中的是 `.pylibs` 里的 vendored 文件） |
| `task51-contract.log` | contract 域 121 条的完整输出（含那条同伴红） |
| `task51_recon.py` / `task51_scan_repo.py` / `task51_evidence.py` | 三个只读取证脚本 |

## 11. 上报事实

1. **本任务没有修任何文件**（只读扫描）。真仓库当前**是干净的**：`cr_files=0 / bom_files=0`（271 个文件）。
2. `pdfprobe.py` 现在已经不是 CRLF（`CR=0`）—— render-export 后来重存过；本判据此后会一直盯着它。
3. 树内确实存在 CRLF 文件，但全部在 vendored 的 `backend/tests/e2e_m2/.pylibs/**`（第三方 wheel），按排除项跳过并在测试里留了旁证。
4. 本机 `git config core.autocrlf=true`，但 `.gitattributes` 的 `* text=auto eol=lf` 才是仓库策略；工作区一次普通保存就能引入 CRLF —— 这是本判据存在的具体理由。
5. contract 域最终 **121 条全绿**（`Ran 121 tests in 1.772s` / `OK`）。首跑那 1 条 error 来自同伴进行中的
   `backend/app/services/pdf_export.py`（`class ExportedPdf(bytes)` 与 `__slots__` 冲突），同伴落地后自行消失，
   与本判据无关（本判据不 import `app.*`）。
