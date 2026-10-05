# ui/character_panel.py
"""墨码写作 - 人物卡面板（嵌在书架侧栏的「人物」标签页）

- 列表显示所有角色，每行"名字 · 身份 · 出场 N 章"
- 单击：仅选中（安全，不误触）
- 双击 / Enter：插入名字到编辑器光标处
- 右键：插入 / 编辑 / 复制 / 删除 / 刷新统计
- 顶部：搜索 + 颜色 + 新建按钮
- 人物名的高亮色可自定义（默认蓝色）

出场统计：打开作品时延迟扫描全本，统计每个角色的出场章节数、
提及次数、最后出场章节，结果回填 characters.json。

数据落盘到作品目录下的 characters.json。
视觉：跟随写作主题；列表 viewport 显式透明，避免深色模式下漏黑底。
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
)
from PySide6.QtCore import Signal, Qt, QTimer, QSize
import qtawesome as qta

from utils.work_store import Work
from ui.writing_editor import accent_foreground
from ui.theme_style import accent_button_qss, qss_alpha
from ui.character_edit_dialog import CharacterEditDialog
from ui.color_picker import ColorPickerDialog, character_default_color
from utils.logger import get_logger

logger = get_logger()


class CharacterPanel(QWidget):
    insert_text = Signal(str)  # 请求把角色名插入到编辑器
    data_changed = Signal()  # 人物卡列表或颜色变化

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("characterRoot")
        self.work: Work = None
        self._data = []  # list[dict]
        self._suppress = False
        self._theme = {
            "sidebar": "#efe8d8",
            "accent": "#b08a4e",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "bg": "#f7f2e8",
        }
        self._color_value = character_default_color()
        self._build_ui()
        self._reload()

    # ---------------- UI ----------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 6, 2, 2)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤角色…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._refresh_list)
        top_row.addWidget(self.filter_edit, 1)
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(30, 30)
        self.color_btn.setToolTip("修改人物名的高亮颜色")
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._edit_character_color)
        top_row.addWidget(self.color_btn)
        self.add_btn = QPushButton()
        self.add_btn._icon_name = "fa5s.user-plus"
        self.add_btn.setFixedSize(30, 30)
        self.add_btn.setToolTip("新建角色")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_character)
        top_row.addWidget(self.add_btn)
        layout.addLayout(top_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        # 单击不绑定动作（只选中）；双击才插入
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_menu)
        self.list_widget.keyPressEvent = self._list_key_press
        layout.addWidget(self.list_widget, 1)

        self.tip_label = QLabel("双击或按 Enter 插入光标处；右键可编辑。")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.tip_label)
        self._apply_styles()

    def _apply_styles(self):
        t = self._theme
        accent = t.get("accent", "#b08a4e")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", t.get("bg", "#fcfaf4"))
        accent_fg = t.get("accent_fg") or accent_foreground(accent)
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(f"""
            #characterRoot {{ background-color: {t.get('sidebar', '#efe8d8')}; }}
            QWidget {{ color: {text}; background: transparent; }}
            QLineEdit {{
                background-color: {editor_bg};
                color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 6px;
                padding: 5px 8px;
                selection-background-color: {accent};
                selection-color: {accent_fg};
            }}
            QLineEdit:focus {{ border: 1px solid {accent}; }}
            QListWidget {{
                background-color: {editor_bg};
                border: none;
                color: {text};
            }}
            QListWidget::viewport {{ background: transparent; }}
            QListWidget::item {{ padding: 6px 4px; border-radius: 5px; }}
            QListWidget::item:hover {{
                background-color: {qss_alpha(accent, 0x14)};
            }}
            QListWidget::item:selected {{
                background-color: {qss_alpha(accent, 0x33)};
                color: {text};
            }}
            QLabel {{ color: {muted}; }}
        """)

        add_ss = accent_button_qss(
            accent, accent_fg, accent_hover, selector="QPushButton", padding="0"
        )
        self.add_btn.setStyleSheet(add_ss)
        if hasattr(self.add_btn, "_icon_name"):
            self.add_btn.setIcon(qta.icon(self.add_btn._icon_name, color=accent_fg))
        self._refresh_color_btn()

    def _refresh_color_btn(self):
        """颜色按钮：底色 = 当前高亮色，图标 = 调色板。

        图标色按底色亮度自适应（深底白字 / 浅底深字），
        确保任何颜色下图标都清晰可见。
        """
        from PySide6.QtGui import QColor

        color = self._color_value or character_default_color()
        c = QColor(color)
        fg = "#FFFFFF" if c.lightness() < 150 else "#262833"
        self.color_btn.setIcon(qta.icon("fa5s.palette", color=fg))
        self.color_btn.setIconSize(QSize(15, 15))
        self.color_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {color};"
            f"  border: 1px solid rgba(0,0,0,0.18);"
            f"  border-radius: 6px;"
            f"  padding: 0;"
            f"}}"
            f"QPushButton:hover {{"
            f"  border: 2px solid {color};"
            f"}}"
        )

    def apply_theme(self, theme: dict):
        self._theme = theme or self._theme
        self._apply_styles()

    # ---------------- 数据 ----------------
    def set_work(self, work: Work):
        self.work = work
        self._reload()
        self._refresh_color_btn()
        # 延迟扫描出场统计：不阻塞打开作品，也不影响 UI 首屏。
        # 只在 100 章以下自动扫；长篇（500 章 +）一次扫描要 1 秒以上会
        # 冻 UI，改由用户右键菜单「刷新出场统计」手动触发。
        if work is not None and self._data and len(work.chapters) < 100:
            QTimer.singleShot(500, self._rescan_stats)

    def _reload(self):
        self._suppress = True
        self._data = []
        self._color_value = character_default_color()
        if self.work is not None:
            try:
                self._data = self.work.load_characters()
            except Exception as e:
                logger.warning(f"加载人物卡失败: {e}")
            try:
                c = self.work.load_characters_color()
                if c:
                    self._color_value = c
            except Exception:
                pass
        self.add_btn.setEnabled(self.work is not None)
        self._suppress = False
        self._refresh_list()

    def _save(self):
        if self.work is None:
            return
        try:
            self.work.save_characters(self._data)
        except OSError as e:
            logger.error(f"保存人物卡失败: {e}")
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.data_changed.emit()

    def _rescan_stats(self):
        """重扫全本，更新每个角色的出场统计并回写。"""
        if self.work is None or not self._data:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._data = self.work.scan_character_appearances(self._data)
            self.work.save_characters(self._data)
            logger.info("[CharPanel] 出场统计已更新")
        except Exception as e:
            logger.warning(f"扫描角色出场失败: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_list()

    def _refresh_list(self):
        self.list_widget.clear()
        kw = self.filter_edit.text().strip().lower()
        for c in self._data:
            name = c.get("name", "")
            role = c.get("role", "")
            if kw:
                haystack = " ".join(
                    [
                        name,
                        role,
                        c.get("personality", ""),
                        c.get("background", ""),
                    ]
                ).lower()
                if kw not in haystack:
                    continue
            bits = []
            if role:
                bits.append(role)
            stats = c.get("_stats") or {}
            if stats.get("chapter_count"):
                bits.append(f"出场 {stats['chapter_count']} 章")
            display = name + ("  ·  " + " · ".join(bits) if bits else "")
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, c.get("id", ""))
            if stats.get("chapter_count") == 0:
                item.setToolTip("本作品中尚未出现")
            elif stats.get("last_title"):
                item.setToolTip(f"最后出场：{stats['last_title']}")
            self.list_widget.addItem(item)

    def _current_id(self) -> str:
        it = self.list_widget.currentItem()
        if not it:
            return ""
        return it.data(Qt.UserRole) or ""

    def _find(self, cid: str):
        for c in self._data:
            if c.get("id") == cid:
                return c
        return None

    # ---------------- 操作 ----------------
    def _add_character(self):
        if self.work is None:
            return
        dlg = CharacterEditDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not data.get("name"):
            return
        data["id"] = uuid.uuid4().hex[:12]
        self._data.append(data)
        self._save()
        self._refresh_list()
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.UserRole) == data["id"]:
                self.list_widget.setCurrentItem(it)
                break
        # 长篇不自动重扫全本：一次扫 500 章会卡 1 秒以上。
        # 用户可右键菜单手动「刷新出场统计」。
        if self.work is not None and len(self.work.chapters) < 100:
            QTimer.singleShot(300, self._rescan_stats)

    def _edit_character(self, cid: str):
        c = self._find(cid)
        if c is None:
            return
        dlg = CharacterEditDialog(self, c)
        if dlg.exec() != QDialog.Accepted:
            return
        new_data = dlg.get_data()
        if not new_data.get("name"):
            return
        new_data["id"] = c["id"]
        if "_stats" in c:
            new_data["_stats"] = c["_stats"]
        for i, item in enumerate(self._data):
            if item.get("id") == cid:
                self._data[i] = new_data
                break
        self._save()
        self._refresh_list()
        if self.work is not None and len(self.work.chapters) < 100:
            QTimer.singleShot(300, self._rescan_stats)

    def _delete_character(self, cid: str):
        c = self._find(cid)
        if c is None:
            return
        name = c.get("name", "")
        if (
            QMessageBox.question(
                self, "删除角色",
                f"确定删除「{name}」吗？此操作不可恢复。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        self._data = [x for x in self._data if x.get("id") != cid]
        self._save()
        self._refresh_list()

    def _insert_name(self, cid: str):
        c = self._find(cid)
        if c is None:
            return
        name = c.get("name", "")
        if name:
            self.insert_text.emit(name)

    def _edit_character_color(self):
        """修改人物名的高亮颜色。"""
        if self.work is None:
            return
        cur = self._color_value or character_default_color()
        dlg = ColorPickerDialog(cur, "人物名的高亮颜色", self)
        if dlg.exec() != QDialog.Accepted:
            return
        color = dlg.get_color()
        try:
            self.work.save_characters_color(color)
        except Exception as e:
            logger.warning(f"保存人物颜色失败: {e}")
            return
        self._color_value = color
        self._refresh_color_btn()
        self.data_changed.emit()

    # ---------------- 交互 ----------------
    def _on_item_double_clicked(self, item):
        """双击：插入名字到光标处。"""
        cid = item.data(Qt.UserRole)
        if cid:
            self._insert_name(cid)

    def _show_menu(self, pos):
        it = self.list_widget.itemAt(pos)
        if it is None:
            return
        cid = it.data(Qt.UserRole)
        if not cid:
            return
        menu = QMenu(self)
        act_ins = menu.addAction("插入名字到光标处")
        act_edit = menu.addAction("编辑…")
        menu.addSeparator()
        act_copy = menu.addAction("复制名字")
        act_rescan = menu.addAction("刷新出场统计")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen == act_ins:
            self._insert_name(cid)
        elif chosen == act_edit:
            self._edit_character(cid)
        elif chosen == act_copy:
            c = self._find(cid)
            if c:
                QApplication.clipboard().setText(c.get("name", ""))
        elif chosen == act_rescan:
            self._rescan_stats()
        elif chosen == act_del:
            self._delete_character(cid)

    def _list_key_press(self, event):
        # Enter 插入名字
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            cid = self._current_id()
            if cid:
                self._insert_name(cid)
                return
        # Delete / Backspace 删除角色
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            cid = self._current_id()
            if cid:
                self._delete_character(cid)
                return
        QListWidget.keyPressEvent(self.list_widget, event)
