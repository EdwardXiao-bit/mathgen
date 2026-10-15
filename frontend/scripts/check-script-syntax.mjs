/**
 * 门禁脚本的**语法自检** —— 对 `frontend/scripts/*.mjs` 逐个跑 `node --check`。
 *
 * ## 为什么是这条判据，而不是"反引号检查"
 *
 * 门禁脚本用 `String.raw` 承载内联 Python。这类文件一旦**语法坏掉**，报出来的错往往
 * 与真实原因毫不相关（例如"missing ) after argument list"指向几十行之外），
 * 排查很费时间。所以前置自检是对的 —— 但**判据必须是能命中这一整类问题的通用判据**。
 *
 * 我上一版做的是"扫描内联 Python 模板块里有没有反引号"，**方向是反的**，已被实验证伪：
 *
 * | 输入 | `node --check` | 旧的"反引号检查" |
 * | --- | --- | --- |
 * | 体内含**裸**反引号（真的会让模板提前终止） | **失败** | 报「没有反引号 ✅」→ **漏报** |
 * | 体内含**转义**反引号（语法完全合法） | 通过 | 报「1 处会导致模板提前终止」→ **假报红** |
 *
 * 根因是一条逻辑推论：`String.raw` 的体内**不可能**有未转义反引号（那会被 JS 解析器
 * 当成模板结束），所以**能活着到达扫描器体内的反引号必然是转义过的** ——
 * 也就是说，那种扫描器在语法层面**只能抓到无害形态**，永远抓不到真问题。
 * 这与"枚举式判据永远不完整"是同一个教训：**不要枚举坑的形状，直接判语法**。
 *
 * ## 负对照（V9 / V9.2）
 *
 * 光写 `node --check` 调用是不够的：万一调用写错（比如漏传文件名、拿错退出码），
 * 它会**恒真**。所以本脚本自己现造一个**语法确实坏掉**的文件，断言 `node --check` 必须红；
 * 再造一个**语法正确**的文件，断言必须绿。两条一起才说明"这个判据抓得住东西、且不误报"。
 * 坏文件建在临时目录里、跑完即删，不会把坏文件留在仓库。
 *
 * 用法：node scripts/check-script-syntax.mjs
 */

import { spawnSync } from 'node:child_process';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const TMP_DIR = path.join(FRONTEND_DIR, '.tmp-syntax');

/** 用 `node --check` 判一个文件语法是否成立。返回 { ok, status, stderr }。 */
function nodeCheck(file) {
  const proc = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' });
  return { ok: proc.status === 0, status: proc.status, stderr: proc.stderr ?? '' };
}

const failures = [];

// ---------- 1) 负对照与正对照：先证明这个判据抓得住东西 ----------
console.log('—— 判据自检（负对照 / 正对照）——');
mkdirSync(TMP_DIR, { recursive: true });
const brokenFile = path.join(TMP_DIR, 'deliberately-broken.mjs');
const goodFile = path.join(TMP_DIR, 'deliberately-good.mjs');

try {
  // 负对照：体内一个**裸**反引号 —— 这正是会让 String.raw 模板提前终止的形态。
  // 用最简单的语法错误即可（这里是未闭合的调用），关键是"文件确实是坏的"。
  writeFileSync(brokenFile, 'const PY = String.raw`\nprint(1)\n`;\nconsole.log(PY\n', 'utf8');
  const broken = nodeCheck(brokenFile);
  const brokenOk = broken.ok === false;
  console.log(
    `  [${brokenOk ? 'PASS' : 'FAIL'}] 负对照：语法坏的文件必须被判红 —— ` +
      `node --check exit=${broken.status}（期望非 0）`,
  );
  if (!brokenOk) failures.push('负对照失败：node --check 对语法坏的文件没有报错');

  // 正对照：语法正确的同构文件必须判绿（否则判据是"一律报红"）
  writeFileSync(goodFile, 'const PY = String.raw`\nprint(1)\n`;\nconsole.log(PY);\n', 'utf8');
  const good = nodeCheck(goodFile);
  const goodOk = good.ok === true;
  console.log(
    `  [${goodOk ? 'PASS' : 'FAIL'}] 正对照：语法正确的文件必须判绿 —— ` +
      `node --check exit=${good.status}（期望 0）`,
  );
  if (!goodOk) failures.push('正对照失败：node --check 对语法正确的文件也报错');
} finally {
  rmSync(TMP_DIR, { recursive: true, force: true });
}

// ---------- 2) 真实目标：frontend/scripts/*.mjs 逐个检查 ----------
const targets = readdirSync(__dirname)
  .filter((f) => f.endsWith('.mjs'))
  .sort();

console.log(`\n—— frontend/scripts/*.mjs 逐个 node --check（${targets.length} 个）——`);
let badCount = 0;
for (const name of targets) {
  const full = path.join(__dirname, name);
  const r = nodeCheck(full);
  if (r.ok) {
    console.log(`  [PASS] ${name}`);
  } else {
    badCount += 1;
    const firstLine = r.stderr.split('\n').find((l) => l.includes('SyntaxError')) ?? '';
    console.log(`  [FAIL] ${name} —— exit=${r.status} ${firstLine.trim()}`);
    failures.push(`${name} 语法不成立：${firstLine.trim()}`);
  }
}

if (failures.length > 0) {
  console.log(`\n语法自检未通过（${failures.length} 项）：`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log(
  `\n结果：${targets.length}/${targets.length} 个脚本语法成立；` +
    `负对照与正对照均如期（判据抓得住语法坏文件，且不误报语法正确文件）。`,
);
