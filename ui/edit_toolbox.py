# ui/edit_toolbox.py
"""墨码写作 - 编辑工具箱（非模态对话框）

四个工具，共用同一套骨架：
  顶部标签 → 范围选择 → 参数区 → 预览区 → 底部按钮

工具：
  1. 段落处理   拆分长段 / 合并短行 / 空行 / 缩进
  2. 文本清洗   行尾行首空白 / 空行压缩 / 不可见字符 / 复制残留
  3. 格式统一   标点 / 引号 / 数字 / 全角半角
  4. 术语替换   作品级查找替换，按章节分组列出所有匹配，逐条可勾选

约定：
- 不点"预览"不能"应用"
- 范围始终在顶部，预览/应用按钮位置固定
- 参数持久化到 config 的 [EditToolbox] / state

S3：段落处理 / 文本清洗 / 格式统一三个 tab 各加"模式"下拉。
选非"自定义"时，下方参数控件整体隐藏，只显示模式说明；
选"自定义"才展开全部参数控件。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QComboBox,
    QCheckBox,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QWidget,
    QMessageBox,
    QApplication,
    QScrollArea,
    QSplitter,
    QFrame,
    QTreeWidget,
    QTreeWidgetItem,
    QFileDialog,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QTextCursor
import qtawesome as qta
import json

from ui.theme_style import accent_button_qss, qss_alpha
from ui.batch_progress import BatchProgressMixin
from utils.format_engine import build_diff
from utils.edit_tools.paragraph import (
    ParagraphParams,
    process_paragraphs,
    format_stats,
)
from utils.edit_tools.clean import (
    CleanParams,
    clean_text,
    format_clean_stats,
)
from utils.edit_tools.unify import (
    UnifyParams,
    unify_text,
    format_unify_stats,
)
from utils.edit_tools.term_replace import (
    TermParams,
    find_replacements,
    apply_replacements,
    format_term_stats,
)
from utils.logger import get_logger

logger = get_logger()

_SCOPE_SELECTION = "selection"
_SCOPE_CURRENT = "current"
_SCOPE_BOOK = "book"

_CFG_SECTION = "EditToolbox"
_CFG_KEY = "state"


class EditToolboxDialog(BatchProgressMixin, QDialog):
    """编辑工具箱（非模态）"""

    applied = Signal(set)

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.ws = workspace
        self.setWindowTitle("编辑工具箱")
        self.setMinimumSize(900, 640)
        self.setModal(False)

        self._preview = {}
        self._preview_stats = {}
        self._term_preview = {}
        self._theme = self._resolve_theme()
        self._term_updating = False
        # 构建期间抑制 _on_params_changed 的副作用：
        # _load_toolbox_params 恢复参数时，会触发一堆 stateChanged /
        # currentIndexChanged → _on_params_changed → _invalidate_batch，
        # 把 _batch_gen 反复推进，导致后续首次 _start_batch 的 gen 号
        # 与回调对不上（表现为“预览跑完但 UI 不更新”）。
        self._building = True

        self._build_ui()
        self._load_toolbox_params()
        self._building = False
        self._refresh_scope_state()

    def _resolve_theme(self) -> dict:
        editor = getattr(self.ws, "editor", None)
        if editor is not None and hasattr(editor, "_theme"):
            return editor._theme or {}
        return {}

    # =================== UI 骨架 ===================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        scope_bar = QFrame()
        scope_bar.setObjectName("scopeBar")
        scope_row = QHBoxLayout(scope_bar)
        scope_row.setContentsMargins(12, 6, 12, 6)
        scope_row.setSpacing(10)
        scope_row.addWidget(QLabel("应用范围"))
        self.scope_group = QButtonGroup(self)
        self.rb_selection = QRadioButton("选中的文本")
        self.rb_current = QRadioButton("当前章节")
        self.rb_book = QRadioButton("整部作品")
        self.rb_current.setChecked(True)
        for rb in (self.rb_selection, self.rb_current, self.rb_book):
            self.scope_group.addButton(rb)
            scope_row.addWidget(rb)
        scope_row.addStretch()
        self.scope_tip = QLabel("")
        self.scope_tip.setObjectName("scopeTip")
        scope_row.addWidget(self.scope_tip)
        root.addWidget(scope_bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_paragraph_tab(), "段落处理")
        self.tabs.addTab(self._build_clean_tab(), "文本清洗")
        self.tabs.addTab(self._build_unify_tab(), "格式统一")
        self.tabs.addTab(self._build_term_tab(), "术语替换")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        preview_box = QFrame()
        preview_box.setObjectName("previewBox")
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        self.stats_bar = QFrame()
        self.stats_bar.setObjectName("statsBar")
        sb = QHBoxLayout(self.stats_bar)
        sb.setContentsMargins(12, 8, 12, 8)
        self.stats_label = QLabel("点击下方「预览」按钮查看效果")
        self.stats_label.setObjectName("statsLabel")
        sb.addWidget(self.stats_label)
        sb.addStretch()
        pv.addWidget(self.stats_bar)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.diff_view.setPlaceholderText("差异会显示在这里")
        self.diff_view.setFrameShape(QFrame.NoFrame)
        pv.addWidget(self.diff_view, 1)

        self.term_view = QTreeWidget()
        self.term_view.setHeaderLabels(["原文 / 上下文", "→ 替换为"])
        self.term_view.setColumnCount(2)
        self.term_view.setColumnWidth(0, 500)
        self.term_view.setRootIsDecorated(True)
        self.term_view.setAlternatingRowColors(True)
        self.term_view.setRootIsDecorated(True)
        self.term_view.setMouseTracking(True)
        self.term_view.setStyleSheet("QTreeWidget::item { padding: 8px 6px; }")
        self.term_view.setVisible(False)
        self.term_view.setFrameShape(QFrame.NoFrame)
        self.term_view.itemChanged.connect(self._on_term_item_changed)
        pv.addWidget(self.term_view, 1)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(preview_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 300])
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        # ---- 批量进度行（平时隐藏）----
        self._build_progress_row(root)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        self.preview_btn = QPushButton(" 预览")
        self.preview_btn.setCursor(Qt.PointingHandCursor)
        self.preview_btn.clicked.connect(self._do_preview)
        btn_row.addWidget(self.preview_btn)
        self.apply_btn = QPushButton(" 应用到正文")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._do_apply)
        btn_row.addWidget(self.apply_btn)
        btn_row.addSpacing(12)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._apply_theme()

    # =================== 主题 ===================

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

        from ui.dialog_styles import input_qss, button_qss, table_qss
        from ui.theme_style import accent_button_qss, ghost_button_qss

        primary_ss = accent_button_qss(
            accent,
            accent_fg,
            accent_hover,
            selector="QPushButton#tbPrimary",
            padding="8px 22px",
            bold=True,
        )
        # 「预览」是次级正向操作：幽灵描边按钮，只有「应用」是实底主按钮
        ghost_ss = ghost_button_qss(
            t, selector="QPushButton#tbGhost", padding="8px 22px"
        )

        self.setStyleSheet(
            f"EditToolboxDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#sectionHeader {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; padding: 10px 0 2px 0; }}"
            f"QLabel#scopeTip {{ color: {muted}; font-size: 11px; }}"
            f"QLabel#statsLabel {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; }}"
            f"QFrame#sectionDivider {{"
            f" background-color: {qss_alpha(muted, 0x33)}; border: none; }}"
            f"QFrame#scopeBar {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x18)};"
            f" border-radius: 12px; }}"
            f"QFrame#previewBox {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; }}"
            f"QFrame#statsBar {{"
            f" background-color: {qss_alpha(accent, 0x14)};"
            f" border-top-left-radius: 10px;"
            f" border-top-right-radius: 10px;"
            f" border-bottom: 1px solid {qss_alpha(accent, 0x28)}; }}"
            # Tab：透明底圆角块，选中态底部 2px 金色下划线
            f"QTabWidget::pane {{ border: none; background: transparent;"
            f" top: -1px; }}"
            f"QTabBar {{ qproperty-drawBase: 0; }}"
            f"QTabBar::tab {{ background: transparent; color: {muted};"
            f" border: none; border-bottom: 2px solid transparent;"
            f" border-radius: 6px; padding: 8px 18px 5px 18px;"
            f" margin-right: 4px; }}"
            f"QTabBar::tab:hover {{ color: {accent}; }}"
            f"QTabBar::tab:selected {{ color: {accent};"
            f" border-bottom: 2px solid {accent}; }}" + input_qss(t)
            # 输入控件统一 32px 高（覆盖 input_qss 的默认高度）
            + (
                f"QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{"
                f" min-height: 32px; }}"
            )
            + button_qss(t)
            + table_qss(t)
            + primary_ss
            + ghost_ss
        )

        self.diff_view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {editor_bg}; color: {text};"
            f" border: none; border-radius: 10px;"
            f" padding: 12px 14px;"
            f" selection-background-color: {qss_alpha(accent, 0x55)}; }}"
        )

        self.preview_btn.setObjectName("tbGhost")
        self.apply_btn.setObjectName("tbPrimary")
        self.preview_btn.setStyleSheet(ghost_ss)
        self.apply_btn.setStyleSheet(primary_ss)
        self.preview_btn.setIcon(qta.icon("fa5s.eye", color=accent))
        self.apply_btn.setIcon(qta.icon("fa5s.check", color=accent_fg))

    # =================== 参数区辅助 ===================

    def _scroll_wrap(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        return scroll

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

    def _check(self, text: str, checked: bool = False) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(checked)
        cb.stateChanged.connect(self._on_params_changed)
        return cb

    def _spin(self, lo: int, hi: int, val: int, suffix: str = "") -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setValue(val)
        if suffix:
            sb.setSuffix(suffix)
        sb.setMinimumWidth(84)
        sb.valueChanged.connect(self._on_params_changed)
        return sb

    def _combo(self, items, default: str = "") -> QComboBox:
        cb = QComboBox()
        for label, data in items:
            cb.addItem(label, data)
        if default:
            idx = cb.findData(default)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        cb.setMinimumWidth(170)
        cb.currentIndexChanged.connect(self._on_params_changed)
        return cb

    def _line_edit(self, placeholder: str = "") -> QLineEdit:
        ed = QLineEdit()
        ed.setPlaceholderText(placeholder)
        ed.textChanged.connect(self._on_params_changed)
        return ed

    # =================== 段落处理 ===================

    def _build_paragraph_tab(self) -> QWidget:
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(16, 4, 16, 12)
        form.setSpacing(2)

        # ---- S3：模式下拉 ----
        form.addWidget(self._section_header("模式"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self.para_mode_combo = QComboBox()
        self.para_mode_combo.setMinimumWidth(260)
        for label, data in (
            ("自定义（手动勾选下方规则）", "custom"),
            ("合并短行", "merge"),
            ("拆分长段", "split"),
            ("精简优化（拆分+合并+缩进+空行）", "optimize"),
        ):
            self.para_mode_combo.addItem(label, data)
        self.para_mode_combo.currentIndexChanged.connect(self._on_para_mode_changed)
        mode_row.addWidget(self.para_mode_combo)
        self.para_mode_hint = QLabel("")
        self.para_mode_hint.setStyleSheet(
            f"font-size: 12px; color: {self._theme.get('accent', '#b08a4e')};"
        )
        self.para_mode_hint.setWordWrap(True)
        mode_row.addWidget(self.para_mode_hint, 1)
        form.addLayout(mode_row)
        form.addWidget(self._divider())

        # ---- S3：参数容器（非自定义模式整块隐藏）----
        self.para_controls = QWidget()
        pc = QVBoxLayout(self.para_controls)
        pc.setContentsMargins(0, 0, 0, 0)
        pc.setSpacing(2)

        # 拆分长段
        pc.addWidget(self._section_header("拆分长段"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.split_enabled = self._check("启用", checked=True)
        row.addWidget(self.split_enabled)
        row.addSpacing(12)
        row.addWidget(QLabel("阈值"))
        self.split_threshold = self._spin(40, 500, 120, " 字")
        row.addWidget(self.split_threshold)
        row.addSpacing(8)
        row.addWidget(QLabel("单段最少"))
        self.split_min_keep = self._spin(10, 200, 40, " 字")
        row.addWidget(self.split_min_keep)
        row.addStretch()
        pc.addLayout(row)

        # 拆分位置（多选）
        row_pos = QHBoxLayout()
        row_pos.setSpacing(10)
        row_pos.addWidget(QLabel("拆分位置"))
        self.split_at_period = self._check("句号 。", True)
        self.split_at_question = self._check("问号 ？", True)
        self.split_at_exclaim = self._check("感叹号 ！", True)
        self.split_at_ellipsis = self._check("省略号 …", True)
        for cb in (
            self.split_at_period,
            self.split_at_question,
            self.split_at_exclaim,
            self.split_at_ellipsis,
        ):
            row_pos.addWidget(cb)
        row_pos.addStretch()
        pc.addLayout(row_pos)
        pc.addWidget(self._divider())

        # 合并短行
        pc.addWidget(self._section_header("合并短行"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.merge_enabled = self._check("启用", checked=False)
        row.addWidget(self.merge_enabled)
        row.addSpacing(12)
        row.addWidget(QLabel("短行阈值"))
        self.merge_threshold = self._spin(5, 100, 20, " 字")
        row.addWidget(self.merge_threshold)
        row.addSpacing(8)
        row.addWidget(QLabel("合并上限"))
        self.merge_max = self._spin(50, 1000, 200, " 字")
        row.addWidget(self.merge_max)
        row.addStretch()
        pc.addLayout(row)
        self.merge_skip_dialogue = self._check("对话行不合并（推荐）", True)
        pc.addWidget(self.merge_skip_dialogue)
        pc.addWidget(self._divider())

        # 空行处理
        pc.addWidget(self._section_header("空行处理"))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("段间空行"))
        self.blank_combo = self._combo(
            [("保持原样", "keep"), ("全部删除", "none"), ("保证恰好一个空行", "one")],
            "keep",
        )
        row.addWidget(self.blank_combo)
        row.addStretch()
        pc.addLayout(row)
        pc.addWidget(self._divider())

        # 缩进处理
        pc.addWidget(self._section_header("缩进处理"))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("缩进"))
        self.indent_combo = self._combo(
            [
                ("保持原样", "keep"),
                ("每段加两个全角空格", "add"),
                ("去掉段首缩进", "remove"),
            ],
            "keep",
        )
        row.addWidget(self.indent_combo)
        row.addSpacing(12)
        self.indent_dialogue = self._check("对话行也加缩进", False)
        row.addWidget(self.indent_dialogue)
        row.addStretch()
        pc.addLayout(row)

        pc.addStretch()

        form.addWidget(self.para_controls)
        form.addStretch()
        return self._scroll_wrap(content)

    # ---- S3：段落模式切换 ----

    def _on_para_mode_changed(self, *_):
        key = self.para_mode_combo.currentData()
        if key == "custom":
            self.para_controls.setVisible(True)
            self.para_mode_hint.setText("")
            self._on_params_changed()
            return

        # 非自定义：收起参数控件，填预设值
        self.para_controls.setVisible(False)
        hint_map = {
            "merge": "已应用：合并连续短行（对话行、标题行不动）",
            "split": "已应用：超长段落按句末标点拆分",
            "optimize": "已应用：拆分 + 合并 + 段首加两全角缩进 + 空行压缩",
        }
        preset_map = {
            "merge": dict(split=False, merge=True, blank="keep", indent="keep"),
            "split": dict(split=True, merge=False, blank="keep", indent="keep"),
            "optimize": dict(split=True, merge=True, blank="one", indent="add"),
        }
        p = preset_map.get(key, {})
        if p:
            for w, v in (
                (self.split_enabled, p["split"]),
                (self.merge_enabled, p["merge"]),
            ):
                w.blockSignals(True)
                w.setChecked(v)
                w.blockSignals(False)
            self._set_combo_data(self.blank_combo, p["blank"])
            self._set_combo_data(self.indent_combo, p["indent"])
        self.para_mode_hint.setText(hint_map.get(key, ""))
        self._on_params_changed()

    # =================== 文本清洗 ===================

    def _build_clean_tab(self) -> QWidget:
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(16, 4, 16, 12)
        form.setSpacing(2)

        # ---- S3：模式下拉 ----

        form.addWidget(self._section_header("模式"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self.clean_mode_combo = QComboBox()
        self.clean_mode_combo.setMinimumWidth(260)
        for label, data in (
            ("自定义（手动勾选下方规则）", "custom"),
            ("轻度清理（行首行尾 + 复制残留）", "light"),
            ("标准清理（+ 不可见字符 + 空行压缩）", "standard"),
            ("深度清理（+ 行号 + 连续标点）", "deep"),
        ):
            self.clean_mode_combo.addItem(label, data)
        self.clean_mode_combo.currentIndexChanged.connect(self._on_clean_mode_changed)
        mode_row.addWidget(self.clean_mode_combo)
        self.clean_mode_hint = QLabel("")
        self.clean_mode_hint.setStyleSheet(
            f"font-size: 12px; color: {self._theme.get('accent', '#b08a4e')};"
        )
        self.clean_mode_hint.setWordWrap(True)
        mode_row.addWidget(self.clean_mode_hint, 1)
        form.addLayout(mode_row)
        form.addWidget(self._divider())

        # ---- S3：参数容器 ----
        self.clean_controls = QWidget()
        cc = QVBoxLayout(self.clean_controls)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(2)

        cc.addWidget(self._section_header("行首行尾"))
        self.clean_trailing = self._check("删除行尾空白（半角空格、Tab）", True)
        cc.addWidget(self.clean_trailing)
        self.clean_leading_half = self._check("删除行首空白（保留全角缩进）", True)
        cc.addWidget(self.clean_leading_half)
        self.clean_leading_full = self._check("删除行首空白（含全角缩进）", False)
        self.clean_leading_full.setToolTip("章节标题行始终豁免")
        cc.addWidget(self.clean_leading_full)
        cc.addWidget(self._divider())

        cc.addWidget(self._section_header("段间空行"))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("连续空行"))
        self.clean_blanks = self._combo(
            [
                ("保持原样", "keep_all"),
                ("保留 1 个空行", "keep_one"),
                ("保留 2 个空行", "keep_two"),
                ("全部删除", "remove_all"),
            ],
            "keep_one",
        )
        row.addWidget(self.clean_blanks)
        row.addStretch()
        cc.addLayout(row)
        cc.addWidget(self._divider())

        cc.addWidget(self._section_header("字符清洗"))
        self.clean_invisible = self._check(
            "删除不可见字符（零宽、BOM、NBSP、软连字符）", True
        )
        cc.addWidget(self.clean_invisible)
        self.clean_punct = self._check("合并连续标点（只合并 。，、；：）", False)
        self.clean_punct.setToolTip("！！！ ？？？ 和省略号 …… 保留不动")
        cc.addWidget(self.clean_punct)
        self.clean_linenum = self._check("删除行号残留（行首形如 1. / 12、）", False)
        cc.addWidget(self.clean_linenum)
        self.clean_artifacts = self._check(
            "删除复制残留（[图片]、[插图]、HTML 注释、代码块标记）", True
        )
        cc.addWidget(self.clean_artifacts)

        cc.addStretch()

        form.addWidget(self.clean_controls)
        form.addStretch()
        return self._scroll_wrap(content)

    def _on_clean_mode_changed(self, *_):
        key = self.clean_mode_combo.currentData()
        if key == "custom":
            self.clean_controls.setVisible(True)
            self.clean_mode_hint.setText("")
            self._on_params_changed()
            return
        self.clean_controls.setVisible(False)

        presets = {
            "light": dict(
                trailing=True,
                leading_half=True,
                leading_full=False,
                blanks="keep_all",
                invisible=False,
                punct=False,
                linenum=False,
                artifacts=True,
            ),
            "standard": dict(
                trailing=True,
                leading_half=True,
                leading_full=False,
                blanks="keep_one",
                invisible=True,
                punct=False,
                linenum=False,
                artifacts=True,
            ),
            "deep": dict(
                trailing=True,
                leading_half=True,
                leading_full=False,
                blanks="keep_one",
                invisible=True,
                punct=True,
                linenum=True,
                artifacts=True,
            ),
        }
        hint_map = {
            "light": "已应用：行首行尾清理 + 删除复制残留",
            "standard": "已应用：轻度 + 删不可见字符 + 压缩空行",
            "deep": "已应用：标准 + 删行号 + 合并连续标点",
        }
        p = presets.get(key, {})
        if p:
            for w, v in (
                (self.clean_trailing, p["trailing"]),
                (self.clean_leading_half, p["leading_half"]),
                (self.clean_leading_full, p["leading_full"]),
                (self.clean_invisible, p["invisible"]),
                (self.clean_punct, p["punct"]),
                (self.clean_linenum, p["linenum"]),
                (self.clean_artifacts, p["artifacts"]),
            ):
                w.blockSignals(True)
                w.setChecked(v)
                w.blockSignals(False)
            self._set_combo_data(self.clean_blanks, p["blanks"])
        self.clean_mode_hint.setText(hint_map.get(key, ""))
        self._on_params_changed()

    # =================== 格式统一 ===================

    def _build_unify_tab(self) -> QWidget:
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(16, 4, 16, 12)
        form.setSpacing(2)

        # ---- S3：模式下拉 ----

        form.addWidget(self._section_header("模式"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self.unify_mode_combo = QComboBox()
        self.unify_mode_combo.setMinimumWidth(260)
        for label, data in (
            ("自定义（手动勾选下方规则）", "custom"),
            ("中文规范（英→中标点 + 弯引号 + 省略破折号）", "cn"),
            ("英文规范（中→英标点 + 半角括号）", "en"),
            ("全角转半角（字母 + 数字 + 空格）", "halfwidth"),
        ):
            self.unify_mode_combo.addItem(label, data)
        self.unify_mode_combo.currentIndexChanged.connect(self._on_unify_mode_changed)
        mode_row.addWidget(self.unify_mode_combo)
        self.unify_mode_hint = QLabel("")
        self.unify_mode_hint.setStyleSheet(
            f"font-size: 12px; color: {self._theme.get('accent', '#b08a4e')};"
        )
        self.unify_mode_hint.setWordWrap(True)
        mode_row.addWidget(self.unify_mode_hint, 1)
        form.addLayout(mode_row)
        form.addWidget(self._divider())

        # ---- S3：参数容器 ----

        self.unify_controls = QWidget()
        uc = QVBoxLayout(self.unify_controls)
        uc.setContentsMargins(0, 0, 0, 0)
        uc.setSpacing(2)

        uc.addWidget(self._section_header("标点"))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("方向"))
        self.unify_punct = self._combo(
            [
                ("保持原样", "keep"),
                ("英文标点 → 中文标点", "to_cn"),
                ("中文标点 → 英文标点", "to_en"),
            ],
            "keep",
        )
        row.addWidget(self.unify_punct)
        row.addSpacing(12)
        self.unify_ellipsis = self._check("省略号统一为 ……", False)
        row.addWidget(self.unify_ellipsis)
        row.addSpacing(8)
        self.unify_dash = self._check("破折号统一为 ——", False)
        row.addWidget(self.unify_dash)
        row.addStretch()
        uc.addLayout(row)
        uc.addWidget(self._divider())

        uc.addWidget(self._section_header("引号"))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("目标风格"))
        self.unify_quote = self._combo(
            [
                ("保持原样", "keep"),
                ("中文 “” ‘’", "cn"),
                ("直角引号「」『』", "corner"),
            ],
            "keep",
        )
        row.addWidget(self.unify_quote)
        row.addStretch()
        uc.addLayout(row)
        uc.addWidget(self._divider())

        uc.addWidget(self._section_header("数字"))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("方向"))
        self.unify_number = self._combo(
            [
                ("保持原样", "keep"),
                ("阿拉伯数字 → 中文", "to_cn"),
                ("中文数字 → 阿拉伯", "to_en"),
            ],
            "keep",
        )
        row.addWidget(self.unify_number)
        row.addSpacing(12)
        row.addWidget(QLabel("范围"))
        self.unify_number_scope = self._combo(
            [("全部数字", "all"), ("仅年份（4 位）", "year")], "all"
        )
        row.addWidget(self.unify_number_scope)
        row.addStretch()
        uc.addLayout(row)
        uc.addWidget(self._divider())

        uc.addWidget(self._section_header("全角半角"))
        self.unify_fw_letters = self._check(
            "英文字母全角 → 半角（ＡＢＣ → ABC）", False
        )
        uc.addWidget(self.unify_fw_letters)
        self.unify_fw_digits = self._check("数字全角 → 半角（１２３ → 123）", False)
        uc.addWidget(self.unify_fw_digits)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("括号统一"))
        self.unify_bracket = self._combo(
            [("保持原样", "keep"), ("全用中文 （）", "cn"), ("全用英文 ()", "en")],
            "keep",
        )
        row.addWidget(self.unify_bracket)
        row.addSpacing(12)
        self.unify_space = self._check("全角空格 → 半角（段首缩进除外）", False)
        row.addWidget(self.unify_space)
        row.addStretch()
        uc.addLayout(row)

        uc.addStretch()

        form.addWidget(self.unify_controls)
        form.addStretch()
        return self._scroll_wrap(content)

    def _on_unify_mode_changed(self, *_):
        key = self.unify_mode_combo.currentData()
        if key == "custom":
            self.unify_controls.setVisible(True)
            self.unify_mode_hint.setText("")
            self._on_params_changed()
            return
        self.unify_controls.setVisible(False)

        presets = {
            "cn": dict(
                punct="to_cn",
                ellipsis=True,
                dash=True,
                quote="cn",
                number="keep",
                number_scope="all",
                fw_letters=False,
                fw_digits=False,
                bracket="keep",
                space=False,
            ),
            "en": dict(
                punct="to_en",
                ellipsis=False,
                dash=False,
                quote="keep",
                number="keep",
                number_scope="all",
                fw_letters=False,
                fw_digits=False,
                bracket="en",
                space=False,
            ),
            "halfwidth": dict(
                punct="keep",
                ellipsis=False,
                dash=False,
                quote="keep",
                number="keep",
                number_scope="all",
                fw_letters=True,
                fw_digits=True,
                bracket="keep",
                space=True,
            ),
        }
        hint_map = {
            "cn": "已应用：英文标点转中文 + 弯引号 + 省略号/破折号规范",
            "en": "已应用：中文标点转英文 + 半角括号",
            "halfwidth": "已应用：全角字母/数字转半角 + 全角空格转半角",
        }
        p = presets.get(key, {})
        if p:
            self._set_combo_data(self.unify_punct, p["punct"])
            self.unify_ellipsis.blockSignals(True)
            self.unify_ellipsis.setChecked(p["ellipsis"])
            self.unify_ellipsis.blockSignals(False)
            self.unify_dash.blockSignals(True)
            self.unify_dash.setChecked(p["dash"])
            self.unify_dash.blockSignals(False)
            self._set_combo_data(self.unify_quote, p["quote"])
            self._set_combo_data(self.unify_number, p["number"])
            self._set_combo_data(self.unify_number_scope, p["number_scope"])
            self.unify_fw_letters.blockSignals(True)
            self.unify_fw_letters.setChecked(p["fw_letters"])
            self.unify_fw_letters.blockSignals(False)
            self.unify_fw_digits.blockSignals(True)
            self.unify_fw_digits.setChecked(p["fw_digits"])
            self.unify_fw_digits.blockSignals(False)
            self._set_combo_data(self.unify_bracket, p["bracket"])
            self.unify_space.blockSignals(True)
            self.unify_space.setChecked(p["space"])
            self.unify_space.blockSignals(False)
        self.unify_mode_hint.setText(hint_map.get(key, ""))
        self._on_params_changed()

    # =================== 术语替换 ===================

    def _build_term_tab(self) -> QWidget:
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(16, 4, 16, 12)
        form.setSpacing(2)

        form.addWidget(self._section_header("查找 / 替换"))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("查找"))
        self.term_find = self._line_edit("如：王小明")
        row.addWidget(self.term_find, 1)
        row.addSpacing(8)
        row.addWidget(QLabel("替换为"))
        self.term_replace = self._line_edit("如：王晓明")
        row.addWidget(self.term_replace, 1)
        form.addLayout(row)
        self.term_regex = self._check("使用正则表达式", False)
        form.addWidget(self.term_regex)
        form.addWidget(self._divider())

        form.addWidget(self._section_header("智能匹配"))
        self.term_ignore_spaces = self._check("忽略中间空格（小 明 匹配 小明）", False)
        form.addWidget(self.term_ignore_spaces)
        self.term_ignore_width = self._check(
            "忽略全角半角差异（Lin 匹配 Ｌｉｎ）", False
        )
        form.addWidget(self.term_ignore_width)
        self.term_case_sensitive = self._check("区分大小写（英文术语）", True)
        form.addWidget(self.term_case_sensitive)
        self.term_whole_word = self._check("全词匹配（Lin 不匹配 Linear）", False)
        form.addWidget(self.term_whole_word)
        form.addWidget(self._divider())

        form.addWidget(self._section_header("对照表"))
        tip = QLabel(
            "术语替换只处理正文，不动书名、章节标题、大纲与笔记。"
            "预览时按章节分组显示所有匹配，每条可单独勾选。"
            "预览后可以导出对照表。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"font-size: 12px; color: {self._theme.get('muted', '#9a8e76')};"
            f" padding: 2px 0;"
        )
        form.addWidget(tip)

        export_row = QHBoxLayout()
        export_btn = QPushButton(
            qta.icon("fa5s.file-export",
                     color=self._theme.get("muted", "#9a8e76")),
            " 导出对照表"
        )
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.clicked.connect(self._export_term_result)
        export_row.addWidget(export_btn)
        export_row.addStretch()
        form.addLayout(export_row)

        form.addStretch()
        return self._scroll_wrap(content)

    def _export_term_result(self):
        if self.term_view.topLevelItemCount() == 0:
            QMessageBox.information(
                self, "提示", "请先在「术语替换」页点「预览」生成对照表。"
            )
            return
        path, selected = QFileDialog.getSaveFileName(
            self,
            "导出对照表",
            "术语替换对照表.csv",
            "CSV 文件 (*.csv);;文本文件 (*.txt)",
        )
        if not path:
            return
        is_csv = path.lower().endswith(".csv") or "CSV" in selected

        rows = []
        total = 0
        checked = 0
        for i in range(self.term_view.topLevelItemCount()):
            group = self.term_view.topLevelItem(i)
            g_title = group.text(0)
            for j in range(group.childCount()):
                child = group.child(j)
                state = "保留" if child.checkState(0) == Qt.Checked else "跳过"
                if child.checkState(0) == Qt.Checked:
                    checked += 1
                total += 1
                rows.append((g_title, state, child.text(0), child.text(1)))
        if not rows:
            QMessageBox.information(self, "提示", "当前没有可导出的条目。")
            return

        try:
            if is_csv:
                import csv

                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["章节", "状态", "原文（上下文）", "替换后（上下文）"])
                    w.writerows(rows)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("章节\t状态\t原文\t替换后\n")
                    for r in rows:
                        f.write("\t".join(r) + "\n")
            QMessageBox.information(
                self,
                "导出完成",
                f"已导出 {len(rows)} 条，其中 {checked} 条标记为「保留」。\n{path}",
            )
        except OSError as e:
            logger.error(f"导出对照表失败: {e}", exc_info=True)
            QMessageBox.critical(self, "导出失败", str(e))

    # =================== 状态 ===================

    def _refresh_scope_state(self):
        has_work = getattr(self.ws, "work", None) is not None
        has_current = bool(getattr(self.ws, "current_chapter_id", None))
        editor = getattr(self.ws, "editor", None)
        has_selection = bool(editor and editor.has_selection())

        self.rb_selection.setEnabled(has_selection)
        self.rb_current.setEnabled(has_work and has_current)
        self.rb_book.setEnabled(has_work and has_current)

        if not (has_selection or (has_work and has_current)):
            self.scope_tip.setText("请先打开作品或选中文本")
            return
        if self.rb_selection.isChecked() and not has_selection:
            self.rb_current.setChecked(True)
        if self.rb_current.isChecked() and not (has_work and has_current):
            if has_selection:
                self.rb_selection.setChecked(True)
        self.scope_tip.setText("")

    def _current_scope(self) -> str:
        if self.rb_selection.isChecked():
            return _SCOPE_SELECTION
        if self.rb_book.isChecked():
            return _SCOPE_BOOK
        return _SCOPE_CURRENT

    # =================== 参数收集 ===================

    _TOOL_KEYS = ("paragraph", "clean", "unify", "term")

    def _current_tool_key(self) -> str:
        idx = self.tabs.currentIndex()
        if 0 <= idx < len(self._TOOL_KEYS):
            return self._TOOL_KEYS[idx]
        return "paragraph"

    def _collect_split_chars(self) -> str:
        chars = ""
        if self.split_at_period.isChecked():
            chars += "。"
        if self.split_at_question.isChecked():
            chars += "？"
        if self.split_at_exclaim.isChecked():
            chars += "！"
        if self.split_at_ellipsis.isChecked():
            chars += "…"
        return chars or "。！？…"

    def _collect_params(self):
        key = self._current_tool_key()

        if key == "clean":
            mode = self.clean_mode_combo.currentData()
            if mode == "light":
                return CleanParams(
                    strip_trailing=True,
                    strip_leading_halfwidth=True,
                    strip_leading_fullwidth=False,
                    collapse_blanks="keep_all",
                    remove_invisible=False,
                    collapse_punctuation=False,
                    strip_line_numbers=False,
                    strip_paste_artifacts=True,
                )
            if mode == "standard":
                return CleanParams(
                    strip_trailing=True,
                    strip_leading_halfwidth=True,
                    strip_leading_fullwidth=False,
                    collapse_blanks="keep_one",
                    remove_invisible=True,
                    collapse_punctuation=False,
                    strip_line_numbers=False,
                    strip_paste_artifacts=True,
                )
            if mode == "deep":
                return CleanParams(
                    strip_trailing=True,
                    strip_leading_halfwidth=True,
                    strip_leading_fullwidth=False,
                    collapse_blanks="keep_one",
                    remove_invisible=True,
                    collapse_punctuation=True,
                    strip_line_numbers=True,
                    strip_paste_artifacts=True,
                )
            return CleanParams(
                strip_trailing=self.clean_trailing.isChecked(),
                strip_leading_halfwidth=self.clean_leading_half.isChecked(),
                strip_leading_fullwidth=self.clean_leading_full.isChecked(),
                collapse_blanks=self.clean_blanks.currentData() or "keep_one",
                remove_invisible=self.clean_invisible.isChecked(),
                collapse_punctuation=self.clean_punct.isChecked(),
                strip_line_numbers=self.clean_linenum.isChecked(),
                strip_paste_artifacts=self.clean_artifacts.isChecked(),
            )

        if key == "unify":
            mode = self.unify_mode_combo.currentData()
            if mode == "cn":
                return UnifyParams(
                    punct_direction="to_cn",
                    ellipsis_unify=True,
                    dash_unify=True,
                    quote_style="cn",
                    number_direction="keep",
                    number_scope="all",
                    fullwidth_letters=False,
                    fullwidth_digits=False,
                    bracket_style="keep",
                    space_unify=False,
                )
            if mode == "en":
                return UnifyParams(
                    punct_direction="to_en",
                    ellipsis_unify=False,
                    dash_unify=False,
                    quote_style="keep",
                    number_direction="keep",
                    number_scope="all",
                    fullwidth_letters=False,
                    fullwidth_digits=False,
                    bracket_style="en",
                    space_unify=False,
                )
            if mode == "halfwidth":
                return UnifyParams(
                    punct_direction="keep",
                    ellipsis_unify=False,
                    dash_unify=False,
                    quote_style="keep",
                    number_direction="keep",
                    number_scope="all",
                    fullwidth_letters=True,
                    fullwidth_digits=True,
                    bracket_style="keep",
                    space_unify=True,
                )
            return UnifyParams(
                punct_direction=self.unify_punct.currentData() or "keep",
                ellipsis_unify=self.unify_ellipsis.isChecked(),
                dash_unify=self.unify_dash.isChecked(),
                quote_style=self.unify_quote.currentData() or "keep",
                number_direction=self.unify_number.currentData() or "keep",
                number_scope=self.unify_number_scope.currentData() or "all",
                fullwidth_letters=self.unify_fw_letters.isChecked(),
                fullwidth_digits=self.unify_fw_digits.isChecked(),
                bracket_style=self.unify_bracket.currentData() or "keep",
                space_unify=self.unify_space.isChecked(),
            )

        if key == "term":
            return TermParams(
                find=self.term_find.text(),
                replace=self.term_replace.text(),
                use_regex=self.term_regex.isChecked(),
                ignore_spaces=self.term_ignore_spaces.isChecked(),
                ignore_width=self.term_ignore_width.isChecked(),
                case_sensitive=self.term_case_sensitive.isChecked(),
                whole_word=self.term_whole_word.isChecked(),
            )

        # paragraph
        mode = self.para_mode_combo.currentData()
        if mode == "merge":
            return ParagraphParams(
                split_enabled=False,
                merge_enabled=True,
                merge_threshold=20,
                merge_max=200,
                merge_skip_dialogue=True,
                merge_skip_titles=True,
                blank_mode="keep",
                indent_mode="keep",
            )
        if mode == "split":
            return ParagraphParams(
                split_enabled=True,
                split_threshold=120,
                split_min_keep=40,
                split_chars="。！？…",
                merge_enabled=False,
                merge_threshold=20,
                merge_max=200,
                merge_skip_dialogue=True,
                merge_skip_titles=True,
                blank_mode="keep",
                indent_mode="keep",
            )
        if mode == "optimize":
            return ParagraphParams(
                split_enabled=True,
                split_threshold=120,
                split_min_keep=40,
                split_chars="。！？…",
                merge_enabled=True,
                merge_threshold=20,
                merge_max=200,
                merge_skip_dialogue=True,
                merge_skip_titles=True,
                blank_mode="one",
                indent_mode="add",
                indent_dialogue=False,
            )
        return ParagraphParams(
            split_enabled=self.split_enabled.isChecked(),
            split_threshold=self.split_threshold.value(),
            split_min_keep=self.split_min_keep.value(),
            split_chars=self._collect_split_chars(),
            merge_enabled=self.merge_enabled.isChecked(),
            merge_threshold=self.merge_threshold.value(),
            merge_max=self.merge_max.value(),
            merge_skip_dialogue=self.merge_skip_dialogue.isChecked(),
            merge_skip_titles=True,
            blank_mode=self.blank_combo.currentData() or "keep",
            indent_mode=self.indent_combo.currentData() or "keep",
            indent_dialogue=self.indent_dialogue.isChecked(),
        )

    def _transform(self, text: str, params):
        key = self._current_tool_key()
        if key == "clean":
            return clean_text(text, params)
        if key == "unify":
            return unify_text(text, params)
        return process_paragraphs(text, params)

    def _format_tool_stats(self, stats: dict) -> str:
        key = self._current_tool_key()
        if key == "clean":
            return format_clean_stats(stats)
        if key == "unify":
            return format_unify_stats(stats)
        return format_stats(stats)

    def _on_params_changed(self, *_):
        # 构建期间参数变化不算“用户改动”，直接忽略副作用
        if getattr(self, "_building", False):
            return
        self._invalidate_batch()
        self._preview.clear()
        self._preview_stats.clear()
        self._term_preview.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("参数已修改，请重新点「预览」")
        self.diff_view.setPlainText("")
        self.term_view.clear()

    def _on_tab_changed(self, _index: int):
        self._invalidate_batch()
        self._preview.clear()
        self._preview_stats.clear()
        self._term_preview.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("点击下方「预览」按钮查看效果")
        self.diff_view.setPlainText("")
        self.term_view.clear()
        is_term = self._current_tool_key() == "term"
        self.term_view.setVisible(is_term)
        self.diff_view.setVisible(not is_term)

    # =================== 预览 ===================

    _batch_chunk = 20

    def _do_preview(self):
        self._refresh_scope_state()
        if self._current_tool_key() == "term":
            self._do_term_preview()
            return

        scope = self._current_scope()
        params = self._collect_params()
        self._preview.clear()
        self._preview_stats.clear()

        # 选区 / 单章：单项处理，同步即可
        if scope == _SCOPE_SELECTION:
            self._preview_selection(params)
            self._render_diff()
            return
        if scope == _SCOPE_CURRENT:
            self._preview_current(params)
            self._render_diff()
            return

        # 整部作品：分块，保证界面可响应、可取消
        if not self.ws.work:
            return
        chapters = list(self.ws.work.chapters)

        def step(ch):
            cid = ch["id"]
            old = self.ws.work.read_chapter(cid)
            new, stats = self._transform(old, params)
            if new != old:
                self._preview[cid] = new
                self._preview_stats[cid] = stats

        def done(cancelled, error):
            if error is not None:
                QMessageBox.critical(self, "预览失败", str(error))
                return
            if cancelled:
                self._preview.clear()
                self._preview_stats.clear()
                self.diff_view.setPlainText("")
                self.apply_btn.setEnabled(False)
                self.stats_label.setText("已取消预览")
                return
            self._render_diff()

        self._start_batch(chapters, step, "预览", done)

    def _preview_selection(self, params):
        editor = getattr(self.ws, "editor", None)
        if editor is None or not editor.has_selection():
            self.stats_label.setText("编辑器没有选中文本")
            return
        old = editor.selected_text().replace("\u2029", "\n")
        new, stats = self._transform(old, params)
        self._preview["__selection__"] = new
        self._preview_stats["__selection__"] = stats

    def _preview_current(self, params):
        cid = getattr(self.ws, "current_chapter_id", None)
        if not cid or not self.ws.work:
            return
        editor = getattr(self.ws, "editor", None)
        old = (
            editor.get_text() if editor is not None else self.ws.work.read_chapter(cid)
        )
        new, stats = self._transform(old, params)
        self._preview[cid] = new
        self._preview_stats[cid] = stats

    def _preview_book(self, params):
        if not self.ws.work:
            return
        for c in self.ws.work.chapters:
            cid = c["id"]
            old = self.ws.work.read_chapter(cid)
            new, stats = self._transform(old, params)
            if new != old:
                self._preview[cid] = new
                self._preview_stats[cid] = stats

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

    def _render_diff(self):
        if not self._preview:
            self.stats_label.setText("无需改动")
            self.diff_view.setPlainText("")
            self.apply_btn.setEnabled(False)
            return

        agg = {}
        for st in self._preview_stats.values():
            for k, v in st.items():
                agg[k] = agg.get(k, 0) + v
        summary = self._format_tool_stats(agg)

        parts = []
        if "__selection__" in self._preview:
            editor = getattr(self.ws, "editor", None)
            old = editor.selected_text().replace("\u2029", "\n") if editor else ""
            new = self._preview["__selection__"]
            parts.append("▎选中的文本")
            parts.append(self._clean_diff(old, new))
            self.stats_label.setText(f"选中模式 · {summary}")
        else:
            total = len(self._preview)
            for cid, new in self._preview.items():
                old = self.ws.work.read_chapter(cid)
                ch = self.ws.work.get_chapter(cid)
                title = ch.get("title", cid) if ch else cid
                st = self._preview_stats.get(cid, {})
                line = f"▎{title}"
                stat_line = self._format_tool_stats(st)
                if stat_line and stat_line not in ("无改动", "无需改动"):
                    line += f"    {stat_line}"
                parts.append(line)
                parts.append(self._clean_diff(old, new))
                parts.append("")
            self.stats_label.setText(f"将修改 {total} 章 · {summary}")

        self.diff_view.setPlainText("\n".join(parts))
        self._recolor_diff()
        self.apply_btn.setEnabled(True)

    def _recolor_diff(self):
        t = self._theme or {}
        try:
            is_light = QColor(t.get("bg", "#f7f2e8")).lightness() > 150
        except Exception:
            is_light = True

        accent = QColor(t.get("accent", "#b08a4e"))
        if is_light:
            c_del = QColor("#a93326")
            c_add = QColor("#137333")
            c_head = accent
            c_muted = QColor("#9a8e76")
            bg_del = QColor(192, 57, 43, 22)
            bg_add = QColor(26, 127, 55, 22)
        else:
            c_del = QColor("#ff8a80")
            c_add = QColor("#7ee787")
            c_head = accent
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
                fmt.setForeground(c_head)
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

    # =================== 术语替换预览 ===================

    def _render_term_tree(self, entries, scope):
        """把术语匹配结果渲染进 term_view。

        entries: {cid: [(start, end, replacement), ...]}
                 cid == "__selection__" 表示选区。
        scope:   当前范围（用于 _term_scope_text 取上下文文本）

        抽出来的原因：预览从「一次性算完」改成「分块算」后，
        渲染这一步既可能同步调用（选区/单章），也可能在分块收尾时
        调用（全书），放独立方法两边共用。
        """
        self._term_updating = True
        try:
            self.term_view.clear()
            for cid, matches in entries.items():
                ch = self.ws.work.get_chapter(cid) if self.ws.work else None
                title = ch.get("title", cid) if ch else "选中的文本"
                old_text = self._term_scope_text(scope, cid)

                group_item = QTreeWidgetItem([f"{title}    {len(matches)} 处", ""])
                group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
                group_item.setCheckState(0, Qt.Checked)
                group_item.setData(0, Qt.UserRole, ("__group__", cid))
                font = group_item.font(0)
                font.setBold(True)
                group_item.setFont(0, font)
                self.term_view.addTopLevelItem(group_item)

                for start, end, repl in matches:
                    pre = old_text[max(0, start - 15) : start]
                    mid = old_text[start:end]
                    post = old_text[end : end + 15]
                    display = f"…{pre}『{mid}』{post}…"
                    replaced_display = f"…{pre}『{repl}』{post}…"
                    item = QTreeWidgetItem([display, replaced_display])
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked)
                    item.setData(0, Qt.UserRole, ("__match__", cid, start, end, repl))
                    group_item.addChild(item)
                group_item.setExpanded(True)
        finally:
            self._term_updating = False

        total = sum(len(v) for v in entries.values())
        if total == 0:
            self.stats_label.setText("没有找到匹配")
            self.apply_btn.setEnabled(False)
        else:
            self.stats_label.setText(format_term_stats(total, len(entries)))
            self.apply_btn.setEnabled(True)

    def _do_term_preview(self):
        params = self._collect_params()

        if not params.find:
            self.term_view.clear()
            self.stats_label.setText("请输入查找内容")
            self.apply_btn.setEnabled(False)
            return

        scope = self._current_scope()

        # 先禁掉「应用」：新预览还没出来，不能拿旧结果去改稿
        self.apply_btn.setEnabled(False)

        # ---- 选区：单项，同步处理即可 ----
        if scope == _SCOPE_SELECTION:
            editor = getattr(self.ws, "editor", None)
            if editor is None or not editor.has_selection():
                self.term_view.clear()
                self.stats_label.setText("编辑器没有选中文本")
                self.apply_btn.setEnabled(False)
                return
            text = editor.selected_text().replace("\u2029", "\n")
            # 缓存选区的文本与位置：渲染和应用都用这一份，避免
            # 预览后编辑器选区折叠/变化导致渲染取到空文本、应用被跳过。
            cur = editor.edit.textCursor()
            self._selection_preview_text = text
            self._selection_preview_range = (cur.selectionStart(), cur.selectionEnd())
            matches = find_replacements(text, params)
            if not matches:
                self.term_view.clear()
                self.stats_label.setText("没有找到匹配")
                self.apply_btn.setEnabled(False)
                return
            self._render_term_tree({"__selection__": matches}, scope)
            return

        # ---- 当前章 / 整部作品：分块扫描 ----
        if not self.ws.work:
            return
        if scope == _SCOPE_CURRENT:
            cids = [self.ws.current_chapter_id]
        else:
            cids = [c["id"] for c in self.ws.work.chapters]
        cids = [c for c in cids if c]

        entries = {}

        def step(cid):
            text = self.ws.work.read_chapter(cid)
            matches = find_replacements(text, params)
            if matches:
                entries[cid] = matches

        def done(cancelled, error):
            if error is not None:
                QMessageBox.critical(self, "预览失败", str(error))
                return
            if cancelled:
                self.term_view.clear()
                self.stats_label.setText("已取消预览")
                self.apply_btn.setEnabled(False)
                return
            self._render_term_tree(entries, scope)

        self._start_batch(cids, step, "预览", done)

    def _on_term_item_changed(self, item, _col):
        if self._term_updating:
            return
        self._term_updating = True
        try:
            data = item.data(0, Qt.UserRole)
            if not data:
                return
            kind = data[0]
            if kind == "__group__":
                state = item.checkState(0)
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
            elif kind == "__match__":
                parent = item.parent()
                if parent is None:
                    return
                n = parent.childCount()
                checked = sum(
                    1 for i in range(n) if parent.child(i).checkState(0) == Qt.Checked
                )
                if checked == n:
                    parent.setCheckState(0, Qt.Checked)
                elif checked == 0:
                    parent.setCheckState(0, Qt.Unchecked)
                else:
                    parent.setCheckState(0, Qt.PartiallyChecked)
        finally:
            self._term_updating = False

    def _term_scope_text(self, scope: str, cid: str) -> str:
        if scope == _SCOPE_SELECTION or cid == "__selection__":
            # 预览时缓存的文本优先。缓存存在时不再实时读编辑器——
            # 对话框抢焦点后选区可能折叠，实时读会拿到空串。
            cached = getattr(self, "_selection_preview_text", None)
            if cached is not None:
                return cached
            editor = getattr(self.ws, "editor", None)
            return editor.selected_text().replace("\u2029", "\n") if editor else ""
        return self.ws.work.read_chapter(cid) if self.ws.work else ""

    # =================== 应用 ===================

    def _do_apply(self):
        if self._current_tool_key() == "term":
            self._do_term_apply()
            return

        if not self._preview:
            QMessageBox.information(self, "提示", "请先点「预览」查看效果。")
            return
        scope = self._current_scope()
        if scope == _SCOPE_SELECTION:
            self._apply_selection()
        elif scope == _SCOPE_CURRENT:
            self._apply_current()
        else:
            self._apply_book()

    def _apply_selection(self):
        editor = getattr(self.ws, "editor", None)
        if editor is None or not editor.has_selection():
            return
        new = self._preview.get("__selection__", "")
        editor.replace_selected_text(new)
        self._preview.clear()
        self._preview_stats.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("已应用到选中的文本")
        # 只改了编辑器内存（选区不是整章，没法 write_chapter），
        # 不能带 current_chapter_id——workspace 收到后会从磁盘重读章节
        # 覆盖编辑器，把刚改的选区内容冲掉。
        # 编辑器自身的 textChanged → save_debounce 会在几秒后落盘，
        # 字数统计也由 textChanged 触发刷新。
        self.applied.emit(set())

    def _apply_current(self):
        cid = getattr(self.ws, "current_chapter_id", None)
        if not cid or cid not in self._preview or not self.ws.work:
            return
        new_text = self._preview[cid]
        try:
            self.ws.work.create_snapshot(cid)
            self.ws.work.write_chapter(cid, new_text)
        except OSError as e:
            logger.error(f"应用失败: {e}", exc_info=True)
            QMessageBox.critical(self, "应用失败", str(e))
            return
        self._preview.clear()
        self._preview_stats.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("已应用到当前章节")
        self.applied.emit({cid})

    _apply_chunk = 5

    def _apply_book(self):
        if not self.ws.work or not self._preview:
            return
        items = list(self._preview.items())
        affected = set()

        def step(item):
            cid, new_text = item
            self.ws.work.create_snapshot(cid)
            self.ws.work.write_chapter(cid, new_text)
            affected.add(cid)

        def done(cancelled, error):
            self.apply_btn.setEnabled(False)
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
                    "已处理的章节原稿均已打快照，可在历史快照浏览器里恢复；"
                    "剩余章节未改动。",
                )
            else:
                QMessageBox.information(
                    self,
                    "完成",
                    f"已修改 {len(affected)} 个章节，" "原稿均已自动备份为历史快照。",
                )
            self._preview.clear()
            self._preview_stats.clear()
            self.stats_label.setText(f"已应用到 {len(affected)} 个章节")

        self.apply_btn.setEnabled(False)
        self._start_batch(items, step, "应用", done, chunk=self._apply_chunk)

    def _do_term_apply(self):
        grouped = {}
        total_checked = 0
        for i in range(self.term_view.topLevelItemCount()):
            group = self.term_view.topLevelItem(i)
            data = group.data(0, Qt.UserRole)
            if not data or data[0] != "__group__":
                continue
            cid = data[1]
            for j in range(group.childCount()):
                child = group.child(j)
                if child.checkState(0) != Qt.Checked:
                    continue
                cdata = child.data(0, Qt.UserRole)
                if not cdata or cdata[0] != "__match__":
                    continue
                _, _, start, end, repl = cdata
                grouped.setdefault(cid, []).append((start, end, repl))
                total_checked += 1

        if total_checked == 0:
            QMessageBox.information(self, "提示", "没有勾选任何条目。")
            return

        if "__selection__" in grouped:
            editor = getattr(self.ws, "editor", None)
            if editor is None:
                return
            rng = getattr(self, "_selection_preview_range", None)
            cached = getattr(self, "_selection_preview_text", "")
            if not rng or not cached:
                QMessageBox.information(
                    self, "提示", "选中的文本信息已失效，请重新在编辑器里选中并预览。"
                )
                return

            # 二次校验：预览之后用户可能改过正文。此时按旧位置替换
            # 会吃到别的内容——和 workspace._apply_rewrite 同样的防护。
            from PySide6.QtGui import QTextCursor

            full = editor.get_text()
            start, end = rng
            start = max(0, min(start, len(full)))
            end = max(start, min(end, len(full)))
            if full[start:end] != cached:
                ret = QMessageBox.question(
                    self,
                    "选区已变化",
                    "预览之后正文被改动过，原选区位置已经失效。\n\n"
                    "继续替换会把结果写进当前该位置的内容，"
                    "可能覆盖你刚写的文字。\n\n"
                    "是否仍然继续？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return

            old = cached
            new = apply_replacements(old, grouped["__selection__"])

            # 按记录的位置直接改文档，不依赖编辑器当前是否仍有选中。
            c = editor.edit.textCursor()
            c.beginEditBlock()
            c.setPosition(start)
            c.setPosition(end, QTextCursor.KeepAnchor)
            c.insertText(new)
            c.endEditBlock()
            editor.edit.setTextCursor(c)

            # 用完即清，防止下次误用旧选区
            self._selection_preview_text = None
            self._selection_preview_range = None

            del grouped["__selection__"]
            self.stats_label.setText("已替换选中文本")
            self.apply_btn.setEnabled(False)
            # 同 _apply_selection：不能带 current_chapter_id，
            # 否则 workspace 会从磁盘重读章节，覆盖掉内存里的选区改动。
            self.applied.emit(set())
            if not grouped:
                return

        if not self.ws.work:
            return
        items = list(grouped.items())
        affected = set()

        def step(item):
            cid, matches = item
            old = self.ws.work.read_chapter(cid)
            new = apply_replacements(old, matches)
            if new == old:
                return
            self.ws.work.create_snapshot(cid)
            self.ws.work.write_chapter(cid, new)
            affected.add(cid)

        def done(cancelled, error):
            self.apply_btn.setEnabled(False)
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
                    f"已替换 {len(affected)} 章后取消。\n\n"
                    "已处理的章节原稿均已打快照，可在历史快照浏览器里恢复；"
                    "剩余章节未改动。",
                )
            else:
                QMessageBox.information(
                    self,
                    "完成",
                    f"已替换 {total_checked} 处，涉及 {len(affected)} 章。\n"
                    "原稿均已自动备份为历史快照。",
                )
            self.stats_label.setText(f"已应用 {total_checked} 处")

        self.apply_btn.setEnabled(False)
        self._start_batch(items, step, "替换", done, chunk=self._apply_chunk)

    # =================== 参数持久化 ===================

    def _set_combo_data(self, combo: QComboBox, data):
        idx = combo.findData(data)
        if idx >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _save_toolbox_params(self):
        cfg = getattr(self.ws, "cfg", None)
        if cfg is None:
            return
        try:
            state = {
                "last_tab": self.tabs.currentIndex(),
                "paragraph_mode": self.para_mode_combo.currentData(),
                "clean_mode": self.clean_mode_combo.currentData(),
                "unify_mode": self.unify_mode_combo.currentData(),
                "paragraph": {
                    "split_enabled": self.split_enabled.isChecked(),
                    "split_threshold": self.split_threshold.value(),
                    "split_min_keep": self.split_min_keep.value(),
                    "split_period": self.split_at_period.isChecked(),
                    "split_question": self.split_at_question.isChecked(),
                    "split_exclaim": self.split_at_exclaim.isChecked(),
                    "split_ellipsis": self.split_at_ellipsis.isChecked(),
                    "merge_enabled": self.merge_enabled.isChecked(),
                    "merge_threshold": self.merge_threshold.value(),
                    "merge_max": self.merge_max.value(),
                    "merge_skip_dialogue": self.merge_skip_dialogue.isChecked(),
                    "blank_mode": self.blank_combo.currentData(),
                    "indent_mode": self.indent_combo.currentData(),
                    "indent_dialogue": self.indent_dialogue.isChecked(),
                },
                "clean": {
                    "trailing": self.clean_trailing.isChecked(),
                    "leading_half": self.clean_leading_half.isChecked(),
                    "leading_full": self.clean_leading_full.isChecked(),
                    "blanks": self.clean_blanks.currentData(),
                    "invisible": self.clean_invisible.isChecked(),
                    "punct": self.clean_punct.isChecked(),
                    "linenum": self.clean_linenum.isChecked(),
                    "artifacts": self.clean_artifacts.isChecked(),
                },
                "unify": {
                    "punct": self.unify_punct.currentData(),
                    "ellipsis": self.unify_ellipsis.isChecked(),
                    "dash": self.unify_dash.isChecked(),
                    "quote": self.unify_quote.currentData(),
                    "number": self.unify_number.currentData(),
                    "number_scope": self.unify_number_scope.currentData(),
                    "fw_letters": self.unify_fw_letters.isChecked(),
                    "fw_digits": self.unify_fw_digits.isChecked(),
                    "bracket": self.unify_bracket.currentData(),
                    "space": self.unify_space.isChecked(),
                },
                "term": {
                    "regex": self.term_regex.isChecked(),
                    "ignore_spaces": self.term_ignore_spaces.isChecked(),
                    "ignore_width": self.term_ignore_width.isChecked(),
                    "case_sensitive": self.term_case_sensitive.isChecked(),
                    "whole_word": self.term_whole_word.isChecked(),
                },
            }
            cfg.set_config(
                _CFG_SECTION,
                _CFG_KEY,
                json.dumps(state, ensure_ascii=False),
                validate=False,
            )
        except Exception as e:
            logger.warning(f"保存工具箱参数失败: {e}")

    def _load_toolbox_params(self):
        cfg = getattr(self.ws, "cfg", None)
        if cfg is None:
            return
        try:
            raw = cfg.get_config(_CFG_SECTION, _CFG_KEY, "")
            if not raw:
                return
            state = json.loads(raw)
        except Exception as e:
            logger.warning(f"读取工具箱参数失败: {e}")
            return

        try:
            p = state.get("paragraph", {})
            self.split_enabled.setChecked(p.get("split_enabled", True))
            self.split_threshold.setValue(int(p.get("split_threshold", 120)))
            self.split_min_keep.setValue(int(p.get("split_min_keep", 40)))
            self.split_at_period.setChecked(p.get("split_period", True))
            self.split_at_question.setChecked(p.get("split_question", True))
            self.split_at_exclaim.setChecked(p.get("split_exclaim", True))
            self.split_at_ellipsis.setChecked(p.get("split_ellipsis", True))
            self.merge_enabled.setChecked(p.get("merge_enabled", False))
            self.merge_threshold.setValue(int(p.get("merge_threshold", 20)))
            self.merge_max.setValue(int(p.get("merge_max", 200)))
            self.merge_skip_dialogue.setChecked(p.get("merge_skip_dialogue", True))
            self._set_combo_data(self.blank_combo, p.get("blank_mode", "keep"))
            self._set_combo_data(self.indent_combo, p.get("indent_mode", "keep"))
            self.indent_dialogue.setChecked(p.get("indent_dialogue", False))

            c = state.get("clean", {})
            self.clean_trailing.setChecked(c.get("trailing", True))
            self.clean_leading_half.setChecked(c.get("leading_half", True))
            self.clean_leading_full.setChecked(c.get("leading_full", False))
            self._set_combo_data(self.clean_blanks, c.get("blanks", "keep_one"))
            self.clean_invisible.setChecked(c.get("invisible", True))
            self.clean_punct.setChecked(c.get("punct", False))
            self.clean_linenum.setChecked(c.get("linenum", False))
            self.clean_artifacts.setChecked(c.get("artifacts", True))

            u = state.get("unify", {})
            self._set_combo_data(self.unify_punct, u.get("punct", "keep"))
            self.unify_ellipsis.setChecked(u.get("ellipsis", False))
            self.unify_dash.setChecked(u.get("dash", False))
            self._set_combo_data(self.unify_quote, u.get("quote", "keep"))
            self._set_combo_data(self.unify_number, u.get("number", "keep"))
            self._set_combo_data(self.unify_number_scope, u.get("number_scope", "all"))
            self.unify_fw_letters.setChecked(u.get("fw_letters", False))
            self.unify_fw_digits.setChecked(u.get("fw_digits", False))
            self._set_combo_data(self.unify_bracket, u.get("bracket", "keep"))
            self.unify_space.setChecked(u.get("space", False))

            t = state.get("term", {})
            self.term_regex.setChecked(t.get("regex", False))
            self.term_ignore_spaces.setChecked(t.get("ignore_spaces", False))
            self.term_ignore_width.setChecked(t.get("ignore_width", False))
            self.term_case_sensitive.setChecked(t.get("case_sensitive", True))
            self.term_whole_word.setChecked(t.get("whole_word", False))

            # S3：恢复各 tab 的模式（触发 on_xxx_mode_changed 收起参数控件）
            # 放在其它参数恢复之后，非自定义模式会按预设覆盖控件——这是预期。
            self._set_combo_data(
                self.para_mode_combo, state.get("paragraph_mode", "custom")
            )
            self._set_combo_data(
                self.clean_mode_combo, state.get("clean_mode", "custom")
            )
            self._set_combo_data(
                self.unify_mode_combo, state.get("unify_mode", "custom")
            )
            self._on_para_mode_changed()
            self._on_clean_mode_changed()
            self._on_unify_mode_changed()

            last_tab = int(state.get("last_tab", 0))
            if 0 <= last_tab < self.tabs.count():
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(last_tab)
                self.tabs.blockSignals(False)
                is_term = self._current_tool_key() == "term"
                self.term_view.setVisible(is_term)
                self.diff_view.setVisible(not is_term)
        except Exception as e:
            logger.warning(f"恢复工具箱参数失败: {e}")

    # =================== 生命周期 ===================

    def closeEvent(self, event):
        try:
            self._save_toolbox_params()
        except Exception as e:
            logger.warning(f"关闭保存参数失败: {e}")
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_scope_state()
