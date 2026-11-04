"""启动横幅必须报出「学生真的连得上」的地址，而且要报全。

为什么有这条判据：老师常见配置是**笔记本连着校园网、同时开着热点，手机连热点**。
默认路由走校园网，于是「探测一块网卡」的老做法只会打印校园网地址——手机在热点网段里
根本路由不到（用户 2026-11-04 报「扫码打不开」）。手机唯一可达的是 Windows 移动热点
（ICS）AP 侧的 `192.168.137.1`（M4 的手机实测：`backend/tests/e2e_m4/REPORT-task-30.md`）。

判据测五件事：
- 热点地址排第一，理由那句话要能让人看懂该用哪条；
- 回环与 169.254 链路本地必须被丢掉（列出来只会让老师挑错）；
- 去重、保序，`lan_ip()` 取第一个，一个都没有时返回 None；
- `ipconfig` 的中英文两种行都能解析（Windows 的主机名解析不保证列全每块网卡）；
- 横幅在局域网模式下逐条列出候选、端口用真的那个；本机地址模式下不列（没意义）。
"""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
