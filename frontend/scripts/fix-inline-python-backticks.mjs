/**
 * 内联 Python 模板块里的反引号 —— **代码整洁检查，不是语法判据**。
 *
 * ## ⚠️ 这个工具**不能**用来判断语法，它第一版的方向是反的
 *
 * 我最初把它当成"能抓出会让 `String.raw` 模板提前终止的反引号"的语法判据，
 * 并接进了门禁前置自检。**这个主张已被实验证伪**（Lead 用两个探针实测）：
 *
 * | 输入 | `node --check` | 本工具（旧版主张） |
 * | --- | --- | --- |
 * | 体内含**裸**反引号 | **失败** | 报「没有反引号 ✅」→ **漏报** |
 * | 体内含**转义**反引号 | 通过（语法合法） | 报「会导致模板提前终止」→ **假报红** |
 *
 * 根因是一条逻辑推论：`String.raw` 体内**不可能**有未转义反引号（那会被 JS 解析器
 * 当成模板结束、文件直接语法错），所以**能活着到达体内扫描的反引号必然是转义过的**。
 * 也就是说：这个工具在语法层面**只能看到无害形态**，永远看不到真问题。
 *
 * **真正的语法判据是 `node --check`** —— 见 `check-script-syntax.mjs`，
 * 它带负对照与正对照，直接命中"模板提前终止"这**一整类**问题，
 * 不依赖任何人去枚举坑的形状（与「枚举式判据永远不完整」是同一个教训）。
 *
 * ## 那这个工具还留着干什么
 *
 * 保留一条**可读性约定**：内联 Python 里不要出现反引号，**即使转义** ——
 * 它正好与 JS 的模板语法同形，读代码的人容易看错，也确实让"模板到底在哪结束"
 * 这类困惑反复发生（M3.5 期间踩了五次）。这是**约定**，不是**语法要求**。
 *
 * 因此默认行为是**告警**（exit=0），不阻断门禁 —— 转义反引号是合法代码，不该假报红。
 *
 * 用法：
 *   node scripts/fix-inline-python-backticks.mjs             # 只告警（默认，exit=0）
 *   node scripts/fix-inline-python-backticks.mjs --strict     # 有反引号即失败
 *   node scripts/fix-inline-python-backticks.mjs --write      # 就地整洁化
 *   node scripts/fix-inline-python-backticks.mjs --self-test  # 工具自检
 *   node scripts/fix-inline-python-backticks.mjs path.mjs     # 只处理一个
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
const strict = args.includes('--strict');
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
      `${write ? 'FIXED' : 'WARN '}  ${path.basename(file)}  ` +
        `模板体 ${r.bodies} 个，体内反引号 ${r.replaced} 处`,
    );
  }
}

if (filesWithProblem === 0) {
  console.log('内联 Python 模板块里没有反引号 ✅');
  process.exit(0);
}

// 能走到这里的反引号，**语法上一定是转义过的**（裸的早就让文件语法错了，
// 那种情况由 check-script-syntax.mjs 的 node --check 负责抓）。所以默认只告警。
console.log(
  `\n共 ${totalReplaced} 处“体内反引号”。` +
    `\n注意：这些**都是转义形态**（裸的会让文件直接语法错，由 node --check 抓），` +
    `\n所以它们不影响语法，只是可读性问题（与 JS 模板语法同形，容易看错）。` +
    `\n整洁化：node scripts/fix-inline-python-backticks.mjs --write`,
);
if (write) {
  console.log(`\n已处理 ${totalReplaced} 处。`);
  process.exit(0);
}
if (strict) {
  console.log('\n--strict 下把这条可读性约定当作失败。');
  process.exit(1);
}
console.log('\n（默认只告警，exit=0 —— 合法代码不该被假报红。）');
process.exit(0);
