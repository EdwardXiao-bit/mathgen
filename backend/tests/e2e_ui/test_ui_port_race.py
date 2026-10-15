"""task-33 负对照：端口竞争**真的**会被救回来，而且**不会**吞掉真故障。

背景（Lead 全树门禁实测 + 受控实验）：`free_port()` 是 TOCTOU —— 探测到的端口在 uvicorn
真正 bind 之前可能被别的套接字占走。Windows 下这一次失败长得像权限问题：

    ERROR: [Errno 13] error while attempting to bind on address ('127.0.0.1', 64695):
    [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。

实际上 10013 在这里的含义就是「端口已被别人占着」（uvicorn 0.54.0 的
`Config.bind_socket()` 无条件设了 `SO_REUSEADDR`，Windows 上把「地址已在使用」翻译成了
「访问权限不允许」）。修复见 `backend/tests/_uvicorn_boot.py`。

本模块给出**两个方向**的结论（V11），都用真实 uvicorn、真实监听套接字，不用桩：

  方向一 `TestPortRaceRetryIsReal`
      ① 端口被我自己的活监听套接字占住 → `boot_uvicorn` 换端口后**起绿**；
      ② 同一场景 `max_attempts=1` → **必红**（这就是门禁里那一次的形态），
         且错误信息是人话（点明「疑似端口竞争」「不是防火墙、也不是权限问题」）。
  方向二 `TestNonPortFailureIsNotSwallowed`
      喂一个**非端口原因**的真故障（app 目标写坏 → 导入错误）→ **只尝试 1 次**、
      uvicorn 原始输出原样透出、消息里不出现「疑似端口竞争」或 10013。

加上 `TestBindFailureClassifier` 对判据本身做双向单测（认得出 bind 失败 / 不把真 bug 当端口问题）。

    & $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -p "test_ui_port_race.py" -v
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
sys.path.insert(0, HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.makedirs(ARTIFACTS, exist_ok=True)

VPY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(VPY):
    VPY = sys.executable

from tests import _uvicorn_boot as boot  # noqa: E402


class _OccupiedPort:
    """用一个**真实监听套接字**占住端口。

    刻意不复用 `free_port()`：那是「探测后立刻关掉」，不构成占用；本类要保持 `listen()`
    直到 `__exit__`，复现「窗口里端口被抢走」的那一半。
    """

    def __enter__(self):
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        return self

    def __exit__(self, *exc):
        self._socket.close()
        return False


def _command(port: int) -> list[str]:
    """与 `test_ui_structure.RealServer._command()` 同形（产品的一条命令启动）。"""
    return [VPY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]


def _broken_command(port: int) -> list[str]:
    """**非端口原因**的真故障：app 目标写坏（`app.does_not_exist` 导入必失败）。"""
    return [VPY, "-m", "uvicorn", "app.does_not_exist:app", "--app-dir", "backend",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]


def _env() -> dict:
    env = dict(os.environ)
    env.update({
        "MATHGEN_DB": os.path.join(ARTIFACTS, f"port-race-{uuid.uuid4().hex[:8]}.db"),
        "MATHGEN_PW_TMP": os.path.join(REPO, ".tmp-pw"),
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("MATHGEN_STATIC_DIR", None)
    return env


def _stop(process) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


class TestBindFailureClassifier(unittest.TestCase):
    """判据本身：只认 bind 失败的证据。"""

    def test_recognises_the_bind_failure_forms(self):
        self.assertTrue(boot.looks_like_bind_failure(
            "ERROR: [Errno 13] error while attempting to bind on address ('127.0.0.1', 64695): "
            "[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。"))
        self.assertTrue(boot.looks_like_bind_failure("winerror=10013"))
        self.assertTrue(boot.looks_like_bind_failure("[Errno 98] Address already in use"))
        self.assertTrue(boot.looks_like_bind_failure("WinError 10048 地址已在使用"))

    def test_does_not_call_a_real_bug_a_port_problem(self):
        """**方向二的核心**：真故障绝不能被判成端口问题（否则重试会把它吞掉）。"""
        self.assertFalse(boot.looks_like_bind_failure(
            "ModuleNotFoundError: No module named 'app.does_not_exist'"))
        self.assertFalse(boot.looks_like_bind_failure(
            "AssertionError: 判据失败：期望 200，实际 500"))
        self.assertFalse(boot.looks_like_bind_failure(""))
        self.assertFalse(boot.looks_like_bind_failure(None))


class TestPortRaceRetryIsReal(unittest.TestCase):
    """方向一：重试真的会发生（两态对照：不重试必红 / 重试起绿）。"""

    def test_retry_moves_to_another_port_and_serves(self):
        """端口被活监听套接字占住 → 换端口后**真的起来了**。"""
        with _OccupiedPort() as held:
            process, port, attempts = boot.boot_uvicorn(
                _command, cwd=REPO, env=_env(), timeout=40.0,
                max_attempts=boot.MAX_BOOT_ATTEMPTS, first_port=held.port)
        try:
            self.assertGreaterEqual(len(attempts), 2,
                                    f"第一次尝试指向被占端口，应当换端口重试：{attempts}")
            self.assertEqual(attempts[0]["port"], held.port)
            self.assertEqual(attempts[0]["outcome"], "bind-failure",
                             f"被占端口上的第一次尝试应当是 bind 失败：{attempts[0]}")
            self.assertTrue(boot.looks_like_bind_failure(attempts[0]["output"]),
                            f"第一次尝试的输出里应当有 bind 失败证据：{attempts[0]['output']!r}")
            self.assertNotEqual(port, held.port, "最终端口必须换过")
            self.assertTrue(boot.http_ready(port), f"http://127.0.0.1:{port}/api/papers 应当 200")
        finally:
            _stop(process)

    def test_without_the_retry_the_same_race_is_a_hard_failure(self):
        """同一场景、`max_attempts=1`：必红，且错误信息是人话。"""
        with _OccupiedPort() as held:
            with self.assertRaises(boot.UvicornBootError) as ctx:
                boot.boot_uvicorn(_command, cwd=REPO, env=_env(), timeout=20.0,
                                  max_attempts=1, first_port=held.port)
        error = ctx.exception
        self.assertTrue(error.port_race, "应当判定为端口竞争")
        self.assertEqual(len(error.attempts), 1)
        self.assertEqual(error.attempts[0]["outcome"], "bind-failure")
        message = str(error)
        self.assertIn("疑似端口竞争", message)
        self.assertIn(boot.BIND_FAILURE_HINT, message)
        self.assertIn("不是防火墙", message, "错误信息要拦住『去查防火墙』的念头")
        self.assertIn(str(held.port), message, "要能看出是哪个端口")
        self.assertIn("netstat", message, "要告诉下一个人怎么查是谁占着")


class TestNonPortFailureIsNotSwallowed(unittest.TestCase):
    """方向二：非端口原因的真故障必须**一次都不重试**，原样可见。"""

    def test_broken_app_target_is_reported_once_without_retrying(self):
        with self.assertRaises(boot.UvicornBootError) as ctx:
            boot.boot_uvicorn(_broken_command, cwd=REPO, env=_env(), timeout=30.0,
                              max_attempts=boot.MAX_BOOT_ATTEMPTS)
        error = ctx.exception
        self.assertFalse(error.port_race, "导入错误不是端口竞争")
        self.assertEqual(len(error.attempts), 1,
                         f"非端口原因必须一次都不重试，实际尝试了 {len(error.attempts)} 次")
        self.assertEqual(error.attempts[0]["outcome"], "early-exit")
        raw = error.attempts[0]["output"]
        self.assertIn("does_not_exist", raw, f"原始输出要如实透出：{raw[-400:]!r}")
        message = str(error)
        self.assertIn("非端口原因", message)
        self.assertNotIn("疑似端口竞争", message)
        self.assertNotIn("10013", message)
        self.assertIn("does_not_exist", message, "原始输出要进错误信息")


if __name__ == "__main__":
    unittest.main(verbosity=2)
