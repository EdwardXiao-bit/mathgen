/**
 * 产物「无外部 CDN」门禁（NFR-9 / 红线 6：离线可用）。
 *
 * ── 判定口径（改过一版，判据写在这里，免得下一个人以为只是「把红改成绿」）──
 *
 * 第一版的做法是「产物里出现任何 http(s):// 就算违规」，只用白名单放行几条已知的
 * W3C 命名空间与 React 报错地址。这个口径**既误报又误导**：
 * 实测它把 `http://127.0.0.1:8000` 判成「产物引用了外部地址」，而那个字符串的真实上下文是
 *
 *     function nd(){ return typeof window>"u" ? "http://127.0.0.1:8000" : window.location.origin }
 *
 * 即**非浏览器环境（SSR/测试）的兜底常量**；浏览器里永远走 `window.location.origin`。
 * 而且同源托管时 `window.location.origin` 正是正确选择（局域网 IP 打开发卷页时 API 指向同一源）。
 *
 * 所以现在的判据是**位置感知**的 —— 判「这个 URL 出现在资源位吗」，而不是判「出现过没有」：
 *
 *   A. **只把资源位的 URL 当违规**：
 *      - HTML 属性：`src=` / `<link href=>` / `<use href=>` / `srcset=` / `poster=` /
 *        `action=` / `formaction=` / `data=`
 *      - **`href` 按标签区分**（第二版修正）：`<link href>` 会加载样式表并产生请求，
 *        `<a href>` 只是一次导航、用户不点就不发请求。欢迎页页脚指向仓库的那条
 *        `<a href="https://github.com/...">` 曾被判红，属于判据把「会不会自动产生请求」
 *        错当成「出现过 href」。
 *      - CSS：`url(...)` / `@import`
 *      - JS 网络原语：`fetch(...)` / `import(...)` / `new Worker(...)` / `new Image().src=...`
 *        等，且**参数是绝对 http(s) URL 字面量**（相对路径不算，跨域才算）
 *   B. **出现在普通字符串常量 / 提示文案里的 URL 不计违规**：它们不产生网络请求，
 *      逐条打印为 `[TEXT]`（带文件:行号与上下文），便于人工复核，但**不影响退出码**。
 *   C. W3C 命名空间 / React 报错地址这类已知非资源常量仍走 `[ALLOW]`。
 *
 * 这样既不放过真实的外部资源引用（资源位是唯一会产生请求的形态），也不会因为
 * 提示文案里写了一个自己的地址就把门禁判红。
 *
 * 用法：node scripts/verify-no-cdn.mjs
 */

import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.resolve(__dirname, '..', 'dist');

/**
 * 允许出现的 URL，**每一条都必须能说清「为什么运行时不产生请求」**：
 *
 * A. W3C 命名空间 / 规范标识符 —— 只是 XML/SVG 的命名空间字符串常量。
 * B. React 压缩产物的错误提示 URL —— 只在控制台显示出错误文档地址，从不自动请求。
 *
 * 新增白名单条目必须在交接报告里写明理由并给出 bundle 上下文。
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

/**
 * **资源位**：只有落在这些形态里的绝对 URL 才可能产生网络请求。
 *
 * 每条都对应「浏览器/运行时真的会去取这个地址」的语义，避免把字符串常量算进来。
 * 注意 `import(` / `require(` / `new Worker(` 等只匹配**绝对** http(s) 字面量：
 * 打包产物里的相对 chunk 路径（`import("./assets/x.js")`）不是外部依赖。
 */
const RESOURCE_SLOTS = [
  // HTML 属性（单双引号、无引号都覆盖）
  { name: 'HTML src=', re: /\bsrc\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  // `href` 要分语境：`<link href>` 是**加载**样式表/图标，会产生请求；
  // `<a href>` 是**导航**，只有用户点击才离开本页，不破坏离线可用。
  // 第一版把两者一起判，结果页脚一条指向仓库的 `<a href>` 就被判红——
  // 判的是「会不会自动产生请求」，所以按标签区分，而不是按属性名区分。
  { name: 'HTML <link href=>', re: /<link\b[^>]*\bhref\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  { name: 'HTML <use href=>', re: /<use\b[^>]*\bhref\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  { name: 'HTML srcset=', re: /\bsrcset\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  { name: 'HTML poster=', re: /\bposter\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  { name: 'HTML action=', re: /\baction\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  { name: 'HTML formaction=', re: /\bformaction\s*=\s*["']?(https?:\/\/[^"'\s>]+)/gi },
  // CSS
  { name: 'CSS url()', re: /url\(\s*["']?(https?:\/\/[^"')]+)/gi },
  { name: 'CSS @import', re: /@import\s+(?:url\()?\s*["']?(https?:\/\/[^"')]+)/gi },
  // JS 网络原语（**绝对 URL 字面量**才算）
  { name: 'JS fetch()', re: /\bfetch\(\s*["'`](https?:\/\/[^"'`]+)/g },
  { name: 'JS import()', re: /\bimport\(\s*["'`](https?:\/\/[^"'`]+)/g },
  { name: 'JS new Worker()', re: /\bnew\s+Worker\(\s*["'`](https?:\/\/[^"'`]+)/g },
  { name: 'JS XHR.open()', re: /\.open\(\s*["'][A-Z]+["']\s*,\s*["'`](https?:\/\/[^"'`]+)/g },
  { name: 'JS location 赋值', re: /(?:location(?:\.href)?|window\.location(?:\.href)?)\s*=\s*["'`](https?:\/\/[^"'`]+)/g },
  { name: 'JS window.open()', re: /\bwindow\.open\(\s*["'`](https?:\/\/[^"'`]+)/g },
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

/** 一行里落在「资源位」的 URL 原文集合（自测与主扫描共用同一套规则）。 */
function resourceUrlsIn(line) {
  const urls = new Set();
  for (const slot of RESOURCE_SLOTS) {
    slot.re.lastIndex = 0;
    let m;
    while ((m = slot.re.exec(line)) !== null) urls.add(m[1]);
  }
  return urls;
}

/**
 * 自测（V9.1：判据自己也要能被证明会红）。
 *
 * 只放宽 `href` 那条规则而不带负对照，等于「把红改成绿还不留证据」。
 * 这里用合成行把两种语境各钉一条，随门禁一起跑（`--self-test` 可单跑）。
 */
const SELF_TEST_CASES = [
  { line: '<link rel="stylesheet" href="https://cdn.example.com/x.css">', expect: true, why: '外部样式表会被加载' },
  { line: '<use href="https://cdn.example.com/sprite.svg#i">', expect: true, why: '外部 sprite 会被加载' },
  { line: '<img src="https://cdn.example.com/a.png" alt="">', expect: true, why: '外部图片会被加载' },
  { line: 'background: url(https://fonts.example.com/x.woff2)', expect: true, why: '外部字体会被加载' },
  { line: '<a href="https://github.com/EdwardXiao-bit/mathgen" target="_blank">', expect: false, why: '普通链接只是导航，不自动请求' },
  { line: '<a href="/teacher">出题</a>', expect: false, why: '站内相对链接不受影响' },
];

function runSelfTest(log) {
  let failed = 0;
  for (const testCase of SELF_TEST_CASES) {
    const flagged = resourceUrlsIn(testCase.line).size > 0;
    const ok = flagged === testCase.expect;
    if (!ok) failed += 1;
    const verdict = ok ? 'PASS' : `FAIL（期望${testCase.expect ? '判红' : '判绿'}，实际${flagged ? '判红' : '判绿'}）`;
    log(`  [${verdict}] ${testCase.why}`);
  }
  return failed;
}

const SELF_TEST_ONLY = process.argv.includes('--self-test');

if (SELF_TEST_ONLY) {
  console.log('资源位规则自测：');
  const failures = runSelfTest(console.log);
  console.log(failures === 0 ? '\n结论：6 条自测全部通过。' : `\n结论：${failures} 条自测失败。`);
  process.exit(failures === 0 ? 0 : 1);
}

const selfTestFailures = (() => {
  console.log('资源位规则自测（负对照）：');
  return runSelfTest(console.log);
})();

const files = await walk(DIST_DIR);
const offenders = [];
const allowedHits = [];
const textHits = [];
const totalUrls = { count: 0 };

for (const file of files) {
  const text = await readFile(file, 'utf8');
  const lines = text.split(/\r?\n/);
  lines.forEach((line, index) => {
    const matches = line.match(URL_RE);
    if (!matches) return;

    // 本行里「资源位」覆盖到的 URL 原文集合
    const resourceUrls = resourceUrlsIn(line);

    for (const url of matches) {
      totalUrls.count += 1;
      // 剥掉 JSON/字符串语境里的尾随字符，并按「scheme://host/path」截断
      const cleaned = url.replace(/["'`,;)\]}]+$/, '').replace(/([?&][^?#\s]*)?#.*$/, '');
      const normalized = cleaned.replace(/[?&].*$/, (query) => (query.startsWith('?invariant=') ? query : ''));
      const hit = ALLOWED.find((entry) => entry.pattern.test(normalized) || entry.pattern.test(cleaned));
      if (hit) {
        allowedHits.push({ file: path.relative(DIST_DIR, file), line: index + 1, url: cleaned, reason: hit.reason });
        continue;
      }
      // 位置感知：只有在资源位出现才算违规
      const inResourceSlot = [...resourceUrls].some(
        (resourceUrl) => resourceUrl === url || resourceUrl.startsWith(url) || url.startsWith(resourceUrl),
      );
      if (inResourceSlot) {
        offenders.push({ file: path.relative(DIST_DIR, file), line: index + 1, url: cleaned });
      } else {
        textHits.push({ file: path.relative(DIST_DIR, file), line: index + 1, url: cleaned, lineText: line.trim().slice(0, 160) });
      }
    }
  });
}

console.log(`扫描 dist 下 ${files.length} 个文本文件，共发现 ${totalUrls.count} 处 http(s):// 形态字符串。`);
console.log('判据：只有出现在**资源位**（src=/href=/url()/@import/fetch()/import() 等，见脚本头注释）的绝对 URL 才算违规。');

if (allowedHits.length > 0) {
  console.log(`\n其中 ${allowedHits.length} 处是**已知非资源性常量**（予以放行，理由如下）：`);
  const seen = new Map();
  for (const hit of allowedHits) {
    const key = hit.url.replace(/\d+$/, '');
    if (seen.has(key)) continue;
    seen.set(key, hit);
    console.log(`  [ALLOW] ${hit.url}\n          理由：${hit.reason}\n          位置：${hit.file}:${hit.line}`);
  }
}

if (textHits.length > 0) {
  console.log(`\n其中 ${textHits.length} 处是**字符串常量 / 提示文案里的 URL**（非资源位，不产生请求，不计违规）：`);
  const seen = new Set();
  for (const hit of textHits) {
    // 同一 URL 只详细打印一次，其余只记位置，避免刷屏
    if (seen.has(hit.url)) continue;
    seen.add(hit.url);
    console.log(`  [TEXT] ${hit.url}\n         位置：${hit.file}:${hit.line}\n         上下文：${hit.lineText}`);
  }
  console.log(`  （共 ${textHits.length} 处，去重后 ${seen.size} 个不同 URL）`);
  console.log(
    '  说明：这类 URL 不产生网络请求，所以**不计入失败**；但逐条打印以便复核。\n' +
      '  典型例子：非浏览器环境的 API 兜底常量 `typeof window>"u" ? "http://127.0.0.1:8000" : window.location.origin`，\n' +
      '  浏览器里走 `window.location.origin`（同源托管时这是正确选择，不要改）。',
  );
}

if (offenders.length > 0) {
  console.log(`\n发现 ${offenders.length} 处**资源位的外部引用**（判失败）：`);
  for (const hit of offenders) {
    console.log(`  [FAIL] ${hit.file}:${hit.line}  ${hit.url}`);
  }
  console.log('\n结论：产物在资源位引用了外部地址，破坏离线可用（NFR-9 / 红线 6）。');
  process.exit(1);
}

if (selfTestFailures > 0) {
  console.log(`\n结论：资源位规则自测有 ${selfTestFailures} 条失败，判据本身不可信。`);
  process.exit(1);
}

console.log('\n结论：产物在资源位不引用任何外部 CDN / 在线字体 / 外部 API（NFR-9 通过）。');
