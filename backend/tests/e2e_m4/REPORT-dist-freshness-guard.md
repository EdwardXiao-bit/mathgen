# task-43 报告：e2e_m4 的 dist 新鲜度门禁曾是「恒不抛」，现已变红并可自证

写域：`backend/tests/e2e_m4/**`（未动 `backend/app/**`、`frontend/**`、`docs/**`）。
日期：2026-10-16。结论：**通过**。

## §0 一屏结论

| 项 | 结论 |
| --- | --- |
| 缺陷 | `backend/tests/e2e_m4/_harness.py:133` 旧签名 `assert_dist_fresh(case)` 体内写 `case.assertTrue(fresh, 消息)`，而 6 个调用点传的是**类** → `TestCase.assertTrue` 被当普通函数调用，`self=fresh`、`expr=非空消息`，`if not expr:` 恒假 → **真不新鲜也永远不抛**（一条恒绿的守卫） |
| 修复 | `_harness.py:133-149` 改为**无参** `assert_dist_fresh()`，内部 `if not fresh: raise AssertionError(...)` 并点名两个 mtime；6 个调用点同步为 `assert_dist_fresh()` |
| 两态原始输出 | 修复前 `artifacts/task43-guard-probe-prefix.log`：`old_pattern_on_stale_world raised=False`、`new_call_on_stale_world raised=True type=TypeError`（当时还没有无参签名）；修复后 `artifacts/task43-guard-probe-postfix.log`：`old_pattern_on_stale_world raised=False`（旧写法仍静默）、`new_call_on_stale_world raised=True type=AssertionError`、`new_call_on_fresh_world raised=False` |
| 留仓库的负对照 | 新建 `backend/tests/e2e_m4/test_dist_freshness_guard.py`（6 条）：`Ran 6 tests in 0.072s` / `OK`（`artifacts/task43-guard-tests.log`），并在**同一个陈旧世界**里做判别对照（旧写法静默 vs 新守卫抛） |
| 回归 | e2e_m4 全套修复后 `Ran 68 tests in 71.041s` / `OK` / exit 0（`artifacts/task43-e2e-m4-all.log`；62 原有 + 6 新增）→ **没有新红**，即当前 dist 新鲜、六条守卫确实在守且通过 |
| 全仓反模式扫描 | 176 个 `*.py`：形状 A（在类上直接调断言方法）1 处；形状 B（`case`/`tc` 助手）12 个；形状 C（助手调用点）83 处，实参 `{实例(self):73, 形参:9, 类(本文件定义):1}`，**命中缺陷形状 1 处**，**追不出结论 0 处**；两处命中都是本报告为取证而**故意留在仓库**的复刻（见 §3/§4） |
| 报告订正 | `backend/tests/e2e_m4/REPORT-paper-archive.md:78` 的冻结值 `359efa53…` → `31202c98…`（M5 起重冻结后的真值），只改这一处已确认过期的陈述 |

## §1 缺陷机制（直证，不是推测）

- 旧代码（`_harness.py`，修复前）：`def assert_dist_fresh(case) -> dict:` … `case.assertTrue(fresh, "前端产物早于源码 → 本套件判据无效。…（src=… dist=…）。")`。
- 6 个调用点（修复前）全部传**类**：`test_mobile_layout.py:190,321`、`test_paper_archive_ui.py:88,174,211`、`test_storage_disabled_path.py:41`，形如 `cls.freshness = assert_dist_fresh(cls)`。
- 机制直证（可重跑，见 §3）：`unittest.TestCase.assertTrue(False, "非空消息")` → **返回 `None`、不抛**。原因：`assertTrue` 是实例方法，这里以 `case=类` 调用时没有实例绑定，`fresh`(False) 落进 `self` 位、消息落进 `expr` 位，方法体 `if not expr: raise` 判断的是消息字符串而不是条件。
- 后果（诚实登记）：这 6 条调用本来要守的门 —— 「`frontend/dist` 比源码旧时，本套件判据无效」—— **在 M4 全部门禁里从来没生效过**。当前 dist 是新鲜的，所以从没有假红；若当时 dist 陈旧，本套件会照常绿着给结论。

## §2 修复与两态原始输出

修复后 `_harness.py:133-149`：

```python
def assert_dist_fresh() -> dict:
    """产物不新则**明确变红**（不重建：重建是 `frontend/**` 写域）。"""
    fresh, src_m, dist_m = dist_freshness()
    if not fresh:
        raise AssertionError(
            "前端产物早于源码 → 本套件判据无效。请在 `frontend/**` 写域里"
            f"先跑 `pnpm --dir frontend build`（src={src_m:.0f} dist={dist_m:.0f}）。"
        )
    return {"dist_student_html_mtime": dist_m, "src_newest_mtime": src_m}
```

6 个调用点全部改为无参 `assert_dist_fresh()`（`test_mobile_layout.py:190` 仍保留 `cls.freshness = …` 的取值用法）。

探针 `artifacts/task43_guard_probe.py` 用 `mock.patch.object(harness, "dist_freshness", lambda: (False, 2000.0, 1000.0))` 造陈旧世界，同一脚本修复前后各跑一次（**两态都是脚本真跑出来的输出，不是回忆**）：

| 观测 | 修复前（prefix 日志） | 修复后（postfix 日志） |
| --- | --- | --- |
| 真世界 | `real_world=[True, 1792082763.96, 1792086687.66]` | 同（未动） |
| 机制直证 `TestCase.assertTrue(False, 消息)` | `raised=False returned="None"` | 同 |
| 旧写法喂陈旧世界 | `raised=False`（**静默**） | `raised=False`（旧写法仍然静默，说明判别对照成立） |
| 新守卫喂陈旧世界 | `raised=True type=TypeError message="assert_dist_fresh() missing 1 required positional argument: 'case'"` | `raised=True type=AssertionError message="前端产物早于源码 → 本套件判据无效。请在 `frontend/**` 写域里先跑 `pnpm --dir frontend build`（src=2000 dist=1000）。"` |
| 新守卫喂新鲜世界 | `raised=True type=TypeError` | `raised=False returned={'dist_student_html_mtime': 2000.0, 'src_newest_mtime': 1000.0}` |

## §3 留在仓库的负对照（V9.1 留仓库 + V9.2 自证真的制造了失败条件）

`backend/tests/e2e_m4/test_dist_freshness_guard.py`（6 条，`Ran 6 tests in 0.072s` / `OK`）：

- `TestTheOldCallPatternWasVacuous`
  - `test_the_stale_world_really_is_stale_under_the_patch` —— **V9.2**：先证明夹具真的制造了失败条件（补丁下 `dist_freshness()` 返回 `(False, 2000.0, 1000.0)`）。
  - `test_the_historical_pattern_stays_green_while_the_guard_now_raises` —— **判别对照**：同一陈旧世界里，先直证 `assertIsNone(unittest.TestCase.assertTrue(False, "非空消息"))`，再断言旧写法返回 `{'dist_student_html_mtime': 1000.0, 'src_newest_mtime': 2000.0}` 且不抛、新守卫 `assertRaises(AssertionError)` 且消息含 `2000`/`1000`/`pnpm --dir frontend build`。
  - `test_the_guard_signature_forbids_a_case_argument` —— `inspect.signature(...).parameters == []`，且 `assert_dist_fresh(HistoricalCallSite)` 当场 `TypeError`（回归在签名层面被堵死）。
- `TestTheGuardRaisesInBothDirections`
  - 新鲜世界不抛并返回两个 mtime；
  - 把 `harness.STUDENT_HTML` 指向不存在的 `DIST/__task43_missing_student.html` 后，走**真** `dist_freshness()` 判旧、守卫抛；
  - 真世界当前 fresh（与 §5 的 68 绿互相印证）。

证据 JSON：`artifacts/task43-guard-evidence.json`（`real_world{true, src 1792082763.96, dist 1792086687.66}` / `signature{parameters [], extra_arg_raises "TypeError"}` / `discriminating_pair{old_pattern_raised false, old_pattern_returned {1000.0, 2000.0}, new_guard_raised true, new_guard_message "…（src=2000 dist=1000）。"}` / `stale_world{false, 2000.0, 1000.0}`）。

## §4 全仓同类反模式扫描（有就修、没有就要能说清扫了什么）

脚本 `artifacts/task43_scan_assert_calls.py`（AST，三类形状 + 「形参向上追」的小数据流，只读），日志 `artifacts/task43-antipattern-scan.log`，机读结果 `artifacts/task43-antipattern-scan.json`。

- **范围**：仓库根 `C:\Users\Ed\dsh_workspace\mathgen` 下**全部 176 个 `*.py`**（排除 `.git/.pylibs/.tmp-pw/.venv/__pycache__/artifacts/build/dist/node_modules/site-packages`），其中 101 个文件定义了测试类。
- **形状 A —— 在类上直接调断言方法**：**1 处**，`backend/tests/e2e_m4/test_dist_freshness_guard.py:83 unittest.TestCase.assertTrue(False, "非空消息")`，即 §3 的机制直证本身（故意留的）。
- **形状 B —— 接收 `case`/`tc` 形参并对其断言的助手**：**12 个** —— `backend/tests/api/_student_support.py:75,85,103,110`、`backend/tests/api/test_archive.py:43`、`backend/tests/api/test_attempt_list.py:42`、`backend/tests/api/test_error_code_sources.py:130`、`backend/tests/api/test_errors_and_sheet.py:65`、`backend/tests/api/test_schema.py:104`、`backend/tests/e2e/harness.py:864,878`，外加本文件故意复刻的 `test_dist_freshness_guard.py:39`。
- **形状 C —— 这些助手的调用点**：**83 处**，实参种类 `{'实例(self)': 73, '形参(需向上追)': 9, '类(本文件定义)': 1}`；**命中缺陷形状（传类）1 处** = `test_dist_freshness_guard.py:85 old_style_assert_dist_fresh(HistoricalCallSite)`（故意留的复刻，负对照需要它）；**追不出结论 0 处**。
- 那 9 处「需向上追」全部追到 `实例(self)`，逐条人工复核过：`_student_support.py:90,99,100,105,107,123,126` 的 `tc` 来自 `test_student_whitelist.py:96,97,100,105,112,155,157,170,172,189,194,195,196`、`test_student_api.py:92,93,148,149`、`test_attempts.py:134,136`、`test_archive.py:48`（经 `_assert_student_flow_works(self, …)`，调用点 `test_archive.py:414,433,438`）—— 全部传 `self`；`test_m1_coverage_matrix.py:29 call_generate(case)` 的 `case` 来自 `_papers(self, env, …)` 的调用点 `test_m1_coverage_matrix.py:36,50,71,85,139` —— 全部传 `self`。
- **结论**：全仓除本次故意留的复刻之外，**没有第二处「把 `TestCase` 断言方法当普通函数调用/实参错位」的实例**。修完 e2e_m4 后也没有需要顺手修的其它文件（不需要改动 `backend/tests/api/**` 与 `backend/tests/e2e/**`）。
- 扫描器自身的三次收紧（记下来是因为「0 处未判定」必须是**扫出来的**，不是放弃判定得来的）：第一版只在本文件内追调用点 → 8 处未判定；改为跨文件追 + 同文件优先消歧 + 排除自递归 → 6 处 → 3 处 → **0 处**。已知局限仍然写明：**同名函数在多个模块里出现且无法用同文件消歧时，报「未知(需人工看)」而不是猜**（本次由人工逐条复核兜住）。

## §5 回归

- e2e_m4 全套（修复后）：`Ran 68 tests in 71.041s` / `OK` / exit 0（`artifacts/task43-e2e-m4-all.log`）。68 = 62 原有 + 6 新增。
- **没有出现新红**：说明当前 `frontend/dist` 确实比源码新，六条守卫现在真的在守并通过；不存在「以前被死门禁盖住的失败」。
- 本次修复未触碰任何产品代码（`backend/app/**` 零改动），因此不存在行为回退面。

## §6 报告订正（缺陷 2）

`backend/tests/e2e_m4/REPORT-paper-archive.md:78`：冻结值 `359efa5323376c66e2abb04768f6a3f74b49fa8ef3510436aaaee16526c7c435` → `31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded`。测试从来没有坏：两处机器守卫（`backend/tests/contract/test_layering.py:31`、`backend/tests/e2e_m4/test_paper_archive_acceptance.py:30`）在 M5 重冻结时都已同步，是**报告正文誊写的那一份**陈了。报告里同时写明：只改这一处已确认过期的陈述、其余结论与证据一字未动；以及「**报告会持续变陈，而没有任何东西会因此变红**」—— 测试锁得住代码里的冻结值，锁不住文档里手抄的那一份。

## §7 边界与诚实登记

- 扫描是**静态**的（AST），不执行被测代码；动态注入（`setattr`/`getattr` 后调用断言）不在覆盖内，本次 176 个文件里没有这种写法。
- 探针与负对照都用 `mock.patch` 造陈旧世界，**不改磁盘 mtime**；真实陈旧世界下的行为由 §3 的第三条用「指向不存在的产物文件」走真 `dist_freshness()` 覆盖。
- 修好的守卫只在**本套件**（`backend/tests/e2e_m4/**`）生效；其它套件若也要守 dist 新鲜度，需要各自引用这个无参版本（本次未扩大写域）。

## §8 复核命令

```powershell
$env:PYTHONUTF8=1
# 两态探针（含机制直证与判别对照）
& .\backend\.venv\Scripts\python.exe backend\tests\e2e_m4\artifacts\task43_guard_probe.py
# 留仓库的负对照 6 条
& .\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests\e2e_m4 -t backend -v -p "test_dist_freshness_guard.py"
# 全套
& .\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests\e2e_m4 -t backend -v
# 反模式扫描（只读）
& .\backend\.venv\Scripts\python.exe backend\tests\e2e_m4\artifacts\task43_scan_assert_calls.py
```
