/**
 * 验收脚本的服务器进程（**独立进程**，不是被调用者）。
 *
 * 为什么必须独立进程：`verify-layout-css-vars.mjs` 用 `spawnSync` 驱动 Playwright，
 * 那会阻塞 Node 的事件循环；如果 HTTP 服务跑在同一个进程里，
 * 服务虽然 `listen()` 了，但 `connection` 事件永远派发不到（实测：Chromium 连接超时，
 * 服务端一条请求日志都没有）。所以服务与浏览器驱动必须分进程。
 *
 * 提供的两条服务：
 *   :4173  frontend/dist 静态产物 + `/api/*` 反代（模拟 vite dev 的 proxy 语义）
 *   :8123  模拟后端（JSON 形状严格照 backend/app/contracts/api.py）
 *
 * 用法：node scripts/verify-server.mjs
 * 就绪后往 stdout 打印 `VERIFY_SERVER_READY`，日志写在 `.tmp-playwright/verify-server.log`。
 */

import { createServer, request as httpRequest } from 'node:http';
import { readFile, stat, appendFile } from 'node:fs/promises';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const DIST_DIR = path.join(FRONTEND_DIR, 'dist');

const TMP_DIR = path.join(FRONTEND_DIR, '.tmp-playwright');
mkdirSync(TMP_DIR, { recursive: true });
const SERVER_LOG = path.join(TMP_DIR, 'verify-server.log');

export const STATIC_PORT = Number(process.env.MATHGEN_VERIFY_STATIC_PORT ?? 4173);
export const API_PORT = Number(process.env.MATHGEN_VERIFY_API_PORT ?? 8123);

/**
 * 验收场景开关（供 verify-teacher-ui.mjs 模拟「后端未就绪」）：
 *   online / 缺省 —— 模拟后端正常应答
 *   proxy-down    —— **不启动**模拟后端，静态服务的 /api 反代必然 ECONNREFUSED → 502
 *   api-down      —— 模拟后端在，但卷详情返回 500（后端半死不活）
 */
const MODE = process.env.MATHGEN_VERIFY_MODE ?? 'online';

function log(line) {
  appendFile(SERVER_LOG, `${new Date().toISOString()} ${line}\n`).catch(() => {});
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.json': 'application/json; charset=utf-8',
  '.woff2': 'font/woff2',
};

function json(res, status, body) {
  // 负对照开关：默认（两个环境变量都未设）`applyTamper` 原样返回，零影响。
  const payload = JSON.stringify(applyTamper(body));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

/**
 * 204：**无响应体**。
 *
 * 为什么单独有这个函数、而不是写 `json(res, 204, {})`：后者是「有响应体的 204」，
 * 而真实后端 `DELETE /api/papers/{id}` 是显式 `Response(status_code=204)`（空体）。
 * 逐字段比对里「空体」与「一个空对象」是两种形状 —— 用同一个函数就分不出来了。
 */
function noContent(res) {
  res.writeHead(204);
  res.end();
}

// ---------------------------------------------------------------------------
// 故意改坏一条 mock 响应的开关（负对照用，默认完全不生效）
// ---------------------------------------------------------------------------
//
// **为什么需要它**：`frontend/scripts/compare-mock-and-real.mjs` 的职责是
// 「拿 mock 与真实后端逐字段比」；要证明这个比对**抓得住东西**，就必须能
// 造出「mock 与真实不一致」的状态。但那个脚本不该去改写本文件（会留下脏改动，
// 也可能被并发写入撞掉），所以改成：**本文件提供一个只读环境变量驱动的开关**，
// 由它自己、在内存里、只在响应体上删字段 —— 默认（两个变量都未设）零影响。
//
//   MATHGEN_MOCK_TAMPER_DELETE   逗号分隔的字段路径，`[]` 表示数组每个元素
//                                （例：`items[].answer`）
//   MATHGEN_MOCK_TAMPER_ANY      同上，但按后缀匹配（用于字段在哪个层级不确定时）
//
// 与上面 `assertNoTamper`（检测）方向相反：这个是**制造**差异。两者都在测试期才有意义。
const TAMPER_DELETE = (process.env.MATHGEN_MOCK_TAMPER_DELETE ?? '')
  .split(',').map((s) => s.trim()).filter(Boolean);
const TAMPER_DELETE_ANY = (process.env.MATHGEN_MOCK_TAMPER_ANY ?? '')
  .split(',').map((s) => s.trim()).filter(Boolean);

/** 路径里每个 `[]` 都表示「对数组的每个元素再走一遍」。 */
function splitWildcards(spec) {
  const parts = [];
  for (const raw of spec.split('.')) {
    const m = raw.match(/^([^[\]]*)((\[\])*)$/);
    const name = m ? m[1] : raw;
    const depth = m ? (m[2].match(/\[\]/g) ?? []).length : 0;
    parts.push({ name, depth });
  }
  return parts;
}

/**
 * 按路径删字段。`[]` 表示「对数组的每个元素再走一遍」。
 *
 * **这个函数修过一次真 bug（2026-10-15）**：旧版对最后一段的处理是错的 ——
 * `tamperWalk` 在 `isLast` 分支里执行 `delete target[name]`，而 `name` 是**当前**
 * 路径段的名字、`target` 却是 `node[name]`（上一段取下来的数组元素）。
 * 于是 `items[].answer` 变成「对每个 item 执行 delete item['answer']」——看着对，
 * 其实 `parts` 走到最后一段时 `targets` 已经是**数组本身**，循环里 `target` 是每个
 * 元素，`delete target['answer']` 删的就是**正确字段**；但 depth>0 的那一段
 * （`items`）在 `isLast=false` 时走的是 `tamperWalk(target[name], …)`，
 * 把**数组**当 node 传进去，下一段 `answer` 的 `targets=[node]` 里 `node` 是数组、
 * `node['answer']` 是 undefined —— 结果删掉的是数组里**恰好排在前面的** `steps`
 * （`delete` 一个不存在的键本应无操作，但循环体当时作用在同一层，把兄弟键删了）。
 *
 * 实证：`MATHGEN_MOCK_TAMPER_DELETE=items[].answer` 打出的响应里 `answer` 仍在、
 * `steps` 消失；而 `compare-mock-and-real.mjs --self-test` 正是**断言它必须点名
 * answer** —— 这条负对照当场抓出了自己工具里的错。修法是按「前缀展开」重写：
 * 每处理一段就用它的 depth 展开 targets，再把**剩下的路径段**递归到这些 target 上。
 */
function tamperWalk(node, parts) {
  if (node === null || typeof node !== 'object' || parts.length === 0) return;
  const [first, ...rest] = parts;
  // depth>0：取 node[名字] 这个**数组**，逐个元素继续（名字已被消费掉）。
  // depth=0：**名字尚未被消费**，先取 node[名字]，最后一段才删它。
  const targets = first.depth > 0
    ? (Array.isArray(node[first.name]) ? node[first.name] : [])
    : [node];
  for (const target of targets) {
    if (target === null || typeof target !== 'object') continue;
    if (rest.length === 0) {
      delete target[first.name];
    } else {
      // 下一段作用在**名字所指的那个对象**上（不是再往下取一层）
      tamperWalk(first.depth > 0 ? target : target[first.name], rest);
    }
  }
}

/** 数组删空后留下 `[]` 会干扰差异阅读（真实那边有元素），故把空容器剪掉。 */
function pruneEmpty(node) {
  if (node === null || typeof node !== 'object') return undefined;
  if (Array.isArray(node)) {
    for (let i = node.length - 1; i >= 0; i -= 1) {
      if (node[i] !== null && typeof node[i] === 'object' && pruneEmpty(node[i]) === 'empty') {
        delete node[i];
        node.splice(i, 1);
      }
    }
    return node.length === 0 ? 'empty' : undefined;
  }
  for (const key of Object.keys(node)) {
    if (node[key] !== null && typeof node[key] === 'object' && pruneEmpty(node[key]) === 'empty') {
      delete node[key];
    }
  }
  return Object.keys(node).length === 0 ? 'empty' : undefined;
}

function applyTamper(body) {
  if (TAMPER_DELETE.length === 0 && TAMPER_DELETE_ANY.length === 0) return body;
  const clone = JSON.parse(JSON.stringify(body));
  for (const spec of TAMPER_DELETE) tamperWalk(clone, splitWildcards(spec));
  for (const spec of TAMPER_DELETE_ANY) {
    // 后缀匹配：遍历所有对象，命中同名键就删
    const leaf = spec.split('.').pop();
    const stack = [clone];
    while (stack.length > 0) {
      const node = stack.pop();
      if (node === null || typeof node !== 'object') continue;
      if (!Array.isArray(node) && Object.prototype.hasOwnProperty.call(node, leaf)) delete node[leaf];
      for (const value of Object.values(node)) {
        if (value !== null && typeof value === 'object') stack.push(value);
      }
    }
  }
  pruneEmpty(clone);
  return clone;
}

// ---------------------------------------------------------------------------
// 模拟后端：形状照 backend/app/contracts/api.py（字段 snake_case）
// ---------------------------------------------------------------------------

const MOCK_PAPER = {
  id: 1,
  code: null,
  title: '三位数乘两位数 随堂练',
  status: 'draft',
  seed: 20261014,
  config: {
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
  },
  layout: {
    cols_per_row: 4,
    row_gap: 1.0,
    font_size: 14,
    page_margin: 2.0,
    answer_line: true,
    header_bar: true,
  },
  created_at: '2026-10-14T20:00:00+08:00',
  published_at: null,
  // D78：归档时间。`PaperDetail` 有这一键（**没有** `has_attempts` —— 那是
  // `PaperSummary` 独有的，只给列表页决定「删除」能不能点）。
  archived_at: null,
  // M3：第六维答案版形式（`paper.answer_layout`），四字段与 AnswerLayoutIn 一致
  answer_layout: {
    separate_page: true,
    include_steps: true,
    include_difficulty: true,
    include_tags: true,
  },
  items: [
    ['243 × 12', '2916', 2],
    ['187 × 34', '6358', 3],
    ['356 × 21', '7476', 2],
    ['129 × 45', '5805', 4],
    ['275 × 13', '3575', 3],
    ['408 × 26', '10608', 4],
  ].map(([expr, answer, difficulty], index) => ({
    seq: index + 1,
    display: expr,
    expr,
    answer,
    difficulty,
    tags: ['三位数乘两位数', '进位'],
    steps: [],
    origin: 'generated',
  })),
};

/** 请求流水：验收脚本靠它判断「调参期间有没有读卷」和「改题打到哪条路径」。 */
const apiLog = [];

/**
 * 卷库用的**多卷数据集**（M3 分页 / 搜索 / 筛选 / 按卷号打开需要足够样本）。
 *
 * 共 37 套：前 20 套已发布（有卷号 `2610-NNNN`），后 17 套是草稿（无卷号）。
 * 标题里混入「三位数」/「两位数」关键字，便于断言搜索过滤真的生效。
 *
 * **id 从 100 起**，刻意避开 `MOCK_PAPER.id = 1`：否则 `/teacher/paper/1` 会被这份
 * 数据集覆盖成「已发布」，把其它门禁全带偏（实测踩到：卷页面变只读，连 reset 都找不到）。
 */
const LIBRARY_ID_BASE = 100;
const MOCK_LIBRARY = Array.from({ length: 37 }, (_, index) => {
  const seq = index + 1;
  const published = seq <= 20;
  return {
    id: LIBRARY_ID_BASE + index,
    code: published ? `2610-${String(seq).padStart(4, '0')}` : null,
    // 两类标题**互不包含**（关键字只出现在一类里），这样搜索过滤才有可判定的效果：
    // 「两位数乘一位数」不含「三位数乘两位数」，反之亦然。
    title: seq % 3 === 0 ? `两位数乘一位数 练习 ${seq}` : `三位数乘两位数 练习 ${seq}`,
    status: published ? 'published' : 'draft',
    item_count: 10 + (seq % 5),
    created_at: `2026-10-${String(10 + (seq % 20)).padStart(2, '0')}T09:${String(seq % 60).padStart(2, '0')}:00+08:00`,
    published_at: published ? `2026-10-${String(10 + (seq % 20)).padStart(2, '0')}T10:00:00+08:00` : null,
    // D78：这两个键是 `PaperSummary` 的一部分（契约 §5.1）。
    // 预置的 37 套**全部未归档**，所以默认视图（`archived=exclude`）看到的仍是原来那 37 套，
    // 现有分页 / 搜索判据的「总数」断言不会被这轮改动动到。
    archived_at: null,
    // 第 6 套刻意标成「已有学生成绩」：界面上「删除」必须**事先禁用**并写明人话理由，
    // 这一行就是那条判据的可见样本。选第 6 套而不是第 1 套，是因为第 1 套
    // （id 100 / 卷号 2610-0001）是学生端取卷逐键比对的样本，保持它不带额外语义。
    has_attempts: seq === 6,
  };
});

/**
 * 已归档的卷（D78）—— **单独追加在尾部**，不改变上面 37 套的既有断言。
 *
 * 为什么必须真的预置几套，而不是「只加归档路由就够了」：默认视图是
 * `archived=exclude`，如果把归档行混进那 37 套里，「默认看不到归档」这条判据
 * 就会和「总数仍是 37」互相打架。分开之后两种情况都能被断言：
 * 默认视图 37 套、`archived=only` 视图 3 套、`archived=include` 视图 40 套。
 *
 * 这三套都用 `2610-00NN` 卷号（延续已有编号规则），状态保持 `published` ——
 * 归档与 `status` **正交**，归档不改状态，所以列表里「已发布」与「已归档」会同时出现。
 */
const MOCK_ARCHIVED = Array.from({ length: 3 }, (_, index) => {
  const seq = 38 + index;
  const day = String(10 + (seq % 20)).padStart(2, '0');
  return {
    id: LIBRARY_ID_BASE + 37 + index,
    code: `2610-${String(seq).padStart(4, '0')}`,
    // 标题里带上「两位数」是为了让**搜索与归档两个筛选条件可以叠加**被断言
    // （seq=39 时 `seq % 3 === 0`，与上面 37 套的标题规则一致）。
    title: seq % 3 === 0 ? `两位数乘一位数 练习 ${seq}` : `三位数乘两位数 练习 ${seq}`,
    status: 'published',
    item_count: 10 + (seq % 5),
    created_at: `2026-10-${day}T09:00:00+08:00`,
    published_at: `2026-10-${day}T10:00:00+08:00`,
    archived_at: '2026-10-15T12:00:00+08:00',
    has_attempts: false,
  };
});
MOCK_LIBRARY.push(...MOCK_ARCHIVED);

/** 列表里的每套卷都能按 ID / 卷号打开；`MOCK_PAPER`（id=1，草稿）保持原样 */
const MOCK_DETAILS = new Map();
MOCK_DETAILS.set(MOCK_PAPER.id, MOCK_PAPER);
for (const summary of MOCK_LIBRARY) {
  // `has_attempts` 只属于 `PaperSummary`（列表页用它决定「删除」能不能点）；
  // `PaperDetail` 里**没有**这一键，卷详情页也不需要知道成绩数量 —— 所以这里必须剥掉。
  // 若照抄进详情，`GET /api/papers/{id}` 会多出一个真实后端没有的键，
  // 逐字段比对（门禁第 ⑫ 步）就会报一条**并不存在的**「差异」，把真差异淹掉。
  // `archived_at` 相反：`PaperDetail` 有这个键，必须保留。
  const base = { ...summary };
  delete base.has_attempts;
  MOCK_DETAILS.set(summary.id, {
    ...base,
    seed: 1000 + summary.id,
    config: { ...MOCK_PAPER.config, count: summary.item_count },
    layout: { ...MOCK_PAPER.layout },
    answer_layout: { ...MOCK_PAPER.answer_layout },
    // 卷库里只放前两题：老师端列表页的判据不关心题量，题少能让页面短一些。
    // **注意**：`2610-0001`（第一套）的学生端取卷要走**完整**题量——学生端要渲染
    // 整份卷子，题量与真实后端一致才能逐键比对（见下面 COVERED_BY_STUDENT 的说明）。
    items: MOCK_PAPER.items.slice(0, 2),
  });
}

// 学生端取卷的**唯一**被覆盖卷号：真实后端同 config 同 seed 生成 6 题、发布后卷号
// 恰好也是 `2610-0001`，所以两边题量一致 → 「mock 形状是否照抄真实」这件事可以被
// 逐键比对脚本客观证明（`frontend/.tmp-capture/compare-mock-and-real.mjs`）。
// 其余卷号仍走上面那两题的构造：它们的学生端形状由这一套代表，不必套套都 6 题。
const STUDENT_PAPER_CODE = '2610-0001';
const studentPaperDetail = MOCK_DETAILS.get(LIBRARY_ID_BASE);
if (studentPaperDetail) {
  studentPaperDetail.items = MOCK_PAPER.items.map((item) => ({ ...item }));
  studentPaperDetail.item_count = studentPaperDetail.items.length;
}

/**
 * 归档筛选（D78 / 契约 `PaperListOut`）。
 *
 * `archived` 三态：`exclude`（**默认**，只看在用）/ `include`（两档都要）/ `only`（只看已归档）。
 * 默认就是 `exclude` —— 这正是「归档 = 软删除」的全部意义：收起来的东西默认不再出现在眼前。
 *
 * 判据写成 `!paper.archived_at` 而不是 `=== null`：预置数据显式写了 `null`，
 * 但 `POST /api/papers` 之后 push 进列表的摘要也要走同一条路。用真值判断可以少一个
 * 「新增字段忘了补 → 卷凭空从列表消失」的机会；缺字段按「未归档」处理，与后端默认值一致。
 */
function applyArchivedFilter(rows, url) {
  const archived = url.searchParams.get('archived') ?? 'exclude';
  if (archived === 'include') return rows;
  if (archived === 'only') return rows.filter((paper) => Boolean(paper.archived_at));
  return rows.filter((paper) => !paper.archived_at);
}

/** 卷库列表：契约形态（回显 limit/offset） */
function listPapers(url) {
  const limit = Number(url.searchParams.get('limit') ?? 20);
  const offset = Number(url.searchParams.get('offset') ?? 0);
  const q = (url.searchParams.get('q') ?? '').trim();
  const status = url.searchParams.get('status') ?? '';
  let rows = applyArchivedFilter(MOCK_LIBRARY, url);
  if (q !== '') rows = rows.filter((paper) => paper.title.includes(q));
  if (status === 'draft' || status === 'published') {
    rows = rows.filter((paper) => paper.status === status);
  }
  return { total: rows.length, limit, offset, papers: rows.slice(offset, offset + limit) };
}

/** 历史形态（只有 total + papers，靠 page/size）—— 用来验证前端形状自适应回退 */
function listPapersLegacy(url) {
  const size = Number(url.searchParams.get('size') ?? 20);
  const page = Number(url.searchParams.get('page') ?? 1);
  const q = (url.searchParams.get('q') ?? '').trim();
  const status = url.searchParams.get('status') ?? '';
  let rows = applyArchivedFilter(MOCK_LIBRARY, url);
  if (q !== '') rows = rows.filter((paper) => paper.title.includes(q));
  if (status === 'draft' || status === 'published') {
    rows = rows.filter((paper) => paper.status === status);
  }
  const start = (page - 1) * size;
  return { total: rows.length, papers: rows.slice(start, start + size) };
}

function findDetailByCode(code) {
  for (const paper of MOCK_DETAILS.values()) {
    if (paper.code === code) return paper;
  }
  return undefined;
}

/**
 * 故意破坏钩子 —— 给 `verify-negative-controls.mjs` 的「mock 契约哨兵」用。
 *
 * 为什么需要它（而不是只写断言）：光写「mock 的响应里有 `answer` 键」这种断言，
 * 我无法证明**这条代码路径真的会被执行**。之前恰恰吃过这个亏：`answer` 整键消失
 * （我把已剥掉答案的 wire 数据存去判分了），而所有断言照样绿。
 *
 * 用法：`MATHGEN_MOCK_TAMPER=<钩子名> node verify-server.mjs` → 打到该钩子就抛错，
 * 服务对这条请求返回 500。负对照套件据此断言「破坏钩子真的能触发」，
 * 从而证明哨兵守着的路径是活的。
 */
const TAMPER = process.env.MATHGEN_MOCK_TAMPER ?? '';

function assertNoTamper(hook) {
  if (TAMPER && TAMPER === hook) {
    throw new Error(`MOCK_TAMPER_ARMED:${hook}（负对照专用：这条路径确实会被执行）`);
  }
}

// ---------------------------------------------------------------------------
// M4 学生端：可变状态 + 三种响应体的构造函数
//
// 状态放在**模块级**而不是每请求新建，因为 D76 幂等与 D75「回传 seed」都是跨请求事实：
// 判据要能断言「同一个 (student_id, attempt_uuid) 交两次只得到一条记录」，
// 状态不跨请求就永远测不到这条。
// ---------------------------------------------------------------------------

/** (name\0student_no) → student_id。真实后端是「取或建」，这里同样按这个键去重。 */
const MOCK_STUDENTS = new Map();
let MOCK_STUDENT_SEQ = 1000;
const MOCK_PRACTICE = new Map();
const MOCK_ATTEMPTS = new Map();
let MOCK_ATTEMPT_SEQ = 0;
//: 建卷/发布用（见 dispatchApi 里新增的两条路由）。建卷 id 从 2000 起，避免与预置卷库
//: （`LIBRARY_ID_BASE` 段）撞号；卷号从 20 起 —— 预置的 published 是 0001–0020。
let MOCK_PAPER_SEQ = 2000;
let MOCK_CODE_SEQ = 20;
//: 与 `app/routers/urls.py` 的 `STUDENT_PAPER_PATH` 一致（发布响应里拼 student_url 用）
const STUDENT_PAPER_PATH = '/student/paper';

/**
 * 与真实后端 `student_service.paper_payload` 同样的三件套：`paper_id` / `title` / `total` / `items`。
 *
 * **D74：`items` 只有 `seq` / `display` / `question_type`，绝不含 `answer` / `steps`。**
 * 这不是省事，是白名单守卫本身——mock 多带一个 answer，前端「误读 answer 当正确答案」
 * 这类越界缺陷就会被 mock 喂着跑而全绿。
 */
function studentPaperPayload(paper) {
  return {
    paper_id: paper.id,
    title: paper.title,
    total: paper.items.length,
    items: paper.items.map((item) => ({
      seq: item.seq,
      display: item.display,
      question_type: 'oral',
    })),
  };
}

/**
 * 造一份**确定性的**自主练习：同 seed + 同 config → 逐字同样的 items。
 *
 * 用 `djb2` 而不是 `Math.random()`：判据要能复跑、要能在负对照里证明「注入真的改了东西」。
 * 算式形状与 `MOCK_PAPER.items` 同族（20 以内加减法），保证学生端渲染路径一致。
 */
function djb2Seed(text) {
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(i)) >>> 0;
  }
  return hash % 1000000;
}

function practicePayload(config, seed) {
  const count = Number.isInteger(config?.count) ? config.count : 10;
  const items = Array.from({ length: count }, (_, index) => {
    // 每道题由 seed + 序号唯一决定，与真实引擎「同种子同题」的口径一致
    const pick = (seed + index * 7919) % 20;
    const first = 1 + (pick % 19);
    const second = 1 + ((pick * 3 + index) % (20 - first));
    const isPlus = (pick + index) % 2 === 0;
    const a = isPlus ? first : Math.max(first, second);
    const b = isPlus ? second : Math.min(first, second);
    return {
      seq: index + 1,
      display: `${a} ${isPlus ? '+' : '-'} ${b} = ____`,
      question_type: 'oral',
      _answer: String(isPlus ? a + b : a - b),
    };
  });
  return {
    // **内部账与 wire 分开返回**：`unwrapped` 带 `_answer`（只有判分要用），
    // `wire` 是不带答案的响应体。第一版我把 `wire` 存进了 `MOCK_PRACTICE`，
    // 于是判分时 `_answer` 已经不存在 → 交卷响应里 `answer` 整键消失，
    // 而其它字段看着都对，判据全绿。逐键比对脚本抓出来的。
    unwrapped: items,
    wire: {
      seed,
      total: items.length,
      items: items.map(({ _answer, ...rest }) => rest),
    },
  };
}

/**
 * 判分：按提交形态二选一取正确答案（契约 §5.2 `AttemptTarget`）。
 *
 * 真实后端的判分走 `app.grading.grade()`（含规范化、等值比较、逐步判定），
 * mock **不重实现那套规则**，只做逐字串比较 —— 因为 mock 的职责是「让前端流程能跑完」，
 * 不是「替后端判分」；判分正确性由后端自己的验收脚本负责。
 */
function gradeAttempt(body) {
  assertNoTamper('submit_attempt');
  if (Number.isInteger(body.paper_id)) {
    const paper = MOCK_DETAILS.get(body.paper_id);
    if (!paper || paper.status !== 'published') return null;
    // 交卷响应里**必须**带 `answer`（真实后端如此）：它是判分结果的一部分，
    // 学生交完卷看到「正确答案 / 你的答案」就靠它。我第一版漏了这键，
    // 是逐键比对脚本抓出来的——mock 少一键会让「学生看不到答案」这类缺陷全绿。
    const expected = new Map(paper.items.map((item) => [item.seq, item.answer]));
    return grade(body, expected, new Map(paper.items.map((item) => [item.seq, item.display])));
  }
  if (body.config && Number.isInteger(body.seed)) {
    const stored = MOCK_PRACTICE.get(body.seed);
    if (!stored) return null;
    const expected = new Map(stored.items.map((item) => [item.seq, item._answer]));
    return grade(body, expected, new Map(stored.items.map((item) => [item.seq, item.display])));
  }
  return null;
}

function grade(body, expected, displays) {
  const submitted = new Map((body.answers ?? []).map((a) => [a.seq, a.answer]));
  const items = [...expected.keys()].sort((a, b) => a - b).map((seq) => {
    const studentAnswer = submitted.get(seq) ?? '';
    return {
      seq,
      display: displays.get(seq) ?? '',
      question_type: 'oral',
      answer: expected.get(seq),
      student_answer: studentAnswer,
      is_correct: String(studentAnswer).trim() === String(expected.get(seq)).trim(),
    };
  });
  return {
    total: items.length,
    correct: items.filter((item) => item.is_correct).length,
    items,
  };
}

/**
 * 模拟 `app/render/sheet.py` 的卷面 HTML。
 *
 * 为什么必须有它：D56 之后**卷面 HTML 的唯一来源是后端**，前端只是 iframe 容器。
 * 验收脚本要断言「面板改 → iframe 文档里的 CSS 变量变」，就必须有一份卷面 HTML。
 * 这里刻意复刻真实渲染产物的关键结构（不是造型）：
 *   - `:root` 内联六个 CSS 变量（变量名取自冻结的 `CSS_VAR_NAMES`）
 *   - **D59**：`answer_line` / `header_bar` 输出 `display` 关键字，且样式表用
 *     `display: var(…, 默认值)` 真正消费它们 → 面板覆盖即生效（这是本次 bug 的修复方式）
 *   - `.sheet[data-mode]`、`.q-item[data-seq]`、`.q-blank`、`.answer-section`
 *   - `@page { size: A4; margin: <具体长度>cm }`（真实实现写具体长度，不用 var()）
 * 真实产物另由真后端验收脚本直接取样校验（断言 `meta generator=mathgen render_sheet`）。
 */
function sheetHtml(paper, mode) {
  const layout = paper.layout;
  const vars = [
    `--cols-per-row: ${layout.cols_per_row}`,
    `--row-gap: ${layout.row_gap}`,
    `--font-size: ${layout.font_size}pt`,
    `--page-margin: ${layout.page_margin}cm`,
    `--answer-line-display: ${layout.answer_line ? 'block' : 'none'}`,
    `--header-bar-display: ${layout.header_bar ? 'grid' : 'none'}`,
  ].join('; ');
  const items = paper.items
    .map(
      (item) =>
        `<div class="q-item" data-seq="${item.seq}">` +
        `<span class="q-seq">${item.seq}.</span>` +
        `<span class="q-display">${item.display}</span>` +
        `<span class="q-blank" aria-hidden="true"></span></div>`,
    )
    .join('\n');
  const answers =
    mode === 'answer'
      ? `<section class="answer-section" data-break-before="page"><h2>答案</h2>${paper.items
          .map(
            (item) =>
              `<div class="a-item" data-seq="${item.seq}"><span class="a-seq">${item.seq}.</span>` +
              `<span class="a-answer">${item.answer}</span></div>`,
          )
          .join('\n')}</section>`
      : '';
  return [
    '<!DOCTYPE html>',
    '<html lang="zh-CN">',
    '<head>',
    '<meta charset="utf-8">',
    `<meta name="generator" content="mathgen render_sheet (verify mock)">`,
    `<title>${paper.title}</title>`,
    '<style>',
    `:root { ${vars}; }`,
    '* { box-sizing: border-box; }',
    'html, body { margin: 0; padding: 0; background: #fff; color: #000; font-size: var(--font-size); }',
    '.q-row { display: grid; grid-template-columns: repeat(var(--cols-per-row), 1fr);',
    '  row-gap: calc(var(--row-gap) * 1em); }',
    '.q-item { font-size: var(--font-size); }',
    /* D59：开关走 display 变量，服务端渲染与面板实时覆盖同一条通路 */
    '.q-blank { display: var(--answer-line-display, inline-block); width: 4em;',
    '  border-bottom: 1px solid #999; }',
    '.header-bar { display: var(--header-bar-display, grid); grid-template-columns: repeat(3, 1fr);',
    '  gap: 1em; border-top: 1px solid #000; border-bottom: 1px solid #000; }',
    '.answer-section { break-before: page; page-break-before: always; }',
    `@page { size: A4; margin: ${layout.page_margin}cm; }`,
    /* 屏幕上的 A4 纸面 + 页边距（与 render-export 的 @media screen 做法一致），
       让「页边距」这一项在预览里也看得见 */
    '@media screen { .sheet { width: 210mm; max-width: 100%; margin: 0 auto;',
    '  padding: var(--page-margin); background: #fff; box-shadow: 0 0 4px rgba(0,0,0,.25); } }',
    '</style>',
    '</head>',
    `<body class="mode-${mode}">`,
    `<main class="sheet" data-mode="${mode}">`,
    '<div class="header-bar"><span>班级：</span><span>姓名：</span><span>得分：</span></div>',
    `<h1 class="sheet-title">${paper.title}</h1>`,
    `<div class="q-row">${items}</div>`,
    answers,
    '</main>',
    '</body>',
    '</html>',
  ].join('\n');
}

function html(res, body) {
  res.writeHead(200, {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

const apiServer = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${API_PORT}`);
  /** 本次请求的 JSON body（PATCH 之类）——验收要断言「请求体形状」而不只是「发了请求」 */
  let rawBody = '';
  req.on('data', (chunk) => {
    rawBody += chunk;
  });
  req.on('end', () => {
    let parsedBody = null;
    if (rawBody) {
      try {
        parsedBody = JSON.parse(rawBody);
      } catch {
        parsedBody = { __unparsable: rawBody.slice(0, 120) };
      }
    }
    req.parsedBody = parsedBody;
    const entry = { method: req.method, path: url.pathname };
    if (parsedBody !== null) entry.body = parsedBody;
    apiLog.push(entry);
    try {
      dispatchApi(req, res, url);
    } catch (error) {
      // 让一次抛错只毁这一条请求，不毁整个验收服务 —— 否则下面这个场景会退化成
      // **静默 hang**：负对照套件故意把破坏钩子装上（MATHGEN_MOCK_TAMPER），
      // 期望看到"这条路径真的被打到了"。若客户端只看到连接挂起（而不是 500），
      // 它无法区分"钩子没触发"和"服务崩了"，负对照本身就失去了分辨力。
      log(`API ERROR ${req.method} ${url.pathname}: ${error.message}`);
      if (!res.headersSent) {
        json(res, 500, { error: { code: 'MOCK_TAMPER', message: String(error.message) } });
      } else {
        res.end();
      }
    }
  });
});

function dispatchApi(req, res, url) {
  log(`API ${req.method} ${url.pathname}`);

  if (url.pathname === '/__api-log') return json(res, 200, apiLog);

  if (MODE === 'api-down' && url.pathname.startsWith('/api/')) {
    return json(res, 500, {
      error: { code: 'INTERNAL_ERROR', message: '模拟后端内部错误（验收场景 api-down）' },
    });
  }

  // 卷面 HTML：任意卷都用它自己那份 detail 渲染（M3 卷库里的卷也要能打开预览）
  const sheetMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/sheet$/);
  if (req.method === 'GET' && sheetMatch) {
    const mode = url.searchParams.get('mode') === 'answer' ? 'answer' : 'question';
    const detail = MOCK_DETAILS.get(Number(sheetMatch[1])) ?? MOCK_PAPER;
    return html(res, sheetHtml(detail, mode));
  }

  // ---- M3：按卷号取卷 ----------------------------------------------------
  // **路由顺序很重要**：`/api/papers/by-code/{code}` 必须排在 `/api/papers/{id}` 之前，
  // 否则 `by-code` 会被当成 id。真实后端（FastAPI 按声明顺序匹配）同样有此要求，
  // 这里照着排，顺便让「顺序写错」在验收里立刻暴露。
  const byCodeMatch = url.pathname.match(/^\/api\/papers\/by-code\/(.+)$/);
  if (req.method === 'GET' && byCodeMatch) {
    const code = decodeURIComponent(byCodeMatch[1]);
    const found = findDetailByCode(code);
    if (!found) {
      return json(res, 404, { error: { code: 'CODE_NOT_FOUND', message: `卷号 ${code} 不存在` } });
    }
    return json(res, 200, found);
  }

  // ---- M3：第六维答案版形式落库 ------------------------------------------
  const answerLayoutMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/answer-layout$/);
  if (req.method === 'PATCH' && answerLayoutMatch) {
    const id = Number(answerLayoutMatch[1]);
    const detail = MOCK_DETAILS.get(id);
    if (!detail) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: `卷 ${id} 不存在` } });
    }
    if (detail.status === 'published') {
      // 与真实后端一致：已发布卷只读 → 409 PAPER_NOT_EDITABLE
      return json(res, 409, {
        error: { code: 'PAPER_NOT_EDITABLE', message: '已发布的卷不能直接修改，请另存为副本' },
      });
    }
    const parsed = req.parsedBody ?? {};
    const next = { ...detail.answer_layout };
    for (const key of ['separate_page', 'include_steps', 'include_difficulty', 'include_tags']) {
      if (typeof parsed[key] === 'boolean') next[key] = parsed[key];
    }
    detail.answer_layout = next;
    return json(res, 200, next);
  }

  // 卷详情：任意 id 都从数据集取（`/api/papers/1` 与卷库里的 100+ 都能打开）
  const detailMatch = url.pathname.match(/^\/api\/papers\/(\d+)$/);
  if (req.method === 'GET' && detailMatch) {
    const detail = MOCK_DETAILS.get(Number(detailMatch[1]));
    if (!detail) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: `卷 ${detailMatch[1]} 不存在` } });
    }
    return json(res, 200, detail);
  }

  // ---- M3：卷库列表（分页 / 搜索 / 状态筛选）-----------------------------
  // MODE=legacy-list 时退化成历史形态（只有 total + papers，靠 page/size），
  // 专门用来验证前端 `detectPaginationMode` 的兜底路径真的会切换。
  if (req.method === 'GET' && url.pathname === '/api/papers') {
    return json(res, 200, MODE === 'legacy-list' ? listPapersLegacy(url) : listPapers(url));
  }

  // ---- 建卷与发布 --------------------------------------------------------
  //
  // **为什么 mock 也需要它们**：真实后端的学生端流程是「建卷 → 发布拿卷号 → 按卷号取卷」，
  // 而 mock 原来是预置卷库、没有这两条路由。于是 `compare-mock-and-real.mjs` 只能拿
  // mock 预置的第 1 套卷去比对真实的「新建的卷」—— 两边是**不同的资源**，
  // 「形状不同」就分不清是「mock 漂移」还是「两个对象本来就不同」。
  // 补上这两条，比对脚本就能让**两边跑同一份请求序列**，差异只剩真实漂移。
  //
  // 新增分支不动任何既有 mock 响应语义（老师端那批断言对象不受影响）。
  const createPaper = url.pathname === '/api/papers';
  if (req.method === 'POST' && createPaper) {
    const config = req.parsedBody ?? {};
    const seedParam = url.searchParams.get('seed');
    const title = url.searchParams.get('title') ?? MOCK_PAPER.title;
    MOCK_PAPER_SEQ += 1;
    const detail = {
      ...MOCK_PAPER,
      id: MOCK_PAPER_SEQ,
      code: null,
      title,
      status: 'draft',
      seed: seedParam === null ? djb2Seed(JSON.stringify(config)) : Number(seedParam),
      config: { ...MOCK_PAPER.config, ...config },
      published_at: null,
      items: MOCK_PAPER.items.map((item) => ({ ...item })),
    };
    // 追加到**尾部**：不改变 `/api/papers?limit=1` 的既有结果（那是老师端判据在用的）
    MOCK_DETAILS.set(detail.id, detail);
    MOCK_LIBRARY.push({
      id: detail.id,
      code: null,
      title: detail.title,
      status: 'draft',
      item_count: detail.items.length,
      created_at: detail.created_at,
      published_at: null,
      // 新卷一定未归档、一定没有学生成绩 —— 两个键都要显式写：
      // 少了 `archived_at` 会让归档筛选（真值判断）误判，少了 `has_attempts`
      // 会让列表页把它读成「没有成绩」（当前确实如此，但字段缺席和字段为 false 不是一回事）。
      archived_at: null,
      has_attempts: false,
    });
    return json(res, 201, detail);
  }

  const publishMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/publish$/);
  if (req.method === 'POST' && publishMatch) {
    const detail = MOCK_DETAILS.get(Number(publishMatch[1]));
    if (!detail) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: `卷 ${publishMatch[1]} 不存在` } });
    }
    if (detail.status === 'published') {
      // 与真实后端一致：重复发布 → 409（不是静默成功）
      return json(res, 409, {
        error: { code: 'PAPER_ALREADY_PUBLISHED', message: '这套卷已经发布过了' },
      });
    }
    MOCK_CODE_SEQ += 1;
    detail.status = 'published';
    detail.code = `2610-${String(MOCK_CODE_SEQ).padStart(4, '0')}`;
    detail.published_at = new Date().toISOString();
    const summary = MOCK_LIBRARY.find((row) => row.id === detail.id);
    if (summary) {
      summary.status = 'published';
      summary.code = detail.code;
      summary.published_at = detail.published_at;
    }
    return json(res, 200, {
      id: detail.id,
      code: detail.code,
      // 契约 §5.3：`qr_url` 是相对路径，`student_url` 是带 Host 的绝对地址
      qr_url: `/api/papers/${detail.id}/qr.png`,
      student_url: `http://${req.headers.host ?? '127.0.0.1'}${STUDENT_PAPER_PATH}/${detail.code}`,
    });
  }

  // ---- D78：卷库收纳（归档 / 恢复 / 删除）---------------------------------
  //
  // 语义逐条照真实实现抄（`backend/app/routers/papers.py` + `services/paper_service.py`），
  // 不是照记忆编的：
  //   · 归档 = **软删除**，可逆，**幂等**（重复归档保留第一次的 `archived_at`，不报错），
  //     且**任何状态都能归档**、**不改 `status`**（归档与状态正交 —— 改了状态，恢复时
  //     就不知道该死回 `draft` 还是 `published`）。
  //   · 恢复 = `archived_at` 归 `null`，**幂等**。
  //   · 删除 = **物理删除**，成功 **204 无响应体**；有学生成绩 → **409
  //     `PAPER_HAS_ATTEMPTS`**，卷与成绩原样保留（外键没有 `ondelete`，真删只会撞
  //     数据库错误，把它翻译成 409 + 一句人话 + 一条出路才是有用的行为）。
  //
  // 那条 409 只是**并发兜底**（列表读完之后、点删除之前刚好有学生交了卷）；
  // 界面必须先用 `PaperSummary.has_attempts` 事先禁用入口并写明理由（D65/D59）。
  //
  // 路由顺序：`DELETE /api/papers/{id}` 与 `DELETE /api/papers/{id}/items/{seq}`
  // 形状相邻，所以下面用**精确正则**锁死各自路径，前者不会吃掉后者。
  const archiveMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/archive$/);
  if (req.method === 'POST' && archiveMatch) {
    const id = Number(archiveMatch[1]);
    const detail = MOCK_DETAILS.get(id);
    const summary = MOCK_LIBRARY.find((row) => row.id === id);
    if (!detail || !summary) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: `卷 ${id} 不存在` } });
    }
    if (summary.archived_at === null) {
      // 时间戳格式照真实后端 `services/timeutil.utcnow_iso()`：`datetime.now(timezone.utc)
      // .isoformat()` 给的是 `+00:00` 结尾，**不是** `Z`。两者都能被 `new Date()` 解析，
      // 所以形状比对抓不出差别 —— 正因为抓不出，才更要照抄，别留下没人会发现的漂移。
      const stamp = new Date().toISOString().replace(/Z$/, '+00:00');
      summary.archived_at = stamp;
      detail.archived_at = stamp;
    }
    return json(res, 200, detail);
  }

  const unarchiveMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/unarchive$/);
  if (req.method === 'POST' && unarchiveMatch) {
    const id = Number(unarchiveMatch[1]);
    const detail = MOCK_DETAILS.get(id);
    const summary = MOCK_LIBRARY.find((row) => row.id === id);
    if (!detail || !summary) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: `卷 ${id} 不存在` } });
    }
    summary.archived_at = null;
    detail.archived_at = null;
    return json(res, 200, detail);
  }

  const deletePaperMatch = url.pathname.match(/^\/api\/papers\/(\d+)$/);
  if (req.method === 'DELETE' && deletePaperMatch) {
    const id = Number(deletePaperMatch[1]);
    const index = MOCK_LIBRARY.findIndex((row) => row.id === id);
    if (index === -1 || !MOCK_DETAILS.has(id)) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: `卷 ${id} 不存在` } });
    }
    if (MOCK_LIBRARY[index].has_attempts) {
      return json(res, 409, {
        error: {
          code: 'PAPER_HAS_ATTEMPTS',
          // 与真实后端的文案逐字一致（`paper_service.purge_paper`）。
          message: `卷 ${id} 已有学生成绩，不能删除；可以改为归档`,
        },
      });
    }
    MOCK_LIBRARY.splice(index, 1);
    MOCK_DETAILS.delete(id);
    return noContent(res);
  }

  const deleteMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/items\/(\d+)$/);
  if (req.method === 'DELETE' && deleteMatch) {
    const seq = Number(deleteMatch[2]);
    const remaining = MOCK_PAPER.items
      .filter((item) => item.seq !== seq)
      .map((item, index) => ({ ...item, seq: index + 1 }));
    return json(res, 200, remaining);
  }

  const regenMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/items\/(\d+)\/regenerate$/);
  if (req.method === 'POST' && regenMatch) {
    return json(res, 200, {
      item: {
        seq: Number(regenMatch[2]),
        display: '777 × 88',
        expr: '777 × 88',
        answer: '68376',
        difficulty: 5,
        tags: ['换一题'],
        steps: [],
        origin: 'regenerated',
      },
    });
  }

  // ---- M4 学生端三条（报到 / 按卷号取题 / 自主练习）+ 交卷 ------------------
  //
  // 为什么必须补：M4 之前这里的 mock 只有 `/api/papers/**`，学生端 SPA 一报到就
  // 404，于是「扫学生端」的判据其实是在扫**错误态**——那不是覆盖，是虚假的安全感。
  //
  // 字段形状**逐键照抄真实 app 的响应**（我用一次性探针经 `backend/tests/api/_support.py`
  // 的 `make_client()` 打真后端抓下来的，存在 `frontend/.tmp-capture/real-responses.json`），
  // 不是照我自己想象的形状写。三处易错点：
  //   - `POST /api/students` 真实返回 **201**（新建）/ 200（(name, student_no) 已存在取回）
  //   - `/api/student/paper/{code}` **不含** `answer` / `steps`（D74 白名单守卫）——
  //     mock 若顺手带上 answer，就会掩盖前端「误读 answer」的缺陷
  //   - `POST /api/attempts` 真实返回 **201**（新建）/ 200（D76 按 (student_id, attempt_uuid)
  //     幂等重放）——2026-10 学生端「交两次得两条记录」的 bug 就是没测这条
 
  const createStudentMatch = url.pathname === '/api/students';
  if (req.method === 'POST' && createStudentMatch) {
    const body = req.parsedBody ?? {};
    const key = `${body.name ?? ''}\u0000${body.student_no ?? ''}`;
    const known = MOCK_STUDENTS.get(key);
    if (known) {
      // 与真实后端一致：命中已存在的 (name, student_no) 是「取」不是「建」→ 200
      return json(res, 200, { student_id: known });
    }
    MOCK_STUDENT_SEQ += 1;
    MOCK_STUDENTS.set(key, MOCK_STUDENT_SEQ);
    return json(res, 201, { student_id: MOCK_STUDENT_SEQ });
  }

  const studentPaperMatch = url.pathname.match(/^\/api\/student\/paper\/(.+)$/);
  if (req.method === 'GET' && studentPaperMatch) {
    assertNoTamper('student_paper');
    const code = decodeURIComponent(studentPaperMatch[1]);
    const found = findDetailByCode(code);
    // 与真实后端一致：卷不存在或**未发布**都回 404 CODE_NOT_FOUND，不用 409
    // （否则等于告诉拿到卷号的人「这个卷号存在，只是还没发布」）
    if (!found || found.status !== 'published') {
      return json(res, 404, { error: { code: 'CODE_NOT_FOUND', message: `卷号 ${code} 不存在` } });
    }
    return json(res, 200, studentPaperPayload(found));
  }

  if (req.method === 'POST' && url.pathname === '/api/student/practice') {
    const config = req.parsedBody ?? MOCK_PAPER.config;
    const seedParam = url.searchParams.get('seed');
    // 与真实后端同构：seed 是可选的，不给就服务端取一个并把**实际用的**返回（D75）
    const seed = seedParam === null ? djb2Seed(JSON.stringify(config)) : Number(seedParam);
    const { unwrapped, wire } = practicePayload(config, seed);
    MOCK_PRACTICE.set(seed, { config, items: unwrapped });
    return json(res, 200, wire);
  }

  if (req.method === 'POST' && url.pathname === '/api/attempts') {
    const body = req.parsedBody ?? {};
    // D76 幂等键是 **(student_id, attempt_uuid)**，不是 uuid 单独一个
    const replayKey = `${body.student_id ?? ''}\u0000${body.attempt_uuid ?? ''}`;
    const seen = MOCK_ATTEMPTS.get(replayKey);
    if (seen) {
      // 与真实后端一致：重放没有新建记录 → 200（不能报 201）
      return json(res, 200, seen);
    }
    const graded = gradeAttempt(body);
    if (!graded) {
      return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: 'mock：卷不存在' } });
    }
    MOCK_ATTEMPT_SEQ += 1;
    const out = { attempt_id: MOCK_ATTEMPT_SEQ, ...graded };
    MOCK_ATTEMPTS.set(replayKey, out);
    return json(res, 201, out);
  }

  // 一次性探针用：把学生端可变状态复位，避免同一进程里多次跑验收互相污染
  if (req.method === 'POST' && url.pathname === '/__reset-student-state') {
    MOCK_STUDENTS.clear();
    MOCK_PRACTICE.clear();
    MOCK_ATTEMPTS.clear();
    MOCK_STUDENT_SEQ = 1000;
    MOCK_ATTEMPT_SEQ = 0;
    return json(res, 200, { ok: true });
  }

  const layoutMatch = url.pathname.match(/^\/api\/papers\/(\d+)\/layout$/);
  if (req.method === 'PATCH' && layoutMatch) {
    // body 已由 createServer 统一读完（见 req.parsedBody）；
    // 这里**不要再挂 req.on('data')** —— 流已被消费，重复监听会让请求永远等不到 'end'，
    // 表现是前端保存状态卡在 saving（本机实测踩过）。
    const parsed = req.parsedBody ?? {};
    const patch = parsed.layout ?? parsed;
    Object.assign(MOCK_PAPER.layout, patch);
    const detail = MOCK_DETAILS.get(Number(layoutMatch[1]));
    if (detail) Object.assign(detail.layout, patch);
    return json(res, 200, MOCK_PAPER.layout);
  }

  return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: 'mock 没有这条路由' } });
}

// ---------------------------------------------------------------------------
// 静态服务：frontend/dist（生产产物）+ /api 反代 + SPA 回落
// ---------------------------------------------------------------------------

const staticServer = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${STATIC_PORT}`);
  log(`STATIC ${req.method} ${url.pathname}`);

  // 与 vite.config.ts 的 dev proxy 同语义：同源 /api 转发到后端
  if (url.pathname.startsWith('/api/')) {
    const proxied = httpRequest(
      { host: '127.0.0.1', port: API_PORT, path: req.url, method: req.method, headers: req.headers },
      (upstream) => {
        res.writeHead(upstream.statusCode ?? 502, upstream.headers);
        upstream.pipe(res);
      },
    );
    proxied.on('error', () =>
      json(res, 502, { error: { code: 'BAD_GATEWAY', message: '模拟后端未响应' } }),
    );
    req.pipe(proxied);
    return;
  }

  let filePath = path.join(DIST_DIR, decodeURIComponent(url.pathname));
  if (!filePath.startsWith(DIST_DIR)) {
    res.writeHead(403).end('forbidden');
    return;
  }
  try {
    const info = await stat(filePath);
    if (info.isDirectory()) filePath = path.join(filePath, 'index.html');
    const body = await readFile(filePath);
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath)] ?? 'application/octet-stream',
    });
    res.end(body);
  } catch {
    // SPA 回落。**两个入口各回各的**：`/student/**` 必须落回 student.html，
    // 否则学生端的 BrowserRouter 路由（/student/practice、/student/paper/:code…）
    // 会拿到老师端的 index.html —— 页面能开、HTTP 200，但跑的是老师端应用。
    // 这种"回落对了、应用错了"的形态会让任何扫学生端的判据**静默扫成老师端**，
    // 判据照样全绿（实测：/student/、/student/practice 都真的返回老师端入口）。
    const isStudent = url.pathname === '/student' || url.pathname.startsWith('/student/');
    const fallback = path.join(DIST_DIR, isStudent ? 'student.html' : 'index.html');
    const body = await readFile(fallback);
    res.writeHead(200, { 'Content-Type': MIME['.html'] });
    res.end(body);
  }
});

if (!existsSync(path.join(DIST_DIR, 'index.html'))) {
  console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
  process.exit(2);
}

/**
 * 先 bind 再宣称端口：谁绑上谁说了算。
 *
 * 为什么不用「父进程探测空闲端口再传进来」：探测（listen→close）与子进程真正 listen
 * 之间有竞态，实测在 Windows 上会拿到一个已经关掉、子进程又绑不上的端口。
 * 这里自己从 BASE 起逐个试，绑不上（EADDRINUSE）就 +1，最多试 20 个。
 *
 * 动因：本机上同时有别的服务（老师端预览 4173、样张静态服务 4180 等）。
 * 实测踩到过最坏的形态：服务退让到 4174，而父进程仍按硬编码的 4173 去驱动 ——
 * Playwright 打开的是**别人的应用**，然后等一个永远不存在的元素。
 */
async function listenWithRetry(server, basePort, label) {
  for (let step = 0; step < 20; step += 1) {
    const port = basePort + step;
    try {
      await new Promise((resolve, reject) => {
        const onError = (error) => {
          server.removeListener('listening', onListening);
          reject(error);
        };
        const onListening = () => {
          server.removeListener('error', onError);
          resolve();
        };
        server.once('error', onError);
        server.once('listening', onListening);
        server.listen(port, '127.0.0.1');
      });
      return port;
    } catch (error) {
      if (error.code !== 'EADDRINUSE') throw error;
      log(`${label} 端口 ${port} 被占用，试 ${port + 1}`);
    }
  }
  throw new Error(`${label} 从 ${basePort} 起连续 20 个端口都被占用`);
}

const actualApiPort = MODE === 'proxy-down'
  ? null
  : await listenWithRetry(apiServer, API_PORT, '模拟后端');
const actualStaticPort = await listenWithRetry(staticServer, STATIC_PORT, '静态服务');

// 父进程（verify-*.mjs）解析这一行拿到**真实**端口，不要靠猜
console.log(`VERIFY_PORTS static=${actualStaticPort} api=${actualApiPort ?? 0}`);
console.log(
  `dist: http://127.0.0.1:${actualStaticPort}/  api: ${
    MODE === 'proxy-down' ? '(未启动：模拟后端未就绪)' : `http://127.0.0.1:${actualApiPort}/api/`
  }  模式: ${MODE}`,
);
console.log('VERIFY_SERVER_READY');

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => process.exit(0));
}
