"""task-4 独立复核：engine / grading 的纯函数纪律（契约 §8，**冻结**）。

契约要求：`app/engine/**` 与 `app/grading/**` 不得依赖数据库、Web 框架或任何 I/O。

**刻意不复用** `backend/tests/contract/test_layering.py` 的扫描器（那是正则扫描）：
本文件用 `ast` 解析源码、按语法节点判定，两条独立路径互证 —— 只有两条都通过，
才能排除「某一方的扫描器恰好漏掉」的可能。
"""
import ast
import os
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(BACKEND, "app")
PURE_PACKAGES = ("engine", "grading")

#: 顶层模块黑名单：Web 框架、ORM、网络、文件系统、进程、数据库
FORBIDDEN_TOP_MODULES = {
    "fastapi", "starlette", "uvicorn", "pydantic",
    "sqlalchemy", "alembic", "sqlite3",
    "requests", "httpx", "aiohttp", "urllib", "urllib3", "socket",
    "pathlib", "shutil", "tempfile", "subprocess", "pickle", "csv", "json",
}
#: 本仓库内不得被纯函数包引用的上层模块
FORBIDDEN_LOCAL_PREFIXES = ("app.models", "app.routers", "app.render",
                            "app.export", "app.layout")
#: 禁止直接调用的内建 / 构造器（I/O 入口）
FORBIDDEN_CALLS = {"open", "eval", "exec", "compile", "__import__", "Path", "input"}


def _iter_sources(package):
    root = os.path.join(APP, package)
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _scan_file(path):
    """按 AST 节点判定违规，返回可读描述列表。"""
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)
    rel = os.path.relpath(path, BACKEND)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_TOP_MODULES:
                    violations.append(f"{rel}:{node.lineno} import {alias.name}")
                if alias.name.startswith(FORBIDDEN_LOCAL_PREFIXES):
                    violations.append(f"{rel}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top in FORBIDDEN_TOP_MODULES:
                violations.append(f"{rel}:{node.lineno} from {module} import ...")
            if module.startswith(FORBIDDEN_LOCAL_PREFIXES):
                violations.append(f"{rel}:{node.lineno} from {module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in FORBIDDEN_CALLS:
                violations.append(f"{rel}:{node.lineno} 调用 {name}(...)")
    return violations


class TestPureFunctionDiscipline(unittest.TestCase):
    def test_scan_is_not_vacuous(self):
        """先证明扫描确实扫到了实现代码（防止"目录为空所以通过"）。"""
        for package in PURE_PACKAGES:
            files = [p for p in _iter_sources(package)
                     if os.path.basename(p) != "__init__.py"]
            with self.subTest(package=package):
                self.assertTrue(files, f"app/{package} 下没有实现文件，扫描是空转的")
                total_lines = 0
                for path in files:
                    with open(path, encoding="utf-8") as handle:
                        total_lines += len(handle.readlines())
                self.assertGreater(total_lines, 100,
                                   f"app/{package} 仅 {total_lines} 行，扫描覆盖不足")

    def test_no_framework_database_or_io_dependency(self):
        violations = []
        for package in PURE_PACKAGES:
            for path in _iter_sources(package):
                violations.extend(_scan_file(path))
        self.assertEqual(violations, [],
                         "engine / grading 必须是纯函数叶子包（契约 §8，冻结）：\n  "
                         + "\n  ".join(violations))

    def test_public_entries_are_plain_functions(self):
        """顺带确认扫的是真代码：三个公开入口都是普通函数、签名与契约一致。"""
        import importlib
        import inspect

        from app.engine import generate, regenerate_one     # noqa: F401
        from app.grading import grade                       # noqa: F401

        expected = {
            "generate": ["config", "seed"],
            "regenerate_one": ["config", "seed", "seq", "existing"],
            "grade": ["question", "raw_answer"],
        }
        for name, params in expected.items():
            with self.subTest(name=name):
                module = "app.engine" if name != "grade" else "app.grading"
                obj = getattr(importlib.import_module(module), name)
                self.assertTrue(inspect.isfunction(obj), f"{module}.{name} 应为函数")
                self.assertEqual(list(inspect.signature(obj).parameters), params,
                                 f"{module}.{name} 形参应与契约一致")


if __name__ == "__main__":
    unittest.main()
