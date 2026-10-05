# -*- coding: utf-8 -*-
"""主题调色板 WCAG 对比度审计：逐主题计算文字/背景、强调按钮等关键配对

说明：
- 强调按钮上的文字色是运行时按 accent 亮度自适应计算的 accent_fg（深墨或白），
  不再固定白色，因此按钮配对用 with_computed_colors 的结果审计。
- accent vs 页面底/侧栏底只影响图标和链接（装饰性图形），低于 3:1 记为 WARN，
  不计入 FAIL。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.writing_editor import WRITING_THEMES, with_computed_colors


def lum(hex_c: str) -> float:
    h = hex_c.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    R, G, B = f(r), f(g), f(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def cr(a: str, b: str) -> float:
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# (取值方式, 前景键, 背景键, 最小对比度, 说明, 是否仅 WARN)
PAIRS = [
    ("raw", "text", "editor_bg", 4.5, "正文文字 vs 正文纸", False),
    ("raw", "text", "bg", 4.5, "正文文字 vs 页面底", False),
    ("raw", "text", "sidebar", 4.5, "正文文字 vs 侧栏底", False),
    ("raw", "muted", "editor_bg", 3.0, "辅助文字 vs 正文纸（小字下限3:1）", False),
    ("raw", "muted", "bg", 3.0, "辅助文字 vs 页面底", False),
    ("raw", "muted", "sidebar", 3.0, "辅助文字 vs 侧栏底", False),
    ("raw", "caret", "editor_bg", 3.0, "光标色 vs 正文纸", False),
    ("computed", "accent_fg", "accent", 3.0, "强调按钮文字（自适应）vs 按钮底", False),
    ("raw", "accent", "bg", 3.0, "强调色 vs 页面底（图标/链接，装饰性）", True),
    ("raw", "accent", "sidebar", 3.0, "强调色 vs 侧栏底（图标/链接，装饰性）", True),
]

fails = 0
warns = 0
for key, raw in WRITING_THEMES.items():
    comp = with_computed_colors(dict(raw))
    for kind, fgk, bgk, threshold, desc, warn_only in PAIRS:
        t = comp if kind == "computed" else raw
        ratio = cr(t[fgk], t[bgk])
        if ratio < threshold:
            if warn_only:
                warns += 1
                print(f"[WARN] {t['name']:6s} {desc}: {ratio:.2f}:1 "
                      f"({t[fgk]} on {t[bgk]})")
            else:
                fails += 1
                print(f"[FAIL] {t['name']:6s} {desc}: {ratio:.2f}:1 < {threshold}:1 "
                      f"({t[fgk]} on {t[bgk]})")

print(f"\n共 {fails} 项不达标，{warns} 项装饰性 WARN（可接受）"
      if fails else f"\n全部文字配对达标（{warns} 项装饰性 WARN，可接受）")
sys.exit(1 if fails else 0)
