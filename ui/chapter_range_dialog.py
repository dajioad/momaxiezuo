# ui/chapter_range_dialog.py
"""墨码写作 - 章节区间选择对话框（墨灵试读 / 批量提取关系共用）。

选择起始章与结束章，实时显示：章节数、合计字数、按每章发送上限
估算的预计发送字数与请求次数。调用方拿到区间后自行二次确认。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDialogButtonBox,
)
from PySide6.QtCore import Qt

from ui.theme_style import apply_dialog_theme


class ChapterRangeDialog(QDialog):
    def __init__(self, work, parent=None, theme: dict = None,
                 title: str = "选择章节范围",
                 per_chapter_cap: int = 3800,
                 current_chapter_id: str = "",
                 default_all: bool = False,
                 note: str = ""):
        super().__init__(parent)
        self._work = work
        self._cap = per_chapter_cap
        self._current_id = current_chapter_id
        self.setWindowTitle(title)
        self.setMinimumWidth(460)

        self._chapters = list(getattr(work, "chapters", []) or [])
        self._labels = [self._chapter_label(c) for c in self._chapters]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(12)

        head = QLabel(title)
        head.setObjectName("rangeTitle")
        lay.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.start_combo = QComboBox()
        self.end_combo = QComboBox()
        for cb in (self.start_combo, self.end_combo):
            cb.addItems(self._labels)
        row.addWidget(QLabel("从"))
        row.addWidget(self.start_combo, 1)
        row.addWidget(QLabel("到"))
        row.addWidget(self.end_combo, 1)
        lay.addLayout(row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        for text, fn in (
            ("前三章", self._pick_first3),
            ("到当前章", self._pick_to_current),
            ("全部", self._pick_all),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            preset_row.addWidget(b)
        preset_row.addStretch()
        lay.addLayout(preset_row)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setObjectName("rangeInfo")
        lay.addWidget(self.info_label)

        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setObjectName("rangeNote")
            lay.addWidget(n)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setText("下一步")
        lay.addWidget(buttons)

        self.start_combo.currentIndexChanged.connect(self._on_changed)
        self.end_combo.currentIndexChanged.connect(self._on_changed)

        self._init_selection(current_chapter_id, default_all)
        apply_dialog_theme(self, theme=theme)
        try:
            muted = self.palette().placeholderText().color().name()
        except Exception:
            muted = "#928a7c"
        self.info_label.setStyleSheet(
            f"#rangeInfo {{ color: {muted}; font-size: 12.5px; }}")
        if note:
            n.setStyleSheet(
                f"#rangeNote {{ color: {muted}; font-size: 12px; }}")

    @staticmethod
    def _chapter_label(c: dict) -> str:
        words = int(c.get("word_count", 0) or 0)
        return f"{c.get('title', '') or c.get('id', '')}（{words:,} 字）"

    def _init_selection(self, current_id: str, default_all: bool):
        n = len(self._chapters)
        if n == 0:
            return
        if default_all:
            start, end = 0, n - 1
        else:
            cur = next(
                (i for i, c in enumerate(self._chapters)
                 if c.get("id") == current_id), -1)
            if cur > 0:
                start, end = 0, cur
            else:
                start, end = 0, min(2, n - 1)
        self.start_combo.setCurrentIndex(start)
        self.end_combo.setCurrentIndex(end)

    def _pick_first3(self):
        if not self._chapters:
            return
        self.start_combo.setCurrentIndex(0)
        self.end_combo.setCurrentIndex(min(2, len(self._chapters) - 1))

    def _pick_to_current(self):
        if not self._chapters:
            return
        self.start_combo.setCurrentIndex(0)
        cur = next(
            (i for i, c in enumerate(self._chapters)
             if c.get("id") == self._current_id), -1)
        self.end_combo.setCurrentIndex(
            cur if cur >= 0 else len(self._chapters) - 1)

    def _pick_all(self):
        if not self._chapters:
            return
        self.start_combo.setCurrentIndex(0)
        self.end_combo.setCurrentIndex(len(self._chapters) - 1)

    def _on_changed(self, *_):
        s = self.start_combo.currentIndex()
        e = self.end_combo.currentIndex()
        if s > e:
            # 不打断用户选择，仅提示
            self.info_label.setText("起始章在结束章之后，请调整区间。")
            return
        total_words = sum(
            int(c.get("word_count", 0) or 0)
            for c in self._chapters[s:e + 1])
        est_send = sum(
            min(int(c.get("word_count", 0) or 0), self._cap)
            for c in self._chapters[s:e + 1])
        self.info_label.setText(
            f"共 {e - s + 1} 章　·　合计约 {total_words:,} 字　·　"
            f"预计发送约 {est_send:,} 字（每章最多截取 {self._cap:,} 字），"
            f"约 {e - s + 1} 次 AI 请求")

    def selected_chapters(self) -> list:
        s = self.start_combo.currentIndex()
        e = self.end_combo.currentIndex()
        if s < 0 or e < s:
            return []
        return self._chapters[s:e + 1]
