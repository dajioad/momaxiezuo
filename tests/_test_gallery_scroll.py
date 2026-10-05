# -*- coding: utf-8 -*-
"""画廊滚动行为回归：
1. 竖向滚动条常驻且为 10px 现代细滚动条
2. 返回画廊 / 搜索 / 排序等数据重建后，滚动位置回到顶部（不再停在底部）
3. 窗口宽度变化导致的重排保持纵向阅读位置
"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
app.setFont(QFont("Microsoft YaHei", 9))

import utils.work_store as ws_mod
_tmp = tempfile.mkdtemp(prefix="mmscrtest_")
ws_mod.get_data_root = lambda: _tmp
from utils.config import ConfigManager
cfg = ConfigManager()
cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini")
cfg._loaded = False
cfg.init_config()
from ui.main_window import MainWindow

w = MainWindow(cfg)
ws = w.workspace
for i in range(12):
    wk = ws.store.create_work(f"作品{i:02d}", "")
    c = wk.add_chapter("第一章")
    wk.write_chapter(c["id"], "字" * 1000)
    wk.save_meta()
w.resize(760, 640)
w.show()
QTest.qWait(350)
scroll = ws._gallery_scroll
sb = scroll.verticalScrollBar()

# 1. 常驻 + 细滚动条样式
assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn
assert sb.width() == 10, f"滚动条宽应为 10px，实际 {sb.width()}"
assert "border-radius" in scroll.styleSheet() and "add-line" in scroll.styleSheet()
assert sb.maximum() > 0, "12 部作品在该窗口下应可纵向滚动"
print(f"[1] PASS - 滚动条常驻、宽 10px、圆角无箭头（max={sb.maximum()}）")

# 2. 滚到底 → 模拟进入作品再返回 → 回到顶部
sb.setValue(sb.maximum())
QTest.qWait(40)
assert sb.value() > 0
ws._open_work_by_path(ws.store.list_works()[0]["path"])
QTest.qWait(40)
ws._back_to_gallery()
QTest.qWait(60)
sb = ws._gallery_scroll.verticalScrollBar()
assert sb.value() == 0, f"返回画廊应在顶部，实际 {sb.value()}"
print("[2] PASS - 返回画廊滚动位置归零（修复停在底部）")

# 3. 搜索触发的数据重建也归零
sb = ws._gallery_scroll.verticalScrollBar()
sb.setValue(sb.maximum())
QTest.qWait(20)
ws.gallery_search.setText("不存在xyz")
QTest.qWait(40)
assert ws._gallery_scroll.verticalScrollBar().value() == 0
ws.gallery_search.clear()
QTest.qWait(40)
print("[3] PASS - 搜索/过滤重建后回到顶部")

# 4. 窗口缩放重排保持阅读位置
ws._gallery_scroll.verticalScrollBar().setValue(300)
QTest.qWait(30)
w.resize(1280, 820)
QTest.qWait(350)
val_after = ws._gallery_scroll.verticalScrollBar().value()
assert abs(val_after - 300) <= 2, f"宽度重排应保持位置，实际 {val_after}"
print(f"[4] PASS - 宽度重排保持滚动位置（300 → {val_after}）")

print("\nALL PASS - 画廊滚动行为正常")
