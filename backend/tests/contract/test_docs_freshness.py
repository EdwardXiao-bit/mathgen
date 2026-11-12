# -*- coding: utf-8 -*-
"""`docs/*.md` → `docs/word/*.docx` 新鲜度判据（T-M8-03 / D37 的机器判据）。

## 为什么存在

D37 只规定「`docs/*.md` 是唯一事实来源，`docs/word/*.docx` 由
`tools/build_docs.py` 派生」。规则有了，判据没有：改了 md 忘记重跑构建，仓库里
没有任何东西会变红。M7 终检就撞上过这个静默漂移（T-M8-03：01/02 的 docx 停在
10-14 的初始化提交，md 之后又改过两轮——01 Word 缺整条 FR-T16，02 Word 仍写着
已废弃的草案路由）。

本判据把「docx 是不是用**当前这份 md 的字节**构建的」变成可机器验证的事实：
`build_docs.py` 构建时把源 md 的**字节级 sha256** 写进 docx 的 core properties
（`dc:description`），本文件重算当前 md 的 sha256 并逐字比对。

## 判据读什么

* **docx 侧**：python-docx 打开，读 `core_properties.comments`，按
  `md-sha256=<64 位小写 hex> md=<源 md 文件名>` 解析。格式是两侧共同的契约：
  生产端（`tools/build_docs.py` 的 `SHA256_RE`）与判据端**各自写死**，
  谁单方面改格式都会让本判据红——契约变化必须由人来裁决。
* **md 侧**：`Path.read_bytes()` + `hashlib.sha256`（字节级，不看 mtime、
  不看换行风格、不重新解码文本）。
* **正向**：三份 docx 记录的 sha256 与当前三份源 md 的 sha256 完全相等，
  且记录里的源 md 名对得上。
* **负向**（`TestCriterionCanGoRed`）：把记录值篡改一位（仍是合法 hex）后，
  **同一条比对函数**必须判不一致——证明判据不是「拿文件跟它自己比」的永真式。

## 诚实边界

* 只证明「docx 记录的内容指纹 == 当前 md 字节」；**不证明** Word 排版/分页/目录
  正确（那些靠人工按 F9 与版式验收）。
* `docs/word/*.pdf` **不在**本判据范围内：PDF 由 docx 另经 LibreOffice 导出，
  重建 docx 后 PDF 可能仍是旧的。这是已知遗留，不在这里假装覆盖。
* `DOC_PAIRS` 是冻结清单（与 `test_write_domains.py` 的 `EXPECTED_ROLES` 同风格）：
  新增/改名派生产物必须**同步改本文件**，否则新产物根本不在判据内。只改
  `DOC_JOBS` 不改这里时，本测试会红（读不到 docx，或指纹里的 md 名不符）。

运行：
    cd backend && python -m unittest tests.contract.test_docs_freshness -v
"""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

from docx import Document

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs"
WORD = DOCS / "word"

#: 冻结清单：源 md → 派生 docx。三份课程文档（见 `tools/build_docs.py` 的 `DOC_JOBS`）。
DOC_PAIRS = (
    ("01-需求分析.md", "01-需求分析文档.docx"),
    ("02-技术设计.md", "02-技术设计文档.docx"),
    ("08-测试文档.md", "03-测试文档.docx"),
)

#: 与生产端写入口径逐字一致（**故意不复用**生产端正则）。
MARKER_RE = re.compile(r"md-sha256=([0-9a-f]{64}) md=(\S+)")


def sha256_of(path):
    """源 md 的字节级 sha256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_marker(docx_path):
    """读 docx core properties 里的 (sha256, md_name)；没有标记返回 None。"""
    comments = Document(str(docx_path)).core_properties.comments or ""
    m = MARKER_RE.fullmatch(comments)
    if not m:
        return None
    return m.group(1), m.group(2)


def is_fresh(recorded_sha, expected_sha):
    """判据本体：两个 hex 逐字相等才算新鲜。负对照直接打这一条，证明它有分辨力。"""
    return recorded_sha == expected_sha


def flip_one_hex_char(digest):
    """把 sha256 的最后一位换成另一个合法 hex 字符（负对照用，改动最小）。"""
    return digest[:-1] + ("0" if digest[-1] != "0" else "1")


class _DocsWordMixin:
    """仓库根或 `docs/word` 不存在时跳过：本判据的输入是仓库产物，缺了无从判定。"""

    def setUp(self):
        if not REPO_ROOT.is_dir():
            self.skipTest(f"仓库根不存在：{REPO_ROOT}")
        if not WORD.is_dir():
            self.skipTest(f"派生文档目录不存在：{WORD}")


class TestDocsFreshness(_DocsWordMixin, unittest.TestCase):
    """正向：三份 docx 的指纹 == 当前三份源 md 的字节 sha256。"""

    def test_md_and_docx_pairs_exist(self):
        """缺源 md 或缺 docx 都必须红（`--check` 里对应 MISSING），不能静默跳过。"""
        for md_name, docx_name in DOC_PAIRS:
            with self.subTest(docx=docx_name):
                self.assertTrue((DOCS / md_name).is_file(), f"缺源 md：{md_name}")
                self.assertTrue(
                    (WORD / docx_name).is_file(),
                    f"缺派生 docx：{docx_name}（重跑 tools/build_docs.py）",
                )

    def test_recorded_sha256_matches_source_md(self):
        for md_name, docx_name in DOC_PAIRS:
            with self.subTest(docx=docx_name):
                recorded = recorded_marker(WORD / docx_name)
                self.assertIsNotNone(
                    recorded,
                    f"{docx_name} 未记录 md-sha256（旧产物？重跑 tools/build_docs.py）",
                )
                recorded_sha, recorded_md = recorded
                self.assertEqual(
                    recorded_md,
                    md_name,
                    f"{docx_name} 记录的源 md 名不符：{recorded_md} != {md_name}",
                )
                expected = sha256_of(DOCS / md_name)
                self.assertTrue(
                    is_fresh(recorded_sha, expected),
                    f"{docx_name} 已过期：docx={recorded_sha[:8]} md={expected[:8]}"
                    f"（重跑 tools/build_docs.py）",
                )


class TestCriterionCanGoRed(_DocsWordMixin, unittest.TestCase):
    """负对照（V9）：篡改一位后，同一条比对逻辑必须判定不一致。"""

    def test_tampered_sha256_is_rejected(self):
        for md_name, docx_name in DOC_PAIRS:
            with self.subTest(docx=docx_name):
                recorded = recorded_marker(WORD / docx_name)
                self.assertIsNotNone(recorded, f"{docx_name} 未记录 md-sha256")
                recorded_sha, _ = recorded
                expected = sha256_of(DOCS / md_name)
                tampered = flip_one_hex_char(recorded_sha)

                # 篡改必须真的改掉值、且仍是合法 hex：否则负对照自己失效成永真。
                self.assertNotEqual(tampered, recorded_sha)
                self.assertRegex(tampered, r"^[0-9a-f]{64}$")

                # 同一条判据：真值绿、篡改值红 —— 这才是「有分辨力」。
                self.assertTrue(is_fresh(recorded_sha, expected))
                self.assertFalse(is_fresh(tampered, expected))


if __name__ == "__main__":
    unittest.main()
