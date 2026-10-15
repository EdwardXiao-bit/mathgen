"""Tier 1 真机证据采集器（人工只负责扫码点几下，证据全部由服务端产生）。

为什么需要一层代理
------------------
uvicorn 的访问日志**不记 User-Agent**，而 Tier 1 的判据要求「非 127.0.0.1 的客户端 IP
+ `MicroMessenger/8.x` 的 UA」都来自服务端。产品代码在本轮**不许改**（`backend/app/**`
是冻结区），所以这里用一个只做转发与记录的薄代理顶在前面：

    手机 ──▶ 本机 0.0.0.0:<port>（记录 IP / UA / 路径 / 状态码）──▶ uvicorn 127.0.0.1

代理会把上游请求的 `Host` 改写成 `<局域网地址>:<port>`，因此产品自己生成的
`student_url` 与二维码**天然就是手机可达的地址**——顺带把 Tier 0-① 的判据放到真机场景里验。

用法
----
    python backend/tests/e2e_m4/collect_device_evidence.py --minutes 12

然后把打印出来的 URL/二维码给对方扫。脚本会自动定格：

- `artifacts/device-<nonce>-requests.jsonl`：每一条请求（含**手机真实 UA 与客户端 IP**）
- `artifacts/device-<nonce>-uvicorn.log`：uvicorn 自己的访问日志（第二份独立证据）
- `artifacts/device-<nonce>.db`：本次验收专用库（标题带 nonce，避免和张冠李戴的历史行混淆）
- `artifacts/device-session.json`：汇总 + 逐条判据结论

退出码：0 = Tier 1 判据齐备；2 = 没等到真机提交（这本身是有价值的现场事实）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

try:                                                       # --proxy-only 模式只需要标准库
    from tests.e2e_m4._harness import (  # noqa: E402
        ARTIFACTS, CONFIG_ADD_SUB, REPO, aggregate_hash, choose_lan_ip, describe_lan_ips,
    )
except ImportError:                                        # 换别的解释器跑纯代理时走这里
    ARTIFACTS = CONFIG_ADD_SUB = REPO = None               # type: ignore[assignment]
    aggregate_hash = choose_lan_ip = describe_lan_ips = None  # type: ignore[assignment]

try:
    import httpx  # noqa: E402
except ImportError:                                        # pragma: no cover - 纯代理模式
    httpx = None                                           # type: ignore[assignment]

try:
    import qrcode  # noqa: E402
except ImportError:                                        # pragma: no cover - 纯代理模式
    qrcode = None                                          # type: ignore[assignment]


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def port_in_use(port: int) -> bool:
    """端口上已经有东西在应答吗？（Windows 下双绑不会报错，只能主动探。）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
# 记录型代理
# ---------------------------------------------------------------------------

class LoggingProxy(http.server.ThreadingHTTPServer):
    # 故意**不**用 SO_REUSEADDR：Windows 上它允许第二个进程静默绑同一端口，
    # 于是请求被上一次的进程接走、本次 DB 永远空 —— 宁可当场报错。
    allow_reuse_address = False

    def __init__(self, address, handler, *, upstream_host: str, upstream_port: int,
                 log_path: str):
        super().__init__(address, handler)
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.log_path = log_path
        self.entries: list[dict] = []
        self._lock = threading.Lock()

    def record(self, entry: dict) -> None:
        with self._lock:
            self.entries.append(entry)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if entry["client_ip"] not in ("127.0.0.1", "::1"):
            print(f"  [手机] {entry['time']} {entry['client_ip']} "
                  f"{entry['method']} {entry['path']} -> {entry['status']}"
                  f"{' [微信]' if 'MicroMessenger' in entry['ua'] else ''}", flush=True)


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mathgen-tier1-proxy"

    def log_message(self, *args):                       # 静音默认日志
        return

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in {"host", "connection", "content-length"}}
        headers["Host"] = self.server.upstream_host      # ← 让产品生成手机可达的 URL
        if body is not None:
            headers["Content-Length"] = str(len(body))

        status, payload, content_type = 502, b"upstream unavailable", "text/plain"
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port,
                                             timeout=30)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                status = response.status
                content_type = response.getheader("Content-Type") or content_type
                payload = response.read()
            finally:
                conn.close()
        except Exception as exc:                          # noqa: BLE001
            payload = f"proxy error: {exc}".encode("utf-8")

        self.server.record({
            "time": now_iso(),
            "client_ip": self.client_address[0],
            "client_port": self.client_address[1],
            "method": self.command,
            "path": self.path,
            "status": status,
            "ua": self.headers.get("User-Agent", ""),
            "referer": self.headers.get("Referer", ""),
            "accept": self.headers.get("Accept", ""),
        })
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _proxy


# ---------------------------------------------------------------------------
# 服务生命周期
# ---------------------------------------------------------------------------

def start_backend(port: int, db_path: str, log_path: str) -> subprocess.Popen:
    env = dict(os.environ)
    env.update({
        "MATHGEN_DB": db_path,
        "MATHGEN_PW_TMP": os.path.join(REPO, ".tmp-pw"),
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("MATHGEN_STATIC_DIR", None)                  # 默认供 frontend/dist
    handle = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
        cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 40
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"uvicorn 提前退出，见 {log_path}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/papers", timeout=2).status_code == 200:
                return process
        except Exception:                                 # noqa: BLE001
            pass
        time.sleep(0.3)
    raise SystemExit("uvicorn 未就绪")


def attempt_rows(db_path: str, paper_id: int) -> list[dict]:
    """只读打开产品库，取本次 nonce 卷的提交行（不写、不锁）。"""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, student_id, paper_id, attempt_uuid, total, correct, "
                "duration_ms, submitted_at FROM attempt WHERE paper_id = ? "
                "ORDER BY id", (paper_id,)).fetchall()
        except sqlite3.Error:
            return []
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            student = conn.execute(
                "SELECT name, student_no FROM student WHERE id = ?",
                (item["student_id"],)).fetchone()
            item["student"] = dict(student) if student else None
            out.append(item)
        return out
    finally:
        conn.close()


def _powershell_json(script: str, *, empty_ok: bool = False):
    """跑一段 PowerShell 并把 JSON 取回来。

    **脚本里不许出现双引号** —— 通过 CreateProcess 传给 `powershell -Command` 时，
    内层双引号会被 PowerShell 自己的命令行解析吃掉，症状是「空输出」（踩过）。
    """
    try:
        completed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                    "-Command", script],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    raw = (completed.stdout or "").strip()
    if not raw:
        return ([], "") if empty_ok else (None, (completed.stderr or "空输出").strip()[:400])
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as exc:
        return None, f"{exc}: {raw[:200]}"


FIREWALL_RULES_PS = (
    "$ErrorActionPreference='Continue';"
    "$Error.Clear();"
    "$f = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | "
    "Where-Object { $_.Program -like '*python*' });"
    "$o = foreach($x in $f){ $r = $x | Get-NetFirewallRule -ErrorAction SilentlyContinue;"
    " if($r -and $r.Enabled -eq 'True' -and $r.Direction -eq 'Inbound' "
    "-and $r.Action -eq 'Allow'){ $p = [string]$r.Profile; "
    "[PSCustomObject]@{ Name=$r.DisplayName; Profiles=$p; Program=$x.Program } } };"
    "$errs = @($Error | ForEach-Object { [string]$_ });"
    "[PSCustomObject]@{ ok = ($errs.Count -eq 0); error = ($errs -join ' | '); "
    "rules = @($o) } | ConvertTo-Json -Depth 5")


def query_firewall_python_rules() -> tuple[list[dict], bool, str]:
    """查「有入站允许规则的 python 程序」，返回 `(规则, 查询是否成功, 错误原文)`。

    **为什么脚本自己要带一个 `ok` 字段**：非管理员从本采集器的 python 子进程里查
    NetSecurity 会被拒（`Access is denied` / Windows System Error 5），但 PowerShell
    仍然正常退出、stdout 仍是**合法的空结果**——于是「查询被拒」会伪装成「0 条规则」。
    本项目反复吃过这类「查询失败被当成结论」的亏（V9/V12），这里靠脚本自报。
    """
    payload, error = _powershell_json(FIREWALL_RULES_PS)
    if payload is None:
        return [], False, error or "查询无输出"
    if not isinstance(payload, dict):
        return [], False, f"预检脚本返回了非预期结构：{str(payload)[:200]}"
    rules = payload.get("rules") or []
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        rules = []
    return rules, bool(payload.get("ok")), str(payload.get("error") or "")


def firewall_python_candidates() -> tuple[list[str], str]:
    """有「入站允许」规则、且真在本机上存在的 python 解释器（可用于监听 0.0.0.0）。

    非管理员建不了规则，但**已经**有规则的及时解释器可以拿来用：让监听进程换成它，
    入站就被现有规则放行。这是本机唯一不依赖人点弹窗的路径。

    返回 `(解释器列表, 查询错误)`；**查询被拒时列表为空但第二个值非空**，
    调用方必须看第二个值 —— 把「查不到」当成「没有规则」是本项目反复吃过亏的一类缺陷。
    """
    rules, ok, error = query_firewall_python_rules()
    if not ok:
        return [], error or "查询被拒"
    found: list[str] = []
    for rule in rules:
        program = rule.get("Program") or ""
        if program and os.path.exists(program) and program not in found:
            found.append(program)
    return found, ""


def firewall_preflight(lan_ip: str, interpreter: str | None = None) -> dict:
    """打印「这条链路用哪块网卡、属于哪个防火墙配置文件、监听用的解释器有没有入站允许规则」。

    非管理员改不了防火墙规则，所以要么有规则、要么等人点弹窗——两种情况都必须
    **当场打印**并原样进报告（不许写成「已配置防火墙」）。
    """
    interpreter = interpreter or sys.executable
    address_script = (
        "$ProgressPreference='SilentlyContinue';"
        f"$ip = Get-NetIPAddress -AddressFamily IPv4 -IPAddress '{lan_ip}' "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 InterfaceAlias,InterfaceIndex;"
        "$profiles = Get-NetConnectionProfile | Select-Object InterfaceAlias,NetworkCategory;"
        "[PSCustomObject]@{ ip=@($ip); profiles=@($profiles) } | ConvertTo-Json -Depth 5")
    info, addr_error = _powershell_json(address_script)
    info = info or {}
    address = info.get("ip") or {}
    if isinstance(address, list):
        address = address[0] if address else {}
    profiles = info.get("profiles") or []
    if isinstance(profiles, dict):
        profiles = [profiles]
    alias = address.get("InterfaceAlias")
    category = next((item.get("NetworkCategory") for item in profiles
                     if item.get("InterfaceAlias") == alias), None)

    rules, rules_query_ok, rules_error = query_firewall_python_rules()
    mine = [rule for rule in rules
            if (rule.get("Program") or "").lower() == interpreter.lower()]
    rule_profiles = {part.strip() for rule in mine
                     for part in (rule.get("Profiles") or "").split(",") if part.strip()}
    if not rules_query_ok:
        # 查不到 ≠ 没有规则。本项目反复吃过「把查询失败当成结论」的亏，这里必须留痕。
        covered: bool | None = None
    elif mine and (category is None or category in rule_profiles or "Any" in rule_profiles):
        covered = True
    elif not mine:
        covered = False
    else:
        covered = None

    alternate_interpreters, alternate_error = firewall_python_candidates()
    print("---- 防火墙预检（非管理员：只能查、不能改）----")
    print(f"  载有 {lan_ip} 的接口：{alias or '未找到'}"
          f"；该接口的防火墙配置文件：{category or '无类别（Get-NetConnectionProfile 没列出它）'}")
    if rules_query_ok:
        print(f"  python 入站允许规则共 {len(rules)} 条；精确匹配将用于监听的解释器 "
              f"({interpreter}) 的 {len(mine)} 条，覆盖配置文件："
              f"{sorted(rule_profiles) or '（无）'}")
    else:
        print("  **规则查询失败**（不是「没有规则」）："
              f"{rules_error[:200]}")
        print("  已知：从 DSH 的 pwsh 工具里、以同一用户同一权限能查到；"
              "从本采集器的 python 子进程里被拒（Access is denied / Windows System Error 5）。"
              "\n        因此本次采集**无法**在本机判定防火墙是否放行，"
              "以手机的真实请求是否到达服务器日志为最终判据。")
    if addr_error:
        print(f"  （接口查询有噪声：{addr_error}）")
    if covered is True:
        print("  结论：监听进程有覆盖该配置文件的入站允许规则 → 防火墙这一层不应拦。")
    elif covered is False:
        print("  结论：**缺**该解释器的入站允许规则 → 手机侧很可能连不上。"
              "\n        非管理员建不了规则，两条出路：(a) 用户在弹窗点「允许访问」；"
              "\n        (b) 把监听进程换成已有规则的及时解释器（见下一行）。")
        print(f"        已有规则的解释器：{alternate_interpreters or '（无）'}"
              + (f"（查询失败：{alternate_error}）" if alternate_error else ""))
    else:
        print("  结论：本机无法判定该链路是否放行 —— "
              "以手机的真实请求是否到达服务器日志为最终判据。")
    return {"interface": alias, "network_category": category,
            "python_rules": len(rules), "matching_rules": len(mine),
            "rule_profiles": sorted(rule_profiles), "covered": covered,
            "query_ok": rules_query_ok, "query_error": rules_error[:200],
            "interpreter": interpreter,
            "allowed_interpreters": alternate_interpreters,
            "allowed_interpreters_query_error": alternate_error}


def print_qr(url: str) -> None:
    if qrcode is None:
        print("（本解释器没有 qrcode，跳过控制台二维码；PNG 路径见上）")
        return
    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    try:
        code.print_ascii(invert=True)
    except Exception:                                     # noqa: BLE001
        print("（控制台画不出二维码，请用 artifacts 下的 PNG 或直接看 URL）")


def read_entries(log_path: str) -> list[dict]:
    """从 JSONL 复原请求记录（代理可能与采集器**不在同一个进程**里）。"""
    if not os.path.exists(log_path):
        return []
    entries: list[dict] = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def start_proxy_child(python: str, *, port: int, backend_port: int, lan_host: str,
                      request_log: str, proxy_log: str) -> subprocess.Popen:
    """用**另一个解释器**起代理。

    为什么需要它：防火墙入站允许规则是**按程序路径**给的。本机上
    `backend/.venv/Scripts/python.exe` 没有规则，而 `D:\\anaconda3\\python.exe` 有
    （Profile=Public）；非管理员建不了新规则，于是只能**换监听进程**。

    子进程的 stdout 落到文件（不用管道，避免读端阻塞）。
    """
    command = [python, os.path.abspath(__file__), "--proxy-only",
               "--port", str(port), "--backend-port", str(backend_port),
               "--host-header", lan_host, "--log-path", request_log]
    print(f"（代理将用另一个解释器起：{python}）", flush=True)
    with open(proxy_log, "wb") as handle:
        child = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                                 cwd=os.path.dirname(os.path.dirname(HERE)))
    deadline = time.time() + 25
    while time.time() < deadline:
        if "PROXY READY" in open(proxy_log, "r", encoding="utf-8", errors="replace").read():
            return child
        if child.poll() is not None:
            raise RuntimeError(f"代理子进程退出（code={child.returncode}），日志：{proxy_log}")
        time.sleep(0.3)
    child.terminate()
    raise RuntimeError(f"代理子进程 25 秒内没就绪，日志：{proxy_log}")


def proxy_only(argv: list[str]) -> int:
    """`--proxy-only`：只跑标准库代理，供上面那个「换解释器」的路径调用。"""
    parser = argparse.ArgumentParser(description="只跑记录型代理（标准库）")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--host-header", required=True)
    parser.add_argument("--log-path", required=True)
    args = parser.parse_args(argv)
    proxy = LoggingProxy(("0.0.0.0", args.port), ProxyHandler,
                         upstream_host=args.host_header, upstream_port=args.backend_port,
                         log_path=args.log_path)
    print(f"PROXY READY 0.0.0.0:{args.port} upstream_host={args.host_header} "
          f"-> 127.0.0.1:{args.backend_port} pid={os.getpid()} "
          f"python={sys.executable}", flush=True)
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proxy.server_close()
    return 0


def rebuild_session(nonce: str, *, lan_ip: str, port: int) -> int:
    """从**已有原始产物**重建 `device-session-<nonce>.json`。

    为什么需要它：`device-session.json` 是「最新一轮」的别名，第二轮采集会把它覆盖掉；
    而验收报告要**同时**引用 1a（系统浏览器）与 1b（微信）两轮。原始产物
    （`device-<nonce>-requests.jsonl` / `device-<nonce>.db`）是逐轮命名的，不会互相覆盖，
    所以从它们重建即可 —— 重建件里显式标 `reconstructed=true` 与窗口来源，不冒充原采集。
    """
    request_log = os.path.join(ARTIFACTS, f"device-{nonce}-requests.jsonl")
    db_path = os.path.join(ARTIFACTS, f"device-{nonce}.db")
    uvicorn_log = os.path.join(ARTIFACTS, f"device-{nonce}-uvicorn.log")
    qr_path = os.path.join(ARTIFACTS, f"device-{nonce}-qr.png")
    for path in (request_log, db_path):
        if not os.path.exists(path):
            print(f"!! 缺原始产物，无法重建：{path}")
            return 2

    entries = read_entries(request_log)
    phone_entries = [e for e in entries if e["client_ip"] not in ("127.0.0.1", "::1")]
    wechat = [e for e in phone_entries if "MicroMessenger" in e["ua"]]
    uas = sorted({e["ua"] for e in phone_entries})

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select id, code, title from paper order by id limit 1").fetchone()
    paper_id = int(row["id"]) if row else 0
    code = row["code"] if row else ""
    title = row["title"] if row else ""
    rows = attempt_rows(db_path, paper_id)

    uvicorn_text = ""
    if os.path.exists(uvicorn_log):
        with open(uvicorn_log, "r", encoding="utf-8", errors="replace") as handle:
            uvicorn_text = handle.read()
    uvicorn_phone = [line.strip() for line in uvicorn_text.splitlines()
                     if "127.0.0.1" not in line and " - " in line]

    started = entries[0]["time"] if entries else ""
    finished = entries[-1]["time"] if entries else ""
    # 请求日志只有**秒**精度，而 `submitted_at` 带微秒；重建窗口的上界补 1 秒，
    # 否则「提交落在窗口内」会因为截断而假红（实测踩到过：…51.413743 <= …51 为假）。
    if finished:
        try:
            finished = (dt.datetime.fromisoformat(finished)
                        + dt.timedelta(seconds=1)).isoformat(timespec="seconds")
        except ValueError:                                     # pragma: no cover - 脏数据
            pass
    summary = {
        "nonce": nonce,
        "label": f"rebuilt-from-artifacts-{nonce}",
        "reconstructed": True,
        "reconstruction_note": "由 device-<nonce>-requests.jsonl + device-<nonce>.db 重建；"
                               "窗口取请求日志首条时间到下一条之后 +1s（日志只有秒精度），"
                               "不是原采集器的 started/finished",
        "lan_ip": lan_ip,
        "lan_host": f"{lan_ip}:{port}",
        "candidates": [],
        "lan_ip_described": [],
        "lan_ip_reason": "重建件：本轮采集时的选卡理由见同目录下的报告与 device-session-*.json",
        "firewall": {},
        "server_bound": f"proxy 0.0.0.0:{port} -> uvicorn 127.0.0.1:{port + 10}",
        "proxy_interpreter": "",
        "proxy_log": os.path.join(ARTIFACTS, f"device-{nonce}-proxy.log"),
        "paper": {"id": paper_id, "code": code, "title": title,
                  "student_url": f"http://{lan_ip}:{port}/student/paper/{code}",
                  "qr_png": qr_path if os.path.exists(qr_path) else ""},
        "window": {"started": started, "finished": finished,
                   "source": "reconstructed_from_request_log"},
        "proxy_requests_total": len(entries),
        "phone_requests": phone_entries,
        "phone_user_agents": uas,
        "wechat_requests": len(wechat),
        "uvicorn_phone_lines": uvicorn_phone[:20],
        "attempt_rows": rows,
        "verdict": {
            "t1_1_client_ip_is_not_loopback": bool(phone_entries),
            "t1_2_user_agent_recorded": bool(uas),
            "t1_3_wechat_inner_browser_ua": bool(wechat),
            "t1_4_attempt_row_points_at_this_nonce_paper": bool(rows),
            "t1_5_attempt_row_is_from_the_phone_client": bool(rows and phone_entries),
        },
        "artifacts": {"requests_jsonl": request_log, "uvicorn_log": uvicorn_log,
                      "db": db_path},
        "hashes": {
            "backend_app": aggregate_hash([os.path.join(REPO, "backend", "app")],
                                          exts=(".py",)),
            "e2e_m4_scope": aggregate_hash([HERE]),
        },
    }
    out = os.path.join(ARTIFACTS, f"device-session-{nonce}.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"已重建 {out}")
    print(f"  paper={paper_id} code={code} title={title!r} "
          f"phone_requests={len(phone_entries)} wechat={len(wechat)} attempt_rows={len(rows)}")
    print(f"  verdict={json.dumps(summary['verdict'], ensure_ascii=False)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Tier 1 真机证据采集")
    parser.add_argument("--port", type=int, default=8000, help="手机访问的端口（代理）")
    parser.add_argument("--backend-port", type=int, default=8010, help="uvicorn 内部端口")
    parser.add_argument("--lan-ip", default=None, help="笔记本局域网地址（默认自动探测）")
    parser.add_argument("--minutes", type=float, default=12.0, help="等待真机提交的分钟数")
    parser.add_argument("--label", default="", help="本次采集的备注（进证据文件）")
    parser.add_argument("--proxy-python", default=None,
                        help="用哪个解释器起监听（默认本进程）。防火墙入站允许规则是**按程序路径**"
                             "给的：本机只有 D:\\anaconda3\\python.exe 与其 cpu_1 环境、"
                             "以及 C:\\users\\ed\\appdata\\local\\python\\bin\\python3.exe 有规则；"
                             "venv 的 python 没有，非管理员也建不了新规则。")
    parser.add_argument("--rebuild-session", default=None, metavar="NONCE",
                        help="不采集，只从 device-<nonce>-requests.jsonl 与 device-<nonce>.db "
                             "重建 device-session-<nonce>.json（保住被「最新一轮」覆盖的历史轮次）")
    args = parser.parse_args()

    if args.rebuild_session:
        return rebuild_session(args.rebuild_session, lan_ip=args.lan_ip or "192.168.137.1",
                               port=args.port)

    nonce = uuid.uuid4().hex[:8]
    described = describe_lan_ips()
    candidates = [item["ip"] for item in described if item["usable"]]
    if args.lan_ip:
        lan_ip, why = args.lan_ip, "命令行 --lan-ip 显式指定"
    else:
        lan_ip, why = choose_lan_ip(described)
    if not lan_ip:
        print("!! 探测不到局域网地址，请用 --lan-ip 指定（例如移动热点下的 192.168.137.1）")
        return 2

    busy = [port for port in (args.port, args.backend_port) if port_in_use(port)]
    if busy:
        print(f"!! 端口已被占用：{busy} —— 上一次采集还在跑？（干跑时踩过这个坑：Windows 的 "
              "SO_REUSEADDR 会让第二个进程**静默**绑上同一个端口，于是请求被上一次的进程接走，"
              "本次 DB 永远是空的）")
        return 2

    db_path = os.path.join(ARTIFACTS, f"device-{nonce}.db")
    request_log = os.path.join(ARTIFACTS, f"device-{nonce}-requests.jsonl")
    uvicorn_log = os.path.join(ARTIFACTS, f"device-{nonce}-uvicorn.log")
    session_path = os.path.join(ARTIFACTS, "device-session.json")
    qr_path = os.path.join(ARTIFACTS, f"device-{nonce}-qr.png")

    lan_host = f"{lan_ip}:{args.port}"
    print("=" * 72)
    print(f"Tier 1 真机采集  nonce={nonce}  label={args.label!r}")
    print("候选局域网地址（★ = 本次采用；每条都给出「选/不选」的理由）：")
    for item in described:
        mark = "★" if item["ip"] == lan_ip else " "
        print(f"  {mark} {item['ip']:<16} {item['adapter']:<38} {item['reason']}")
    print(f"选它的理由：{why}")
    print(f"手机访问地址：http://{lan_host}/teacher   （在笔记本上用这个地址打开老师端）")
    firewall = firewall_preflight(lan_ip)
    print("=" * 72, flush=True)

    started = now_iso()
    backend = start_backend(args.backend_port, db_path, uvicorn_log)
    proxy_python = args.proxy_python or sys.executable
    proxy_log = os.path.join(ARTIFACTS, f"device-{nonce}-proxy.log")
    proxy: LoggingProxy | None = None
    proxy_child: subprocess.Popen | None = None
    if os.path.abspath(proxy_python) != os.path.abspath(sys.executable):
        proxy_child = start_proxy_child(proxy_python, port=args.port,
                                        backend_port=args.backend_port, lan_host=lan_host,
                                        request_log=request_log, proxy_log=proxy_log)
    else:
        proxy = LoggingProxy(("0.0.0.0", args.port), ProxyHandler,
                             upstream_host=lan_host, upstream_port=args.backend_port,
                             log_path=request_log)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()

    try:
        # 1) 经代理建卷+发布：产品看到的 Host 就是手机将访问的地址
        created = httpx.post(f"http://127.0.0.1:{args.port}/api/papers",
                             params={"seed": 20261015, "title": f"真机验收卷-{nonce}"},
                             json=dict(CONFIG_ADD_SUB, count=3), timeout=20)
        created.raise_for_status()
        paper_id = int(created.json()["id"])
        published = httpx.post(
            f"http://127.0.0.1:{args.port}/api/papers/{paper_id}/publish", timeout=20)
        published.raise_for_status()
        info = published.json()
        url = info["student_url"]
        qr_url = f"http://127.0.0.1:{args.port}{info['qr_url']}"
        png = httpx.get(qr_url, timeout=20).content
        with open(qr_path, "wb") as handle:
            handle.write(png)

        print(f"\n卷号 code = {info['code']}   paper_id = {paper_id}")
        print(f"学生端 URL = {url}")
        print(f"（标题带 nonce：真机验收卷-{nonce}）\n")
        print("请用手机扫码（或直接输入上面的 URL），走完：填姓名学号 → 题目 → 交卷。")
        print("二维码：\n")
        print_qr(url)
        print(f"\n等待真机提交，最多 {args.minutes:g} 分钟……", flush=True)

        deadline = time.time() + args.minutes * 60
        rows: list[dict] = []
        warned = False
        while time.time() < deadline:
            rows = attempt_rows(db_path, paper_id)
            if rows:
                break
            phone_seen = [e for e in read_entries(request_log)
                          if e["client_ip"] not in ("127.0.0.1", "::1")]
            if not warned and phone_seen:
                print("  已看到手机请求，等待交卷……", flush=True)
                warned = True
            time.sleep(2)

        finished = now_iso()
        all_entries = read_entries(request_log)
        phone_entries = [e for e in all_entries
                         if e["client_ip"] not in ("127.0.0.1", "::1")]
        wechat = [e for e in phone_entries if "MicroMessenger" in e["ua"]]
        uas = sorted({e["ua"] for e in phone_entries})
        uvicorn_text = ""
        if os.path.exists(uvicorn_log):
            with open(uvicorn_log, "r", encoding="utf-8", errors="replace") as handle:
                uvicorn_text = handle.read()
        uvicorn_phone = [line.strip() for line in uvicorn_text.splitlines()
                         if "127.0.0.1" not in line and " - " in line]

        verdict = {
            "t1_1_client_ip_is_not_loopback": bool(phone_entries),
            "t1_2_user_agent_recorded": bool(uas),
            "t1_3_wechat_inner_browser_ua": bool(wechat),
            "t1_4_attempt_row_points_at_this_nonce_paper": bool(rows),
            "t1_5_attempt_row_is_from_the_phone_client": bool(rows and phone_entries),
        }
        summary = {
            "nonce": nonce,
            "label": args.label,
            "lan_ip": lan_ip,
            "lan_host": lan_host,
            "candidates": candidates,
            "lan_ip_described": described,
            "lan_ip_reason": why,
            "firewall": firewall,
            "server_bound": f"proxy 0.0.0.0:{args.port} -> uvicorn 127.0.0.1:{args.backend_port}",
            "paper": {"id": paper_id, "code": info["code"], "title": f"真机验收卷-{nonce}",
                      "student_url": url, "qr_png": qr_path},
            "window": {"started": started, "finished": finished},
            "proxy_interpreter": proxy_python,
            "proxy_log": proxy_log,
            "proxy_requests_total": len(all_entries),
            "phone_requests": phone_entries,
            "phone_user_agents": uas,
            "wechat_requests": len(wechat),
            "uvicorn_phone_lines": uvicorn_phone[:20],
            "attempt_rows": rows,
            "verdict": verdict,
            "artifacts": {"requests_jsonl": request_log, "uvicorn_log": uvicorn_log,
                          "db": db_path},
            "hashes": {
                "backend_app": aggregate_hash([os.path.join(REPO, "backend", "app")],
                                              exts=(".py",)),
                "e2e_m4_scope": aggregate_hash([HERE]),
            },
        }
        with open(session_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        # 逐轮留档：`device-session.json` 是「最新一轮」的别名，第二轮会覆盖它，
        # 而验收要同时引用多轮（1a 系统浏览器 / 1b 微信），所以按 nonce 再存一份。
        per_nonce_path = os.path.join(ARTIFACTS, f"device-session-{nonce}.json")
        with open(per_nonce_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)

        print("\n" + "=" * 72)
        for key, ok in verdict.items():
            print(f"{'PASS' if ok else 'FAIL'}  {key}")
        print(f"手机请求 {len(phone_entries)} 条；UA：{uas or '（无）'}")
        print(f"attempt 行：{rows or '（无）'}")
        print(f"证据：{session_path}")
        if not phone_entries:
            print("\n手机一次都没连上 —— 这是**现场事实**（不是推测）。下一步：改走 Plan B，"
                  "\n用笔记本开移动热点（Windows 设置 → 移动热点），然后带上 `--lan-ip "
                  "<热点网关IP>` 重跑本脚本。")
        print("=" * 72, flush=True)
        return 0 if rows and phone_entries else 2
    finally:
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        if proxy_child is not None:
            proxy_child.terminate()
            try:
                proxy_child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy_child.kill()
        backend.terminate()
        try:
            backend.wait(timeout=15)
        except subprocess.TimeoutExpired:
            backend.kill()


if __name__ == "__main__":
    if "--proxy-only" in sys.argv:
        raise SystemExit(proxy_only([arg for arg in sys.argv[1:] if arg != "--proxy-only"]))
    raise SystemExit(main())
