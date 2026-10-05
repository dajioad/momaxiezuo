# -*- coding: utf-8 -*-
"""逐主题离屏渲染巡检：实测每个文字/按钮控件的「墨色 vs 底色」对比度。

做法不依赖解析 QSS：直接 grab 控件像素，边框环取背景色，
与背景差异显著的像素视为文字/图标墨色，计算 WCAG 对比度。
阈值 <3.0 的控件记为不清晰；文字控件无墨色像素记为疑似不可见。
"""

import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QCheckBox, QRadioButton,
    QToolButton, QLineEdit, QTabBar, QHeaderView
)
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

app = QApplication.instance() or QApplication(sys.argv)
import platform
if platform.system() == "Windows":
    for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc'):
        if os.path.exists(fp):
            QFontDatabase.addApplicationFont(fp)
    if os.path.exists(fp):
        QFontDatabase.addApplicationFont(fp)
app.setFont(QFont('Microsoft YaHei', 9))

import utils.work_store as ws_mod
_tmp = tempfile.mkdtemp(prefix="mmaudit_")
ws_mod.get_data_root = lambda: _tmp
from utils.config import ConfigManager
cfg = ConfigManager(); cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini"); cfg._loaded = False; cfg.init_config()

from ui.main_window import MainWindow
from ui.writing_editor import WRITING_THEMES
from ui.settings_writing import SettingsWritingDialog
from ui.format_dialog import FormatDialog
from ui.batch_tools import BatchToolsDialog
from ui.proofread_dialog import ProofreadDialog
from ui.reader_preview import ReaderPreviewDialog


def lum_rgb(r, g, b):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    R, G, B = f(r), f(g), f(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def cr(c1, c2):
    l1, l2 = lum_rgb(*c1), lum_rgb(*c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def median(seq, idx):
    s = sorted(p[idx] for p in seq)
    return s[len(s) // 2]


def measure(widget):
    """返回 (ratio, bg, ink, ink_count)；无墨色返回 None"""
    img = widget.grab().toImage()
    w, h = img.width(), img.height()
    if w < 28 or h < 16:
        return None
    border = []
    for x in range(0, w, 3):
        for c in (img.pixelColor(x, 0), img.pixelColor(x, h - 1)):
            border.append((c.red(), c.green(), c.blue()))
    for y in range(0, h, 3):
        for c in (img.pixelColor(0, y), img.pixelColor(w - 1, y)):
            border.append((c.red(), c.green(), c.blue()))
    bg = (median(border, 0), median(border, 1), median(border, 2))
    ink = []
    for y in range(1, h - 1, 2):
        for x in range(1, w - 1, 2):
            c = img.pixelColor(x, y)
            r, g, b, a = c.red(), c.green(), c.blue(), c.alpha()
            if a < 128:
                continue
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 70:
                ink.append((r, g, b))
    if len(ink) < 6:
        return (0.0, bg, None, len(ink))
    m = (median(ink, 0), median(ink, 1), median(ink, 2))
    return (cr(m, bg), bg, m, len(ink))


TEXT_TYPES = (QLabel, QPushButton, QCheckBox, QRadioButton, QToolButton,
              QLineEdit, QTabBar, QHeaderView)
SKIP_OBJECTS = {"workCover", "galleryLogo"}


def scan(root, surface, theme_name, findings, limit=120):
    seen = 0
    for typ in TEXT_TYPES:
        for child in root.findChildren(typ):
            if not child.isVisible() or child.objectName() in SKIP_OBJECTS:
                continue
            r = child.geometry()
            if r.width() < 28 or r.height() < 16:
                continue
            txt = ""
            if isinstance(child, (QLabel, QPushButton, QCheckBox, QRadioButton,
                                  QToolButton, QLineEdit)):
                txt = child.text() if hasattr(child, "text") else child.placeholderText()
            m = measure(child)
            if not m:
                continue
            ratio, bg, inkc, n = m
            label = f"{type(child).__name__}[{child.objectName() or txt[:10] or '-'}]"
            if ratio == 0.0 and txt.strip():
                findings.append((theme_name, surface, label, "无墨色(疑似不可见)", 0.0))
            elif 0 < ratio < 3.0 and n >= 6:
                findings.append((theme_name, surface, label, f"对比不足 {ratio:.2f}", ratio))
            seen += 1
            if seen > limit:
                return


w = MainWindow(cfg)
w.resize(1280, 820)
store = w.workspace.store
work = store.create_work("审计测试小说", "测试作者")
ch = work.add_chapter("第一章 开端")
work.write_chapter(ch["id"], "这是用于巡检的正文内容。" * 40)
work.save_meta()

results_data = [{"category": "错别字", "message": "的的", "snippet": "这是一段上下文",
                 "suggestion": "的", "start": 0, "end": 2}]

all_findings = []

for key, theme in WRITING_THEMES.items():
    name = theme["name"]
    w._switch_writing_theme(key)
    w.show()
    app.processEvents()
    QTest.qWait(60)

    # A 写作页（书架/工具条/状态条）
    w.workspace._open_work_by_path(work.root_dir)
    app.processEvents(); QTest.qWait(60)
    scan(w.workspace, "写作页", name, all_findings)

    # B 符号面板
    if not w.workspace.symbol_panel.isVisible():
        w.workspace.toggle_symbol_panel()
    app.processEvents(); QTest.qWait(60)
    scan(w.workspace.symbol_panel, "符号面板", name, all_findings)

    # C AI 面板
    if not w.workspace.ai_panel.isVisible():
        w.workspace.toggle_ai()
    app.processEvents(); QTest.qWait(60)
    scan(w.workspace.ai_panel, "AI面板", name, all_findings)
    w.workspace.symbol_panel.setVisible(False)
    w.workspace.ai_panel.setVisible(False)

    # D 图墙
    w.workspace._back_to_gallery()
    app.processEvents(); QTest.qWait(60)
    scan(w.workspace.welcome_page, "图墙", name, all_findings)
    w.workspace._open_work_by_path(work.root_dir)
    app.processEvents(); QTest.qWait(40)

    # E 对话框
    dlg_specs = [
        ("设置-通用", lambda: SettingsWritingDialog(cfg, w)),
        ("排版", lambda: FormatDialog(work, ch["id"], w)),
        ("批量工具", lambda: BatchToolsDialog([], "", w)),
        ("校对结果", lambda: ProofreadDialog(results_data, w)),
        ("读者预览", lambda: ReaderPreviewDialog("巡检正文内容。" * 30, "第一章", w)),
    ]
    for sname, factory in dlg_specs:
        try:
            dlg = factory()
        except Exception as e:
            all_findings.append((name, sname, "-", f"构造失败: {e}", 0.0))
            continue
        dlg.show()
        app.processEvents(); QTest.qWait(40)
        scan(dlg, sname, name, all_findings, limit=80)
        if sname == "设置-通用":
            for tab in (1, 2, 3):
                try:
                    dlg.select_tab(tab)
                    app.processEvents(); QTest.qWait(30)
                    scan(dlg, f"设置-标签{tab}", name, all_findings, limit=80)
                except Exception as e:
                    all_findings.append((name, f"设置-标签{tab}", "-", str(e), 0.0))
        dlg.deleteLater()

# 汇总输出
print(f"\n===== 巡检完成：{len(WRITING_THEMES)} 主题 × 多界面，共 {len(all_findings)} 个疑似问题 =====\n")
for f in sorted(all_findings, key=lambda x: x[4]):
    print(f"[{f[0]}] {f[1]:10s} {f[2]:40s} {f[3]}")
