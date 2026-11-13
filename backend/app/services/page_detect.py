"""拍照卷面的定位与校正（M18-03，D92 / docs/10 §8）.

输入是一张「打印出来的本系统卷面」的照片，输出是摆正到 A4 坐标系的白底页面图。
识别链路：**先解页脚二维码**（确认是本系统、checksum 与版面版本对得上），
**再找四角定位块**（解出 8×8mm 黑块的四心），用四心 → A4 目标点的单应变换把
照片拉到 150dpi 的 A4 画布（1240×1754）。

为什么先解二维码再找角块：二维码能回答「这是不是我们的卷子」，还能给出
checksum 供上层核对；角块只回答「四角在哪」。两者都失败时的拒绝原因不同，
上层据此回 422（不是本系统卷面）还是 409（checksum/版面不符）。

本模块只依赖 numpy/opencv，不 import FastAPI、数据库或渲染模块的 I/O 部分；
图像以 `bytes` 或 `numpy.ndarray` 传入，便于单测直接喂合成图。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

import cv2
import numpy as np

from app.render.marking import LAYOUT_VERSION, QR_FORMAT

#: A4 页面尺寸（mm）。卷面渲染与 PDF 导出都以 A4 为准。
PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0

#: 校正后页面的分辨率。150dpi 够识别 14pt 字，又不至于让每页图片过大
#: （150dpi 的 A4 = 1240×1754；250dpi 会到 2067×2923，体积翻 2.7 倍）。
RECTIFIED_DPI = 150
RECTIFIED_WIDTH = round(PAGE_WIDTH_MM / 25.4 * RECTIFIED_DPI)
RECTIFIED_HEIGHT = round(PAGE_HEIGHT_MM / 25.4 * RECTIFIED_DPI)

#: 四角定位块的几何（与 `app.render.sheet` 的打印样式同源，改一处必须改两处；
#: 判据 `tests/services/test_page_detect.py` 用常量对比渲染端导出值）。
_MM_PER_INCH = 25.4
_CORNER_INSET_MM = 6.0
_CORNER_SIZE_MM = 8.0

#: 检测前把照片缩到这个宽度以内：手机原图动辄 4000px，缩到 1600 再找块，
#: 速度快一个量级，坐标乘回原始比例后对 150dpi 输出没有可见精度损失。
_DETECT_WIDTH = 1600


class PageRejected(Exception):
    """该页不能继续处理（上层转成 422/409）.

    `code` 是稳定标记，前端只认它、文案由前端渲染：
    - `no_qr`：照片里找不到可解的页脚二维码 → 不是本系统卷面或没拍全（422）
    - `wrong_sheet`：二维码可解但四角块不全 → 纸被遮/裁/严重失焦（422）
    - `checksum`：checksum 与调用方期望不符 → 拍错卷或卷子被改过（409）
    - `layout`：版面版本不符 → 印刷模板与当前程序不是同一版（409）
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RectifiedPage:
    """一页校正结果."""

    image: np.ndarray  # BGR、RECTIFIED_WIDTH×RECTIFIED_HEIGHT
    corners: tuple[tuple[float, float], ...]  # 原始照片上的四角块中心（tl,tr,br,bl）
    marking: dict[str, Any]  # 二维码解出的 payload
    rotated: bool  # 是否做过 180° 翻转


# ---------------------------------------------------------------------------
# 二维码


def _decode_qr(gray: np.ndarray) -> tuple[str | None, np.ndarray | None]:
    """在灰度图上解二维码，返回 `(文本, 四个角点)`；解不出返回 `(None, None)`.

    多尺度重试：手机照片里二维码只有 ~150–300px，直接解可能失败；把图缩到
    几个常见宽度各试一次（QRCodeDetector 会自己找定位点，不要求正放）。
    角点会被调用方用来排除「二维码自己也算一个候选黑块」的干扰。
    """
    detector = cv2.QRCodeDetector()
    height, width = gray.shape[:2]
    for variant in _qr_variants(gray):
        for target in (width, _DETECT_WIDTH, 1000, 700):
            if target <= 0 or target > width:
                continue
            ratio = target / width
            scaled = variant if ratio == 1.0 else cv2.resize(
                variant, (target, round(height * ratio))
            )
            text, points, _ = detector.detectAndDecode(scaled)
            if text and points is not None:
                # 解出的角点是缩放图的坐标，换回调用方的坐标（ratio == 1 时原样）
                return text, points.reshape(-1, 2) / ratio
    return None, None


def _qr_variants(gray: np.ndarray) -> list[np.ndarray]:
    """给二维码解码准备的几种预处理：原图、CLAHE、去背景光照.

    实测：手机照片常有半明半暗的梯度，直接解会失败；把「除以背景估计」这一步
    加上以后，0.45 强度的梯度样本也能解出（`tests/services/test_page_detect.py`
    里的 `gradient=0.45` 用例）。
    """
    variants = [gray]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    variants.append(clahe)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=max(gray.shape) / 20.0)
    flat = cv2.divide(gray, background, scale=255)
    variants.append(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(flat))
    return variants


def parse_marking(text: str) -> dict[str, Any]:
    """解析二维码 payload，字段不全或版本不认识时抛 `PageRejected('no_qr')`."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise PageRejected("no_qr", "页脚二维码内容不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise PageRejected("no_qr", "页脚二维码内容不是对象")
    version = payload.get("v")
    if version != QR_FORMAT:
        raise PageRejected("no_qr", f"二维码格式版本不认识：{version!r}")
    for key in ("paper", "checksum", "layout"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise PageRejected("no_qr", f"二维码缺少字段：{key}")
    return payload


# ---------------------------------------------------------------------------
# 四角定位块


def _corner_candidates(
    gray: np.ndarray, exclude: np.ndarray | None = None
) -> list[tuple[float, float, float]]:
    """找「实心小方块」候选：返回 (cx, cy, 边长) 列表.

    用 `minAreaRect` 的填充率与边长比判断实心方块，而不是轴对齐外接框：
    照片里纸通常有 ±10° 的歪斜，正方形旋转后轴对齐填充率会掉到 0.72，
    按 0.75 筛会把四角块整批丢掉（实测）。旋转矩形的填充率对旋转不敏感。
    """
    height, width = gray.shape[:2]
    _, binary = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_side = max(6.0, min(width, height) * 0.008)
    max_side = min(width, height) * 0.12
    found: list[tuple[float, float, float]] = []
    for contour in contours:
        (cx, cy), (bw, bh), angle = cv2.minAreaRect(contour)
        short, long = min(bw, bh), max(bw, bh)
        if not (min_side <= short and long <= max_side):
            continue
        if short / max(long, 1e-6) < 0.65:  # 正方形：长边比接近 1
            continue
        if cv2.contourArea(contour) / max(bw * bh, 1e-6) < 0.80:  # 实心
            continue
        if exclude is not None and cv2.pointPolygonTest(
            exclude.astype(np.float32), (float(cx), float(cy)), False
        ) >= 0:
            continue  # 二维码外框也被判成实心方块，按四点多边形排除
        found.append((float(cx), float(cy), float(short)))
    return found


def _pick_four_corners(
    candidates: Iterable[tuple[float, float, float]], width: int, height: int
) -> list[tuple[float, float]]:
    """从候选中挑出四角块中心，按 tl,tr,br,bl 排列.

    先按「离四个纸角最近」分组（照片可能有透视，但纸仍在画面主体区域），
    每组取面积排名靠前者里最靠近角的那一个；分组失败再用 x+y / y-x 排序兜底。
    """
    points = [(float(p[0]), float(p[1])) for p in candidates]
    if len(points) < 4:
        raise PageRejected("wrong_sheet", "照片里找不到四角定位块（纸没拍全或被遮挡）")
    targets = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    picked: list[tuple[float, float]] = []
    for tx, ty in targets:
        best = min(points, key=lambda p: (p[0] - tx) ** 2 + (p[1] - ty) ** 2)
        if best in picked:
            raise PageRejected("wrong_sheet", "四角定位块识别重复（纸可能没拍全）")
        picked.append(best)
    return picked


def _order_corners(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """按 tl,tr,br,bl 排序（任意四边形，不假设轴对齐）."""
    sums = [p[0] + p[1] for p in points]
    diffs = [p[1] - p[0] for p in points]
    top_left = points[sums.index(min(sums))]
    bottom_right = points[sums.index(max(sums))]
    top_right = points[diffs.index(min(diffs))]
    bottom_left = points[diffs.index(max(diffs))]
    ordered = [top_left, top_right, bottom_right, bottom_left]
    if len({tuple(round(v, 2) for v in p) for p in ordered}) != 4:
        raise PageRejected("wrong_sheet", "四角定位块共线或重复")
    return ordered


def _target_corners(scale: float) -> np.ndarray:
    """四角块中心在 A4 校正图上的目标坐标（px）.

    `scale` = 输出分辨率 px/mm；四角块中心 = 内缩 + 半边长。
    """
    inset = (_CORNER_INSET_MM + _CORNER_SIZE_MM / 2.0) * scale
    right = RECTIFIED_WIDTH - inset
    bottom = RECTIFIED_HEIGHT - inset
    return np.array(
        [[inset, inset], [right, inset], [right, bottom], [inset, bottom]], dtype=np.float32
    )


# ---------------------------------------------------------------------------


def load_image(data: bytes | np.ndarray) -> np.ndarray:
    """把上传的字节解成 BGR 图；解不开抛 `PageRejected('no_qr')`."""
    if isinstance(data, np.ndarray):
        return data
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise PageRejected("no_qr", "图片无法解码（格式不支持或文件损坏）")
    return image


def rectify(
    data: bytes | np.ndarray,
    *,
    expected_checksum: str | None = None,
    expected_layout: str = LAYOUT_VERSION,
) -> RectifiedPage:
    """把一张卷面照片校正成 A4 页面图（150dpi）.

    `expected_checksum` 给了就核对（上层从 `paper_id` 对应的卷面 checksum 取）；
    版面版本不符、checksum 不符分别抛 `PageRejected('layout')` / `('checksum')`。
    """
    image = load_image(data)
    height, width = image.shape[:2]
    if width < 200 or height < 200:
        raise PageRejected("no_qr", "图片太小，无法定位（短边至少 200px）")

    ratio = min(1.0, _DETECT_WIDTH / width)
    small = image if ratio == 1.0 else cv2.resize(
        image, (round(width * ratio), round(height * ratio)), interpolation=cv2.INTER_AREA
    )
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    text, qr_points = _decode_qr(gray)
    if text is None:
        raise PageRejected("no_qr", "找不到页脚二维码——不是本系统的卷面")
    marking = parse_marking(text)

    centers = _corner_candidates(gray, exclude=qr_points)
    ordered_small = _pick_four_corners(centers, small.shape[1], small.shape[0])
    source = np.array(ordered_small, dtype=np.float32) / ratio

    scale = RECTIFIED_DPI / _MM_PER_INCH
    matrix = cv2.getPerspectiveTransform(source, _target_corners(scale))
    rectified = cv2.warpPerspective(
        image, matrix, (RECTIFIED_WIDTH, RECTIFIED_HEIGHT), flags=cv2.INTER_LINEAR
    )

    rotated = False
    center = _qr_center(rectified)
    if center is not None and center[1] < RECTIFIED_HEIGHT / 2.0:
        rectified = cv2.rotate(rectified, cv2.ROTATE_180)
        rotated = True

    if expected_layout and marking["layout"] != expected_layout:
        raise PageRejected(
            "layout", f"版面版本不符：二维码 {marking['layout']}，程序 {expected_layout}"
        )
    if expected_checksum is not None and marking["checksum"] != expected_checksum:
        raise PageRejected("checksum", "卷面 checksum 与所选卷不符")

    return RectifiedPage(
        image=rectified,
        corners=tuple((float(x), float(y)) for x, y in ordered_small),
        marking=marking,
        rotated=rotated,
    )


def _qr_center(image: np.ndarray) -> tuple[float, float] | None:
    """找二维码中心（校正图坐标），用于 180° 朝向判断.

    倒置页面上 `QRCodeDetector.detect()` 常常失败、但把图翻 180° 就能找到，
    所以两个方向都试；翻过来的结果再把坐标映射回原图。
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.QRCodeDetector()
    ok, points = detector.detect(gray)
    if ok and points is not None:
        flat = points.reshape(-1, 2)
        return float(flat[:, 0].mean()), float(flat[:, 1].mean())
    flipped = cv2.rotate(gray, cv2.ROTATE_180)
    ok, points = detector.detect(flipped)
    if ok and points is not None:
        height, width = gray.shape[:2]
        flat = points.reshape(-1, 2)
        return float(width - 1 - flat[:, 0].mean()), float(height - 1 - flat[:, 1].mean())
    return None


def encode_jpeg(image: np.ndarray, quality: int = 88) -> bytes:
    """把校正页编码成 JPEG（存盘/回传都用它的体积优势）."""
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    return buffer.tobytes()


def decode_jpeg(data: bytes) -> np.ndarray:
    """读取自己写出的 JPEG（供判据与调试脚本复用）."""
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("不是合法 JPEG")
    return image
