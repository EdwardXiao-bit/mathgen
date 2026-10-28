/**
 * 门禁 ⑧：A9 —— 预览必须是**打印几何的等比缩小**，不是「把 A4 横向压扁」（D68）。
 *
 * 为什么单开一条：A1–A8 全绿时这个缺陷仍然存在 —— 因为 A1 只管颜色、A2 只管组件状态、
 * A4 只管纸面**容器**比例、A7 只管浮动层几何、A8 只管不溢出。**没有一条量到「纸里面」**。
 * 所以这条门禁的核心是：**进到 iframe 文档里去量**。
 *
 * 缺陷形态（Lead 实测确认，D68）：
 *   后端已冻结的屏幕 CSS 是 `.sheet { width: 210mm; max-width: 100%; padding: var(--page-margin) }`。
 *   若 iframe 的内容视口只有容器宽（459px），`max-width:100%` 就把这张「210mm 的纸」**横向钳制**到
 *   459px，而 `padding`（2cm=75.59px）与 `font-size`（14pt）仍是绝对长度 →
 *   内容区只剩 459−2×75.59≈308px，4 列每列 77px，`243 × 12 ____` 需要约 118px → **必然异常换行**。
 *   屏幕上乱、打印却正常，正是 C3 要防的「预览 ≠ 打印」。
 *
 * 本门禁逐条对应 task-17 的验收标准 2–5，外加两种视口（标准 7）：
 *   A9-1  iframe 内 `.sheet` 计算宽度 = 793.7px ± 2px（210mm @96dpi），**不是**被压窄的值
 *   A9-2  `padding-left / .sheet 宽度` ≈ 0.0952（±0.002）—— 错误态是 0.165
 *   A9-3  `scaleX == scaleY`（等比，不是压扁）
 *   A9-4  题面不再异常换行（给可判定证据：题块高度 = 单行高度，且列宽 ≥ 内容所需）
 *   A9-7  1440×900 与 1280×900 下不横向溢出、预览区就是灰底画布
 *
 * 用法：node scripts/verify-preview-scale.mjs
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

/** A4 @96dpi（与 src/teacher/SheetFrame.tsx 的常量同源；这里独立算一遍，避免「自证」） */
const A4_W = (210 / 25.4) * 96; // 793.698…
const A4_H = (297 / 25.4) * 96; // 1122.52…

const checks = [];
function check(name, condition, detail) {
  checks.push({ name, ok: Boolean(condition), detail });
  console.log(`  [${condition ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
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
        resolve({ child, staticPort: Number(ports[1]), apiPort: Number(ports[2]) });
      } else if (buffer.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve({ child, staticPort: BASE_STATIC_PORT, apiPort: BASE_API_PORT });
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
    return sorted(candidates)[-1] if candidates else None

def emit(obj):
    print("##RESULT##" + json.dumps(obj))

def measure(page):
    """在**iframe 文档内部**量纸面几何 —— 这是 A9 唯一能抓到缺陷的地方。"""
    return page.evaluate("""() => {
        const f = document.getElementById('sheet-frame');
        const d = f && f.contentDocument;
        const sheet = d && d.querySelector('.sheet');
        const grid = d && (d.querySelector('.q-row-grid') || d.querySelector('.q-row'));
        const items = d ? Array.from(d.querySelectorAll('.q-item')) : [];
        const heights = items.map(e => +e.getBoundingClientRect().height.toFixed(2));
        const uniqueHeights = [...new Set(heights)];
        const cs = sheet ? getComputedStyle(sheet) : null;
        const fRect = f ? f.getBoundingClientRect() : null;

        // 缩放系数：**实测**从渲染结果反推，而不是读我们写进去的那个内联值
        // （读内联值等于自证 —— 只证明「我们写了 k」，不证明「浏览器按 k 渲染了」）
        const scaleX = fRect && f.offsetWidth ? +(fRect.width / f.offsetWidth).toFixed(5) : null;
        const scaleY = fRect && f.offsetHeight ? +(fRect.height / f.offsetHeight).toFixed(5) : null;

        // 列宽 vs 单题所需宽度：列宽不足 → 必然换行
        const colWidths = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ') : [];
        const colW = colWidths.length ? parseFloat(colWidths[0]) : null;

        return {
            docScrollW: d ? d.documentElement.scrollWidth : null,
            sheetComputedWidth: cs ? cs.width : null,
            sheetComputedHeight: cs ? cs.height : null,
            sheetPaddingLeft: cs ? cs.paddingLeft : null,
            sheetPaddingTop: cs ? cs.paddingTop : null,
            sheetFontSize: cs ? cs.fontSize : null,
            paddingOverWidth: cs ? +(parseFloat(cs.paddingLeft) / parseFloat(cs.width)).toFixed(4) : null,
            iframeOffsetW: f ? f.offsetWidth : null,
            iframeOffsetH: f ? f.offsetHeight : null,
            iframeRectW: fRect ? +fRect.width.toFixed(2) : null,
            iframeRectH: fRect ? +fRect.height.toFixed(2) : null,
            scaleX, scaleY,
            scaleInline: f && f.style.transform ? f.style.transform : null,
            colCount: colWidths.length,
            colW: colW ? +colW.toFixed(2) : null,
            itemCount: items.length,
            uniqueItemHeights: uniqueHeights,
            // 「一行的题块高度」= 出现次数最多的那个高度（出现 >1 行时会出现更大的值）
            singleLineHeight: uniqueHeights.length ? Math.min(...uniqueHeights) : null,
            allItemsSingleLine: heights.length > 0 && uniqueHeights.length === 1,
            // 预览区结构（用户 2026-10-28：灰底画布本身就是预览区，不要「白纸小窗口」）。
            // 不再量 .mg-paper-frame 的 A4 比例 —— 它现在只是透明的弹性区域。
            preview: (() => {
                const stage = document.querySelector('.mg-paper-stage');
                const col = document.querySelector('.mg-canvas-column');
                const frame = document.querySelector('.mg-paper-frame');
                if (!stage || !col) return null;
                const s = stage.getBoundingClientRect();
                const c = col.getBoundingClientRect();
                return {
                    stageRight: +s.right.toFixed(1),
                    colRight: +c.right.toFixed(1),
                    stageW: Math.round(s.width),
                    stageH: Math.round(s.height),
                    stageBg: getComputedStyle(stage).backgroundColor,
                    frameBg: frame ? getComputedStyle(frame).backgroundColor : null,
                };
            })(),
            // 卷面文档自己绝不能滚动：里面的原生滚动条会贴在纸面右缘，吃掉 10-17px 纸宽，
            // 而且它长得完全不像设计的一部分（用户 2026-10-28 报过一张截图）。
            iframeScroll: (() => {
                const frame = document.querySelector('#sheet-frame');
                const doc = frame && frame.contentDocument;
                if (!frame || !doc || !doc.body) return null;
                const view = doc.defaultView;
                const overflow = (el) => (el ? view.getComputedStyle(el).overflowY + '/' + view.getComputedStyle(el).overflowX : null);
                return {
                    scrollbarH: frame.offsetHeight - frame.clientHeight,
                    scrollbarW: frame.offsetWidth - frame.clientWidth,
                    html: overflow(doc.documentElement),
                    body: overflow(doc.body),
                };
            })(),
            // 每一页都必须正好是一张 A4（用户 2026-10-28：「最后不满也应该显示到页末」）。
            // 页高靠给灰缝/尾块补白实现，所以这里量的是**最终几何**而不是代码意图。
            pages: (() => {
                const sheetEl = d && d.querySelector('.sheet');
                if (!sheetEl) return null;
                const rect = sheetEl.getBoundingClientRect();
                const seams = Array.from(sheetEl.querySelectorAll('.page-seam'));
                const a4 = (297 / 25.4) * 96;
                const gap = 18;
                return {
                    count: seams.length + 1,
                    sheetH: +rect.height.toFixed(2),
                    a4: +a4.toFixed(2),
                    gap,
                    expectedH: +((seams.length + 1) * a4 + seams.length * gap).toFixed(2),
                    seamHeights: seams.map((s) => +s.getBoundingClientRect().height.toFixed(2)),
                    tailCount: sheetEl.querySelectorAll('.page-tail').length,
                    pagerButtons: document.querySelectorAll('.pager-page').length,
                };
            })(),
            scrollbar: (() => {
                const stage = document.querySelector('.mg-paper-stage');
                if (!stage) return null;
                const cs = getComputedStyle(stage);
                const root = getComputedStyle(document.documentElement);
                return {
                    color: cs.scrollbarColor,
                    width: cs.scrollbarWidth,
                    desk1: root.getPropertyValue('--mg-desk-1').trim(),
                    desk2: root.getPropertyValue('--mg-desk-2').trim(),
                };
            })(),
            framePointerEvents: (() => {
                const frame = document.querySelector('#sheet-frame');
                return frame ? getComputedStyle(frame).pointerEvents : null;
            })(),
            scrollW: document.documentElement.scrollWidth,
            innerW: window.innerWidth,
            overflowing: Array.from(document.querySelectorAll('*'))
                .filter(e => e.getBoundingClientRect().width > 0
                          && e.getBoundingClientRect().right > window.innerWidth + 1)
                .map(e => (e.className || e.tagName)).slice(0, 5),
        };
    }""")

def zoom_label(page):
    return page.locator(".zoom-value").inner_text().strip()


def measure_wheel(page):
    """Ctrl+滚轮：方向、iframe 内也生效、预览区外禁用（用户 2026-10-28 实测报告的三条）。

    必须真的发 wheel 事件 —— 只读代码证明不了「指针落在纸面上时事件进的是 iframe 文档」。
    """
    out = {}
    stage = page.locator(".mg-paper-stage")
    box = stage.bounding_box()
    frame = page.frame_locator("iframe")
    page.evaluate(
        "() => { window.__wheelSeen = [];"
        " window.addEventListener('wheel', (e) =>"
        "   window.__wheelSeen.push({ctrl: e.ctrlKey, prevented: e.defaultPrevented})); }")
    frame.locator("body").evaluate(
        "() => { window.__wheelInFrame = [];"
        " window.addEventListener('wheel', (e) =>"
        "   window.__wheelInFrame.push({ctrl: e.ctrlKey, dy: e.deltaY})); }")

    def wheel_at(x, y, dy):
        page.mouse.move(x, y)
        page.keyboard.down("Control")
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(280)
        page.keyboard.up("Control")
        page.wait_for_timeout(240)

    out["labelStart"] = zoom_label(page)
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    wheel_at(cx, cy, -120)  # 浏览器惯例：滚轮向上 = 放大
    out["labelAfterUpOnPaper"] = zoom_label(page)
    wheel_at(cx, cy, 120)
    out["labelAfterDownOnPaper"] = zoom_label(page)
    out["frameWheel"] = frame.locator("body").evaluate("() => window.__wheelInFrame || []")
    out["labelBeforeOutside"] = zoom_label(page)
    wheel_at(200, 500, -120)  # 改题列表：预览区外
    out["labelAfterOutside"] = zoom_label(page)
    out["windowWheel"] = page.evaluate("() => window.__wheelSeen || []")
    return out


result = {"viewports": {}, "console_errors": []}
exe = find_chromium()
result["chromium"] = exe

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    for label, viewport in (("1440x900", {"width": 1440, "height": 900}),
                            ("1280x900", {"width": 1280, "height": 900})):
        page = browser.new_page(viewport=viewport)
        page.on("pageerror", lambda e: result["console_errors"].append(str(e)))
        page.goto(BASE + "/teacher/paper/1", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const f = document.getElementById('sheet-frame');"
            " return f && f.contentDocument && f.contentDocument.querySelector('.sheet'); }",
            timeout=20000)
        page.wait_for_timeout(600)
        result["viewports"][label] = measure(page)
        if label == "1440x900":
            result["wheel"] = measure_wheel(page)
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-a9-after-1440x900.png"))
        else:
            page.screenshot(path=os.path.join(r"@@FRONTEND_DIR@@".lstrip("/"), "verify-a9-after-1280x900.png"))
        page.close()
    browser.close()

emit(result)
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
  console.log(`  静态服务 http://127.0.0.1:${server.staticPort}/`);
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
  console.log('\n—— A9：iframe 内纸面几何（这是唯一能抓到「压扁」的地方）——');

  const v1440 = result.viewports?.['1440x900'] ?? {};
  const v1280 = result.viewports?.['1280x900'] ?? {};

  check(
    'A9-1 iframe 内 .sheet 计算宽度 = 793.7px ± 2px（210mm @96dpi，不是被压窄的值）',
    v1440.sheetComputedWidth !== null
      && Math.abs(parseFloat(v1440.sheetComputedWidth) - A4_W) <= 2,
    `实测 ${v1440.sheetComputedWidth}（期望 ${A4_W.toFixed(1)}；错误态是 459px）`,
  );
  check(
    'A9-1 iframe 的内容视口确实按 A4 宽渲染（docScrollWidth ≈ 793.7）',
    v1440.docScrollW !== null && Math.abs(v1440.docScrollW - A4_W) <= 2,
    `iframe 内容 scrollWidth=${v1440.docScrollW}；iframe offsetWidth=${v1440.iframeOffsetW}`,
  );
  check(
    'A9-2 padding-left / .sheet 宽度 ≈ 0.0952（±0.002）；错误态是 0.165',
    v1440.paddingOverWidth !== null && Math.abs(v1440.paddingOverWidth - 0.0952) <= 0.002,
    `实测 ${v1440.paddingOverWidth}（padding-left=${v1440.sheetPaddingLeft} / 宽=${v1440.sheetComputedWidth}）`,
  );
  check(
    'A9-3 宽高共用同一个缩放系数：scaleX == scaleY（等比，不是压扁）',
    v1440.scaleX !== null
      && v1440.scaleY !== null
      && Math.abs(v1440.scaleX - v1440.scaleY) < 0.005,
    `scaleX=${v1440.scaleX} scaleY=${v1440.scaleY}（内联 ${v1440.scaleInline}）`,
  );
  check(
    'A9-3 缩放后视觉尺寸 ≤ 容器可用尺寸（确实适配了画布，不是溢出后被裁）',
    v1440.iframeRectW !== null
      && v1440.iframeRectH !== null
      && v1440.scaleX < 1
      && Math.abs(v1440.iframeRectW - A4_W * v1440.scaleX) < 1.5
      && Math.abs(v1440.iframeRectH - A4_H * v1440.scaleY) < 1.5,
    `纸面视觉尺寸 ${v1440.iframeRectW}×${v1440.iframeRectH}（k=${v1440.scaleX}）`,
  );
  check(
    'A9-4 题面不再异常换行：所有题块高度一致（= 单行高度）',
    v1440.allItemsSingleLine === true && (v1440.itemCount ?? 0) > 0,
    `题块数=${v1440.itemCount} 不同高度集合=${JSON.stringify(v1440.uniqueItemHeights)}`,
  );
  check(
    'A9-4 列宽足以容纳一行算式（列宽 ≥ 单题所需）',
    v1440.colW !== null && v1440.colW >= 100,
    `grid 列宽=${v1440.colW}px × ${v1440.colCount} 列（错误态是 77px）`,
  );
  check(
    'A9 关键量没有随「纸面容器」一起被缩放（否则就是把几何算错了）',
    parseFloat(v1440.sheetPaddingLeft) > 75
      && Math.abs(parseFloat(v1440.sheetPaddingLeft) - (2 / 2.54) * 96) < 1,
    `padding-left=${v1440.sheetPaddingLeft}（2cm @96dpi ≈ 75.59px）；字号=${v1440.sheetFontSize}`,
  );

  console.log('\n—— A9-7 两种视口：不溢出 + 预览区就是灰底画布 ——');
  for (const [label, v] of Object.entries(result.viewports ?? {})) {
    check(
      `${label}：scrollWidth == innerWidth（不横向溢出）`,
      v.scrollW === v.innerW,
      `scrollW=${v.scrollW} innerW=${v.innerW} 越界=${JSON.stringify(v.overflowing ?? [])}`,
    );
    check(
      `${label}：滚动条长在灰区右缘（滚动区右缘 == 画布列右缘 ±2）`,
      v.preview !== null && Math.abs(v.preview.stageRight - v.preview.colRight) <= 2,
      `stage.r=${v.preview?.stageRight} col.r=${v.preview?.colRight}（滚动区 ${v.preview?.stageW}×${v.preview?.stageH}）`,
    );
    check(
      `${label}：预览区是灰底画布本身（滚动区与纸面容器背景都透明，没有白纸盒）`,
      v.preview !== null
        && v.preview.stageBg === 'rgba(0, 0, 0, 0)'
        && v.preview.frameBg === 'rgba(0, 0, 0, 0)',
      `stageBg=${v.preview?.stageBg} frameBg=${v.preview?.frameBg}`,
    );
    check(
      `${label}：.sheet 宽度仍是 793.7 ± 2（换视口不改变纸内几何）`,
      v.sheetComputedWidth !== null && Math.abs(parseFloat(v.sheetComputedWidth) - A4_W) <= 2,
      `实测 ${v.sheetComputedWidth}`,
    );
    check(
      `${label}：所有题块仍是单行（换视口不产生换行）`,
      v.allItemsSingleLine === true,
      `不同高度集合=${JSON.stringify(v.uniqueItemHeights)}`,
    );
  }

  console.log('\n—— A9-8 Ctrl+滚轮：方向、iframe 内也生效、预览区外禁用 ——');
  const wheel = result.wheel ?? {};
  const pct = (value) => {
    const n = parseFloat(String(value ?? '').replace('%', ''));
    return Number.isFinite(n) ? n : null;
  };
  const startPct = pct(wheel.labelStart);
  const upPct = pct(wheel.labelAfterUpOnPaper);
  const downPct = pct(wheel.labelAfterDownOnPaper);
  check(
    'A9-8 滚轮向上 = 放大（浏览器惯例，方向不能反）',
    startPct !== null && upPct !== null && upPct > startPct,
    `${wheel.labelStart} → ${wheel.labelAfterUpOnPaper}`,
  );
  check(
    'A9-8 滚轮向下 = 缩小',
    upPct !== null && downPct !== null && downPct < upPct,
    `${wheel.labelAfterUpOnPaper} → ${wheel.labelAfterDownOnPaper}`,
  );
  check(
    'A9-8 指针落在纸面上时 wheel 事件不再进入卷面文档（进了就拦不住浏览器整页缩放）',
    (wheel.frameWheel ?? []).length === 0,
    `iframe 内收到 ${JSON.stringify(wheel.frameWheel ?? [])}`,
  );
  check(
    'A9-8 指针落在纸面上时，父文档收到这次 Ctrl+滚轮并 preventDefault',
    (wheel.windowWheel ?? []).some((event) => event.ctrl === true && event.prevented === true),
    `父文档收到 ${JSON.stringify((wheel.windowWheel ?? []).filter((e) => e.ctrl))}`,
  );
  check(
    'A9-8 卷面 iframe 不接鼠标（pointer-events: none），滚轮才会落到预览区',
    v1440.framePointerEvents === 'none',
    `#sheet-frame pointer-events=${v1440.framePointerEvents}`,
  );
  check(
    'A9-8 预览区外 Ctrl+滚轮不缩放预览',
    wheel.labelBeforeOutside !== undefined && wheel.labelAfterOutside === wheel.labelBeforeOutside,
    `${wheel.labelBeforeOutside} → ${wheel.labelAfterOutside}`,
  );
  check(
    'A9-8 预览区外 Ctrl+滚轮被 preventDefault（浏览器整页缩放被禁用）',
    (wheel.windowWheel ?? []).some((event) => event.ctrl === true && event.prevented === true),
    `父文档收到 ${JSON.stringify(wheel.windowWheel ?? [])}`,
  );

  console.log('\n—— A9-9 滚动条和灰底不能长得一样（用户 2026-10-28）——');
  const scrollbar = v1440.scrollbar ?? {};
  const parseColor = (text) => {
    const match = String(text ?? '').match(/(?:rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+))|#([0-9a-f]{6})/i);
    if (!match) return null;
    if (match[4]) {
      const hex = match[4];
      return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
    }
    return [match[1], match[2], match[3]].map((n) => parseFloat(n));
  };
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map((v) => {
      const s = v / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a, b) => {
    const la = luminance(a);
    const lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const thumb = parseColor(scrollbar.color);
  const desk1 = parseColor(scrollbar.desk1);
  const desk2 = parseColor(scrollbar.desk2);
  const c1 = thumb && desk1 ? contrast(thumb, desk1) : null;
  const c2 = thumb && desk2 ? contrast(thumb, desk2) : null;
  check(
    'A9-9 滚动条拇指与桌面两端色的对比都 ≥ 3:1（非文本控件底线）',
    c1 !== null && c2 !== null && Math.min(c1, c2) >= 3,
    `拇指 ${scrollbar.color}；对 ${scrollbar.desk1} = ${c1?.toFixed(2)}:1、对 ${scrollbar.desk2} = ${c2?.toFixed(2)}:1`,
  );
  check(
    'A9-9 滚动条是「细」的（不抢纸面）',
    scrollbar.width === 'thin',
    `scrollbar-width=${scrollbar.width}`,
  );

  console.log('');
  console.log('—— A9-10 卷面文档自己不滚动（纸面右缘不该长出原生滚动条）——');
  const iframeScroll = v1440.iframeScroll ?? {};
  check(
    'A9-10 卷面 iframe 没有内部滚动条',
    iframeScroll.scrollbarW === 0 && iframeScroll.scrollbarH === 0,
    `iframe 滚动条占位 横 ${iframeScroll.scrollbarW}px / 纵 ${iframeScroll.scrollbarH}px`,
  );
  check(
    'A9-10 卷面文档的 html 与 body 都是 overflow:hidden',
    iframeScroll.html === 'hidden/hidden' && iframeScroll.body === 'hidden/hidden',
    `html ${iframeScroll.html}；body ${iframeScroll.body}`,
  );

  console.log('');
  console.log('—— A9-11 每一页正好一张 A4，最后一页也画到页底（用户 2026-10-28）——');
  const pages = v1440.pages ?? {};
  check(
    'A9-11 卷面总高 == 页数 × A4 + 页间灰缝（±2px）',
    typeof pages.sheetH === 'number' && typeof pages.expectedH === 'number'
      && Math.abs(pages.sheetH - pages.expectedH) <= 2,
    `实测 ${pages.sheetH} / 期望 ${pages.expectedH}（${pages.count} 页，A4=${pages.a4}，灰缝 ${pages.gap}）`,
  );
  check(
    'A9-11 页数 == 页码栏按钮数（页数不许算错）',
    typeof pages.count === 'number' && pages.count === pages.pagerButtons,
    `页数 ${pages.count} / 页码按钮 ${pages.pagerButtons}`,
  );
  check(
    'A9-11 每条灰缝高度 == 2 × 页边距 + 灰缝（≥1 页时）',
    Array.isArray(pages.seamHeights)
      && (pages.count === 1 || pages.seamHeights.length === pages.count - 1),
    `灰缝高 ${JSON.stringify(pages.seamHeights)}`,
  );

  check('无 pageerror', (result.console_errors ?? []).length === 0, (result.console_errors ?? []).join(' ; ').slice(0, 200));

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    for (const f of failed) console.log(`  - ${f.name} (${f.detail ?? ''})`);
    process.exit(1);
  }
  console.log('A9（预览等比缩放，不压扁 A4）通过。');
}

await main();
