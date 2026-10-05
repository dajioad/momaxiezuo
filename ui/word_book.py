# ui/word_book.py
"""墨码写作 - 高频词库面板（嵌在书架侧栏的「高频词」标签页）

- 按作品独立保存 wordbook.json：角色姓名 / 技能名称 / 地名势力 / 物品法宝 / 其他
- 支持新增/重命名/删除词条、新增自定义分类、按关键字过滤
- 交互：单击仅选中；双击 / Enter 插入到编辑器光标处；右键编辑
- 每个分类可自定义高亮颜色（正文里该分类词条按此色高亮）

视觉：跟随写作主题。输入框用 editor_bg、按钮分三档（主/次/小图标），
深色主题下不会出现"白底浅字"的糊字问题。
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QComboBox,
    QPushButton,
    QLineEdit,
    QLabel,
    QMenu,
    QInputDialog,
    QMessageBox,
    QAbstractItemView,
    QDialog,
)
from PySide6.QtCore import Signal, Qt, QSize
import qtawesome as qta

from utils.work_store import Work
from ui.writing_editor import accent_foreground
from ui.theme_style import accent_button_qss, qss_alpha
from ui.color_picker import ColorPickerDialog, default_category_colors
from utils.logger import get_logger

logger = get_logger()

DEFAULT_CATEGORIES = ["角色姓名", "技能名称", "地名势力", "物品法宝", "其他"]


class WordBookPanel(QWidget):
    insert_word = Signal(str)  # 请求把词条插入编辑器光标处
    data_changed = Signal()  # 词条/分类/颜色变化

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wordBookRoot")
        self.work: Work = None
        self._data = {"categories": []}
        self._suppress = False
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 6, 2, 2)
        layout.setSpacing(6)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(4)
        self.cat_combo = QComboBox()
        self.cat_combo.currentIndexChanged.connect(self._on_category_changed)
        cat_row.addWidget(self.cat_combo, 1)
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(30, 30)
        self.color_btn.setToolTip("修改当前分类的高亮颜色")
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._edit_category_color)
        cat_row.addWidget(self.color_btn)
        self.add_cat_btn = self._small_icon_btn("fa5s.folder-plus", "新增分类")
        self.add_cat_btn.clicked.connect(self._add_category)
        cat_row.addWidget(self.add_cat_btn)
        self.del_cat_btn = self._small_icon_btn(
            "fa5s.folder-minus", "删除当前自定义分类"
        )
        self.del_cat_btn.clicked.connect(self._delete_category)
        cat_row.addWidget(self.del_cat_btn)
        layout.addLayout(cat_row)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤词条…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._refresh_word_list)
        layout.addWidget(self.filter_edit)

        self.word_list = QListWidget()
        self.word_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.word_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.word_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.word_list.customContextMenuRequested.connect(self._show_menu)
        self.word_list.keyPressEvent = self._list_key_press
        layout.addWidget(self.word_list, 1)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self.word_edit = QLineEdit()
        self.word_edit.setPlaceholderText("新词条，如：林墨 / 九霄惊雷诀")
        self.word_edit.setMaxLength(30)
        self.word_edit.returnPressed.connect(self._add_word)
        add_row.addWidget(self.word_edit, 1)
        self.add_word_btn = QPushButton("添加")
        self.add_word_btn.clicked.connect(self._add_word)
        add_row.addWidget(self.add_word_btn)
        layout.addLayout(add_row)

        self.tip_label = QLabel("双击或按 Enter 插入光标处；右键可编辑描述。")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.tip_label)
        self._apply_styles()

    def _small_icon_btn(self, icon_name: str, tip: str) -> QPushButton:
        b = QPushButton()
        b._icon_name = icon_name
        b.setFixedSize(30, 30)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setIconSize(QSize(16, 16))
        return b

    def _apply_styles(self):
        t = self._theme
        accent = t.get("accent", "#b08a4e")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", t.get("bg", "#fcfaf4"))
        accent_fg = t.get("accent_fg") or accent_foreground(accent)
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(f"""
            #wordBookRoot {{ background-color: {t.get('sidebar', '#efe8d8')}; }}
            QWidget {{ color: {text}; background: transparent; }}
            QComboBox, QLineEdit {{
                background-color: {editor_bg};
                color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 6px;
                padding: 5px 8px;
                selection-background-color: {accent};
                selection-color: {accent_fg};
            }}
            QComboBox:hover, QLineEdit:hover {{
                border-color: {qss_alpha(accent, 0x66)};
            }}
            QComboBox:focus, QLineEdit:focus {{
                border: 1px solid {accent};
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background-color: {editor_bg};
                color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 6px;
                padding: 4px;
                selection-background-color: {qss_alpha(accent, 0x33)};
                selection-color: {text};
                outline: none;
            }}
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
            QPushButton {{
                background: transparent;
                color: {text};
                border: 1px solid {qss_alpha(muted, 0x66)};
                border-radius: 6px;
                padding: 5px 10px;
            }}
            QPushButton:hover {{
                background-color: {qss_alpha(accent, 0x22)};
                border-color: {accent};
                color: {accent};
            }}
        """)

        primary_ss = accent_button_qss(
            accent, accent_fg, accent_hover, selector="QPushButton", padding="5px 14px"
        )
        self.add_word_btn.setStyleSheet(primary_ss)

        small_ss = (
            f"QPushButton {{"
            f" background-color: {accent};"
            f" color: {accent_fg};"
            f" border: none;"
            f" border-radius: 6px;"
            f" padding: 0; }}"
            f"QPushButton:hover {{"
            f" background-color: {accent_hover}; }}"
            f"QPushButton:pressed {{"
            f" background-color: {accent};"
            f" padding-top: 1px; }}"
            f"QPushButton:disabled {{"
            f" background-color: {qss_alpha(accent, 0x55)}; }}"
        )
        for b in (self.add_cat_btn, self.del_cat_btn):
            b.setStyleSheet(small_ss)
            b.setIconSize(QSize(16, 16))
            if hasattr(b, "_icon_name"):
                b.setIcon(qta.icon(b._icon_name, color=accent_fg))
        self._refresh_color_btn()

    def _refresh_color_btn(self):
        """颜色按钮：底色 = 当前分类色，图标 = 调色板。"""
        from PySide6.QtGui import QColor

        cat = self._current_category()
        color = None
        if cat:
            color = cat.get("color")
        if not color:
            defaults = default_category_colors()
            name = cat["name"] if cat else ""
            color = defaults.get(name, "#9A8E76")
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

    def _reload(self):
        self._suppress = True
        self._data = {"categories": []}
        if self.work is not None:
            stored = self.work.load_wordbook()
            cats = stored.get("categories", [])
            names = set()
            for c in cats:
                if (
                    isinstance(c, dict)
                    and isinstance(c.get("name"), str)
                    and isinstance(c.get("words"), list)
                ):
                    name = c["name"].strip()
                    if name and name not in names:
                        words = []
                        for w in c["words"]:
                            if isinstance(w, dict):
                                words.append(
                                    {
                                        "word": str(w.get("word", "")),
                                        "desc": str(w.get("desc", "")),
                                    }
                                )
                            elif str(w).strip():
                                words.append({"word": str(w), "desc": ""})
                        color = c.get("color", "")
                        self._data["categories"].append(
                            {"name": name, "words": words, "color": color}
                        )
                        names.add(name)
            defaults = default_category_colors()
            for d in DEFAULT_CATEGORIES:
                if d not in names:
                    self._data["categories"].append(
                        {"name": d, "words": [], "color": defaults.get(d, "#64748B")}
                    )
                    names.add(d)
            # 补全老数据缺 color
            for c in self._data["categories"]:
                if not c.get("color"):
                    c["color"] = defaults.get(c["name"], "#64748B")
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        if self.work is None:
            self.cat_combo.addItem("（请先打开作品）", None)
            self.add_cat_btn.setEnabled(False)
            self.del_cat_btn.setEnabled(False)
            self.add_word_btn.setEnabled(False)
            self.word_edit.setEnabled(False)
        else:
            for c in self._data["categories"]:
                self.cat_combo.addItem(c["name"], c["name"])
            self.add_cat_btn.setEnabled(True)
            self.del_cat_btn.setEnabled(True)
            self.add_word_btn.setEnabled(True)
            self.word_edit.setEnabled(True)
        self.cat_combo.blockSignals(False)
        self._suppress = False
        self._refresh_word_list()
        self._refresh_color_btn()

    def _current_category(self):
        name = self.cat_combo.currentData()
        if not name:
            return None
        for c in self._data["categories"]:
            if c["name"] == name:
                return c
        return None

    def _save(self):
        if self.work is not None:
            try:
                self.work.save_wordbook(self._data)
            except OSError as e:
                logger.error(f"词库保存失败: {e}")
                QMessageBox.warning(self, "保存失败", str(e))
                return
            self.data_changed.emit()

    # ---------------- 分类 ----------------
    def _on_category_changed(self, _idx):
        if not self._suppress:
            self._refresh_word_list()
        self._refresh_color_btn()

    def _add_category(self):
        if self.work is None:
            return
        name, ok = QInputDialog.getText(
            self, "新增分类", "分类名称（如：宗门 / 丹药）："
        )
        name = name.strip()
        if not ok or not name:
            return
        if any(c["name"] == name for c in self._data["categories"]):
            QMessageBox.information(self, "提示", f"分类「{name}」已存在。")
            return
        dlg = ColorPickerDialog("#3B82F6", f"选择「{name}」的高亮颜色", self)
        if dlg.exec() != QDialog.Accepted:
            return
        color = dlg.get_color()
        self._data["categories"].append({"name": name, "words": [], "color": color})
        self._save()
        self.cat_combo.addItem(name, name)
        self.cat_combo.setCurrentIndex(self.cat_combo.count() - 1)
        self._refresh_color_btn()

    def _delete_category(self):
        cat = self._current_category()
        if cat is None:
            return
        if cat["name"] in DEFAULT_CATEGORIES:
            QMessageBox.information(
                self, "提示", "内置分类不能删除，只可清空其中词条。"
            )
            return
        ret = QMessageBox.question(
            self,
            "删除分类",
            f"确定删除分类「{cat['name']}」及其下 {len(cat['words'])} 个词条吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self._data["categories"].remove(cat)
        self._save()
        idx = self.cat_combo.currentIndex()
        self.cat_combo.removeItem(idx)
        self._refresh_word_list()
        self._refresh_color_btn()

    def _edit_category_color(self):
        """修改当前分类的高亮颜色。"""
        cat = self._current_category()
        if cat is None:
            return
        cur = cat.get("color") or "#3B82F6"
        dlg = ColorPickerDialog(cur, f"「{cat['name']}」的高亮颜色", self)
        if dlg.exec() != QDialog.Accepted:
            return
        cat["color"] = dlg.get_color()
        self._save()
        self._refresh_color_btn()

    # ---------------- 词条 ----------------
    def _refresh_word_list(self):
        self.word_list.clear()
        cat = self._current_category()
        if cat is None:
            return
        keyword = self.filter_edit.text().strip()
        for entry in cat["words"]:
            w = entry["word"] if isinstance(entry, dict) else entry
            desc = entry.get("desc", "") if isinstance(entry, dict) else ""
            if keyword and keyword not in w:
                continue
            display = w
            if desc:
                display = f"{w} — {desc[:20]}"
            QListWidgetItem(display, self.word_list)

    def _add_word(self):
        cat = self._current_category()
        if cat is None:
            return
        text = self.word_edit.text().strip()
        if not text:
            return
        words = [e["word"] if isinstance(e, dict) else e for e in cat["words"]]
        if text in words:
            self.word_edit.clear()
            return
        cat["words"].append({"word": text, "desc": ""})
        self._save()
        self.word_edit.clear()
        self._refresh_word_list()
        matches = self.word_list.findItems(text, Qt.MatchStartsWith)
        if matches:
            self.word_list.setCurrentItem(matches[0])

    def _selected_word(self) -> str:
        it = self.word_list.currentItem()
        if not it:
            return ""
        text = it.text()
        return text.split(" — ")[0].strip()

    def _delete_word(self, word: str):
        cat = self._current_category()
        if cat is None or not word:
            return
        # 词条可能携带描述/备注，误触 Delete 直接删除无法找回：
        # 与删分类一致，先确认且默认焦点在「否」
        if QMessageBox.question(
            self, "删除词条", f"确定删除词条「{word}」吗？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        for i, entry in enumerate(cat["words"]):
            w = entry["word"] if isinstance(entry, dict) else entry
            if w == word:
                del cat["words"][i]
                break
        self._save()
        self._refresh_word_list()

    def _rename_word(self, old: str):
        cat = self._current_category()
        if cat is None or not old:
            return
        old_desc = ""
        old_idx = -1
        for i, entry in enumerate(cat["words"]):
            w = entry["word"] if isinstance(entry, dict) else entry
            if w == old:
                old_idx = i
                old_desc = entry.get("desc", "") if isinstance(entry, dict) else ""
                break
        text, ok = QInputDialog.getText(self, "修改词条", "新内容：", text=old)
        text = text.strip()
        if not ok or not text or text == old:
            return
        words = [e["word"] if isinstance(e, dict) else e for e in cat["words"]]
        if text in words:
            QMessageBox.information(self, "提示", "该词条已存在。")
            return
        cat["words"][old_idx] = {"word": text, "desc": old_desc}
        self._save()
        self._refresh_word_list()

    def _edit_desc(self, word: str):
        """编辑词条描述（角色卡功能）"""
        cat = self._current_category()
        if cat is None or not word:
            return
        old_desc = ""
        old_idx = -1
        for i, entry in enumerate(cat["words"]):
            w = entry["word"] if isinstance(entry, dict) else entry
            if w == word:
                old_idx = i
                old_desc = entry.get("desc", "") if isinstance(entry, dict) else ""
                break
        desc, ok = QInputDialog.getText(
            self, "编辑描述", f"{word} 的描述/备注：", text=old_desc
        )
        if not ok:
            return
        if isinstance(cat["words"][old_idx], dict):
            cat["words"][old_idx]["desc"] = desc.strip()
        else:
            cat["words"][old_idx] = {"word": word, "desc": desc.strip()}
        self._save()
        self._refresh_word_list()

    # ---------------- 交互 ----------------
    def _on_item_double_clicked(self, item: QListWidgetItem):
        if self.work is not None and item.text():
            self.insert_word.emit(item.text())

    def _show_menu(self, pos):
        item = self.word_list.itemAt(pos)
        if item is None:
            return
        word = item.text().split(" — ")[0].strip() if item else ""
        menu = QMenu(self)
        act_ins = menu.addAction("插入到光标处")
        act_desc = menu.addAction("编辑描述/备注")
        act_rename = menu.addAction("修改词条")
        menu.addSeparator()
        act_del = menu.addAction("删除词条")
        action = menu.exec(self.word_list.viewport().mapToGlobal(pos))
        if action == act_ins:
            self.insert_word.emit(word)
        elif action == act_desc:
            self._edit_desc(word)
        elif action == act_rename:
            self._rename_word(word)
        elif action == act_del:
            self._delete_word(word)

    def _list_key_press(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            word = self._selected_word()
            if word:
                self.insert_word.emit(word)
                return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            word = self._selected_word()
            if word:
                self._delete_word(word)
                return
        QListWidget.keyPressEvent(self.word_list, event)
