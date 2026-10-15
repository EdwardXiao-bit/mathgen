# task-22 六要素报告：门禁 `asyncio loop` 冲突（12 条 errors）修复

> 角色：独立验证者 `verify-m1`（证伪 not 配合） · 日期：2026-10-15
> 写域：`backend/tests/e2e_ui/**`（**未触碰 `backend/app/**` 与 `frontend/src/design`**）
> 落盘位置说明：本报告写在 verify 自己的写域内；`docs/05-任务板.md` 按 `AGENTS.md`「共享文件写者」表**仅 Lead 可改**，
> 故在 §6 给出**可直接粘贴**的段落，由 Lead 落到 §3。

---

## 1. 任务 ID

**task-22**（团队任务板 id；M3.5 修复轮）——「`e2e_ui` 12 处裸 `sync_playwright()` 改用产品浏览器通道」。
领取时间 2026-10-15，状态：**修复完成，待验收**（owner `verify-m1`，revision 2）。

**现象（Lead 交派时的原始证据）**：`& $VPY -m unittest discover -s backend/tests -t backend`
→ `Ran 682 tests` / `FAILED (errors=12)` / `exit=1`，12 条 ERROR 全在 `tests.e2e_ui.*`，
文本均为 `It looks like you are using Playwright Sync API inside the asyncio loop. Please use the Async API instead.`

## 2. 改动文件清单

| # | 文件 | 改动 | 行 |
| --- | --- | --- | --- |
| 1 | `backend/tests/e2e_ui/test_ui_structure.py` | 新增产品通道导入块 | 51（`from tests.e2e_m2._browser import browser_session, session  # noqa: E402`） |
| | | `browser_page(chan, viewport)` 改走产品通道（不再自己 launch） | 210 |
| | | 新增 AST 检测器 `bare_playwright_calls()` / 运行中循环探针 `running_loop_name()` | 234 / 263 |
| | | 5 处站点改 `with browser_session() as chan:` | 500、636、676、715（+737 类内 781） |
| | | 新增守卫用例类 `TestZPlaywrightChannelGuard`（3 用例） | 737 |
| 2 | `backend/tests/e2e_ui/test_ui_a9.py` | 导入 `browser_session` | 29 |
| | | 7 处站点改 `with browser_session() as chan:` | 205、259、298、316、353、397、488 |
| 3 | `backend/tests/e2e_ui/probe_a9.py` | 1 处站点改产品通道 | 170 |
| 4 | `backend/tests/e2e_ui/REPORT-task-22.md` | 本报告（新增） | — |

**13 处裸调用全部清零**：`test_ui_a9.py` 7 + `test_ui_structure.py` 5 + `probe_a9.py` 1。
全仓复检（`grep -n sync_playwright backend/**/*.py`）后，`e2e_ui` 下**只剩**：
注释/文档串里的说明文字，以及守卫用例里那**唯一一处故意为之**的裸调用（见 §5 豁免记账）。

## 3. 验证命令 + 原始输出

环境（不设则必假红，见 `AGENTS.md` 坑 2）：`PLAYWRIGHT_BROWSERS_PATH=<repo>\.playwright`、`TEMP=TMP=<repo>\.tmp-pw`；
`$VPY = C:\Users\Ed\dsh_workspace\mathgen\backend\.venv\Scripts\python.exe`；工作目录 `<repo>`。

### (1) 单套件（修后）

```
& $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -v
```
```
Ran 22 tests in 72.011s
OK
standalone exit=0        # asyncio loop 出现次数 = 0
```
（22 = 修复前 19 + 新增 3 条守卫用例；日志 `artifacts/standalone-AFTER.log`）

### (2) 全量门禁（修后，最终）

```
& $VPY -m unittest discover -s backend/tests -t backend
```
```
Ran 685 tests in 222.949s
OK
exit=0
asyncio loop 出现次数 = 0
```
（685 = 682 + 3 条新增；**未删除、未跳过任何用例**，`OK` 行无 `skipped=`；日志 `artifacts/full-AFTER.log`）

修复前对照（同一命令、同一环境、改动前工作区，`artifacts/full-BEFORE.log`）：
```
Ran 682 tests in 200.287s
FAILED (errors=12)
exit=1
asyncio loop 出现次数 = 12
```
12 条 ERROR 与 13 处调用点中的 12 处**一一对应**（`probe_a9.py` 不在套件内故不产生用例）。
运行耗时 200.3s → 222.9s（**+11%**）：原先 12 条用例是秒错，现在真的在跑浏览器。

## 4. 根因（实测取证，非推测）

**污染者**：`backend/tests/e2e_m2/test_m2_acceptance.py:302`
`TestLayoutToggles.test_d59_pdf_print_path_unaffected_by_screen_media` 走产品 PDF 路径，
会启动 `backend/app/export/browser.py:215` 的**进程级常驻会话** `_SESSION = _BrowserSession()`；
它的 Playwright dispatcher greenlet 让主线程长期处于「有运行中的 loop」状态，而
`playwright/sync_api/_context_manager.py:39-49` 的守卫是
`if self._loop.is_running(): raise Error("It looks like you are using Playwright Sync API inside the asyncio loop...")`
→ 其后任何**裸建** `PlaywrightContextManager` 必被拒。

**机理探针**（可重跑：`& $VPY backend/tests/e2e_ui/artifacts/probe_channel.py`）原始输出：
```
1) 启动前： no running loop (no running event loop)
2) 产品会话启动后：connected = True | ProactorEventLoop is_running=True
3) 裸 sync_playwright() 被拒：Error: It looks like you are using Playwright Sync API inside the asyncio loop. Please use the Async API in…
4) 同一状态下产品通道仍可用：connected = True
5) close() 之后： no running loop (no running event loop)
6) close() 之后裸 start = OK
```

**产品通道本就安全**（`backend/app/export/browser.py`）：
`browser()` 行 142-144 —— `if self._browser is not None and self._browser.is_connected(): return self._browser`（复用，不新建）；
需新建时失败会先 `self._playwright.stop()`（行 177-182，注释即写明「否则重试会报 Sync API inside the asyncio loop」）再退到
`backend/.tmp-playwright` 重试（行 184-187）。`browser_session()` 行 223-229 = `try: yield _SESSION finally: _SESSION.close()`。

⇒ **结论：不是测试不稳，是调用姿势错误。**12 条 errors 与「偶发」无关，与被写者改实现无关。

## 5. 负对照（判据必须能红，`03-协作规范.md` V9）

**① 留在仓库里的负对照（常驻）**
- `test_ui_structure.py:737` `TestZPlaywrightChannelGuard.test_detector_flags_a_synthetic_bare_call`
  —— 正对照：检测器必须抓住一段合成的裸调用（`bare_playwright_calls(...) == [(2, "")]`），否则「全绿」可能只是空转。
- 同文件 `test_bare_playwright_is_rejected_while_product_session_is_alive`
  —— 动态机理断言：产品会话活着时裸 `sync_playwright()` **必然**抛含 `asyncio loop` 的异常，
  且同一状态走产品通道能 `new_page` + `inner_text` 成功。
- 静态扫描的**唯一豁免**按「函数名 + 恰好 1 处」记账（`EXEMPT = {"test_bare_playwright_is_rejected_while_product_session_is_alive": 1}`），
  函数被改名/删除/处数变化都会让用例失败 → 无法靠改名扩豁免面。

**② 一次性负对照实验（临时插回裸调用 → 全量门禁重新变红 → 字节级还原）**

第一次尝试（`artifacts/full-NEGCTL.log`）**不可用，如实披露**：注入点落在 `TestA9PreviewGeometry`
（a9 类名字母序第 4 位）→ 在它之前已有用例用 `browser_session()` 跑过、`finally` 里把常驻会话 `close()` 了，
污染已被清掉，于是裸 `sync_playwright()` 竟然**成功**，最终报 `AttributeError: 'Playwright' object has no attribute 'browser'`。
它只证明了静态守卫有牙，**没有**证明机理，故重做。

第二次（注入到 `test_ui_a9.py` 中**最先执行**的 `TestA9FocusRingClosed.test_no_visible_ring_is_measured_while_sheet_focused`，日志 `artifacts/full-NEGCTL2.log`）：
```
& $VPY -m unittest discover -s backend/tests -t backend
exit=1
Ran 685 tests in 212.255s
FAILED (failures=1, errors=1)
asyncio loop 出现次数 = 2

ERROR: test_no_visible_ring_is_measured_while_sheet_focused (tests.e2e_ui.test_ui_a9.TestA9FocusRingClosed.test_no_visible_ring_is_measured_while_sheet_focused)
  File "...\backend\tests\e2e_ui\test_ui_a9.py", line 398, in test_no_visible_ring_is_measured_while_sheet_focused
    with sync_playwright() as chan:
  File "...\playwright\sync_api\_context_manager.py", line 46, in __enter__
    raise Error(
playwright._impl._errors.Error: It looks like you are using Playwright Sync API inside the asyncio loop.
Please use the Async API instead.

FAIL: test_no_bare_sync_playwright_in_this_suite (...TestZPlaywrightChannelGuard...)
AssertionError: Lists differ: ['test_ui_a9.py:398（在 test_no_visible_ring_is_measured_while_sheet_focused 内）'] != []
```
→ 同一命令、同一环境：**去掉修复即红**（机理红 + 静态守卫红双触发）。还原为**字节级**还原：
`test_ui_a9.py` sha256 = `F80EDB229E5F1A3AF89E0E0478FC264F44FBCE94E4F754F3A8D95DEC7FE2970B`（还原前后一致）。

## 6. V8 哈希夹逼（运行窗口内容不变）

口径：`backend/app` + `backend/tests` 的 `*.py`（跳过 `__pycache__`/`artifacts`/`.pylibs`）聚合 sha256[:16]，另附 `frontend/dist` 全文件聚合。

| 时点 | backend_py | dist |
| --- | --- | --- |
| T0（改动前，红复现） | `F84FB142822569F9`（122 文件） | `8B69046461AC2D03`（3 文件） |
| **T1（改动后，最终绿门禁之前）** | **`8E3DF714E54B3153`**（122 文件） | **`0795A996FBFFC7A7`**（3 文件） |
| **T3（最终绿门禁之后）** | **`8E3DF714E54B3153`** | **`0795A996FBFFC7A7`** |
| T4（负对照还原后复测） | `8E3DF714E54B3153` | `0795A996FBFFC7A7` |

**T1 == T3 == T4** ⇒ 最终那次 `Ran 685 / OK` 跑的就是现在磁盘上的内容，窗口内无人改动。
T0→T1 的变化正是本次修复的 3 个文件（`backend_count` 保持 122，无新增/删除 `.py`）；`dist` 在窗口外被别人重建过
（`frontend/scripts/**` 当时由 `fe-teacher` 的 task-23 在改），与本次修复无关，且 T1 起已冻结。
（哈希快照：`artifacts/hash-T0.json`、`hash-T1.json`、`hash-T3.json`、`hash-T4.json`。）

## 7. 未完成遗留

1. **`browser_session()` 每个用例都会 `close()` 常驻会话** → 每条 e2e_ui 用例各起一次 Chromium（单套件 72s）。
   功能正确、与产品 PDF 导出路径同源，但若后续 e2e_ui 用例规模变大，可考虑改用 `session()`（不 close）以复用浏览器。
   **本次不改**：改它会削弱「跑完即归还资源」的确定性，且不在 task-22 判据内。
2. `probe_a9.py` / `artifacts/probe_channel.py`、各 `.log`、`hash-*.json` 均在 `backend/tests/e2e_*/artifacts/` 下**被 gitignore**
   （`.gitignore:34`），属本机取证产物，不随仓库分发；本报告在写域内、**可入库**。
3. `docs/06-测试方案.md` 未新增小节（task-22 是修复轮，非里程碑验收；如需，只需在 §9 追加一段引用本报告）。
4. `frontend/dist` 在 T0 之后被重建（非本人操作）——若后续有人改 `frontend/src/**`，UI 判据需重跑，与本修复无关。

## 8. 是否触碰冻结契约

**否。** 逐项核对：
- `backend/app/**`：**一行未动**（`git status -- backend` 仅有上述 3 个 `backend/tests/e2e_ui/*.py`）。
- `frontend/src/design/tokens.css`（D67 冻结）：未动。
- `docs/04-接口契约.md` 冻结项：未动；无新增/修改接口、数据结构、表。
- `backend/app/engine/`、`grading/` 的 I/O 禁令：未涉及。
- 新增的 3 条用例不改变任何产品行为，只是把「必须走产品通道」写成可执行判据。

---

## 9. 建议 Lead 贴进 `docs/05-任务板.md` 的段落（写域归 Lead）

在 §3 的「M3.5：界面优化」表末尾追加两行：

```markdown
| task-22 | T-UI-FIX 门禁 asyncio 冲突：`e2e_ui` 12 处裸 `sync_playwright()` 改走产品浏览器通道 | verify-m1 | `backend/tests/e2e_ui/**` | task-19 | **已修复 · 待验收**（全量 `Ran 685 / OK`，`asyncio loop` 0 次；负对照两轮，第二轮双红；T1==T3==T4 哈希夹逼） |
| task-23 | T-SCRIPT-FIX 内联 Python 反引号门禁判据方向 | fe-teacher | `frontend/scripts/**` | — | 进行中 |
```

并在表下补一段：

```markdown
**task-22 技术结论（verify-m1，2026-10-15）**：12 条 `errors` 不是测试不稳，是调用姿势错误 ——
`e2e_m2` 的 PDF 用例启动的产品常驻会话（`backend/app/export/browser.py:215 _SESSION`）让主线程长期
「有运行中的 loop」，其后裸建 `PlaywrightContextManager` 必被 `_context_manager.py:46` 的守卫拒绝。
修法：13 处改用产品通道 `browser_session()`/`session()`（`browser.py:142-144` 复用、`177-187` 自带回退）；
并把「不得裸用」固化为 `TestZPlaywrightChannelGuard`（AST 静态扫描 + 动态机理断言 + 正对照）。
证据与六要素见 `backend/tests/e2e_ui/REPORT-task-22.md`。
```
