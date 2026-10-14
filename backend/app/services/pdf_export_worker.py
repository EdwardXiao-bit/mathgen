"""PDF 导出**工作进程**（子进程 CLI，契约 §5.1 export 路由的打印通道）。

为什么需要它：`app/export` 用的 Playwright **同步** API 是「进程级 + 线程亲和」的，
而 FastAPI 的同步端点跑在线程池里。本机实测（记录在此，避免后人重走）：

| 场景 | 实测结果 |
| --- | --- |
| 会话已在 A 线程创建，再从 B 线程调用 | `error: Cannot switch to a different thread` |
| 从 B 线程调 `session().close()` 再重建 | 前一个 dispatcher 没被真正停掉 → 之后 `sync_playwright().start()` 报 `It looks like you are using Playwright Sync API inside the asyncio loop`，**整个进程永久不能再起 Playwright**（连原线程也不行） |

也就是说：同一个进程里，只要 HTTP 路径与「直接调用 `export_pdf`」的路径
（本项目 `tests/render`、`tests/e2e_m2` 就是这么调的）交错，就会把渲染导出整体搞坏。
因此**把打印放进一次性子进程**：父进程完全不碰 Playwright，两条路径互不干扰。
实测：子进程导出 5/5 成功，且子进程导出后主线程直接 `export_pdf` 仍可用。

用法（父进程调用，见 `app.services.pdf_export`）：

    python -m app.services.pdf_export_worker <html_path> <pdf_path> [error_path]

* 成功：退出码 0，PDF 落在 `pdf_path`；**不产生日志文件**。
* 失败：退出码非 0，原因写入 `error_path`（若提供）同时打到 stderr。

刻意**不使用**「父进程开日志文件当子进程 stdout」的写法：子进程里 Playwright 的
Node 驱动会继承该句柄，导致子进程退出后文件仍被占用（实测 `WinError 32`），
每次导出都留下一个删不掉的 0 字节日志。
"""
from __future__ import annotations

import sys
import traceback

__all__ = ["main", "USAGE"]

USAGE = "用法：python -m app.services.pdf_export_worker <html_path> <pdf_path> [error_path]"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 3):
        print(USAGE, file=sys.stderr)
        return 2

    html_path, pdf_path = argv[0], argv[1]
    error_path = argv[2] if len(argv) == 3 else None
    try:
        with open(html_path, encoding="utf-8") as handle:
            html = handle.read()
        if not html.strip():
            raise ValueError(f"HTML 文件为空：{html_path}")
        from app.export import export_pdf

        export_pdf(html, pdf_path)
    except BaseException as exc:  # noqa: BLE001 - 子进程边界：一律转成错误文件 + 非 0
        report = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(f"[pdf_export_worker] 导出失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        if error_path:
            try:
                with open(error_path, "w", encoding="utf-8") as handle:
                    handle.write(f"{type(exc).__name__}: {exc}\n\n{report}")
            except OSError:
                traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - 子进程入口
    raise SystemExit(main())
