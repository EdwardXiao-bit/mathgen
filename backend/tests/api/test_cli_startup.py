"""启动器在打开浏览器之前，必须等端口真的能连上。

为什么有这条判据：老师双击脚本启动时，uvicorn 的冷启动（导入 FastAPI / SQLAlchemy、
建库、定位前端产物）实测能到十几秒。早先的实现是「打横幅 → 等 1.5 秒 → 打开浏览器」，
慢一点的机器上浏览器抢在监听之前发请求，看到的是「无法访问此网站」——用户 2026-10-28
报的就是这个。修法是把「开浏览器」放到 `announce_ready` 里，先 `wait_until_ready`
探测端口，连上了才开。

这条判据测三件事：
- 端口有人监听时 `wait_until_ready` 立刻返回 True；
- 没人监听时它会在超时后返回 False（不能无限等，也不能骗自己说就绪）；
- `announce_ready` 只有在就绪后才开浏览器，没就绪就不开（负对照：否则用户还是
  会被弹到一个连不上的页面）。
"""
from __future__ import annotations

import socket
import time
import unittest
from unittest import mock

from app import __main__ as cli


class _Listener:
    """在 127.0.0.1 上占一个端口并 listen，模拟「uvicorn 已经起来了」。"""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def close(self) -> None:
        self.sock.close()


def _closed_port() -> int:
    """拿一个刚刚被关闭的端口：上面一定没有监听。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


URLS = {
    "home": "http://127.0.0.1:8000/",
    "teacher": "http://127.0.0.1:8000/teacher",
    "student": "http://127.0.0.1:8000/student",
    "docs": "http://127.0.0.1:8000/docs",
}


class TestWaitUntilReady(unittest.TestCase):
    # 判据问题 1：已经能连上时，不该再白等（否则启动感觉变慢）。
    def test_returns_true_quickly_when_port_listens(self) -> None:
        listener = _Listener()
        try:
            started = time.monotonic()
            ready = cli.wait_until_ready("127.0.0.1", listener.port, timeout=5.0)
            elapsed = time.monotonic() - started
        finally:
            listener.close()
        self.assertTrue(ready)
        self.assertLess(elapsed, 2.0, f"已经能连上了还等了 {elapsed:.2f} 秒")

    # 判据问题 2：没人监听时必须如实返回 False，不能假装就绪。
    def test_returns_false_after_timeout_when_nothing_listens(self) -> None:
        port = _closed_port()
        started = time.monotonic()
        ready = cli.wait_until_ready("127.0.0.1", port, timeout=0.4, interval=0.05)
        elapsed = time.monotonic() - started
        self.assertFalse(ready)
        self.assertGreaterEqual(elapsed, 0.35, "没等够超时就返回了 False")

    # 判据问题 3：监听在 0.0.0.0（局域网模式）时，本机要连回环地址。
    def test_wildcard_host_probes_loopback(self) -> None:
        self.assertEqual(cli._readiness_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(cli._readiness_host("::"), "127.0.0.1")
        self.assertEqual(cli._readiness_host("192.168.1.5"), "192.168.1.5")


class TestAnnounceReady(unittest.TestCase):
    def _run(self, port: int, *, open_browser: bool, timeout: float = 5.0):
        printed: list[str] = []
        with mock.patch.object(cli.webbrowser, "open") as opened:
            ok = cli.announce_ready(
                URLS, "127.0.0.1", port, open_browser,
                timeout=timeout, out=printed.append,
            )
        return ok, printed, opened

    def test_opens_the_home_page_after_the_service_listens(self) -> None:
        listener = _Listener()
        try:
            ok, printed, opened = self._run(listener.port, open_browser=True)
        finally:
            listener.close()
        self.assertTrue(ok)
        opened.assert_called_once_with(URLS["home"])
        self.assertTrue(any("启动完成" in line for line in printed), printed)

    def test_respects_no_browser(self) -> None:
        listener = _Listener()
        try:
            ok, _printed, opened = self._run(listener.port, open_browser=False)
        finally:
            listener.close()
        self.assertTrue(ok)
        opened.assert_not_called()

    # 负对照：端口一直连不上，就绝不能开浏览器——那正是用户看到的错误页面。
    def test_never_opens_a_page_that_cannot_be_reached(self) -> None:
        port = _closed_port()
        ok, printed, opened = self._run(port, open_browser=True, timeout=0.4)
        self.assertFalse(ok)
        opened.assert_not_called()
        self.assertTrue(any("还没起来" in line for line in printed), printed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
