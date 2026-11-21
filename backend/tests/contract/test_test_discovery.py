"""测试目录必须可被 `unittest discover` 发现（防止整目录被静默跳过）。

`python -m unittest discover -s tests -t .` 只在**可 import 的包**里收集用例：
某个子目录缺 `__init__.py` 时，它里面的 `test_*.py` 一条都不会跑，而且退出码仍是 0。

2026-11-13 实测：`backend/tests/services/` 缺 `__init__.py`，`test_page_detect`（11 条）、
`test_answer_ocr`（14 条）、`test_ocr_run_batch`（6 条）连续三轮「全量 1371 条 OK」里
都没有执行过——全量数字没变就是线索。判据 `test_every_test_directory_is_an_importable_package`
钉住「目录里只要有 test_*.py，就必须有 __init__.py」。

负对照留在同一个文件里：构造一个只有 `test_*.py`、没有 `__init__.py` 的目录，
检测函数必须把它点出来（否则判据本身可能恒绿）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]


def test_files_by_dir(root: Path) -> dict[Path, list[Path]]:
    """按目录归组 `test_*.py`（跳过 `__pycache__`）。"""
    grouped: dict[Path, list[Path]] = {}
    for path in sorted(root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        grouped.setdefault(path.parent, []).append(path)
    return grouped


def dirs_missing_init(root: Path) -> list[str]:
    """返回「有测试文件但缺 __init__.py」的目录（相对 root，排序）。"""
    missing: list[str] = []
    for directory in test_files_by_dir(root):
        if not (directory / "__init__.py").is_file():
            missing.append(directory.relative_to(root).as_posix() or ".")
    return sorted(missing)


class TestDiscoveryTest(unittest.TestCase):
    """测试树本身的结构判据。"""

    def test_every_test_directory_is_an_importable_package(self):
        missing = dirs_missing_init(TESTS_ROOT)
        self.assertEqual(
            missing,
            [],
            "这些测试目录缺 __init__.py，unittest discover 会静默跳过整目录："
            + ", ".join(missing),
        )

    def test_the_detector_catches_a_missing_init(self):
        # 负对照 1：只有 test_*.py、没有 __init__.py → 必须被点名
        # 负对照 2：补上 __init__.py → 不再被点名
        base = TESTS_ROOT.parent / ".tmp-test-discovery"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            root = Path(tmp)
            good = root / "good"
            good.mkdir()
            (good / "__init__.py").write_text("", encoding="utf-8")
            (good / "test_ok.py").write_text("", encoding="utf-8")
            bad = root / "bad"
            bad.mkdir()
            (bad / "test_skipped.py").write_text("", encoding="utf-8")

            self.assertEqual(dirs_missing_init(root), ["bad"])

            (bad / "__init__.py").write_text("", encoding="utf-8")
            self.assertEqual(dirs_missing_init(root), [])


if __name__ == "__main__":
    unittest.main()
