"""task-49 探针（Python 层）：断网自证。

用法（由 `_m6_support.run_probe` 拼装）：

    python backend/tests/e2e_m6/offline/socket_probe.py --json <out.json> \
        [--loopback-url http://127.0.0.1:PORT/api/papers]

必须在**目标环境**里跑：装了守卫（目录在 `PYTHONPATH` 上）就是「断网态」，不装就是
「对照态」。结论一律写 JSON 文件，不靠 stdout —— task-48 的教训是管道会骗人。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.request


def _chain(exc: BaseException):
    """异常链（含 `urllib` 的 `URLError.reason`）—— 守卫抛在里面的那层也要认出来。"""
    seen: set[int] = set()
    queue = [exc]
    while queue:
        current = queue.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for attr in ("reason", "__cause__", "__context__"):
            nxt = getattr(current, attr, None)
            if isinstance(nxt, BaseException):
                queue.append(nxt)


def classify(exc: BaseException) -> dict:
    guard = sys.modules.get("sitecustomize")
    marker_cls = getattr(guard, "OfflineBlocked", None)
    nodes = list(_chain(exc))
    guard_node = None
    if marker_cls is not None:
        for node in nodes:
            if isinstance(node, marker_cls):
                guard_node = node
                break
    return {
        "outcome": "blocked" if guard_node is not None else "error",
        "type": type(exc).__name__,
        "marker": guard_node is not None,
        "wrapped_by": [type(node).__name__ for node in nodes],
        "message": (str(guard_node) if guard_node is not None else str(exc))[:300],
    }


def timed(label: str, fn) -> dict:
    started = time.perf_counter()
    try:
        value = fn()
    except BaseException as exc:  # noqa: BLE001 - 探针要把任何失败原样记下来
        result = classify(exc)
    else:
        result = {"outcome": "ok", "value": value}
    result["label"] = label
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    return result


def http_get(url: str, *, proxy: str | None = None, timeout: float = 8.0) -> dict:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    response = opener.open(url, timeout=timeout)
    try:
        body = response.read(2048)
    finally:
        response.close()
    return {
        "status": int(getattr(response, "status", 0) or 0),
        "bytes": len(body),
        "prefix": body[:16].decode("utf-8", "replace"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--loopback-url", default=None)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    args = parser.parse_args(argv)

    guard = sys.modules.get("sitecustomize")
    payload: dict = {
        "probe": "socket",
        "python": sys.version.split()[0],
        "pid": os.getpid(),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "guard": {
            "module_loaded": guard is not None,
            "verdict": getattr(guard, "VERDICT", None),
            "pythonpath": os.environ.get("PYTHONPATH", ""),
            "env_flag": os.environ.get("MATHGEN_OFFLINE_GUARD"),
        },
        "observations": {},
    }
    observations = payload["observations"]

    observations["loopback_dns"] = timed(
        "getaddrinfo(localhost, 443)", lambda: socket.getaddrinfo("localhost", 443)[0][4]
    )
    observations["loopback_connect"] = timed(
        "create_connection((127.0.0.1, 9))",
        lambda: _connect_close(("127.0.0.1", 9), timeout=2.0),
    )
    if args.loopback_url:
        observations["loopback_http"] = timed(
            f"GET {args.loopback_url}",
            lambda: http_get(args.loopback_url, timeout=8.0),
        )

    observations["external_dns"] = timed(
        "getaddrinfo(example.com, 443)",
        lambda: socket.getaddrinfo("example.com", 443)[0][4],
    )
    observations["external_connect"] = timed(
        "create_connection((example.com, 443))",
        lambda: _connect_close(("example.com", 443), timeout=6.0),
    )
    observations["external_connect_raw_ip"] = timed(
        "create_connection((104.20.23.154, 443))",
        lambda: _connect_close(("104.20.23.154", 443), timeout=6.0),
    )
    observations["external_https"] = timed(
        "GET https://example.com/",
        lambda: http_get("https://example.com/", timeout=8.0),
    )
    observations["external_https_via_local_proxy"] = timed(
        f"GET https://example.com/ via {args.proxy}",
        lambda: http_get("https://example.com/", proxy=args.proxy, timeout=10.0),
    )

    if guard is not None:
        payload["guard"]["blocked"] = guard.blocked_entries()

    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"socket_probe → {args.json}")

    # 退出码永远为 0：判据读 JSON，不把「外网失败」变成脚本失败。
    return 0


def _connect_close(address, *, timeout: float) -> dict:
    sock = socket.create_connection(address, timeout=timeout)
    try:
        return {"peer": repr(sock.getpeername()), "tls_ok": _tls_handshake(sock, address)}
    finally:
        sock.close()


def _tls_handshake(sock, address) -> bool:
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(sock, server_hostname=str(address[0])):
            return True
    except BaseException:  # noqa: BLE001 - 只关心「能不能握手成功」
        return False


if __name__ == "__main__":
    raise SystemExit(main())
