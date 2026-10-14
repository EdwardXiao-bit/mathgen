# mathgen —— 小学四则运算练习题生成与批改系统

面向**小学老师**的「出题 + 批改」工具：**老师端**配置题目、生成、打印、发卷、看成绩；**学生端**局域网扫码、自主练习、自动批改。
部署形态：跑在老师本机 / 教室内网机器，**不依赖公网**（D10）。

当前阶段：**需求已冻结 → 技术设计 v0.4 → 课程 Word 已产出 → 代码未开始**。

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
| 技术设计文档 | **v0.4 讨论稿**（覆盖矩阵 50/50 自检通过），DQ5–DQ8 待确认 |
| 协作体系 | **已建立**（`docs/03`–`06` + `PROJECT.md`） |
| 课程提交 Word | **需求分析、技术设计 已产出**；测试文档待写（M7） |
| 配图 | **10 张 PNG**，见 `docs/word/figures/` |
| 代码 | 未开始（迭代 1：M1 出题引擎） |

## 文档索引

| 文件 | 作用 |
| --- | --- |
| `docs/PROJECT.md` | **项目总览与导航**：范围、里程碑、约束、文档地图 |
| `docs/00-决策记录.md` | **唯一事实来源**：已定决策 D1–D42 + 待决问题 |
| `docs/01-需求分析.md` | 需求分析文档（v1.2 冻结） |
| `docs/02-技术设计.md` | 技术设计文档（v0.4，含设计理由与 DQ） |
| `docs/03-协作规范.md` | **多智能体协作硬规则**：写域、交接、冲突、验证 |
| `docs/04-接口契约.md` | **接口唯一权威来源**：数据结构、API、表、依赖方向 |
| `docs/05-任务板.md` | **工作台账**：WBS、写域、依赖、验收标准、DoD |
| `docs/06-测试方案.md` | 门禁测试、安全测试、端到端场景、性能基准 |
| `docs/word/` | 课程提交 Word / PDF / 配图 |
| `tools/` | 文档构建脚本 |

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
| `AGENTS.md`、`docs/PROJECT.md`、`docs/00`、`docs/03`、`docs/04`、`docs/05`、`README.md` | **仅 Lead** |
| `docs/01-需求分析.md`、`docs/02-技术设计.md` | 只读；修改需申请并同步重新生成 Word |
| `docs/06-测试方案.md` | verify |
| `docs/word/**`、`tools/**` | docs-build |

## 仓库与远程

| 项 | 值 |
| --- | --- |
| 远程 | `https://github.com/EdwardXiao-bit/mathgen.git` |
| 分支 | **`main` 单分支直接提交**（项目规模小，不设 dev/release 分支） |
| 代理 | **必需**：本机直连 `github.com` 会被 reset。已在仓库内设 `http.proxy=http://127.0.0.1:7890`；**新克隆需自行设置**（`git config http.proxy http://127.0.0.1:7890`） |
| 凭据 | 走 Git Credential Manager（system 配置 `credential.helper=manager`），首次推送会弹登录 |
| 行尾 | 统一 LF，见 `.gitattributes`（不要在 Windows 上改成 CRLF，会产生整文件 diff） |
| 入库 | `docs/word/` 的 docx / pdf / figures **入库**（课程交付物与配图源文件） |
| 不入库 | `data/`、`*.db`、`node_modules/`、`__pycache__/`、测试与构建产物，见 `.gitignore` |

提交信息格式见 [`docs/03-协作规范.md`](docs/03-协作规范.md) §10；**提交前必须跑通该模块的门禁测试**（[`docs/06-测试方案.md`](docs/06-测试方案.md)）。

## 常用命令

```powershell
# 工作区 Python（后端测试与文档构建都用它）
$PY = 'C:\Users\Ed\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'

# 后端门禁测试（M1 起生效；标准库 unittest，无需安装任何依赖，见 D43）
$PY -m unittest discover -s backend/tests -t backend -v            # 全部
$PY -m unittest discover -s backend/tests/engine -t backend -v     # 只跑引擎

# 文档与配图
& $PY tools\make_diagrams.py    # 10 张配图 -> docs/word/figures/
& $PY tools\build_docs.py       # md -> docx（封面 / 目录 / 图表题注）-> docs/word/

# 前端与端到端（M2 起生效，目录尚未创建）
pnpm --dir frontend test
npx playwright test tests/e2e
```

**文档构建要点**：图的位置在 `build_docs.py` 的 `ANCHORS` 里按标题声明，锚点找不到会**报错退出**（不会静默错位）；Word 打开后按 **F9 更新目录**（python-docx 只写域代码，无法预生成条目）。

> **本机 pwsh 陷阱（已实测）**：`unittest` 把输出写到 stderr，PowerShell 会把它包成 `NativeCommandError`，
> 命令看起来是 `exit code 1`。**判门禁必须以 `$LASTEXITCODE` 为准**（Python 自身返回 0）。
> 取证写法：`& $PY -m unittest ...; "exit=$LASTEXITCODE"`

## 验收必读：LibreOffice 光栅渲染有一个已实测的坑

LibreOffice Kit 的 `render`（**docx → PNG**）在本机会把整个版心**右移 1.53cm**，看上去像内容溢出页面、被右边界裁掉。

**这是渲染器缺陷，不是文档缺陷。** 同一份 docx：

| 通道 | 正文左边界（设定值 3.0cm） |
| --- | --- |
| docx → PNG（LibreOffice 光栅） | 4.53cm ❌ |
| docx → PDF → PNG（pdfium） | **3.01cm** ✅ |

**版式验收一律走 `convert` 成 PDF 再渲染**，不要用 docx → PNG 判断版式。
（已用 docGrid 与边距对照实验排除文档侧原因。另一条真实缺陷记录见 `docs/word` 的渲染校验过程。）

## 本项目的规矩

- **文件才是记忆**：谈定的事立刻落盘，不要只留在对话里。
- **先登记再动手**：任务必须在 `docs/05-任务板.md` 中存在，含写域 / 依赖 / 验收标准。
- **待决问题集中管理**：登记在 `00-决策记录.md`，解决后原地标记结论与日期；**不确定就登记，不要自己拍板**。
- **大范围阅读交给子代理**，只带回结论；能查的别贴，用 `grep` / `glob` / `read` 定位。
- **文档与代码注释用中文**，代码标识符用英文（`03-协作规范.md` §10）。
