# mathgen

**小学四则运算练习题生成与批改系统** —— 跑在老师本机 / 教室内网机器上的网站。

- **老师端**：六维参数出题 → 生成 → 预览改题 → 排版可调 → 打印 A4 / 导出 PDF / Word；发卷（二维码 + 卷号）；看成绩与逐题明细。
- **学生端**：局域网扫码进入，输入姓名 + 学号，自主练习或做老师发的卷，提交后立刻判分。

> 课程：现代软件工程课程设计 ｜ 指导教师：罗以宁 ｜ 成员：肖铭皓、胡宇书
> 技术栈：Python FastAPI + SQLite + React/Vite ｜ 完全离线可用，不依赖公网

## 当前状态

**需求已冻结，技术设计完成，代码尚未开始。** 进度见 [`docs/PROJECT.md`](docs/PROJECT.md) §5。

## 从哪里开始读

| 你是 | 先读 |
| --- | --- |
| 想了解这个项目 | [`docs/PROJECT.md`](docs/PROJECT.md)（项目总览） |
| 要参与开发 | [`AGENTS.md`](AGENTS.md)（红线与导航）→ [`docs/04-接口契约.md`](docs/04-接口契约.md) → [`docs/05-任务板.md`](docs/05-任务板.md) |
| 关心需求 / 设计 | [`docs/01-需求分析.md`](docs/01-需求分析.md)、[`docs/02-技术设计.md`](docs/02-技术设计.md) |
| 想知道某个决定怎么来的 | [`docs/00-决策记录.md`](docs/00-决策记录.md) |

## 课程提交材料

| 文档 | Word | PDF |
| --- | --- | --- |
| 需求分析文档 | [`01-需求分析文档.docx`](docs/word/01-需求分析文档.docx) | [PDF](docs/word/01-需求分析文档.pdf) |
| 技术设计文档 | [`02-技术设计文档.docx`](docs/word/02-技术设计文档.docx) | [PDF](docs/word/02-技术设计文档.pdf) |
| 测试文档 | 待产出（M7） | — |

> Word 打开后按 **F9 更新目录**。配图源文件在 [`docs/word/figures/`](docs/word/figures/)。

## 重新生成文档

内容的事实来源是 `docs/*.md`；Word 是派生产物，**不要直接编辑 docx**。

```powershell
$PY = 'C:\Users\Ed\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
& $PY tools\make_diagrams.py    # 重新生成 10 张配图
& $PY tools\build_docs.py       # md -> docx（封面 / 目录 / 图表题注）
```

## 目录结构

```
mathgen/
├─ AGENTS.md        入口与红线（智能体自动加载）
├─ README.md        本文件
├─ docs/            项目文档（00 决策 / 01 需求 / 02 设计 / 03–06 协作 / word 课程交付）
├─ tools/           文档与配图构建脚本
├─ backend/         未创建
└─ frontend/        未创建
```
