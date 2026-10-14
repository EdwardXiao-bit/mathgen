"""判分包。

对外入口（契约 §3，冻结签名）：

    from app.grading import grade          # grade(question, raw_answer) -> Verdict

**纪律**：本包内不得 import 数据库、FastAPI、requests 或任何 I/O 模块
（docs/04-接口契约.md §8，冻结项）。
"""
from app.grading.grader import grade, normalize_answer

__all__ = ["grade", "normalize_answer"]
