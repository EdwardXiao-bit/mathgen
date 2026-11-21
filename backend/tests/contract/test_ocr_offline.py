"""离线承诺的静态判据（J-10-07，M18-07）。

模式 C 的识别在本机做（D89），上传/校对不碰外网。这条判据不跑网络，而是**读源码**：

- 模式 C 的 Python 侧（定位、识别、存储、任务、路由、卷面标记与几何）不得出现网络客户端；
- 不得出现外部 URL 字面量（`http://` / `https://`）——注释与文档字符串里的说明不算，
  因为它们不会被浏览器或请求库用到；
- 老师端的新页面（上传 / 校对）不得引用外部资源（`<img src="http…">` 之类）。

## 诚实边界

静态扫描只覆盖我们自己的代码：`rapidocr_onnxruntime` / `onnxruntime` 内部如果偷偷联网，
这条判据看不见 —— 那条用「真跑一遍 + 断网」的 e2e 判据兜（`tests/e2e_m18`，`NO_PROXY` 环境）。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

__all__ = ["NETWORK_MODULES", "network_uses", "external_urls"]

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"

#: 模式 C 的 Python 侧：这几处不该出现任何网络客户端。
OCR_MODULES = (
    "app/services/page_detect.py",
    "app/services/answer_ocr.py",
    "app/services/ocr_store.py",
    "app/services/ocr_jobs.py",
    "app/services/ocr_service.py",
    "app/routers/ocr.py",
    "app/render/marking.py",
    "app/render/geometry.py",
)

#: 老师端模式 C 页面：不该引用外部资源。
OCR_FRONTEND = (
    "frontend/src/teacher/pages/OcrUploadPage.tsx",
    "frontend/src/teacher/pages/OcrReviewPage.tsx",
)

NETWORK_MODULES = frozenset(
    {"requests", "urllib", "urllib3", "httpx", "aiohttp", "socket", "http", "websockets"}
)


def _strip_docstrings(source: str) -> str:
    """抹掉文档字符串（它们说明「不联网」，不该被判成联网）。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body[0].value.value = ""
    return ast.unparse(tree)


def network_uses(source: str) -> list[str]:
    """返回源码里用到的网络模块名（已去重、已排序）。"""
    tree = ast.parse(source)
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in NETWORK_MODULES:
                    used.add(root)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root = node.module.split(".")[0]
            if root in NETWORK_MODULES:
                used.add(root)
    return sorted(used)


def external_urls(source: str) -> list[str]:
    """返回文档字符串之外的外部 URL 字面量。"""
    stripped = _strip_docstrings(source)
    found: set[str] = set()
    tree = ast.parse(stripped)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value.startswith(("http://", "https://")):
                found.add(value[:80])
    return sorted(found)


class OcrOfflineTest(unittest.TestCase):
    """J-10-07（静态侧）：模式 C 的代码里没有网络客户端、没有外部 URL。"""

    def test_01_backend_ocr_modules_do_not_import_network_clients(self) -> None:
        offenders = {}
        for relative in OCR_MODULES:
            source = (BACKEND / relative).read_text(encoding="utf-8")
            used = network_uses(source)
            if used:
                offenders[relative] = used
        self.assertEqual(offenders, {}, f"模式 C 的代码里出现了网络客户端：{offenders}")

    def test_02_backend_ocr_modules_have_no_external_urls(self) -> None:
        offenders = {}
        for relative in OCR_MODULES:
            source = (BACKEND / relative).read_text(encoding="utf-8")
            urls = external_urls(source)
            if urls:
                offenders[relative] = urls
        self.assertEqual(offenders, {}, f"模式 C 的代码里出现了外部 URL：{offenders}")

    def test_03_teacher_ocr_pages_reference_no_external_resources(self) -> None:
        offenders = {}
        for relative in OCR_FRONTEND:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            hits = [
                line.strip()
                for line in source.splitlines()
                if ("http://" in line or "https://" in line) and not line.strip().startswith(("*", "//"))
            ]
            if hits:
                offenders[relative] = hits
        self.assertEqual(offenders, {}, f"模式 C 的页面引用了外部资源：{offenders}")

    def test_04_negative_controls_redden_on_planted_offences(self) -> None:
        """负对照：种一个 `import requests` 与一个 URL，两个检测函数必须点名。"""
        planted = (
            '"""docstring 里的 https://example.com 不算。"""\n'
            "import json\n"
            "import requests\n"
            "URL = 'https://ocr.example.com/api'\n"
        )
        self.assertEqual(network_uses(planted), ["requests"])
        self.assertEqual(external_urls(planted), ["https://ocr.example.com/api"])
        clean = '"""不联网。"""\nimport json\nURL = "#not-a-url"\n'
        self.assertEqual(network_uses(clean), [])
        self.assertEqual(external_urls(clean), [])
