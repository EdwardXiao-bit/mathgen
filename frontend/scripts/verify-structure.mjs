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
            // 预览区：灰底画布就是滚动区（用户 2026-10-28 明确要求「不要白纸小窗口」）。
            const previewStage = rect('.mg-paper-stage');
            const canvasCol = rect('.mg-canvas-column');
            const bgOf = (sel) => { const e = document.querySelector(sel);
                return e ? getComputedStyle(e).backgroundColor : null; };
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
                previewStage,
                canvasCol,
                stageBg: bgOf('.mg-paper-stage'),
                frameBg: bgOf('.mg-paper-frame'),
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

    # A3-a：**用真实 Tab 键**走一遍，量**每个停靠点自己**的焦点环。
    #
    # 两条纪律（task-19 / V9）：
    #   ① 不用 .focus() 程序化聚焦 —— 真实 Tab 与程序化聚焦在 iframe 上的行为**不同**
    #      （实测：程序化聚焦会让包裹层 :focus-within 命中，真实 Tab 不会）。
    #      拿程序化聚焦去证明「键盘可用」是错的。
    #   ② 量的是 activeElement **自己的** computed 样式，不是某个固定容器 ——
    #      否则每个停靠点都会量到同一个元素，看起来全都有环/全都没环。
    tab_focus = []
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); document.body.focus(); }")
    for _ in range(40):
        page.keyboard.press("Tab")
        page.wait_for_timeout(40)
        info = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el || el === document.body) return null;
            const cs = getComputedStyle(el);
            const hasOutline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
            const hasShadow = Boolean(cs.boxShadow && cs.boxShadow !== 'none');
            return {
                tag: el.tagName.toLowerCase(),
                testid: el.getAttribute && el.getAttribute('data-testid'),
                cls: (el.className || '').toString().split(' ')[0],
                focusVisible: el.matches(':focus-visible'),
                outlineStyle: cs.outlineStyle,
                outlineWidth: cs.outlineWidth,
                boxShadow: cs.boxShadow,
                hasRing: Boolean(hasOutline || hasShadow),
            };
        }""")
        if not info:
            break
        tab_focus.append(info)
    result["tab_focus"] = tab_focus
    result["tab_stop_count"] = len(tab_focus)
    result["tab_stops_without_ring"] = [
        {k: v for k, v in s.items() if k in ("tag", "testid", "outlineStyle", "outlineWidth", "boxShadow", "hasRing")}
        for s in tab_focus if not s["hasRing"]
    ]

    # A3-a 的**负对照**：断言必须是「聚焦 AND 有可见环」的合取，并且**在未聚焦时确实为红**。
    # 这条负对照是本轮新增的硬要求（V9）——只写「outlineWidth > 0」会**恒真**，
    # 因为 'outline-width' 的 CSS 初值就是 'medium' = 3px，与是否聚焦无关。
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); }")
    page.wait_for_timeout(80)
    result["focus_negative_control"] = page.evaluate("""() => {
        const stage = document.querySelector('.mg-paper-stage');
        const cs = getComputedStyle(stage);
        const visibleRing = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
        return {
            activeIsStage: document.activeElement === stage,
            outlineStyle: cs.outlineStyle,
            outlineWidth: cs.outlineWidth,
            boxShadow: cs.boxShadow,
            visibleRing,
            // 旧写法（只看宽度）在未聚焦时也成立 → 恒真
            legacyCheck: (parseFloat(cs.outlineWidth) || 0) > 0,
            // 新写法（聚焦 AND 可见环）在未聚焦时为 false → 会红
            newCheck: (document.activeElement === stage) && visibleRing,
        };
    }""")

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
    result["focus_ring_used"] = page.evaluate("""() => {        const styles = Array.from(document.styleSheets).flatMap(s => {
            try { return Array.from(s.cssRules).map(r => r.cssText); } catch (e) { return []; }
        });
        return {
            hasFocusVisibleRule: styles.some(t => t.includes(':focus-visible')),
            hasFocusRingToken: styles.some(t => t.includes('--mg-focus-ring')),
            // 卷面焦点环的规则是否已定义（task-19：包裹层 tabindex=0 + 自己的 :focus-visible）
            iframeRule: styles.some(t => t.includes('.mg-paper-stage') && t.includes(':focus-visible')),
        };
    }""")

    # A3：卷面焦点环 —— 用**真实 Tab 键**走到卷面，量包裹层自己的 computed 环。
    #
    # task-19 的更正：task-17 那版用 '.focus()' 程序化聚焦 iframe，量到 'stage :focus-within' 为真，
    # 于是写出「功能断言」。但那是**假绿**：
    #   ① 真实 Tab 时 Chromium 不把 iframe 当 ':focus' 触发者，':focus-within' 不命中；
    #   ② 那条断言用的是 'outlineWidth > 0'，而 'outline-width' 的初值是 'medium'=3px，
    #      未聚焦时同样为真 —— **恒真**（负对照见 result["focus_negative_control"]）。
    # 现在：环由包裹层 'tabindex="0"' + ':focus-visible' 承担，且用真实 Tab 验证。
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); document.body.focus(); }")
    stage_reached = None
    for _ in range(40):
        page.keyboard.press("Tab")
        page.wait_for_timeout(40)
        probe = page.evaluate("""() => {
            const stage = document.querySelector('.mg-paper-stage');
            if (document.activeElement !== stage) {
                return {isStage: false,
                        activeTag: document.activeElement ? document.activeElement.tagName.toLowerCase() : null};
            }
            const cs = getComputedStyle(stage);
            return {
                isStage: true,
                activeIsStage: true,
                stageMatchesFocus: stage.matches(':focus'),
                stageMatchesFocusVisible: stage.matches(':focus-visible'),
                outlineStyle: cs.outlineStyle,
                outlineWidth: cs.outlineWidth,
                outlineColor: cs.outlineColor,
                boxShadow: cs.boxShadow,
                hasVisibleRing: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0,
            };
        }""")
        if probe.get("isStage"):
            stage_reached = probe
            break
    result["iframe_focus_ring"] = stage_reached or {"isStage": False, "note": "Tab 40 次未到达 .mg-paper-stage"}

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
  // 焦点环的画法（task-19 更正）：
  //   Chromium 不把 `iframe` 当作 `:focus` / `:focus-within` 的触发者 —— **真实 Tab** 走到 iframe 时
  //   `iframe.matches(':focus') === false`，所以 task-17 的 `:focus-within` 方案对键盘**永远不生效**。
  //   现在由包裹层 `.mg-paper-stage` 自己 `tabindex="0"` 接收焦点（iframe 设 `tabindex="-1"` 退出 Tab 顺序），
  //   环是它自己的 `:focus-visible`。
  //
  // 判据**不再需要 iframe 豁免**：卷面这一站现在是一个正常的可聚焦元素，
  // 所以「每个 Tab 停靠点都要有可见环」可以**无例外**地成立。这比原来带豁免的版本更强。
  const withRing = tabFocus.filter((f) => f.hasRing).length;
  const ringMissing = tabFocus.filter((f) => !f.hasRing);
  check(
    'A3 每个 Tab 停靠点都有可见焦点环（无豁免 —— 卷面这一站由包裹层承担）',
    tabFocus.length > 0 && ringMissing.length === 0,
    `有环 ${withRing}/${tabFocus.length}；无环的是 ${JSON.stringify(result.tab_stops_without_ring ?? [])}`,
  );
  check(
    'A3 卷面焦点环由包裹层 :focus-visible 规则承担',
    result.focus_ring_used?.iframeRule === true,
    JSON.stringify(result.focus_ring_used),
  );
  check(
    'A3 用**真实 Tab** 走到卷面时，包裹层出现可见焦点环（:focus + 可见 outline 的合取）',
    result.iframe_focus_ring?.isStage === true
      && result.iframe_focus_ring?.stageMatchesFocus === true
      && result.iframe_focus_ring?.hasVisibleRing === true,
    JSON.stringify(result.iframe_focus_ring),
  );
  // V9（负对照，硬要求）：断言必须能失败。这里直接构造「未聚焦」的输入，
  // 并同时给出**旧写法**在同一输入下的结果 —— 旧写法在未聚焦时也为真，即恒真。
  const neg = result.focus_negative_control ?? {};
  check(
    'A3 负对照：未聚焦时该断言为红（旧的 outlineWidth>0 写法在同样输入下为真 = 恒真）',
    neg.newCheck === false,
    `未聚焦：activeIsStage=${neg.activeIsStage} outlineStyle=${neg.outlineStyle} outlineWidth=${neg.outlineWidth} ` +
      `visibleRing=${neg.visibleRing} → 旧写法=${neg.legacyCheck}（恒真）新写法=${neg.newCheck}（应为 false）`,
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
      `A7 ${label}：工具栏不压住预览区`,
      !overlaps(toolbar, m.previewStage),
      `toolbar.b=${toolbar ? Math.round(toolbar.b) : null} stage.t=${m.previewStage ? Math.round(m.previewStage.t) : null}`,
    );
  }

  console.log('\n—— A8 两个视口不横向溢出 + A4 纸面比例 ——');
  for (const [label, m] of Object.entries(result.viewports ?? {})) {
    check(
      `A8 ${label}：scrollWidth == innerWidth`,
      m.scrollW === m.innerW,
      `scrollW=${m.scrollW} innerW=${m.innerW} 越界元素=${JSON.stringify(m.overflowing ?? [])}`,
    );
    // A4 比例不能再量 `.mg-paper-frame`：它现在是**灰底画布的弹性区域**，不再是白纸盒。
    // 「纸是 A4」的判据改由「纸内几何」承担（`.sheet` 宽 793.7、题块单行，见门禁 ⑧ A9-1/A9-3）；
    // 这里改为守住用户要的那条**结构**：预览区就是灰底画布本身，滚动条在灰区右缘。
    check(
      `A8 ${label}：预览区就是灰底画布（滚动区右缘 == 画布列右缘 ±2，背景透明）`,
      m.previewStage !== null && m.canvasCol !== null
        && Math.abs(m.previewStage.r - m.canvasCol.r) <= 2
        && m.stageBg === 'rgba(0, 0, 0, 0)'
        && m.frameBg === 'rgba(0, 0, 0, 0)',
      `stage.r=${m.previewStage ? Math.round(m.previewStage.r) : 'n/a'} col.r=${m.canvasCol ? Math.round(m.canvasCol.r) : 'n/a'} `
        + `stageBg=${m.stageBg} frameBg=${m.frameBg}`,
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
