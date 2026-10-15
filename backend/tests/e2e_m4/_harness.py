"""Tier 0 公共设施：真实服务、产品浏览器通道、设备档案、局域网地址、哈希、证据。

设计口径（三条，都为「判据要能自证」服务）：

1. **不 import 别人的测试包**：`backend/tests/api/**` 是 be-paper 的域，
   `backend/tests/security/**` 已被 task-25 封存。本模块自带 HTTP 客户端与数据构造。
2. **产物新鲜度是判据有效性的前提**：`dist` 比 `src` 旧时**不重建**（重建属于
   `frontend/**` 写域，不是本套件的权限），而是让用例**明确变红**并说清原因。
3. **设备档案自写**：不依赖 `playwright.devices` 的版本漂移，数值是公开机型规格；
   `is_mobile` / `has_touch` / DPR / UA 全部显式列出，便于逐条核对。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
DIST = os.path.join(REPO, "frontend", "dist")
SRC = os.path.join(REPO, "frontend", "src")
STUDENT_HTML = os.path.join(DIST, "student.html")
VPY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(VPY):
    VPY = sys.executable
os.makedirs(ARTIFACTS, exist_ok=True)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests._uvicorn_boot import boot_uvicorn, free_port  # noqa: E402,F401
from tests.e2e_m2._browser import browser_session  # noqa: E402

__all__ = [
    "ARTIFACTS", "BACKEND", "DEVICES", "DEVICE_WECHAT_UA", "DIST", "HERE", "REPO", "SRC",
    "VPY", "WIDTHS", "AppClient", "UiServer", "aggregate_hash", "boot_uvicorn",
    "browser_session",
    "collect_page_errors", "degraded_dist", "dist_freshness", "dump_evidence",
    "free_port", "lan_ip_candidates", "mobile_context", "record_evidence", "run_paper_flow",
    "storage_block_script", "storage_is_blocked", "warm_student_page",
]


# --------------------------------------------------------------------------
# 设备档案（手机竖屏）
# --------------------------------------------------------------------------

_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)
_WECHAT_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003133) "
    "NetType/WIFI Language/zh_CN"
)

IPHONE_13 = {
    "viewport": {"width": 390, "height": 664},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": _IPHONE_UA,
}
PIXEL_5 = {
    "viewport": {"width": 393, "height": 727},
    "device_scale_factor": 2.75,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": _ANDROID_UA,
}
WECHAT_IOS = {
    "viewport": {"width": 390, "height": 664},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": _WECHAT_IOS_UA,
}

DEVICES = {"iPhone 13": IPHONE_13, "Pixel 5": PIXEL_5}
DEVICE_WECHAT_UA = {"微信内置浏览器（iOS UA）": WECHAT_IOS}

#: 竖屏宽度档（小屏安卓 / iPhone 12-13 / Plus・Max 一档）
WIDTHS = (360, 390, 414)

#: 点击目标下限（Apple HIG 44pt；Material 48dp —— 取更宽松的 44 作为硬线）
TAP_MIN = 44.0

#: iOS 聚焦输入框会自动放大页面，除非 font-size >= 16px
IOS_ZOOM_SAFE_FONT = 16.0

CONFIG_ADD_SUB = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral"}


# --------------------------------------------------------------------------
# 路径 / 端口 / 网络
# --------------------------------------------------------------------------

# `free_port` 现由 `tests/_uvicorn_boot.py` 提供（task-33）：它是 TOCTOU 探测，只作起点；
# 真正起服务一律走 `boot_uvicorn`（bind 失败 -> 换端口有限重试）。


def newest_mtime(root: str) -> float:
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", "__pycache__"}]
        for name in filenames:
            newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
    return newest


def dist_freshness() -> tuple[bool, float, float]:
    """返回 `(产物是否比源码新, 源码最新 mtime, student.html mtime)`。"""
    if not os.path.exists(STUDENT_HTML):
        return False, newest_mtime(SRC), 0.0
    src_m = newest_mtime(SRC)
    dist_m = os.path.getmtime(STUDENT_HTML)
    return dist_m >= src_m, src_m, dist_m


def assert_dist_fresh() -> dict:
    """产物不新则**明确变红**（不重建：重建是 `frontend/**` 写域）。

    这里**刻意不接 `case` 参数**（task-43 的修复）：旧版签名是 `assert_dist_fresh(case)`
    并在体内写 `case.assertTrue(fresh, 消息)`，而六个调用点传的是**类**不是实例 ——
    于是 `TestCase.assertTrue` 被当普通函数调用：`self=fresh`(bool)、`expr=消息`(非空)，
    `if not expr:` 恒假 → **真不新鲜也永远不抛**（一条恒绿的守卫）。
    改成无参后，`assert_dist_fresh(cls)` 会当场 `TypeError`，签名层面就不允许再传错东西；
    判据留在仓库：`tests/e2e_m4/test_dist_freshness_guard.py`。
    """
    fresh, src_m, dist_m = dist_freshness()
    if not fresh:
        raise AssertionError(
            "前端产物早于源码 → 本套件判据无效。请在 `frontend/**` 写域里"
            f"先跑 `pnpm --dir frontend build`（src={src_m:.0f} dist={dist_m:.0f}）。"
        )
    return {"dist_student_html_mtime": dist_m, "src_newest_mtime": src_m}


VIRTUAL_KEYWORDS = ("vmware", "virtualbox", "hyper-v", "vethernet", "loopback",
                    "docker", "wsl", "tap-windows", "bluetooth")
# Windows 移动热点（ICS）固定的 AP 侧网段；手机连上热点后只有这个地址可达。
ICS_AP_NETWORK = "192.168.137.0/24"


def default_route_ip() -> str | None:
    """内核为访问外网会选的那块网卡的地址（不发包，只问路由表）。

    这台机器上 `gethostbyname_ex` 会把 VMware 的 host-only 网卡（192.168.45.1）排在
    前面，而**手机永远连不上它**——所以候选顺序必须以默认路由为准。
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        return None


def _ipconfig_entries() -> list[tuple[str, str]]:
    """`ipconfig` 里出现的全部 IPv4 → (ip, 适配器名)，保持输出顺序。"""
    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(["ipconfig"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    entries: list[tuple[str, str]] = []
    current = ""
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith(":") and " . " not in line and "IPv4" not in line:
            current = line[:-1].strip()
        elif "IPv4" in line and ":" in line:
            ip = line.rsplit(":", 1)[1].strip()
            if ip:
                entries.append((ip, current))
    return entries


def adapter_names() -> dict[str, str]:
    """ip → 适配器名（解析 `ipconfig`，取不到就返回空表）。"""
    mapping: dict[str, str] = {}
    for ip, adapter in _ipconfig_entries():
        mapping.setdefault(ip, adapter)
    return mapping


def describe_lan_ips() -> list[dict]:
    """候选局域网地址，**逐个给出「选/不选」的理由**（可复核，不是黑箱）。

    顺序不是随便排的：Windows 移动热点（ICS）的 AP 侧地址 `192.168.137.1/24`
    排最前 —— 手机连热点后**只有这个地址**是可达的；校园网地址、VMware host-only、
    169.254 链路本地都不行。适配器名字只作辅助证据（`vEthernet` 之类会被过滤），
    但**判据是地址与默认路由**，不是名字。
    """
    names = adapter_names()
    ordered: list[str] = []
    override = os.environ.get("M4_LAN_IP", "").strip()
    if override:
        ordered.append(override)
    primary = default_route_ip()
    if primary:
        ordered.append(primary)
    ordered.extend(ip for ip, _ in _ipconfig_entries())
    try:
        ordered.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    described: list[dict] = []
    for ip in ordered:
        if any(item["ip"] == ip for item in described):
            continue
        adapter = names.get(ip, "")
        lowered = adapter.lower()
        virtual = any(word in lowered for word in VIRTUAL_KEYWORDS)
        loopback = ip.startswith("127.")
        link_local = ip.startswith("169.254.")
        try:
            ics_ap = ipaddress.ip_address(ip) in ipaddress.ip_network(ICS_AP_NETWORK)
        except ValueError:
            ics_ap = False
        reasons = []
        if loopback:
            reasons.append("回环地址，手机不可达")
        if link_local:
            reasons.append("169.254 链路本地（没拿到 DHCP 租约），手机不可达")
        if virtual:
            reasons.append(f"虚拟网卡（名字含 {'/'.join(w for w in VIRTUAL_KEYWORDS if w in lowered)}），手机不可达")
        if ics_ap:
            reasons.append("Windows 移动热点/ICS 的 AP 侧地址，手机连热点后可达")
        if ip == primary:
            reasons.append("默认路由出口网卡")
        described.append({
            "ip": ip,
            "adapter": adapter or "适配器名未知",
            "default_route": ip == primary,
            "virtual": virtual,
            "loopback": loopback,
            "link_local": link_local,
            "ics_ap": ics_ap,
            "usable": not (loopback or link_local),
            "reason": "；".join(reasons) or "普通局域网地址",
        })
    return described


def choose_lan_ip(described: list[dict]) -> tuple[str, str]:
    """按「可否被手机访问」的优先级挑一个地址，返回 (ip, 为什么选它)。"""
    def first(predicate):
        for item in described:
            if predicate(item):
                return item
        return None

    override = os.environ.get("M4_LAN_IP", "").strip()
    if override:
        return override, "环境变量 M4_LAN_IP 显式指定"
    chosen = first(lambda item: item["ics_ap"] and item["usable"])
    if chosen:
        return chosen["ip"], "Windows 移动热点（ICS）的 AP 侧地址：手机连热点后唯一可达"
    chosen = first(lambda item: item["default_route"] and item["usable"] and not item["virtual"])
    if chosen:
        return chosen["ip"], "默认路由出口网卡（非虚拟）"
    chosen = first(lambda item: item["usable"] and not item["virtual"])
    if chosen:
        return chosen["ip"], "第一个可用的非虚拟地址"
    chosen = first(lambda item: item["usable"])
    if chosen:
        return chosen["ip"], "唯一可用的地址（可能是虚拟网卡）"
    return "", "没有任何可用地址"


def lan_ip_candidates() -> list[str]:
    """本机可用作局域网地址的 IPv4（排除回环与 169.254 链路本地）。"""
    return [item["ip"] for item in describe_lan_ips() if item["usable"]]


# --------------------------------------------------------------------------
# 哈希 / 证据
# --------------------------------------------------------------------------

def aggregate_hash(paths: list[str], *, exts: tuple[str, ...] | None = None,
                   skip_dirs: tuple[str, ...] = (
                       "__pycache__", "artifacts", ".pylibs", ".venv", ".tmp-pw",
                       "node_modules")) -> dict:
    """把若干目录/文件聚合成一条 sha256（逐文件哈希再聚合），附文件数。"""
    digest = hashlib.sha256()
    count = 0
    for root in paths:
        candidates: list[str] = []
        if os.path.isfile(root):
            candidates = [root]
        else:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                for name in filenames:
                    candidates.append(os.path.join(dirpath, name))
        for path in sorted(candidates):
            if exts is not None and not path.endswith(exts):
                continue
            rel = os.path.relpath(path, REPO).replace("\\", "/")
            with open(path, "rb") as handle:
                digest.update(rel.encode("utf-8"))
                digest.update(hashlib.sha256(handle.read()).digest())
            count += 1
    return {"sha256": digest.hexdigest()[:16], "files": count}


EVIDENCE: dict[str, dict] = {}


def record_evidence(name: str, **data) -> dict:
    """按 name 合并记录（多模块共用一个证据文件）。"""
    EVIDENCE.setdefault(name, {}).update(data)
    return EVIDENCE[name]


def dump_evidence(path: str | None = None) -> str:
    target = path or os.path.join(ARTIFACTS, "evidence-tasks.json")
    existing: dict = {}
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, ValueError):
            existing = {}
    existing.update(EVIDENCE)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return target


# --------------------------------------------------------------------------
# 真实后端 + 静态产物
# --------------------------------------------------------------------------

class UiServer:
    """`uvicorn app.main:app` + `frontend/dist`（一条命令即托管界面）。

    `static_dir` 可指向**另一份产物副本**，用于「不改产品源码」的负对照实验。

    端口**不再事先用 `free_port()` 留位**（TOCTOU：探测到的端口可能在此之后被抢走，
    Windows 上表现为误导性的 `WinError 10013`）。改用 `boot_uvicorn`：bind 失败
    （有证据）就换端口重试，其它失败一次都不重试。
    """

    def __init__(self, static_dir: str | None = None, db_path: str | None = None):
        self.port = None                       # 起服务时才定（见 boot_uvicorn）
        self.attempts: list = []
        self.static_dir = static_dir
        self.db_path = db_path or os.path.join(
            ARTIFACTS, f"m4-{uuid.uuid4().hex[:10]}.db")
        self.process = None

    def _command(self, port: int) -> list[str]:
        return [VPY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]

    def __enter__(self):
        env = dict(os.environ)
        env.update({
            "MATHGEN_DB": self.db_path,
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

    # -- 数据构造（走真实 HTTP，不碰内部对象） ----------------------------
    def create_paper(self, *, count: int = 3, seed: int = 20261015,
                     title: str = "Tier0 移动端验收卷") -> int:
        import httpx

        payload = dict(CONFIG_ADD_SUB, count=count)
        response = httpx.post(
            f"{self.base_url}/api/papers", params={"seed": seed, "title": title},
            json=payload, timeout=10.0)
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def publish(self, paper_id: int) -> dict:
        import httpx

        response = httpx.post(f"{self.base_url}/api/papers/{paper_id}/publish", timeout=10.0)
        assert response.status_code == 200, response.text
        return response.json()

    def teacher_detail(self, paper_id: int) -> dict:
        import httpx

        response = httpx.get(f"{self.base_url}/api/papers/{paper_id}", timeout=10.0)
        assert response.status_code == 200, response.text
        return response.json()

    def published_paper(self, *, count: int = 3, seed: int = 20261015,
                        title: str = "Tier0 移动端验收卷") -> dict:
        paper_id = self.create_paper(count=count, seed=seed, title=title)
        published = self.publish(paper_id)
        return {"id": paper_id, "code": published["code"],
                "student_url": published["student_url"], "qr_url": published["qr_url"]}


class AppClient:
    """临时库上的 `TestClient`，可按请求改写 `Host` 头 —— 二维码判据的关键。

    自己建库、自己注入 `get_db`，不 import 别人的 harness；生产库不受影响。
    """

    def __init__(self):
        self.db_path = os.path.join(ARTIFACTS, f"m4-qr-{uuid.uuid4().hex[:8]}.db")
        self.engine = None
        self.client = None

    def __enter__(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine

        from app.db import enable_sqlite_foreign_keys, get_db, init_db, session_factory
        from app.main import create_app

        self.engine = enable_sqlite_foreign_keys(create_engine(
            f"sqlite+pysqlite:///{self.db_path}", connect_args={"check_same_thread": False},
            future=True))
        init_db(self.engine)
        factory = session_factory(self.engine)
        app = create_app(engine=self.engine, static_dir=False)

        def _override():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        return self

    def __exit__(self, *exc):
        if self.engine is not None:
            self.engine.dispose()
        return False

    def request(self, method: str, path: str, *, host: str | None = None, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        if host:
            headers["Host"] = host          # 显式改写 Host：二维码内容由它决定
        return self.client.request(method, path, headers=headers, **kwargs)

    def published_paper(self, *, host: str, count: int = 3, seed: int = 20261015,
                        title: str = "二维码 Host 敏感性验收卷") -> dict:
        created = self.request("POST", "/api/papers", host=host,
                               params={"seed": seed, "title": title},
                               json=dict(CONFIG_ADD_SUB, count=count))
        assert created.status_code == 201, created.text
        paper_id = int(created.json()["id"])
        published = self.request("POST", f"/api/papers/{paper_id}/publish", host=host)
        assert published.status_code == 200, published.text
        body = published.json()
        body["id"] = paper_id
        return body

    def qr_png(self, paper_id: int, *, host: str) -> bytes:
        response = self.request("GET", f"/api/papers/{paper_id}/qr", host=host)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("image/png"), \
            response.headers.get("content-type")
        return response.content


# --------------------------------------------------------------------------
# 浏览器
# --------------------------------------------------------------------------

def mobile_context(browser, device: str = "iPhone 13"):
    return browser.new_context(**DEVICES[device])


def collect_page_errors(page) -> list[str]:
    """把页面里未捕获的异常收进列表（返回的列表会被原地追加）。"""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


#: 让 `localStorage` / `sessionStorage` 的**每个方法都抛异常**——
#: 复现「微信内置浏览器 / 隐私模式里存储不可用」（DQ7 的内核）。
STORAGE_BLOCK_SCRIPT = """
(() => {
  const Boom = function () { throw new DOMException('storage disabled by Tier0', 'SecurityError'); };
  for (const name of ['getItem', 'setItem', 'removeItem', 'clear', 'key']) {
    Object.defineProperty(Storage.prototype, name, { configurable: true, writable: true, value: Boom });
  }
  Object.defineProperty(window, '__tier0StorageBlocked', { value: true, configurable: true });
})();
"""


def storage_block_script() -> str:
    return STORAGE_BLOCK_SCRIPT


def storage_is_blocked(page) -> dict:
    """注入生效自证：两条存储的读/写都必须真的抛异常。"""
    return page.evaluate(
        """() => {
            const probe = (fn) => { try { fn(); return 'NO-THROW'; } catch (e) { return e.name; } };
            return {
                localStorage_get: probe(() => window.localStorage.getItem('tier0')),
                localStorage_set: probe(() => window.localStorage.setItem('tier0', '1')),
                sessionStorage_get: probe(() => window.sessionStorage.getItem('tier0')),
                sessionStorage_set: probe(() => window.sessionStorage.setItem('tier0', '1')),
                flag: window.__tier0StorageBlocked === true,
            };
        }"""
    )


def run_paper_flow(page, base_url: str, code: str, answers: list[str], *,
                   name: str = "验收学生", student_no: str = "20260406001",
                   wait_after: float = 15.0) -> dict:
    """走完「扫码 → 填身份 → 逐题作答 → 交卷 → 结果页」，返回响应码与结果文本。

    **不自行 sleep 到结果**：每一步都用等待选择器同步。
    """
    statuses: list[tuple[str, int]] = []
    page.on("response", lambda response: statuses.append(
        (response.url, response.status)) if "/api/" in response.url else None)

    page.goto(f"{base_url}/student/paper/{code}", wait_until="domcontentloaded")
    # 扫码落到卷子页时先补身份（PaperPage 在 identity 为空时先给表单）
    page.wait_for_selector("#st-name", timeout=int(wait_after * 1000))
    page.fill("#st-name", name)
    page.fill("#st-student-no", student_no)
    page.click("form.st-form button[type=submit]")
    page.wait_for_selector("#st-answer", timeout=int(wait_after * 1000))

    total = len(answers)
    for index, answer in enumerate(answers):
        page.fill("#st-answer", answer)
        label = "交卷" if index == total - 1 else "下一题"
        page.get_by_role("button", name=label, exact=True).click()

    page.wait_for_selector(".st-score", timeout=int(wait_after * 1000))
    result = {
        "url": page.url,
        "score": (page.inner_text(".st-score") or "").strip(),
        "score_sub": (page.inner_text(".st-score-sub") or "").strip(),
        "rows": page.locator(".st-result-row").count(),
        "api_calls": [{"url": url, "status": status} for url, status in statuses],
    }
    record_evidence("storage_flow", last=result)
    return result


def warm_student_page(page, base_url: str, code: str, *, timeout: float = 15.0,
                      js_submit: bool = False) -> None:
    """打开学生端取卷页并停在答题界面（`#st-answer` 可见）。

    `js_submit=True` 时用 `requestSubmit()` 触发表单（**负对照专用**：产物被改坏后
    提交按钮可能落在视口之外，普通 click 会被 Playwright 的可见性检查拦住，
    而我们要测的正是「版式坏掉之后」这件事）。
    """
    page.goto(f"{base_url}/student/paper/{code}", wait_until="domcontentloaded")
    page.wait_for_selector("#st-name", timeout=int(timeout * 1000))
    page.fill("#st-name", "版式验收")
    page.fill("#st-student-no", "20260406002")
    if js_submit:
        page.eval_on_selector(
            "form.st-form",
            "form => form.requestSubmit ? form.requestSubmit()"
            " : form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))")
    else:
        page.click("form.st-form button[type=submit]")
    page.wait_for_selector("#st-answer", timeout=int(timeout * 1000))


# --------------------------------------------------------------------------
# 负对照：不改产品源码，只改产物副本
# --------------------------------------------------------------------------

DEGRADED_CSS = """
<style id="tier0-degraded">
  html, body { min-width: 1200px !important; }
  .st-input { font-size: 11px !important; }
  .st-footer button { min-height: 18px !important; height: 18px !important; }
</style>
"""


def degraded_dist(tag: str = "layout") -> str:
    """复制产物并在 `student.html` 里注入一段坏样式，返回副本目录。"""
    target = os.path.join(ARTIFACTS, f"dist-degraded-{tag}")
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(DIST, target)
    entry = os.path.join(target, "student.html")
    with open(entry, "r", encoding="utf-8") as handle:
        html = handle.read()
    if "</head>" not in html:
        raise AssertionError("student.html 里没有 </head>，注入点失效")
    with open(entry, "w", encoding="utf-8") as handle:
        handle.write(html.replace("</head>", DEGRADED_CSS + "</head>", 1))
    return target
