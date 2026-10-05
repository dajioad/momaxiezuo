# -*- coding: utf-8 -*-
"""验证图墙模式：隐藏写作模块 + 仅留画廊 + 3:4 网文封面比例 + 自适应列数"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
app = QApplication.instance() or QApplication(sys.argv)

import utils.work_store as ws_mod
_tmp = tempfile.mkdtemp(prefix="mmgallery_")
ws_mod.get_data_root = lambda: _tmp

from utils.config import ConfigManager
cfg = ConfigManager()
cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini")
cfg._loaded = False
cfg.init_config()

from ui.main_window import MainWindow
from ui.workspace import _CoverLabel

w = MainWindow(cfg)
ws_ui = w.workspace
w.resize(1280, 820)
w.show()
app.processEvents()

# ---------- 1. 启动即图墙：写作模块全部隐藏 ----------
assert ws_ui.stack.currentIndex() == 0, "startup should be gallery page"
assert not ws_ui.toolbar.isVisible(), "toolbar must be hidden in gallery"
assert not ws_ui.statusbar.isVisible(), "statusbar must be hidden in gallery"
assert not ws_ui.bookshelf.add_chapter_btn.isVisible(), \
    "左下角新章节 button must be hidden in gallery"
assert not ws_ui.btn_find.isVisible() and not ws_ui.btn_format.isVisible(), \
    "查找/排版 buttons must be hidden in gallery"
assert not w.menuBar().isVisible(), "menu bar must be hidden in gallery"
assert not ws_ui.find_bar.isVisible(), "find bar must be hidden in gallery"
assert not hasattr(ws_ui, "btn_new_chapter"), \
    "顶部重复的新章节按钮应已删除，只保留左下角一个"
print("[1] PASS - 图墙模式：工具条/状态条/菜单栏/新章节/查找排版 全部隐藏")

# ---------- 2. 图墙下快捷键动作被拦截 ----------
ws_ui.find_bar.setVisible(False)
ws_ui.toggle_findbar()
assert not ws_ui.find_bar.isVisible(), "Ctrl+F must be blocked in gallery"
ws_ui.symbol_panel.setVisible(False)
ws_ui.toggle_symbol_panel()
assert not ws_ui.symbol_panel.isVisible(), "Ctrl+K must be blocked in gallery"
ws_ui.toggle_focus()
assert not ws_ui._focus_mode, "F11 focus must be blocked in gallery"
print("[2] PASS - 图墙模式下查找/符号/专注快捷键不生效")

# ---------- 3. 打开作品：写作模块恢复 ----------
work = ws_ui.store.create_work("测试小说", "墨码")
ch = work.add_chapter("第一章 开端")
work.write_chapter(ch["id"], "这是正文内容。" * 10)
work.save_meta()
gallery_events = []
ws_ui.gallery_changed.connect(lambda g: gallery_events.append(g))
ws_ui._open_work_by_path(work.root_dir)
app.processEvents()
assert ws_ui.stack.currentIndex() == 1, "opening a work enters writing page"
assert ws_ui.toolbar.isVisible(), "toolbar must restore in writing mode"
assert ws_ui.statusbar.isVisible(), "statusbar must restore in writing mode"
assert ws_ui.bookshelf.add_chapter_btn.isVisible(), \
    "左下角新章节 button available in writing mode"
assert w.menuBar().isVisible(), "menu bar must restore in writing mode"
assert gallery_events == [False], f"signal should fire once with False, got {gallery_events}"
print("[3] PASS - 进入写作：工具条/状态条/菜单栏恢复，gallery_changed(False)")

# ---------- 4. 返回图墙：再次隐藏 ----------
ws_ui._back_to_gallery()
app.processEvents()
assert ws_ui.stack.currentIndex() == 0
assert not ws_ui.toolbar.isVisible() and not w.menuBar().isVisible()
assert gallery_events[-1] is True
print("[4] PASS - 返回图墙：写作模块再次隐藏")

# ---------- 5. 封面 3:4 比例 ----------
cover = _CoverLabel("", "测试")
cover.show()
cover.resize(210, 100)
app.processEvents()
assert cover.height() == 280, f"3:4 cover height should be 280 for width 210, got {cover.height()}"
cover2 = _CoverLabel("", "测试")
cover2.show()
cover2.resize(180, 100)
app.processEvents()
assert cover2.height() == 240, f"3:4 cover height should be 240 for width 180, got {cover2.height()}"
print("[5] PASS - 封面严格 3:4（210x280 / 180x240）")

# ---------- 6. 真实封面图：填满裁剪不变形 ----------
img_path = os.path.join(_tmp, "c.png")
QPixmap(300, 400).save(img_path)  # 本身 3:4
work2 = ws_ui.store.create_work("图封作品", "")
work2.set_cover(img_path)
ws_ui._refresh_gallery()
app.processEvents()
card_cover = ws_ui._gallery_layout.itemAtPosition(0, 0).widget().findChild(_CoverLabel)
assert card_cover is not None, "card should contain a _CoverLabel"
assert card_cover._src is not None, "cover image should be loaded"
print("[6] PASS - 自定义封面图正常加载")

# ---------- 7. 自适应列数 ----------
w.resize(1500, 900)
w.show()
app.processEvents()
from PySide6.QtTest import QTest
QTest.qWait(250)  # 等 120ms 防抖 + 布局
assert ws_ui._gallery_cols >= 4, f"wide window should use >=4 cols, got {ws_ui._gallery_cols}"
wide_cols = ws_ui._gallery_cols
w.resize(1000, 680)
app.processEvents()
QTest.qWait(250)
assert ws_ui._gallery_cols < wide_cols, f"narrow window should reduce cols, {ws_ui._gallery_cols} vs {wide_cols}"
print(f"[7] PASS - 列数随宽度自适应（宽屏 {wide_cols} 列 → 窄屏 {ws_ui._gallery_cols} 列）")

# ---------- 8. 卡片封面实际渲染比例 ----------
card = ws_ui._gallery_layout.itemAtPosition(0, 0).widget()
cv = card.findChild(_CoverLabel)
app.processEvents()
QTest.qWait(50)
expected = int(round(cv.width() * 4 / 3))
assert cv.width() > 0 and abs(cv.height() - expected) <= 1, \
    f"gallery cover ratio off: {cv.width()}x{cv.height()}, expected h={expected}"
print(f"[8] PASS - 画廊内封面实际尺寸 {cv.width()}x{cv.height()}（3:4）")

# ---------- 9. Esc 快捷键链（新语义：只关浮层，不返回画廊） ----------
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent


def press_esc(target):
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    return w.eventFilter(target, ev)


assert "Ctrl+W" in ws_ui.btn_back_gallery.toolTip(), \
    "画廊按钮 tooltip 应提示 Ctrl+W"

# 9.1 写作模式 + 编辑器焦点：Esc 不应回画廊（避免误触关闭当前章节）
ws_ui._open_work_by_path(work.root_dir)
app.processEvents()
ws_ui.editor.setFocus()
assert press_esc(ws_ui.editor) is False, "编辑器里 Esc 不应被拦截"
assert not ws_ui._is_gallery(), "Esc 不应回画廊"

# 9.2 查找条打开时：Esc 关闭查找条，仍在写作模式
ws_ui.toggle_findbar()
assert ws_ui.find_bar.isVisible()
assert press_esc(ws_ui.find_bar.find_edit) is True
assert not ws_ui.find_bar.isVisible() and not ws_ui._is_gallery(), \
    "Esc 应关闭查找条且仍在写作模式"

# 9.3 符号面板打开时：Esc 关闭面板
ws_ui.symbol_panel.setVisible(False)
ws_ui.toggle_symbol_panel()
assert ws_ui.symbol_panel.isVisible()
assert press_esc(ws_ui.editor) is True
assert not ws_ui.symbol_panel.isVisible() and not ws_ui._is_gallery(), \
    "Esc 应关闭符号面板且仍在写作模式"

# 9.4 AI 面板打开时：Esc 关闭面板
ws_ui.ai_panel.setVisible(False)
ws_ui.toggle_ai()
assert ws_ui.ai_panel.isVisible()
assert press_esc(ws_ui.editor) is True
assert not ws_ui.ai_panel.isVisible() and not ws_ui._is_gallery(), \
    "Esc 应关闭 AI 面板且仍在写作模式"

# 9.5 专注模式：Esc 先退专注
ws_ui.toggle_focus()
assert ws_ui._focus_mode
ws_ui.editor.setFocus()
press_esc(ws_ui.editor)
assert not ws_ui._focus_mode and not ws_ui._is_gallery(), \
    "Esc 应只退出专注模式"

# 9.6 Ctrl+W 返回画廊
ws_ui._back_to_gallery()
assert ws_ui._is_gallery()
print("[9] PASS - Esc 只关浮层/退专注；Ctrl+W 返回画廊")