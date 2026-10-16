"""导出中间文件目录的解析（`app.services.export_common.export_tmp_dir`）。

**这条判据是从一次真实事故里长出来的**：装成 wheel 之后
`BACKEND_DIR = Path(__file__).resolve().parent.parent.parent` 指向
`site-packages`，于是默认目录变成 `/usr/local/lib/python3.12/site-packages/data/exports`，
非 root 用户 `mkdir` 吃 `Errno 13` → PDF 与 Word 导出**双双 503**。
源码树里这条路径永远可写，所以单元测试、api 测试、render 测试**全都发现不了它**；
第一次暴露是在 Docker 冒烟（`docs/00` D82）。换句话说：这不是「忘了一个分支」，
而是**判据只覆盖了开发者的那一种布局**。

`test_installed_layout_must_not_use_the_package_dir` 就是钉住这件事的负对照：
在「不是源码树」的布局下，`SOURCE_TMP` 必须**不再出现**在候选里。谁把探测去掉、
退回无条件用 `BACKEND_DIR/data/exports`，它就会红。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services import export_common


class _EnvIsolated(unittest.TestCase):
    """把两个相关环境变量清干净，避免受跑测试的机器影响。"""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MATHGEN_EXPORT_TMP", "MATHGEN_DATA_DIR"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)


class TestExportTmpDir(_EnvIsolated):
    def test_explicit_override_is_used_verbatim_and_does_not_fall_back(self) -> None:
        target = Path(tempfile.gettempdir()) / "mathgen-test-explicit"
        os.environ["MATHGEN_EXPORT_TMP"] = str(target)
        # 显式指定只给一个候选：配置写错要照实报错，不能悄悄换到别处
        self.assertEqual(export_common.export_tmp_candidates(), [target])
        self.assertEqual(export_common.export_tmp_dir(), target)

    def test_data_dir_wins_over_the_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MATHGEN_DATA_DIR"] = tmp
            self.assertEqual(export_common.export_tmp_dir(), Path(tmp) / "exports")
            self.assertTrue((Path(tmp) / "exports").is_dir(), "应当真的把目录建出来")

    def test_source_checkout_keeps_writing_inside_the_repo(self) -> None:
        """源码树（含 `pip install -e`）行为不能变：还是 `<backend>/data/exports`。"""
        with mock.patch.object(export_common, "_is_source_checkout", return_value=True):
            self.assertEqual(export_common.export_tmp_dir(), export_common.SOURCE_TMP)

    def test_installed_layout_must_not_use_the_package_dir(self) -> None:
        """**负对照**：不是源码树时，`SOURCE_TMP` 必须离开候选。"""
        with tempfile.TemporaryDirectory() as tmp:
            fake_home_tmp = Path(tmp) / "home-exports"
            with mock.patch.object(export_common, "_is_source_checkout", return_value=False), \
                 mock.patch.object(export_common, "HOME_TMP", fake_home_tmp):
                candidates = export_common.export_tmp_candidates()
                self.assertNotIn(export_common.SOURCE_TMP, candidates)
                self.assertEqual(candidates[0], fake_home_tmp)
                # 不只看列表：解析结果真的落到可写的那一个上
                self.assertEqual(export_common.export_tmp_dir(), fake_home_tmp)
                self.assertTrue(fake_home_tmp.is_dir())

    def test_unwritable_candidate_is_skipped(self) -> None:
        """不可写的候选必须被跳过，而不是让导出在 `mkdir` 处炸掉。

        造法：把一个**文件**放在本该是目录的位置上（`mkdir` 必失败），
        系统临时目录那一条也一起指到这个干净目录里，免得污染真实 `/tmp`。
        """
        with tempfile.TemporaryDirectory() as tmp:
            bogus = Path(tmp) / "not-a-dir"
            bogus.write_text("我是文件，不是目录", encoding="utf-8")
            with mock.patch.object(export_common, "_is_source_checkout", return_value=False), \
                 mock.patch.object(export_common, "HOME_TMP", bogus), \
                 mock.patch.object(export_common.tempfile, "gettempdir", return_value=tmp):
                candidates = export_common.export_tmp_candidates()
                self.assertEqual(candidates[0], bogus, "候选里仍然有它")
                self.assertEqual(
                    export_common.export_tmp_dir(),
                    Path(tmp) / "mathgen-exports",
                    "跳过不可写的那一个，落到下一个候选",
                )

    def test_all_unwritable_returns_the_last_candidate_for_the_caller_to_report(self) -> None:
        """全都不行时不抛异常，返回最后一个 —— 报错留在调用方的 `mkdir` 处（消息里带路径）。"""
        with tempfile.TemporaryDirectory() as tmp:
            bogus = Path(tmp) / "not-a-dir"
            bogus.write_text("我是文件，不是目录", encoding="utf-8")
            with mock.patch.object(export_common, "_is_source_checkout", return_value=False), \
                 mock.patch.object(export_common, "HOME_TMP", bogus), \
                 mock.patch.object(export_common.tempfile, "gettempdir", return_value=str(bogus)):
                candidates = export_common.export_tmp_candidates()
                self.assertEqual(export_common.export_tmp_dir(), candidates[-1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
