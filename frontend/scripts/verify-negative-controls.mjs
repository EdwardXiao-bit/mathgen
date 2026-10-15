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

# ---- A10：无开发者文本（与门禁⑩ verify-ui-voice.mjs 同一套检测）----
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
        // 与门禁⑩ 逐字一致：按**编号族形状**覆盖，而不是枚举我想到的那几个。
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

# ---- A11：灰底只用于画布（与门禁⑩同一套判据）----
BG_PROBE = """() => {
    const cs = (el) => {
        if (!el) return null;
        const c = getComputedStyle(el);
        return {bg: c.backgroundColor, bgImage: c.backgroundImage};
    };
    return {body: cs(document.body), root: cs(document.querySelector('.config-page'))};
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

result = {"a9": {}, "focus": {}, "a10": {}, "a11": {}, "console_errors": []}
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
    # 与门禁⑩同一套检测逻辑（照搬过来，确保验的就是同一条判据）。
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

  const fmt = (m) =>
    m
      ? `宽=${m.sheetWidth.toFixed(2)} padding=${m.paddingLeft.toFixed(2)} ratio=${m.ratio.toFixed(4)} ` +
        `scaleX=${m.scaleX.toFixed(5)} scaleY=${m.scaleY.toFixed(5)}`
      : '(量不到)';

  const a9 = result.a9 ?? {};
  console.log('\n—— 负对照 1/2：A9 纸内几何（判据见门禁⑧ verify-preview-scale.mjs）——');
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

  console.log('\n—— 负对照 2/2：卷面焦点环（判据见门禁⑦ 的 A3）——');
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
  console.log('\n—— 负对照 3/4：无开发者文本（判据见门禁⑩ verify-ui-voice.mjs）——');
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
  console.log('\n—— 负对照 4/4：灰底只用于画布（判据见门禁⑩）——');
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
