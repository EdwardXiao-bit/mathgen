"""跨模块冻结契约（Lead 维护，**只读**）。

本包存放**多个模块都必须遵守**的接口定义，作用与 `app/engine/types.py` 相同：
先冻结接口，再让写域不同的智能体并行实现。

| 文件 | 冻结的接口 | 生产方 | 消费方 |
| --- | --- | --- | --- |
| `layout.py` | `LayoutParams` + CSS 变量映射 | 前端传入 / API 存储 | 渲染、导出、前端预览 |
| `sheet.py` | `SheetContext` / `SheetItem` / 渲染与导出函数签名 | 卷 API 组装 | 渲染、导出 |
| `api.py` | 老师端 REST 出入参模型 + 路径常量 | 卷 API | 前端、验收 |

**纪律**：实现方**不得修改本包内任何文件**。需要改字段 → 先在
`docs/00-决策记录.md` 新增决策，再由 Lead 更新本包与
`backend/tests/contract/test_layering.py` 中的基准哈希。
"""
