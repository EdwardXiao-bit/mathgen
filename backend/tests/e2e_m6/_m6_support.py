"""task-49 的判据设施：断网进程的启动方式 + 两个探针的取数口子 + 纯函数判据。

设计要点（为什么长这样）
------------------------
* **判据进程保持联网，被测服务才断网**：`OfflineServer` 只往子进程 env 里塞
  `PYTHONPATH=<offline 目录>`，所以服务进程（以及它在进程内拉起的 Chromium）加载
  `sitecustomize` 后断网；本进程不受影响 —— 才能在同一时间窗里给出「此刻外网本来是通的」
  这条对照（V9）。若判据进程也断网，「外网失败」就证明不了任何东西。
* **断网自证**：外部访问失败的异常类型必须是守卫自己的 `OfflineBlocked`
  （`marker is True`），否则无法区分「我拦住了」与「网本来就不通」。
* 探针一律**写 JSON 文件**再读，不用 stdout 通信（task-48 的管道教训）。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from tests.e2e_m4 import _harness

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
REPO = BACKEND.parent
ARTIFACTS = HERE / "artifacts"
OFFLINE_DIR = HERE / "offline"
PW_TMP_DIR = REPO / ".tmp-playwright"

SOCKET_PROBE = OFFLINE_DIR / "socket_probe.py"
BROWSER_PROBE = OFFLINE_DIR / "browser_probe.py"

EXTERNAL_URL = "https://example.com/"
LOCAL_PROXY = "http://127.0.0.1:7890"
NON_HTTP_SCHEMES = ("data:", "about:", "blob:", "chrome:", "devtools:", "javascript:")
# 守卫日志里「拦截」记录的 kind（守卫自己的记法，不是字面量 "blocked"）：
BLOCK_KINDS = frozenset({"getaddrinfo", "connect", "connect_ex", "create_connection", "sendto"})


def python_path(hooked: bool) -> str:
    """给子进程的 `PYTHONPATH`。装钩子时把 `offline/` 放最前（`sitecustomize` 才被导入）。"""
    parts = [str(OFFLINE_DIR)] if hooked else []
    parts.append(str(BACKEND))
    return os.pathsep.join(parts)


def offline_env(*, hooked: bool, log_path: str | None = None, base: dict | None = None) -> dict:
    env = dict(base or os.environ)
    env["PYTHONPATH"] = python_path(hooked)
    env["PYTHONIOENCODING"] = "utf-8"
    env["MATHGEN_OFFLINE_GUARD"] = "1" if hooked else "0"
    # Playwright 的 Node 驱动会 `mkdtemp`；本机系统 Temp 在本会话里建不出来（EPERM），
    # 所以把 TMP/TEMP/TMPDIR 指到仓库内可写目录（`backend/app/export/browser.py` 的对策）。
    tmp = str(PW_TMP_DIR)
    Path(tmp).mkdir(parents=True, exist_ok=True)
    for name in ("TMP", "TEMP", "TMPDIR", "MATHGEN_PW_TMP"):
        env[name] = tmp
    if log_path:
        env["MATHGEN_OFFLINE_LOG"] = str(log_path)
    else:
        env.pop("MATHGEN_OFFLINE_LOG", None)
    return env


@contextlib.contextmanager
def temporary_env(mapping: dict):
    """临时改本进程的 `os.environ`（只为让 `UiServer.__enter__` 把它抄给子进程）。

    对**本进程**没有副作用：`sitecustomize` 只在解释器启动时导入，此刻改 `PYTHONPATH`
    不会把判据进程变成断网进程。
    """
    saved = {key: os.environ.get(key) for key in mapping}
    try:
        for key, value in mapping.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class OfflineServer(_harness.UiServer):
    """断网态的真机 uvicorn（服务进程 + 它拉起的 Chromium 都在断网环境里）。"""

    def __init__(self, *, log_path=None, hooked: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.log_path = str(log_path) if log_path else None
        self.hooked = hooked

    def child_env(self) -> dict:
        env = {
            "PYTHONPATH": python_path(self.hooked),
            "MATHGEN_OFFLINE_LOG": self.log_path,
            "MATHGEN_OFFLINE_GUARD": "1" if self.hooked else "0",
        }
        # 服务进程里也可能启动 Chromium（导出 PDF 就是服务端 Playwright 干的），
        # 所以临时目录变量必须一起给：本机系统 Temp 建不出目录（EPERM at mkdtemp），
        # 而 `mkdtemp` 发生在 Playwright 的 Node 驱动进程里，`launch(env=…)` 治不了。
        tmp = str(PW_TMP_DIR)
        Path(tmp).mkdir(parents=True, exist_ok=True)
        for name in ("TMP", "TEMP", "TMPDIR", "MATHGEN_PW_TMP"):
            env[name] = tmp
        return env

    def __enter__(self):
        with temporary_env(self.child_env()):
            return super().__enter__()


# ------------------------------------------------------------------ 探针取数口子


def run_probe(
    script: Path,
    *,
    json_path: Path,
    hooked: bool,
    extra_args: tuple = (),
    log_path: Path | None = None,
    timeout: float = 240.0,
) -> dict:
    """跑一个探针子进程并读回它的 JSON。永远返回字典，不抛（失败信息在字典里）。"""
    json_path = Path(json_path)
    if json_path.exists():
        json_path.unlink()
    command = [
        sys.executable,
        str(script),
        "--json",
        str(json_path),
        *[str(item) for item in extra_args],
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO),
            env=offline_env(hooked=hooked, log_path=str(log_path) if log_path else None),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "timeout": True,
            "stdout": _tail(exc.stdout),
            "stderr": _tail(exc.stderr),
            "json": _read_json(json_path),
            "command": command,
        }
    return {
        "returncode": completed.returncode,
        "timeout": False,
        "stdout": _tail(completed.stdout),
        "stderr": _tail(completed.stderr),
        "json": _read_json(json_path),
        "command": command,
    }


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _tail(text, limit: int = 800) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    return text if len(text) <= limit else "…" + text[-limit:]


def write_evidence(name: str, payload: dict) -> str:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / name
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return str(path)


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ 纯函数判据


def is_loopback_host(host) -> bool:
    if not host:
        return True
    text = str(host).lower()
    return text in {"localhost", "127.0.0.1", "::1", "localhost.localdomain"} or text.startswith(
        "127."
    )


def external_requests(requests) -> list[str]:
    """请求列表里所有「打到外网」的 URL（环回/内联 scheme 不算）。"""
    found = []
    for item in requests or []:
        url = item.get("url") if isinstance(item, dict) else str(item)
        if not url:
            continue
        lowered = url.lower()
        if lowered.startswith(NON_HTTP_SCHEMES):
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if is_loopback_host(parsed.hostname):
            continue
        found.append(url)
    return found


def external_request_problems(requests, *, label: str) -> list[str]:
    """界面/卷面在断网下**一个外部请求都不该发**；发了就是问题（负对照喂一条外网 URL 必红）。"""
    offenders = external_requests(requests)
    if not offenders:
        return []
    return [
        f"{label}：加载了外部资源 {url}（断网环境下应当一个都没有）" for url in offenders[:5]
    ]


def _blocked(observation: dict) -> bool:
    return bool(observation) and observation.get("outcome") == "blocked" and observation.get(
        "marker"
    )


def socket_probe_problems(probe: dict | None, *, label: str, hooked: bool) -> list[str]:
    if not probe:
        return [f"{label}：探针没有产出 JSON（子进程可能没起来）"]
    problems: list[str] = []
    observations = probe.get("observations") or {}
    guard = probe.get("guard") or {}

    if bool(guard.get("module_loaded")) != bool(hooked):
        problems.append(
            f"{label}：守卫加载状态与预期不符（module_loaded="
            f"{guard.get('module_loaded')}，期望 {hooked}）"
        )

    loopback = observations.get("loopback_dns") or {}
    if loopback.get("outcome") != "ok":
        problems.append(
            f"{label}：环回 DNS 都不通（{loopback.get('type')}: {loopback.get('message')}）"
        )
    attempt = observations.get("loopback_connect")
    if attempt is not None and attempt.get("outcome") == "blocked":
        problems.append(f"{label}：守卫把环回连接也拦了（{attempt.get('message')}）")
    if observations.get("loopback_http") is not None:
        local = observations["loopback_http"]
        if local.get("outcome") != "ok" or (local.get("value") or {}).get("status") != 200:
            problems.append(f"{label}：环回 HTTP 取不到 200（{local}）")

    external_keys = ("external_dns", "external_connect", "external_connect_raw_ip", "external_https")
    if hooked:
        for key in external_keys:
            observation = observations.get(key)
            if observation is None:
                problems.append(f"{label}：缺观测 {key}")
                continue
            if not _blocked(observation):
                problems.append(
                    f"{label}：{observation.get('label')} 没有被守卫拦住"
                    f"（outcome={observation.get('outcome')}，"
                    f"type={observation.get('type')}，marker={observation.get('marker')}）"
                )
    else:
        https = observations.get("external_https") or {}
        if https.get("outcome") != "ok":
            problems.append(
                f"{label}：对照态里外网竟然不通（{https.get('outcome')} / "
                f"{https.get('type')}: {https.get('message')}）—— 对照组无效，本次离线验证作废"
            )
        elif int((https.get("value") or {}).get("status") or 0) != 200:
            problems.append(f"{label}：对照态外网状态码不是 200（{https.get('value')}）")
    return problems


def browser_probe_problems(probe: dict | None, *, label: str, hooked: bool) -> list[str]:
    if not probe:
        return [f"{label}：探针没有产出 JSON（子进程可能没起来）"]
    problems: list[str] = []
    guard = probe.get("guard") or {}
    if bool(guard.get("module_loaded")) != bool(hooked):
        problems.append(
            f"{label}：守卫加载状态与预期不符（module_loaded={guard.get('module_loaded')}，"
            f"期望 {hooked}）"
        )

    loopback = probe.get("loopback") or {}
    if loopback.get("outcome") != "ok":
        problems.append(
            f"{label}：产品的预览页在断网态打不开（{loopback.get('type')}: "
            f"{loopback.get('message')}）"
        )
    else:
        if loopback.get("status") != 200:
            problems.append(f"{label}：预览页状态码不是 200（{loopback.get('status')}）")
        if int(loopback.get("text_length") or 0) <= 0:
            problems.append(f"{label}：预览页正文是空的")

    pdf = probe.get("pdf") or {}
    problems.extend(pdf_problems(pdf, where=f"{label}：浏览器打印"))

    external = probe.get("external") or {}
    if hooked:
        if external.get("outcome") != "failed":
            problems.append(
                f"{label}：断网态下 Chromium 竟然打开了外网（{external.get('outcome')} / "
                f"status={external.get('status')}）—— 本次离线验证无效"
            )
    else:
        if external.get("outcome") != "ok":
            problems.append(
                f"{label}：对照态下 Chromium 打不开外网（{external.get('type')}: "
                f"{external.get('message')}）—— 对照组无效"
            )

    # 请求记录要**切一刀**：`requests` 里最后一条是探针**故意**打的外网 URL，
    # 判据只看它之前的那些（否则「故意打外网」这条探针自己会被报成打了外网）。
    all_requests = probe.get("requests") or []
    cut = probe.get("requests_before_external")
    if cut is None:
        problems.append(
            f"{label}：探针没有记录 requests_before_external，无法区分"
            f"「渲染时打了外网」与「故意打外网」（拿到 {len(all_requests)} 条请求）"
        )
        before_external = all_requests
    else:
        before_external = all_requests[: int(cut)]
    problems.extend(external_request_problems(before_external, label=f"{label}：渲染期间请求记录"))
    return problems


def read_guard_logs(base) -> list[dict]:
    """读取守卫写的**每进程一份**日志：`<MATHGEN_OFFLINE_LOG>.<pid>.json`。

    守卫刻意把 pid 写进文件名：同一份日志会被多个进程写（判据进程、服务进程、各探针），
    共用文件名时后启动的进程会把前一个的证据整份覆盖（本任务踩过一次：诊断用的
    `python -c` 也加载了守卫，把探针刚写的 `chromium-launch` 记录冲掉，害得我以为补丁没生效）。
    """
    if not base:
        return []
    root = Path(str(base))
    if not root.parent.exists():
        return []
    logs: list[dict] = []
    for path in sorted(root.parent.glob(root.name + ".*.json")):
        data = _read_json(path)
        if isinstance(data, dict):
            logs.append(data)
    return logs


def guard_log_problems(
    logs, *, label: str, expect_chromium_launch: bool, min_blocked: int = 0
) -> list[str]:
    """守卫日志要能自证「补丁真装上了」；`expect_chromium_launch` 时还要自证
    「真改写了 Chromium 启动参数」——只看探针的结果是分不出「Chromium 本来就上不了网」
    和「我把它的网掐了」的。"""
    if not logs:
        return [f"{label}：没有找到任何守卫日志（守卫没生效，或日志路径没交给子进程）"]
    problems: list[str] = []
    kinds = [entry.get("kind") for log in logs for entry in (log.get("blocked") or [])]
    if "install" not in kinds:
        problems.append(f"{label}：守卫日志里没有 install 记录（装没装不知道）：{kinds}")
    forced = [log.get("chromium_args") for log in logs if log.get("chromium_args")]
    if expect_chromium_launch:
        if "chromium-launch" not in kinds:
            problems.append(
                f"{label}：守卫日志里没有 chromium-launch 记录 —— Playwright 补丁没拦到启动：{kinds}"
            )
        if not forced:
            problems.append(f"{label}：守卫日志里 forced chromium args 是空的（补丁没塞参数）")
        else:
            joined = " ".join(forced[0])
            for token in ("--host-resolver-rules=", "--proxy-server=", "--proxy-bypass-list="):
                if token not in joined:
                    problems.append(f"{label}：forced chromium args 缺 {token}：{joined}")
    blocked = [
        entry
        for log in logs
        for entry in (log.get("blocked") or [])
        if entry.get("kind") in BLOCK_KINDS
    ]
    if len(blocked) < min_blocked:
        problems.append(
            f"{label}：守卫日志里只有 {len(blocked)} 条 blocked 记录，"
            f"期望至少 {min_blocked} 条 —— 「断网」在进程内没有留下证据"
        )
    return problems


def pdf_problems(pdf: dict, *, where: str, min_bytes: int = 1024) -> list[str]:
    if not pdf:
        return [f"{where}：没有 PDF 观测"]
    if pdf.get("outcome") != "ok":
        return [f"{where}：失败（{pdf.get('type')}: {pdf.get('message')}）"]
    problems = []
    magic = str(pdf.get("magic") or "")
    if not magic.startswith("%PDF-"):
        problems.append(f"{where}：前 5 字节不是 %PDF-（实际 {magic!r}）")
    if int(pdf.get("bytes") or 0) < min_bytes:
        problems.append(f"{where}：只有 {pdf.get('bytes')} 字节，不像真 PDF")
    return problems


def http_problems(response, *, where: str, status: int = 200) -> list[str]:
    if response is None:
        return [f"{where}：没有响应"]
    if response.status_code != status:
        return [f"{where}：状态码 {response.status_code} ≠ {status}：{response.text[:200]}"]
    return []


def pdf_bytes_problems(data: bytes, *, where: str, min_bytes: int = 1024) -> list[str]:
    if not data:
        return [f"{where}：响应体是空的"]
    problems = []
    if not data.startswith(b"%PDF-"):
        problems.append(f"{where}：前 5 字节不是 %PDF-（实际 {data[:5]!r}）")
    if len(data) < min_bytes:
        problems.append(f"{where}：只有 {len(data)} 字节，不像真 PDF")
    return problems


# ------------------------------------------------------------------ 真机取数


def sheet_response(server, paper_id: int, *, mode: str = "question"):
    """老师端预览：`GET /api/papers/{id}/sheet`（与 `/export` 走同一个渲染器）。"""
    import httpx

    return httpx.get(
        f"{server.base_url}/api/papers/{paper_id}/sheet", params={"mode": mode}, timeout=15.0
    )


def export_response(server, paper_id: int, *, file_format: str = "pdf"):
    import httpx

    return httpx.get(
        f"{server.base_url}/api/papers/{paper_id}/export",
        params={"format": file_format},
        timeout=90.0,
    )


def guard_logs_for_pid(base, pid) -> list[dict]:
    """只要某个进程写的那一份守卫日志。

    **慎用**：本机 venv 的 `python.exe` 会把真解释器当子进程拉起来（实测 launcher 33688 /
    真干活 23880），所以 `UiServer.process.pid` 通常**不等于**导入守卫那个进程的 pid；
    隔离服务进程请按日志基名（`read_guard_logs`）而不是按 pid。
    """
    wanted = int(pid)
    return [log for log in read_guard_logs(base) if int(log.get("pid") or -1) == wanted]


_MARKUP_URL_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']([^"']+)["']|url\(\s*['"]?([^)'"\s]+)""",
    re.IGNORECASE,
)


def markup_urls(html: str) -> list[str]:
    """卷面/页面 HTML 里出现的所有 `src=` / `href=` / CSS `url(...)` 目标。

    NFR-9 的口径是「卷面 HTML 只有内联 `<style>`，不出现 `http://`/`https://`/`src=`/`href=`」，
    这里把「出现了哪些 URL」抽出来，交给 `external_request_problems` 判「有没有外网」——
    抽取与判定分开，负对照才好写。
    """
    found: list[str] = []
    for match in _MARKUP_URL_RE.finditer(html or ""):
        url = match.group(1) or match.group(2) or ""
        url = url.strip()
        if url:
            found.append(url)
    return found


def markup_external_problems(html: str, *, where: str) -> list[str]:
    return external_request_problems(
        [{"url": url} for url in markup_urls(html)], label=f"{where}的 HTML 标记"
    )

