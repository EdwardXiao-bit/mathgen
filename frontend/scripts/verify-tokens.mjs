/**
 * 门禁 ⑥-a：判据 A1 —— 令牌之外不得有颜色字面量。
 *
 * 守的是什么：`docs/07-设计规范.md` §6 A1。M2 交付时实测有 33 处硬编码颜色，
 * 「改一次主色要手工改多处」。冻结 `design/tokens.css` 之后，`frontend/src/**`
 * 里除该文件外**不得**再出现颜色字面量（`#rgb` / `#rrggbb` / `rgb()` / `rgba()` / `hsl()` / `hsla()`）。
 *
 * 允许清单（`ALLOWED`）：**逐条**给理由，且必须带文件 + 行号 + 该行原文，
 * 不允许「整类豁免」。目前为**空**——结构轮就是把 33 处全部换成令牌。
 *
 * 用法：node scripts/verify-tokens.mjs
 */

import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC_DIR = path.resolve(__dirname, '..', 'src');
/** 令牌唯一来源：本文件**就是**定义颜色的地方，跳过 */
const TOKENS_FILE = path.join(SRC_DIR, 'design', 'tokens.css');

/**
 * 逐条例外。每条必须写清「为什么它不能被令牌替代」。
 * 结构轮**没有例外**——这条数组保持空着本身就是验收结论的一部分。
 */
const ALLOWED = [];

const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(/g;
const TEXT_EXT = new Set(['.css', '.tsx', '.ts', '.jsx', '.js', '.html']);

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(full)));
    else if (TEXT_EXT.has(path.extname(entry.name))) files.push(full);
  }
  return files;
}

const files = await walk(SRC_DIR);
const hits = [];

for (const file of files) {
  if (path.resolve(file) === path.resolve(TOKENS_FILE)) continue;
  const text = await readFile(file, 'utf8');
  text.split(/\r?\n/).forEach((line, index) => {
    const matches = line.match(COLOR_RE);
    if (!matches) return;
    const rel = path.relative(path.resolve(__dirname, '..'), file).replace(/\\/g, '/');
    const allowed = ALLOWED.find((entry) => entry.file === rel && entry.line === index + 1);
    hits.push({
      file: rel,
      line: index + 1,
      literals: [...new Set(matches)].join(' '),
      text: line.trim().slice(0, 120),
      allowed,
    });
  });
}

const offending = hits.filter((hit) => !hit.allowed);

console.log('判据 A1：令牌之外不得有颜色字面量（docs/07 §6）');
console.log(`扫描范围：frontend/src/** 共 ${files.length} 个文件，其中跳过令牌文件 design/tokens.css`);
console.log(`颜色字面量（#rgb / #rrggbb / rgb( / rgba( / hsl( / hsla(）出现行数：${hits.length}`);

if (ALLOWED.length > 0) {
  console.log(`\n允许清单 ${ALLOWED.length} 条（逐条给理由）：`);
  for (const entry of ALLOWED) {
    console.log(`  [ALLOW] ${entry.file}:${entry.line}\n          理由：${entry.reason}`);
  }
} else {
  console.log('允许清单：空（结构轮要求全部换成令牌，没有例外）');
}

if (offending.length > 0) {
  console.log(`\n发现 ${offending.length} 处令牌之外的颜色字面量（判失败）：`);
  for (const hit of offending.slice(0, 40)) {
    console.log(`  [FAIL] ${hit.file}:${hit.line}  ${hit.literals}`);
    console.log(`         ${hit.text}`);
  }
  if (offending.length > 40) console.log(`  …（共 ${offending.length} 处）`);
  console.log('\n结论：颜色必须来自 design/tokens.css 的 var(--mg-*)。缺令牌请向 Lead 申请，不要本地加。');
  process.exit(1);
}

console.log('\n结论：frontend/src/** 除 design/tokens.css 外，颜色字面量计数 = 0（A1 通过）。');
