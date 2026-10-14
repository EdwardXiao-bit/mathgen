"""纯函数纪律（契约 §8，**冻结**）：engine 包内不得出现任何 I/O 或上层依赖。

用源码扫描断言，而不是靠人自觉（docs/06-测试方案.md §6.3）。

检查分两层，避免「文档字符串里提到 FastAPI 就误报」：
  1. **AST 层**：真正 import 了哪些模块（这才是硬约束）；
  2. **文本层**：真正的 I/O 调用标记（`open(` / `Path(` 等）与包管理相关字样。
注意：本测试自身读文件是允许的——被约束的是 `app/engine/**`。
"""
from __future__ import annotations

import ast
import os
import unittest

import app.engine as engine_package

ENGINE_DIR = os.path.dirname(os.path.abspath(engine_package.__file__))

# 禁止 import 的模块（顶层名）：框架、ORM、网络、文件系统
FORBIDDEN_MODULES = {
    "fastapi", "sqlalchemy", "requests", "httpx", "aiohttp",
    "os", "io", "pathlib", "subprocess", "socket", "urllib",
    "http", "tempfile", "shutil", "sqlite3", "json", "pickle",
}

# 禁止出现的 I/O 调用标记（文本层，不会命中中文文档字符串）
FORBIDDEN_TEXT = ["open(", "Path(", "io.open", "os.path", "sys.path"]

FORBIDDEN_UPWARD = [
    "app.models",
    "app.routers",
    "app.schemas",
    "app.grading",
    "app.layout",
    "app.render",
    "app.export",
]

ALLOWED_MODULES = {"__future__", "dataclasses", "enum", "typing"}


def _engine_sources():
    for name in sorted(os.listdir(ENGINE_DIR)):
        if name.endswith(".py"):
            path = os.path.join(ENGINE_DIR, name)
            with open(path, encoding="utf-8") as handle:
                yield path, handle.read()


class TestEnginePurity(unittest.TestCase):
    def test_engine_sources_exist(self):
        sources = list(_engine_sources())
        self.assertGreaterEqual(len(sources), 8)
        names = {os.path.basename(path) for path, _ in sources}
        for expected in ("types.py", "config.py", "ranges.py", "builders.py",
                         "constraints.py", "dedupe.py", "assemble.py", "generate.py"):
            self.assertIn(expected, names)

    def test_no_io_calls_in_text(self):
        """文本层：不得出现真正的 I/O 调用标记。"""
        for path, source in _engine_sources():
            for token in FORBIDDEN_TEXT:
                self.assertNotIn(token, source,
                                 "%s 出现了禁止的 I/O 标记 %r" % (path, token))

    def test_no_upward_dependencies(self):
        for path, source in _engine_sources():
            for token in FORBIDDEN_UPWARD:
                self.assertNotIn(token, source,
                                 "%s 依赖了上层模块 %s" % (path, token))

    def test_imported_modules_are_whitelisted(self):
        """AST 层：engine 只能 import 白名单标准库、本包相对模块，或 `app.engine.*`。"""
        for path, source in _engine_sources():
            tree = ast.parse(source, filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        self.assertIn(top, ALLOWED_MODULES,
                                      "%s 导入了非白名单模块 %s" % (path, alias.name))
                        self.assertNotIn(top, FORBIDDEN_MODULES,
                                         "%s 导入了禁止模块 %s" % (path, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.level:      # 包内相对导入（from .types import ...）
                        continue
                    module = node.module or ""
                    top = module.split(".")[0]
                    if top == "app":
                        # 自包绝对导入只允许指向 app.engine.*
                        # （`app/engine/__init__.py` 的公开入口 re-export，见 D48）
                        self.assertTrue(
                            module == "app.engine" or module.startswith("app.engine."),
                            "%s 从 %s 导入（engine 只能依赖 app.engine.*）" % (path, module))
                        continue
                    self.assertIn(top, ALLOWED_MODULES,
                                  "%s 从非白名单模块 %s 导入" % (path, node.module))
                    self.assertNotIn(top, FORBIDDEN_MODULES,
                                     "%s 从禁止模块 %s 导入" % (path, node.module))

    def test_no_dynamic_import_or_eval(self):
        """不允许用 `__import__` / `eval` / `exec` 绕过静态检查。"""
        for path, source in _engine_sources():
            for token in ("__import__", " eval(", "exec(", "compile("):
                self.assertNotIn(token, source, "%s 使用了 %r" % (path, token))

    def test_frozen_contract_module_is_imported_not_copied(self):
        """引擎必须复用冻结契约的枚举，而不是自己另立一套同名字符串。"""
        for path, source in _engine_sources():
            if os.path.basename(path) in ("types.py",):
                continue
            self.assertNotIn("class Op(", source, "%s 重新定义了 Op" % path)
            self.assertNotIn("class RangeId(", source, "%s 重新定义了 RangeId" % path)
            self.assertNotIn("class QuestionType(", source, "%s 重新定义了 QuestionType" % path)


if __name__ == "__main__":
    unittest.main()
