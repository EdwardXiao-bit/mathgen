"""模式 C 的答案识别（M18-04）：两遍识别、答案框匹配与归一。

分层口径（docs/10 §9）：
- 本模块**只算不写**：不碰数据库、不碰磁盘，输入是校正后的整页图像与答案框，
  输出是「题号 → 识别文本 + 置信度」。写库与裁图落盘在 `ocr_service.run_batch()`。
- OCR 引擎只在真正调用时惰性导入（`MATHGEN_OCR_E2E` 之外的单元测试注入假引擎），
  所以基础安装 `import app.main` 不会 import onnxruntime（Q18 / J-10-08）。
- 两遍（P1 全页、P3 光照归一）分别识别，归一后**逐字相等**才算一致；
  不一致或两遍都空都不许自动判分，留给老师（D91）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

__all__ = [
    "PASS_P1",
    "PASS_P3",
    "DETECTION_PARAMS",
    "PASS_SCALE",
    "normalize_answer",
    "illumination_normalized",
    "resize_for_ocr",
    "crop_boxes",
    "match_answers",
    "recognize_page",
    "load_engine",
]

PASS_P1 = "p1"
PASS_P3 = "p3"

#: P1 与 P3 共用的 det+rec 阈值，spike 实测（docs/09 §8.1）
DETECTION_PARAMS = {"box_thresh": 0.2, "text_score": 0.2, "unclip_ratio": 2.2}

#: 整页放大倍数：spike 实测 2× 明显优于原尺寸
PASS_SCALE = 2.0

#: 识别原文里允许出现的字符（归一之后）；其余一律丢弃
_ALLOWED = set("0123456789+-*/().=")
_FULLWIDTH = str.maketrans(
    {
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "＋": "+", "－": "-", "−": "-", "×": "*", "＊": "*",
        "÷": "/", "／": "/", "＝": "=", "（": "(", "）": ")",
        "　": " ",
    }
)


def normalize_answer(raw: str | None) -> str:
    """把识别原文归一成判分口径可读的答案串。

    与 `grading.normalize_answer` 分开（docs/10 §9.5）：这里处理的是**识别**的
    噪声（全角、乘除号、空白、前后多余的等号），判分口径仍归 `grading`。
    """
    if not raw:
        return ""
    text = str(raw).translate(_FULLWIDTH).replace(" ", "")
    text = "".join(ch for ch in text if ch in _ALLOWED)
    text = text.rstrip("=").lstrip("=")
    if not text:
        return ""
    if text == "0":
        return "0"
    # 去无意义前导 0：08 → 8、00 → 0；但 0.5 这种小数不动（一期卷面是非负整数）
    if re.fullmatch(r"0+", text):
        return "0"
    if re.fullmatch(r"0+\d+", text):
        return text.lstrip("0") or "0"
    return text


def load_engine() -> Any:  # pragma: no cover - 真实引擎在 CI 之外才装
    """惰性构造 RapidOCR（只在真正识别时导入 onnxruntime）。"""
    from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

    return RapidOCR()


def resize_for_ocr(image: Any, scale: float = PASS_SCALE) -> Any:
    """按整数倍最近邻放大（spike 实测放大后再 det 更稳，插值无关紧要）。"""
    import cv2  # noqa: PLC0415

    if scale == 1.0:
        return image
    height, width = image.shape[:2]
    return cv2.resize(
        image,
        (int(round(width * scale)), int(round(height * scale))),
        interpolation=cv2.INTER_CUBIC,
    )


def illumination_normalized(image: Any) -> Any:
    """P3 预处理：背景估计除法压掉反光/阴影梯度，再 CLAHE 提局部对比。"""
    import cv2  # noqa: PLC0415

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    sigma = max(gray.shape) / 20.0
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    flattened = cv2.divide(gray, background, scale=255)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    corrected = clahe.apply(flattened)
    return cv2.cvtColor(corrected, cv2.COLOR_GRAY2BGR)


def crop_boxes(image: Any, boxes_px: Sequence[dict], *, pad: float = 0.004) -> dict[int, Any]:
    """按答案框裁图（题目与答案同一页），给校对界面用。"""
    import cv2  # noqa: PLC0415

    height, width = image.shape[:2]
    crops: dict[int, Any] = {}
    for box in boxes_px:
        seq = int(box["seq"])
        x0 = max(0, int(round((box["x"] - pad) * width)))
        y0 = max(0, int(round((box["y"] - pad) * height)))
        x1 = min(width, int(round((box["x"] + box["w"] + pad) * width)))
        y1 = min(height, int(round((box["y"] + box["h"] + pad) * height)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        crops[seq] = image[y0:y1, x0:x1].copy()
    return crops


def _box_center(quad: Iterable[Sequence[float]]) -> tuple[float, float]:
    points = list(quad)
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def match_answers(
    detections: Sequence[Sequence[Any]],
    boxes_px: Sequence[dict],
    *,
    scale: float = 1.0,
    page_size: tuple[float, float] = (1.0, 1.0),
) -> dict[int, tuple[str, float]]:
    """把整页 det+rec 的文本按「中心点落在答案框内」分配给题号。

    - 同一框命中多条时取置信度最高的那条；
    - 框外文本（题干、姓名栏、页脚二维码校验码）直接丢掉；
    - 识别不到就是没这个 key（调用方按「两遍都空 → 需人工」处理）。
    """
    assigned: dict[int, tuple[str, float]] = {}
    for item in detections:
        if len(item) < 3:
            continue
        quad, text, score = item[0], item[1], float(item[2])
        if not text:
            continue
        cx, cy = _box_center(quad)
        cx /= scale
        cy /= scale
        cx /= max(page_size[0], 1e-9)
        cy /= max(page_size[1], 1e-9)
        best_seq = None
        best_area = None
        for box in boxes_px:
            left = box["x"]
            top = box["y"]
            right = left + box["w"]
            bottom = top + box["h"]
            if left <= cx <= right and top <= cy <= bottom:
                area = box["w"] * box["h"]
                if best_area is None or area < best_area:
                    best_area = area
                    best_seq = int(box["seq"])
        if best_seq is None:
            continue
        previous = assigned.get(best_seq)
        if previous is None or score > previous[1]:
            assigned[best_seq] = (str(text), score)
    return assigned


def recognize_page(
    image: Any,
    boxes_px: Sequence[dict],
    *,
    engine: Any = None,
    pass_name: str = PASS_P1,
) -> dict[int, tuple[str, float]]:
    """跑一遍（P1 原图放大 / P3 光照归一放大）并返回「题号 → 原文 + 置信度」。"""
    if engine is None:
        engine = load_engine()
    prepared = resize_for_ocr(
        image if pass_name == PASS_P1 else illumination_normalized(image)
    )
    result = engine(prepared, **DETECTION_PARAMS)
    detections = result[0] if isinstance(result, tuple) else result
    height, width = prepared.shape[:2]
    return match_answers(
        detections or [],
        boxes_px,
        scale=PASS_SCALE,
        page_size=(width / PASS_SCALE, height / PASS_SCALE),
    )
