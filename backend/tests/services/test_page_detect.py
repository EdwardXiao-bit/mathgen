"""页面定位与校正（M18-03）的判据.

照片不是「渲染出来的」，所以判据也不能只靠 Playwright 截图：这里用 numpy/cv2
**合成**一张与本系统打印物同几何的卷面（四角块 + 页脚二维码位置都取自
`app.render.sheet` 的常量），再对它做透视、旋转、光照梯度、JPEG 压缩，
模拟真实拍照。这样判据离线可跑，而且失败时能定位到「是定位错了还是解码错了」。
"""

from __future__ import annotations

import json
import unittest

try:  # 可选依赖：不在 [ocr] extra 里时整模块跳过（CI 默认环境不装 opencv）
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - 只有没装 extra 的环境会走到
    raise unittest.SkipTest("需要 [ocr] extra（numpy / opencv）")

from app.render import marking
from app.render.sheet import (
    CORNER_INSET_MM,
    CORNER_SIZE_MM,
    QR_BOTTOM_MM,
    QR_INSET_MM,
    QR_SIZE_MM,
)
from app.services import page_detect

MM = 25.4  # 与 page_detect 一样按 150dpi 出图
PX_PER_MM = page_detect.RECTIFIED_DPI / MM
CHECKSUM = "0123456789abcdef"


def _qr_tile(
    size_px: int, checksum: str = CHECKSUM, *, margin_mm: float | None = None
) -> np.ndarray:
    png = marking.qr_png_bytes(
        marking.qr_payload(
            "2611-0001",
            checksum,
            layout=marking.LAYOUT_VERSION,
            margin_mm=margin_mm,
        )
    )
    tile = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.resize(tile, (size_px, size_px), interpolation=cv2.INTER_NEAREST)


def printed_page(checksum: str = CHECKSUM, *, margin_mm: float | None = None) -> np.ndarray:
    """合成一张 150dpi 的打印卷面：白纸 + 四角块 + 页脚二维码.

    `checksum` 可换成某张真卷的 `sheet_checksum()`，这样上传用例的二维码
    能和卷的真实 checksum 对上（`rectify(expected_checksum=...)` 才能通过）。

    `margin_mm` 非空时把页边距写进二维码 payload（键 `m`）：打印媒体里
    `position: fixed` 锚在**内容区**，角块实际落在「纸边 + 页边距 + 6mm」，
    识别端必须按同一口径映射（2026-11-18 实测 1.27× 放大 + 20mm 左移）。
    """
    page = np.full(
        (page_detect.RECTIFIED_HEIGHT, page_detect.RECTIFIED_WIDTH, 3), 255, dtype=np.uint8
    )
    margin_px = round((margin_mm or 0.0) * PX_PER_MM)
    side = round(CORNER_SIZE_MM * PX_PER_MM)
    inset = margin_px + round(CORNER_INSET_MM * PX_PER_MM)
    width = page.shape[1]
    height = page.shape[0]
    corners = [
        (inset, inset),
        (width - inset - side, inset),
        (inset, height - inset - side),
        (width - inset - side, height - inset - side),
    ]
    for x, y in corners:
        page[y : y + side, x : x + side] = 0
    qr_side = round(QR_SIZE_MM * PX_PER_MM)
    qr_x = width - margin_px - round(QR_INSET_MM * PX_PER_MM) - qr_side
    qr_y = height - margin_px - round(QR_BOTTOM_MM * PX_PER_MM) - qr_side
    page[qr_y : qr_y + qr_side, qr_x : qr_x + qr_side] = _qr_tile(
        qr_side, checksum, margin_mm=margin_mm
    )
    return page


def photograph(
    page: np.ndarray,
    *,
    rotate_deg: float = 0.0,
    jitter_px: float = 0.0,
    width: int = 1800,
    gradient: float = 0.0,
    seed: int = 7,
) -> bytes:
    """把卷面「拍成照片」：透视 + 旋转 + 光照梯度 + JPEG 压缩，返回 JPEG 字节.

    旋转角度大时，画布要按旋转后的外接框放大：纸 297mm 高、转 11.5° 后
    外接框宽 210*cos+297*sin ≈ 265mm，按 210mm 开画布会把左右两角块切出画面
    （第一版判据就是这么误报 `wrong_sheet` 的）。
    """
    rng = np.random.default_rng(seed)
    height, width_px = page.shape[:2]
    scale = width / width_px
    theta = np.deg2rad(abs(rotate_deg))
    cos, sin = float(np.cos(theta)), float(np.sin(theta))
    inner_w, inner_h = width, round(height * scale)
    # 旋转后外接框 + 两侧各 7% 背景
    canvas_w = round((inner_w * cos + inner_h * sin) * 1.14)
    canvas_h = round((inner_w * sin + inner_h * cos) * 1.14)
    canvas_w += canvas_w % 2
    canvas_h += canvas_h % 2

    left = (canvas_w - inner_w) // 2
    top = (canvas_h - inner_h) // 2
    source = np.array(
        [[0, 0], [width_px, 0], [width_px, height], [0, height]], dtype=np.float32
    )
    jitter = rng.uniform(-jitter_px, jitter_px, size=(4, 2)).astype(np.float32)
    target = np.array(
        [[left, top], [left + inner_w, top],
         [left + inner_w, top + inner_h], [left, top + inner_h]],
        dtype=np.float32,
    ) + jitter
    matrix = cv2.getPerspectiveTransform(source, target)
    photo = cv2.warpPerspective(page, matrix, (canvas_w, canvas_h), borderValue=(28, 28, 30))

    center = (canvas_w / 2.0, canvas_h / 2.0)
    rotation = cv2.getRotationMatrix2D(center, rotate_deg, 1.0)
    photo = cv2.warpAffine(photo, rotation, (canvas_w, canvas_h), borderValue=(28, 28, 30))

    if gradient:
        ramp = np.linspace(1.0 - gradient, 1.0, photo.shape[1], dtype=np.float32)
        photo = np.clip(photo.astype(np.float32) * ramp[None, :, None], 0, 255).astype(np.uint8)
    noise = rng.normal(0.0, 2.0, photo.shape).astype(np.float32)
    photo = np.clip(photo.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    ok, buffer = cv2.imencode(".jpg", photo, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    assert ok
    return buffer.tobytes()


class RectifyTest(unittest.TestCase):
    def test_plain_scan_is_rectified_and_marking_preserved(self) -> None:
        result = page_detect.rectify(photograph(printed_page(), jitter_px=0.0))
        self.assertEqual(result.marking["checksum"], CHECKSUM)
        self.assertEqual(result.marking["paper"], "2611-0001")
        self.assertEqual(result.image.shape[:2], (page_detect.RECTIFIED_HEIGHT, page_detect.RECTIFIED_WIDTH))
        self.assertFalse(result.rotated)

    def test_margin_payload_maps_corner_blocks_back_to_the_paper(self) -> None:
        """页边距 20mm 时，角块在纸上的位置是「20 + 6」mm，校正后必须回到这里.

        2026-11-18 的缺陷：打印媒体里 `position: fixed` 锚在**内容区**，角块
        实际落在纸边内 26mm，而识别端按「距纸边 6mm」硬编码 → 整页放大 1.27×
        并向左偏 20mm，裁图全部错位。二维码里带 `m` 后两边口径才对上。
        """
        margin_mm = 20.0
        result = page_detect.rectify(photograph(printed_page(margin_mm=margin_mm)))
        self.assertEqual(result.marking["m"], 20)
        self.assertEqual(result.marking["checksum"], CHECKSUM)

        gray = cv2.cvtColor(result.image, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        centers = []
        for contour in contours:
            rect = cv2.minAreaRect(contour)
            (cx, cy), (bw, bh), _ = rect
            if bw < 20 or bh < 20:
                continue
            if not (0.6 <= min(bw, bh) / max(bw, bh) <= 1.7):
                continue
            if cv2.contourArea(contour) / (bw * bh) < 0.75:
                continue
            centers.append((cx, cy))
        self.assertEqual(len(centers), 4, f"应只认出四个角块，实际 {centers}")
        expected = (margin_mm + CORNER_INSET_MM + CORNER_SIZE_MM / 2) * PX_PER_MM
        for cx, cy in centers:
            near_x = min(abs(cx - expected), abs(cx - (page_detect.RECTIFIED_WIDTH - expected)))
            near_y = min(abs(cy - expected), abs(cy - (page_detect.RECTIFIED_HEIGHT - expected)))
            self.assertLess(near_x, 8, f"角块横向位置偏了 {near_x:.1f}px（{cx:.0f}）")
            self.assertLess(near_y, 8, f"角块纵向位置偏了 {near_y:.1f}px（{cy:.0f}）")

    def test_small_qr_is_decoded_by_region_fallback(self) -> None:
        """页脚二维码只有约 118px 时，整图 detector 会漏，分区域回退必须能解.

        payload 加了 `m` 字段后二维码版本变高、模块变小（2026-11-18 实测
        本用例在修前抛 `no_qr`）。
        """
        page = np.full(
            (page_detect.RECTIFIED_HEIGHT, page_detect.RECTIFIED_WIDTH, 3),
            255,
            dtype=np.uint8,
        )
        tile = _qr_tile(round(QR_SIZE_MM * PX_PER_MM), margin_mm=20.0)
        side = tile.shape[0]
        margin_px = round(20 * PX_PER_MM)
        right = page.shape[1] - margin_px - round(QR_INSET_MM * PX_PER_MM)
        bottom = page.shape[0] - margin_px - round(QR_BOTTOM_MM * PX_PER_MM)
        page[bottom - side : bottom, right - side : right] = tile
        gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        text, points = page_detect._decode_qr(gray)
        self.assertIsNotNone(text, "分区域回退没解出页脚二维码")
        payload = page_detect.parse_marking(text)
        self.assertEqual(payload["m"], 20)
        self.assertEqual(payload["checksum"], CHECKSUM)
        self.assertEqual(len(points), 4)

    def test_perspective_tilt_and_lighting_gradient(self) -> None:
        photo = photograph(printed_page(), rotate_deg=-11.5, jitter_px=26, gradient=0.45)
        result = page_detect.rectify(photo)
        self.assertEqual(result.marking["checksum"], CHECKSUM)

        # 校正后的四角块中心必须回到目标位置（±8px）：定位错了这里立刻变大
        scale = PX_PER_MM
        expected = [
            ((CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale, (CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale),
            (page_detect.RECTIFIED_WIDTH - (CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale,
             (CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale),
            (page_detect.RECTIFIED_WIDTH - (CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale,
             page_detect.RECTIFIED_HEIGHT - (CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale),
            ((CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale,
             page_detect.RECTIFIED_HEIGHT - (CORNER_INSET_MM + CORNER_SIZE_MM / 2) * scale),
        ]
        gray = cv2.cvtColor(result.image, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
        for index, (ex, ey) in enumerate(expected):
            with self.subTest(corner=index):
                window = binary[max(0, int(ey) - 40) : int(ey) + 40, max(0, int(ex) - 40) : int(ex) + 40]
                ys, xs = np.nonzero(window)
                self.assertGreater(len(xs), 20, "校正图上该角找不到黑块")
                cx = xs.mean() + max(0, int(ex) - 40)
                cy = ys.mean() + max(0, int(ey) - 40)
                self.assertLess(abs(cx - ex), 8.0)
                self.assertLess(abs(cy - ey), 8.0)

    def test_upside_down_scan_is_turned_upright(self) -> None:
        page = cv2.rotate(printed_page(), cv2.ROTATE_180)
        result = page_detect.rectify(photograph(page, rotate_deg=3.0))
        self.assertTrue(result.rotated)
        center = page_detect._qr_center(result.image)
        self.assertIsNotNone(center)
        assert center is not None
        self.assertGreater(center[1], page_detect.RECTIFIED_HEIGHT / 2)

    def test_blank_photo_is_rejected_as_not_our_sheet(self) -> None:
        blank = np.full((1400, 1000, 3), 240, dtype=np.uint8)
        ok, buffer = cv2.imencode(".jpg", blank)
        assert ok
        with self.assertRaises(page_detect.PageRejected) as caught:
            page_detect.rectify(buffer.tobytes())
        self.assertEqual(caught.exception.code, "no_qr")

    def test_missing_corner_blocks_is_rejected(self) -> None:
        page = printed_page()
        side = round(CORNER_SIZE_MM * PX_PER_MM)
        inset = round(CORNER_INSET_MM * PX_PER_MM)
        height, width = page.shape[:2]
        for x, y in (
            (inset, inset),
            (width - inset - side, inset),
            (inset, height - inset - side),
            (width - inset - side, height - inset - side),
        ):
            page[y : y + side, x : x + side] = 255  # 擦掉四角块
        with self.assertRaises(page_detect.PageRejected) as caught:
            page_detect.rectify(photograph(page))
        self.assertEqual(caught.exception.code, "wrong_sheet")

    def test_checksum_mismatch_is_rejected(self) -> None:
        with self.assertRaises(page_detect.PageRejected) as caught:
            page_detect.rectify(photograph(printed_page()), expected_checksum="0" * 16)
        self.assertEqual(caught.exception.code, "checksum")

    def test_layout_mismatch_is_rejected(self) -> None:
        with self.assertRaises(page_detect.PageRejected) as caught:
            page_detect.rectify(photograph(printed_page()), expected_layout="99")
        self.assertEqual(caught.exception.code, "layout")

    def test_matching_expected_checksum_passes(self) -> None:
        result = page_detect.rectify(
            photograph(printed_page()), expected_checksum=CHECKSUM
        )
        self.assertEqual(result.marking["checksum"], CHECKSUM)


class ParseMarkingTest(unittest.TestCase):
    def test_rejects_non_json_and_unknown_version(self) -> None:
        for text in ("not json", "[]", json.dumps({"v": 9, "paper": "p", "checksum": "c", "layout": "1"})):
            with self.subTest(text=text):
                with self.assertRaises(page_detect.PageRejected):
                    page_detect.parse_marking(text)

    def test_requires_all_fields(self) -> None:
        payload = {"v": marking.QR_FORMAT, "paper": "2611-0001", "checksum": CHECKSUM}
        with self.assertRaises(page_detect.PageRejected):
            page_detect.parse_marking(json.dumps(payload))


class JpegRoundTripTest(unittest.TestCase):
    def test_encode_decode(self) -> None:
        page = printed_page()
        again = page_detect.decode_jpeg(page_detect.encode_jpeg(page, quality=90))
        self.assertEqual(again.shape, page.shape)
        self.assertLess(np.abs(again.astype(int) - page.astype(int)).mean(), 3.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
