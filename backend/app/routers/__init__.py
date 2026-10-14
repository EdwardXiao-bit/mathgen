"""路由层（T-M2-BE）。

分层纪律（契约 §8）：`routers` → `services` → `models`。
路径常量一律引用 `app.contracts.api.ROUTES`，禁止在本包内写死路径字符串。
"""
