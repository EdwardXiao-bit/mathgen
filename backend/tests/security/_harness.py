"""task-25 的独立测试基座：自建引擎 / 会话 / 客户端 / 造数据。

**刻意不复用** `backend/tests/api/**` 的 fixture（任务书第 3 条），所以这里从产品代码出发
自己装配：`app.db.create_engine` + `init_db` + `app.main.create_app(engine=..., static_dir=False)`
+ `dependency_overrides[get_db]`，与 FastAPI 官方测试姿势一致。

数据落在本包 `artifacts/db-<随机>/sec.db`（不用系统 temp：这台机器上沙箱里的
`tempfile.mkdtemp()` 曾被证明会 EPERM），`tearDownClass` 收工即删。
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
REPO = BACKEND.parent
ARTIFACTS = HERE / "artifacts"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.contracts.api import ROUTES  # noqa: E402
from app.db import enable_sqlite_foreign_keys, get_db, init_db, session_factory  # noqa: E402
from app.main import create_app  # noqa: E402

__all__ = [
    "ARTIFACTS",
    "CONFIG_ADD_SUB",
    "EVIDENCE",
    "REPO",
    "ROUTES",
    "SecurityHarness",
    "create_paper",
    "dump_evidence",
    "practice_body",
    "publish_paper",
    "record",
    "report_student",
    "student_paper_body",
    "submit_attempt",
    "teacher_detail",
]

#: 一份最小可用的出题配置（够生成题目、又不引入额外开关）。
CONFIG_ADD_SUB: dict[str, Any] = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral"}

#: 供 `report_evidence.py` / 测试收集原始证据，最后由 `dump_evidence()` 落盘。
EVIDENCE: list[dict[str, Any]] = []


def record(name: str, **data: Any) -> None:
    EVIDENCE.append({"name": name, **data})


def dump_evidence(path: Path | None = None) -> Path:
    """把收集到的证据落盘（同名条目按最后一次覆盖，便于多模块共用一份文件）。"""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = path or ARTIFACTS / "evidence-task-25.json"
    merged: dict[str, dict[str, Any]] = {}
    if target.exists():
        for entry in json.loads(target.read_text(encoding="utf-8")):
            merged[entry["name"]] = entry
    for entry in EVIDENCE:
        merged[entry["name"]] = entry
    target.write_text(
        json.dumps(list(merged.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def create_paper(
    client: TestClient,
    *,
    count: int = 6,
    seed: int = 20261015,
    title: str = "安全契约验收卷",
) -> int:
    """建一张 draft 卷（老师端 `POST /api/papers`），返回 `paper_id`。"""
    response = client.post(
        f"{ROUTES['create_paper']}?seed={seed}&title={title}",
        json={**CONFIG_ADD_SUB, "count": count},
    )
    if response.status_code != 201:
        raise AssertionError(f"建卷失败 {response.status_code}: {response.text}")
    return int(response.json()["id"])


def publish_paper(client: TestClient, paper_id: int) -> str:
    """发布（冻结）并返回卷号 `code`。"""
    response = client.post(ROUTES["publish_paper"].format(paper_id=paper_id))
    if response.status_code != 200:
        raise AssertionError(f"发布失败 {response.status_code}: {response.text}")
    return str(response.json()["code"])


def student_paper_body(client: TestClient, code: str) -> dict[str, Any]:
    response = client.get(ROUTES["student_paper"].format(code=code))
    if response.status_code != 200:
        raise AssertionError(f"取题失败 {response.status_code}: {response.text}")
    return response.json()


def practice_body(client: TestClient, *, count: int = 6, seed: int = 7) -> dict[str, Any]:
    response = client.post(
        f"{ROUTES['student_practice']}?seed={seed}", json={**CONFIG_ADD_SUB, "count": count}
    )
    if response.status_code != 200:
        raise AssertionError(f"练习出题失败 {response.status_code}: {response.text}")
    return response.json()


def teacher_detail(client: TestClient, paper_id: int) -> dict[str, Any]:
    response = client.get(ROUTES["get_paper"].format(paper_id=paper_id))
    if response.status_code != 200:
        raise AssertionError(f"老师端详情失败 {response.status_code}: {response.text}")
    return response.json()


def report_student(
    client: TestClient,
    *,
    name: str = "验收学生",
    student_no: str = "20261015001",
) -> int:
    """学生报到（`POST /api/students`，D41 姓名+学号），返回 `student_id`。"""
    response = client.post(ROUTES["create_student"], json={"name": name, "student_no": student_no})
    if response.status_code not in (200, 201):
        raise AssertionError(f"报到失败 {response.status_code}: {response.text}")
    return int(response.json()["student_id"])


def submit_attempt(
    client: TestClient,
    *,
    student_id: int,
    attempt_uuid: str,
    answers: list[dict[str, Any]],
    paper_id: int | None = None,
    config: dict[str, Any] | None = None,
    seed: int | None = None,
    duration_ms: int = 1000,
) -> tuple[int, dict[str, Any]]:
    """提交作答（`POST /api/attempts`），返回 `(status_code, json)`。

    返回状态码而不是断言它 —— D76 的幂等重放是 200、首次提交是 201，**状态码本身是判据**。
    """
    body: dict[str, Any] = {
        "student_id": student_id,
        "attempt_uuid": attempt_uuid,
        "answers": answers,
        "duration_ms": duration_ms,
    }
    if paper_id is not None:
        body["paper_id"] = paper_id
    if config is not None:
        body["config"] = config
    if seed is not None:
        body["seed"] = seed
    response = client.post(ROUTES["submit_attempt"], json=body)
    try:
        payload = response.json()
    except ValueError:  # pragma: no cover - 只在产品返回非 JSON 时触发
        payload = {"_raw": response.text}
    return response.status_code, payload


class SecurityHarness(unittest.TestCase):
    """每个测试类一个独立 SQLite 文件 + 自己的 TestClient。"""

    engine: Any
    factory: sessionmaker[Session]
    app: Any
    client: TestClient
    _db_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._db_dir = ARTIFACTS / f"db-{uuid.uuid4().hex[:8]}"
        cls._db_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+pysqlite:///{(cls._db_dir / 'sec.db').as_posix()}"
        cls.engine = enable_sqlite_foreign_keys(
            create_engine(url, connect_args={"check_same_thread": False}, future=True)
        )
        init_db(cls.engine)
        cls.factory = session_factory(cls.engine)
        cls.app = create_app(engine=cls.engine, static_dir=False)

        def _override() -> Iterator[Session]:
            db = cls.factory()
            try:
                yield db
            finally:
                db.close()

        cls.app.dependency_overrides[get_db] = _override
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.engine.dispose()
        shutil.rmtree(cls._db_dir, ignore_errors=True)

    @classmethod
    @contextmanager
    def session(cls) -> Iterator[Session]:
        db = cls.factory()
        try:
            yield db
        finally:
            db.close()
