"""契约级守卫：**冻结的 CSS 变量必须真的被渲染器消费**。

**为什么这条测试存在**：M2 交付后，老师端排版面板里「答题横线」与「卷头栏」两个开关
改了以后预览毫无变化，而面板仍显示「排版已保存到后端」——很容易让人以为坏了或没保存。

根因不是渲染错，也不是保存失败，而是**契约与实现脱节**：
契约声明「六个排版参数注入为 CSS 变量」，渲染器也确实把它们写进了 `:root`，
但隐藏逻辑走的是渲染期烘死的 `data-*` 属性，样式表里**一条读这些变量的规则都没有**。
于是面板覆盖变量的动作完全没有任何效果——变量成了装饰品。

这条守卫把「变量必须被消费」变成可执行断言，同类问题不会再悄悄溜过去。

> 本文件由 Lead 维护。它跨了 contract 与 render 两个模块，放在 contract 是有意的：
> 它守的是**契约是否名副其实**，而不是渲染细节。
"""
import re
import unittest

from app.contracts.layout import CSS_VAR_NAMES, LayoutParams, css_variables
from app.contracts.sheet import SheetContext, SheetItem, SheetMode

try:
    from app.render.sheet import render_sheet
except ImportError as exc:  # pragma: no cover - 渲染模块缺失时给出可读失败
    raise AssertionError(
        "无法导入 app.render.sheet.render_sheet —— 该守卫要求渲染模块存在；"
        f"原始错误：{exc}"
    ) from exc


def _sample_context(layout: LayoutParams) -> SheetContext:
    return SheetContext(
        title="契约变量消费守卫",
        mode=SheetMode.QUESTION,
        items=tuple(
            SheetItem(seq=i, display=f"{i} + {i} = ____") for i in range(1, 6)
        ),
        layout=layout,
    )


def _style_block(html: str) -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))


class TestEveryContractVariableIsConsumed(unittest.TestCase):
    def test_stylesheet_reads_every_frozen_variable(self):
        html = render_sheet(_sample_context(LayoutParams()))
        style = _style_block(html)
        self.assertTrue(style.strip(), "卷面 HTML 里没有 <style>，无法校验变量消费")

        missing = []
        for key, var in CSS_VAR_NAMES.items():
            if not re.search(r"var\(\s*" + re.escape(var) + r"\s*[,)]", style):
                missing.append(f"{key} ({var})")

        self.assertEqual(
            missing, [],
            "以下冻结 CSS 变量在渲染器的样式表里**从未被 var() 读取**，"
            "即老师端排版面板改它们不会产生任何视觉效果：\n  - "
            + "\n  - ".join(missing)
            + "\n要么让样式表真正消费它，要么把它从契约里去掉（D59）。",
        )

    def test_variables_carry_the_values_the_stylesheet_expects(self):
        """变量不仅要被引用，还得是样式表能用的形态。"""
        html = render_sheet(_sample_context(LayoutParams(answer_line=False, header_bar=False)))
        style = _style_block(html)
        for key in ("answer_line", "header_bar"):
            var = CSS_VAR_NAMES[key]
            self.assertIn(var, style, f"{var} 应出现在样式表里")
            self.assertIn(f"var({var}", style.replace(" ", ""),
                          f"{var} 应被用作 display 取值")
        # 关掉时必须输出 none，否则 display 取值无效
        self.assertIn("--answer-line-display: none", style.replace("  ", " "))
        self.assertIn("--header-bar-display: none", style.replace("  ", " "))

    def test_css_variables_keys_match_the_frozen_names(self):
        """`css_variables()` 的键集合必须与 `CSS_VAR_NAMES` 完全一致。"""
        self.assertEqual(set(css_variables(LayoutParams())), set(CSS_VAR_NAMES.values()))


if __name__ == "__main__":
    unittest.main()
