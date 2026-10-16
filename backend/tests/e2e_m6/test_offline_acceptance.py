"""task-49 判据（NFR-9 模式 A）：断网下「生成卷 → 预览 → 打印/导出 PDF」全程可用。

为什么长这样
------------
* **判据进程不装守卫**：只有先证明「此前外网本来是通的」，断网态里的失败才能归因给守卫
  （V9 对照）。若判据进程也断网，「打不开外网」什么都证明不了。
* **「断了网」必须自证**：外部访问失败的异常必须带守卫自己的标记（`OfflineBlocked`），
  且守卫日志里要有对应记录 —— 否则分不出「我拦住了」和「网本来就不通」。
* **浏览器层单独测**：产品自己的 Chromium 由**服务进程内**的 Playwright 拉起，Python 的
  socket 补丁管不到它，所以守卫在 `BrowserType.launch` 上再打一层（强制死代理 +
  `--host-resolver-rules`），并要求服务进程的守卫日志里有 `chromium-launch` 记录 +
  参数内容 —— 否则「Chromium 打不开外网」可能只是因为它本来就打不开。
* **本会话非管理员**：不动 `netsh` / 网卡 / 路由，只做进程级隔离（提权动作一律没试过，
  也不谎报）。

本套件不能发现什么（诚实边界）
------------------------------
* 进程内 socket 补丁**挡不住别的进程**：同机其它程序、系统级后台流量照旧，本套件不测它们。
* Chromium 的 `--host-resolver-rules` + 死代理挡的是**浏览器**的网络栈；操作系统级的
  DNS/连接能力没有被改变（真断网时另有一类失败，例如系统代理自动发现超时，本套件看不到）。
* 「显式配了本机代理」的客户端**仍然能出去**（守卫口径是「非环回即拒绝」）—— 已如实登记。
* 只覆盖模式 A（出题/打印），不覆盖离线下的作答、成绩、导出成绩等其它链路。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.e2e_m4 import _harness
from tests.e2e_m6 import _m6_support as m6

ARTIFACTS = m6.ARTIFACTS
GUARD_BASE = ARTIFACTS / "task49-guard.json"
WORLD: dict = {}


def setUpModule() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    # 先清掉上一轮留下的守卫日志：否则「服务进程的 Chromium 参数被强制改写过」这条
    # 判据可能看到**上一轮**写下的 chromium-launch 记录而假绿。
    for stale in ARTIFACTS.glob("task49-guard*.json*"):
        stale.unlink()
    server = m6.OfflineServer(db_path=str(ARTIFACTS / "task49-offline.db"), log_path=GUARD_BASE)
    server.__enter__()
    paper_id = server.create_paper(count=3, seed=20261015, title="NFR-9 离线验收卷")
    server.publish(paper_id)
    sheet = m6.sheet_response(server, paper_id)
    sheet_file = ARTIFACTS / "task49-sheet.html"
    sheet_file.write_text(sheet.text, encoding="utf-8")
    WORLD.update(
        {
            "server": server,
            "paper_id": paper_id,
            "sheet": sheet,
            "sheet_file": sheet_file,
        }
    )


def tearDownModule() -> None:
    server = WORLD.get("server")
    if server is not None:
        server.__exit__(None, None, None)
    WORLD.clear()


class M6Case(unittest.TestCase):
    @property
    def server(self):
        return WORLD["server"]

    @property
    def paper_id(self) -> int:
        return WORLD["paper_id"]


class TestTheGuardActuallyCutsTheNetwork(M6Case):
    """先证明「断网」是守卫干的，再证明产品在断网下能用。"""

    def test_01_without_the_guard_the_internet_is_reachable(self):
        """V9 对照：同一时间窗里不装守卫，外网必须真的通（否则本套件结论无效）。"""
        result = m6.run_probe(
            m6.SOCKET_PROBE,
            json_path=ARTIFACTS / "task49-socket-control.json",
            hooked=False,
            extra_args=("--loopback-url", f"{self.server.base_url}/api/papers/{self.paper_id}/sheet"),
            log_path=ARTIFACTS / "task49-guard-control.json",
        )
        probe = result.get("json")
        problems = m6.socket_probe_problems(probe, label="对照（未装守卫）", hooked=False)
        m6.write_evidence(
            "task49-socket-control-evidence.json",
            {
                "purpose": "V9 对照：不装守卫时外网是通的",
                "returncode": result.get("returncode"),
                "problems": problems,
                "observations": (probe or {}).get("observations"),
            },
        )
        self.assertEqual([], problems, f"对照组无效，本次离线验证作废：{problems}")

    def test_02_with_the_guard_external_access_fails_with_the_guards_marker(self):
        result = m6.run_probe(
            m6.SOCKET_PROBE,
            json_path=ARTIFACTS / "task49-socket-hooked.json",
            hooked=True,
            extra_args=("--loopback-url", f"{self.server.base_url}/api/papers/{self.paper_id}/sheet"),
            log_path=ARTIFACTS / "task49-guard-probe.json",
        )
        probe = result.get("json")
        problems = m6.socket_probe_problems(probe, label="断网态", hooked=True)
        m6.write_evidence(
            "task49-socket-hooked-evidence.json",
            {
                "purpose": "断网自证：外部访问必须被守卫以 OfflineBlocked 拦住",
                "returncode": result.get("returncode"),
                "problems": problems,
                "observations": (probe or {}).get("observations"),
            },
        )
        self.assertEqual([], problems, f"断网没有自证：{problems}")

    def test_03_the_guards_own_log_records_install_and_blocks(self):
        """守卫日志是第三个来源：探针说被拦了，进程内也要留下「装过、拦过」的记录。"""
        logs = m6.read_guard_logs(ARTIFACTS / "task49-guard-probe.json")
        problems = m6.guard_log_problems(logs, label="探针进程守卫日志", expect_chromium_launch=False, min_blocked=1)
        m6.write_evidence(
            "task49-guard-log-summary.json",
            {
                "purpose": "守卫日志：install + blocked 记录",
                "files": len(logs),
                "kinds": [entry.get("kind") for log in logs for entry in (log.get("blocked") or [])],
                "problems": problems,
            },
        )
        self.assertEqual([], problems, f"守卫日志不能自证：{problems}")

    def test_04_negative_control_a_reachable_external_observation_is_caught(self):
        """负对照（留仓库）：把「外网可达」的观测喂给同一条判据，它必须红。"""
        fake = {
            "guard": {"module_loaded": True},
            "observations": {
                "loopback_dns": {"outcome": "ok"},
                "loopback_connect": {"outcome": "error", "type": "TimeoutError"},
                "external_dns": {"outcome": "ok", "label": "外部 DNS"},
                "external_connect": {"outcome": "ok", "label": "外部连接"},
                "external_connect_raw_ip": {"outcome": "ok", "label": "外部字面 IP 连接"},
                "external_https": {"outcome": "ok", "status": 200, "label": "外部 HTTPS"},
            },
        }
        problems = m6.socket_probe_problems(fake, label="负对照", hooked=True)
        joined = " | ".join(problems)
        self.assertTrue(problems, "同一条判据在「外网可达」的观测上竟然没红")
        self.assertIn("没有被守卫拦住", joined)


class TestTheProductFlowWorksOffline(M6Case):
    """断网态下：生成卷（已在夹具里）→ 预览卷面 → 服务端导出 PDF。"""

    def test_01_the_sheet_preview_renders_without_external_resources(self):
        sheet = WORLD["sheet"]
        self.assertEqual([], m6.http_problems(sheet, where="断网态预览", status=200))
        html = sheet.text
        self.assertGreater(len(html), 500, "卷面 HTML 太短，夹具可能没生成出来")
        problems = m6.markup_external_problems(html, where="断网态预览")
        m6.write_evidence(
            "task49-sheet-markup.json",
            {
                "purpose": "卷面 HTML 里不得有外部 URL（NFR-9）",
                "status": sheet.status_code,
                "bytes": len(html),
                "markup_urls": m6.markup_urls(html),
                "problems": problems,
            },
        )
        self.assertEqual([], problems, f"卷面里出现了外部资源：{problems}")

    def test_02_the_server_exports_a_real_pdf_while_offline(self):
        response = m6.export_response(self.server, self.paper_id, file_format="pdf")
        problems = m6.http_problems(response, where="断网态导出 PDF", status=200)
        if not problems:
            problems = m6.pdf_bytes_problems(response.content, where="断网态导出 PDF")
        m6.write_evidence(
            "task49-server-pdf.json",
            {
                "purpose": "服务端（断网态）导出 PDF",
                "status": response.status_code,
                "bytes": len(response.content),
                "magic": response.content[:5].decode("latin-1", "replace"),
                "problems": problems,
            },
        )
        self.assertEqual([], problems, f"断网态导出失败：{problems}")

    def test_03_the_servers_guard_log_shows_chromium_was_forced_offline(self):
        """服务进程里导出 PDF 会拉起 Chromium；守卫日志必须显示它的启动参数被改写过。

        **按日志基名筛选，不按 pid**：本机 venv 的 `python.exe` 会把真正的解释器当子进程
        拉起来（实测 launcher pid 33688 / 真正干活的 23880），所以 `UiServer.process.pid`
        并不等于「导入守卫的那个进程」的 pid。日志基名是本套件独有的，足以隔离服务进程。
        """
        logs = m6.read_guard_logs(GUARD_BASE)
        problems = m6.guard_log_problems(logs, label="服务进程守卫日志", expect_chromium_launch=True)
        m6.write_evidence(
            "task49-server-guard-log.json",
            {
                "purpose": "服务进程守卫日志：install + chromium-launch + 强制参数",
                "launcher_pid": None if self.server.process is None else self.server.process.pid,
                "log_pids": [log.get("pid") for log in logs],
                "logs": logs,
                "problems": problems,
            },
        )
        self.assertEqual([], problems, f"服务进程的 Chromium 没有被强制断网：{problems}")

    def test_04_negative_control_a_pdf_without_the_magic_is_caught(self):
        problems = m6.pdf_bytes_problems(b"<html>not a pdf</html>", where="负对照")
        self.assertTrue(problems, "前 5 字节不是 %PDF- 却没红")
        self.assertIn("%PDF-", " | ".join(problems))


class TestTheBrowserIsOfflineWhileItPrints(M6Case):
    """产品自己那份 Chromium（服务进程内 Playwright 拉起）的断网 + 打印。"""

    def _browser_probe(self, *, hooked: bool, name: str):
        extra = [
            "--loopback-url",
            f"{self.server.base_url}/api/papers/{self.paper_id}/sheet",
            "--html-file",
            str(WORLD["sheet_file"]),
        ]
        if not hooked:
            extra += ["--proxy", m6.LOCAL_PROXY]
        return m6.run_probe(
            m6.BROWSER_PROBE,
            json_path=ARTIFACTS / name,
            hooked=hooked,
            extra_args=tuple(extra),
            log_path=ARTIFACTS / f"task49-guard-{name.replace('.json', '')}.json",
        )

    def test_01_the_control_browser_can_reach_the_internet(self):
        result = self._browser_probe(hooked=False, name="task49-browser-control.json")
        probe = result.get("json")
        problems = m6.browser_probe_problems(probe, label="对照（未装守卫）", hooked=False)
        m6.write_evidence(
            "task49-browser-control-evidence.json",
            {
                "purpose": "V9 对照：未装守卫时这份 Chromium 能打开外网",
                "returncode": result.get("returncode"),
                "problems": problems,
                "external": (probe or {}).get("external"),
                "requests": (probe or {}).get("requests"),
            },
        )
        self.assertEqual([], problems, f"浏览器对照组无效：{problems}")

    def test_02_the_browser_previews_and_prints_while_offline(self):
        result = self._browser_probe(hooked=True, name="task49-browser-hooked.json")
        probe = result.get("json")
        problems = m6.browser_probe_problems(probe, label="断网态浏览器", hooked=True)
        payload = probe or {}
        rendered = (payload.get("requests") or [])[: int(payload.get("requests_before_external") or 0)]
        m6.write_evidence(
            "task49-browser-hooked-evidence.json",
            {
                "purpose": "断网态：预览可用、打印成 PDF、渲染期间零外部请求",
                "returncode": result.get("returncode"),
                "problems": problems,
                "loopback": payload.get("loopback"),
                "pdf": payload.get("pdf"),
                "external": payload.get("external"),
                "requests_before_external": payload.get("requests_before_external"),
                "requests_rendered": rendered,
                "forced_chromium_args": (payload.get("guard") or {}).get("forced_chromium_args"),
            },
        )
        self.assertEqual([], problems, f"断网态下浏览器不可用：{problems}")
        self.assertTrue(rendered, "渲染期间一条请求都没记录到，观测点可能没挂上")

    def test_03_the_browser_probes_guard_log_shows_the_forced_args(self):
        collected = m6.read_guard_logs(ARTIFACTS / "task49-guard-task49-browser-hooked.json")
        problems = m6.guard_log_problems(collected, label="浏览器探针守卫日志", expect_chromium_launch=True)
        m6.write_evidence(
            "task49-browser-guard-log.json",
            {
                "purpose": "浏览器探针守卫日志：强制启动参数",
                "logs": collected,
                "problems": problems,
            },
        )
        self.assertEqual([], problems, f"浏览器探针的守卫日志不能自证：{problems}")

    def test_04_negative_control_an_external_resource_in_a_page_is_caught(self):
        problems = m6.external_request_problems(
            [{"url": "https://cdn.example.com/app.js"}], label="负对照"
        )
        self.assertTrue(problems, "页面请求了外部资源却没红")
        self.assertIn("cdn.example.com", " | ".join(problems))


if __name__ == "__main__":
    unittest.main()
