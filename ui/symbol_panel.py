# ui/symbol_panel.py
"""墨码写作 - 符号面板

三步流程：选中正文 → 点符号 → 选方式。

设计要点：
- 顶部固定引导条告知操作顺序
- 底部固定状态栏显示当前该做什么
- **预览区**（固定）：始终显示当前参数下的处理结果
- **自动应用开关**（固定）：默认勾选＝立即改正文；
  取消勾选则只在预览区显示效果，用户点"应用到正文"才生效
- 分区：常用符号 / 插入方式 / 分段处理 / 折叠区

分段处理：
- 整个选区：对整段文字应用插入方式
- 按分隔符分段：先按分隔符拆成多段，对每段独立应用插入方式，
  段之间用"连接"指定的符号拼接
  例：123，45，6789 + 《 + 分隔符"，" + 两端 → 《123》，《45》，《6789》
"""

import json

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QScrollArea,
    QLabel,
    QPushButton,
    QLineEdit,
    QFrame,
    QMenu,
    QSpinBox,
    QCheckBox,
    QButtonGroup,
    QToolButton,
    QComboBox,
    QRadioButton,
)
from PySide6.QtCore import Qt, Signal, QTimer

from ui.theme_style import qss_alpha
from utils.symbol_logic import build_edge_result

PRIMARY_SYMBOLS = [
    "……",
    "。",
    "，",
    "！",
    "？",
    "——",
    "、",
    "：",
    "；",
    "《",
    "》",
    "“",
    "”",
    "‘",
    "’",
    "「",
    "」",
    "『",
    "』",
    "（",
    "）",
    "【",
    "】",
    "…",
    "·",
    "～",
    "♪",
    "→",
]

EXTRA_GROUPS = [
    (
        "星标装饰",
        [
            "※",
            "★",
            "☆",
            "◇",
            "◆",
            "○",
            "●",
            "△",
            "▲",
            "□",
            "■",
            "♡",
            "♥",
            "✿",
            "✦",
            "▶",
        ],
    ),
    (
        "序号其他",
        ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ"],
    ),
]

PAIRS = [
    ("「」", "「", "」"),
    ("『』", "『", "』"),
    ("“”", "“", "”"),
    ("（）", "（", "）"),
    ("《》", "《", "》"),
    ("【】", "【", "】"),
]

LINE_QUICK = ["◇", "◆", "※", "★", "☆", "▶", "·"]

DIVIDERS = ["※※※※※", "☆★☆★☆", "◇◆◇◆◇", "——————", "· · · · ·", "♡♥♡♥♡"]

SYMBOL_NAMES = {
    "……": "省略号",
    "…": "单省略号",
    "。": "句号",
    "，": "逗号",
    "！": "感叹号",
    "？": "问号",
    "——": "破折号",
    "、": "顿号",
    "：": "冒号",
    "；": "分号",
    "·": "间隔号",
    "～": "波浪号",
    "《": "左书名号",
    "》": "右书名号",
    "“": "左双引号",
    "”": "右双引号",
    "‘": "左单引号",
    "’": "右单引号",
    "「": "左直角引号",
    "」": "右直角引号",
    "『": "左双直角引号",
    "』": "右双直角引号",
    "（": "左括号",
    "）": "右括号",
    "【": "左方头括号",
    "】": "右方头括号",
    "♪": "音符",
    "→": "右箭头",
    "※": "参考标记",
    "★": "实心五角星",
    "☆": "空心五角星",
    "◇": "空心菱形",
    "◆": "实心菱形",
    "○": "空心圆",
    "●": "实心圆",
    "△": "空心三角形",
    "▲": "实心三角形",
    "▶": "实心右三角",
    "□": "空心方块",
    "■": "实心方块",
    "♡": "空心爱心",
    "♥": "实心爱心",
    "✿": "花朵",
    "✦": "四角星",
    "①": "带圈数字 1",
    "②": "带圈数字 2",
    "③": "带圈数字 3",
    "④": "带圈数字 4",
    "⑤": "带圈数字 5",
    "⑥": "带圈数字 6",
    "⑦": "带圈数字 7",
    "⑧": "带圈数字 8",
    "⑨": "带圈数字 9",
    "⑩": "带圈数字 10",
    "Ⅰ": "罗马数字 1",
    "Ⅱ": "罗马数字 2",
    "Ⅲ": "罗马数字 3",
    "Ⅳ": "罗马数字 4",
    "Ⅴ": "罗马数字 5",
}


def symbol_name(sym: str) -> str:
    return SYMBOL_NAMES.get(sym, "")


_BIG_COLS = 5
_SMALL_COLS = 6

EDGE_MODES = [
    ("头部", "head", "在选中文字最前面加一次符号"),
    ("尾部", "tail", "在选中文字最后面加一次符号"),
    ("两端", "both", "选中文字两端各加符号"),
    ("分隔", "split", "按字符数每隔 N 字插入；或按分隔符拆段后逐段处理"),
]


class SymbolPanel(QWidget):
    """符号面板：选中正文 → 点符号 → 选方式。"""

    insert_symbol = Signal(str)
    wrap_selected = Signal(str, str)
    edge_apply = Signal(
        str, str, int, bool, str, str
    )  # kind, sym, every, skip_ws, delimiter, connector
    revert_live = Signal()
    prefix_lines = Signal(str)
    suffix_lines = Signal(str)
    convert_selected = Signal(str)
    insert_divider = Signal(str)

    def __init__(self, config_manager=None, parent=None):
        super().__init__(parent)
        self.setObjectName("symbolPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.cfg = config_manager
        self._theme = None
        self._custom = self._load_custom()
        self._has_selection = False
        self._selection_text = ""  # 当前选区原文（用于预览）
        self._mode = None
        self._active_sym = ""
        self._applying = False
        self._sym_buttons = {}
        self._split_use_delim = False
        self.setMinimumWidth(260)
        self._load_split_options_from_cfg()
        self._build_ui()
        self.apply_colors(None)
        self.set_selection_text("")

    # ==================== 配置读写 ====================
    def _load_split_options_from_cfg(self):
        if self.cfg is None:
            return
        try:
            self._split_use_delim = (
                self.cfg.get_config("Writing", "split_use_delim", "false") == "true"
            )
        except Exception:
            self._split_use_delim = False

    def _save_split_options_to_cfg(self):
        if self.cfg is None:
            return
        try:
            self.cfg.set_config(
                "Writing",
                "split_use_delim",
                "true" if self._split_use_delim else "false",
                validate=False,
            )
        except Exception:
            pass

    def _load_auto_apply_from_cfg(self) -> bool:
        # 默认 False：选择符号/方式后先只更新预览，用户点「应用到正文」才改稿。
        # 与主流写作软件一致——三步操作（选符号 → 选方式 → 生效）没走完
        # 就改稿是反直觉的，用户选错符号只能 Ctrl+Z 撤销，很可能忘了刚才选什么。
        if self.cfg is None:
            return False
        try:
            return (
                self.cfg.get_config("Writing", "symbol_auto_apply", "false") == "true"
            )
        except Exception:
            return False

    def _save_auto_apply_to_cfg(self, enabled: bool):
        if self.cfg is None:
            return
        try:
            self.cfg.set_config(
                "Writing",
                "symbol_auto_apply",
                "true" if enabled else "false",
                validate=False,
            )
        except Exception:
            pass

    # ==================== UI 构建 ====================
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)

        # 标题
        title = QLabel("符号")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        root.addWidget(title)

        # 三步引导条
        self.guide_bar = QLabel("选中正文 → 点符号 → 选方式")
        self.guide_bar.setObjectName("symGuideBar")
        self.guide_bar.setAlignment(Qt.AlignCenter)
        root.addWidget(self.guide_bar)

        # 中部滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.NoFocus)
        self._scroll = scroll

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(2, 4, 6, 4)
        cl.setSpacing(10)

        # 1. 常用符号
        cl.addWidget(self._section_title("常用符号"))
        primary_grid = QGridLayout()
        primary_grid.setSpacing(6)
        for i, s in enumerate(PRIMARY_SYMBOLS):
            b = self._char_btn(s, big=True)
            self._track_btn(s, b)
            b.clicked.connect(lambda _=False, x=s: self._on_insert_symbol(x))
            primary_grid.addWidget(b, i // _BIG_COLS, i % _BIG_COLS)
        cl.addLayout(primary_grid)

        # 2. 插入方式
        cl.addWidget(self._section_title("插入方式"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(False)
        self.mode_buttons = {}
        for label, kind, tip in EDGE_MODES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._mode_btn_qss())
            b.toggled.connect(lambda checked, k=kind: self._on_mode_toggled(k, checked))
            self.mode_group.addButton(b)
            self.mode_buttons[kind] = b
            mode_row.addWidget(b, 1)
        cl.addLayout(mode_row)

        # 2.1 选"分隔"时展开：每隔 N 字符设置
        self.split_char_block = QWidget()
        scb = QHBoxLayout(self.split_char_block)
        scb.setContentsMargins(4, 2, 4, 2)
        scb.setSpacing(4)
        scb.addWidget(self._mini_label("每隔"))
        self.split_every_spin = QSpinBox()
        self.split_every_spin.setRange(1, 20)
        self.split_every_spin.setValue(1)
        self.split_every_spin.setFixedWidth(50)
        self.split_every_spin.valueChanged.connect(self._on_split_options_changed)
        scb.addWidget(self.split_every_spin)
        scb.addWidget(self._mini_label("字符"))
        self.skip_ws_check = QCheckBox("跳过空白")
        self.skip_ws_check.setChecked(True)
        self.skip_ws_check.stateChanged.connect(self._on_split_options_changed)
        scb.addWidget(self.skip_ws_check)
        scb.addStretch()
        cl.addWidget(self.split_char_block)
        self.split_char_block.setVisible(False)

        # 3. 分段处理（可选）
        cl.addWidget(self._section_title("分段处理（可选）"))

        self.scope_all_radio = QRadioButton("整个选区")
        self.scope_all_radio.setToolTip("选中文字作为一段整体处理（默认）")
        self.scope_delim_radio = QRadioButton("按分隔符分段")
        self.scope_delim_radio.setToolTip(
            "按分隔符把选区拆成多段，对每段独立应用插入方式。\n"
            "例：123，45，6789 + 《 + 分隔符「，」 + 两端 → 《123》，《45》，《6789》"
        )
        self.scope_all_radio.setChecked(not self._split_use_delim)
        self.scope_delim_radio.setChecked(self._split_use_delim)
        self.scope_all_radio.toggled.connect(self._on_scope_toggled)
        cl.addWidget(self.scope_all_radio)
        cl.addWidget(self.scope_delim_radio)

        # 3.1 分隔符输入 + 连接（仅选"按分隔符分段"时显示）
        self.delim_options = QWidget()
        do_lay = QVBoxLayout(self.delim_options)
        do_lay.setContentsMargins(20, 2, 4, 4)
        do_lay.setSpacing(4)

        delim_row = QHBoxLayout()
        delim_row.setSpacing(4)
        delim_row.addWidget(self._mini_label("分隔符"))
        self.split_delim_edit = QLineEdit()
        self.split_delim_edit.setMaxLength(20)
        self.split_delim_edit.setPlaceholderText("如 , 多个用 | 隔")
        self.split_delim_edit.setToolTip(
            "按此分隔符把选区拆成多段。\n"
            "多个分隔符用 | 分隔，例如：，|。|；\n"
            "支持转义：\\t 制表符、\\n 换行、\\s 空格"
        )
        self.split_delim_edit.textChanged.connect(self._on_split_options_changed)
        delim_row.addWidget(self.split_delim_edit, 1)
        do_lay.addLayout(delim_row)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(4)
        conn_row.addWidget(self._mini_label("连接"))
        self.split_connector_combo = QComboBox()
        self.split_connector_combo.addItem("原分隔符", "delim")
        self.split_connector_combo.addItem("逗号+空格", "comma")
        self.split_connector_combo.addItem("无", "none")
        self.split_connector_combo.setToolTip(
            "分段后各段之间用什么连接：\n"
            "原分隔符 = 沿用你输入的第一个分隔符\n"
            "逗号+空格 = 用「, 」连接\n"
            "无 = 各段直接相邻"
        )
        self.split_connector_combo.currentIndexChanged.connect(
            self._on_split_options_changed
        )
        conn_row.addWidget(self.split_connector_combo, 1)
        do_lay.addLayout(conn_row)

        cl.addWidget(self.delim_options)
        self.delim_options.setVisible(self._split_use_delim)

        # 4. 折叠：我的符号
        cl.addWidget(
            self._collapse_section(
                "我的符号", self._build_custom_section(), expanded=False
            )
        )

        # 5. 折叠：更多符号
        more_sym = QWidget()
        ml = QVBoxLayout(more_sym)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        for name, symbols in EXTRA_GROUPS:
            ml.addWidget(self._mini_label(name))
            g = QGridLayout()
            g.setSpacing(5)
            for i, s in enumerate(symbols):
                b = self._char_btn(s, big=False)
                self._track_btn(s, b)
                b.clicked.connect(lambda _=False, x=s: self._on_insert_symbol(x))
                g.addWidget(b, i // _SMALL_COLS, i % _SMALL_COLS)
            ml.addLayout(g)
        cl.addWidget(
            self._collapse_section("更多符号（装饰/序号）", more_sym, expanded=False)
        )

        # 6. 折叠：更多文本处理
        cl.addWidget(
            self._collapse_section(
                "更多文本处理", self._build_advanced(), expanded=False
            )
        )

        cl.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # ==================== 预览区（固定） ====================
        preview_box = QFrame()
        preview_box.setObjectName("previewBox")
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(8, 6, 8, 6)
        pv.setSpacing(3)

        preview_title = QLabel("效果预览")
        preview_title.setObjectName("symPreviewTitle")
        pv.addWidget(preview_title)

        self.preview_label = QLabel("在上方选择符号和方式后，这里会显示效果")
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(38)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pv.addWidget(self.preview_label)

        root.addWidget(preview_box)

        # ==================== 应用栏（固定） ====================
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 0, 0, 0)
        apply_row.setSpacing(6)

        self.auto_apply_check = QCheckBox("选完立即应用")
        self.auto_apply_check.setChecked(self._load_auto_apply_from_cfg())
        self.auto_apply_check.setToolTip(
            "勾选：选好符号和方式后立刻改正文（不推荐——容易误触）\n"
            "不勾（默认）：先在预览区看效果，点「应用到正文」才生效"
        )
        self.auto_apply_check.toggled.connect(self._on_auto_apply_toggled)
        apply_row.addWidget(self.auto_apply_check)
        apply_row.addStretch()

        self.apply_now_btn = QPushButton("应用到正文")
        self.apply_now_btn.setCursor(Qt.PointingHandCursor)
        self.apply_now_btn.setEnabled(False)
        self.apply_now_btn.clicked.connect(self._apply_now)
        apply_row.addWidget(self.apply_now_btn)

        root.addLayout(apply_row)

        # ==================== 状态栏（固定） ====================
        self.status_label = QLabel("")
        self.status_label.setObjectName("symStatusLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._update_preview()
        self._update_status()
        self._update_apply_btn()

    def _build_custom_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.custom_holder = QWidget()
        self.custom_grid = QGridLayout(self.custom_holder)
        self.custom_grid.setContentsMargins(0, 0, 0, 0)
        self.custom_grid.setSpacing(4)
        lay.addWidget(self.custom_holder)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("输入字符或短句")
        self.custom_edit.setMaxLength(20)
        self.custom_edit.returnPressed.connect(self._add_custom)
        add_btn = QPushButton("添加")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(self._op_btn_qss())
        add_btn.clicked.connect(self._add_custom)
        add_row.addWidget(self.custom_edit, 1)
        add_row.addWidget(add_btn)
        lay.addLayout(add_row)

        lay.addWidget(self._mini_label("左键选用，右键删除"))

        self._refresh_custom_grid()
        return w

    def _build_advanced(self) -> QWidget:
        w = QWidget()
        bl = QVBoxLayout(w)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        bl.addWidget(self._mini_label("成对包裹："))
        pair_row = QGridLayout()
        pair_row.setSpacing(4)
        for i, (label, lch, rch) in enumerate(PAIRS):
            b = self._char_btn(label, big=False)
            b.setToolTip(f"用 {label} 包裹选中文字")
            b.clicked.connect(
                lambda _=False, a=lch, c=rch: self.wrap_selected.emit(a, c)
            )
            pair_row.addWidget(b, i // 3, i % 3)
        bl.addLayout(pair_row)

        bl.addWidget(self._mini_label("每行行首加："))
        bl.addLayout(self._line_edit_row(self.prefix_lines))
        bl.addWidget(self._mini_label("每行行尾加："))
        bl.addLayout(self._line_edit_row(self.suffix_lines))

        conv_row = QHBoxLayout()
        conv_row.setSpacing(4)
        for label, kind, tip in (
            ('"→「」', "corner", "选中区内成对英文双引号转直角引号"),
            ("'→『』", "book", "选中区内成对英文单引号转『』"),
            ("半角→全角", "halfwidth", ",.!?() 等转中文标点"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._op_btn_qss())
            b.clicked.connect(lambda _=False, k=kind: self.convert_selected.emit(k))
            conv_row.addWidget(b)
        bl.addLayout(conv_row)

        bl.addWidget(self._hr())
        bl.addWidget(self._mini_label("插入分隔线："))
        for tpl in DIVIDERS:
            b = QPushButton(tpl)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._op_btn_qss())
            b.clicked.connect(lambda _=False, t=tpl: self.insert_divider.emit(t))
            bl.addWidget(b)
        return w

    # ==================== UI 小组件 ====================
    def _section_title(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("symGroupTitle")
        return lab

    def _mini_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("symMiniLabel")
        return lab

    def _hr(self) -> QFrame:
        line = QFrame()
        line.setObjectName("symHr")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    def _collapse_section(
        self, title: str, content: QWidget, expanded: bool = False
    ) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        btn = QToolButton()
        btn.setText(f"{'▾' if expanded else '▸'} {title}")
        btn.setCheckable(True)
        btn.setChecked(expanded)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        content.setVisible(expanded)

        def _toggle(checked):
            sb = self._scroll
            saved_y = sb.verticalScrollBar().value() if sb else None
            content.setVisible(checked)
            btn.setText(f"{'▾' if checked else '▸'} {title}")
            if sb and saved_y is not None:
                QTimer.singleShot(0, lambda: sb.verticalScrollBar().setValue(saved_y))

        btn.toggled.connect(_toggle)
        v.addWidget(btn)
        v.addWidget(content)
        return wrap

    def _track_btn(self, sym: str, btn: QPushButton):
        btn.setCheckable(True)
        self._sym_buttons.setdefault(sym, []).append(btn)

    def _char_btn(self, text: str, big: bool = False) -> QPushButton:
        b = QPushButton(text)
        if big:
            b.setFixedSize(32, 32)
        else:
            b.setFixedSize(30, 30)
        b.setCursor(Qt.PointingHandCursor)
        b.setCheckable(True)
        name = symbol_name(text)
        if name:
            b.setToolTip(f"{name}（{text}）")
        b.setStyleSheet("QPushButton { padding: 0; margin: 0; }")
        font = b.font()
        font.setPixelSize(14 if big else 13)
        b.setFont(font)
        return b

    def _mode_btn_qss(self) -> str:
        return (
            "QPushButton { font-size: 12px; padding: 5px 2px; }"
            "QPushButton:checked { font-weight: bold; }"
        )

    def _op_btn_qss(self) -> str:
        return "QPushButton { font-size: 12px; padding: 4px 2px; }"

    def _line_edit_row(self, signal) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(3)
        edit = QLineEdit()
        edit.setMaxLength(10)
        edit.setFixedWidth(56)
        row.addWidget(edit)
        for q in LINE_QUICK[:4]:
            b = QPushButton(q)
            b.setFixedSize(24, 24)
            b.setCursor(Qt.PointingHandCursor)
            if symbol_name(q):
                b.setToolTip(symbol_name(q))
            b.setStyleSheet(
                "QPushButton { border: none; border-radius: 4px; background: transparent;"
                " padding: 0; margin: 0; }"
            )
            b.clicked.connect(lambda _=False, e=edit, v=q: e.setText(v))
            row.addWidget(b)
        apply = QPushButton("应用")
        apply.setCursor(Qt.PointingHandCursor)
        apply.setStyleSheet(self._op_btn_qss())
        apply.clicked.connect(lambda: self._emit_line(signal, edit))
        row.addWidget(apply)
        return row

    def _emit_line(self, signal, edit: QLineEdit):
        text = edit.text()
        if text.strip():
            signal.emit(text)

    # ==================== 自定义符号 ====================
    def _cfg_key(self) -> str:
        return "custom_symbols"

    def _load_custom(self):
        if self.cfg is None:
            return []
        raw = self.cfg.get_config("Writing", self._cfg_key(), "[]")
        try:
            data = json.loads(raw or "[]")
            if isinstance(data, list):
                return [str(x) for x in data][:30]
        except (ValueError, TypeError):
            pass
        return []

    def _save_custom(self):
        if self.cfg is None:
            return
        self.cfg.set_config(
            "Writing",
            self._cfg_key(),
            json.dumps(self._custom, ensure_ascii=False),
            validate=False,
        )

    def _refresh_custom_grid(self):
        while self.custom_grid.count():
            item = self.custom_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._custom:
            tip = self._mini_label("（还没有自定义符号）")
            self.custom_grid.addWidget(tip, 0, 0, 1, _BIG_COLS)
            return
        for i, s in enumerate(self._custom):
            b = self._char_btn(s if len(s) <= 2 else s[:2] + "…", big=True)
            self._track_btn(s, b)
            b.setToolTip(f"{s}\n左键选用，右键删除")
            b.clicked.connect(lambda _=False, x=s: self._on_insert_symbol(x))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, idx=i, btn=b: self._custom_context_menu(
                    idx, btn.mapToGlobal(pos)
                )
            )
            # 关键：把按钮加进网格。原来忘了这行，按钮创建完就被 GC。
            self.custom_grid.addWidget(b, i // _BIG_COLS, i % _BIG_COLS)

    def _custom_context_menu(self, idx: int, global_pos=None):
        menu = QMenu(self)
        act_del = menu.addAction("删除该符号")
        chosen = menu.exec(global_pos or self.cursor().pos())
        if chosen == act_del:
            removed = self._custom[idx]
            del self._custom[idx]
            self._save_custom()
            self._refresh_custom_grid()
            # 改：原来比较的 active_sym_edit 控件并不存在
            if self._active_sym == removed:
                self._active_sym = ""
                self._sync_active_check("")
                self._maybe_clear_live()

    def _add_custom(self):
        text = self.custom_edit.text().strip()
        if not text:
            return
        if text in self._custom:
            self.custom_edit.clear()
        else:
            self._custom.append(text)
            self._save_custom()
            self.custom_edit.clear()
            self._refresh_custom_grid()
        self._on_insert_symbol(text)

    # ==================== 核心交互 ====================
    def _on_insert_symbol(self, symbol: str):
        if self._active_sym == symbol:
            # 再点同一个 = 取消选中
            self._active_sym = ""
            self._sync_active_check("")
            self._maybe_clear_live()
            self._after_state_change()
            return
        self._active_sym = symbol
        self._sync_active_check(symbol)
        # 统一行为：此方法只"选中符号"，不插入。
        # - 勾选"选完立即应用"：_after_state_change 内部会调 _apply_now
        # - 未勾选：只更新预览/状态，用户点"应用到正文"才生效
        # 之前无选区时直接 insert_symbol，与"未勾选自动应用却直接生效"矛盾。
        self._after_state_change()

    def _on_mode_toggled(self, kind: str, checked: bool):
        if checked:
            for k, b in self.mode_buttons.items():
                if k != kind and b.isChecked():
                    b.blockSignals(True)
                    b.setChecked(False)
                    b.blockSignals(False)
            self._mode = kind
        else:
            self._mode = None
            self._maybe_clear_live()
        self.split_char_block.setVisible(self._mode == "split")
        self._after_state_change()

    def _on_scope_toggled(self, _checked):
        self._split_use_delim = self.scope_delim_radio.isChecked()
        self._save_split_options_to_cfg()
        self.delim_options.setVisible(self._split_use_delim)
        self._after_state_change()

    def _on_split_options_changed(self, *_):
        self._after_state_change()

    def _on_auto_apply_toggled(self, checked: bool):
        self._save_auto_apply_to_cfg(checked)
        self._after_state_change()

    def _after_state_change(self):
        """任何影响结果的状态改变后调用：
        1. 更新预览
        2. 根据自动/手动模式决定立即应用还是启用按钮
        """
        self._update_preview()
        self._update_status()
        self._update_apply_btn()

        if not self._can_apply():
            return
        if self.auto_apply_check.isChecked():
            self._apply_now()

    def _can_apply(self) -> bool:
        """是否满足应用条件。

        - 无选区：只需选中符号（在光标处插入）
        - 有选区：需选中符号 + 选插入方式（用于批量处理）
        """
        if not self._active_sym:
            return False
        if self._has_selection:
            return self._mode is not None
        return True

    def _apply_now(self):
        if not self._can_apply():
            return
        if self._applying:
            return
        self._applying = True
        try:
            if self._has_selection:
                self._emit_edge(self._mode)
            else:
                # 无选区：插入符号到光标处
                self.insert_symbol.emit(self._active_sym)
                # 插入后清空选中态，允许再次点击同一符号
                self._active_sym = ""
                self._sync_active_check("")
        finally:
            self._applying = False
        # 关键：插入/替换结束后，主动同步一次编辑器真实选区。
        # 上面 emit 触发的 editor.selection_changed 会被 _applying
        # 标志屏蔽（防止递归），但屏蔽后 _has_selection/_selection_text
        # 就会停留在"应用前"的状态。用户紧接着点润色，_quick_action
        # 读到的是过期选区，处理的是旧内容。
        try:
            w = self.window()
            ws = getattr(w, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None:
                    self._applying = False  # 已置过，显式再置一次防混淆
                    self.set_selection_text(ed.selected_text() or "")
        except Exception:
            pass
        self._update_apply_btn()

    def _maybe_clear_live(self):
        if self._applying:
            return
        self._applying = True
        try:
            self.revert_live.emit()
        finally:
            self._applying = False

    def _sync_active_check(self, sym: str):
        for buttons in self._sym_buttons.values():
            for b in buttons:
                try:
                    b.blockSignals(True)
                    b.setChecked(False)
                    b.blockSignals(False)
                except RuntimeError:
                    pass
        if not sym:
            return
        if sym in self._sym_buttons:
            for b in self._sym_buttons[sym]:
                try:
                    b.blockSignals(True)
                    b.setChecked(True)
                    b.blockSignals(False)
                except RuntimeError:
                    pass
            return
        for text, buttons in self._sym_buttons.items():
            if text in sym or sym in text:
                for b in buttons:
                    try:
                        b.blockSignals(True)
                        b.setChecked(True)
                        b.blockSignals(False)
                    except RuntimeError:
                        pass

    def _emit_edge(self, kind: str):
        sym = self._active_sym
        if not sym:
            return
        delimiter = ""
        connector = "delim"
        if self._split_use_delim:
            delimiter = self.split_delim_edit.text().strip()
            connector = self.split_connector_combo.currentData() or "delim"
        self.edge_apply.emit(
            kind,
            sym,
            self.split_every_spin.value(),
            self.skip_ws_check.isChecked(),
            delimiter,
            connector,
        )

    # ==================== 预览 / 状态 / 按钮 ====================
    def _update_preview(self):
        # ---- 无选区：只预览"光标处插入什么符号" ----
        if not self._selection_text.strip():
            if self._active_sym:
                self.preview_label.setText(f"将在光标处插入：{self._active_sym}")
                self.preview_label.setStyleSheet(
                    f"font-size: 14px; color: {self._text_c()};" f" font-weight: bold;"
                )
            else:
                self.preview_label.setText("点上方符号，将插入到光标处")
                self.preview_label.setStyleSheet(
                    f"font-size: 12px; color: {self._muted_c()};"
                )
            return

        # ---- 有选区：跟原来一样 ----
        if not self._active_sym or not self._mode:
            self.preview_label.setText("继续选择符号和插入方式…")
            self.preview_label.setStyleSheet(
                f"font-size: 12px; color: {self._muted_c()};"
            )
            return

        delim = ""
        conn = "delim"
        if self._split_use_delim:
            delim = self.split_delim_edit.text().strip()
            conn = self.split_connector_combo.currentData() or "delim"
        result = build_edge_result(
            self._selection_text,
            self._mode,
            self._active_sym,
            self.split_every_spin.value(),
            self.skip_ws_check.isChecked(),
            delim,
            conn,
        )
        if result is None:
            self.preview_label.setText("（当前设置下无变化）")
            self.preview_label.setStyleSheet(
                f"font-size: 12px; color: {self._muted_c()};"
            )
            return
        # 结果可能带换行；统一为可见的换行符不影响显示
        shown = result.replace("\n", "⏎")
        if len(shown) > 120:
            shown = shown[:120] + "…"
        self.preview_label.setText(shown)
        self.preview_label.setStyleSheet(f"font-size: 12px; color: {self._text_c()};")

    def _update_status(self):
        auto = self.auto_apply_check.isChecked()
        # ---- 无选区 ----
        if not self._has_selection:
            if not self._active_sym:
                self.status_label.setText(
                    "点上方符号，将在光标处插入（可选中文字批量处理）"
                )
            else:
                if auto:
                    self.status_label.setText(f"已插入「{self._active_sym}」到光标处")
                else:
                    self.status_label.setText(
                        f"已选符号「{self._active_sym}」"
                        f"· 点「应用到正文」插入到光标处"
                    )
            return

        # ---- 有选区 ----
        if not self._active_sym:
            self.status_label.setText("已选中文字 · 请点上方符号")
        elif not self._mode:
            self.status_label.setText(f"已选符号「{self._active_sym}」· 请选插入方式")
        else:
            if auto:
                self.status_label.setText("已自动应用到正文（可按 Ctrl+Z 撤销）")
            else:
                self.status_label.setText("预览已生成 · 点「应用到正文」生效")

    def _update_apply_btn(self):
        ready = self._can_apply() and not self.auto_apply_check.isChecked()
        self.apply_now_btn.setEnabled(ready)

    def _muted_c(self) -> str:
        t = self._theme or {}
        return t.get("muted", "#9A8E76")

    def _text_c(self) -> str:
        t = self._theme or {}
        return t.get("text", "#3B342A")

    # ==================== 选区 ====================
    def set_selection_text(self, text: str):
        """外部（editor.selection_changed）调用：传入当前选区文本。

        - 有内容：更新 _selection_text，可能触发自动应用
        - 空串：清空状态
        """
        if self._applying:
            return
        text = text or ""
        had = self._has_selection
        self._has_selection = bool(text.strip())
        self._selection_text = text

        if self._has_selection:
            if not had:
                # 从无选区切到有选区：解除上一次的符号/方式残留
                self._disarm_all()
            # 无论新旧选区，都刷新预览
            self._after_state_change()
        else:
            self._update_preview()
            self._update_status()
            self._update_apply_btn()

    # 兼容旧调用名（workspace 若还有旧连接也能跑）
    def set_has_selection(self, has: bool):
        if not has:
            self.set_selection_text("")
        else:
            # 只有布尔值，无法更新预览，退化为"仅刷新状态"
            self._has_selection = True
            self._update_status()

    def _disarm_all(self):
        self._active_sym = ""
        self._mode = None
        self._sync_active_check("")
        for b in self.mode_buttons.values():
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self.split_char_block.setVisible(False)

    # ==================== 主题 ====================
    def apply_colors(self, theme: dict):
        theme = theme or {}
        self._theme = theme
        # 性能：面板隐藏时（启动默认隐藏）不做任何 setStyleSheet，
        # 只挂起最新主题，等 showEvent 首次显示前一次性应用。
        if self.isHidden():
            self._theme_pending = True
            return
        self._theme_pending = False
        bg = theme.get("sidebar", "#EFE8D8")
        text_color = theme.get("text", "#3B342A")
        muted = theme.get("muted", "#9A8E76")
        accent = theme.get("accent", "#B08A4E")
        editor_bg = theme.get("editor_bg", theme.get("bg", "#fcfaf4"))

        # 整个面板只设置这一次 QSS：原实现切一次主题要对全面板所有
        # QLabel/QToolButton/QFrame 逐个 setStyleSheet（20+ 次，每次都触发
        # re-polish），现在全部用 objectName / 类型选择器在一条样式表里声明。
        self.setStyleSheet(
            f"#symbolPanel {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text_color}; background: transparent; }}"
            f"QPushButton {{ color: {text_color}; background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)}; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: {qss_alpha(accent, 0x22)};"
            f" border-color: {accent}; }}"
            f"QPushButton:checked {{ background: {qss_alpha(accent, 0x22)};"
            f" border: 1px solid {accent}; }}"
            f"QPushButton:disabled {{ color: {muted};"
            f" border-color: {qss_alpha(muted, 0x33)}; }}"
            f"QScrollArea {{ border: none; background: transparent; }}"
            # 输入类：背景 + 边框 + 圆角，跟随当前主题
            f"QLineEdit, QSpinBox, QComboBox {{"
            f" color: {text_color}; background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x55)}; border-radius: 4px;"
            f" padding: 2px 6px; }}"
            f"QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{"
            f" border: 1px solid {accent}; }}"
            # 勾选框 / 单选按钮：文字 + 指示器跟随主题。
            # 未勾选 = editor_bg 底 + muted 边框；勾选 = accent 底色块。
            # 说明：这里不画勾/点的具体图形（app 冻结表里的 image: url()
            # 是启动主题生成的 PNG，切主题不会重画）。用"纯色填充"区分
            # 勾/未勾，小尺寸下视觉上够用，且颜色跟随当前主题。
            f"QCheckBox, QRadioButton {{ color: {text_color};"
            f" background: transparent; spacing: 7px; }}"
            f"QCheckBox::indicator, QRadioButton::indicator {{"
            f" width: 15px; height: 15px;"
            f" background-color: {editor_bg};"
            f" border: 1px solid {muted}; }}"
            f"QCheckBox::indicator {{ border-radius: 4px; }}"
            f"QRadioButton::indicator {{ border-radius: 8px; }}"
            f"QCheckBox::indicator:hover, QRadioButton::indicator:hover {{"
            f" border-color: {accent}; }}"
            f"QCheckBox::indicator:checked, QRadioButton::indicator:checked {{"
            f" background-color: {accent}; border-color: {accent}; }}"
            f"QFrame#previewBox {{ background: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x55)}; border-radius: 6px; }}"
            f"QWidget#qt_scrollarea_viewport {{ background: transparent; }}"
            # 以下为原"逐控件补色"区域，统一收敛到这里
            f"QLabel#symGuideBar {{ font-size: 12px; padding: 5px 6px; border-radius: 5px;"
            f" background: {qss_alpha(accent, 0x18)}; color: {accent}; }}"
            f"QLabel#symStatusLabel {{ font-size: 12px; padding: 4px 6px; border-radius: 4px;"
            f" background: {qss_alpha(muted, 0x18)}; color: {muted}; }}"
            f"QLabel#symGroupTitle {{ font-size: 12px; font-weight: bold;"
            f" color: {muted}; margin-top: 2px; }}"
            f"QLabel#symMiniLabel {{ font-size: 12px; color: {text_color}; }}"
            f"QLabel#symPreviewTitle {{ font-size: 12px; font-weight: bold; color: {muted}; }}"
            f"QToolButton {{ border: none; font-size: 12px; font-weight: bold;"
            f" color: {muted}; background: transparent; text-align: left; padding: 2px; }}"
            f"QToolButton:hover {{ color: {accent}; }}"
            f"QFrame#symHr {{ background-color: {qss_alpha(muted, 0x33)}; border: none; }}"
        )

        self._update_preview()

    def showEvent(self, event):
        super().showEvent(event)
        # 显示前把挂起的最新主题一次性补上（此时尚未绘制，不会出现颜色闪烁）
        if getattr(self, "_theme_pending", False):
            self.apply_colors(self._theme)
