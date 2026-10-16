"""task-50 复现与回归用的**合成** ASGI 应用（不是产品代码，也不进生产镜像）。

为什么要它：task-50 要修的是**测试设施**（`_uvicorn_boot` 把子进程输出写进没人读的管道），
不是产品行为。用合成应用能把「服务端一定会产生 >4KB 输出」变成可控前提，不依赖注入数据库
写锁，也不碰 `backend/app/**`（他人写域）。

路由：

* `GET /api/papers` → 200；`http_ready()` 的默认就绪判据靠它。
* `GET /boom` → **先**往 stdout 写 `MATHGEN_PIPELINE_BIG_CHARS` 个字符（默认 30000），
  **再**回 200。这波输出一旦超过管道缓冲、而且**没人读**，旧设施就会把 uvicorn 的事件循环
  堵在 `write()` 上，客户端因此拿不到响应 —— 这就是 task-48 报的假象的可控版本（真实故障里
  uvicorn 也是先写日志、后发 500）。实测本机这级缓冲装得下 ~30KB，所以「4KB」不是要点，
  **「没人读」+「一波输出大于缓冲」**才是。
* `GET /explode` → 抛一条带 `MATHGEN_PIPELINE_TRACE_CHARS` 个字符消息的异常，让 uvicorn
  打出 >4KB 的 traceback（真实故障的形态）。
* `GET /pid` → 本进程的 `pid`/`ppid`；用来证明「谁在写日志」不能按 `Popen.pid` 认。

每一段写到 stdout 的文字都带 `MATHGEN_PIPELINE_MARKER`（探针给的随机串），于是「日志非空」
可以被断言成「**这一条**输出确实到达了」，而不是被别的什么凑出来的。
"""
from __future__ import annotations

import json
import os

MARKER = os.environ.get("MATHGEN_PIPELINE_MARKER", "marker-not-set")
BIG_CHARS = int(os.environ.get("MATHGEN_PIPELINE_BIG_CHARS", "30000"))
TRACE_CHARS = int(os.environ.get("MATHGEN_PIPELINE_TRACE_CHARS", "8000"))


async def _send(send, status: int, body: bytes, content_type: str) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", content_type.encode()),
                    (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


async def app(scope, receive, send):                      # noqa: ANN001 - ASGI 形状
    if scope["type"] != "http":
        return
    path = scope["path"]

    if path == "/api/papers":
        await _send(send, 200, b'{"items":[]}', "application/json")
        return

    if path == "/pid":
        body = json.dumps({"pid": os.getpid(), "ppid": os.getppid(),
                           "marker": MARKER}).encode("utf-8")
        await _send(send, 200, body, "application/json")
        return

    if path == "/boom":
        # 顺序是关键：**先**写日志（此处可能因管道写满而阻塞），**后**发响应。
        # 两句 STAGE 标记让「卡在哪一步」变成可读的证据：
        #   BOOM-LINE 之后仍能读到 AFTER-PRINT → print 没卡；
        #   读不到 AFTER-RESPONSE → 卡在发响应那一步。
        print("BOOM-LINE marker=%s pid=%d payload=%s"
              % (MARKER, os.getpid(), "x" * BIG_CHARS), flush=True)
        print("AFTER-PRINT marker=%s pid=%d" % (MARKER, os.getpid()), flush=True)
        body = json.dumps({"ok": True, "marker": MARKER, "pid": os.getpid()}).encode("utf-8")
        await _send(send, 200, body, "application/json")
        print("AFTER-RESPONSE marker=%s pid=%d" % (MARKER, os.getpid()), flush=True)
        return

    if path == "/explode":
        raise ValueError("EXPLODE-LINE marker=%s %s" % (MARKER, "y" * TRACE_CHARS))

    await _send(send, 404, b"fixture: not found", "text/plain")
