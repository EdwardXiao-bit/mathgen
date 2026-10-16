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

import json
import os
import subprocess
import sys
import threading
from typing import Any

from app.services.export_common import (
    BACKEND_DIR,
    best_effort_unlink,
    export_tmp_dir,
    new_token,
    purge_stale,
    read_text_quietly,
)

__all__ = [
    "EXPORT_LAYOUT_EXIT",
    "ExportedPdf",
    "ExportLayoutRejected",
    "ExportUnavailable",
    "LAYOUT_GUARD_ENV",
    "LAYOUT_GUARD_SKIPPED_HEADER",
    "LAYOUT_GUARD_SKIPPED_VALUE",
    "PDF_MAGIC",
    "export_pdf_bytes",
    "export_tmp_dir",
    "export_timeout_seconds",
    "layout_guard_enabled",
]

#: PDF 文件头（测试与自检用）
PDF_MAGIC = b"%PDF"

#: 子进程因**排版放不下**而拒绝导出时的退出码（与「环境坏了」的退出码 1 区分开：
#: 前者是 409 `LAYOUT_DOES_NOT_FIT` + 可照做的诊断，后者是 503 `EXPORT_NOT_READY`）。
EXPORT_LAYOUT_EXIT = 3

#: 关闭 NFR-5 服务端守卫的应急开关（**仅运维用**）。
#:
#: 没有任何正常流程需要它：守卫只有在「这一卷在这个排版下必然有题被跨页切开」时才拒绝，
#: 而那正是 NFR-5 要求拒绝的情形。留这条退路是因为开关在一个环境变量里、
#: 而拒绝是一条**新的失败路径**——真有线上误判时，值班主任能先恢复导出让老师拿得走卷子，
#: 再去查判据（而不是只能改代码重新发版）。
#:
#: ## 值只认精确字面 `1`（D81 硬约束 1）
#:
#: 洞本身不致命，**能被悄悄打开才致命**。`=0` 是最容易出现的写法——有人以为自己在
#: 「显式关闭开关」，实际是在关闭守卫。所以除了字面 `1` 之外的**任何**取值
#: （`0` / `false` / `TRUE` / `yes` / 空串 / `1 ` 带空格）都**不生效**，
#: 守卫保持开启。宁可多拒绝一次，也不要因为一个宽松的读法把 NFR-5 关掉。
LAYOUT_GUARD_ENV = "MATHGEN_SKIP_LAYOUT_GUARD"

#: 守卫被跳过时响应里必须带的头（D81 硬约束 2）。
#: 只写日志不够：判据要能在**不读日志**的情况下区分「守卫生效」与「守卫被跳过」两个世界。
LAYOUT_GUARD_SKIPPED_HEADER = "X-Mathgen-Layout-Guard"
LAYOUT_GUARD_SKIPPED_VALUE = "skipped"


def layout_guard_enabled() -> bool:
    """NFR-5 服务端守卫是否启用。

    只有 `MATHGEN_SKIP_LAYOUT_GUARD` 的取值**逐字节等于** `1` 时返回 `False`，
    其余一切取值（含 `0` / `false` / 空串 / 带空格的 `1`）都返回 `True`。
    理由见 `LAYOUT_GUARD_ENV` 的注释：宽松读法会把守卫悄悄关掉。
    """
    return os.environ.get(LAYOUT_GUARD_ENV) != "1"


class ExportedPdf(bytes):
    """`export_pdf_bytes` 的返回值：**就是** `bytes`，另附本次实测的排版事实。

    为什么用 `bytes` 子类，而不是返回 `(data, report)` 元组或一个小对象：

    * 调用方现有写法全是 `data.startswith(b"%PDF")`、`bytes` 运算、`Response(content=data)`；
    * `tests/api/test_export.py` 会把 `export_pdf_bytes` **整个替换**成返回 `bytes` 的假函数。

    子类让这两边一个字都不用改：需要排版事实的地方用 `getattr(data, "overflows", ())`，
    普通 `bytes` 上取不到 —— 等价于「没有实测事实」，正是假函数应有的语义。

    注意：**不能**加 `__slots__`（`TypeError: nonempty __slots__ not supported for
    subtype of 'bytes'`），所以每个实例带一个 `__dict__`；导出是「打印」动作、
    每次一个实例，这点开销无意义。
    """

    def __new__(
        cls,
        data: bytes,
        *,
        pages: int = 0,
        layout: str = "",
        overflows: Any = (),
        guard_skipped: bool = False,
    ) -> "ExportedPdf":
        self = super().__new__(cls, data)
        self.pages = int(pages)
        self.layout = str(layout)
        self.overflows = tuple(overflows)
        self.guard_skipped = bool(guard_skipped)
        return self

    @property
    def overflow_rows(self) -> tuple[int, ...]:
        """溢出行的行号（**行数**取 `len(...)`）。"""
        return tuple(int(item.get("row_index", -1)) for item in self.overflows)


_EXPORT_LOCK = threading.RLock()

#: 单次导出的超时（秒），可用 MATHGEN_EXPORT_TIMEOUT 覆盖
DEFAULT_TIMEOUT_S = 120.0

#: 子进程入口模块
WORKER_MODULE = "app.services.pdf_export_worker"


class ExportUnavailable(RuntimeError):
    """导出通道不可用（浏览器缺失 / 启动失败 / 超时）——映射为 503。"""


class ExportLayoutRejected(RuntimeError):
    """卷面在当前排版下放不下，子进程拒绝产出（NFR-5）——映射为 409。

    携带子进程送回的诊断字典（`app.layout.fitting.LayoutTooLarge.to_payload()`）：
    路由用它拼面向老师的提示，而不是把子进程的 traceback 甩给前端。
    """

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        self.diagnostic = diagnostic
        message = diagnostic.get("message") or "当前排版放不下这一卷，导出已拒绝"
        super().__init__(message)

    @property
    def layout_description(self) -> str:
        return str(self.diagnostic.get("layout") or "")

    @property
    def rows_over(self) -> tuple[int, ...]:
        return tuple(
            int(item.get("row_index", -1))
            for item in (self.diagnostic.get("overflows") or ())
        )


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
    """HTML → PDF 字节流（子进程隔离；进程内串行）。

    导出前会在子进程里跑 NFR-5 守卫：这一卷在当前排版下**必然**有题目被跨页切开时，
    抛 `ExportLayoutRejected`（调用方翻 409），**不产出任何文件**。
    """
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
    #: 子进程用**同一个**导出产出**实测事实**（页数 / 排版 / 溢出行）。
    #: 为什么走伴随文件而不是改 `export_pdf_bytes` 的签名或让子进程打印到 stdout：
    #: stdout 被刻意丢弃（见 `pdf_export_worker` 的 docstring：父进程开句柄会让
    #: Node 驱动继承，文件删不掉）；而伴随文件的路径双方都能从 `pdf_path` 推出来，
    #: 不需要给 CLI 加参数。**缺了它不算失败**：守卫被跳过时的 WARNING 会退化成
    #: 「跳过但没量到溢出行数」，导出本身照常返回。
    info_path = out_dir / f"sheet-{token}.pdf.json"

    guard_enabled = layout_guard_enabled()

    with _EXPORT_LOCK:                      # 一个 Chromium 实例，串行打印
        purge_stale(out_dir)
        try:
            html_path.write_text(html, encoding="utf-8")
            returncode = _run_worker(
                html_path, pdf_path, error_path, reject_overflow=guard_enabled
            )
            if returncode == EXPORT_LAYOUT_EXIT:
                raise ExportLayoutRejected(_read_layout_diagnostic(error_path))
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
            facts = _read_export_facts(info_path)
            data = ExportedPdf(
                pdf_path.read_bytes(),
                pages=facts.get("pages") or 0,
                layout=str(facts.get("layout") or ""),
                overflows=facts.get("overflows") or (),
                guard_skipped=not guard_enabled,
            )
        finally:
            # **尽力而为**地清理：Windows 上子进程（及其 Playwright 驱动）可能
            # 还短暂持有句柄（实测 WinError 32），此时删不掉也不能让请求失败。
            best_effort_unlink(html_path, pdf_path, error_path, info_path)

    if not data.startswith(PDF_MAGIC):
        raise ExportUnavailable(
            f"导出结果不是 PDF（前 8 字节为 {data[:8]!r}），拒绝返回给前端"
        )
    return data


def _read_export_facts(info_path) -> dict[str, Any]:
    """读回子进程留下的实测事实（页数 / 排版 / 溢出行）。

    **读不到不是错误**：伴随文件只是为了让父进程能写出一条带行数的 WARNING、
    并把页数带进返回值；导出本身早已成功。所以任何异常都退化成空字典，
    绝不把一次成功的导出变成 5xx。
    """
    raw = read_text_quietly(info_path).strip()
    if not raw:
        return {}
    try:
        facts = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(facts, dict):
        return {}
    overflows = facts.get("overflows")
    facts["overflows"] = [
        item for item in overflows if isinstance(item, dict)
    ] if isinstance(overflows, list) else []
    return facts


def _read_layout_diagnostic(error_path) -> dict[str, Any]:
    """读回子进程写的排版诊断（`LayoutTooLarge.to_payload()` 的 JSON）。

    **解不出 JSON 也要有话说**：这条路径的失败已经确定为「排版放不下」，
    所以退化成 `{"message": <原始文本>}` 而不是抛 `ExportUnavailable` ——
    把 409 说成 503 会让老师以为是系统故障、去重试一个必然失败的动作。
    """
    raw = read_text_quietly(error_path).strip()
    if not raw:
        return {"message": "当前排版放不下这一卷，导出已拒绝（子进程未留下诊断）"}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"message": _tail(raw, 800)}
    if not isinstance(payload, dict):
        return {"message": _tail(raw, 800)}
    for key in ("layout_fields", "overflows"):
        if not isinstance(payload.get(key), dict if key == "layout_fields" else list):
            payload.pop(key, None)
    return payload


def _run_worker(html_path, pdf_path, error_path, reject_overflow: bool = True) -> int:
    """执行一次导出子进程；返回退出码。

    子进程的 stdout/stderr 一律丢弃：出错原因由**子进程自己**写 `error_path`
    （见 `pdf_export_worker` 的模块 docstring——父进程开句柄会让 Node 驱动继承，
    导致文件删不掉、每次导出都留垃圾）。

    `reject_overflow` 用**显式参数**传给子进程，不靠环境变量继承：同一个进程里
    「HTTP 导出」与「测试直接调用导出」可能要求不同策略，参数比进程级状态好推理，
    也让子进程 CLI 单独可测。
    """
    env = dict(os.environ)
    # 子进程要用 `-m app.services...`，必须能找到 app 包
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND_DIR), existing]))

    argv = [sys.executable, "-m", WORKER_MODULE,
            str(html_path), str(pdf_path), str(error_path)]
    if not reject_overflow:
        argv.append("--no-reject-overflow")

    try:
        completed = subprocess.run(
            argv,
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
