"""M1 里程碑**跨模块**验收套件（verify 写域，docs/03-协作规范.md §2）。

与 `backend/tests/engine`、`backend/tests/grading` 的分工：

| 目录 | 谁写 | 性质 |
| --- | --- | --- |
| `tests/engine`、`tests/grading` | 实现方 | 守自己的实现细节 |
| **`tests/e2e`** | **verify** | **独立验证**：只经 `generate` / `regenerate_one` / `grade` 三个公开入口断言，不复用实现内部函数 |

覆盖：`docs/06-测试方案.md` §4.1（约束完备）、§4.2（确定性）、§4.3（单题重生成）、
§4.4（异常路径）、§11（覆盖矩阵抽查）。

门禁命令（仓库根目录执行）：

    $PY -m unittest discover -s backend/tests/e2e -t backend -v

**失败类型约定**（任务卡硬要求）：实现尚未就位时，失败必须是**断言失败**
（AssertionError / ConfigError / InsufficientQuestions），不能是 ImportError 或语法错误。
做法：测试模块**不在导入期** import `app.engine` / `app.grading`，公开入口统一由
`harness.load_env()` 惰性解析，解析失败一律转成 AssertionError。
"""
