/**
 * 一条命令跑完全部前端验收（门禁）。
 *
 * 用法：& $NODE $PNPM --dir frontend verify      （等价于 node scripts/run-all-verifications.mjs）
 *       node scripts/run-all-verifications.mjs --list   只列步骤与脚本路径，不执行任何门禁
 *       node scripts/run-all-verifications.mjs --help   用法 + 步骤清单，不执行任何门禁
 *       未知参数 → 退出码 2，不执行任何门禁（见文件中部「参数解析」块）
 *
 * 依次跑十二个脚本，任一失败即整体失败，最后打印汇总与产物路径：
 *   1. scripts/verify-no-cdn.mjs           产物无外部资源引用（位置感知：只看资源位）
 *   2. scripts/verify-contract.py          前端类型 ↔ backend/app/contracts/{api,layout}.py
 *   3. scripts/verify-layout-css-vars.mjs  排版面板六控件实时驱动预览区 CSS 变量
 *   4. scripts/verify-teacher-ui.mjs       六维表单 / 打印版式 / 后端未就绪降级
 *   5. scripts/verify-library-m3.mjs       M3 卷库（分页/搜索/筛选/按卷号）与第六维落库
 *   6. scripts/verify-tokens.mjs           M3.5 判据 A1：令牌之外没有颜色字面量
 *   7. scripts/verify-structure.mjs        M3.5 判据 A2/A3/A4/A7/A8：组件状态与骨架几何
 *   8. scripts/verify-preview-scale.mjs    A9：预览是打印几何的等比缩小（进 iframe 量纸内几何）
 *   9. scripts/verify-ui-voice.mjs         视觉轮 A10/A11：无开发者文本 / 灰底只用于画布
 *  10. scripts/verify-negative-controls.mjs 负对照（V9）：故意弄红，证明上述判据能失败
 *  11. scripts/verify-ui-voice.mjs --self-test（MATHGEN_VERIFY_VOICE_SELF_TEST=1）
 *                                          门禁⑨ 自带负对照：覆盖面注入对照 + 身份预置承重
 *  12. scripts/compare-mock-and-real.mjs   mock↔真实后端逐字段比对（结构/状态码/D74 方向）。
 *                                          前置（backend/.venv 的 python、_support.py、可用端口）
 *                                          缺失时**红**并打印人话原因，不跳过、不打印 PASS。
 *
 * 另有 2 项**前置自检**（先于上面 12 步）：scripts/check-script-syntax.mjs 逐个 node --check，
 * 以及反引号整洁工具自身的 --self-test。
 *
 * 本脚本**不构建**：dist 必须由 `pnpm --dir frontend build` 先行产出。它做两件事：
 *   1. 检查 frontend/dist/index.html 是否存在；
 *   2. **断言 dist 比它的构建输入新**（scripts/dist-freshness.mjs，V13 同族）。
 * 第 2 条是必需的：第 ③④⑤⑦⑨ 步加载的是 frontend/dist/**，若 dist 陈旧，
 * 那几个浏览器门禁测的是**旧 bundle**，会给出与源码无关的绿（真发生过一次）。
 */

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync, writeFileSync, writeSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { checkDistFreshness, formatFreshnessResult, REBUILD_COMMAND } from './dist-freshness.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');
const PYTHON = path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe');

/**
 * 按「相对路径 + 文件内容」聚合 `frontend/src` + `frontend/scripts` 的 SHA-256。
 *
 * 为什么要在这里算（V8 哈希夹逼）：门禁跑完不能**改到源码本身**，否则「跑之前绿、
 * 跑之后红」这种差异没有可信的基线。本轮真的出现过一次 `T1 != T3`，而逐门禁单跑
 * 又都稳定 —— 只有把夹逼做进这条命令本身，才能定位到底哪一步动了源码。
 *
 * 这个夹逼守的是**引导（bootstrapping）问题**：用来验收源码的那套脚本自己也是源码，
 * 它们若在运行时被改写，验收结果就不可信。
 */
function hashSources() {
  const roots = [path.join(FRONTEND_DIR, 'src'), path.join(FRONTEND_DIR, 'scripts')];
  const skip = new Set(['node_modules', '.tmp-playwright', '.tmp-capture']);
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (skip.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) files.push(full);
    }
  };
  for (const root of roots) if (existsSync(root)) walk(root);
  files.sort();
  const outer = createHash('sha256');
  const rows = [];
  for (const file of files) {
    const rel = path.relative(FRONTEND_DIR, file).replace(/\\/g, '/');
    const inner = createHash('sha256').update(readFileSync(file)).digest('hex').toUpperCase();
    outer.update(`${rel}\u0000${inner}\n`);
    rows.push(`${inner}  ${rel}`);
  }
  return { count: files.length, aggregate: outer.digest('hex').toUpperCase(), rows };
}

const steps = [
  {
    name: '① 产物无外部 CDN（离线可用 NFR-9）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-no-cdn.mjs')],
  },
  {
    name: '② 契约一致性（字段名 / ROUTES / CSS 变量）',
    cmd: PYTHON,
    args: [path.join(__dirname, 'verify-contract.py')],
  },
  {
    name: '③ 排版面板实时生效（六控件 → CSS 变量）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-layout-css-vars.mjs')],
  },
  {
    name: '④ 老师端界面（六维表单 / 打印 / 后端未就绪）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-teacher-ui.mjs')],
  },
  {
    name: '⑤ M3 卷库与第六维（分页 / 搜索 / 按卷号 / answer-layout 落库）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-library-m3.mjs')],
  },
  {
    name: '⑥ M3.5 判据 A1（令牌之外没有颜色字面量）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-tokens.mjs')],
  },
  {
    name: '⑦ M3.5 判据 A2/A3/A4/A7/A8（组件状态 / 键盘 / 几何 / 视口）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-structure.mjs')],
  },
  {
    name: '⑧ A9 预览等比缩放（不得把 A4 压扁）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-preview-scale.mjs')],
  },
  {
    name: '⑨ 视觉轮 A10/A11（无开发者文本 / 灰底只用于画布）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-ui-voice.mjs')],
  },
  {
    // 故意让判据失败，证明它们真的会红（V9 / V9.1 / V9.2）。
    // 单独一个脚本、且放在最后：主门禁其余各条的意义是「全绿才算过」，
    // 而这一步的意义恰恰相反 —— 混在一起会让输出读不懂。
    name: '⑩ 负对照（故意弄红，证明判据能失败）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-negative-controls.mjs')],
  },
  {
    // 也是负对照，但归属门禁⑨自己的脚本：证明 A10/A11 的**覆盖面**是真的。
    // 与⑩分开的理由：⑩ 的每组负对照都在自己的 Python 探针里手工重演判据，
    // 而这一条必须复用**生产用的同一份扫描器**才能证明"学生端确实被扫到"，
    // 所以它跑的是 verify-ui-voice.mjs 自己的 --self-test 分支（同一服务、同一浏览器）。
    // 两项断言：① 10 个页面（老师端 5 + 学生端 5）各自注入同一段开发者文本 → 逐页必抓到；
    //           ② 故意不预置身份时 /student/practice 必须停在身份门槛。
    name: '⑪ 门禁⑨自带负对照（注入对照 + 身份预置承重）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'verify-ui-voice.mjs')],
    env: { MATHGEN_VERIFY_VOICE_SELF_TEST: '1' },
  },
  {
    // ⑫ task-32：把 mock↔真实后端的逐字段比对接进默认门禁。
    //
    // 为什么必须在这里：mock（verify-server.mjs）是**手写的第二事实来源** —— 它回答的是
    // 「我以为真实后端会返回什么」。于是①~⑪ 的全绿只能证明「我的 mock 与我的断言一致」。
    // task-27 扩 mock 时正是这么漏掉过 `answer` 整键消失（把已为 D74 剥掉答案的 wire 数据
    // 拿去判分），而当轮门禁所有断言都是绿的；唯一抓得住它的是逐字段比对。
    // 那份比对脚本此后一直只在**人记得手动跑**时才执行 —— 而一份不进默认门禁的判据
    // 等于没有判据（D69 / V9.1）。这一步就是为了终结这个状态。
    //
    // 为什么**不做**「前置缺失就跳过」：一个会静默变绿的步骤比没有这一步更坏
    // （V9/V11/V12 的核心教训）。前置（backend/.venv 的 python、backend/tests/api/_support.py、
    // 可用端口）缺失时本步必须**红**。脚本自身已经做到：缺 python → exit 2 并打印缺失路径；
    // 真实侧抛错/无产出 → exit 2 并打印 traceback；比对有差异 → exit 1 并逐条点名字段路径。
    // 所以这里**不写 try/catch、不判断输出文本**，只认退出码 —— 前置缺失必然非 0，
    // 于是被下面的 fail-fast 计为 FAIL，绝不会显示成 PASS 或 skipped。
    //
    // 代价：实测 2.5s（真实侧起一次内存 SQLite + TestClient），对本就分钟级的全量门禁可忽略。
    // 对照：它自己带负对照的另一态 `--self-test` 是 4.6s（两个世界各跑一次）。
    name: '⑫ mock↔真实后端逐字段比对（结构 / 状态码 / D74 方向，task-29）',
    cmd: process.execPath,
    args: [path.join(__dirname, 'compare-mock-and-real.mjs')],
  },
];

// ---------------------------------------------------------------------------
// 参数解析（task-31）—— 必须待在**任何副作用之前**
// ---------------------------------------------------------------------------
// 位置本身就是判据：下面会依次做「查 dist/index.html → 定位 Python → 跑两项前置
// 自检（spawn 两个脚本）→ 算 T1 源码哈希 → 逐个 spawn 11 个门禁」，全是副作用。
// `--help` / `--list` / 未知参数必须在**第一个副作用之前**结束，于是「一个门禁都
// 没跑」是**结构上成立**的事实（进程根本没走到那一步），而不是事后从输出文本里
// 推断的结论 —— 文本会骗人（步骤跑了而输出被吞、或将来有人加了个静默 spawn，
// 输出照样干净），进程状态不会。
//
// 放在 `steps` 之后是因为 --list/--help 直接复用它：另抄一份清单会随时间漂移，
// 然后「列表说 11 步、实际跑 12 步」，而两者看着都对。
//
// 输出用 writeSync(1/2) 而不是 console.log：管道下 console.log 是异步写，紧跟
// process.exit() 会把没 flush 的字节截掉，而这份 --help/--list 输出正是要被 CI
// 用管道捕获的。

const USAGE = [
  '用法：node scripts/run-all-verifications.mjs [--help | --list]',
  '',
  '  无参数      跑完整门禁（1 项前置产物判据 + 2 项前置自检 + 12 步，全绿才算通过）',
  '  -h, --help  只打印本用法与步骤清单，不执行任何门禁',
  '  --list      只按执行顺序列出步骤名与脚本路径，不执行任何门禁',
  '',
  '未知参数 → 退出码 2，且不执行任何门禁。',
  '（退出码 2 = 用法错误、门禁状态未知，与「门禁跑了但没过」的退出码 1 区分。）',
].join('\n');

const KNOWN_FLAGS = new Set(['-h', '--help', '--list']);
const argv = process.argv.slice(2);
const unknownArgs = argv.filter((a) => !KNOWN_FLAGS.has(a));

const emit = (text) => writeSync(1, text.endsWith('\n') ? text : `${text}\n`);
const emitErr = (text) => writeSync(2, text.endsWith('\n') ? text : `${text}\n`);

// 步骤清单从 `steps` 生成 —— 单一来源，不另抄一份。
const stepLines = steps
  .map((s, i) => {
    const script = s.args.find((a) => typeof a === 'string' && /\.(mjs|py)$/.test(a));
    return `  ${String(i + 1).padStart(2, ' ')}. ${s.name}\n        → ${script ? path.relative(FRONTEND_DIR, script) : '(无独立脚本)'}`;
  })
  .join('\n');

if (unknownArgs.length > 0) {
  emitErr(USAGE);
  emitErr(`\n未知参数，未执行任何门禁：${unknownArgs.join(' ')}`);
  process.exit(2);
}

if (argv.includes('--help') || argv.includes('-h')) {
  emit(`${USAGE}\n\n步骤清单（按执行顺序）：\n${stepLines}`);
  process.exit(0);
}

if (argv.includes('--list')) {
  emit(`步骤清单（按执行顺序；本命令不执行任何步骤）：\n${stepLines}`);
  process.exit(0);
}

if (!existsSync(path.join(FRONTEND_DIR, 'dist', 'index.html'))) {
  console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
  process.exit(2);
}

// ---- 前置判据：dist 必须是新的（scripts/dist-freshness.mjs，V13 同族）----
// 为什么必须在跑任何步骤**之前**：第 ③④⑤⑦⑨ 步是浏览器门禁，它们加载 frontend/dist/**。
// dist 陈旧时那些门禁测的是旧 bundle，会给出与源码无关的绿 —— 与其跑出一堆不可信的
// PASS 再在汇总里发现，不如在这一步大声红、并给出能直接照做的命令。
// 退出码取 2（与上面"缺 dist"一致）：2 = 门禁状态不可信/前置不满足，与"跑了但没过"的 1 区分。
{
  const title = '前置判据：frontend/dist 必须比构建输入新（陈旧产物 = 浏览器门禁测的是旧 bundle）';
  console.log(`\n${'='.repeat(72)}\n${title}\n${'='.repeat(72)}`);
  const freshness = checkDistFreshness({ frontendDir: FRONTEND_DIR });
  const text = formatFreshnessResult(freshness);
  if (freshness.ok) {
    console.log(text);
  } else {
    console.error(text);
    console.error(
      `\n陈旧产物：门禁状态不可信，未执行任何步骤（退出码 2）。` +
        `\n修正：${REBUILD_COMMAND} 之后重跑本命令（pnpm --dir frontend verify）。`,
    );
    process.exit(2);
  }
}

if (!existsSync(PYTHON)) {
  console.error(`找不到 ${PYTHON}（契约检查与 Playwright 都依赖它）`);
  process.exit(2);
}

// ---- 前置自检：门禁脚本自身的健康 ----
// 判据是**逐个 node --check**，而不是"扫描内联 Python 里有没有反引号"。
//
// 为什么不扫反引号（这是被实验证伪的一版，记在这里免得有人再走回去）：
//   `String.raw` 的体内**不可能**有未转义反引号 —— 那会被 JS 解析器当成模板结束，
//   文件直接语法错。所以"能活着到达体内扫描"的反引号**必然是转义过的、语法无害的**；
//   反过来，真正让模板提前终止的裸反引号，扫描器恰恰看不到（它自己就被截断了）。
//   实测对照（Lead 的探针）：
//     体内裸反引号   → node --check 失败；反引号扫描器报「没有反引号 ✅」→ 漏报
//     体内转义反引号 → node --check 通过；反引号扫描器报「会导致模板终止」→ 假报红
//   即那个扫描器在语法层面**只能抓到无害形态**。
//
// `node --check` 直接命中"模板提前终止"这**一整类**问题，是通用硬判据，
// 不依赖任何人去枚举坑的形状（与「枚举式判据永远不完整」同一个教训）。
// 它自带负对照与正对照（现造语法坏文件与好文件，断言分别红/绿），所以不是恒真断言。
{
  const preflight = [
    {
      name: '门禁脚本自检：frontend/scripts/*.mjs 逐个 node --check（含负/正对照）',
      cmd: process.execPath,
      args: [path.join(__dirname, 'check-script-syntax.mjs')],
    },
    {
      name: '门禁脚本自检：反引号整洁工具本身通过自检（只改体内、不动界定符与体外 JS）',
      cmd: process.execPath,
      args: [path.join(__dirname, 'fix-inline-python-backticks.mjs'), '--self-test'],
    },
  ];
  for (const step of preflight) {
    console.log(`\n${'='.repeat(72)}\n${step.name}\n${'='.repeat(72)}`);
    const proc = spawnSync(step.cmd, step.args, { cwd: FRONTEND_DIR, stdio: 'inherit' });
    if (proc.status !== 0) {
      console.error(
        `\n前置自检未通过：${step.name}\n` +
          `语法问题看 node --check 的原始报错；可读性约定（内联 Python 里的反引号）用：\n` +
          `  node scripts/fix-inline-python-backticks.mjs --write`,
      );
      process.exit(2);
    }
  }
}

const results = [];
const sourcesBefore = hashSources();
console.log(
  `\n源码夹逼（运行前）：${sourcesBefore.count} 个文件，聚合 ${sourcesBefore.aggregate}`,
);
for (const step of steps) {
  console.log(`\n${'='.repeat(72)}\n${step.name}\n${'='.repeat(72)}`);
  const proc = spawnSync(step.cmd, step.args, {
    cwd: FRONTEND_DIR,
    stdio: 'inherit',
    // step.env 用于少数需要开关的步骤（如⑪ 门禁⑨自带负对照）；其余步骤不受影响。
    env: { ...process.env, PYTHONUTF8: '1', ...(step.env ?? {}) },
  });
  results.push({ ...step, code: proc.status });
  if (proc.status !== 0) break;
}

console.log(`\n${'='.repeat(72)}\n汇总\n${'='.repeat(72)}`);
for (const r of results) {
  console.log(`  ${r.code === 0 ? 'PASS' : 'FAIL'}  ${r.name}`);
}
const failed = results.filter((r) => r.code !== 0);
const skipped = steps.filter((step) => !results.some((r) => r.name === step.name));

// ---- 源码夹逼（V8）：跑完必须与跑之前逐字节相同 ----
const sourcesAfter = hashSources();
console.log(
  `\n源码夹逼（运行后）：${sourcesAfter.count} 个文件，聚合 ${sourcesAfter.aggregate}`,
);
const sourcesIntact =
  sourcesBefore.aggregate === sourcesAfter.aggregate &&
  sourcesBefore.count === sourcesAfter.count;
console.log(
  `  ${sourcesIntact ? 'PASS' : 'FAIL'}  源码在门禁运行前后未被改写（T1 == T3）`,
);
if (!sourcesIntact) {
  const before = new Map(sourcesBefore.rows.map((line) => line.split('  ').reverse()));
  const after = new Map(sourcesAfter.rows.map((line) => line.split('  ').reverse()));
  for (const [rel, hash] of before) {
    if (after.get(rel) !== hash) console.log(`    (changed) ${rel}`);
  }
  for (const rel of after.keys()) if (!before.has(rel)) console.log(`    (added)   ${rel}`);
  for (const rel of before.keys()) if (!after.has(rel)) console.log(`    (removed) ${rel}`);
  // 落盘快照，方便事后逐文件比对（写进忽略目录，不进源码树）
  const snapshotDir = path.join(FRONTEND_DIR, '.tmp-capture');
  if (existsSync(snapshotDir)) {
    writeFileSync(
      path.join(snapshotDir, 'sources-before.txt'),
      sourcesBefore.rows.join('\n') + '\n',
      'utf8',
    );
    writeFileSync(
      path.join(snapshotDir, 'sources-after.txt'),
      sourcesAfter.rows.join('\n') + '\n',
      'utf8',
    );
  }
}
// 注意这里必须**分别**报「失败的」与「没跑的」：
// 主循环是 fail-fast（`if (proc.status !== 0) break;`），所以只要有一条红，
// 后面的步骤就不会跑，于是 `results.length !== steps.length` 也同时成立。
// 早期版本只打印 `未通过：${failed.length} 项` —— 在"一条红 + 若干没跑"时
// 这个数字会**低估**实际受影响的范围，读的人容易以为只坏了一条。
if (failed.length > 0 || skipped.length > 0 || !sourcesIntact) {
  console.log(
    `\n未通过：${failed.length} 项；未运行（因 fail-fast 中断）：${skipped.length} 项` +
      (sourcesIntact ? '' : '；源码夹逼未通过：门禁运行改写了 frontend/src 或 frontend/scripts'),
  );
  for (const r of failed) console.log(`  (FAIL)    ${r.name}`);
  for (const step of skipped) console.log(`  (skipped) ${step.name}`);
  process.exit(1);
}
console.log('\n全部验收通过。产物：');
console.log('  frontend/dist/                          构建产物（离线可用，无 CDN 引用）');
console.log('  frontend/verify-layout-preview.png      排版面板调参后的预览截图');
console.log('  frontend/verify-config-form.png         六维配置表单截图');
console.log('  frontend/verify-structure-1440x900.png  C1 骨架（1440×900）');
console.log('  frontend/verify-structure-1280x900.png  C1 骨架（1280×900，侧栏收窄）');
console.log('  frontend/verify-a9-after-1440x900.png   A9 修后：预览等比缩放（对比 shot-struct-paper.png 是修前）');
console.log('  frontend/shot-focus-ring-1440x900.png   键盘 Tab 到卷面时的焦点环');
console.log('  frontend/verify-voice-config-1440x900.png            视觉轮：配置页居中 + 浅底');
console.log('  frontend/verify-voice-library-1440x900.png           视觉轮：卷库页浅底 + 内容列居中');
console.log('  frontend/verify-voice-config-1280x900.png            视觉轮：1280 视口（不溢出）');
console.log('\n视觉轮改前/改后对比（用户反馈的三条）：');
console.log('  改前：frontend/vis-before-{config,library,paper}-1440.png');
console.log('  改后：frontend/vis-after-{config,library,paper}-1440.png');
console.log('\n负对照（⑩）可单独重跑，用来证明判据真的会红：');
console.log('  & $NODE $PNPM --dir frontend verify:negative');
console.log('  （见 scripts/verify-negative-controls.mjs 头注释：V9/V9.1/V9.2 的由来）');
