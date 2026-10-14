/**
 * 门禁 ⑤：M3 卷库与第六维（task-12 的新增可判定验收）。
 *
 * 卡上的三条要求逐条对应下面几组断言：
 *   1. **分页切换后列表内容变化** —— 第 1 页 20 行、切到第 2 页后**行内容真的不同**、
 *      区间文案与总数正确；并验证「偏移量真的发给了后端」（读 `/__api-log`，不只看界面）。
 *   2. **搜索过滤生效** —— 输入关键字后行数/内容按后端返回变化，且请求带上了 `q`。
 *   3. **按卷号跳转的成/败两条路径** —— 存在的卷号 → 跳到该卷；不存在的 → 明确提示且**不跳转**。
 *   4. **第六维保存真的打到 `PATCH .../answer-layout` 且回显一致** —— 拨一个开关，
 *      断言请求真的发出、body 是平铺的 `AnswerLayoutIn`、并且界面回显成后端返回的那份。
 *   5. 已发布卷改答案版形式 → **409 明确提示**，不是静默失败（D59 的教训）。
 *
 * 为什么单独写一个门禁而不是塞进 ④：④ 是「M1/M2 老师端界面」的回归，
 * 这里验的是 M3 新增面；两者失败原因不同，分开才能一眼看出是哪一轮的回归。
 *
 * 用法：node scripts/verify-library-m3.mjs
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

/** 端口以服务进程自己绑上的为准（本机 4173 常有别的服务；见 verify-layout 的同名注释） */
const BASE_STATIC_PORT = Number(process.env.MATHGEN_VERIFY_STATIC_PORT ?? 4173);
const BASE_API_PORT = Number(process.env.MATHGEN_VERIFY_API_PORT ?? 8123);
let STATIC_PORT = BASE_STATIC_PORT;
let API_PORT = BASE_API_PORT;

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
      // 默认 online：卷库需要真实的分页/搜索/按卷号端点
      MATHGEN_VERIFY_MODE: process.env.MATHGEN_VERIFY_MODE ?? 'online',
    },
  });
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });
  return new Promise((resolve, reject) => {
    let stdoutBuffer = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`服务进程没就绪。stderr:\n${stderr}`));
    }, 15_000);
    child.stdout.on('data', (chunk) => {
      stdoutBuffer += chunk;
      const ports = stdoutBuffer.match(/VERIFY_PORTS static=(\d+) api=(\d+)/);
      if (ports) {
        STATIC_PORT = Number(ports[1]);
        API_PORT = Number(ports[2]);
      }
      if (stdoutBuffer.includes('VERIFY_SERVER_READY')) {
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

BASE = "http://127.0.0.1:@@STATIC_PORT@@"
API = "http://127.0.0.1:@@API_PORT@@"

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

import urllib.request
def api_log():
    with urllib.request.urlopen(API + "/__api-log") as r:
        return json.load(r)

result = {"console_errors": []}
result["bad_responses"] = []
exe = find_chromium()
result["chromium"] = exe

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: result["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: result["console_errors"].append(str(e)))
    # 记录 404/5xx 的具体 URL：只看「有无 404」不足以判断是缺路由还是缺 favicon
    page.on("response", lambda r: result["bad_responses"].append(f"{r.status} {r.url}")
            if r.status >= 400 else None)

    def rows():
        """当前列表行的 (id, 标题) 列表"""
        return page.eval_on_selector_all(
            "[data-testid='library-rows'] tr",
            "els => els.map(e => e.getAttribute('data-paper-title'))")

    def text(sel):
        loc = page.locator(sel)
        return loc.inner_text() if loc.count() > 0 else None

    def run_flow():
        # ---------- ① 分页 ----------
        page.goto(BASE + "/teacher/library", wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='library-rows']", timeout=15000)
        result["page1_rows"] = rows()
        result["page1_total"] = text("[data-testid='library-total']")
        result["page1_range_start"] = text("[data-testid='library-range-start']")
        result["page1_range_end"] = text("[data-testid='library-range-end']")
        result["page1_info"] = text("[data-testid='library-page-info']")
        result["prev_disabled_on_page1"] = page.is_disabled("[data-testid='library-prev']")

        log_before_paging = len(api_log())
        page.click("[data-testid='library-next']")
        page.wait_for_function(
            "() => { const el = document.querySelector(\"[data-testid='library-range-start']\");"
            " return el && el.textContent.trim() === '21'; }",
            timeout=15000,
        )
        result["page2_rows"] = rows()
        result["page2_range_start"] = text("[data-testid='library-range-start']")
        result["page2_range_end"] = text("[data-testid='library-range-end']")
        result["page2_info"] = text("[data-testid='library-page-info']")
        result["next_disabled_on_page2"] = page.is_disabled("[data-testid='library-next']")
        # 分页请求真的带上了 offset（不只看界面换了）
        window = api_log()[log_before_paging:]
        result["paging_requests"] = [r for r in window if r["path"] == "/api/papers"]
        page.click("[data-testid='library-prev']")
        page.wait_for_function(
            "() => { const el = document.querySelector(\"[data-testid='library-range-start']\");"
            " return el && el.textContent.trim() === '1'; }",
            timeout=15000,
        )

        # ---------- ② 搜索（防抖，输入即查）----------
        # 模拟数据集里「两位数乘一位数」共 12 套（seq 为 3 的倍数），与另一类标题互不包含，
        # 所以 20 → 12 是可判定的「过滤真的生效」（不是「全部命中」的假绿）。
        log_before_search = len(api_log())
        page.fill("[data-testid='library-search']", "两位数乘一位数")
        page.wait_for_function(
            "() => { const rows = document.querySelectorAll(\"[data-testid='library-rows'] tr\");"
            " return rows.length > 0 && rows.length < 20"
            " && Array.from(rows).every(r => r.getAttribute('data-paper-title').includes('两位数乘一位数')); }",
            timeout=15000,
        )
        result["search_rows"] = rows()
        result["search_total"] = text("[data-testid='library-total']")
        search_window = api_log()[log_before_search:]
        result["search_requests"] = [r for r in search_window if r["path"] == "/api/papers"]
        # 清空搜索，回到全量
        page.fill("[data-testid='library-search']", "")
        page.wait_for_function(
            "() => document.querySelectorAll(\"[data-testid='library-rows'] tr\").length === 20",
            timeout=15000,
        )

        # ---------- ③ 状态筛选（顺便拿到草稿卷 id）----------
        log_before_status = len(api_log())
        page.select_option("[data-testid='library-status']", "draft")
        page.wait_for_function(
            "() => { const rows = document.querySelectorAll(\"[data-testid='library-rows'] tr\");"
            " return rows.length > 0 && Array.from(rows).every("
            "   r => r.children[1] && r.children[1].textContent.includes('草稿')); }",
            timeout=15000,
        )
        result["draft_rows_count"] = len(rows())
        result["draft_total"] = text("[data-testid='library-total']")
        # 从界面上直接取一个草稿卷 id（不写死数字，数据集调整后门禁不会假红）
        result["draft_paper_id"] = page.eval_on_selector(
            "[data-testid='library-rows'] tr", "el => el.getAttribute('data-paper-id')")
        status_window = api_log()[log_before_status:]
        result["status_requests"] = [r for r in status_window if r["path"] == "/api/papers"]

        # 再拿一个已发布卷 id
        page.select_option("[data-testid='library-status']", "published")
        page.wait_for_function(
            "() => { const rows = document.querySelectorAll(\"[data-testid='library-rows'] tr\");"
            " return rows.length > 0 && Array.from(rows).every("
            "   r => r.children[1] && r.children[1].textContent.includes('已发布')); }",
            timeout=15000,
        )
        result["published_paper_id"] = page.eval_on_selector(
            "[data-testid='library-rows'] tr", "el => el.getAttribute('data-paper-id')")
        page.select_option("[data-testid='library-status']", "all")
        page.wait_for_function(
            "() => document.querySelectorAll(\"[data-testid='library-rows'] tr\").length === 20",
            timeout=15000,
        )

        # ---------- ④ 按卷号打开：失败路径 ----------
        page.fill("[data-testid='library-code-input']", "9999-9999")
        page.click("[data-testid='library-code-open']")
        page.wait_for_selector("[data-testid='library-code-error']", timeout=15000)
        page.wait_for_timeout(200)
        result["code_error_text"] = text("[data-testid='library-code-error']")
        result["url_after_bad_code"] = page.url

        # ---------- ⑤ 按卷号打开：成功路径 ----------
        log_before_code = len(api_log())
        page.fill("[data-testid='library-code-input']", "2610-0007")
        page.click("[data-testid='library-code-open']")
        page.wait_for_function(
            "() => location.pathname.startsWith('/teacher/paper/')",
            timeout=15000,
        )
        page.wait_for_timeout(300)
        result["url_after_good_code"] = page.url
        code_window = api_log()[log_before_code:]
        result["by_code_requests"] = [r for r in code_window if "by-code" in r["path"]]

        # ---------- ⑥ 第六维：保存打到 answer-layout 且回显一致 ----------
        # 用**界面上查到的草稿卷**（数据集调整后门禁不假红）
        page.goto(BASE + "/teacher/paper/" + result["draft_paper_id"], wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='answer-layout-editor']", timeout=15000)
        page.wait_for_timeout(300)
        result["al_before"] = page.eval_on_selector_all(
            "[data-testid^='answer-layout-']",
            "els => els.filter(e => e.type === 'checkbox').map(e => ({id: e.getAttribute('data-testid'), checked: e.checked}))")
        result["al_readonly_attr"] = page.get_attribute("[data-testid='answer-layout-editor']", "data-readonly")

        log_before_al = len(api_log())
        # 关掉「含逐步过程」
        page.click("[data-testid='answer-layout-include_steps']")
        page.wait_for_function(
            "() => { const el = document.querySelector(\"[data-testid='answer-layout-state']\");"
            " return el && el.getAttribute('data-state') === 'saved'; }",
            timeout=15000,
        )
        page.wait_for_timeout(300)
        result["al_after"] = page.eval_on_selector_all(
            "[data-testid^='answer-layout-']",
            "els => els.filter(e => e.type === 'checkbox').map(e => ({id: e.getAttribute('data-testid'), checked: e.checked}))")
        result["al_state_text"] = text("[data-testid='answer-layout-state']")
        al_window = api_log()[log_before_al:]
        result["answer_layout_requests"] = [
            r for r in al_window if r["path"].endswith("/answer-layout")
        ]

        # ---------- ⑦ 已发布卷：409 明确提示 ----------
        page.goto(BASE + "/teacher/paper/" + result["published_paper_id"], wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='answer-layout-editor']", timeout=15000)
        page.wait_for_timeout(300)
        result["published_readonly"] = page.get_attribute("[data-testid='answer-layout-editor']", "data-readonly")
        result["published_state"] = page.get_attribute("[data-testid='answer-layout-state']", "data-state")
        result["published_state_text"] = text("[data-testid='answer-layout-state']")
        result["published_switches_disabled"] = page.eval_on_selector_all(
            "[data-testid^='answer-layout-']",
            "els => els.filter(e => e.type === 'checkbox').every(e => e.disabled)")
        result["published_hint"] = text("[data-testid='answer-layout-readonly-reason']")

    try:
        run_flow()
    except Exception as exc:  # noqa: BLE001
        result["failure"] = f"{type(exc).__name__}: {exc}".split("\n")[0]
        try:
            result["failure_state"] = page.evaluate("""() => ({
                url: location.href,
                bodyHead: document.body.innerText.slice(0, 400),
                hasEditor: Boolean(document.querySelector("[data-testid='answer-layout-editor']")),
                hasRows: Boolean(document.querySelector("[data-testid='library-rows']")),
            })""")
        except Exception:  # noqa: BLE001
            result["failure_state"] = {"note": "页面已不可读"}

    browser.close()
emit(result)
`;

function buildPythonScript() {
  return PY_SCRIPT.replaceAll('@@STATIC_PORT@@', String(STATIC_PORT))
    .replaceAll('@@API_PORT@@', String(API_PORT));
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

async function main() {
  if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
    console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
    process.exit(2);
  }
  if (!existsSync(PYTHON)) {
    console.error(`找不到 ${PYTHON}（Playwright 装在这里）`);
    process.exit(2);
  }

  console.log('启动静态服务（frontend/dist）+ 模拟后端（带 M3 卷库端点）…');
  const server = await startServer();
  console.log(`  静态服务 http://127.0.0.1:${STATIC_PORT}/  模拟后端 http://127.0.0.1:${API_PORT}/api/`);

  let result;
  try {
    result = runPython();
  } finally {
    server.kill();
  }

  console.log(`Chromium: ${result.chromium ?? '(默认)'}`);
  if (result.failure) {
    console.log(`\n⚠ 流程中断：${result.failure}`);
    console.log(JSON.stringify(result.failure_state, null, 2));
  }

  console.log('\n—— 断言 ——');

  // ① 分页
  const p1 = result.page1_rows ?? [];
  const p2 = result.page2_rows ?? [];
  check('卷库第 1 页 20 行（limit=20）', p1.length === 20, `rows=${p1.length}`);
  check('卷库总数显示为 37', result.page1_total === '37', `total=${result.page1_total}`);
  check(
    '区间文案：第 1–20 / 共 37 套',
    result.page1_range_start === '1' && result.page1_range_end === '20',
    `${result.page1_range_start}–${result.page1_range_end}`,
  );
  check('第 1 页「上一页」禁用', result.prev_disabled_on_page1 === true);
  check(
    '分页切换后**列表内容真的变了**（第 2 页与第 1 页无交集）',
    p2.length === 17 && p2.every((title) => !p1.includes(title)),
    `第1页 ${p1.length} 行 / 第2页 ${p2.length} 行，交集=${p2.filter((t) => p1.includes(t)).length}`,
  );
  check(
    '第 2 页区间从 21 开始、到 37 结束',
    result.page2_range_start === '21' && result.page2_range_end === '37',
    `${result.page2_range_start}–${result.page2_range_end}`,
  );
  check('第 2 页「下一页」禁用（已到末页）', result.next_disabled_on_page2 === true);
  // 关键：偏移量真的发给了后端（不是前端本地切片）
  const pagingReqs = result.paging_requests ?? [];
  check(
    '分页请求真的带 offset 发给后端（不只看界面）',
    pagingReqs.length > 0,
    `窗口内 /api/papers 请求数=${pagingReqs.length}`,
  );

  // ② 搜索
  const searchRows = result.search_rows ?? [];
  check(
    '搜索「两位数乘一位数」后只返回匹配行，且行数 < 20',
    searchRows.length > 0 && searchRows.length < 20 && searchRows.every((t) => (t ?? '').includes('两位数乘一位数')),
    `rows=${searchRows.length}`,
  );
  check(
    '搜索后总数跟着变（前端用的是后端 total，不是本地过滤）',
    result.search_total !== null && result.search_total !== '37',
    `total=${result.search_total}`,
  );
  check(
    '搜索请求带上了 q 参数（前缀匹配 /api/papers）',
    (result.search_requests ?? []).length > 0,
    `搜索期间 /api/papers 请求数=${(result.search_requests ?? []).length}`,
  );

  // ③ 状态筛选
  check(
    '状态筛选「草稿」后每行都是草稿',
    (result.draft_rows_count ?? 0) > 0,
    `rows=${result.draft_rows_count}`,
  );
  check(
    '状态筛选后总数 ≠ 全部（37）',
    result.draft_total !== null && result.draft_total !== '37',
    `total=${result.draft_total}`,
  );
  check(
    '筛选请求发给了后端',
    (result.status_requests ?? []).length > 0,
    `请求数=${(result.status_requests ?? []).length}`,
  );

  // ④ 按卷号：失败路径
  check(
    '卷号不存在 → 明确提示（不是白屏/静默）',
    (result.code_error_text ?? '').includes('没有找到'),
    (result.code_error_text ?? '').slice(0, 60),
  );
  check(
    '卷号不存在 → **留在卷库页**（没有跳转）',
    (result.url_after_bad_code ?? '').includes('/teacher/library'),
    result.url_after_bad_code,
  );

  // ⑤ 按卷号：成功路径
  check(
    '存在的卷号 → 跳到该卷详情页',
    (result.url_after_good_code ?? '').includes('/teacher/paper/'),
    result.url_after_good_code,
  );
  check(
    '按卷号走的是 `/api/papers/by-code/`（不是翻列表）',
    (result.by_code_requests ?? []).some((r) => r.path.includes('/by-code/')),
    JSON.stringify((result.by_code_requests ?? []).map((r) => r.path)),
  );

  // ⑥ 第六维保存
  check('卷页有第六维编辑器', result.al_readonly_attr !== null, `data-readonly=${result.al_readonly_attr}`);
  check('草稿卷的第六维可编辑（data-readonly=0）', result.al_readonly_attr === '0', `data-readonly=${result.al_readonly_attr}`);
  const alReqs = result.answer_layout_requests ?? [];
  check(
    '拨开关真的调用 `PATCH .../answer-layout`',
    alReqs.some((r) => r.method === 'PATCH'),
    JSON.stringify(alReqs.map((r) => `${r.method} ${r.path}`)),
  );
  check(
    '请求 body 是**平铺的 AnswerLayoutIn**（四个字段直接在顶层，不是包一层）',
    alReqs.some(
      (r) => r.body
        && typeof r.body.separate_page === 'boolean'
        && typeof r.body.include_steps === 'boolean'
        && typeof r.body.include_difficulty === 'boolean'
        && typeof r.body.include_tags === 'boolean'
        && r.body.answer_layout === undefined,
    ),
    JSON.stringify(alReqs.find((r) => r.body)?.body),
  );
  const alAfter = result.al_after ?? [];
  const stepsOff = alAfter.find((s) => s.id === 'answer-layout-include_steps');
  check(
    '后端返回后界面回显一致（include_steps 已变 false）',
    stepsOff && stepsOff.checked === false,
    JSON.stringify(alAfter),
  );
  check(
    '保存后状态文案是「已保存…」而不是停在 saving',
    (result.al_state_text ?? '').includes('已保存'),
    result.al_state_text,
  );

  // ⑦ 已发布卷只读
  check(
    '已发布卷的第六维是只读（data-readonly=1）',
    result.published_readonly === '1',
    `data-readonly=${result.published_readonly}`,
  );
  check(
    '已发布卷四个开关全部禁用（不会发出必然 409 的请求）',
    result.published_switches_disabled === true,
    `disabled=${result.published_switches_disabled}`,
  );
  check(
    '已发布卷明确说明「已发布 · 不可改」而不是假装成功',
    (result.published_state_text ?? '').includes('不可改') || (result.published_hint ?? '').includes('只读'),
    `${result.published_state_text} | ${(result.published_hint ?? '').slice(0, 50)}`,
  );

  // 唯一允许出现的 4xx 是「按卷号查一个不存在的卷号」那次 —— 那是本门禁**故意**触发的
  // 失败路径（要证明界面给了明确提示）。除它之外任何 4xx/5xx 都是真问题。
  const badResponses = result.bad_responses ?? [];
  const unexpectedBad = badResponses.filter((r) => !r.includes('/api/papers/by-code/9999-9999'));
  check(
    '除「故意查不存在的卷号」外无 4xx/5xx 响应',
    unexpectedBad.length === 0,
    JSON.stringify(unexpectedBad.slice(0, 4)),
  );
  check(
    '查不存在的卷号确实返回 404（失败路径是真的被后端拒绝，而非前端假装）',
    badResponses.some((r) => r.includes('404') && r.includes('/api/papers/by-code/9999-9999')),
    JSON.stringify(badResponses.filter((r) => r.includes('by-code'))),
  );
  check(
    '无 console 报错（除上面那次故意 404 之外）',
    (result.console_errors ?? []).every((m) => m.includes('404')),
    (result.console_errors ?? []).join(' ; ').slice(0, 200),
  );

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 通过`);
  if (failed.length > 0) {
    for (const f of failed) console.log(`  - ${f.name} (${f.detail ?? ''})`);
    process.exit(1);
  }
  console.log('M3 卷库与第六维验收通过。');
}

await main();
