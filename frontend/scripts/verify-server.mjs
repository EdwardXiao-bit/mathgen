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
  apiLog.push({ method: req.method, path: url.pathname });
  log(`API ${req.method} ${url.pathname}`);

  if (url.pathname === '/__api-log') return json(res, 200, apiLog);

  if (MODE === 'api-down' && url.pathname.startsWith('/api/')) {
    return json(res, 500, {
      error: { code: 'INTERNAL_ERROR', message: '模拟后端内部错误（验收场景 api-down）' },
    });
  }

  if (req.method === 'GET' && url.pathname === '/api/papers/1/sheet') {
    const mode = url.searchParams.get('mode') === 'answer' ? 'answer' : 'question';
    return html(res, sheetHtml(MOCK_PAPER, mode));
  }

  if (req.method === 'GET' && url.pathname === '/api/papers/1') return json(res, 200, MOCK_PAPER);
  if (req.method === 'GET' && url.pathname === '/api/papers') {
    return json(res, 200, {
      total: 1,
      papers: [
        {
          id: 1,
          code: null,
          title: MOCK_PAPER.title,
          status: 'draft',
          item_count: MOCK_PAPER.items.length,
          created_at: MOCK_PAPER.created_at,
          published_at: null,
        },
      ],
    });
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
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
    });
    req.on('end', () => {
      const parsed = JSON.parse(body || '{}');
      Object.assign(MOCK_PAPER.layout, parsed.layout ?? {});
      json(res, 200, { layout: MOCK_PAPER.layout });
    });
    return undefined;
  }

  return json(res, 404, { error: { code: 'PAPER_NOT_FOUND', message: 'mock 没有这条路由' } });
});

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

await new Promise((resolve) => staticServer.listen(STATIC_PORT, '127.0.0.1', resolve));
if (MODE !== 'proxy-down') {
  await new Promise((resolve) => apiServer.listen(API_PORT, '127.0.0.1', resolve));
}
console.log(`dist: http://127.0.0.1:${STATIC_PORT}/  api: ${MODE === 'proxy-down' ? '(未启动：模拟后端未就绪)' : `http://127.0.0.1:${API_PORT}/api/`}  模式: ${MODE}`);
console.log('VERIFY_SERVER_READY');

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => process.exit(0));
}
