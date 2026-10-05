# -*- coding: utf-8 -*-
"""封面高 DPI 适配回归：

在 150% 系统缩放（QT_SCREEN_SCALE_FACTORS=1.5 + PassThrough）下：
  1. 控件 devicePixelRatioF() 应为 1.5 —— 验证环境变量确实生效
  2. 封面必须被实际绘制到控件上（grab 出蓝色像素，不是兜底色块）

关于自绘控件：_CoverLabel 重写 paintEvent，从 self._src（原始上传图）
按 devicePixelRatioF() 缩放后 drawPixmap。所以 _src.devicePixelRatio()
恒为 1.0 是正常的——真实的 1.5 倍放大发生在 paintEvent 内部。要验证
"上屏清晰度"要看渲染结果（grab），不是 _src。

直接运行本脚本即可：若未带缩放环境变量，会以 150% 重新拉起自身。
"""
import os, sys, subprocess, tempfile

CHILD_FLAG = "MM_COVER_HIDPI_CHILD"
if os.environ.get(CHILD_FLAG) != "1":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCREEN_SCALE_FACTORS"] = "1.5"
    env[CHILD_FLAG] = "1"
    r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                       cwd=root, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit(r.returncode)
    sys.exit(0)

# ---------------- 子进程：150% 缩放环境 ----------------
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
from PySide6.QtGui import QFont, QFontDatabase, QPixmap, QColor
from PySide6.QtTest import QTest

app = QApplication.instance() or QApplication(sys.argv)
import platform
if platform.system() == "Windows":
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"):
        if os.path.exists(fp):
            QFontDatabase.addApplicationFont(fp)
app.setFont(QFont("Microsoft YaHei", 9))

dpr = app.primaryScreen().devicePixelRatio()
assert abs(dpr - 1.5) < 0.01, f"测试前置失败：DPR 应为 1.5，实际 {dpr}"

import utils.work_store as ws_mod
_tmp = tempfile.mkdtemp(prefix="mmhidpi_")
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
wk = ws_ui.store.create_work("高DPI封面", "")
wk.add_chapter("第一章")
up = os.path.join(_tmp, "up.png")
pm = QPixmap(600, 800)
pm.fill(QColor(30, 90, 200))
pm.save(up)
wk.set_cover(up)

w.resize(1280, 820)
w.show()
ws_ui._refresh_gallery()
QTest.qWait(200)

# ---- 找到绘制了封面的卡片 ----
cover = None
for f in ws_ui._gallery_container.findChildren(QFrame):
    if f.objectName() == "workCard":
        cover = f.findChild(_CoverLabel)
        break
assert cover is not None and cover._src is not None, "封面必须被实际加载"

# ---- 环境验证：控件所在屏幕 DPR 应为 1.5 ----
# 说明：_CoverLabel 是自绘控件，_src 是原始上传图（DPR 恒为 1.0）。
# 真实的 1.5 倍物理放大发生在 paintEvent 内部——self.devicePixelRatioF()
# 决定缩放倍率。所以这里验证"控件 DPR"，不是"_src DPR"。
assert abs(cover.devicePixelRatioF() - 1.5) < 0.01, \
    f"控件 DPR 应为 1.5，实际 {cover.devicePixelRatioF()}"

# ---- 渲染验证：封面内容必须被真实绘制（不是兜底色块）----
img = cover.grab().toImage()
assert img.width() > 0 and img.height() > 0, "封面应有渲染结果"
col = img.pixelColor(img.width() // 2, img.height() // 2)
assert abs(col.blue() - 200) < 12 and col.red() < 70, \
    f"封面内容应为上传的蓝色图，实际 {col.red()},{col.green()},{col.blue()}"

print(f"ALL PASS - 封面 150% 高DPI：控件 {cover.width()}x{cover.height()}，"
      f"渲染 {img.width()}x{img.height()}，dpr={cover.devicePixelRatioF()}")