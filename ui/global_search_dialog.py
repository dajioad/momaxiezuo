# ui/global_search_dialog.py
"""墨码写作 - 全局搜索对话框

- 关键词 + 正则 + 大小写敏感三个开关
- 结果按章节分组，双击跳转到对应位置
- 键盘操作：Enter 搜索，Esc 关闭，↑↓ 浏览结果

非模态，可与编辑器同时存在，跳转后仍可继续浏览结果。
"""

import json

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QTreeWidget,
    QTreeWidgetItem,
    QFrame,
    QMessageBox,
    QApplication,
    QMenu,
    QWidget,
    QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QColor
import qtawesome as qta
from html import escape as html_escape

from ui.theme_style import qss_alpha, accent_button_qss
from ui.dialog_styles import make_empty_state
from utils.global_search import search_work, format_search_summary
from utils.logger import get_logger

logger = get_logger()

_CFG_SECTION = "GlobalSearch"
_CFG_KEY = "state"


class GlobalSearchDialog(QDialog):
    """全局搜索（非模态）"""

    goto_match = Signal(str, int, int)

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.ws = workspace
        self._theme = self._resolve_theme()
        self._results = []
        self._total_matches = 0

        self.setWindowTitle("全局搜索")
        self.setMinimumSize(760, 560)
        self.setModal(False)

        self._build_ui()
        self._apply_theme()
        self._load_state()

    def _resolve_theme(self) -> dict:
        editor = getattr(self.ws, "editor", None)
        if editor is not None and hasattr(editor, "_theme"):
            return editor._theme or {}
        return {}

    # =================== UI ===================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ---- 搜索行 ----
        search_bar = QFrame()
        search_bar.setObjectName("searchBar")
        srow = QHBoxLayout(search_bar)
        srow.setContentsMargins(10, 8, 10, 8)
        srow.setSpacing(8)

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("输入关键词，回车搜索")
        self.keyword_edit.setClearButtonEnabled(True)
        self.keyword_edit.returnPressed.connect(self._do_search)
        srow.addWidget(self.keyword_edit, 1)

        self.regex_check = QCheckBox("正则")
        self.regex_check.setToolTip("使用正则表达式匹配")
        srow.addWidget(self.regex_check)

        self.case_check = QCheckBox("区分大小写")
        srow.addWidget(self.case_check)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.clicked.connect(self._do_search)
        srow.addWidget(self.search_btn)

        root.addWidget(search_bar)

        # ---- 摘要行 ----
        self.summary_label = QLabel("输入关键词开始搜索")
        self.summary_label.setObjectName("summaryLabel")
        root.addWidget(self.summary_label)

        # ---- 结果树 ----
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(0)  # 新样式用 QLabel 做子项，不需要缩进
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(False)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_activated)
        self.tree.itemActivated.connect(self._on_item_activated)

        # 空状态页（初始引导 / 无结果）
        self._stack = QStackedWidget()
        self._stack.addWidget(self.tree)
        self._empty_initial = make_empty_state(
            "fa5s.search",
            "输入关键词开始搜索",
            "回车或点击搜索按钮，支持正则与区分大小写",
            self._theme,
        )
        self._empty_none = make_empty_state(
            "fa5s.search", "没有找到匹配", "换个关键词试试", self._theme
        )
        self._stack.addWidget(self._empty_initial)
        self._stack.addWidget(self._empty_none)
        self._stack.setCurrentWidget(self._empty_initial)
        root.addWidget(self._stack, 1)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        self.expand_btn = QPushButton("展开全部")
        self.expand_btn.setCursor(Qt.PointingHandCursor)
        self.expand_btn.clicked.connect(lambda: self.tree.expandAll())
        self.collapse_btn = QPushButton("折叠全部")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(lambda: self.tree.collapseAll())
        self.copy_btn = QPushButton("复制结果")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_results)
        bottom.addWidget(self.expand_btn)
        bottom.addWidget(self.collapse_btn)
        bottom.addWidget(self.copy_btn)
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        QShortcut(QKeySequence("Escape"), self, self.close)

    def _apply_theme(self):
        t = self._theme
        if not t:
            return
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        bg = t.get("bg", "#f7f2e8")
        sidebar = t.get("sidebar", "#efe8d8")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"GlobalSearchDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#summaryLabel {{ color: {muted}; font-size: 12px; padding: 2px 0; }}"
            f"QFrame#searchBar {{ background-color: {sidebar}; border-radius: 10px; }}"
            # 树：扁平化、无缩进线、圆角
            f"QTreeWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x22)}; border-radius: 10px;"
            f" padding: 6px; outline: none; }}"
            f"QTreeWidget::item {{ border-radius: 6px; }}"
            f"QTreeWidget::item:selected {{ background: transparent; }}"
            # 分组行 widget 的 hover
            f"QWidget#groupRow {{ background: transparent; }}"
            f"QWidget#groupRow:hover {{ background: {qss_alpha(accent, 0x10)}; border-radius: 6px; }}"
            # 匹配行 widget 的 hover
            f"QWidget#matchRow {{ background: transparent; }}"
            f"QWidget#matchRow:hover {{ background: {qss_alpha(accent, 0x10)}; border-radius: 6px; }}"
        )

        btn_ss = accent_button_qss(
            accent,
            accent_fg,
            accent_hover,
            selector="QPushButton",
            padding="7px 18px",
            bold=True,
        )
        self.search_btn.setStyleSheet(btn_ss)
        self.search_btn.setIcon(qta.icon("fa5s.search", color=accent_fg))

    # =================== 搜索 ===================

    def _do_search(self):
        work = getattr(self.ws, "work", None)
        if work is None:
            self.summary_label.setText("请先打开一部作品")
            self.tree.clear()
            self._stack.setCurrentWidget(self._empty_initial)
            return

        keyword = self.keyword_edit.text()
        if not keyword:
            self.summary_label.setText("请输入关键词")
            self.tree.clear()
            self._stack.setCurrentWidget(self._empty_initial)
            return

        try:
            self.ws.flush_save()
        except Exception:
            pass

        use_regex = self.regex_check.isChecked()
        case_sensitive = self.case_check.isChecked()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results, err, truncated = search_work(
                work,
                keyword,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
                max_total=500,
            )
        finally:
            QApplication.restoreOverrideCursor()

        if err:
            self.summary_label.setText(err)
            self.tree.clear()
            self._results = []
            self._total_matches = 0
            self._stack.setCurrentWidget(self._empty_none)
            return

        self._results = results
        self._total_matches = sum(r["match_count"] for r in results)
        self.summary_label.setText(format_search_summary(results, truncated))
        if self._total_matches == 0:
            self._stack.setCurrentWidget(self._empty_none)
        else:
            self._stack.setCurrentWidget(self.tree)
        self._populate_tree()
        self._save_state()

    def _populate_tree(self):
        self.tree.clear()
        t = self._theme or {}
        accent = t.get("accent", "#b08a4e")
        muted = t.get("muted", "#9a8e76")
        text_c = t.get("text", "#3b342a")
        kw_bg = "#ffe58f"

        for entry in self._results:
            title = entry["chapter_title"]
            n = entry["match_count"]

            # --- 分组行：整行 QLabel（金色标题 + 计数胶囊） ---
            group = QTreeWidgetItem([""])
            group.setData(0, Qt.UserRole, ("__group__", entry["chapter_id"]))
            group.setSizeHint(0, QSize(0, 38))
            group.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(group)

            group_wrap = QWidget()
            group_wrap.setObjectName("groupRow")
            group_wrap.setAttribute(Qt.WA_Hover, True)
            gw = QHBoxLayout(group_wrap)
            gw.setContentsMargins(14, 6, 12, 6)
            gw.setSpacing(8)

            title_lab = QLabel(f"▼  {title}")
            title_lab.setStyleSheet(
                f"color: {accent}; font-size: 13px; font-weight: 600;"
                f" background: transparent;"
            )
            gw.addWidget(title_lab)

            count_lab = QLabel(f"{n} 处")
            count_lab.setStyleSheet(
                f"color: {muted}; font-size: 11px;"
                f" background: rgba(154,142,118,0.15);"
                f" padding: 2px 8px; border-radius: 9px;"
            )
            gw.addWidget(count_lab)
            gw.addStretch()
            self.tree.setItemWidget(group, 0, group_wrap)

            # --- 匹配行：整行 QLabel（黄底关键词 + hover 底色） ---
            for m in entry["matches"]:
                item = QTreeWidgetItem([""])
                item.setData(
                    0,
                    Qt.UserRole,
                    ("__match__", entry["chapter_id"], m["start"], m["end"]),
                )
                item.setSizeHint(0, QSize(0, 32))

                # 把 snippet 里的 【】 换成真高亮
                snippet = m.get("snippet", "")
                safe = html_escape(snippet)
                html = safe.replace(
                    "【",
                    f'<span style="background:{kw_bg};color:#3b342a;'
                    f'padding:0 3px;border-radius:3px;">',
                ).replace("】", "</span>")

                wrap = QWidget()
                wrap.setObjectName("matchRow")
                wrap.setAttribute(Qt.WA_Hover, True)
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(30, 4, 12, 4)
                label = QLabel(html)
                label.setTextFormat(Qt.RichText)
                label.setStyleSheet(
                    f"color: {text_c}; font-size: 12px; background: transparent;"
                )
                wl.addWidget(label, 1)

                group.addChild(item)
                self.tree.setItemWidget(item, 0, wrap)

                # 双击/单击跳转（QLabel 拦截事件，需手动绑定）
                def _jump(e=None, it=item):
                    self._on_item_activated(it)

                wrap.mouseDoubleClickEvent = _jump
                label.mouseDoubleClickEvent = _jump

            group.setExpanded(True)

    # =================== 交互 ===================

    def _on_item_activated(self, item: QTreeWidgetItem, _col=0):
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "__match__":
            return
        _, cid, start, end = data
        self.goto_match.emit(cid, start, end)

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)

        if item is not None:
            data = item.data(0, Qt.UserRole)
            if data and data[0] == "__match__":
                act_jump = menu.addAction("跳转到此处")
                act_copy_one = menu.addAction("复制此行上下文")
                menu.addSeparator()
                chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
                if chosen == act_jump:
                    self._on_item_activated(item)
                elif chosen == act_copy_one:
                    QApplication.clipboard().setText(item.text(0))
                return

        act_expand = menu.addAction("展开全部")
        act_collapse = menu.addAction("折叠全部")
        menu.addSeparator()
        act_copy = menu.addAction("复制全部结果")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_expand:
            self.tree.expandAll()
        elif chosen == act_collapse:
            self.tree.collapseAll()
        elif chosen == act_copy:
            self._copy_results()

    def _copy_results(self):
        if not self._results:
            QMessageBox.information(self, "提示", "还没有搜索结果。")
            return
        lines = []
        keyword = self.keyword_edit.text()
        lines.append(f"搜索：{keyword}")
        lines.append(f"结果：{self.summary_label.text()}")
        lines.append("")
        for entry in self._results:
            lines.append(
                f"━━━ {entry['chapter_title']}（{entry['match_count']} 处）━━━"
            )
            for m in entry["matches"]:
                lines.append(m["snippet"])
            lines.append("")
        text = "\n".join(lines).rstrip()
        QApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "已复制", f"结果已复制到剪贴板（共 {self._total_matches} 条）。"
        )

    # =================== 状态持久化 ===================

    def _save_state(self):
        cfg = getattr(self.ws, "cfg", None)
        if cfg is None:
            return
        try:
            state = {
                "keyword": self.keyword_edit.text(),
                "use_regex": self.regex_check.isChecked(),
                "case_sensitive": self.case_check.isChecked(),
            }
            cfg.set_config(
                _CFG_SECTION,
                _CFG_KEY,
                json.dumps(state, ensure_ascii=False),
                validate=False,
            )
        except Exception as e:
            logger.warning(f"保存搜索状态失败: {e}")

    def _load_state(self):
        cfg = getattr(self.ws, "cfg", None)
        if cfg is None:
            return
        try:
            raw = cfg.get_config(_CFG_SECTION, _CFG_KEY, "")
            if not raw:
                return
            state = json.loads(raw)
            self.regex_check.setChecked(state.get("use_regex", False))
            self.case_check.setChecked(state.get("case_sensitive", False))
            kw = state.get("keyword", "")
            if kw:
                self.keyword_edit.setText(kw)
                self.keyword_edit.selectAll()
        except Exception as e:
            logger.warning(f"读取搜索状态失败: {e}")

    def showEvent(self, event):
        super().showEvent(event)
        self.keyword_edit.setFocus()
