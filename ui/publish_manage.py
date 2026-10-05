# ui/publish_manage.py
"""墨码写作 - 发布平台管理对话框

- QTreeWidget 展示：分组 → 平台
- 预置平台：可用勾选框控制显示/隐藏（隐藏项可通过「恢复隐藏」找回），
  但不可编辑、不可删除
- 自定义平台：可增删改；没有勾选框（要么在列表里、要么删掉），
  避免"取消勾选不生效"的误导

视觉：
- 分组行用 setItemWidget 自定义（金色分组名 + [N 项] 计数胶囊）
- 目标行用 setItemWidget 自定义（缩进 + 复选框 + 名称 + 来源小标签）
- hover / 选中态仍由底层 QTreeWidget::item 绘制，widget 背景透明透出
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QMenu,
    QMessageBox,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QDialogButtonBox,
    QWidget,
)
from PySide6.QtCore import Qt, QSize

import qtawesome as qta

from ui.theme_style import qss_alpha
from utils.publish_targets import (
    load_all_for_manage,
    all_group_names,
    set_hidden,
    add_custom_target,
    update_custom_target,
    delete_custom_target,
    restore_hidden,
)

_TYPE_ROLE = Qt.UserRole + 1  # "group" / "target"
_GROUP_ROLE = Qt.UserRole + 2  # 分组名
_NAME_ROLE = Qt.UserRole + 3  # 平台名
_URL_ROLE = Qt.UserRole + 4
_NOTE_ROLE = Qt.UserRole + 5
_BUILTIN_ROLE = Qt.UserRole + 6


class PublishTargetEditDialog(QDialog):
    """新增/编辑一个发布平台"""

    def __init__(
        self,
        parent=None,
        group_names=None,
        *,
        group: str = "",
        name: str = "",
        url: str = "",
        note: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("编辑发布平台" if name else "新增发布平台")
        self.setMinimumWidth(480)
        self._group_names = list(group_names or [])
        form = QFormLayout(self)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        for g in self._group_names:
            self.group_combo.addItem(g)
        if group:
            self.group_combo.setCurrentText(group)
        form.addRow("分组：", self.group_combo)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("如：我的公众号")
        form.addRow("名称：", self.name_edit)

        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://...")
        form.addRow("跳转地址：", self.url_edit)

        self.note_edit = QLineEdit(note)
        self.note_edit.setPlaceholderText("可选，显示在菜单里的简短备注")
        form.addRow("备注：", self.note_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _validate(self):
        if not self.group_combo.currentText().strip():
            QMessageBox.warning(self, "提示", "请选择或输入分组名。")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请输入平台名称。")
            return
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入跳转地址。")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "提示", "地址需要以 http:// 或 https:// 开头。")
            return
        self.accept()

    def get_data(self) -> tuple:
        return (
            self.group_combo.currentText().strip(),
            self.name_edit.text().strip(),
            self.url_edit.text().strip(),
            self.note_edit.text().strip(),
        )


class PublishManageDialog(QDialog):
    """发布平台管理对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理发布平台")
        self.setMinimumSize(680, 540)
        self._theme = self._resolve_theme()
        self._build_ui()
        self._apply_theme()
        self._refresh()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ed = getattr(p, "editor", None)
            if ed is not None and hasattr(ed, "_theme"):
                return ed._theme or {}
            ws = getattr(p, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None and hasattr(ed, "_theme"):
                    return ed._theme or {}
                break
            p = p.parent()
        return {}

    def _apply_theme(self):
        t = self._theme or {
            "bg": "#f7f2e8",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "accent": "#b08a4e",
        }
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            f"PublishManageDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {muted}; background: transparent;"
            f" font-size: 11px; }}"
            f"QTreeWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; padding: 6px; outline: none; }}"
            f"QTreeWidget::item {{ padding: 0; border-radius: 6px;"
            f" margin: 1px 2px; }}"
            f"QTreeWidget::item:hover {{"
            f" background-color: {qss_alpha(accent, 0x08)}; }}"
            f"QTreeWidget::item:selected {{"
            f" background-color: {qss_alpha(accent, 0x15)}; color: {text}; }}"
            # setItemWidget 的 widget 必须透明，否则会盖住 item 底层的
            # hover / selected 背景
            f"QWidget#pubGroupRow, QWidget#pubItemRow {{"
            f" background: transparent; }}"
        )

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        hint = QLabel(
            "预置平台：勾选框控制是否显示在「发布」菜单里（取消勾选＝隐藏，"
            "可用下方「恢复隐藏」找回）；不可编辑、不可删除。\n"
            "自定义平台：没有勾选框——不想要就删掉；双击或右键可编辑。\n"
            "平台改版导致入口失效时：隐藏旧项，再「新增平台」填入新地址。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: gray;")
        root.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        root.addWidget(self.tree, 1)

        btn_row = QHBoxLayout()
        icon_text = (self._theme or {}).get("text", "#3b342a")
        self.add_btn = QPushButton(
            qta.icon("fa5s.plus", color=icon_text), " 新增平台")
        self.add_btn.clicked.connect(self._add_target)
        self.restore_btn = QPushButton(
            qta.icon("fa5s.undo", color=icon_text), " 恢复隐藏")
        self.restore_btn.setToolTip("把所有被隐藏的预置平台重新显示出来")
        self.restore_btn.clicked.connect(self._restore_hidden)
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.restore_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # 关闭钮右对齐（QDialogButtonBox 直接放进 VBox 会默认贴左）
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.button(QDialogButtonBox.Close).setText("关闭")
        close_box.rejected.connect(self.reject)
        close_row.addWidget(close_box)
        root.addLayout(close_row)

    # ---------- 行 widget 构造 ----------

    def _make_group_widget(self, name: str, count: int) -> QWidget:
        """分组行：▼ + 分组名（金色） + [N 项] 计数胶囊。"""
        t = self._theme or {}
        accent = t.get("accent", "#b08a4e")
        muted = t.get("muted", "#9a8e76")

        w = QWidget()
        w.setObjectName("pubGroupRow")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 2, 8, 2)
        lay.setSpacing(7)

        arrow = QLabel("▼")
        arrow.setStyleSheet(
            f"color: {accent}; font-size: 9px; background: transparent;"
        )
        arrow.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(arrow)

        title = QLabel(name)
        title.setStyleSheet(
            f"color: {accent}; font-size: 13px; font-weight: 600;"
            f" background: transparent;"
        )
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(title)

        count_lab = QLabel(f"{count} 项")
        count_lab.setStyleSheet(
            f"color: {muted}; font-size: 10px; font-weight: 600;"
            f" background: rgba(154,142,118,0.15);"
            f" padding: 2px 8px; border-radius: 8px;"
        )
        count_lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(count_lab)
        lay.addStretch()
        return w

    def _make_target_widget(
        self, name, url, note, builtin, visible, group_name
    ) -> QWidget:
        """目标行：复选框（仅预置） + 名称 + note 小标签。"""
        t = self._theme or {}
        text_c = t.get("text", "#3b342a")
        accent = t.get("accent", "#b08a4e")

        w = QWidget()
        w.setObjectName("pubItemRow")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(28, 2, 10, 2)
        lay.setSpacing(8)

        if builtin:
            cb = QCheckBox()
            cb.setChecked(bool(visible))
            cb.setCursor(Qt.PointingHandCursor)
            cb.setToolTip("勾选 = 显示在「发布」菜单里；取消 = 隐藏")
            cb.stateChanged.connect(
                lambda st, g=group_name, n=name: self._on_visibility_changed(g, n, st)
            )
            lay.addWidget(cb)

        name_lab = QLabel(name)
        name_lab.setStyleSheet(
            f"color: {text_c}; font-size: 12.5px; background: transparent;"
        )
        name_lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(name_lab)

        if note:
            note_lab = QLabel(note)
            note_lab.setStyleSheet(
                f"color: {accent}; font-size: 10px; font-weight: 600;"
                f" background: rgba(176,138,78,0.14);"
                f" padding: 1px 7px; border-radius: 8px;"
            )
            note_lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(note_lab)

        lay.addStretch()

        tip = f"{name}\n{url}"
        if note:
            tip += f"\n{note}"
        if builtin:
            tip += "\n（预置平台：可勾选显示/隐藏，不可编辑）"
        else:
            tip += "\n（自定义平台：双击编辑，右键删除）"
        w.setToolTip(tip)
        return w

    def _on_visibility_changed(self, group_name, name, state):
        """复选框变化：写盘。

        PySide6 的 QCheckBox.stateChanged 传 int（0=Unchecked,
        1=PartiallyChecked, 2=Checked），不用 Qt.CheckState 比较。
        """
        checked = int(state) == int(Qt.CheckState.Checked.value)
        set_hidden(group_name, name, not checked)

    # ---------- 数据填充 ----------

    def _refresh(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        for g in load_all_for_manage():
            count = len(g["targets"])

            # 分组行
            group_item = QTreeWidgetItem([""])
            group_item.setData(0, _TYPE_ROLE, "group")
            group_item.setData(0, _GROUP_ROLE, g["group"])
            group_item.setFlags(Qt.ItemIsEnabled)
            group_item.setSizeHint(0, QSize(0, 34))
            self.tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)
            self.tree.setItemWidget(
                group_item, 0, self._make_group_widget(g["group"], count)
            )

            # 目标行
            for name, url, note, builtin, visible in g["targets"]:
                item = QTreeWidgetItem([""])
                item.setData(0, _TYPE_ROLE, "target")
                item.setData(0, _GROUP_ROLE, g["group"])
                item.setData(0, _NAME_ROLE, name)
                item.setData(0, _URL_ROLE, url)
                item.setData(0, _NOTE_ROLE, note)
                item.setData(0, _BUILTIN_ROLE, builtin)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setSizeHint(0, QSize(0, 32))
                group_item.addChild(item)
                self.tree.setItemWidget(
                    item,
                    0,
                    self._make_target_widget(
                        name, url, note, builtin, visible, g["group"]
                    ),
                )
        self.tree.blockSignals(False)

    # ---------- 交互 ----------

    def _on_double_clicked(self, item: QTreeWidgetItem, _column):
        if item.data(0, _TYPE_ROLE) != "target":
            return
        self._edit_target(item)

    def _show_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        act_edit = None
        act_del = None
        if item and item.data(0, _TYPE_ROLE) == "target":
            builtin = item.data(0, _BUILTIN_ROLE)
            act_edit = menu.addAction("编辑")
            act_del = menu.addAction("删除")
            if builtin:
                act_edit.setEnabled(False)
                act_del.setEnabled(False)
                act_edit.setToolTip("预置平台不可编辑，可取消勾选隐藏后新增自定义项")
                act_del.setToolTip("预置平台不可删除，可取消勾选隐藏")
            menu.addSeparator()
        act_add = menu.addAction("新增平台")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._edit_target(item)
            return
        if chosen == act_del:
            self._delete_target(item)
            return
        if chosen == act_add:
            self._add_target()

    def _add_target(self):
        dlg = PublishTargetEditDialog(self, all_group_names())
        if dlg.exec() == QDialog.Accepted:
            group, name, url, note = dlg.get_data()
            add_custom_target(group, name, url, note)
            self._refresh()

    def _edit_target(self, item: QTreeWidgetItem):
        if item is None or item.data(0, _TYPE_ROLE) != "target":
            return
        if item.data(0, _BUILTIN_ROLE):
            QMessageBox.information(
                self,
                "提示",
                "预置平台不可编辑。\n"
                "若平台入口已变化，可取消勾选隐藏它，再「新增平台」填入新地址。",
            )
            return
        group = item.data(0, _GROUP_ROLE)
        name = item.data(0, _NAME_ROLE)
        url = item.data(0, _URL_ROLE)
        note = item.data(0, _NOTE_ROLE)
        dlg = PublishTargetEditDialog(
            self, all_group_names(), group=group, name=name, url=url, note=note
        )
        if dlg.exec() == QDialog.Accepted:
            new_group, new_name, new_url, new_note = dlg.get_data()
            update_custom_target(group, name, new_group, new_name, new_url, new_note)
            self._refresh()

    def _delete_target(self, item: QTreeWidgetItem):
        if item is None or item.data(0, _TYPE_ROLE) != "target":
            return
        if item.data(0, _BUILTIN_ROLE):
            return
        group = item.data(0, _GROUP_ROLE)
        name = item.data(0, _NAME_ROLE)
        if (
            QMessageBox.question(self, "删除平台", f"确定删除「{name}」？")
            == QMessageBox.Yes
        ):
            delete_custom_target(group, name)
            self._refresh()

    def _restore_hidden(self):
        if (
            QMessageBox.question(self, "恢复隐藏", "恢复所有被隐藏的预置平台？")
            == QMessageBox.Yes
        ):
            restore_hidden()
            self._refresh()
