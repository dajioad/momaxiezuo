# ui/proofread_dialog.py
"""墨码写作 - 错别字/敏感词检测结果对话框

设计：
- 单击行：定位（当前章校对直接跳编辑器；全书校对先切章节再定位）
- 双击行：可替换项 → 应用建议替换；不可替换项（敏感词等）→ 仅定位
- 右键行：忽略此条（不再报告）
- 顶部：分类筛选 + 关键词搜索，快速聚焦
- 底部：批量替换只处理勾选的、可替换的项
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QHeaderView,
    QCheckBox,
    QMenu,
    QMessageBox,
    QComboBox,
    QLineEdit,
    QFrame,
    QAbstractItemView,
    QWidget,
    QLayout,
)
from PySide6.QtCore import Qt, Signal
from ui.theme_style import qss_alpha
from PySide6.QtGui import QColor, QFont

import qtawesome as qta

from utils.proofreader import ignore_item


class ProofreadDialog(QDialog):
    """显示检测结果，单击跳转、双击替换、右键忽略、可批量替换。"""

    apply_fix = Signal(int, int, str)
    locate = Signal(int, int)
    locate_in_chapter = Signal(str, int, int)
    ignored_changed = Signal()

    COL_CHECK = 0

    def __init__(
        self,
        results: list,
        parent=None,
        *,
        data_root: str = "",
        whole_book: bool = False,
    ):
        super().__init__(parent)

        # 兼容老的 result（没有 replaceable 字段）：按 suggestion 判断
        for r in results:
            if "replaceable" not in r:
                sugg = (r.get("suggestion") or "").strip()
                r["replaceable"] = bool(sugg) and ("建议" not in sugg)

        self.results = results
        self.data_root = data_root
        self.whole_book = whole_book
        self._theme = self._resolve_theme()
        # 勾选状态保留：(chapter_id, start, end) → bool
        self._checked = {}
        for r in results:
            self._checked[self._key(r)] = True
        # 每次渲染后：row_index → result dict
        self._row_to_result = {}
        # 已勾选计数（避免每次 _update_status 都遍历整张表）
        self._checked_count = 0
        self._checked_count_replaceable = 0
        # 初始化：所有条目默认勾选
        for r in results:
            self._checked_count += 1
            if r.get("replaceable"):
                self._checked_count_replaceable += 1

        self._build_ui()
        self._apply_theme()
        self._apply_filter()

    # ---------------- 工具 ----------------
    @staticmethod
    def _key(r: dict):
        return (r.get("chapter_id", ""), r["start"], r["end"])

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and getattr(editor, "_theme", None):
                    return editor._theme
                break
            if hasattr(p, "_theme") and getattr(p, "_theme"):
                return p._theme
            p = p.parent()
        return {}

    # ---------------- UI ----------------
    def _build_ui(self):
        n = len(self.results)
        n_replaceable = sum(1 for r in self.results if r.get("replaceable"))
        title = "全书校对结果" if self.whole_book else "本章校对结果"
        self.setWindowTitle(f"{title} - {n} 处问题")
        self.setMinimumSize(820, 620)
        self.resize(960, 720)

        # ★ 关键顺序：先创建布局，再设 sizeConstraint
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        # 让 QVBoxLayout 把每个子控件的 minHeight 累加作为对话框的最小高度。
        # 缺这一行时，Qt 允许把底部按钮区压到 0 高度。
        lay.setSizeConstraint(QLayout.SetMinimumSize)

        # ---- 头部：标题 + 统计 ----
        head = QHBoxLayout()
        head_icon = QLabel()
        head_icon.setPixmap(qta.icon(
            "fa5s.clipboard-list",
            color=(self._theme or {}).get("text", "#3b342a")).pixmap(16, 16))
        head.addWidget(head_icon)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleLabel")
        head.addWidget(self.title_label)
        head.addStretch()
        self.summary_label = QLabel(
            f"共 {n} 处 · 可一键替换 {n_replaceable} 处 · "
            f"需手动检查 {n - n_replaceable} 处"
        )
        self.summary_label.setObjectName("summaryLabel")
        head.addWidget(self.summary_label)
        lay.addLayout(head)

        # ---- 筛选条 ----
        filter_bar = QFrame()
        filter_bar.setObjectName("filterBar")
        fb = QHBoxLayout(filter_bar)
        fb.setContentsMargins(10, 6, 10, 6)
        fb.setSpacing(8)
        fb.addWidget(QLabel("类型"))
        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(140)
        self.filter_combo.setMinimumHeight(32)
        self.filter_combo.addItem("全部类型", "")
        cats = sorted({r["category"] for r in self.results})
        for c in cats:
            self.filter_combo.addItem(c, c)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        fb.addWidget(self.filter_combo)

        fb.addWidget(QLabel("搜索"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("问题、上下文、建议、章节名…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.setMinimumHeight(32)
        fb.addWidget(self.search_edit, 1)

        reset_btn = QPushButton("清除筛选")
        reset_btn.setMinimumHeight(32)
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_filter)
        fb.addWidget(reset_btn)
        lay.addWidget(filter_bar)

        # ---- 表格 ----
        headers = ["", "类型", "问题", "上下文", "建议 / 提示"]
        if self.whole_book:
            headers.insert(1, "章节")
        self.table = QTableWidget()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(self.COL_CHECK, QHeaderView.Fixed)
        h.resizeSection(self.COL_CHECK, 40)
        col = 1
        if self.whole_book:
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            col += 1
        h.setSectionResizeMode(col + 0, QHeaderView.Fixed)  # 类型
        h.resizeSection(col + 0, 140)  # 足够放下“用语提示”这类标签
        h.setSectionResizeMode(col + 1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(col + 2, QHeaderView.Stretch)  # 上下文
        h.setSectionResizeMode(col + 3, QHeaderView.ResizeToContents)  # 建议
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        # 现代扁平风格
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setFixedHeight(40)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.cellClicked.connect(self._on_click)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.table, 1)

        # ---- 底部提示 ----
        self.tip_label = QLabel(
            "双击条目定位到原文位置 · 右键条目可应用建议替换或忽略此条"
        )
        self.tip_label.setObjectName("tipLabel")
        self.tip_label.setWordWrap(True)
        self.tip_label.setMinimumHeight(28)
        lay.addWidget(self.tip_label)

        # ---- 底部按钮（包成 QWidget，保证不被压缩）----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.select_replaceable_btn = QPushButton("全选可替换")
        self.select_replaceable_btn.setToolTip("只勾选可一键替换的项")
        self.select_replaceable_btn.clicked.connect(self._select_replaceable)
        bottom.addWidget(self.select_replaceable_btn)

        self.toggle_all_btn = QPushButton("全选/取消")
        self.toggle_all_btn.clicked.connect(self._toggle_all)
        bottom.addWidget(self.toggle_all_btn)

        self.ignore_btn = QPushButton("忽略选中")
        self.ignore_btn.setToolTip("以后同类问题不再报告；可在「词库管理」中恢复")
        self.ignore_btn.clicked.connect(self._ignore_selected)
        bottom.addWidget(self.ignore_btn)

        bottom.addStretch()

        self.apply_btn = QPushButton("批量替换")
        self.apply_btn.setObjectName("applyBtn")
        self.apply_btn.setMinimumHeight(34)
        self.apply_btn.setMinimumWidth(140)
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.setProperty("variant", "primary")
        self.apply_btn.clicked.connect(self._apply_selected)
        bottom.addWidget(self.apply_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)

        bottom_wrap = QWidget()
        bottom_wrap.setLayout(bottom)
        bottom_wrap.setMinimumHeight(52)
        lay.addWidget(bottom_wrap)

        self._update_status()

    # ---------------- 主题 ----------------
    def _apply_theme(self):
        t = self._theme or {}
        bg = t.get("bg", "#f7f2e8")
        sidebar = t.get("sidebar", "#efe8d8")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        from ui.dialog_styles import input_qss, button_qss
        from ui.theme_style import accent_button_qss

        primary_btn_qss = accent_button_qss(
            accent,
            accent_fg,
            accent_hover,
            selector="QPushButton#applyBtn",
            padding="8px 20px",
            bold=True,
        )

        # 扁平化表格 QSS：无网格、悬停底色、表头只留底横线
        flat_table_qss = (
            f"QTableWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x22)}; border-radius: 10px;"
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
            f" background-color: transparent; color: {muted};"
            f" border: none;"
            f" border-bottom: 2px solid {qss_alpha(muted, 0x33)};"
            f" padding: 10px 8px; font-weight: 600; font-size: 12px; }}"
            f"QTableCornerButton::section {{ background: transparent; border: none; }}"
        )

        self.setStyleSheet(
            f"ProofreadDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#titleLabel {{ font-size: 16px; font-weight: bold; }}"
            f"QLabel#summaryLabel {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#tipLabel {{ color: {muted}; font-size: 12px; }}"
            f"QFrame#filterBar {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x18)};"
            f" border-radius: 12px; }}"
            + input_qss(t)
            + flat_table_qss
            + button_qss(t)
            + primary_btn_qss
        )

    # ---------------- 筛选 ----------------
    def _reset_filter(self):
        self.filter_combo.blockSignals(True)
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.blockSignals(False)
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self._apply_filter()

    def _get_filtered(self):
        cat = self.filter_combo.currentData() or ""
        kw = self.search_edit.text().strip().lower()
        out = []
        for r in self.results:
            if cat and r["category"] != cat:
                continue
            if kw:
                blob = " ".join(
                    [
                        r.get("category", ""),
                        r.get("message", ""),
                        r.get("snippet", ""),
                        r.get("suggestion", ""),
                        r.get("chapter_title", ""),
                    ]
                ).lower()
                if kw not in blob:
                    continue
            out.append(r)
        return out

    def _apply_filter(self):
        filtered = self._get_filtered()
        self._render_rows(filtered)
        self._update_status(filtered)

    def _render_rows(self, filtered):
        self._row_to_result = {}
        self.table.setRowCount(0)
        self.table.setRowCount(len(filtered))

        is_light = QColor(self._theme.get("bg", "#f7f2e8")).lightness() > 150
        text_qc = QColor(self._theme.get("text", "#3b342a"))
        muted_qc = QColor(self._theme.get("muted", "#9a8e76"))
        accent_qc = QColor(self._theme.get("accent", "#b08a4e"))
        if is_light:
            c_sens = QColor("#c0392b")
            c_typo = QColor("#b07818")
            c_replace = QColor("#1a7f37")
        else:
            c_sens = QColor("#ff8a80")
            c_typo = QColor("#e5b450")
            c_replace = QColor("#7ee787")

        for i, r in enumerate(filtered):
            self._row_to_result[i] = r

            # 勾选
            chk = QCheckBox()
            chk.setChecked(self._checked.get(self._key(r), True))
            chk.setCursor(Qt.PointingHandCursor)
            key = self._key(r)
            chk.stateChanged.connect(
                lambda state, k=key: self._on_check_changed(k, state)
            )
            wrap = QFrame()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            # 关键：加 Qt.AlignCenter 让 QCheckBox 保持原始尺寸、垂直居中，
            # 不被 QHBoxLayout 拉伸成竖长条
            wl.addWidget(chk, 0, Qt.AlignCenter)
            self.table.setCellWidget(i, self.COL_CHECK, wrap)

            col = 1
            # 章节
            if self.whole_book:
                chapter_txt = (
                    f"{r.get('chapter_index', 0) + 1}. {r.get('chapter_title', '')}"
                )
                item = QTableWidgetItem(chapter_txt)
                item.setForeground(muted_qc)
                self.table.setItem(i, col, item)
                col += 1

            # 类型：改用 pill 标签（QLabel + 单元格 widget）
            cat = r["category"]
            from ui.dialog_styles import category_color, pill_qss

            pill_color = category_color(cat, is_light)
            pill_label = QLabel(cat)
            pill_label.setStyleSheet(pill_qss(pill_color))
            pill_label.setMinimumWidth(80)
            pill_label.setAlignment(Qt.AlignCenter)
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(4, 0, 4, 0)
            wl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            wl.addWidget(pill_label)
            self.table.setCellWidget(i, col, wrap)
            col += 1

            # 问题
            item = QTableWidgetItem(r["message"])
            item.setForeground(text_qc)
            self.table.setItem(i, col, item)
            col += 1

            # 上下文（snippet 里已用【】标记匹配词）
            item = QTableWidgetItem(r["snippet"])
            item.setForeground(text_qc)
            self.table.setItem(i, col, item)
            col += 1

            # 建议 / 提示
            sugg = r.get("suggestion", "")
            item = QTableWidgetItem(sugg if sugg else "—")
            if r.get("replaceable"):
                item.setForeground(c_replace)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                item.setToolTip("双击仅定位；右键本行可「应用建议替换」")
            else:
                item.setForeground(muted_qc)
                f = item.font()
                f.setItalic(True)
                item.setFont(f)
                item.setToolTip("此类问题需要手动修改，双击仅定位")
            self.table.setItem(i, col, item)

    # ---------------- 交互 ----------------
    def _on_check_changed(self, key, state):
        was_checked = self._checked.get(key, True)
        now_checked = (
            state == Qt.Checked.value if hasattr(Qt.Checked, "value") else bool(state)
        )
        if was_checked == now_checked:
            return
        self._checked[key] = now_checked

        delta = 1 if now_checked else -1
        self._checked_count += delta
        for r in self.results:
            if self._key(r) == key:
                if r.get("replaceable"):
                    self._checked_count_replaceable += delta
                break

        self._update_status()

    def _on_click(self, row, _col):
        r = self._row_to_result.get(row)
        if r is None:
            return
        if self.whole_book:
            self.locate_in_chapter.emit(r.get("chapter_id", ""), r["start"], r["end"])
        else:
            self.locate.emit(r["start"], r["end"])

    def _on_double_click(self, row, _col):
        self._on_click(row, _col)

    def _apply_one(self, row) -> bool:
        r = self._row_to_result.get(row)
        if r is None:
            return False
        if not (r.get("replaceable") and r.get("suggestion")):
            return False
        if self.whole_book:
            self.locate_in_chapter.emit(r.get("chapter_id", ""), r["start"], r["end"])
        delta = len(r["suggestion"]) - (r["end"] - r["start"])
        if delta != 0:
            cid = r.get("chapter_id", "")
            for other in self.results:
                if other is r:
                    continue
                if other.get("chapter_id", "") != cid:
                    continue
                if other["start"] >= r["end"]:
                    other["start"] += delta
                    other["end"] += delta
        self.results.remove(r)
        self.apply_fix.emit(r["start"], r["end"], r["suggestion"])
        self._apply_filter()
        return True

    def _show_menu(self, pos):
        row = self.table.rowAt(pos.y())
        r = self._row_to_result.get(row)
        if r is None:
            return
        menu = QMenu(self)
        act_jump = menu.addAction("跳转到此处")
        if r.get("replaceable") and r.get("suggestion"):
            act_replace = menu.addAction("应用建议替换")
        else:
            act_replace = None
        menu.addSeparator()
        act_ignore = menu.addAction("忽略此条（以后不再报告）")
        act_ignore_all = menu.addAction("忽略所有同类问题")

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_jump:
            self._on_click(row, 0)
        elif act_replace is not None and chosen == act_replace:
            self._apply_one(row)
        elif chosen == act_ignore:
            self._ignore_rows([row])
        elif chosen == act_ignore_all:
            key = r.get("ignore_key", "")
            rows = [
                i
                for i, rr in self._row_to_result.items()
                if rr.get("ignore_key") == key
            ]
            self._ignore_rows(rows)

    # ---------------- 底部按钮 ----------------
    def _toggle_all(self):
        states = []
        for i in range(self.table.rowCount()):
            w = self.table.cellWidget(i, self.COL_CHECK)
            cb = w.findChild(QCheckBox) if w else None
            if cb:
                states.append(cb.isChecked())
        target = not all(states) if states else True
        for i in range(self.table.rowCount()):
            w = self.table.cellWidget(i, self.COL_CHECK)
            cb = w.findChild(QCheckBox) if w else None
            if cb:
                cb.setChecked(target)

    def _select_replaceable(self):
        for i in range(self.table.rowCount()):
            r = self._row_to_result.get(i)
            w = self.table.cellWidget(i, self.COL_CHECK)
            cb = w.findChild(QCheckBox) if w else None
            if cb is None:
                continue
            cb.setChecked(bool(r and r.get("replaceable")))

    def _checked_rows(self):
        rows = []
        for i in range(self.table.rowCount()):
            w = self.table.cellWidget(i, self.COL_CHECK)
            cb = w.findChild(QCheckBox) if w else None
            if cb and cb.isChecked():
                rows.append(i)
        return rows

    def _ignore_selected(self):
        rows = self._checked_rows()
        if not rows:
            QMessageBox.information(self, "提示", "没有勾选任何条目。")
            return
        self._ignore_rows(rows)

    def _ignore_rows(self, rows):
        if not rows or not self.data_root:
            return
        count = 0
        for i in rows:
            r = self._row_to_result.get(i)
            if r is None:
                continue
            key = r.get("ignore_key", "")
            if key:
                ignore_item(self.data_root, key)
                count += 1
        if count:
            self.ignored_changed.emit()
            QMessageBox.information(
                self,
                "已忽略",
                f"已忽略 {count} 条。下次校对不会再报告这些内容。\n"
                "如需恢复，请在「词库管理」中清空忽略列表。",
            )

    def _apply_selected(self):
        items = []
        for i in self._checked_rows():
            r = self._row_to_result.get(i)
            if r is None or not r.get("replaceable") or not r.get("suggestion"):
                continue
            items.append(r)
        if not items:
            QMessageBox.information(
                self,
                "提示",
                "没有可替换的勾选项。\n（敏感词、标点、格式类问题需要手动修改）",
            )
            return

        if self.whole_book:
            items.sort(
                key=lambda r: (r.get("chapter_index", 0), r["start"]), reverse=True
            )
        else:
            items.sort(key=lambda r: r["start"], reverse=True)

        # 批量替换跨多处甚至跨多章，属于难以逐条撤回的写操作：
        # 执行前二次确认，默认焦点在「否」，防误触。
        if self.whole_book:
            n_ch = len({r.get("chapter_id", "") for r in items})
            confirm_text = (
                f"将跨 {n_ch} 章替换 {len(items)} 处内容。\n\n"
                "替换会逐章自动保存，确定继续吗？"
            )
        else:
            confirm_text = f"将替换本章 {len(items)} 处内容，确定继续吗？"
        if QMessageBox.question(
            self, "确认批量替换", confirm_text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        for r in items:
            if self.whole_book:
                self.locate_in_chapter.emit(
                    r.get("chapter_id", ""), r["start"], r["end"]
                )
            self.apply_fix.emit(r["start"], r["end"], r["suggestion"])

        QMessageBox.information(
            self,
            "完成",
            f"已应用 {len(items)} 处替换。\n（其他不可替换项请手动处理）",
        )
        self.accept()

    def _update_status(self, filtered=None):
        if filtered is None:
            filtered = self._get_filtered()
        total = len(self.results)
        n_repl = sum(1 for r in self.results if r.get("replaceable"))
        shown = len(filtered)
        n_checked_repl = self._checked_count_replaceable
        self.apply_btn.setText(
            f"批量替换（已勾选可替换 {n_checked_repl} 项）"
            if n_checked_repl
            else "批量替换"
        )
        self.apply_btn.setEnabled(n_checked_repl > 0)

        suffix = f" · 当前显示 {shown}" if shown != total else ""
        self.summary_label.setText(
            f"共 {total} 处 · 可一键替换 {n_repl} 处 · "
            f"需手动检查 {total - n_repl} 处{suffix}"
        )
