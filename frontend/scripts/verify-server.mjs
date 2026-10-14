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
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
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
  };
});

/** 列表里的每套卷都能按 ID / 卷号打开；`MOCK_PAPER`（id=1，草稿）保持原样 */
const MOCK_DETAILS = new Map();
MOCK_DETAILS.set(MOCK_PAPER.id, MOCK_PAPER);
for (const summary of MOCK_LIBRARY) {
  MOCK_DETAILS.set(summary.id, {
    ...summary,
    seed: 1000 + summary.id,
    config: { ...MOCK_PAPER.config, count: summary.item_count },
    layout: { ...MOCK_PAPER.layout },
    answer_layout: { ...MOCK_PAPER.answer_layout },
    items: MOCK_PAPER.items.slice(0, 2),
  });
}

/** 卷库列表：契约形态（回显 limit/offset） */
function listPapers(url) {
  const limit = Number(url.searchParams.get('limit') ?? 20);
  const offset = Number(url.searchParams.get('offset') ?? 0);
  const q = (url.searchParams.get('q') ?? '').trim();
  const status = url.searchParams.get('status') ?? '';
  let rows = MOCK_LIBRARY;
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
  let rows = MOCK_LIBRARY;
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
    dispatchApi(req, res, url);
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
    // BrowserRouter 的前端路由：未知路径回落 index.html
    const body = await readFile(path.join(DIST_DIR, 'index.html'));
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
