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

## 九条验收脚本 + 一套负对照（都可复现，退出码 0 = 通过）

```powershell
& $NODE $PNPM --dir frontend verify                       # 一次跑完 9 条门禁 + 负对照
# 或分开跑（package.json 里都有对应 script）：
& $NODE frontend\scripts\verify-no-cdn.mjs
& backend\.venv\Scripts\python.exe frontend\scripts\verify-contract.py
& $NODE frontend\scripts\verify-layout-css-vars.mjs
& $NODE frontend\scripts\verify-teacher-ui.mjs
& $NODE frontend\scripts\verify-library-m3.mjs
& $NODE frontend\scripts\verify-tokens.mjs
& $NODE frontend\scripts\verify-structure.mjs
& $NODE frontend\scripts\verify-preview-scale.mjs
& $NODE frontend\scripts\verify-ui-voice.mjs
# 负对照单独一条（也可用 pnpm --dir frontend verify:negative）：
& $NODE frontend\scripts\verify-negative-controls.mjs
```

| 脚本 | 验证什么 | 怎么保证不是「自证」 |
| --- | --- | --- |
| `verify-no-cdn.mjs` | 产物不引用外部 CDN（NFR-9） | **位置感知**：只有出现在**资源位**（`src=` / `href=` / `url()` / `@import` / `fetch()` / `import()` …）的绝对 URL 才算违规；普通字符串常量与提示文案里的 URL 逐条打印为 `[TEXT]` 但**不计失败**。判据写在脚本头注释里 |
| `verify-contract.py` | 前端 TS 类型 ↔ `backend/app/contracts/api.py` 的**每个模型逐字段**（含 M3 新增的 `AnswerLayoutIn` 与 `answer_layout` / `limit` / `offset`）、`ROUTES` 键值（含 `by-code` / `answer-layout`）、`layout.py` 的 CSS 变量名与取值范围；并断言前端不出现裸 `/api` 字面量 | 用 Pydantic **真加载** `api.py` 取字段，不是正则对着注释猜 |
| `verify-layout-css-vars.mjs` | 排版面板六个控件**实时**改变 **iframe 文档**里的 CSS 变量；打印取的是后端卷面 | 真实产物 + 模拟后端（含 `/sheet` 路由），Playwright 读 `iframe.contentDocument`；断言调参期间 **0 次**读卷/读卷面 |
| `verify-teacher-ui.mjs` | 六维表单选项 = D16/D17、**第六维四个开关可保存**（M3 起不再是只读占位）、打印的是后端卷面、后端未就绪三种形态都不白屏 | 三个场景各跑一遍：后端可用 / 代理 502 / 后端 500 |
| `verify-library-m3.mjs` | M3 卷库：**分页**（20→17、区间文案、末页禁用、offset 真的发给后端）、**搜索**（20→12、带 `q`）、**状态筛选**、**按卷号打开成/败两条路径**（失败给明确提示且不跳转）、**第六维保存**真的 `PATCH .../answer-layout` 且 body 是平铺的 `AnswerLayoutIn`、已发布卷只读 | 断言同时看**界面**与**后端请求流水**（`/__api-log`）：只看界面的话，前端本地切片也能"看起来分页了" |
| `verify-tokens.mjs` | **判据 A1**：`frontend/src/**` 除 `design/tokens.css` 外，颜色字面量计数 = 0 | 脚本**逐行扫源码**并打印命中行；允许清单是**逐条**的（带文件+行号+理由），当前为空。注释里也刻意不出现颜色写法，否则会被它自己命中 |
| `verify-structure.mjs` | **判据 A2/A3/A4/A7/A8**：组件必备状态（样式表规则 + 运行时测 `disabled` / `invalid` 文字原因 / Slider 量纲 / Switch 人话）、键盘可达与焦点环、状态条不谎报、空态与错误态分离、浮动层几何（不互压、不压纸面）、两个视口不横向溢出、纸面 A4 比例、顶栏 44 / 状态条 34 / 1280 侧栏收窄 | 交互类断言用 Playwright 真点真读（`data-state`、`aria-*`、`getBoundingClientRect`），不是看源码里有没写某个 class |
| `verify-preview-scale.mjs` | **A9**：预览必须是**打印几何的等比缩小**。进到 iframe 文档里量：`.sheet` 计算宽度 = 793.7px ± 2（210mm@96dpi）、`padding-left/宽度 ≈ 0.0952`、`scaleX == scaleY`、题块高度一致（= 不换行）、两种视口下纸内几何不变 | **实测**从渲染结果反推缩放比（`rect.width / offsetWidth`），而不是读我们自己写进去的内联 `scale(k)` —— 后者只证明「我们写了 k」，不证明「浏览器按 k 渲染了」 |
| `verify-ui-voice.mjs` | **视觉轮 A10/A11**（D70）：① 界面**可见**文本里不得出现决策编号（`D\d+`）、CSS 变量名（`--xxx`）、裸 CSS 关键字（`none/block/grid/flex/inline`）、需求编号（`FR-xx`）、内部实现痕迹；② 卷库/配置/发布页的**计算后底色**必须是浅底令牌（近白浅灰），灰桌面底只允许出现在画布 | ① 扫的是**渲染后可见**的文本节点（`getBoundingClientRect` 非零 + 祖先可见），不是源码字符串 —— 源码里全是决策编号注释，源码扫法会立刻失效；`display:none` 的验收 dump 因此不计入 ② 读的是**计算后颜色**并与令牌值比对，不是看 CSS 里写了哪个变量名 |
| `verify-negative-controls.mjs` | **V9 负对照**：故意注入错误状态，断言判据**必须变红**。覆盖 A9（注入 3cm 边距 → 比例判据红；把纸钳制到 459px → 宽度判据红）、卷面焦点环（未聚焦 → 新判据红、旧写法仍绿）、A10（注入含决策编号/CSS 变量的可见文本 → 红；改成 `display:none` → 不报）、A11（把配置页底色改成灰桌面 → 红） | 每条负对照都额外断言**注入真的改变了关键量**（V9.2）——否则「判据没红」会被误读成「判据有问题」，而真相可能是「注入没生效」 |

> **A3 焦点环（task-19 更正）**：环画在**包裹层 `.mg-paper-stage` 的 `:focus-visible`** 上，
> 该层带 `tabindex="0"`，iframe 设 `tabindex="-1"` 退出 Tab 顺序。
> **不要改回 `:focus-within`**：Chromium 不把 `iframe` 当作 `:focus` / `:focus-within` 的触发者 ——
> 实测程序化 `.focus()` 时 `:focus-within` 会命中，但**真实 Tab 键走到 iframe 时不会**
> （`iframe.matches(':focus') === false`），键盘用户会 Tab 到一个没有任何焦点指示的死停靠点。
> 现在卷面这一站由包裹层正常承担，所以「每个 Tab 停靠点都要有可见环」可以**无豁免**成立
> （33/33，比原先带 iframe 豁免的版本更强）。

> **V9 · 断言必须能失败（负对照）**：任何新断言都要举出一个让它失败的输入，并在报告里给出实测结果。
> 教训来自 task-17：我曾把 `(stage.outlineWidth ?? 0) > 0` 说成「功能断言、判据更强」——
> 它其实**恒真**：`outline-width` 的 CSS 初值就是 `medium` = 3px，**与是否聚焦无关**
> （实测未聚焦元素同样读到 `outlineStyle: none` + `outlineWidth: 3px`）。
>
> **V9.1 · 负对照要留在仓库里**：报告会过期，脚本不会。
> 上面那次我确实跑了负对照，但它只活在一次性探针里，汇报的原始输出在仓库中 grep 不到 —— 别人无法复核。
> 现在集中在 `scripts/verify-negative-controls.mjs`，可随时重跑（`pnpm --dir frontend verify:negative`）。
>
> **V9.2 · 负对照本身也要被验证**：要断言「注入真的改变了关键量」。
> 这也是实测换来的：A9 的负对照我最初注入 `.sheet { width: 100% }`，**判据没红** ——
> 但那不是判据有问题，而是**注入没生效**（iframe 内容视口已是 793.7px，`100%` 解析出来还是 793.7px）。
> 必须显式给一个比 A4 窄的固定宽度才重现出「被钳制」。
> 把这个坑写在脚本注释里，免得后人以为「注入没生效 = 判据没问题」。

> **空态与错误态是两个状态，不共用槽位**（Lead 裁决）。`Table` 有独立的 `emptyAction` 与 `errorAction`：
> 后者一旦传入，wrapper 的 `data-state` 就是 `error`。把 `ErrorState` 塞进 `emptyAction` 会让一个**错误**
> 场景对外宣称「没有数据」，那是语义层的小谎报 —— 与 D59（界面谎报已保存）、D65（`?page=2` 静默拿到第一页）
> 同一条原则。门禁⑦为此**另起一个 `MATHGEN_VERIFY_MODE=api-down` 的服务**，断言后端 500 时
> `data-state === 'error'` 且只渲染 `.mg-table-error`、不渲染 `.mg-table-empty`。

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

### M3.5 · 结构轮：令牌 + 组件层 + C1 骨架

三件事，纪律优先于观感（观感留给视觉轮）：

1. **设计令牌**在 `src/design/tokens.css`（**Lead 冻结，实现方只读**，D67）。
   `src/**` 里任何颜色/间距/字号/圆角/阴影都必须写 `var(--mg-*)`。
   判据 A1 由 `verify-tokens.mjs` 机器扫描；**缺令牌要向 Lead 申请，不要本地加**。
2. **组件层**在 `src/ui/**`：`Button` / `IconButton` / `Input` / `NumberInput` / `Checkbox` /
   `Switch` / `Slider` / `SegmentedControl` / `List` / `Table` / `Card` / `FloatingPanel` /
   `Toolbar` / `StatusBar` / `Badge` / `Chip` / `EmptyState` / `ErrorState` / `Toast`。
   每个组件在 DOM 上带 `data-state`，状态样式只引用令牌 —— 这是判据 A2 能被机器检查的前提。
   两个 API 是**刻意「不好用」**的：
   - `Input` 的校验失败要传 `invalidReason: string`（不是 `invalid: boolean`），
     从接口层面杜绝「只变红不说原因」（§4）；
   - `ErrorState` 必须同时给 `what`（发生了什么）与 `how`（怎么办），
     从接口层面杜绝 M2 那种「只有一句 TimeoutError」（I3）。
3. **骨架取 C1「工作台」**：顶栏 44px + 三栏（侧栏 `--mg-side-w` / 画布 / 面板）+ 状态条 34px；
   纸面按 A4 真实比例 `210/297` 居中缩放；1280 宽侧栏收窄到 `--mg-side-w-narrow`。

两条与骨架有关的实现选择，都不是「随手这么写」：

- **浮动工具栏用文档流占位，不用绝对定位**。工具栏是画布列的**第一行**，
  于是「不压住纸面」是布局的自然结果（判据 A7 的几何断言直接量 `toolbar.b <= paper.t`），
  而不是靠调 `top` 数值凑出来 —— 样张初版就是 `position: absolute` 把工具栏压在纸面上（D62 实测缺陷）。
- **验收用的 CSS 变量 dump 默认收起、并放在右侧栏**。它常开时占约 160px 高，
  而纸面按剩余高度缩放，结果 A4 纸被压到 329×465；挪到侧栏并收起后是 459×650。
  门禁③读的是它的 `textContent`（不是可见性），所以收起不改变那条断言的语义。

### M3.5 · 视觉轮：灰底归位 / 内容列居中 / 清掉开发者文本（D70）

用户看图后的逐条反馈，只做了下面这些（**没顺手做别的**）：

1. **灰桌面底只属于画布**。`--mg-desk-*` 那一族（中灰）只允许出现在**卷页中间的画布**上；
   卷库页 / 配置页 / 发布页改用 `--mg-surface-sunken`（近白浅灰）。
   原来 `body` 就是灰桌面底，于是列表页变成「白卡片浮在灰桌面上」——用户说这条"确实严重"。
   `body` 现在是浅底，画布自己带灰底 + 环境光 + 网格（C1/C2 质感不回归）。
2. **配置页内容居中**。三个页面的根是 flex 列 + `align-items: center`，各内容块自带 `max-width`
   （配置/发布 880，卷库 1180）。**同时**修了两个连带缺陷：
   - 卷库翻页条（`position: sticky`）被"一刀切"的 `width:100%` 子元素规则撑变形、压到表格上；
   - `.mg-table-wrap` 作为 flex 子项时 `min-width:auto` 会收缩到表格 min-content 宽度
     （实测 528.8px），且会被压到视口剩余高度导致翻页条**叠住最后一行**。
     组件层因此固定 `width:100%; min-width:0; flex:none`（注释里写了原因）。
3. **清掉面向开发者的文本**（用户单独点名）。改前实测有 7 处可见的 `--cols-per-row` 之类调试小字，
   以及 `（D56）`、`（FR-T10 / D23）`、`uvicorn app.main:app …` 这类内部痕迹。全部换成老师能懂的一句话。
   - 排版面板每项下方的变量名小字**整段删掉**（原先 Lead 要求它"证明在工作"，用户判定不该出现在产品里 —— 以用户为准）。
   - 六个变量名仍写进**对用户不可见**的验收 dump（`display:none`，门禁③读它的 `textContent`），
     所以「可核对性」没有被削弱，而门禁④那条断言也相应改成
     「可见处必须没有 + 非可见处必须有」——**整体更强，不是放宽**。
   - 错误提示统一走 `shared/errorText.ts` 的 `readError()`（原先三个页面各写一遍），
     口径是"说人话 + 错误码单列"，不再把服务端原文怼给老师。
4. **明确不做**：纸面偏小（题目多时自然不空）、库表表头与数字列对齐（用户判定非问题）、
   纸下半空白（用户决定保留完整 A4，不做"纸高随内容收缩"）。

> 判据 A10/A11 由 `verify-ui-voice.mjs` 守，负对照见 `verify-negative-controls.mjs`。
> 特别注意 A10 **扫的是渲染后的可见文本**：源码里到处是 `D56` 之类的注释，扫源码会立刻失效。

### M3.5 · A9：预览是「等比缩小」，不是「把 A4 横向压扁」（D68）


后端的屏幕 CSS 是**已冻结**的（D56）：

```css
@media screen { .sheet { width: 210mm; max-width: 100%; padding: var(--page-margin) } }
```

如果 iframe 的内容视口只有容器那么宽（例如 459px），`max-width: 100%` 会把这张
「210mm 的纸」**横向钳制**到 459px，而 `padding`（2cm=75.59px）与 `font-size`（14pt）
仍是**绝对长度**。于是内容区只剩 `459−2×75.59≈308px`，4 列每列 77px，
而 `243 × 12 ____` 需要约 118px → **题面必然异常换行**。
屏幕上是乱的、打印出来却是好的 —— 正是 C3 要防的「预览 ≠ 打印」。

修法（**只在前端**，一行后端 CSS 都没动）：

1. 让 **iframe 的内容视口就是真实 A4**（内联 `793.7×1122.5px`）。于是 `.sheet` 的计算宽度
   就是 793.7px，`max-width:100%` 不再触发钳制，**纸内一切几何与打印逐像素一致**。
2. 再由外层整体 `transform: scale(k)` 缩到画布大小，
   `k = min(可用宽/793.7, 可用高/1122.5)` —— **宽高同一个 k**，这就是「等比」的定义。
3. `SheetFrame` 用 `ResizeObserver` 在 `useLayoutEffect` 里算 k（首帧就位，避免先铺满再缩小）；
   测量层 `.mg-paper-stage` 与缩放层分开，**避免「量到自己缩放后的尺寸」形成反馈循环**。

修前/修后（1440×900，实测）：

| 指标 | 修前（压扁） | 修后（等比） |
| --- | --- | --- |
| iframe 内 `.sheet` 计算宽度 | 459px | **793.688px**（=210mm） |
| `padding-left / 宽度` | 0.1647 | **0.0952** |
| 4 列每列宽度 | 76.95px | **160.63px** |
| 题块高度集合（>1 行会分叉） | —（内容区不够，算式折行） | **{26}** 全部单行 |
| `.sheet` 矩形比例 | 1.0273（横向扁） | 内容自然高度，**几何 = 打印** |

顺带一条可复用的教训：**判据 A1–A8 全绿也抓不到这个缺陷**，因为没有任何一条量到「纸里面」。
A1 只管颜色、A2 只管组件状态、A4 只管纸面**容器**比例、A7 只管浮动层几何、A8 只管不溢出。
所以门禁⑧ 的做法是**进 iframe 文档内部**量 —— 这也是它能抓到这类缺陷的唯一原因。

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
  design/
    tokens.css     设计令牌唯一来源（**Lead 冻结，只读**；改它要新增决策 D67）
  ui/              M3.5 组件层（状态统一、只引用令牌；不含任何颜色字面量）
    Button / Input / Checkbox / Switch / Slider / SegmentedControl / List / Table
    Panel（Card + FloatingPanel）/ Toolbar / StatusBar / Badge / State / Toast
  shared/          类型 + API 客户端 + query key（老师端与学生端唯一共享层，契约 §8 D-5）
    api.ts         冻结契约的镜像（字段名 snake_case，禁止驼峰映射）
    apiClient.ts   fetch 封装：统一错误结构、超时、可读的中文提示、分页形状自适应
  teacher/         老师端页面（不 import student/**）
    TeacherShell.tsx         外壳：顶栏 44px + 路由出口（状态条由卷页渲染，避免两条）
    ConfigForm.tsx           六维表单（D16 档位 / D17 题型；第六维可保存，M3）
    AnswerLayoutEditor.tsx   第六维四开关（配置页与卷页复用同一份语义）
    SheetFrame.tsx           卷面 iframe 容器 + 六个变量的实时注入（D56）
    LayoutPanel.tsx          排版面板六控件（走组件层 Slider / Switch）
    pages/                   ConfigPage / PaperPage（C1 三栏工作台）/ LibraryPage / PublishPage
scripts/           验收脚本（见上）
```
