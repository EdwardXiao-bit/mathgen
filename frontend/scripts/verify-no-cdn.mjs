/**
 * 产物「无外部 CDN」门禁（NFR-9 / 红线 6：离线可用）。
 *
 * 验收标准要求「构建后 grep 产物里的 http:// / https://」。
 * 人肉 grep 有两个问题：一是会漏（只看文件名不看上下文），二是会误报——
 * React 的 DOM 代码里本来就含 W3C **命名空间标识符**（`http://www.w3.org/1999/xhtml` 等），
 * 它们只是字符串常量，不会产生任何网络请求。
 *
 * 所以这里的判定是：
 *   1. 扫 dist 下**所有文本文件**里的完整 URL；
 *   2. 白名单只放行「W3C 命名空间 / 规范标识」（语义上不是可获取资源）；
 *   3. 其余任何 http(s) URL 一律判失败，并把文件名 + 行号 + URL 打出来。
 *
 * 用法：node scripts/verify-no-cdn.mjs
 */

import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.resolve(__dirname, '..', 'dist');

/**
 * 允许出现的 URL，分两类，**每一条都必须能说清「为什么运行时不产生请求」**：
 *
 * A. W3C 命名空间 / 规范标识符 —— 只是 XML/SVG 的命名空间字符串常量。
 * B. React 压缩产物的错误提示 URL —— React 把
 *    `"https://reactjs.org/docs/error-decoder.html?invariant=" + code` 拼进**异常 message**，
 *    只在控制台显示出错误文档地址，从不自动请求（已核对上下文：位于
 *    `function C(e){...return "Minified React error #"+e+"; visit "+t+...}` 内）。
 *
 * 新增白名单条目必须在交接报告里写明理由并给出 bundle 上下文——不允许悄悄放真实资源进来。
 */
const ALLOWED = [
  {
    reason: 'W3C 命名空间标识符（XML/SVG/MathML/RDF），不是可获取资源',
    pattern:
      /^https?:\/\/www\.w3\.org\/(1999\/xhtml|1999\/xlink|2000\/svg|1998\/Math\/MathML|2000\/xmlns\/?|2000\/01\/rdf-schema#|XML\/1998\/namespace)$/,
  },
  {
    reason: 'React 压缩产物的错误文档地址（只出现在异常 message 文本里，不会自动请求）',
    pattern: /^https:\/\/reactjs\.org\/docs\/error-decoder\.html\?invariant=\d*$/,
  },
];

const TEXT_EXTENSIONS = new Set(['.html', '.js', '.css', '.json', '.svg', '.txt', '.map', '.webmanifest']);

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(full)));
    else if (TEXT_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) files.push(full);
  }
  return files;
}

const URL_RE = /https?:\/\/[^\s"'`)<>\\]+/g;

const files = await walk(DIST_DIR);
const offenders = [];
const allowedHits = [];
const totalUrls = { count: 0 };

for (const file of files) {
  const text = await readFile(file, 'utf8');
  const lines = text.split(/\r?\n/);
  lines.forEach((line, index) => {
    const matches = line.match(URL_RE);
    if (!matches) return;
    for (const url of matches) {
      totalUrls.count += 1;
      // 剥掉 JSON/字符串语境里的尾随字符，并按「scheme://host/path」截断，
      // 避免把 `...?invariant=` 后面的代码与 `&args[]=` 混进 URL
      const cleaned = url.replace(/["'`,;)\]}]+$/, '').replace(/([?&][^?#\s]*)?#.*$/, '');
      const normalized = cleaned.replace(/[?&].*$/, (query) => (query.startsWith('?invariant=') ? query : ''));
      const hit = ALLOWED.find((entry) => entry.pattern.test(normalized) || entry.pattern.test(cleaned));
      if (hit) {
        allowedHits.push({ file: path.relative(DIST_DIR, file), line: index + 1, url: cleaned, reason: hit.reason });
      } else {
        offenders.push({ file: path.relative(DIST_DIR, file), line: index + 1, url: cleaned });
      }
    }
  });
}

console.log(`扫描 dist 下 ${files.length} 个文本文件，共发现 ${totalUrls.count} 处 http(s):// 形态字符串。`);
if (allowedHits.length > 0) {
  console.log(`\n其中 ${allowedHits.length} 处是**非资源性字符串常量**（予以放行，理由如下）：`);
  const seen = new Map();
  for (const hit of allowedHits) {
    const key = hit.url.replace(/\d+$/, '');
    if (seen.has(key)) continue;
    seen.set(key, hit);
    console.log(`  [ALLOW] ${hit.url}\n          理由：${hit.reason}\n          位置：${hit.file}:${hit.line}`);
  }
}

if (offenders.length > 0) {
  console.log(`\n发现 ${offenders.length} 处疑似外部资源引用（判失败）：`);
  for (const hit of offenders) {
    console.log(`  [FAIL] ${hit.file}:${hit.line}  ${hit.url}`);
  }
  console.log('\n结论：产物引用了外部地址，破坏离线可用（NFR-9 / 红线 6）。');
  process.exit(1);
}

console.log('\n结论：产物不引用任何外部 CDN / 在线字体 / 外部 API（NFR-9 通过）。');
