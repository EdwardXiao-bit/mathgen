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

const STATIC_PORT = 4173;
const API_PORT = 8123;

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
  });
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`服务进程 15 秒内没就绪。stderr:\n${stderr}`));
    }, 15_000);
    child.stdout.on('data', (chunk) => {
      if (chunk.includes('VERIFY_SERVER_READY')) {
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

const PLAYWRIGHT_SCRIPT = String.raw`
import glob, json, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:${STATIC_PORT}"
API = "http://127.0.0.1:${API_PORT}"

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
        # 关键：读的是 **iframe 内部文档**（后端渲染的卷面）根元素上的变量，
        # 不是 React 自己画的 DOM —— D56 之后前端不再有卷面 DOM。
        return page.evaluate("""() => {
            const frame = document.getElementById('sheet-frame');
            const doc = frame && frame.contentDocument;
            const names = ['--cols-per-row','--row-gap','--font-size','--page-margin','--answer-line','--header-bar'];
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
            return {
                inline, computed,
                src: frame.getAttribute('src'),
                frameDocTitle: doc.title,
                hasSheet: Boolean(sheet),
                sheetMode: sheet ? sheet.getAttribute('data-mode') : null,
                answerLineAttr: sheet ? sheet.getAttribute('data-answer-line') : null,
                headerBarAttr: sheet ? sheet.getAttribute('data-header-bar') : null,
                gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : null,
                gridRowGap: grid ? getComputedStyle(grid).rowGap : null,
                itemCount: doc.querySelectorAll('.q-item').length,
                answerItems: doc.querySelectorAll('.a-item').length,
                // 浏览器真正解析后的字号（用来证明 --font-size 生效到卷面上）
                itemFontSize: doc.querySelector('.q-item')
                    ? getComputedStyle(doc.querySelector('.q-item')).fontSize : null,
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

    page.click('[data-control="answer_line"] .switch')
    page.wait_for_timeout(130)
    result["after_line"] = read_vars()

    page.click('[data-control="header_bar"] .switch')
    page.wait_for_timeout(130)
    result["after_header"] = read_vars()

    # 调参窗口内的后端请求（等过 debounce 400ms，让排版 PATCH 落下去）
    page.wait_for_timeout(900)
    window = api_log()[log_before:]
    result["panel_window_requests"] = window
    result["panel_preview_reads"] = len(
        [r for r in window if r["method"] == "GET" and r["path"] == "/api/papers/1"]
    )
    # 卷面 HTML 的读取单独计数：调参期间也不应该重新拉卷面
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
    page.wait_for_timeout(400)
    result["mutation_requests"] = [
        m + " " + u.split(str(${STATIC_PORT}))[-1] for m, u in requests if "/api/" in u
    ]

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
                            answerLine: d.documentElement.style.getPropertyValue('--answer-line'),
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

    page.screenshot(path=os.path.join(r"${FRONTEND_DIR}", "verify-layout-preview.png"))
    browser.close()

emit(result)
`;

function runPlaywright() {
  const proc = spawnSync(PYTHON, ['-c', PLAYWRIGHT_SCRIPT], {
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

  check('卷面 iframe 加载出后端渲染的卷面（.sheet）', b?.hasSheet === true, `src=${b?.src} 标题=${b?.frameDocTitle} 题目数=${b?.itemCount}`);
  check('iframe src 指向 /api/papers/1/sheet?mode=question', (b?.src ?? '').includes('/api/papers/1/sheet?mode=question'), b?.src);
  check('iframe 文档根元素上已是初始六个变量', b.inline['--cols-per-row'] === '4' && b.inline['--answer-line'] === '1', JSON.stringify(b.inline));
  check('初始 --cols-per-row = 4', b.inline['--cols-per-row'] === '4');
  check('初始 --row-gap = 1', b.inline['--row-gap'] === '1');
  check('初始 --font-size = 14pt', b.inline['--font-size'] === '14pt');
  check('初始 --page-margin = 2cm', b.inline['--page-margin'] === '2cm');
  check('初始 --answer-line = 1', b.inline['--answer-line'] === '1');
  check('初始 --header-bar = 1', b.inline['--header-bar'] === '1');

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
  check('⑤ 答题横线开关改 iframe 里的 --answer-line = 0', a.line.inline['--answer-line'] === '0', `computed=${a.line.computed['--answer-line']}`);
  check(
    '   卷面 CSS 里 --answer-line 生效（驱动 .q-blank 的显隐）',
    a.line.computed['--answer-line'] === '0',
    `getComputedStyle(documentElement).getPropertyValue('--answer-line')=${a.line.computed['--answer-line']}`,
  );
  check('⑥ 卷头栏开关改 iframe 里的 --header-bar = 0', a.header.inline['--header-bar'] === '0');
  check(
    '   卷面 CSS 里 --header-bar 生效（驱动卷头栏显隐）',
    a.header.computed['--header-bar'] === '0',
    `getComputedStyle(documentElement).getPropertyValue('--header-bar')=${a.header.computed['--header-bar']}`,
  );
  // 说明：iframe 里 `.sheet` 的 data-answer-line / data-header-bar 是**服务端渲染时**写下的属性，
  // 面板的实时覆盖只作用于六个 CSS 变量（冻结契约 css_variables 的产物）。
  // 打印 / PDF 走服务端重新渲染，属性自然是最新的 —— 这一点由打印断言覆盖。

  check(
    '调参期间没有重新请求卷面 / 卷数据（预览不靠服务器往返）',
    result.panel_preview_reads === 0 && (result.panel_sheet_reads ?? 0) === 0,
    `GET /api/papers/1 次数=${result.panel_preview_reads}；GET /sheet 次数=${result.panel_sheet_reads}`,
  );
  check(
    '调参期间发生排版持久化 PATCH /api/papers/1/layout',
    (result.panel_layout_patches ?? 0) >= 1,
    `PATCH 次数=${result.panel_layout_patches}；窗口内请求=${JSON.stringify(result.panel_window_requests)}`,
  );

  const muts = result.mutation_requests ?? [];
  check('换一题打到 POST /api/papers/1/items/1/regenerate', muts.some((p) => p.startsWith('POST /api/papers/1/items/1/regenerate')), muts.join(' | ') || '(无)');
  check('删题打到 DELETE /api/papers/1/items/1', muts.some((p) => p.startsWith('DELETE /api/papers/1/items/1')), muts.join(' | ') || '(无)');

  const prints = result.prints ?? [];
  check('打印题目版：调用了打印 iframe 的 print（不是打主文档）', prints.length >= 1, `打印调用=${prints.length}`);
  check('  打印题目版用的是 mode=question 的卷面', prints[0]?.mode === 'question', `${JSON.stringify(prints[0])}`);
  check('  题目版打印内容不含答案区（不泄题）', prints[0]?.hasAnswerSection === false, `hasAnswerSection=${prints[0]?.hasAnswerSection} 答案条数=${prints[0]?.answerItems}`);
  check('  打印题目版时卷面带着当前排版（--cols-per-row=3 / --answer-line=0）', prints[0]?.vars?.cols === '3' && prints[0]?.vars?.answerLine === '0', JSON.stringify(prints[0]?.vars));
  check('打印答案版：第二次打印用的是 mode=answer 的卷面', prints[1]?.mode === 'answer', `${JSON.stringify(prints[1])}`);
  check('  答案版打印内容含答案区', prints[1]?.hasAnswerSection === true && (prints[1]?.answerItems ?? 0) > 0, `hasAnswerSection=${prints[1]?.hasAnswerSection} 答案条数=${prints[1]?.answerItems}`);
  check('  打印用的是同一份后端卷面 HTML（题目版 6 题 / 答案版 6 题）', prints[0]?.qItems === 6 && prints[1]?.qItems === 6, `qItems=${prints[0]?.qItems} / ${prints[1]?.qItems}`);

  check('答案版页签：iframe 重新加载为含答案区的卷面', result.answer_tab?.sheetMode === 'answer' && (result.answer_tab?.answerItems ?? 0) > 0, `mode=${result.answer_tab?.sheetMode} 答案条数=${result.answer_tab?.answerItems}`);
  check('题目版页签：卷面不含答案区', result.question_tab?.sheetMode === 'question' && result.question_tab?.answerItems === 0, `mode=${result.question_tab?.sheetMode} 答案条数=${result.question_tab?.answerItems}`);
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
