"""导出通道的公共部分：中间文件目录、命名与清理。

PDF 与 Word 都在「仓库内的可写目录」里落一个中间文件再读回字节，原因见
`pdf_export` 的模块 docstring（沙箱下子进程写 `%TEMP%` 会 PermissionError）。

**为什么成功时零残留**：Windows 上子进程（及其驱动）会继承父进程打开的句柄，
父进程「开日志文件当子进程 stdout」会让文件删不掉（实测 `WinError 32`），
所以约定：**父进程只写输入、只读输出，日志类信息由子进程自己按需写**，
清理一律尽力而为（删不掉也不能让请求失败）。
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

__all__ = [
    "BACKEND_DIR",
    "DEFAULT_TMP",
    "export_tmp_dir",
    "new_token",
    "best_effort_unlink",
    "purge_stale",
    "read_text_quietly",
]

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

#: 中间文件目录必须可由**子进程**写入：默认落在仓库内（`.gitignore` 已忽略 `data/`）
DEFAULT_TMP = BACKEND_DIR / "data" / "exports"

#: 遗留文件的最长存活时间（秒）
STALE_AFTER_S = 3600.0


def export_tmp_dir() -> Path:
    """中间文件的落盘目录（`MATHGEN_EXPORT_TMP` 可覆盖）。"""
    override = os.environ.get("MATHGEN_EXPORT_TMP")
    return Path(override) if override else DEFAULT_TMP


def new_token() -> str:
    return uuid.uuid4().hex


def best_effort_unlink(*paths: Path) -> None:
    """尽力删除；句柄未释放（Windows）等 OSError 一律忽略。"""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 句柄未释放
            pass


def purge_stale(out_dir: Path, max_age_s: float = STALE_AFTER_S) -> None:
    """清掉历史遗留的中间文件（含 M2 时期留下的 `worker-*.log`）。"""
    now = time.time()
    for pattern in ("request-*.html", "sheet-*.pdf", "error-*.txt",
                    "docx-*.docx", "worker-*.log"):
        for path in out_dir.glob(pattern):
            try:
                if now - path.stat().st_mtime > max_age_s:
                    path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - 句柄仍被占用
                pass


def read_text_quietly(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
