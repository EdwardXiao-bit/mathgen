/**
 * 排版面板「实时生效」验收脚本（Playwright + 模拟后端，零额外 npm 依赖）。
 *
 * 验收标准第 3 条要求「六个控件能实时改变预览区 CSS 变量」且**可复现**，
 * 本脚本把整条链路真实跑一遍：
 *
 *   1. 起一个**独立进程**的静态服务 + 模拟后端（`scripts/verify-server.mjs`）——
 *      独立进程是必须的：本脚本用 `spawnSync` 驱动 Playwright，会阻塞 Node 事件循环，
 *      服务若同进程则 `connection` 事件派发不到（实测 Chromium 连接超时且服务端无日志）
 *   2. 用 Playwright（Chromium，本机 backend/.venv 里已装）打开 /teacher/paper/1
 *   3. 逐个操作六个控件，每次读回预览区 `#sheet-preview` 的
 *      - 内联 style 上的 CSS 变量（React 写入，实时性的直接证据）
 *      - getComputedStyle 解析后的值（浏览器真正生效的结果）
 *   4. 断言六个变量都按预期变化；并断言调参窗口内**没有重新读取卷数据**
 *      （预览不靠服务器往返；排版 PATCH 是持久化，单独断言）
 *   5. 顺带验证：改题打到冻结路径、打印按钮触发 window.print、答案版不泄题
 *
 * 用法：
 *   & $NODE frontend/scripts/verify-layout-css-vars.mjs
 * 退出码 0 = 全部断言通过。截图：frontend/verify-layout-preview.png
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const DIST_DIR = path.join(FRONTEND_DIR, 'dist');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');
const NODE_EXE = process.execPath;

/**
 * Playwright 要在临时目录里 mkdtemp（浏览器 artifacts）。本机沙箱进程可能没有
 * 系统 TEMP 的写权限（实测 `EPERM ... mkdtemp 'C:\\Users\\...\\Temp\\playwright-artifacts-XXXXXX'`），
 * 所以把 TEMP/TMP 指到工作区内自建目录。
 */
const TMP_DIR = path.join(FRONTEND_DIR, '.tmp-playwright');
mkdirSync(TMP_DIR, { recursive: true });
const SERVER_LOG = path.join(TMP_DIR, 'verify-server.log');

/**
 * 端口：默认 4173 / 8123，但**以服务进程自己绑上的端口为准**（见 startServers 的解析）。
 *
 * 动因：本机上同时有别的服务（老师端预览 4173、样张静态服务 4180）。
 * 实测踩过最坏的形态：服务端因端口被占退让到 4174，而父进程仍按硬编码的 4173 驱动 ——
 * Playwright 打开的是**别人的应用**，等一个永远不存在的元素，报错却指向别处。
 */
const BASE_STATIC_PORT = Number(process.env.MATHGEN_VERIFY_STATIC_PORT ?? 4173);
const BASE_API_PORT = Number(process.env.MATHGEN_VERIFY_API_PORT ?? 8123);
let STATIC_PORT = BASE_STATIC_PORT;
let API_PORT = BASE_API_PORT;

const PYTHON = path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe');

// ---------------------------------------------------------------------------
// 断言小工具
// ---------------------------------------------------------------------------

const checks = [];
function check(name, condition, detail) {
  checks.push({ name, ok: Boolean(condition), detail });
  console.log(`  [${condition ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

// ---------------------------------------------------------------------------
// ① 起服务（独立进程）
// ---------------------------------------------------------------------------

function startServers() {
  rmSync(SERVER_LOG, { force: true });
  const child = spawn(NODE_EXE, [path.join(__dirname, 'verify-server.mjs')], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      MATHGEN_VERIFY_STATIC_PORT: String(BASE_STATIC_PORT),
      MATHGEN_VERIFY_API_PORT: String(BASE_API_PORT),
    },
  });
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });

  return new Promise((resolve, reject) => {
    let stdoutBuffer = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`服务进程 15 秒内没就绪。stderr:\n${stderr}`));
    }, 15_000);
    child.stdout.on('data', (chunk) => {
      stdoutBuffer += chunk;
      // 以服务进程自己绑上的端口为准（它可能因端口被占而退让）
      const ports = stdoutBuffer.match(/VERIFY_PORTS static=(\d+) api=(\d+)/);
      if (ports) {
        STATIC_PORT = Number(ports[1]);
        API_PORT = Number(ports[2]);
      }
      if (stdoutBuffer.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve(child);
      }
    });
    child.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`服务进程提前退出（code=${code}）。stderr:\n${stderr}`));
    });
  });
}

// ---------------------------------------------------------------------------
// ② Playwright 断言（内联 Python，用 backend/.venv 里已装的 Playwright）
// ---------------------------------------------------------------------------

/**
 * 内联 Python 脚本的**模板**。
 *
 * 注意它是 `String.raw` 模板字面量，所以 `${...}` 会在**模块加载时**就做替换。
 * 因此这里不能直接写 `const PLAYWRIGHT_SCRIPT = String.raw\`...${STATIC_PORT}...\``——
 * 那样会把「服务进程退让前的 4173」固化进脚本，而 Playwright 实际去打开的却是
 * 端口被占时服务真正绑上的 4174/…，于是**打开了另一个应用**（本机实测：打开了
 * Lead 的 vite preview，页面标题与题目数都对不上，报错却指向选择器超时）。
 *
 * 所以端口用占位符，到 `buildPlaywrightScript()` 里用**当时的**真实端口替换。
 */
const PLAYWRIGHT_SCRIPT_TEMPLATE = String.raw`
import glob, json, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:@@STATIC_PORT@@"
API = "http://127.0.0.1:@@API_PORT@@"

def find_chromium():
    """本机 ms-playwright 里已下好的 Chromium 可能比 pip 版 Playwright 期望的版本旧
    （实测装的是 chromium-1223，而 playwright 期望 chromium-1248），此时默认路径不存在。
    允许用 MATHGEN_CHROMIUM 显式指定，否则自动挑版本号最大的一份。"""
    explicit = os.environ.get("MATHGEN_CHROMIUM")
    if explicit and os.path.exists(explicit):
        return explicit
    roots = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright"),
    ]
    candidates = []
    for root in roots:
        candidates += glob.glob(os.path.join(root, "chromium-*", "chrome-win64", "chrome.exe"))
        candidates += glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome"))
    def rev(p):
        part = [c for c in p.split(os.sep) if c.startswith("chromium-")]
        tag = part[0].split("-")[-1] if part else "0"
        return int(tag) if tag.isdigit() else 0
    return sorted(candidates, key=rev)[-1] if candidates else None

def emit(obj):
    print("##RESULT##" + json.dumps(obj))

import urllib.request
def api_log():
    with urllib.request.urlopen(API + "/__api-log") as r:
        return json.load(r)

result = {"console_errors": []}
exe = find_chromium()
result["chromium"] = exe

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: result["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: result["console_errors"].append(str(e)))

    def run_flow():
        page.goto(BASE + "/teacher/paper/1", wait_until="domcontentloaded")
        # 等 iframe 里的卷面真的加载出来（D56：卷面 HTML 来自后端 /sheet）
        page.wait_for_selector("#sheet-frame", timeout=15000)
        page.wait_for_function(
            "() => { const f = document.getElementById('sheet-frame');"
            " return f && f.contentDocument && f.contentDocument.querySelector('.sheet'); }",
            timeout=15000,
        )
        page.wait_for_timeout(400)

        def read_vars():
            # 关键：读的是 **iframe 内部文档**（后端渲染的卷面）根元素上的变量与真实 display 值，
            # 不是 React 自己画的 DOM —— D56 之后前端不再有卷面 DOM。
            return page.evaluate("""() => {
                const frame = document.getElementById('sheet-frame');
                const doc = frame && frame.contentDocument;
                const names = ['--cols-per-row','--row-gap','--font-size','--page-margin',
                               '--answer-line-display','--header-bar-display'];
                if (!doc || !doc.documentElement) {
                    return { error: 'iframe 文档不可读' };
                }
                const root = doc.documentElement;
                const cs = getComputedStyle(root);
                const inline = {}, computed = {};
                for (const n of names) {
                    inline[n] = root.style.getPropertyValue(n);
                    computed[n] = cs.getPropertyValue(n);
                }
                const sheet = doc.querySelector('.sheet');
                const grid = doc.querySelector('.q-row');
                const blank = doc.querySelector('.q-blank');
                const headerBar = doc.querySelector('.header-bar');
                return {
                    inline, computed,
                    src: frame.getAttribute('src'),
                    frameDocTitle: doc.title,
                    hasSheet: Boolean(sheet),
                    sheetMode: sheet ? sheet.getAttribute('data-mode') : null,
                    gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : null,
                    gridRowGap: grid ? getComputedStyle(grid).rowGap : null,
                    itemCount: doc.querySelectorAll('.q-item').length,
                    answerItems: doc.querySelectorAll('.a-item').length,
                    // D59 的核心：开关是否真的作用到卷面元素上
                    blankDisplay: blank ? getComputedStyle(blank).display : null,
                    headerBarDisplay: headerBar ? getComputedStyle(headerBar).display : null,
                    headerBarCount: doc.querySelectorAll('.header-bar').length,
                    // 浏览器真正解析后的字号（用来证明 --font-size 生效到卷面上）
                    itemFontSize: doc.querySelector('.q-item')
                        ? getComputedStyle(doc.querySelector('.q-item')).fontSize : null,
                    sheetPadding: sheet ? getComputedStyle(sheet).paddingLeft : null,
                    // 切页签（题目版 ↔ 答案版）之后，滚动区高度必须跟着内容走。
                    // 这里量的是「卷面内容高（body）」「iframe 元素高」「舞台滚动高」三者，
                    // 用来抓「切到答案版再切回来，第 2 页拖出一大截空白」这类高度只涨不落的 bug。
                    contentH: doc.body ? Math.ceil(doc.body.getBoundingClientRect().height) : null,
                    sheetH: sheet ? Math.ceil(sheet.getBoundingClientRect().height) : null,
                    iframeOffsetH: frame ? frame.offsetHeight : null,
                    iframeScale: frame && frame.offsetHeight
                        ? +(frame.getBoundingClientRect().height / frame.offsetHeight).toFixed(4) : null,
                    stageScrollH: (() => {
                        const s = document.querySelector('.mg-paper-stage');
                        return s ? Math.ceil(s.scrollHeight) : null;
                    })(),
                    stageClientH: (() => {
                        const s = document.querySelector('.mg-paper-stage');
                        return s ? Math.ceil(s.clientHeight) : null;
                    })(),
                    dump: (document.querySelector('[data-testid="css-var-dump"]') || {}).textContent || '',
                };
            }""")

        result["before"] = read_vars()
        log_before = len(api_log())

        def set_range(testid, value):
            page.eval_on_selector(
                '[data-testid="' + testid + '"]',
                """(el, v) => {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, String(v));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value,
            )
            page.wait_for_timeout(130)

        set_range("layout-cols_per_row", 3)
        result["after_cols"] = read_vars()
        set_range("layout-row_gap", 2.0)
        result["after_gap"] = read_vars()
        set_range("layout-font_size", 20)
        result["after_font"] = read_vars()
        set_range("layout-page_margin", 3.0)
        result["after_margin"] = read_vars()

        # ---- D59 回归：两个开关（本次真 bug 的现场）----
        #
        # 防的是什么：面板写的 CSS 变量在渲染器样式表里**从来没被任何规则读取**时，
        # 「拖动时实时覆盖」看起来一切正常（变量确实改了），但后端 HTML 是加载时定格的，
        # 所以预览完全不动，而界面还显示「已保存」——比报错更糟。
        # 两个层次分别断言：
        #   ① 拖动瞬间：实时覆盖让卷面元素立刻变（手感，0 请求）
        #   ② 保存 + 重载之后：后端**重新渲染**出来的卷面里开关仍然生效
        #     （② 才是真正抓到 bug 的那条：只有渲染器真消费变量才会过）
        def wait_persist_done(timeout_ms=8000):
            page.wait_for_function(
                "() => { const el = document.querySelector('.layout-panel-status');"
                " return el && (el.getAttribute('data-persist-state') === 'refreshed'"
                " || el.getAttribute('data-persist-state') === 'error'); }",
                timeout=timeout_ms,
            )
            return page.get_attribute('.layout-panel-status', 'data-persist-state')

        page.click('[data-control="answer_line"] .switch')
        page.wait_for_timeout(130)
        result["after_line_instant"] = read_vars()
        result["persist_after_line"] = wait_persist_done()
        result["after_line"] = read_vars()

        page.click('[data-control="header_bar"] .switch')
        page.wait_for_timeout(130)
        result["after_header_instant"] = read_vars()
        result["persist_after_header"] = wait_persist_done()
        result["after_header"] = read_vars()

        # 关闭之后再打开一次，验证反向（这条防的是「只能关不能开」的假修复）
        page.click('[data-control="answer_line"] .switch')
        page.wait_for_timeout(130)
        result["after_line_on_instant"] = read_vars()
        result["persist_after_line_on"] = wait_persist_done()
        result["after_line_on"] = read_vars()

        page.click('[data-control="header_bar"] .switch')
        page.wait_for_timeout(130)
        result["after_header_on_instant"] = read_vars()
        result["persist_after_header_on"] = wait_persist_done()
        result["after_header_on"] = read_vars()

        # 调参窗口内的后端请求（等过 debounce 400ms，让排版 PATCH 落下去）
        page.wait_for_timeout(900)
        window = api_log()[log_before:]
        result["panel_window_requests"] = window
        result["panel_preview_reads"] = len(
            [r for r in window if r["method"] == "GET" and r["path"] == "/api/papers/1"]
        )
        # D59 兜底会重载卷面，所以这里期望 **恰好等于保存次数**，而不是 0：
        # 拖动过程中 0 次；每次保存成功后 1 次。
        result["panel_sheet_reads"] = len(
            [r for r in window if r["method"] == "GET" and r["path"] == "/api/papers/1/sheet"]
        )
        result["panel_layout_patches"] = len(
            [r for r in window if r["method"] == "PATCH" and r["path"].endswith("/layout")]
        )

        # 改题：换一题 / 删题
        requests = []
        page.on("request", lambda r: requests.append((r.method, r.url)))
        page.click('[data-testid="regenerate-1"]')
        page.wait_for_timeout(400)
        page.click('[data-testid="delete-1"]')
        page.wait_for_timeout(600)
        result["mutation_requests"] = [
            m + " " + u.split("127.0.0.1:@@STATIC_PORT@@")[-1] for m, u in requests if "/api/" in u
        ]
        # 改题后预览要自己重载（用户实测反馈：不点刷新，纸面还是旧的）
        result["post_edit_sheet_reads"] = len(
            [1 for m, u in requests if m == "GET" and "/api/papers/1/sheet" in u]
        )
        # 题目列表不许把算式截断（右侧栏 300px，四位数乘两位数会被挤没）
        result["item_rows"] = page.evaluate(
            """() => {
                const style = (el) => (el ? getComputedStyle(el) : null);
                return Array.from(document.querySelectorAll('.item-row')).map((row) => {
                  const expr = row.querySelector('.item-expr');
                  const cs = style(expr);
                  return {
                    text: expr ? expr.textContent.trim() : '',
                    clientWidth: expr ? Math.round(expr.clientWidth) : 0,
                    scrollWidth: expr ? Math.round(expr.scrollWidth) : 0,
                    whiteSpace: cs ? cs.whiteSpace : 'missing',
                    textOverflow: cs ? cs.textOverflow : 'missing',
                  };
                });
            }"""
        )

        # 打印：D56 之后打印的是**隐藏 iframe 里的后端卷面**（.print-frame），
        # 不是 window.print() 打主文档。所以要在打印 iframe 的 window 上装桩。
        #
        # 实现里是「fetch 卷面 HTML → document.write 进 about:blank iframe → 轮询 readyState → print()」，
        # 没有 load 事件可依赖；所以这里轮询接管 print，每次调用都抓下**打印时刻**的卷面内容。
        page.evaluate("""() => {
            window.__printed = [];
            const origCreate = document.createElement.bind(document);
            document.createElement = function(tag) {
                const el = origCreate(tag);
                if (String(tag).toLowerCase() !== 'iframe') return el;
                let hooked = false;
                const hook = () => {
                    if (hooked) return;
                    const win = el.contentWindow;
                    const doc = win && win.document;
                    if (!win || !doc || !doc.documentElement || doc.readyState === 'loading') return;
                    hooked = true;
                    win.print = () => {
                        const d = win.document;
                        const sheet = d.querySelector('.sheet');
                        window.__printed.push({
                            src: el.getAttribute('src'),
                            mode: sheet ? sheet.getAttribute('data-mode') : null,
                            hasAnswerSection: Boolean(d.querySelector('.answer-section')),
                            answerItems: d.querySelectorAll('.a-item').length,
                            qItems: d.querySelectorAll('.q-item').length,
                            vars: {
                                cols: d.documentElement.style.getPropertyValue('--cols-per-row'),
                                rowGap: d.documentElement.style.getPropertyValue('--row-gap'),
                                fontSize: d.documentElement.style.getPropertyValue('--font-size'),
                                answerLineDisplay: d.documentElement.style.getPropertyValue('--answer-line-display'),
                            },
                        });
                    };
                };
                const timer = setInterval(hook, 20);
                setTimeout(() => clearInterval(timer), 12000);
                return el;
            };
        }""")

        page.click('[data-testid="print-question"]')
        page.wait_for_timeout(900)
        page.click('[data-testid="print-answer"]')
        page.wait_for_timeout(900)
        result["prints"] = page.evaluate("() => window.__printed")

        # 答案版：切页签后 iframe 重新指向 mode=answer，并且卷面里出现答案区
        page.click('[data-testid="preview-tab-answer"]')
        page.wait_for_function(
            "() => { const f = document.getElementById('sheet-frame');"
            " return f && f.contentDocument && f.contentDocument.querySelector('.answer-section'); }",
            timeout=15000,
        )
        result["answer_tab"] = read_vars()
        page.click('[data-testid="preview-tab-question"]')
        page.wait_for_function(
            "() => { const f = document.getElementById('sheet-frame');"
            " return f && f.contentDocument && f.contentDocument.querySelector('.sheet')"
            " && !f.contentDocument.querySelector('.answer-section'); }",
            timeout=15000,
        )
        result["question_tab"] = read_vars()

    try:
        run_flow()
    except Exception as exc:  # noqa: BLE001
        # 失败时把「当时页面长什么样」带回去：只有一行 TimeoutError 时无从下手（本机踩过）
        result["failure"] = f"{type(exc).__name__}: {exc}".split("\n")[0]
        try:
            result["failure_state"] = page.evaluate("""() => {
                const f = document.getElementById('sheet-frame');
                return {
                    url: location.href,
                    bodyHead: document.body.innerText.slice(0, 400),
                    frameSrc: f ? f.getAttribute('src') : null,
                    hasSheet: Boolean(f && f.contentDocument && f.contentDocument.querySelector('.sheet')),
                    frameVars: (() => {
                        const d = f && f.contentDocument;
                        if (!d) return null;
                        const r = d.documentElement;
                        return {
                            cols: r.style.getPropertyValue('--cols-per-row'),
                            gap: r.style.getPropertyValue('--row-gap'),
                            font: r.style.getPropertyValue('--font-size'),
                            margin: r.style.getPropertyValue('--page-margin'),
                        };
                    })(),
                    hasAnswerLineSwitch: Boolean(document.querySelector('[data-control="answer_line"] .switch')),
                    panelStatus: (document.querySelector('.layout-panel-status') || {}).getAttribute
                        ? document.querySelector('.layout-panel-status').getAttribute('data-persist-state') : null,
                };
            }""")
        except Exception:  # noqa: BLE001
            result["failure_state"] = {"note": "页面已不可读"}
    # 截图只是辅助证据，**不能让它拖垮门禁**：路径形态在不同机器上可能不可写
    try:
        page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-layout-preview.png"))
    except Exception as exc:  # noqa: BLE001
        result["screenshot_error"] = f"{type(exc).__name__}: {exc}"[:200]
    browser.close()
emit(result)
`;

/**
 * 用**当时**的端口/路径渲染出真正要执行的 Python。
 *
 * 必须在 `startServers()` 之后调用 —— 这时 STATIC_PORT / API_PORT 才是服务进程
 * 真正绑上的端口（可能因为被占用而与默认值不同）。
 */
function buildPlaywrightScript() {
  // 用「可识别标记」而不是 `${...}` 做占位：`${...}` 在模板字面量里会被**立即插值**，
  // 于是脚本被固化成「服务退让前的端口」，Playwright 就会去打开另一个应用
  // （本机实测：打开了 Lead 的 vite preview，页面标题/题目数都对不上）。
  // @@NAME@@ 不参与任何转义规则，纯文本替换，最不容易出错。
  return PLAYWRIGHT_SCRIPT_TEMPLATE
    .replaceAll('@@STATIC_PORT@@', String(STATIC_PORT))
    .replaceAll('@@API_PORT@@', String(API_PORT))
    // 路径统一成正斜杠：Windows 上 Python 也认，且不必再操心反斜杠转义
    .replaceAll('@@FRONTEND_DIR@@', FRONTEND_DIR.replace(/\\/g, '/'));
}

function runPlaywright() {
  const proc = spawnSync(PYTHON, ['-c', buildPlaywrightScript()], {
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, PYTHONUTF8: '1', TEMP: TMP_DIR, TMP: TMP_DIR, TMPDIR: TMP_DIR },
  });
  if (proc.error) throw proc.error;
  const line = (proc.stdout ?? '').split(/\r?\n/).find((l) => l.startsWith('##RESULT##'));
  if (proc.status !== 0 || !line) {
    console.error('Playwright 子进程失败（exit 非 0 或没有输出结果行）：');
    console.error(proc.stdout);
    console.error(proc.stderr);
    throw new Error(`playwright exit ${proc.status}`);
  }
  return JSON.parse(line.slice('##RESULT##'.length));
}

// ---------------------------------------------------------------------------
// ③ 主流程 + 断言
// ---------------------------------------------------------------------------

async function main() {
  if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
    console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
    process.exit(2);
  }
  if (!existsSync(PYTHON)) {
    console.error(`找不到后端 venv 的 Python：${PYTHON}（Playwright 装在这里）`);
    process.exit(2);
  }

  console.log('启动静态服务（frontend/dist）+ 模拟后端（形状照 api.py）…');
  const server = await startServers();
  console.log(`  静态服务 http://127.0.0.1:${STATIC_PORT}/  模拟后端 http://127.0.0.1:${API_PORT}/api/`);
  console.log('  服务运行在独立进程；请求日志：frontend/.tmp-playwright/verify-server.log\n');

  let result;
  try {
    console.log('—— 用 Playwright 驱动真实产物 ——');
    result = runPlaywright();
  } finally {
    server.kill();
  }

  console.log(`Chromium: ${result.chromium ?? '(默认)'}`);
  if (result.failure) {
    console.log(`\n⚠ Playwright 流程中断：${result.failure}`);
    console.log('  中断时的页面状态：');
    console.log(JSON.stringify(result.failure_state, null, 2));
  }
  console.log('\n—— 断言 ——');
  const b = result.before;
  const a = {
    cols: result.after_cols,
    gap: result.after_gap,
    font: result.after_font,
    margin: result.after_margin,
    line: result.after_line,
    header: result.after_header,
  };

  check('卷面 iframe 加载出后端渲染的卷面（.sheet）', b?.hasSheet === true, `src=${b?.src} 标题=${b?.frameDocTitle} 题目数=${b?.itemCount}`);  check('iframe src 指向 /api/papers/1/sheet?mode=question', (b?.src ?? '').includes('/api/papers/1/sheet?mode=question'), b?.src);
  check('iframe 文档根元素上已是初始六个变量', b.inline['--cols-per-row'] === '4' && b.inline['--answer-line-display'] === 'block', JSON.stringify(b.inline));
  check('初始 --cols-per-row = 4', b.inline['--cols-per-row'] === '4');
  check('初始 --row-gap = 1', b.inline['--row-gap'] === '1');
  check('初始 --font-size = 14pt', b.inline['--font-size'] === '14pt');
  check('初始 --page-margin = 2cm', b.inline['--page-margin'] === '2cm');
  check('初始 --answer-line-display = block（D59：display 关键字）', b.inline['--answer-line-display'] === 'block', b.inline['--answer-line-display']);
  check('初始 --header-bar-display = grid（D59：display 关键字）', b.inline['--header-bar-display'] === 'grid', b.inline['--header-bar-display']);

  check('① 每行题数 4→3 改 iframe 里的 --cols-per-row', a.cols.inline['--cols-per-row'] === '3', `computed=${a.cols.computed['--cols-per-row']}`);
  check('   卷面网格真的变成 3 列', (a.cols.gridColumns || '').trim().split(/\s+/).length === 3, `gridTemplateColumns=${a.cols.gridColumns}`);
  check('② 行间距 1→2 改 iframe 里的 --row-gap', a.gap.inline['--row-gap'] === '2', `computed=${a.gap.computed['--row-gap']}`);
  check(
    '   卷面行间距随 --row-gap 变大',
    Number.parseFloat(a.gap.gridRowGap) > Number.parseFloat(a.cols.gridRowGap) * 1.4,
    `--row-gap=1 时 rowGap=${a.cols.gridRowGap}；--row-gap=2 时 rowGap=${a.gap.gridRowGap}`,
  );
  check('③ 字号 14→20 改 iframe 里的 --font-size', a.font.inline['--font-size'] === '20pt');
  check('   卷面题目的实际字号 = 20pt', (a.font.itemFontSize || '').startsWith('26.6'), `computed fontSize=${a.font.itemFontSize}`);
  check('④ 页边距 2→3 改 iframe 里的 --page-margin', a.margin.inline['--page-margin'] === '3cm');
  // 渲染器给了 @media screen 的纸面 box 后，页边距在**预览**里也应该看得见：
  // 卷面纸面必须真的有非零内边距（= 那段 @media screen 消费了 --page-margin）。
  // 若哪天后端去掉那段，这条会红 —— 那时面板上必须重新挂「屏幕上看不出」的说明。
  check(
    '   卷面纸面有非零内边距（屏幕预览能吃 --page-margin，而非只影响打印）',
    Number.parseFloat(a.margin.sheetPadding) > 0,
    `.sheet padding-left=${a.margin.sheetPadding}（期望 3cm≈113.4px）`,
  );

  console.log('\n—— D59 回归：两个开关（本次真 bug 的防线）——');
  const inst = {
    line: result.after_line_instant ?? {},
    header: result.after_header_instant ?? {},
    lineOn: result.after_line_on_instant ?? {},
    headerOn: result.after_header_on_instant ?? {},
  };
  const reloaded = {
    line: result.after_line ?? {},
    header: result.after_header ?? {},
    lineOn: result.after_line_on ?? {},
    headerOn: result.after_header_on ?? {},
  };

  // 层次 ①：拖动瞬间的实时覆盖（手感，0 请求）
  check(
    '⑤-a 关掉答题横线：拖动瞬间 --answer-line-display = none',
    inst.line.inline['--answer-line-display'] === 'none',
    `inline=${inst.line.inline['--answer-line-display']}`,
  );
  check(
    '   且卷面 .q-blank 立刻变 none（变量真的被样式表消费）',
    inst.line.blankDisplay === 'none',
    `.q-blank display=${inst.line.blankDisplay}`,
  );
  check(
    '⑤-b 关掉卷头栏：拖动瞬间 --header-bar-display = none',
    inst.header.inline['--header-bar-display'] === 'none',
    `inline=${inst.header.inline['--header-bar-display']}`,
  );
  check(
    '   且卷面 .header-bar 立刻变 none',
    inst.header.headerBarDisplay === 'none',
    `.header-bar display=${inst.header.headerBarDisplay}`,
  );

  // 层次 ②：保存 + **重载后端渲染的卷面**之后仍然生效 —— 这条才是抓到本次 bug 的那条。
  // 说明：本断言在渲染器样式表真正消费变量时通过；若 render-export 尚未改完（D59 未落地），
  // 它会红 —— 那是实现未完成，不是前端问题（渲染器的写域不属于 frontend）。
  check('保存后状态推进到「已保存，预览已刷新」', result.persist_after_line === 'refreshed', `data-persist-state=${result.persist_after_line}`);
  check(
    '⑤-c 重载后（后端重新渲染）答题横线仍为关：class="q-blank" 实际 display = none',
    reloaded.line.blankDisplay === 'none',
    `重载后 .q-blank display=${reloaded.line.blankDisplay}；--answer-line-display=${reloaded.line.inline['--answer-line-display']}`,
  );
  check(
    '⑥-a 保存后重载，卷头栏仍为关：.header-bar 实际 display = none',
    reloaded.header.headerBarDisplay === 'none',
    `重载后 .header-bar display=${reloaded.header.headerBarDisplay}`,
  );
  check(
    '   元素仍在 DOM 里（不是渲染器干脆不输出，而是用 display 控制）',
    (reloaded.header.headerBarCount ?? 0) > 0 && (reloaded.line.itemCount ?? 0) > 0,
    `header-bar 数=${reloaded.header.headerBarCount} 题目数=${reloaded.line.itemCount}`,
  );

  // 层次 ③：反向也要成立（防「只能关不能开」的假修复）
  check(
    '⑤-d 重新打开答题横线：重载后 .q-blank display = block',
    reloaded.lineOn.blankDisplay === 'block',
    `重载后 .q-blank display=${reloaded.lineOn.blankDisplay}；--answer-line-display=${reloaded.lineOn.inline['--answer-line-display']}`,
  );
  check(
    '⑥-b 重新打开卷头栏：重载后 .header-bar display = grid',
    reloaded.headerOn.headerBarDisplay === 'grid',
    `重载后 .header-bar display=${reloaded.headerOn.headerBarDisplay}；--header-bar-display=${reloaded.headerOn.inline['--header-bar-display']}`,
  );

  check(
    '调参期间没有重新读取卷数据（预览不靠服务器往返）',
    result.panel_preview_reads === 0,
    `GET /api/papers/1 次数=${result.panel_preview_reads}`,
  );
  check(
    '调参期间发生排版持久化 PATCH /api/papers/1/layout',
    (result.panel_layout_patches ?? 0) >= 1,
    `PATCH 次数=${result.panel_layout_patches}`,
  );
  check(
    'D59 兜底：每次保存成功后重载一次卷面（拖动过程中不重载）',
    (result.panel_sheet_reads ?? 0) === (result.panel_layout_patches ?? 0),
    `GET /sheet 次数=${result.panel_sheet_reads}；PATCH 次数=${result.panel_layout_patches}（两者应相等）`,
  );

  const muts = result.mutation_requests ?? [];
  check('换一题打到 POST /api/papers/1/items/1/regenerate', muts.some((p) => p.startsWith('POST /api/papers/1/items/1/regenerate')), muts.join(' | ') || '(无)');
  check('删题打到 DELETE /api/papers/1/items/1', muts.some((p) => p.startsWith('DELETE /api/papers/1/items/1')), muts.join(' | ') || '(无)');

  // 用户实测反馈的两条（2026-10-28）：改完题要手动刷新才同步 / 算式被截断。
  check(
    '改题后预览自己重载，不用老师手动刷新（换一题 + 删题之后各重新取一次卷面）',
    (result.post_edit_sheet_reads ?? 0) >= 2,
    `改题后 GET /sheet 次数=${result.post_edit_sheet_reads ?? 0}`,
  );

  const rows = result.item_rows ?? [];
  const truncated = rows.filter((r) => r.scrollWidth > r.clientWidth + 1);
  const nowrap = rows.filter((r) => r.whiteSpace === 'nowrap');
  const ellipsis = rows.filter((r) => r.textOverflow === 'ellipsis');
  check(
    '题目列表完整显示算式（不被省略号截断、不允许 nowrap 单行硬挤）',
    rows.length > 0 && truncated.length === 0 && nowrap.length === 0 && ellipsis.length === 0,
    `行数=${rows.length} 超出宽=${truncated.length} nowrap=${nowrap.length} ellipsis=${ellipsis.length}`,
  );

  const prints = result.prints ?? [];
  check('打印题目版：调用了打印 iframe 的 print（不是打主文档）', prints.length >= 1, `打印调用=${prints.length}`);
  check('  打印题目版用的是 mode=question 的卷面', prints[0]?.mode === 'question', `${JSON.stringify(prints[0])}`);
  check('  题目版打印内容不含答案区（不泄题）', prints[0]?.hasAnswerSection === false, `hasAnswerSection=${prints[0]?.hasAnswerSection} 答案条数=${prints[0]?.answerItems}`);
  check('  打印题目版时卷面带着当前排版（--cols-per-row=3）', prints[0]?.vars?.cols === '3', JSON.stringify(prints[0]?.vars));
  check('打印答案版：第二次打印用的是 mode=answer 的卷面', prints[1]?.mode === 'answer', `${JSON.stringify(prints[1])}`);
  check('  答案版打印内容含答案区', prints[1]?.hasAnswerSection === true && (prints[1]?.answerItems ?? 0) > 0, `hasAnswerSection=${prints[1]?.hasAnswerSection} 答案条数=${prints[1]?.answerItems}`);
  check('  打印用的是同一份后端卷面 HTML（题目版 6 题 / 答案版 6 题）', prints[0]?.qItems === 6 && prints[1]?.qItems === 6, `qItems=${prints[0]?.qItems} / ${prints[1]?.qItems}`);

  check('答案版页签：iframe 重新加载为含答案区的卷面', result.answer_tab?.sheetMode === 'answer' && (result.answer_tab?.answerItems ?? 0) > 0, `mode=${result.answer_tab?.sheetMode} 答案条数=${result.answer_tab?.answerItems}`);
  check('题目版页签：卷面不含答案区', result.question_tab?.sheetMode === 'question' && result.question_tab?.answerItems === 0, `mode=${result.question_tab?.sheetMode} 答案条数=${result.question_tab?.answerItems}`);
  // 用户 2026-10-28：「50 题，先预览题单时正常，切换到答案版，再切回预览题单，第 2 页就会很长」。
  // 根因是卷面高度取的是 documentElement.scrollHeight —— iframe 视口被撑高之后，scrollHeight
  // 至少等于视口高，于是高度只涨不落，切回题目版时滚动区还留着答案版的高度。
  const qTab = result.question_tab ?? {};
  const aTab = result.answer_tab ?? {};
  check(
    '切回题目版：iframe 高度跟得上内容（不许留着答案版的高度）',
    typeof qTab.contentH === 'number' && typeof qTab.iframeOffsetH === 'number'
      && Math.abs(qTab.iframeOffsetH - qTab.contentH) <= 2,
    `iframe 高 ${qTab.iframeOffsetH} / 卷面内容高 ${qTab.contentH}（答案版是 ${aTab.iframeOffsetH}）`,
  );
  check(
    '切回题目版：滚动区高度 == 内容高 × 缩放 + 内边距（不许拖出空白）',
    typeof qTab.stageScrollH === 'number' && typeof qTab.contentH === 'number'
      && typeof qTab.iframeScale === 'number'
      && Math.abs(qTab.stageScrollH - (Math.ceil(qTab.contentH * qTab.iframeScale) + 48)) <= 4,
    `舞台滚动高 ${qTab.stageScrollH} / 应为 ${Math.ceil((qTab.contentH ?? 0) * (qTab.iframeScale ?? 0)) + 48}`
      + `（答案版 ${aTab.stageScrollH}）`,
  );
  check('页面无 console error / pageerror', (result.console_errors ?? []).length === 0, (result.console_errors ?? []).join(' ; ') || '(无)');

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    console.log('失败项：');
    for (const item of failed) console.log(`  - ${item.name} (${item.detail ?? ''})`);
    process.exit(1);
  }
  console.log('全部通过。截图：frontend/verify-layout-preview.png');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
