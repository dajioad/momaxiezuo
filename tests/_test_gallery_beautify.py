# -*- coding: utf-8 -*-
"""「我的作品」画廊美化回归：
1. 空库时：虚线新建占位卡存在 + 引导空状态
2. 有作品：每部一张作品卡 + 网格末尾 1 张占位卡，占位卡与作品卡等宽
3. 搜索框按书名/作者过滤；无匹配时显示提示
4. 排序：书名 A-Z / 字数 多到少
5. 卡片含 ⋯ 菜单按钮；占位卡封面槽 3:4
6. 重命名只改书名后画廊即时刷新
7. PC 1280 宽度下为 4 列
8. 多套主题下占位卡 QSS 均为虚线
"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QInputDialog
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
app.setFont(QFont("Microsoft YaHei", 9))

import utils.work_store as ws_mod
_tmp = tempfile.mkdtemp(prefix="mmbeauty_")
ws_mod.get_data_root = lambda: _tmp

from utils.config import ConfigManager
cfg = ConfigManager()
cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini")
cfg._loaded = False
cfg.init_config()

from ui.main_window import MainWindow
from ui.workspace import _CoverSlot

w = MainWindow(cfg)
ws_ui = w.workspace
w.resize(1280, 820)
w.show()
QTest.qWait(250)


def cards_by_name(name):
    return [f for f in ws_ui._gallery_container.findChildren(QFrame)
            if f.objectName() == name]


def card_titles():
    out = []
    for c in cards_by_name("workCard"):
        lbls = [l for l in c.findChildren(QLabel) if l.objectName() == "workCardTitle"]
        if lbls:
            out.append(lbls[0].text())
    return out


# ---------- 1. 空库 ----------
assert cards_by_name("newWorkCard"), "空库也必须有新建占位卡"
tips = [l for l in ws_ui._gallery_container.findChildren(QLabel)
        if l.objectName() == "galleryEmptyTip"]
assert tips and "作品" in tips[0].text(), "应显示空状态引导"
print("[1] PASS - 空库：虚线占位卡 + 引导文案")

# ---------- 2. 造 3 部作品 ----------
wa = ws_ui.store.create_work("阿尔法", "张三")
ch = wa.add_chapter("第一章"); wa.write_chapter(ch["id"], "字" * 100); wa.save_meta()
wb = ws_ui.store.create_work("贝塔", "")
ch = wb.add_chapter("第一章"); wb.write_chapter(ch["id"], "字" * 300); wb.save_meta()
wc = ws_ui.store.create_work("星辰大海", "李四")
ch = wc.add_chapter("第一章"); wc.write_chapter(ch["id"], "字" * 200); wc.save_meta()
ws_ui._refresh_gallery()
QTest.qWait(30)
work_cards = cards_by_name("workCard")
new_cards = cards_by_name("newWorkCard")
assert len(work_cards) == 3, f"应有 3 张作品卡，实际 {len(work_cards)}"
assert len(new_cards) == 1, f"应有 1 张占位卡，实际 {len(new_cards)}"
if work_cards[0].width() > 0:
    assert new_cards[0].width() == work_cards[0].width(), "占位卡应与作品卡等宽"
print("[2] PASS - 作品卡 + 末尾占位卡，宽度一致")

# ---------- 3. 搜索 ----------
ws_ui.gallery_search.setText("贝塔")
QTest.qWait(20)
assert card_titles() == ["贝塔"], card_titles()
ws_ui.gallery_search.setText("李四")  # 按作者
QTest.qWait(20)
assert card_titles() == ["星辰大海"], card_titles()
ws_ui.gallery_search.setText("不存在的书名xyz")
QTest.qWait(20)
assert card_titles() == [], "无匹配应无作品卡"
miss = [l for l in ws_ui._gallery_container.findChildren(QLabel)
        if l.objectName() == "galleryEmptyTip"]
assert any("匹配" in l.text() for l in miss), "应提示没有匹配"
assert cards_by_name("newWorkCard"), "无匹配时占位卡仍在"
ws_ui.gallery_search.clear()
QTest.qWait(20)
assert len(cards_by_name("workCard")) == 3
print("[3] PASS - 书名/作者搜索过滤与无结果提示")

# ---------- 4. 排序 ----------
ws_ui.gallery_sort.setCurrentIndex(ws_ui.gallery_sort.findData("title"))
QTest.qWait(20)
assert card_titles() == ["星辰大海", "贝塔", "阿尔法"], card_titles()
# 字数：贝塔300 > 星辰200 > 阿尔法100
ws_ui.gallery_sort.setCurrentIndex(ws_ui.gallery_sort.findData("words"))
QTest.qWait(20)
assert card_titles() == ["贝塔", "星辰大海", "阿尔法"], card_titles()
ws_ui.gallery_sort.setCurrentIndex(ws_ui.gallery_sort.findData("updated"))
QTest.qWait(20)
print("[4] PASS - 书名/字数排序正确")

# ---------- 5. ⋯ 按钮 + 3:4 占位封面 ----------
for c in work_cards:
    pass  # work_cards 是旧引用，重新取
first = cards_by_name("workCard")[0]
menu_btns = [b for b in first.findChildren(QPushButton)
             if b.objectName() == "cardMenuBtn"]
assert len(menu_btns) == 1, "每张作品卡应有 1 个 ⋯ 菜单按钮"
slot = cards_by_name("newWorkCard")[0].findChild(_CoverSlot)
assert slot is not None, "占位卡应含 3:4 封面槽"
QTest.qWait(30)
if slot.width() > 0:
    assert abs(slot.height() - round(slot.width() * 4 / 3)) <= 1, \
        f"占位封面槽非 3:4: {slot.width()}x{slot.height()}"
print("[5] PASS - ⋯ 菜单按钮在位，占位封面槽 3:4")

# ---------- 6. 重命名 ----------
QInputDialog.getText = staticmethod(lambda *a, **k: ("阿尔法新编", True))
ws_ui._rename_work(wa, wa.root_dir)
assert ws_ui.store.open_work(wa.root_dir).title == "阿尔法新编"
assert "阿尔法新编" in card_titles()
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))  # 还原为取消
print("[6] PASS - 重命名写盘并即时刷新画廊")

# ---------- 7. 列数 ----------
assert ws_ui._gallery_cols == 4, f"1280 宽应为 4 列，实际 {ws_ui._gallery_cols}"
w.resize(900, 700)
QTest.qWait(250)
assert ws_ui._gallery_cols == 3, f"900 宽应为 3 列，实际 {ws_ui._gallery_cols}"
w.resize(1280, 820)
QTest.qWait(250)
print(f"[7] PASS - 响应式列数（1280→4 列，900→{3} 列）")

# ---------- 7.5 上传封面后必须真正绘制出来（早期版本卡在兜底色块） ----------
from PySide6.QtGui import QPixmap, QColor
from ui.workspace import _CoverLabel
up = os.path.join(ws_ui.store.data_root, "up_red.png")
pm = QPixmap(600, 800)
pm.fill(QColor(220, 40, 40))
pm.save(up)
target = ws_ui.store.open_work(wa.root_dir)
target.set_cover(up)
ws_ui._refresh_gallery()
QTest.qWait(120)
painted = None
for c in cards_by_name("workCard"):
    cv = c.findChild(_CoverLabel)
    if cv is not None and cv._src is not None:   # 直接看 _src
        painted = cv
        break
assert painted is not None, "上传封面后应有卡片真正绘制了封面 pixmap"

# 抓整个 widget 的渲染像素（而不是 pixmap()）
img = painted.grab().toImage()
cx, cy = img.width() // 2, img.height() // 2
col = img.pixelColor(cx, cy)
assert abs(col.red() - 220) < 12 and col.green() < 70, \
    f"封面中心应为上传的红色，实际 {col.red()},{col.green()},{col.blue()}"
print("[7.5] PASS - 上传封面后即时绘制，内容为上传图片")

# ---------- 8. 多主题占位卡虚线 ----------
from ui.writing_editor import get_writing_theme
for key in ("paper", "midnight", "forest_night", "rose"):
    t = get_writing_theme(key)
    ws_ui.apply_writing_theme(t)
    ss = ws_ui._gallery_placeholder.styleSheet()
    assert "#newWorkCard" in ss and "dashed" in ss, f"{key} 缺占位卡虚线样式"
print("[8] PASS - 4 套代表主题下占位卡虚线样式存在")

print("\nALL PASS - 画廊美化功能回归通过")
