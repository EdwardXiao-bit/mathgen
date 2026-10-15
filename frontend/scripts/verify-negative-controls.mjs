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

result = {"a9": {}, "focus": {}, "console_errors": []}
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
