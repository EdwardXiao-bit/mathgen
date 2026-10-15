"""成绩导出（FR-T13，契约 §5.5 / D80）。

把一串**已经算好的** `AttemptSummary` 变成老师能直接打开的文件。
本模块是**纯函数**：不碰数据库、不认识 FastAPI，因此判据可以直接构造
`AttemptSummary` 对象来下断言，不必等路由与服务层就绪。

三条容易被做成「看起来对」的规则，在这里各有一条硬实现：

1. **CSV 必须是 UTF-8 with BOM**（`utf-8-sig`）。理由不是美观：Windows Excel
   打开无 BOM 的 UTF-8 CSV 时中文列名是乱码。目标用户是 Windows Excel，
   所以「在 Linux/macOS 上打开正常」**不构成证据**——判据读回收件流的前 3 字节。
2. **导出不受 `limit`/`offset` 影响**。那两个参数是列表（FR-T11）的分页参数；
   `export_attempts` 的签名里**根本没有**它们，所以「偷偷套用分页」在类型上
   就无从发生——这条是结构性的，不是靠记得别写。
3. **`score` 直接用 `AttemptSummary.score`**（D80 唯一来源）。这里**不重算**，
   否则导出与界面就会各自漂移出一套得分口径。
"""
from __future__ import annotations

import csv
import io
import urllib.parse
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from app.contracts.api import AttemptSummary

__all__ = [
    "COLUMNS",
    "HEADER_LABELS",
    "MEDIA_TYPE_CSV",
    "MEDIA_TYPE_XLSX",
    "SUPPORTED_FORMATS",
    "content_disposition",
    "export_attempts",
    "filename_for",
    "format_duration",
    "rows_of",
]

#: 支持的导出格式（契约 §5.5：`csv` 默认 | `xlsx`；其它值 → 422）
SUPPORTED_FORMATS: Final = ("csv", "xlsx")

MEDIA_TYPE_CSV: Final = "text/csv; charset=utf-8"
#: Excel (xlsx) 的标准 MIME
MEDIA_TYPE_XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 列定义：`(表头, 取值函数)`，**顺序即契约**（`docs/04` §5.5「列（固定顺序）」）。
#: 表头是「人能读的中文」而不是字段名——老师要的是表格，不是 API 转储。
COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("卷号", "paper_code"),
    ("卷名", "paper_title"),
    ("姓名", "student_name"),
    ("学号", "student_no"),
    ("得分", "score"),
    ("答对题数", "correct"),
    ("总题数", "total"),
    ("用时", "duration_ms"),
    ("提交时间", "submitted_at"),
)

#: 表头文字（按列顺序），供判据逐字比对
HEADER_LABELS: Final[tuple[str, ...]] = tuple(label for label, _ in COLUMNS)


def format_duration(duration_ms: int | None) -> str:
    """把毫秒写成**人能读**的形式（契约 §5.5：不是裸毫秒）。

    规则：只保留「分」与「秒」两级，毫秒**向下取整到秒**。

    * `83000` → `1分23秒`
    * `45000` → `45秒`（不足 1 分时不写「0分」）
    * `0` / 负数 / `None` → `0秒`
    * 超过 1 小时也只进「分」（`3661000` → `61分1秒`）——小学练习题不存在小时级用时，
      多一级单位只会让表格更宽，不会更有用。

    毫秒**截断**而不是四舍五入：把 59.6 秒显示成「1分0秒」会让学生以为自己多用了一分钟。
    """
    if duration_ms is None or duration_ms <= 0:
        return "0秒"
    total_seconds = int(duration_ms) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def rows_of(rows: Sequence[AttemptSummary]) -> list[list[str]]:
    """按 `COLUMNS` 的固定顺序把概要铺成「字符串单元格」的二维表。

    **自主练习（D75）**：没有挂卷 → `paper_code` / `paper_title` 为 `None` → 留空字符串。
    契约明说接口**不返回**「自主练习」这类显示文案（文案属于界面层），
    所以这里也**不写**那四个字，只留空格子。
    """
    table: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for _label, field in COLUMNS:
            value = getattr(row, field)
            if field == "duration_ms":
                cells.append(format_duration(value))
            elif value is None:
                cells.append("")
            else:
                cells.append(str(value))
        table.append(cells)
    return table


def export_attempts(
    rows: Sequence[AttemptSummary], fmt: str, *, now: datetime | None = None
) -> tuple[bytes, str, str]:
    """把成绩概要导出成文件，返回 `(内容, media_type, 文件名)`。

    `rows` 是**当前筛选下的全部记录**（调用方负责取全，见本模块开头第 2 条）。
    `fmt` 只接受 `csv` / `xlsx`，其它值抛 `ValueError`（路由层在此之前已经把它
    变成 422；这里再抛一次是为了让**纯函数**自己也是可判的，不依赖路由）。
    `now` 仅用于文件名里的时间戳，注入它判据才能断言文件名。
    """
    value = fmt.strip().lower()
    if value == "csv":
        return (
            _export_csv(rows),
            MEDIA_TYPE_CSV,
            filename_for("csv", now=now),
        )
    if value == "xlsx":
        return (
            _export_xlsx(rows),
            MEDIA_TYPE_XLSX,
            filename_for("xlsx", now=now),
        )
    raise ValueError(f"format 只能是 {' 或 '.join(SUPPORTED_FORMATS)}，实际为 {fmt!r}")


def filename_for(fmt: str, *, now: datetime | None = None) -> str:
    """`成绩-YYYYMMDD-HHMM.csv` / `.xlsx`（契约 §5.5）。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"成绩-{stamp}.{fmt}"


def content_disposition(filename: str) -> str:
    """`Content-Disposition` 头：ASCII 兜底 + RFC 5987 `filename*`。

    文件名固定含中文（「成绩」），所以**两条都要给**：老浏览器只认 `filename=`，
    现代浏览器优先用 `filename*`。ASCII 兜底里把非 ASCII 换成 `_`，
    避免把原始中文直接塞进 Latin-1 头里（那会变成乱码甚至报错）。
    """
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    quoted = urllib.parse.quote(filename, safe="")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quoted}"
    )


# ---- 内部实现 -----------------------------------------------------------


def _export_csv(rows: Sequence[AttemptSummary]) -> bytes:
    """UTF-8 **with BOM** 的 CSV。

    `utf-8-sig` 就是「UTF-8 + BOM」；BOM 是给 Excel 认编码用的，不是给 Python 看的。
    换行用 `\\r\\n`（RFC 4180，也是 Excel 的口味）。
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADER_LABELS)
    writer.writerows(rows_of(rows))
    return buffer.getvalue().encode("utf-8-sig")


def _export_xlsx(rows: Sequence[AttemptSummary]) -> bytes:
    """xlsx：表头加粗并冻结首行（老师滚动时表头不跑掉）。

    用已装的 `openpyxl`，不引入新依赖。
    """
    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "成绩"

    sheet.append(list(HEADER_LABELS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows_of(rows):
        sheet.append(row)

    # 列宽按内容放宽一点（中文表头默认宽度会被截断）
    for index, label in enumerate(HEADER_LABELS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(10, len(label) * 2 + 4)
    sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
