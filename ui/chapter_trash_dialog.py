# ui/chapter_trash_dialog.py
"""墨码写作 - 章节回收站

- 列出已删除的章节（在保留期内）
- 双击或点「恢复」：放回章节树原位（卷归属保留）
- 点「彻底删除」：删章节文件 + 章纲 + 全部快照
- 顶部「清空回收站」：一次性彻底删除所有条目
- 底部显示保留策略提示
"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QWidget,
    QStackedWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from ui.theme_style import qss_alpha, accent_button_qss, SEMANTIC_BAD
from ui.dialog_styles import make_empty_state
from utils.work_store import CHAPTER_TRASH_RETENTION_DAYS
from utils.logger import get_logger

logger = get_logger()


class ChapterTrashDialog(QDialog):
    """章节回收站。"""

    changed = Signal()

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self.work = work
        self._theme = self._resolve_theme()
        self.setWindowTitle(f"章节回收站 - {work.title}")
        self.setMinimumSize(720, 480)
        self._build_ui()
        self._apply_theme()
        self._refresh()

    # ---------- 主题 ----------

    def _resolve_theme(self) -> dict:
        # 统一走 theme_style._resolve_dialog_theme：
        #   1. 沿 parent 链找任意带 _theme 属性的 widget（editor / panel
        #      都能命中），与其它对话框保持一致
        #   2. 找不到时用 app palette 兜底
        # 之前每个对话框各写一份 while 循环，逻辑容易漂移；统一下来
        # 避免"切了主题但这个对话框没跟随"的问题。
        from ui.theme_style import _resolve_dialog_theme
        return _resolve_dialog_theme(self)

    def _apply_theme(self):
        t = self._theme or {}
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"ChapterTrashDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#ctTitle {{ font-size: 15px; font-weight: 600; }}"
            f"QLabel#ctTip {{ color: {muted}; font-size: 12px; }}"
            f"QTableWidget {{"
            f" background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px;"
            f" alternate-background-color: {qss_alpha(muted, 0x08)};"
            f" gridline-color: transparent; outline: none; }}"
            f"QTableWidget::item {{"
            f" border-bottom: 1px solid {qss_alpha(muted, 0x12)};"
            f" padding: 10px 8px; }}"
            f"QTableWidget::item:hover {{"
            f" background-color: {qss_alpha(accent, 0x08)}; }}"
            f"QTableWidget::item:selected {{"
            f" background-color: {qss_alpha(accent, 0x22)}; color: {text}; }}"
            f"QHeaderView {{ background: transparent; border: none; }}"
            f"QHeaderView::section {{"
            f" background: transparent; color: {muted}; border: none;"
            f" border-bottom: 2px solid {qss_alpha(muted, 0x33)};"
            f" padding: 10px 8px; font-weight: 600; font-size: 12px; }}"
            f"QTableCornerButton::section {{ background: transparent; border: none; }}"
        )

    # ---------- UI ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("章节回收站")
        title.setObjectName("ctTitle")
        root.addWidget(title)

        tip = QLabel(
            "删除的章节会先放到这里，可以在保留期内恢复。"
            f"超过 {CHAPTER_TRASH_RETENTION_DAYS} 天的章节在下次打开作品时自动清理。\n"
            "「彻底删除」会连同该章节的**全部历史快照**一起删除，不可恢复。"
        )
        tip.setObjectName("ctTip")
        tip.setWordWrap(True)
        root.addWidget(tip)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["章节标题", "卷", "字数", "删除时间"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setHighlightSections(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setMouseTracking(True)
        self.table.doubleClicked.connect(self._on_double_click)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.table)
        self._empty = make_empty_state(
            "fa5s.trash-alt", "回收站空荡荡的",
            "删除的章节会先放到这里，可以恢复", self._theme
        )
        self._stack.addWidget(self._empty)
        root.addWidget(self._stack, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.clear_btn = QPushButton("清空回收站")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_all)
        bottom.addWidget(self.clear_btn)

        bottom.addStretch()

        self.purge_btn = QPushButton("彻底删除")
        self.purge_btn.setCursor(Qt.PointingHandCursor)
        self.purge_btn.setToolTip("删除选中章节及其全部历史快照，不可恢复")
        self.purge_btn.clicked.connect(self._purge_selected)
        bottom.addWidget(self.purge_btn)

        self.restore_btn = QPushButton("恢复")
        self.restore_btn.setProperty("variant", "primary")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.setToolTip("把选中章节放回章节树")
        self.restore_btn.clicked.connect(self._restore_selected)
        bottom.addWidget(self.restore_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ---------- 数据 ----------

    def _refresh(self):
        items = self.work.trashed_chapters

        t = self._theme or {}
        muted = t.get("muted", "#9a8e76")
        bad = QColor(SEMANTIC_BAD)

        # 清空按钮样式
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)
        self.restore_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                accent_hover,
                selector="QPushButton",
                padding="6px 18px",
                bold=True,
            )
        )

        self.purge_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {qss_alpha(SEMANTIC_BAD, 0x66)};"
            f" border-radius: 6px; padding: 6px 14px; background: transparent;"
            f" color: {SEMANTIC_BAD}; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {qss_alpha(SEMANTIC_BAD, 0x15)};"
            f" border-color: {SEMANTIC_BAD}; }}"
        )

        if not items:
            self._stack.setCurrentWidget(self._empty)
            self.clear_btn.setEnabled(False)
            self.purge_btn.setEnabled(False)
            self.restore_btn.setEnabled(False)
            return

        self._stack.setCurrentWidget(self.table)
        self.clear_btn.setEnabled(True)
        self.purge_btn.setEnabled(True)
        self.restore_btn.setEnabled(True)

        self.table.setRowCount(len(items))
        for i, c in enumerate(items):
            title_item = QTableWidgetItem(c.get("title", "") or "（无标题）")
            title_item.setData(Qt.UserRole, c.get("id", ""))
            self.table.setItem(i, 0, title_item)

            vol = c.get("volume", "") or "正文"
            vol_item = QTableWidgetItem(vol)
            vol_item.setForeground(QColor(muted))
            self.table.setItem(i, 1, vol_item)

            wc = int(c.get("word_count", 0))
            wc_item = QTableWidgetItem(f"{wc:,} 字")
            wc_item.setForeground(QColor(muted))
            self.table.setItem(i, 2, wc_item)

            deleted_at = c.get("deleted_at", "") or "—"
            dt_item = QTableWidgetItem(deleted_at)
            dt_item.setForeground(QColor(muted))
            self.table.setItem(i, 3, dt_item)

    def _selected_ids(self):
        ids = []
        for item in self.table.selectedItems():
            if item.column() == 0:
                cid = item.data(Qt.UserRole)
                if cid:
                    ids.append(cid)
        return ids

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    # ---------- 操作 ----------

    def _on_double_click(self, _index):
        self._restore_selected()

    def _restore_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选中要恢复的章节。")
            return
        n = 0
        for cid in ids:
            if self.work.restore_chapter(cid):
                n += 1
        if n:
            QMessageBox.information(self, "已恢复", f"已恢复 {n} 个章节。")
            self._refresh()
            self.changed.emit()

    def _purge_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选中要彻底删除的章节。")
            return
        ret = QMessageBox.question(
            self,
            "彻底删除",
            f"确定彻底删除选中的 {len(ids)} 个章节吗？\n\n"
            "此操作会同时删除：\n"
            "  · 章节正文文件\n"
            "  · 该章节的全部历史快照\n\n"
            "不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        for cid in ids:
            try:
                self.work.permanently_delete_chapter(cid)
            except Exception as e:
                logger.warning(f"彻底删除章节失败 {cid}: {e}")
        self._refresh()
        self.changed.emit()

    def _clear_all(self):
        items = self.work.trashed_chapters
        if not items:
            return
        ret = QMessageBox.question(
            self,
            "清空回收站",
            f"确定清空回收站吗？\n\n"
            f"将彻底删除全部 {len(items)} 个章节及其历史快照，不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        for c in list(items):
            try:
                self.work.permanently_delete_chapter(c.get("id"))
            except Exception as e:
                logger.warning(f"清空回收站失败 {c.get('id')}: {e}")
        self._refresh()
        self.changed.emit()
