"""M3.5 结构轮独立验收套件（verify 写域，task-16）。

验收依据：`docs/07-设计规范.md` §6 判据 **A1–A8**；对象：git `0a7ae8e`。

与实现方自测的关系：**互不调用**。
  * A1 用我自己的 Python 扫描器（`scan_tokens.py`，不设扩展名白名单）+ 与冻结版逐字节比对；
  * A2 我自己解析 `styles.css` 的规则体，断言「状态选择器存在」**且**「取值来自 `var(--mg-*)`」；
  * A3/A4/A6/A7/A8 我自己起服务、自己驱动 Chromium（A4 用一个**自建的 500 桩服务**）。

运行时前置：`frontend/dist` 必须比 `frontend/src` 新（本模块在需要时自动 `pnpm build`）。

    & $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -v
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import unittest
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
DIST = os.path.join(REPO, "frontend", "dist")
SRC = os.path.join(REPO, "frontend", "src")
STYLES = os.path.join(SRC, "styles.css")
TOKENS = os.path.join(SRC, "design", "tokens.css")
FROZEN_COMMIT = "865eb8e"          # Lead 冻结 tokens.css 的提交
HEAD_COMMIT = "0a7ae8e"            # 本轮验收对象
sys.path.insert(0, HERE)
os.makedirs(ARTIFACTS, exist_ok=True)

VPY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(VPY):
    VPY = sys.executable

# 浏览器通道：**只走产品自己的** `app.export.browser`（task-22）。理由见 `browser_page`。
# 复用 verify 侧已有的引导模块 `tests/e2e_m2/_browser.py`（它负责把仓库内的 `.playwright`
# 指给 Playwright，并把产品的 `browser_session` / `session` 原样 re-export 出来）。
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
from tests._uvicorn_boot import boot_uvicorn, free_port  # noqa: E402,F401
from tests.e2e_m2._browser import browser_session, session  # noqa: E402


# --------------------------------------------------------------------------
# 前置：构建产物必须比源码新
# --------------------------------------------------------------------------

def _newest_mtime(root: str) -> float:
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", "__pycache__"}]
        for name in filenames:
            newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
    return newest


def ensure_dist_fresh() -> str:
    index = os.path.join(DIST, "index.html")
    if os.path.exists(index) and os.path.getmtime(index) >= _newest_mtime(SRC):
        return "dist 已是最新（无需重建）"
    result = subprocess.run(["pnpm", "--dir", "frontend", "build"], cwd=REPO,
                            capture_output=True, text=True, shell=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"前端构建失败：{result.stderr[-500:]}")
    return "已重新构建 dist"


# --------------------------------------------------------------------------
# 服务与浏览器工具
# --------------------------------------------------------------------------

class RealServer:
    """真实后端 + 静态产物（M3 起一条命令即托管界面）。

    `static_dir` 可指向**另一份构建产物副本**（用于「不改产品源码」的负对照实验）。

    端口**不再事先用 `free_port()` 留位**（那是 TOCTOU：探测到的端口可能被别的套接字
    在这之后抢走，Windows 上表现为误导性的 `WinError 10013`）。改用 `boot_uvicorn`：
    bind 失败（有证据）就换端口重试，其它失败一次都不重试。
    """

    def __init__(self, static_dir: str | None = None, db_path: str | None = None):
        self.port = None                    # 起服务时才定（见 boot_uvicorn）
        self.attempts: list = []
        self.static_dir = static_dir
        self.db_path = db_path
        self.process = None

    def _command(self, port: int) -> list[str]:
        return [VPY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]

    def __enter__(self):
        env = dict(os.environ)
        env.update({
            # 端口要等 boot 完才知道，所以库名用随机后缀（多次尝试共享同一个库）
            "MATHGEN_DB": self.db_path or os.path.join(
                ARTIFACTS, f"ui-{uuid.uuid4().hex[:8]}.db"),
            "MATHGEN_PW_TMP": os.path.join(REPO, ".tmp-pw"),
            "PYTHONIOENCODING": "utf-8",
        })
        if self.static_dir:
            env["MATHGEN_STATIC_DIR"] = self.static_dir
        else:
            env.pop("MATHGEN_STATIC_DIR", None)
        self.process, self.port, self.attempts = boot_uvicorn(
            self._command, cwd=REPO, env=env, timeout=40.0)
        return self

    def __exit__(self, *exc):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class ApiDownServer:
    """静态产物照常，**所有 `/api/**` 返回 500** —— 用来验 A4「不谎报状态」。"""

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, directory=None, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def _is_api(self) -> bool:
            return self.path.startswith("/api/")

        def do_GET(self):                # noqa: N802
            if self._is_api():
                return self._fail()
            if not os.path.exists(self.translate_path(self.path)) and "." not in self.path:
                self.path = "/index.html"      # SPA 回落
            return super().do_GET()

        def do_POST(self):               # noqa: N802
            return self._fail()

        def do_PATCH(self):              # noqa: N802
            return self._fail()

        def do_DELETE(self):             # noqa: N802
            return self._fail()

        def _fail(self):
            body = json.dumps({"error": {"code": "BOOM", "message": "后端 500（验收桩）"}})
            payload = body.encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):    # 静音
            pass

    def __init__(self):
        self.port = free_port()
        self.server = None
        self.thread = None

    def __enter__(self):
        handler = lambda *a, **kw: self.Handler(*a, directory=DIST, **kw)   # noqa: E731
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def browser_page(chan, viewport=(1440, 900)):
    """用**产品自己的浏览器通道**开一个页面。

    task-22 起不再自己 `sync_playwright()`：产品的 `app/export/browser.py` 有一个
    **进程级常驻会话**（`_SESSION`），Playwright 的 sync API 用 greenlet 跑调度器 ——
    两次 sync 调用之间它仍把事件循环标记为 running，于是**本线程之后再新建**
    `PlaywrightContextManager` 会被守卫拒绝：

        It looks like you are using Playwright Sync API inside the asyncio loop.
        Please use the Async API instead.

    （全量门禁里 `tests.e2e_m2` 的 PDF 路径先启动了那个常驻会话，因此 `tests.e2e_ui`
    里所有裸 `sync_playwright()` 都撞上它 = 12 条 errors。）走产品通道则有两条安全路径：
    已连上时 `_BrowserSession.browser()` **提前 return** 复用，需要新建时它自带
    「停掉失败的旧 manager 再重试」的回退 —— 两条都不会撞守卫。
    """
    browser = chan.browser()
    return browser, browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})


# --------------------------------------------------------------------------
# task-22 回归护栏：本套件**不得**再裸用 Playwright 的 sync API
# --------------------------------------------------------------------------

def bare_playwright_calls(source: str, filename: str = "<string>") -> list[tuple[int, str]]:
    """裸调用 `sync_playwright(...)` 的清单：`(行号, 所在函数名；模块级为 "")`。

    必须是 AST 而不是文本匹配：本文件的文档串里就写着 `sync_playwright()` 这个名字，
    grep 会自伤；AST 只看真正的调用表达式。
    """
    def is_bare(node) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        return ((isinstance(func, ast.Name) and func.id == "sync_playwright")
                or (isinstance(func, ast.Attribute) and func.attr == "sync_playwright"))

    hits: set[tuple[int, str]] = set()

    def scan(node, owner: str) -> None:
        if is_bare(node):
            hits.add((node.lineno, owner))
        for item in getattr(node, "items", []):        # `with ... as x:` 的上下文表达式
            if is_bare(item.context_expr):
                hits.add((node.lineno, owner))
        for child in ast.iter_child_nodes(node):
            scan(child, child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                 else owner)

    scan(ast.parse(source, filename=filename), "")
    return sorted(hits)


def running_loop_name() -> str:
    """本线程当前是否有运行中的事件循环（Playwright sync API 的守卫看的就是它）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return ""
    return type(loop).__name__ if loop.is_running() else ""


def rects_intersect(a: dict, b: dict) -> bool:
    return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])


def element_rect(page, selector: str):
    return page.evaluate(
        """(sel) => { const el = document.querySelector(sel); if (!el) return null;
             const r = el.getBoundingClientRect();
             return {x: r.x, y: r.y, w: r.width, h: r.height}; }""", selector)


# --------------------------------------------------------------------------
# A1 令牌化
# --------------------------------------------------------------------------

class TestA1Tokens(unittest.TestCase):
    def test_no_color_literals_outside_tokens(self):
        result = subprocess.run([VPY, os.path.join(HERE, "scan_tokens.py"), "--json"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertIn(result.returncode, (0, 1), f"扫描器自身出错：{result.stderr[-400:]}")
        data = json.loads(result.stdout)
        pretty = "\n".join(f"  {h['file']}:{h['line']}  {' '.join(h['literals'])}"
                           for h in data["hits"][:20])
        self.assertEqual(data["hits"], [], f"A1：令牌之外出现颜色字面量：\n{pretty}")
        self.assertGreater(data["scanned_files"], 10,
                           f"扫描文件数过少（{data['scanned_files']}），疑似空转")

    def test_tokens_css_is_byte_identical_to_frozen_version(self):
        """`design/tokens.css` 是 Lead 的只读契约：必须与冻结提交逐字节相同。"""
        import hashlib

        frozen = subprocess.run(["git", "show", f"{FROZEN_COMMIT}:frontend/src/design/tokens.css"],
                                cwd=REPO, capture_output=True)
        self.assertEqual(frozen.returncode, 0, frozen.stderr[:200])
        current = open(TOKENS, "rb").read()
        self.assertEqual(
            hashlib.sha256(current).hexdigest(), hashlib.sha256(frozen.stdout).hexdigest(),
            "tokens.css 与冻结版不一致（实现方不得加/改令牌；变更须走 Lead + 决策）")
        self.assertEqual(current, frozen.stdout, "字节级差异（含行尾）")


# --------------------------------------------------------------------------
# A2 组件必备状态（解析样式表规则体）
# --------------------------------------------------------------------------

RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
#: 颜色相关声明（这些属性一旦出现，取值必须来自令牌；A1 已保证全仓无颜色字面量）
COLOR_PROPS = ("color", "background", "background-color", "border", "border-color",
               "border-top", "border-bottom", "border-left", "border-right",
               "outline", "outline-color", "box-shadow", "fill", "stroke")
SAFE_VALUES = ("var(--mg-", "none", "transparent", "currentcolor", "inherit", "unset",
               "initial", "0", "0px", "auto")


def split_selectors(selector_text: str) -> list[str]:
    """按顶层逗号切选择器（**括号内的逗号不算**，否则 `:where(a, button)` 会被切碎）。"""
    parts, depth, current = [], 0, ""
    for char in selector_text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def parse_rules(css: str) -> list[tuple[str, str]]:
    """把样式表切成 [(单条选择器, 规则体)] —— 保留**全部**规则，便于按包含关系匹配。"""
    rules: list[tuple[str, str]] = []
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selector, body in RULE_RE.findall(stripped):
        for single in split_selectors(selector):
            key = " ".join(single.split())
            if key:
                rules.append((key, body))
    return rules


def color_declarations_without_token(body: str) -> list[str]:
    offenders = []
    for declaration in body.split(";"):
        if ":" not in declaration:
            continue
        prop, _, value = declaration.partition(":")
        prop = prop.strip().lower()
        if prop.startswith("--"):
            continue
        if any(prop == p or prop.startswith(p + "-") for p in COLOR_PROPS):
            lowered = value.strip().lower()
            if not any(safe in lowered for safe in SAFE_VALUES):
                offenders.append(f"{prop}: {value.strip()}")
    return offenders


class TestA2ComponentStates(unittest.TestCase):
    """每个必备状态**规则存在**且**取值来自令牌**（不是只写类名、更不是写死颜色）。"""

    REQUIRED = [
        ("Button 默认", ".mg-btn"),
        ("Button hover", ".mg-btn:hover:not(:disabled)"),
        ("Button active", ".mg-btn:active:not(:disabled)"),
        ("Button disabled", ".mg-btn:disabled"),
        ("Button 主色", ".mg-btn-primary"),
        ("Button 危险", ".mg-btn-danger"),
        ("IconButton", ".mg-icon-btn"),
        ("IconButton disabled", ".mg-icon-btn:disabled"),
        ("Input", ".mg-input"),
        ("Input disabled", ".mg-input:disabled"),
        ("Input invalid", '.mg-input[data-state="invalid"]'),
        ("Input invalid 文字原因", ".mg-field-reason"),
        ("Switch", ".mg-switch"),
        ("Switch disabled", ".mg-switch:disabled"),
        ("Switch 人话取值", ".mg-switch-value"),
        ("Slider", ".mg-slider"),
        ("Slider 当前值", ".mg-slider-value"),
        ("Slider disabled", '.mg-slider[data-state="disabled"]'),
        ("SegmentedControl", ".mg-segmented"),
        ("SegmentedControl 选中", ".mg-segment-on"),
        ("SegmentedControl disabled", '.mg-segment[data-state="disabled"]'),
        ("List 空态", ".mg-list-empty"),
        ("List 行", ".mg-list-row"),
        ("List 斑马纹", ".mg-list-row:nth-child(odd)"),
        ("List hover", ".mg-list-row:hover"),
        ("Table loading", ".mg-table-loading"),
        ("Table 空态", ".mg-table-empty"),
        ("Table 错误态", ".mg-table-error"),
        ("Card", ".mg-card"),
        ("FloatingPanel", ".mg-float"),
        ("FloatingPanel 折叠开关", ".mg-float-toggle"),
        ("Toolbar", ".mg-toolbar"),
        ("Toolbar 分组分隔", ".mg-toolbar-divider"),
        ("StatusBar", ".mg-statusbar"),
        ("StatusBar 已保存", '.mg-statusbar[data-state="saved"]'),
        ("StatusBar 失败", '.mg-statusbar[data-state="error"]'),
        ("Badge", ".mg-badge"),
        ("Chip", ".mg-chip"),
        ("EmptyState", ".mg-empty"),
        ("ErrorState", ".mg-error"),
        ("ErrorState 发生了什么", ".mg-error-what"),
        ("ErrorState 怎么办", ".mg-error-how"),
        ("ErrorState 错误码", ".mg-error-code"),
        ("Toast", ".mg-toast"),
        ("Toast 关闭", ".mg-toast-close"),
    ]

    @classmethod
    def setUpClass(cls):
        cls.rules = parse_rules(open(STYLES, encoding="utf-8").read())

    def _matched(self, selector: str) -> list[str]:
        """包含式匹配：`.mg-statusbar[data-state="saved"]` 由后代选择器实现也算存在。"""
        return [body for sel, body in self.rules if selector in sel]

    def test_required_selectors_exist(self):
        missing = [f"{label} ({sel})" for label, sel in self.REQUIRED
                   if not self._matched(sel)]
        self.assertEqual(missing, [], f"A2：缺少必备状态规则 {missing}")

    def test_state_rules_use_tokens_for_colors(self):
        """A2「状态样式取自令牌」的可判定化：颜色相关声明不得写死，必须 `var(--mg-*)`。

        （不要求每条规则都出现令牌 —— 例如 disabled 只写 `cursor/opacity` 就无需令牌。）
        """
        offenders = []
        for label, selector in self.REQUIRED:
            for body in self._matched(selector):
                bad = color_declarations_without_token(body)
                if bad:
                    offenders.append(f"{label} ({selector}): {bad}")
        self.assertEqual(offenders, [], f"A2：以下状态规则的颜色取值不是令牌：{offenders}")

    def test_focus_visible_ring_uses_token(self):
        target = [body for sel, body in self.rules
                  if sel == ".mg-focus-target:focus-visible"]
        self.assertTrue(target, "缺少 .mg-focus-target:focus-visible 规则")
        ring = target[0]
        self.assertIn("var(--mg-focus-ring)", ring, "焦点环必须来自 --mg-focus-ring 令牌")
        self.assertIn("outline", ring, "焦点环要有可见 outline")
        global_rules = [body for sel, body in self.rules
                        if ":focus-visible" in sel and ":where(" in sel]
        self.assertTrue(global_rules, "缺少全局 :focus-visible 规则")
        self.assertIn("outline", "".join(global_rules),
                      "全局 :focus-visible 规则应给出可见环")
        for body in global_rules:
            self.assertNotIn("outline: none", body.replace(" ", ""),
                             "不得用 outline:none 抹掉全局焦点环")


# --------------------------------------------------------------------------
# A3 / A4 / A6 / A7 / A8 —— 运行时
# --------------------------------------------------------------------------

class UiRuntimeCase(unittest.TestCase):
    server: RealServer

    @classmethod
    def setUpClass(cls):
        cls.build_note = ensure_dist_fresh()
        cls.server = RealServer()
        cls.server.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    @classmethod
    def new_paper(cls) -> int:
        import httpx

        client = httpx.Client(base_url=cls.server.base_url, timeout=30.0)
        paper = client.post("/api/papers", params={"seed": 20261015, "title": "结构轮验收卷"},
                            json={"ops": ["+", "-"], "ranges": ["R2"],
                                  "question_type": "oral", "count": 12}).json()
        return paper["id"]


class TestA3Keyboard(UiRuntimeCase):
    """全程只用键盘：Tab / Enter / Space / 方向键，且焦点始终有可见环。"""

    def test_keyboard_walkthrough_and_focus_ring(self):
        pid = self.new_paper()
        with browser_session() as chan:
            browser, page = browser_page(chan)
            try:
                page.goto(f"{self.server.base_url}/teacher", wait_until="networkidle",
                          timeout=60_000)
                page.keyboard.press("Tab")
                visited = []
                for _ in range(28):
                    page.keyboard.press("Tab")
                    info = page.evaluate(
                        """() => { const el = document.activeElement;
                             if (!el || el === document.body) return null;
                             const cs = getComputedStyle(el);
                             return {
                               tag: el.tagName.toLowerCase(),
                               testid: el.getAttribute('data-testid') || '',
                               text: (el.innerText || el.value || el.getAttribute('aria-label') || '')
                                       .trim().slice(0, 24),
                               outline: cs.outlineStyle + ' ' + cs.outlineWidth,
                               shadow: cs.boxShadow,
                               id: el.id || '',
                             }; }""")
                    if info:
                        visited.append(info)
                self.assertGreaterEqual(len(visited), 10,
                                        f"A3：Tab 只走到 {len(visited)} 个可聚焦元素")

                def visible_ring(item) -> bool:
                    outline_ok = (item["outline"].split()[0] != "none"
                                  and float(item["outline"].split()[1].replace("px", "") or 0) > 0)
                    shadow_ok = item["shadow"] not in ("none", "")
                    return outline_ok or shadow_ok

                # task-19 起**不再需要任何豁免**：卷面停靠点由包裹层承担并自带环，
                # 所以「每个 Tab 停靠点都有可见焦点环」可以无例外地成立（比带豁免的版本更强）。
                missing = [f"{i['tag']}#{i['id']}{i['testid']}" for i in visited
                           if not visible_ring(i)]
                self.assertEqual(missing, [], f"A3：以下停靠点没有可见焦点环：{missing}")
                with open(os.path.join(ARTIFACTS, "a3-tab-focus.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(visited, fh, ensure_ascii=False, indent=2)

                # task-19 起：卷面这一站的 Tab 停靠点是**包裹层**（`tabindex="0"`），
                # iframe 退出 Tab 顺序（`tabindex="-1"`）。所以停靠点应当**自带可见环**，
                # 不再需要任何豁免 —— 与门禁⑦的「无例外」一致。
                page.goto(f"{self.server.base_url}/teacher/paper/{pid}",
                          wait_until="networkidle", timeout=60_000)
                page.wait_for_selector("#sheet-frame", timeout=30_000)
                reached_by_tab = False
                for _ in range(60):
                    page.keyboard.press("Tab")
                    if page.evaluate(
                            """() => { const a = document.activeElement;
                                 return !!a && (a.className || '').toString()
                                                 .includes('mg-paper-stage'); }"""):
                        reached_by_tab = True
                        break
                self.assertTrue(reached_by_tab, "A3：键盘 Tab 走不到卷面停靠点（包裹层）")
                frame_probe = page.evaluate(
                    """() => { const stage = document.querySelector('.mg-paper-stage');
                         const iframe = document.getElementById('sheet-frame');
                         const cs = getComputedStyle(stage);
                         return {stageFocus: stage.matches(':focus'),
                                 stageFocusVisible: stage.matches(':focus-visible'),
                                 outline: cs.outlineStyle + ' ' + cs.outlineWidth,
                                 shadow: cs.boxShadow,
                                 iframeTabIndex: iframe.tabIndex,
                                 iframeFocusVisible: iframe.matches(':focus-visible')}; }""")
                self.assertTrue(frame_probe["stageFocus"], f"A3：停靠点未真正获得焦点：{frame_probe}")
                self.assertNotEqual(frame_probe["outline"].split()[0], "none",
                                    f"A3：卷面停靠点没有可见焦点环：{frame_probe}")
                self.assertEqual(frame_probe["iframeTabIndex"], -1,
                                 "A3：iframe 应退出 Tab 顺序（tabindex=-1）")
                # 规则本身来自令牌（A2/A3 的静态面）
                stage_rule_exists = any(
                    ".mg-paper-stage" in sel and (":focus-visible" in sel or ":focus" in sel)
                    for sel, _body in parse_rules(open(STYLES, encoding="utf-8").read()))
                self.assertTrue(stage_rule_exists, "A3：包裹层聚焦环规则不存在")
                with open(os.path.join(ARTIFACTS, "a3-iframe-focus.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"reached_by_tab": reached_by_tab,
                               "stage_rule_exists": stage_rule_exists,
                               **frame_probe}, fh, ensure_ascii=False, indent=2)

                # Space 能切换开关（role=switch）
                toggled = page.evaluate(
                    """() => { const sw = document.querySelector('[role="switch"]');
                         if (!sw) return null;
                         const before = sw.getAttribute('aria-checked');
                         sw.focus();
                         return {before, focusable: document.activeElement === sw}; }""")
                self.assertIsNotNone(toggled, "页面上应有 role=switch 的开关")
                self.assertTrue(toggled["focusable"], "开关必须可聚焦")
                page.keyboard.press("Space")
                page.wait_for_timeout(200)
                after = page.evaluate(
                    """() => document.querySelector('[role="switch"]')?.getAttribute('aria-checked')""")
                self.assertNotEqual(toggled["before"], after, "Space 应能切换 role=switch")
            finally:
                browser.close()


class TestA4NoFalseStatus(unittest.TestCase):
    """后端 500 时不得出现「已保存 / 预览已刷新」，且错误态**不冒充空态**。"""

    def test_backend_500_shows_error_not_empty_state(self):
        ensure_dist_fresh()
        with ApiDownServer() as down, browser_session() as chan:
            browser, page = browser_page(chan)
            try:
                page.goto(f"{down.base_url}/teacher/library", wait_until="networkidle",
                          timeout=60_000)
                page.wait_for_timeout(1500)
                body_text = page.inner_text("body")
                for forbidden in ("已保存", "预览已刷新"):
                    self.assertNotIn(forbidden, body_text,
                                     f"A4：后端 500 时出现了「{forbidden}」")
                error_nodes = page.eval_on_selector_all(
                    ".mg-error, .mg-table-error, [data-state='error']", "els => els.length")
                empty_nodes = page.eval_on_selector_all(".mg-empty, .mg-table-empty",
                                                        "els => els.length")
                self.assertGreater(error_nodes, 0,
                                   "A4：后端 500 时应显示错误态，实际没有 error 节点")
                self.assertEqual(empty_nodes, 0,
                                 "A4：错误态不得复用空态槽位（data-state 应为 error）")
                text = page.inner_text(".mg-error") if page.query_selector(".mg-error") else body_text
                self.assertTrue(any(k in text for k in ("失败", "错误", "不可用", "500")),
                                f"A4：错误文案要说清发生了什么：{text[:160]!r}")
                page.screenshot(path=os.path.join(ARTIFACTS, "a4-api-down.png"))
            finally:
                browser.close()


class TestA6PreviewFromBackend(UiRuntimeCase):
    def test_iframe_src_is_backend_sheet(self):
        pid = self.new_paper()
        with browser_session() as chan:
            browser, page = browser_page(chan)
            try:
                sheet_requests = []
                page.on("request", lambda r: sheet_requests.append(r.url)
                        if "/sheet" in r.url else None)
                page.goto(f"{self.server.base_url}/teacher/paper/{pid}",
                          wait_until="networkidle", timeout=60_000)
                page.wait_for_selector("#sheet-frame", timeout=30_000)
                src = page.get_attribute("#sheet-frame", "src") or ""
                self.assertIn(f"/api/papers/{pid}/sheet", src)
                in_frame = 0
                page_rule = ""
                for frame in page.frames:
                    if frame is page.main_frame:
                        continue
                    try:
                        in_frame = frame.eval_on_selector_all(".q-item", "e => e.length")
                        page_rule = frame.evaluate(
                            "() => { for (const ss of document.styleSheets) {"
                            " try { for (const r of ss.cssRules) {"
                            " if (r.constructor.name === 'CSSPageRule') return r.cssText; } }"
                            " catch (e) {} } return ''; }")
                    except Exception:
                        continue
                    if in_frame:
                        break
                top_items = page.eval_on_selector_all(".q-item", "e => e.length")
                self.assertEqual(top_items, 0, "A6：卷面 DOM 不得出现在顶层文档（D56）")
                self.assertGreater(in_frame, 0, "A6：iframe 内应有后端渲染的 .q-item")
                self.assertIn("a4", page_rule.replace(" ", "").lower(),
                              f"A6：打印几何应来自后端 HTML：{page_rule!r}")
                self.assertTrue(sheet_requests, "A6：浏览器应请求过后端 /sheet")
            finally:
                browser.close()


class TestA7Geometry(UiRuntimeCase):
    def test_floating_layers_do_not_overlap_paper(self):
        pid = self.new_paper()
        with browser_session() as chan:
            browser, page = browser_page(chan)
            try:
                page.goto(f"{self.server.base_url}/teacher/paper/{pid}",
                          wait_until="networkidle", timeout=60_000)
                page.wait_for_selector("#sheet-frame", timeout=30_000)
                paper = element_rect(page, ".sheet-frame") or element_rect(page, ".mg-paper-frame")
                self.assertIsNotNone(paper, "找不到纸面元素")
                layers = {}
                for name, selector in (("toolbar", ".mg-toolbar"), ("float", ".mg-float"),
                                       ("panel", ".mg-panel"), ("sidebar", ".mg-side")):
                    rect = element_rect(page, selector)
                    if rect:
                        layers[name] = rect
                self.assertTrue(layers, "找不到任何浮动层/侧栏，几何断言无从谈起")
                collisions = []
                names = list(layers)
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        if rects_intersect(layers[names[i]], layers[names[j]]):
                            collisions.append(f"{names[i]}×{names[j]}")
                self.assertEqual(collisions, [], f"A7：浮动层互相压住：{collisions}")
                over_paper = [name for name, rect in layers.items()
                              if name in ("toolbar", "float") and rects_intersect(rect, paper)]
                self.assertEqual(over_paper, [],
                                 f"A7：浮动层压住纸面：{over_paper}（paper={paper}）")
                ratio = paper["w"] / paper["h"]
                self.assertAlmostEqual(ratio, 210 / 297, delta=0.02,
                                       msg=f"纸面比例应≈0.7071，实测 {ratio:.4f}")
                with open(os.path.join(ARTIFACTS, "a7-geometry.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"paper": paper, "layers": layers}, fh, ensure_ascii=False, indent=2)
            finally:
                browser.close()


class TestA8Viewports(UiRuntimeCase):
    def test_no_horizontal_overflow(self):
        pid = self.new_paper()
        with browser_session() as chan:
            browser = chan.browser()
            try:
                for width, height in ((1440, 900), (1280, 900)):
                    page = browser.new_page(viewport={"width": width, "height": height})
                    with self.subTest(viewport=f"{width}x{height}"):
                        for path in ("/teacher", f"/teacher/paper/{pid}", "/teacher/library"):
                            page.goto(f"{self.server.base_url}{path}",
                                      wait_until="networkidle", timeout=60_000)
                            page.wait_for_timeout(400)
                            metrics = page.evaluate(
                                "() => ({sw: document.documentElement.scrollWidth,"
                                " iw: window.innerWidth})")
                            self.assertEqual(
                                metrics["sw"], metrics["iw"],
                                f"A8：{path} 在 {width}x{height} 下横向溢出"
                                f"（scrollWidth={metrics['sw']} innerWidth={metrics['iw']}）")
                    page.close()
            finally:
                browser.close()


class TestZPlaywrightChannelGuard(unittest.TestCase):
    """task-22：把「12 条 errors」的根因与本修法固定成可复跑的判据。

    ① 静态：AST 扫描本目录，任何裸 `sync_playwright()` 调用即失败（附正对照，证明检测器有牙）；
    ② 机理：在产品常驻会话活着时，裸新建 `PlaywrightContextManager` **必然**被 Playwright 拒绝
       （本线程一直「有运行中的 loop」），而同一状态走产品通道正常 —— 修前 12 条 errors 由此而来。
    """

    SNIPPET = ("from playwright.sync_api import sync_playwright\n"
               "with sync_playwright() as pw:\n"
               "    browser = pw.chromium.launch()\n")

    #: 唯一豁免：下面那条测试**本身就是实验**（验证「裸调用必被拒」），它的裸调用是刺激、
    #: 不是误用。豁免按「函数名 + 恰好 1 处」记账 —— 函数被删/改名或处数变化都会让本测试失败，
    #: 因此无法靠改名字偷偷扩大豁免面。
    EXEMPT = {"test_bare_playwright_is_rejected_while_product_session_is_alive": 1}

    def test_detector_flags_a_synthetic_bare_call(self):
        """**正对照**：检测器必须抓住一段已知的裸调用，否则「全绿」可能只是空转。"""
        self.assertEqual(bare_playwright_calls(self.SNIPPET, "synthetic.py"), [(2, "")])

    def test_no_bare_sync_playwright_in_this_suite(self):
        offenders: list[str] = []
        exempt_seen: dict[str, int] = {}
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                for lineno, owner in bare_playwright_calls(fh.read(), name):
                    if owner in self.EXEMPT:
                        exempt_seen[owner] = exempt_seen.get(owner, 0) + 1
                    else:
                        offenders.append(f"{name}:{lineno}（在 {owner or '模块级'} 内）")
        self.assertEqual(
            offenders, [],
            "task-22：本套件不得裸用 sync_playwright（会撞产品常驻会话的 asyncio 守卫，"
            f"全量门禁里表现为「Playwright Sync API inside the asyncio loop」）；"
            f"请改用 browser_session()/session()：{offenders}")
        self.assertEqual(exempt_seen, self.EXEMPT,
                         f"豁免账目不符（应恰好 {self.EXEMPT}），实际 {exempt_seen}")

    def test_bare_playwright_is_rejected_while_product_session_is_alive(self):
        from playwright.sync_api import sync_playwright

        with browser_session() as chan:
            browser = chan.browser()
            self.assertTrue(browser.is_connected(), "产品浏览器通道未启动")
            self.assertTrue(running_loop_name(),
                            "产品会话启动后本线程应能看到运行中的事件循环（守卫的前提）")
            with self.assertRaises(Exception) as caught:
                with sync_playwright():
                    pass
            self.assertIn("asyncio loop", str(caught.exception),
                          f"裸 start 的失败原因不是 asyncio 守卫：{caught.exception}")
            # 同一状态、同一线程，走产品通道依然可用（这才是修法的判据）
            page = browser.new_page()
            try:
                page.set_content("<b id='probe'>ok</b>")
                self.assertEqual(page.inner_text("#probe"), "ok")
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
