"""二维码与学生端 URL 测试（契约 §5.1 / §5.3，D53 + D54 冻结）。

覆盖：
- `PublishResult.student_url` 是**绝对** URL，由请求 `Host` 头构造；`qr_url` 保持相对
- `GET /api/papers/{id}/qr` 返回真 PNG（魔数 + PIL 可开），内容与 `student_url` 一致
- 二维码内容对 Host 敏感（换 Host → 换内容），且与独立用 `qrcode` 编码的结果**逐像素相同**
- draft → 409 `PAPER_NOT_PUBLISHED`；卷不存在 → 404 `PAPER_NOT_FOUND`；副本（draft）同样 409
- `Host` 缺失时退回 `request.base_url`（不猜网卡）
"""
from __future__ import annotations

import io
import unittest

import qrcode
from PIL import Image

from app.routers import papers as papers_router
from app.routers.urls import qr_url_for, student_url_for
from app.services import qr_svc
from tests.api._support import ApiTestCase, url_of


class _FakeRequest:
    """只带 `headers` 与 `base_url` 的假请求（用于单测 URL 组装）。"""

    def __init__(self, headers: dict[str, str], base_url: str = "http://127.0.0.1:8000/") -> None:
        self.headers = headers
        self.base_url = base_url


def _pixels(png: bytes) -> tuple[tuple[int, int], bytes]:
    """PNG → (尺寸, 原始灰度像素)。用 `tobytes()` 而非已弃用的 `getdata()`。"""
    image = Image.open(io.BytesIO(png)).convert("L")
    return image.size, image.tobytes()


class TestStudentUrlHelper(unittest.TestCase):
    def test_uses_host_header(self):
        request = _FakeRequest({"host": "192.168.1.5:8000"})
        self.assertEqual(
            student_url_for(request, "2610-0007"),
            "http://192.168.1.5:8000/student/paper/2610-0007",
        )

    def test_falls_back_to_base_url_without_host(self):
        request = _FakeRequest({}, base_url="http://127.0.0.1:8000/")
        self.assertEqual(
            student_url_for(request, "2610-0007"),
            "http://127.0.0.1:8000/student/paper/2610-0007",
        )

    def test_empty_code_is_rejected(self):
        with self.assertRaises(ValueError):
            student_url_for(_FakeRequest({"host": "a"}), "")

    def test_qr_url_is_relative(self):
        self.assertEqual(qr_url_for(7), "/api/papers/7/qr")
        self.assertFalse(qr_url_for(7).startswith("http"))


class TestQrService(unittest.TestCase):
    def test_png_bytes_returns_png_magic(self):
        png = qr_svc.png_bytes("http://192.168.1.5:8000/student/paper/2610-0001")
        self.assertTrue(png.startswith(qr_svc.PNG_MAGIC))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_png_bytes_is_deterministic(self):
        payload = "http://192.168.1.5:8000/student/paper/2610-0001"
        self.assertEqual(qr_svc.png_bytes(payload), qr_svc.png_bytes(payload))

    def test_png_bytes_encodes_given_data(self):
        """与独立用 `qrcode` 编码同一字符串的结果逐像素相同（证明编码的确实是入参）。"""
        payload = "http://192.168.1.5:8000/student/paper/2610-0001"
        self.assertEqual(_pixels(qr_svc.png_bytes(payload)), _pixels(_reference_png(payload)))

    def test_png_bytes_rejects_empty(self):
        with self.assertRaises(ValueError):
            qr_svc.png_bytes("")

    def test_different_payload_yields_different_qr(self):
        first = qr_svc.png_bytes("http://192.168.1.5:8000/student/paper/2610-0001")
        second = qr_svc.png_bytes("http://192.168.1.6:8000/student/paper/2610-0001")
        self.assertNotEqual(first, second)


def _reference_png(payload: str) -> bytes:
    """测试侧独立编码（不用被测服务），用于像素级比对。"""
    buffer = io.BytesIO()
    qrcode.make(payload).save(buffer, format="PNG")
    return buffer.getvalue()


class TestPublishUrls(ApiTestCase):
    def test_publish_student_url_is_absolute(self):
        created = self.create_paper({"count": 2})
        body = self.publish(created["id"]).json()
        self.assertEqual(
            body["student_url"], f"http://testserver/student/paper/{body['code']}"
        )
        self.assertTrue(body["student_url"].startswith("http://"))

    def test_publish_honours_host_header(self):
        created = self.create_paper({"count": 2})
        response = self.client.post(
            url_of("publish_paper", paper_id=created["id"]),
            headers={"Host": "192.168.1.5:8000"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body["student_url"], f"http://192.168.1.5:8000/student/paper/{body['code']}"
        )

    def test_publish_qr_url_stays_relative(self):
        created = self.create_paper({"count": 2})
        body = self.publish(created["id"]).json()
        self.assertEqual(body["qr_url"], f"/api/papers/{created['id']}/qr")
        self.assertFalse(body["qr_url"].startswith("http"))

    def test_publish_same_code_with_different_hosts(self):
        """卷号与 Host 无关：换个访问地址不会改变已分配的卷号。"""
        created = self.create_paper({"count": 2})
        first = self.publish(created["id"]).json()
        second = self.client.post(
            url_of("publish_paper", paper_id=created["id"]),
            headers={"Host": "10.0.0.9:8000"},
        ).json()
        self.assertEqual(first["code"], second["code"])
        self.assertEqual(second["student_url"], f"http://10.0.0.9:8000/student/paper/{first['code']}")


class TestQrRoute(ApiTestCase):
    def test_qr_returns_valid_png(self):
        created = self.create_paper({"count": 2})
        published = self.publish(created["id"]).json()

        response = self.client.get(url_of("qr", paper_id=created["id"]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(qr_svc.PNG_MAGIC))

        image = Image.open(io.BytesIO(response.content))
        self.assertEqual(image.format, "PNG")
        self.assertGreater(image.size[0], 0)

    def test_qr_content_equals_absolute_student_url(self):
        """二维码编码的正是 `publish` 返回的绝对 `student_url`。"""
        created = self.create_paper({"count": 2})
        published = self.publish(created["id"]).json()

        response = self.client.get(url_of("qr", paper_id=created["id"]))
        # 与「独立用 qrcode 编码 publish 给出的 student_url」逐像素相同
        self.assertEqual(
            _pixels(response.content),
            _pixels(_reference_png(published["student_url"])),
        )

    def test_qr_honours_host_header(self):
        created = self.create_paper({"count": 2})
        published = self.publish(created["id"]).json()
        expected = f"http://192.168.1.5:8000/student/paper/{published['code']}"

        response = self.client.get(
            url_of("qr", paper_id=created["id"]), headers={"Host": "192.168.1.5:8000"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(_pixels(response.content), _pixels(_reference_png(expected)))
        # 与默认 Host 的二维码不同（证明 Host 真的进了编码内容）
        default_response = self.client.get(url_of("qr", paper_id=created["id"]))
        self.assertNotEqual(response.content, default_response.content)

    def test_qr_is_deterministic_across_calls(self):
        created = self.create_paper({"count": 2})
        self.publish(created["id"])
        first = self.client.get(url_of("qr", paper_id=created["id"])).content
        second = self.client.get(url_of("qr", paper_id=created["id"])).content
        self.assertEqual(first, second)

    def test_qr_payload_passed_to_encoder_is_absolute(self):
        """直接抓取交给编码器的字符串（不依赖像素比对）。"""
        created = self.create_paper({"count": 2})
        published = self.publish(created["id"]).json()
        captured: list[str] = []
        original = qr_svc.png_bytes

        def spy(data: str) -> bytes:
            captured.append(data)
            return original(data)

        qr_svc.png_bytes = spy
        try:
            response = self.client.get(
                url_of("qr", paper_id=created["id"]), headers={"Host": "192.168.1.5:8000"}
            )
        finally:
            qr_svc.png_bytes = original

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured, [f"http://192.168.1.5:8000/student/paper/{published['code']}"])

    def test_qr_draft_is_409_paper_not_published(self):
        created = self.create_paper({"count": 2})
        response = self.client.get(url_of("qr", paper_id=created["id"]))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_PUBLISHED")
        self.assertEqual(set(response.json()), {"error"})

    def test_qr_copy_of_published_paper_is_409(self):
        """副本是 draft（无卷号），二维码同样不可用。"""
        created = self.create_paper({"count": 2})
        self.publish(created["id"])
        clone = self.client.post(url_of("copy_paper", paper_id=created["id"])).json()
        self.assertEqual(clone["status"], "draft")

        response = self.client.get(url_of("qr", paper_id=clone["id"]))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_PUBLISHED")

    def test_qr_unknown_paper_is_404(self):
        response = self.client.get(url_of("qr", paper_id=987654))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_qr_route_is_registered_from_frozen_constant(self):
        """路径必须原样出现在 OpenAPI（即引用 `ROUTES` 常量而非另写字符串）。"""
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/api/papers/{paper_id}/qr", paths)
        self.assertIn("get", paths["/api/papers/{paper_id}/qr"])
        self.assertIn("/api/papers/{paper_id}/copy", paths)

    def test_copy_route_uses_frozen_constant(self):
        """D53：`ROUTES["copy_paper"]` 是唯一来源，路由层不得再定义本地常量。"""
        self.assertFalse(hasattr(papers_router, "COPY_ROUTE"))
        from app.contracts.api import ROUTES

        self.assertEqual(ROUTES["copy_paper"], "/api/papers/{paper_id}/copy")
        self.assertEqual(ROUTES["qr"], "/api/papers/{paper_id}/qr")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
