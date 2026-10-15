/**
 * 「陈旧产物」判据：`frontend/dist` 必须比它的构建输入新（V13 同族）。
 *
 * 要防的那个**静默的绿**：
 *   门禁第 ③④⑤⑦⑨ 步是浏览器门禁，它们加载的是 `frontend/dist/**`（构建产物），
 *   而 `run-all-verifications.mjs` 刻意**不构建**。于是「改了 `frontend/src/**` 却忘了
 *   `pnpm --dir frontend build`」会让这四个门禁照样全 PASS —— 它们测的是**旧 bundle**，
 *   与被改的源码无关。实测发生过：`dist` 构建于 18:46，源码 21:54 之后还在改，
 *   新 testid `library-archive-filter` 在 `dist` 里 grep 不到，而 ⑨ 报 PASS。
 *
 * 这就是「门禁的输入集合里含一个**可以不跟着源码变**的产物，而门禁没有断言这件事」。
 *
 * ---- 判据 ----
 *   `max(dist/**  的 mtime) >= max(构建输入集合的 mtime)`   → PASS
 *   否则 → 红，并点名**最晚的那个输入文件**与 dist 最新的产物。
 *
 * ---- 为什么容差是 0（同秒口径，确定性口径）----
 *   判据写成「dist 不早于源码」（严格小于才红），容差 0ms。理由：
 *     · NTFS 的 mtime 粒度是 100ns，`statSync().mtimeMs` 是浮点毫秒 ——
 *       正常情况下 build 一定发生在源码最后一次写入**之后**，两者相等几乎不可能出现；
 *     · 若落在粒度只有 1 秒的文件系统上，源码与产物会被**同时截断到同一秒** →
 *       两者相等 → 判 PASS。所以 0 容差在粗粒度下**不会产生假红**，
 *       在细粒度下又是**最灵敏**的 —— 是两种粒度下都安全的选择。
 *     · 代价（已知边界，不掩饰）：源码若在 build **之后**的同一拍内被改写，
 *       且文件系统把它们截断到同一个时间戳，本判据判 PASS —— 陈旧但看不出来。
 *       这是 mtime 判据的固有下界，不是本实现可以绕过的。
 *
 * ---- 它不能发现什么（不掩饰）----
 *   1. 有人手动 `touch dist/**`（或从别处拷一份 dist 过来）伪造新鲜度 —— mtime 是
 *      元数据，我们没有比对**内容**。判据只问「顺序」，不问「bundle 里是不是新代码」。
 *   2. build 中途失败但**已经写了**一部分产物（mtime 变新、bundle 不完整）。
 *   3. 时钟回拨 / 从别的机器拷来的文件带着未来的 mtime。
 *   4. 依赖变了（`pnpm-lock.yaml` + `node_modules`）但源码没动 —— mtime 看不出来。
 *      这是**有意**不把 lockfile 放进输入集合（见下）。
 *
 * ---- 构建输入集合（是本判据的**全部**语义，写在这里以免漂移）----
 *   收：`frontend/src/**`、`frontend/public/**`（存在才收）、
 *       `frontend/index.html`、`frontend/student.html`、`frontend/package.json`、
 *       `frontend/vite.config.*`、`frontend/tsconfig*.json`
 *   不收（附理由，别改成"顺手全收"）：
 *     · `frontend/dist/**` —— 产物本身，收进来等于自己比自己；
 *     · `frontend/node_modules/**` —— 不是"源码改了要重建"的信号；
 *     · `frontend/scripts/**` —— 门禁脚本不进 bundle。收进来会让「改一次门禁脚本」
 *       就要求重建 dist，那是在训练人忽略这条红（V9：判据一旦常红就死了）；
 *     · `frontend/*.png`（截图，**门禁自己**就会往这里写 `verify-*.png`）、`README.md`、
 *       `mockups/`、`*.tsbuildinfo` —— 都不是 bundle 的输入。特别注意截图：若把
 *       frontend 根目录下的文件一概收进来，门禁每跑一次都会把 dist 判成陈旧（自己写的东西
 *       让判据变红），这条判据会立刻变成恒红。
 *     · `pnpm-lock.yaml` / `pnpm-workspace.yaml` —— 依赖身份而非构建输入；
 *       lockfile 变了但没 `pnpm install` 时 bundle 不会变，纳入会产生假红。
 *
 * 用**可注入目录**写成函数（`checkDistFreshness({ frontendDir, distDir })`），
 * 自检/负对照才能拿一棵合成的临时目录树构造「dist 新 → 绿」与「dist 旧 → 红」两个世界
 * （V9.1/V9.2）。
 *
 * 用法（自检/手动排查；门禁走的是 run-all-verifications.mjs 里的同名前置判据）：
 *   node scripts/dist-freshness.mjs                 检查真实的 frontend/
 *   node scripts/dist-freshness.mjs <frontendDir>   检查指定目录（默认 dist 在其下）
 * 退出码：0 = 新鲜；2 = 陈旧或前置缺失（与门禁一致：2 表示"门禁状态不可信"）。
 */

import { existsSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_FRONTEND_DIR = path.resolve(__dirname, '..');

/** 走目录时一律跳过的目录名（不是构建输入）。 */
const SKIP_DIRS = new Set(['node_modules', 'dist', '.git']);

/** 目录型构建输入；存在才收。 */
const DIR_INPUTS = ['src', 'public'];

/** frontend 根目录下算构建输入的文件名模式。 */
const ROOT_FILE_PATTERNS = [
  /^index\.html$/,
  /^student\.html$/,
  /^package\.json$/,
  /^vite\.config\.[^./\\]+$/,
  /^tsconfig[^/\\]*\.json$/,
];

/** 报错文案里给出的、可以直接照做的那条命令。 */
export const REBUILD_COMMAND = 'pnpm --dir frontend build';

function walkFiles(dir, out) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walkFiles(full, out);
    } else if (entry.isFile()) {
      out.push(full);
    }
  }
  return out;
}

function toEntry(abs, frontendDir) {
  try {
    const st = statSync(abs);
    return {
      abs,
      rel: path.relative(frontendDir, abs).replace(/\\/g, '/'),
      mtimeMs: st.mtimeMs,
    };
  } catch {
    // 走到一半被删掉的文件不该让判据崩掉 —— 跳过它，但计数会体现在下面。
    return null;
  }
}

/** 收集构建输入（绝对路径列表）。导出是为了让自检能断言"集合里有/没有谁"。 */
export function collectBuildInputs(frontendDir) {
  const files = [];
  for (const dirName of DIR_INPUTS) {
    const dir = path.join(frontendDir, dirName);
    if (existsSync(dir)) walkFiles(dir, files);
  }
  let rootEntries = [];
  try {
    rootEntries = readdirSync(frontendDir, { withFileTypes: true });
  } catch {
    rootEntries = [];
  }
  for (const entry of rootEntries) {
    if (!entry.isFile()) continue;
    if (ROOT_FILE_PATTERNS.some((re) => re.test(entry.name))) {
      files.push(path.join(frontendDir, entry.name));
    }
  }
  return files;
}

/** 收集 dist 产物（绝对路径列表）。 */
export function collectDistArtifacts(distDir) {
  if (!existsSync(distDir)) return [];
  return walkFiles(distDir, []);
}

function newest(entries) {
  let best = null;
  for (const entry of entries) {
    if (best === null || entry.mtimeMs > best.mtimeMs) best = entry;
  }
  return best;
}

/**
 * 判据本体。可注入目录，纯只读。
 *
 * @returns {{
 *   ok: boolean,
 *   reason: 'fresh'|'stale'|'no-dist'|'no-inputs',
 *   distNewest: {rel:string,mtimeMs:number}|null,
 *   srcNewest: {rel:string,mtimeMs:number}|null,
 *   offenders: Array<{rel:string,mtimeMs:number}>,
 *   counts: {inputs:number, artifacts:number},
 *   toleranceMs: number,
 * }}
 */
export function checkDistFreshness({
  frontendDir = DEFAULT_FRONTEND_DIR,
  distDir = path.join(frontendDir, 'dist'),
  toleranceMs = 0,
} = {}) {
  const inputs = collectBuildInputs(frontendDir)
    .map((abs) => toEntry(abs, frontendDir))
    .filter(Boolean);
  const artifacts = collectDistArtifacts(distDir)
    .map((abs) => toEntry(abs, frontendDir))
    .filter(Boolean);

  const base = {
    distNewest: null,
    srcNewest: null,
    offenders: [],
    counts: { inputs: inputs.length, artifacts: artifacts.length },
    toleranceMs,
  };

  if (artifacts.length === 0) return { ...base, ok: false, reason: 'no-dist' };
  // 输入集合为空 = 这条判据其实什么都没在判断。那必须红，不能绿（V11：
  // 判据要对两种世界给出不同结论；"没得比"与"比过且一致"必须不同）。
  if (inputs.length === 0) return { ...base, ok: false, reason: 'no-inputs' };

  const distNewest = newest(artifacts);
  const srcNewest = newest(inputs);
  const limit = distNewest.mtimeMs + toleranceMs;
  const offenders = inputs
    .filter((f) => f.mtimeMs > limit)
    .sort((a, b) => b.mtimeMs - a.mtimeMs);

  return {
    ok: offenders.length === 0,
    reason: offenders.length === 0 ? 'fresh' : 'stale',
    distNewest,
    srcNewest,
    offenders,
    counts: base.counts,
    toleranceMs,
  };
}

function clock(mtimeMs) {
  const d = new Date(mtimeMs);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function ago(ms) {
  const seconds = Math.round(ms / 1000);
  if (Math.abs(seconds) < 60) return `${seconds} 秒`;
  return `${Math.round(seconds / 60)} 分`;
}

/**
 * 把判据结果渲染成**可以直接照做**的文字。
 * 第一行刻意写成 `frontend/dist 比源码旧：<源文件>（时刻）晚于 <dist 产物>（时刻）—— 请先运行 <命令>`。
 */
export function formatFreshnessResult(result) {
  if (result.reason === 'no-dist') {
    return (
      `[FAIL] 找不到 frontend/dist 下的任何产物 —— 请先运行 ${REBUILD_COMMAND}\n` +
      '       （浏览器门禁③④⑤⑦⑨加载的就是这里的 bundle，没有它那些门禁无从谈起。）'
    );
  }
  if (result.reason === 'no-inputs') {
    return (
      '[FAIL] 构建输入集合是空的 —— 这条判据什么都没在判断，判"不可信"而不是"通过"\n' +
      `       （检查 frontend/src 是否存在；判据定义见 scripts/dist-freshness.mjs 顶部注释。）`
    );
  }

  const dist = result.distNewest;
  const src = result.srcNewest;

  if (result.ok) {
    return (
      `[PASS] frontend/dist 比构建输入新：dist 最新 ${dist.rel}（${clock(dist.mtimeMs)}）` +
      `不早于源码最新 ${src.rel}（${clock(src.mtimeMs)}）` +
      `；输入 ${result.counts.inputs} 个文件 / 产物 ${result.counts.artifacts} 个文件，容差 ${result.toleranceMs}ms`
    );
  }

  const head = result.offenders[0];
  const lines = [];
  lines.push(
    `[FAIL] frontend/dist 比源码旧：${head.rel}（${clock(head.mtimeMs)}）` +
      `晚于 ${dist.rel}（${clock(dist.mtimeMs)}）—— 请先运行 ${REBUILD_COMMAND}`,
  );
  lines.push(
    `       共 ${result.offenders.length} 个构建输入比 dist 新（列出前 5，按新旧倒序）：`,
  );
  for (const f of result.offenders.slice(0, 5)) {
    lines.push(`         - ${f.rel}  （${clock(f.mtimeMs)}，比 dist 新 ${ago(f.mtimeMs - dist.mtimeMs)}）`);
  }
  if (result.offenders.length > 5) {
    lines.push(`         …还有 ${result.offenders.length - 5} 个`);
  }
  lines.push(
    `       判据：max(dist/** 的 mtime) 必须不早于 max(构建输入集合的 mtime)；容差 ${result.toleranceMs}ms。`,
  );
  lines.push(
    '       浏览器门禁③④⑤⑦⑨加载的是 frontend/dist，陈旧 dist 会让它们测的是旧 bundle —— 那些绿是假的。',
  );
  return lines.join('\n');
}

// ---- CLI（自检/手动排查用；门禁走 run-all-verifications.mjs 里的同名前置判据）----
const invokedDirectly =
  process.argv[1] !== undefined &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  const root = process.argv[2] ? path.resolve(process.argv[2]) : DEFAULT_FRONTEND_DIR;
  const result = checkDistFreshness({ frontendDir: root });
  const text = formatFreshnessResult(result);
  if (result.ok) console.log(text);
  else console.error(text);
  process.exit(result.ok ? 0 : 2);
}
