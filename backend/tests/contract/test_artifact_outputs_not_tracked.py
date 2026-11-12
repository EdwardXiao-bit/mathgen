"""判据：套件每次重写的测量证据 JSON 必须「不入库、被 git 忽略」（遗留 T-M8-09）。

## 这条判据在回答什么

perf / pipeline / security 三个套件各跑一次，合计会重写 17 个曾经入库的测量 JSON。它们的
内容**每次都不一样**（本次 pid、端口、耗时、随机行 id），是测量产物而不是判据；入库只会让
「跑一次全量套件，`git status` 脏一片」。同目录下的 `.py` 探针脚本是源码、一次性探针跑出来
的证据是历史结论，二者都必须继续入库。

## 怎么判

- 17 个路径逐条跑 `git check-ignore -q -- <path>`：0 = 命中忽略规则，1 = 没被忽略，
  其他 = git 自身出错（不当作结论）。
- 同时 `git ls-files --error-unmatch -- <path>` 断言它们**不在索引里**，防「忽略规则在、
  但又被人 `git add -f` 加回去」。
- **不依赖文件是否存在**：`check-ignore` 只做规则匹配，`ls-files` 只读索引，都不看工作区内容。
- git 缺失、或当前目录不是预期的那个 git 仓库时 `skipTest`。

## 负对照（V9：判据必须能红）

与它们同目录、必须继续入库的一次性探针证据（`task48-lock-probe*.json`、`hash-T1.json` 等）
断言 **不** 被忽略、且仍在索引里。谁把规则写成「整目录忽略」，这条负对照立刻变红。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))                     # backend/tests/contract
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))        # 仓库根

#: 被套件每次重写的 17 个测量 JSON（相对仓库根）。改这份清单等于改判据，必须同步 .gitignore。
SUITE_REWRITTEN = (
    # ---- perf：backend/tests/perf/test_concurrent_submit.py ----
    "backend/tests/perf/artifacts/task48-api-vs-database.json",
    "backend/tests/perf/artifacts/task48-concurrent-batch.json",
    "backend/tests/perf/artifacts/task48-doctored-database.json",
    "backend/tests/perf/artifacts/task48-injected-write-lock.json",
    "backend/tests/perf/artifacts/task48-item-integrity.json",
    "backend/tests/perf/artifacts/task48-landed-rows.json",
    "backend/tests/perf/artifacts/task48-measurement-window.json",
    "backend/tests/perf/artifacts/task48-measurement-window-check.json",
    "backend/tests/perf/artifacts/task48-replay-batch.json",
    "backend/tests/perf/artifacts/task48-serial-baseline.json",
    "backend/tests/perf/artifacts/task48-window-guard-negative-control.json",
    # ---- pipeline：backend/tests/pipeline/test_server_log_pipeline.py ----
    "backend/tests/pipeline/artifacts/task50-criterion-attribution.json",
    "backend/tests/pipeline/artifacts/task50-criterion-new-world.json",
    "backend/tests/pipeline/artifacts/task50-criterion-old-world.json",
    "backend/tests/pipeline/artifacts/task50-criterion-opposite-verdicts.json",
    "backend/tests/pipeline/artifacts/task50-product-server-log.json",
    # ---- security：backend/tests/security/*.py 的模块级 tearDownModule ----
    "backend/tests/security/artifacts/evidence-tests.json",
)

#: 负对照：同目录下由**一次性探针**写出、必须继续跟踪的证据（不是套件每次重写的）。
ONE_OFF_PROBE_EVIDENCE = (
    "backend/tests/perf/artifacts/task48-lock-probe.json",
    "backend/tests/perf/artifacts/task48-lock-probe2.json",
    "backend/tests/perf/artifacts/task48-lock-probe3.json",
    "backend/tests/pipeline/artifacts/task50-prefix-world-pipe.json",
    "backend/tests/pipeline/artifacts/task50-fixed-world-boot.json",
    "backend/tests/pipeline/artifacts/task50-stage-nodrain.json",
    "backend/tests/pipeline/artifacts/task50-pipe-scan.json",
    "backend/tests/pipeline/artifacts/task50-regression-summary.txt",
    "backend/tests/security/artifacts/hash-T1.json",
    "backend/tests/security/artifacts/hash-T3.json",
    "backend/tests/security/artifacts/hash-current.json",
    "backend/tests/security/artifacts/evidence-schema-migration.json",
)

#: 同目录下的探针脚本（源码，必须继续入库）。
TRACKED_PROBE_SCRIPTS = (
    "backend/tests/perf/artifacts/task48_lock_probe.py",
    "backend/tests/perf/artifacts/task48_lock_probe2.py",
    "backend/tests/perf/artifacts/task48_lock_probe3.py",
    "backend/tests/perf/artifacts/task48_schema_dump.py",
    "backend/tests/pipeline/artifacts/task50_pipe_matrix.py",
    "backend/tests/pipeline/artifacts/task50_pipe_scan.py",
    "backend/tests/pipeline/artifacts/task50_pipeline_probe.py",
    "backend/tests/pipeline/artifacts/task50_stage_probe.py",
    "backend/tests/security/artifacts/agg_hash.py",
    "backend/tests/security/artifacts/probe_old_db_migration.py",
)


class ArtifactOutputsNotTrackedTest(unittest.TestCase):
    """T-M8-09：套件重写的测量 JSON 不入库，探针脚本与一次性证据继续入库。"""

    git_bin: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        git_bin = shutil.which("git")
        if git_bin is None:
            raise unittest.SkipTest("git 不可用，跳过")
        cls.git_bin = git_bin
        probe = cls._run("rev-parse", "--show-toplevel")
        if probe.returncode != 0:
            raise unittest.SkipTest(f"不是 git 仓库，跳过：{probe.stderr.strip()}")
        top = os.path.realpath(probe.stdout.strip())
        if os.path.normcase(top) != os.path.normcase(os.path.realpath(REPO)):
            raise unittest.SkipTest(f"仓库根与预期不符（{top} != {REPO}），跳过")

    @classmethod
    def _run(cls, *args: str) -> subprocess.CompletedProcess:
        assert cls.git_bin is not None
        return subprocess.run(
            [cls.git_bin, *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _check_ignore(self, rel: str) -> subprocess.CompletedProcess:
        """`git check-ignore -q -- <rel>`；-q 下 0=忽略、1=未忽略、其他=出错。"""
        return self._run("check-ignore", "-q", "--", rel)

    def test_01_suite_rewritten_artifacts_are_ignored_and_untracked(self) -> None:
        # 清单长度本身也是判据：防止有人删空清单让「空集通过」（V11）。
        self.assertEqual(17, len(SUITE_REWRITTEN), "被套件重写的测量 JSON 清单应为 17 条")
        for rel in SUITE_REWRITTEN:
            with self.subTest(path=rel):
                ignored = self._check_ignore(rel)
                self.assertEqual(
                    0,
                    ignored.returncode,
                    f"{rel} 未被 .gitignore 忽略（check-ignore 退出码 {ignored.returncode}）",
                )
                indexed = self._run("ls-files", "--error-unmatch", "--", rel)
                self.assertNotEqual(
                    0,
                    indexed.returncode,
                    f"{rel} 仍被 git 跟踪（应先 `git rm --cached`，且被忽略规则挡住）",
                )

    def test_02_probe_evidence_and_probe_scripts_stay_tracked(self) -> None:
        # 负对照：这些证据/脚本必须「不忽略 + 在索引里」，证明上面的忽略不是整目录一刀切。
        for rel in ONE_OFF_PROBE_EVIDENCE:
            with self.subTest(kind="one-off-evidence", path=rel):
                ignored = self._check_ignore(rel)
                self.assertEqual(
                    1,
                    ignored.returncode,
                    f"{rel} 是一次性探针证据，不应被忽略（check-ignore 退出码 {ignored.returncode}）",
                )
                indexed = self._run("ls-files", "--error-unmatch", "--", rel)
                self.assertEqual(
                    0,
                    indexed.returncode,
                    f"{rel} 是一次性探针证据，必须继续入库",
                )
        for rel in TRACKED_PROBE_SCRIPTS:
            with self.subTest(kind="probe-script", path=rel):
                ignored = self._check_ignore(rel)
                self.assertEqual(
                    1,
                    ignored.returncode,
                    f"{rel} 是探针源码，不应被忽略（check-ignore 退出码 {ignored.returncode}）",
                )
                indexed = self._run("ls-files", "--error-unmatch", "--", rel)
                self.assertEqual(0, indexed.returncode, f"{rel} 是探针源码，必须继续入库")


if __name__ == "__main__":
    unittest.main()
