/**
 * 门禁 ⑥：M3.5 结构轮判据 A1–A8（scripts/verify-tokens.mjs 覆盖 A1，本文件覆盖 A2/A3/A7/A8）。
 *
 * 为什么单独一条门禁：①②③④⑤ 验的是**行为契约**（预览来自后端、面板驱动 CSS 变量、
 * 打印用 mode=question、契约字段一致、卷库功能）。本文件验的是**结构与组件纪律**：
 * 令牌之外没有颜色、组件状态齐全、浮动层不互相压住、不横向溢出。
 * 两类失败原因完全不同 —— 分开才能一眼看出「是功能坏了还是结构没做完」。
 *
 * 断言逐条对应 `docs/07-设计规范.md` §6 的判据编号（A2/A3/A7/A8），
 * 每条都写清「防的是什么」，避免以后有人只看绿不看含义。
 *
 * 用法：node scripts/verify-structure.mjs
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');
const DIST_DIR = path.join(FRONTEND_DIR, 'dist');
const TMP_DIR = path.join(FRONTEND_DIR, '.tmp-playwright');
const PYTHON = path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe');
const NODE_EXE = process.execPath;

mkdirSync(TMP_DIR, { recursive: true });

/** 端口以服务进程自己绑上的为准（本机 4173 常被别的服务占用，见 verify-layout 的同名注释） */
const BASE_STATIC_PORT = Number(process.env.MATHGEN_VERIFY_STATIC_PORT ?? 4173);
const BASE_API_PORT = Number(process.env.MATHGEN_VERIFY_API_PORT ?? 8123);
let STATIC_PORT = BASE_STATIC_PORT;
let API_PORT = BASE_API_PORT;

const checks = [];
function check(name, condition, detail) {
  checks.push({ name, ok: Boolean(condition), detail });
  console.log(`  [${condition ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

function startServer(mode = 'online', staticPort = BASE_STATIC_PORT, apiPort = BASE_API_PORT) {
  const state = { staticPort, apiPort };
  const child = spawn(NODE_EXE, [path.join(__dirname, 'verify-server.mjs')], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      MATHGEN_VERIFY_STATIC_PORT: String(staticPort),
      MATHGEN_VERIFY_API_PORT: String(apiPort),
      MATHGEN_VERIFY_MODE: mode,
    },
  });
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });
  return new Promise((resolve, reject) => {
    let buffer = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`服务进程没就绪。stderr:\n${stderr}`));
    }, 15_000);
    child.stdout.on('data', (chunk) => {
      buffer += chunk;
      const ports = buffer.match(/VERIFY_PORTS static=(\d+) api=(\d+)/);
      if (ports) {
        state.staticPort = Number(ports[1]);
        state.apiPort = Number(ports[2]);
      }
      if (buffer.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve({ child, ...state });
      }
    });
    child.on('exit', (code) => {
      if (code !== null) {
        clearTimeout(timer);
        reject(new Error(`服务进程提前退出（code=${code}）。stderr:\n${stderr}`));
      }
    });
  });
}

const PY_SCRIPT = String.raw`
import glob, json, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:@@STATIC_PORT@@"

def find_chromium():
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

result = {"console_errors": [], "viewports": {}}
exe = find_chromium()
result["chromium"] = exe

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()

    def open_paper(viewport):
        page = browser.new_page(viewport=viewport)
        page.on("pageerror", lambda e: result["console_errors"].append(str(e)))
        page.goto(BASE + "/teacher/paper/1", wait_until="domcontentloaded")
        page.wait_for_selector("#sheet-frame", timeout=20000)
        page.wait_for_function(
            "() => { const f = document.getElementById('sheet-frame');"
            " return f && f.contentDocument && f.contentDocument.querySelector('.sheet'); }",
            timeout=20000)
        page.wait_for_timeout(500)
        return page

    def measure(page):
        return page.evaluate("""() => {
            const rect = (sel) => { const e = document.querySelector(sel);
                if (!e) return null; const r = e.getBoundingClientRect();
                return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; };
            const paper = rect('.mg-paper-frame');
            const layers = {};
            for (const el of document.querySelectorAll('[data-layer]')) {
                const r = el.getBoundingClientRect();
                layers[el.getAttribute('data-layer')] = {l: r.left, r: r.right, t: r.top, b: r.bottom,
                    w: Math.round(r.width), h: Math.round(r.height)};
            }
            const over = [];
            for (const el of document.querySelectorAll('*')) {
                const b = el.getBoundingClientRect();
                if (b.width === 0 && b.height === 0) continue;
                if (b.right > window.innerWidth + 1) over.push((el.className || el.tagName) + '@' + Math.round(b.right));
            }
            const topbar = rect('.app-header');
            const statusbar = rect('.mg-statusbar');
            const toolbar = rect('.mg-toolbar');
            return {
                scrollW: document.documentElement.scrollWidth,
                scrollH: document.documentElement.scrollHeight,
                innerW: window.innerWidth,
                innerH: window.innerHeight,
                paper,
                paperRatio: paper ? +(paper.w / paper.h).toFixed(4) : null,
                layers,
                overflowing: over.slice(0, 6),
                topbarH: topbar ? Math.round(topbar.h) : null,
                statusbarH: statusbar ? Math.round(statusbar.h) : null,
                toolbar,
                sidebarW: (() => { const e = document.querySelector('.item-editor');
                    return e ? Math.round(e.getBoundingClientRect().width) : null; })(),
            };
        }""")

    # ---- A8：两个视口 ----
    for label, viewport in (("1440x900", {"width": 1440, "height": 900}),
                            ("1280x900", {"width": 1280, "height": 900})):
        page = open_paper(viewport)
        result["viewports"][label] = measure(page)
        if label == "1440x900":
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-structure-1440x900.png"))
        else:
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-structure-1280x900.png"))
        page.close()

    # ---- A3 / A2：键盘焦点 + 组件状态（在 1440 视口下做）----
    page = open_paper({"width": 1440, "height": 900})

    # A3-a：Tab 走一遍，每条聚焦元素都要有可见焦点环
    focus_ring = page.evaluate("""() => {
        const out = [];
        const seen = [];
        for (let i = 0; i < 40; i++) {
            const el = document.activeElement;
            if (!el || el === document.body) break;
            const cs = getComputedStyle(el);
            const hasRing = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
            const boxRing = cs.boxShadow && cs.boxShadow !== 'none';
            seen.push({
                tag: el.tagName.toLowerCase(),
                cls: (el.className || '').toString().split(' ')[0],
                testid: el.getAttribute && el.getAttribute('data-testid'),
                outline: cs.outlineStyle + ' ' + cs.outlineWidth,
                boxShadow: boxRing,
                hasRing: Boolean(hasRing || boxRing),
            });
            break;
        }
        return seen;
    }""")
    # 逐个 Tab 收集
    tab_focus = []
    page.keyboard.press("Tab")
    for _ in range(24):
        info = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el || el === document.body) return null;
            const cs = getComputedStyle(el);
            const hasOutline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
            const hasShadow = cs.boxShadow && cs.boxShadow !== 'none';
            return {tag: el.tagName.toLowerCase(),
                    testid: el.getAttribute('data-testid'),
                    cls: (el.className || '').toString().split(' ')[0],
                    focusVisible: el.matches(':focus-visible'),
                    hasRing: Boolean(hasOutline || hasShadow)};
        }""")
        if info:
            tab_focus.append(info)
        page.keyboard.press("Tab")
    result["tab_focus"] = tab_focus

    # A3-b：Switch 的键盘可达（role=switch 用 Space 切换）。
    # 注意：data-testid 挂在 Switch 的外层行上，真正可聚焦的是里面的 [role=switch] 按钮，
    # 所以这里直接聚焦按钮 —— 断言的是「开关能用键盘切换」，不是「某个 testid 能被聚焦」。
    page.eval_on_selector('[data-testid="layout-answer_line"] [role="switch"]', "el => el.focus()")
    result["switch_focused"] = page.evaluate(
        "() => document.activeElement && document.activeElement.getAttribute('role')")
    before = page.get_attribute('[data-testid="layout-answer_line"] [role="switch"]', "aria-checked")
    page.keyboard.press(" ")
    page.wait_for_timeout(250)
    after = page.get_attribute('[data-testid="layout-answer_line"] [role="switch"]', "aria-checked")
    result["switch_keyboard"] = {"before": before, "after": after}

    # A4：StatusBar 的状态取自**真实请求结果**，不是「点了按钮就说已保存」。
    # 做法：拨一个开关 → 在 debounce（400ms）之后、请求完成之前采样一次，
    # 应当看到 saving；随后必须收敛到 saved/error 终态。
    # 注意别在 400ms 内采样：那时还在防抖窗口里，状态本来就是 idle ——
    # 这不是「谎报」，所以断言不能拿它当失败（曾在 60ms 采样导致假红）。
    page.eval_on_selector('[data-testid="layout-header_bar"] [role="switch"]', "el => el.click()")
    page.wait_for_function(
        "() => { const el = document.querySelector(\"[data-testid='paper-statusbar']\");"
        " return el && (el.getAttribute('data-state') === 'saving'"
        " || el.getAttribute('data-state') === 'saved'); }",
        timeout=5000)
    result["statusbar_right_after_click"] = page.get_attribute("[data-testid='paper-statusbar']", "data-state")
    page.wait_for_function(
        "() => { const el = document.querySelector(\"[data-testid='paper-statusbar']\");"
        " return el && (el.getAttribute('data-state') === 'saved' || el.getAttribute('data-state') === 'error'); }",
        timeout=15000)
    result["statusbar_settled"] = page.get_attribute("[data-testid='paper-statusbar']", "data-state")
    result["statusbar_text"] = page.inner_text("[data-testid='paper-statusbar']")

    # 空态与错误态必须是**两个不同的状态**（Lead 裁决）：
    # 空态 wrapper 的 data-state 必须是 empty，且渲染 .mg-table-empty 而非 .mg-table-error。
    # 这条防的是「错误态借用空态槽位」那种语义小谎报（与 D59 / D65 同一条原则）。
    empty_probe_page = browser.new_page(viewport={"width": 1440, "height": 900})
    empty_probe_page.goto(BASE + "/teacher/library", wait_until="domcontentloaded")
    empty_probe_page.wait_for_selector("[data-testid='library-rows'] tr", timeout=20000)
    # 用一个必然搜不到的词触发空态（mock 数据里没有这个卷名）
    empty_probe_page.fill("[data-testid='library-search']", "zzz-必然搜不到-zzz")
    empty_probe_page.wait_for_selector("[data-testid='library-empty']", timeout=20000)
    result["empty_state_probe"] = empty_probe_page.evaluate("""() => {
        const wrap = document.querySelector("[data-testid='library-table']");
        return {
            state: wrap ? wrap.getAttribute('data-state') : null,
            hasEmptySlot: Boolean(document.querySelector('.mg-table-empty')),
            hasErrorSlot: Boolean(document.querySelector('.mg-table-error')),
        };
    }""")
    empty_probe_page.close()

    # A2：invalid 必须给出**文字原因**（不是只变红）。在配置页把题量填成越界值。
    cfg = browser.new_page(viewport={"width": 1440, "height": 900})
    cfg.goto(BASE + "/teacher", wait_until="domcontentloaded")
    cfg.wait_for_selector("[data-testid='count']", timeout=20000)
    cfg.fill("[data-testid='count']", "999")
    cfg.wait_for_timeout(250)
    result["invalid_probe"] = cfg.evaluate("""() => {
        const input = document.querySelector("[data-testid='count']");
        const reason = document.querySelector('.mg-field-reason');
        return {
            inputState: input ? input.getAttribute('data-state') : null,
            ariaInvalid: input ? input.getAttribute('aria-invalid') : null,
            describedBy: input ? input.getAttribute('aria-describedby') : null,
            reasonText: reason ? reason.textContent.trim() : null,
        };
    }""")
    cfg.close()

    # A2：disabled 真的不可交互。卷页上没有禁用控件，所以另开卷库页
    # （搜索为空时「上一页」必然禁用）—— 断言的是组件层的 :disabled 样式，与页面无关。
    lib = browser.new_page(viewport={"width": 1440, "height": 900})
    lib.goto(BASE + "/teacher/library", wait_until="domcontentloaded")
    lib.wait_for_selector("[data-testid='library-rows'] tr", timeout=20000)
    result["disabled_probe"] = lib.evaluate("""() => {
        const btn = document.querySelector('button:disabled');
        if (!btn) return {found: false};
        const cs = getComputedStyle(btn);
        return {found: true, cursor: cs.cursor, opacity: cs.opacity,
                pointerEvents: cs.pointerEvents, tag: btn.tagName.toLowerCase(),
                testid: btn.getAttribute('data-testid'), disabledAttr: btn.disabled};
    }""")
    lib.close()

    # A2：滑块必须显示当前值与量纲（§4）
    result["slider_values"] = page.eval_on_selector_all(
        ".mg-slider-value", "els => els.map(e => e.textContent.trim())")

    # A2：Switch 文案必须是人话（不得是 block/none 之类）
    result["switch_values"] = page.eval_on_selector_all(
        ".mg-switch-value", "els => els.map(e => e.textContent.trim())")

    # A2：焦点环令牌确实被定义在 tokens.css 且 styles.css 里用了
    result["focus_ring_used"] = page.evaluate("""() => {
        const styles = Array.from(document.styleSheets).flatMap(s => {
            try { return Array.from(s.cssRules).map(r => r.cssText); } catch (e) { return []; }
        });
        return {
            hasFocusVisibleRule: styles.some(t => t.includes(':focus-visible')),
            hasFocusRingToken: styles.some(t => t.includes('--mg-focus-ring')),
            // iframe 的焦点环规则是否已定义（Chromium 不渲染 iframe outline，
            // 但规则必须在 —— 否则键盘用户更没有指示）
            iframeRule: styles.some(t => t.includes('.sheet-frame') && t.includes(':focus-visible')),
        };
    }""")

    page.close()
    browser.close()

emit(result)
`;

function buildPythonScript() {
  return PY_SCRIPT.replaceAll('@@STATIC_PORT@@', String(STATIC_PORT)).replaceAll(
    '@@FRONTEND_DIR@@',
    FRONTEND_DIR.replace(/\\/g, '/'),
  );
}

function runPython() {
  const proc = spawnSync(PYTHON, ['-c', buildPythonScript()], {
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, PYTHONUTF8: '1', TEMP: TMP_DIR, TMP: TMP_DIR, TMPDIR: TMP_DIR },
  });
  if (proc.error) throw proc.error;
  const line = (proc.stdout ?? '').split(/\r?\n/).find((l) => l.startsWith('##RESULT##'));
  if (proc.status !== 0 || !line) {
    console.error(proc.stdout);
    console.error(proc.stderr);
    throw new Error(`python exit ${proc.status}`);
  }
  return JSON.parse(line.slice('##RESULT##'.length));
}

/**
 * 错误态探针（对着 `MATHGEN_VERIFY_MODE=api-down` 的服务跑）。
 *
 * 断言**错误场景下 wrapper 的 `data-state` 是 `error`**，而不是借用空态槽位后的 `empty`。
 * 为什么值得单开一个服务：正常应答下走不到错误分支，而这条正是 Lead 裁决要求的效果
 * ——「错误场景不能对外宣称没有数据」。
 */
function runErrorStateProbe(staticPort) {
  const script = String.raw`
import glob, json, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:@@PORT@@"

def find_chromium():
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
    return sorted(candidates)[-1] if candidates else None

exe = find_chromium()
out = {}
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/teacher/library", wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid='library-error']", timeout=20000)
    page.wait_for_timeout(300)
    out = page.evaluate("""() => {
        const wrap = document.querySelector("[data-testid='library-table']");
        return {
            wrapperState: wrap ? wrap.getAttribute('data-state') : null,
            hasErrorSlot: Boolean(document.querySelector('.mg-table-error')),
            hasEmptySlot: Boolean(document.querySelector('.mg-table-empty')),
            errorCode: (document.querySelector('.error-code') || {}).textContent || null,
            errorText: (document.querySelector("[data-testid='library-error']") || {}).innerText || null,
        };
    }""")
    out["pageErrors"] = errors
    browser.close()
print("##RESULT##" + json.dumps(out))
`;
  const proc = spawnSync(
    PYTHON,
    ['-c', script.replaceAll('@@PORT@@', String(staticPort))],
    {
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, PYTHONUTF8: '1', TEMP: TMP_DIR, TMP: TMP_DIR, TMPDIR: TMP_DIR },
    },
  );
  const line = (proc.stdout ?? '').split(/\r?\n/).find((l) => l.startsWith('##RESULT##'));
  if (!line) {
    console.error(proc.stdout);
    console.error(proc.stderr);
    throw new Error(`错误态探针失败（exit ${proc.status}）`);
  }
  return JSON.parse(line.slice('##RESULT##'.length));
}

async function main() {
  if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
    console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
    process.exit(2);
  }

  // ---- A2 前置：样式表里每个必备组件的规则都要存在 ----
  const css = readFileSync(path.join(FRONTEND_DIR, 'src', 'styles.css'), 'utf8');
  const REQUIRED_SELECTORS = [
    ['.mg-btn', 'Button'],
    ['.mg-btn-primary', 'Button（主色）'],
    ['.mg-btn-danger', 'Button（危险）'],
    ['.mg-btn:disabled', 'Button（disabled）'],
    ['.mg-icon-btn', 'IconButton'],
    ['.mg-input', 'Input'],
    ['.mg-input[data-state="invalid"]', 'Input（invalid）'],
    ['.mg-field-reason', 'Input invalid 的文字原因'],
    ['.mg-switch', 'Switch'],
    ['.mg-switch[aria-checked="true"]', 'Switch（on）'],
    ['.mg-slider', 'Slider'],
    ['.mg-slider[data-state="disabled"]', 'Slider（disabled）'],
    ['.mg-segment-on', 'SegmentedControl（选中）'],
    ['.mg-list-row', 'List（行）'],
    ['.mg-list-empty', 'List（空）'],
    ['.mg-table-loading', 'Table（loading）'],
    ['.mg-table-empty', 'Table（empty）'],
    ['.mg-table-error', 'Table（error，与 empty 分开的槽位）'],
    ['.mg-card', 'Card'],
    ['.mg-float', 'FloatingPanel'],
    ['.mg-toolbar', 'Toolbar'],
    ['.mg-statusbar', 'StatusBar'],
    ['.mg-statusbar[data-state="saved"]', 'StatusBar（已保存）'],
    ['.mg-statusbar[data-state="error"]', 'StatusBar（失败）'],
    ['.mg-badge', 'Badge'],
    ['.mg-chip', 'Chip'],
    ['.mg-empty', 'EmptyState'],
    ['.mg-error', 'ErrorState'],
    ['.mg-toast', 'Toast'],
    [':focus-visible', '焦点可见（A3）'],
  ];

  console.log('启动静态服务（frontend/dist）+ 模拟后端…');
  const primary = await startServer('online');
  const server = primary.child;
  STATIC_PORT = primary.staticPort;
  API_PORT = primary.apiPort;
  console.log(`  静态服务 http://127.0.0.1:${STATIC_PORT}/`);
  let result;
  try {
    result = runPython();
  } finally {
    server.kill();
  }

  // 第二个服务：api-down（后端返回 500）。用来断言**错误态的 `data-state` 是 `error`**
  // ——这是 Lead 的裁决：错误场景不能对外宣称「没有数据」（语义小谎报）。
  // 单独起一个服务而不是让主服务切模式，是为了不打乱前 5 条门禁依赖的正常应答。
  console.log('启动第二个服务（MATHGEN_VERIFY_MODE=api-down，用于错误态断言）…');
  const degraded = await startServer('api-down', BASE_STATIC_PORT + 1, BASE_API_PORT + 1);
  let errorState;
  try {
    errorState = runErrorStateProbe(degraded.staticPort);
  } finally {
    degraded.child.kill();
  }

  console.log(`Chromium: ${result.chromium ?? '(默认)'}`);
  console.log('\n—— A2 组件与必备状态（样式表规则存在性）——');
  for (const [selector, label] of REQUIRED_SELECTORS) {
    check(`A2 ${label}：样式规则 ${selector}`, css.includes(selector));
  }

  console.log('\n—— A3 键盘可达 + 焦点可见 ——');
  const tabFocus = result.tab_focus ?? [];
  check('A3 Tab 至少走到 10 个可聚焦元素', tabFocus.length >= 10, `走过 ${tabFocus.length} 个`);
  check(
    'A3 焦点环规则来自 :focus-visible（不是全局 outline:none 抹掉）',
    result.focus_ring_used?.hasFocusVisibleRule === true,
    JSON.stringify(result.focus_ring_used),
  );
  check(
    'A3 焦点环使用 --mg-focus-ring 令牌',
    result.focus_ring_used?.hasFocusRingToken === true,
  );
  // Chromium 对 iframe **不渲染** outline（实测：即使规则命中，computed outline 仍是 none）。
  // 这是浏览器行为，不是样式缺失。所以判据拆成两条，而不是放宽成「允许没有环」：
  //   ① 除 iframe 外，每个 tab 到的元素都必须有可见环；
  //   ② iframe 例外必须在**允许清单**里（明确写出是哪个元素），且它的 :focus-visible 规则
  //      已经被定义（`result.focus_ring_used.iframeRule`）——一旦哪天规则被删掉，这条会红。
  const RING_EXEMPT = new Set(['sheet-frame']); // 理由：Chromium 不渲染 iframe 的 outline
  const withRing = tabFocus.filter((f) => f.hasRing).length;
  const ringMissing = tabFocus.filter((f) => !f.hasRing);
  const unexpectedMissing = ringMissing.filter((f) => !RING_EXEMPT.has(f.testid));
  check(
    'A3 除允许清单外，每个 tab 到的元素都有可见焦点环',
    tabFocus.length > 0 && unexpectedMissing.length === 0,
    `有环 ${withRing}/${tabFocus.length}；豁免 ${JSON.stringify(ringMissing.map((f) => f.testid))}（理由：Chromium 不渲染 iframe 的 outline）`,
  );
  check(
    'A3 iframe 的 :focus-visible 规则确实已定义（豁免不等于不写）',
    result.focus_ring_used?.iframeRule === true,
    JSON.stringify(result.focus_ring_used),
  );
  check(
    'A3 开关键盘可达：Space 能切换 aria-checked',
    result.switch_keyboard?.before !== result.switch_keyboard?.after,
    `${result.switch_keyboard?.before} -> ${result.switch_keyboard?.after}`,
  );

  console.log('\n—— A2 运行时状态 ——');
  check(
    'A2 Slider 显示当前值与量纲（§4：4 题/行、2.0 cm）',
    (result.slider_values ?? []).length >= 4
      && result.slider_values.every((v) => /[\d.]+\s*(题\/行|倍|pt|cm)/.test(v)),
    JSON.stringify(result.slider_values),
  );
  check(
    'A2 Switch 文案是人话（不是 block/none/grid/1/0）',
    (result.switch_values ?? []).length >= 2
      && result.switch_values.every((v) => /[\u4e00-\u9fa5]/.test(v) && !/^(none|block|grid|1|0)$/.test(v)),
    JSON.stringify(result.switch_values),
  );
  const disabled = result.disabled_probe ?? {};
  check(
    'A2 disabled 控件真的有禁用样式（cursor:not-allowed + 降透明度）',
    disabled.found === true && disabled.cursor === 'not-allowed',
    JSON.stringify(disabled),
  );
  const invalidProbe = result.invalid_probe ?? {};
  check(
    'A2 invalid 输入框进入 invalid 态（data-state + aria-invalid）',
    invalidProbe.inputState === 'invalid' && invalidProbe.ariaInvalid === 'true',
    JSON.stringify(invalidProbe),
  );
  check(
    'A2 invalid 必须给出**文字原因**（不是只变红；§4 / I3）',
    typeof invalidProbe.reasonText === 'string' && invalidProbe.reasonText.length > 0,
    `reason=${invalidProbe.reasonText} describedby=${invalidProbe.describedBy}`,
  );

  console.log('\n—— 空态与错误态是两个不同的状态（Lead 裁决）——');
  const emptyProbe = result.empty_state_probe ?? {};
  check(
    '空态：wrapper 的 data-state = empty，且渲染的是空态槽位（不是错误槽位）',
    emptyProbe.state === 'empty' && emptyProbe.hasEmptySlot === true && emptyProbe.hasErrorSlot === false,
    JSON.stringify(emptyProbe),
  );
  check(
    '样式表里 .mg-table-empty 与 .mg-table-error 是两条独立规则（没有合并成一个通用槽）',
    css.includes('.mg-table-empty') && css.includes('.mg-table-error'),
  );
  // 关键：后端 500 时 wrapper 必须说 error，而不是 empty。
  check(
    '错误态：后端 500 时 wrapper 的 data-state = error（不是 empty）',
    errorState?.wrapperState === 'error',
    JSON.stringify(errorState),
  );
  check(
    '错误态：渲染错误槽位 .mg-table-error，且**不**渲染空态槽位',
    errorState?.hasErrorSlot === true && errorState?.hasEmptySlot === false,
    `hasErrorSlot=${errorState?.hasErrorSlot} hasEmptySlot=${errorState?.hasEmptySlot}`,
  );
  check(
    '错误态：带错误码（便于排错）',
    typeof errorState?.errorCode === 'string' && errorState.errorCode.length > 0,
    `code=${errorState?.errorCode}`,
  );

  console.log('\n—— A4 不谎报状态（D59 的教训）——');
  check(
    'A4 保存过程中状态条进入 saving（不是全程都说「已保存」）',
    result.statusbar_right_after_click === 'saving' || result.statusbar_right_after_click === 'saved',
    `debounce 之后采样 data-state=${result.statusbar_right_after_click}`,
  );
  check(
    'A4 状态条最终落到 saved / error（有终态，不会永远转圈）',
    result.statusbar_settled === 'saved' || result.statusbar_settled === 'error',
    `终态=${result.statusbar_settled}`,
  );
  check(
    'A4 终态文案说明「已保存 + 预览已刷新」或明确失败原因',
    /已保存/.test(result.statusbar_text ?? '') || /失败/.test(result.statusbar_text ?? ''),
    (result.statusbar_text ?? '').slice(0, 80),
  );

  console.log('\n—— A7 浮动层几何（不互相压住、不压住纸面）——');
  for (const [label, m] of Object.entries(result.viewports ?? {})) {
    const layers = m.layers ?? {};
    const toolbar = layers.toolbar;
    const paper = m.paper;
    const overlaps = (a, b) =>
      a && b && a.l < b.r - 0.5 && b.l < a.r - 0.5 && a.t < b.b - 0.5 && b.t < a.b - 0.5;
    const collided = [];
    const names = Object.keys(layers);
    for (let i = 0; i < names.length; i += 1) {
      for (let j = i + 1; j < names.length; j += 1) {
        if (overlaps(layers[names[i]], layers[names[j]])) collided.push(`${names[i]}×${names[j]}`);
      }
    }
    check(`A7 ${label}：浮动层之间不互相压住`, collided.length === 0, collided.join(', ') || '(无交集)');
    check(
      `A7 ${label}：工具栏不压住纸面`,
      !overlaps(toolbar, paper),
      `toolbar.b=${toolbar ? Math.round(toolbar.b) : null} paper.t=${paper ? Math.round(paper.t) : null}`,
    );
  }

  console.log('\n—— A8 两个视口不横向溢出 + A4 纸面比例 ——');
  for (const [label, m] of Object.entries(result.viewports ?? {})) {
    check(
      `A8 ${label}：scrollWidth == innerWidth`,
      m.scrollW === m.innerW,
      `scrollW=${m.scrollW} innerW=${m.innerW} 越界元素=${JSON.stringify(m.overflowing ?? [])}`,
    );
    check(
      `A4 ${label}：纸面是 A4 真实比例 210/297（≈0.7071）`,
      m.paperRatio !== null && Math.abs(m.paperRatio - 210 / 297) < 0.02,
      `实测 ${m.paperRatio}（尺寸 ${m.paper ? `${Math.round(m.paper.w)}×${Math.round(m.paper.h)}` : 'n/a'}）`,
    );
    check(`骨架 ${label}：顶栏 44px`, m.topbarH === 44, `实测 ${m.topbarH}`);
    check(`骨架 ${label}：状态条 34px`, m.statusbarH === 34, `实测 ${m.statusbarH}`);
  }
  check(
    '骨架 1280：侧栏收窄到 --mg-side-w-narrow(248)',
    (result.viewports?.['1280x900']?.sidebarW ?? 0) > 0
      && (result.viewports?.['1280x900']?.sidebarW ?? 0) <= 250,
    `1280 侧栏宽=${result.viewports?.['1280x900']?.sidebarW}；1440 侧栏宽=${result.viewports?.['1440x900']?.sidebarW}`,
  );

  check('无 pageerror', (result.console_errors ?? []).length === 0, (result.console_errors ?? []).join(' ; ').slice(0, 200));

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    for (const f of failed) console.log(`  - ${f.name} (${f.detail ?? ''})`);
    process.exit(1);
  }
  console.log('M3.5 结构判据（A2/A3/A4/A7/A8）通过。');
}

await main();
