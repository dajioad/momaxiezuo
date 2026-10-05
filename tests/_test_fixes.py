# -*- coding: utf-8 -*-
"""功能修复回归：
1. 删除当前打开章节后，编辑器自动打开剩余第一章，不悬空
2. 删除最后一章后编辑器清空，不崩溃、不复活已删章节
3. AI 清空对话后旧请求的迟到 chunk/finished/failed 回调安全忽略
"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
app = QApplication.instance() or QApplication(sys.argv)
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
app.setFont(QFont("Microsoft YaHei", 9))

import utils.work_store as ws_mod
ws_mod.get_data_root = lambda: tempfile.mkdtemp()
from utils.config import ConfigManager
cfg = ConfigManager()
cfg.data_dir = tempfile.mkdtemp()
cfg.config_path = os.path.join(cfg.data_dir, "config.ini")
cfg._loaded = False
cfg.init_config()

from ui.main_window import MainWindow
w = MainWindow(cfg)
w.resize(1280, 820)
ws = w.workspace
created = ws.store.create_work("功能测试", "墨码")
c1 = created.add_chapter("第一章")
c2 = created.add_chapter("第二章")
created.write_chapter(c1["id"], "甲" * 10)
created.write_chapter(c2["id"], "乙" * 10)
created.save_meta()
ws._open_work_by_path(created.root_dir)
w.show()
QTest.qWait(50)
# 后续操作必须用工作台内存中的同一个 Work 实例（书架删除走的就是它）
work = ws.work
assert work is ws.bookshelf.work
c1, c2 = work.chapters[0]["id"], work.chapters[1]["id"]
work.write_chapter(c1, "甲" * 10)
work.write_chapter(c2, "乙" * 10)

# 1. 删除当前章（自动打开的第一章）→ 应自动切到剩余章节（原第二章）
ws.open_chapter(c1)
assert ws.current_chapter_id == c1
work.delete_chapter(c1)
ws.bookshelf.refresh_tree()
ws._update_titles()
QTest.qWait(10)
# 自动打开剩余章节列表中的第一章（即原第二章）
assert ws.current_chapter_id == c2, \
    f"期望跳转剩余第一章，实际 {ws.current_chapter_id}"
assert "乙" in ws.editor.get_text()
print("[1] PASS - 删除当前章节后自动打开剩余章节")

# 2. 删除最后一章 → 编辑器清空，current=None，再写内容不会复活文件
work.delete_chapter(c2)
ws.bookshelf.refresh_tree()
ws._update_titles()
QTest.qWait(10)
assert ws.current_chapter_id is None
assert ws.editor.get_text() == ""
assert work.get_chapter(c2) is None
print("[2] PASS - 删除最后一章后编辑器清空且不悬空")

# 3. AI 回调守卫：清空后旧序号回调必须静默忽略
ai = ws.ai_panel
ai._ai_bubble = None
bubble = ai._add_bubble("assistant", "思考中…")
ai._ai_bubble = bubble
ai._request_seq = 5
ai._buffer = ""
ai._on_chunk("迟到内容", seq=4)          # 旧请求
ai._on_finished("迟到完成", seq=4)
ai._on_failed("迟到失败", seq=4)
assert bubble.label.text() == "思考中…", "旧序号 chunk 不应更新气泡"
ai._on_chunk("当前内容", seq=5)
assert bubble.label.text() == "当前内容"
# 模拟清空对话（无 worker 运行）
ai.clear_conversation()
assert ai._ai_bubble is None
ai._on_chunk("清空后到达", seq=5)       # 气泡已删，必须不抛异常
ai._on_finished("清空后完成", seq=5)
print("[3] PASS - AI 清空对话/旧请求回调安全")

# 4. 已删除 id 的 flush_save 是 no-op，不会复活章节文件
ws.current_chapter_id = "deleted-xyz"
ws.flush_save()
assert work.get_chapter("deleted-xyz") is None
print("[4] PASS - 悬空 id 保存为 no-op")

print("ALL PASS - 功能修复回归通过")
