"""T-M4-06 Tier 0 验收套件（写域：verify-m1 / `backend/tests/e2e_m4/**`）。

三档结构（Lead 批准，见任务书）：

- **Tier 0（本包）**：不需要手机的机器判据 —— 二维码 Host 敏感性、存储被禁用的降级
  路径、移动版式与点击目标。
- **Tier 1**：真机存在时由**服务器**产生证据（访问日志里的非回环客户端 IP +
  `MicroMessenger` UA、指向本次验收专用卷的 attempt 行）。
- **Tier 2**：只有真机能证的部分，**不进自动化**，明标 `UNVERIFIED`。

本包**不 import** `backend/tests/api/**`（be-paper 的域）与 `backend/tests/security/**`
（task-25 已封存）；唯一复用的是 verify 侧自己的浏览器引导 `tests/e2e_m2/_browser.py`，
它 re-export 产品的 `app.export.browser.browser_session`（task-22 建立的唯一通道）。
"""
