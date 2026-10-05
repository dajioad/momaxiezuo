# ui/format_dialog.py
"""墨码写作 - 一键排版面板

规则勾选 → 差异预览 → 确认应用；应用前自动生成章节快照（铁律）。

S2：顶部加"排版方案"单选（网文标准 / 投稿格式 / 只清理文字 / 自定义）。
选前三个时，下方规则控件整体隐藏，只留「预览差异 / 应用排版」；
选「自定义」才展开全部控件（保持旧行为）。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QComboBox,
    QPlainTextEdit,
    QFrame,
    QGridLayout,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QApplication,
    QButtonGroup,
    QWidget,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QTextCursor
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from ui.batch_progress import BatchProgressMixin
from utils.format_engine import (
    RULES,
    DEFAULT_RULES,
    format_text,
    build_diff,
    BLANK_KEEP_ONE,
    BLANK_REMOVE_ALL,
    INDENT_KEEP,
    INDENT_FULL2,
    INDENT_HALF2,
    INDENT_HALF4,
    INDENT_NONE,
)
from utils.work_store import Work
from utils.logger import get_logger

logger = get_logger()


# ============================================================
# 排版方案预设（S2）
# ============================================================
# 选非"自定义"时，下方规则控件整体隐藏，只用这里的固定参数。
# 参数与 format_engine.RULES 的 rule_id 一一对应。
_PRESETS = {
    "webnovel": {
        "label": "网文标准",
        "desc": "段首两个全角缩进、压缩空行、标题前后空行、清理行尾空格",
        "rules": {
            "punct": False,
            "fullwidth_digit": False,
            "quote": False,
            "single_quote": False,
            "ellipsis": False,
            "dash": False,
            "cn_space": False,
            "trailing": True,
            "indent": True,
            "title_blank": True,
            "blank": True,
        },
        "indent_style": INDENT_FULL2,
        "blank_mode": BLANK_KEEP_ONE,
    },
    "submission": {
        "label": "投稿格式",
        "desc": "网文标准 + 删除全部空行 + 引号配对 + 省略号破折号规范 + 标点转中文",
        "rules": {
            "punct": True,
            "fullwidth_digit": True,
            "quote": True,
            "single_quote": True,
            "ellipsis": True,
            "dash": True,
            "cn_space": False,
            "trailing": True,
            "indent": True,
            "title_blank": True,
            "blank": True,
        },
        "indent_style": INDENT_FULL2,
        "blank_mode": BLANK_REMOVE_ALL,
    },
    "clean_only": {
        "label": "只清理文字",
        "desc": "行尾空格 + 汉字段间空格 + 全角数字转半角，不动缩进和空行",
        "rules": {
            "punct": False,
            "fullwidth_digit": True,
            "quote": False,
            "single_quote": False,
            "ellipsis": False,
            "dash": False,
            "cn_space": True,
            "trailing": True,
            "indent": False,
            "title_blank": False,
            "blank": False,
        },
        "indent_style": INDENT_KEEP,
        "blank_mode": BLANK_KEEP_ONE,
    },
    "custom": {
        "label": "自定义",
        "desc": "手动勾选下方的每一条规则",
    },
}
_PRESET_ORDER = ["webnovel", "submission", "clean_only", "custom"]


class _ChapterPickerDialog(QDialog):
    """多选章节对话框（一键排版 → 选定章节）。"""

    def __init__(self, chapters: list, preselected: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择章节")
        self.setMinimumSize(480, 560)
        self._chapters = list(chapters)
        pre_set = set(preselected or [])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        tip = QLabel(f"共 {len(self._chapters)} 章。勾选要处理的章节。")
        tip.setStyleSheet("color: #928a7c; font-size: 12px;")
        lay.addWidget(tip)

        btn_row = QHBoxLayout()
        for label, slot in (
            ("全选", lambda: self._set_all(True)),
            ("全不选", lambda: self._set_all(False)),
            ("反选", self._invert),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.NoSelection)
        for i, ch in enumerate(self._chapters):
            title = ch.get("title", "") or ch.get("id", "")
            item = QListWidgetItem(f"{i + 1:03d}  {title}")
            item.setData(Qt.UserRole, ch.get("id", ""))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if ch.get("id") in pre_set else Qt.Unchecked)
            self.list_widget.addItem(item)
        lay.addWidget(self.list_widget, 1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            "color: #b08a4e; font-size: 12px; font-weight: 600;"
        )
        lay.addWidget(self.summary_label)
        self.list_widget.itemChanged.connect(self._update_summary)
        self._update_summary()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _set_all(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def _invert(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setCheckState(
                Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            )

    def _update_summary(self, *_):
        n = sum(
            1
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.Checked
        )
        self.summary_label.setText(f"已选 {n} 章")

    def get_selected(self) -> list:
        out = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.data(Qt.UserRole))
        return out


class FormatDialog(BatchProgressMixin, QDialog):
    # 参数：受影响章节 id 集合（workspace 据此刷新编辑器）
    applied = Signal(set)

    def __init__(self, work: Work, current_chapter_id: str = None, parent=None):
        super().__init__(parent)
        self.work = work
        self.current_chapter_id = current_chapter_id
        self._preview_cache = {}  # chapter_id -> 排版后文本
        self._preview_stats = {}  # chapter_id -> {rule_id: count}
        self._preview_expanded = True
        self._selected_chapter_ids: list = []  # "选定章节"模式用
        self.setWindowTitle("一键排版")
        # 720 在 1366×768 上几乎顶到屏幕边；内部是 splitter，压缩安全。
        self.setMinimumSize(900, 620)
        self._build_ui()
        self.apply_theme_colors()
        self._refresh_scope()
        # 默认方案：网文标准（会触发 _on_preset_changed）
        self.preset_radios["webnovel"].setChecked(True)
        QTimer.singleShot(0, lambda: self._set_preview_expanded(True))

    # =================== 骨架 ===================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ---- 顶部：范围 ----
        scope_bar = QFrame()
        scope_bar.setObjectName("scopeBar")
        sr = QHBoxLayout(scope_bar)
        sr.setContentsMargins(12, 6, 12, 6)
        sr.setSpacing(10)
        sr.addWidget(QLabel("排版范围"))
        self.scope_combo = QComboBox()
        self.scope_combo.setMinimumWidth(260)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        sr.addWidget(self.scope_combo)
        sr.addStretch()
        hint = QLabel("应用前会自动备份原章节")
        hint.setObjectName("scopeHint")
        sr.addWidget(hint)
        root.addWidget(scope_bar)

        # ---- 排版方案（S2 新增）----
        preset_bar = QFrame()
        preset_bar.setObjectName("presetBar")
        pr = QHBoxLayout(preset_bar)
        pr.setContentsMargins(12, 6, 12, 6)
        pr.setSpacing(14)
        pr.addWidget(QLabel("排版方案"))
        self.preset_group = QButtonGroup(self)
        self.preset_radios = {}
        for key in _PRESET_ORDER:
            info = _PRESETS[key]
            rb = QRadioButton(info["label"])
            rb.setToolTip(info.get("desc", ""))
            rb.toggled.connect(self._on_preset_changed)
            self.preset_group.addButton(rb)
            self.preset_radios[key] = rb
            pr.addWidget(rb)
        pr.addStretch()
        root.addWidget(preset_bar)

        # ---- 参数区 ----
        param_content = QFrame()
        param_content.setObjectName("paramBox")
        cl = QVBoxLayout(param_content)
        cl.setContentsMargins(16, 10, 16, 12)
        cl.setSpacing(2)

        # 预设说明（非"自定义"时显示，替代规则控件）
        self.preset_hint = QLabel("")
        self.preset_hint.setObjectName("presetHint")
        self.preset_hint.setWordWrap(True)
        self.preset_hint.setVisible(False)
        cl.addWidget(self.preset_hint)

        # 参数控件容器（选预设时整体隐藏）
        self.param_controls = QWidget()
        pc = QVBoxLayout(self.param_controls)
        pc.setContentsMargins(0, 0, 0, 0)
        pc.setSpacing(2)

        # 规则复选框（跳过 indent —— 它单独用下拉控制）
        pc.addWidget(self._section_header("排版规则"))
        rules_grid = QGridLayout()
        rules_grid.setHorizontalSpacing(20)
        rules_grid.setVerticalSpacing(6)
        self.rule_checks = {}
        row, col = 0, 0
        for rule in RULES:
            if rule.rule_id == "indent":
                continue
            cb = QCheckBox(rule.label)
            cb.setChecked(DEFAULT_RULES.get(rule.rule_id, rule.default_on))
            cb.setToolTip(rule.desc)
            cb.stateChanged.connect(self._clear_preview)
            self.rule_checks[rule.rule_id] = cb
            rules_grid.addWidget(cb, row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1
        pc.addLayout(rules_grid)
        pc.addWidget(self._divider())

        # 缩进样式
        pc.addWidget(self._section_header("段首缩进"))
        indent_row = QHBoxLayout()
        indent_row.setSpacing(10)
        self.indent_combo = QComboBox()
        self.indent_combo.setMinimumWidth(280)
        self.indent_combo.addItem("两个全角空格　　（网文默认）", INDENT_FULL2)
        self.indent_combo.addItem("两个半角空格", INDENT_HALF2)
        self.indent_combo.addItem("四个半角空格（Markdown 风）", INDENT_HALF4)
        self.indent_combo.addItem("去掉段首缩进", INDENT_NONE)
        self.indent_combo.addItem("保持原样（不处理缩进）", INDENT_KEEP)
        self.indent_combo.currentIndexChanged.connect(self._clear_preview)
        indent_row.addWidget(self.indent_combo)
        indent_row.addStretch()
        pc.addLayout(indent_row)
        pc.addWidget(self._divider())

        # 空行模式
        pc.addWidget(self._section_header("空行处理"))
        blank_row = QHBoxLayout()
        blank_row.setSpacing(16)
        self.rb_keep = QRadioButton("保留一个空行")
        self.rb_remove = QRadioButton("删除全部空行")
        self.rb_remove.setToolTip("适合网文投稿，段落之间不留空行")
        self.rb_keep.setChecked(True)
        self.rb_keep.toggled.connect(self._clear_preview)
        blank_row.addWidget(self.rb_keep)
        blank_row.addWidget(self.rb_remove)
        blank_row.addStretch()
        pc.addLayout(blank_row)
        pc.addStretch()

        cl.addWidget(self.param_controls)
        cl.addStretch()

        self.param_scroll = QScrollArea()
        self.param_scroll.setWidgetResizable(True)
        self.param_scroll.setFrameShape(QFrame.NoFrame)
        self.param_scroll.setWidget(param_content)

        # ---- 预览区 ----
        preview_box = QFrame()
        preview_box.setObjectName("previewBox")
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        self.stats_bar = QFrame()
        self.stats_bar.setObjectName("statsBar")
        sb = QHBoxLayout(self.stats_bar)
        sb.setContentsMargins(14, 6, 8, 6)
        sb.setSpacing(8)
        self.stats_label = QLabel("点下方「预览差异」查看效果")
        self.stats_label.setObjectName("statsLabel")
        sb.addWidget(self.stats_label)
        sb.addStretch()

        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("togglePreviewBtn")
        self.toggle_btn.setFixedSize(26, 26)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setFlat(True)
        self.toggle_btn.clicked.connect(self._toggle_preview)
        sb.addWidget(self.toggle_btn)
        pv.addWidget(self.stats_bar)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.diff_view.setPlaceholderText("差异会显示在这里")
        self.diff_view.setFrameShape(QFrame.NoFrame)
        pv.addWidget(self.diff_view, 1)

        self.preview_box = preview_box

        # ---- splitter ----
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.param_scroll)
        self.splitter.addWidget(preview_box)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 1)
        # ---- 批量进度行（平时隐藏）----
        self._build_progress_row(root)
        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        self.preview_btn = QPushButton(" 预览差异")
        self.preview_btn.setCursor(Qt.PointingHandCursor)
        self.preview_btn.clicked.connect(self._do_preview)
        btn_row.addWidget(self.preview_btn)
        self.apply_btn = QPushButton(" 应用排版")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._do_apply)
        btn_row.addWidget(self.apply_btn)
        btn_row.addSpacing(12)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _section_header(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("sectionHeader")
        return lab

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("sectionDivider")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    # =================== 预设切换（S2） ===================

    def _current_preset(self) -> str:
        for key, rb in self.preset_radios.items():
            if rb.isChecked():
                return key
        return "custom"

    def _on_preset_changed(self, *_):
        """切换预设：非自定义时隐藏参数控件，应用预设参数。"""
        # 构建 UI 阶段 radio 尚未全就绪，保护
        if not hasattr(self, "rule_checks"):
            return
        key = self._current_preset()

        if key == "custom":
            self.param_controls.setVisible(True)
            self.preset_hint.setVisible(False)
            self._reset_preview_state()
            return

        info = _PRESETS[key]
        # 应用预设到控件（保持一致性，切回自定义时参数还在）
        for rid, cb in self.rule_checks.items():
            cb.blockSignals(True)
            cb.setChecked(info["rules"].get(rid, False))
            cb.blockSignals(False)
        style = info.get("indent_style", INDENT_FULL2)
        idx = self.indent_combo.findData(style)
        if idx >= 0:
            self.indent_combo.blockSignals(True)
            self.indent_combo.setCurrentIndex(idx)
            self.indent_combo.blockSignals(False)
        if info.get("blank_mode") == BLANK_REMOVE_ALL:
            self.rb_remove.blockSignals(True)
            self.rb_remove.setChecked(True)
            self.rb_remove.blockSignals(False)
        else:
            self.rb_keep.blockSignals(True)
            self.rb_keep.setChecked(True)
            self.rb_keep.blockSignals(False)

        self.preset_hint.setText(f"已应用「{info['label']}」：{info['desc']}")
        self.preset_hint.setVisible(True)
        self.param_controls.setVisible(False)
        self._reset_preview_state()

    def _reset_preview_state(self):
        self._preview_cache.clear()
        self._preview_stats.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("点下方「预览差异」查看效果")
        self.diff_view.setPlainText("")

    # =================== 折叠控制 ===================

    def _toggle_preview(self):
        self._set_preview_expanded(not self._preview_expanded)

    def _set_preview_expanded(self, expanded: bool):
        self._preview_expanded = bool(expanded)
        self.diff_view.setVisible(self._preview_expanded)

        if self._preview_expanded:
            total = max(self.splitter.height(), 400)
            self.splitter.setSizes([int(total * 0.6), int(total * 0.4)])
        else:
            total = max(self.splitter.height(), 400)
            self.splitter.setSizes([total - 42, 42])

        self._refresh_toggle_icon()

    def _refresh_toggle_icon(self):
        t = self._resolve_theme()
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        if self._preview_expanded:
            self.toggle_btn.setIcon(qta.icon("fa5s.chevron-up", color=accent))
            self.toggle_btn.setToolTip("收起预览")
        else:
            self.toggle_btn.setIcon(qta.icon("fa5s.chevron-down", color=muted))
            self.toggle_btn.setToolTip("展开预览")

    # =================== 主题 ===================

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    return editor._theme or {}
                break
            if hasattr(p, "_theme"):
                return getattr(p, "_theme") or {}
            p = p.parent()
        return {}

    def apply_theme_colors(self):
        t = self._resolve_theme()
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text_color = t.get("text", "#3b342a")
        bg = t.get("bg", "#f7f2e8")
        sidebar = t.get("sidebar", "#efe8d8")

        from ui.dialog_styles import input_qss, button_qss
        from ui.theme_style import accent_button_qss, ghost_button_qss

        self.setStyleSheet(
            f"FormatDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text_color}; background: transparent; }}"
            f"QLabel#sectionHeader {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; padding: 10px 0 2px 0; }}"
            f"QLabel#scopeHint {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#presetHint {{ color: {accent}; font-size: 13px;"
            f" padding: 12px 4px; }}"
            f"QFrame#sectionDivider {{"
            f" background-color: {qss_alpha(muted, 0x33)}; border: none; }}"
            f"QFrame#scopeBar, QFrame#presetBar {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x18)};"
            f" border-radius: 10px; }}"
            f"QFrame#paramBox {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; }}"
            f"QFrame#previewBox {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; }}"
            f"QFrame#statsBar {{"
            f" background-color: {qss_alpha(accent, 0x14)};"
            f" border-top-left-radius: 10px;"
            f" border-top-right-radius: 10px;"
            f" border-bottom: 1px solid {qss_alpha(accent, 0x28)}; }}"
            f"QLabel#statsLabel {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; }}"
            f"QPushButton#togglePreviewBtn {{"
            f" border: none; background: transparent; border-radius: 13px; }}"
            f"QPushButton#togglePreviewBtn:hover {{"
            f" background-color: {qss_alpha(accent, 0x22)}; }}"
            f"QScrollArea {{ border: none; background: transparent; }}"
            f"QSplitter::handle {{ background: transparent; }}"
            f"QSplitter::handle:vertical {{ height: 6px; }}"
            f"QSplitter::handle:hover {{"
            f" background-color: {qss_alpha(accent, 0x40)}; }}"
            + input_qss(t)
            + button_qss(t)
        )

        btn_ss = accent_button_qss(
            accent,
            accent_fg,
            accent_hover,
            selector="QPushButton",
            padding="8px 22px",
            bold=True,
        )
        # 「预览」为幽灵次按钮，「应用」才是实底主按钮
        ghost_ss = ghost_button_qss(t, selector="QPushButton", padding="8px 22px")
        self.preview_btn.setStyleSheet(ghost_ss)
        self.apply_btn.setStyleSheet(btn_ss)
        self.preview_btn.setIcon(qta.icon("fa5s.eye", color=accent))
        self.apply_btn.setIcon(qta.icon("fa5s.check", color=accent_fg))

        self.diff_view.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; color: {text_color};"
            f" padding: 10px 14px; border: none;"
            f" selection-background-color: {qss_alpha(accent, 0x55)}; }}"
        )
        self._refresh_toggle_icon()
        self._recolor_diff()

    # =================== 差异着色 ===================

    def _clean_diff(self, old: str, new: str, context: int = 1) -> str:
        raw = build_diff(old, new, context=context)
        if not raw:
            return ""
        lines = []
        for line in raw.splitlines():
            if (
                line.startswith("---")
                or line.startswith("+++")
                or line.startswith("--- ")
                or line.startswith("+++ ")
            ):
                continue
            if line.startswith("@@"):
                lines.append("  ···")
                continue
            lines.append(line)
        return "\n".join(lines)

    def _recolor_diff(self):
        text = self.diff_view.toPlainText()
        if not text:
            return
        lines = text.split("\n")
        if len(lines) > 3000:
            return

        t = self._resolve_theme()
        try:
            is_light = QColor(t.get("bg", "#f7f2e8")).lightness() > 150
        except Exception:
            is_light = True

        accent = QColor(t.get("accent", "#b08a4e"))
        if is_light:
            c_del = QColor("#a93326")
            c_add = QColor("#137333")
            c_muted = QColor("#9a8e76")
            bg_del = QColor(192, 57, 43, 22)
            bg_add = QColor(26, 127, 55, 22)
        else:
            c_del = QColor("#ff8a80")
            c_add = QColor("#7ee787")
            c_muted = QColor("#7A7E8C")
            bg_del = QColor(255, 138, 128, 36)
            bg_add = QColor(126, 231, 135, 36)

        doc = self.diff_view.document()
        block = doc.begin()
        while block.isValid():
            line = block.text()
            fmt = QTextCharFormat()
            colored = True
            if line.startswith("▎"):
                fmt.setForeground(accent)
                fmt.setFontWeight(QFont.Bold)
                fmt.setFontPointSize(11)
            elif line.startswith("  ···"):
                fmt.setForeground(c_muted)
            elif line.startswith("-"):
                fmt.setForeground(c_del)
                fmt.setBackground(bg_del)
            elif line.startswith("+"):
                fmt.setForeground(c_add)
                fmt.setBackground(bg_add)
            else:
                colored = False
            if colored:
                c = QTextCursor(block)
                c.setPosition(block.position())
                c.setPosition(block.position() + len(line), QTextCursor.KeepAnchor)
                c.mergeCharFormat(fmt)
            block = block.next()

    # =================== 范围 / 参数 ===================

    def _refresh_scope(self):
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        if self.current_chapter_id:
            ch = self.work.get_chapter(self.current_chapter_id)
            if ch:
                self.scope_combo.addItem(
                    f"仅当前章节：{ch['title']}", ("chapter", self.current_chapter_id)
                )
        self.scope_combo.addItem("选定章节…", ("select_chapters", None))
        if self._selected_chapter_ids:
            self.scope_combo.addItem(
                f"已选 {len(self._selected_chapter_ids)} 章", ("selected", None)
            )
        self.scope_combo.addItem(
            f"全书所有章节（{len(self.work.chapters)} 章）", ("all", None)
        )
        self.scope_combo.blockSignals(False)

    def _scope(self):
        data = self.scope_combo.currentData()
        if not data:
            return ("all", None)
        return data

    def _on_scope_changed(self, _idx: int):
        """范围下拉变化：若选的是"选定章节…"，弹多选对话框。"""
        data = self._scope()
        if data[0] == "select_chapters":
            self._open_chapter_picker()
            return
        self._clear_preview()

    def _open_chapter_picker(self):
        chapters = [
            {"id": c.get("id", ""), "title": c.get("title", "")}
            for c in self.work.chapters
        ]
        dlg = _ChapterPickerDialog(
            chapters, preselected=self._selected_chapter_ids, parent=self
        )
        if dlg.exec() != QDialog.Accepted:
            self._restore_default_scope()
            return
        selected = dlg.get_selected()
        if not selected:
            QMessageBox.information(self, "提示", "没有勾选任何章节。")
            self._restore_default_scope()
            return
        self._selected_chapter_ids = selected
        self._refresh_scope()
        # 定位到"已选 N 章"
        for i in range(self.scope_combo.count()):
            if self.scope_combo.itemData(i) == ("selected", None):
                self.scope_combo.blockSignals(True)
                self.scope_combo.setCurrentIndex(i)
                self.scope_combo.blockSignals(False)
                break
        self._clear_preview()

    def _restore_default_scope(self):
        """取消多选时，回退到第一个可用选项。"""
        self._refresh_scope()
        if self.scope_combo.count() > 0:
            self.scope_combo.blockSignals(True)
            self.scope_combo.setCurrentIndex(0)
            self.scope_combo.blockSignals(False)

    def _target_chapters(self):
        kind, cid = self._scope()
        if kind == "chapter":
            return [cid]
        if kind == "selected":
            return list(self._selected_chapter_ids)
        if kind == "select_chapters":
            # 理论上不会走到（_on_scope_changed 会先弹对话框）
            return []
        return [c["id"] for c in self.work.chapters]

    def _rules(self) -> dict:
        """S2：非自定义时用预设固定参数；自定义时读控件。"""
        key = self._current_preset()
        if key != "custom":
            return dict(_PRESETS[key]["rules"])
        return {rid: cb.isChecked() for rid, cb in self.rule_checks.items()}

    def _indent_style(self) -> str:
        key = self._current_preset()
        if key != "custom":
            return _PRESETS[key].get("indent_style", INDENT_FULL2)
        return self.indent_combo.currentData() or INDENT_FULL2

    def _blank_mode(self) -> str:
        key = self._current_preset()
        if key != "custom":
            return _PRESETS[key].get("blank_mode", BLANK_KEEP_ONE)
        return BLANK_REMOVE_ALL if self.rb_remove.isChecked() else BLANK_KEEP_ONE

    def _clear_preview(self, *_):
        # 规则变了，正在跑的预览已过时，静默作废（不触发它的收尾）
        self._invalidate_batch()
        self._preview_cache.clear()
        self._preview_stats.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("规则已修改，请重新点「预览差异」")
        self.diff_view.setPlainText("")

    # =================== 预览 ===================

    _batch_chunk = 20  # 预览是纯内存变换，块可以大些

    def _do_preview(self):
        rules = self._rules()
        indent_style = self._indent_style()
        rules["indent"] = indent_style != INDENT_KEEP
        mode = self._blank_mode()

        self._preview_cache.clear()
        self._preview_stats.clear()
        self.apply_btn.setEnabled(False)

        cids = list(self._target_chapters())
        if not cids:
            self._render_diff(0, 0)
            return

        state = {"changed": 0, "total": 0}

        def step(cid):
            old = self.work.read_chapter(cid)
            new, stats = format_text(old, rules, mode, indent_style=indent_style)
            if new == old:
                return
            self._preview_cache[cid] = new
            self._preview_stats[cid] = stats
            state["total"] += sum(stats.values())
            state["changed"] += 1

        def done(cancelled, error):
            if error is not None:
                QMessageBox.critical(self, "预览失败", str(error))
                return
            if cancelled:
                # 部分预览没有意义：拿它去应用只会改一半章节
                self._preview_cache.clear()
                self._preview_stats.clear()
                self.diff_view.setPlainText("")
                self.apply_btn.setEnabled(False)
                self.stats_label.setText("已取消预览")
                return
            self._render_diff(state["changed"], state["total"])

        self._start_batch(cids, step, "预览", done)

        if self._preview_cache and not self._preview_expanded:
            self._set_preview_expanded(True)

    def _render_diff(self, changed_chapters: int, total_changes: int):
        if not self._preview_cache:
            self.stats_label.setText("✓ 当前规则下没有需要修改的内容")
            self.diff_view.setPlainText("")
            self.apply_btn.setEnabled(False)
            return

        from utils.format_engine import RULES as _RULES

        rule_labels = {r.rule_id: r.label for r in _RULES}

        parts = []
        for cid, new in self._preview_cache.items():
            old = self.work.read_chapter(cid)
            ch = self.work.get_chapter(cid)
            title = ch["title"] if ch else cid
            stats = self._preview_stats.get(cid, {})

            detail = " · ".join(
                f"{rule_labels.get(k, k)} {v} 处" for k, v in stats.items() if v
            )
            header = f"▎{title}"
            if detail:
                header += f"    {detail}"
            parts.append(header)
            parts.append(self._clean_diff(old, new))
            parts.append("")

        self.diff_view.setPlainText("\n".join(parts).rstrip())
        self._recolor_diff()

        self.stats_label.setText(
            f"将修改 {changed_chapters} 章 · 共 {total_changes} 处"
        )
        self.apply_btn.setEnabled(True)

    # =================== 应用 ===================

    _apply_chunk = 5  # 每项要打快照 + fsync 写盘，块小些更跟手

    def _do_apply(self):
        if not self._preview_cache:
            QMessageBox.information(self, "提示", "请先点「预览差异」查看效果。")
            return

        items = list(self._preview_cache.items())
        affected = set()

        def step(item):
            cid, new_text = item
            old = self.work.read_chapter(cid)
            if old == new_text:
                return
            self.work.create_snapshot(cid)
            self.work.write_chapter(cid, new_text)
            affected.add(cid)

        def done(cancelled, error):
            self.preview_btn.setEnabled(True)
            self.apply_btn.setEnabled(True)
            if error is not None:
                QMessageBox.critical(
                    self,
                    "应用失败",
                    f"写入章节时出错：{error}\n" "已处理的章节可从历史快照恢复。",
                )
                if affected:
                    self.applied.emit(affected)
                return
            if affected:
                self.applied.emit(affected)
            if cancelled:
                QMessageBox.information(
                    self,
                    "已取消",
                    f"已在处理 {len(affected)} 章后取消。\n\n"
                    "已处理的章节原稿均已打快照，可在「作品 → 历史快照浏览器」"
                    "里恢复；剩余章节未改动。",
                )
                self._reset_preview_state()
                return
            QMessageBox.information(
                self,
                "排版完成",
                f"已排版 {len(affected)} 个章节，原稿均已自动备份为历史快照。",
            )
            self.accept()

        self.preview_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self._start_batch(items, step, "应用", done, chunk=self._apply_chunk)
