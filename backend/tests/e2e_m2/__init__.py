"""M2 里程碑验收套件包（verify 写域，task-8）。

与 `backend/tests/e2e`（M1 验收）同性质：**非写者**写的跨模块验收，
只经公开入口（HTTP / `render_sheet` / `export_pdf`）断言。

- `test_m2_acceptance.py`：unittest 用例（可被 `discover` 收集）
- `m2_e2e.py`：对**已启动服务**的真实 HTTP 端到端运行器（含请求/响应证据）
- `make_and_inspect_pdf.py`：导出 PDF 并用两条独立路径实测几何（页边距 / A4 / 分页）
- `check_layout_toggles.py`：六个排版开关的可判定复核
- `check_frontend_d56.py`：D56（预览来自后端 `/sheet`）与 NFR-9（无外部 CDN）
- `artifacts/`：PDF、PNG、几何报告与运行记录
"""
