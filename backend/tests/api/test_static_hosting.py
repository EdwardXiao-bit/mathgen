"""单一服务托管测试（M3，M2 欠账）：`uvicorn` 一条命令跑起老师端。

用**临时 dist 目录**构造（不依赖真实 `frontend/dist` 是否存在），覆盖：
- `/`、`/teacher`、`/teacher/paper/1` → `index.html`（SPA 深链接不能 404）
- `/assets/*.js` → 真文件 + 正确 MIME
- `/api/*` **不被静态托管吞掉**：命中路由照常；未命中 → 统一 404 JSON（不是 index.html）
- `/docs`、`/openapi.json` 仍可用（注册在兜底之前）
- 保留前缀即使 dist 里真有同名文件也不放行
- 路径穿越不吐 dist 之外的文件
- `/student/*`：有 `student.html` 用它，没有就回落 `index.html`
- `dist` 不存在 → 服务照常启动，只是没有界面
- `MATHGEN_STATIC_DIR` 环境变量可覆盖默认目录
"""
from __future__ import annotations

import contextlib
import shutil
import unittest
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.services import static_site
from tests.api._support import ApiTestCase, fresh_engine, make_client, url_of

BACKEND = Path(__file__).resolve().parents[2]
TMP_ROOT = BACKEND / ".tmp-tests"

INDEX_MARK = "MATHGEN_INDEX_MARK"
STUDENT_MARK = "MATHGEN_STUDENT_MARK"


@contextlib.contextmanager
def temp_dist(
    *,
    index: bool = True,
    student: bool = False,
    assets: bool = True,
    extra_files: dict[str, str] | None = None,
) -> Iterator[Path]:
    """在仓库内造一个临时 dist（`backend/.tmp-tests/...`，`.gitignore` 已忽略 `.tmp-*`）。"""
    root = TMP_ROOT / f"dist-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        if index:
            (root / "index.html").write_text(
                f"<!doctype html><html><body>{INDEX_MARK}</body></html>", encoding="utf-8"
            )
        if student:
            (root / "student.html").write_text(
                f"<!doctype html><html><body>{STUDENT_MARK}</body></html>", encoding="utf-8"
            )
        if assets:
            (root / "assets").mkdir()
            (root / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
            (root / "assets" / "app.css").write_text("body{}", encoding="utf-8")
        for relative, text in (extra_files or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class StaticCase(ApiTestCase):
    """基类：把 dist 目录换成临时目录（子类在 `setUp` 里指定）。"""

    def build(self, directory: Path | str | None | bool) -> None:
        self._client_cm.__exit__(None, None, None)
        self._client_cm = make_client(self.engine, static_dir=directory)
        self.client = self._client_cm.__enter__()


class TestSpaHosting(StaticCase):
    def setUp(self) -> None:
        super().setUp()
        self._dist_cm = temp_dist()
        self.dist = self._dist_cm.__enter__()
        self.build(self.dist)

    def tearDown(self) -> None:
        self._dist_cm.__exit__(None, None, None)
        super().tearDown()

    def test_index_is_served_at_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(INDEX_MARK, response.text)
        self.assertIn("text/html", response.headers["content-type"])

    def test_teacher_entry_is_served(self):
        response = self.client.get("/teacher")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(INDEX_MARK, response.text)

    def test_teacher_deep_link_falls_back_to_index(self):
        """`/teacher/paper/1` 必须返回 index.html，而不是 404（前端是客户端路由）。"""
        for path in ("/teacher/paper/1", "/teacher/library", "/teacher/paper/1/publish"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn(INDEX_MARK, response.text)

    def test_assets_are_served_with_correct_mime(self):
        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("javascript", script.headers["content-type"])
        self.assertIn("console.log", script.text)

        style = self.client.get("/assets/app.css")
        self.assertEqual(style.status_code, 200)
        self.assertIn("text/css", style.headers["content-type"])

    def test_real_file_wins_over_index(self):
        with temp_dist(extra_files={"robots.txt": "User-agent: *"}) as dist:
            self.build(dist)
            response = self.client.get("/robots.txt")
            self.assertEqual(response.status_code, 200)
            self.assertIn("User-agent", response.text)

    # ---- 后端自己的路由不能被吞掉 ---------------------------------------

    def test_api_routes_still_work(self):
        response = self.client.get(url_of("list_papers"))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"total", "limit", "offset", "papers"})

    def test_unknown_api_path_is_404_json_not_index(self):
        for path in ("/api/nonexistent", "/api/papers/1/nope", "/api/nonexistent/deep/path"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404, response.text)
                body = response.json()
                self.assertEqual(set(body), {"error"})
                self.assertEqual(body["error"]["code"], "NOT_FOUND")
                self.assertNotIn(INDEX_MARK, response.text)

    def test_post_to_unknown_api_path_is_404_json(self):
        """挂上界面后，`POST /api/nonexistent` 仍必须是 404（不能变成 405/HTML）。

        这正是「不用一个全捕获路由」的原因：Starlette 先按方法匹配，
        全捕获会让未知路径看起来"存在"，把 404 变成 405。
        """
        response = self.client.post("/api/nonexistent", json={})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")
        self.assertNotIn(INDEX_MARK, response.text)

    def test_wrong_method_on_known_route_stays_405(self):
        # publish 是 POST-only；用 GET 打它才是"方法不对"
        response = self.client.get(url_of("publish_paper", paper_id=1))
        self.assertEqual(response.status_code, 405, response.text)
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_unknown_deep_path_is_404_not_index(self):
        """只有契约里的前端前缀才走 SPA 兜底；其余深路径保持 404。"""
        for path in ("/nope/deep/path", "/teacher2/x"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_missing_asset_is_404_not_index(self):
        response = self.client.get("/assets/does-not-exist.js")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn(INDEX_MARK, response.text)

    def test_docs_and_openapi_are_not_swallowed(self):
        for path in ("/docs", "/openapi.json", "/redoc"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn(INDEX_MARK, response.text)

    def test_reserved_prefix_is_blocked_even_if_file_exists_in_dist(self):
        """dist 里就算有 `api/foo.json`，也不能从静态通道吐出来。"""
        with temp_dist(extra_files={"api/foo.json": '{"secret": 1}'}) as dist:
            self.build(dist)
            response = self.client.get("/api/foo.json")
            self.assertEqual(response.status_code, 404, response.text)
            self.assertNotIn("secret", response.text)

    def test_student_falls_back_to_index_without_student_entry(self):
        response = self.client.get("/student/practice")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(INDEX_MARK, response.text)

    # ---- 安全：路径穿越 ------------------------------------------------

    def test_path_traversal_does_not_escape_dist(self):
        secret = self.dist.parent / "secret.txt"
        secret.write_text("TOP_SECRET", encoding="utf-8")
        try:
            # 直接测内部映射（HTTP 客户端会先把 `..` 归一化掉）
            self.assertIsNone(static_site._safe_file(self.dist, "../secret.txt"))
            self.assertIsNone(static_site._safe_file(self.dist, "../../etc/passwd"))
            response = self.client.get("/%2e%2e/secret.txt")
            self.assertNotIn("TOP_SECRET", response.text)
        finally:
            secret.unlink(missing_ok=True)


class TestStudentEntryPreference(StaticCase):
    def test_student_entry_is_used_when_present(self):
        with temp_dist(student=True) as dist:
            self.build(dist)
            student = self.client.get("/student/practice")
            teacher = self.client.get("/teacher/paper/1")
            self.assertEqual(student.status_code, 200, student.text)
            self.assertIn(STUDENT_MARK, student.text)
            self.assertNotIn(INDEX_MARK, student.text)
            self.assertEqual(teacher.status_code, 200)
            self.assertIn(INDEX_MARK, teacher.text)
            self.assertNotIn(STUDENT_MARK, teacher.text)

    def test_student_root_uses_student_entry(self):
        with temp_dist(student=True) as dist:
            self.build(dist)
            self.assertIn(STUDENT_MARK, self.client.get("/student").text)


class TestWelcomeEntry(StaticCase):
    """`/` 先给欢迎页（M8 收尾）：装好之后打开就是这一页，它自己再把人送进老师端/学生端。

    判据问的是「`/` 到底给哪一页」，所以正反两边都钉：
    - 有 `welcome.html` → `/` 是欢迎页，且**不再是** index.html；
    - 没有 `welcome.html`（老产物、CI 的临时 dist）→ `/` 回落 index.html，行为与 M3 一致；
    - 老师端深链接不受影响（欢迎页只占 `/` 这一个位置）。
    """

    WELCOME_MARK = "MATHGEN_WELCOME_MARK"

    def test_root_serves_welcome_page(self):
        with temp_dist(extra_files={"welcome.html": f"<html>{self.WELCOME_MARK}</html>"}) as dist:
            self.build(dist)
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(self.WELCOME_MARK, response.text)
            self.assertNotIn(INDEX_MARK, response.text)

    def test_welcome_page_is_reachable_by_name(self):
        with temp_dist(extra_files={"welcome.html": f"<html>{self.WELCOME_MARK}</html>"}) as dist:
            self.build(dist)
            response = self.client.get("/welcome.html")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(self.WELCOME_MARK, response.text)

    def test_welcome_page_does_not_steal_teacher_or_student(self):
        with temp_dist(
            student=True, extra_files={"welcome.html": f"<html>{self.WELCOME_MARK}</html>"}
        ) as dist:
            self.build(dist)
            teacher = self.client.get("/teacher/paper/1")
            student = self.client.get("/student/practice")
            self.assertIn(INDEX_MARK, teacher.text)
            self.assertNotIn(self.WELCOME_MARK, teacher.text)
            self.assertIn(STUDENT_MARK, student.text)
            self.assertNotIn(self.WELCOME_MARK, student.text)


class TestMissingDist(StaticCase):
    def test_missing_dist_still_starts_and_serves_api(self):
        """dist 不存在 → 服务照常启动（CI / 纯 API 场景），不能启动失败。"""
        self.build(BACKEND / ".tmp-tests" / "definitely-missing")
        self.assertFalse(static_site.is_mounted(self.client.app))

        self.assertEqual(self.client.get(url_of("list_papers")).status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

        response = self.client.get("/teacher/paper/1")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_explicit_opt_out_disables_ui(self):
        with temp_dist() as dist:
            self.build(False)
            self.assertFalse(static_site.is_mounted(self.client.app))
            self.assertEqual(self.client.get("/teacher").status_code, 404)
            self.assertEqual(self.client.get(url_of("list_papers")).status_code, 200)
            del dist  # 仅为表达"即使 dist 存在也不挂"

    def test_env_var_overrides_default_dir(self):
        with temp_dist() as dist:
            import os

            saved = os.environ.get("MATHGEN_STATIC_DIR")
            os.environ["MATHGEN_STATIC_DIR"] = str(dist)
            try:
                self.build(None)          # None → 读环境变量
                self.assertEqual(static_site.is_mounted(self.client.app), True)
                self.assertIn(INDEX_MARK, self.client.get("/teacher").text)
            finally:
                if saved is None:
                    os.environ.pop("MATHGEN_STATIC_DIR", None)
                else:
                    os.environ["MATHGEN_STATIC_DIR"] = saved

    def test_env_var_pointing_nowhere_does_not_break_startup(self):
        import os

        saved = os.environ.get("MATHGEN_STATIC_DIR")
        os.environ["MATHGEN_STATIC_DIR"] = str(BACKEND / ".tmp-tests" / "nowhere")
        try:
            self.build(None)
            self.assertFalse(static_site.is_mounted(self.client.app))
            self.assertEqual(self.client.get(url_of("list_papers")).status_code, 200)
        finally:
            if saved is None:
                os.environ.pop("MATHGEN_STATIC_DIR", None)
            else:
                os.environ["MATHGEN_STATIC_DIR"] = saved


class TestStaticUnit(unittest.TestCase):
    def test_default_static_dir_is_repo_frontend_dist(self):
        default = static_site.default_static_dir()
        self.assertEqual(default.name, "dist")
        self.assertEqual(default.parent.name, "frontend")

    def test_resolve_returns_none_for_missing_dir(self):
        self.assertIsNone(static_site.resolve_static_dir(BACKEND / ".tmp-tests" / "nope"))
        self.assertIsNone(static_site.resolve_static_dir(False))

    def test_reserved_prefixes_cover_api_and_docs(self):
        for prefix in ("api", "docs", "redoc", "openapi.json"):
            self.assertIn(prefix, static_site.RESERVED_PREFIXES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
