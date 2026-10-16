"""task-51（T-M6-08）判据：仓库源文件必须「纯 LF + 无 BOM」。

背景：`backend/tests/render/pdfprobe.py` 曾被写成 CRLF（CR=190/LF=190），只有
`git status` 给了个警告；而 12+ 份报告的证据段都写着「纯 LF / 无 BOM」——
那句话此前从没有任何机器判据撑着。

规则不是我的口味：`.gitattributes:2` 写的是 `* text=auto eol=lf`。

本文件里的每一组用例都在回答一个问题（V9/V13/V14 同族：判据有没有分辨力）：
- `TestTheCriterionCanGoRedOnADirtyTree` —— 同一判据在脏树红且**点名文件**、净树绿；
- `TestParserBlindnessIsRed` —— 扫空目录 / 扫不存在的目录 / 范围内目录缺失，**必须红**；
- `TestNoEmptySetPasses` —— 文件数下限与「0 个文件必须红」（V11）；
- `TestTheRealRepositoryIsGreenAndNotEmpty` —— 真仓库扫到 ≥ 下限个文件且命中 0；
- `TestTheCriterionGoesRedOnARealFileThenRestoresIt` —— 把一个**真文件**改成 CRLF
  （render-export 那次的形态），判据必须点名它，然后还原并复核字节数/哈希回到原值；
- `TestTheRuleComesFromTheRepositoryPolicy` —— 规则与 `.gitattributes` 对齐；
- `TestExclusionsAreJustifiedAndLoadBearing` —— 每条排除都有理由，且在挡真东西
  （`.pylibs` 里确实躺着 CRLF 的 vendored 源文件，它必须不在扫描结果里）。

运行：
    & $VPY -m unittest discover -s backend/tests/contract -t backend -v
"""

from __future__ import annotations

import hashlib
import os
import shutil
import unittest
import uuid

from tests.contract import _text_hygiene_support as support

CRLF = b"x = 1\r\ny = 2\r\n"
BOM = b"\xef\xbb\xbfx = 1\n"
CLEAN = b"x = 1\ny = 2\n"

#: 合成树放在仓库根的隐藏目录下：它不在 include_roots 里，不会污染真仓库那一跑。
TREE_BASE = os.path.join(support.REPO, ".tmp-task51-trees")


def _make_tree(files: dict) -> str:
    tree = os.path.join(TREE_BASE, uuid.uuid4().hex[:10])
    for rel, data in files.items():
        path = os.path.join(tree, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    return tree


def _sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestTheCriterionCanGoRedOnADirtyTree(unittest.TestCase):
    """脏树必须红并点名；净树必须绿。同一条判据，两种结论。"""

    def setUp(self):
        self.trees: list[str] = []

    def tearDown(self):
        for tree in self.trees:
            shutil.rmtree(tree, ignore_errors=True)

    def _scan(self, files: dict, **kwargs):
        tree = _make_tree(files)
        self.trees.append(tree)
        kwargs.setdefault("min_files", 1)
        kwargs.setdefault("include_roots", ("tree",))
        return support.text_hygiene_problems(tree, **kwargs)

    def test_dirty_tree_names_both_offenders_and_the_clean_file_is_silent(self):
        problems, observation = self._scan({
            "tree/dirty_crlf.py": CRLF,
            "tree/dirty_bom.py": BOM,
            "tree/clean.py": CLEAN,
        })

        self.assertEqual(2, len(problems), f"应恰好两条：{problems}")
        self.assertEqual(1, len(observation["cr_files"]),
                         f"只有那一个文件带 CR：{observation['cr_files']}")
        self.assertEqual(1, len(observation["bom_files"]))

        cr_line = [p for p in problems if "dirty_crlf.py" in p]
        self.assertEqual(1, len(cr_line), f"CRLF 那条必须点名文件：{problems}")
        self.assertIn("CR 出现 2 次", cr_line[0])
        self.assertIn("LF 2", cr_line[0])

        bom_line = [p for p in problems if "dirty_bom.py" in p]
        self.assertEqual(1, len(bom_line), f"BOM 那条必须点名文件：{problems}")
        self.assertIn("BOM", bom_line[0])

        self.assertFalse([p for p in problems if "clean.py" in p],
                         f"干净文件不许被点名：{problems}")
        self.assertEqual(3, observation["files"], "三个文件都要被真的读到")

    def test_clean_tree_is_green(self):
        problems, observation = self._scan({
            "tree/clean.py": CLEAN,
            "tree/clean.md": b"# \xe6\xa0\x87\xe9\xa2\x98\n",   # 含中文，仍是合法 UTF-8
        })
        self.assertEqual([], problems)
        self.assertEqual(2, observation["files"])

    def test_a_bom_only_file_is_red_even_without_any_cr(self):
        """BOM 与 CRLF 是两个独立事实，不许被合并成一条。"""
        problems, _ = self._scan({"tree/only_bom.py": BOM})
        self.assertEqual(1, len(problems), problems)
        self.assertIn("BOM", problems[0])
        self.assertNotIn("CR 出现", problems[0])


class TestParserBlindnessIsRed(unittest.TestCase):
    """解析器失明（什么都扫不到）必须红，不许静默通过。"""

    def setUp(self):
        self.trees: list[str] = []

    def tearDown(self):
        for tree in self.trees:
            shutil.rmtree(tree, ignore_errors=True)

    def test_an_empty_directory_is_red(self):
        tree = _make_tree({"tree/.keep": b"x\n"})
        self.trees.append(tree)
        problems, observation = support.text_hygiene_problems(
            tree, include_roots=("tree",), min_files=1)
        self.assertTrue(problems, "空目录竟然绿了")
        self.assertEqual(0, observation["files"])
        self.assertIn("0 个文件", " ".join(problems))

    def test_a_nonexistent_root_is_red(self):
        problems, observation = support.text_hygiene_problems(
            os.path.join(support.REPO, ".tmp-task51-does-not-exist-" + uuid.uuid4().hex[:6]))
        self.assertTrue(problems, "不存在的根竟然绿了")
        self.assertIn("不存在", " ".join(problems))
        self.assertEqual(0, observation["files"])

    def test_a_missing_include_root_is_still_red(self):
        """范围里少了一个目录（改名/搬走）不能当没看见。"""
        tree = _make_tree({"tree/clean.py": CLEAN})
        self.trees.append(tree)
        problems, observation = support.text_hygiene_problems(
            tree, include_roots=("tree", "no_such_dir"), min_files=1)
        self.assertEqual(["no_such_dir"], observation["missing_roots"])
        self.assertTrue([p for p in problems if "no_such_dir" in p], problems)


class TestNoEmptySetPasses(unittest.TestCase):
    """V11：不许空集通过。文件数少于下限必须红。"""

    def setUp(self):
        self.trees: list[str] = []

    def tearDown(self):
        for tree in self.trees:
            shutil.rmtree(tree, ignore_errors=True)

    def test_a_tree_below_the_floor_is_red(self):
        tree = _make_tree({"tree/clean.py": CLEAN})
        self.trees.append(tree)
        problems, observation = support.text_hygiene_problems(
            tree, include_roots=("tree",), min_files=5)
        self.assertEqual(1, observation["files"])
        self.assertEqual(1, len(problems), problems)
        self.assertIn("少于下限 5", problems[0])

    def test_exactly_at_the_floor_is_green(self):
        files = {f"tree/clean_{i}.py": CLEAN for i in range(5)}
        tree = _make_tree(files)
        self.trees.append(tree)
        problems, observation = support.text_hygiene_problems(
            tree, include_roots=("tree",), min_files=5)
        self.assertEqual([], problems)
        self.assertEqual(5, observation["files"])

    def test_a_binary_file_is_skipped_not_counted_and_leaves_the_set_empty(self):
        """二进制按内容（NUL）排除；排完没有文件了，于是仍然必须红。"""
        tree = _make_tree({"tree/blob.py": b"\x00\x01\x02\r\n"})
        self.trees.append(tree)
        problems, observation = support.text_hygiene_problems(
            tree, include_roots=("tree",), min_files=1)
        self.assertEqual([], observation["cr_files"])
        self.assertEqual(["tree/blob.py"], observation["binary_skipped"])
        self.assertIn("0 个文件", " ".join(problems))


class TestTheRealRepositoryIsGreenAndNotEmpty(unittest.TestCase):
    """真仓库那一跑：扫到的文件够多、命中 0 —— 而且真的读到了文件本身。"""

    @classmethod
    def setUpClass(cls):
        cls.problems, cls.observation = support.text_hygiene_problems()

    def test_it_actually_read_real_files(self):
        self.assertGreaterEqual(self.observation["files"], support.MIN_FILES,
                                support.summarise(self.observation))
        scanned = {os.path.relpath(p, support.REPO).replace(os.sep, "/")
                   for p in support.iter_text_files(support.REPO)}
        # 自证：本判据文件自己必须在扫描集合里，否则「扫了 271 个」可能什么也没读到。
        self.assertIn("backend/tests/contract/test_text_hygiene.py", scanned)
        self.assertIn("backend/tests/contract/_text_hygiene_support.py", scanned)
        self.assertIn("docs/06-测试方案.md", scanned)
        self.assertEqual([], self.observation["missing_roots"])

    def test_the_real_repository_is_green(self):
        self.assertEqual([], self.problems, support.summarise(self.observation))
        self.assertEqual([], self.observation["cr_files"])
        self.assertEqual([], self.observation["bom_files"])

    def test_no_undecodable_text_file_is_silently_skipped(self):
        """非 UTF-8 也照样按字节查 CR/BOM，但要把它记进证据，不能悄悄跳过。"""
        self.assertEqual([], self.observation["undecodable"],
                         f"有非 UTF-8 的文本文件：{self.observation['undecodable']}")


class TestTheCriterionGoesRedOnARealFileThenRestoresIt(unittest.TestCase):
    """render-export 那次的真实形态：把一个**真文件**改成 CRLF，判据必须红。

    只碰本判据自己的支撑文件（写域内），走 `finally` 还原，并复核字节数与哈希回到原值。
    """

    TARGET = os.path.join(support.HERE, "_text_hygiene_support.py")

    def test_a_real_crlf_file_is_named_then_restored_byte_for_byte(self):
        with open(self.TARGET, "rb") as handle:
            original = handle.read()
        before_size, before_sha = len(original), _sha256(self.TARGET)
        self.assertEqual(0, original.count(b"\r"), "前置：支撑文件本来就是纯 LF")
        crlf_form = original.replace(b"\n", b"\r\n")
        try:
            with open(self.TARGET, "wb") as handle:
                handle.write(crlf_form)

            problems, observation = support.text_hygiene_problems()
            hit = [p for p in problems if "_text_hygiene_support.py" in p]
            self.assertEqual(1, len(hit), f"改成 CRLF 的真文件必须被点名：{problems}")
            self.assertIn("CR 出现", hit[0])
            self.assertTrue(observation["cr_files"], "证据里要有 CR 计数")
        finally:
            with open(self.TARGET, "wb") as handle:
                handle.write(original)

        after_size, after_sha = os.path.getsize(self.TARGET), _sha256(self.TARGET)
        self.assertEqual(before_size, after_size, "还原后字节数必须回到原值")
        self.assertEqual(before_sha, after_sha, "还原后哈希必须回到原值")
        with open(self.TARGET, "rb") as handle:
            self.assertEqual(0, handle.read().count(b"\r"))

        problems_after, _ = support.text_hygiene_problems()
        self.assertEqual([], problems_after, "还原之后必须重新变绿")


class TestTheRuleComesFromTheRepositoryPolicy(unittest.TestCase):
    """规则要和 `.gitattributes` 对齐，否则只是某个人的偏好。"""

    def test_gitattributes_declares_lf(self):
        path = os.path.join(support.REPO, ".gitattributes")
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertIn(b"* text=auto eol=lf", data)
        self.assertEqual(0, data.count(b"\r"), ".gitattributes 自己也要是 LF")


class TestExclusionsAreJustifiedAndLoadBearing(unittest.TestCase):
    """排除项要逐条有理由，而且要真的在挡东西（不是凭空的清单）。"""

    def test_every_exclusion_has_a_reason(self):
        for name, reason in support.EXCLUDED_DIRS.items():
            self.assertTrue(reason.strip(), f"{name} 没有理由")
        self.assertIsNotNone(support.excluded_dir_reason(".pylibs"))
        self.assertIsNotNone(support.excluded_dir_reason("artifacts"))
        self.assertIsNone(support.excluded_dir_reason("src"), "src 不该被排除")

    def test_excluded_trees_never_show_up_in_the_scan(self):
        scanned = [os.path.relpath(p, support.REPO).replace(os.sep, "/")
                   for p in support.iter_text_files(support.REPO)]
        for marker in ("/node_modules/", "/.venv/", "/artifacts/", "/.pylibs/",
                       "/frontend/dist/", "/__pycache__/"):
            self.assertFalse([p for p in scanned if marker in p],
                             f"排除项没生效：{marker}")

    def test_the_vendored_crlf_file_exists_but_is_excluded(self):
        """`.pylibs` 里确实躺着 CRLF 的第三方源文件 —— 排除项正在挡真东西。"""
        vendored = os.path.join(
            support.REPO, "backend", "tests", "e2e_m2", ".pylibs", "pypdfium2",
            "_helpers", "document.py")
        if not os.path.isfile(vendored):
            self.skipTest("vendored 目录不在本机，跳过这条只读旁证")
        with open(vendored, "rb") as handle:
            self.assertGreater(handle.read().count(b"\r"), 0,
                               "前置：vendored 文件本来就是 CRLF")
        scanned = {os.path.relpath(p, support.REPO).replace(os.sep, "/")
                   for p in support.iter_text_files(support.REPO)}
        self.assertNotIn(
            "backend/tests/e2e_m2/.pylibs/pypdfium2/_helpers/document.py", scanned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
