"""task-54（T-M8-08）判据：发行说明里**能复制的命令**必须与真实产物对得上。

背景：第一版发布时这类缺陷连着栽了两次，**两次都没有任何机器判据**，只有人肉眼看：
1. 说明里的镜像写成 `ghcr.io/edwardxiao-bit/mathgen:v1.0.0-rc1`、npm 写成
   `npx mathgen@v1.0.0-rc1` —— 根因是 `release.yml` 把**带 v 的 tag 名**直接替换进模板；
   而镜像标签必须去 v（与同文件 docker push 的 `${GITHUB_REF_NAME#v}` 一致），
   npm 版本号本来就没有 v 前缀。
2. 后来 npm 包名从 `mathgen` 改成 `mathgen-cli`（npm 判定 `mathgen` 与早已存在的
   `math-gen` 太像，发布被 403 挡住），说明模板里的命令却还写着 `npx mathgen@…`。
两次的共同点：**文档里那条命令 ≠ 用户真能跑的命令**，而且失效发生在「模板 → 产物」这一步。

判据形态：契约域按 D43 **只用标准库**，所以不引 PyYAML。做法是把 `release.yml` 里
「生成 release 说明」那一步的 `python3 - <<'PY' … PY` heredoc **抽出来真的执行一遍**，
再审它产出的 `RELEASE_NOTES.md`。判的是**生成出来的结果**，不是模板的字面。

每一组用例回答一个问题（V9/V13/V14/V11 同族）：
- `TestTheCriterionIsGreenOnTheRealWorkflow` —— 真仓库这一跑是绿的，且说明正文真的取自
  CHANGELOG（不是「没匹配到」的兜底文案）；
- `TestTheNpmNameComesFromThePackageManifest` —— 命令里的包名**取自 `npm/package.json`**：
  谁改包名而忘了改说明，就会红（这正是第 2 次事故的形态）；
- `TestTheCriterionGoesRedOnTheStalePackageName` —— 喂回 `npx mathgen@VER`，必须红；
- `TestTheCriterionGoesRedOnTheTagNameBug` —— 喂回「用原始 tag 名替换」，必须红；
- `TestTheCriterionGoesRedWhenThePlaceholderSurvives` —— 漏替换 `VER`，必须红；
- `TestMissingHeredocIsRed` —— 抽不到 heredoc 必须**报错**，不能把空集合当成通过（V11）；
- `TestTheExtractorDistinguishesTheTwoHeredocs` —— 文件里有两个 `python3 - <<'PY'` 块
  （前端轮子检查 / 发行说明），必须选中后者，不能靠「第一个」。

运行：
    & $VPY -m unittest discover -s backend/tests/contract -t backend -v
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "release.yml")
CHANGELOG = os.path.join(REPO, "CHANGELOG.md")
NPM_MANIFEST = os.path.join(REPO, "npm", "package.json")

REF = "v1.0.0-rc3"
VERSION = "1.0.0-rc3"
IMAGE = "ghcr.io/edwardxiao-bit/mathgen"

#: 发行说明里那一节在 CHANGELOG 里的标题（用来确认正文不是兜底文案）。
SECTION_TITLE = f"## [{VERSION}]"

#: 每个 `python3 - <<'PY'` 块都以一行只有 PY 的行结束。
HEREDOC_OPEN = "python3 - <<'PY'"
HEREDOC_CLOSE = "PY"

#: 只有生成发行说明的那一块会写这个文件；用它把两块区分开。
NOTES_MARKER = "RELEASE_NOTES.md"

#: 命令模板里的占位符（`release.yml` 用 `.replace("VER", want)` 替换）。
PLACEHOLDER = "VER"


class ExtractionError(RuntimeError):
    """抽不到发行说明脚本时抛这个 —— 宁可炸，也不要把"空集合"当成通过。"""


def package_name(manifest_path: str = NPM_MANIFEST) -> str:
    """从 npm 包清单里取真实包名。发行说明里的命令必须用它，不能照抄记忆里的名字。"""
    with open(manifest_path, encoding="utf-8") as fh:
        return json.load(fh)["name"]


def _dedent(block: str) -> str:
    """把 YAML 的块缩进整体去掉（不改变三引号字符串内部各行的相对缩进）。"""
    lines = block.split("\n")
    indents = [len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(ln[cut:] for ln in lines)


def extract_notes_script(workflow_text: str) -> str:
    """抽出「生成 release 说明」那一步的 heredoc 正文。

    文件里有不止一个 `python3 - <<'PY'` 块，**不能取第一个**：前一个是核对 wheel 内容的。
    只认正文里出现 `RELEASE_NOTES.md` 的那一块；一块都没有就报错。
    """
    lines = workflow_text.split("\n")
    found: list[str] = []
    i = 0
    while i < len(lines):
        if HEREDOC_OPEN in lines[i]:
            body: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() != HEREDOC_CLOSE:
                body.append(lines[j])
                j += 1
            if j >= len(lines):
                raise ExtractionError(f"第 {i + 1} 行的 heredoc 没有结束标记 {HEREDOC_CLOSE!r}")
            text = "\n".join(body)
            if NOTES_MARKER in text:
                found.append(text)
            i = j + 1
        else:
            i += 1
    if not found:
        raise ExtractionError(f"在 workflow 里找不到写 {NOTES_MARKER} 的 heredoc —— 判据不能静默通过")
    if len(found) > 1:
        raise ExtractionError(f"有 {len(found)} 块 heredoc 都提到 {NOTES_MARKER}，无法确定该审哪一块")
    return _dedent(found[0])


def generate_notes(workflow_text: str, changelog_text: str, ref: str = REF) -> str:
    """真的把那段脚本跑一遍，返回它写出来的 RELEASE_NOTES.md。"""
    script = extract_notes_script(workflow_text)
    with tempfile.TemporaryDirectory(prefix="mathgen-notes-") as tmp:
        with open(os.path.join(tmp, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write(changelog_text)
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            with mock.patch.dict(os.environ, {"GITHUB_REF_NAME": ref}):
                exec(compile(script, "<release-notes-heredoc>", "exec"), {"__name__": "__main__"})
        finally:
            os.chdir(cwd)
        with open(os.path.join(tmp, NOTES_MARKER), encoding="utf-8") as fh:
            return fh.read()


def dist_tag_for(version: str) -> str:
    """这个版本会发到哪个 dist-tag 下：预发布是 `rc`（1.0.0-rc1 → rc），正式版是 `latest`。

    说明里两种引用都合法：精确版本号（照抄就能复现）与 dist-tag（跟着这一档走）。
    """
    if "-" not in version:
        return "latest"
    match = re.match(r"[A-Za-z]+", version.split("-", 1)[1])
    return match.group(0) if match else "latest"


def problems_with(notes: str, name: str) -> list[str]:
    """发行说明里每一条可复制命令的判据。返回问题清单，空列表 = 通过。"""
    found: list[str] = []

    if PLACEHOLDER in notes:
        found.append(f"模板占位符 {PLACEHOLDER} 没被替换，发行说明里留着裸的 {PLACEHOLDER}")

    image = f"{IMAGE}:{VERSION}"
    if re.search(rf"{re.escape(IMAGE)}:v", notes):
        found.append(f"镜像标签带了 v 前缀（镜像必须去 v，要的是 {image}）")
    elif image not in notes:
        found.append(f"发行说明里没有镜像 {image}")

    # npm 命令：包名必须等于 npm/package.json 里的包名；版本引用必须是去 v 的版本号或 dist-tag。
    allowed = {VERSION, dist_tag_for(VERSION)}
    npm_refs = re.findall(r"npx\s+(@?[\w.\-]+(?:/[\w.\-]+)?)(?:@([\w.\-]+))?", notes)
    if not npm_refs:
        found.append("发行说明里没有 npx 命令")
    for pkg, ver in npm_refs:
        if pkg != name:
            found.append(f"npx 的包名是 {pkg}，但 npm/package.json 里的包名是 {name}")
        # `findall` 对「没写版本号」返回空串（不是 None）—— 裸写 `npx <pkg>` 本身合法
        # （正式版上架、`latest` 指向它之后就该这么写），所以只在**写了**版本号时才判。
        if ver and ver not in allowed:
            found.append(f"npx {pkg} 的版本引用是 {ver}，只允许 {' 或 '.join(sorted(allowed))}（不能带 v）")

    exact = f"npx {name}@{VERSION}"
    if exact not in notes:
        found.append(f"发行说明里没有精确版本的命令 `{exact}`；只给 dist-tag 会让人跟着标签漂移")

    return found


class ReleaseNotesCase(unittest.TestCase):
    """公共部分：同一套判据既审真文件，也审故意写坏的副本（负对照）。"""

    @classmethod
    def setUpClass(cls) -> None:
        with open(WORKFLOW, encoding="utf-8") as fh:
            cls.workflow = fh.read()
        with open(CHANGELOG, encoding="utf-8") as fh:
            cls.changelog = fh.read()

    def assert_notes_ok(self, notes: str) -> None:
        """主判据 —— 负对照就靠它变红（V9：负对照必须制造出真实的失败条件）。"""
        self.assertEqual(problems_with(notes, package_name()), [])

    def notes_for(self, workflow_text: str) -> str:
        return generate_notes(workflow_text, self.changelog)


class TestTheCriterionIsGreenOnTheRealWorkflow(ReleaseNotesCase):
    def test_real_workflow_passes(self) -> None:
        notes = self.notes_for(self.workflow)
        self.assert_notes_ok(notes)

    def test_body_really_comes_from_the_changelog(self) -> None:
        """绿不能是"没匹配到所以兜底"造成的 —— 正文必须真是 CHANGELOG 里那一节。"""
        notes = self.notes_for(self.workflow)
        self.assertIn(SECTION_TITLE, notes)
        self.assertNotIn("CHANGELOG.md 里没有这一节", notes)

    def test_real_package_name_is_pinned_by_the_manifest(self) -> None:
        """包名这条判据的输入是 npm/package.json，不是记忆。"""
        name = package_name()
        self.assertTrue(name, "npm/package.json 里的 name 不能为空")
        self.assertIn(f"npx {name}@{VERSION}", self.notes_for(self.workflow))

    def test_dist_tag_reference_is_allowed(self) -> None:
        """说明里同时给精确版本号和 dist-tag（`@rc`），两种都合法，都不该被判为问题。

        这里不再依赖真实 CHANGELOG 的正文——正文会随版本变（rc3 那节就没提 `@rc`），
        判据要审的是**规则**，所以自己造一份只提 dist-tag 的小节来喂它。
        """
        name = package_name()
        tag = dist_tag_for(VERSION)
        synthetic = (
            "## [" + VERSION + "] - 2026-10-28\n\n"
            "装法：`npx " + name + "@" + tag + "`（也可以钉死成 `npx " + name + "@" + VERSION + "`）。\n"
        )
        notes = generate_notes(self.workflow, synthetic)
        self.assertIn(f"npx {name}@{tag}", synthetic)
        self.assert_notes_ok(notes)

    def test_dist_tag_rules(self) -> None:
        self.assertEqual(dist_tag_for("1.0.0-rc1"), "rc")
        self.assertEqual(dist_tag_for("1.0.0"), "latest")


class TestTheVersionConstantsTrackTheNewestChangelogSection(ReleaseNotesCase):
    """REF / VERSION 必须等于 CHANGELOG 里**最新的那个版本小节**。

    每发一版都要改这两个常量；忘了改，判据就会拿着上一版的版本号去审这一版的
    发行说明——仍然全绿，但审的是过期的目标。所以在这里让它直接红。
    """

    def test_constants_match_the_newest_released_section(self) -> None:
        versions = re.findall(r"^## \[([^\]]+)\] - ", self.changelog, re.MULTILINE)
        self.assertTrue(versions, "CHANGELOG 里一个小节都没找到")
        newest, is_prerelease = versions[0], "-" in versions[0]
        self.assertEqual(
            newest,
            VERSION,
            f"CHANGELOG 最新小节是 {newest}，而判据里的 VERSION 还是 {VERSION}；"
            "发版时把 REF / VERSION 一起改掉",
        )
        expected_ref = f"v{newest}" if is_prerelease else f"v{newest}"
        self.assertEqual(REF, expected_ref, f"REF 应写成 {expected_ref}")


class TestTheExtractorDistinguishesTheTwoHeredocs(ReleaseNotesCase):
    def test_first_heredoc_is_not_the_notes_one(self) -> None:
        """文件里两个 heredoc：第一个查 wheel，第二个才是发行说明。取错会静默审错东西。"""
        script = extract_notes_script(self.workflow)
        self.assertIn(NOTES_MARKER, script)
        self.assertNotIn("zipfile", script)

    def test_ordered_extraction_still_finds_the_notes_one(self) -> None:
        """就算把两个块的顺序倒过来，也必须仍选中发行说明那块（不靠"第一个"）。"""
        lines = self.workflow.split("\n")
        blocks: list[list[str]] = []
        i = 0
        while i < len(lines):
            if HEREDOC_OPEN in lines[i]:
                j = i + 1
                while j < len(lines) and lines[j].strip() != HEREDOC_CLOSE:
                    j += 1
                blocks.append(lines[i : j + 1])
                i = j + 1
            else:
                i += 1
        self.assertGreaterEqual(len(blocks), 2, "这里假定 workflow 里至少有两个 heredoc 块")
        swapped = "\n".join(blocks[1] + blocks[0])
        self.assertIn(NOTES_MARKER, extract_notes_script(swapped))


class TestTheNpmNameComesFromThePackageManifest(ReleaseNotesCase):
    def test_a_rename_in_the_manifest_moves_the_criterion(self) -> None:
        """换一份 name 不同的清单，判据期望的包名跟着变（证明它读的是清单，不是常量）。"""
        with tempfile.TemporaryDirectory(prefix="mathgen-manifest-") as tmp:
            other = os.path.join(tmp, "package.json")
            with open(other, "w", encoding="utf-8") as fh:
                json.dump({"name": "some-other-name"}, fh)
            self.assertEqual(package_name(other), "some-other-name")
            self.assertNotEqual(package_name(other), package_name())
            # 用真清单生成的说明，对"别的包名"这个期望必须是**红的**
            self.assertTrue(problems_with(self.notes_for(self.workflow), "some-other-name"))


class TestTheCriterionGoesRedOnTheStalePackageName(ReleaseNotesCase):
    def test_bare_mathgen_is_red(self) -> None:
        """第 2 次事故的形态：包名改了，说明里还是 `npx mathgen@…`。"""
        name = package_name()
        mutant = self.workflow.replace(f"npx {name}@{PLACEHOLDER}", f"npx mathgen@{PLACEHOLDER}")
        self.assertNotEqual(mutant, self.workflow, "负对照没有改到文件，改法要跟着模板走")
        with self.assertRaises(AssertionError):
            self.assert_notes_ok(self.notes_for(mutant))


class TestTheCriterionGoesRedOnTheTagNameBug(ReleaseNotesCase):
    def test_raw_tag_name_is_red(self) -> None:
        """第 1 次事故的形态：拿带 v 的 tag 名去替换，镜像与 npm 版本号都带上 v。"""
        mutant = self.workflow.replace(
            f'""".replace("{PLACEHOLDER}", want)',
            f'""".replace("{PLACEHOLDER}", os.environ["GITHUB_REF_NAME"])',
        )
        self.assertNotEqual(mutant, self.workflow, "负对照没有改到文件，改法要跟着模板走")
        notes = self.notes_for(mutant)
        self.assertIn(f"{IMAGE}:v", notes)
        with self.assertRaises(AssertionError):
            self.assert_notes_ok(notes)

    def test_v_prefixed_npm_version_is_red(self) -> None:
        """同一事故的另一半：镜像改对了、npm 仍带 v —— 也必须红（npm 版本号没有 v 前缀）。"""
        name = package_name()
        mutant = self.workflow.replace(f"npx {name}@{PLACEHOLDER}", f"npx {name}@v{PLACEHOLDER}")
        self.assertNotEqual(mutant, self.workflow)
        notes = self.notes_for(mutant)
        self.assertIn(f"npx {name}@v{VERSION}", notes)
        with self.assertRaises(AssertionError):
            self.assert_notes_ok(notes)


class TestTheCriterionGoesRedWhenThePlaceholderSurvives(ReleaseNotesCase):
    def test_unreplaced_placeholder_is_red(self) -> None:
        mutant = self.workflow.replace(f'""".replace("{PLACEHOLDER}", want)', '"""')
        self.assertNotEqual(mutant, self.workflow, "负对照没有改到文件")
        notes = self.notes_for(mutant)
        self.assertIn(PLACEHOLDER, notes)
        with self.assertRaises(AssertionError):
            self.assert_notes_ok(notes)


class TestMissingHeredocIsRed(ReleaseNotesCase):
    def test_no_heredoc_at_all_is_an_error(self) -> None:
        with self.assertRaises(ExtractionError):
            extract_notes_script("# 什么都没有\n")

    def test_heredoc_without_the_notes_marker_is_an_error(self) -> None:
        """有一块 heredoc，但它不写发行说明 —— 也得报错，不能"抽到了就往下走"。"""
        text = "\n".join([f"          {HEREDOC_OPEN}", "          print('hi')", f"          {HEREDOC_CLOSE}"])
        with self.assertRaises(ExtractionError):
            extract_notes_script(text)

    def test_unterminated_heredoc_is_an_error(self) -> None:
        text = "\n".join([f"          {HEREDOC_OPEN}", "          print('hi')"])
        with self.assertRaises(ExtractionError):
            extract_notes_script(text)


if __name__ == "__main__":
    unittest.main()
