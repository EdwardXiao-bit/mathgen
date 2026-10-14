# -*- coding: utf-8 -*-
"""mathgen 文档配图工具箱。

用 Pillow 直接绘制，2 倍超采样后缩回，保证插入 Word 缩放后依然清晰。
所有对外坐标都是"逻辑像素"，内部自动乘 S。
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
S = 2  # supersample factor

# 统一配色（浅底深边，打印友好）
C = {
    "ink": "#1f2937",        # 主文字
    "line": "#374151",       # 线条
    "box_fill": "#eef2ff",   # 普通节点
    "box_edge": "#4f46e5",
    "alt_fill": "#ecfdf5",   # 次要节点
    "alt_edge": "#059669",
    "warm_fill": "#fff7ed",  # 强调节点
    "warm_edge": "#ea580c",
    "band_fill": "#f8fafc",  # 分层底
    "band_edge": "#94a3b8",
    "actor": "#111827",
    "uc_fill": "#f5f3ff",
    "uc_edge": "#7c3aed",
    "note_fill": "#fffbeb",
    "note_edge": "#d97706",
}


class Canvas:
    def __init__(self, w, h, bg="#ffffff"):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * S, h * S), bg)
        self.d = ImageDraw.Draw(self.img)
        self._fonts = {}

    # ---------- 基础 ----------
    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, int(size * S))
        return self._fonts[key]

    def textlen(self, s, size, bold=False):
        return self.d.textlength(s, font=self.font(size, bold)) / S

    def _wrap(self, text, size, bold, max_w):
        f = self.font(size, bold)
        out = []
        for para in str(text).split("\n"):
            cur = ""
            for ch in para:
                if self.d.textlength(cur + ch, font=f) <= max_w * S or not cur:
                    cur += ch
                else:
                    out.append(cur)
                    cur = ch
            out.append(cur)
        return out

    def _block(self, cx, cy, lines, size, bold, color, gap=1.32):
        """在 (cx, cy) 居中绘制多行文本"""
        f = self.font(size, bold)
        lh = size * gap * S
        total = lh * len(lines)
        y = cy * S - total / 2 + lh / 2
        for ln in lines:
            self.d.text((cx * S, y), ln, font=f, fill=color, anchor="mm")
            y += lh

    def text(self, x, y, s, size=18, bold=False, color=None, anchor="mm"):
        self.d.text((x * S, y * S), s, font=self.font(size, bold),
                    fill=color or C["ink"], anchor=anchor)

    # ---------- 图形 ----------
    def rect(self, x, y, w, h, fill=None, outline=None, width=2, radius=0):
        box = [x * S, y * S, (x + w) * S, (y + h) * S]
        if radius:
            self.d.rounded_rectangle(box, radius=radius * S, fill=fill,
                                     outline=outline, width=int(width * S))
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=int(width * S))

    def box(self, x, y, w, h, label, size=18, bold=False, fill=None, outline=None,
            radius=8, pad=12, color=None, width=2, gap=1.32):
        """以左上角为基准的矩形节点，文本居中"""
        self.rect(x, y, w, h, fill=fill or C["box_fill"],
                  outline=outline or C["box_edge"], width=width, radius=radius)
        lines = self._wrap(label, size, bold, w - pad * 2)
        self._block(x + w / 2, y + h / 2, lines, size, bold,
                    color or C["ink"], gap=gap)

    def ellipse(self, cx, cy, rx, ry, label, size=18, bold=False, fill=None,
                outline=None, width=2, pad=16, color=None):
        box = [(cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S]
        self.d.ellipse(box, fill=fill or C["uc_fill"], outline=outline or C["uc_edge"],
                       width=int(width * S))
        lines = self._wrap(label, size, bold, rx * 2 - pad * 2)
        self._block(cx, cy, lines, size, bold, color or C["ink"])

    def diamond(self, cx, cy, w, h, label, size=16, fill=None, outline=None, pad=18):
        pts = [(cx * S, (cy - h / 2) * S), ((cx + w / 2) * S, cy * S),
               (cx * S, (cy + h / 2) * S), ((cx - w / 2) * S, cy * S)]
        self.d.polygon(pts, fill=fill or C["warm_fill"], outline=outline or C["warm_edge"],
                       width=int(2 * S))
        lines = self._wrap(label, size, False, (w - pad * 2) * 0.72)
        self._block(cx, cy, lines, size, False, C["ink"])

    def stadium(self, cx, cy, w, h, label, size=18, fill=None, outline=None):
        """圆角胶囊，用于开始/结束"""
        self.rect(cx - w / 2, cy - h / 2, w, h, fill=fill or C["alt_fill"],
                  outline=outline or C["alt_edge"], radius=h / 2, width=2)
        self._block(cx, cy, self._wrap(label, size, True, w - 30), size, True, C["ink"])

    def actor(self, cx, top, label, size=18):
        """火柴人，cx 为水平中心，top 为头顶 y"""
        self.d.ellipse([(cx - 16) * S, top * S, (cx + 16) * S, (top + 32) * S],
                       outline=C["actor"], width=int(2.5 * S))
        self.d.line([(cx * S, (top + 32) * S), (cx * S, (top + 96) * S)],
                    fill=C["actor"], width=int(2.5 * S))
        self.d.line([((cx - 34) * S, (top + 56) * S), ((cx + 34) * S, (top + 56) * S)],
                    fill=C["actor"], width=int(2.5 * S))
        self.d.line([(cx * S, (top + 96) * S), ((cx - 28) * S, (top + 146) * S)],
                    fill=C["actor"], width=int(2.5 * S))
        self.d.line([(cx * S, (top + 96) * S), ((cx + 28) * S, (top + 146) * S)],
                    fill=C["actor"], width=int(2.5 * S))
        self._block(cx, top + 176, [label], size, True, C["ink"])

    # ---------- 连线 ----------
    def line(self, pts, color=None, width=2, dashed=False):
        color = color or C["line"]
        if dashed:
            self._dashed(pts, color, width)
        else:
            self.d.line([(p[0] * S, p[1] * S) for p in pts], fill=color,
                        width=int(width * S), joint="curve")

    def _dashed(self, pts, color, width, dash=9, gap=7):
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            total = math.hypot(x2 - x1, y2 - y1)
            if total == 0:
                continue
            ux, uy = (x2 - x1) / total, (y2 - y1) / total
            pos = 0.0
            while pos < total:
                end = min(pos + dash, total)
                self.d.line([((x1 + ux * pos) * S, (y1 + uy * pos) * S),
                             ((x1 + ux * end) * S, (y1 + uy * end) * S)],
                            fill=color, width=int(width * S))
                pos = end + gap

    def arrow(self, pts, color=None, width=2, dashed=False, head=True,
              label=None, label_size=15, label_at=0.5, label_dx=0, label_dy=-14,
              both=False):
        color = color or C["line"]
        pts = [tuple(p) for p in pts]
        if head:
            self._head(pts[-2], pts[-1], color, width)
            # 缩短线尾，避免箭头与线重叠
            pts = pts[:-1] + [self._shrink(pts[-2], pts[-1], 9)]
        if both:
            self._head(pts[1], pts[0], color, width)
        self.line(pts, color, width, dashed)
        if label:
            p = self._point_at(pts, label_at)
            self.text(p[0] + label_dx, p[1] + label_dy, label,
                      size=label_size, color=C["ink"])

    def _shrink(self, a, b, d):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1
        return (b[0] - dx / L * d, b[1] - dy / L * d)

    def _head(self, a, b, color, width, size=11):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ang = math.atan2(dy, dx)
        p1 = (b[0] - size * math.cos(ang - 0.42), b[1] - size * math.sin(ang - 0.42))
        p2 = (b[0] - size * math.cos(ang + 0.42), b[1] - size * math.sin(ang + 0.42))
        self.d.polygon([(b[0] * S, b[1] * S), (p1[0] * S, p1[1] * S), (p2[0] * S, p2[1] * S)],
                       fill=color)

    def _point_at(self, pts, t):
        segs = []
        total = 0.0
        for i in range(len(pts) - 1):
            L = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            segs.append(L)
            total += L
        want = total * t
        acc = 0.0
        for i, L in enumerate(segs):
            if acc + L >= want or i == len(segs) - 1:
                r = (want - acc) / L if L else 0
                return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * r,
                        pts[i][1] + (pts[i + 1][1] - pts[i][1]) * r)
            acc += L
        return pts[-1]

    def elbow(self, a, b, mode="vh", **kw):
        """直角折线：vh = 先竖后横，hv = 先横后竖"""
        if mode == "vh":
            pts = [a, (a[0], b[1]), b]
        else:
            pts = [a, (b[0], a[1]), b]
        return pts

    def note(self, x, y, w, h, text, size=16, title=None):
        self.rect(x, y, w, h, fill=C["note_fill"], outline=C["note_edge"], radius=6)
        cy = y + h / 2
        if title:
            self.text(x + 14, y + 22, title, size=size, bold=True, anchor="lm")
            lines = self._wrap(text, size, False, w - 28)
            self._block(x + w / 2, cy + 12, lines, size, False, C["ink"])
        else:
            lines = self._wrap(text, size, False, w - 28)
            self._block(x + w / 2, cy, lines, size, False, C["ink"])

    # ---------- 输出 ----------
    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = self.img.resize((self.w, self.h), Image.LANCZOS)
        out.save(path, "PNG")
        return path
