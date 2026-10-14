"""出题引擎包。

对外入口（契约 §2.3 / §2.2，冻结签名）：::

    from app.engine import generate, regenerate_one
    generate(config, seed) -> list[Question]
    regenerate_one(config, seed, seq, existing) -> Question

**纪律**：本包内不得 import 数据库、FastAPI、requests 或任何 I/O 模块
（docs/04-接口契约.md §8，冻结项）。
"""
from app.engine.generate import generate, regenerate_one

__all__ = ["generate", "regenerate_one"]
