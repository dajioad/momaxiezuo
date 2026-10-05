# -*- coding: utf-8 -*-
"""真正的互斥测试：全角符号"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.symbol_panel import SymbolPanel
sp = SymbolPanel()

def count_checked(sp):
    return sum(1 for btns in sp._sym_buttons.values() 
               for b in btns if b.isChecked())

def get_checked_labels(sp):
    return [sym for sym, btns in sp._sym_buttons.items() 
            for b in btns if b.isChecked()]

print("=== 符号按钮互斥（全角符号）===")

# 1. 点《
sp._on_insert_symbol("《")
print(f"点《: _active_sym={repr(sp._active_sym)}, checked={get_checked_labels(sp)}, count={count_checked(sp)}")
assert count_checked(sp) == 1, "应该只有《被选中"

# 2. 再点 》 → 应该只留 》（互斥切换）
sp._on_insert_symbol("》")
print(f"点》: _active_sym={repr(sp._active_sym)}, checked={get_checked_labels(sp)}, count={count_checked(sp)}")
assert count_checked(sp) == 1 and "》" in get_checked_labels(sp), "应该切换到》"

# 3. 点 ，（全角逗号）→ 只留 ，
sp._on_insert_symbol("，")
print(f"点，: _active_sym={repr(sp._active_sym)}, checked={get_checked_labels(sp)}, count={count_checked(sp)}")
assert count_checked(sp) == 1 and "，" in get_checked_labels(sp), "应该只有，"

# 4. 双字符《》→ 应该高亮《和》
sp._on_insert_symbol("《》")
print(f"点《》: _active_sym={repr(sp._active_sym)}, checked={get_checked_labels(sp)}, count={count_checked(sp)}")
assert "《" in get_checked_labels(sp) and "》" in get_checked_labels(sp)

# 5. 再点一次《》（toggle off）→ 全部取消
sp._on_insert_symbol("《》")
print(f"再点《》: _active_sym={repr(sp._active_sym)}, checked={get_checked_labels(sp)}, count={count_checked(sp)}")
assert count_checked(sp) == 0, "toggle off 后应该全取消"

print("\n=== 方式按钮互斥 ===")
sp2 = SymbolPanel()
sp2.mode_buttons["head"].click()
print(f"点 head: _mode={sp2._mode}, head.checked={sp2.mode_buttons['head'].isChecked()}")
assert sp2._mode == "head"
sp2.mode_buttons["tail"].click()
print(f"点 tail: _mode={sp2._mode}, head.checked={sp2.mode_buttons['head'].isChecked()}, tail.checked={sp2.mode_buttons['tail'].isChecked()}")
assert sp2._mode == "tail" and not sp2.mode_buttons["head"].isChecked(), "head 应该被取消"
sp2.mode_buttons["tail"].click()  # toggle off
print(f"再点 tail: _mode={sp2._mode}, tail.checked={sp2.mode_buttons['tail'].isChecked()}")
assert sp2._mode is None and not sp2.mode_buttons["tail"].isChecked()

print("\n🎉 全部互斥 + toggle 测试通过！")
