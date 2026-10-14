/**
 * 老师端界面验收脚本（之二）：配置页六维表单 + 打印版式 + **后端未就绪时的降级行为**。
 *
 * 覆盖任务卡里三条不太容易一眼看出的要求：
 *   A. `/teacher` 六维表单的选项与 `docs/00-决策记录.md` D16 档位表 / D17 题型**逐条一致**
 *      （不是「看起来像」，而是把 R1–R7 七行、三种题型、五个约束开关都断言出来）
 *   B. **后端未就绪时给出明确错误提示，而不是白屏**（任务卡硬要求，两种失败形态各测一遍：
 *      ① 代理打不通后端 → 502；② 后端不响应 /api/papers/{id}）
 *   C. 打印版式：题目版打印隐藏答案、答案版打印隐藏答题空位（FR-T6 分别打印）
 *
 * 用法：
 *   & $NODE frontend/scripts/verify-teacher-ui.mjs
 * 退出码 0 = 全部断言通过。
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const DIST_DIR = path.join(FRONTEND_DIR, 'dist');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');
const NODE_EXE = process.execPath;

const TMP_DIR = path.join(FRONTEND_DIR, '.tmp-playwright');
mkdirSync(TMP_DIR, { recursive: true });

const STATIC_PORT = 4173;
const API_PORT = 8123;
const PYTHON = path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe');

const checks = [];
function check(name, condition, detail) {
  checks.push({ name, ok: Boolean(condition), detail });
  console.log(`  [${condition ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

/**
 * @param {'online'|'proxy-down'|'api-down'} mode
 *   online      模拟后端起来，给齐 /api
 *   proxy-down  8000 那一侧没人应答（模拟后端服务未启动）
 *   api-down    模拟后端进程在，但 /api/papers/{id} 返回 500（模拟后端半死不活）
 */
function startServers(mode) {
  const child = spawn(NODE_EXE, [path.join(__dirname, 'verify-server.mjs')], {
    cwd: FRONTEND_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, MATHGEN_VERIFY_MODE: mode },
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
      reject(new Error(`服务进程没就绪。stderr:\n${stderr}`));
    }, 15_000);
    child.stdout.on('data', (chunk) => {
      if (chunk.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve(child);
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

BASE = "http://127.0.0.1:${STATIC_PORT}"

def find_chromium():
    explicit = os.environ.get("MATHGEN_CHROMIUM")
    if explicit and os.path.exists(explicit):
        return explicit
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
             os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")]
    cands = []
    for root in roots:
        cands += glob.glob(os.path.join(root, "chromium-*", "chrome-win64", "chrome.exe"))
        cands += glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome"))
    def rev(p):
        part = [c for c in p.split(os.sep) if c.startswith("chromium-")]
        tag = part[0].split("-")[-1] if part else "0"
        return int(tag) if tag.isdigit() else 0
    return sorted(cands, key=rev)[-1] if cands else None

def emit(obj):
    print("##RESULT##" + json.dumps(obj))

with sync_playwright() as p:
    exe = find_chromium()
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    result = {}

    # ---------- A. 六维表单（配置页不依赖任何后端数据）----------
    page.goto(BASE + "/teacher", wait_until="domcontentloaded")
    page.wait_for_selector('[data-dimension="ops"]', timeout=15000)
    page.wait_for_timeout(300)

    result["dimensions"] = page.eval_on_selector_all(
        "[data-dimension]", "els => els.map(e => e.getAttribute('data-dimension'))")
    result["ops"] = page.eval_on_selector_all(
        '[data-dimension="ops"] .chip', "els => els.map(e => e.textContent.trim())")
    result["ranges"] = page.eval_on_selector_all(
        '[data-dimension="ranges"] .range-label', "els => els.map(e => e.textContent.trim())")
    result["constraints"] = page.eval_on_selector_all(
        '[data-dimension="constraints"] .switch-row-label', "els => els.map(e => e.textContent.trim())")
    result["question_types"] = page.eval_on_selector_all(
        '[data-dimension="question_type"] .chip', "els => els.map(e => e.textContent.trim())")
    result["answer_forms"] = page.eval_on_selector_all(
        '[data-dimension="answer_form"] .chip', "els => els.map(e => e.textContent.trim())")
    result["answer_form_pending"] = (page.inner_text('[data-testid="answer-form-pending"]')
        if page.locator('[data-testid="answer-form-pending"]').count() > 0 else None)
    result["answer_form_clickable_count"] = page.eval_on_selector_all(
        '[data-dimension="answer_form"] input[type="radio"]',
        "els => els.filter(e => !e.disabled && !e.readOnly).length")
    result["answer_form_radio_count"] = page.locator(
        '[data-dimension="answer_form"] input[type="radio"]').count()
    result["count_value"] = page.input_value('[data-testid="count"]')
    result["count_bounds"] = {
        "min": page.get_attribute('[data-testid="count"]', "min"),
        "max": page.get_attribute('[data-testid="count"]', "max"),
    }

    result["max_value_visible_before"] = page.is_visible('[data-testid="max-value"]')
    page.click('[data-testid="range-R7"]')
    page.wait_for_timeout(120)
    result["max_value_visible_after"] = page.is_visible('[data-testid="max-value"]')
    result["problems_r7_without_value"] = page.inner_text('[data-testid="config-problems"]')
    page.fill('[data-testid="max-value"]', "50")
    page.wait_for_timeout(150)
    result["problems_r7_with_value"] = page.inner_text('[data-testid="config-ok"]')

    page.click('[data-testid="constraint-exact_division"]')
    page.click('[data-testid="constraint-allow_remainder"]')
    page.wait_for_timeout(150)
    result["problems_mutex"] = page.inner_text('[data-testid="config-problems"]')

    page.screenshot(path=os.path.join(r"${FRONTEND_DIR}", "verify-config-form.png"))

    # ---------- B/C. 卷页面 ----------
    page.goto(BASE + "/teacher/paper/1", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    # D56：卷面由 iframe 装后端 HTML；能不能拿到卷面取决于模拟后端是否就绪
    result["has_sheet_frame"] = page.locator("#sheet-frame").count() > 0
    result["sheet_in_frame"] = page.evaluate(
        "() => { const f = document.getElementById('sheet-frame');"
        " const d = f && f.contentDocument;"
        " return Boolean(d && d.querySelector('.sheet')); }")
    result["has_preview"] = result["sheet_in_frame"]
    result["error_card"] = (page.inner_text('[data-testid="paper-error"]')
                            if page.locator('[data-testid="paper-error"]').count() > 0 else None)
    result["error_code_text"] = (page.inner_text('.error-code')
                                 if page.locator('.error-code').count() > 0 else None)
    result["body_text_head"] = page.inner_text("body")[:200]
    result["backend_banner"] = (page.inner_text('[data-testid="backend-banner"]')
                                if page.locator('[data-testid="backend-banner"]').count() > 0 else None)

    # ---------- 排版面板文案（D59）：给老师看人话，不怼 CSS 关键字 ----------
    # 仅在卷页面真的渲染出排版面板时采集（后端不可用的场景没有面板）
    panel_ready = page.locator(".layout-panel").count() > 0
    result["has_layout_panel"] = panel_ready
    result["panel_bool_values"] = page.eval_on_selector_all(
        '[data-control="answer_line"] .layout-control-value, [data-control="header_bar"] .layout-control-value',
        "els => els.map(e => e.textContent.trim())") if panel_ready else []
    result["panel_visible_text"] = page.eval_on_selector(
        ".layout-panel", "el => el.innerText") if panel_ready else ""
    result["panel_var_chips"] = page.eval_on_selector_all(
        ".layout-panel .var-chip", "els => els.map(e => e.textContent.trim())") if panel_ready else []
    result["page_margin_note"] = (page.inner_text('[data-testid="screen-note-page_margin"]')
                                  if page.locator('[data-testid="screen-note-page_margin"]').count() > 0 else None)

    if result["has_preview"]:
        # 打印版式必须在**打印媒体**里断言（屏幕媒体的 display 不能说明打印结果）
        # D56：打印不再是「window.print() 打主文档 + body[data-print-mode] 切版本」，
        # 而是「取后端 /sheet HTML → 写进隐藏 iframe → 打印那个 iframe」。
        # 所以这里在打印 iframe 的 window.print 上装桩，抓打印时刻的卷面内容。
        page.click('[data-testid="layout-reset"]')   # 恢复排版默认，避免场景串味
        page.wait_for_timeout(300)
        page.click('[data-testid="preview-tab-question"]')
        page.wait_for_timeout(300)

        def probe_print(testid):
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
                                mode: sheet ? sheet.getAttribute('data-mode') : null,
                                hasAnswerSection: Boolean(d.querySelector('.answer-section')),
                                answerItems: d.querySelectorAll('.a-item').length,
                                headerBar: sheet ? sheet.getAttribute('data-header-bar') : null,
                                cols: d.documentElement.style.getPropertyValue('--cols-per-row'),
                                answerLineDisplay:
                                    d.documentElement.style.getPropertyValue('--answer-line-display'),
                                /* 打印媒体下卷头的显隐（D56：卷面 CSS 由后端提供） */
                                headerDisplay: sheet
                                    ? getComputedStyle(d.querySelector('.header-bar')).display : null,
                                blankDisplay: d.querySelector('.q-blank')
                                    ? getComputedStyle(d.querySelector('.q-blank')).display : null,
                            });
                        };
                    };
                    const timer = setInterval(hook, 20);
                    setTimeout(() => clearInterval(timer), 12000);
                    return el;
                };
            }""")
            page.eval_on_selector(
                '[data-testid="' + testid + '"]',
                "el => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))")
            page.wait_for_timeout(1200)
            return page.evaluate("() => window.__printed")

        result["print_question"] = probe_print("print-question")
        result["print_answer"] = probe_print("print-answer")

    result["page_errors"] = errors
    browser.close()

emit(result)
`;

function runPython() {
  const proc = spawnSync(PYTHON, ['-c', PY_SCRIPT], {
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

async function runScenario(mode) {
  const server = await startServers(mode);
  try {
    return runPython();
  } finally {
    server.kill();
  }
}

async function main() {
  if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
    console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
    process.exit(2);
  }

  console.log('=== 场景 1/3：后端可用 ===');
  const online = await runScenario('online');
  console.log('=== 场景 2/3：后端未启动（代理 502）===');
  const proxyDown = await runScenario('proxy-down');
  console.log('=== 场景 3/3：后端返回 500 ===');
  const apiDown = await runScenario('api-down');

  console.log('\n—— A. 六维表单 ——');
  const dims = online.dimensions ?? [];
  for (const expected of ['ops', 'ranges', 'constraints', 'question_type', 'count', 'answer_form']) {
    check(`六维里存在「${expected}」`, dims.includes(expected), dims.join(','));
  }
  check('运算类型 4 项（+ − × ÷）', (online.ops ?? []).length === 4, (online.ops ?? []).join(' / '));
  check('数值范围正好 R1–R7 七行（D16 档位表）', (online.ranges ?? []).length === 7, (online.ranges ?? []).join(' / '));
  const r = online.ranges ?? [];
  check('R1 = 20 以内加减法（一年级）', (r[0] ?? '').includes('20 以内加减法'), r[0]);
  check('R2 = 100 以内加减法（含进位 / 退位）', (r[1] ?? '').includes('100 以内加减法'), r[1]);
  check('R3 = 表内乘除法（九九表范围内）', (r[2] ?? '').includes('表内乘除法'), r[2]);
  check('R4 = 两位数 × 一位数、两位数 ÷ 一位数', (r[3] ?? '').includes('两位数 × 一位数') && (r[3] ?? '').includes('两位数 ÷ 一位数'), r[3]);
  check('R5 = 三位数 × 两位数、三位数 ÷ 两位数', (r[4] ?? '').includes('三位数 × 两位数') && (r[4] ?? '').includes('三位数 ÷ 两位数'), r[4]);
  check('R6 = 四位数及以上多位数加减', (r[5] ?? '').includes('四位数及以上'), r[5]);
  check('R7 = 老师自定义上限（最大不超过 N）', (r[6] ?? '').includes('自定义上限'), r[6]);
  check('特殊约束 5 个开关', (online.constraints ?? []).length === 5, (online.constraints ?? []).join(' / '));
  const qt = JSON.stringify(online.question_types ?? []);
  check('题型 3 种 = D17（口算横式 / 脱式递等式 / 填空求未知数）', (online.question_types ?? []).length === 3 && qt.includes('口算横式') && qt.includes('脱式') && qt.includes('填空'), (online.question_types ?? []).join(' / '));
  check(
    '题量默认 20、范围 1..100（对齐 PaperConfigIn.count）',
    online.count_value === '20' && online.count_bounds?.min === '1' && online.count_bounds?.max === '100',
    `value=${online.count_value} min=${online.count_bounds?.min} max=${online.count_bounds?.max}`,
  );
  check(
    '第六维答案版形式：只展示 1 个 D20 默认选项',
    (online.answer_forms ?? []).length === 1 && (online.answer_forms ?? [])[0].includes('单独打印'),
    (online.answer_forms ?? []).join(' / '),
  );
  check(
    '第六维：标注「M3 起生效」而不是假装可点（D55 / D20）',
    (online.answer_form_pending ?? '').includes('M3 起生效'),
    (online.answer_form_pending ?? '').slice(0, 60),
  );
  check(
    '第六维：没有可点的 radio（全部 readonly）',
    online.answer_form_clickable_count === 0 && online.answer_form_radio_count === 1,
    `可点数=${online.answer_form_clickable_count} / radio 总数=${online.answer_form_radio_count}`,
  );
  check('未选 R7 时不显示 max_value 输入框', online.max_value_visible_before === false);
  check('勾选 R7 后出现 max_value 输入框', online.max_value_visible_after === true);
  check('R7 缺 max_value 时前端给出「必须填」提示', (online.problems_r7_without_value ?? '').includes('必须填'), online.problems_r7_without_value);
  check('R7 填了上限后提示变为「可以生成」', (online.problems_r7_with_value ?? '').includes('可以生成'), online.problems_r7_with_value);
  check('整除 + 有余数 互斥可被前端识别', (online.problems_mutex ?? '').includes('互斥'), online.problems_mutex);

  console.log('\n—— B. 后端未就绪：明确报错而非白屏 ——');
  check('场景 2 卷页面渲染出错误卡片（不是白屏）', Boolean(proxyDown.error_card), `error_card=${(proxyDown.error_card ?? '').slice(0, 80)}`);
  check('场景 2 错误卡片写了「读不到卷」', (proxyDown.error_card ?? '').includes('读不到卷'), (proxyDown.error_card ?? '').slice(0, 60));
  check('场景 2 页面顶级横幅提示后端未就绪', (proxyDown.backend_banner ?? '').includes('后端未就绪'), (proxyDown.backend_banner ?? '').slice(0, 80));
  check('场景 3 后端 500 时同样渲染错误卡片', Boolean(apiDown.error_card), `error_card=${(apiDown.error_card ?? '').slice(0, 80)}`);
  check('场景 3 错误卡片带错误码（便于排错）', Boolean(apiDown.error_code_text), apiDown.error_code_text);
  check('场景 2/3 都没有 pageerror（不是崩溃）', (proxyDown.page_errors ?? []).length === 0 && (apiDown.page_errors ?? []).length === 0, [...(proxyDown.page_errors ?? []), ...(apiDown.page_errors ?? [])].join(' ; ') || '(无)');

  console.log('\n—— D. 排版面板文案（D59：给老师人话，不怼 CSS 关键字）——');
  check('后端可用时卷页面渲染出排版面板', online.has_layout_panel === true);
  const boolValues = online.panel_bool_values ?? [];
  check('两个开关的状态文案是人类可读的（保留 / 显示…）', boolValues.length === 2 && boolValues.every((v) => /[\u4e00-\u9fa5]/.test(v)) && !boolValues.some((v) => /^(none|block|grid|1|0)$/.test(v)), boolValues.join(' / '));
  check('面板正文里不出现裸 CSS 关键字（none / block / grid）', !/\bnone\b|\bblock\b|\bgrid\b/.test(online.panel_visible_text ?? ''), `面板文本片段=${(online.panel_visible_text ?? '').slice(0, 120)}`);
  check('CSS 变量名以调试小字出现（老师要排查时才需要）', (online.panel_var_chips ?? []).some((v) => v.includes('--answer-line-display')) && (online.panel_var_chips ?? []).some((v) => v.includes('--header-bar-display')), (online.panel_var_chips ?? []).join(' / '));
  check('页边距控件带「屏幕上看不出效果」的说明（不让它显得像坏了）', Boolean(online.page_margin_note) && (online.page_margin_note ?? '').length > 0, online.page_margin_note);

  console.log('\n—— C. 打印（D56：打印的是后端卷面 iframe，不是主文档）——');
  check('后端可用时卷页面 iframe 里渲染出后端卷面（.sheet）', online.sheet_in_frame === true, `has_sheet_frame=${online.has_sheet_frame}`);

  const pq = (online.print_question ?? [])[0];
  const pa = (online.print_answer ?? [])[0];
  check('打印题目版：打印的是 mode=question 的卷面', pq?.mode === 'question', JSON.stringify(pq));
  check('  题目版打印内容不含答案区（不泄题）', pq?.hasAnswerSection === false && pq?.answerItems === 0, `hasAnswerSection=${pq?.hasAnswerSection} 答案条数=${pq?.answerItems}`);
  check('打印答案版：打印的是 mode=answer 的卷面', pa?.mode === 'answer', JSON.stringify(pa));
  check('  答案版打印内容含答案区', pa?.hasAnswerSection === true && (pa?.answerItems ?? 0) > 0, `hasAnswerSection=${pa?.hasAnswerSection} 答案条数=${pa?.answerItems}`);
  check('  两次打印用的是同一份后端卷面（同样的排版变量）', pq?.cols === '4' && pa?.cols === '4', `题目版 cols=${pq?.cols} / 答案版 cols=${pa?.cols}`);
  check('  排版恢复默认后答题横线在卷面里可见（--answer-line-display=block）', pq?.answerLineDisplay === 'block' && pq?.blankDisplay !== 'none', `answerLineDisplay=${pq?.answerLineDisplay} q-blank display=${pq?.blankDisplay}`);

  check('在线场景无 pageerror', (online.page_errors ?? []).length === 0, (online.page_errors ?? []).join(' ; ') || '(无)');

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    for (const item of failed) console.log(`  - ${item.name} (${item.detail ?? ''})`);
    process.exit(1);
  }
  console.log('全部通过。截图：frontend/verify-config-form.png');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
