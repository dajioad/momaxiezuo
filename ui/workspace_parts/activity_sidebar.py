"""墨码写作 - 侧栏活动栏容器（VS Code 式）。

48px 图标活动栏 + 书架面板：
- 点图标：切换书架的 章节/资料/人物/高频词 标签
- 点已选中的图标（或底部折叠钮）：收起整个书架，只留活动栏
- 收起/展开、当前标签可通过 config 持久化
宽度恢复由 workspace 配合 splitter 完成（见 collapse_changed 信号）。
"""

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QToolButton, QButtonGroup,
)
from PySide6.QtCore import Qt, Signal, QSize

import qtawesome as qta

from ui.theme_style import qss_alpha
from ui.bookshelf import BookshelfPanel

# (tab 索引, 图标, 提示)
_ACTIVITIES = (
    (0, "fa5s.list-ul",      "章节（大纲 / 卷章管理）"),
    (1, "fa5s.folder-open",  "资料（世界观设定）"),
    (2, "fa5s.user-friends", "人物"),
    (3, "fa5s.hashtag",      "高频词"),
)

_BAR_WIDTH = 48


class _ActivitySidebar(QFrame):
    # 收起状态变化（True=已收起）；workspace 用来保存/恢复 splitter 宽度
    collapse_changed = Signal(bool)

    def __init__(self, work_store, parent=None):
        super().__init__(parent)
        self.setObjectName("activitySidebar")
        self._collapsed = False
        self._theme = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 活动栏（常驻 48px） ----------
        bar = QFrame()
        bar.setObjectName("activityBar")
        bar.setFixedWidth(_BAR_WIDTH)
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(0, 10, 0, 10)
        bl.setSpacing(4)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._tab_buttons = {}
        for idx, icon_name, tip in _ACTIVITIES:
            b = QToolButton()
            b.setCheckable(True)
            b.setIcon(qta.icon(icon_name, color="#9a8e76"))
            b._icon_name = icon_name
            b._tab_index = idx
            b.setFixedSize(40, 40)
            b.setIconSize(QSize(20, 20))
            b.setAutoRaise(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, i=idx: self._on_activity(i))
            self._group.addButton(b, idx)
            self._tab_buttons[idx] = b
            bl.addWidget(b)

        bl.addStretch(1)

        self.collapse_btn = QToolButton()
        self.collapse_btn.setFixedSize(40, 36)
        self.collapse_btn.setAutoRaise(True)
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        bl.addWidget(self.collapse_btn)
        root.addWidget(bar)

        # ---------- 书架 ----------
        self.bookshelf = BookshelfPanel(work_store)
        root.addWidget(self.bookshelf, 1)

        # 书架内 tab 被直接点击时，活动栏高亮同步
        self.bookshelf.tabs.currentChanged.connect(self._sync_checked)

    # ---------- 状态 ----------

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def current_tab(self) -> int:
        try:
            return self.bookshelf.tabs.currentIndex()
        except RuntimeError:
            return 0

    def set_tab(self, idx: int):
        try:
            if 0 <= idx < self.bookshelf.tabs.count():
                self.bookshelf.tabs.setCurrentIndex(idx)
        except RuntimeError:
            pass

    def set_collapsed(self, collapsed: bool):
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.bookshelf.setVisible(not collapsed)
        # 收起时把容器压到活动栏宽度；展开由 workspace 恢复 splitter
        self.setMaximumWidth(_BAR_WIDTH if collapsed else 16777215)
        self.collapse_btn.setIcon(qta.icon(
            "fa5s.angle-double-right" if collapsed
            else "fa5s.angle-double-left",
            color=self._theme.get("muted", "#9a8e76")))
        self.collapse_btn.setToolTip(
            "展开侧栏" if collapsed else "收起侧栏")
        if not collapsed:
            self._sync_checked()
        else:
            for b in self._tab_buttons.values():
                b.setChecked(False)
        self.collapse_changed.emit(collapsed)

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def _on_activity(self, idx: int):
        """点活动图标：收起则展开；已选中再点则收起；否则切 tab。"""
        if self._collapsed:
            self.set_collapsed(False)
            self.set_tab(idx)
            return
        if self.current_tab() == idx:
            self.set_collapsed(True)
            return
        self.set_tab(idx)

    def _sync_checked(self, *_):
        if self._collapsed:
            return
        idx = self.current_tab()
        btn = self._tab_buttons.get(idx)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    # ---------- 主题 ----------

    def apply_theme(self, theme: dict):
        self._theme = theme or {}
        t = self._theme
        sidebar = t.get("sidebar", "#efe8d8")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        text = t.get("text", "#3b342a")
        self.setStyleSheet(
            f"QFrame#activitySidebar {{ background: {sidebar}; }}"
            f"QFrame#activityBar {{ background: {sidebar};"
            f" border: none;"
            f" border-right: 1px solid {qss_alpha(muted, 0x1E)}; }}"
            f"QToolButton {{ border: none; border-radius: 10px;"
            f" background: transparent; }}"
            f"QToolButton:hover {{ background:"
            f" {qss_alpha(accent, 0x12)}; }}"
            f"QToolButton:checked {{ background:"
            f" {qss_alpha(accent, 0x1E)}; }}"
            f"QToolButton:checked:hover {{ background:"
            f" {qss_alpha(accent, 0x28)}; }}"
        )
        for idx, b in self._tab_buttons.items():
            on = (not self._collapsed and b.isChecked())
            b.setIcon(qta.icon(b._icon_name,
                               color=accent if on else (text if self._collapsed
                                                        else muted)))
        self.collapse_btn.setIcon(qta.icon(
            "fa5s.angle-double-right" if self._collapsed
            else "fa5s.angle-double-left", color=muted))
        try:
            self.bookshelf.apply_theme(t)
        except RuntimeError:
            pass
