"""冻结面守卫测试（对应 docs/06-测试方案.md §6.3，docs/04-接口契约.md §8）。

两类守卫：
  1. **契约文件防篡改**：types.py 是冻结契约，实现方不得修改。用 sha256 锁住，
     一旦被改动立刻失败——这是「接口冻结」从口号变成机制的关键一步。
     Lead 合法更新契约时必须同步更新本文件的 EXPECTED_SHA256。
  2. **依赖方向**：engine / grading 是纯函数叶子包，不得依赖 Web 框架、数据库或 I/O。

本文件由 Lead 维护。
"""
import hashlib
import os
import re
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(BACKEND, "app")

# 冻结契约文件与其基准哈希（Lead 更新契约时同步修改）
EXPECTED_SHA256 = {
    os.path.join(APP, "engine", "types.py"):
        "94d1195662229af9b8e2e5a4b69498d94f5af285ac3f061c8f67c50c0163677d",
    # 跨模块契约（M2）：卷 API / 渲染导出 / 前端界面共同依赖，任何人不得擅改
    os.path.join(APP, "contracts", "__init__.py"):
        "b04c3444dae8c3e61720c281ab9b09b5e3c626c3656ce072c65dd6d298885807",
    os.path.join(APP, "contracts", "layout.py"):
        "284eeafc8029e5d10f3522692bab9f4d758024043885e4b01aea43cf561f4dde",
    os.path.join(APP, "contracts", "sheet.py"):
        "dd6e3eadba118792101307e88fd3ead9ab2e0322b75af0e9520eba75131e0f00",
    os.path.join(APP, "contracts", "api.py"):
        "a1ffb2b516180cc0d5a2f2d4c6d899b670de7a32d92619d17459a766aca852c4",
}

# 纯函数包中禁止出现的依赖
FORBIDDEN_MODULES = {
    "fastapi", "starlette", "sqlalchemy", "alembic",
    "requests", "httpx", "aiohttp", "urllib", "urllib3",
    "pathlib", "shutil", "tempfile", "socket", "sqlite3", "subprocess",
}
FORBIDDEN_LOCAL_MODULES = ("app.models", "app.routers", "app.render", "app.export")

# 纯函数包
PURE_PACKAGES = ["engine", "grading"]

IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.M)


def _iter_sources(package):
    root = os.path.join(APP, package)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class TestFrozenContractFiles(unittest.TestCase):
    def test_types_py_is_unchanged(self):
        for path, expected in EXPECTED_SHA256.items():
            self.assertTrue(os.path.exists(path), f"冻结契约文件缺失：{path}")
            with open(path, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(
                actual, expected,
                f"{os.path.basename(path)} 是冻结契约，已被修改。\n"
                "如果你确实需要改字段：先在 docs/00-决策记录.md 新增决策，"
                "由 Lead 更新契约与 backend/tests/contract/test_layering.py 中的基准哈希。",
            )


class TestDependencyDirection(unittest.TestCase):
    """engine / grading 不得依赖 Web 框架、数据库或 I/O（契约 §8，冻结）。"""

    def _scan(self, package):
        violations = []
        for path in _iter_sources(package):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for match in IMPORT_RE.finditer(text):
                module = match.group(1) or match.group(2) or ""
                top = module.split(".")[0]
                if top in FORBIDDEN_MODULES:
                    violations.append(f"{os.path.relpath(path, BACKEND)}: import {module}")
                if module.startswith(FORBIDDEN_LOCAL_MODULES):
                    violations.append(f"{os.path.relpath(path, BACKEND)}: import {module}")
            for lineno, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r"(?<![\w.])open\s*\(", code):
                    violations.append(f"{os.path.relpath(path, BACKEND)}:{lineno}: 调用了 open()")
        return violations

    def test_engine_has_no_io_or_framework_dependency(self):
        self.assertEqual(self._scan("engine"), [],
                         "app/engine 必须是纯函数包，不得依赖 I/O 或 Web 框架")

    def test_grading_has_no_io_or_framework_dependency(self):
        self.assertEqual(self._scan("grading"), [],
                         "app/grading 必须是纯函数包，不得依赖 I/O 或 Web 框架")

    def test_scanner_actually_has_files_to_scan(self):
        """防止「目录为空所以通过」的假绿。

        注意取舍：守卫只应在**真的被违反**时变红，「实现还没写」不算违反。
        因此这里用 skip 而不是 fail，但会在输出里明确暴露「守卫当前是空转的」。
        里程碑验收（task-4）必须确认本用例**没有**被跳过。
        """
        for package in PURE_PACKAGES:
            with self.subTest(package=package):
                files = [p for p in _iter_sources(package)
                         if os.path.basename(p) != "__init__.py"]
                if not files:
                    self.skipTest(f"app/{package} 尚无实现文件，依赖方向守卫当前为空转")


if __name__ == "__main__":
    unittest.main()
