"""task-50 判据：服务端输出去哪，决定「失败现场」还在不在。

同一条判据（`_pipeline_support.server_log_pipeline_problems`）分别喂给两个世界，结论必须
**相反**：

* `old_pipe_no_reader()` —— 忠实复刻修复前的设施（`Popen(stdout=PIPE)` 且无人读）：
  客户端拿不到响应，失败现场也没了 → **红**；
* `new_file_redirect()` —— 仓库当前的 `boot_uvicorn(log_path=…)`：响应是真状态码，
  日志非空且含本次异常 → **绿**。

旧世界那一半**故意留在仓库里**：判据只有能在旧行为上红，才配在等保（V9/V11）里算证据。
"""
from __future__ import annotations

import os
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))          # backend/tests/pipeline
BACKEND = os.path.dirname(os.path.dirname(HERE))           # backend
REPO = os.path.dirname(BACKEND)                            # mathgen
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.pipeline import _pipeline_support as support                    # noqa: E402

CLIENT_TIMEOUT = 6.0          # 旧世界每次请求都必然超时；6 秒足够判定「响应没来」
BIG = support.BIG_CHARS


class TestTheOldFacilityIsRed(unittest.TestCase):
    """负对照：旧行为下判据必须红（复现卡片报的原始症状）。"""

    def test_a_burst_with_no_reader_starves_the_client_and_loses_the_scene(self):
        world = support.old_pipe_no_reader(big_chars=BIG)
        try:
            observation = support.observe(world, timeout=CLIENT_TIMEOUT)
        finally:
            support.kill_tree(world["process"])

        # 前提：服务真的起来过（否则「超时」只能说明没启动）
        self.assertTrue(observation["ready"], observation)
        self.assertEqual(200, observation["pid_route"]["status"])
        # 写日志的是**哪个进程**：按句柄/自报的 pid 判断，而不是 `Popen.pid`
        self.assertIsInstance(observation["writer_pid"], int, observation)
        self.assertEqual(world["marker"], observation["pid_route"].get("marker"))

        problems = support.server_log_pipeline_problems(observation, label="旧世界")
        path = support.write_evidence("task50-criterion-old-world", {
            **observation, "problems": problems, "verdict": "red"})

        self.assertNotEqual([], problems, f"旧行为竟然没红：{observation}")
        self.assertTrue(any("没拿到响应" in p for p in problems), problems)
        self.assertTrue(self._client_starved(observation),
                        f"旧世界应该两个请求都超时：{observation}")
        self.assertEqual("pipe-after-kill", observation["log_source"])
        print(f"[task-50] 旧世界：problems={len(problems)} 条，日志 {observation['log_bytes']} "
              f"字节，证据 {path}")

    @staticmethod
    def _client_starved(observation: dict) -> bool:
        starved = [r for r in ("boom", "explode")
                   if not isinstance((observation.get(r) or {}).get("status"), int)]
        return len(starved) == 2


class TestTheFixedFacilityIsGreen(unittest.TestCase):
    """修复后：**两件事同时成立**（响应 + 日志），一个不缺。"""

    def test_client_gets_a_real_status_and_the_log_keeps_the_traceback(self):
        world = support.new_file_redirect(big_chars=BIG)
        try:
            observation = support.observe(world, timeout=CLIENT_TIMEOUT)
        finally:
            support.kill_tree(world["process"])

        problems = support.server_log_pipeline_problems(observation, label="新世界")
        support.write_evidence("task50-criterion-new-world", {
            **observation, "problems": problems, "verdict": "green"})

        self.assertTrue(observation["ready"], observation)
        self.assertEqual(200, observation["boom"]["status"],
                         "写了大段输出的那个请求必须拿到响应")
        self.assertEqual(500, observation["explode"]["status"],
                         "真实异常必须回真状态码，而不是把客户端拖到超时")
        self.assertGreater(observation["log_bytes"], 4096, observation)
        self.assertTrue(observation["log_has_boom_marker"], observation)
        self.assertTrue(observation["log_has_traceback"], observation)
        self.assertEqual([], problems)
        self.assertEqual("file", observation["log_source"])
        self.assertTrue(os.path.isfile(observation["log_path"]))


class TestTheSameCriterionGivesOppositeVerdicts(unittest.TestCase):
    """V11：同一条判据、同一个测试里，两个世界的结论必须相反。"""

    def test_red_in_the_old_world_and_green_in_the_new_one(self):
        old = support.old_pipe_no_reader(big_chars=BIG)
        try:
            old_observation = support.observe(old, timeout=CLIENT_TIMEOUT)
        finally:
            support.kill_tree(old["process"])

        new = support.new_file_redirect(big_chars=BIG)
        try:
            new_observation = support.observe(new, timeout=CLIENT_TIMEOUT)
        finally:
            support.kill_tree(new["process"])

        old_problems = support.server_log_pipeline_problems(old_observation, label="旧世界")
        new_problems = support.server_log_pipeline_problems(new_observation, label="新世界")
        support.write_evidence("task50-criterion-opposite-verdicts", {
            "old": {**old_observation, "problems": old_problems},
            "new": {**new_observation, "problems": new_problems}})

        self.assertNotEqual([], old_problems)
        self.assertEqual([], new_problems)


class TestTheLogBelongsToThatVeryProcess(unittest.TestCase):
    """「非空」必须归因到**这一次**那波输出：唯一 marker + 落盘后仍留存。"""

    def test_the_only_marker_in_the_log_is_this_runs(self):
        old_marker = "old-" + uuid.uuid4().hex[:12]
        new_marker = "new-" + uuid.uuid4().hex[:12]

        old = support.old_pipe_no_reader(marker=old_marker, big_chars=BIG)
        try:
            old_observation = support.observe(old, timeout=CLIENT_TIMEOUT)
        finally:
            support.kill_tree(old["process"])

        new = support.new_file_redirect(marker=new_marker, big_chars=BIG,
                                        log_path=os.path.join(
            support.ARTIFACTS, "task50-attribution.log"))
        try:
            new_observation = support.observe(new, timeout=CLIENT_TIMEOUT)
        finally:
            support.kill_tree(new["process"])

        with open(new["log_path"], encoding="utf-8", errors="replace") as handle:
            log = handle.read()
        support.write_evidence("task50-criterion-attribution", {
            "old": {**old_observation, "marker": old_marker},
            "new": {**new_observation, "marker": new_marker}})

        self.assertIn(support.BOOM_LINE + new_marker, log)
        self.assertIn(support.EXPLODE_LINE + new_marker, log)
        self.assertNotIn(old_marker, log, "新世界日志里混进了旧世界那次跑的输出")
        self.assertNotIn(new_marker, old_observation["log_head"],
                         "旧世界的残骸里不该有新世界这一次的 marker")
        # 进程已经死了，文件还在（这正是管道与世界之间的差别）
        self.assertGreater(os.path.getsize(new["log_path"]), 0)


class TestTheProductServerNoLongerUsesAPipe(unittest.TestCase):
    """真设施：`UiServer` 起的产品服务不再留无人读的管道，收尾也读得到自己的日志。"""

    def test_product_server_writes_a_file_that_survives_termination(self):
        from tests.e2e_m4 import _harness
        from tests.perf import _perf_support as perf

        class ChattyUiServer(_harness.UiServer):
            """`--log-level info`，让 uvicorn 一定会往输出写启动横幅。

            不这样做的话，健康跑（`warning`）的日志本来就是空的，「日志非空」就无从断言——
            那正是 task-50 里最容易糊弄过去的地方。
            """

            def _command(self, port):
                return [_harness.VPY, "-m", "uvicorn", "app.main:app",
                        "--app-dir", "backend", "--host", "127.0.0.1",
                        "--port", str(port), "--log-level", "info"]

        server = ChattyUiServer()
        with server:
            self.assertIsNone(server.process.stdout,
                              "产品服务不该再有无人读的管道")
            self.assertEqual(server.server_log_path,
                             server.attempts[-1]["log_path"])
            self.assertIs(200, support.request(server.port, "/api/papers",
                                               timeout=10.0)["status"])
            live = server.read_server_log()
        after = perf.drain_server_log(server)

        support.write_evidence("task50-product-server-log", {
            "server_log_path": server.server_log_path,
            "stdout_is_pipe": server.process.stdout is not None,
            "live_bytes": len(live.encode("utf-8")),
            "after_termination_bytes": len(after.encode("utf-8")),
            "has_startup_banner": "Uvicorn running on" in after,
            "head": after[:200]})

        self.assertIn("Uvicorn running on", live,
                      "产品的启动横幅必须落在文件里")
        self.assertIn("Uvicorn running on", after,
                      "进程结束后日志仍在（管道在那里必然已经丢了）")
        self.assertGreater(os.path.getsize(server.server_log_path), 0)


class TestNoOtherTestFacilityHandsOutAPipe(unittest.TestCase):
    """性质守卫：**起 uvicorn 的地方**不得再把手伸进无人读的管道。

    口径说明：不做「任何地方都不许有 PIPE」那样的一刀切 —— 一次性子进程（输出小、调用方
    自己读）与长期运行的服务是两类风险。这里守的是 task-50 那个缺陷类：**服务会一直写，
    而没人读**。判据同时给出「能红」的直证（喂一段修复前的形状必须被抓到）。
    """

    NEGATIVE_CONTROL_DIR = os.path.join("tests", "pipeline")
    SERVER_HINT = "uvicorn"

    def test_only_the_deliberate_negative_control_creates_pipes(self):
        offenders = []
        unparsed = []
        scanned = 0
        for root, dirs, files in os.walk(os.path.join(BACKEND, "tests")):
            dirs[:] = [d for d in dirs if d not in {"__pycache__"}]
            rel_dir = os.path.relpath(root, BACKEND)
            if rel_dir == self.NEGATIVE_CONTROL_DIR or \
                    rel_dir.startswith(self.NEGATIVE_CONTROL_DIR + os.sep):
                continue
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                # utf-8-sig：仓库里有带 BOM 的脚本（BOM 会让 ast.parse 直接抛 SyntaxError）
                with open(path, encoding="utf-8-sig", errors="replace") as handle:
                    source = handle.read()
                scanned += 1
                rel = os.path.relpath(path, BACKEND)
                try:
                    sites = support.pipe_popen_sites(source, filename=rel)
                except SyntaxError as exc:
                    unparsed.append(f"{rel}: {exc}")
                    continue
                if sites and self.SERVER_HINT in source.lower():
                    offenders.append((rel, sites))

        self.assertEqual([], unparsed, f"这些文件没解析成功，守卫等于漏看了它们：{unparsed}")
        self.assertEqual([], offenders,
                         "这些地方起服务又把输出接进了管道（task-50 修的就是它）："
                         f"{offenders}")
        self.assertGreater(scanned, 20, "扫描范围太小，守卫本身可能失效")

        # 守卫自身的能红直证：把修复前的形状喂给它，必须被抓到
        before = ("import subprocess\n"
                  "process = subprocess.Popen(['python', '-m', 'uvicorn', 'app.main:app'],\n"
                  "                           stdout=subprocess.PIPE,\n"
                  "                           stderr=subprocess.STDOUT)\n")
        self.assertTrue(support.pipe_popen_sites(before, filename="<before>"),
                        "守卫抓不到修复前的形状，等于没有守卫")
        # 而 docstring 里提到 PIPE 不算调用点（AST 口径）
        prose = '"""以前这里用 Popen(stdout=subprocess.PIPE) 起 uvicorn。"""\n'
        self.assertEqual([], support.pipe_popen_sites(prose, filename="<prose>"))

        with open(os.path.join(BACKEND, "tests", "_uvicorn_boot.py"),
                  encoding="utf-8") as handle:
            boot_source = handle.read()
        self.assertEqual([], support.pipe_popen_sites(
            boot_source, filename="tests/_uvicorn_boot.py"),
            "引导模块不得再把输出接进管道（docstring 里提到 PIPE 不算，这里按 AST 判）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
