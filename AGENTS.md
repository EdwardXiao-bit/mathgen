# mathgen —— 小学四则运算练习题生成与批改系统

面向**小学老师**的「出题 + 批改」工具：**老师端**配置题目、生成、打印、发卷、看成绩；**学生端**局域网扫码、自主练习、自动批改。
部署形态：跑在老师本机 / 教室内网机器，**不依赖公网**（D10）。

当前阶段：**需求已冻结 → 技术设计 v0.4 → M1–M6 全部完成并验收 → M7 课程《测试文档》已产出 → 正在做第一版发布（`v1.0.0-rc1`）**；系统本体功能已完备，不依赖公网。

## 先读什么

| 你要做的事 | 阅读顺序 |
| --- | --- |
| 第一次接触项目 | **`docs/PROJECT.md`** → `docs/00-决策记录.md` |
| 动手写代码 | **本文「红线」** → `docs/04-接口契约.md` → 你的任务卡（`docs/05-任务板.md`） |
| 与其他智能体并行 | **`docs/03-协作规范.md`** → `docs/04-接口契约.md` |
| 改需求 / 设计 | `docs/00-决策记录.md` §二 待决问题 → 改完同步 `01`/`02` 并**重新生成 Word** |
| 写测试 / 验收 | `docs/06-测试方案.md` |

**不要**在没有读 `docs/04-接口契约.md` 的情况下开始写接口相关代码——冻结项改错要返工。

## 当前状态

| 阶段 | 状态 |
| --- | --- |
| 需求讨论 | **已结束**（11 轮，**Q1–Q31 全部关闭**） |
| 需求分析文档 | **v1.2 已冻结**（41 功能需求 + 9 非功能需求） |
| 技术设计文档 | **v0.4 讨论稿**（覆盖矩阵 50/50 自检通过），DQ5 / DQ8 已关闭（D75 / D76），DQ6 / DQ7 待决 |
| 协作体系 | **已建立**（`docs/03`–`07` + `PROJECT.md`） |
| 课程提交 Word | **三份均已产出**：需求分析、技术设计、测试文档（M7，源 `docs/08-测试文档.md`） |
| 配图 | **13 张 PNG**，见 `docs/word/figures/` |
| 代码 | **M1–M6 全部已验收**（引擎 / 判分 / API / 卷面渲染导出 / 老师端 / 学生端 / 卷库 / 成绩 / 导出 / 单一服务托管），后端全量 **1254 用例 OK**、前端 **12 步门禁全 PASS** |
| 发布 | **`v1.0.0-rc1`**：GitHub Release（wheel 内含前端产物）+ Docker 镜像 `ghcr.io/edwardxiao-bit/mathgen` + npm 启动器 `npx mathgen` + 一键脚本。版本号**单点定义**在 `backend/app/__init__.py` 的 `__version__`，与 `backend/pyproject.toml`、`frontend/package.json`、tag 四处必须一致（CI 会拦） |

## 文档索引

| 文件 | 作用 |
| --- | --- |
| `docs/PROJECT.md` | **项目总览与导航**：范围、里程碑、约束、文档地图 |
| `docs/00-决策记录.md` | **唯一事实来源**：已定决策 D1–D82 + 待决问题 |
| `docs/01-需求分析.md` | 需求分析文档（v1.2 冻结） |
| `docs/02-技术设计.md` | 技术设计文档（v0.4，含设计理由与 DQ） |
| `docs/03-协作规范.md` | **多智能体协作硬规则**：写域、交接、冲突、验证 |
| `docs/04-接口契约.md` | **接口唯一权威来源**：数据结构、API、表、依赖方向 |
| `docs/05-任务板.md` | **工作台账**：WBS、写域、依赖、验收标准、DoD |
| `docs/06-测试方案.md` | 门禁测试、安全测试、端到端场景、性能基准 |
| `docs/07-设计规范.md` | **老师端设计规范**（M3.5）：设计令牌、组件清单与必备状态、交互约定、可判定判据 A1–A8 |
| `docs/08-测试文档.md` | **课程《测试文档》源文件**（M7）：测试计划、45 条用例、45 条缺陷记录、需求覆盖分析、结论与遗留问题 |
| `docs/word/` | 课程提交 Word / PDF / 配图 |
| `tools/` | 文档构建脚本 |
| `README.md` | **面向使用者的入口**：四种运行方式、老师/学生流程、FAQ |
| `CHANGELOG.md` | **对外版本变化 + 已知限制**（Keep a Changelog 格式）；发版时 CI 从这里抽对应小节当 Release 说明 |
| `LICENSE` | MIT（`backend/LICENSE` 是它的逐字副本，给子目录构建根用） |
| `scripts/`、`Dockerfile`、`docker-compose.yml`、`npm/`、`.github/workflows/` | **第一版发布的四种安装形态**：一键脚本 / 容器 / `npx` 启动器 / wheel |

## 红线（违反即返工）

1. **不改他人写域内的文件** —— 写域表见 `docs/03-协作规范.md` §2；跨域改动先登记、由 Lead 裁决。
2. **不直接编辑 `docs/word/*.docx`** —— 改 `docs/*.md` 后重跑 `tools/build_docs.py`（D37）。
3. **不擅改冻结的接口契约** —— 冻结项见 `docs/04-接口契约.md` §1；改动必须先在 `00-决策记录.md` 新增决策。
4. **不把 `answer` / `steps` 下发到学生端** —— 契约 §9 冻结，由自动化测试守住。
5. **`backend/app/engine/` 与 `grading/` 不得 import 数据库、FastAPI 或任何 I/O** —— 契约 §8 冻结。
6. **不引入外部网络依赖**（CDN、在线字体、外部 API）—— 破坏离线可用（NFR-1 / NFR-9）。
7. **不发没有证据的完工声明** —— 必须有可复现命令 + 原始输出，见 `03-协作规范.md` §8。
8. **不在持怀疑态度时「先让代码跑起来」而绕过测试**。

## 共享文件写者

| 文件 | 谁可以改 |
| --- | --- |
| `AGENTS.md`、`docs/PROJECT.md`、`docs/00`、`docs/03`、`docs/04`、`docs/05`、`docs/07`、`README.md`、`CHANGELOG.md`、`LICENSE`、`Dockerfile`、`docker-compose.yml`、`.dockerignore`、`scripts/**`、`npm/**`、`.github/**` | **仅 Lead** |
| `frontend/src/design/tokens.css` | **仅 Lead**（设计令牌冻结契约，D67；实现方只读） |
| `docs/01-需求分析.md`、`docs/02-技术设计.md` | 只读；修改需申请并同步重新生成 Word |
| `docs/06-测试方案.md` | verify |
| `docs/08-测试文档.md`、`docs/word/**`、`tools/**` | docs-build |

## 仓库与远程

| 项 | 值 |
| --- | --- |
| 远程 | `https://github.com/EdwardXiao-bit/mathgen.git` |
| 分支 | **`main` 单分支直接提交**（项目规模小，不设 dev/release 分支） |
| 代理 | **必需**：本机直连 `github.com` 会被 reset。已在仓库内设 `http.proxy=http://127.0.0.1:7890`；**新克隆需自行设置**（`git config http.proxy http://127.0.0.1:7890`） |
| 凭据 | 走 Git Credential Manager（system 配置 `credential.helper=manager`），首次推送会弹登录 |
| 提交身份 | `user.name=EdwardXiao-bit`、`user.email=186912622+EdwardXiao-bit@users.noreply.github.com`（GitHub **noreply** 地址，D60）。全局与仓库局部都已设。<br>**不要**改回真实邮箱：GitHub 只认 `author email` 且必须**已绑定账号**，否则提交仍在但 contributors 为空。<br>**注意**：`user.name` 填什么都不影响归属（曾经被误设成一串邮箱，看着像配错了） |
| 行尾 | 统一 LF，见 `.gitattributes`（不要在 Windows 上改成 CRLF，会产生整文件 diff） |
| 入库 | `docs/word/` 的 docx / pdf / figures **入库**（课程交付物与配图源文件） |
| 不入库 | `data/`、`*.db`、`node_modules/`、`__pycache__/`、测试与构建产物，见 `.gitignore` |

提交信息格式见 [`docs/03-协作规范.md`](docs/03-协作规范.md) §10；**提交前必须跑通该模块的门禁测试**（[`docs/06-测试方案.md`](docs/06-测试方案.md)）。

> **代理会临时抽风（已实测两次，方向相反）**：端口 7890 在监听但上游连不上 GitHub 时，
> `git push` 报 `OpenSSL SSL_connect: SSL_ERROR_SYSCALL`。
> 此时可**临时绕过**（不改配置）：`git -c http.proxy= push origin main` 直连。
> 实测中「直连不通、代理通」与「代理不通、直连通」都出现过——**两条路都可能临时失败，重试即可**，
> 不要把仓库的 `http.proxy` 配置删掉。

## 常用命令

```powershell
# 工作区 Python（纯逻辑测试与文档构建用它，零依赖）
$PY  = 'C:\Users\Ed\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
# 项目 venv Python（M2 起：FastAPI / SQLAlchemy / Jinja2 / Playwright 装在这里，见 D51）
$VPY = 'C:\Users\Ed\dsh_workspace\mathgen\backend\.venv\Scripts\python.exe'

# 后端门禁测试（标准库 unittest，见 D43；两套 Python 都能跑，装了依赖的用 $VPY）
& $VPY -m unittest discover -s backend/tests -t backend -v            # 全部
& $VPY -m unittest discover -s backend/tests/engine -t backend -v     # 只跑引擎
& $VPY -m unittest discover -s backend/tests/api    -t backend -v     # 只跑 API（M2）

# 启动服务（M3 起**一条命令**即可，后端同时托管前端产物）
& $VPY -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
#   → http://127.0.0.1:8000/teacher 即为老师端（无需另开 Vite）
#   → 若要从局域网访问（学生端 M4 用），把 --host 改成 0.0.0.0

# 文档与配图
& $PY tools\make_diagrams.py    # 13 张配图 -> docs/word/figures/
& $PY tools\build_docs.py       # md -> docx（封面 / 目录 / 图表题注）-> docs/word/
                                # 只重建一份：& $PY tools\build_docs.py 03
```

> **端口约定（冻结）**：后端固定 `127.0.0.1:8000`；前端 Vite dev server `5173`，把 `/api` 代理到 `127.0.0.1:8000`。
> **依赖安装**：`& 'C:\Users\Ed\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe' -m venv backend\.venv` 后
> `& $VPY -m pip install --no-cache-dir -e backend`（清单见 `backend/pyproject.toml`，D51）。**不要**往运行时自带的 site-packages 里装业务依赖。

**文档构建要点**：图的位置在 `build_docs.py` 的 `ANCHORS` 里按标题声明，锚点找不到会**报错退出**（不会静默错位）；Word 打开后按 **F9 更新目录**（python-docx 只写域代码，无法预生成条目）。

> **本机 pwsh 陷阱（已实测）**：`unittest` 把输出写到 stderr，PowerShell 会把它包成 `NativeCommandError`，
> 命令看起来是 `exit code 1`。**判门禁必须以 `$LASTEXITCODE` 为准**（Python 自身返回 0）。
> 取证写法：`& $PY -m unittest ...; "exit=$LASTEXITCODE"`

## 验收必读：本机两个已实测的环境坑

### 坑 1 · LibreOffice 光栅渲染

LibreOffice Kit 的 `render`（**docx → PNG**）在本机会把整个版心**右移 1.53cm**，看上去像内容溢出页面、被右边界裁掉。

**这是渲染器缺陷，不是文档缺陷。** 同一份 docx：

| 通道 | 正文左边界（设定值 3.0cm） |
| --- | --- |
| docx → PNG（LibreOffice 光栅） | 4.53cm ❌ |
| docx → PDF → PNG（pdfium） | **3.01cm** ✅ |

**版式验收一律走 `convert` 成 PDF 再渲染**，不要用 docx → PNG 判断版式。
（已用 docGrid 与边距对照实验排除文档侧原因。）

> **同一个 `mkdtemp` 坑也命中 LibreOffice Kit**（M3 实测）：转换子进程若用系统 `TEMP`，会报
> `EPERM: operation not permitted`（目录形如 `libreoffice-kit-XXXXXX`）。
> **调用 LibreOffice 时同样要把 `TEMP`/`TMP` 指向可写目录**，与坑 2 是同一类问题、同一套绕法。

### 坑 2 · Playwright 启动（PDF 导出依赖它）

`BrowserType.launch()` 在本机会连续撞两个**成因不同**的问题，**两个都要解决**才能出 PDF：

| # | 现象 | 绕过（实测有效） |
| --- | --- | --- |
| 1 | `EPERM: not permitted, mkdir '...\AppData\Local\ms-playwright\__dirlock'` | 把浏览器装到工作区：`$env:PLAYWRIGHT_BROWSERS_PATH="<repo>\.playwright"` 后再 `playwright install chromium`（`.playwright/` 已 gitignore） |
| 2 | `EPERM: not permitted, mkdtemp '...\Temp\playwright-artifacts-XXXXXX'` | 把 `TEMP` 与 `TMP` 指向工作区目录：`$env:TEMP=$env:TMP="<repo>\.tmp-pw"` |

**两个都设好后的实测结果**：Chromium 156.0.8078.4 可正常启动并渲染页面。

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$REPO\.playwright"
$env:TEMP = "$REPO\.tmp-pw"; $env:TMP = "$REPO\.tmp-pw"
```

**实现口径**：`export_pdf` 里用 `MATHGEN_PW_TMP` 环境变量读取可写临时目录并传给 `launch(env=...)`，
未设置时用系统默认——本机能跑、其他机器不受影响，也不把本机路径硬编进生产代码。
`PLAYWRIGHT_BROWSERS_PATH` 由 Playwright 自行读取，代码里无需处理。

## 本项目的规矩

- **文件才是记忆**：谈定的事立刻落盘，不要只留在对话里。
- **先登记再动手**：任务必须在 `docs/05-任务板.md` 中存在，含写域 / 依赖 / 验收标准。
- **待决问题集中管理**：登记在 `00-决策记录.md`，解决后原地标记结论与日期；**不确定就登记，不要自己拍板**。
- **大范围阅读交给子代理**，只带回结论；能查的别贴，用 `grep` / `glob` / `read` 定位。
- **文档与代码注释用中文**，代码标识符用英文（`03-协作规范.md` §10）。
- **判据必须能红**（`03-协作规范.md` V9）：新增断言要附**留在仓库里**的负对照，并验证负对照真的制造了失败条件。

> **本机工具链坑（前端门禁）**：门禁脚本用 `String.raw` 模板承载内联 Python 时，写内联 Python（含注释）
> **不要用反引号**。但要分清两种形态，**它们的性质完全不同**：
> - **裸**反引号 → 真的提前终止模板 → `SyntaxError: Unexpected identifier`（M3.5 连续命中三次的就是它）
> - **转义**反引号（`` \` ``）→ 语法**完全合法**，只是难读、易与 JS 模板语法混淆
>
> 因此**不要**为这条约定去写"扫描模板体内有没有反引号"的 linter：能活着进到模板体内的反引号**必然**是转义的，
> 而裸的会让文件根本解析不了（扫描器自己就被截断了）—— 那种扫描器**只能抓到无害形态**（实测证伪过一次）。
> **语法正确性交给通用判定**：`frontend/scripts/check-script-syntax.mjs` 逐个 `node --check` `frontend/scripts/*.mjs`（含正负对照）。
> 这是"**枚举式判据永远不完整**"（D71）的同一个教训：按**性质**判，不按**枚举**判。
