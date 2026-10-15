/**
 * 把门禁脚本里内联 Python 模板块中的**反引号**替换掉。
 *
 * ## 为什么需要这个工具
 *
 * 这些脚本用 `String.raw` 模板承载内联 Python。模板由**反引号**界定，所以
 * 只要 Python 那一段（**包括注释和 JS 字符串里的正则**）出现一个反引号，
 * 模板就被提前终止，报出看起来毫不相关的 `SyntaxError: Unexpected identifier`。
 * M3.5 期间这个坑连续踩了 **五次**（AGENTS.md 也记了这一条）。
 *
 * 靠人眼 review 是拦不住的：反引号通常在**中文注释**里，看起来完全无害。
 * 所以做成工具：改完脚本跑一次，先修再报错。
 *
 * 用法：
 *   node scripts/fix-inline-python-backticks.mjs            # 检查全部门禁脚本
 *   node scripts/fix-inline-python-backticks.mjs --write     # 就地替换
 *   node scripts/fix-inline-python-backticks.mjs path.mjs    # 只处理一个
 *
 * 退出码：0 = 无反引号问题（或已成功修复）；1 = 只检查模式下发现问题。
 */

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRIPTS_DIR = __dirname;

/**
 * 找出 `String.raw` 模板体 —— **只返回体内范围**，不含界定模板的那对反引号。
 *
 * 三个必须做对的地方（都踩过，逐条写下来）：
 *
 * ① **界定符不能改**。第一版把「`String.raw` + 界定反引号」也算进范围，
 *    于是把界定符也换成单引号（`String.raw'...'`），直接改坏两个门禁脚本。
 *
 * ② **结束位置必须精确**。第二版用 `/\n\s*`\s*;/` 找结束，那个正则可能匹配到
 *    **文件后面 JS 代码里**的另一个 `` `; ``，把中间大段正常 JS 圈进"体内"，报一堆假阳性。
 *    改成从起始处顺序扫描、碰到第一个未转义反引号就停。
 *
 * ③ **`String.raw` 这几个字可能出现在注释里**（本文件自己的文档注释就提到了它！）。
 *    第三版直接用正则搜 String.raw + 反引号，结果匹配到了**注释里**那处，
 *    取的"模板起点"是注释中间的反引号 —— 于是把后面的 JS 全当成模板体，又报假阳性。
 *    所以这里改成**扫描器**：逐字符前进，跳过 // 行注释、块注释与字符串，
 *    只在**真实代码**里认 String.raw + 反引号。
 */
function findTemplateBodies(source) {
  const bodies = [];
  let i = 0;
  const n = source.length;
  let inLineComment = false;
  let inBlockComment = false;
  let inString = null; // "'" 或 '"'
  while (i < n) {
    const ch = source[i];
    const next = source[i + 1];

    if (inLineComment) {
      if (ch === '\n') inLineComment = false;
      i += 1;
      continue;
    }
    if (inBlockComment) {
      if (ch === '*' && next === '/') {
        inBlockComment = false;
        i += 2;
        continue;
      }
      i += 1;
      continue;
    }
    if (inString) {
      if (ch === '\\') {
        i += 2;
        continue;
      }
      if (ch === inString) inString = null;
      i += 1;
      continue;
    }
    // 不在注释/字符串里
    if (ch === '/' && next === '/') {
      inLineComment = true;
      i += 2;
      continue;
    }
    if (ch === '/' && next === '*') {
      inBlockComment = true;
      i += 2;
      continue;
    }
    if (ch === "'" || ch === '"') {
      inString = ch;
      i += 1;
      continue;
    }
    // 真实的 String.raw 模板？
    const tail = source.startsWith('String.raw', i);
    if (tail) {
      let j = i + 'String.raw'.length;
      while (j < n && /\s/.test(source[j])) j += 1;
      if (source[j] === '`') {
        const bodyStart = j + 1;
        let k = bodyStart;
        while (k < n) {
          if (source[k] === '\\') {
            k += 2;
            continue;
          }
          if (source[k] === '`') break;
          k += 1;
        }
        if (k < n) {
          bodies.push([bodyStart, k]);
          i = k + 1;
          continue;
        }
      }
    }
    i += 1;
  }
  return bodies;
}

function processFile(file, write) {
  const source = readFileSync(file, 'utf8');
  const bodies = findTemplateBodies(source);
  if (bodies.length === 0) return { file, bodies: 0, replaced: 0 };

  // 先把所有体的替换结果算出来（基于**原始** source，避免下标位移），再一次拼回去
  const edits = bodies.map(([start, end]) => {
    const before = source.slice(start, end);
    const after = before.replaceAll('`', "'");
    return { start, end, after, count: (before.match(/`/g) ?? []).length };
  });
  const replaced = edits.reduce((sum, e) => sum + e.count, 0);
  if (replaced === 0) return { file, bodies: bodies.length, replaced: 0 };

  if (write) {
    let out = source;
    for (const e of [...edits].reverse()) {
      out = out.slice(0, e.start) + e.after + out.slice(e.end);
    }
    writeFileSync(file, out, 'utf8');
  }
  return { file, bodies: bodies.length, replaced };
}

/**
 * 自检：验证「只改体内、不动界定符、不碰体外 JS」。
 *
 * ⚠️ 样本必须**符合 JS 语法**：`String.raw` 的体内**不可能**有未转义的反引号
 * —— 那会被 JS 解析器当成模板结束，文件根本跑不起来。
 * 第一版自检的样本就写错了（体内塞了裸反引号），于是自检"失败"其实是我样本的问题。
 * 能真正到达 `String.raw` 体内、又需要处理的反引号形态是**转义过的** `\``。
 * 自检直接对内存字符串跑，不碰磁盘。
 */
function selfTest() {
  const sample = [
    'const PY = String.raw`',
    'import os',
    'X = "a\\`b"  # 体内转义反引号',
    '`;',
    '// 后面是普通 JS：body 之外的反引号不该被碰',
    'const t = `port ${P}`;',
    'const c = "`";',
  ].join('\n');
  // 体内的 `\`` 换成 `\'`（仍是一个合法的 JS 字符串内容），界定符与体外 JS 一律不动
  const expected = [
    'const PY = String.raw`',
    'import os',
    "X = \"a\\'b\"  # 体内转义反引号",
    '`;',
    '// 后面是普通 JS：body 之外的反引号不该被碰',
    'const t = `port ${P}`;',
    'const c = "`";',
  ].join('\n');

  const bodies = findTemplateBodies(sample);
  if (bodies.length !== 1) {
    return { ok: false, why: `应找到 1 个模板体，实际 ${bodies.length}` };
  }
  const [start, end] = bodies[0];
  const after = sample.slice(0, start) + sample.slice(start, end).replaceAll('`', "'") + sample.slice(end);
  if (after !== expected) {
    return { ok: false, why: `输出与预期不符：\n${JSON.stringify(after)}\n期望：\n${JSON.stringify(expected)}` };
  }
  // 断言 1：界定符必须还在（第一版的 bug：把界定符也改了）
  if (!after.includes('String.raw`') || !after.includes('\n`;')) {
    return { ok: false, why: '界定反引号被改动了 —— 第一版的 bug' };
  }
  // 断言 2：体外 JS 里的反引号必须原样（第二版的 bug：范围算到了文件后面）
  if (!after.includes('const t = `port ${P}`;') || !after.includes('const c = "`";')) {
    return { ok: false, why: '体外的 JS 模板字面量/字符串被误改 —— 第二版的 bug' };
  }
  // 断言 3：体内确实被改掉了（否则这个工具什么也不做）
  if (after.includes('a\\`b')) {
    return { ok: false, why: '体内的转义反引号没有被替换，工具没起作用' };
  }
  // 断言 4：**注释里提到 `String.raw` 不能把扫描器带偏**（第三版的 bug）
  // 本工具自己的文档注释就写了 `String.raw`，所以这条必须成立。
  const commentCase = [
    "// 说明：本文件用 String.raw`...` 承载脚本",
    'const PY = String.raw`',
    'x = 1',
    '`;',
  ].join('\n');
  const cb = findTemplateBodies(commentCase);
  if (cb.length !== 1 || commentCase.slice(cb[0][0], cb[0][1]).trim() !== 'x = 1') {
    return {
      ok: false,
      why: `注释里出现 String.raw 时扫描器被带偏（第三版的 bug）：${JSON.stringify(cb)}`,
    };
  }
  return { ok: true, why: '只改体内；界定符、体外 JS、注释里的 String.raw 都不动' };
}

const args = process.argv.slice(2);
const write = args.includes('--write');
const explicit = args.filter((a) => !a.startsWith('--'));

if (args.includes('--self-test')) {
  const r = selfTest();
  console.log(`[${r.ok ? 'PASS' : 'FAIL'}] 自检：${r.why}`);
  process.exit(r.ok ? 0 : 1);
}

// 任何一次实际处理前先自检 —— 工具自身不健康就不该去改别人的文件。
{
  const r = selfTest();
  if (!r.ok) {
    console.error(`[FAIL] 工具自检未通过，拒绝改动任何文件：${r.why}`);
    process.exit(2);
  }
}

const targets = explicit.length
  ? explicit.map((p) => path.resolve(process.cwd(), p))
  : readdirSync(SCRIPTS_DIR)
      .filter((f) => f.endsWith('.mjs'))
      .map((f) => path.join(SCRIPTS_DIR, f));

let totalReplaced = 0;
let filesWithProblem = 0;
for (const file of targets) {
  const r = processFile(file, write);
  if (r.replaced > 0) {
    filesWithProblem += 1;
    totalReplaced += r.replaced;
    console.log(
      `${write ? 'FIXED' : 'FOUND'}  ${path.basename(file)}  ` +
        `模板体 ${r.bodies} 个，体内反引号 ${r.replaced} 处 → 替换为单引号`,
    );
  }
}

if (filesWithProblem === 0) {
  console.log('内联 Python 模板块里没有反引号 ✅');
  process.exit(0);
}
if (!write) {
  console.log(
    `\n共 ${totalReplaced} 处会导致 String.raw 模板被提前终止。` +
      `\n这些反引号通常在中文注释里，人眼很难发现 —— 跑 --write 修复：` +
      `\n  node scripts/fix-inline-python-backticks.mjs --write`,
  );
  process.exit(1);
}
console.log(`\n已替换 ${totalReplaced} 处反引号。请重新运行 node --check 确认语法。`);
