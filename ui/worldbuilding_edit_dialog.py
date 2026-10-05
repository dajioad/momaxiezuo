# ui/worldbuilding_edit_dialog.py
"""墨码写作 - 世界观条目编辑对话框"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QDialogButtonBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PySide6.QtCore import Qt
from ui.theme_style import apply_dialog_theme
from utils import worldbuilding as wb


class _AttrTable(QTableWidget):
    """属性键值对表。"""

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["键", "值"])
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 100)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setMinimumHeight(100)

    def set_attributes(self, attrs: dict):
        self.setRowCount(0)
        for k, v in (attrs or {}).items():
            self._add_row(k, v)
        self._add_row("", "")  # 空行，方便直接输入

    def _add_row(self, k, v):
        r = self.rowCount()
        self.insertRow(r)
        self.setItem(r, 0, QTableWidgetItem(str(k)))
        self.setItem(r, 1, QTableWidgetItem(str(v)))

    def get_attributes(self) -> dict:
        out = {}
        for r in range(self.rowCount()):
            k_item = self.item(r, 0)
            v_item = self.item(r, 1)
            k = k_item.text().strip() if k_item else ""
            v = v_item.text().strip() if v_item else ""
            if k:
                out[k] = v
        return out

    def keyPressEvent(self, event):
        # Tab 到最后一格时自动新增一行
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            r = self.currentRow()
            if r == self.rowCount() - 1:
                self._add_row("", "")
            self.setCurrentCell(r + 1, self.currentColumn())
            return
        super().keyPressEvent(event)


class WorldbuildingEditDialog(QDialog):
    """新建 / 编辑世界观条目。"""

    def __init__(self, parent=None, entry: dict = None, default_type: str = "location"):
        super().__init__(parent)
        self._entry = entry or {}
        self.setWindowTitle("编辑条目" if entry else "新建条目")
        self.setMinimumSize(560, 600)
        self._build_ui(default_type)
        apply_dialog_theme(self)
        if entry:
            self._load()

    def _build_ui(self, default_type: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self.type_combo = QComboBox()
        for key, label, _ in wb.ENTRY_TYPES:
            self.type_combo.addItem(label, key)
        idx = self.type_combo.findData(default_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        form.addRow("类型", self.type_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("必填，如：青锋山")
        form.addRow("名称 *", self.name_edit)

        self.aliases_edit = QLineEdit()
        self.aliases_edit.setPlaceholderText("用逗号分隔，如：青锋，剑山")
        form.addRow("别名", self.aliases_edit)

        self.parent_edit = QLineEdit()
        self.parent_edit.setPlaceholderText("上级条目名（可空），如：青云宗")
        form.addRow("上级", self.parent_edit)

        self.attr_table = _AttrTable()
        form.addRow("属性", self.attr_table)

        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText("详细描述…")
        self.desc_edit.setFixedHeight(80)
        form.addRow("描述", self.desc_edit)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用「、」分隔，如：名山、剑修")
        form.addRow("标签", self.tags_edit)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load(self):
        e = self._entry
        idx = self.type_combo.findData(e.get("type", "location"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.name_edit.setText(e.get("name", ""))
        self.aliases_edit.setText("，".join(e.get("aliases") or []))
        self.parent_edit.setText(e.get("parent", ""))
        self.attr_table.set_attributes(e.get("attributes") or {})
        self.desc_edit.setPlainText(e.get("description", ""))
        self.tags_edit.setText("、".join(e.get("tags") or []))

    def _validate(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "名称不能为空。")
            return
        self.accept()

    def get_data(self) -> dict:
        aliases = []
        for sep in ("，", ","):
            text = self.aliases_edit.text().replace(sep, "\n")
            if "\n" in text:
                aliases = [a.strip() for a in text.splitlines() if a.strip()]
                break
        else:
            aliases = [a.strip() for a in self.aliases_edit.text().split() if a.strip()]

        tags = []
        for sep in ("、", ",", "，"):
            text = self.tags_edit.text().replace(sep, "\n")
            if "\n" in text:
                tags = [t.strip() for t in text.splitlines() if t.strip()]
                break
        else:
            tags = [t.strip() for t in self.tags_edit.text().split() if t.strip()]

        return {
            "type": self.type_combo.currentData() or "location",
            "name": self.name_edit.text().strip(),
            "aliases": aliases,
            "parent": self.parent_edit.text().strip(),
            "attributes": self.attr_table.get_attributes(),
            "description": self.desc_edit.toPlainText().strip(),
            "tags": tags,
            "chapter_refs": self._entry.get("chapter_refs", []),
        }
