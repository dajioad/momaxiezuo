# -*- coding: utf-8 -*-
"""核心符号面板交互测试"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

from PySide6.QtGui import QTextCursor
from ui.symbol_panel import SymbolPanel
from ui.writing_editor import WritingEditor

# === 测试 1: 方式按钮 toggle + 互斥 ===
print("测试 1: 方式按钮 toggle + 互斥")
sp = SymbolPanel()
sp.mode_buttons["head"].click()
assert sp._mode == "head" and sp.mode_buttons["head"].isChecked()
sp.mode_buttons["head"].click()  # 再点取消
assert sp._mode is None and not sp.mode_buttons["head"].isChecked()
sp.mode_buttons["tail"].click()
assert sp._mode == "tail" and sp.mode_buttons["tail"].isChecked() and not sp.mode_buttons["head"].isChecked()
sp.mode_buttons["both"].click()  # 互斥：tail 自动取消
assert sp._mode == "both" and sp.mode_buttons["both"].isChecked() and not sp.mode_buttons["tail"].isChecked()
print("  ✅ PASS")

# === 测试 2: 编辑器 + 两端书名号 ===
print("测试 2: 选中文本 → 两端方式 + 《》 → 《123》 + 撤回")
editor = WritingEditor()
editor.edit.setPlainText("选中123测试段落")
c = editor.edit.textCursor()
c.setPosition(2); c.setPosition(5, QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c)
before = editor.edit.toPlainText()

sp2 = SymbolPanel()
sp2.edge_apply.connect(editor.apply_symbol_live)
sp2.revert_live.connect(editor.revert_symbol_live)
sp2.insert_symbol.connect(editor.insert_text)
sp2.set_has_selection(True)

sp2.mode_buttons["both"].click()
sp2._on_insert_symbol("《》")
assert "《123》" in editor.edit.toPlainText(), f"got {editor.edit.toPlainText()}"
# 撤回
c = editor.edit.textCursor(); s = editor._sym_session
c.setPosition(s["start"]); c.setPosition(s["start"]+s["last_len"], QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c); sp2.set_has_selection(True)
sp2._on_insert_symbol("《》")  # toggle off
assert editor.edit.toPlainText() == before
print("  ✅ PASS")

# === 测试 3: 编辑器 + 分隔逗号 ===
print("测试 3: 选中文本 → 分隔方式 + 逗号 → 1,2,3 + 撤回")
editor.edit.setPlainText("选中123测试段落")
c = editor.edit.textCursor()
c.setPosition(2); c.setPosition(5, QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c)
before = editor.edit.toPlainText()

sp3 = SymbolPanel()
sp3.edge_apply.connect(editor.apply_symbol_live)
sp3.revert_live.connect(editor.revert_symbol_live)
sp3.insert_symbol.connect(editor.insert_text)
sp3.set_has_selection(True)

sp3.mode_buttons["split"].click()
sp3._on_insert_symbol(",")
assert "1,2,3" in editor.edit.toPlainText(), f"got {editor.edit.toPlainText()}"
# 撤回
c = editor.edit.textCursor(); s = editor._sym_session
c.setPosition(s["start"]); c.setPosition(s["start"]+s["last_len"], QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c); sp3.set_has_selection(True)
sp3.mode_buttons["split"].click()  # toggle off 方式
assert editor.edit.toPlainText() == before
print("  ✅ PASS")

# === 测试 4: 切方式自动重新应用 ===
print("测试 4: 切方式（head → tail）自动重新应用符号")
editor.edit.setPlainText("测试文字ABC测试")
c = editor.edit.textCursor()
c.setPosition(4); c.setPosition(7, QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c)
before = editor.edit.toPlainText()

sp4 = SymbolPanel()
sp4.edge_apply.connect(editor.apply_symbol_live)
sp4.revert_live.connect(editor.revert_symbol_live)
sp4.insert_symbol.connect(editor.insert_text)
sp4.set_has_selection(True)

sp4.mode_buttons["head"].click()
sp4._on_insert_symbol("《》")
# head 方式 = 头部加符号 → 《》ABC
assert "《》ABC" in editor.edit.toPlainText(), f"got {editor.edit.toPlainText()}"

# 切到 tail（互斥自动）
sp4.mode_buttons["tail"].click()
# tail 方式 + 《》符号 = ABC + 《》 = ABC《》
assert "ABC《》" in editor.edit.toPlainText(), f"got {editor.edit.toPlainText()}"
# 撤回
c = editor.edit.textCursor(); s = editor._sym_session
c.setPosition(s["start"]); c.setPosition(s["start"]+s["last_len"], QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c); sp4.set_has_selection(True)
sp4.mode_buttons["tail"].click()
assert editor.edit.toPlainText() == before
print("  ✅ PASS")

print("\n🎉 全部 4 项核心交互通过！")
