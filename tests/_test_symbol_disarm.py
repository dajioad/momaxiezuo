# -*- coding: utf-8 -*-
"""验证：取消选中后再选中"其他文本"时，自动解除残留的符号 + 插入方式武装，
新选文本不会被上次的组合自动应用。同一选区的连续操作（live 重算）不受影响。

改动说明（相比旧版）：
  旧版调用 SymbolPanel.set_has_selection(True/False) 来模拟选区变化——
  但该 API 只更新 _has_selection 布尔标志，不触发 _disarm_all()。
  真实运行链路（workspace._connect_signals）连的是
  editor.selection_changed -> symbol_panel.set_selection_text，
  只有 set_selection_text 才会在「从无选区切到有选区」时解除武装。
  本版统一走 set_selection_text，行为与产品一致。
"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from PySide6.QtGui import QTextCursor
from ui.symbol_panel import SymbolPanel
from ui.writing_editor import WritingEditor

editor = WritingEditor()
editor.edit.setPlainText("第一段选择内容AAA。第二段另一段内容BBB。")
sp = SymbolPanel()
sp.edge_apply.connect(editor.apply_symbol_live)
sp.revert_live.connect(editor.revert_symbol_live)
sp.insert_symbol.connect(editor.insert_text)


def select(a, b):
    c = editor.edit.textCursor()
    c.setPosition(a)
    c.setPosition(b, QTextCursor.KeepAnchor)
    editor.edit.setTextCursor(c)


def emit_selection():
    """模拟 editor.selection_changed 信号链：把当前选区文本推给面板。

    必须用 set_selection_text——它是 workspace 真实连接的 API，
    也只有它在「从无选区切到有选区」时会触发 _disarm_all()。
    """
    sp.set_selection_text(editor.edit.textCursor().selectedText())


# 1) 选中 A(0..6) → 武装 符号《 + 方式 head → 自动应用
select(0, 6)
emit_selection()
sp.mode_buttons["head"].click()
sp._on_insert_symbol("《")
text1 = editor.edit.toPlainText()
assert "《" in text1 and sp._active_sym == "《" and sp._mode == "head"
assert sp.mode_buttons["head"].isChecked()
print(f"[1] PASS - 首次选中并应用：{text1}")

# 2) 取消选中（用户点空白处）→ 武装保留
select(0, 0)              # 塌缩成光标 = 无选区
emit_selection()
assert not sp._has_selection, "取消选中后面板应记录无选区"
assert sp._active_sym == "《" and sp._mode == "head", "取消选中期间武装可保留"
print("[2] PASS - 取消选中（尚未重选）")

# 3) 重新选中另一段文本 B → 应自动解除武装，且 B 不被加符号
doc = editor.edit.toPlainText()
i = doc.index("第二段另一段内容BBB")
select(i, i + 7)
emit_selection()
assert sp._active_sym == "", f"新选区应解除符号，仍为 {sp._active_sym!r}"
assert sp._mode is None, f"新选区应解除插入方式，仍为 {sp._mode}"
assert not sp.mode_buttons["head"].isChecked(), "方式按钮应弹起"
text2 = editor.edit.toPlainText()
assert "《" not in text2[i:i + 7], f"新选文本不应被自动加符号：{text2[i:i + 7]}"
print(f"[3] PASS - 选其他文本自动解除武装，新文本未被改动：{text2}")

# 4) 解除后需要重新点选才会应用：此时只点方式不应用（符号已空）
sp.mode_buttons["tail"].click()
select(i, i + 7)
emit_selection()          # 同选区再收到一次回调（had=True），不应误解除
assert sp._mode == "tail"
before = editor.edit.toPlainText()
assert before == text2, "无符号时不应有任何改动"
print("[4] PASS - 解除后无自动应用；同选区 True→True 不误解除")

# 5) 同一段选区的 live 切换仍然可用（不受新逻辑影响）
sp._on_insert_symbol("》")
assert editor.edit.toPlainText() != text2, "重新点符号后应正常应用"
# live 应用后 editor 会再发一次 selection_changed（选区文本已含 》）
# 同选区再次 set_selection_text 时 had=True，不应误触发 _disarm_all
emit_selection()
assert sp._active_sym == "》" and sp._mode == "tail", "同选区武装应保留"
print("[5] PASS - 同选区实时重算流程完好")

print("\nALL PASS - 重新选择其他文本时符号/方式自动解除")