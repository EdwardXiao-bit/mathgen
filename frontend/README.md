# mathgen 老师端前端（M2 · T-M2-FE）

React 18 + TypeScript + Vite + TanStack Query；样式是**原生 CSS + CSS 变量**（D18，未引任何 UI 框架）。
老师端只覆盖契约 §7 中属于 M2 的四条路由。

| 路由 | 页面 | 状态 |
| --- | --- | --- |
| `/teacher` | 出题配置：六维表单 | 完成（第六维只读，M3 起生效） |
| `/teacher/paper/:id` | 预览与改题 + 排版面板六项 + 打印 | 完成（卷面 iframe 装后端 HTML，D56） |
| `/teacher/paper/:id/publish` | 发卷（卷号 + 二维码） | 最小闭环（接口已具备） |
| `/teacher/library` | 卷库 | 列表 + 打开（只读） |
| `/teacher/paper/:id/attempts` | 成绩列表 | **未做**（属 M3，不在本任务卡范围） |

## 常用命令

```powershell
$NODE = 'C:\Users\Ed\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
$PNPM = 'C:\Users\Ed\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs'

& $NODE $PNPM --dir frontend install    # 装依赖
& $NODE $PNPM --dir frontend build      # tsc -b && vite build → frontend/dist/
& $NODE $PNPM --dir frontend dev        # dev server 5173，/api 代理到 127.0.0.1:8000
& $NODE $PNPM --dir frontend verify     # 跑全部验收（见下）
```

## 五条验收脚本（都可复现，退出码 0 = 通过）

```powershell
& $NODE $PNPM --dir frontend verify                       # 一次跑完五条
# 或分开跑：
& $NODE frontend\scripts\verify-no-cdn.mjs
& backend\.venv\Scripts\python.exe frontend\scripts\verify-contract.py
& $NODE frontend\scripts\verify-layout-css-vars.mjs
& $NODE frontend\scripts\verify-teacher-ui.mjs
& $NODE frontend\scripts\verify-library-m3.mjs
```

| 脚本 | 验证什么 | 怎么保证不是「自证」 |
| --- | --- | --- |
| `verify-no-cdn.mjs` | 产物不引用外部 CDN（NFR-9） | **位置感知**：只有出现在**资源位**（`src=` / `href=` / `url()` / `@import` / `fetch()` / `import()` …）的绝对 URL 才算违规；普通字符串常量与提示文案里的 URL 逐条打印为 `[TEXT]` 但**不计失败**。判据写在脚本头注释里 |
| `verify-contract.py` | 前端 TS 类型 ↔ `backend/app/contracts/api.py` 的**每个模型逐字段**（含 M3 新增的 `AnswerLayoutIn` 与 `answer_layout` / `limit` / `offset`）、`ROUTES` 键值（含 `by-code` / `answer-layout`）、`layout.py` 的 CSS 变量名与取值范围；并断言前端不出现裸 `/api` 字面量 | 用 Pydantic **真加载** `api.py` 取字段，不是正则对着注释猜 |
| `verify-layout-css-vars.mjs` | 排版面板六个控件**实时**改变 **iframe 文档**里的 CSS 变量；打印取的是后端卷面 | 真实产物 + 模拟后端（含 `/sheet` 路由），Playwright 读 `iframe.contentDocument`；断言调参期间 **0 次**读卷/读卷面 |
| `verify-teacher-ui.mjs` | 六维表单选项 = D16/D17、**第六维四个开关可保存**（M3 起不再是只读占位）、打印的是后端卷面、后端未就绪三种形态都不白屏 | 三个场景各跑一遍：后端可用 / 代理 502 / 后端 500 |
| `verify-library-m3.mjs` | M3 卷库：**分页**（20→17、区间文案、末页禁用、offset 真的发给后端）、**搜索**（20→12、带 `q`）、**状态筛选**、**按卷号打开成/败两条路径**（失败给明确提示且不跳转）、**第六维保存**真的 `PATCH .../answer-layout` 且 body 是平铺的 `AnswerLayoutIn`、已发布卷只读 | 断言同时看**界面**与**后端请求流水**（`/__api-log`）：只看界面的话，前端本地切片也能"看起来分页了" |

> **端口自适应**：本机 4173 常被别的服务占用（老师端预览、样张静态服务）。服务进程会自己退让到
> 4174…，并把**真实端口**打印成 `VERIFY_PORTS static=… api=…`，父脚本解析后用它渲染
> Playwright 脚本。**不要**在父进程里把端口写成常量：实测踩过「服务在 4174、脚本仍去 4173」，
> 结果 Playwright 打开的是**别人的应用**，报错却指向选择器超时。

> 另有一条**真后端**验收（不随 `verify` 一起跑，因为它需要起两个服务）：
> 用 Playwright 打开卷页，断言 iframe 里的产物带 `meta[name=generator]="mathgen render_sheet"`
> 与 `layout-fingerprint`，改面板后真卷面的 `--cols-per-row` / `--font-size` 跟着变、
> 刷新后仍是已落库的值、答案版页签拿到含 `.answer-section` 的后端卷面。
> 本轮实测 13/13 通过。
>
> **M3 联调实测**（前端产物 × 真实后端 `uvicorn`，466 套卷）：卷库列出 20 行、翻页后按卷 id
> 无交集、搜索把总数从 466 改到 440、不存在的卷号给明确提示且不跳转、第六维拨开关 →
> `PATCH` 成功 → **刷新后仍是新值**（真的落库）、无 pageerror，**10/10 通过**。

> `verify-server.mjs` 是被上面几个脚本 spawn 的**独立进程**服务。独立进程不是洁癖：
> 驱动 Playwright 用的 `spawnSync` 会阻塞 Node 事件循环，服务若同进程则连 `connection` 事件都派发不到
> （实测：Chromium 连接超时，服务端一条请求日志都没有）。
>
> `verify-server.mjs` 里 **body 只读一次**（`createServer` 统一读完放 `req.parsedBody`）。
> 各路由**不要再挂 `req.on('data')`**：流已被消费，重复监听会让请求永远等不到 `end`，
> 表现是前端保存状态卡在 `saving`（M3 实测踩过）。

## 几个必须知道的实现约定

### 1. 排版面板：本地状态驱动预览，PATCH 负责持久化 + 重载

```
拖动控件 → 覆盖 iframe 文档根元素上的六个 CSS 变量 → 预览立刻变（0 次网络请求）
         ↘ debounce 400ms → PATCH /api/papers/{id}/layout
                              → 成功：状态='refreshing' + reloadToken++
                              → iframe 重新加载后端 HTML → onReloaded → 状态='已保存，预览已刷新'
```

### 2. 卷面只有一套渲染实现：后端 `app/render/`（D56）

前端**不画任何卷面 DOM、不写任何卷面 CSS**。预览是一个 `<iframe id="sheet-frame">`，
src 指向 `GET /api/papers/{id}/sheet?mode=question|answer`；打印也是取同一个接口的 HTML。
于是「预览 = 打印 = PDF」在**结构上**成立，而不是靠两套 CSS 碰巧看起来一样。

面板的实时反馈这样实现：改动时把六个 CSS 变量写到 **iframe 文档的 `documentElement`** 上
（`SheetFrame.tsx` 的 `applyLayoutToDocument`）。这只是覆盖后端已经写进 `:root` 的那几个变量，
既即时（0 次请求），也没有引入第二套版式逻辑。

打印流程（`PaperPage.handlePrint`）：

```
flush 待写入的排版（PATCH，让后端渲染时读到最新值）
  → fetch /sheet?mode=question|answer 拿 HTML
  → document.write 进隐藏 iframe（.print-frame）
  → 轮询 readyState 完成后 iframe.contentWindow.print()
```

> **踩过的坑**：不要「先 `srcdoc=''` 再设 `src`」——iframe 会停在那个空文档上，
> 打印出来是一张白纸（实测：`contentDocument` 里既无 `.sheet` 也无 `.q-item`）。
> 改成自己取 HTML 再 `document.write`，时序完全可控。

### 3. 保存成功后重新加载卷面（D59 兜底，很重要）

服务端渲染的 HTML 是**加载时定格**的。只要有任何一个参数不是由 CSS 变量驱动，
「拖动时覆盖变量」这条通路就会让预览与后端不一致 —— 而界面还显示「已保存」，比直接报错更糟。
（历史上 `answer_line` / `header_bar` 就是这样：面板写的变量在渲染器样式表里没有规则读取。）

所以 `PATCH /layout` **成功返回后**自增 `sheetReloadToken`，让 iframe 重新加载后端最新 HTML；
完成后状态才推进到「已保存，预览已刷新（当前显示 = 后端渲染结果）」。

- 拖动过程中**不重载**（手感不被破坏）。回归断言：`GET /sheet` 次数 == `PATCH /layout` 次数
- 对应回归用例在 `verify-layout-css-vars.mjs` 的「D59 回归」段：开关关掉 → 保存 → 重载后
  卷面里 `.q-blank` / `.header-bar` 的实际 `display` 必须是 `none`；再打开必须是 `block` / `grid`

### 4. 两个开关走 `display` 变量（D59 契约变更）

`--answer-line-display: block|none`、`--header-bar-display: grid|none`（不再是 `1|0`），
渲染器样式表用 `display: var(--…-display, 默认值)` 真正消费它们 ——
服务端渲染与面板实时覆盖因此走同一条通路。`verify-contract.py` 会逐字比对前端
`CSS_DISPLAY_VALUES` 与后端 `css_variables()` 的取值。

面板文案只说人话（「保留 / 不留」「显示 / 隐藏」），变量名与关键字降级为调试小字。

### 5. 第六维「答案版形式」——M3 起真的可保存

`paper.answer_layout` 在 M3 正式接入，字段即契约的 `AnswerLayoutIn` 四个布尔：
**答案单独成页 / 含逐步过程 / 含难度 / 含考点标签**（默认全 `true`，即 D20 默认）。

- 组件 `src/teacher/AnswerLayoutEditor.tsx` 被**两处**复用：出题配置页（`/teacher`）与卷页
  （`/teacher/paper/:id`）。两处若各写一遍，字段名/文案/默认值必然漂移——这是本项目已发生过的失效模式。
- 保存走 `PATCH /api/papers/{id}/answer-layout`，body 是**平铺的** `AnswerLayoutIn`
  （与 D55 的排版一致，不包一层）。
- **创建时怎么落库**：`AnswerLayoutIn` 不是 `PaperConfigIn` 的字段，按契约不该出现在创建请求体里；
  但用户在创建表单上已经勾了开关，丢掉就是「选了不生效」。所以 `createPaper` 先尝试带在创建请求里，
  被拒（422）则退回「创建后补一次 `PATCH`」，并把走的是哪条路径回传，界面如实显示。
- **已发布卷**：后端返回 **409 `PAPER_NOT_EDITABLE`**。界面把四个开关禁用并写明
  「已发布 · 不可改 / 需要调整请先另存为可编辑副本」——**不允许静默失败**（D59 的教训）。
- 保存成功后会让答案版预览重新加载（iframe `reloadToken`），确保预览 = 后端当前渲染结果。

### 6. 卷库（M3）：分页 / 搜索 / 筛选 / 按卷号

- 列表走契约的 `limit` / `offset` / `q` / `status`。`q` 与 `status` 变化会**回到第 1 页**
  （否则会停在「第 3 页但结果只有 5 条」的空页上）。
- 搜索输入**防抖 300ms**（输入即查，但不每敲一个字符打一次请求）。
- **按卷号打开**走 `ROUTES.get_paper_by_code`，**不是**「翻列表找卷号」：列表是分页 + 带筛选的，
  用卷号去列表里搜在过滤条件不匹配时会「明明存在却找不到」。
- 失败提示**分两类**：卷号不存在（404）说「没有找到卷号…」且**留在卷库页**；
  后端连不上说「暂时连不上后端」。混在一起会把老师引向错误方向。
- **形状自适应**：契约参数是 `limit`/`offset`，而 M2 的后端曾是 `page`/`size`。两者语义不同，
  且后端对未知查询参数通常是**忽略**——盲目发 `limit` 会拿到「永远是第 1 页的 20 条」。
  所以 `listPapers` 先探测一次（`limit=1`，判据是**响应回显**而不是「请求没报错」），
  不匹配才回退 `page`/`size`，并把模式回传（命中的是旧形态时界面会显示「后端仍是 page/size 分页，前端已换算」）。
  探测结果按 origin 缓存，不每次多打一发。**后端已支持新参数后，这段回退是死代码但无害。**

### 7. `--page-margin` 在屏幕上的可见性取决于渲染器

该变量只进 `@page { margin }`（打印 / PDF 一定生效）。屏幕预览能否看出变化，
取决于卷面样式表有没有给一段 `@media screen` 的 A4 纸面留白（render-export 那边在做）。
面板上为此挂了「主要影响打印与 PDF」的说明 —— 不让这一项显得像坏了。

## 排版参数的唯一来源

六个参数的字段名、CSS 变量名、取值范围全部集中在 `src/shared/api.ts`：

- `CSS_VAR_NAMES` / `LAYOUT_BOUNDS` / `LAYOUT_DEFAULTS` —— 与 `backend/app/contracts/layout.py` 对应
- `LAYOUT_CONTROLS`（`src/teacher/LayoutPanel.tsx`）—— 六个控件由它生成，控件 id 就是字段名
- `cssVariables(layout)` —— 与后端 `css_variables()` 输出**逐字相同**（含 `pt` / `cm` 单位与 `1`/`0` 布尔）

改契约时只要改 `api.ts`，面板与预览不会各自漂移；`verify-contract.py` 会拦住漏改。

> 注意一个**已知且可接受**的差别：面板实时覆盖的是六个 CSS 变量，而卷面里
> `.sheet[data-answer-line]` 这类**数据属性**是服务端渲染时写下的，不会跟着实时变。
> 打印 / PDF 走后端重新渲染，因此拿到的属性永远是最新的——判据以冻结契约
> `css_variables()` 定义的六个变量为准（验收脚本按此断言）。

## 目录

```
src/
  shared/          类型 + API 客户端 + query key（老师端与学生端唯一共享层，契约 §8 D-5）
    api.ts         冻结契约的镜像（字段名 snake_case，禁止驼峰映射）
    apiClient.ts   fetch 封装：统一错误结构、超时、可读的中文提示、分页形状自适应
  teacher/         老师端页面（不 import student/**）
    ConfigForm.tsx           六维表单（D16 档位 / D17 题型；第六维可保存，M3）
    AnswerLayoutEditor.tsx   第六维四开关（配置页与卷页复用同一份语义）
    SheetFrame.tsx           卷面 iframe 容器 + 六个变量的实时注入（D56）
    LayoutPanel.tsx          排版面板六控件
    pages/                   ConfigPage / PaperPage / LibraryPage / PublishPage（LibraryPage 含 M3 分页/搜索/按卷号）
scripts/           验收脚本（见上）
```
