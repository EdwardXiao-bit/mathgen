"""PDF 导出通道：把 `render_html` 产出的 HTML 交给打印通道，回收 PDF 字节。

**只做通道**：不写排版、不改 CSS、不注入样式（C3）。渲染在 `sheet_render`，
打印在 `app.export.export_pdf`，本模块负责「传参 → 拿字节 → 清理」。

## 为什么真的要用子进程（不是过度设计）

`app/export` 的 Playwright **同步** API 是进程级 + 线程亲和的，而 FastAPI 的同步端点
跑在线程池里。本机实测（三组独立实验，见 `pdf_export_worker` 的表格）：

1. 会话在 A 线程创建后，B 线程调用 → `Cannot switch to a different thread`；
2. 从 B 线程 `close()` A 线程的会话**不会**真正停掉 dispatcher，之后
   `sync_playwright().start()` 直接报 `It looks like you are using Playwright Sync API
   inside the asyncio loop` —— **整个进程永久失去 Playwright**；
3. 实测把打印放进一次性子进程后：HTTP 路径连续 5 次导出全部成功（2.4~3.1s/次），
   且子进程导出之后，同进程主线程直接调用 `export_pdf` 仍然可用（双向隔离）。

代价：每次导出多付一次解释器启动（约 +1.5s）。导出是「打印」动作，不是交互热点，
用确定性换这点延迟是划算的。**若将来 `app/export` 把 Playwright 收敛到单一专属线程
（并让 HTTP 与直接调用都走它），本模块可以改回进程内调用。**

## 其他两条硬约束

* **不能写系统临时目录**：本机沙箱下子进程写 `%TEMP%` 会 `PermissionError`。
  中间文件落在仓库内 `backend/data/exports/`（`data/` 已被 `.gitignore` 忽略），
  可用 `MATHGEN_EXPORT_TMP` 覆盖；文件名带 uuid4，并发安全，读完即删
  （导出**成功时不产生任何日志文件**——出错原因由子进程自己写到 `error-<token>.txt`）。
* **串行化**：同一时刻只允许一个导出子进程（一个 Chromium），进程级锁保护。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

from app.services.export_common import (
    BACKEND_DIR,
    best_effort_unlink,
    export_tmp_dir,
    new_token,
    purge_stale,
    read_text_quietly,
)

__all__ = [
    "ExportUnavailable",
    "PDF_MAGIC",
    "export_pdf_bytes",
    "export_tmp_dir",
    "export_timeout_seconds",
]

#: PDF 文件头（测试与自检用）
PDF_MAGIC = b"%PDF"

_EXPORT_LOCK = threading.RLock()

#: 单次导出的超时（秒），可用 MATHGEN_EXPORT_TIMEOUT 覆盖
DEFAULT_TIMEOUT_S = 120.0

#: 子进程入口模块
WORKER_MODULE = "app.services.pdf_export_worker"


class ExportUnavailable(RuntimeError):
    """导出通道不可用（浏览器缺失 / 启动失败 / 超时）——映射为 503。"""


def export_timeout_seconds() -> float:
    """单次导出超时（`MATHGEN_EXPORT_TIMEOUT`，秒）。"""
    raw = os.environ.get("MATHGEN_EXPORT_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_S


def export_pdf_bytes(html: str) -> bytes:
    """HTML → PDF 字节流（子进程隔离；进程内串行）。"""
    if not isinstance(html, str) or not html.strip():
        raise ValueError("html 为空，拒绝导出空 PDF")

    token = new_token()
    out_dir = export_tmp_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - 环境分支
        raise ExportUnavailable(f"导出目录不可写：{out_dir}（{exc}）") from exc

    html_path = out_dir / f"request-{token}.html"
    pdf_path = out_dir / f"sheet-{token}.pdf"
    error_path = out_dir / f"error-{token}.txt"

    with _EXPORT_LOCK:                      # 一个 Chromium 实例，串行打印
        purge_stale(out_dir)
        try:
            html_path.write_text(html, encoding="utf-8")
            returncode = _run_worker(html_path, pdf_path, error_path)
            if returncode != 0:
                raise ExportUnavailable(
                    "PDF 导出子进程失败（这是浏览器/打印环境问题，不是排版问题）："
                    f"退出码 {returncode}；原因尾部：{_tail(read_text_quietly(error_path))}"
                )
            if not pdf_path.exists():
                raise ExportUnavailable(
                    "PDF 导出子进程未产出文件（退出码 0），"
                    f"原因尾部：{_tail(read_text_quietly(error_path))}"
                )
            data = pdf_path.read_bytes()
        finally:
            # **尽力而为**地清理：Windows 上子进程（及其 Playwright 驱动）可能
            # 还短暂持有句柄（实测 WinError 32），此时删不掉也不能让请求失败。
            best_effort_unlink(html_path, pdf_path, error_path)

    if not data.startswith(PDF_MAGIC):
        raise ExportUnavailable(
            f"导出结果不是 PDF（前 8 字节为 {data[:8]!r}），拒绝返回给前端"
        )
    return data


def _run_worker(html_path, pdf_path, error_path) -> int:
    """执行一次导出子进程；返回退出码。

    子进程的 stdout/stderr 一律丢弃：出错原因由**子进程自己**写 `error_path`
    （见 `pdf_export_worker` 的模块 docstring——父进程开句柄会让 Node 驱动继承，
    导致文件删不掉、每次导出都留垃圾）。
    """
    env = dict(os.environ)
    # 子进程要用 `-m app.services...`，必须能找到 app 包
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND_DIR), existing]))

    try:
        completed = subprocess.run(
            [sys.executable, "-m", WORKER_MODULE,
             str(html_path), str(pdf_path), str(error_path)],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=export_timeout_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExportUnavailable(
            f"PDF 导出超时（>{export_timeout_seconds():.0f}s），已终止子进程"
        ) from exc
    except OSError as exc:  # pragma: no cover - 环境分支
        raise ExportUnavailable(f"无法启动导出子进程：{exc}") from exc

    return completed.returncode


def _tail(text: str, limit: int = 600) -> str:
    cleaned = " ".join(text.split())
    return cleaned[-limit:] if len(cleaned) > limit else cleaned
