# mathgen · 小学四则运算练习题生成与批改系统

给小学数学老师用的**四则运算出题 / 打印 / 批改**工具。系统运行在本地，老师出卷打印、学生扫码答题，交卷即出分——**无需注册，装好后无需联网，也不需要服务器**。

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![release](https://img.shields.io/github/v/release/EdwardXiao-bit/mathgen?include_prereleases&label=release)](https://github.com/EdwardXiao-bit/mathgen/releases)
[![CI](https://github.com/EdwardXiao-bit/mathgen/actions/workflows/release.yml/badge.svg)](https://github.com/EdwardXiao-bit/mathgen/actions/workflows/release.yml)
[![npm](https://img.shields.io/npm/v/mathgen-cli?label=npm)](https://www.npmjs.com/package/mathgen-cli)
[![docker](https://img.shields.io/badge/ghcr.io-mathgen-2496ed?logo=docker&logoColor=white)](https://github.com/EdwardXiao-bit/mathgen/pkgs/container/mathgen)

[![python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![fastapi](https://img.shields.io/badge/FastAPI-0.143+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![sqlalchemy](https://img.shields.io/badge/SQLAlchemy-2.1+-d71f00)](https://www.sqlalchemy.org/)
[![react](https://img.shields.io/badge/React-18-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![typescript](https://img.shields.io/badge/TypeScript-5.6-3178c6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![vite](https://img.shields.io/badge/Vite-5.4-646cff?logo=vite&logoColor=white)](https://vite.dev/)

![offline](https://img.shields.io/badge/装好后-无需联网-1d6b46)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-5c626a)
![no-cdn](https://img.shields.io/badge/前端-零外部资源-b4622a)

> **当前版本 `v1.0.2`（正式版）**：功能已全部做完并逐项验收，四种安装方式都已上线、逐条实测过。
> 这一版修的是**中文 Windows 上的启动横幅**：它为了报出「手机能连的那几条地址」会读一次 `ipconfig`，
> 而控制台输出是 CP936，按 UTF-8 解码失败又被吞掉，于是地址列表整段消失——手机连热点时就扫不到能用的地址。
> 上一版 `1.0.1` 修的是 `npx` 启动器挑 Python 的顺序（曾可能选中没验收过的新版本）；
> `1.0.0` 才是正式版首发，修的是横幅地址顺序、一键脚本默认监听、学生白卷与成绩页三态、测试误写产品库。
> 细节见 [CHANGELOG.md](CHANGELOG.md)。

**目录**　[它解决什么问题](#它解决什么问题) ·
[四种运行方式](#四种运行方式) ·
[用起来是什么样](#用起来是什么样) ·
[功能清单](#功能清单) ·
[技术栈](#技术栈) ·
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
每次练习都能点开看每道题谁对谁错、学生答的是什么、正确答案是什么。

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
| **npm 启动器** | `npx mathgen-cli`，自动装到 `~/.mathgen` | Node 18+ 与 Python 3.12+ |
| **pip** | 下载 wheel 装上，用 `mathgen` 命令 | Python 3.12+ |

不管选择哪种方式，装好后用浏览器打开 `http://127.0.0.1:8000/` 就是**入口页**（老师与学生都从这里进），
老师端在 `/teacher`、学生端在 `/student`；一键脚本默认会把浏览器直接开到这台电脑在局域网里的地址
（见下一节），这样学生手机扫到的二维码才是能连上的地址。

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

**默认就是「教室模式」**：服务对整个局域网开放，启动窗口会列出这台电脑的几条地址，
并且直接把浏览器开到手机能连的那一条——学生用手机连上同一 Wi-Fi（或这台电脑的热点）就能扫码答题。

窗口里那几行这样看：

```
  学生在别的设备上答题（同一局域网；手机连这台电脑的热点就用第一条）：
    http://192.168.137.1:8000/student   手机连这台电脑的热点，用这条
    http://10.98.246.81:8000/student    默认路由出口（电脑自己上网用的那块网卡）
```

- 手机连的是**这台电脑开的移动热点** → 用第一条（`192.168.137.x`，这来自 Windows 热点）。
- 老师手机和电脑连的是**同一个 Wi-Fi / 路由器** → 用「默认路由出口」那条。
- 只在自己电脑上出题打印、不让学生连 → 加 `-Local`（macOS / Linux：`MATHGEN_HOST=127.0.0.1 ./scripts/start.sh`），
  服务就只监听本机，别的机器连不上。

> 手机扫码后一直转圈？多半是 Windows 防火墙拦了入站。第一次启动时要允许「Python 访问网络」；
> 没弹过窗口的话，用管理员 PowerShell 放行一次端口即可：
>
> ```powershell
> New-NetFirewallRule -DisplayName "mathgen 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
> ```

### 2. Docker

```sh
docker run --rm -p 8000:8000 -v mathgen-data:/data ghcr.io/edwardxiao-bit/mathgen:1.0.2
```

或者用仓库里的 compose 文件（数据放在命名卷里，`docker compose down` 不会丢）：

```sh
docker compose up -d
```

镜像**自带前端产物和 Chromium**，容器里不需要联网、不需要再装任何东西。
代价是体积（1 GB 量级，主要是 Chromium 及其系统依赖）。数据都在 `/data` 卷里。

> 已发布的 `1.0.2` 镜像只有 `linux/amd64`；Apple Silicon 上用 Docker Desktop 的模拟运行即可。
> 下一次发版起会同时提供 `linux/amd64` 与 `linux/arm64`（arm64 原生跑，不再模拟）。

### 3. npm 启动器

```sh
npx mathgen-cli
```

它会：找一个**已验收的** Python（3.12 / 3.13 优先）→ 在 `~/.mathgen/npm-1.0.2/venv` 建**独立**虚拟环境 →
从 GitHub Release 装后端 wheel（**wheel 里自带前端产物**）→ 需要时下 Chromium（约 150 MB）→ 启动。
参数原样透传：

```sh
npx mathgen-cli --host 0.0.0.0 --port 9000
```

它**不**自带 Python 运行时：后端是 Python 写的，Node 里跑不了，所以本机仍需 Python 3.12+。
也**不会**动您的系统 Python，全部装在 `~/.mathgen` 下，删掉那个目录就干净了。

> **别在仓库的 `npm/` 目录里跑 `npx`**：那个目录自带一份 `package.json`（`name` 就叫 `mathgen-cli`），
> npm 会把「本地这个包」当成目标、不去取远端，于是报 `'mathgen' 不是内部或外部命令`。
> 换个目录跑就行（例如 `cd %USERPROFILE%`）。

> **包名是 `mathgen-cli`，转交的命令仍是 `mathgen`**：npm 上 `mathgen` 被判定与早已存在的
> `math-gen` 太像（`npm publish mathgen` 直接 403），所以包名让了一步。
> 当前 `latest` 指向 **`1.0.2`（正式版）**，`rc` 仍留在 `1.0.0-rc3` 作为预发布通道；
> 想用正式版就别写 `@rc`，要钉住版本就写死版本号。查已发布的版本：`npm view mathgen-cli versions`。
> 只想看它挑中哪个 Python（不装任何东西）：`MATHGEN_PRINT_PYTHON=1 npx mathgen-cli --version`。

### 4. pip

从 [Releases](https://github.com/EdwardXiao-bit/mathgen/releases) 下载 wheel：

```sh
pip install mathgen_backend-1.0.2-py3-none-any.whl
mathgen --version                        # 验证装好了，应打印 mathgen 1.0.2
python -m playwright install chromium    # PDF 导出需要；不装则导出返回 503
mathgen                                  # 启动
```

如果 `pip install` 被系统拦下（macOS / 新版 Linux 有 externally managed 保护），先建虚拟环境再装：

```sh
python -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install mathgen_backend-1.0.2-py3-none-any.whl
```

`mathgen` 命令的参数：

```
--host HOST        监听地址，默认 127.0.0.1；要让局域网学生访问用 0.0.0.0
--port PORT        端口，默认 8000
--data-dir DIR     数据目录，安装版默认 ~/.mathgen/data
--db PATH          直接指定数据库文件
--static-dir DIR   前端产物目录
--no-browser       启动后不要自动打开浏览器
--reload           改代码自动重启（只在从源码开发时用）
--log-level LEVEL  日志级别，默认 info
--version          打印版本号
```

---

## 用起来是什么样

### 老师

**出卷 → 调版式 → 打印 → 收成绩**

- 出卷页选参数，右边**实时预览**；不满意可以「换一题」或直接删掉某题。
- 版式面板调每行题数、行距、字号、页边距，预览跟着变；改完**自动落库**，下次打开还是这套。
- 预览按 A4 **真分页**：页与页之间留缝，右侧页码点一下跳到那一页，`Ctrl + 滚轮`（或工具栏的 `−/+`）
  在 50%–200% 之间缩放；页数、每页几题、纸面尺寸都写在底部状态栏。
- 「打印」出的是最终卷面（含卷号与二维码）；「答案版」另出一份带答案的。
- 卷库页管理所有卷子：复制、改标题、归档；**没有成绩的卷可以直接删，有成绩的只能归档**
  （成绩要能追溯到卷子，所以不允许把有成绩的卷物理删掉）。
- 成绩页列出所有交卷记录（按卷、按姓名筛，分页）；点开一条看逐题对错、学生的答案与正确答案，
  列表可以导出 CSV / Excel。

### 学生

**扫码 → 答题 → 交卷 → 立刻看到分数**

- 手机扫卷面上的二维码，或输入卷号；填一次姓名 + 学号，之后进来自动记住。
- **一题一屏**：题干占满一屏、输入框自动聚焦，数字键盘直接接；底部左右翻题，右上角计时。
- 交卷后立刻出分，逐题分「答对 / 答错 / 未作答」三态；一题没答就交卷时会先确认一次。
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
- 按 A4 真分页预览：页间留缝、页码可点着跳、50%–200% 七档缩放（`Ctrl + 滚轮`）；改一题整卷即时重排
- 「预览 = 打印 = 导出」同源：三条路径用的是**同一份页面**，不会出现「预览好好的印出来错位」
- 答案版形式（题干带答案，用于老师自查）
- 导出 PDF / Word，导出前有**排版容量守卫**，操作透明

**答题与判分**
- 学生扫码 / 输入卷号进入，无需账号；一题一屏、提交即判
- 服务端判分，得分口径全系统唯一
- 成绩三态（答对 / 答错 / 未作答）、逐题对错明细、导出 CSV / Excel

**工程**
- 后端 1317 个用例、前端 12 步门禁（其中 1 条「离线对照组」用例要求验收机真能上外网：未装离线守卫的浏览器必须能打开外部网站，缺网或代理故障时这一条必红，其余照常）
- 接口契约冻结在 `docs/04`，有「文档 ↔ 实现」的机器对账判据
- 单一服务托管前后端，一个进程一个端口，没有额外组件

---

## 技术栈

![层·后端](https://img.shields.io/badge/后端-FastAPI%20%2B%20Uvicorn%20%2B%20Pydantic-009688?logo=fastapi&logoColor=white)
![层·数据](https://img.shields.io/badge/数据-SQLite%20%2B%20SQLAlchemy-d71f00)
![层·卷面](https://img.shields.io/badge/卷面-Jinja2%20%2B%20A4%20@page-4a4f57)
![层·导出](https://img.shields.io/badge/导出-Chromium%20打印%20%2B%20python--docx-2b2f36)
![层·前端](https://img.shields.io/badge/前端-React%2018%20%2B%20TypeScript%205.6-61dafb?logo=react&logoColor=white)
![层·构建](https://img.shields.io/badge/构建-Vite%205%20%2B%20pnpm-646cff?logo=vite&logoColor=white)
![层·质量](https://img.shields.io/badge/质量-1200%2B%20用例%20%2B%2012%20步门禁-1d6b46)

下面这张表把每一层**为什么这么选**写清楚——技术栈不是为了显得现代，是为了让「打印出来正好是一张 A4」
这件事在每个环节都成立。

一句话：**Python 出一份 HTML，浏览器印它、导出它、预览它**——预览、打印、PDF、Word 都来自同一份页面，
所以「预览什么样、印出来就什么样」不是靠两套代码对齐，而是本来就只有一套。

| 层 | 选型 | 为什么是它 |
| --- | --- | --- |
| 后端 | Python 3.12 · FastAPI 0.143 · Uvicorn · Pydantic 2.14 | 一个进程一个端口托管前后端；接口契约自动出 OpenAPI 文档，契约测试直接对它做对账 |
| 数据 | SQLAlchemy 2.1 · SQLite 单文件 | 没有数据库服务要装要启；「备份」就是拷一个文件，换电脑也一样 |
| 卷面渲染 | Jinja2 服务端渲染 HTML + A4 `@page` 打印 CSS | 卷面是**印刷品**，排版交给 CSS 引擎而不是前端框架；老师看到的预览是同一个页面的 iframe |
| 导出 | Chromium 无头打印（PDF）· python-docx（Word） | 和浏览器打印走同一条通道，避免「预览好好的、印出来错位」 |
| 二维码 | `qrcode[pil]` | 纯 Python 生成 PNG，不依赖任何在线服务 |
| 前端 | React 18 · TypeScript 5.6 · Vite 5.4 · TanStack Query 5 · React Router 6 | 两个入口（老师端 / 学生端）分别打包，学生端只带答题需要的那一小份 |
| 离线 | **不用任何 CDN、不外链字体与图标** | 断网可用是硬要求：字体用系统字、图标是内联 SVG/CSS，构建产物全打进 wheel |
| 打包发布 | PEP 621 wheel（内含前端产物）· Docker 镜像 · npm 启动器 | 三种装法共用同一份后端与同一套启动器（`python -m app`） |
| 质量门禁 | 后端 1200+ 用例 · 契约域对账用例 · 前端 12 步 UI 门禁（真实 Chromium） | 判据是代码的一部分：改坏了会红，改判据本身也要带负对照 |

**打印相关的取舍**：纸面尺寸、页边距、每行题数、行距、字号都写进 CSS 变量，卷面 HTML 与预览共用；
容量守卫（放不下就拒绝并给建议）是第二层，第一层是 `break-inside: avoid` 让题不成两半。

> 徽标由 [shields.io](https://shields.io/) 现取现画：版本号、CI 状态、npm 版本都是按当前仓库状态
> 渲染的，发新版本时不必手改徽标。

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
| [`docs/00-决策记录.md`](docs/00-决策记录.md) | 全部工程决策（D1–D86）与理由 |

课程提交材料（Word 打开后按 **F9 更新目录**；配图源文件在 [`docs/word/figures/`](docs/word/figures/)）：

| 文档 | Word | PDF |
| --- | --- | --- |
| 需求分析文档 | [`01-需求分析文档.docx`](docs/word/01-需求分析文档.docx) | [PDF](docs/word/01-需求分析文档.pdf) |
| 技术设计文档 | [`02-技术设计文档.docx`](docs/word/02-技术设计文档.docx) | [PDF](docs/word/02-技术设计文档.pdf) |
| 测试文档 | [`03-测试文档.docx`](docs/word/03-测试文档.docx) | [PDF](docs/word/03-测试文档.pdf) |

---

## 许可

[MIT](LICENSE) © 2026 肖铭皓、胡宇书
