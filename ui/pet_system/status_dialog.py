# ui/pet_system/status_dialog.py
"""墨码写作 - 墨灵状态面板。

展示养成系统的全部可见数值（以前这些值每秒都在算，但用户看不到）：
- 亲密度阶段 + 升级进度
- 饱腹 / 水分 / 精力 / 清洁 / 心情
- 当前最迫切的需求
- 今日码字、连续写作天数（把"养成"和"写作"绑在一起）
- 累计互动次数

数据来源：PetBrain.state + WritingStats。打开期间每秒刷新。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QFrame, QWidget,
)
from PySide6.QtCore import Qt, QTimer

from . import constants as C


# (state 字段, 显示名, 进度条颜色)
_STAT_DEFS = [
    ("hunger",      "饱腹", "#c98a4b"),
    ("thirst",      "水分", "#5b9bd5"),
    ("energy",      "精力", "#7bb87b"),
    ("cleanliness", "清洁", "#8e7cc3"),
    ("mood",        "心情", "#e086a0"),
]


class _StatRow(QWidget):
    """单行：名称 + 进度条 + 数值。"""

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)

        self.name = QLabel(label)
        self.name.setFixedWidth(36)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.value = QLabel("--")
        self.value.setFixedWidth(30)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        lay.addWidget(self.name)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.value)

        self._color = color
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: rgba(0,0,0,0.08);"
            f" border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {color};"
            f" border-radius: 4px; }}")

    def set_value(self, v: float):
        iv = int(max(0, min(100, round(v))))
        self.bar.setValue(iv)
        self.value.setText(str(iv))


class PetStatusDialog(QDialog):
    """墨灵状态面板。"""

    def __init__(self, brain, stats=None, theme: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._brain = brain
        self._stats = stats
        self._theme = theme or {}
        self.setWindowTitle("墨灵状态")
        self.setMinimumWidth(320)
        self._build_ui()
        self._apply_theme()
        self.refresh()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        # 阶段
        head = QHBoxLayout()
        head.setSpacing(8)
        self.level_badge = QLabel()
        self.level_badge.setObjectName("levelBadge")
        self.level_badge.setAlignment(Qt.AlignCenter)
        self.level_badge.setFixedHeight(26)
        head.addWidget(self.level_badge, 1)
        root.addLayout(head)

        # 升级进度
        self.level_text = QLabel()
        self.level_text.setWordWrap(True)
        root.addWidget(self.level_text)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(6)
        root.addWidget(self.level_bar)

        root.addSpacing(2)

        # 五维状态
        self._rows = {}
        for key, label, color in _STAT_DEFS:
            row = _StatRow(label, color, self)
            self._rows[key] = row
            root.addWidget(row)

        # 当前需求
        self.need_label = QLabel()
        self.need_label.setWordWrap(True)
        root.addWidget(self.need_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("petSep")
        root.addWidget(sep)

        # 写作数据
        self.writing_label = QLabel()
        self.writing_label.setWordWrap(True)
        root.addWidget(self.writing_label)

        # 累计互动
        self.total_label = QLabel()
        self.total_label.setWordWrap(True)
        root.addWidget(self.total_label)

        root.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("知道了")
        close_btn.clicked.connect(self.accept)
        close_btn.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _apply_theme(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"QLabel {{ color: {text_c}; background: transparent; }}"
            f"#levelBadge {{"
            f" background: {accent}; color: white;"
            f" border-radius: 13px; font-weight: 700; }}"
            f"QPushButton {{"
            f" background: {accent}; color: white; border: none;"
            f" border-radius: 8px; padding: 6px 22px; }}"
            f"QPushButton:hover {{ background: {accent}; }}"
            f"QFrame#petSep {{ color: {muted}; }}"
        )
        self.level_bar.setStyleSheet(
            f"QProgressBar {{ background: rgba(0,0,0,0.08);"
            f" border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {accent};"
            f" border-radius: 3px; }}")

    # ---------- 数据刷新 ----------

    def refresh(self):
        brain = self._brain
        if brain is None:
            return
        try:
            s = brain.state
        except RuntimeError:
            return

        lv, lv_name, next_th = C.level_from_intimacy(s.intimacy)
        floor = C.LEVELS[lv - 1][0]
        self.level_badge.setText(f"Lv.{lv} · {lv_name}")

        if next_th is None:
            self.level_text.setText(
                f"亲密度 {s.intimacy:,} · 已达最高阶段，剩下的都是陪伴")
            self.level_bar.setValue(100)
        else:
            span = max(1, next_th - floor)
            pct = int(max(0, min(100,
                                 (s.intimacy - floor) / span * 100)))
            self.level_text.setText(
                f"亲密度 {s.intimacy:,} / {next_th:,}"
                f"（再互动 {next_th - s.intimacy:,} 点升级）")
            self.level_bar.setValue(pct)

        for key, _label, _color in _STAT_DEFS:
            self._rows[key].set_value(getattr(s, key))

        need = s.current_need()
        if brain.sleeping:
            self.need_label.setText("墨灵正在睡觉，醒来会恢复精力")
        elif need:
            self.need_label.setText(
                f"墨灵现在{C.NEED_LABELS.get(need, '')}，"
                "右键她可以使用道具")
        else:
            self.need_label.setText("状态很好，正安静地陪着你")

        # 写作数据
        if self._stats is not None:
            try:
                today = self._stats.today_words()
                streak = self._stats.streak_days()
                minutes = self._stats.today_minutes()
                total = self._stats.total_words()
                self.writing_label.setText(
                    f"今日 {today:,} 字 · 活跃 {minutes} 分钟"
                    f" · 连续写作 {streak} 天 · 累计 {total:,} 字")
            except Exception:
                self.writing_label.setText("")
        else:
            self.writing_label.setText("")

        # 累计互动
        sleep_h = s.total_sleep_hours
        if sleep_h >= 1:
            sleep_text = f"睡觉 {sleep_h:.1f} 小时"
        else:
            sleep_text = f"睡觉 {int(sleep_h * 60)} 分钟"
        self.total_label.setText(
            f"喂食 {s.total_feed} 次 · 摸头 {s.total_pet} 次"
            f" · 玩耍 {s.total_play} 次 · 清洁 {s.total_clean} 次"
            f" · {sleep_text}")
