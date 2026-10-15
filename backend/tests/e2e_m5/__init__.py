"""M5 里程碑验收套件（写域：verify-m1 / `backend/tests/e2e_m5/**`，task-42 / T-M5-04）。

覆盖三个功能需求：

- **FR-T11** 成绩列表：分页、卷/关键字筛选、每行得分只有一个来源（D80）。
- **FR-T12** 逐题明细：列表 ↔ 明细 ↔ 磁盘库三方对账（`total` / `correct` / `score` / `items`）。
- **FR-T13** 导出：CSV（UTF-8 with BOM）与 xlsx，必须导**当前筛选下的全部**、不受分页限制。

本包**不 import** `backend/tests/api/**`（be-paper 的域）与 `backend/tests/security/**`
（task-25 已封存）。判据设施全部写在 `_m5_support.py` 里，判据本身一律是纯函数
`*_problems(...) -> list[str]`，便于拿构造出来的「坏世界」反向验证判据真的会红。
"""
