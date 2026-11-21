"""模式 C（OCR）的磁盘存储：批次目录、占用统计与 2GB 上限（docs/10 §6）。

设计要点：
- **只做目录与字节**，不碰数据库、不碰请求对象——状态机在 `ocr_service`，路由在
  `routers/ocr.py`。这样存储规则（上限、路径校验）可以单独测。
- 目录布局固定为 `<root>/<batch_id>/raw-N.jpg`、`page-N.jpg`、`crop-<seq>.png`；
  `batch_id` 一律 uuid4，**拼接前必须校验格式**（防 `../` 路径穿越）。
- 总占用按**整个根目录**统计（含已提交批次的原图），超过 2GB 时拒绝新上传并提示先清理
  （Q19 定案，见 docs/10 §17.1）。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from app.services.errors import ApiError

__all__ = [
    "BATCH_ID_RE", "TOTAL_LIMIT_BYTES", "ocr_root", "validate_batch_id",
    "batch_dir", "dir_size", "usage_bytes", "ensure_capacity",
    "save_bytes", "read_bytes", "delete_batch_dir", "crop_path",
]

#: uuid4 的字符串形式；`routers/ocr.py` 在拼接任何路径之前用它把关。
BATCH_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

#: 总占用上限：2 GiB（Q19）。超过就拒绝新上传，而不是悄悄删旧批次。
TOTAL_LIMIT_BYTES = 2 * 1024**3

_BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/
_USER_DIR = Path.home() / ".mathgen"


def ocr_root() -> Path:
    """OCR 批次根目录.

    优先级：`MATHGEN_OCR_DIR`（显式）> `MATHGEN_DATA_DIR/ocr`（容器/一键脚本会设）
    > 源码布局 `<backend>/data/ocr` > `~/.mathgen/ocr`。
    """
    override = os.environ.get("MATHGEN_OCR_DIR")
    if override:
        return Path(override).expanduser()
    data_dir = os.environ.get("MATHGEN_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "ocr"
    if (_BACKEND_DIR / "pyproject.toml").is_file():
        return _BACKEND_DIR / "data" / "ocr"
    return _USER_DIR / "ocr"


def validate_batch_id(batch_id: str) -> str:
    """校验 uuid4 格式；不合法按「不存在」处理（不泄露路径规则）."""
    if not BATCH_ID_RE.match(batch_id or ""):
        raise ApiError("OCR_BATCH_NOT_FOUND", "批次不存在")
    return batch_id


def batch_dir(batch_id: str) -> Path:
    return ocr_root() / validate_batch_id(batch_id)


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:  # pragma: no cover - 文件被并发删除
                continue
    return total


def usage_bytes() -> int:
    return dir_size(ocr_root())


def ensure_capacity(extra_bytes: int) -> None:
    """预检上传是否会越过 2GB；越界抛 `OCR_STORAGE_FULL`(409) 并提示清理."""
    if usage_bytes() + max(extra_bytes, 0) > TOTAL_LIMIT_BYTES:
        raise ApiError(
            "OCR_STORAGE_FULL",
            "模式 C 的图片占用已超过 2GB，请先在「识别批次」里清理已完成的批次",
        )


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def delete_batch_dir(batch_id: str) -> None:
    shutil.rmtree(batch_dir(batch_id), ignore_errors=True)


def crop_path(batch_id: str, seq: int) -> Path:
    return batch_dir(batch_id) / f"crop-{int(seq)}.png"
