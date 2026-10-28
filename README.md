# mathgen · 小学四则运算练习题生成与批改系统

给小学数学老师用的**四则运算出题 / 打印 / 批改**工具。系统运行在本地，老师出卷打印、学生扫码答题，交卷即出分——**无需注册，装好后无需联网，也不需要服务器**。

[![release](https://img.shields.io/github/v/release/EdwardXiao-bit/mathgen?include_prereleases&label=release)](https://github.com/EdwardXiao-bit/mathgen/releases)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![npm](https://img.shields.io/npm/v/mathgen-cli?label=npm)](https://www.npmjs.com/package/mathgen-cli)

> **当前版本 `v1.0.0-rc3`（预发布）**：功能已全部做完并逐项验收，四种安装方式都已上线、逐条实测过。
> 这一版相对 rc1：新增**入口页**（打开就是「进入使用 / 我是学生」两个门＋四步图解）、发卷页对已发布的卷
> 也显示卷号与二维码、表格数字列右对齐、窄屏不再挤成一字一行，界面里不再出现开发者措辞；
> 细节见 [CHANGELOG.md](CHANGELOG.md) 的 `1.0.0-rc3` 一节。
> 还没转成正式版 `1.0.0`，建议先在一两节课上试用一轮。
> 已知限制写在 [CHANGELOG](CHANGELOG.md) 里，请先看一眼再用到真实课堂上。

**目录**　[它解决什么问题](#它解决什么问题) ·
[四种运行方式](#四种运行方式) ·
[用起来是什么样](#用起来是什么样) ·
[功能清单](#功能清单) ·
[常见问题](#常见问题) ·
[从源码开发](#从源码开发) ·
[项目文档](#项目文档)

---

## 它解决什么问题

出一页口算练习，最花时间的从来不是「想题」——是**算答案、排版、印出来、再一道一道改**。
这个系统把这几步连起来：

1. **老师出卷**：选题型（横式口算 / 递等式 / 填空）、数域、运算、是否进位退位、是否带括号、题量；
   系统当场把题目和**标准答案**一起生成好。
2. **打印**：A4 排版，可以调每行几题、行距、字号、页边距；**预览就是打印结果**（同一份页面）。
   还能另存一份「答案版」自己留着。
3. **学生答题**：扫卷面上的二维码（或输入卷号）就能进学生端，在手机上答题、交卷。
4. **批改**：交卷瞬间判完——判分在服务端做，全系统**只有一套得分口径**；
您能看到每道题谁对谁错、全班的正确率分布。

题目不是随机的字符串，是**真算出来的**：每一道题都有正确答案与解题步骤，
所以判分不需要老师复核，也不会出现「题错了」。

---

## 四种运行方式

四种方式跑起来的是**同一个服务**，区别只在「怎么把 Python 环境和前端装到您的电脑上」。
按您机器上已经有什么挑一种即可：

| 方式 | 介绍 | 安装依赖 |
| --- | --- | --- |
| **一键脚本** | 双击即可运行，脚本自己建虚拟环境、装依赖、下 Chromium | Python 3.12+ 与 Git |
| **Docker** | 一条 `docker run`，镜像中包含 Chromium | Docker |
| **npm 启动器** | `npx mathgen-cli@rc`，自动装到 `~/.mathgen` | Node 18+ 与 Python 3.12+ |
| **pip** | 下载 wheel 装上，用 `mathgen` 命令 | Python 3.12+ |

不管您选择哪种方式，安装后的运行方式都是：**浏览器打开 <http://127.0.0.1:8000/teacher>**。

### 1. 一键脚本（最省事）

Windows（PowerShell 里执行，或右键「使用 PowerShell 运行」）：

```powershell
git clone https://github.com/EdwardXiao-bit/mathgen.git
cd mathgen
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

macOS / Linux：

```sh
git clone https://github.com/EdwardXiao-bit/mathgen.git
cd mathgen
./scripts/start.sh
```

脚本会做四件事：检查 Python → 建 `.venv` 并装依赖 → 构建前端 → 下 Chromium（PDF 导出要用），
然后启动服务并自动打开浏览器。**第一次大约 3～5 分钟**（主要是下载），之后再次运行无需等待。

要让**同一个局域网里的学生**也能连上（教室场景）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Lan
```

脚本会打印本机在局域网里的地址，学生用手机连那个地址（或扫卷面上的二维码）即可。
Windows 首次可能弹防火墙提示，需要允许。

### 2. Docker

```sh
docker run --rm -p 8000:8000 -v mathgen-data:/data ghcr.io/edwardxiao-bit/mathgen:1.0.0-rc3
```

或者用仓库里的 compose 文件（数据放在命名卷里，`docker compose down` 不会丢）：

```sh
docker compose up -d
```

镜像**自带前端产物和 Chromium**，容器里不需要联网、不需要再装任何东西。
代价是体积（1 GB 量级，主要是 Chromium 及其系统依赖）。数据都在 `/data` 卷里。

> 目前只提供 `linux/amd64`。Apple Silicon 上用 Docker Desktop 的模拟运行即可。

### 3. npm 启动器

```sh
npx mathgen-cli@rc
```

它会：找一个 Python 3.12+ → 在 `~/.mathgen/npm-1.0.0-rc3/venv` 建**独立**虚拟环境 →
从 GitHub Release 装后端 wheel（**wheel 里自带前端产物**）→ 需要时下 Chromium → 启动。
参数原样透传：

```sh
npx mathgen-cli@rc --host 0.0.0.0 --port 9000
```

它**不**自带 Python 运行时：后端是 Python 写的，Node 里跑不了，所以本机仍需 Python 3.12+。
也**不会**动您的系统 Python，全部装在 `~/.mathgen` 下，删掉那个目录就干净了。

> **包名是 `mathgen-cli`，转交的命令仍是 `mathgen`**：npm 上 `mathgen` 被判定与早已存在的
> `math-gen` 太像（`npm publish mathgen` 直接 403），所以包名让了一步。
> 当前 `rc` 与 `latest` 都指向 **`1.0.0-rc3`**，因此 `@rc`、`@1.0.0-rc3` 与裸写 `npx mathgen-cli`
> 拿到的是同一份；要钉住版本就用带版本号的那条。查已发布的版本：`npm view mathgen-cli versions`。

### 4. pip

从 [Releases](https://github.com/EdwardXiao-bit/mathgen/releases) 下载 wheel：

```sh
pip install mathgen_backend-1.0.0rc3-py3-none-any.whl
mathgen --version                        # 验证装好了，应打印 mathgen 1.0.0rc3
python -m playwright install chromium    # PDF 导出需要；不装则导出返回 503
mathgen                                  # 启动
```

如果 `pip install` 被系统拦下（macOS / 新版 Linux 有 externally managed 保护），先建虚拟环境再装：

```sh
python -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install mathgen_backend-1.0.0rc3-py3-none-any.whl
```

`mathgen` 命令的参数：

```
--host HOST        监听地址，默认 127.0.0.1；要让局域网学生访问用 0.0.0.0
--port PORT        端口，默认 8000
--data-dir DIR     数据目录，安装版默认 ~/.mathgen/data
--db PATH          直接指定数据库文件
--static-dir DIR   前端产物目录
--no-browser       启动后不要自动打开浏览器
--version          打印版本号
```

---

## 用起来是什么样

### 老师

**出卷 → 调版式 → 打印 → 收成绩**

- 出卷页选参数，右边**实时预览**第一页；不满意可以「换一题」或直接删掉某题。
- 版式面板调每行题数、行距、字号、页边距，预览跟着变；改完**自动落库**，下次打开还是这套。
- 「打印」出的是最终卷面（含卷号与二维码）；「答案版」另出一份带答案的。
- 卷库页管理所有卷子：复制、改标题、归档；**没有成绩的卷可以直接删，有成绩的只能归档**
  （成绩要能追溯到卷子，所以不允许把有成绩的卷物理删掉）。
- 成绩页看全班：平均分、最高最低、每道题的正确率、逐题对错明细，可导出。

### 学生

**扫码 → 答题 → 交卷 → 立刻看到分数**

- 手机扫卷面上的二维码，或输入卷号；填一次姓名 + 学号，之后进来自动记住。
- 一道一道答，可以上下翻；交卷后立刻显示得分与对错。
- 交卷是**并发安全**的：50 名学生同时交卷不会互相阻塞。

---

## 功能清单

**出题**
- 三种题型：横式口算、递等式（分步）、填空
- 七个数域 R1–R7、四则运算任意组合、最大数值上限
- 进位 / 退位 / 整除 / 允许余数 / 括号，逐项可控
- 题量 1–100，随机种子可复现

**排版与打印**
- A4 排版：每行 2–6 题、行距 0.8–2.0、字号 10–20、页边距 1.0–3.0 cm
- 「预览 = 打印 = 导出」同源：三条路径用的是**同一份页面**，不会出现「预览好好的印出来错位」
- 答案版形式（题干带答案，用于老师自查）
- 导出 PDF / Word，导出前有**排版容量守卫**，操作透明

**答题与判分**
- 学生扫码 / 输入卷号进入，无需账号
- 服务端判分，得分口径全系统唯一
- 成绩明细、逐题正确率、导出

**工程**
- 后端 1275 个用例、前端 12 步门禁，全部通过
- 接口契约冻结在 `docs/04`，有「文档 ↔ 实现」的机器对账判据
- 单一服务托管前后端，一个进程一个端口，没有额外组件

---

## 常见问题

**Q：需要联网吗？**
装的时候需要（下依赖）。装好后出题、打印、答题、判分**全都不用联网**——
断网也能出卷和打印，这条有专门的离线判据。

**Q：数据存在哪？会丢吗？**
一个 SQLite 文件。源码运行放在 `<仓库>/backend/data/mathgen.db`，
安装版放在 `~/.mathgen/data/mathgen.db`（`mathgen` 启动时会打印完整路径），
Docker 版在 `/data` 卷里。**备份就是拷这个文件。**

**Q：能装到服务器上让多个班一起用吗？**
不建议，也没做。它是为「教室一台电脑」设计的：没有账号体系、没有权限隔离、没有多副本。
**不要暴露到公网。** 局域网内一个班用没问题。

**Q：PDF 导出报 503 / `EXPORT_NOT_READY`？**
Chromium 没装好。跑一次 `python -m playwright install chromium` 即可。
出题、预览、打印都不受影响，只有 PDF 导出需要它。

**Q：打印出来的卷子字体太大 / 太挤？**
在版式面板里调；预览就是打印结果。
如果放不下，导出会**直接拒绝**并给出建议（比如「请把字号从 20 号调到 17 号以下」），
照做就能导出。浏览器直接 Ctrl+P 打印没有这层拦截，**用系统打印对话框确认一下再上纸**。

**Q：学生答题时断网了会怎样？**
答题页面已经加载在手机上，交卷需要服务在。教室场景建议先把服务跑起来再让学生扫码。

**Q：为什么 `npx mathgen-cli` 还要装 Python？**
因为后端是 Python 写的。npm 包只是**启动器**：它把 Python 环境装到 `~/.mathgen`，
然后转交给 `mathgen` 命令。不想装 Python 就用 Docker 方式。

---

## 从源码开发

前端（React + TypeScript + Vite）：

```sh
pnpm --dir frontend install
pnpm --dir frontend dev        # 开发服务器，热更新
pnpm --dir frontend build      # 产出 frontend/dist
pnpm --dir frontend verify     # 12 步门禁（含真实浏览器验收）
```

后端（FastAPI + SQLAlchemy + SQLite）：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t backend   # 全部用例
backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests/contract -t backend
```

一起跑（后端托管前端产物，一个端口）：

```powershell
scripts\start.ps1
```

仓库里还有可复用的判据：源码文本卫生（纯 LF / 无 BOM）、接口契约与文档对账、
排版容量与打印保真、并发提交、离线可用、性能……都写在 [`docs/06-测试方案.md`](docs/06-测试方案.md) 里。

---

## 项目文档

内容的事实来源是 `docs/*.md`；`docs/word/` 下的 Word 是**派生产物**，不要直接编辑 docx。

| 文档 | 内容 |
| --- | --- |
| [`docs/PROJECT.md`](docs/PROJECT.md) | 项目总览与里程碑 |
| [`docs/01-需求分析.md`](docs/01-需求分析.md) | 功能与非功能需求（FR / NFR 编号） |
| [`docs/02-技术设计.md`](docs/02-技术设计.md) | 架构、模块划分、数据模型、关键流程 |
| [`docs/03-协作规范.md`](docs/03-协作规范.md) | 提交规范与协作约定 |
| [`docs/04-接口契约.md`](docs/04-接口契约.md) | **接口契约（冻结）**：路由、状态码、错误结构 |
| [`docs/05-任务板.md`](docs/05-任务板.md) | 任务分解、验收记录、遗留问题 |
| [`docs/06-测试方案.md`](docs/06-测试方案.md) | 测试策略、门禁清单、判据与原始证据 |
| [`docs/07-设计规范.md`](docs/07-设计规范.md) | 视觉与交互规范 |
| [`docs/08-测试文档.md`](docs/08-测试文档.md) | 课程《测试文档》：用例、缺陷记录、覆盖分析 |
| [`docs/00-决策记录.md`](docs/00-决策记录.md) | 全部工程决策（D1–D82）与理由 |

课程提交材料（Word 打开后按 **F9 更新目录**；配图源文件在 [`docs/word/figures/`](docs/word/figures/)）：

| 文档 | Word | PDF |
| --- | --- | --- |
| 需求分析文档 | [`01-需求分析文档.docx`](docs/word/01-需求分析文档.docx) | [PDF](docs/word/01-需求分析文档.pdf) |
| 技术设计文档 | [`02-技术设计文档.docx`](docs/word/02-技术设计文档.docx) | [PDF](docs/word/02-技术设计文档.pdf) |
| 测试文档 | [`03-测试文档.docx`](docs/word/03-测试文档.docx) | [PDF](docs/word/03-测试文档.pdf) |

---

## 许可

[MIT](LICENSE) © 2026 肖铭皓、胡宇书
