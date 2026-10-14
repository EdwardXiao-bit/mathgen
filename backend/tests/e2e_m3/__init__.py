"""M3 里程碑验收套件包（verify 写域，task-13）。

与 `tests/e2e`（M1）、`tests/e2e_m2`（M2）同性质：**非写者**写的跨模块验收，
只经公开入口（HTTP / 产品导出 / 库存快照直读）断言。

- `test_m3_acceptance.py` —— 三条核心判据：卷号复现、Word 二次编辑、单命令启动
- `artifacts/` —— docx / PDF / 日志等产物（`.gitignore` 已忽略）
"""
