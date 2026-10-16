#!/usr/bin/env node
/**
 * mathgen-cli —— mathgen 的 npm 启动器。
 *
 *   npx mathgen-cli@1.0.0-rc1            # 直接跑（首次会准备环境，之后秒起）
 *   npx mathgen-cli@1.0.0-rc1 --host 0.0.0.0   # 让局域网里的学生也能连
 *
 * 包名为什么是 mathgen-cli 而不是 mathgen：npm 判定 `mathgen` 与早已存在的 `math-gen`
 * 太像（去标点后同名），`npm publish mathgen` 直接 403。包名换了，里面转交的命令仍是 `mathgen`。
 *
 * 它**不是**把 Python 后端重新实现一遍，也不自带运行时：Python 应用没法在 Node 里跑。
 * 它做的是「把 Python 那套东西装到用户目录下，然后把参数转交给 `mathgen` 命令」：
 *
 *   1. 找一个 Python 3.12+（没有就明确告诉用户去哪装）
 *   2. 在 ~/.mathgen/npm-<版本>/venv 建一个**独立**虚拟环境
 *   3. 从 GitHub Release 装后端 wheel（**wheel 里自带前端产物**，不用再下第二次）
 *   4. 需要的话下 Chromium（PDF 导出用）
 *   5. exec `mathgen`，把命令行参数原样传过去
 *
 * 为什么从 Release 装而不是 `pip install mathgen-backend`：这个项目**没有发 PyPI**
 * （PyPI 上的 `mathgen` 是别人的包）。Release 里的 wheel 是同一个构建产物，只是换个下载地址。
 *
 * 刻意不做：不用 root、不动系统 Python、不写全局配置、不常驻后台。
 */
'use strict'

const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const https = require('node:https')
const os = require('node:os')
const path = require('node:path')

const OWNER_REPO = 'EdwardXiao-bit/mathgen'
const { version } = require('./package.json')
const RELEASE_BASE = `https://github.com/${OWNER_REPO}/releases/download/v${version}`
const WHEEL = `mathgen_backend-${version.replace(/-/g, '')}-py3-none-any.whl`
const ROOT = path.join(os.homedir(), '.mathgen')
const HOME = path.join(ROOT, `npm-${version}`)
const VENV = path.join(HOME, 'venv')
const PY = path.join(VENV, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const MATHGEN = path.join(VENV, process.platform === 'win32' ? 'Scripts/mathgen.exe' : 'bin/mathgen')
const STAMP = path.join(HOME, '.installed')

const say = (text = '') => console.log(text)

function die(text, code = 1) {
  console.error(`\n  ${text}\n`)
  process.exit(code)
}

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { stdio: 'inherit', ...opts })
}

// ------------------------------------------------------------------ 1. Python

function findPython() {
  const candidates = [['py', ['-3.12']], ['python3.12', []], ['python3', []], ['python', []]]
  for (const [exe, prefix] of candidates) {
    // 这里必须 shell:false。Windows 上走 shell 会把 args 只做字符串拼接（Node 自己为此发 DEP0190
    // 警告），`-c` 里的引号与分号会被 cmd.exe 吃掉 —— 实测 shell:true 时四个候选全部 status=1、
    // 一个都探不到；shell:false 时 `python` 正常返回 "3.12"。参数按数组传给 CreateProcess，
    // 不经过任何 shell，才不会被引号、分号或路径里的空格影响。
    const probe = spawnSync(exe, [...prefix, '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], {
      encoding: 'utf8',
      shell: false,
    })
    if (probe.status === 0 && probe.stdout) {
      const [major, minor] = probe.stdout.trim().split('.').map(Number)
      if (major > 3 || (major === 3 && minor >= 12)) return { exe, prefix }
    }
  }
  return null
}

// ------------------------------------------------------------------ 2. 下载

function download(url, dest) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: { 'user-agent': `mathgen-npm/${version}` } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume()
          return download(res.headers.location, dest).then(resolve, reject)
        }
        if (res.statusCode !== 200) {
          res.resume()
          return reject(new Error(`HTTP ${res.statusCode} — ${url}`))
        }
        const tmp = `${dest}.part`
        const out = fs.createWriteStream(tmp)
        res.pipe(out)
        out.on('finish', () => {
          out.close(() => {
            fs.renameSync(tmp, dest)
            resolve(dest)
          })
        })
        out.on('error', reject)
      })
      .on('error', reject)
  })
}

// ------------------------------------------------------------------ 3. 装配

async function ensureInstalled() {
  if (fs.existsSync(STAMP)) return

  say(`\n首次运行：把服务装到 ${HOME}`)
  say('（只装一次，以后 npx mathgen-cli 直接起）\n')

  const python = findPython()
  if (!python) {
    die(
      '没有找到 Python 3.12 或更高版本。\n' +
        '  mathgen 的后端是 Python 写的，需要先装它：https://www.python.org/downloads/\n' +
        '  （macOS：brew install python@3.12   Debian/Ubuntu：sudo apt install python3.12 python3.12-venv）'
    )
  }
  say(`  [1/4] Python：${python.exe} ${python.prefix.join(' ')}`.trimEnd())

  fs.mkdirSync(HOME, { recursive: true })
  say(`  [2/4] 建虚拟环境 ${VENV}`)
  // 同样 shell:false：VENV 与 wheelPath 都是可能含空格的绝对路径（Windows 用户名里带空格很常见），
  // 走 shell 时它们不会被加引号。
  if (run(python.exe, [...python.prefix, '-m', 'venv', VENV], { shell: false }).status !== 0) {
    die('创建虚拟环境失败。')
  }

  const wheelPath = path.join(HOME, WHEEL)
  const local = process.env.MATHGEN_WHEEL_PATH
  const override = process.env.MATHGEN_WHEEL_URL
  if (local) {
    say(`  [3/4] 使用本地 wheel ${local}`)
    if (!fs.existsSync(local)) die(`MATHGEN_WHEEL_PATH 指向的文件不存在：${local}`)
    fs.copyFileSync(local, wheelPath)
  } else {
    const url = override || `${RELEASE_BASE}/${WHEEL}`
    say(`  [3/4] 下载后端 ${WHEEL}`)
    try {
      await download(url, wheelPath)
    } catch (err) {
      die(
        `下载失败：${err.message}\n` +
          `  可以手动下载后重试：${RELEASE_BASE}\n` +
          `  或者改用源码方式：git clone https://github.com/${OWNER_REPO}.git\n` +
          `  （也可用 MATHGEN_WHEEL_URL / MATHGEN_WHEEL_PATH 指定别处的构建产物）`
      )
    }
  }

  // 刻意不跑 `pip install --upgrade pip`：在 Windows + 3.12 上实测它会长时间空转不下载
  // （三次独立复现），而 venv 自带的 pip 处理 `pip install <wheel>` 完全够用。
  // 要换源就设 PIP_INDEX_URL，pip 会从环境里读。
  if (
    run(PY, ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', wheelPath]).status !== 0
  ) {
    die(
      '安装后端失败。常见原因：网络到不了 PyPI。\n' +
        "  可先设镜像再重跑：set PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
    )
  }

  say('  [4/4] 准备 Chromium（PDF 导出用，约 150 MB）')
  if (run(PY, ['-c', 'import app.export.browser as b,sys; sys.exit(0 if b.is_available() else 1)'], {
    stdio: 'ignore',
  }).status !== 0) {
    if (run(PY, ['-m', 'playwright', 'install', 'chromium']).status !== 0) {
      say('  （下载失败：PDF 导出会返回 503，其余功能正常，可稍后重跑）')
    }
  } else {
    say('  Chromium 已就绪')
  }

  fs.writeFileSync(STAMP, new Date().toISOString())
  say('\n装好了。\n')
}

// ------------------------------------------------------------------ 入口

async function main() {
  await ensureInstalled()
  const result = run(MATHGEN, process.argv.slice(2), { shell: false })
  if (result.error) die(`启动失败：${result.error.message}`)
  process.exit(result.status === null ? 1 : result.status)
}

main().catch((err) => die(err.stack || String(err)))
