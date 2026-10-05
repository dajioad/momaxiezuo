"""墨码写作 - 编辑器顶部的章节标题带"""

from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QHBoxLayout
from PySide6.QtCore import Qt, Signal

from ui.theme_style import qss_alpha


class _ChapterHeader(QFrame):
    """编辑器顶部的章节标题带。标题可直接编辑（回车/失焦提交）。

    title_changed 信号带 (chapter_id, new_title)：
    - 带上 chapter_id 是为了防止「编辑标题时切换章节」的竞态——若只传
      new_title，Qt 处理焦点变化和点击章节树的顺序不确定，可能把新标题
      写到错误的章节上。带上 id 后，无论谁先执行，都写到该写的那一章。
    """

    title_changed = Signal(str, str)  # chapter_id, new_title

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chapterHeader")
        # 单行紧凑标题带：左侧章节名、右侧卷/字数，省出垂直空间给正文
        self.setFixedHeight(52)
        self._theme = {}
        self._chapter_id = ""
        self._last_title = ""  # 已提交的标题缓存，用于去重

        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(16)

        # 标题：无边框 QLineEdit，看起来像文字，点进去能改
        self.title_edit = QLineEdit("")
        self.title_edit.setObjectName("chapterHeaderTitle")
        self.title_edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title_edit.setFrame(False)
        self.title_edit.setMaxLength(200)
        self.title_edit.setPlaceholderText("（无标题）")
        self.title_edit.setToolTip("点击可编辑章节标题；回车或失焦保存")
        # editingFinished：回车或失焦时触发（Qt 对 QLineEdit 的约定行为）
        self.title_edit.editingFinished.connect(self._on_title_edited)
        lay.addWidget(self.title_edit, 1)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("chapterHeaderMeta")
        self.meta_label.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.meta_label, 0)

        # 右侧状态栏插槽：本章字数 / 目标进度（workspace 填入）
        self._right_bar = QHBoxLayout()
        self._right_bar.setContentsMargins(0, 0, 0, 0)
        self._right_bar.setSpacing(6)
        lay.addLayout(self._right_bar, 0)

    def right_bar(self) -> QHBoxLayout:
        return self._right_bar

    def _on_title_edited(self):
        """用户提交标题。与缓存不同才 emit，避免重复写盘。"""
        new_title = self.title_edit.text().strip()
        if new_title == self._last_title:
            return
        self._last_title = new_title
        self.title_changed.emit(self._chapter_id, new_title)

    def set_info(
        self, title: str, volume: str = "", words: int = 0, chapter_id: str = ""
    ):
        """刷新标题带显示。

        编辑中不覆盖用户输入——否则打字时 _update_word_status 频繁调用
        set_info 会把输入框里的内容重置掉。
        """
        self._chapter_id = chapter_id
        self._last_title = title or ""
        if not self.title_edit.hasFocus():
            self.title_edit.setText(title or "")
        # 字数已移到右侧本章状态栏，这里只显示卷名
        self.meta_label.setText(volume or "")
        self.meta_label.setVisible(bool(volume))

    def clear(self):
        self._chapter_id = ""
        self._last_title = ""
        if not self.title_edit.hasFocus():
            self.title_edit.clear()
        self.meta_label.setText("")

    def apply_theme(self, theme: dict):
        t = theme or {}
        self._theme = t
        bg = t.get("editor_bg", "#fcfaf4")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            f"QFrame#chapterHeader {{ background-color: {bg};"
            f" border-bottom: 1px solid {qss_alpha(muted, 0x22)}; }}"
            f"QLineEdit#chapterHeaderTitle {{ color: {text};"
            f" font-size: 17px; font-weight: 700; letter-spacing: 1px;"
            f" background: transparent; border: none;"
            f" padding: 6px 10px; border-radius: 8px;"
            f" selection-background-color: {accent}; }}"
            f"QLineEdit#chapterHeaderTitle:focus {{"
            f" background: {qss_alpha(accent, 0x14)}; }}"
            f"QLabel#chapterHeaderMeta {{ color: {muted};"
            f" font-size: 12px; letter-spacing: 0.5px;"
            f" background: transparent; padding: 0 8px 0 0;"
            f" border: none; }}"
            # 右侧本章字数/目标（从状态栏移来）
            f"QLabel#sbChapter {{ color: {accent}; font-size: 12px;"
            f" font-weight: 700; background: transparent; }}"
            f"QLabel#sbTarget {{ color: {muted}; font-size: 12px;"
            f" background: transparent; }}"
            f"QProgressBar#sbChapterBar {{ background:"
            f" {qss_alpha(muted, 0x22)}; border: none; border-radius: 3px; }}"
            f"QProgressBar#sbChapterBar::chunk {{ background-color: {accent};"
            f" border-radius: 3px; }}"
        )
