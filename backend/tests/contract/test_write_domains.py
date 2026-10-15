"""§2 写域表的自动化对账门禁（D72）。

**为什么存在**：写域是并行协作唯一的防覆盖机制（失效模式 F1）。`docs/03-协作规范.md`
§2 的表曾**同时漂移四处**——写了不存在的文件 `backend/app/schemas.py`、不存在的目录
`backend/migrations/**`、不存在的角色名 `be-api`（实际是 `be-paper`）、写错的目录层级
（`tests/e2e/**` 而非 `backend/tests/e2e/**`）——而且**没有一个人发现**，因为没人会去
验证一张表。这张表因此被固化成判据（与 V9 同源：**没有被验证过的约定，和被违反的
约定一样不可靠**）。

**覆盖范围与诚实边界**：
- **路径存在性**：完全可从磁盘判定。`/**` 要求目录存在；`/*` 要求目录存在且非空；
  其余要求文件或目录存在；标注「尚未创建」的路径则**必须不存在**（反例也要能判）。
- **本门禁只看 §2 的表格本身**，不扫散文里的路径提及——那些是**历史记录**。
  例如 §2 尾写着「`backend/migrations/**` 是一个不存在的路径，已删除」，它本来
  就不该存在，若一并扫描会把它误判为失效。
- **角色名**：权威名单在宿主的 `list_agents` 里，单元测试够不到。所以这里**钉住一份
  名册**（`EXPECTED_ROLES`），`doc` 与名册必须**完全相等**。队友新增/改名时要像改
  契约哈希一样**刻意**更新本文件——这正是本门禁想要的效果。
- **写域互斥**：§2 自己声明「一个路径同一时刻只有一个写者」，故两个角色的前缀
  不允许相等或互为前缀。
"""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DOC = REPO_ROOT / "docs" / "03-协作规范.md"

#: 与 `list_agents` 逐字一致的角色名册。**不是**从文档读出来的——故意钉死，
#: 好让「文档与真实队友不一致」这件事必须由人来裁决，而不是自动跟随。
EXPECTED_ROLES = {
    "Lead",
    "eng-engine",
    "eng-grader",
    "be-paper",
    "render-export",
    "fe-teacher",
    "fe-student",
    "docs-build",
    "verify-m1",
}

#: 允许被显式标注为「尚未创建」的角色（尚未 spawn）。其在 §2 里的写域**必须确实不存在**。
ANNOTATED_MISSING = "尚未创建"

_TOKEN_RE = re.compile(r"`([^`]+)`")
_ROLE_CELL_RE = re.compile(r"^\*\*[^*]+\*\*$")


@dataclass(frozen=True)
class Scope:
    """一个写域条目。"""

    spec: str  # 原样（含 `/**` 通配后缀）
    annotated_missing: bool  # 该条目被显式标注「尚未创建」

    @property
    def kind(self) -> str:
        if self.spec.endswith("/**"):
            return "dir"
        if self.spec.endswith("/*"):
            return "dir-nonempty"
        return "any"

    @property
    def base(self) -> str:
        """去掉通配后缀后的仓库相对路径。"""
        if self.spec.endswith("/**"):
            return self.spec[:-3]
        if self.spec.endswith("/*"):
            return self.spec[:-2]
        return self.spec


@dataclass(frozen=True)
class Role:
    name: str
    scopes: tuple[Scope, ...]


def parse_write_domain_table(text: str) -> list[Role]:
    """从 `docs/03-协作规范.md` §2 的 Markdown 表里解析出角色与写域。

    只认「第一列是 `**角色名**`（整格只有这一个粗体词）」的行，表头与分隔行自动跳过。
    """
    roles: list[Role] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if not _ROLE_CELL_RE.match(cells[0]):
            continue

        name = cells[0].strip("*").strip()
        scope_cell = cells[2]

        scopes: list[Scope] = []
        matches = list(_TOKEN_RE.finditer(scope_cell))
        for index, match in enumerate(matches):
            spec = match.group(1)
            # 只看紧跟在 token 之后、下一个 token 之前的那段文字里有没有「尚未创建」。
            tail_start = match.end()
            tail_end = matches[index + 1].start() if index + 1 < len(matches) else len(scope_cell)
            annotated = ANNOTATED_MISSING in scope_cell[tail_start:tail_end]
            scopes.append(Scope(spec=spec, annotated_missing=annotated))

        roles.append(Role(name=name, scopes=tuple(scopes)))
    return roles


def audit_scopes(roles: list[Role], path_state) -> list[str]:
    """逐个写域条目对账。`path_state(base) -> str`，取值见 `disk_path_state`。"""
    problems: list[str] = []
    for role in roles:
        if not role.scopes:
            problems.append(f"[{role.name}] 写域为空——角色必须至少有一个可写前缀")
        for scope in role.scopes:
            state = path_state(scope.base)
            if scope.annotated_missing:
                if state != "missing":
                    problems.append(
                        f"[{role.name}] `{scope.spec}` 被标注为「尚未创建」，"
                        f"但磁盘上确实存在（state={state}）——标注失效"
                    )
                continue
            if state == "missing":
                problems.append(f"[{role.name}] `{scope.spec}` 在磁盘上不存在（state=missing）")
                continue
            if scope.kind == "dir" and state != "dir-nonempty" and state != "dir-empty":
                problems.append(f"[{role.name}] `{scope.spec}` 要求目录，实际是文件（state={state}）")
                continue
            if scope.kind == "dir" and state == "file":
                problems.append(f"[{role.name}] `{scope.spec}` 要求目录，实际是文件")
                continue
            if scope.kind == "dir-nonempty" and state != "dir-nonempty":
                problems.append(
                    f"[{role.name}] `{scope.spec}` 要求**非空**目录，实际 state={state}"
                )
    return problems


def disk_path_state(base: str) -> str:
    """把仓库相对路径映射成 `dir-nonempty` / `dir-empty` / `file` / `missing`。"""
    path = REPO_ROOT / base
    if path.is_dir():
        try:
            return "dir-nonempty" if any(path.iterdir()) else "dir-empty"
        except OSError:
            return "dir-empty"
    if path.is_file():
        return "file"
    return "missing"


def audit_exclusivity(roles: list[Role]) -> list[str]:
    """§2 声明「一个路径同一时刻只有一个写者」——两个角色的前缀不许相等或互为前缀。"""
    problems: list[str] = []
    entries: list[tuple[str, str, str]] = []  # (角色, 原样 spec, base)
    for role in roles:
        for scope in role.scopes:
            entries.append((role.name, scope.spec, scope.base))
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            owner_a, spec_a, base_a = entries[i]
            owner_b, spec_b, base_b = entries[j]
            if owner_a == owner_b:
                continue
            collides = (
                base_a == base_b
                or base_a.startswith(base_b + "/")
                or base_b.startswith(base_a + "/")
            )
            if collides:
                problems.append(f"写域重叠：`{owner_a}` 的 `{spec_a}` 与 `{owner_b}` 的 `{spec_b}`")
    return problems


class TestWriteDomainTable(unittest.TestCase):
    """对 `docs/03-协作规范.md` §2 的真实内容对账。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CONTRACT_DOC.read_text(encoding="utf-8")
        cls.roles = parse_write_domain_table(cls.text)

    def test_parsed_something(self) -> None:
        """防止解析器悄悄退化成「什么都不匹配」——那会让下面每条断言恒真（V9）。"""
        self.assertGreaterEqual(len(self.roles), len(EXPECTED_ROLES))
        total_scopes = sum(len(r.scopes) for r in self.roles)
        self.assertGreaterEqual(total_scopes, 30, "解析出来的写域条目过少，解析器可能坏了")

    def test_roster_matches_pinned_list(self) -> None:
        actual = {r.name for r in self.roles}
        self.assertEqual(
            actual,
            EXPECTED_ROLES,
            "§2 的角色名与钉住的名册不一致。新增/改名队友时请同步更新 EXPECTED_ROLES，"
            "并对照 list_agents 逐字核对。",
        )

    def test_every_scope_exists_on_disk(self) -> None:
        problems = audit_scopes(self.roles, disk_path_state)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_annotated_missing_scopes_really_are_absent(self) -> None:
        annotated = [s for r in self.roles for s in r.scopes if s.annotated_missing]
        self.assertTrue(annotated, "预期至少有一个「尚未创建」的写域（fe-student）")
        for scope in annotated:
            self.assertEqual(
                disk_path_state(scope.base),
                "missing",
                f"`{scope.spec}` 标注了「尚未创建」，但磁盘上存在——标注已失效，请改表",
            )

    def test_scopes_are_mutually_exclusive(self) -> None:
        problems = audit_exclusivity(self.roles)
        self.assertEqual(problems, [], "\n".join(problems))


class TestNegativeControls(unittest.TestCase):
    """V9：每一条判据都要能举出一个让它失败的输入。以下是合成输入，不碰真实文档。"""

    TABLE_HEADER = (
        "| 角色（**= 实际队友名**） | 职责 | 写域（独占） |\n"
        "| --- | --- | --- |\n"
    )

    def _parse(self, body: str) -> list[Role]:
        return parse_write_domain_table(self.TABLE_HEADER + body)

    def test_negative_control_nonexistent_path_is_flagged(self) -> None:
        roles = self._parse("| **fake-role** | x | `backend/app/does-not-exist/**` |\n")
        self.assertEqual(len(roles), 1, "合成表没被解析出来，这条负对照恒真")
        problems = audit_scopes(roles, disk_path_state)
        self.assertTrue(problems, "不存在的路径必须被判红")
        self.assertIn("does-not-exist", "\n".join(problems))

    def test_negative_control_file_where_directory_expected_is_flagged(self) -> None:
        # `backend/app/db.py` 是文件，却声明成目录前缀。
        roles = self._parse("| **fake-role** | x | `backend/app/db.py/**` |\n")
        problems = audit_scopes(roles, disk_path_state)
        self.assertTrue(problems, "把文件声明成目录必须被判红")

    def test_negative_control_annotated_missing_but_present_is_flagged(self) -> None:
        roles = self._parse(
            "| **fake-role** | x | `backend/app/engine/**`（**尚未创建**） |\n"
        )
        self.assertEqual(len(roles), 1)
        self.assertTrue(roles[0].scopes[0].annotated_missing, "标注没被解析出来，负对照恒真")
        problems = audit_scopes(roles, disk_path_state)
        self.assertTrue(problems, "标注「尚未创建」但路径真实存在，必须被判红")

    def test_negative_control_empty_directory_for_nonempty_glob_is_flagged(self) -> None:
        roles = self._parse("| **fake-role** | x | `frontend/src/design/*` |\n")
        problems = audit_scopes(roles, lambda base: "dir-empty")
        self.assertTrue(problems, "`/*` 要求非空目录，空目录必须被判红")

    def test_negative_control_overlapping_scopes_are_flagged(self) -> None:
        roles = self._parse(
            "| **role-a** | x | `backend/app/engine/**` |\n"
            "| **role-b** | x | `backend/app/engine/difficulty.py` |\n"
        )
        self.assertEqual(len(roles), 2, "合成表没被解析出来，这条负对照恒真")
        problems = audit_exclusivity(roles)
        self.assertTrue(problems, "互为前缀的两个写域必须被判红")
        self.assertIn("role-a", "\n".join(problems))

    def test_negative_control_disjoint_scopes_pass(self) -> None:
        """正对照：不重叠的写域不该报错——否则上面的判据可能是因为「总是报错」。"""
        roles = self._parse(
            "| **role-a** | x | `backend/app/engine/**` |\n"
            "| **role-b** | x | `backend/tests/engine/**` |\n"
        )
        self.assertEqual(audit_exclusivity(roles), [])


if __name__ == "__main__":
    unittest.main()
