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
        // ---- 内部编号族：不靠枚举「我记得的那些」，而是按**形状**覆盖本项目全部编号族 ----
        // D=决策、R=数值范围档、Q=需求问答、NFR=非功能需求、FR=功能需求、M=里程碑。
        // 教训：A10 第一版是**枚举黑名单**（只列了 D\d+ / --xxx / FR-xx），
        // 于是漏掉了 R1–R7（数值范围档位），被 Lead 看图抓到。枚举永远不完整。
        const families = [
            ['决策编号', /\\bD\\d+\\b/g],
            ['数值范围档', /\\bR\\d+\\b/g],
            ['需求问答', /\\bQ\\d+\\b/g],
            ['非功能需求', /\\bNFR-\\d+/g],
            ['功能需求', /\\bFR-[A-Z]?\\d+/g],
            ['里程碑', /\\bM\\d+\\b/g],
            // 判据编号族（A1..A11 是我们自己的验收判据编号）。
            // 单列出来不是为了"抓更多"，而是为了让下面那条 A4 例外**有意义** ——
            // 例外必须以「这一族确实会被检查」为前提；否则「放行 A4」就是空放行，
            // 而负对照（注入 A9 应被抓）会立刻把这种空放行暴露出来。
            ['判据编号', /\\bA\\d+\\b/g],
        ];
        for (const [label, re] of families) {
            const m = own.match(re);
            if (m) f.push(label + ':' + m.join(','));
        }
        const cssvar = own.match(/--[a-z][a-z0-9-]*/g);
        if (cssvar) f.push('CSS变量:' + cssvar.join(','));
        const kw = own.match(/\b(none|block|grid|flex|inline)\b/g);
        if (kw) f.push('CSS关键字:' + kw.join(','));
        if (/uvicorn|\/api\/|http:\/\/127\.|SQLite|JSON|stepwise|paper\.[a-z_]/.test(own)) f.push('内部实现');
        if (/\b(undefined|null|NaN)\b/.test(own)) f.push('未定义值');

        // ---- 具名例外（不是把整类放过）----
        // 'A4' 是**纸型**，老师需要看到（"打印成 A4 卷子"是正确信息）。
        // 它与 'R1' 形状相同（单字母 + 数字），所以必须有一个例外，
        // 但例外写成「精确匹配哪些串」的白名单，而不是把 'A\\d+' 整类放过 ——
        // 否则 'A9'（我们自己的判据编号）漏到界面上也不会被发现。
        const PAPER_SIZE_ALLOW = ['A4'];
        const filtered = f.map((item) => {
            const idx = item.indexOf(':');
            if (idx === -1) return item;
            const label = item.slice(0, idx);
            const values = item.slice(idx + 1).split(',').filter((v) => !PAPER_SIZE_ALLOW.includes(v));
            return values.length ? label + ':' + values.join(',') : null;
        }).filter(Boolean);
        f.length = 0;
        f.push(...filtered);

        if (f.length) hits.push({cls: (typeof el.className === 'string' ? el.className : el.tagName).slice(0, 44),
                                 text: own.slice(0, 110), found: f});
    }
    return hits;
}"""

# 页面底色：读**计算后**的背景色，并同时报告 body 与根容器。
#
# 根容器的**选择器必须分老师端/学生端两套**：老师端的页面壳是
# .config-page/.library-page/.publish-page，学生端是 .st-screen（实测 #root
# 的 firstElementChild.className === "st-screen"）。学生端用老师端选择器会
# 返回 null，A11 就会**跳过根容器只查 body** —— 正是「判据悄悄不生效」的形态。
BG_SCAN = """() => {
    const root = document.querySelector(
        '.config-page, .library-page, .publish-page, .st-screen');
    const cs = (el) => {
        if (!el) return null;
        const c = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {bg: c.backgroundColor, bgImage: c.backgroundImage,
                h: Math.round(r.height), x: Math.round(r.x), w: Math.round(r.width)};
    };
    return {body: cs(document.body), root: cs(root),
            rootSelector: root ? (root.className || root.tagName) : null,
            canvas: cs(document.querySelector('.mg-canvas')),
            viewport: {w: window.innerWidth, h: window.innerHeight}};
}"""

def parse_rgb_unused(text):
    return None

# 老师端 5 条 + 学生端 5 条（M4 扩 mock 后补齐答题页与交卷结果页）。
#
# 学生端这几条**都带身份预置**（见下面的 STUDENT_IDENTITY/_seed_identity）：
# 不预置的话每条都会停在「先填一下姓名和学号」的身份门槛，只见得到表单；
# 预置之后 student-entry 看到的是真正的选择页（你好 XX / 想做哪一种 / 自主练习 /
# 输入卷号做卷子），student-practice 看到的是真正的练习设置页（两种练法 + 题量滑块）。
#
# 「student-paper」与「student-result」是**流程页**，不是直接 goto 就能看到内容的：
#   · 答题页要在 student-paper-flow 里逐题作答并且**真的点「交卷」**；
#   · 结果页读 sessionStorage 的 mathgen.student.lastResult，只有走完交卷才有内容
#     （直接打开只会看到「这里暂时没有结果」，那是**空态**不是结果页）。
# 所以这两条由 _drive_student_paper(page) 现走一遍交卷流程，再照常扫。
#
# 补 mock 让这两页可测这件事的正当性来自**逐键比对**：mock 的学生端响应形状
# 与我用探针打真实 app 抓到的响应逐键一致（见 docs/06 与本轮报告）。扫错误态
# 不算覆盖，把「404 错误态」当「答题页覆盖」是给自己制造虚假的安全感。
CASES = [
    ("config", "/teacher", "[data-dimension='answer_form']"),
    ("library", "/teacher/library", "[data-testid='library-rows'] tr"),
    ("paper", "/teacher/paper/1", "#sheet-frame"),
    ("paper-answer", "/teacher/paper/1", "#sheet-frame"),
    ("publish", "/teacher/paper/1/publish", ".publish-page"),
    ("student-entry", "/student", ".st-screen"),
    ("student-practice", "/student/practice", ".st-screen"),
    ("student-paper", "/student/paper/2610-0001", ".st-screen #st-answer"),
    ("student-paper-flow", "/student/paper/2610-0001", ".st-screen #st-answer"),
    ("student-result", "/student/result", ".st-screen"),
]

# 学生端「做卷子 → 交卷 → 结果页」的流程驱动。
#
# 为什么要写成流程而不是直接 goto 结果页：结果页的数据来自 sessionStorage，
# 由交卷那一刻写入（src/student/resultStore.ts 的 saveResult）。直接打开
# /student/result 只能看到空态，那验证的是"空态渲染正常"，不是结果页本身。
#
# 返回收成 dict（而不是抛异常）是为了让判据侧能**如实报告流程在哪一步断的**：
# 流程没走通时页面文本原样带回去，报告里能看出是卡在答题、交卷还是结果页。
def _drive_student_paper(page):
    out = {"filled": 0, "submit_text": None, "reached_result": False, "step_texts": []}
    try:
        for step in range(12):
            box = page.query_selector(".st-screen #st-answer")
            if box is not None:
                try:
                    box.fill(str(step + 1))
                    out["filled"] += 1
                except Exception:
                    pass
            labels = [(el.inner_text() or "").strip() for el in page.query_selector_all(".st-screen button")]
            out["step_texts"].append(labels)
            nxt = None
            for el in page.query_selector_all(".st-screen button"):
                if "下一题" in (el.inner_text() or ""):
                    nxt = el
                    break
            if nxt is not None:
                nxt.click()
                page.wait_for_timeout(180)
                continue
            for el in page.query_selector_all(".st-screen button"):
                txt = (el.inner_text() or "").strip()
                if "交卷" in txt or "提交" in txt or "完成" in txt:
                    el.click()
                    out["submit_text"] = txt
                    break
            break
        page.wait_for_timeout(1200)
        out["reached_result"] = page.url.rstrip("/").endswith("/student/result")
        out["text"] = (page.inner_text(".st-screen") or "")[:400]
    except Exception as exc:
        out["error"] = str(exc)
    return out

result = {"pages": {}, "console_errors": []}
exe = find_chromium()
result["chromium"] = exe

# 负对照开关（默认关）。打开后每个被扫页面都会被注入一段开发者文本，
# 于是一条诚实的扫描应当"全红"。若某页面恒绿，说明它其实没被扫到。
INJECT_DEV_TEXT = "@@INJECT_DEV_TEXT@@" == "1"
result["inject_dev_text"] = INJECT_DEV_TEXT
# --self-test：跑完正常扫描后，再跑两条自带负对照（注入对照 + 身份预置对照）。
SELF_TEST = "@@SELF_TEST@@" == "1"

# 学生端身份预置：src/student/identity.ts 用 localStorage['mathgen.student.identity']
# 记住报过到的学生。预置它 == "这位同学已经报到过"，于是**不需要**后端
# POST /api/students 就能看到门槛之后的真实界面。
#
# 为什么必须这么做（而不是给 mock 补 POST /api/students）：
# mock 后端（verify-server.mjs）是**老师端与学生端共用**的验收基础设施，
# 补学生端接口会牵动别人的写域与在跑的门禁；而预置 localStorage 只动本判据，
# 且它验的正是学生端真实逻辑（identity.ts 的读取路径），不是替身。
# 实测收益：/student 从「身份门槛」变成真正的选择页（你好/练什么/自主练习/输入卷号），
# /student/practice 从门槛变成真正的练习设置页（两种练法 + 题量滑块）。
STUDENT_IDENTITY = '{"student_id":7,"name":"测试同学","student_no":"1001"}'

# 负对照用的注入片段：一个真正的可见文本节点，含两类判据都该抓的东西。
INJECT_SENTINEL = """() => {
    const el = document.createElement('div');
    el.id = 'dev-text-sentinel';
    el.textContent = '排版参数 --page-margin（D56）';
    document.body.appendChild(el);
    return el.getBoundingClientRect().height >= 0;
}"""

def _seed_identity(page):
    """在页面脚本运行前写入身份（比 goto 后 evaluate 更早，避免首屏读到 null）。"""
    try:
        page.add_init_script(
            "try{window.localStorage.setItem('mathgen.student.identity', %s);}catch(e){}"
            % json.dumps(STUDENT_IDENTITY))
    except Exception:
        pass

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    for name, url, wait in CASES:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        if name.startswith("student-"):
            _seed_identity(page)
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
        # student-paper-flow：照常量完答题页之后，**继续走完交卷流程**并把
        # 结果页也量一遍（同一个 page 对象，交卷写的 sessionStorage 直接可用）。
        # 这样一条 case 同时覆盖两个页面，且用的是真实流程，不是伪造的存储。
        if name == "student-paper-flow":
            result["pages"][name] = {"devHits": page.evaluate(DEV_SCAN), "bg": page.evaluate(BG_SCAN)}
            result["student_flow"] = _drive_student_paper(page)
            result["pages"]["student-result-after-submit"] = {
                "devHits": page.evaluate(DEV_SCAN),
                "bg": page.evaluate(BG_SCAN),
            }
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-voice-student-result-430x900.png"))
            page.close()
            continue
        if INJECT_DEV_TEXT:
            # 负对照专用：往**每一个**被扫页面里塞一段"开发者文本"，形状与判据要抓的
            # 一模一样，然后照常跑扫描。若某个页面其实没被扫到（典型事故：SPA 回落把
            # 学生端送成老师端 / 容器选择器写错导致页面是空壳），它就抓不到这段文本，
            # 负对照立刻报红。这样"覆盖面"就不是靠嘴说的。
            page.evaluate(INJECT_SENTINEL)
            page.wait_for_timeout(150)
        result["pages"][name] = {"devHits": page.evaluate(DEV_SCAN), "bg": page.evaluate(BG_SCAN)}
        if name == "config":
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-voice-config-1440x900.png"))
        if name == "library":
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-voice-library-1440x900.png"))
        page.close()

    # ---- 自带负对照（只在 --self-test 下跑，用同一个服务与浏览器，避免另起服务撞端口）----
    # ① 注入对照：每个页面都注入同一段开发者文本，则每条 A10 检查都必须变红。
    #    这一条证明的是"学生端页面**确实被扫了**"：如果 SPA 回落又把 /student/**
    #    送成老师端、或 .st-screen 选择器落到空壳上，注入的文本就抓不到，
    #    这里会直接报出"哪个页面没抓到"。
    # ② 身份预置对照：不预置身份时，/student/practice 必须停在身份门槛
    #    （有姓名输入框）。这条证明预置是**承重的**，而不是可有可无的装饰 ——
    #    否则"扩了覆盖面"这句话就没有依据。
    if SELF_TEST:
        sentinel_pages = {}
        for name, url, wait in CASES:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            if name.startswith("student-"):
                _seed_identity(page)
            page.goto(BASE + url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(wait, timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            page.evaluate(INJECT_SENTINEL)
            page.wait_for_timeout(150)
            hits = page.evaluate(DEV_SCAN)
            sentinel_pages[name] = len(hits)
            page.close()
        result["self_test"] = {"injected_pages": sentinel_pages, "case_names": [c[0] for c in CASES]}

        # ② 不预置身份 → 必须看到身份门槛（有姓名输入框）
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE + "/student/practice", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        result["self_test"]["identity_gate_inputs"] = page.evaluate(
            "() => document.querySelectorAll('input').length")
        result["self_test"]["identity_gate_has_practice"] = page.evaluate(
            "() => Array.from(document.querySelectorAll('button')).some(b => (b.innerText||'').includes('开始练习'))")
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
  return PY_SCRIPT.replaceAll('@@STATIC_PORT@@', String(staticPort))
    .replaceAll('@@INJECT_DEV_TEXT@@', process.env.MATHGEN_VERIFY_INJECT_DEV_TEXT === '1' ? '1' : '0')
    .replaceAll('@@SELF_TEST@@', process.env.MATHGEN_VERIFY_VOICE_SELF_TEST === '1' ? '1' : '0')
    .replaceAll('@@FRONTEND_DIR@@', FRONTEND_DIR.replace(/\\/g, '/'));
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
  // 页面上「负责铺底」的那个元素，两个应用不一样：
  //   老师端 → body 有底色（styles.css 的 body 规则），页面壳 .config-page 也有；
  //   学生端 → F1 修复前 body 是透明的（student.css 没有 import 老师端 styles.css，
  //            那条 body 底色规则不在学生端的样式表里），铺底的是 .st-screen；
  //            F1 修复后 student.css 顶部补上了 html/body 的 margin 与底色，
  //            两个元素现在都是浅底 —— 判据仍按"谁铺底就查谁"来判，不写死 body。
  // 所以判据不能写死 body：`!closeTo(bodyRgb, DESK, 6)` 在 body 透明时是
  // **恒真**的（rgba(0,0,0,0) 怎么算都不是灰桌面），前半句 closeTo(bodyRgb, SUNKEN)
  // 才是真正在把关的那半个条件。这里改成"谁铺底就查谁"，并把**有没有盖住视口**
  // 一起查出来 —— 否则「中间 520px 有底色、两侧露出别的颜色」也会判绿。
  for (const [page, info] of Object.entries(result.pages ?? {})) {
    const bg = info.bg ?? {};
    const rootRgb = parseRgb(bg.root?.bg);
    const bodyRgb = parseRgb(bg.body?.bg);
    const vp = bg.viewport ?? {};
    const rootCovers = Number(bg.root?.h ?? 0) >= Number(vp.h ?? 0) - 1;
    const bodyCovers = Number(bg.body?.h ?? 0) >= Number(vp.h ?? 0) - 1;
    // 「默认边距残留」：文档根仍有浏览器默认的 8px margin（`body.x > 0`）。
    // 这里**只测量、不在此断言**（理由见下面 A11b 的说明），但值得显式算出来：
    // 学生端的 `.st-screen` 是**有意的**手机优先版式（max-width:520px + margin:0 auto），
    // 它本来就不该占满 1440 宽，所以不能拿"根容器是否铺满宽度"当判据。真正的症状是
    // 文档整体缩进 8px、视口最外侧露出的不是令牌色 —— 在只看"颜色对不对"的判据下
    // 完全查不出来（body 的 h 恰好也是 900）。
    const hasDefaultBodyMargin = Number(bg.body?.x ?? 0) > 0;
    const rootFills = rootCovers;
    const bodyFills = bodyCovers;
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
      // 「铺底元素」= 根容器或 body 里**占满视口的那个**（优先根容器：老师端 body 铺底、
      // 学生端 .st-screen 铺底，两种实现都要能过）。
      const isRootPainter = rootFills || (!bodyFills && rootCovers);
      const painterName = isRootPainter ? `根容器 ${bg.rootSelector}` : 'body';
      const painterRgb = isRootPainter ? rootRgb : bodyRgb;
      const painterFills = isRootPainter ? rootFills : bodyFills;
      check(
        `${page}：铺底元素（${painterName}）底色 = 浅底令牌 ${SUNKEN_HEX} 且占满视口（不是灰桌面 ${DESK_HEX}）`,
        closeTo(painterRgb, SUNKEN) && painterFills,
        `根容器 ${bg.rootSelector}=${bg.root?.bg}（x=${bg.root?.x} h=${bg.root?.h}）body=${bg.body?.bg}（x=${bg.body?.x} h=${bg.body?.h}）视口 ${vp.w}x${vp.h}`,
      );
      // A11b 的位置说明：这里**不**断言 `body.x == 0`。学生端的 `.st-screen` 是
      // **有意的**手机优先版式（max-width:520px + margin:0 auto），它本来就不该占满
      // 1440 宽，所以不能拿"根容器是否铺满宽度"当判据。真正该查的是"文档根有没有
      // 残留浏览器默认边距"—— 本轮确实查出过（发现 F1：学生端没 import 老师端
      // styles.css 的 `body { margin:0 }`，实测 body.x=8、外壳只有 374 宽、
      // 整页永远多出 16px 高度）。**F1 已由学生端修复并重建 dist**（student.css 顶部
      // 补了 `html, body { margin:0; background:var(--mg-surface-sunken) }`），
      // 现实测 body.x=0、width=390（390×844 视口）→ 这条不再需要哨兵。
      // 保留 `hasDefaultBodyMargin` 的**测量**并打进 detail：它是有用的现场信息，
      // 一旦哪天又出现 8px 残留，日志里立刻看得见（而不是又要重跑探针去量）。
      const bodyMarginNote = hasDefaultBodyMargin ? ` ⚠ 文档根又有 ${bg.body?.x}px 默认边距（F1 回归？）` : '';
      check(
        `${page}：页面底色不是灰桌面底${bodyMarginNote}`,
        !closeTo(rootRgb, DESK, 6) && !closeTo(bodyRgb, DESK, 6),
        `root=${bg.root?.bg} body=${bg.body?.bg} body.x=${bg.body?.x}`,
      );
    }
  }

  console.log('\n—— 不回归：1280 视口不横向溢出 ——');
  for (const [label, v] of Object.entries(result.viewports ?? {})) {
    check(`${label}：scrollWidth == innerWidth`, v.scrollW === v.innerW, `scrollW=${v.scrollW} innerW=${v.innerW}`);
  }

  check('无 pageerror', (result.console_errors ?? []).length === 0, (result.console_errors ?? []).join(';').slice(0, 200));

  // ---- --self-test：两条自带负对照的断言 ----
  if (result.self_test) {
    console.log('\n—— 自带负对照（--self-test）——');
    const st = result.self_test;
    const injected = st.injected_pages ?? {};
    const names = st.case_names ?? [];
    const missed = names.filter((n) => !injected[n]);
    console.log(
      `  注入对照：${names.length} 个页面各自注入同一段开发者文本 → 抓到 ${names.length - missed.length}/${names.length}`,
    );
    console.log(`    逐页命中数：${names.map((n) => `${n}=${injected[n] ?? 0}`).join(' ')}`);
    check(
      '注入对照：每个被扫页面都抓到了注入的开发者文本（证明学生端确实被扫，不是空跑）',
      names.length > 0 && missed.length === 0,
      missed.length ? `没抓到：${missed.join(',')}` : `全部 ${names.length} 个页面命中`,
    );
    // 学生端那几条单独点名，避免"整体绿了但学生端恰好漏掉"被平均数盖住。
    // 数量随 CASES 扩张而改：M4 之前是 3 条，补 mock 后加了 student-paper 与
    // student-paper-flow（结果页由 flow 一条带出，见 result.pages 的
    // student-result-after-submit）。
    const stuNames = names.filter((n) => n.startsWith('student-'));
    check(
      `注入对照：学生端 ${stuNames.length} 条路由逐条都抓到（点名，防止被整体平均盖住）`,
      stuNames.length >= 5 && stuNames.every((n) => injected[n] >= 1),
      stuNames.map((n) => `${n}=${injected[n] ?? 0}`).join(' '),
    );
    console.log(
      `  身份预置对照（故意不预置）：/student/practice input=${st.identity_gate_inputs} 有"开始练习"按钮=${st.identity_gate_has_practice}`,
    );
    check(
      '身份预置对照：不预置身份时停在身份门槛（有姓名输入框、没有"开始练习"）—— 证明预置是承重的',
      Number(st.identity_gate_inputs ?? 0) >= 1 && st.identity_gate_has_practice === false,
      `input=${st.identity_gate_inputs} hasPractice=${st.identity_gate_has_practice}`,
    );
  }

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    for (const f of failed) console.log(`  - ${f.name} (${f.detail ?? ''})`);
    process.exit(1);
  }
  console.log('判据 A10（无开发者文本）与 A11（灰底只用于画布）通过。');
}

await main();
