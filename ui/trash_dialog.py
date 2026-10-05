# ui/trash_dialog.py
"""墨码写作 - 回收站对话框"""

from datetime import datetime

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
from PySide6.QtCore import Qt, Signal, QEvent, QTimer

from utils.work_store import WorkStore
from utils.logger import get_logger
from ui.theme_style import qss_alpha, SEMANTIC_BAD
from ui.dialog_styles import make_empty_state

logger = get_logger()


class TrashDialog(QDialog):
    changed = Signal()

    def __init__(self, store: WorkStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._visible_row = -1
        self.setWindowTitle("回收站")
        self.setMinimumSize(760, 480)
        self._build_ui()
        self._refresh()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    return editor._theme or {}
                break
            if hasattr(p, "_theme") and getattr(p, "_theme"):
                return p._theme
            p = p.parent()
        return {}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        tip = QLabel(
            "删除的作品会先放到回收站，不会出现在作品列表。\n"
            "可以在此恢复，或彻底删除（彻底删除后无法恢复）。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("font-size: 13px; color: #9a8e76;")
        root.addWidget(tip)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["书名", "字数", "删除时间", "操作"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        # 关键修复 1：操作列改 Fixed，给足宽度，否则按钮文字被压扁
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.resizeSection(3, 180)
        h.setHighlightSections(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

        # 空状态页：与表格同一切换栈
        self._stack = QStackedWidget()
        self._stack.addWidget(self.table)
        self._empty_state = make_empty_state(
            "fa5s.trash-alt",
            "回收站空荡荡的",
            "删除的作品会先放到这里，可以恢复",
            self._resolve_theme(),
        )
        self._stack.addWidget(self._empty_state)
        root.addWidget(self._stack, 1)

        bottom = QHBoxLayout()
        self.empty_btn = QPushButton("清空回收站")
        self.empty_btn.setCursor(Qt.PointingHandCursor)
        self.empty_btn.clicked.connect(self._empty_all)
        bottom.addWidget(self.empty_btn)
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        t = self._resolve_theme()
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")

        # 关键修复 2：表头用 !important 式的双写，确保 transparent 生效
        self.setStyleSheet(
            f"QTableWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x33)}; border-radius: 10px;"
            f" alternate-background-color: {qss_alpha(muted, 0x08)};"
            f" gridline-color: transparent; outline: none; }}"
            f"QTableWidget::item {{"
            f"  border-bottom: 1px solid {qss_alpha(muted, 0x12)};"
            f"  padding: 10px 8px;"
            f"}}"
            f"QTableWidget::item:hover {{"
            f"  background-color: {qss_alpha(accent, 0x08)};"
            f"}}"
            f"QTableWidget::item:selected {{"
            f"  background-color: {qss_alpha(accent, 0x22)};"
            f"  color: {text};"
            f"}}"
            f"QHeaderView {{ background: transparent; border: none; }}"
            f"QHeaderView::section {{"
            f"  background: transparent;"
            f"  background-color: transparent;"
            f"  color: {muted};"
            f"  border: none;"
            f"  border-bottom: 2px solid {qss_alpha(muted, 0x33)};"
            f"  padding: 10px 8px;"
            f"  font-weight: bold;"
            f"}}"
            f"QHeaderView::section:vertical {{"
            f"  background: transparent; border: none;"
            f"}}"
            f"QTableCornerButton::section {{ background: transparent; border: none; }}"
        )

    # -------- 关键修复 3：对话框显示后强制隐藏所有操作按钮 --------
    def showEvent(self, event):
        """Qt 在对话框显示时会强制 show 所有 cell widget，
        导致我们在 _refresh 里 hide() 的按钮全部重新出现。
        这里在 show 之后再统一 hide 一次。
        """
        super().showEvent(event)
        QTimer.singleShot(0, self._hide_all_action_cells)

    def _hide_all_action_cells(self):
        self._visible_row = -1
        for i in range(self.table.rowCount()):
            cell = self.table.cellWidget(i, 3)
            if cell is not None:
                cell.hide()

    def _refresh(self):
        items = self.store.list_trashed()

        self.table.setUpdatesEnabled(False)
        try:
            if not items:
                self.table.clearSpans()
                self.table.setRowCount(1)
                item = QTableWidgetItem("（回收站是空的）")
                item.setForeground(Qt.gray)
                self.table.setItem(0, 0, item)
                self.table.setSpan(0, 0, 1, 4)
                self.empty_btn.setEnabled(False)
                self._visible_row = -1
                self._stack.setCurrentWidget(self._empty_state)
                return

            self._stack.setCurrentWidget(self.table)
            self.empty_btn.setEnabled(True)
            self.table.clearSpans()
            self.table.setRowCount(len(items))

            t = self._resolve_theme()
            accent = t.get("accent", "#b08a4e")
            muted = t.get("muted", "#9a8e76")

            restore_ss = (
                f"QPushButton {{ border: 1px solid {accent};"
                f" border-radius: 6px; padding: 4px 12px; background: transparent;"
                f" color: {accent}; font-size: 12px; font-weight: 500; }}"
                f"QPushButton:hover {{ background: {qss_alpha(accent, 0x18)};"
                f" border-color: {accent}; color: {accent}; }}"
            )
            del_ss = (
                f"QPushButton {{ border: 1px solid {qss_alpha(SEMANTIC_BAD, 0x66)};"
                f" border-radius: 6px; padding: 4px 12px; background: transparent;"
                f" color: {SEMANTIC_BAD}; font-size: 12px; }}"
                f"QPushButton:hover {{ background: {qss_alpha(SEMANTIC_BAD, 0x15)};"
                f" border-color: {SEMANTIC_BAD}; }}"
            )

            for i, info in enumerate(items):
                self.table.setItem(i, 0, QTableWidgetItem(info.get("title", "未命名")))
                self.table.setItem(
                    i, 1, QTableWidgetItem(f"{info.get('words', 0):,} 字")
                )
                dt = info.get("deleted_at", "")
                if dt and len(dt) >= 15:
                    try:
                        t_obj = datetime.strptime(dt[:15], "%Y%m%d_%H%M%S")
                        dt = t_obj.strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        pass
                self.table.setItem(i, 2, QTableWidgetItem(dt))

                wrap = QWidget()
                wrap.setProperty("trash_path", info["path"])
                row = QHBoxLayout(wrap)
                row.setContentsMargins(4, 0, 4, 0)
                row.setSpacing(6)
                restore_btn = QPushButton("恢复")
                restore_btn.setCursor(Qt.PointingHandCursor)
                restore_btn.setStyleSheet(restore_ss)
                restore_btn.clicked.connect(
                    lambda _=False, p=info["path"]: self._restore(p)
                )

                del_btn = QPushButton("彻底删除")
                del_btn.setCursor(Qt.PointingHandCursor)
                del_btn.setStyleSheet(del_ss)
                del_btn.clicked.connect(
                    lambda _=False, p=info["path"], t=info.get(
                        "title", "未命名"
                    ): self._permanent_delete(p, t)
                )
                row.addWidget(restore_btn)
                row.addWidget(del_btn)

                self.table.setCellWidget(i, 3, wrap)
                wrap.hide()

            self._visible_row = -1
            if len(items) == 1:
                cell = self.table.cellWidget(0, 3)
                if cell is not None:
                    cell.show()
                    self._visible_row = 0
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

    def eventFilter(self, obj, event):
        if obj is self.table.viewport():
            et = event.type()
            if et == QEvent.MouseMove:
                pos = event.position().toPoint()
                idx = self.table.indexAt(pos)
                row = idx.row() if idx.isValid() else -1
                self._set_visible_row(row)
            elif et == QEvent.Leave:
                self._set_visible_row(-1)
        return super().eventFilter(obj, event)

    def _set_visible_row(self, row: int):
        if self._visible_row == row:
            return
        self._visible_row = row
        for i in range(self.table.rowCount()):
            cell = self.table.cellWidget(i, 3)
            if cell is not None:
                cell.setVisible(i == row)

    # ---------------- 删除 / 恢复 ----------------

    def _restore(self, path: str):
        if (
            QMessageBox.question(
                self,
                "恢复作品",
                "把这份作品恢复到作品列表吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            != QMessageBox.Yes
        ):
            return
        self._remove_row_by_path(path)
        QTimer.singleShot(0, lambda: self._do_restore_io(path))

    def _do_restore_io(self, path: str):
        try:
            final = self.store.restore_work(path)
        except Exception as e:
            logger.warning(f"恢复失败: {e}")
            QMessageBox.critical(self, "恢复失败", str(e))
            self._refresh()
            return
        QMessageBox.information(self, "已恢复", f"作品已恢复到：\n{final}")
        QTimer.singleShot(0, self.changed.emit)

    def _permanent_delete(self, path: str, title: str):
        if (
            QMessageBox.question(
                self,
                "彻底删除",
                f"确定彻底删除《{title}》吗？\n\n"
                "此操作不可恢复，包括该作品的所有历史快照。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self._remove_row_by_path(path)
        QTimer.singleShot(0, lambda: self._do_delete_io(path))

    def _do_delete_io(self, path: str):
        try:
            self.store.permanent_delete(path)
        except Exception as e:
            logger.warning(f"彻底删除失败: {e}")
            QMessageBox.critical(self, "删除失败", str(e))
            self._refresh()
            return
        QTimer.singleShot(0, self.changed.emit)

    def _remove_row_by_path(self, path: str):
        target = -1
        for i in range(self.table.rowCount()):
            cell = self.table.cellWidget(i, 3)
            if cell is not None and cell.property("trash_path") == path:
                target = i
                break
        if target < 0:
            self._refresh()
            return

        self.table.setUpdatesEnabled(False)
        try:
            self.table.removeRow(target)
            self._visible_row = -1

            if self.table.rowCount() == 0:
                self.table.clearSpans()
                self.table.setRowCount(1)
                item = QTableWidgetItem("（回收站是空的）")
                item.setForeground(Qt.gray)
                self.table.setItem(0, 0, item)
                self.table.setSpan(0, 0, 1, 4)
                self.empty_btn.setEnabled(False)
                self._stack.setCurrentWidget(self._empty_state)
            elif self.table.rowCount() == 1:
                cell = self.table.cellWidget(0, 3)
                if cell is not None:
                    cell.show()
                    self._visible_row = 0
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

    def _empty_all(self):
        if (
            QMessageBox.question(
                self,
                "清空回收站",
                "确定清空回收站吗？\n\n回收站里所有作品及其历史快照将被永久删除。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        QTimer.singleShot(0, self._do_empty_io)

    def _do_empty_io(self):
        try:
            n = self.store.empty_trash()
        except Exception as e:
            QMessageBox.critical(self, "清空失败", str(e))
            return
        QMessageBox.information(self, "完成", f"已清空 {n} 个作品。")
        self._refresh()
        QTimer.singleShot(0, self.changed.emit)
