"""启动横幅必须报出「学生真的连得上」的地址，而且要报全。

为什么有这条判据：老师常见配置是**笔记本连着校园网、同时开着热点，手机连热点**。
默认路由走校园网，于是「探测一块网卡」的老做法只会打印校园网地址——手机在热点网段里
根本路由不到（用户 2026-11-04 报「扫码打不开」）。手机唯一可达的是 Windows 移动热点
（ICS）AP 侧的 `192.168.137.1`（M4 的手机实测：`backend/tests/e2e_m4/REPORT-task-30.md`）。

判据测六件事：
- 热点地址排第一，理由那句话要能让人看懂该用哪条；
- 回环与 169.254 链路本地必须被丢掉（列出来只会让老师挑错）；
- 去重、保序，`lan_ip()` 取第一个，一个都没有时返回 None；
- `ipconfig` 的中英文两种行都能解析（Windows 的主机名解析不保证列全每块网卡）；
- Windows 的 `ipconfig` 输出是 CP936 字节，而一键脚本会把 `PYTHONUTF8=1` 传给子进程 ——
  必须自己按字节解码，不能交给 `subprocess` 的文本模式（否则 reader 线程抛
  UnicodeDecodeError，地址列表变空；用户 2026-11-11 报的启动 traceback 就是这个）；
- 横幅在局域网模式下逐条列出候选、端口用真的那个；本机地址模式下不列（没意义）。
"""
from __future__ import annotations

import types
import unittest
from pathlib import Path
from unittest import mock

from app import __main__ as cli


def _banner(host: str, port: int = 8000) -> str:
    urls = {
        "home": f"http://127.0.0.1:{port}/",
        "teacher": f"http://127.0.0.1:{port}/teacher",
        "student": f"http://127.0.0.1:{port}/student",
        "docs": f"http://127.0.0.1:{port}/docs",
    }
    return cli._banner(urls, Path("data/mathgen.db"), host, True, port)


class TestLanAddresses(unittest.TestCase):
    def test_hotspot_address_ranks_first(self):
        """默认路由在校园网时，热点地址仍要排第一并能自证理由。"""
        with mock.patch.object(cli, "_default_route_ip", return_value="10.98.246.81"), \
             mock.patch.object(cli, "_ipv4_addresses",
                               return_value=["10.98.246.81", "192.168.45.1", "192.168.137.1"]):
            candidates = cli.lan_ips()
        self.assertEqual(candidates[0]["ip"], "192.168.137.1")
        self.assertIn("热点", str(candidates[0]["reason"]))
        self.assertEqual([item["ip"] for item in candidates[1:]], ["10.98.246.81", "192.168.45.1"])

    def test_loopback_and_link_local_are_dropped(self):
        """回环 / 169.254 不是给手机用的地址，不能出现在候选里。"""
        with mock.patch.object(cli, "_default_route_ip", return_value="127.0.0.1"), \
             mock.patch.object(cli, "_ipv4_addresses",
                               return_value=["127.0.0.1", "169.254.10.20", "192.168.1.8"]):
            candidates = cli.lan_ips()
        self.assertEqual([item["ip"] for item in candidates], ["192.168.1.8"])

    def test_duplicates_keep_first_position(self):
        """三路来源会重复报同一个地址，去重后按第一次出现的顺序。"""
        with mock.patch.object(cli, "_default_route_ip", return_value="192.168.1.8"), \
             mock.patch.object(cli, "_ipv4_addresses",
                               return_value=["192.168.1.8", "192.168.137.1", "192.168.1.8"]):
            candidates = cli.lan_ips()
        self.assertEqual([item["ip"] for item in candidates], ["192.168.137.1", "192.168.1.8"])

    def test_lan_ip_is_first_candidate_or_none(self):
        with mock.patch.object(cli, "_default_route_ip", return_value="10.1.2.3"), \
             mock.patch.object(cli, "_ipv4_addresses", return_value=["10.1.2.3", "192.168.137.1"]):
            self.assertEqual(cli.lan_ip(), "192.168.137.1")
        with mock.patch.object(cli, "_default_route_ip", return_value=None), \
             mock.patch.object(cli, "_ipv4_addresses", return_value=[]):
            self.assertIsNone(cli.lan_ip())

    def test_ipconfig_lines_are_parsed_in_both_locales(self):
        """中英文 `ipconfig` 的 IPv4 行都要能抓出来（含热点那块网卡）。"""
        chinese = (
            "以太网适配器 本地连接* 2:\n"
            "   IPv4 地址 . . . . . . . . . . . . : 192.168.137.1\n"
            "   IPv4 地址 . . . . . . . . . . . . : 169.254.3.9\n"
        )
        english = (
            "Wireless LAN adapter WLAN:\n"
            "   IPv4 Address. . . . . . . . . . . : 10.98.246.81\n"
        )
        self.assertEqual(cli._IPV4_LINE.findall(chinese), ["192.168.137.1", "169.254.3.9"])
        self.assertEqual(cli._IPV4_LINE.findall(english), ["10.98.246.81"])

    def test_gbk_ipconfig_output_is_decoded_not_crashed(self):
        """简中 Windows 的 `ipconfig` 输出是 CP936 字节，必须自己解码。

        用户 2026-11-11 用一键脚本启动时看到一段 `UnicodeDecodeError` traceback：
        脚本给子进程设了 `PYTHONUTF8=1`，`subprocess` 的文本模式于是用 UTF-8 解
        `ipconfig` 的 CP936 输出 —— reader 线程炸掉，`stdout` 变成空，地址列表也就空了
        （热点那条又不报了）。所以这里同时钉两件事：字节喂进来要能解析出 IP；
        并且**不允许**再把解码交给 `subprocess`（`text=` / `encoding=` / `errors=` 都不能传）。
        """
        gbk = (
            "以太网适配器 本地连接* 2:\r\n"
            "   IPv4 地址 . . . . . . . . . . . . : 192.168.137.1\r\n"
        ).encode("gbk")
        calls: list[dict[str, object]] = []

        def fake_run(command, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(stdout=gbk, returncode=0)

        with mock.patch.object(cli.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(cli.sys, "platform", "win32"):
            found = cli._system_ipv4_addresses()

        self.assertEqual(found, ["192.168.137.1"])
        self.assertTrue(calls, "没有真的去调系统命令")
        for kwargs in calls:
            for forbidden in ("text", "encoding", "errors", "universal_newlines"):
                self.assertNotIn(forbidden, kwargs)

    def test_undecodable_output_is_swallowed(self):
        """完全认不出的字节也不能抛异常（这条路径只是「多给一条线索」）。"""
        with mock.patch.object(
            cli.subprocess,
            "run",
            return_value=types.SimpleNamespace(stdout=b"\xff\xfe\xb1\x00", returncode=0),
        ), mock.patch.object(cli.sys, "platform", "win32"):
            self.assertEqual(cli._system_ipv4_addresses(), [])

    def test_decode_console_prefers_utf8_then_ansi(self):
        """UTF-8 的字节按 UTF-8 解；CP936 的字节按 ANSI 解；两者都不是也不抛。"""
        self.assertEqual(cli.decode_console("IPv4 地址".encode("utf-8")), "IPv4 地址")
        self.assertEqual(cli.decode_console("IPv4 地址".encode("gbk")), "IPv4 地址")
        self.assertIsInstance(cli.decode_console(b"\xff\xfe\xb1"), str)

    def test_banner_lists_every_candidate_in_lan_mode(self):
        """局域网模式下逐条列出，并且用真实端口（不是写死的 8000）。"""
        with mock.patch.object(cli, "_default_route_ip", return_value="10.98.246.81"), \
             mock.patch.object(cli, "_ipv4_addresses",
                               return_value=["10.98.246.81", "192.168.137.1"]):
            text = _banner("0.0.0.0", port=8123)
        self.assertIn("http://192.168.137.1:8123/student", text)
        self.assertIn("http://10.98.246.81:8123/student", text)
        self.assertIn("热点", text)
        self.assertLess(text.index("192.168.137.1"), text.index("10.98.246.81"))

    def test_banner_says_nothing_about_lan_in_local_mode(self):
        """用本机地址启动时，别给出学生地址（给了也是连不上）。"""
        with mock.patch.object(cli, "_default_route_ip", return_value="10.98.246.81"), \
             mock.patch.object(cli, "_ipv4_addresses", return_value=["192.168.137.1"]):
            text = _banner("127.0.0.1")
        self.assertNotIn("192.168.137.1", text)

    def test_local_mode_tells_you_how_to_let_phones_in(self):
        """本机模式下横幅要说清「谁能打开」以及怎么切到局域网模式。

        一键脚本默认已经是局域网模式了，但直接跑 CLI / pip 装的人会落在默认值上；
        只说「127.0.0.1」会让人以为产品不支持手机答题。
        """
        with mock.patch.object(cli, "_default_route_ip", return_value="10.98.246.81"), \
             mock.patch.object(cli, "_ipv4_addresses", return_value=[]):
            text = _banner("127.0.0.1")
        self.assertIn("只有这台电脑能打开", text)
        self.assertIn("scripts", text)
        self.assertIn("start.sh", text)

    def test_lan_mode_does_not_print_the_local_only_hint(self):
        """局域网模式下不该再教人切模式。"""
        with mock.patch.object(cli, "_default_route_ip", return_value="192.168.137.1"), \
             mock.patch.object(cli, "_ipv4_addresses", return_value=["192.168.137.1"]):
            text = _banner("0.0.0.0")
        self.assertNotIn("只有这台电脑能打开", text)


if __name__ == "__main__":
    unittest.main()
