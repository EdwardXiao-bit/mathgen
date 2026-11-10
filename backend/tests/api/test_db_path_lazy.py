"""库文件路径必须**惰性解析**（T-M8-01）。

事故背景（2026-10-16 全量跑顺带查出，2026-11-10 转正前修）：

`app/db.py` 原来在**导入时**就把 `MATHGEN_DB` 读成一个常量 `DB_PATH`。而测试套件里
`tests/api/_support.py` 在模块导入阶段就 import 了 `app.db`，`unittest discover`
又是先导入 `tests/api/*`、后导入 `tests/e2e_m2/*` —— 于是 e2e 用例里的
`os.environ.setdefault("MATHGEN_DB", …)` 还没执行，路径已经冻死了：那批 e2e 把
「验收卷」写进了老师的产品库（实测 1950 行）。

判据要钉住的是**行为**，不是实现：先 `import app.db`，再设环境变量，取到的引擎与
`db_path()` 都必须跟着新变量走。顺带钉住两件事：
- 没有 `MATHGEN_DB` 时回落到 `backend/data/mathgen.db`；
- 兼容别名 `DB_PATH` 保持「导入那一刻的快照」语义，不偷偷变成活值。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app import db


class DbPathLazyTestCase(unittest.TestCase):
    """每个用例都把环境变量与单例还原，避免污染同进程里的其他用例。"""

    def setUp(self) -> None:
        self._saved_env = os.environ.get("MATHGEN_DB")
        self._saved_engine = db._ENGINE
        self._saved_factory = db._FACTORY
        self._tmpdir = tempfile.TemporaryDirectory(prefix="mathgen-dbpath-")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved_env is None:
            os.environ.pop("MATHGEN_DB", None)
        else:
            os.environ["MATHGEN_DB"] = self._saved_env
        engine = db._ENGINE
        if engine is not None and engine is not self._saved_engine:
            engine.dispose()
        db._ENGINE = self._saved_engine
        db._FACTORY = self._saved_factory
        self._tmpdir.cleanup()

    def _target(self, name: str = "x.db") -> Path:
        return Path(self._tmpdir.name) / name

    def test_db_path_follows_env_set_after_import(self) -> None:
        """导入之后设 `MATHGEN_DB`，`db_path()` 立即跟着变。"""
        target = self._target("late.db")
        os.environ["MATHGEN_DB"] = str(target)
        self.assertEqual(db.db_path(), target)

    def test_default_engine_uses_env_set_after_import(self) -> None:
        """引擎也走惰性解析：导入后设变量，单例建在**新**路径上。"""
        target = self._target("engine.db")
        os.environ["MATHGEN_DB"] = str(target)
        db._ENGINE = None
        db._FACTORY = None

        engine = db.default_engine()
        self.assertEqual(Path(engine.url.database or ""), target)

    def test_falls_back_to_backend_data_dir(self) -> None:
        """没有 `MATHGEN_DB` 时回落到 `backend/data/mathgen.db`。"""
        os.environ.pop("MATHGEN_DB", None)
        self.assertEqual(db.db_path(), db.BACKEND_DIR / "data" / "mathgen.db")

    def test_engine_is_cached_but_path_is_not(self) -> None:
        """负对照：路径是活的，引擎是缓存单例——两者语义不同，别混为一谈。"""
        first = self._target("first.db")
        second = self._target("second.db")
        os.environ["MATHGEN_DB"] = str(first)
        db._ENGINE = None
        db._FACTORY = None
        engine = db.default_engine()

        os.environ["MATHGEN_DB"] = str(second)
        self.assertEqual(db.db_path(), second)
        self.assertEqual(Path(engine.url.database or ""), first)


if __name__ == "__main__":  # pragma: no cover - 手动单跑
    unittest.main()
