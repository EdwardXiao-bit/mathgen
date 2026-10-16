"""导出通道的公共部分：中间文件目录、命名与清理。

PDF 与 Word 都在「可写目录」里落一个中间文件再读回字节，原因见 `pdf_export`
的模块 docstring（沙箱下子进程写 `%TEMP%` 会 PermissionError）。

**为什么目录要探测可写**：源码树里 `BACKEND_DIR` 是 `<repo>/backend`，
`<repo>/backend/data/exports` 当然写得进去；可**装成 wheel 之后**同一个表达式
指向 `site-packages`，非 root 用户 `mkdir` 直接吃 `Errno 13 Permission denied` ——
PDF 与 Word 导出**双双 503**，而本机跑源码测试永远看不到（第一次被发现是在
Docker 冒烟里，见 `docs/00` D82）。

**为什么成功时零残留**：Windows 上子进程（及其驱动）会继承父进程打开的句柄，
父进程「开日志文件当子进程 stdout」会让文件删不掉（实测 `WinError 32`），
所以约定：**父进程只写输入、只读输出，日志类信息由子进程自己按需写**，
清理一律尽力而为（删不掉也不能让请求失败）。
"""
from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path

__all__ = [
    "BACKEND_DIR",
    "SOURCE_TMP",
    "export_tmp_candidates",
    "export_tmp_dir",
    "new_token",
    "best_effort_unlink",
    "purge_stale",
    "read_text_quietly",
]

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

#: 源码树里的默认落盘目录（`data/` 已被 `.gitignore` 忽略）
SOURCE_TMP = BACKEND_DIR / "data" / "exports"

#: 装成 wheel（非 editable）之后的回退目录：`site-packages` 不可写，家目录总可写
HOME_TMP = Path.home() / ".mathgen" / "exports"

#: 遗留文件的最长存活时间（秒）
STALE_AFTER_S = 3600.0


def _is_source_checkout() -> bool:
    """`BACKEND_DIR` 是不是源码树（旁边有 `pyproject.toml`）。

    **editable 安装必须算源码树**：一键脚本走的是 `pip install -e backend`，
    那时 `__file__` 仍解析回 `<repo>/backend/app/...`，目录照样可写。
    只有 `pip install`（wheel）与容器才落到 `site-packages`。
    """
    return (BACKEND_DIR / "pyproject.toml").is_file()


def export_tmp_candidates() -> list[Path]:
    """按优先级列出候选目录。

    `MATHGEN_EXPORT_TMP` 是**显式指定**：只返回它一个，不做回退 —— 配置写错了
    应该照实报错，而不是悄悄换到别处去。
    """
    override = os.environ.get("MATHGEN_EXPORT_TMP")
    if override:
        return [Path(override)]

    candidates: list[Path] = []
    data_dir = os.environ.get("MATHGEN_DATA_DIR")
    if data_dir:
        # 容器与一键脚本都设它（`__main__.py` 会把它指向数据库所在目录）
        candidates.append(Path(data_dir) / "exports")
    if _is_source_checkout():
        candidates.append(SOURCE_TMP)
    candidates.append(HOME_TMP)
    candidates.append(Path(tempfile.gettempdir()) / "mathgen-exports")
    return candidates


def _writable(path: Path) -> bool:
    """**真的写一次**再删掉 —— 只看路径存在与否判不出来（父目录可能只读）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".probe-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def export_tmp_dir() -> Path:
    """中间文件的落盘目录。

    优先级：`MATHGEN_EXPORT_TMP`（显式，不回退）> `MATHGEN_DATA_DIR/exports`
    > 源码树 `<backend>/data/exports` > `~/.mathgen/exports` > 系统临时目录。
    前面几个都不行时返回最后一个，让调用方在 `mkdir` 处照实报错。
    """
    candidates = export_tmp_candidates()
    if len(candidates) == 1:  # 显式覆盖：不探测、不回退
        return candidates[0]
    for candidate in candidates:
        if _writable(candidate):
            return candidate
    return candidates[-1]



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
