# -*- coding: utf-8 -*-
"""冒烟：主题适配 + 右键汉化

与旧版相比的三处修正：
  1. 旧版第 1 段替换时把 `w = MainWindow(cfg)` 一起删掉了，
     但后面第 2 段起都在用 `w`——补回。
  2. `_switch_writing_theme` 是把 QSS 设在 **menuBar** 上、
     palette 设在 QApplication 上，主窗口自身的 styleSheet 始终为空。
     第 2 段断言改为 `w.menuBar().styleSheet()`。
  3. `build_global_qss` 输出的颜色键是 sidebar / text / muted / accent /
     editor_bg，**没有 bg**（页面底色走 palette）。
     第 2 段断言色从 bg 改为 sidebar。
"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QContextMenuEvent
app = QApplication.instance() or QApplication(sys.argv)

import utils.work_store as ws
ws.get_data_root = lambda: tempfile.mkdtemp()

from utils.config import ConfigManager
cfg = ConfigManager()
cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini")
cfg._loaded = False
cfg.init_config()

from ui.main_window import MainWindow, build_global_qss
from ui.writing_editor import WRITING_THEMES, get_writing_theme

# 0. 先建 MainWindow —— 后面第 2 段起都要用到
w = MainWindow(cfg)

# 1. 菜单栏 QSS 生成（浅色 + 深色）
paper_theme = get_writing_theme("paper")
qss_light = build_global_qss(paper_theme)
assert qss_light, "传入主题时不应返回空串"
assert paper_theme["text"] in qss_light, "浅色主题 QSS 应含正文色"
assert paper_theme["sidebar"] in qss_light, "浅色主题 QSS 应含侧栏色"

dark_theme = get_writing_theme("midnight")
qss_dark = build_global_qss(dark_theme)
assert qss_dark, "传入主题时不应返回空串"
assert dark_theme["text"] in qss_dark, "深色主题 QSS 应含正文色"
assert dark_theme["sidebar"] in qss_dark, "深色主题 QSS 应含侧栏色"
print("[1] global QSS theme-adaptive PASS")

# 2. 深色主题切换 → menuBar 上的 QSS 更新
#    注意：QSS 挂在 menuBar 上，不是 MainWindow 自身
w._switch_writing_theme("midnight")
qss_current = w.menuBar().styleSheet()
assert qss_current, "切换主题后 menuBar 应有 QSS"
assert dark_theme["sidebar"] in qss_current, \
    f"menuBar QSS 应含深色主题 sidebar 色 {dark_theme['sidebar']}"
print("[2] theme switch updates shell QSS PASS")

# 3. AI 功能方法仍挂在 workspace（A 批次移除旧 ai_panel 后的等价断言）
assert callable(getattr(w.workspace, "extract_relations_with_ai", None)), \
    "workspace 应保留 AI 人物关系提取入口"
assert callable(getattr(w.workspace, "open_beta_reading", None)), \
    "workspace 应保留墨灵试读入口"
print("[3] workspace AI entry methods PASS")

# 4. 符号面板深色主题
sp = w.workspace.symbol_panel
sp.apply_colors(dark_theme)
assert sp._theme == dark_theme
print("[4] symbol panel dark theme PASS")

# 5. 查找栏主题适配
fb = w.workspace.find_bar
assert fb.styleSheet()  # 应有主题色
print("[5] find bar themed PASS")

# 6. QLineEdit 右键汉化
#    触发 contextMenuEvent —— app 级 eventFilter 会拦截并汉化菜单项
le = QLineEdit()
le.setText("test")
evt = QContextMenuEvent(QContextMenuEvent.Mouse,
                        le.rect().center(),
                        le.mapToGlobal(le.rect().center()))
QApplication.sendEvent(le, evt)
print("[6] QLineEdit context menu filter installed PASS")

# 7. 编辑器右键菜单（_InnerPlainEdit 覆写了 contextMenuEvent）
from ui.writing_editor import _InnerPlainEdit
assert hasattr(_InnerPlainEdit, "contextMenuEvent")
assert hasattr(_InnerPlainEdit, "_ctx_menu_qss")
print("[7] editor context menu override PASS")

# 8. 对话框主题强调色解析
#    验证深色主题下对话框不会硬编码浅色强调色 #b08a4e
from ui.format_dialog import FormatDialog
from ui.batch_tools import BatchToolsDialog
from ui.settings_writing import SettingsWritingDialog
from utils.work_store import WorkStore

store = WorkStore()
work = store.create_work("smoke")

fd = FormatDialog(work, None, w)
accent_dark = fd._resolve_theme().get("accent")
assert accent_dark == dark_theme["accent"], \
    f"format dialog accent mismatch: {accent_dark}"
assert accent_dark != "#b08a4e", \
    "format dialog still falls back to light accent on dark theme"
fd.deleteLater()

bd = BatchToolsDialog(None, None, w)
assert bd._resolve_accent() == dark_theme["accent"]
bd.deleteLater()

sd = SettingsWritingDialog(cfg, w)
assert sd._resolve_accent() == dark_theme["accent"]
sd.deleteLater()
print("[8] dialogs resolve theme accent on dark theme PASS")

# 9. 切回浅色主题后对话框强调色随之变化（以主题定义的当前调色板为准，
#    不再硬编码色值——A 批次视觉改版曾把 paper accent 调成 #C19860）
w._switch_writing_theme("paper")
fd2 = FormatDialog(work, None, w)
from ui.writing_editor import get_writing_theme
paper_accent = fd2._resolve_theme().get("accent", "")
assert paper_accent.lower() == get_writing_theme("paper")["accent"].lower(), \
    f"expected paper accent, got {paper_accent}"
fd2.deleteLater()
print("[9] dialogs accent follows theme switch PASS")

print("ALL PASS")