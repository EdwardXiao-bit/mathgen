"""渲染门禁测试的共用夹具与**独立** HTML 遍历器。

本文件**不是**测试模块（文件名不以 `test_` 开头），unittest 不会收集它。

`_Q_ATTRS` / 遍历器刻意**不复用渲染器的模板**：如果测试也用渲染器的拼串逻辑去断言，
那「QUESTION 不泄露 answer」就成了自证。这里用 `html.parser` 独立解析产出的 HTML，
再按标签/属性/文本递归遍历。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterable

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetItem, SheetMode

#: 只有答案版才允许出现的 class（渲染契约第 3 条 / D20）
ANSWER_CLASSES = (
    "answer-section",
    "answer-heading",
    "a-item",
    "a-answer",
    "a-meta",
    "a-difficulty",
    "a-tag",
    "a-steps",
)

VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "source", "col", "area", "base", "wbr"}


class Node:
    """极简 DOM 节点（够本套测试断言用）。"""

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list["Node"] = []
        self.text = ""

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple((self.attrs.get("class") or "").split())

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def walk(self) -> Iterable["Node"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def find_all(self, tag: str | None = None, cls: str | None = None) -> list["Node"]:
        found = []
        for node in self.walk():
            if tag and node.tag != tag:
                continue
            if cls and not node.has_class(cls):
                continue
            found.append(node)
        return found

    def own_text(self) -> str:
        """只取本节点的直接文本（含 `script`/`style` 之外的子文本）。"""
        parts = [self.text]
        for child in self.children:
            if child.tag not in ("script", "style"):
                parts.append(child.own_text())
        return "".join(parts)

    def attr_text(self) -> str:
        """本节点所有属性值拼起来——防止把答案塞进 `title` / `data-*` 里绕过检查。"""
        return " ".join(f"{k}={v}" for k, v in self.attrs.items())


class _DomBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].text += data


def parse_html(html: str) -> Node:
    """把 HTML 解析成节点树（不执行脚本、不取外部资源）。"""
    builder = _DomBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def build_ctx(
    count: int = 8,
    mode: SheetMode = SheetMode.QUESTION,
    title: str = "三年级数学练习",
    code: str | None = "2610-0001",
    header_note: str = "限时 10 分钟",
    layout: LayoutParams | None = None,
    tag_text: str = "三位数乘两位数·进位",
    answer_salt: str = "",
) -> SheetContext:
    """构造一份**内容可辨识**的卷面，便于断言「答案是否泄露」。"""
    items = tuple(
        SheetItem(
            seq=index + 1,
            display=f"{100 + index} × {7 + index} =",
            answer=f"{index + 1000}{answer_salt}",
            difficulty=(index % 5) + 1,
            tags=(tag_text,),
            steps=(f"步骤A{index + 1}", f"步骤B{index + 1}"),
        )
        for index in range(count)
    )
    return SheetContext(
        title=title,
        mode=mode,
        items=items,
        layout=layout or LayoutParams(),
        code=code,
        header_note=header_note,
    )


def style_block(html: str) -> str:
    """取出 `<style>` 内容（渲染契约第 2 条要检查变量注入）。"""
    match = re.search(r"<style>(.*?)</style>", html, re.S)
    return match.group(1) if match else ""


def css_rule(style: str, selector: str) -> str:
    """取出指定选择器的规则体（不写完整 CSS 解析器，够用即可）。"""
    pattern = re.escape(selector) + r"\s*\{(.*?)\}"
    match = re.search(pattern, style, re.S)
    return match.group(1) if match else ""
