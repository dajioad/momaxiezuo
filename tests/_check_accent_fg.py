# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.writing_editor import get_writing_theme, WRITING_THEMES
from PySide6.QtGui import QColor


def lum(hexc):
    c = QColor(hexc)

    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b, _ = c.getRgb()
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def cr(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + .05) / (lo + .05)


for k in WRITING_THEMES:
    t = get_writing_theme(k)
    print("%-12s accent=%s fg=%s  对比度 %.2f:1" % (
        k, t['accent'], t['accent_fg'], cr(t['accent'], t['accent_fg'])))
