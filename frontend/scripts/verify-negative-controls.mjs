/**
 * 负对照套件（V9 / V9.1 / V9.2）—— **故意让判据失败，证明它们真的会红**。
 *
 * ## 为什么单独一个脚本，而不是塞进主门禁
 *
 * 主门禁的意义是「全绿才算通过」；而这里做的事情是**故意破坏**再断言「应该红」。
 * 两者语义相反，放在同一条命令里会让人读不懂输出（一会儿红一会儿绿）。
 * 但**必须留在仓库里**（V9.1）—— 报告会过期，脚本不会：
 * M3.5 的 task-19 里我就犯过一次，A9 的负对照只跑在一次性探针里，
 * 汇报的原始输出在仓库中 grep 不到，别人无法复核。
 *
 * ## 每个负对照必须验证「注入真的生效了」（V9.2）
 *
 * 这条规矩也是实测换来的：A9 的负对照我最初注入 `.sheet { width: 100% }`，
 * **判据没红** —— 但那不是因为判据有效，而是因为**注入本身没生效**：
 * iframe 的内容视口已经是 A9 宽 793.7px，`100%` 解析出来还是 793.7px。
 * 必须显式给一个比 A4 窄的固定宽度（如 459px）才重现出「被钳制」。
 * 所以本脚本对每个负对照都额外断言「注入后的关键量确实变了」。
 *
 * 用法：node scripts/verify-negative-controls.mjs
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { checkDistFreshness, formatFreshnessResult } from './dist-freshness.mjs';

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

const results = [];
function record(name, controlPasses, detail) {
  results.push({ name, ok: Boolean(controlPasses), detail });
  console.log(`  [${controlPasses ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
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

/**
 * 第 7 组：mock 学生端契约哨兵。
 *
 * 为什么这一组必须存在（本轮真实教训）：门禁⑨ 新增的两条学生端页面（答题页、交卷结果页）
 * **依赖我自己写进 mock 的响应形状**。如果我把形状抄错，界面判据会在一个错误的世界里
 * 全绿 —— 这正是 D69 那一族「抓不住东西的判据」，比红灯危险得多。
 *
 * 本轮真实发生过一次：`answer` 整键在我扩展 mock 时消失了（我把已经为 D74 剥掉答案的
 * wire 数据直接拿去判分），而**当时门禁所有断言都是绿的**，是逐键比对才抓出来。
 *
 * 所以这一组同时做两件事：① 断言 mock 的关键不变量（D74 不泄答案 / D75 回传 seed /
 * D76 幂等 + 状态码）；② 证明这些断言守着的代码路径真的会被执行 —— 另起一个装了
 * `MATHGEN_MOCK_TAMPER` 破坏钩子的服务实例，若它照旧返回 200，就说明钩子没接上、
 * 上面四条断言全是摆设。
 *
 * @param {number} staticPort 主服务实例的静态端口（mock API 也挂在同一个端口上）
 */
async function runMockContractSentinel(staticPort) {
  const base = `http://127.0.0.1:${staticPort}`;
  const out = {};

  const paperRes = await fetch(`${base}/api/student/paper/2610-0001`);
  const paper = await paperRes.json();
  out.status = paperRes.status;
  out.paperKeys = { paperId: paper.paper_id, title: paper.title, total: paper.total };
  out.paperItemKeys = Object.keys(paper.items?.[0] ?? {});

  const seed = 424242;
  // `seed` 走**查询参数**（不是请求体）：真实后端 `backend/app/routers/students.py:63`
  // 是 `seed: int | None = Query(default=None)`，前端 `student/api.ts:219` 也从不往 body 里放 seed
  // —— 不给 seed 时服务端随机取一个并把**实际用的**返回（D75）。我第一版把 seed 写进了 body，
  // 结果 mock 走 djb2Seed(config) 分支、哨兵断言 424242 失败，而失败原因看起来像「mock 抄错了形状」。
  const practiceRes = await fetch(`${base}/api/student/practice?seed=${seed}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ops: ['+', '-'], ranges: ['R1'], question_type: 'oral', count: 6 }),
  });
  const practice = await practiceRes.json();
  out.practiceStatus = practiceRes.status;
  out.practiceSeed = practice.seed;
  out.practiceItemKeys = Object.keys(practice.items?.[0] ?? {});

  const attemptBody = {
    student_id: 7,
    attempt_uuid: 'cccccccc-0000-0000-0000-00000000c001',
    answers: (practice.items ?? []).map((it) => ({ seq: it.seq, answer: '0' })),
    duration_ms: 1000,
    config: { ops: ['+', '-'], ranges: ['R1'], question_type: 'oral', count: 6 },
    seed,
  };
  const attemptRes = await fetch(`${base}/api/attempts`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(attemptBody),
  });
  const attempt = await attemptRes.json();
  out.attemptStatus = attemptRes.status;
  out.firstAttemptId = attempt.attempt_id;
  out.attemptItemKeys = Object.keys(attempt.items?.[0] ?? {});

  const replayRes = await fetch(`${base}/api/attempts`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(attemptBody),
  });
  const replay = await replayRes.json();
  out.replayStatus = replayRes.status;
  out.replayAttemptId = replay.attempt_id;

  // ② 破坏钩子：另起一个服务实例，证明上面这几条路径确实会被执行。
  const tamperPorts = { static: BASE_STATIC_PORT + 7, api: BASE_API_PORT + 7 };
  const tamperChild = spawn(NODE_EXE, [path.join(__dirname, 'verify-server.mjs')], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      MATHGEN_VERIFY_STATIC_PORT: String(tamperPorts.static),
      MATHGEN_VERIFY_API_PORT: String(tamperPorts.api),
      MATHGEN_VERIFY_MODE: 'online',
      MATHGEN_MOCK_TAMPER: 'student_paper',
    },
  });
  const ready = await new Promise((resolve) => {
    let buffer = '';
    const timer = setTimeout(() => resolve(false), 15_000);
    tamperChild.stdout.on('data', (chunk) => {
      buffer += chunk;
      if (buffer.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve(true);
      }
    });
    tamperChild.on('exit', () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
  if (ready) {
    const res = await fetch(
      `http://127.0.0.1:${tamperPorts.static}/api/student/paper/2610-0001`,
    );
    const body = await res.json();
    out.tamperStatus = res.status;
    out.tamperCode = body?.error?.code;
  } else {
    out.tamperStatus = 0;
    out.tamperCode = '(装了破坏钩子的服务实例没起来)';
  }
  tamperChild.kill();

  return out;
}

/**
 * 内联 Python：跑一组负对照，每组给出「基线 / 注入后」两次测量。
 *
 * ⚠️ 本模板是 `String.raw` 字面量：**块内不要用反引号**，否则会提前终止模板
 * （M3.5 连续踩过三次，AGENTS.md 也记了这一条）。
 */
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
    return sorted(candidates)[-1] if candidates else None

A4_W = (210 / 25.4) * 96      # 793.698...
A4_H = (297 / 25.4) * 96      # 1122.52...

# ---- 判据本体：与 gate 8（verify-preview-scale.mjs）保持同一套阈值 ----
def a9_probe(page):
    return page.evaluate("""() => {
        const f = document.getElementById('sheet-frame');
        const d = f && f.contentDocument;
        const sheet = d && d.querySelector('.sheet');
        if (!sheet) return null;
        const cs = getComputedStyle(sheet);
        const r = f.getBoundingClientRect();
        return {
            sheetWidth: parseFloat(cs.width),
            paddingLeft: parseFloat(cs.paddingLeft),
            ratio: parseFloat(cs.paddingLeft) / parseFloat(cs.width),
            scaleX: r.width / f.offsetWidth,
            scaleY: r.height / f.offsetHeight,
        };
    }""")

def a9_checks(m):
    if not m:
        return {"A9-1": False, "A9-2": False, "A9-3": False}
    return {
        "A9-1 .sheet 宽度 793.7+-2": abs(m["sheetWidth"] - A4_W) <= 2,
        "A9-2 padding/宽 =0.0952+-0.002": abs(m["ratio"] - 0.0952) <= 0.002,
        "A9-3 scaleX == scaleY": abs(m["scaleX"] - m["scaleY"]) < 0.005,
    }

# ---- A10：无开发者文本（与门禁⑨ verify-ui-voice.mjs 同一套检测）----
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
        // 与门禁⑨ 逐字一致：按**编号族形状**覆盖，而不是枚举我想到的那几个。
        // 教训：第一版只列了 D\\d+ / --xxx / FR-xx，漏掉了 R1-R7；扩的时候又发现
        // 旧的正则 [A-Z]? 只允许一个字母，抓不到 fr-t14 这类三字母写法。
        const families = [
            ['决策编号', /\\bD\\d+\\b/g],
            ['数值范围档', /\\bR\\d+\\b/g],
            ['需求问答', /\\bQ\\d+\\b/g],
            ['非功能需求', /\\bNFR-\\d+/g],
            ['功能需求', /\\bFR-[A-Z]?\\d+/g],
            ['里程碑', /\\bM\\d+\\b/g],
            ['判据编号', /\\bA\\d+\\b/g],
        ];
        for (const [label, re] of families) {
            const m = own.match(re);
            if (m) f.push(label + ':' + m.join(','));
        }
        const cssvar = own.match(/--[a-z][a-z0-9-]*/g);
        if (cssvar) f.push('CSS变量:' + cssvar.join(','));
        const kw = own.match(/\\b(none|block|grid|flex|inline)\\b/g);
        if (kw) f.push('CSS关键字:' + kw.join(','));

        // 具名例外：A4 是纸型，老师需要看到。只放行精确串，不放过整个 A\\d+ 族。
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

# ---- A11：灰底只用于画布（与门禁⑨ verify-ui-voice.mjs 同一套判据）----
# 根容器选择器必须与门禁⑨ 的 BG_SCAN **逐字一致**：老师端是
# .config-page/.library-page/.publish-page，学生端是 .st-screen。
# 只写老师端那三个 class 时，学生端 root=null → 判据悄悄退化成"只查 body"。
BG_PROBE = """() => {
    const cs = (el) => {
        if (!el) return null;
        const c = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {bg: c.backgroundColor, bgImage: c.backgroundImage,
                h: Math.round(r.height), x: Math.round(r.x), w: Math.round(r.width)};
    };
    const root = document.querySelector(
        '.config-page, .library-page, .publish-page, .st-screen');
    return {body: cs(document.body), root: cs(root),
            rootSelector: root ? (root.className || root.tagName) : null,
            canvas: cs(document.querySelector('.mg-canvas')),
            viewport: {w: window.innerWidth, h: window.innerHeight}};
}"""

# ---- 焦点环判据（与 gate 7 的 A3 一致：聚焦 AND 可见环 的合取）----
FOCUS_PROBE = """() => {
    const stage = document.querySelector('.mg-paper-stage');
    if (!stage) return null;
    const cs = getComputedStyle(stage);
    const focused = document.activeElement === stage;
    const visibleRing = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
    return {
        focused,
        visibleRing,
        outlineStyle: cs.outlineStyle,
        outlineWidth: cs.outlineWidth,
        boxShadow: cs.boxShadow,
        // 新判据：聚焦且可见环
        newCheck: focused && visibleRing,
        // 旧判据（曾被误称为「功能断言」）：只看宽度 —— outline-width 初值就是 medium=3px，恒真
        legacyCheck: (parseFloat(cs.outlineWidth) || 0) > 0,
    };
}"""

result = {"a9": {}, "focus": {}, "a10": {}, "a10_student": {}, "a11": {}, "a11_student": {}, "console_errors": []}
exe = find_chromium()
result["chromium"] = exe

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: result["console_errors"].append(str(e)))
    page.goto(BASE + "/teacher/paper/1", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const f = document.getElementById('sheet-frame');"
        " return f && f.contentDocument && f.contentDocument.querySelector('.sheet'); }",
        timeout=20000)
    page.wait_for_timeout(500)

    # ---------- A9 负对照 ----------
    result["a9"]["baseline"] = {"measured": a9_probe(page)}
    result["a9"]["baseline"]["checks"] = a9_checks(result["a9"]["baseline"]["measured"])

    # A9-N1：注入错误的页边距（模拟「纸内几何被算错」）。比例判据应当红，宽度判据不受影响。
    page.evaluate("""() => {
        const d = document.getElementById('sheet-frame').contentDocument;
        d.documentElement.style.setProperty('--page-margin', '3cm');
    }""")
    page.wait_for_timeout(250)
    result["a9"]["n1_margin3cm"] = {"measured": a9_probe(page)}
    result["a9"]["n1_margin3cm"]["checks"] = a9_checks(result["a9"]["n1_margin3cm"]["measured"])
    # 还原
    page.evaluate("""() => {
        const d = document.getElementById('sheet-frame').contentDocument;
        d.documentElement.style.setProperty('--page-margin', '2cm');
    }""")
    page.wait_for_timeout(200)

    # A9-N2：显式把纸钳制到旧容器宽度（重现 D68 缺陷本身）。
    # 注意：**不能用 width:100%** —— iframe 内容视口已经是 793.7px，100% 解析出来还是 793.7px，
    # 注入等于没注入，会得到一个假的「判据没问题」结论（实测踩过）。
    page.evaluate("""() => {
        const d = document.getElementById('sheet-frame').contentDocument;
        const s = d.createElement('style');
        s.id = 'negative-control-clamp';
        s.textContent = '.sheet { width: 459px !important; }';
        d.head.appendChild(s);
    }""")
    page.wait_for_timeout(250)
    result["a9"]["n2_clamp459"] = {"measured": a9_probe(page)}
    result["a9"]["n2_clamp459"]["checks"] = a9_checks(result["a9"]["n2_clamp459"]["measured"])
    # 还原，供后面的焦点负对照在「正常页面」上跑
    page.evaluate("""() => {
        const d = document.getElementById('sheet-frame').contentDocument;
        const s = d.getElementById('negative-control-clamp');
        if (s) s.remove();
    }""")
    page.wait_for_timeout(200)
    result["a9"]["restored"] = {"measured": a9_probe(page)}
    result["a9"]["restored"]["checks"] = a9_checks(result["a9"]["restored"]["measured"])

    # ---------- 焦点环负对照 ----------
    # 基线（未聚焦）→ 断言必须为红；再真实 Tab 到卷面 → 断言必须为绿。
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); document.body.focus(); }")
    page.wait_for_timeout(120)
    result["focus"]["baseline_unfocused"] = page.evaluate(FOCUS_PROBE)

    page.evaluate("() => { document.activeElement && document.activeElement.blur(); document.body.focus(); }")
    reached = None
    for _ in range(40):
        page.keyboard.press("Tab")
        page.wait_for_timeout(40)
        probe = page.evaluate(FOCUS_PROBE)
        if probe and probe["focused"]:
            reached = probe
            break
    result["focus"]["after_real_tab"] = reached

    # ---------- A10 负对照：无开发者文本 ----------
    # 与门禁⑨同一套检测逻辑（照搬过来，确保验的就是同一条判据）。
    # 做法：往页面里塞一个**可见**的哨兵元素，内部文本含决策编号 / CSS 变量 / CSS 关键字，
    # 检测器必须把它抓出来。
    dev_page = browser.new_page(viewport={"width": 1440, "height": 900})
    dev_page.goto(BASE + "/teacher", wait_until="domcontentloaded")
    dev_page.wait_for_selector("[data-dimension='answer_form']", timeout=20000)
    dev_page.wait_for_timeout(600)
    result["a10"]["baseline"] = dev_page.evaluate(DEV_SCAN)
    # 哨兵 1：决策编号 + CSS 变量（模拟旧版「调试小字」）
    dev_page.evaluate("""() => {
        const p = document.createElement('p');
        p.id = 'negative-control-devtext';
        p.style.cssText = 'position:static;display:block;color:#000;font-size:12px;';
        p.textContent = '页边距 --page-margin（D56）';
        document.querySelector('.config-page').appendChild(p);
    }""")
    dev_page.wait_for_timeout(200)
    result["a10"]["with_sentinel"] = dev_page.evaluate(DEV_SCAN)
    # 哨兵 2：单独的裸 CSS 关键字（验证关键字那条分支也会红）
    dev_page.evaluate("""() => {
        const s = document.getElementById('negative-control-devtext');
        s.textContent = 'display: block';
    }""")
    dev_page.wait_for_timeout(200)
    result["a10"]["with_keyword"] = dev_page.evaluate(DEV_SCAN)
    # 哨兵 2b：数值范围档编号（R1）—— 这是 Lead 看图抓到的那一类，必须有负对照兜住，
    # 否则将来有人为了变绿把 R\\d+ 从黑名单删掉，门禁不会红。
    dev_page.evaluate("""() => {
        const s = document.getElementById('negative-control-devtext');
        s.textContent = 'R1 · 20 以内加减法';
    }""")
    dev_page.wait_for_timeout(200)
    result["a10"]["with_range_id"] = dev_page.evaluate(DEV_SCAN)
    # 哨兵 2c：**例外项** A4 —— 纸型是老师要看的信息，检测器**不应**报。
    # 这条与上一条成对：证明「按形状覆盖 + 具名例外」既抓得住 R1，又不会误伤 A4。
    dev_page.evaluate("""() => {
        const s = document.getElementById('negative-control-devtext');
        s.textContent = '打印成 A4 卷子';
    }""")
    dev_page.wait_for_timeout(200)
    result["a10"]["with_a4_allowed"] = dev_page.evaluate(DEV_SCAN)
    # 哨兵 2d：同形状但**不在**例外清单里的 A9 —— 必须被抓（证明例外是"具名"而非"整类放过"）
    dev_page.evaluate("""() => {
        const s = document.getElementById('negative-control-devtext');
        s.textContent = '判据 A9 未通过';
    }""")
    dev_page.wait_for_timeout(200)
    result["a10"]["with_a9_not_allowed"] = dev_page.evaluate(DEV_SCAN)
    # 哨兵 3：仅视觉隐藏（display:none）→ 检测器**不应**报（证明判据看的是"可见"而不是"存在"）
    dev_page.evaluate("""() => {
        const s = document.getElementById('negative-control-devtext');
        s.style.display = 'none';
    }""")
    dev_page.wait_for_timeout(200)
    result["a10"]["hidden_sentinel"] = dev_page.evaluate(DEV_SCAN)
    dev_page.close()

    # ---------- A10 负对照（学生端覆盖）：判据真的扫到了学生端页面 ----------
    # 为什么需要这一组：A10 的 CASES 原先是 5 条纯 /teacher/**。给学生端加页面时，
    # 「加了但没生效」是最可能的失败模式，而且它**不会报错**——只会安静地少查几个页面。
    # 更早还有一层更隐蔽的坑：verify-server.mjs 的 SPA 回落在 catch 分支里**一律**回
    # dist/index.html，于是 /student/** 拿到的是**老师端**入口；那样判据会拿学生端的
    # URL 扫老师端的页面，全绿且完全看不出问题（断言写的是"0 处开发者文本"，老师端
    # 本来就是 0 处）。这一组负对照就是钉住"学生端 URL 真的渲染的是学生端"。
    stu_page = browser.new_page(viewport={"width": 1440, "height": 900})
    stu_page.goto(BASE + "/student", wait_until="domcontentloaded")
    try:
        stu_page.wait_for_selector(".st-screen", timeout=15000)
    except Exception:
        pass
    stu_page.wait_for_timeout(600)
    result["a10_student"]["shell"] = stu_page.evaluate("""() => {
        const root = document.getElementById('root');
        return {
            stScreen: !!document.querySelector('.st-screen'),
            teacherShell: !!document.querySelector(
                '.config-page, .library-page, .publish-page, .teacher-shell'),
            rootChildCls: root && root.firstElementChild
                ? (root.firstElementChild.className || root.firstElementChild.tagName) : null,
        };
    }""")
    result["a10_student"]["baseline"] = stu_page.evaluate(DEV_SCAN)
    stu_page.evaluate("""() => {
        const p = document.createElement('p');
        p.id = 'negative-control-devtext-student';
        p.style.cssText = 'position:static;display:block;color:#000;font-size:12px;';
        p.textContent = '题号种子 --page-margin（D56）';
        document.querySelector('.st-screen').appendChild(p);
    }""")
    stu_page.wait_for_timeout(200)
    result["a10_student"]["with_sentinel"] = stu_page.evaluate(DEV_SCAN)
    # 第二条学生端路由：/student/result 是**唯一不需要身份就能看全量文案**的页面
    # （练习结果 + 空态说明 + 回首页），单独确认它也被 DEV_SCAN 扫到、且基线干净。
    stu_page.goto(BASE + "/student/result", wait_until="domcontentloaded")
    try:
        stu_page.wait_for_selector(".st-screen", timeout=15000)
    except Exception:
        pass
    stu_page.wait_for_timeout(500)
    result["a10_student"]["result_page_baseline"] = stu_page.evaluate(DEV_SCAN)
    result["a10_student"]["result_page_shell"] = stu_page.evaluate("""() => {
        const root = document.getElementById('root');
        return {
            stScreen: !!document.querySelector('.st-screen'),
            teacherShell: !!document.querySelector(
                '.config-page, .library-page, .publish-page, .teacher-shell'),
            rootChildCls: root && root.firstElementChild
                ? (root.firstElementChild.className || root.firstElementChild.tagName) : null,
        };
    }""")
    stu_page.close()

    # ---------- A11 负对照（学生端覆盖）：学生端底色也走同一条判据 ----------
    # 学生端页面根容器是 .st-screen，**不是**老师端的 .config-page/.library-page/.publish-page。
    # 若 BG_SCAN 只查老师端那三个 class，学生端会 root=null → 判据退化成"只查 body"，
    # 仍然全绿。这一组连同上面的 shell 断言一起把"选择器真的匹配到了容器"钉住。
    stu_bg_page = browser.new_page(viewport={"width": 1440, "height": 900})
    stu_bg_page.goto(BASE + "/student", wait_until="domcontentloaded")
    try:
        stu_bg_page.wait_for_selector(".st-screen", timeout=15000)
    except Exception:
        pass
    stu_bg_page.wait_for_timeout(600)
    result["a11_student"]["baseline"] = stu_bg_page.evaluate(BG_PROBE)
    stu_bg_page.evaluate("""() => {
        const el = document.querySelector('.st-screen');
        el.style.backgroundColor = '#8e939a';
    }""")
    stu_bg_page.wait_for_timeout(200)
    result["a11_student"]["injected_desk"] = stu_bg_page.evaluate(BG_PROBE)
    stu_bg_page.evaluate("""() => {
        const el = document.querySelector('.st-screen');
        el.style.backgroundColor = '';
    }""")
    stu_bg_page.wait_for_timeout(200)
    result["a11_student"]["restored"] = stu_bg_page.evaluate(BG_PROBE)
    stu_bg_page.close()

    # ---------- A11 负对照：灰底只用于画布 ----------
    # 做法：把配置页底色硬改成灰桌面色 → 判据必须变红。
    # ⚠️ 这里必须**显式改颜色值**，不能改成 var(--mg-desk-1) 就以为生效 ——
    #    改完要读回 computed 颜色确认真的变成了 #8e939a（V9.2）。
    bg_page = browser.new_page(viewport={"width": 1440, "height": 900})
    bg_page.goto(BASE + "/teacher", wait_until="domcontentloaded")
    bg_page.wait_for_selector("[data-dimension='answer_form']", timeout=20000)
    bg_page.wait_for_timeout(600)
    result["a11"]["baseline"] = bg_page.evaluate(BG_PROBE)
    bg_page.evaluate("""() => {
        const page = document.querySelector('.config-page');
        page.style.background = '#8e939a';
        document.body.style.background = '#8e939a';
    }""")
    bg_page.wait_for_timeout(200)
    result["a11"]["injected_desk"] = bg_page.evaluate(BG_PROBE)
    # 还原（并确认还原后真的回到浅底）
    bg_page.evaluate("""() => {
        const page = document.querySelector('.config-page');
        page.style.background = '';
        document.body.style.background = '';
    }""")
    bg_page.wait_for_timeout(200)
    result["a11"]["restored"] = bg_page.evaluate(BG_PROBE)
    bg_page.close()

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
  let mockContract = {};
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
    // mock 契约哨兵必须在**服务还活着**的时候打（下面 finally 里就 kill 了）。
    // 我第一版把这一组写在 finally 之后，`server.staticPort` 已经是个死端口 ——
    // 那组断言会全部失败，而失败原因看起来像"mock 抄错了形状"，非常误导。
    mockContract = await runMockContractSentinel(server.staticPort);
  } finally {
    server.child.kill();
  }

  console.log(`Chromium: ${result.chromium ?? '(默认)'}`);

  const fmt = (m) =>
    m
      ? `宽=${m.sheetWidth.toFixed(2)} padding=${m.paddingLeft.toFixed(2)} ratio=${m.ratio.toFixed(4)} ` +
        `scaleX=${m.scaleX.toFixed(5)} scaleY=${m.scaleY.toFixed(5)}`
      : '(量不到)';

  const a9 = result.a9 ?? {};
  console.log('\n—— 负对照 1/11：A9 纸内几何（判据见门禁⑧ verify-preview-scale.mjs）——');
  for (const [key, label] of [
    ['baseline', '基线（无注入）'],
    ['n1_margin3cm', '负对照 A9-N1：注入 --page-margin:3cm'],
    ['n2_clamp459', '负对照 A9-N2：把 .sheet 钳制到 459px（重现 D68）'],
    ['restored', '还原后'],
  ]) {
    const entry = a9[key];
    if (!entry) continue;
    console.log(`  ${label}`);
    console.log(`    实测 ${fmt(entry.measured)}`);
    for (const [k, v] of Object.entries(entry.checks ?? {})) {
      console.log(`      ${v ? '绿' : '红'}  ${k}`);
    }
  }

  console.log('\n—— 负对照 2/11：卷面焦点环（判据见门禁⑦ 的 A3）——');
  const fBase = result.focus?.baseline_unfocused;
  const fTab = result.focus?.after_real_tab;
  console.log('  基线（未聚焦）');
  console.log(
    `    聚焦=${fBase?.focused} outlineStyle=${fBase?.outlineStyle} outlineWidth=${fBase?.outlineWidth} ` +
      `boxShadow=${fBase?.boxShadow}`,
  );
  console.log(`      新判据（聚焦 AND 可见环）= ${fBase?.newCheck}  ← 必须为 False（否则恒真）`);
  console.log(`      旧写法（只看 outlineWidth>0）= ${fBase?.legacyCheck}  ← 恒真：未聚焦也为 True`);
  console.log('  真实 Tab 到卷面后');
  console.log(
    `    聚焦=${fTab?.focused} outlineStyle=${fTab?.outlineStyle} outlineWidth=${fTab?.outlineWidth} ` +
      `boxShadow=${fTab?.boxShadow}`,
  );
  console.log(`      新判据 = ${fTab?.newCheck}  ← 必须为 True`);

  console.log('\n—— 结论（每条负对照都要「注入后判据变红」）——');

  record(
    'A9：基线下三条判据全绿（否则负对照无意义）',
    Object.values(a9.baseline?.checks ?? {}).every(Boolean),
    JSON.stringify(a9.baseline?.checks),
  );
  record(
    'A9-N1（注入 3cm 边距）：比例判据 A9-2 变红',
    a9.n1_margin3cm?.checks?.['A9-2 padding/宽 =0.0952+-0.002'] === false,
    `ratio=${a9.n1_margin3cm?.measured?.ratio?.toFixed(4)}`,
  );
  record(
    'A9-N1 注入真的生效（关键量确实变了，不是「没注入成功」）',
    a9.n1_margin3cm?.measured?.paddingLeft > a9.baseline?.measured?.paddingLeft,
    `padding ${a9.baseline?.measured?.paddingLeft?.toFixed(2)} → ${a9.n1_margin3cm?.measured?.paddingLeft?.toFixed(2)}`,
  );
  record(
    'A9-N2（重现钳制 459px）：宽度判据 A9-1 变红',
    a9.n2_clamp459?.checks?.['A9-1 .sheet 宽度 793.7+-2'] === false,
    `宽=${a9.n2_clamp459?.measured?.sheetWidth?.toFixed(2)}`,
  );
  record(
    'A9-N2 注入真的生效（关键量确实变了）',
    a9.n2_clamp459?.measured?.sheetWidth < a9.baseline?.measured?.sheetWidth - 100,
    `宽 ${a9.baseline?.measured?.sheetWidth?.toFixed(2)} → ${a9.n2_clamp459?.measured?.sheetWidth?.toFixed(2)}`,
  );
  record(
    'A9 还原后判据重新全绿（负对照没留下副作用）',
    Object.values(a9.restored?.checks ?? {}).every(Boolean),
    JSON.stringify(a9.restored?.checks),
  );

  record(
    '焦点环：未聚焦时新判据为红（会失败，不是恒真）',
    fBase?.newCheck === false,
    `newCheck=${fBase?.newCheck}`,
  );
  record(
    '焦点环：旧写法在同样输入下为真（说明它恒真，证明不了任何事）',
    fBase?.legacyCheck === true,
    `legacyCheck=${fBase?.legacyCheck}（outlineWidth=${fBase?.outlineWidth}）`,
  );
  record(
    '焦点环：真实 Tab 到卷面后新判据为绿',
    fTab?.newCheck === true,
    `outline=${fTab?.outlineStyle} ${fTab?.outlineWidth} boxShadow=${fTab?.boxShadow}`,
  );

  // ---------- A10：无开发者文本 ----------
  const a10 = result.a10 ?? {};
  const sentinelHit = (a10.with_sentinel ?? []).find((h) => (h.text ?? '').includes('--page-margin'));
  const keywordHit = (a10.with_keyword ?? []).find((h) => (h.text ?? '').includes('block'));
  console.log('\n—— 负对照 3/11：无开发者文本（判据见门禁⑨ verify-ui-voice.mjs）——');
  console.log(`  基线（真实页面）：命中 ${(a10.baseline ?? []).length} 处  ← 必须为 0`);
  console.log(
    `  注入哨兵「页边距 --page-margin（D56）」：命中 ${(a10.with_sentinel ?? []).length} 处` +
      `  → ${sentinelHit ? JSON.stringify(sentinelHit.found) : '(未抓到！)'}`,
  );
  console.log(
    `  注入哨兵「display: block」：命中 ${(a10.with_keyword ?? []).length} 处` +
      `  → ${keywordHit ? JSON.stringify(keywordHit.found) : '(未抓到！)'}`,
  );
  console.log(
    `  把哨兵改成 display:none：命中 ${(a10.hidden_sentinel ?? []).length} 处  ← 应为 0（判据看"可见"而非"存在"）`,
  );
  const rangeIdHit = (a10.with_range_id ?? []).find((h) => (h.text ?? '').includes('R1'));
  const a9Hit = (a10.with_a9_not_allowed ?? []).find((h) => (h.text ?? '').includes('A9'));
  console.log(
    `  注入「R1 · 20 以内加减法」：命中 ${(a10.with_range_id ?? []).length} 处` +
      `  → ${rangeIdHit ? JSON.stringify(rangeIdHit.found) : '(未抓到！)'}   ← 必须抓到`,
  );
  console.log(
    `  注入「打印成 A4 卷子」（**例外项**）：命中 ${(a10.with_a4_allowed ?? []).length} 处` +
      `   ← 必须为 0（A4 是纸型，老师要看）`,
  );
  console.log(
    `  注入「判据 A9 未通过」（同形状但**不在**例外清单）：命中 ${(a10.with_a9_not_allowed ?? []).length} 处` +
      `  → ${a9Hit ? JSON.stringify(a9Hit.found) : '(未抓到！)'}   ← 必须抓到（证明例外是具名的，不是整类放过）`,
  );
  record(
    'A10 基线：真实页面可见文本里 0 处开发者文本',
    (a10.baseline ?? []).length === 0,
    `命中 ${(a10.baseline ?? []).length} 处`,
  );
  record(
    'A10 负对照：注入含决策编号/CSS 变量的可见文本 → 判据变红并分别报出两类',
    Boolean(sentinelHit)
      && sentinelHit.found.some((f) => f.startsWith('决策编号'))
      && sentinelHit.found.some((f) => f.startsWith('CSS变量')),
    sentinelHit ? JSON.stringify(sentinelHit.found) : '未抓到',
  );
  record(
    'A10 负对照：注入裸 CSS 关键字（block）→ 判据变红',
    Boolean(keywordHit) && keywordHit.found.some((f) => f.startsWith('CSS关键字')),
    keywordHit ? JSON.stringify(keywordHit.found) : '未抓到',
  );
  record(
    'A10 负对照：注入数值范围档编号（R1）→ 判据变红（这类曾被枚举黑名单漏掉）',
    Boolean(rangeIdHit) && rangeIdHit.found.some((f) => f.startsWith('数值范围档')),
    rangeIdHit ? JSON.stringify(rangeIdHit.found) : '未抓到',
  );
  record(
    'A10 例外项：「A4」（纸型）**不**被判为违规 —— 老师需要看到它',
    (a10.with_a4_allowed ?? []).length === 0,
    `命中 ${(a10.with_a4_allowed ?? []).length} 处`,
  );
  record(
    'A10 例外是**具名**的：同形状的「A9」仍会被抓（不是把 A\\d+ 整类放过）',
    Boolean(a9Hit),
    a9Hit ? JSON.stringify(a9Hit.found) : '未抓到',
  );
  record(
    'A10 负对照有效性：把哨兵 display:none 后**不再**命中（证明判据看的是可见性，不是「源码里有」）',
    (a10.hidden_sentinel ?? []).length === 0,
    `命中 ${(a10.hidden_sentinel ?? []).length} 处`,
  );

  // ---------- A11：灰底只用于画布 ----------
  const a11 = result.a11 ?? {};
  const baseBg = a11.baseline ?? {};
  const injBg = a11.injected_desk ?? {};
  const restoredBg = a11.restored ?? {};
  console.log('\n—— 负对照 4/11：灰底只用于画布（判据见门禁⑨）——');
  console.log(`  基线：root=${baseBg.root?.bg} body=${baseBg.body?.bg}  ← 应为 #f7f8f9（rgb(247,248,249)）`);
  console.log(`  注入灰桌面色 #8e939a：root=${injBg.root?.bg} body=${injBg.body?.bg}  ← 应变成 rgb(142,147,154)`);
  console.log(`  还原后：root=${restoredBg.root?.bg} body=${restoredBg.body?.bg}`);
  record(
    'A11 基线：配置页底色是浅底 #f7f8f9（不是灰桌面）',
    baseBg.root?.bg === 'rgb(247, 248, 249)' && baseBg.body?.bg === 'rgb(247, 248, 249)',
    `root=${baseBg.root?.bg} body=${baseBg.body?.bg}`,
  );
  record(
    'A11 负对照：把配置页底色改成灰桌面 → 判据变红',
    injBg.root?.bg === 'rgb(142, 147, 154)' && injBg.body?.bg === 'rgb(142, 147, 154)',
    `root=${injBg.root?.bg} body=${injBg.body?.bg}`,
  );
  record(
    'A11 负对照有效性：注入真的生效（关键量确实从浅底变成灰桌面，不是「没注入成功」）',
    baseBg.root?.bg !== injBg.root?.bg,
    `${baseBg.root?.bg} → ${injBg.root?.bg}`,
  );
  record(
    'A11 还原后判据重新为绿（负对照没留下副作用）',
    restoredBg.root?.bg === 'rgb(247, 248, 249)' && restoredBg.body?.bg === 'rgb(247, 248, 249)',
    `root=${restoredBg.root?.bg} body=${restoredBg.body?.bg}`,
  );

  // ---------- 5/11：学生端覆盖（A10 + A11 的覆盖面真实性） ----------
  // 这一组盯的是**覆盖面的真实性**，不是文案本身。历史坑有两个，都会"全绿但没查"：
  //   (a) verify-server.mjs 的 SPA 回落在 catch 分支里一律回 dist/index.html，于是
  //       /student/** 拿到的是**老师端**入口 —— 判据会拿学生端 URL 扫老师端页面；
  //   (b) A11 的根容器选择器若只写老师端那三个 class，学生端 root=null，判据退化成
  //       "只查 body"，照样绿。
  // 所以这一组同时断言"渲染的确实是学生端"和"根容器选择器真的匹配到 .st-screen"，
  // 再各自注入一次真正的违规/灰底，证明判据在学生端页面上真的会红。
  const a10s = result.a10_student ?? {};
  const stuShell = a10s.shell ?? {};
  const stuResShell = a10s.result_page_shell ?? {};
  const stuSentinel = (a10s.with_sentinel ?? []).find(
    (h) => (h.text ?? '').includes('--page-margin'));
  const a11s = result.a11_student ?? {};
  const sBase = a11s.baseline ?? {};
  const sInj = a11s.injected_desk ?? {};
  const sRestored = a11s.restored ?? {};
  console.log('\n—— 负对照 5/11：学生端覆盖（判据见门禁⑨ 的 A10/A11）——');
  console.log(`  A10 /student        根容器=${stuShell.rootChildCls} stScreen=${stuShell.stScreen} teacherShell=${stuShell.teacherShell}`);
  console.log(`  A10 /student/result 根容器=${stuResShell.rootChildCls} stScreen=${stuResShell.stScreen}`);
  console.log(`  A10 基线命中：/student ${(a10s.baseline ?? []).length} 处；/student/result ${(a10s.result_page_baseline ?? []).length} 处；注入哨兵后 ${(a10s.with_sentinel ?? []).length} 处`);
  console.log(`  A11 基线：rootSelector=${sBase.rootSelector} root=${sBase.root?.bg}(h=${sBase.root?.h}) body=${sBase.body?.bg}(h=${sBase.body?.h}) 视口 h=${sBase.viewport?.h}`);
  console.log(`  A11 注入灰桌面色：root=${sInj.root?.bg} body=${sInj.body?.bg}；还原后 ${sRestored.root?.bg}`);
  record(
    'A10 学生端覆盖：/student 渲染的确实是学生端（.st-screen），不是老师端壳',
    stuShell.stScreen === true && stuShell.teacherShell === false && stuShell.rootChildCls === 'st-screen',
    `stScreen=${stuShell.stScreen} teacherShell=${stuShell.teacherShell} rootChildCls=${stuShell.rootChildCls}`,
  );
  record(
    'A10 学生端覆盖：/student/result 同样是学生端（这条路由不需身份，能看全量文案）',
    stuResShell.stScreen === true && stuResShell.teacherShell === false,
    `stScreen=${stuResShell.stScreen} teacherShell=${stuResShell.teacherShell}`,
  );
  record(
    'A10 学生端基线：学生端可见文本里 0 处开发者文本',
    (a10s.baseline ?? []).length === 0 && (a10s.result_page_baseline ?? []).length === 0,
    `/student 命中 ${(a10s.baseline ?? []).length} 处；/student/result 命中 ${(a10s.result_page_baseline ?? []).length} 处`,
  );
  record(
    'A10 学生端负对照有效性：往 .st-screen 里注入可见的开发者文本 → 判据变红并具名',
    Boolean(stuSentinel)
      && stuSentinel.found.some((f) => f.startsWith('决策编号'))
      && stuSentinel.found.some((f) => f.startsWith('CSS变量')),
    stuSentinel ? JSON.stringify(stuSentinel.found) : '未抓到（说明学生端页面根本没被扫到）',
  );
  record(
    'A11 学生端覆盖：根容器选择器匹配到 .st-screen（不是 null 而退化成只查 body）',
    sBase.rootSelector === 'st-screen' && sRestored.rootSelector === 'st-screen',
    `rootSelector=${sBase.rootSelector}`,
  );
  record(
    'A11 学生端基线：学生端铺底元素底色 = 浅底 #f7f8f9（与老师端同一令牌）',
    sBase.root?.bg === 'rgb(247, 248, 249)',
    `root=${sBase.root?.bg} body=${sBase.body?.bg}`,
  );
  // ↓ 发现 F1 的**哨兵已按生命周期撤除**（2026-10-15 18:46 学生端修复 + dist 重建）。
  //   哨兵原文断言「body 没有底色、且文档根仍有浏览器默认 8px 边距」——即断言
  //   **该缺陷当前仍然存在**。fe-student 在 `frontend/src/student/student.css` 顶部
  //   补上 `html, body { margin: 0; background: var(--mg-surface-sunken); }` 之后，
  //   两条哨兵如期自己变红（这正是它工作正常），于是按约定撤除：
  //   撤除本身就是「缺陷已修」的证据，所以我把它连同修复数字一起记在这里，
  //   而不是把它删得无影无踪。
  //
  //   修复前后实测（390×844 真实 Chromium，fe-student 提供、我复测）：
  //     body margin   8px   → 0px
  //     body rect     8/374 → 0/390
  //     .st-screen 宽 374   → 390
  //     scrollHeight  860   → 844（多余 16px → 0px）
  //     body 底色     rgba(0,0,0,0) → rgb(247,248,249)
  //   本套件下面这条断言的是**修复后的事实**（不是"哨兵还在"）：
  record(
    'F1 已修（哨兵兑现）：学生端文档根不再有浏览器默认边距，且 body 有浅底令牌色',
    Number(sBase.body?.x ?? -1) === 0 && sBase.body?.bg === 'rgb(247, 248, 249)',
    `body.x=${sBase.body?.x} body.bg=${sBase.body?.bg}（哨兵于 F1 修复后撤除，见上方说明）`,
  );
  record(
    'A11 学生端负对照：把 .st-screen 底色改成灰桌面 → 判据变红',
    sInj.root?.bg === 'rgb(142, 147, 154)',
    `root=${sInj.root?.bg}`,
  );
  record(
    'A11 学生端负对照有效性：注入真的生效（关键量确实变了，不是「没注入成功」）',
    sBase.root?.bg !== sInj.root?.bg,
    `${sBase.root?.bg} → ${sInj.root?.bg}`,
  );
  record(
    'A11 学生端还原后重新为绿（负对照没留下副作用）',
    sRestored.root?.bg === 'rgb(247, 248, 249)',
    `root=${sRestored.root?.bg}`,
  );

  // ---------- 6/11：裸 /api 路径扫描（判据见门禁② verify-contract.py 的 ④） ----------
  // 这条判据历史上真的**空过**：排除条件按**文件名**（api.ts）写，把学生端同名的
  // src/student/api.ts 一起跳过，于是 4 处裸路径全部漏报、判据还显示 PASS。
  // 修法是按**文件路径**排除。下面直接把 verify-contract.py 的 --self-test 跑起来，
  // 它自己会注入一个真文件（走同一条 rglob）、断言转红且红得具名、最后删掉。
  console.log('\n—— 负对照 6/11：裸 /api 路径扫描（判据见门禁② 的 ④）——');
  const contractSelfTest = spawnSync(PYTHON, [path.join(__dirname, 'verify-contract.py'), '--self-test'], {
    encoding: 'utf8',
    cwd: FRONTEND_DIR,
    env: { ...process.env, PYTHONUTF8: '1' },
  });
  const cstOut = `${contractSelfTest.stdout ?? ''}${contractSelfTest.stderr ?? ''}`;
  for (const line of cstOut.split('\n')) {
    if (line.trim()) console.log(`  ${line.trim()}`);
  }
  record(
    '裸 /api 判据负对照：注入 2 处裸路径后判据转红（verify-contract.py --self-test）',
    contractSelfTest.status === 0 && /负对照：注入 2 处裸路径后判据转红.*抓到 2 处/.test(cstOut),
    `exit=${contractSelfTest.status}`,
  );
  record(
    '裸 /api 判据具名：红的位置就是注入文件本身（没被别的问题带红）',
    /具名：红的位置就是注入文件.*命中 2\/2/.test(cstOut),
    '命中 2/2',
  );
  record(
    '裸 /api 判据清理：自检跑完不留坏文件（仓库回到 0 处）',
    /清理：注入文件已删除，仓库回到 0 处.*0 处/.test(cstOut) &&
      !existsSync(path.join(FRONTEND_DIR, 'src', '__contract_selftest_bare_paths.ts')),
    '无残留',
  );

  // ---------- 7/11：mock 学生端契约哨兵（判据见门禁⑨ 的学生端两条页面） ----------
  // 这一组守的是「**mock 自己**有没有照抄真实契约」，不是产品代码。
  //
  // 为什么需要它：本轮我把学生端三条接口补进 mock（`verify-server.mjs`），而门禁⑨
  // 从此依赖这些 mock 响应。如果我抄错了形状，门禁会**全绿**——正是最危险的那种
  // 静默失效。本轮真实发生过一次：`answer` 整键消失（我把已经剥掉答案的 wire 数据
  // 存去判分了），而当时所有断言都是绿的，是逐键比对脚本抓出来的。
  //
  // 所以这里做两件事：
  //   ① 断言 mock 响应的**关键不变量**（D74 不泄答案、D76 幂等、状态码 201/200）；
  //   ② 证明这些断言守着的代码路径**真的会被执行** —— 用 `MATHGEN_MOCK_TAMPER`
  //      故意破坏钩子，若服务照旧返回 200 就说明钩子没接上、哨兵是摆设。
  console.log('\n—— 负对照 7/11：mock 学生端契约哨兵（判据见门禁⑨ 的学生端两条页面）——');

  record(
    'mock 契约（D74）：GET /api/student/paper/{code} 带 paper_id/title/total/items，且 items **不含** answer/steps',
    mockContract.status === 200 &&
      typeof mockContract.paperKeys?.paperId === 'number' &&
      mockContract.paperItemKeys?.includes('seq') &&
      !mockContract.paperItemKeys?.includes('answer') &&
      !mockContract.paperItemKeys?.includes('steps'),
    `status=${mockContract.status} item keys=${(mockContract.paperItemKeys ?? []).join(',')}`,
  );
  record(
    'mock 契约（D75）：POST /api/student/practice 回传**实际使用的** seed，且 items 不含答案',
    mockContract.practiceStatus === 200 &&
      mockContract.practiceSeed === 424242 &&
      !mockContract.practiceItemKeys?.includes('answer'),
    `status=${mockContract.practiceStatus} seed=${mockContract.practiceSeed}（请求 424242）`,
  );
  record(
    'mock 契约（§5.2）：POST /api/attempts 交卷响应含 total/correct/items，且每题带 answer/student_answer/is_correct',
    mockContract.attemptStatus === 201 &&
      mockContract.attemptItemKeys?.includes('answer') &&
      mockContract.attemptItemKeys?.includes('student_answer') &&
      mockContract.attemptItemKeys?.includes('is_correct'),
    `status=${mockContract.attemptStatus} item keys=${(mockContract.attemptItemKeys ?? []).join(',')}`,
  );
  record(
    'mock 契约（D76）：同一 (student_id, attempt_uuid) 重放 → 200 且**同一个** attempt_id（不是 201 新记录）',
    mockContract.replayStatus === 200 &&
      mockContract.replayAttemptId === mockContract.firstAttemptId &&
      mockContract.firstAttemptId !== undefined,
    `首次 status=${mockContract.attemptStatus} id=${mockContract.firstAttemptId}；重放 status=${mockContract.replayStatus} id=${mockContract.replayAttemptId}`,
  );
  record(
    'mock 契约哨兵有效性：装上 MATHGEN_MOCK_TAMPER=student_paper 后该路径真的报错（证明钩子与断言都活着）',
    mockContract.tamperStatus === 500 && mockContract.tamperCode === 'MOCK_TAMPER',
    `status=${mockContract.tamperStatus} code=${mockContract.tamperCode}`,
  );

  // ---------- 8/11：门禁入口的参数解析（判据见 run-all-verifications.mjs） ----------
  // 守的是**门禁入口自己**的用法契约，不是产品代码。
  //
  // 为什么需要：`--list` 的卖点是「列出步骤但不执行」。如果用「输出里没有 PASS/FAIL
  // 行」来判，那是**弱判据** —— 步骤可能真跑了而输出被吞，或将来有人加了个静默
  // spawn，文本照样干净。所以实现上把解析放在**第一个副作用之前**并直接
  // process.exit，让「没执行」结构上成立；这里再从外部核对可观测后果。
  //
  // EXEC_ONLY 是**只会在真正执行时**才出现的字串（前置自检 banner / 源码夹逼 /
  // 汇总标题 / 通过语），它们全部在副作用之后才可能被打印。
  // 但「--list 输出里没有这些」单独还不够 —— 一个坏掉、什么都不打印的入口同样
  // 「干净」。所以下面还有一条反向断言：--list 必须**列满 12 步**（task-32 起第 ⑫ 步是
  // mock↔真实后端逐字段比对）。
  // 两条合起来才能区分「正确地只列不跑」与「坏掉了什么都不说」。
  console.log('\n—— 负对照 8/11：门禁入口的参数解析（判据见 run-all-verifications.mjs）——');

  const RUNNER = path.join(__dirname, 'run-all-verifications.mjs');
  const EXEC_ONLY = ['门禁脚本自检', '源码夹逼', '汇总', '全部验收通过'];

  const runRunner = (args) =>
    spawnSync(process.execPath, [RUNNER, ...args], {
      encoding: 'utf8',
      cwd: FRONTEND_DIR,
      timeout: 60_000,
    });
  const outOf = (p) => `${p.stdout ?? ''}${p.stderr ?? ''}`;

  const badFlag = runRunner(['--definitely-not-a-flag']);
  const badOut = outOf(badFlag);
  record(
    '未知参数 → 退出码 2（用法错误、门禁状态未知，与「跑了但没过」的退出码 1 区分）',
    badFlag.status === 2,
    `exit=${badFlag.status}`,
  );
  record(
    '未知参数 → 点名那个参数，且明说「未执行任何门禁」',
    /未知参数，未执行任何门禁：.*--definitely-not-a-flag/.test(badOut),
    '点名 + 声明未执行',
  );

  // 打错的 --help 变体：不能因为「看着像 help」就放行成 exit 0。
  const typoFlag = runRunner(['--helpp']);
  record(
    '打错的 --help 变体（--helpp）→ 同样 exit 2，不放行成 0',
    typoFlag.status === 2 && /未知参数/.test(outOf(typoFlag)),
    `exit=${typoFlag.status}`,
  );

  const helpRun = runRunner(['--help']);
  const helpOut = outOf(helpRun);
  record(
    '--help → exit 0，含用法与步骤清单，且无任何执行期标记',
    helpRun.status === 0 && /用法：/.test(helpOut) && EXEC_ONLY.every((m) => !helpOut.includes(m)),
    `exit=${helpRun.status} 执行期标记=${EXEC_ONLY.filter((m) => helpOut.includes(m)).join('|') || '无'}`,
  );

  const listRun = runRunner(['--list']);
  const listOut = outOf(listRun);
  const listedSteps = (listOut.match(/^\s*\d+\. /gm) ?? []).length;
  record(
    '--list → exit 0，且执行期标记一个都没有（= 解析在任何副作用之前就退出了）',
    listRun.status === 0 && EXEC_ONLY.every((m) => !listOut.includes(m)),
    `exit=${listRun.status} 执行期标记=${EXEC_ONLY.filter((m) => listOut.includes(m)).join('|') || '无'}`,
  );
  record(
    '--list → 12 步全列（与上一条互为对照：不是「什么都没干」才干净；第 ⑫ 步即 task-32 接进来的 parity）',
    listedSteps === 12,
    `列出 ${listedSteps} 步`,
  );

  // V9.2：负对照必须证明它**制造了失败条件**。若这个入口无条件 exit 2，上面那条
  // 「未知参数 → 2」照样是绿的，却什么都没证明。所以把同一入口的合法调用并列出来：
  // 0（--list）vs 2（未知参数）—— 差异只可能来自参数本身。
  record(
    'V9.2 失败条件真实：同一入口合法调用 exit 0、未知参数 exit 2（2 由参数引起，不是常量）',
    listRun.status === 0 && badFlag.status === 2 && badFlag.status !== listRun.status,
    `--list=${listRun.status} vs 未知参数=${badFlag.status}`,
  );

  // V11：上面对「执行期标记」的断言，只有在**该谓词真的能报红**时才有判别力。
  // 用一个**合成的全量运行转录**喂同一个谓词，断言它必须命中 —— 不跑真全量门禁
  // （M4 真机采集期间不该跑），判别力照样被证明。
  const syntheticFullRun = [
    '门禁脚本自检：frontend/scripts/*.mjs 逐个 node --check（含负/正对照）',
    '源码夹逼（运行前）：63 个文件，聚合 deadbeef',
    '汇总',
    '全部验收通过。产物：',
  ].join('\n');
  record(
    'V11 判别力：「无执行期标记」谓词喂合成全量转录 → 必报红（不是恒真）',
    EXEC_ONLY.some((m) => syntheticFullRun.includes(m)) &&
      !EXEC_ONLY.every((m) => !syntheticFullRun.includes(m)),
    `合成转录命中 ${EXEC_ONLY.filter((m) => syntheticFullRun.includes(m)).length}/${EXEC_ONLY.length} 个标记`,
  );

  // ---------- 9/11：task-32 —— 判据「不进默认门禁就不存在」（判据见 compare-mock-and-real.mjs） ----------
  // 守的是 task-32(a) 新增的**第 ⑫ 步**：把 mock↔真实后端的逐字段比对接进默认门禁。
  //
  // 为什么需要：task-29 交付的比对本身很硬（它的自检 C 段证明了抓得住「少一个键」和
  // 「两边一起错」），但它此后**只在人记得手动跑时才执行** ——
  // 而一份不进默认门禁的判据等于没有判据（D69 / V9.1）。第 ⑫ 步就是补这个洞。
  //
  // 但**接线本身**也必须被证明有判别力（V11）：如果第 ⑫ 步写成「前置缺失就跳过」
  // 或「打印个 PASS 了事」，门禁照样全绿，而判据其实一次都没真正比过。
  // 所以这里对**同一个真实脚本**喂两种世界，断言它给出**相反**的结论：
  //   态 A 正常输入 → exit 0（绿）      态 B 前置缺失 → exit ≠ 0 且点名原因、且**不含 PASS**（红）
  // 两态的原始输出都打出来（V9.1：报告会过期，脚本不会 —— 别人要能在仓库里 grep 到它）。
  console.log('\n—— 负对照 9/11：第 ⑫ 步「前置缺失时红、绝不静默绿」（判据见 compare-mock-and-real.mjs）——');

  const PARITY = path.join(__dirname, 'compare-mock-and-real.mjs');
  const runParity = (env) =>
    spawnSync(process.execPath, [PARITY], {
      encoding: 'utf8',
      cwd: FRONTEND_DIR,
      timeout: 120_000,
      env: { ...process.env, ...env },
    });

  // 态 A：正常输入 —— 这一半世界必须绿。
  const parityOkWorld = runParity({});
  const parityOkOut = outOf(parityOkWorld);
  record(
    '态 A 正常输入 → exit 0，且输出确实做了逐条比对（不是空跑）',
    parityOkWorld.status === 0 &&
      /逐条一致/.test(parityOkOut) &&
      /^\s*\[PASS\]/m.test(parityOkOut),
    `exit=${parityOkWorld.status} PASS行=${(parityOkOut.match(/^\s*\[PASS\]/gm) ?? []).length}`,
  );

  // 态 B：坏输入 —— 后端 venv 的 python 不存在（前置缺失）。必须红，且说人话。
  // 用 MATHGEN_PARITY_PYTHON 注入一个不存在的路径，而不是去动真的 venv：
  // 这个注入**只可能让结论变红**，不会污染环境，也不会让别人误以为 venv 坏了。
  const MISSING_PYTHON = 'C:\\__mathgen_no_such_venv__\\python.exe';
  const parityMissingWorld = runParity({ MATHGEN_PARITY_PYTHON: MISSING_PYTHON });
  const parityMissingOut = outOf(parityMissingWorld);
  record(
    '态 B 前置缺失 → exit ≠ 0（红），且点名缺失的那个路径（人话原因）',
    parityMissingWorld.status !== 0 && parityMissingOut.includes(MISSING_PYTHON),
    `exit=${parityMissingWorld.status}`,
  );
  // 「不许打印 PASS」是 Lead 在 task-32 里明确点名的纪律：一个会静默变绿的步骤比没有这一步更坏。
  record(
    '态 B 前置缺失 → 输出里**没有**任何 PASS 行、也没有「逐条一致」（不许静默绿）',
    !/^\s*\[PASS\]/m.test(parityMissingOut) && !/逐条一致/.test(parityMissingOut),
    `含PASS=${/^\s*\[PASS\]/m.test(parityMissingOut)} 含「逐条一致」=${/逐条一致/.test(parityMissingOut)}`,
  );

  // V9.2：负对照必须证明「它制造了失败条件」。若这个脚本无论如何都 exit ≠ 0，
  // 上面那条「前置缺失 → 红」照样是绿的，却什么都没证明。所以把同一脚本的合法调用并列：
  // 0（正常）vs ≠0（前置缺失）—— 差异只可能来自注入。
  record(
    'V9.2 失败条件真实：同一脚本两种世界结论相反（0 vs ≠0），差异只来自注入',
    parityOkWorld.status === 0 && parityMissingWorld.status !== 0,
    `正常=${parityOkWorld.status} 前置缺失=${parityMissingWorld.status}`,
  );

  // V11 判别力：判据自己要能报红。用故障注入逼出「覆盖面不足」那条分支 ——
  // 它守的是另一类假绿：真实侧写出一个合法但空的 `{}`，于是 0 行相比、0 差异，
  // 然后打印「逐条一致。退出码 0」—— 一个**什么都没比过**的绿。
  const parityFaultWorld = runParity({ MATHGEN_PARITY_FAULT: 'coverage' });
  const parityFaultOut = outOf(parityFaultWorld);
  record(
    'V11 判别力：覆盖面故障注入 → exit ≠ 0 且点名「覆盖面不足」（不是只看 failed === 0）',
    parityFaultWorld.status !== 0 &&
      /覆盖面不足/.test(parityFaultOut) &&
      !/逐条一致/.test(parityFaultOut),
    `exit=${parityFaultWorld.status}`,
  );

  console.log('\n  ---- 态 A（正常输入）原始输出 ----');
  for (const line of parityOkOut.split('\n')) if (line.trim()) console.log(`  | ${line}`);
  console.log('\n  ---- 态 B（前置缺失）原始输出 ----');
  for (const line of parityMissingOut.split('\n')) if (line.trim()) console.log(`  | ${line}`);
  console.log('\n  ---- 态 C（覆盖面故障注入）原始输出末尾 ----');
  for (const line of parityFaultOut.split('\n').filter((l) => l.trim()).slice(-4)) {
    console.log(`  | ${line}`);
  }

  // ---------- 10/11：task-35 卷库归档 / 恢复 / 删除（判据见契约 D78 与门禁②/⑨） ----------
  //
  // 为什么需要：task-35 给卷库加了「归档 / 恢复 / 删除」，有实质风险的只有删除。
  // 用户裁决原话是「有成绩的可以归档，类似于软删除」——于是界面上「有成绩的卷」必须
  // **事先禁用**删除并写明人话理由，而不是等老师点下去才吃一个 409（D65：报错好过静默错）。
  //
  // 界面那条禁用条件读的是 `PaperSummary.has_attempts`，所以真正的判据在**数据层**：
  // `has_attempts` 必须能预测 DELETE 的真实结局。判据错了，界面就会在一个错误的世界里
  // 全绿（D69 那一族），或者把一个能删的卷禁掉。
  //
  // 于是这里做三件同源的事：
  //   ① 同一个谓词分别喂**真实响应**与**合成响应**，证明它对两种世界给出相反结论（V11）；
  //   ② 用破坏钩子把 `has_attempts` 从响应里抹掉，证明上面那个绿**真的在看这个字段**（V9.2）；
  //   ③ 正面证明「有成绩的卷能归档」（用户要的正是这条出路）。
  console.log('\n—— 负对照 10/11：卷库归档 / 恢复 / 删除（判据见契约 D78）——');

  // 谓词**只定义一次**，真实响应与合成响应共用 —— 另抄一份等于什么都没证明。
  const defaultViewHidesArchived = (payload) =>
    Array.isArray(payload?.papers) &&
    payload.papers.length > 0 && // 空世界必须红：「没得比」不等于「比过且一致」
    payload.papers.every((p) => p.archived_at === null);
  const hasAttemptsPredictsDelete = (hasAttempts, deleteStatus) =>
    hasAttempts === true ? deleteStatus === 409 : deleteStatus === 204;

  const spawnMock = (env) => {
    const child = spawn(NODE_EXE, [path.join(__dirname, 'verify-server.mjs')], {
      cwd: FRONTEND_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, MATHGEN_VERIFY_MODE: 'online', ...env },
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
        reject(new Error(`mock 服务没就绪。stderr:\n${stderr}`));
      }, 15_000);
      child.stdout.on('data', (chunk) => {
        buffer += chunk;
        const ports = buffer.match(/VERIFY_PORTS static=(\d+) api=(\d+)/);
        if (ports && buffer.includes('VERIFY_SERVER_READY')) {
          clearTimeout(timer);
          // 端口由 listenWithRetry 决定，**必须从 stdout 读**，不能写死。
          //
          // 这里刻意**直连 api 端口**，而不是走静态端口上的 `/api` 代理 —— 2026-10-15 实测踩到：
          // `verify-server.mjs` 的静态服务把 `/api/*` 转发到**请求的** api 端口，而不是重试后
          // **实际**绑上的那个。于是当默认端口被别的实例占用、listenWithRetry 把本实例挪到
          // 4175/8125 时，走静态端口的请求会被**静默转发到占用 8123 的另一个进程**上。
          // 症状极具误导性：破坏钩子明明生效（直连 8125 看到 0/40 行有 `has_attempts`），
          // 经 4175 却看到 40/40 —— 看起来像「钩子没接上」，实际是问错了进程。
          // 直连 api 端口即规避整个代理，也让「我测的就是我 spawn 的那个实例」结构上成立。
          const apiPort = Number(ports[2]);
          resolve({ child, base: `http://127.0.0.1:${apiPort > 0 ? apiPort : ports[1]}` });
        }
      });
      child.on('exit', (code) => {
        if (code !== null) {
          clearTimeout(timer);
          reject(new Error(`mock 服务提前退出（code=${code}）。stderr:\n${stderr}`));
        }
      });
    });
  };

  const call = async (base, method, apiPath) => {
    const res = await fetch(base + apiPath, { method });
    const text = await res.text();
    return { status: res.status, text, body: text ? JSON.parse(text) : null };
  };

  const libMock = await spawnMock({});
  const libBase = libMock.base;

  const listDefault = (await call(libBase, 'GET', '/api/papers?limit=100')).body;
  const listOnly = (await call(libBase, 'GET', '/api/papers?limit=100&archived=only')).body;
  const listInclude = (await call(libBase, 'GET', '/api/papers?limit=100&archived=include')).body;

  const leaks = (listDefault?.papers ?? []).filter((p) => p.archived_at !== null).length;
  record(
    '默认档（archived=exclude）不返回已归档行，且返回的不是空表',
    defaultViewHidesArchived(listDefault),
    `total=${listDefault?.total} 已归档泄漏=${leaks}`,
  );
  record(
    '三档互为划分：include 总数 = exclude + only，且 only 档每行都有 archived_at',
    listInclude?.total === listDefault?.total + listOnly?.total &&
      (listOnly?.papers?.length ?? 0) > 0 &&
      listOnly.papers.every((p) => p.archived_at !== null),
    `exclude=${listDefault?.total} only=${listOnly?.total} include=${listInclude?.total}`,
  );
  record(
    'V11 判别力：同一谓词喂「默认档混进一行已归档」的合成响应 → 必为假（判据能红）',
    defaultViewHidesArchived({
      papers: [
        { id: 1, archived_at: null },
        { id: 2, archived_at: '2026-10-15T12:00:00+08:00' },
      ],
    }) === false &&
      defaultViewHidesArchived({ papers: [{ id: 1, archived_at: null }] }) === true &&
      defaultViewHidesArchived({ papers: [] }) === false,
    '坏世界=false 好世界=true 空世界=false',
  );

  // 夹具自检：若响应里根本没有 has_attempts=true 的行，下面那条判据就是**空的**（V9.1）。
  const withAttempts = (listInclude?.papers ?? []).filter((p) => p.has_attempts === true);
  const withoutAttempts = (listInclude?.papers ?? [])
    .filter((p) => p.has_attempts !== true)
    .slice(0, 2);
  record(
    '夹具存在：至少一行 has_attempts=true、两行 false（否则下一条判据什么都没测）',
    withAttempts.length >= 1 && withoutAttempts.length >= 2,
    `有成绩=${withAttempts.length} 无成绩=${withoutAttempts.length}`,
  );

  const del = async (base, paper) => {
    const r = await call(base, 'DELETE', `/api/papers/${paper.id}`);
    return {
      id: paper.id,
      hasAttempts: paper.has_attempts,
      status: r.status,
      empty: r.text === '',
      code: r.body?.error?.code ?? null,
    };
  };
  const goodDeletes = [];
  for (const p of [...withAttempts, ...withoutAttempts]) goodDeletes.push(await del(libBase, p));
  record(
    'has_attempts 能预测 DELETE 的真实结局（有成绩→409 且点名 PAPER_HAS_ATTEMPTS；无成绩→204 真空体）',
    goodDeletes.every((r) => hasAttemptsPredictsDelete(r.hasAttempts, r.status)) &&
      goodDeletes.some((r) => r.status === 409 && r.code === 'PAPER_HAS_ATTEMPTS') &&
      goodDeletes.some((r) => r.status === 204 && r.empty),
    goodDeletes
      .map((r) => `${r.id}:has=${r.hasAttempts}→${r.status}${r.code ? `(${r.code})` : ''}`)
      .join(' '),
  );

  // 正面：用户要的那条出路 —— 有成绩的卷可以归档（软删除），且归档后立刻离开默认档。
  const victim = withAttempts[0];
  const arch = await call(libBase, 'POST', `/api/papers/${victim.id}/archive`);
  const afterArchDefault = (await call(libBase, 'GET', '/api/papers?limit=100')).body;
  const afterArchOnly = (await call(libBase, 'GET', '/api/papers?limit=100&archived=only')).body;
  const unarch = await call(libBase, 'POST', `/api/papers/${victim.id}/unarchive`);
  const afterUnarchDefault = (await call(libBase, 'GET', '/api/papers?limit=100')).body;
  record(
    '有成绩的卷可以归档（用户裁决的出路）：归档后立刻移出默认档、出现在 only 档',
    arch.status === 200 &&
      typeof arch.body?.archived_at === 'string' &&
      !afterArchDefault.papers.some((p) => p.id === victim.id) &&
      afterArchOnly.papers.some((p) => p.id === victim.id),
    `archive=${arch.status} 默认档含它=${afterArchDefault.papers.some((p) => p.id === victim.id)} only档含它=${afterArchOnly.papers.some((p) => p.id === victim.id)}`,
  );
  record(
    '恢复幂等：unarchive 后 archived_at 归零，卷立刻回到默认档',
    unarch.status === 200 &&
      unarch.body?.archived_at === null &&
      afterUnarchDefault.papers.some((p) => p.id === victim.id),
    `unarchive=${unarch.status} archived_at=${JSON.stringify(unarch.body?.archived_at)}`,
  );

  // V9.2：证明上面的绿**真的在看 has_attempts**。另起一个装了破坏钩子的实例，
  // 把 `papers[].has_attempts` 整键抹掉 —— 若判据仍绿，说明它根本没读这个字段。
  // 注意钩子只能**删键**（不能改成 false），所以坏世界里该字段是 undefined，
  // 而这条卷实际仍会被 409 拒绝 → 预测与现实相反 → 判据必须红。
  const libTamper = await spawnMock({ MATHGEN_MOCK_TAMPER_DELETE: 'papers[].has_attempts' });
  const tPapers = (await call(libTamper.base, 'GET', '/api/papers?limit=100&archived=include')).body
    .papers;
  const tVictim = tPapers.find((p) => p.id === victim.id);
  const tDelete = await call(libTamper.base, 'DELETE', `/api/papers/${victim.id}`);
  const tKeyGone =
    tVictim !== undefined && !Object.prototype.hasOwnProperty.call(tVictim, 'has_attempts');
  record(
    'V9.2 破坏钩子真实生效：抹掉 papers[].has_attempts 后，同一判据在同一夹具上变红',
    tKeyGone &&
      tDelete.status === 409 &&
      hasAttemptsPredictsDelete(tVictim.has_attempts, tDelete.status) === false,
    `键被抹掉=${tKeyGone} 实际 DELETE=${tDelete.status} 谓词=${hasAttemptsPredictsDelete(tVictim?.has_attempts, tDelete.status)}`,
  );
  record(
    'V11 判别力：合成「预测与现实相反」的两种世界 → 谓词给出相反结论',
    hasAttemptsPredictsDelete(true, 409) === true &&
      hasAttemptsPredictsDelete(true, 204) === false &&
      hasAttemptsPredictsDelete(undefined, 409) === false,
    '预测=现实 → true；预测与现实相反 → false；键缺失 → false',
  );

  libTamper.child.kill();
  libMock.child.kill();

  // —— 负对照 11/11：门禁入口的「陈旧产物」前置判据（判据见 scripts/dist-freshness.mjs）——
  // 为什么要有这一条（V13 同族）：门禁第 ③④⑤⑦⑨ 步加载 frontend/dist/**，而门禁**不构建**。
  // 改了 src 却忘了 build，那四个浏览器门禁测的是旧 bundle —— 会给出与源码无关的**绿**。
  // 这里用**合成目录树**构造两个世界（不碰真实 frontend/dist，也不依赖真实构建）：
  //   A 世界：dist 比源码新 → 判据绿
  //   B 世界：dist 比源码旧 → 判据红，且必须**点名**那个更晚的源文件
  //   （V9.2：B 世界必须自证它真的制造了失败条件，而不只是"我们相信它会红"）
  console.log(
    '\n—— 负对照 11/11：门禁入口的「陈旧产物」前置判据（判据见 scripts/dist-freshness.mjs）——',
  );
  {
    const WORLDS = path.join(TMP_DIR, 'dist-freshness-worlds');
    const FRESHNESS_CLI = path.join(__dirname, 'dist-freshness.mjs');
    const T0 = Date.parse('2026-10-15T22:00:00+08:00');
    const at = (seconds) => new Date(T0 + seconds * 1000);
    rmSync(WORLDS, { recursive: true, force: true });
    const makeTree = (name) => {
      const root = path.join(WORLDS, name);
      mkdirSync(path.join(root, 'src', 'teacher'), { recursive: true });
      mkdirSync(path.join(root, 'dist', 'assets'), { recursive: true });
      writeFileSync(path.join(root, 'src', 'teacher', 'Page.tsx'), 'export const x = 1;\n');
      writeFileSync(path.join(root, 'index.html'), '<div id="root"></div>\n');
      writeFileSync(path.join(root, 'dist', 'index.html'), '<html></html>\n');
      writeFileSync(path.join(root, 'dist', 'assets', 'main-abc.js'), 'console.log(1);\n');
      return root;
    };
    const stamp = (root, rel, date) => {
      const target = path.join(root, ...rel.split('/'));
      utimesSync(target, date, date);
    };

    const freshWorld = makeTree('fresh');
    stamp(freshWorld, 'src/teacher/Page.tsx', at(0));
    stamp(freshWorld, 'index.html', at(0));
    stamp(freshWorld, 'dist/index.html', at(10));
    stamp(freshWorld, 'dist/assets/main-abc.js', at(10));

    const staleWorld = makeTree('stale');
    stamp(staleWorld, 'index.html', at(0));
    stamp(staleWorld, 'dist/index.html', at(0));
    stamp(staleWorld, 'dist/assets/main-abc.js', at(0));
    stamp(staleWorld, 'src/teacher/Page.tsx', at(30));

    const fresh = checkDistFreshness({ frontendDir: freshWorld });
    const stale = checkDistFreshness({ frontendDir: staleWorld });

    record(
      'A 世界（dist 比源码新）→ 判据绿，并报出输入/产物文件数',
      fresh.ok === true && fresh.counts.inputs >= 2 && fresh.counts.artifacts === 2,
      `ok=${fresh.ok} reason=${fresh.reason} 输入=${fresh.counts.inputs} 产物=${fresh.counts.artifacts}`,
    );
    record(
      'B 世界（dist 比源码旧）→ 判据红（V9.2：证明 A 的绿不是恒真，这个输入真的能把它弄红）',
      stale.ok === false && stale.reason === 'stale',
      `ok=${stale.ok} reason=${stale.reason}`,
    );
    record(
      'B 世界红的时候**点名**了那个更晚的源文件（不是只说"不一致"）',
      stale.offenders.length === 1 && stale.offenders[0].rel === 'src/teacher/Page.tsx',
      `offenders=${JSON.stringify(stale.offenders.map((f) => f.rel))}`,
    );
    record(
      'V11 判别力：A/B 两个世界得到**相反**结论（同一函数、同一份代码）',
      fresh.ok !== stale.ok,
      `A.ok=${fresh.ok} B.ok=${stale.ok}`,
    );
    const staleText = formatFreshnessResult(stale);
    record(
      '报错文案可照做：含晚的源文件名 + 含 pnpm --dir frontend build + 写明"比源码旧"',
      staleText.includes('src/teacher/Page.tsx') &&
        staleText.includes('pnpm --dir frontend build') &&
        staleText.includes('frontend/dist 比源码旧'),
      staleText.split('\n')[0],
    );

    // 同拍口径（报告里必须写明的确定性口径）：mtime 相等判 PASS —— 判据是"dist 不早于源码"。
    const sameWorld = makeTree('same');
    for (const rel of [
      'src/teacher/Page.tsx',
      'index.html',
      'dist/index.html',
      'dist/assets/main-abc.js',
    ]) {
      stamp(sameWorld, rel, at(5));
    }
    const same = checkDistFreshness({ frontendDir: sameWorld });
    record(
      '同拍口径确定：dist 与源码 mtime 相等 → 判 PASS（「不早于」而非「严格晚于」，故粗粒度文件系统上不会假红）',
      same.ok === true,
      `ok=${same.ok} reason=${same.reason}`,
    );

    // 输入集合不含 scripts/：改门禁脚本不该要求重建 dist（否则这条红会被训练成噪声，最终被忽略）。
    const scriptsWorld = makeTree('scripts-new');
    stamp(scriptsWorld, 'src/teacher/Page.tsx', at(0));
    stamp(scriptsWorld, 'index.html', at(0));
    stamp(scriptsWorld, 'dist/index.html', at(10));
    stamp(scriptsWorld, 'dist/assets/main-abc.js', at(10));
    mkdirSync(path.join(scriptsWorld, 'scripts'), { recursive: true });
    writeFileSync(path.join(scriptsWorld, 'scripts', 'gate.mjs'), '// x\n');
    stamp(scriptsWorld, 'scripts/gate.mjs', at(999));
    const scriptsNew = checkDistFreshness({ frontendDir: scriptsWorld });
    record(
      '构建输入集合**不含** scripts/：只有门禁脚本变新时仍判绿（改了门禁脚本不该被要求重建 dist）',
      scriptsNew.ok === true,
      `ok=${scriptsNew.ok} reason=${scriptsNew.reason}`,
    );

    // "没得比"不许当绿（V11）：输入集合为空必须红。
    const emptyWorld = path.join(WORLDS, 'empty');
    mkdirSync(path.join(emptyWorld, 'dist'), { recursive: true });
    writeFileSync(path.join(emptyWorld, 'dist', 'index.html'), 'x\n');
    const empty = checkDistFreshness({ frontendDir: emptyWorld });
    record(
      '输入集合为空 → 判红（「没得比」不等于「比过且一致」，不许静默变绿）',
      empty.ok === false && empty.reason === 'no-inputs',
      `ok=${empty.ok} reason=${empty.reason}`,
    );

    // 门禁读的是**退出码**，所以退出码语义本身也要被证明：陈旧 → 2；新鲜 → 0。
    const cliStale = spawnSync(NODE_EXE, [FRESHNESS_CLI, staleWorld], {
      encoding: 'utf8',
      timeout: 30_000,
    });
    const cliFresh = spawnSync(NODE_EXE, [FRESHNESS_CLI, freshWorld], {
      encoding: 'utf8',
      timeout: 30_000,
    });
    record(
      'CLI 退出码语义与门禁一致：陈旧 → 2（非零），新鲜 → 0',
      cliStale.status === 2 && cliFresh.status === 0,
      `陈旧 exit=${cliStale.status} 新鲜 exit=${cliFresh.status}`,
    );
    record(
      'CLI 在陈旧世界的输出里点名了源文件（端到端可照做，不只是函数返回值对）',
      `${cliStale.stdout}${cliStale.stderr}`.includes('src/teacher/Page.tsx'),
      `${cliStale.stdout}${cliStale.stderr}`.split('\n')[0],
    );

    // 真实世界阳性：门禁此刻正在跑，说明前置判据对真实 frontend/ 判的就是"新鲜"。
    const real = checkDistFreshness({ frontendDir: FRONTEND_DIR });
    record(
      '真实 frontend/ 此刻判绿（与门禁前置判据同一函数、同一目录；这条红 = dist 陈旧，先 pnpm --dir frontend build）',
      real.ok === true,
      `reason=${real.reason} 输入=${real.counts.inputs} 产物=${real.counts.artifacts} 最新输入=${real.srcNewest?.rel}`,
    );

    rmSync(WORLDS, { recursive: true, force: true });
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n结果：${results.length - failed.length}/${results.length} 通过`);
  if (failed.length > 0) {
    for (const f of failed) console.log(`  - ${f.name} (${f.detail ?? ''})`);
    console.log('\n负对照失败说明**判据可能抓不住东西**，或**注入没生效** —— 两者都必须查清。');
    process.exit(1);
  }
  console.log('负对照套件通过：每条判据都能被一个具体输入弄红，且负对照本身确实改变了关键量。');
}

await main();
