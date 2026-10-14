"""业务服务层（T-M2-BE）：卷状态机、卷号、快照、排版与卷面上下文。

分层纪律（契约 §8）：`routers` → `services` → `models`；
本包**不得**被 `engine` / `grading` / `render` / `export` 反向依赖。
"""
