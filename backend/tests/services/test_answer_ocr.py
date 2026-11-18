"""模式 C 答案识别判据（M18-04）：归一、框匹配、两遍一致与整批落库。

为什么在 Services 层用注入引擎：真实 RapidOCR 要 onnxruntime（约 180MB），
基础安装与 CI 都不该背这个体积；识别逻辑本身（归一、匹配、一致判定）与
引擎无关，用假引擎就能把每条分支钉住（docs/10 Q8 / J-10-08）。
真实引擎的链路只在 `MATHGEN_OCR_E2E=1` 的 e2e 里跑。
"""
from __future__ import annotations

import unittest

from app.services import answer_ocr

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - 没装 extra 的环境跳过像素相关用例
    cv2 = None
    np = None

__all__ = [
    "NormalizeAnswerTest",
    "MatchAnswersTest",
    "RecognizePageTest",
    "IlluminationTest",
]


class NormalizeAnswerTest(unittest.TestCase):
    """识别原文 → 判分口径可读的答案串（docs/10 §9.5）。"""

    def test_fullwidth_digits_and_operators(self) -> None:
        self.assertEqual(answer_ocr.normalize_answer("８＋９"), "8+9")
        self.assertEqual(answer_ocr.normalize_answer("１２×３"), "12*3")
        self.assertEqual(answer_ocr.normalize_answer("２４÷６"), "24/6")

    def test_spaces_and_trailing_equals_are_dropped(self) -> None:
        self.assertEqual(answer_ocr.normalize_answer(" 9 9 = "), "99")
        self.assertEqual(answer_ocr.normalize_answer("=42"), "42")

    def test_noise_characters_are_removed(self) -> None:
        self.assertEqual(answer_ocr.normalize_answer("99."), "99.")
        self.assertEqual(answer_ocr.normalize_answer("?1O"), "1")
        self.assertEqual(answer_ocr.normalize_answer("一"), "")

    def test_leading_zeros(self) -> None:
        self.assertEqual(answer_ocr.normalize_answer("08"), "8")
        self.assertEqual(answer_ocr.normalize_answer("0"), "0")
        self.assertEqual(answer_ocr.normalize_answer("00"), "0")

    def test_empty_and_none(self) -> None:
        self.assertEqual(answer_ocr.normalize_answer(None), "")
        self.assertEqual(answer_ocr.normalize_answer(""), "")
        self.assertEqual(answer_ocr.normalize_answer("  "), "")


class MatchAnswersTest(unittest.TestCase):
    """det+rec 结果按「中心点落在答案框内」分配到题号。"""

    BOXES = [
        {"seq": 1, "x": 0.10, "y": 0.20, "w": 0.10, "h": 0.03},
        {"seq": 2, "x": 0.40, "y": 0.20, "w": 0.10, "h": 0.03},
    ]

    @staticmethod
    def _detection(cx: float, cy: float, text: str, score: float = 0.9):
        return [[[cx - 0.01, cy - 0.005], [cx + 0.01, cy - 0.005],
                 [cx + 0.01, cy + 0.005], [cx - 0.01, cy + 0.005]], text, score]

    def test_center_inside_box_maps_to_seq(self) -> None:
        detections = [self._detection(0.15, 0.215, "99"), self._detection(0.45, 0.215, "1")]
        matched = answer_ocr.match_answers(detections, self.BOXES)
        self.assertEqual(matched[1][0], "99")
        self.assertEqual(matched[2][0], "1")

    def test_text_outside_boxes_is_dropped(self) -> None:
        detections = [self._detection(0.80, 0.60, "姓名")]
        self.assertEqual(answer_ocr.match_answers(detections, self.BOXES), {})

    def test_highest_confidence_wins_inside_one_box(self) -> None:
        detections = [
            self._detection(0.14, 0.215, "66", score=0.4),
            self._detection(0.16, 0.215, "99", score=0.9),
        ]
        self.assertEqual(answer_ocr.match_answers(detections, self.BOXES)[1], ("99", 0.9))

    def test_scale_is_undone(self) -> None:
        # 2× 图片里的坐标要除回 1× 再判框（recognize_page 放大后识别）
        detections = [self._detection(0.30, 0.43, "99")]
        self.assertEqual(
            answer_ocr.match_answers(detections, self.BOXES, scale=2.0)[1][0], "99"
        )
        # 忘了除 scale 时同一坐标落在框外 —— 负对照：判据确实依赖 scale
        self.assertEqual(answer_ocr.match_answers(detections, self.BOXES, scale=1.0), {})


@unittest.skipIf(cv2 is None, "需要 [ocr] extra（numpy / opencv）")
class RecognizePageTest(unittest.TestCase):
    """两遍识别：P1 原图放大、P3 光照归一，返回题号 → 原文 + 置信度。"""

    class _Engine:
        def __init__(self, texts: dict[int, str]) -> None:
            self.texts = texts
            self.shapes: list[tuple[int, int]] = []

        def __call__(self, image, **kwargs):
            self.shapes.append(image.shape[:2])
            height, width = image.shape[:2]
            detections = []
            for seq, text in self.texts.items():
                cx = 0.15 * width
                cy = 0.215 * height
                detections.append(
                    [[[cx - 8, cy - 4], [cx + 8, cy - 4],
                      [cx + 8, cy + 4], [cx - 8, cy + 4]], text, 0.93]
                )
            return detections, 0.01

    BOXES = [{"seq": 1, "x": 0.10, "y": 0.20, "w": 0.10, "h": 0.03}]

    def test_p1_doubles_the_page_before_detection(self) -> None:
        engine = self._Engine({1: "99"})
        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        matched = answer_ocr.recognize_page(image, self.BOXES, engine=engine, pass_name=answer_ocr.PASS_P1)
        self.assertEqual(engine.shapes[0], (200, 200))
        self.assertEqual(matched[1][0], "99")

    def test_p3_uses_illumination_normalization(self) -> None:
        calls: list[str] = []
        original = answer_ocr.illumination_normalized

        def spy(image):
            calls.append("p3")
            return original(image)

        answer_ocr.illumination_normalized = spy
        try:
            image = np.full((100, 100, 3), 255, dtype=np.uint8)
            engine = self._Engine({1: "7"})
            answer_ocr.recognize_page(image, self.BOXES, engine=engine, pass_name=answer_ocr.PASS_P3)
        finally:
            answer_ocr.illumination_normalized = original
        self.assertEqual(calls, ["p3"])

    def test_p1_does_not_touch_illumination(self) -> None:
        calls: list[str] = []
        original = answer_ocr.illumination_normalized

        def spy(image):
            calls.append("p3")
            return original(image)

        answer_ocr.illumination_normalized = spy
        try:
            image = np.full((100, 100, 3), 255, dtype=np.uint8)
            answer_ocr.recognize_page(
                image, self.BOXES, engine=self._Engine({1: "7"}), pass_name=answer_ocr.PASS_P1
            )
        finally:
            answer_ocr.illumination_normalized = original
        self.assertEqual(calls, [])


@unittest.skipIf(cv2 is None, "需要 [ocr] extra（numpy / opencv）")
class IlluminationTest(unittest.TestCase):
    """P3 预处理要真的压掉光照梯度，否则两遍识别会一起错。"""

    def test_gradient_flatness_is_reduced(self) -> None:
        height, width = 120, 160
        ramp = np.tile(np.linspace(60, 240, width, dtype=np.uint8), (height, 1))
        image = cv2.cvtColor(ramp, cv2.COLOR_GRAY2BGR)
        before = float(np.std(ramp))
        flat = answer_ocr.illumination_normalized(image)
        after = float(np.std(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)))
        self.assertLess(after, before)

    def test_shape_is_preserved(self) -> None:
        image = np.full((50, 70, 3), 200, dtype=np.uint8)
        self.assertEqual(answer_ocr.illumination_normalized(image).shape, image.shape)


class SingleCharRepairTest(unittest.TestCase):
    """手写单字符被认成字母时的修读（2026-11-18 演示卷实测：`9`→`q`、`8`→`&`）."""

    def test_common_letter_confusions_map_to_digits(self) -> None:
        cases = {"q": "9", "&": "8", "o": "0", "l": "1", "s": "5", "z": "2", "b": "6"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(answer_ocr.normalize_answer(raw), expected)

    def test_multi_char_text_is_not_rewritten(self) -> None:
        # 多字符只看数字部分，不能把字母硬换成数字（那是题干残留，不是答案）
        self.assertEqual(answer_ocr.normalize_answer("6.31 - 28 ="), "6.31-28")
        self.assertEqual(answer_ocr.normalize_answer("l3"), "3")


class RecognizeCropTest(unittest.TestCase):
    """整页漏掉的单字符答案，靠裁图补认一遍（三票里至少两票一致才采纳）."""

    class _Engine:
        def __init__(self, detections) -> None:
            self.detections = detections
            self.calls = 0

        def __call__(self, image, **kwargs):
            self.calls += 1
            return self.detections, 0.01

    def test_picks_the_highest_confidence_detection(self) -> None:
        quad = [[0, 0], [10, 0], [10, 10], [0, 10]]
        engine = self._Engine(
            [
                [quad, "3", 0.55],
                [quad, "", 0.99],
                [quad, "8", 0.91],
            ]
        )
        crop = np.full((40, 60, 3), 255, dtype=np.uint8)
        text, score = answer_ocr.recognize_crop(engine, crop)
        self.assertEqual(text, "8")
        self.assertAlmostEqual(score, 0.91)
        self.assertEqual(engine.calls, 1)

    def test_returns_none_when_nothing_is_recognized(self) -> None:
        engine = self._Engine([])
        crop = np.full((40, 60, 3), 255, dtype=np.uint8)
        self.assertEqual(answer_ocr.recognize_crop(engine, crop), (None, None))

    def test_tiny_or_missing_crop_is_skipped(self) -> None:
        engine = self._Engine([[[[0, 0], [1, 0], [1, 1], [0, 1]], "3", 0.9]])
        self.assertEqual(answer_ocr.recognize_crop(engine, None), (None, None))
        self.assertEqual(
            answer_ocr.recognize_crop(engine, np.zeros((2, 2, 3), dtype=np.uint8)),
            (None, None),
        )
        self.assertEqual(engine.calls, 0)
