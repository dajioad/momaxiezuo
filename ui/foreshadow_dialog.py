# ui/foreshadow_dialog.py
"""墨码写作 - 伏笔追踪对话框

- 表格列出所有伏笔：状态 / 内容 / 埋伏章节 / 计划回收 / 实际回收
- 双击编辑；右键标记回收/恢复/删除
- 顶部筛选：全部 / 未回收 / 已回收
- 底部按钮：新建、AI 扫描当前章
- AI 扫描期间显示点点动画 + 状态提示，避免用户误以为卡死
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QComboBox,
    QMenu,
    QMessageBox,
    QAbstractItemView,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QCheckBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont

import qtawesome as qta

from ui.theme_style import (
    qss_alpha, accent_button_qss, apply_dialog_theme,
    SEMANTIC_OK, SEMANTIC_WARN,
)
from utils.foreshadow import (
    ForeshadowItem,
    load_all,
    add_item,
    update_item,
    delete_item,
    close_item,
    reopen_item,
)


class _EditDialog(QDialog):
    """新建 / 编辑伏笔。"""

    def __init__(self, item: ForeshadowItem = None, chapters=None, parent=None):
        super().__init__(parent)
        self._item = item
        self._chapters = chapters or []
        self.setWindowTitle("编辑伏笔" if item else "新建伏笔")
        self.setMinimumWidth(520)
        self._build_ui()
        apply_dialog_theme(self)
        if item:
            self._load()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.content_edit = QLineEdit()
        self.content_edit.setPlaceholderText("如：老乞丐送的玉佩")
        form.addRow("伏笔内容 *", self.content_edit)

        # 埋伏章节下拉
        self.planted_combo = QComboBox()
        self.planted_combo.setEditable(True)
        self.planted_combo.addItem("（不指定）", "")
        for cid, title in self._chapters:
            self.planted_combo.addItem(f"{cid}  {title}", cid)
        form.addRow("埋在哪章", self.planted_combo)

        self.planned_edit = QLineEdit()
        self.planned_edit.setPlaceholderText("如：第50章左右 / 结局时揭")
        form.addRow("计划何时揭", self.planned_edit)

        self.closed_check = QCheckBox("已回收")
        form.addRow("", self.closed_check)

        self.actual_combo = QComboBox()
        self.actual_combo.setEditable(True)
        self.actual_combo.addItem("（未指定）", "")
        for cid, title in self._chapters:
            self.actual_combo.addItem(f"{cid}  {title}", cid)
        self.actual_combo.setEnabled(False)
        form.addRow("实际揭在", self.actual_combo)

        self.closed_check.toggled.connect(self.actual_combo.setEnabled)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("备注：为什么埋这个、打算怎么用…")
        self.notes_edit.setFixedHeight(70)
        form.addRow("备注", self.notes_edit)

        root.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load(self):
        it = self._item
        self.content_edit.setText(it.content)
        idx = self.planted_combo.findData(it.planted_chapter)
        if idx < 0 and it.planted_chapter:
            self.planted_combo.addItem(it.planted_chapter, it.planted_chapter)
            idx = self.planted_combo.count() - 1
        self.planted_combo.setCurrentIndex(max(0, idx))
        self.planned_edit.setText(it.planned_payoff)
        self.closed_check.setChecked(it.is_closed)
        self.actual_combo.setEnabled(it.is_closed)
        idx2 = self.actual_combo.findData(it.actual_payoff)
        if idx2 < 0 and it.actual_payoff:
            self.actual_combo.addItem(it.actual_payoff, it.actual_payoff)
            idx2 = self.actual_combo.count() - 1
        self.actual_combo.setCurrentIndex(max(0, idx2))
        self.notes_edit.setPlainText(it.notes)

    def _validate(self):
        if not self.content_edit.text().strip():
            QMessageBox.warning(self, "提示", "请输入伏笔内容。")
            return
        if self.closed_check.isChecked() and not self.actual_combo.currentData():
            QMessageBox.warning(self, "提示", "标记为已回收时请指定章节。")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "content": self.content_edit.text().strip(),
            "planted_chapter": self.planted_combo.currentData() or "",
            "planned_payoff": self.planned_edit.text().strip(),
            "actual_payoff": (
                self.actual_combo.currentData() or ""
                if self.closed_check.isChecked()
                else ""
            ),
            "notes": self.notes_edit.toPlainText().strip(),
        }


class ForeshadowDialog(QDialog):
    """伏笔追踪面板。"""

    request_scan = Signal()  # 请求 AI 扫描当前章

    def __init__(self, work, current_chapter_id: str = "", parent=None):
        super().__init__(parent)
        self._work = work
        self._current_chapter_id = current_chapter_id
        self._theme = self._resolve_theme()
        self._items = []
        self._filter = "all"
        # AI 扫描状态：点点动画定时器 + 计数器
        self._scanning = False
        self._scan_dots = 0
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(500)
        self._scan_timer.timeout.connect(self._tick_scan_anim)
        self.setWindowTitle(f"伏笔追踪 - {work.title}")
        self.setMinimumSize(860, 560)
        self._build_ui()
        self._apply_theme()
        self.reload()

    def _resolve_theme(self) -> dict:
        # 统一走 theme_style._resolve_dialog_theme：
        #   1. 沿 parent 链找任意带 _theme 属性的 widget（editor / panel
        #      都能命中），与其它对话框保持一致
        #   2. 找不到时用 app palette 兜底
        # 之前每个对话框各写一份 while 循环，逻辑容易漂移；统一下来
        # 避免"切了主题但这个对话框没跟随"的问题。
        from ui.theme_style import _resolve_dialog_theme
        return _resolve_dialog_theme(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # 顶部
        head = QHBoxLayout()
        head.addWidget(QLabel("显示"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", "all")
        self.filter_combo.addItem("未回收", "open")
        self.filter_combo.addItem("已回收", "closed")
        self.filter_combo.currentIndexChanged.connect(self._on_filter)
        head.addWidget(self.filter_combo)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("fsStats")
        head.addWidget(self.stats_label)
        head.addStretch()

        self.scan_btn = QPushButton(
            qta.icon("fa5s.magic",
                     color=(self._theme or {}).get("text", "#3b342a")),
            " AI 扫描本章")
        self.scan_btn.setToolTip("让 AI 扫当前章节，提示可能的伏笔")
        self.scan_btn.clicked.connect(self.request_scan.emit)
        head.addWidget(self.scan_btn)

        self.add_btn = QPushButton(
            qta.icon("fa5s.plus",
                     color=(self._theme or {}).get("accent_fg", "#ffffff")),
            " 新建")
        self.add_btn.setProperty("variant", "primary")
        self.add_btn.clicked.connect(self._add)
        head.addWidget(self.add_btn)
        root.addLayout(head)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["状态", "伏笔内容", "埋在哪章", "计划回收", "实际回收"]
        )
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 100)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setMouseTracking(True)
        self.table.horizontalHeader().setHighlightSections(False)

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.cellDoubleClicked.connect(self._on_double_clicked)
        root.addWidget(self.table, 1)

        # 底部
        bottom = QHBoxLayout()
        self.tip_label = QLabel(
            "双击编辑 · 右键标记回收/恢复/删除。"
            "「AI 扫描本章」让 AI 提示可能被埋的线索。"
        )
        self.tip_label.setObjectName("fsTip")
        self.tip_label.setWordWrap(True)
        bottom.addWidget(self.tip_label, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ---------- AI 扫描状态 ----------
    def set_scanning(self, scanning: bool):
        """AI 扫描开始/结束：更新按钮文案 + 状态提示 + 点点动画。

        扫描期间禁用「AI 扫描本章」按钮，避免重复触发；
        状态栏显示提示，让用户确信没卡死。
        用户若想中途取消，关闭对话框即可（closeEvent 会 stop worker）。
        """
        self._scanning = bool(scanning)
        if self._scanning:
            self._scan_dots = 0
            self.scan_btn.setEnabled(False)
            self.add_btn.setEnabled(False)
            self.scan_btn.setText(" AI 扫描中")
            self.stats_label.setText("⏳ 正在请求 AI（约 20-60 秒）…")
            self._scan_timer.start()
        else:
            self._scan_timer.stop()
            self.scan_btn.setEnabled(True)
            self.add_btn.setEnabled(True)
            self.scan_btn.setText(" AI 扫描本章")
            self.scan_btn.setIcon(qta.icon(
                "fa5s.magic",
                color=(self._theme or {}).get("text", "#3b342a")))
            # 让 _render 重算统计文字（覆盖扫描中的提示）
            self._render()

    def _tick_scan_anim(self):
        """点点动画：让按钮上的文字动态变化，视觉上确认没卡。"""
        if not self._scanning:
            return
        self._scan_dots = (self._scan_dots + 1) % 4
        dots = "·" * self._scan_dots
        self.scan_btn.setText(f" AI 扫描中{dots}")

    # ---------- 数据 ----------
    def reload(self):
        self._items = load_all(self._work)
        self._render()

    def _on_filter(self, _):
        self._filter = self.filter_combo.currentData() or "all"
        self._render()

    def _render(self):
        items = self._items
        if self._filter == "open":
            items = [it for it in items if not it.is_closed]
        elif self._filter == "closed":
            items = [it for it in items if it.is_closed]

        total = len(self._items)
        open_count = sum(1 for it in self._items if not it.is_closed)
        closed_count = total - open_count
        self.stats_label.setText(
            f"共 {total} 条 · 未回收 {open_count} · 已回收 {closed_count}"
        )

        self.table.setRowCount(0)
        self.table.setRowCount(len(items))

        text_qc = QColor(self._theme.get("text", "#3b342a"))
        muted_qc = QColor(self._theme.get("muted", "#9a8e76"))
        accent_qc = QColor(self._theme.get("accent", "#b08a4e"))
        # 与其它对话框（chapter_trash / consistency / readability）
        # 保持一致：都走 SEMANTIC_* 常量。
        green = QColor(SEMANTIC_OK)
        warn = QColor(SEMANTIC_WARN)

        for i, it in enumerate(items):
            # 状态列：改用 "✓ 已回收" / "● 未回收" 并加粗染色
            status_text = "✓ 已回收" if it.is_closed else "● 未回收"
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(green if it.is_closed else warn)
            f = status_item.font()
            f.setBold(True)
            f.setPointSize(f.pointSize())
            status_item.setFont(f)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setData(Qt.UserRole, it.id)
            self.table.setItem(i, 0, status_item)

            # 内容
            content_item = QTableWidgetItem(it.content)
            content_item.setForeground(text_qc)
            self.table.setItem(i, 1, content_item)

            # 埋伏章节
            planted = self._chapter_label(it.planted_chapter)
            pi = QTableWidgetItem(planted or "—")
            pi.setForeground(muted_qc)
            self.table.setItem(i, 2, pi)

            # 计划
            planned = it.planned_payoff or "—"
            pli = QTableWidgetItem(planned)
            pli.setForeground(muted_qc)
            self.table.setItem(i, 3, pli)

            # 实际
            actual = self._chapter_label(it.actual_payoff)
            ai = QTableWidgetItem(actual or "—")
            if it.is_closed:
                ai.setForeground(green)
            else:
                ai.setForeground(muted_qc)
            self.table.setItem(i, 4, ai)

    def _chapter_label(self, cid: str) -> str:
        if not cid:
            return ""
        ch = self._work.get_chapter(cid)
        if ch:
            return f"{cid} {ch.get('title', '')}"
        return cid

    def _current_item(self) -> ForeshadowItem:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        cid = item.data(Qt.UserRole)
        for it in self._items:
            if it.id == cid:
                return it
        return None

    def _chapters(self):
        return [(c.get("id", ""), c.get("title", "")) for c in self._work.chapters]

    # ---------- 交互 ----------
    def _add(self):
        dlg = _EditDialog(None, self._chapters(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        add_item(self._work, **data)
        self.reload()

    def _on_double_clicked(self, row, _col):
        it = self._current_item()
        if it is None:
            return
        self._edit(it)

    def _edit(self, item: ForeshadowItem):
        dlg = _EditDialog(item, self._chapters(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        update_item(self._work, item.id, **data)
        self.reload()

    def _show_menu(self, pos):
        it = self._current_item()
        if it is None:
            return
        menu = QMenu(self)
        act_edit = menu.addAction("编辑…")
        if it.is_closed:
            act_close = menu.addAction("恢复为未回收")
        else:
            act_close = menu.addAction("标记为已回收…")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._edit(it)
        elif chosen == act_close:
            if it.is_closed:
                reopen_item(self._work, it.id)
                self.reload()
            else:
                self._close_item_dialog(it)
        elif chosen == act_del:
            if (
                QMessageBox.question(
                    self, "删除伏笔", f"确定删除「{it.content[:20]}」？"
                )
                == QMessageBox.Yes
            ):
                delete_item(self._work, it.id)
                self.reload()

    def _close_item_dialog(self, item: ForeshadowItem):
        from PySide6.QtWidgets import QInputDialog

        chapters = self._chapters()
        labels = [f"{cid}  {t}" for cid, t in chapters]
        if not labels:
            QMessageBox.information(self, "提示", "本作品还没有章节。")
            return
        label, ok = QInputDialog.getItem(
            self, "标记回收", "在哪一章揭开的？", labels, 0, False
        )
        if not ok or not label:
            return
        idx = labels.index(label)
        cid = chapters[idx][0]
        close_item(self._work, item.id, cid)
        self.reload()

    # ---------- 主题 ----------
    def _apply_theme(self):
        t = self._theme or {
            "bg": "#f7f2e8",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "accent": "#b08a4e",
            "sidebar": "#efe8d8",
        }
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"ForeshadowDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#fsStats {{ color: {muted}; font-size: 12px; padding-left: 8px; }}"
            f"QLabel#fsTip {{ color: {muted}; font-size: 12px; }}"
            # ---- 表格优化：无网格、扁平表头、悬停底色、行高 ----
            f"QTableWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px; alternate-background-color: {qss_alpha(muted, 0x08)};"
            f" gridline-color: transparent; outline: none; }}"
            f"QTableWidget::item {{ border-bottom: 1px solid {qss_alpha(muted, 0x12)}; padding: 10px 8px; }}"
            f"QTableWidget::item:hover {{ background-color: {qss_alpha(accent, 0x08)}; }}"
            f"QTableWidget::item:selected {{ background-color: {qss_alpha(accent, 0x22)}; color: {text}; }}"
            f"QHeaderView::section {{ background-color: transparent; color: {muted};"
            f" border: none; border-bottom: 2px solid {qss_alpha(muted, 0x33)};"
            f" padding: 10px 8px; font-weight: bold; }}"
        )
        self.add_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                accent_hover,
                selector="QPushButton",
                padding="6px 12px",
                bold=True,
            )
        )
        # qta 图标色不跟随 QSS，主题切换时显式重着色
        self.add_btn.setIcon(qta.icon("fa5s.plus", color=accent_fg))
        if not self._scanning:
            self.scan_btn.setIcon(qta.icon("fa5s.magic", color=text))

    def closeEvent(self, event):
        """关闭时：停动画、标记 scanning=False。

        不 wait worker —— requests 阻塞在 IO 上不响应 stop 标志，
        wait 只会卡死主线程；worker 由 workspace 持有，会自己跑完。
        若用户想在扫描中取消：关闭即可，结果被静默丢弃。
        """
        self._scan_timer.stop()
        self._scanning = False
        # 现在 worker 挂 workspace（见 _run_foreshadow_scan），dialog 上
        # 没有 worker 引用，无需 disconnect —— 之前对 QThread 调无参
        # disconnect() 在 PySide6 上行为不稳定，是历史残留。
        super().closeEvent(event)
