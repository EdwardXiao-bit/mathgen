# task-25 / T-M4-02 安全契约测试 —— 验收报告（verify-m1）

**结论：判据六条全部通过；Lead 加的三条（A/B/C）全部落实。**
本报告只写**可复核的原始输出**；没有原始输出的结论不写。

* 对象：git HEAD `7df91da8210865bc75485409f081fe6396c073a1` + 工作区未提交状态（task-24 的成果全在工作区）
* 写域：`backend/tests/security/**`（**只新建**）。`backend/app/**`、`backend/tests/api/**`、`frontend/**`、`docs/**` 一行未改。
* 运行环境：`$VPY = C:\Users\Ed\dsh_workspace\mathgen\backend\.venv\Scripts\python.exe`

---

## 1 改动文件清单（全部为新建）

| 文件 | 作用 |
| --- | --- |
| `backend/tests/security/__init__.py` | 包说明（写域归属 / 权威判据出处） |
| `backend/tests/security/_whitelist.py` | 独立守卫：递归白名单 + 答案性键黑名单 + 反向断言 + 负对照助手 |
| `backend/tests/security/_harness.py` | 自建 engine / session / `TestClient` / 造数据（**不 import `backend/tests/api/**`**） |
| `backend/tests/security/test_student_whitelist.py` | 主判据：取题与练习两条路径的白名单递归 + 老师端 `answer` 反向断言 |
| `backend/tests/security/test_negative_controls.py` | V9 负对照：10 条合成 + 5 条**真实产品路径** |
| `backend/tests/security/test_attempt_boundary.py` | **判据边界**：判分结果必须含 `answer`（白名单到此为止）+ D76 重放行为 |
| `backend/tests/security/artifacts/agg_hash.py` | V8 夹逼工具（可重跑） |
| `backend/tests/security/artifacts/probe_old_db_migration.py` | DB 层只读探针（复核 be-paper 那条 ORM 元数据断言） |

`_harness.py` 的两处增量（第 131 行起）：`report_student()`、`submit_attempt()`（`submit_attempt` 返回 `(status_code, json)` 而**不断言**状态码——D76 的重放是 200、首次是 201，**状态码本身就是判据**）。

```powershell
git status --porcelain | Select-String security
# ?? backend/tests/security/
```

## 2 验证命令与原始输出

```powershell
# 判据 ⑥：安全套件全绿
& $VPY -m unittest discover -s backend/tests/security -t backend -v
```
```
Ran 34 tests in 0.857s
OK
EXIT=0
```
（日志 `artifacts/security-run2.log`；**第三跑（独立进程、各自全新 DB 目录）** `Ran 34 tests in 0.792s / OK / EXIT=0`，日志 `artifacts/security-run3.log`；
首跑 29 条时的日志 `artifacts/security-run1.log` = `Ran 29 tests in 0.398s / OK`。
唯一噪声：`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`——第三方库自身警告。）

```powershell
# （A）复核：被改动的两个既有用例文件仍然绿（走 :memory: 独立内存库）
& $VPY -m unittest -v tests.api.test_errors_and_sheet tests.api.test_schema
```
```
Ran 33 tests in 0.635s
OK
EXIT=0
```
（日志 `artifacts/api-touchpoint-run.log`）

```powershell
# DB 层只读探针
& $VPY backend/tests/security/artifacts/probe_old_db_migration.py
```
```
production_before: columns=[id, student_id, paper_id, config_json, total, correct, duration_ms, submitted_at]
                   has_attempt_uuid_column=false  unique_sets=[]  attempt_rows=0
copy_after_migration: columns=[...,attempt_uuid]  unique_sets=[{name:uq_attempt_student_uuid,
                      columns:[student_id,attempt_uuid], origin:c}]
copy_after_second_run: 与 copy_after_migration 逐字段相同      migration_idempotent=true
fresh_after_create_all: unique_sets=[{name:sqlite_autoindex_attempt_1,
                        columns:[student_id,attempt_uuid], origin:u}]
EXIT=0
```
（完整 JSON：`artifacts/evidence-schema-migration.json`）

**生产库没被碰**（每一轮都复验）：
```
backend/data/mathgen.db  LastWriteTime = 2026/10/15 18:48:52
```
该时间戳**早于**我全部运行窗口；探针对生产库只用 `sqlite3` URI `mode=ro` + `Connection.backup()` 复制到本目录。
测试侧另有旁证：真实路径用例能在**我自己的** engine 上按 `code` 取到刚发布的卷，说明 `dependency_overrides[get_db]` 生效（否则读到的是生产库）。

## 3 判据逐条对照

| # | 判据 | 结果 | 证据 |
| --- | --- | --- | --- |
| ① | D74 白名单**逐层递归**（取题 + 练习） | 通过 | `d74_recursive_walk`：`item_paths=$.items[0..5]` / `$.items[0..4]`；`student_paper_response` 6 题键集全 `[display,question_type,seq]`，`top_keys=[items,paper_id,title,total]`；`student_practice_response` 5 题同，`top_keys=[items,seed,total]` |
| ② | 老师端必须**含** `answer` | 通过 | `teacher_reverse_assertion`：`answers={"1":"48","2":"33","3":"99","4":"42","5":"65","6":"18"}`，`result=pass`；另有区分力用例（学生端响应喂进去必失败、老师端删掉 `answer` 必失败） |
| ③ | 自起客户端/自造数据，不依赖 `tests/api/**` | 通过 | `suite_independence`：`scanned=[__init__.py,_harness.py,_whitelist.py,test_negative_controls.py,test_student_whitelist.py]`、`offenders=[]`（AST/正则双向扫 `tests.api`）；`_harness.py` 从 `app.db`/`app.main` 自行装配 |
| ④ | V9 负对照留在仓库 + 诚实边界 | 通过 | 见 §5；15 条负对照（10 合成 + 5 真实路径）全在 `test_negative_controls.py` |
| ⑤ | V8 哈希夹逼 | 通过（含一处方法披露） | 见 §6 |
| ⑥ | `-s backend/tests/security` 全绿 | 通过 | `Ran 34 tests / OK / EXIT=0` |

**只看顶层不够**——这条是独立钉住的，不是口头保证：
```
nested_leak_contrast: naive_result=放行
                      recursive_result=嵌套泄漏（meta.questions[0].answer）:
                      $.meta.questions[0] 键集=['answer','display','question_type','seq']，多出=['answer']
```
`naive_top_level_only_check()` 是**故意写坏的**只看一层检查器（产品里不存在，仅作对照），它放行同一个嵌套泄漏体，而递归守卫拒绝——证明「只查顶层会漏」在本判据上确实成立。

## 4 Lead 加的三条

**(B) 写域登记**：目录创建的同轮已告知 Lead（`team-message-dae4c5df…`），Lead 已撤 `docs/03` §2 的「（尚未创建）」标注并复跑 `test_write_domains.py` `Ran 12 tests / OK / EXIT=0`。

**(C) 负对照的诚实边界**（写在用例里，也在证据里）：

* **能做到「真实产品路径」的部分**：`mock.patch.object(app.services.student_service, "public_item", widened)` 在**真实**投影上多写 `tags`+`answer`，再经**真实** `paper_payload` + 真实 DB 出题 →
  ```
  real_path_service_projection: 真实服务层投影（注入 tags+answer）: $.items[0] 键集=
    ['answer','display','question_type','seq','tags']，多出=['answer','tags']  → 守卫变红
  ```
  **HTTP 层真牙**（这一条是真实 HTTP 响应被抓，不是构造物）：让真实路由少发一题但 `total` 不变 →
  ```
  real_path_http_dropped_item: expected_items=4，但只探测到 3 个题目对象  → HTTP 层判据变红
  ```
* **做不到、必须写明的边界**：一次只改一层的情况下，**不可能**让真实 HTTP 响应体泄漏白名单外键——`response_model=` 会把模型外的键丢掉：
  ```
  real_path_http_boundary: injected=[tags,answer]  http_body_item_keys 全是 [display,question_type,seq]
    conclusion=response_model 过滤掉了越权键 → 只查 HTTP 体查不出『投影多写键』
  ```
  因此「HTTP 体会红」那条用的是**构造体**（真实响应外壳 + 真实被改宽的题目对象），已在用例名与证据里标注 `constructed_http_leak`。
  **诚实表述**：本套件证明了「守卫能抓真实服务层投影的越权」「守卫能抓真实 HTTP 响应的缺题」「一旦 HTTP 出口真的出现越权键，判据会红」；它**没有**证明「产品当前的 HTTP 出口会产生越权键」——后者由 `response_model` 这一层拦着，拦得对不对由 task-24 的 `_student_support.py` 守卫与本次的边界用例共同覆盖。

**(A) 两处既有断言的逐条审查** —— §5 单独一节（本轮核心判断）。

## 5 (A) 对 be-paper 两处既有断言改动的独立判断

我按 M3.5 的老办法办：不看结论，先看**改动前后逐字 diff + 前提在不在**。

`git diff` 全量只有两个 hunk（`21 insertions(+), 2 deletions(-)`），没有 `if` 跳过、没有新增「永不执行」的死断言。

### 5.1 `backend/tests/api/test_errors_and_sheet.py` —— 判定：**改对了（前提真的变了），且是加强不是放松**

* 改动：`contract` 字典里删掉 `"ATTEMPT_DUPLICATE": 200,`（原第 136 行）→ 加注释 → 新增 `test_deleted_attempt_duplicate_code_is_not_mapped`：`assertNotIn("ATTEMPT_DUPLICATE", ERROR_HTTP_STATUS)`。原循环 `for code, status in contract.items(): assertEqual(http_status_for(code), status)` **一字未动**。
* **前提确实变了，而且是「删掉了漂移」而不是「改了口味」**：
  ```
  git grep -n ATTEMPT_DUPLICATE HEAD
  HEAD:backend/app/services/errors.py:26:    "ATTEMPT_DUPLICATE": 200,
  HEAD:backend/tests/api/test_errors_and_sheet.py:136:            "ATTEMPT_DUPLICATE": 200,
  HEAD:docs/04-接口契约.md:372:> **已删除 `ATTEMPT_DUPLICATE`（D76 落地时）**：它原本写作「HTTP 200 + 错误码」——**自相矛盾**，
  git log --oneline -S ATTEMPT_DUPLICATE -- backend/app/services/errors.py
  3402182 feat(m2): 后端 API、卷面渲染与导出（task-5 / task-6）    # 该符号只在 M2 那次出现过
  ```
  三点事实：(1) 冻结契约 `docs/04` §9 在 **HEAD 提交里就已经写着「已删除」**，而实现还留着 → 这是**代码落后于冻结契约的漂移**，不是新裁决；(2) 全仓 `backend/app` **没有任何一处 raise/构造过这个码** → 它是**死映射**，`http_status_for("ATTEMPT_DUPLICATE")==200` 这条断言保护的是「一个谁也不用的自相矛盾项」；(3) 「HTTP 200 + 错误码」在语义上确实矛盾（200 = 成功）。
* 因此：旧断言的前提（该码存在且映射到 200）**真的不成立**了；替换成反向断言后，判据从「这个死项还在」变成「这个死项不许回来」——方向相反，**强度是增强**（原测试里 `contract` 只做 contract→map 单向校验，反向校验此前**一条都没有**）。
* **我自己补的行为判据（不依赖他的改动）**：`test_attempt_boundary.py::test_05` 用真实 HTTP 把「重复提交 = 200 + 正常响应体」钉住 →
  ```
  attempt_duplicate_replay: first_status=201  second_status=200
                            bodies_identical=true  error_envelope_present=false
  ```
  即：不是「200 + 错误信封」，重放返回的是逐字相同的正常结果。这条是**行为**证据，正好补上「只断言映射表里没有这个键」覆盖不到的那一半。
* **保留意见（不阻断，属于他的写域）**：`test_error_codes_cover_contract` 的名字写着「必须都在映射表里」，但它只遍历手写的 7 个码，`ERROR_HTTP_STATUS` 里还有 `ITEM_NOT_FOUND`/`RENDER_NOT_READY`/`EXPORT_*` 等（全表约 13 项）**无人对账**；反向断言也只挡了这一个码。建议（我没改，越域）把判据升成「映射表键集 ⊆ 契约 §9 码表」，任何新增码都会自己冒出来。**现状不是放松**（改动前同样没有这条），是**既有薄弱点**。

### 5.2 `backend/tests/api/test_schema.py` —— 判定：**改对了，两条新断言与产品代码一致；但它的强度只到 ORM 元数据层，我补了 DB 层**

* 改动三处：期望列集合加 `"attempt_uuid"`；新增 `assertTrue(Attempt.__table__.c.attempt_uuid.nullable, ...)`；新增 `test_attempt_uuid_is_unique_per_student`（从 `Attempt.__table__.constraints` 取 `UniqueConstraint` 列组合，断言含 `("attempt_uuid","student_id")`）。
* **列集合的前提真的变了**：`backend/app/models.py:117` `attempt_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)`（D76 新增列）→ 旧期望集合必然要改，否则测试反而红。
* **`nullable` 与唯一约束与产品一致**：`models.py:126` `UniqueConstraint("student_id","attempt_uuid", name="uq_attempt_student_uuid")`。`nullable=True` 有明确理由（`attempt_service.py:66-68`：历史成绩没有 uuid；NULL 在 SQLite 唯一索引里互不相等，老成绩不会互相撞键）——我的探针进一步确认：**真实生产库的 `attempt` 表连 `attempt_uuid` 列都还没有**（`columns=[id,student_id,paper_id,config_json,total,correct,duration_ms,submitted_at]`, `attempt_rows=0`），`ALTER TABLE ADD COLUMN` 只有可空列才加得上去。
* **我补的独立复核（DB 层，不是元数据层）**：这两条断言读的是 **ORM 元数据**，不是真实数据库。在这个「没有 Alembic、只靠 `create_all` + 手写 ALTER」的项目里两者是两回事，所以我在**生产库的副本**上把 `ensure_attempt_schema` 跑了一遍：
  ```
  copy_before:            has_attempt_uuid_column=false  has_target_unique_index=false
  copy_after_migration:   has_attempt_uuid_column=true   unique_sets=[uq_attempt_student_uuid (student_id,attempt_uuid) origin=c]
  copy_after_second_run:  与上一次逐字段相同              migration_idempotent=true
  fresh_after_create_all: unique_sets=[sqlite_autoindex_attempt_1 (student_id,attempt_uuid) origin=u]
  ```
  → ORM 元数据断言为真，**且**真实 DB 层（老库迁移后 + 新库 `create_all` 后）确实都有唯一索引，迁移对老库幂等。
* **保留意见（不阻断，属于他的写域）**：`test_attempt_uuid_is_unique_per_student` 在「模型有约束但某个真实库没有索引」时**仍然会绿**（例如老库、或将来换了迁移方式）。建议加一条 DB 层断言（`inspect(engine).get_indexes("attempt")` / `PRAGMA index_list`），与元数据断言并存。风险目前由 `ensure_attempt_schema` 兜着，我已在真实老库形状上验证过——**不是缺陷，是判据深度**。

## 6 V9 负对照清单（全部留在仓库；`test_negative_controls.py`）

**合成（10 条，只断言守卫指名违规路径）**：多键 / 少键 / 深层 `data.steps[0].answer` / 兄弟容器 `meta.questions[0].answer` / 非 `items` 容器里的题目 / 空响应与空 items **不得空过** / 数量与 `total` 不符 / `items` 元素非对象 / 顶层字段表。
```
synthetic_negative_controls: cases=10  result=全部按预期拒绝
```
**真实产品路径（5 条）**：注入生效自证 / 服务层投影被改宽→守卫抓到 / **同一注入下 HTTP 体仍干净（诚实边界）** / 真实路由少发一题→HTTP 层判据红 / 构造的 HTTP 越权体→判据会红。

**就地最小化负对照**（`test_student_whitelist.py::test_05`）：把**真实响应副本**改坏四处（加 `tags` / 删 `display` / 塞 `items[2].extra.answer` / 删掉一题）逐处必须变红；证据里 4 个 `subTest` 同名条目按最后一次覆盖（`items 少一题（数量与 total 对不上）`），这点在报告里如实说明。

## 7 判据边界：白名单**不适用于判分结果**（Lead 提醒第 2 条）

`test_attempt_boundary.py` 用断言钉住（不是注释）：
```
attempt_result_boundary: top_keys=[attempt_id,correct,items,total]
  item_keys=[answer,display,is_correct,question_type,seq,student_answer]
  answers_in_response={"1":"48","2":"33","3":"99"} == teacher_answers  answers_match_teacher=true  correct=1
attempt_result_forbidden_keys: scanned=[explanation,steps,tags,difficulty,signature,origin,expr,payload]  offenders=[]
attempt_boundary_whitelist_not_applicable: whitelist_verdict=拒绝（预期如此）
  reason=$.items[0] 键集=[...6 键...]，多出=['answer','is_correct','student_answer']
attempt_practice_mode_boundary: total=4  item_keys=[answer,display,is_correct,question_type,seq,student_answer]  result=pass
```
即：判分结果**必须**含 `answer`（且与老师端逐字一致）、**不得**含 `explanation`/`steps`/`tags`/`difficulty`/`signature`/`origin`/`expr`/`payload`；而把取题白名单套到判分结果上**必然报错**——哪天有人「统一」白名单，会是**这两类用例红、边界用例绿**，一眼能看出是判据越界而不是产品漏答案。

## 8 V8 哈希夹逼

工具：`artifacts/agg_hash.py`（跳过 `__pycache__`/`artifacts`/`.pylibs`）。

```
& $VPY .../agg_hash.py T1
backend_app                  36E5DCB90B38F969  (58 files)
backend_tests_ex_security    A4FAF09EB858031F  (75 files)
backend_tests_all            D92F03B45D877935  (81 files)
security_scope               BC38AFDB0835A935  (6 files)
```
**方法披露（如实）**：task-25 **开工时没有快照 T1**——我是在守卫与用例都落地之后才建的哈希工具，这是我这次的方法缺陷，不粉饰。所以 T1 的含义是「我的写域已就位、报告尚未落笔时」，**不是**「开工前」。
能给出的等价（且仍然有意义的）夹逼：
1. `backend_app`（58 文件）与 `backend_tests_ex_security`（75 文件）在 T1 与报告落笔后（T3，见 `artifacts/hash-T3.json`）**逐文件哈希相同** → 在整个验证窗口里，**被验对象一行未动**；变了的只有 `security_scope`（我自己的写域）。
2. 结构性证据：`git status --porcelain` 里我引入的条目**只有** `?? backend/tests/security/`，且 `backend/app/**` 的改动全部来自 task-24（在我开工前就已存在）。
3. `security_scope` 的 T1→T3 差异必须**恰好**是我自己的文件——这点用 `git grep --untracked` 与文件清单逐条核对。

## 9 未完成 / 遗留

* **无未完成判据**。四条纪律（写域、不 import api 测试包、不动产品代码、全量后端门禁没跑）全部遵守：本轮我只跑了 `-s backend/tests/security`（34 条）与两个既有 api 文件（33 条），**没有**跑全树（那是 Lead 在 fe-teacher 收工后自己跑的事）。
* 两条**保留意见**（§5.1 映射表全键对账、§5.2 DB 层索引断言）都在 be-paper 的写域，我未越域修改。
* `docs/05-任务板.md` 按 AGENTS.md 归 Lead；成品段落见 §10。

## 10 给 Lead 的 `docs/05` 成品段落（可直接粘贴）

| 任务 | 负责人 | 状态 | 关键证据 |
| --- | --- | --- | --- |
| task-25 T-M4-02 安全契约测试 | verify-m1 | 完成 | 新建 `backend/tests/security/**`（7 文件）；`& $VPY -m unittest discover -s backend/tests/security -t backend` → `Ran 34 tests / OK / EXIT=0`；D74 两条取题路径递归白名单 + 老师端 `answer` 反向断言 + 判分结果边界（判决含答案、白名单不适用）+ 15 条负对照（含真实服务层/真实 HTTP 路径）+ DB 层迁移探针；生产库 `LastWriteTime` 全程 `2026/10/15 18:48:52` 未变 |

**技术结论（贴报告用）**：学生端取题/练习两条路径的响应体在**任意深度**都不含白名单外键（题目对象键集恰好 `seq/display/question_type`，与老师端同一批题面逐字一致），老师端 `GET /api/papers/{id}` 逐题含 `answer`；判分结果 `POST /api/attempts` 按契约反向保留 `answer`/`is_correct`（白名单至此为止），重复提交为 `201 → 200` 且两次响应体逐字相同、无错误信封。守卫对真实服务层投影越权与真实 HTTP 缺题均能变红；白名单外键**不可能**从当前 HTTP 出口漏出（`response_model` 过滤），故「HTTP 出口会不会泄漏」这一命题的诚实边界是：本套件证明的是**判据有牙 + 真实 HTTP 层的缺题检测有牙**，产品出口的过滤正确性由 task-24 的守卫与本次边界用例共同覆盖。另复核 be-paper 两处既有断言：`ATTEMPT_DUPLICATE` 那一处是**删掉代码落后于冻结契约的漂移**（HEAD 上该码只出现在映射表/测试/文档三处，`backend/app` 无任何 raise 点，是死映射），替换为反向断言属**加强**；`attempt_uuid` 那一处的列集合变化源于 D76 新列，两条新断言与 `models.py` 一致，且我以真实老库副本上的 DB 层探针补验了唯一索引与迁移幂等。
