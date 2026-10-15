#!/usr/bin/env node
/**
 * 把 mock 与**真实后端**逐字段比对（task-29 / V12）。
 *
 * ## 为什么必须存在这个脚本
 *
 * `frontend/scripts/verify-server.mjs` 的 mock 是**一份手写的第二事实来源**：
 * 它回答的是「我认为真实后端会返回什么」，而不是「真实后端返回什么」。
 * 于是所有界面判据的 36/36 全绿，只能证明**我自己的 mock 与我自己的断言一致**。
 *
 * 这件事不是假设 —— task-27 扩 mock 时，学生端交卷响应里 `answer` **整键消失过一次**
 * （把已为 D74 剥掉答案的 wire 数据直接拿去判分），而当时门禁所有断言都是绿的。
 * 唯一抓得住它的是逐字段比对；那次比对脚本是一次性的、随临时目录被清理，
 * 于是这类偏差又重新变成不可见的（D69 同族 / V9.1：证据与负对照都要留在仓库里）。
 *
 * ## 用法与前置
 *
 *   pnpm --dir frontend verify:mock-parity                    # 比对，任何字段差异 → exit 1
 *   pnpm --dir frontend verify:mock-parity -- --self-test     # 跑「改坏 → 必须红 → 改回 → 必须绿」
 *   pnpm --dir frontend verify:mock-parity -- --dump-real     # 只把真实响应打出来看
 *
 * 自 task-32 起，它同时是**默认门禁的第 ⑫ 步**（`run-all-verifications.mjs` 的 steps 数组）。
 * 理由：mock 是手写的第二事实来源，而「一份只靠人记得去跑的判据等于没有判据」（D69 / V9.1）。
 * 这一步**不会静默跳过**：前置缺失时它红且打印原因（见下），不会打印 PASS。
 *
 * 前置：
 *   - `backend/.venv` 存在（真实侧走 `TestClient(create_app(engine=...))`，**不需要**起 uvicorn）
 *   - `backend/tests/api/_support.py` 可用（复用它的内存 SQLite + 建表 lifespan，
 *     不自造一套夹具 —— 夹具会漂移，那正是本脚本要防的东西）
 *   - mock 侧由本脚本自己 spawn（空闲端口自动重试，端口从 stdout 的 `VERIFY_PORTS` 读，
 *     **不要**写死端口）
 *
 * ## 它比什么、不比什么（诚实边界）
 *
 * 比：**结构** —— 每个响应的键集合、每个键的类型、数组元素形状、以及**状态码**。
 * 不比：**取值** —— 两边种子/夹具不同，值必然不同（`243 × 12` vs `19 + 1`）。
 *       逐值比对需要两边跑同一份夹具，那是另一个量级的工程，且抓不住「少一个键」这类偏差。
 *
 * 这条边界是刻意选的：task-27 那次真缺陷就是**字段存在性**（`answer` 没了），
 * 而结构比对对「少键 / 多键 / 类型变了 / 状态码变了」全部敏感。
 *
 * ## 它抓不住什么（同样重要）
 *
 * - 两边**同时**错（mock 与真实都漏了同一个键，或都算错了值）；
 * - 值层面的语义错误（判分规则、编号、日期）；
 * - 真实后端的接口本身违反契约（那由 `backend/tests/contract/**` 负责，不是这里）。
 */

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(__dirname, '..');
const REPO = path.resolve(FRONTEND, '..');
const MOCK_SERVER = path.join(__dirname, 'verify-server.mjs');
// 后端 venv 的 python。允许用环境变量覆盖：`verify-negative-controls.mjs` 第 9 组正是靠它
// 注入「前置缺失」这一态（指向一个不存在的路径），从而证明 main() 里那个分支真的会红 ——
// 而**不是**只存在于代码里、从未被任何判据走过（V11：不可达的分支等于没测的分支）。
// 不设该变量时行为与从前逐字节相同：就是 backend/.venv 的真 python。
const PYTHON =
  process.env.MATHGEN_PARITY_PYTHON ||
  path.join(REPO, 'backend', '.venv', 'Scripts', 'python.exe');
const NODE = process.execPath;
const TMP = path.join(FRONTEND, '.tmp-capture');

const ARGS = new Set(process.argv.slice(2));
const SELF_TEST = ARGS.has('--self-test');
const DUMP_REAL_ONLY = ARGS.has('--dump-real');

/** 与 mock 的 `2610-0001`（`STUDENT_PAPER_CODE`）对齐：同 config 同 seed → 同卷号。 */
const CONFIG = {
  ops: ['×'],
  ranges: ['R5'],
  question_type: 'oral',
  max_value: null,
  require_carry: true,
  require_borrow: false,
  exact_division: false,
  allow_remainder: false,
  parentheses: false,
  count: 6,
};
const SEED = 20261014;
/** 序列里报到用的学生（学号 9002：真实侧同一个 DB，9001 可能在上一轮已被留下）。 */
const STUDENT = { name: '比对同学二号', student_no: '9002' };
/** 只用于「201 新建路径」的学生：前奏不会碰它，所以序列里第一次报必然是新建。 */
const STUDENT_FIRST = { name: '比对同学三号', student_no: '9003' };

// ---------------------------------------------------------------------------
// 结构签名：把一份响应压成「路径 → 类型」的列表
// ---------------------------------------------------------------------------

/**
 * 只取数组**第一个**元素作为形状代表：题量受夹具影响（mock 与真实各自生成），
 * 元素个数不是接口契约；元素**形状**才是。
 */
function signature(value, prefix = '') {
  const out = [];
  if (value === null) {
    out.push([prefix || '$', 'null']);
  } else if (Array.isArray(value)) {
    out.push([`${prefix}[]`, 'array']);
    if (value.length > 0) out.push(...signature(value[0], `${prefix}[]`));
  } else if (typeof value === 'object') {
    if (prefix) out.push([prefix, 'object']);
    for (const key of Object.keys(value).sort()) {
      out.push(...signature(value[key], prefix ? `${prefix}.${key}` : key));
    }
  } else {
    out.push([prefix, typeof value]);
  }
  return out;
}

function diffSignatures(mockBody, realBody) {
  const mockSig = new Map(signature(mockBody));
  const realSig = new Map(signature(realBody));
  const problems = [];
  for (const [key, type] of realSig) {
    if (!mockSig.has(key)) problems.push(`mock 缺字段 ${key}（真实是 ${type}）`);
    else if (mockSig.get(key) !== type) {
      problems.push(`mock 的 ${key} 类型是 ${mockSig.get(key)}，真实是 ${type}`);
    }
  }
  for (const [key, type] of mockSig) {
    if (!realSig.has(key)) problems.push(`mock 多字段 ${key}（真实没有，mock 类型 ${type}）`);
  }
  return problems;
}

// ---------------------------------------------------------------------------
// mock 侧：spawn 服务、按同一份请求序列探测
// ---------------------------------------------------------------------------

async function startMock(env = {}) {
  const child = spawn(NODE, [MOCK_SERVER], {
    cwd: FRONTEND,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, MATHGEN_VERIFY_STATIC_PORT: '4800', MATHGEN_VERIFY_API_PORT: '4801', ...env },
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += String(chunk); });
  // 端口从 stdout 读 —— 服务会在端口被占用时自动往后试（listenWithRetry）
  const port = await new Promise((resolve, reject) => {
    let buf = '';
    const timer = setTimeout(() => reject(new Error(`mock 服务 15s 未就绪。stderr:\n${stderr.slice(-2000)}`)), 15000);
    child.stdout.on('data', (chunk) => {
      buf += String(chunk);
      const m = buf.match(/VERIFY_PORTS static=(\d+) api=(\d+)/);
      if (m && buf.includes('VERIFY_SERVER_READY')) {
        clearTimeout(timer);
        resolve(Number(m[2]));
      }
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`mock 服务提前退出（exit ${code}）。stderr:\n${stderr.slice(-2000)}`));
    });
  });
  return { child, port };
}

async function call(base, method, url, body) {
  const res = await fetch(base + url, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = text.slice(0, 200); }
  return { status: res.status, body: parsed };
}

/**
 * 两边共用的请求序列模板。
 *
 * **为什么用 `@@TOKEN@@` 占位符而不是参数**：前奏里才拿得到 `paper_id` / `code` /
 * `practice_seed`，而 Python 侧（真实后端）和 Node 侧（mock）必须跑**逐字节相同**的序列。
 * 早先的写法是 JS 生成一份、Python 里再手抄一份 —— 那正是「两边漂移」的来源
 * （我第一版就在 Python 里漏抄了 `by_code` 与 `students`，导致比对报出假差异）。
 * 现在 Python 直接收这份 JSON 模板、自己替换 token，**序列只有一处定义**。
 */
const PLAN_TOKENS = ['@@PAPER_ID@@', '@@CODE@@', '@@STUDENT_ID@@', '@@PRACTICE_SEED@@', '@@PREFIX@@'];

function requestPlan() {
  return [
    ['list_papers', 'GET', '/api/papers?limit=1&status=published'],
    ['by_code', 'GET', '/api/papers/by-code/@@CODE@@'],
    ['student_paper', 'GET', '/api/student/paper/@@CODE@@'],
    ['student_paper_missing', 'GET', '/api/student/paper/9999-9999'],
    // 报到：**必须有一个覆盖「201 新建」的用例**。如果序列里只留重放那条，
    // mock 即使永远回 200 也会绿 —— 那正是「断言没在验东西」的形态。
    // 注意：前奏也会报一次同一个学生（为了拿 id 去交卷），所以 `students` 这条是**重放**
    // → 200；新建路径由 `students_first` 覆盖，它用的是前奏没用过的学号。
    ['students_first', 'POST', '/api/students', STUDENT_FIRST],
    ['students_first_replay', 'POST', '/api/students', STUDENT_FIRST],
    ['students', 'POST', '/api/students', STUDENT],
    ['students_replay', 'POST', '/api/students', STUDENT],
    ['practice', 'POST', '/api/student/practice?seed=@@SEED@@', CONFIG],
    ['attempt_practice', 'POST', '/api/attempts', {
      student_id: '@@STUDENT_ID@@',
      attempt_uuid: '@@PREFIX@@-practice',
      duration_ms: 1000,
      config: CONFIG,
      seed: '@@PRACTICE_SEED@@',
      answers: [{ seq: 1, answer: '0' }],
    }],
    ['attempt_paper', 'POST', '/api/attempts', {
      student_id: '@@STUDENT_ID@@',
      attempt_uuid: '@@PREFIX@@-paper',
      duration_ms: 1000,
      paper_id: '@@PAPER_ID@@',
      answers: [{ seq: 1, answer: '0' }],
    }],
    ['attempt_replay', 'POST', '/api/attempts', {
      student_id: '@@STUDENT_ID@@',
      attempt_uuid: '@@PREFIX@@-paper',
      duration_ms: 1000,
      paper_id: '@@PAPER_ID@@',
      answers: [{ seq: 1, answer: '0' }],
    }],
    // 老师端详情：用前奏新建的那套卷（两边同一资源）。**必须含 `answer`/`steps`** ——
    // 这是反向断言：学生端取卷被 D74 剥掉答案，老师端详情**不能**也被剥掉。
    ['detail', 'GET', '/api/papers/@@PAPER_ID@@'],
  ];
}

/** 替换后必须还原成数字的字段名 —— 与 Python 侧 fill() 的 NUMERIC_KEYS 一一对应。 */
const NUMERIC_KEYS = new Set(['student_id', 'paper_id', 'seed']);

/** 递归替换 token；数字型字段还原成 number（否则真实后端会以 422 拒掉）。 */
function fillValue(value, map, key) {
  if (typeof value === 'string') {
    let out = value;
    for (const [token, real] of Object.entries(map)) out = out.split(token).join(String(real));
    if (out.includes('@@') && !out.includes('9999-9999')) {
      throw new Error(`请求计划里还有没替换的占位符：${out}`);
    }
    if (NUMERIC_KEYS.has(key)) return Number(out);
    return out;
  }
  if (Array.isArray(value)) return value.map((v) => fillValue(v, map));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fillValue(v, map, k)]));
  }
  return value;
}

/** 把 token 换成前奏解析出来的真实值（两边都用这一份）。 */
function fillPlan(plan, vals) {
  const map = {
    '@@PAPER_ID@@': vals.paperId,
    '@@CODE@@': vals.code,
    '@@STUDENT_ID@@': vals.studentId,
    '@@PRACTICE_SEED@@': vals.practiceSeed,
    '@@PREFIX@@': vals.prefix,
    '@@SEED@@': SEED,
  };
  return fillValue(plan, map);
}

async function probeMock(port, prefix) {
  const base = `http://127.0.0.1:${port}`;
  const out = {};

  // 与真实侧**完全同一份前奏**：建卷 → 发布 → 用返回的卷号/id 继续。
  // （不在这里再报一次到：报到已放进 requestPlan，前奏里再报会把序列里那条变成重放。）
  const created = await call(base, 'POST', `/api/papers?seed=${SEED}&title=%E6%AF%94%E5%AF%B9%E5%8D%B7`, CONFIG);
  out.create_paper = created;
  const paperId = created.body?.id;

  const pub = await call(base, 'POST', `/api/papers/${paperId}/publish`, undefined);
  out.publish = pub;
  const code = pub.body?.code;

  const studentId = 9001;
  const practice = await call(base, 'POST', `/api/student/practice?seed=${SEED}`, CONFIG);
  const practiceSeed = practice.body?.seed ?? SEED;

  // 报到**必须先于**交卷：真实后端校验「学生存在」，用没报过到的 id 交卷会 422。
  // 所以这里先报一次拿到 id，再把它填进计划（序列里那条 `students` 仍会再报一次 —
  // 那是用来验 201→200 幂等语义的，不是拿来取 id 的）。
  const reg = await call(base, 'POST', '/api/students', STUDENT);
  const realStudentId = reg.body?.student_id ?? studentId;

  out.student_paper_code = code;
  out.student_paper_id = paperId;
  out.student_id = realStudentId;
  out.practice_seed = practiceSeed;

  const plan = fillPlan(requestPlan(), { paperId, code, studentId: realStudentId, practiceSeed, prefix });
  for (const entry of plan) {
    const [name, method, url] = entry;
    const body = entry.length > 3 ? entry[3] : undefined;
    out[name] = await call(base, method, url, body);
  }
  return out;
}

// ---------------------------------------------------------------------------
// 真实侧：TestClient 跑同一份请求序列（由 Python 落地成 JSON）
// ---------------------------------------------------------------------------

const REAL_PY = String.raw`
import json, sys, traceback
sys.path.insert(0, r"@@REPO@@/backend")
sys.path.insert(0, r"@@REPO@@/backend/tests")

CONFIG = json.loads(r'''@@CONFIG@@''')
SEED = @@SEED@@
PREFIX = "@@PREFIX@@"
PLAN = json.loads(r'''@@PLAN@@''')
out = {}

def rec(name, resp):
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:200]
    out[name] = {"status": resp.status_code, "body": body}

def fill(value, vals, key=None):
    """把 @@TOKEN@@ 换成前奏解析出来的真实值 —— 与 JS 侧同一份规则。

    数字型字段（student_id / paper_id / seed）替换后必须**还原成 int**：
    真实后端用 pydantic 校验，收到字符串 "9001" 会回 422，而 mock 是手写解析、会照收，
    于是两边一个 422 一个成功 —— 那种差异是探针自己造的，不是接口漂移。
    """
    if isinstance(value, str):
        ORIGINAL = value
        for token, real in vals.items():
            value = value.replace(token, str(real))
        if AT in value and "9999-9999" not in value:
            raise RuntimeError("请求计划里还有没替换的占位符：%s" % value)
        # 只有「本来是 token、被换成了数字串」的才还原成 int；true JSON 整数不动。
        if key in NUMERIC_KEYS and AT in ORIGINAL:
            return int(value)
        return value
    if isinstance(value, dict):
        return {k: fill(v, vals, k) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, vals) for v in value]
    return value

try:
    from tests.api._support import make_client
    with make_client() as client:
        r = client.post("/api/papers", params={"seed": SEED, "title": "比对卷"}, json=CONFIG)
        rec("create_paper", r)
        paper_id = r.json().get("id")
        pub = client.post("/api/papers/%s/publish" % paper_id)
        rec("publish", pub)
        code = pub.json().get("code")
        out["student_paper_code"] = code
        out["student_paper_id"] = paper_id

        prac = client.post("/api/student/practice", params={"seed": SEED}, json=CONFIG)
        practice_seed = prac.json().get("seed")

        # 报到**必须先于**交卷：真实后端校验「学生存在」，没报过到的 id 交卷会 422。
        # 用 PLAN 里 students 那条的 body，避免在 Python 里再抄一份学生身份。
        STUDENT = [e for e in PLAN if e[0] == "students"][0][3]
        reg = client.post("/api/students", json=STUDENT)
        student_id = reg.json().get("student_id")

        # PLAN 由 JS 侧传**带 token 的模板**，这里用本侧前奏拿到的真实值填。
        # 坑：这份脚本整体要过一轮 replaceAll，所以**代码里不能出现完整 token 字面量**
        # （否则那一轮会把字典的键改写成值，症状是「计划里还有没替换的占位符」）。
        # 用 AT + 名字 + AT 现场拼出来，模板里就不存在完整的 @@…@@ 序列。
        AT = "@" * 2
        NUMERIC_KEYS = ("student_id", "paper_id", "seed")
        def tok(name):
            return AT + name + AT
        vals = {tok("PAPER_ID"): paper_id, tok("CODE"): code, tok("STUDENT_ID"): student_id,
                tok("PRACTICE_SEED"): practice_seed, tok("PREFIX"): PREFIX, tok("SEED"): SEED}
        out["student_id"] = student_id
        out["practice_seed"] = practice_seed
        for entry in PLAN:
            name, method, url = entry[0], entry[1], entry[2]
            body = entry[3] if len(entry) > 3 else None
            url, body = fill(url, vals), fill(body, vals)
            if method == "GET":
                rec(name, client.get(url))
            else:
                rec(name, client.post(url, json=body))
except Exception:
    out["__error__"] = traceback.format_exc()
with open(r"@@OUT@@", "w", encoding="utf-8", newline="\n") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
`;

function probeReal(outFile, prefix) {
  const script = REAL_PY
    .replaceAll('@@REPO@@', REPO.replace(/\\/g, '/'))
    .replaceAll('@@CONFIG@@', JSON.stringify(CONFIG))
    .replaceAll('@@SEED@@', String(SEED))
    .replaceAll('@@PREFIX@@', prefix)
    .replaceAll('@@STUDENT@@', JSON.stringify(STUDENT))
    // 注入**带 token 的模板**（只有它能留 token —— 上面几轮 replaceAll 不认 PLAN 里的名字）
    .replaceAll('@@PLAN@@', JSON.stringify(requestPlan()))
    .replaceAll('@@OUT@@', outFile.replace(/\\/g, '/'));
  const res = spawnSync(PYTHON, ['-c', script], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONPATH: path.join(REPO, 'backend') },
  });
  if (!fs.existsSync(outFile)) {
    throw new Error(`真实侧没有产出 ${outFile}\nstdout: ${res.stdout}\nstderr: ${res.stderr}`);
  }
  const parsed = JSON.parse(fs.readFileSync(outFile, 'utf8'));
  if (parsed.__error__) throw new Error(`真实侧抛错：\n${parsed.__error__}`);
  return parsed;
}

// ---------------------------------------------------------------------------
// 比对与报告
// ---------------------------------------------------------------------------

/** mock 侧没有 POST /api/papers（它预置卷库），这一项只单向检查真实侧形状。 */
const MOCK_ONLY = new Set(['create_paper_unsupported']);

/**
 * 语义不变量（**不是**两侧对称比对能替代的）。
 *
 * 对称比对只会说「两边一样」。如果哪天有人把 mock 与真实两侧**同时**改成
 * 「学生端取卷带答案」或「老师端详情丢答案」，对称比对仍然全绿 —— 而这正是
 * D74 要防的事：学生端取卷必须**不含** `answer`（学生不该在取卷时拿到答案），
 * 老师端详情必须**含** `answer`（老师要改题、要看答案）。
 *
 * 所以这里做**方向相反的两条**硬断言，与两侧是否一致无关：
 *   - 学生端 `GET /api/student/paper/{code}` 的 items 每条**不得**含 answer/steps
 *   - 老师端 `GET /api/papers/{id}` 的 items 每条**必须**含 answer/steps
 * 两侧任一侧违反都直接判红，并点名是哪一侧、哪个字段。
 */
function checkSemanticInvariants(mock, real) {
  const problems = [];
  const itemsOf = (entry) => (entry && entry.body && Array.isArray(entry.body.items) ? entry.body.items : null);
  const LEAF = (item) => (item && typeof item === 'object' && !Array.isArray(item) ? item : null);

  for (const [side, out] of [['mock', mock], ['真实', real]]) {
    const studentItems = (itemsOf(out.student_paper) ?? []).map(LEAF).filter(Boolean);
    if (studentItems.length > 0) {
      for (const leaked of ['answer', 'steps']) {
        if (studentItems.some((item) => leaked in item)) {
          problems.push(`D74 违反（${side}侧）：学生端取卷的 item 里出现了 \`${leaked}\` —— 学生不该在取卷时拿到答案`);
        }
      }
    }

    const teacherItems = (itemsOf(out.detail) ?? []).map(LEAF).filter(Boolean);
    if (teacherItems.length > 0) {
      for (const required of ['answer', 'steps']) {
        if (!teacherItems.every((item) => required in item)) {
          problems.push(`D74 反向违反（${side}侧）：老师端详情的 item 缺少 \`${required}\` —— 老师端详情不能也被剥掉答案`);
        }
      }
    }
  }
  return problems;
}

function compare(mock, real) {
  const names = Object.keys(real).filter((k) => !k.startsWith('__') && !k.endsWith('_seed') && k !== 'student_id');
  const rows = [];
  let failed = 0;
  // 语义不变量先跑：它不管两侧是否一致，只管「方向对不对」
  const invariantProblems = checkSemanticInvariants(mock, real);
  if (invariantProblems.length > 0) {
    rows.push({ name: '语义不变量（D74 方向）', status: '—', problems: invariantProblems });
    failed += 1;
  } else {
    rows.push({ name: '语义不变量（D74 方向：学生端无答案 / 老师端有答案）', status: '—', problems: [] });
  }  for (const name of names) {
    if (!(name in mock)) {
      rows.push({ name, status: '—', problems: [`mock 根本没有这条用例`] });
      failed += 1;
      continue;
    }
    const m = mock[name];
    const r = real[name];
    const problems = [];
    if (MOCK_ONLY.has(name)) {
      problems.push(`（仅真实侧有：${r.status}，mock 无此路由）`);
      rows.push({ name, status: `${m.status} / ${r.status}`, problems, informational: true });
      continue;
    }
    if (!m || typeof m !== 'object' || !('status' in m)) continue; // 非响应条目（如 code/id）
    if (m.status !== r.status) problems.push(`状态码 mock=${m.status}，真实=${r.status}`);
    problems.push(...diffSignatures(m.body, r.body));
    rows.push({ name, status: `${m.status} / ${r.status}`, problems });
    if (problems.length > 0) failed += 1;
  }
  return { rows, failed };
}

// 覆盖面下限（实测 rows = 16）。存在的唯一理由是堵住一类**结构性假绿**：
// 若真实侧写出一个合法但空的 `{}`（例如将来有人加了一个提前 return / sys.exit(0)），
// `compare()` 会算出 0 行、0 差异，然后打印「逐条一致。退出码 0」—— 一个**什么都没比过**的绿。
// 判据对「没得比」与「比过且一致」必须给出不同结论（V11），所以这里显式要求被比较的行数
// 达到请求计划量级，而不是只看 failed === 0。
// 故障注入开关：**只可能让结论变红，不可能让它变绿**，因此不可能被用来削弱门禁。
// 生产路径永不设置它；只有 verify-negative-controls.mjs 第 9 组用它逼出「覆盖面不足」
// 这条分支，证明这条判据真的会红（V11：不可达的分支等于没测的分支）。
const FAULT = process.env.MATHGEN_PARITY_FAULT ?? '';

const MIN_ROWS = FAULT === 'coverage' ? 9999 : 12;

function printComparison(mock, real) {
  const { rows, failed } = compare(mock, real);
  console.log('被比对字段路径（mock 侧签名，真实侧同构即视为一致）：');
  const firstReal = real.student_paper ?? real.detail;
  if (firstReal) {
    for (const [key, type] of signature(firstReal.body)) console.log(`  ${key}: ${type}`);
  }
  console.log('');
  for (const row of rows) {
    const flag = row.informational ? '··' : row.problems.length === 0 ? 'PASS' : 'FAIL';
    console.log(`[${flag}] ${row.name}（mock / 真实 状态码 = ${row.status}）`);
    for (const p of row.problems) console.log(`        ${p}`);
  }
  const tooFew = rows.length < MIN_ROWS;
  if (tooFew) {
    console.log(
      `[FAIL] 覆盖面不足：只比较了 ${rows.length} 行（下限 ${MIN_ROWS}${
        FAULT ? `，故障注入 MATHGEN_PARITY_FAULT=${FAULT}` : ''
      }）—— 「没得比」不等于「比过且一致」`,
    );
  }
  return { rows, failed, tooFew };
}

async function runOnce({ tamperEnv = {}, prefix, keepReal = false }) {
  fs.mkdirSync(TMP, { recursive: true });
  const realFile = path.join(TMP, `real-${prefix}.json`);
  const mock = await startMock(tamperEnv);
  try {
    const mockOut = await probeMock(mock.port, prefix);
    // 真实侧建卷后才能知道自己的 paper_id，但请求序列里用到的都从各自响应里取，
    // 所以两侧顺序一致、形状可比。
    const realOut = probeReal(realFile, prefix);
    if (!keepReal) fs.rmSync(realFile, { force: true });
    return { mockOut, realOut };
  } finally {
    mock.child.kill();
  }
}

async function main() {
  if (!fs.existsSync(PYTHON)) {
    console.error(`找不到后端 venv 的 python：${PYTHON}`);
    process.exit(2);
  }

  if (DUMP_REAL_ONLY) {
    fs.mkdirSync(TMP, { recursive: true });
    const realOut = probeReal(path.join(TMP, 'real-dump.json'), 'dump');
    console.log(JSON.stringify(realOut, null, 1));
    return;
  }

  if (!SELF_TEST) {
    const { mockOut, realOut } = await runOnce({ prefix: 'main' });
    const { failed, tooFew } = printComparison(mockOut, realOut);
    console.log('');
    if (failed > 0 || tooFew) {
      console.log(
        `结果：mock 与真实后端有 ${failed} 条不一致${tooFew ? '，且覆盖面不足（见上面的 FAIL 行）' : ''} —— 退出码 1。`,
      );
      process.exit(1);
    }
    console.log('结果：mock 与真实后端的结构、状态码逐条一致。退出码 0。');
    return;
  }

  // ---- 自检：改坏 mock → 必须红且指出字段名；改回 → 必须绿 ----
  console.log('=== 自检 A：原样（预期绿）===');
  const clean = await runOnce({ prefix: 'selftest-clean' });
  const cleanResult = printComparison(clean.mockOut, clean.realOut);
  console.log(`clean.failed = ${cleanResult.failed}`);

  console.log('');
  console.log('=== 自检 B：把 mock 的 `items[].answer` 删掉（预期红，且必须点名 answer）===');
  const broken = await runOnce({
    prefix: 'selftest-broken',
    tamperEnv: { MATHGEN_MOCK_TAMPER_DELETE: 'items[].answer' },
  });
  const brokenResult = printComparison(broken.mockOut, broken.realOut);
  console.log(`broken.failed = ${brokenResult.failed}`);

  const brokenMessages = brokenResult.rows.flatMap((r) => r.problems).join('\n');
  const namesAnswer = brokenMessages.includes('answer');

  // ---- 自检 C：方向不变量单独自检（合成响应，不依赖真实后端）----
  // 为什么必须单独做：A/B 两条都是**对称**比对，只能证明「两侧一样」。
  // 如果有人把 mock 与真实两侧**同时**改成「学生端带答案」或「老师端丢答案」，
  // 对称比对依旧全绿，而 D74 已经被破坏。C 用合成响应直接钉这两条方向。
  const fakeItem = (extra) => ({ seq: 1, display: '1 + 1', question_type: 'oral', ...extra });
  const withAnswer = { body: { total: 1, items: [fakeItem({ answer: '2', steps: [] })] } };
  const withoutAnswer = { body: { total: 1, items: [fakeItem({})] } };
  const directionCases = [
    {
      name: '方向 C1：学生端取卷**含** answer（D74 泄漏）→ 必须红',
      out: { student_paper: withAnswer, detail: withAnswer },
      expectRed: true,
      expectField: 'answer',
      expectSide: '学生端取卷',
    },
    {
      name: '方向 C2：老师端详情**丢** answer（反向）→ 必须红',
      out: { student_paper: withoutAnswer, detail: withoutAnswer },
      expectRed: true,
      expectField: 'answer',
      expectSide: '老师端详情',
    },
    {
      name: '方向 C3：学生端无答案 + 老师端有答案（正确方向）→ 必须绿',
      out: { student_paper: withoutAnswer, detail: withAnswer },
      expectRed: false,
    },
  ];

  console.log('');
  console.log('=== 自检 C：D74 方向不变量（合成响应，不与真实后端对称比对）===');
  let directionOk = true;
  for (const c of directionCases) {
    const problems = checkSemanticInvariants(c.out, c.out);
    const isRed = problems.length > 0;
    const flagged = problems.join(' ');
    const named = !c.expectField || flagged.includes(c.expectField);
    const sided = !c.expectSide || flagged.includes(c.expectSide);
    const pass = isRed === c.expectRed && (!c.expectRed || (named && sided));
    if (!pass) directionOk = false;
    console.log(`  [${pass ? 'PASS' : 'FAIL'}] ${c.name}`);
    console.log(`        问题数=${problems.length}${problems.length ? ` 具名=${JSON.stringify(problems)}` : ''}`);
  }

  console.log('');
  console.log('=== 自检结论 ===');
  console.log(`原样时 failed = ${cleanResult.failed}（要求 0）`);
  console.log(`原样时被比较行数 = ${cleanResult.rows.length}（要求 >= ${MIN_ROWS}，防「没得比」被当成「一致」）`);
  console.log(`改坏后 failed = ${brokenResult.failed}（要求 > 0）`);
  console.log(`改坏后输出里点名了 answer 字段：${namesAnswer}（要求 true）`);
  console.log(`方向不变量自检：${directionOk ? '三条全部如期' : '有不如期的用例'}（要求 true）`);
  const ok =
    cleanResult.failed === 0 &&
    !cleanResult.tooFew &&
    brokenResult.failed > 0 &&
    namesAnswer &&
    directionOk;
  console.log(ok ? '结果：自检通过 —— 比对抓得住「少一个键」，方向不变量也抓得住「两边一起错」。' : '结果：自检不通过。');
  process.exit(ok ? 0 : 1);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(2);
});
