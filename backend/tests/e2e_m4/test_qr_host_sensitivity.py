"""Tier 0-① 二维码必须编码**局域网可达的绝对地址**（契约 §5.3 / D54）。

判据（全部机验，不需要手机）：

1. `POST /publish` 的 `student_url` 用**请求的 `Host` 头**构造（不是 `localhost`、
   也不是服务端探测的网卡）；
2. `GET /api/papers/{id}/qr` 的 PNG 与 `qrcode.make(<期望 URL>)` **逐像素相同**
   ——「二维码里写的确实是那个网址」这件事不靠人眼看；
3. 换 `Host` 必须换内容（差分判据，同时是 ② 的负对照：证明比较器有分辨力）；
4. **把坑变成判据**：老师若用 `localhost` 打开老师端，二维码里就是 `localhost`，
   手机**扫了也打不开**——所以「用局域网地址打开老师端」不是建议，是前提。
"""
from __future__ import annotations

import io
import ipaddress
import unittest

import qrcode
from PIL import Image, ImageChops

from tests.e2e_m4._harness import (
    AppClient,
    dump_evidence,
    lan_ip_candidates,
    record_evidence,
)

LAN_HOST = "192.168.1.50:8000"        # 合成局域网地址：判据不依赖本机当时的网络
LAN_HOST_ALT = "192.168.1.51:8000"
LOCAL_HOST = "localhost:8000"


def _image(payload: bytes) -> Image.Image:
    return Image.open(io.BytesIO(payload)).convert("RGB")


def _encoded(url: str) -> Image.Image:
    """独立用 `qrcode` 编码同一字符串（与产品实现同库、同默认参数）。"""
    return qrcode.make(url).get_image().convert("RGB")


def _pixel_diff(a: Image.Image, b: Image.Image):
    return ImageChops.difference(a, b).getbbox()


class TestQrHostSensitivity(unittest.TestCase):
    """二维码内容 = f(请求 Host)，且编码结果可逐像素复核。"""

    @classmethod
    def setUpClass(cls):
        cls.client = AppClient().__enter__()
        # 先用 LAN_HOST 发布（同一张卷的 code 与 Host 无关，只有 URL 与二维码有关）
        cls.published = cls.client.published_paper(host=LAN_HOST, count=3)
        cls.paper_id = int(cls.published["id"])
        cls.code = cls.published["code"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_01_publish_uses_the_request_host(self):
        expected = f"http://{LAN_HOST}/student/paper/{self.code}"
        self.assertEqual(self.published["student_url"], expected)
        self.assertEqual(self.published["qr_url"], f"/api/papers/{self.paper_id}/qr")
        self.assertFalse(self.published["qr_url"].startswith("http"),
                         "qr_url 必须是相对路径（同源引用，契约 §5.3）")

    def test_02_qr_is_pixel_identical_to_the_lan_url(self):
        url = f"http://{LAN_HOST}/student/paper/{self.code}"
        actual = _image(self.client.qr_png(self.paper_id, host=LAN_HOST))
        expected = _encoded(url)
        self.assertEqual(actual.size, expected.size)
        self.assertIsNone(
            _pixel_diff(actual, expected),
            f"二维码内容不是期望的绝对局域网地址：{url}")
        record_evidence("qr_host_sensitivity", lan_url=url, png_size=actual.size)

    def test_03_changing_the_host_changes_the_qr(self):
        lan_png = _image(self.client.qr_png(self.paper_id, host=LAN_HOST))
        local_png = _image(self.client.qr_png(self.paper_id, host=LOCAL_HOST))
        self.assertIsNotNone(
            _pixel_diff(lan_png, local_png),
            "换了 Host 二维码却一模一样 —— 说明它编码的不是请求 URL")
        local_url = f"http://{LOCAL_HOST}/student/paper/{self.code}"
        self.assertIsNone(
            _pixel_diff(local_png, _encoded(local_url)),
            f"localhost 场景下二维码内容应为：{local_url}")
        # 换到另一个局域网地址同样要变（不只是「本地 vs 远程」的差别）
        alt_png = _image(self.client.qr_png(self.paper_id, host=LAN_HOST_ALT))
        self.assertIsNotNone(_pixel_diff(lan_png, alt_png))

    def test_04_localhost_qr_is_unreachable_from_a_phone(self):
        """把「老师用 localhost 打开」的后果钉成判据，而不是留在经验里。"""
        lan_body = self.client.request("POST", f"/api/papers/{self.paper_id}/publish",
                                       host=LAN_HOST).json()
        local_body = self.client.request("POST", f"/api/papers/{self.paper_id}/publish",
                                         host=LOCAL_HOST).json()
        self.assertEqual(lan_body["student_url"], f"http://{LAN_HOST}/student/paper/{self.code}")
        self.assertEqual(local_body["student_url"],
                         f"http://{LOCAL_HOST}/student/paper/{self.code}")

        lan_ip = lan_body["student_url"].split("/")[2].split(":")[0]
        self.assertTrue(ipaddress.ip_address(lan_ip).is_private,
                        f"局域网地址应是私有地址，实际 {lan_ip}")
        local_ip = local_body["student_url"].split("/")[2].split(":")[0]
        self.assertEqual(local_ip, "localhost")
        self.assertNotEqual(lan_body["student_url"], local_body["student_url"])
        record_evidence(
            "qr_localhost_risk",
            lan_student_url=lan_body["student_url"],
            localhost_student_url=local_body["student_url"],
            conclusion="二维码内容取决于 Host：用 localhost 打开老师端 → 手机扫到 localhost → 打不开",
        )

    def test_05_comparator_has_teeth(self):
        """正/负对照：像素判据必须能区分「对的 URL」与「错的 URL」。"""
        url = f"http://{LAN_HOST}/student/paper/{self.code}"
        actual = _image(self.client.qr_png(self.paper_id, host=LAN_HOST))
        wrong_host = _encoded(f"http://{LAN_HOST_ALT}/student/paper/{self.code}")
        wrong_code = _encoded(f"http://{LAN_HOST}/student/paper/9999-0001")
        wrong_scheme = _encoded(f"https://{LAN_HOST}/student/paper/{self.code}")
        for label, candidate in (("换主机", wrong_host), ("换卷号", wrong_code),
                                 ("换协议", wrong_scheme)):
            with self.subTest(错误内容=label):
                self.assertIsNotNone(_pixel_diff(actual, candidate),
                                     f"比较器没能区分出「{label}」")
        self.assertIsNone(_pixel_diff(actual, _encoded(url)),
                          "比较器对正确内容必须判等（否则它是恒红，没有信息量）")

    def test_06_real_lan_ip_round_trips(self):
        """本机实测局域网地址也走一遍（不依赖它存在，缺了就如实记录）。"""
        candidates = lan_ip_candidates()
        record_evidence("qr_real_lan_ip", candidates=candidates)
        if not candidates:
            self.skipTest("本机没有可用的局域网地址（只有回环/链路本地）")
        for ip in candidates[:2]:
            with self.subTest(局域网地址=ip):
                host = f"{ip}:8000"
                body = self.client.request("POST", f"/api/papers/{self.paper_id}/publish",
                                           host=host).json()
                expected_url = f"http://{host}/student/paper/{self.code}"
                self.assertEqual(body["student_url"], expected_url)
                png = _image(self.client.qr_png(self.paper_id, host=host))
                self.assertIsNone(_pixel_diff(png, _encoded(expected_url)))


def tearDownModule():
    print(f"\n[Tier0-①] 证据已落盘：{dump_evidence()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
