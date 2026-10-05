# ui/worldbuilding_panel.py
"""墨码写作 - 世界观设定库面板

按类型分组展示条目（地理 / 势力 / 年代 / 体系 / 生物），
单击选中，双击编辑，右键增删。

主题跟随写作主题（apply_theme）。数据落盘到 worldbuilding.json。
"""

import uuid

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLineEdit,
    QLabel,
    QMenu,
    QMessageBox,
    QDialog,
    QAbstractItemView,
    QApplication,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import Signal, Qt, QSize
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from utils.logger import get_logger
from utils import worldbuilding as wb

logger = get_logger()


class WorldbuildingPanel(QWidget):
    """世界观设定库面板。"""

    insert_text = Signal(str)  # 双击条目 → 插入名字到编辑器
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wbRoot")
        self.work = None
        self._data = []
        self._filter_type = ""  # 空 = 全部
        self._theme = {
            "sidebar": "#efe8d8",
            "accent": "#b08a4e",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "bg": "#f7f2e8",
        }
        self._build_ui()
        self._reload()

    # ---------------- UI ----------------
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 2)
        lay.setSpacing(6)

        # 搜索 + 新建
        top = QHBoxLayout()
        top.setSpacing(4)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤条目…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._refresh_list)
        top.addWidget(self.filter_edit, 1)

        self.add_btn = QPushButton()
        self.add_btn._icon_name = "fa5s.plus"
        self.add_btn.setFixedSize(30, 30)
        self.add_btn.setToolTip("新建条目")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_entry)
        top.addWidget(self.add_btn)
        lay.addLayout(top)

        # 类型筛选栏
        self.type_bar = QHBoxLayout()
        self.type_bar.setSpacing(4)
        self.type_btns = {}
        for key in [""] + [k for k, _, _ in wb.ENTRY_TYPES]:
            label = "全部" if key == "" else wb.type_label(key)
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _=False, k=key: self._on_type_filter(k))
            self.type_btns[key] = btn
            self.type_bar.addWidget(btn)
        self.type_bar.addStretch()
        lay.addLayout(self.type_bar)

        # 条目列表
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setSpacing(4)
        self.list_widget.itemDoubleClicked.connect(self._on_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_menu)
        lay.addWidget(self.list_widget, 1)

        self.tip_label = QLabel("双击插入名字 · 右键编辑 / 删除")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.tip_label)

        self._apply_styles()

    def _apply_styles(self):
        t = self._theme
        accent = t.get("accent", "#b08a4e")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")

        self.setStyleSheet(f"""
            #wbRoot {{ background-color: {t.get('sidebar', '#efe8d8')}; }}
            QWidget {{ color: {text}; background: transparent; }}
            QLineEdit {{
                background-color: {editor_bg}; color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 6px; padding: 5px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {accent}; }}
            QPushButton {{
                background: transparent; color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 6px; padding: 3px 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {qss_alpha(accent, 0x18)};
                border-color: {accent}; color: {accent};
            }}
            QPushButton:checked {{
                background: {qss_alpha(accent, 0x22)};
                border-color: {accent}; color: {accent};
                font-weight: 600;
            }}
            QListWidget {{
                background-color: transparent;
                border: none; color: {text};
            }}
            QListWidget::item {{
                padding: 0; border-radius: 8px; margin: 1px 0;
            }}
            QListWidget::item:hover {{
                background-color: {qss_alpha(accent, 0x10)};
            }}
            QListWidget::item:selected {{
                background-color: {qss_alpha(accent, 0x20)};
            }}
            QLabel {{ color: {muted}; }}
        """)

        accent_fg = t.get("accent_fg", "white")
        self.add_btn.setStyleSheet(
            f"QPushButton {{ background-color: {accent}; color: {accent_fg};"
            f" border: none; border-radius: 6px; padding: 0; }}"
            f"QPushButton:hover {{ background-color: {accent}; opacity: 0.9; }}"
        )
        self.add_btn.setIcon(qta.icon("fa5s.plus", color=accent_fg))

    def apply_theme(self, theme: dict):
        self._theme = theme or self._theme
        self._apply_styles()
        self._refresh_list()

    # ---------------- 数据 ----------------
    def set_work(self, work):
        self.work = work
        self._reload()

    def _reload(self):
        self._data = wb.load_entries(self.work) if self.work else []
        self.add_btn.setEnabled(self.work is not None)
        self._refresh_list()

    def _on_type_filter(self, key: str):
        self._filter_type = key
        for k, btn in self.type_btns.items():
            btn.setChecked(k == key)
        self._refresh_list()

    def _refresh_list(self):
        self.list_widget.clear()
        kw = self.filter_edit.text().strip().lower()

        shown = []
        for e in self._data:
            if self._filter_type and e.get("type") != self._filter_type:
                continue
            if kw:
                hay = " ".join(
                    [
                        e.get("name", ""),
                        " ".join(e.get("aliases") or []),
                        e.get("parent", ""),
                        e.get("description", ""),
                        " ".join(e.get("tags") or []),
                    ]
                ).lower()
                if kw not in hay:
                    continue
            shown.append(e)

        if not shown:
            item = QListWidgetItem(
                "（还没有条目，点右上角 + 新建）"
                if not self._data
                else "（没有匹配的条目）"
            )
            item.setFlags(Qt.ItemIsEnabled)
            item.setForeground(Qt.gray)
            self.list_widget.addItem(item)
            return

        for e in shown:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, e["id"])
            item.setSizeHint(QSize(0, 62))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, self._make_card(e))

    def _make_card(self, e: dict) -> QWidget:
        """单条目卡片：图标 + 名称 + 上级 + 属性 chips。"""
        t = self._theme
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        editor_bg = t.get("editor_bg", "#fcfaf4")

        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x28)};"
            f" border-radius: 8px; }}"
        )
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        # 第一行：图标 + 名称 + 上级
        head = QHBoxLayout()
        head.setSpacing(6)
        color = e.get("color") or wb.type_color(e.get("type", ""))
        icon_char = wb.type_label(e.get("type", ""))[0]
        icon = QLabel(icon_char)
        icon.setFixedSize(20, 20)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            f"QLabel {{ background-color: {color}; color: white;"
            f" border-radius: 4px; font-size: 11px; font-weight: 600; }}"
        )
        head.addWidget(icon)

        name_lab = QLabel(e.get("name", ""))
        name_lab.setStyleSheet(
            f"color: {text}; font-size: 13px; font-weight: 600;"
            f" background: transparent;"
        )
        head.addWidget(name_lab)

        if e.get("parent"):
            parent_lab = QLabel(f"· {e['parent']}")
            parent_lab.setStyleSheet(
                f"color: {muted}; font-size: 11px; background: transparent;"
            )
            head.addWidget(parent_lab)

        head.addStretch()
        lay.addLayout(head)

        # 第二行：属性 chips（最多 3 个）
        attrs = e.get("attributes") or {}
        if attrs:
            chips = QHBoxLayout()
            chips.setSpacing(4)
            for i, (k, v) in enumerate(list(attrs.items())[:3]):
                chip = QLabel(f"{k} {v}")
                chip.setStyleSheet(
                    f"QLabel {{ color: {muted}; font-size: 10px;"
                    f" background: {qss_alpha(muted, 0x14)};"
                    f" border-radius: 8px; padding: 1px 7px; }}"
                )
                chips.addWidget(chip)
            chips.addStretch()
            lay.addLayout(chips)

        return wrap

    def _current_id(self):
        it = self.list_widget.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    def _find(self, entry_id: str):
        for e in self._data:
            if e["id"] == entry_id:
                return e
        return None

    # ---------------- 交互 ----------------
    def _add_entry(self):
        if self.work is None:
            return
        from ui.worldbuilding_edit_dialog import WorldbuildingEditDialog

        dlg = WorldbuildingEditDialog(
            self, default_type=self._filter_type or "location"
        )
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not data.get("name"):
            return
        try:
            wb.add_entry(
                self.work,
                data["type"],
                data["name"],
                description=data.get("description", ""),
                parent=data.get("parent", ""),
                attributes=data.get("attributes") or {},
                aliases=data.get("aliases") or [],
                tags=data.get("tags") or [],
            )
        except ValueError as e:
            QMessageBox.warning(self, "新建失败", str(e))
            return
        self._reload()
        self.data_changed.emit()

    def _edit_entry(self, entry_id: str):
        e = self._find(entry_id)
        if e is None:
            return
        from ui.worldbuilding_edit_dialog import WorldbuildingEditDialog

        dlg = WorldbuildingEditDialog(self, entry=e)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not data.get("name"):
            return
        wb.update_entry(
            self.work,
            entry_id,
            name=data["name"],
            type=data["type"],
            description=data.get("description", ""),
            parent=data.get("parent", ""),
            attributes=data.get("attributes") or {},
            aliases=data.get("aliases") or [],
            tags=data.get("tags") or [],
            chapter_refs=data.get("chapter_refs") or [],
        )
        self._reload()
        self.data_changed.emit()

    def _delete_entry(self, entry_id: str):
        e = self._find(entry_id)
        if e is None:
            return
        name = e.get("name", "")
        if (
            QMessageBox.question(
                self, "删除条目", f"确定删除「{name}」？此操作不可恢复。"
            )
            != QMessageBox.Yes
        ):
            return
        wb.delete_entry(self.work, entry_id)
        self._reload()
        self.data_changed.emit()

    def _on_double_clicked(self, item):
        entry_id = item.data(Qt.UserRole)
        if entry_id:
            e = self._find(entry_id)
            if e:
                self.insert_text.emit(e.get("name", ""))

    def _show_menu(self, pos):
        it = self.list_widget.itemAt(pos)
        if it is None:
            return
        entry_id = it.data(Qt.UserRole)
        if not entry_id:
            return
        menu = QMenu(self)
        act_ins = menu.addAction("插入名字到光标处")
        act_edit = menu.addAction("编辑…")
        menu.addSeparator()
        act_copy = menu.addAction("复制名字")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen == act_ins:
            self._on_double_clicked(it)
        elif chosen == act_edit:
            self._edit_entry(entry_id)
        elif chosen == act_copy:
            e = self._find(entry_id)
            if e:
                QApplication.clipboard().setText(e.get("name", ""))
        elif chosen == act_del:
            self._delete_entry(entry_id)
