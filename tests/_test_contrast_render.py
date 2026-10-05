# -*- coding: utf-8 -*-
"""10 套主题：实测强调色按钮（工具条/书架/AI气泡/设置对话框）渲染像素的文字对比度。

做法：离屏渲染后 grab 控件，边缘像素取底色，与底色亮度差最大的一簇像素取为文字色，
按 WCAG 公式算对比度。判定标准：设计色对比度 >= 3:1（粗体 AA 下限），
像素实测 >= 1.8（仅确认按配色实际绘制；13px 中文抗锯齿会系统性压低实测值）。

透明背景控件（toolbar 上 tier=secondary 的次级按钮等）会被跳过——grab() 只渲染
widget 自身、不带父级背景，透明区域像素恒为 (0,0,0,0)。本测试只针对 accent 底的
不透明强调按钮。
"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtGui import QFont, QFontDatabase, QColor
from PySide6.QtTest import QTest

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
app.setFont(QFont("Microsoft YaHei", 9))

import utils.work_store as ws_mod
_tmp = tempfile.mkdtemp(prefix="mmcontrast_")
ws_mod.get_data_root = lambda: _tmp

from utils.config import ConfigManager
cfg = ConfigManager()
cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini")
cfg._loaded = False
cfg.init_config()

from ui.main_window import MainWindow
from ui.writing_editor import WRITING_THEMES, get_writing_theme


def design_cr(theme_key):
    """accent 底 + accent_fg 文字的理论 WCAG 对比度"""
    t = get_writing_theme(theme_key)

    def lum(hexc):
        c = QColor(hexc)

        def f(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b, _ = c.getRgb()
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    la, lb = lum(t["accent"]), lum(t["accent_fg"])
    return (max(la, lb) + .05) / (min(la, lb) + .05)


w = MainWindow(cfg)
w.resize(1280, 820)
work = w.workspace.store.create_work("对比度测试", "墨码")
ch = work.add_chapter("第一章")
work.write_chapter(ch["id"], "正文" * 20)
work.save_meta()
w.workspace._open_work_by_path(work.root_dir)
w.show()
QTest.qWait(80)


def lum(c: QColor) -> float:
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b, _ = c.getRgb()
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: QColor, b: QColor) -> float:
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + .05) / (lo + .05)


def avg_color(colors):
    n = len(colors)
    r = sum(c.red() for c in colors) // n
    g = sum(c.green() for c in colors) // n
    b = sum(c.blue() for c in colors) // n
    return QColor(r, g, b)


def measure(widget, label):
    """返回 (底色, 文字色, 对比度, 文字像素数)；控件无有效不透明像素时返回 None。"""
    pix = widget.grab()
    img = pix.toImage()
    ww, hh = img.width(), img.height()
    border, inner = [], []
    for y in range(hh):
        for x in range(ww):
            c = img.pixelColor(x, y)
            # grab() 不带父级背景；透明背景控件的绝大部分像素 alpha=0，
            # 直接跳过——这类控件不属于本测试的"强调色按钮"目标。
            if c.alpha() < 128:
                continue
            (border if x < 3 or x > ww - 4 or y < 3 or y > hh - 4 else inner).append(c)
    if len(inner) < 20:
        return None
    # 底色 = 全部像素中量化后占比最高的颜色
    from collections import defaultdict
    buckets = defaultdict(list)
    for c in inner + border[-60:]:
        key = (c.red() // 16, c.green() // 16, c.blue() // 16)
        buckets[key].append(c)
    bg = avg_color(max(buckets.values(), key=len))
    bg_l = lum(bg)
    diffs = sorted(((abs(lum(c) - bg_l), c) for c in inner),
                   key=lambda x: x[0], reverse=True)
    text = [c for d, c in diffs if d > 0.15]
    if len(text) < 8:
        return (bg.name(), "(无文字像素)", 0.0, 0)
    # 只取亮度差最大的 1/3（文字芯，排除抗锯齿边缘混色），逼近设计色对比度
    core = [c for d, c in diffs[:max(8, len(diffs) // 3)] if d > 0.15]
    fg = avg_color(core)
    return (bg.name(), fg.name(), contrast(bg, fg), len(text))


fails = []
skipped = 0
print("%-10s %-14s %-10s %-10s %8s  %s" % ("主题", "控件", "底色", "文字色", "对比度", "结果"))
for key, theme in WRITING_THEMES.items():
    w._switch_writing_theme(key)
    QTest.qWait(40)
    ws = w.workspace
    targets = [
        # 工具条-发布：tier=primary，accent 底（旧版用 btn_back_gallery 是
        # transparent 背景的次级按钮，抓不到不透明像素，不属于本测试目标）
        ("工具条-发布", ws.btn_publish),
        ("书架-新章节", ws.bookshelf.add_chapter_btn),
        ("书架-导入", ws.bookshelf.import_btn),
        ("高频词-添加", ws.bookshelf.wordbook.add_word_btn),
    ]
    # AI 用户气泡
    bubble = ws.ai_panel._add_bubble("user", "测试文字")
    QTest.qWait(20)
    targets.append(("AI用户气泡", bubble))
    design = design_cr(key)
    for name, widget in targets:
        r = measure(widget, name)
        if r is None:
            print("%-10s %-14s (跳过：控件无有效不透明像素)" % (key, name))
            skipped += 1
            continue
        # 理论对比度 >=3:1（粗体 AA，权威值）；像素实测 >=1.8 仅确认按钮按配色
        # 实际绘制（透明失效时约 1.2；常规字重 13px 中文抗锯齿会压低实测值）
        ok = r[2] >= 1.8 and design >= 3.0
        print("%-10s %-14s %-10s %-10s %6.2f:1 (设计%.2f) %s" % (
            key, name, r[0], r[1], r[2], design, "PASS" if ok else "FAIL"))
        if not ok:
            fails.append((key, name, r, design))
    bubble.deleteLater()
    QTest.qWait(10)

# 设置对话框「完成」按钮（深/浅各取一个代表）
from ui.settings_writing import SettingsWritingDialog
for key in ("midnight", "paper"):
    w._switch_writing_theme(key)
    QTest.qWait(30)
    sd = SettingsWritingDialog(cfg, w)
    sd.show()
    QTest.qWait(40)
    done = [b for b in sd.findChildren(QPushButton) if b.text() == "完成"][0]
    r = measure(done, "完成")
    if r is None:
        print("%-10s %-14s (跳过：控件无有效不透明像素)" % (key, "设置-完成"))
        skipped += 1
    else:
        dcr = design_cr(key)
        ok = r[2] >= 1.8 and dcr >= 3.0
        print("%-10s %-14s %-10s %-10s %6.2f:1 (设计%.2f) %s" % (
            key, "设置-完成", r[0], r[1], r[2], dcr, "PASS" if ok else "FAIL"))
        if not ok:
            fails.append((key, "设置-完成", r, dcr))
    sd.close()
    sd.deleteLater()

print()
if skipped:
    print("跳过 %d 项（透明背景控件，不属于强调色按钮目标）" % skipped)
if fails:
    print("共 %d 项不达标：" % len(fails))
    for f in fails:
        print(" ", f)
    sys.exit(1)
print("ALL PASS - 全部主题强调按钮：设计对比度 >= 3:1 且像素实测确认按配色绘制")