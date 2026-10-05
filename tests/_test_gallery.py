# -*- coding: utf-8 -*-
"""验证画廊→打开作品核心修复（不启动完整 Workspace）"""
import os, sys, tempfile
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

from utils.work_store import WorkStore

# 用临时目录
tmp = tempfile.mkdtemp(prefix="mmtest_")
data_root = os.path.join(tmp, "data")
store = WorkStore(data_root)

# 创建测试作品
work = store.create_work("测试小说", "墨码")
ch1 = work.add_chapter("第一章 开端")
work.write_chapter(ch1["id"], "这是第一章的内容。" * 20)
ch2 = work.add_chapter("第二章 发展")
work.write_chapter(ch2["id"], "这是第二章的内容。" * 30)
work.save_meta()

# 1. 验证 store.list_works 返回正确
works = store.list_works()
assert len(works) == 1, f"expected 1 work, got {len(works)}"
w_info = works[0]
print(f"[1] OK - work listed: title={w_info['title']}, chapters={w_info['chapter_count']}, words={w_info['words']}")

# 2. 封面渐变兜底色：同一书名确定性，空书名也能处理
from ui.workspace import WritingWorkspace
c1, c2 = WritingWorkspace._cover_gradient_colors("测试小说")
assert c1.startswith("#") and c2.startswith("#")
assert WritingWorkspace._cover_gradient_colors("测试小说") == (c1, c2)
empty = WritingWorkspace._cover_gradient_colors("")
assert isinstance(empty, tuple) and len(empty) == 2
print("[2] OK - _cover_gradient_colors: deterministic, handles empty seed")

# 3. 验证 _time_ago 相对时间
assert WritingWorkspace._time_ago("") == "", "empty time should return empty"
assert WritingWorkspace._time_ago("2020-01-01 00:00:00") == "2020-01-01", "old time should show date"
result = WritingWorkspace._time_ago("2099-01-01 00:00:00")  # 未来时间会报异常，应 fallback
print(f"[3] OK - _time_ago works")

# 4. 验证 WorkStore.open_work 可正常打开（关键：_open_work_by_path 内部依赖）
opened = store.open_work(w_info["path"])
assert opened is not None, "open_work should return Work object"
assert opened.title == "测试小说"
print(f"[4] OK - store.open_work works, title={opened.title}")

# 5. 验证 Work 对象有 chapters 和 total_words
assert len(opened.chapters) == 2
assert opened.total_words() > 0
print(f"[5] OK - work has {len(opened.chapters)} chapters, {opened.total_words()} words")

import shutil
shutil.rmtree(tmp, ignore_errors=True)
print("\nALL PASS - Gallery core logic verified")
