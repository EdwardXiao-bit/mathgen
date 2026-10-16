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

    python -m app.services.pdf_export_worker <html_path> <pdf_path> [error_path] \
        [--no-reject-overflow]

* 成功：退出码 0，PDF 落在 `pdf_path`；**不产生日志文件**。
* 排版放不下（NFR-5 守卫拒绝）：退出码 3（`EXPORT_LAYOUT_EXIT`），诊断 JSON 写入
  `error_path`（父进程读回后翻成 409 `LAYOUT_DOES_NOT_FIT`）。
* 其他失败：退出码 1，原因写入 `error_path`（若提供）同时打到 stderr。

`--no-reject-overflow` 关掉 NFR-5 守卫（父进程由 `MATHGEN_SKIP_LAYOUT_GUARD` 控制）；
**缺省是开启**，这样子进程 CLI 直接手工调用时也是安全的默认。

刻意**不使用**「父进程开日志文件当子进程 stdout」的写法：子进程里 Playwright 的
Node 驱动会继承该句柄，导致子进程退出后文件仍被占用（实测 `WinError 32`），
每次导出都留下一个删不掉的 0 字节日志。
"""
from __future__ import annotations

import json
import sys
import traceback

__all__ = ["main", "USAGE"]

USAGE = (
    "用法：python -m app.services.pdf_export_worker <html_path> <pdf_path> "
    "[error_path] [--no-reject-overflow]"
)

#: 退出码从父进程模块取，**不**在两边各写一个 3：两个常量一旦漂移，
#: 症状是 409 变成 503（诊断 JSON 被当成环境故障），而且不会有任何测试报错。
from app.services.pdf_export import EXPORT_LAYOUT_EXIT  # noqa: E402

_OPT_NO_REJECT = "--no-reject-overflow"


def _parse_argv(argv: list[str]) -> tuple[list[str], bool]:
    """拆出位置参数与 `--no-reject-overflow`；返回 (位置参数, 是否拒绝溢出)。"""
    positional = [a for a in argv if a != _OPT_NO_REJECT]
    return positional, _OPT_NO_REJECT not in argv


def _write_export_facts(pdf_path: str, html: str, report) -> None:
    """把实测事实写到 `<pdf_path>.json`（伴随文件），供父进程读。

    为什么**总是**写（而不是只在守卫被跳过时写）：父进程要写一条带**实际溢出行数**的
    WARNING，而它手上没有浏览器、量不出来。这份文件是唯一能把行数带出子进程的通道
    （stdout/stderr 被父进程刻意丢弃，理由见模块 docstring）。

    为什么失败**只警告不抛**：导出已经成功了，一份辅助说明没落盘不该让老师拿不到卷子。
    父进程那边同样把「读不到伴随文件」当成可接受的退化（`_read_export_facts`）。
    """
    info_path = f"{pdf_path}.json"
    try:
        from app.export.pdf import layout_of
        from app.layout.fitting import describe_layout
        from app.layout.pagination import detect_overflow

        layout = layout_of(html)
        overflows = (
            detect_overflow(report.measurement, layout) if report.measurement else ()
        )
        payload = {
            "pages": report.pages,
            "layout": describe_layout(layout),
            "layout_fields": {
                "cols_per_row": layout.cols_per_row,
                "row_gap": layout.row_gap,
                "font_size": layout.font_size,
                "page_margin": layout.page_margin,
            },
            "overflows": [
                {
                    "row_index": item.row_index,
                    "height_px": item.height_px,
                    "content_height_px": item.content_height_px,
                    "overflow_px": item.overflow_px,
                }
                for item in overflows
            ],
        }
        with open(info_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError:
        print(f"[pdf_export_worker] 实测事实写不进 {info_path}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - 辅助信息，任何形态的失败都不该毁掉导出
        print(f"[pdf_export_worker] 实测事实未能生成：{type(exc).__name__}: {exc}",
              file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    positional, reject_overflow = _parse_argv(argv)
    if len(positional) not in (2, 3):
        print(USAGE, file=sys.stderr)
        return 2

    html_path, pdf_path = positional[0], positional[1]
    error_path = positional[2] if len(positional) == 3 else None

    from app.layout.fitting import LayoutTooLarge

    try:
        with open(html_path, encoding="utf-8") as handle:
            html = handle.read()
        if not html.strip():
            raise ValueError(f"HTML 文件为空：{html_path}")
        from app.export import export_pdf_report

        report = export_pdf_report(html, pdf_path, reject_overflow=reject_overflow)
        _write_export_facts(pdf_path, html, report)
    except LayoutTooLarge as exc:
        # NFR-5 守卫拒绝：**只**写诊断 JSON（不写 traceback），父进程按退出码 3 识别。
        # 不写 traceback 是为了让 error_path 成为一份可直接解析的数据文件——
        # 这份内容会变成给老师看的提示，混进栈帧只会让解析与文案都变脆。
        payload = exc.to_payload()
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(f"[pdf_export_worker] 排版放不下，拒绝导出：{exc}", file=sys.stderr)
        if error_path:
            try:
                with open(error_path, "w", encoding="utf-8") as handle:
                    handle.write(text)
            except OSError:
                traceback.print_exc()
        return EXPORT_LAYOUT_EXIT
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
