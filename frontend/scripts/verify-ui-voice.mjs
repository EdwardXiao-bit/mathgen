/**
 * 门禁 ⑩：视觉轮的两条可判定判据（D70）。
 *
 * ① **界面不得出现开发者文本**：决策编号（`D\d+`）、CSS 变量名（`--xxx`）、
 *    裸 CSS 关键字（`none` / `block` / `grid` / `flex` / `inline`）、需求编号（`FR-xx`）、
 *    内部实现痕迹（`uvicorn` / `/api/` / `paper.xxx` / `stepwise` …）。
 *    这条来自用户逐条反馈：老师在界面上看到 `--page-margin`、`D56` 毫无意义，还暴露内部。
 *    （早先这些「调试小字」是刻意加的，用来"证明它在工作"；用户判定不该出现在产品里 —— 以用户为准。）
 *
 * ② **灰桌面底只能用于画布**：卷库页 / 配置页 / 发布页这些「列表与表单类」页面的底色
 *    必须取自浅底令牌（`--mg-surface-sunken` = #f7f8f9），**不得**是 `--mg-desk-*`（#8e939a 一带的灰）。
 *    这条来自用户："白卡片浮在灰桌面上" 是最容易一眼看出不专业的地方。
 *
 * ## 判据怎么保证不是自证
 * - 「无开发者文本」扫的是**渲染后的可见文本节点**（`getBoundingClientRect` 非零 + 祖先可见），
 *   不是源码字符串 —— 源码里全是 `D56` 注释，源码扫法会立刻失效。
 *   `display:none` 的验收用 dump 因此不计入（它确实对用户不可见）。
 * - 「灰底」读的是**计算后的背景色**并与令牌值比对，不是看 CSS 里写了哪个变量名。
 * - 两条都配了**留在仓库里的负对照**（`scripts/verify-negative-controls.mjs`，V9.1）。
 *
 * 用法：node scripts/verify-ui-voice.mjs
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
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

const BASE_STATIC_PORT = Number(process.env.MATHGEN_VERIFY_STATIC_PORT ?? 4173);
const BASE_API_PORT = Number(process.env.MATHGEN_VERIFY_API_PORT ?? 8123);

/** 令牌里「浅底」与「灰桌面」的期望值（与 design/tokens.css 一致；这里独立写一遍，避免自证）。 */
const SUNKEN = { r: 247, g: 248, b: 249 }; // --mg-surface-sunken: #f7f8f9
const DESK = { r: 142, g: 147, b: 154 }; // --mg-desk-1: #8e939a

const checks = [];
function check(name, ok, detail) {
  checks.push({ name, ok: Boolean(ok), detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

/** 解析 `rgb(r, g, b)` / `rgba(r, g, b, a)`；解析不了返回 null。 */
function parseRgb(text) {
  if (typeof text !== 'string') return null;
  const m = text.match(/^rgba?\(([^)]+)\)$/);
  if (!m) return null;
  const nums = m[1].split(',').map((s) => Number(s.trim()));
  if (nums.length < 3 || nums.some((n) => Number.isNaN(n))) return null;
  return { r: nums[0], g: nums[1], b: nums[2], a: nums.length > 3 ? nums[3] : 1 };
}

/** 与目标色比较（默认容差 2，避免取整误差）。`a === 0` 视为不匹配（透明不算「有这个底色」）。 */
function closeTo(rgb, target, tol = 2) {
  if (!rgb || rgb.a === 0) return false;
  return (
    Math.abs(rgb.r - target.r) <= tol &&
    Math.abs(rgb.g - target.g) <= tol &&
    Math.abs(rgb.b - target.b) <= tol
  );
}

function startServer() {
  const child = spawn(NODE_EXE, [path.join(__dirname, 'verify-server.mjs')], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      MATHGEN_VERIFY_STATIC_PORT: String(BASE_STATIC_PORT),
      MATHGEN_VERIFY_API_PORT: String(BASE_API_PORT),
      MATHGEN_VERIFY_MODE: 'online',
    },
  });
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  let stderr = '';
  child.stderr.on('data', (c) => {
    stderr += c;
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
      if (ports && buffer.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve({ child, staticPort: Number(ports[1]) });
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

/** ⚠️ String.raw 模板块：内部不要用反引号（会提前终止模板，M3.5 踩过三次）。 */
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
        os.path.join(os.environ.get("HOME", ""), ".cache", "ms-playwright"),
    ]
    candidates = []
    for root in roots:
        candidates += glob.glob(os.path.join(root, "chromium-*", "chrome-win64", "chrome.exe"))
        candidates += glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome"))
    return sorted(candidates)[-1] if candidates else None

# 可见文本扫描：只看**渲染后可见**的文本节点。
DEV_SCAN = """() => {
    const vis = (el) => {
        if (!el) return false;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
        if (el.closest('[hidden]') || el.closest('[aria-hidden="true"]')) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const hits = [];
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
        const el = n.parentElement;
        if (!el) continue;
        let own = '';
        for (const c of el.childNodes) if (c.nodeType === 3) own += c.textContent;
        own = own.trim();
        if (!own) continue;
        if (!vis(el)) continue;
        const f = [];
        const dec = own.match(/\bD\d+\b/g);
        if (dec) f.push('决策编号:' + dec.join(','));
        const cssvar = own.match(/--[a-z][a-z0-9-]*/g);
        if (cssvar) f.push('CSS变量:' + cssvar.join(','));
        const kw = own.match(/\b(none|block|grid|flex|inline)\b/g);
        if (kw) f.push('CSS关键字:' + kw.join(','));
        const fr = own.match(/\bFR-[A-Z]?\d+/g);
        if (fr) f.push('需求编号:' + fr.join(','));
        if (/uvicorn|\/api\/|http:\/\/127\.|SQLite|JSON|stepwise|paper\.[a-z_]/.test(own)) f.push('内部实现');
        if (/\b(undefined|null|NaN)\b/.test(own)) f.push('未定义值');
        if (f.length) hits.push({cls: (typeof el.className === 'string' ? el.className : el.tagName).slice(0, 44),
                                 text: own.slice(0, 110), found: f});
    }
    return hits;
}"""

# 页面底色：读**计算后**的背景色，并同时报告 body 与根容器。
BG_SCAN = """() => {
    const root = document.querySelector('.config-page, .library-page, .publish-page');
    const cs = (el) => {
        if (!el) return null;
        const c = getComputedStyle(el);
        return {bg: c.backgroundColor, bgImage: c.backgroundImage};
    };
    return {body: cs(document.body), root: cs(root),
            rootSelector: root ? (root.className || root.tagName) : null,
            canvas: cs(document.querySelector('.mg-canvas'))};
}"""

def parse_rgb_unused(text):
    return None

CASES = [
    ("config", "/teacher", "[data-dimension='answer_form']"),
    ("library", "/teacher/library", "[data-testid='library-rows'] tr"),
    ("paper", "/teacher/paper/1", "#sheet-frame"),
    ("paper-answer", "/teacher/paper/1", "#sheet-frame"),
    ("publish", "/teacher/paper/1/publish", ".publish-page"),
]

result = {"pages": {}, "console_errors": []}
exe = find_chromium()
result["chromium"] = exe

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    for name, url, wait in CASES:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: result["console_errors"].append(str(e)))
        page.goto(BASE + url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(wait, timeout=15000)
        except Exception:
            pass
        if name == "paper-answer":
            try:
                page.click("[data-testid='preview-tab-answer']", timeout=5000)
            except Exception:
                pass
        page.wait_for_timeout(800)
        result["pages"][name] = {"devHits": page.evaluate(DEV_SCAN), "bg": page.evaluate(BG_SCAN)}
        if name == "config":
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-voice-config-1440x900.png"))
        if name == "library":
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-voice-library-1440x900.png"))
        page.close()

    # 1280 视口：不横向溢出（不回归）
    for label, vp in (("1280x900", {"width": 1280, "height": 900}),):
        for name, url, wait in (("config", "/teacher", "[data-dimension='answer_form']"),
                                ("library", "/teacher/library", "[data-testid='library-rows'] tr")):
            page = browser.new_page(viewport=vp)
            page.goto(BASE + url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(wait, timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(600)
            result.setdefault("viewports", {})[label + "/" + name] = page.evaluate(
                "() => ({scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth})")
            if name == "config":
                page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-voice-config-1280x900.png"))
            page.close()

    browser.close()

print("##RESULT##" + json.dumps(result, ensure_ascii=False))
`;

function buildPythonScript(staticPort) {
  return PY_SCRIPT.replaceAll('@@STATIC_PORT@@', String(staticPort)).replaceAll(
    '@@FRONTEND_DIR@@',
    FRONTEND_DIR.replace(/\\/g, '/'),
  );
}

async function main() {
  if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
    console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
    process.exit(2);
  }

  console.log('启动静态服务（frontend/dist）+ 模拟后端…');
  const server = await startServer();
  let result;
  try {
    const proc = spawnSync(PYTHON, ['-c', buildPythonScript(server.staticPort)], {
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, PYTHONUTF8: '1', TEMP: TMP_DIR, TMP: TMP_DIR, TMPDIR: TMP_DIR },
    });
    const line = (proc.stdout ?? '').split(/\r?\n/).find((l) => l.startsWith('##RESULT##'));
    if (!line) {
      console.error(proc.stdout);
      console.error(proc.stderr);
      throw new Error(`python exit ${proc.status}`);
    }
    result = JSON.parse(line.slice('##RESULT##'.length));
  } finally {
    server.child.kill();
  }

  console.log(`Chromium: ${result.chromium ?? '(默认)'}`);

  console.log('\n—— ① 界面不得出现开发者文本（D70）——');
  let devTotal = 0;
  for (const [page, info] of Object.entries(result.pages ?? {})) {
    const hits = info.devHits ?? [];
    devTotal += hits.length;
    check(
      `${page}：界面可见文本里没有开发者文本`,
      hits.length === 0,
      hits.length === 0
        ? '0 处'
        : `${hits.length} 处：` + hits.map((h) => `"${h.text}"[${h.found.join('|')}]`).join(' ; '),
    );
  }

  console.log('\n—— ② 灰桌面底只能用于画布（D70）——');
  const SUNKEN_HEX = '#f7f8f9';
  const DESK_HEX = '#8e939a';
  for (const [page, info] of Object.entries(result.pages ?? {})) {
    const bg = info.bg ?? {};
    const rootRgb = parseRgb(bg.root?.bg);
    const bodyRgb = parseRgb(bg.body?.bg);
    if (page === 'paper' || page === 'paper-answer') {
      // 卷页：画布必须是灰桌面底，页面其余部分不是
      const canvasRgb = parseRgb(bg.canvas?.bg);
      check(
        `${page}：画布仍是灰桌面底（--mg-desk-*，C1 质感不回归）`,
        Boolean(bg.canvas?.bgImage && bg.canvas.bgImage !== 'none') || closeTo(canvasRgb, DESK, 6),
        `canvas bgImage=${(bg.canvas?.bgImage ?? '').slice(0, 40)} bg=${bg.canvas?.bg}`,
      );
      check(
        `${page}：页面本身不是灰桌面底`,
        !closeTo(rootRgb, DESK, 6) && !closeTo(bodyRgb, DESK, 6),
        `root=${bg.root?.bg ?? '(无)'} body=${bg.body?.bg}`,
      );
    } else {
      check(
        `${page}：页面底色 = 浅底令牌 ${SUNKEN_HEX}（不是灰桌面 ${DESK_HEX}）`,
        closeTo(rootRgb, SUNKEN) && closeTo(bodyRgb, SUNKEN),
        `root=${bg.root?.bg} body=${bg.body?.bg}（root 选择器 ${bg.rootSelector}）`,
      );
      check(
        `${page}：页面底色不是灰桌面底`,
        !closeTo(rootRgb, DESK, 6) && !closeTo(bodyRgb, DESK, 6),
        `root=${bg.root?.bg} body=${bg.body?.bg}`,
      );
    }
  }

  console.log('\n—— 不回归：1280 视口不横向溢出 ——');
  for (const [label, v] of Object.entries(result.viewports ?? {})) {
    check(`${label}：scrollWidth == innerWidth`, v.scrollW === v.innerW, `scrollW=${v.scrollW} innerW=${v.innerW}`);
  }

  check('无 pageerror', (result.console_errors ?? []).length === 0, (result.console_errors ?? []).join(';').slice(0, 200));

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    for (const f of failed) console.log(`  - ${f.name} (${f.detail ?? ''})`);
    process.exit(1);
  }
  console.log('判据 A10（无开发者文本）与 A11（灰底只用于画布）通过。');
}

await main();
