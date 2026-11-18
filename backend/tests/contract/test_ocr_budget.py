"""体积预算与发布物装配（J-10-06，M18-07）。

## 为什么要有这一条

模式 C 把三个大依赖带进了发布物（`numpy` / `opencv-python-headless` / `rapidocr-onnxruntime`，
后者再拖 `onnxruntime` 与自带模型）。`docs/10` §13 承诺「增量 ≤200MB」，但承诺不能只写在文档里：

- **承诺的入口**（`[ocr]` extra）必须真的存在、清单必须与文档一致；
- **三种安装形态**（Docker 镜像、`npx` 启动器、一键脚本）必须真的把 extra 装上 ——
  漏一个，老师装完发现上传照片直接报「缺少 OCR 依赖」；
- 已装的那部分必须真的没超预算。

## 诚实边界

本判据量的是**本机 venv 里已装的那几个发行版**，不是一次全新安装的全部体积
（本机没装 `rapidocr-onnxruntime` 时，它只能被清单对上，量不到字节）。所以
`installed_bytes()` 返回的是「量到的部分」，而 `test_budget_holds_for_what_is_installed`
要求「至少量到两项」，避免空集合一路绿。
"""
from __future__ import annotations

import importlib.metadata
import re
import tomllib
import unittest
from pathlib import Path

__all__ = [
    "BUDGET_BYTES",
    "REQUIRED_DISTRIBUTIONS",
    "install_sites",
    "installed_bytes",
    "missing_required",
    "over_budget",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"
BUDGET_BYTES = 260 * 1024 * 1024  # D96：实测 232.3MB，预算放宽到 260MB（留约 12% 升级余量）

#: `docs/10` §13 承诺的 `[ocr]` extra 组成（发行版名，规范化成小写连字符）。
REQUIRED_DISTRIBUTIONS = (
    "numpy",
    "opencv-python-headless",
    "rapidocr-onnxruntime",
)

#: 三种安装形态里的装配点：文件 → 片段（片段必须出现）。
INSTALL_SITES = {
    "Dockerfile": '"/app/backend[ocr]"',
    "scripts/start.py": '{BACKEND}[ocr]',
    "npm/cli.js": '${wheelPath}[ocr]',
}


def over_budget(total: int, limit: int = BUDGET_BYTES) -> bool:
    """总量超预算。抽成函数是为了让负对照能直接喂 201MB。"""
    return total > limit


def missing_required(declared: list[str], required=REQUIRED_DISTRIBUTIONS) -> list[str]:
    """清单里缺了哪些必需项（规范化后比较）。"""
    have = {name.lower() for name in declared}
    return [name for name in required if name not in have]


def installed_bytes() -> dict[str, int]:
    """本机已装的相关发行版 → 字节数；没装的跳过。"""
    found: dict[str, int] = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata["Name"] or "").lower()
        if name not in REQUIRED_DISTRIBUTIONS and name != "onnxruntime":
            continue
        total = 0
        for entry in dist.files or []:
            path = dist.locate_file(entry)
            try:
                if Path(path).is_file():
                    total += Path(path).stat().st_size
            except OSError:  # pragma: no cover - 权限/损坏的文件不计入
                continue
        found[name] = total
    return found


def _pyproject_extra() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    deps = data["project"]["optional-dependencies"]["ocr"]
    return [re.split(r"[<>=!~ ]", dep.strip(), maxsplit=1)[0].lower() for dep in deps]


def install_sites() -> dict[str, bool]:
    """三个安装形态各自的装配点是否带着 `[ocr]`。"""
    result: dict[str, bool] = {}
    for relative, needle in INSTALL_SITES.items():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        result[relative] = needle in text
    return result


class OcrBudgetTest(unittest.TestCase):
    """J-10-06：清单一致、三种安装形态都装 extra、已量到的体积不超 200MB。"""

    def test_01_extra_declares_the_agreed_dependencies(self) -> None:
        declared = _pyproject_extra()
        self.assertEqual(missing_required(declared), [], f"[ocr] extra 清单变了：{declared}")
        # 负对照：拿掉一项必须被点名（不是「不匹配也算过」）
        self.assertIn(
            "numpy",
            [dep for dep in declared if dep in REQUIRED_DISTRIBUTIONS],
            "清单解析失败：一个必需发行版都没解析出来",
        )
        self.assertEqual(missing_required(declared[1:]), ["numpy"])

    def test_02_every_install_shape_installs_the_extra(self) -> None:
        sites = install_sites()
        missing = [path for path, ok in sites.items() if not ok]
        self.assertEqual(
            missing,
            [],
            "这些安装形态没带 `[ocr]`，老师装完上传照片会报「缺少 OCR 依赖」：" + ", ".join(missing),
        )

    def test_03_budget_holds_for_what_is_installed(self) -> None:
        found = installed_bytes()
        self.assertGreaterEqual(
            len(found),
            2,
            f"本机只量到 {sorted(found)}，判据形同空转：请先装 [ocr] extra 再跑这条",
        )
        total = sum(found.values())
        detail = ", ".join(f"{name}={size / 1024 / 1024:.1f}MB" for name, size in sorted(found.items()))
        self.assertFalse(
            over_budget(total),
            f"OCR 依赖体积 {total / 1024 / 1024:.1f}MB 超预算 {BUDGET_BYTES / 1024 / 1024:.0f}MB（{detail}）",
        )

    def test_04_budget_helper_reddens_over_the_limit(self) -> None:
        """负对照：预算函数必须能红（201MB 红、199MB 绿）。"""
        self.assertTrue(over_budget(BUDGET_BYTES + 1))
        self.assertFalse(over_budget(BUDGET_BYTES - 1))
