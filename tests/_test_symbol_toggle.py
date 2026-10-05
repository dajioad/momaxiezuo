# -*- coding: utf-8 -*-
"""验证符号面板完整交互：toggle 选中 + 自动撤回 + live session 正确性"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QPlainTextEdit
app = QApplication(sys.argv)

from PySide6.QtGui import QTextCursor
from ui.symbol_panel import SymbolPanel
from ui.writing_editor import WritingEditor

# === 测试 1: 符号/方式 toggle + 自动撤回 ===
print("=" * 60)
print("测试 1: 纯 SymbolPanel toggle 行为")
print("=" * 60)

sp = SymbolPanel()

# 1a. 初始：无选中
assert sp._mode is None and sp._active_sym == ""
print("[1a] OK - 初始无选中")

# 1b. 点击方式按钮（用 click() 触发 toggle）
sp.mode_buttons["head"].click()
assert sp._mode == "head", f"_mode={sp._mode}, 预期 head"
print("[1b] OK - click head 选中")

# 1c. 模拟用户点同一按钮取消（custom mousePressEvent 会先 setChecked(False) 再触发 toggled）
sp.mode_buttons["head"].blockSignals(True)
sp.mode_buttons["head"].setChecked(False)
sp.mode_buttons["head"].blockSignals(False)
# 手动触发信号（模拟 custom press 后的 toggled 回调）
sp._on_mode_toggled("head", False)
assert sp._mode is None, f"_mode={sp._mode}, 预期 None"
print("[1c] OK - toggle head OFF -> None")

# 1d. 切到另一个方式
sp.mode_buttons["tail"].click()
assert sp._mode == "tail"
print("[1d] OK - click tail 选中（互斥：head 自动取消）")

# 1e. 符号 toggle
sp._on_insert_symbol("……")
assert sp._active_sym == "……"
sp._on_insert_symbol("……")  # 再点一次取消
assert sp._active_sym == ""
print("[1e] OK - 符号 toggle ON/OFF")

# === 测试 2: 完整编辑器 + 自动撤回 ===
print("\n" + "=" * 60)
print("测试 2: 编辑器 live apply + 撤回")
print("=" * 60)

editor = WritingEditor()
editor.edit.setPlainText("这是一段用来测试符号面板的正文内容。")
# 选中文本
c = editor.edit.textCursor()
c.setPosition(3); c.setPosition(15, QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c)
before = editor.edit.toPlainText()

sp2 = SymbolPanel()
sp2.edge_apply.connect(editor.apply_symbol_live)
sp2.revert_live.connect(editor.revert_symbol_live)
sp2.insert_symbol.connect(editor.insert_text)
sp2.set_has_selection(True)

# 2a. 有选区 + 有方式 + 有符号 → 自动应用
sp2.mode_buttons["head"].click()
sp2._on_insert_symbol("《")
text_after = editor.edit.toPlainText()
assert text_after != before, "应该已应用符号"
print(f"[2a] OK - 自动应用: '{before}' → '{text_after}'")

# 2b. 取消符号 → 撤回
# 先恢复原选区
text_after = editor.edit.toPlainText()
c = editor.edit.textCursor()
# 找到 live session 的内容
assert editor._sym_session is not None, "应该有 live session"
start = editor._sym_session["start"]
end = start + editor._sym_session["last_len"]
c.setPosition(start); c.setPosition(end, QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c)
sp2.set_has_selection(True)
# 再点符号取消
sp2._on_insert_symbol("《")  # toggle off
reverted = editor.edit.toPlainText()
assert reverted == before, f"应该恢复原文: '{reverted}' == '{before}'"
print(f"[2b] OK - 撤回成功: '{reverted}'")

# 2c. 重新选中原段（2b 已撤回，live 会话已结束），选符号后切换方式应自动重算
c = editor.edit.textCursor()
c.setPosition(3)
c.setPosition(15, QTextCursor.KeepAnchor)
editor.edit.setTextCursor(c)
sp2.set_has_selection(True)
sp2._on_insert_symbol("》")        # 选符号（head 方式仍选中，即刻应用）
sp2.mode_buttons["tail"].click()  # 切到 tail（互斥自动取消 head）
new_text = editor.edit.toPlainText()
print(f"[2c] OK - 切换方式 tail: '{new_text}'")
assert "》" in new_text, f"tail 应该包含符号: '{new_text}'"

print("\n" + "=" * 60)
print("ALL PASS - 符号面板 toggle + 自动撤回 + live session")
print("=" * 60)
