"""单一服务托管前端产物（M3，M2 欠账）：`uvicorn` 一条命令就能把老师端跑起来。

## 规则（可测，且不"吞掉"后端自己的路由）

| 请求 | 行为 |
| --- | --- |
| `/api/*` | **永不**交给静态托管。命中路由 → 正常响应；未命中 → 404 统一 JSON |
| `/docs`、`/redoc`、`/openapi.json` | FastAPI 内建文档（注册在静态兜底之前，天然优先） |
| `/teacher`、`/teacher/paper/1` | SPA 深链接 → `index.html`（前端是客户端路由） |
| `/` | 入口页：`welcome.html` 存在就给它（欢迎页，学生与老师都从这里进），否则回落 `index.html` |
| `/assets/xxx.js` 等真实文件 | 直接给文件（含正确的 MIME） |
| `/student/*` | `student.html` 若存在则给（M4 拆入口后自动生效），否则回落 `index.html` |
| 路径穿越（`..`、绝对路径） | 404，绝不读出 dist 之外的文件 |
| `dist` 不存在 | **服务照常启动**，只是没有界面（CI / 纯 API 场景） |

## 为什么不用「一个 `/{full_path:path}` 兜底路由」

试过，会**改坏后端自己的语义**：Starlette 先按方法匹配、再决定 404/405，
一个接受 GET 的全捕获路由会让 `POST /api/nonexistent` 变成 405 而不是 404
（路径"存在"了，只是方法不对）。因此这里只为**前端契约里真实存在的前缀**
（`/`、`/teacher/*`、`/student/*`、`/assets/*` 与根目录单段文件）注册兜底，
其余路径保持"没有路由" → 统一 404；已知路由的错方法 → 仍是 405。
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException

__all__ = [
    "ASSETS_PREFIX",
    "INDEX_FILE",
    "RESERVED_PREFIXES",
    "SPA_PREFIXES",
    "STUDENT_ENTRY",
    "STUDENT_PREFIX",
    "TEACHER_PREFIX",
    "WELCOME_ENTRY",
    "default_static_dir",
    "resolve_static_dir",
    "is_mounted",
    "mount_spa",
]

#: 前端产物入口
INDEX_FILE = "index.html"

#: 欢迎页（M8 收尾：`/` 先给它，让装好之后有个入口；不存在则回落 `index.html`）
WELCOME_ENTRY = "welcome.html"

#: 学生端入口（M4 若拆出独立入口，本模块无需改动；不存在则回落 index.html）
STUDENT_ENTRY = "student.html"

#: 这三个前缀归后端自己管，**永远**不走静态兜底
RESERVED_PREFIXES = frozenset({"api", "docs", "redoc", "openapi.json"})

#: 前端路由前缀（契约 §7：老师端全在 `/teacher/*`，学生端全在 `/student/*`）
TEACHER_PREFIX = "teacher"
STUDENT_PREFIX = "student"

#: 静态资源目录
ASSETS_PREFIX = "assets"

#: 需要 SPA 兜底的前缀（其余路径保持 404，不被界面"吞掉"）
SPA_PREFIXES = (TEACHER_PREFIX, STUDENT_PREFIX)

#: Windows 上 `mimetypes` 会读注册表，个别机器把 .js 认成 text/plain；
#: 前端产物用到的几个类型显式钉死，避免「脚本被当纯文本」这类诡异故障。
_MIME_OVERRIDES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".ico": "image/vnd.microsoft.icon",
    ".map": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
}

#: `create_app` 上记录已挂载的目录（供测试与排错查询）
_STATE_KEY = "static_dir"


def default_static_dir() -> Path:
    """默认前端产物目录：`<repo>/frontend/dist`。"""
    # app/services/static_site.py -> backend/app/services -> backend -> repo
    repo = Path(__file__).resolve().parents[3]
    return repo / "frontend" / "dist"


def resolve_static_dir(explicit: object = None) -> Path | None:
    """决定用哪个目录：显式传入 > `MATHGEN_STATIC_DIR` > 默认。

    返回 `None` 表示**不挂载界面**（显式传 `False`，或路径不存在）。
    """
    if explicit is False:
        return None
    if explicit is not None:
        candidate = Path(str(explicit))
    else:
        override = os.environ.get("MATHGEN_STATIC_DIR")
        candidate = Path(override) if override else default_static_dir()
    return candidate if candidate.is_dir() else None


def is_mounted(application: FastAPI) -> bool:
    """应用是否挂了界面（测试与排错用）。"""
    return getattr(application.state, _STATE_KEY, None) is not None


def _media_type(path: Path) -> str | None:
    override = _MIME_OVERRIDES.get(path.suffix.lower())
    if override:
        return override
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed


def _safe_file(directory: Path, relative: str) -> Path | None:
    """把 URL 相对路径映射为 dist 内的真实文件；越界或不存在返回 None。"""
    if not relative or relative.endswith("/"):
        return None
    try:
        candidate = (directory / relative).resolve()
        root = directory.resolve()
    except (OSError, ValueError):  # pragma: no cover - 非法路径
        return None
    if candidate != root and root not in candidate.parents:
        return None          # 路径穿越 / 绝对路径，绝不吐 dist 之外的文件
    return candidate if candidate.is_file() else None


def _file_response(path: Path) -> FileResponse:
    return FileResponse(path, media_type=_media_type(path))


def mount_spa(application: FastAPI, directory: Path | str | None) -> bool:
    """把 `directory` 作为前端产物挂上；目录不存在则**什么都不做**。

    必须在所有 API 路由注册**之后**调用。
    """
    resolved: Path | None = resolve_static_dir(directory)
    if resolved is None:
        return False

    index = resolved / INDEX_FILE
    welcome_entry = resolved / WELCOME_ENTRY
    student_entry = resolved / STUDENT_ENTRY

    def index_response() -> FileResponse:
        if not index.is_file():
            raise HTTPException(status_code=404)
        return _file_response(index)

    def welcome_response() -> FileResponse:
        return _file_response(welcome_entry) if welcome_entry.is_file() else index_response()

    def student_response() -> FileResponse:
        return _file_response(student_entry) if student_entry.is_file() else index_response()

    def resolve_or(relative: str, fallback) -> FileResponse:
        found = _safe_file(resolved, relative)
        return _file_response(found) if found is not None else fallback()

    # ---- 前端路由前缀（客户端路由，深链接一律回落入口页） ----
    @application.get("/", include_in_schema=False, response_class=FileResponse)
    def spa_root() -> FileResponse:  # pragma: no cover - 行为由测试覆盖
        return welcome_response()

    @application.get(f"/{TEACHER_PREFIX}", include_in_schema=False, response_class=FileResponse)
    @application.get(
        f"/{TEACHER_PREFIX}/{{rest:path}}", include_in_schema=False, response_class=FileResponse
    )
    def spa_teacher(rest: str = "") -> FileResponse:  # pragma: no cover
        return resolve_or(rest, index_response)

    @application.get(f"/{STUDENT_PREFIX}", include_in_schema=False, response_class=FileResponse)
    @application.get(
        f"/{STUDENT_PREFIX}/{{rest:path}}", include_in_schema=False, response_class=FileResponse
    )
    def spa_student(rest: str = "") -> FileResponse:  # pragma: no cover
        return resolve_or(rest, student_response)

    # ---- 静态资源（带 hash 的文件；缺失就是 404，绝不回落 HTML） ----
    @application.get(
        f"/{ASSETS_PREFIX}/{{rest:path}}", include_in_schema=False, response_class=FileResponse
    )
    def spa_asset(rest: str) -> FileResponse:  # pragma: no cover
        found = _safe_file(resolved, f"{ASSETS_PREFIX}/{rest}")
        if found is None:
            raise HTTPException(status_code=404)
        return _file_response(found)

    # ---- 根目录单段文件：favicon.ico / robots.txt 等；不存在则回落入口页 ----
    @application.get("/{file_name}", include_in_schema=False, response_class=FileResponse)
    def spa_root_file(file_name: str) -> FileResponse:  # pragma: no cover
        if file_name in RESERVED_PREFIXES:
            raise HTTPException(status_code=404)
        found = _safe_file(resolved, file_name)
        if found is not None:
            return _file_response(found)
        if "." in file_name:          # 明显是文件却不存在 → 404，别拿 HTML 糊弄
            raise HTTPException(status_code=404)
        return index_response()

    application.state.__setattr__(_STATE_KEY, resolved)
    return True

