/**
 * 一条命令跑完全部前端验收（门禁）。
 *
 * 用法：& $NODE $PNPM --dir frontend verify      （等价于 node scripts/run-all-verifications.mjs）
 *
 * 依次跑八个脚本，任一失败即整体失败，最后打印汇总与产物路径：
 *   1. scripts/verify-no-cdn.mjs           产物无外部资源引用（位置感知：只看资源位）
 *   2. scripts/verify-contract.py          前端类型 ↔ backend/app/contracts/{api,layout}.py
 *   3. scripts/verify-layout-css-vars.mjs  排版面板六控件实时驱动预览区 CSS 变量
 *   4. scripts/verify-teacher-ui.mjs       六维表单 / 打印版式 / 后端未就绪降级
 *   5. scripts/verify-library-m3.mjs       M3 卷库（分页/搜索/筛选/按卷号）与第六维落库
 *   6. scripts/verify-tokens.mjs           M3.5 判据 A1：令牌之外没有颜色字面量
 *   7. scripts/verify-structure.mjs        M3.5 判据 A2/A3/A4/A7/A8：组件状态与骨架几何
 *   8. scripts/verify-preview-scale.mjs    A9：预览是打印几何的等比缩小（进 iframe 量纸内几何）
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
];

if (!existsSync(path.join(FRONTEND_DIR, 'dist', 'index.html'))) {
  console.error('找不到 frontend/dist/index.html —— 请先运行：pnpm --dir frontend build');
  process.exit(2);
}
if (!existsSync(PYTHON)) {
  console.error(`找不到 ${PYTHON}（契约检查与 Playwright 都依赖它）`);
  process.exit(2);
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
if (failed.length > 0 || results.length !== steps.length) {
  console.log(`\n未通过：${failed.length} 项（未跑的用 (skipped) 表示）`);
  for (const step of steps) {
    if (!results.some((r) => r.name === step.name)) console.log(`  (skipped) ${step.name}`);
  }
  process.exit(1);
}
console.log('\n全部验收通过。产物：');
console.log('  frontend/dist/                          构建产物（离线可用，无 CDN 引用）');
console.log('  frontend/verify-layout-preview.png      排版面板调参后的预览截图');
console.log('  frontend/verify-config-form.png         六维配置表单截图');
