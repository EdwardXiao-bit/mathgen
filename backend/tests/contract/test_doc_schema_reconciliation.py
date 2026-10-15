"""`docs/02` §5.2 建表语句 ↔ `backend/app/models.py` 逐列 + 逐索引对账
（task-37 / T-M4-17 / D79）。

## 为什么存在

D78 给 `paper` 加了 `archived_at`、D76 给 `attempt` 加了 `attempt_uuid`，两列**都漏写在
`docs/02-技术设计.md` 的 `CREATE TABLE` 里**，直到 D79 审计才发现。根因与 V13 同族：
**靠人记得同步的约定迟早漂移，而且漂移是静默的** —— 没有任何东西会因为文档少一列而变红。
`docs/03` §6.1 只能要求"改了模型要记得改文档"，改没改只能用这条机器判据来验。
同一套审计还发现模型有 4 个显式索引而文档只写了 2 条 `CREATE INDEX`，因此索引也纳入对账。

## 判据读什么（两侧都不猜格式）

* **文档侧 `CREATE TABLE`**：实测 §5.2 的建表语句**不是表格也不是缩进块**，而是
  206–278 行**一个 ```sql 围栏里的 6 条标准 `CREATE TABLE <表> (\n ... \n);`**
  加 4 条 `CREATE INDEX <名> ON <表>(<列>);`。列定义行带行尾 `--` 中文注释，
  表级约束是独立行（`UNIQUE(paper_id, seq)` 等）。解析规则：行首标识符 = 列名；
  该行 `NOT NULL` 或 `PRIMARY KEY` → 必填，否则可空。
  **`CREATE INDEX` 行在解析表之前就被摘掉**（`_split_fence_body`），
  并且另有断言确保索引名绝不出现在任何表的列集合里 —— 不靠"切表体"的实现细节兜底。
* **文档侧 `CREATE INDEX`**：只认 `CREATE INDEX [UNIQUE] <名> ON <表>(<列>)` 这一形态。
  表级 `UNIQUE(...)` 与列上的 `unique=True` **不是**索引，绝不算进来（见下"同一层级"）。
* **模型侧列**：`app.models.Base.metadata` 的 `Table.columns`（**不用正则读源码**：
  该文件全部写作 `Mapped[...] = mapped_column(...)`，正则漏一种写法就会让判据假绿）。
* **模型侧索引**：`Table.indexes`，即显式 `Index(...)` 对象；**不含**
  `UniqueConstraint`（`paper.code` 的 `unique=True` 走的就是 `UniqueConstraint`）、
  不含主键、不含外键。

## 为什么两侧可比（"同一层级"）

| 维度 | 文档侧 | 模型侧 | 为什么不混别的 |
| --- | --- | --- | --- |
| 列 | `CREATE TABLE` 里的列行 | `Table.columns` | — |
| 索引 | `CREATE INDEX` 行 | `Table.indexes` | 表级 `UNIQUE(...)`／列 `unique=True` 在模型侧是 `UniqueConstraint`，**不在 `Table.indexes` 里**；`paper.code` 就是实例 |
| 索引比什么 | 名字 + 表 + 列 + 是否 `UNIQUE` | 同名四项 | 两边都有这四个属性，硬比其它（类型、默认值、约束名）就会制造假红 |

## 诚实边界（本判据做不到什么）

* **两边同时错**（文档和模型一起漏掉同一列/同一索引）它看不见；`MIN_*` 下限只能挡
  "整体缩水"，不是覆盖率证明。
* 列只比**列名 + 可空性**；索引只比**名字 + 所在表 + 列 + 是否 UNIQUE**。
  类型细节（`TEXT` vs `String(40)`）、默认值、外键 `ON DELETE` 动作、主键/约束名、
  索引的 `WHERE` 部分一律不比。
* 依赖"每张表一条 `CREATE TABLE ... );`、每条索引一行 `CREATE INDEX ...;`"的形态。
  形态变了会**大声红**（未解析行 / 表数或列数或索引数下限），不会静默变绿。
* `EXPECTED_TABLES` / `EXPECTED_INDEXES` 是冻结清单：合法新增表或索引需要同时改文档与本文件
  ——这是有意的，防止"新表/新索引只进代码不进文档"从后门溜过去。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
)

from app.models import Base

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN_DOC = REPO_ROOT / "docs" / "02-技术设计.md"

MIN_TABLES = 6
MIN_COLUMNS = 40
MIN_INDEXES = 4
EXPECTED_TABLES = frozenset(
    {"paper", "paper_item", "edit_log", "student", "attempt", "attempt_item"}
)
EXPECTED_INDEXES = frozenset(
    {"idx_paper_status", "idx_edit_log_paper", "idx_attempt_paper", "idx_attempt_student"}
)

_FENCE_RE = re.compile(r"```sql[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\(",
    re.IGNORECASE,
)
_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s+"
    r"ON\s+[`\"\[]?(\w+)[`\"\]]?\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_CREATE_INDEX_ANY_RE = re.compile(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\b", re.IGNORECASE)
_TABLE_CONSTRAINT_RE = re.compile(
    r"^(UNIQUE|PRIMARY\s+KEY|FOREIGN\s+KEY|CHECK|CONSTRAINT)\b", re.IGNORECASE
)
_COLUMN_RE = re.compile(r"^([A-Za-z_]\w*)\s+(\S.*)$")

# 索引记录：名字 → (表, 列, 是否 UNIQUE)
IndexRecord = tuple[str, tuple[str, ...], bool]


# --------------------------------------------------------------------------- 文档侧


def sql_fences(text: str) -> list[str]:
    """取出所有 ```sql 围栏的正文（建表/建索引语句理应只出现在围栏里）。"""
    return [m.group(1) for m in _FENCE_RE.finditer(text)]


def _split_fence_body(fence: str) -> tuple[str, dict[str, IndexRecord], list[str]]:
    """把一个 sql 围栏拆成 `(摘掉 CREATE INDEX 行后的正文, 索引, 无法解析的索引行)`。

    先摘索引行再解析表：这样"索引行混进最后一张表的列集合"在结构上就不可能发生；
    测试里还会再断言一次，双重保险。
    """
    body_lines: list[str] = []
    indexes: dict[str, IndexRecord] = {}
    broken: list[str] = []

    for raw_line in fence.splitlines():
        statement = raw_line.split("--", 1)[0].strip()
        match = _CREATE_INDEX_RE.match(statement)
        if match:
            unique = bool(match.group(1))
            name, table = match.group(2), match.group(3)
            columns = tuple(part.strip() for part in match.group(4).split(",") if part.strip())
            indexes[name] = (table, columns, unique)
            continue
        if _CREATE_INDEX_ANY_RE.match(statement):
            broken.append(statement)
            continue
        body_lines.append(raw_line)

    return "\n".join(body_lines), indexes, broken


def parse_doc_schema(text: str) -> tuple[dict[str, dict[str, bool]], list[str]]:
    """解析文档里的 `CREATE TABLE`。

    返回 `({表名: {列名: 是否必填}}, [无法解析的非空行])`。
    第二项不是装饰：它让"表格形态变了"变成**红**，而不是"少解析出几列"。
    """
    tables: dict[str, dict[str, bool]] = {}
    unparsed: list[str] = []

    for fence in sql_fences(text):
        body_text, _indexes, _broken = _split_fence_body(fence)
        matches = list(_CREATE_TABLE_RE.finditer(body_text))
        for index, match in enumerate(matches):
            name = match.group(1)
            stop = matches[index + 1].start() if index + 1 < len(matches) else len(body_text)
            body = body_text[match.end() : stop]
            body = re.split(r"(?mi)^\s*CREATE\s", body, maxsplit=1)[0]
            close = body.find(");")
            if close != -1:
                body = body[:close]

            columns: dict[str, bool] = {}
            for raw_line in body.splitlines():
                line = raw_line.split("--", 1)[0].strip().rstrip(",").strip()
                if not line:
                    continue
                if _TABLE_CONSTRAINT_RE.match(line):
                    continue
                column = _COLUMN_RE.match(line)
                if not column:
                    unparsed.append(f"{name}: {line}")
                    continue
                rest = column.group(2).upper()
                columns[column.group(1)] = ("NOT NULL" in rest) or ("PRIMARY KEY" in rest)
            tables[name] = columns

    return tables, unparsed


def parse_doc_indexes(text: str) -> tuple[dict[str, IndexRecord], list[str]]:
    """解析文档里的 `CREATE INDEX`；返回 `({索引名: (表, 列, unique)}, [无法解析的行])`。"""
    indexes: dict[str, IndexRecord] = {}
    broken: list[str] = []

    for fence in sql_fences(text):
        _body, fence_indexes, fence_broken = _split_fence_body(fence)
        indexes.update(fence_indexes)
        broken.extend(fence_broken)

    return indexes, broken


def read_design_doc(path: Path = DESIGN_DOC) -> str:
    """读真实设计文档；读不到 / 明显不是这份文档 → 大声红（绝不静默通过）。"""
    if not path.is_file():
        raise AssertionError(
            f"读不到设计文档：{path} —— 本判据要求对账真实文件，缺文件必须红，不许静默通过"
        )
    text = path.read_text(encoding="utf-8")
    if "CREATE TABLE" not in text:
        raise AssertionError(
            f"{path} 里找不到任何 `CREATE TABLE` —— 文档形态可能变了，本判据拒绝静默通过"
        )
    return text


# --------------------------------------------------------------------------- 模型侧


def _schema_from_metadata(metadata) -> dict[str, dict[str, bool]]:
    """从 SQLAlchemy metadata 读列与可空性。`mapped_column` 与 `Column` 都经同一条路径。"""
    return {
        table.name: {column.name: (not column.nullable) for column in table.columns}
        for table in metadata.sorted_tables
    }


def model_schema() -> dict[str, dict[str, bool]]:
    return _schema_from_metadata(Base.metadata)


def _indexes_from_metadata(metadata) -> dict[str, IndexRecord]:
    """只取 `Table.indexes`（显式 `Index` 对象）。

    `UniqueConstraint`（含列上的 `unique=True`）不在其中 —— 那是约束不是命名索引，
    混进来会让本判据在**正确的**文档上变红，然后人就会想去加白名单。
    """
    indexes: dict[str, IndexRecord] = {}
    for table in metadata.sorted_tables:
        for index in table.indexes:
            name = index.name or f"<unnamed index on {table.name}>"
            indexes[name] = (
                table.name,
                tuple(column.name for column in index.columns),
                bool(index.unique),
            )
    return indexes


def model_indexes() -> dict[str, IndexRecord]:
    return _indexes_from_metadata(Base.metadata)


# --------------------------------------------------------------------------- 对账


def compare_schemas(
    doc: dict[str, dict[str, bool]],
    model: dict[str, dict[str, bool]],
) -> list[str]:
    """逐表逐列双向对账，返回人话问题清单；空清单 = 一致。"""
    problems: list[str] = []

    for table in sorted(set(doc) | set(model)):
        if table not in model:
            problems.append(f"{table}：文档 §5.2 有这张表，`app/models.py` 里没有")
            continue
        if table not in doc:
            problems.append(f"{table}：`app/models.py` 有这张表，文档 §5.2 里没有")
            continue

        doc_columns, model_columns = doc[table], model[table]
        for column in sorted(set(doc_columns) - set(model_columns)):
            problems.append(f"{table}.{column}：**文档有、模型没有**（文档漏删，或代码少建一列）")
        for column in sorted(set(model_columns) - set(doc_columns)):
            problems.append(f"{table}.{column}：**模型有、文档没有**（文档漏更新，D78/D76 同款）")
        for column in sorted(set(doc_columns) & set(model_columns)):
            if doc_columns[column] != model_columns[column]:
                problems.append(
                    f"{table}.{column}：文档写 "
                    f"{'NOT NULL' if doc_columns[column] else '可空'}，"
                    f"模型是 {'NOT NULL' if model_columns[column] else '可空'}"
                )

    return problems


def _describe_index(record: IndexRecord) -> str:
    table, columns, unique = record
    return f"{table}({', '.join(columns)}){' UNIQUE' if unique else ''}"


def compare_indexes(
    doc: dict[str, IndexRecord],
    model: dict[str, IndexRecord],
) -> list[str]:
    """逐索引双向对账（名字 + 表 + 列 + 是否 UNIQUE）；空清单 = 一致。"""
    problems: list[str] = []

    for name in sorted(set(doc) | set(model)):
        if name not in model:
            problems.append(
                f"索引 {name}：**文档有、模型没有**（文档写了 `{_describe_index(doc[name])}`，"
                "但 `app/models.py` 里没有这个索引）"
            )
            continue
        if name not in doc:
            problems.append(
                f"索引 {name}：**模型有、文档没有**（模型是 `{_describe_index(model[name])}`）"
            )
            continue
        if doc[name] != model[name]:
            problems.append(
                f"索引 {name}：文档写 `{_describe_index(doc[name])}`，"
                f"模型是 `{_describe_index(model[name])}`"
            )

    return problems


def _floor_problems(
    schema: dict[str, dict[str, bool]],
    *,
    label: str,
    min_tables: int,
    min_columns: int,
) -> list[str]:
    """下限断言：挡住"空集对空集"式的全绿（那比不写这条判据更糟）。"""
    problems: list[str] = []
    total = sum(len(columns) for columns in schema.values())
    if len(schema) < min_tables:
        problems.append(
            f"{label}只解析出 {len(schema)} 张表（下限 {min_tables}）—— 读到的不是完整表结构"
        )
    if total < min_columns:
        problems.append(
            f"{label}只解析出 {total} 列（下限 {min_columns}）—— 下限用来挡住空集对空集的全绿"
        )
    for table in sorted(table for table, columns in schema.items() if not columns):
        problems.append(f"{label}的 {table} 没有解析出任何列")
    return problems


def _index_floor_problems(
    indexes: dict[str, IndexRecord],
    *,
    label: str,
    min_indexes: int,
) -> list[str]:
    if len(indexes) < min_indexes:
        return [
            f"{label}只解析出 {len(indexes)} 个索引（下限 {min_indexes}）—— "
            "下限用来挡住空集对空集的全绿"
        ]
    return []


def assert_reconciled(
    text: str,
    *,
    min_tables: int = MIN_TABLES,
    min_columns: int = MIN_COLUMNS,
    min_indexes: int = MIN_INDEXES,
    model: dict[str, dict[str, bool]] | None = None,
    indexes: dict[str, IndexRecord] | None = None,
    source: str = "docs/02-技术设计.md §5.2",
) -> None:
    """对账入口：任何一条不一致就抛 `AssertionError`，消息里点名表/列/索引。"""
    model_schema_ = model_schema() if model is None else model
    model_indexes_ = model_indexes() if indexes is None else indexes
    doc, unparsed = parse_doc_schema(text)
    doc_indexes, broken_indexes = parse_doc_indexes(text)

    problems: list[str] = []
    if unparsed:
        problems.append(
            "文档里有无法解析成列定义的非空行（建表语句的形态可能变了）：\n      "
            + "\n      ".join(unparsed)
        )
    if broken_indexes:
        problems.append(
            "文档里有无法解析成 `CREATE INDEX <名> ON <表>(<列>)` 的索引行：\n      "
            + "\n      ".join(broken_indexes)
        )
    problems += _floor_problems(
        doc, label="文档侧", min_tables=min_tables, min_columns=min_columns
    )
    problems += _floor_problems(
        model_schema_, label="模型侧", min_tables=min_tables, min_columns=min_columns
    )
    problems += _index_floor_problems(doc_indexes, label="文档侧", min_indexes=min_indexes)
    problems += _index_floor_problems(model_indexes_, label="模型侧", min_indexes=min_indexes)
    problems += compare_schemas(doc, model_schema_)
    problems += compare_indexes(doc_indexes, model_indexes_)

    if problems:
        raise AssertionError(
            f"{source} 与 `backend/app/models.py` 对不上（{len(problems)} 条）：\n  - "
            + "\n  - ".join(problems)
        )


# --------------------------------------------------------------------------- 合成文档的改写工具（负对照用）


def _rewrite_table_body(text: str, table: str, transform):
    """只改写 `<table>` 的 CREATE TABLE 表体（收尾 `);` 之前）。

    没进到该表体就断言失败——防止负对照"没真的改到东西"却因为别的原因变红。
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    touched = False

    for line in lines:
        match = _CREATE_TABLE_RE.search(line)
        if match:
            inside = match.group(1) == table
            out.append(line)
            continue
        if inside:
            if line.split("--", 1)[0].strip().startswith(");"):
                inside = False
                out.append(line)
                continue
            out.append(transform(line))
            touched = True
            continue
        out.append(line)

    if not touched:
        raise AssertionError(f"负对照没有改到 {table} 的表体（文档形态变了？）")
    return "".join(out)


def _rewrite_index_lines(text: str, transform) -> str:
    """对每个 `CREATE INDEX` 行调用 transform；一行都没碰到就断言失败。"""
    out: list[str] = []
    touched = False
    for line in text.splitlines(keepends=True):
        if _CREATE_INDEX_RE.match(line.split("--", 1)[0].strip()):
            touched = True
            out.append(transform(line))
        else:
            out.append(line)
    if not touched:
        raise AssertionError("负对照没有改到任何 CREATE INDEX 行（文档形态变了？）")
    return "".join(out)


def _drop_column(text: str, table: str, column: str) -> str:
    dropped = {"n": 0}

    def transform(line: str) -> str:
        if re.match(rf"^{re.escape(column)}\s", line.split("--", 1)[0].strip()):
            dropped["n"] += 1
            return ""
        return line

    rewritten = _rewrite_table_body(text, table, transform)
    if dropped["n"] != 1:
        raise AssertionError(f"负对照期望删掉 1 行 {table}.{column}，实际 {dropped['n']} 行")
    return rewritten


def _insert_column(text: str, table: str, column: str, declaration: str = "TEXT NOT NULL") -> str:
    inserted = {"done": False}

    def transform(line: str) -> str:
        if not inserted["done"] and line.strip():
            inserted["done"] = True
            return f"  {column} {declaration},   -- 负对照：文档凭空多一列\n" + line
        return line

    rewritten = _rewrite_table_body(text, table, transform)
    if not inserted["done"]:
        raise AssertionError(f"负对照没能在 {table} 里插入合成列 {column}")
    return rewritten


def _loosen_not_null(text: str, table: str, column: str) -> str:
    loosened = {"n": 0}

    def transform(line: str) -> str:
        body = line.split("--", 1)[0]
        if re.match(rf"^{re.escape(column)}\s", body.strip()) and "NOT NULL" in body:
            loosened["n"] += 1
            return line.replace("NOT NULL", " " * len("NOT NULL"), 1)
        return line

    rewritten = _rewrite_table_body(text, table, transform)
    if loosened["n"] != 1:
        raise AssertionError(f"负对照期望把 {table}.{column} 放宽成可空，实际改了 {loosened['n']} 行")
    return rewritten


def _drop_index_line(text: str, name: str) -> str:
    hits = {"n": 0}

    def transform(line: str) -> str:
        match = _CREATE_INDEX_RE.match(line.split("--", 1)[0].strip())
        if match and match.group(2) == name:
            hits["n"] += 1
            return ""
        return line

    rewritten = _rewrite_index_lines(text, transform)
    if hits["n"] != 1:
        raise AssertionError(f"负对照期望删掉 1 行索引 {name}，实际 {hits['n']} 行")
    return rewritten


def _replace_index_line(text: str, name: str, new_line: str) -> str:
    hits = {"n": 0}

    def transform(line: str) -> str:
        match = _CREATE_INDEX_RE.match(line.split("--", 1)[0].strip())
        if match and match.group(2) == name:
            hits["n"] += 1
            return new_line if new_line.endswith("\n") else new_line + "\n"
        return line

    rewritten = _rewrite_index_lines(text, transform)
    if hits["n"] != 1:
        raise AssertionError(f"负对照期望替换 1 行索引 {name}，实际 {hits['n']} 行")
    return rewritten


def _add_index_line(text: str, new_line: str) -> str:
    lines = text.splitlines(keepends=True)
    positions = [
        position
        for position, line in enumerate(lines)
        if _CREATE_INDEX_RE.match(line.split("--", 1)[0].strip())
    ]
    if not positions:
        raise AssertionError("负对照找不到任何 CREATE INDEX 行可插入其后")
    lines.insert(positions[-1] + 1, new_line if new_line.endswith("\n") else new_line + "\n")
    return "".join(lines)


# --------------------------------------------------------------------------- 测试


class TestDocSchemaReconciliation(unittest.TestCase):
    """门禁本体：真实 `docs/02` 与真实 `app.models` 对账（列 + 索引）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = read_design_doc()
        cls.doc, cls.unparsed = parse_doc_schema(cls.text)
        cls.model = model_schema()
        cls.indexes, cls.broken_indexes = parse_doc_indexes(cls.text)
        cls.model_idx = model_indexes()

    def test_01_the_parser_really_read_the_real_document(self):
        """先证明"读到了真东西"：没有未解析行、表/索引清单相符、列数有下限。"""
        self.assertEqual(self.unparsed, [], "建表语句里有无法解析的非空行")
        self.assertEqual(self.broken_indexes, [], "有解析不出的 CREATE INDEX 行")
        self.assertEqual(set(self.doc), set(EXPECTED_TABLES))
        self.assertEqual(set(self.indexes), set(EXPECTED_INDEXES))
        self.assertGreaterEqual(sum(len(c) for c in self.doc.values()), MIN_COLUMNS)
        self.assertGreaterEqual(len(self.indexes), MIN_INDEXES)
        for table in sorted(EXPECTED_TABLES):
            self.assertGreaterEqual(len(self.doc[table]), 1, f"{table} 没解析出任何列")

    def test_02_no_create_index_line_leaks_into_a_table_column_set(self):
        """索引行绝不出现在任何表的列集合里（不靠切表体的实现细节兜底）。"""
        self.assertTrue(self.indexes, "一个索引都没解析出来，这条断言就没意义")
        for table, columns in self.doc.items():
            leaked = set(self.indexes) & set(columns)
            self.assertEqual(leaked, set(), f"{table} 的列集合里混进了索引名：{sorted(leaked)}")

    def test_03_document_and_model_agree_column_by_column(self):
        problems = compare_schemas(self.doc, self.model)
        self.assertEqual(
            problems,
            [],
            "docs/02 §5.2 与 `app/models.py` 列不一致：\n  - " + "\n  - ".join(problems),
        )

    def test_04_document_and_model_agree_index_by_index(self):
        problems = compare_indexes(self.indexes, self.model_idx)
        self.assertEqual(
            problems,
            [],
            "docs/02 §5.2 与 `app/models.py` 索引不一致：\n  - " + "\n  - ".join(problems),
        )

    def test_05_the_gate_is_clean_end_to_end(self):
        """走一遍真正的门禁入口（含两侧下限、未解析行/索引行检查）。"""
        assert_reconciled(self.text, model=self.model, indexes=self.model_idx)

    def test_06_model_side_is_real_ddl_not_source_text(self):
        """模型侧取自 `Table.columns`；`create_all` 到内存 SQLite 后必须完全一致。"""
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine)
            inspector = inspect(engine)
            reflected = {
                name: {column["name"]: (not column["nullable"]) for column in inspector.get_columns(name)}
                for name in inspector.get_table_names()
            }
        finally:
            engine.dispose()
        self.assertEqual(reflected, self.model)


class TestModelSideReadsBothColumnStyles(unittest.TestCase):
    """Lead 点名要求：`mapped_column` 与 `Column` 两种写法都要覆盖，否则漏一列会假绿。"""

    def test_plain_column_and_mapped_column_are_both_read(self):
        metadata = MetaData()
        Table(
            "plain_style",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("note", String, nullable=True),
        )
        self.assertEqual(
            _schema_from_metadata(metadata), {"plain_style": {"id": True, "note": False}}
        )
        self.assertEqual(set(_schema_from_metadata(Base.metadata)), set(EXPECTED_TABLES))


class TestIndexSideIsSameLevel(unittest.TestCase):
    """索引两侧必须取"同一层级"的东西：模型侧 `Table.indexes`，文档侧只有 `CREATE INDEX`。"""

    def test_model_side_is_table_indexes_not_unique_constraints(self):
        indexes = model_indexes()
        self.assertEqual(set(indexes), set(EXPECTED_INDEXES))

        paper = Base.metadata.tables["paper"]
        self.assertEqual({index.name for index in paper.indexes}, {"idx_paper_status"})
        # `code` 的 unique=True 是 UniqueConstraint，绝不能算成索引
        self.assertTrue(
            any(isinstance(constraint, UniqueConstraint) for constraint in paper.constraints),
            "paper 应当有 UNIQUE 约束（code unique=True）",
        )
        self.assertNotIn(
            "code", {column for _t, columns, _u in indexes.values() for column in columns}
        )

        student = Base.metadata.tables["student"]
        self.assertEqual(set(student.indexes), set(), "student 没有显式索引")
        self.assertTrue(
            any(isinstance(constraint, UniqueConstraint) for constraint in student.constraints),
            "student 应当有 UNIQUE(name, student_no) 约束",
        )

    def test_doc_side_only_reads_create_index_lines(self):
        snippet = (
            "```sql\nCREATE TABLE student (\n"
            "  id         INTEGER PRIMARY KEY,\n"
            "  name       TEXT NOT NULL,\n"
            "  student_no TEXT NOT NULL,\n"
            "  UNIQUE(name, student_no)\n"
            ");\n```\n"
        )
        indexes, broken = parse_doc_indexes(snippet)
        self.assertEqual(indexes, {}, "表级 UNIQUE(...) 不是 CREATE INDEX，不该被当成索引")
        self.assertEqual(broken, [])
        self.assertEqual(set(parse_doc_indexes(read_design_doc())[0]), set(EXPECTED_INDEXES))

    def test_index_line_right_after_the_last_table_is_not_a_column(self):
        """对抗形态：索引行紧贴最后一张表、没有空行分隔，索引名不许出现在列集合里。"""
        synthetic = (
            "```sql\nCREATE TABLE t (\n"
            "  id      INTEGER PRIMARY KEY,\n"
            "  payload TEXT NOT NULL\n"
            ");\n"
            "CREATE INDEX idx_t_id ON t(id);\n"
            "```\n"
        )
        tables, unparsed = parse_doc_schema(synthetic)
        indexes, broken = parse_doc_indexes(synthetic)
        self.assertEqual(unparsed, [])
        self.assertEqual(broken, [])
        self.assertEqual(indexes, {"idx_t_id": ("t", ("id",), False)})
        self.assertEqual(tables["t"], {"id": True, "payload": True})
        self.assertNotIn("idx_t_id", tables["t"], "索引名绝不能出现在列集合里")


class TestNegativeControls(unittest.TestCase):
    """合成的坏文档必须红，且点名哪张表/哪一列/哪个索引——这是判据能自证的唯一方式。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = read_design_doc()
        cls.model = model_schema()
        cls.model_idx = model_indexes()
        # 正对照：原文必须过，否则下面的"红"没有意义。
        assert_reconciled(
            cls.text, model=cls.model, indexes=cls.model_idx, source="原文档（正对照）"
        )

    # ---- 列 ----

    def test_01_column_the_model_has_but_the_document_lost_is_red_and_named(self):
        broken = _drop_column(self.text, "paper", "archived_at")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("paper.archived_at", message)
        self.assertIn("模型有、文档没有", message)

    def test_02_missing_attempt_uuid_column_is_red_and_named(self):
        broken = _drop_column(self.text, "attempt", "attempt_uuid")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("attempt.attempt_uuid", message)
        self.assertIn("模型有、文档没有", message)

    def test_03_column_only_the_document_has_is_red_and_named(self):
        broken = _insert_column(self.text, "student", "ghost_column")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("student.ghost_column", message)
        self.assertIn("文档有、模型没有", message)

    def test_04_nullability_drift_is_red_and_named(self):
        broken = _loosen_not_null(self.text, "paper", "title")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("paper.title", message)
        self.assertIn("可空", message)

    def test_05_empty_or_tableless_document_is_loud_not_green(self):
        for label, bad in (
            ("空文本", ""),
            ("只有标题", "# 数据模型\n\n这一节没有建表语句。\n"),
            ("围栏里没有 CREATE TABLE", "```sql\nSELECT 1;\n```\n"),
        ):
            with self.subTest(case=label):
                with self.assertRaises(AssertionError) as ctx:
                    assert_reconciled(bad, model=self.model, indexes=self.model_idx)
                self.assertIn("下限", str(ctx.exception))

    def test_06_truncated_document_trips_the_table_floor(self):
        one_table = (
            "```sql\nCREATE TABLE student (\n"
            "  id         INTEGER PRIMARY KEY,\n"
            "  name       TEXT NOT NULL\n"
            ");\n```\n"
        )
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(one_table, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("下限", message)
        self.assertIn("student", message)

    def test_07_unparsable_line_is_reported_verbatim(self):
        replaced = {"done": False}

        def transform(line: str) -> str:
            if not replaced["done"] and line.strip().startswith("is_correct"):
                replaced["done"] = True
                return "  *** 这里本该是列定义 ***\n"
            return line

        broken = _rewrite_table_body(self.text, "attempt_item", transform)
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("无法解析", message)
        self.assertIn("*** 这里本该是列定义 ***", message)

    def test_08_missing_or_wrong_file_is_loud(self):
        with self.assertRaises(AssertionError) as ctx:
            read_design_doc(Path(__file__).with_name("no-such-design-doc.md"))
        self.assertIn("读不到设计文档", str(ctx.exception))

        with self.assertRaises(AssertionError) as ctx:
            read_design_doc(Path(__file__).with_name("__init__.py"))
        self.assertIn("找不到任何 `CREATE TABLE`", str(ctx.exception))

    # ---- 索引 ----

    def test_09_index_the_model_has_but_the_document_lost_is_red_and_named(self):
        broken = _drop_index_line(self.text, "idx_paper_status")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("索引 idx_paper_status", message)
        self.assertIn("模型有、文档没有", message)

    def test_10_index_only_the_document_has_is_red_and_named(self):
        broken = _add_index_line(self.text, "CREATE INDEX idx_ghost ON paper(status);")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("索引 idx_ghost", message)
        self.assertIn("文档有、模型没有", message)

    def test_11_index_pointing_at_the_wrong_table_is_red_and_named(self):
        broken = _replace_index_line(
            self.text, "idx_edit_log_paper", "CREATE INDEX idx_edit_log_paper ON paper(status);"
        )
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("idx_edit_log_paper", message)
        self.assertIn("文档写 `paper(status)`", message)
        self.assertIn("模型是 `edit_log(paper_id)`", message)

    def test_12_all_index_lines_removed_trips_the_index_floor(self):
        broken = _rewrite_index_lines(self.text, lambda line: "")
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("索引", message)
        self.assertIn("下限", message)

    def test_13_unparsable_index_line_is_reported_verbatim(self):
        broken = _replace_index_line(
            self.text, "idx_attempt_student", "CREATE INDEX idx_attempt_student breakdown;"
        )
        with self.assertRaises(AssertionError) as ctx:
            assert_reconciled(broken, model=self.model, indexes=self.model_idx)
        message = str(ctx.exception)
        self.assertIn("无法解析成 `CREATE INDEX", message)
        self.assertIn("CREATE INDEX idx_attempt_student breakdown;", message)


class TestPositiveControls(unittest.TestCase):
    """防"恒红"：一致的输入必须绿，否则负对照证明不了任何东西。"""

    def test_identical_synthetic_schemas_compare_clean(self):
        schema = {"some_table": {"id": True, "name": False}}
        self.assertEqual(compare_schemas(schema, dict(schema)), [])

    def test_identical_synthetic_indexes_compare_clean(self):
        indexes = {"idx_x": ("t", ("a",), False)}
        self.assertEqual(compare_indexes(indexes, dict(indexes)), [])

    def test_real_document_passes_the_gate(self):
        assert_reconciled(read_design_doc(), model=model_schema(), indexes=model_indexes())


if __name__ == "__main__":
    unittest.main()
