/**
 * 一条命令跑完全部前端验收（门禁）。
 *
 * 用法：& $NODE $PNPM --dir frontend verify      （等价于 node scripts/run-all-verifications.mjs）
 *
 * 依次跑十个脚本，任一失败即整体失败，最后打印汇总与产物路径：
 *   1. scripts/verify-no-cdn.mjs           产物无外部资源引用（位置感知：只看资源位）
 *   2. scripts/verify-contract.py          前端类型 ↔ backend/app/contracts/{api,layout}.py
 *   3. scripts/verify-layout-css-vars.mjs  排版面板六控件实时驱动预览区 CSS 变量
 *   4. scripts/verify-teacher-ui.mjs       六维表单 / 打印版式 / 后端未就绪降级
 *   5. scripts/verify-library-m3.mjs       M3 卷库（分页/搜索/筛选/按卷号）与第六维落库
 *   6. scripts/verify-tokens.mjs           M3.5 判据 A1：令牌之外没有颜色字面量
 *   7. scripts/verify-structure.mjs        M3.5 判据 A2/A3/A4/A7/A8：组件状态与骨架几何
 *   8. scripts/verify-preview-scale.mjs    A9：预览是打印几何的等比缩小（进 iframe 量纸内几何）
 *  10. scripts/verify-ui-voice.mjs         视觉轮 A10/A11：无开发者文本 / 灰底只用于画布
 *   9. scripts/verify-negative-controls.mjs 负对照（V9）：故意弄红，证明上述判据能失败
 */

import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');
const PYTHON = path.join(REPO_ROOT, 'backend', '.venv', 'Scripts', 'python.exe');

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
];

if (!existsSync(path.join(FRONTEND_DIR, 'dist', 'index.html'))) {
  console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
  process.exit(2);
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
for (const step of steps) {
  console.log(`\n${'='.repeat(72)}\n${step.name}\n${'='.repeat(72)}`);
  const proc = spawnSync(step.cmd, step.args, {
    cwd: FRONTEND_DIR,
    stdio: 'inherit',
    env: { ...process.env, PYTHONUTF8: '1' },
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
// 注意这里必须**分别**报「失败的」与「没跑的」：
// 主循环是 fail-fast（`if (proc.status !== 0) break;`），所以只要有一条红，
// 后面的步骤就不会跑，于是 `results.length !== steps.length` 也同时成立。
// 早期版本只打印 `未通过：${failed.length} 项` —— 在"一条红 + 若干没跑"时
// 这个数字会**低估**实际受影响的范围，读的人容易以为只坏了一条。
if (failed.length > 0 || skipped.length > 0) {
  console.log(
    `\n未通过：${failed.length} 项；未运行（因 fail-fast 中断）：${skipped.length} 项`,
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
