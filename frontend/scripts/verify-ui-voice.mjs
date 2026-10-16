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

# 卷库收纳态（D78）：**结构**证据，不是文本证据。
#
# 为什么单靠「扫可见文本」不够：A10 能证明归档页没有开发者术语，但证明不了
# **这一条 case 真的换了一档**。若切档的 click 静默失败，library-archived 扫到的
# 仍是在用列表，而它同样干净 —— 于是"多覆盖了一条"变成重复计数，判据恒真（V11）。
# 所以这里把两档的**互斥特征**抓出来：分段控件的 data-state、行标题集合、
# 归档/恢复按钮各出现几个、以及删除的禁用态与理由文本的**数量对比**。
LIB_STATE_SCAN = """() => {
    const q = (sel) => document.querySelector(sel);
    const all = (sel) => Array.from(document.querySelectorAll(sel));
    const del = all("[data-testid='library-delete']");
    return {
        filterState: (q("[data-testid='library-archive-filter']") || {}).getAttribute
            ? q("[data-testid='library-archive-filter']").getAttribute('data-state') : null,
        rows: all("[data-testid='library-rows'] tr").length,
        start: (q("[data-testid='library-range-start']") || {}).innerText ?? null,
        rowTitles: all("[data-testid='library-rows'] tr")
            .map((tr) => tr.getAttribute('data-paper-title')).slice(0, 3),
        guards: all("[data-delete-guard]").map((g) => g.getAttribute('data-delete-guard')),
        blockedReasons: all("[data-testid='library-delete-blocked-reason']").length,
        deleteButtons: del.length,
        deleteDisabled: del.filter((b) => b.disabled).length,
        archiveButtons: all("[data-testid='library-archive']").length,
        unarchiveButtons: all("[data-testid='library-unarchive']").length,
    };
}"""

def parse_rgb_unused(text):
    return None

# M5 成绩页的**结构**证据（D80）。
#
# 为什么不能只靠 A10：A10 能证明成绩页没有开发者术语，但证明不了「显示的分数来自后端」。
# 界面若自己用 Math.round(correct / total * 100) 算一遍，A10 一样干净 —— 而两个口径
# 在 (40, 23) 上分别是 57 / 58（23/40*100 在 IEEE754 里是 57.49999999999999）。
# 所以这里把**渲染出来的分数文本**与**同一行上的 data-score 属性（后端原值）**一起抓回来，
# 在 JS 侧比对：两者必须相等，且必须等于契约值 58。
ATT_STATE_SCAN = """() => {
    const all = (sel) => Array.from(document.querySelectorAll(sel));
    const rows = all("[data-testid='attempts-rows'] tr");
    return {
        rows: rows.length,
        // 逐行带走 id / 渲染文本 / data-score：**不能只看第一行** ——
        // student-paper-flow 用例会真的交一次卷，那条新成绩的 submitted_at 最新，
        // 于是排在第一行；只看首行就会把"刚交的那份"当成夹具去断言（本判据第一版
        // 就是这么红的：文本="0"）。
        cells: rows.map((tr) => ({
            id: tr.getAttribute('data-attempt-id'),
            attr: tr.getAttribute('data-score'),
            text: (tr.querySelector("[data-testid='attempts-score']") || {}).innerText ?? null,
            paperCode: tr.getAttribute('data-paper-code'),
            practice: Boolean(tr.querySelector("[data-testid='attempts-practice-label']")),
            hasNullText: /(^|\\s)(undefined|null|NaN)(\\s|$)/.test(tr.innerText),
        })),
        practiceLabels: all("[data-testid='attempts-practice-label']").length,
        nullTexts: rows.filter((tr) =>
            /(^|\\s)(undefined|null|NaN)(\\s|$)/.test(tr.innerText)).length,
        exportButtons: all("[data-testid='attempts-export-csv']").length
            + all("[data-testid='attempts-export-xlsx']").length,
        // 空态：卡片要求成绩页「含空态」。空态**不能靠没有人报错**来证明 ——
        // 一个白屏页面同样「没有报错」。所以把空态标题抓回来，由 JS 侧断言它非空、
        // 且说的是人话（还有一个世界是「有数据」，用来证明这条判据不是恒真）。
        emptyTitle: (document.querySelector("[data-testid='attempts-empty']") || {}).innerText
            ?.split('\\n')[0] ?? null,
    };
}"""

# 逐题明细页：行数必须等于 summary 里的「总题数」，且每行都带对错标记与两个答案。
ATT_DETAIL_SCAN = """() => {
    const all = (sel) => Array.from(document.querySelectorAll(sel));
    const rows = all("[data-testid='attempt-detail-rows'] tr");
    return {
        rows: rows.length,
        scoreText: (document.querySelector("[data-testid='attempt-detail-score']") || {}).innerText
            ?? null,
        correct: Array.from(rows).filter((tr) => tr.getAttribute('data-correct') === 'true').length,
        studentAnswers: all("[data-testid='attempt-detail-row-student-answer']").length,
        rightAnswers: all("[data-testid='attempt-detail-row-answer']").length,
        verdicts: all("[data-testid='attempt-detail-row-verdict']").length,
        seqs: rows.map((tr) => tr.getAttribute('data-seq')).slice(0, 3),
    };
}"""

# M6：出题页必须在**生成之前**就说清「生成即入库、卷在卷库」（真实用户提问催生的）。
# 这一段只抓**结构**证据（说明在不在、在不在生成按钮之前），文案有没有说到四个要点
# 交给 Node 侧的 saveNoteClaims() 判 —— 抓与判分开，负对照才能复用同一个谓词。
SAVE_NOTE_SCAN = """() => {
    const note = document.querySelector("[data-testid='config-save-note']");
    const btn = Array.from(document.querySelectorAll('button'))
        .find((b) => (b.innerText || '').includes('生成'));
    let beforeSubmit = null;
    if (note && btn) {
        beforeSubmit = Boolean(note.compareDocumentPosition(btn) & Node.DOCUMENT_POSITION_FOLLOWING);
    }
    let display = null;
    let visibility = null;
    if (note) {
        const cs = getComputedStyle(note);
        display = cs.display;
        visibility = cs.visibility;
    }
    return {
        found: Boolean(note),
        text: note ? note.innerText : null,
        beforeSubmit: beforeSubmit,
        submitText: btn ? btn.innerText : null,
        display: display,
        visibility: visibility,
        visible: Boolean(note) && display !== 'none' && visibility !== 'hidden',
    };
}"""

# 老师端 9 条（配置 / 卷库在用 / 卷库已归档 / 卷预览 / 卷预览-答案页 / 发布 / M5 成绩 /
# M5 逐题明细 / M5 空态）+ 学生端 5 条（M4 扩 mock 后补齐答题页与交卷结果页）。
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
    # 卷库收纳（D78）：同一条路由的**另一档**。分档是组件 state，不在 URL 里，
    # 所以由下面的 click 钩子把分段控件切到「已归档」再扫（见 LIB_STATE_SCAN）。
    ("library-archived", "/teacher/library", "[data-testid='library-rows'] tr"),
    ("paper", "/teacher/paper/1", "#sheet-frame"),
    ("paper-answer", "/teacher/paper/1", "#sheet-frame"),
    ("publish", "/teacher/paper/1/publish", ".publish-page"),
    ("student-entry", "/student", ".st-screen"),
    ("student-practice", "/student/practice", ".st-screen"),
    ("student-paper", "/student/paper/2610-0001", ".st-screen #st-answer"),
    ("student-paper-flow", "/student/paper/2610-0001", ".st-screen #st-answer"),
    ("student-result", "/student/result", ".st-screen"),
    # M5 成绩（FR-T11 列表 / FR-T12 逐题明细）。明细用夹具 id 500：它是刻意挑的
    # (total, correct) = (40, 23) 分叉点（契约 58 / 旧浮点口径 57），所以这条用例
    # 顺手把「页面渲染的是后端 score 还是自己算的」也钉住了（见 ATT_STATE_SCAN）。
    ("attempts", "/teacher/attempts", "[data-testid='attempts-rows'] tr"),
    ("attempt-detail", "/teacher/attempts/500", "[data-testid='attempt-detail-rows'] tr"),
    # 空态：卷 id 999999 在库里不存在 —— 不是「卷存在但没人做」，是「筛选条件一条都不命中」。
    # 空态必须说人话、且**不能是白屏**（白屏同样没有 error、也没有开发者文本）。
    ("attempts-empty", "/teacher/paper/999999/attempts", "[data-testid='attempts-empty']"),
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

archived_tab_switch = None  # 见下面 library-archived 钩子：先记、后并入 result

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
        # library-archived：把「在用 / 已归档」切到「已归档」再扫。
        #
        # 点的是**可见的分段（label）**，不是里面那个 radio：CSS 里 .mg-segment input 是
        # 1px + clip-path: inset(50%) 的视觉隐藏控件（无障碍语义需要它，但用户点不到），
        # 直接点 input 会因为命中点被别的元素盖住而失败 —— 这正是本条判据第一次跑时
        # 抓到的红（filterState 停在 exclude，两档扫的是同一页）。
        # 钩子失败不在这里抛（避免整条 case 崩掉让报告只剩异常），而是让
        # LIB_STATE_SCAN 抓到的 filterState 停在 exclude —— 判据侧会因为
        # 「两档 state 相同」而报红，红的地方在判据里，不在异常栈里。
        if name == "library-archived":
            # 先把分页推到第 2 页，**再**切档。任务卡第 2 条：切档必须把分页重置到第一页，
            # 否则会停在一个空页上 —— 那是「静默的错」。不先离开第 1 页就区分不出
            # "实现了重置" 与 "压根没有重置"（两者在第 1 页上看起来一模一样）。
            try:
                page.click("[data-testid='library-next']", timeout=5000)
                page.wait_for_timeout(900)
            except Exception:
                pass
            paged_start = page.evaluate(
                "() => { const el = document.querySelector(\"[data-testid='library-range-start']\");"
                " return el ? el.innerText : null; }")
            try:
                page.click(
                    "[data-testid='library-archive-filter'] label:has-text('已归档')",
                    timeout=5000)
            except Exception:
                pass
            # 存进局部变量，等下面 result["pages"][name] 建好之后再合并 ——
            # 这一段的执行顺序在 result["pages"][name] 赋值**之前**（见下面的循环体）。
            archived_tab_switch = {"before_start": paged_start}
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
        if name.startswith("library"):
            result["pages"][name]["lib"] = page.evaluate(LIB_STATE_SCAN)
        # M5 成绩页的**结构**证据（D80）：分数文本 / data-score / 自主练习文案 / 逐题行。
        # 这一条与 A10 是两件事：A10 判「没有开发者术语」，这里判「显示的分数就是后端
        # 给的那个数」——界面若自己算一遍，A10 一样干净（见 ATT_STATE_SCAN 的注释）。
        if name == "attempts":
            result["pages"][name]["att"] = page.evaluate(ATT_STATE_SCAN)
        # 空态世界与有数据世界用**同一个**扫描函数 —— 两个世界要能给出不同结论。
        if name == "attempts-empty":
            result["pages"][name]["att"] = page.evaluate(ATT_STATE_SCAN)
        if name == "attempt-detail":
            result["pages"][name]["attDetail"] = page.evaluate(ATT_DETAIL_SCAN)
        # M6：出题页的入库说明（存在性 + 是否排在「生成」按钮之前）。
        if name == "config":
            result["pages"][name]["save_note"] = page.evaluate(SAVE_NOTE_SCAN)
        if name == "library-archived":
            result["pages"][name]["tab_switch"] = archived_tab_switch
        # 卷库归档的**行为**证据（D78）：点第一行的「归档」，看三件事同时发生 ——
        #   · 该行立刻移出「在用」视图（失效重取，archived=exclude）
        #   · 出现 Toast，且带「撤销」（归档可逆，反馈必须可撤销）
        #   · Toast 自己的文案也要过 A10 —— 新出现的可见文本不能逃过扫描
        # 只断言"发出了请求"是不够的：请求成功但列表没刷新，用户看到的是"点了没反应"，
        # 那正是任务卡第 5 条要防的东西。
        if name == "library":
            first_title = page.evaluate(
                "() => { const tr = document.querySelector(\"[data-testid='library-rows'] tr\");"
                " return tr ? tr.getAttribute('data-paper-title') : null; }")
            try:
                page.click(
                    "[data-testid='library-rows'] tr:first-child [data-testid='library-archive']",
                    timeout=5000)
                page.wait_for_timeout(1500)
            except Exception:
                pass
            result["pages"][name]["archive_flow"] = {
                "first_title": first_title,
                "toast": page.evaluate(
                    "() => { const t = document.querySelector(\"[data-testid='library-toast']\");"
                    " return t ? t.innerText : null; }"),
                "has_undo": page.evaluate(
                    "() => Boolean(document.querySelector(\"[data-testid='library-toast-undo']\"))"),
                "still_present": page.evaluate(
                    "(title) => Array.from(document.querySelectorAll(\"[data-testid='library-rows'] tr\"))"
                    ".some((tr) => tr.getAttribute('data-paper-title') === title)", first_title),
                "devHits": page.evaluate(DEV_SCAN),
            }
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

        # ③ M6 出题页入库说明的负对照：**先把说明从 DOM 里删掉**，再用同一个谓词扫一遍。
        #    两条一起才成立 —— 只报"删掉后为假"是空话（元素本来就不存在时，任何谓词都为假）；
        #    所以先把"删除前它确实在"这件事测下来（V9.2：负对照本身要自证制造了失败条件）。
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE + "/teacher", wait_until="domcontentloaded")
        try:
            page.wait_for_selector("[data-dimension='answer_form']", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(500)
        existed_before = page.evaluate(
            "() => Boolean(document.querySelector(\"[data-testid='config-save-note']\"))")
        # 先加 display:none 再扫：这一态专门证明「只查文本」是不够的 ——
        # innerText 在 display:none 下**仍返回文本**（Chromium 实测），
        # 所以四要点仍会全真，只有计算样式断言能把它判红。
        page.evaluate(
            "() => { const n = document.querySelector(\"[data-testid='config-save-note']\");"
            " if (n) n.style.display = 'none'; }")
        page.wait_for_timeout(100)
        hidden_evidence = page.evaluate(SAVE_NOTE_SCAN)
        page.evaluate(
            "() => { const n = document.querySelector(\"[data-testid='config-save-note']\");"
            " if (n) n.remove(); }")
        page.wait_for_timeout(100)
        result["self_test"]["save_note_control"] = {
            "existed_before": existed_before,
            "hidden_evidence": hidden_evidence,
            "evidence": page.evaluate(SAVE_NOTE_SCAN),
        }
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

  // ---- 卷库收纳态（D78）：归档 / 恢复 / 删除 ----
  //
  // 这一节查的是**结构**，不是文本。理由：文本判据（A10）对「这一条 case 到底看的哪一档」
  // 完全不敏感 —— 切档 click 静默失败时，library-archived 扫到的仍是在用列表，而它同样干净。
  // 所以这里先证明**两档真的不同**，再证明每档里该出现的东西确实出现、且**对立面也在场**
  // （只有 blocked 没有 allowed 的话，「事先禁用」也可能是"把所有删除都禁掉"这种错误实现）。
  {
    console.log('\n—— 卷库收纳态（D78）：分档互斥 + 删除的事先禁用 ——');
    const inUse = result.pages?.library?.lib ?? {};
    const arch = result.pages?.['library-archived']?.lib ?? {};
    console.log(`  在用档：${JSON.stringify(inUse)}`);
    console.log(`  已归档档：${JSON.stringify(arch)}`);

    check(
      '卷库两档的分段控件状态互斥（在用=exclude / 已归档=only）—— 证明切档真的生效，不是重复扫同一页',
      inUse.filterState === 'exclude' && arch.filterState === 'only',
      `在用=${inUse.filterState} 已归档=${arch.filterState}`,
    );
    check(
      '卷库两档拿到的是**不同**的行集合（否则「已归档」是假分档）',
      (arch.rows ?? 0) >= 1 &&
        JSON.stringify(inUse.rowTitles) !== JSON.stringify(arch.rowTitles),
      `在用行数=${inUse.rows} 首行=${JSON.stringify(inUse.rowTitles?.[0])} / 已归档行数=${arch.rows} 首行=${JSON.stringify(arch.rowTitles?.[0])}`,
    );
    check(
      '在用档里**同时**存在「可删除」与「已有成绩 → 事先禁用」两种行（禁用是有选择的，不是一律禁掉）',
      Array.isArray(inUse.guards) && inUse.guards.includes('blocked') && inUse.guards.includes('allowed'),
      `guards=${JSON.stringify(inUse.guards)}`,
    );
    check(
      '有成绩的行：删除按钮**真的 disabled**，且旁边写着人话理由（不是点下去才吃 409）',
      (inUse.deleteDisabled ?? 0) >= 1 &&
        (inUse.blockedReasons ?? 0) >= 1 &&
        (inUse.deleteDisabled ?? 0) < (inUse.deleteButtons ?? 0),
      `禁用=${inUse.deleteDisabled}/${inUse.deleteButtons} 理由文本=${inUse.blockedReasons}`,
    );
    check(
      '已归档档：每行给的是「恢复」而不是「归档」（按钮方向随归档态翻转）',
      (arch.unarchiveButtons ?? 0) >= 1 && (arch.archiveButtons ?? 0) === 0,
      `归档按钮=${arch.archiveButtons} 恢复按钮=${arch.unarchiveButtons}`,
    );

    // 切档必须把分页重置回第一页（任务卡第 2 条：否则会停在空页上，那是静默的错）。
    // 判据要**先证明自己离开了第 1 页**，否则「回到第 1 页」与「从来没离开」不可区分 ——
    // 那就是一条恒真断言（V9）。
    const tabSwitch = result.pages?.['library-archived']?.tab_switch ?? {};
    console.log(`  切档分页：${JSON.stringify(tabSwitch)} 切档后起始行=${arch.start}`);
    check(
      '切档前确实翻到了第 2 页（先证明判据的前提成立，否则下面那条是恒真的）',
      tabSwitch.before_start != null && tabSwitch.before_start !== '1',
      `翻页后起始行=${JSON.stringify(tabSwitch.before_start)}`,
    );
    check(
      '切到「已归档」后分页重置回第一页（不是停在空页上）',
      arch.start === '1' && (arch.rows ?? 0) >= 1,
      `切档后起始行=${JSON.stringify(arch.start)} 行数=${arch.rows}`,
    );

    // 行为面：真的点一次「归档」。上面那几条查的是"列表长什么样"，
    // 这一条查的是"按下去之后发生了什么"—— 两者都可能单独坏掉。
    const flow = result.pages?.library?.archive_flow ?? {};
    console.log(`  归档流程：${JSON.stringify(flow)}`);
    check(
      '点了「归档」之后该行**立刻**移出「在用」列表（不是留在原地让人以为没生效）',
      Boolean(flow.first_title) && flow.still_present === false,
      `归档的行=${JSON.stringify(flow.first_title)} 仍在列表里=${flow.still_present}`,
    );
    check(
      '归档成功给出**可撤销**的反馈（Toast + 撤销按钮）—— 归档是可逆操作，反馈也必须可逆',
      typeof flow.toast === 'string' && flow.toast.length > 0 && flow.has_undo === true,
      `toast=${JSON.stringify(flow.toast)} 有撤销=${flow.has_undo}`,
    );
    check(
      '归档后的 Toast 文案本身也过 A10（新出现的可见文本不能逃过扫描）',
      Array.isArray(flow.devHits) && flow.devHits.length === 0,
      (flow.devHits ?? []).map((h) => `"${h.text}"`).join(' ; ') || '0 处',
    );
  }

  // ---- M5 成绩：得分只有一个来源（D80）------------------------------------
  //
  // 判据的形状：`渲染出来的分数文本` 必须等于 `接口给的 score`。单说这一句还不够 ——
  // 若夹具的 (total, correct) 恰好让两种口径算出同一个数，这条判据对"界面自己算分"
  // 完全不敏感（恒真）。所以夹具刻意选 (40, 23)：契约口径 58、旧浮点口径 57。
  const att = result.pages?.attempts?.att ?? {};
  const attDetail = result.pages?.['attempt-detail']?.attDetail ?? {};
  const CONTRACT_SCORE = 58;
  const NAIVE_SCORE = 57;
  console.log('\n—— 成绩页与逐题明细（M5）：得分只有一个来源（D80）——');
  console.log(`  成绩列表：${JSON.stringify(att)}`);
  const fixture = (att.cells ?? []).find((row) => row.id === '500') ?? null;
  const fresh = (att.cells ?? []).find((row) => row.id !== '500') ?? null;
  check('成绩列表渲染出夹具行（>= 3 行）', (att.rows ?? 0) >= 3, `rows=${att.rows}`);
  check(
    '夹具行（id=500，total=40 / correct=23）渲染的分数 == 接口给的 score（58，契约口径）',
    fixture !== null && fixture.text === String(CONTRACT_SCORE) && fixture.attr === String(CONTRACT_SCORE),
    `文本=${JSON.stringify(fixture?.text)} data-score=${JSON.stringify(fixture?.attr)}`,
  );
  check(
    '该夹具确实是两个口径的分叉点（旧浮点口径得到 57）—— 否则上面那条是恒真的',
    Math.round((23 / 40) * 100) === NAIVE_SCORE && NAIVE_SCORE !== CONTRACT_SCORE,
    `本地口径=${Math.round((23 / 40) * 100)} 契约=${CONTRACT_SCORE}`,
  );
  check(
    '（合成负对照）把「界面自己算分」的世界喂进同一个判定必须变红 —— 证明上面那条判据在承重',
    (fixture?.text === String(NAIVE_SCORE)) === false,
    `若页面显示 ${NAIVE_SCORE} 而接口给 ${CONTRACT_SCORE}，判据会红`,
  );
  check(
    '成绩列表确实排在夹具行之前（证明上面找的是夹具、不是碰巧的第一行）',
    fresh === null || (att.cells ?? []).indexOf(fresh) < (att.cells ?? []).indexOf(fixture),
    `最新一条 id=${JSON.stringify(fresh?.id)} 分数=${JSON.stringify(fresh?.text)}；夹具 id=500 在其后`,
  );
  check(
    '自主练习行显示界面补的文案「自主练习」，不是空卷名',
    (att.practiceLabels ?? 0) >= 1,
    `标签数=${att.practiceLabels}`,
  );
  check(
    '成绩行里没有 undefined/null/NaN 这类开发者文本（A10 明令）',
    (att.nullTexts ?? 0) === 0,
    `命中 ${att.nullTexts} 行`,
  );
  check('成绩页给出导出入口（CSV + xlsx）', (att.exportButtons ?? 0) >= 2, `按钮数=${att.exportButtons}`);
  console.log(`  逐题明细：${JSON.stringify(attDetail)}`);
  check('明细页逐题行数 == 总题数（夹具 40 题）', attDetail.rows === 40, `rows=${attDetail.rows}`);
  check(
    '明细页答对行数 == 接口的 correct（23）—— 逐题数据与汇总自洽',
    attDetail.correct === 23,
    `correct=${attDetail.correct}`,
  );
  check(
    '每一行都同时给出「学生答案」与「正确答案」（复用学生端结果页的作答行结构）',
    attDetail.studentAnswers === attDetail.rows && attDetail.rightAnswers === attDetail.rows,
    `学生答案=${attDetail.studentAnswers} 正确答案=${attDetail.rightAnswers} 行=${attDetail.rows}`,
  );
  check(
    '明细页的分数文本同样来自后端 score',
    attDetail.scoreText === String(CONTRACT_SCORE),
    JSON.stringify(attDetail.scoreText),
  );

  // ---- M5 空态（卡片明写「含空态」）-------------------------------------
  //
  // 空态**不能靠「没有报错」证明**：白屏同样不报错、同样没有开发者文本。
  // 所以判据是「0 行 **且** 给出了一句人话标题」，并且用**有数据的那个世界**
  // 再喂一次同一个谓词 —— 两态必须给出相反结论，否则这条是恒真的。
  const attEmpty = result.pages?.['attempts-empty']?.att ?? {};
  const emptyWorldLooksRight = (scan) =>
    scan?.rows === 0 && typeof scan?.emptyTitle === 'string' && scan.emptyTitle.trim() !== '';
  console.log(`  成绩页空态：${JSON.stringify(attEmpty)}`);
  check(
    '空态世界（卷 id 999999 无成绩）：0 行 + 一句人话标题（不是白屏）',
    emptyWorldLooksRight(attEmpty),
    `rows=${attEmpty.rows} title=${JSON.stringify(attEmpty.emptyTitle)}`,
  );
  check(
    'V11 判别力：同一个谓词喂「有数据」的世界必须为假 —— 两态结论相反，判据不是恒真',
    emptyWorldLooksRight(att) === false && att.rows > 0,
    `有数据世界 rows=${att.rows} 空态谓词=${emptyWorldLooksRight(att)}`,
  );

  // ---- M6：界面说清「生成即入库、卷在卷库」（真实用户提问）-----------------
  //
  // 来源：用户自己走完教师端后问「编辑好一个题单之后，怎么保存到题库中」。
  // 产品**没有**保存这一步（点「生成」时就 createPaper 落库了）—— 功能是对的，
  // 是界面没说出来。所以这条判据查的不是行为，是**可见文本有没有说清**。
  //
  // 判据分两层，并且**同一个谓词**要能被负对照复用（见 --self-test）：
  //   · 结构层：说明存在，且排在「生成」按钮**之前**（卡片原话是"生成之前的可见文本"）；
  //   · 语义层：四个要点都在 —— 没有保存步骤 / 点生成就进卷库 / 之后改动立刻生效 /
  //     能按卷号找回。判的是**要点**，不是逐字文案：逐字文案的判据会在改一个标点时
  //     变红，那种红是噪声，会把人训练成忽略它。
  const saveNote = result.pages?.config?.save_note ?? {};
  const saveNoteClaims = (evidence) => {
    const text = typeof evidence?.text === 'string' ? evidence.text : '';
    return {
      存到卷库: /卷库/.test(text),
      生成时就存: /生成/.test(text),
      没有单独的保存步骤: /(没有|不需要|无需|不用)[^。；]{0,10}保存/.test(text),
      之后改动立刻生效: /(立刻|立即|马上)[^。；]{0,6}(生效|保存|落库)/.test(text),
      能按卷号找回: /卷号/.test(text),
    };
  };
  const saveNoteEvidence = saveNoteClaims(saveNote);
  console.log('\n—— 出题页说清「生成即入库」（M6，真实用户提问）——');
  console.log(`  说明文本：${JSON.stringify(saveNote.text)}`);
  console.log(`  四个要点：${JSON.stringify(saveNoteEvidence)}`);
  check(
    '出题页在「生成」按钮**之前**有可见的入库说明（不是 tooltip / placeholder）',
    saveNote.found === true && saveNote.beforeSubmit === true,
    `found=${saveNote.found} 排在生成按钮之前=${saveNote.beforeSubmit} 按钮文本=${JSON.stringify(saveNote.submitText)}`,
  );
  check(
    '这段说明把四件事都说清了：没有保存步骤 / 点生成就进卷库 / 之后改动立刻生效 / 能按卷号找回',
    Object.values(saveNoteEvidence).every(Boolean),
    JSON.stringify(saveNoteEvidence),
  );
  // 只查文本挡不住「被悄悄藏起来」：Chromium 1223 实测 `display:none` 的元素
  // `innerText` **仍取到文本**（规范上退化为 textContent），`opacity:0` / `font-size:0` /
  // 移出屏幕同理。所以这里补一条**计算样式**断言 —— `display:none` 是别人最可能
  // 随手加上去的隐藏方式，若它不红，这段说明随时可以被隐藏而不触发任何东西。
  // （`visibility:hidden` 会让 innerText 变成 ''，本来就会被上面那条抓住。）
  check(
    '说明是**真的可见**的（display 不是 none、visibility 不是 hidden）—— 只查文本挡不住隐藏',
    saveNote.visible === true,
    `display=${JSON.stringify(saveNote.display)} visibility=${JSON.stringify(saveNote.visibility)}`,
  );
  check(
    'V11 判别力：同一个谓词喂「说明被删掉」的世界必须全部为假（真实两态见 --self-test）',
    Object.values(saveNoteClaims({ text: '' })).every((v) => v === false),
    '空文本 → 四个要点全假，与上一行结论相反，判据不是恒真',
  );

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

    // M6 负对照：把出题页那段入库说明**从 DOM 里删掉**，同一个谓词必须翻面。
    // 先断言"删除前它确实在"——否则「删掉后为假」在任何元素都不存在的世界里同样成立，
    // 那样这条负对照就是空的（V9.2：负对照本身也要自证制造了失败条件）。
    const noteCtl = st.save_note_control ?? {};
    const noteMissing = saveNoteClaims(noteCtl.evidence ?? {});
    const noteHidden = noteCtl.hidden_evidence ?? {};
    console.log(
      `  入库说明负对照（先 display:none、再删 DOM）：删除前存在=${noteCtl.existed_before} 隐藏后证据=${JSON.stringify(noteHidden)}`,
    );
    check(
      'M6 负对照：先证明那段说明原本**在**（空集上「删掉后为假」不说明任何事）',
      noteCtl.existed_before === true,
      `删除前存在=${noteCtl.existed_before}`,
    );
    check(
      'M6 负对照：给说明加上 display:none 后，**只有可见性判据**能把它判红（文本仍在，四要点照样全真）—— 这正是补这条的理由',
      noteHidden.display === 'none' &&
        noteHidden.visible === false &&
        Object.values(saveNoteClaims(noteHidden)).every((v) => v === true),
      `display=${JSON.stringify(noteHidden.display)} visible=${noteHidden.visible} 四要点=${JSON.stringify(saveNoteClaims(noteHidden))}`,
    );
    check(
      'M6 负对照：删掉说明后同一条判据的四个要点全部为假 —— 证明这条判据真的在承重',
      Object.values(noteMissing).every((v) => v === false),
      JSON.stringify(noteMissing),
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
