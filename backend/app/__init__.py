"""mathgen 后端应用包。

M1 阶段只有两个纯逻辑子包：
  - app.engine   出题引擎（纯函数，禁止 I/O）
  - app.grading  判分（纯函数，禁止 I/O）

依赖方向见 docs/04-接口契约.md §8。

本模块**刻意只放常量、不做任何 import**：`app/__main__.py` 必须在 `import app.main`
之前设好 `MATHGEN_DB` / `MATHGEN_STATIC_DIR`（`app/db.py` 在 import 期读一次环境变量），
而启动 `python -m app` 时本包会先于一切被导入，这里一旦牵进 I/O 就破坏了那个前提。
"""

#: 与 `backend/pyproject.toml` 的 `[project] version` 必须一致（发行时由 tag 校验）。
__version__ = "1.0.0rc2"
