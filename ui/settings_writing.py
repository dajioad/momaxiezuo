# ui/settings_writing.py
"""墨码写作 - 设置对话框

左侧竖向导航 + 右侧卡片式页面：
外观 / 写作行为 / AI 接口 / 墨灵 / 快捷键 / 通用 / 关于
所有设置在点「完成」时保存到 ConfigManager；AI Key 加密存入 ai.dat。

布局说明：每个页面的内容都包了一层 QScrollArea，
内容超出可视区时页内自己滚动，短页面也不会被长页面撑高。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QLineEdit,
    QFormLayout,
    QScrollArea,
    QFrame,
    QMessageBox,
    QApplication,
    QTextEdit,
    QGridLayout,
    QColorDialog,
    QFileDialog,
    QDialogButtonBox,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QSlider,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QUrl, QTimer, QSize
from PySide6.QtGui import (
    QFontDatabase,
    QFont,
    QColor,
    QDesktopServices,
    QFontMetrics,
    QKeySequence,
)
import qtawesome as qta
import json
import os

from utils.ai_client import (
    save_ai_config,
    load_ai_config,
    load_ai_config_for_provider,
    get_provider_saved_config,
    AIWorker,
    PROVIDERS,
    PROVIDER_ORDER,
    guess_provider,
    find_model,
    model_display_text,
    model_tag,
)
from utils.hotkeys import (
    HotkeyManager,
    all_actions,
    action_groups,
    get_action,
    BUILTIN_EDITOR_SHORTCUTS,
)
from utils.logger import get_logger
from ui.writing_editor import (
    WRITING_THEMES,
    THEME_COLOR_FIELDS,
    CUSTOM_THEME_KEY,
    default_custom_theme,
    load_custom_theme,
    get_writing_theme,
)

from ui.constants import APP_VERSION, AFDIAN_HOME
from ui.dialog_styles import Card, SettingRow, SectionTitle

logger = get_logger()

CONTACT_QQ = "2818491757"
CONTACT_WECHAT = "RaiCoutry"
CONTACT_EMAIL = "ccccccc0819@163.com"


class SettingsWritingDialog(QDialog):
    settings_saved = Signal()
    theme_preview = Signal(object)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self._test_worker = None
        self._loading = False
        self._saved_key = ""
        self._hotkeys = HotkeyManager(config_manager)
        self.setWindowTitle("墨码写作 · 设置")
        self.setMinimumWidth(680)
        self.setMinimumHeight(520)
        self._build_ui()
        self._load_values()
        self._fit_to_parent()

        # 关键：控件全部创建完毕后禁用滚轮改值
        self._install_wheel_guard()

    # ---------------- 滚轮守卫 ----------------
    def _install_wheel_guard(self):
        """完全禁用滚轮改数字框/下拉框/滑块的值。

        用户必须手动输入或点击上下箭头/下拉列表/拖动滑块。
        ——这样避免了在设置面板里滚页面时鼠标路过控件就误改值的坑。
        """

        def _block_wheel(event):
            event.ignore()  # 交给父级 QScrollArea 处理（页面正常滚动）

        for w in self.findChildren(QSpinBox):
            w.wheelEvent = _block_wheel
        for w in self.findChildren(QDoubleSpinBox):
            w.wheelEvent = _block_wheel
        for w in self.findChildren(QComboBox):
            w.wheelEvent = _block_wheel
        for w in self.findChildren(QSlider):
            w.wheelEvent = _block_wheel

    # ---------------- 尺寸自适应 ----------------
    def _fit_to_parent(self):
        parent = self.parent()
        if parent is None:
            parent = QApplication.activeWindow()
        target_h = 620
        if parent is not None and parent.height() > 0:
            target_h = max(520, min(680, int(parent.height() * 0.82)))
        self.resize(760, target_h)

    def _scroll_wrap(self, content: QWidget) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _build_ui(self):
        root = QVBoxLayout(self)
        # 与全部对话框统一的外边距 / 行距令牌
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(12)

        # ---------- 左侧竖向导航 ----------
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(148)
        self.nav.setIconSize(QSize(15, 15))
        self.nav.setFocusPolicy(Qt.NoFocus)

        # ---------- 右侧页面栈 ----------
        self.stack = QStackedWidget()

        pages = [
            ("外观", "fa5s.palette", self._build_appearance_tab),
            ("写作行为", "fa5s.pencil-alt", self._build_writing_behavior_tab),
            ("AI 接口", "fa5s.microchip", self._build_ai_tab),
            ("墨灵", "fa5s.paw", self._build_pet_tab),
            ("快捷键", "fa5s.keyboard", self._build_hotkeys_tab),
            ("通用", "fa5s.cog", self._build_general_tab),
            ("关于", "fa5s.info-circle", self._build_about_tab),
        ]
        muted = self._resolve_theme().get("muted", "#9a8e76")
        for label, icon_name, builder in pages:
            widget = builder()
            self.stack.addWidget(widget)
            try:
                item = QListWidgetItem(qta.icon(icon_name, color=muted), label)
            except Exception:
                item = QListWidgetItem(label)
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("完成")
        close_btn.setProperty("variant", "primary")
        close_btn.setMinimumWidth(96)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

    def _resolve_accent(self) -> str:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    t = editor._theme or {}
                    if t.get("accent"):
                        return t["accent"]
                break
            p = p.parent()
        return "#b08a4e"

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    return editor._theme or {}
                break
            p = p.parent()
        return {}

    def select_tab(self, index: int):
        if 0 <= index < self.nav.count():
            self.nav.setCurrentRow(index)

    # ---------------- 外观 ----------------
    def _build_appearance_tab(self) -> QWidget:
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        # ---------- 主题 ----------
        theme_card = Card("主题")
        tform = QFormLayout()
        tform.setSpacing(12)
        tform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tform.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.theme_combo = QComboBox()
        for key, t in WRITING_THEMES.items():
            self.theme_combo.addItem(t["name"], key)
        self.theme_combo.addItem("我的主题（自定义配色）", CUSTOM_THEME_KEY)
        self.theme_combo.currentIndexChanged.connect(self._preview_selected_theme)
        self.theme_combo.setMinimumWidth(280)
        tform.addRow("写作主题", self.theme_combo)

        tform.addRow(self._build_custom_theme_box())
        theme_card.body().addLayout(tform)
        outer.addWidget(theme_card)

        # ---------- 正文排版 ----------
        text_card = Card("正文排版")
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        _recommended = [
            "SimSun",
            "KaiTi",
            "FangSong",
            "Source Han Serif SC",
        ]
        _all_families = QFontDatabase.families()
        _family_set = set(_all_families)
        for f in _recommended:
            if f in _family_set:
                self.font_combo.addItem(f"★ {f}（印刷体推荐）", f)
        for family in _all_families:
            self.font_combo.addItem(family, family)
        self.font_combo.setMinimumWidth(320)
        self.font_combo.setToolTip(
            "微软雅黑下中文引号不明显，宋体 / 楷体等衬线字体更清晰。"
        )
        form.addRow("正文字体", self.font_combo)

        font_hint = QLabel("想看清中文引号「“ ”」的弯度，换成宋体 / 楷体 / 思源宋体")
        font_hint.setWordWrap(True)
        font_hint.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')};"
            f" font-size: 12px; padding-left: 8px;"
        )
        form.addRow("", font_hint)

        self.font_size = QSpinBox()
        self.font_size.setRange(12, 28)
        self.font_size.setSuffix(" px")
        self.font_size.setMinimumWidth(120)
        self.font_size.setMaximumWidth(160)
        form.addRow("字号", self.font_size)

        self.line_spacing = QDoubleSpinBox()
        self.line_spacing.setRange(1.2, 2.5)
        self.line_spacing.setSingleStep(0.1)
        self.line_spacing.setDecimals(1)
        self.line_spacing.setMinimumWidth(120)
        self.line_spacing.setMaximumWidth(160)
        form.addRow("行距", self.line_spacing)

        self.content_width_combo = QComboBox()
        self.content_width_combo.addItem("紧凑（720px，长时间写作推荐）", "720")
        self.content_width_combo.addItem("舒适（900px）", "900")
        self.content_width_combo.addItem("宽松（1100px）", "1100")
        self.content_width_combo.addItem("铺满窗口（不限宽）", "0")
        self.content_width_combo.setMinimumWidth(280)
        self.content_width_combo.setToolTip(
            "正文居中显示的宽度。窄一点更护眼——一行文字太长时\n"
            "眼睛要来回扫，容易累。720px 约能显示 30-35 个汉字。\n"
            "窗口很宽又想多用屏幕时，选「铺满窗口」。"
        )
        form.addRow("正文宽度", self.content_width_combo)

        self.para_indent_combo = QComboBox()
        self.para_indent_combo.addItem("两个全角空格（网文常用）", "full")
        self.para_indent_combo.addItem("两个半角空格", "half")
        self.para_indent_combo.addItem("不自动缩进", "none")
        self.para_indent_combo.setMinimumWidth(280)
        self.para_indent_combo.setToolTip(
            "新章节与按回车产生的新段落，开头自动补上缩进；\n"
            "已有的手动缩进不会被改动"
        )
        form.addRow("段首缩进", self.para_indent_combo)

        text_card.body().addLayout(form)
        outer.addWidget(text_card)

        # ---------- 界面字号 ----------
        ui_card = Card("界面字号")
        uf = QFormLayout()
        uf.setSpacing(12)
        uf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        uf.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.ui_font_combo = QComboBox()
        self.ui_font_combo.addItem("标准（10pt）", "10")
        self.ui_font_combo.addItem("偏大（11pt）", "11")
        self.ui_font_combo.addItem("大（12pt）", "12")
        self.ui_font_combo.addItem("特大（13pt）", "13")
        self.ui_font_combo.setMinimumWidth(200)
        self.ui_font_combo.setToolTip(
            "影响菜单、按钮、列表等基础控件的字体大小。\n"
            "各面板内已经写死的提示文字不受此设置影响。"
        )
        uf.addRow("界面字号", self.ui_font_combo)

        self.ai_font_size_spin = QSpinBox()
        self.ai_font_size_spin.setRange(12, 20)
        self.ai_font_size_spin.setSuffix(" px")
        self.ai_font_size_spin.setMinimumWidth(120)
        self.ai_font_size_spin.setMaximumWidth(160)
        self.ai_font_size_spin.setToolTip(
            "AI 回复气泡、小说转剧本等 AI 输出内容的字号。\n"
            "不影响编辑器里的正文字号。"
        )
        uf.addRow("AI 输出字号", self.ai_font_size_spin)

        ui_card.body().addLayout(uf)
        outer.addWidget(ui_card)

        # ---------- 正文显示 ----------
        display_card = Card("正文显示")
        df = QVBoxLayout()
        df.setSpacing(10)

        self.dict_highlight_check = QCheckBox("高亮人物 / 高频词（按分类着色）")
        self.dict_highlight_check.setToolTip(
            "关闭后，正文中的人物名、地名、技能等词条\n不再显示彩色高亮"
        )
        df.addWidget(self.dict_highlight_check)

        display_card.body().addLayout(df)
        outer.addWidget(display_card)

        outer.addStretch(1)
        return self._scroll_wrap(content)

    # ---------------- 写作行为 ----------------
    def _build_writing_behavior_tab(self) -> QWidget:
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        # ---------- 目标 ----------
        goal_card = Card("目标")
        gf = QFormLayout()
        gf.setSpacing(12)
        gf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        gf.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.target_spin = QSpinBox()
        self.target_spin.setRange(0, 100000)
        self.target_spin.setSingleStep(500)
        self.target_spin.setSuffix(" 字")
        self.target_spin.setSpecialValueText("不设定目标")
        self.target_spin.setMinimumWidth(160)
        self.target_spin.setMaximumWidth(200)
        gf.addRow("每日码字目标", self.target_spin)

        goal_card.body().addLayout(gf)
        outer.addWidget(goal_card)

        # ---------- 输入辅助 ----------
        input_card = Card("输入辅助")
        inf = QVBoxLayout()
        inf.setSpacing(10)

        self.typewriter_check = QCheckBox("打字机滚动（光标始终保持在屏幕中部）")
        inf.addWidget(self.typewriter_check)

        self.paragraph_focus_check = QCheckBox(
            "段落聚焦（光标所在段落高亮，其它段落变淡）")
        self.paragraph_focus_check.setToolTip(
            "开启后，正在写的那一段保持正常显示，\n"
            "其它段落降到约 32% 不透明度——视线自然聚焦在当前段。\n"
            "喜欢的人会立刻打开再也不关，不喜欢的人不受影响。\n"
            "（默认关闭）")
        inf.addWidget(self.paragraph_focus_check)

        self.auto_indent_check = QCheckBox(
            "回车新段自动补段首缩进（缩进样式见「外观」）"
        )
        self.auto_indent_check.setToolTip(
            "关闭后：按回车新起一段时，不自动加缩进前缀。\n"
            "适合 Markdown 写作或已有手动缩进习惯的用户。"
        )
        inf.addWidget(self.auto_indent_check)

        self.auto_quote_pair_check = QCheckBox(
            "直引号自动配对（输入 \" 或 ' 时自动补齐成对）"
        )
        self.auto_quote_pair_check.setToolTip(
            '关闭后：输入 " 只插入一个直引号，不自动补全为「"…"」。\n'
            "适合粘贴代码、英文写作或从别处复制引号时不想被转义。"
        )
        inf.addWidget(self.auto_quote_pair_check)

        self.tab_insert_check = QCheckBox("Tab 键插入制表符（关闭后 Tab 切换焦点）")
        self.tab_insert_check.setToolTip(
            "开启（默认）：Tab 键在正文里插入制表符。\n"
            "关闭：Tab 键把焦点移到下一个控件，符合 Web 表单习惯。"
        )
        inf.addWidget(self.tab_insert_check)

        input_card.body().addLayout(inf)
        outer.addWidget(input_card)

        tip = QLabel("修改在关闭设置后自动应用。")
        tip.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')}; font-size: 12px;"
        )
        outer.addWidget(tip)
        outer.addStretch(1)
        return self._scroll_wrap(content)

    # ---------------- 快捷键 ----------------
    def _build_hotkeys_tab(self) -> QWidget:
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        card = Card("快捷键", "双击任意一行修改快捷键；按 Esc 取消，"
                    "按 Backspace / Delete 清空（表示禁用）。冲突时会提示。")
        lay = card.body()
        lay.setSpacing(10)

        self.hotkey_tree = QTreeWidget()
        self.hotkey_tree.setHeaderLabels(["动作", "快捷键"])
        self.hotkey_tree.setColumnWidth(0, 300)
        self.hotkey_tree.setIndentation(16)
        self.hotkey_tree.setAlternatingRowColors(True)
        self.hotkey_tree.setUniformRowHeights(True)
        self.hotkey_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.hotkey_tree.itemDoubleClicked.connect(self._on_hotkey_double_clicked)
        self.hotkey_tree.customContextMenuRequested.connect(
            self._on_hotkey_context_menu
        )
        lay.addWidget(self.hotkey_tree, 1)

        btn_row = QHBoxLayout()
        edit_btn = QPushButton("修改所选…")
        edit_btn.clicked.connect(self._edit_selected_hotkey)
        clear_btn = QPushButton("清空所选")
        clear_btn.clicked.connect(self._clear_selected_hotkey)
        reset_btn = QPushButton("恢复所选默认")
        reset_btn.clicked.connect(self._reset_selected_hotkey)
        reset_all_btn = QPushButton("全部恢复默认")
        reset_all_btn.clicked.connect(self._reset_all_hotkeys)
        for b in (edit_btn, clear_btn, reset_btn, reset_all_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        outer.addWidget(card, 1)
        self._refresh_hotkey_tree()
        return self._scroll_wrap(content)

    def _refresh_hotkey_tree(self):
        if not hasattr(self, "hotkey_tree"):
            return
        self.hotkey_tree.blockSignals(True)
        self.hotkey_tree.clear()

        for group in action_groups():
            g_item = QTreeWidgetItem([group, ""])
            font = g_item.font(0)
            font.setBold(True)
            g_item.setFont(0, font)
            g_item.setFlags(Qt.ItemIsEnabled)
            g_item.setForeground(0, QColor("#b08a4e"))
            self.hotkey_tree.addTopLevelItem(g_item)
            for a in all_actions():
                if a.group != group:
                    continue
                key = self._hotkeys.get(a.id) or ""
                display = key if key else "（未设置）"
                item = QTreeWidgetItem([a.label, display])
                item.setData(0, Qt.UserRole, a.id)
                item.setToolTip(0, a.desc)
                item.setToolTip(1, "双击修改；按 Backspace 清空")
                g_item.addChild(item)
            g_item.setExpanded(True)

        g_item = QTreeWidgetItem(["编辑器内置（不可修改）", ""])
        font = g_item.font(0)
        font.setBold(True)
        g_item.setFont(0, font)
        g_item.setFlags(Qt.ItemIsEnabled)
        g_item.setForeground(0, QColor("#7F858F"))
        self.hotkey_tree.addTopLevelItem(g_item)
        for key, desc in BUILTIN_EDITOR_SHORTCUTS:
            item = QTreeWidgetItem([desc, key])
            item.setFlags(Qt.ItemIsEnabled)
            item.setForeground(0, QColor("#7F858F"))
            item.setForeground(1, QColor("#7F858F"))
            g_item.addChild(item)
        g_item.setExpanded(True)

        self.hotkey_tree.blockSignals(False)

    def _current_hotkey_action_id(self) -> str:
        it = self.hotkey_tree.currentItem()
        if it is None:
            return ""
        return it.data(0, Qt.UserRole) or ""

    def _on_hotkey_double_clicked(self, item, _col):
        aid = item.data(0, Qt.UserRole)
        if aid:
            self._edit_hotkey_by_id(aid)

    def _on_hotkey_context_menu(self, pos):
        item = self.hotkey_tree.itemAt(pos)
        if item is None or not item.data(0, Qt.UserRole):
            return
        menu = QMenu(self)
        act_edit = menu.addAction("修改…")
        act_clear = menu.addAction("清空（禁用）")
        act_reset = menu.addAction("恢复此动作默认")
        chosen = menu.exec(self.hotkey_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        aid = item.data(0, Qt.UserRole)
        if chosen == act_edit:
            self._edit_hotkey_by_id(aid)
        elif chosen == act_clear:
            self._hotkeys.set(aid, "")
            self._refresh_hotkey_tree()
        elif chosen == act_reset:
            self._hotkeys.reset_action(aid)
            self._refresh_hotkey_tree()

    def _edit_selected_hotkey(self):
        aid = self._current_hotkey_action_id()
        if aid:
            self._edit_hotkey_by_id(aid)

    def _edit_hotkey_by_id(self, action_id: str):
        a = get_action(action_id)
        if a is None:
            return
        cur = self._hotkeys.get(action_id) or ""
        dlg = _HotkeyEditDialog(a.label, cur, self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_key = dlg.get_key()
        conflict_id = self._hotkeys.find_conflict(new_key, action_id)
        if conflict_id:
            ca = get_action(conflict_id)
            label = ca.label if ca else conflict_id
            QMessageBox.warning(
                self,
                "快捷键冲突",
                f"「{new_key}」已被「{label}」使用。\n\n"
                f"请先修改「{label}」的快捷键，或换一个组合。",
            )
            return
        self._hotkeys.set(action_id, new_key)
        self._refresh_hotkey_tree()

    def _clear_selected_hotkey(self):
        aid = self._current_hotkey_action_id()
        if not aid:
            return
        self._hotkeys.set(aid, "")
        self._refresh_hotkey_tree()

    def _reset_selected_hotkey(self):
        aid = self._current_hotkey_action_id()
        if not aid:
            return
        self._hotkeys.reset_action(aid)
        self._refresh_hotkey_tree()

    def _reset_all_hotkeys(self):
        if (
            QMessageBox.question(
                self,
                "恢复默认",
                "确定把所有快捷键恢复为出厂默认？\n" "当前自定义的绑定会丢失。",
            )
            != QMessageBox.Yes
        ):
            return
        self._hotkeys.reset_all()
        self._refresh_hotkey_tree()

    # ---------------- 个性化主题配色 ----------------
    def _build_custom_theme_box(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(SectionTitle("个性化主题配色"))
        if getattr(self, "_custom_theme", None) is None:
            self._custom_theme = default_custom_theme()

        # 两列布局：左列 3 个色块，右列 3 个色块
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self._color_buttons = {}
        for i, (key, label) in enumerate(THEME_COLOR_FIELDS):
            row = i % 3
            col_group = i // 3
            lab = QLabel(label)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(lab, row, col_group * 2)
            btn = QPushButton()
            btn.setFixedSize(96, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            self._color_buttons[key] = btn
            grid.addWidget(btn, row, col_group * 2 + 1)
        lay.addLayout(grid)

        row = QHBoxLayout()
        base_btn = QPushButton("以当前主题为基础")
        base_btn.clicked.connect(self._copy_selected_as_base)
        reset_btn = QPushButton("恢复默认配色")
        reset_btn.clicked.connect(self._reset_custom_colors)
        row.addWidget(base_btn)
        row.addWidget(reset_btn)
        row.addStretch()
        lay.addLayout(row)

        tip = QLabel(
            "点击色块选色后自动切到「我的主题」并实时预览；"
            "点「完成」保存，取消则不生效。"
        )
        tip.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')}; font-size: 12px;"
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self._refresh_color_buttons()
        return box

    def _refresh_color_buttons(self):
        for k, btn in getattr(self, "_color_buttons", {}).items():
            c = self._custom_theme.get(k, "#ffffff")
            text_color = "#222222" if QColor(c).lightness() > 150 else "#ffffff"
            btn.setText(c.upper())
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {c}; color: {text_color};"
                " border: 1px solid #b0a589; border-radius: 6px;"
                " font-size: 12px; font-weight: 600; }}"
                "QPushButton:hover { border: 2px solid #8a6d3b; }"
            )

    def _select_custom_and_preview(self):
        idx = self.theme_combo.findData(CUSTOM_THEME_KEY)
        if idx >= 0 and self.theme_combo.currentIndex() != idx:
            self.theme_combo.setCurrentIndex(idx)
        else:
            self._preview_selected_theme()

    def _pick_color(self, key: str):
        dlg = QColorDialog(QColor(self._custom_theme[key]), self)
        dlg.setWindowTitle("选择颜色")
        dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        if dlg.exec() == QColorDialog.Accepted:
            color = dlg.currentColor()
            if color.isValid():
                self._custom_theme[key] = color.name()
                self._refresh_color_buttons()
                self._select_custom_and_preview()

    def _copy_selected_as_base(self):
        base = dict(get_writing_theme(self.theme_combo.currentData(), self.cfg))
        base["name"] = "我的主题"
        self._custom_theme = base
        self._refresh_color_buttons()
        self._select_custom_and_preview()

    def _reset_custom_colors(self):
        self._custom_theme = default_custom_theme()
        self._refresh_color_buttons()
        self._select_custom_and_preview()

    def _preview_selected_theme(self, *_args):
        key = self.theme_combo.currentData()
        if not key:
            return
        if key == CUSTOM_THEME_KEY:
            self.theme_preview.emit(dict(self._custom_theme))
        else:
            self.theme_preview.emit(key)

    # ---------------- AI 接口 ----------------
    def _build_ai_tab(self) -> QWidget:
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)

        prov_card = Card("服务商")
        pf = QFormLayout()
        pf.setSpacing(12)
        pf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pf.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        self.provider_combo = QComboBox()
        for key in PROVIDER_ORDER:
            self.provider_combo.addItem(PROVIDERS[key]["name"], key)
        self.provider_combo.setMinimumWidth(280)
        pf.addRow("选择服务商", self.provider_combo)

        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText(
            "选择「自定义」时填写，如 http://localhost:11434/v1"
        )
        self.base_edit.setMinimumWidth(440)
        pf.addRow("接口地址", self.base_edit)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        self.model_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.model_combo.setMinimumContentsLength(22)
        self.model_combo.setMinimumWidth(360)
        self.model_combo.view().setMinimumWidth(640)
        pf.addRow("模型", self.model_combo)

        self.model_desc = QLabel("")
        self.model_desc.setWordWrap(True)
        self.model_desc.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')}; font-size: 12px;"
        )
        pf.addRow("", self.model_desc)

        model_tip = QLabel("只列文本模型 · 〔免费〕仅供参考 · 也可直接输入模型名")
        model_tip.setWordWrap(True)
        model_tip.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')}; font-size: 12px;"
        )
        pf.addRow("", model_tip)

        prov_card.body().addLayout(pf)
        lay.addWidget(prov_card)

        key_card = Card("API Key", "仅保存在本机，加密存储，不上传")
        kl = QVBoxLayout()
        kl.setSpacing(10)
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("粘贴 API Key，将自动识别可判断的服务商…")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.textChanged.connect(self._on_key_edited)
        key_row.addWidget(self.key_edit, 1)
        self.eye_btn = QPushButton()
        self.eye_btn.setIcon(
            qta.icon("fa5s.eye", color=self._resolve_theme().get("muted", "#9a8e76"))
        )
        self.eye_btn.setFixedWidth(34)
        self.eye_btn.setCheckable(True)
        self.eye_btn.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        key_row.addWidget(self.eye_btn)
        kl.addLayout(key_row)

        self.guess_hint = QLabel("")
        self.guess_hint.setWordWrap(True)
        self.guess_hint.setStyleSheet("font-size: 12px;")
        kl.addWidget(self.guess_hint)

        btn_row = QHBoxLayout()
        self.save_key_btn = QPushButton("保存配置")
        self.save_key_btn.setProperty("variant", "primary")
        self.save_key_btn.clicked.connect(self._save_key)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.save_key_btn)
        btn_row.addWidget(self.test_btn)
        btn_row.addStretch()
        kl.addLayout(btn_row)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        kl.addWidget(self.test_result)
        key_card.body().addLayout(kl)
        lay.addWidget(key_card)

        temp_card = Card("AI 创作温度")
        tl = QVBoxLayout()
        tl.setSpacing(8)

        temp_row = QHBoxLayout()
        temp_row.setSpacing(10)
        lab_lo = QLabel("保守")
        lab_lo.setStyleSheet(f"color: {self._resolve_theme().get('muted', '#8a8070')};")
        temp_row.addWidget(lab_lo)

        self.ai_temp_slider = QSlider(Qt.Horizontal)
        self.ai_temp_slider.setRange(0, 100)
        self.ai_temp_slider.setSingleStep(5)
        self.ai_temp_slider.setPageStep(10)
        self.ai_temp_slider.setValue(85)
        self.ai_temp_slider.setTickPosition(QSlider.TicksBelow)
        self.ai_temp_slider.setTickInterval(25)
        self.ai_temp_slider.valueChanged.connect(self._on_ai_temp_changed)
        temp_row.addWidget(self.ai_temp_slider, 1)

        lab_hi = QLabel("生动")
        lab_hi.setStyleSheet(f"color: {self._resolve_theme().get('muted', '#8a8070')};")
        temp_row.addWidget(lab_hi)

        self.ai_temp_value_label = QLabel("0.85")
        self.ai_temp_value_label.setFixedWidth(48)
        self.ai_temp_value_label.setAlignment(Qt.AlignCenter)
        self.ai_temp_value_label.setStyleSheet(
            f"font-weight: bold; color: {self._resolve_theme().get('accent', '#b08a4e')};"
        )
        temp_row.addWidget(self.ai_temp_value_label)
        tl.addLayout(temp_row)

        temp_tip = QLabel(
            "只影响创作类任务：AI 对话、续写、改写对比、小说转剧本。\n"
            "提取词条 / 人物关系 / 伏笔、追读体检、梗概 不受影响"
            "（那些任务需要稳定输出，已固定为低温度）。\n"
            "调低更严谨保守，调高更有想象力，但也可能跑偏。"
        )
        temp_tip.setWordWrap(True)
        temp_tip.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')};"
            f" font-size: 12px;"
        )
        tl.addWidget(temp_tip)
        temp_card.body().addLayout(tl)
        lay.addWidget(temp_card)

        system_card = Card("系统提示词", "AI 的角色设定")
        sl = QVBoxLayout()
        sl.setSpacing(8)
        self.system_prompt_edit = QTextEdit()
        self.system_prompt_edit.setPlaceholderText(
            "留空则使用内置默认（网文编辑与写作顾问）。\n"
            "可自定义 AI 的语气、身份、回答风格，例如：\n"
            "「你是我的专属网文编辑，回答直接给结论，不要客套。」"
        )
        self.system_prompt_edit.setMinimumHeight(110)
        sl.addWidget(self.system_prompt_edit)
        tip_sp = QLabel("修改后从下一条消息开始生效；不影响已进行的对话历史。")
        tip_sp.setWordWrap(True)
        tip_sp.setStyleSheet(
            f"color: {self._resolve_theme().get('muted', '#8a8070')}; font-size: 12px;"
        )
        sl.addWidget(tip_sp)
        system_card.body().addLayout(sl)
        lay.addWidget(system_card)

        self.guide = QLabel("")
        self.guide.setTextFormat(Qt.RichText)
        self.guide.setOpenExternalLinks(True)
        self.guide.setWordWrap(True)
        self.guide.setStyleSheet("color: #777; font-size: 12px;")
        lay.addWidget(self.guide)
        lay.addStretch()
        self._apply_provider_ui("deepseek")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        return self._scroll_wrap(content)

    def _on_ai_temp_changed(self, value: int):
        """滑条 0-100 映射到 0.00-1.00。"""
        self.ai_temp_value_label.setText(f"{value / 100:.2f}")

    def _on_provider_changed(self):
        if self._loading:
            return
        provider = self.provider_combo.currentData()
        saved = get_provider_saved_config(provider)
        self._saved_key = ""
        self._apply_provider_ui(provider, saved["base_url"], saved["model"])
        self._reload_key_for_provider(provider)
        # 不在此处清 guess_hint —— _reload_key_for_provider 已经根据
        # key_error / 已保存 / 未配置三种情况给出了合适的提示，
        # 再清空会把"原 Key 无法解密"的警告抹掉。

    def _reload_key_for_provider(self, provider: str):
        saved = get_provider_saved_config(provider)
        masked = saved.get("masked", "")
        self._loading = True
        self.key_edit.setText(masked)
        self._loading = False

        key_error = saved.get("key_error", "")
        if key_error:
            # 解密失败：环境变了（换网卡 / 改主机名 / salt.bin 丢失）。
            # 明示原因并引导用户重输，而不是装作"从未配置过"。
            self.key_edit.setPlaceholderText("原 Key 无法解密，请重新粘贴")
            self.guess_hint.setStyleSheet("color:#e6a23c; font-size:12px;")
            self.guess_hint.setText(
                f"⚠ 已保存的 API Key 无法解密（{key_error}）。\n"
                "常见于更换网卡 / 主机名、重装系统后 salt.bin 丢失。\n"
                "请重新粘贴一次 API Key，点「保存配置」覆盖。"
            )
        elif masked:
            self.key_edit.setPlaceholderText("已保存（粘贴新 Key 可覆盖）")
            self.guess_hint.setStyleSheet("")
            self.guess_hint.setText("")
        else:
            self.key_edit.setPlaceholderText("粘贴 API Key，将自动识别可判断的服务商…")
            self.guess_hint.setStyleSheet("")
            self.guess_hint.setText("")

    def _apply_provider_ui(self, key: str, base_url: str = "", model: str = ""):
        p = PROVIDERS[key]
        idx = self.provider_combo.findData(key)
        if idx >= 0 and self.provider_combo.currentIndex() != idx:
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(idx)
            self.provider_combo.blockSignals(False)
        custom = key == "custom"
        self.base_edit.setReadOnly(not custom)
        theme = self._resolve_theme()
        muted = theme.get("muted", "#9a8e76")
        editor_bg = theme.get("editor_bg", "#fcfaf4")
        if custom:
            self.base_edit.setStyleSheet("")
        else:
            self.base_edit.setStyleSheet(f"background:{editor_bg}; color:{muted};")
        self.base_edit.setText(base_url or p["base_url"])

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for m in p["models"]:
            self.model_combo.addItem(model_display_text(m), m["id"])
        if model:
            idx = self.model_combo.findData(model)
            if idx < 0:
                self.model_combo.addItem(model, None)
                idx = self.model_combo.count() - 1
            self.model_combo.setCurrentIndex(idx)
        elif p["models"]:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self._sync_model_desc()
        QTimer.singleShot(0, self._normalize_model_edit)

        self._update_guide()

    def _current_model_id(self) -> str:
        text = self.model_combo.currentText().strip()
        data = self.model_combo.currentData()
        if data and text == data.strip():
            return data.strip()
        return text

    def _sync_model_desc(self):
        key = self.provider_combo.currentData()
        data = self.model_combo.currentData()
        mid = (data or self.model_combo.currentText() or "").strip()
        m = find_model(key, mid) if mid else None
        if m:
            color = (
                "#67c23a"
                if m.get("free")
                else self._resolve_theme().get("muted", "#9a8e76")
            )
            self.model_desc.setText(
                f"<span style='color:{color}; font-weight:bold;'>"
                f"〔{model_tag(m)}〕</span> 擅长：{m.get('skill', '')}"
            )
        elif mid:
            self.model_desc.setText(
                "自定义模型（不在预设候选中，请确认名称与服务商一致）"
            )
        else:
            self.model_desc.setText("")
        QTimer.singleShot(0, self._normalize_model_edit)

    def _normalize_model_edit(self):
        data = self.model_combo.currentData()
        if data and self.model_combo.currentText().strip() != data.strip():
            self.model_combo.setEditText(data)

    def _on_model_changed(self):
        if self._loading:
            return
        self._sync_model_desc()

    def _update_guide(self):
        key = self.provider_combo.currentData()
        p = PROVIDERS[key]
        if p["docs"]:
            self.guide.setText(
                f"如何获取 Key：<a href='{p['docs']}'>{p['hint']}</a><br>"
                "AI 按 token 由服务商直接计费，本软件不收取任何费用。"
            )
        else:
            self.guide.setText(
                f"{p['hint']}<br>端点需支持 OpenAI Chat Completions 协议，"
                "Key 按服务商要求填写（本地部署可任意填写）。"
            )

    def _on_key_edited(self, text: str):
        if self._loading or "•" in text:
            return
        t = text.strip()
        guess = guess_provider(t)
        if guess:
            if self.provider_combo.currentData() != guess:
                idx = self.provider_combo.findData(guess)
                if idx >= 0:
                    self.provider_combo.setCurrentIndex(idx)
            self.guess_hint.setStyleSheet("color:#67c23a; font-size:12px;")
            self.guess_hint.setText(
                f"✓ 已根据 Key 特征识别为「{PROVIDERS[guess]['name']}」，如识别有误可手动更改。"
            )
        elif t.lower().startswith("sk-"):
            self.guess_hint.setStyleSheet("color:#e6a23c; font-size:12px;")
            self.guess_hint.setText(
                "提示：sk- 前缀被 DeepSeek、Kimi、通义等多家使用，"
                "无法仅凭 Key 区分，请确认上方服务商选择。"
            )
        else:
            # 清空文字时同步清样式表，避免上次"解密失败"的橙色残留
            self.guess_hint.setStyleSheet("")
            self.guess_hint.setText("")

    def _collect_ai(self) -> dict:
        provider = self.provider_combo.currentData()
        text = self.key_edit.text().strip()
        if text and "•" not in text:
            key = text
        else:
            key = load_ai_config_for_provider(provider).get("api_key", "")
        return {
            "api_key": key,
            "provider": provider,
            "base_url": self.base_edit.text().strip(),
            "model": self._current_model_id(),
        }

    def _save_key(self):
        c = self._collect_ai()
        if not c["api_key"]:
            QMessageBox.warning(self, "提示", "请输入 API Key。")
            return
        if c["provider"] == "custom" and (not c["base_url"] or not c["model"]):
            QMessageBox.warning(self, "提示", "自定义端点需要填写接口地址和模型名。")
            return
        save_ai_config(
            c["api_key"],
            c["base_url"],
            c["model"],
            c["provider"],
            system_prompt=self.system_prompt_edit.toPlainText().strip(),
        )
        self._saved_key = c["api_key"]
        self._reload_key_for_provider(c["provider"])
        self.guess_hint.setText("")
        self.test_result.setStyleSheet("color: #67c23a;")
        self.test_result.setText("✓ 配置已加密保存到本机。")

    def _test_connection(self):
        c = self._collect_ai()
        if not c["api_key"]:
            QMessageBox.warning(self, "提示", "请先填写 API Key。")
            return
        if c["provider"] == "custom" and (not c["base_url"] or not c["model"]):
            QMessageBox.warning(self, "提示", "自定义端点需要填写接口地址和模型名。")
            return
        self.test_result.setStyleSheet(
            f"font-size: 14px; color: {self._resolve_theme().get('muted', '#9a8e76')};"
        )
        self.test_result.setText(f"正在连接 {PROVIDERS[c['provider']]['name']} …")
        self.test_btn.setEnabled(False)
        self._test_worker = AIWorker(
            [{"role": "user", "content": "请只回复两个字：在的"}],
            c["api_key"],
            c["base_url"],
            c["model"],
            temperature=0.1,
        )
        self._test_worker.finished_ok.connect(self._on_test_ok)
        self._test_worker.failed.connect(self._on_test_fail)
        self._test_worker.start()

    def _on_test_ok(self, text: str):
        self.test_btn.setEnabled(True)
        self.test_result.setStyleSheet("color: #67c23a;")
        self.test_result.setText(f"✓ 连接成功，模型回复：{text[:30]}")

    def _on_test_fail(self, msg: str):
        self.test_btn.setEnabled(True)
        self.test_result.setStyleSheet("color: #e6a23c;")
        self.test_result.setText(f"✗ {msg}")

    # ---------------- 墨灵 ----------------
    _PET_DATA_FILES = ("pet_data.json", "pet_memory.json", "pet_economy.json")

    def _pet_data_root(self) -> str:
        """墨灵存档目录：优先取运行中 workspace 的 store.data_root。"""
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                store = getattr(ws, "store", None)
                root = getattr(store, "data_root", "")
                if root:
                    return root
                break
            p = p.parent()
        return getattr(self.cfg, "data_dir", "") or ""

    def _find_pet_float(self):
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                return getattr(ws, "_pet_float", None)
            p = p.parent()
        return None

    def _build_pet_tab(self) -> QWidget:
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        outer.addWidget(
            self._build_page_header(
                "fa5s.paw", "墨灵",
                "住在你书里的小伙伴：陪你码字、记得你的写作习惯"
            )
        )

        # ---------- 陪伴体验 ----------
        exp_card = Card("陪伴体验")
        el = exp_card.body()
        el.setSpacing(10)

        self.pet_roam_check = QCheckBox("空闲时自主漫游")
        self.pet_roam_check.setToolTip("关闭后墨灵只会安静待着，不自己走动")
        el.addWidget(SettingRow("空闲漫游", self.pet_roam_check,
                                "开启后墨灵会在空闲时自己走动、发呆、打盹"))

        self.pet_proactive_check = QCheckBox("主动搭话")
        self.pet_proactive_check.setToolTip(
            "写完一章、写不下去或久坐时，墨灵会主动开口")
        el.addWidget(SettingRow(
            "主动搭话", self.pet_proactive_check,
            "写完一章、写不下去时墨灵主动开口；判断在本地完成，"
            "不额外消耗 token，点她的气泡发起对话才会调用 AI"))

        self.pet_chat_memory_check = QCheckBox("对话记忆")
        self.pet_chat_memory_check.setToolTip(
            "从聊天里偶尔提炼写作偏好、计划和设定，长期记住")
        el.addWidget(SettingRow(
            "对话记忆", self.pet_chat_memory_check,
            "每隔几轮对话自动提炼一次偏好与设定（每天最多 4 次，"
            "只发送简短摘要，消耗少量 token）；"
            "直接对墨灵说「记住，……」则零消耗记一条"))

        outer.addWidget(exp_card)

        # ---------- 记忆与数据 ----------
        data_card = Card("记忆与数据",
                         "墨玉、道具、亲密度与记忆都只保存在本机这三个文件里，"
                         "不会上传。删除后墨灵会回到初次见面的状态。")
        dl = data_card.body()
        dl.setSpacing(10)

        self.pet_files_label = QLabel()
        self.pet_files_label.setWordWrap(True)
        self.pet_files_label.setObjectName("cardDesc")
        self.pet_files_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self.pet_files_label)
        self._refresh_pet_files_label()

        btn_w = QWidget()
        btn_row = QHBoxLayout(btn_w)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        open_dir_btn = QPushButton("打开存档文件夹")
        open_dir_btn.clicked.connect(self._open_pet_data_dir)
        reset_btn = QPushButton("重置墨灵数据")
        reset_btn.clicked.connect(self._reset_pet_data)
        btn_row.addWidget(open_dir_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        dl.addWidget(btn_w)

        outer.addWidget(data_card)
        outer.addStretch(1)
        return self._scroll_wrap(content)

    def _refresh_pet_files_label(self):
        root = self._pet_data_root()
        if not root:
            self.pet_files_label.setText("存档目录未知")
            return
        lines = [f"目录：{root}"]
        for name in self._PET_DATA_FILES:
            p = os.path.join(root, name)
            if os.path.exists(p):
                try:
                    size = os.path.getsize(p)
                    lines.append(f"{name}（{size / 1024:.1f} KB）")
                except OSError:
                    lines.append(f"{name}（存在）")
            else:
                lines.append(f"{name}（尚未生成）")
        self.pet_files_label.setText("　".join(lines[:1]) + "\n" + "　".join(lines[1:]))

    def _open_pet_data_dir(self):
        root = self._pet_data_root()
        if not root:
            QMessageBox.information(self, "提示", "暂时找不到存档目录。")
            return
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    def _reset_pet_data(self):
        root = self._pet_data_root()
        existing = [
            os.path.join(root, n) for n in self._PET_DATA_FILES
            if root and os.path.exists(os.path.join(root, n))
        ]
        if not existing:
            QMessageBox.information(self, "重置墨灵数据", "还没有任何墨灵存档，无需重置。")
            return
        ret = QMessageBox.warning(
            self,
            "重置墨灵数据",
            "将删除墨玉、道具、亲密度以及墨灵记住的全部内容，\n"
            "墨灵会回到初次见面的状态。此操作不可恢复。\n\n"
            "确定继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        failed = []
        for p in existing:
            try:
                os.remove(p)
            except OSError as e:
                failed.append(f"{os.path.basename(p)}: {e}")
        self._refresh_pet_files_label()
        if failed:
            QMessageBox.warning(
                self, "部分文件删除失败",
                "以下文件未能删除：\n" + "\n".join(failed)
                + "\n请关闭软件后手动删除。",
            )
        else:
            QMessageBox.information(
                self, "已重置",
                "墨灵数据已清空。重启软件后墨灵将以全新状态出现。",
            )

    # ---------------- 通用 ----------------
    def _build_general_tab(self) -> QWidget:
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)

        window_card = Card("窗口与托盘")
        wf = QFormLayout()
        wf.setSpacing(12)
        wf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        wf.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.tray_combo = QComboBox()
        self.tray_combo.addItems(
            ["点击关闭：最小化到托盘", "点击关闭：退出程序", "每次询问"]
        )
        self.tray_combo.setMinimumWidth(280)
        wf.addRow("关闭主窗口时", self.tray_combo)
        window_card.body().addLayout(wf)
        lay.addWidget(window_card)

        save_card = Card("自动保存")
        sf = QFormLayout()
        sf.setSpacing(12)
        sf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sf.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.save_path_edit = QLineEdit()
        self.save_path_edit.setPlaceholderText("默认：我的文档/墨码写作")
        self.save_path_edit.setReadOnly(True)
        self.save_path_edit.setMinimumWidth(300)
        browse_btn = QPushButton("选择文件夹")
        browse_btn.clicked.connect(self._pick_save_path)
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_save_path)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(self.save_path_edit, 1)
        path_row.addWidget(browse_btn)
        path_row.addWidget(reset_btn)
        path_row.addStretch()
        sf.addRow("作品存储位置", path_row)

        self.save_debounce_spin = QSpinBox()
        self.save_debounce_spin.setRange(1, 30)
        self.save_debounce_spin.setSuffix(" 秒")
        self.save_debounce_spin.setMinimumWidth(120)
        self.save_debounce_spin.setMaximumWidth(160)
        self.save_debounce_spin.setToolTip(
            "停止输入后多久自动保存（秒）\n建议 2-5 秒，短则更安全但略增写入"
        )
        sf.addRow("停顿后自动保存", self.save_debounce_spin)

        self.save_tick_spin = QSpinBox()
        self.save_tick_spin.setRange(5, 300)
        self.save_tick_spin.setSuffix(" 秒")
        self.save_tick_spin.setMinimumWidth(120)
        self.save_tick_spin.setMaximumWidth(160)
        self.save_tick_spin.setToolTip(
            "无论是否在输入，每隔多久兜底保存一次（秒）\n建议 30-60 秒"
        )
        sf.addRow("定时兜底保存", self.save_tick_spin)

        hint = QLabel(
            "存储位置用于作品文件、历史快照和码字统计；修改后需重启软件生效。\n"
            "停顿保存越短越安全但写入更频繁；定时兜底确保即使不停顿也不会丢失太多。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"font-size: 12px; color: {self._resolve_theme().get('muted', '#8a8070')};"
        )
        sf.addRow(hint)
        save_card.body().addLayout(sf)
        lay.addWidget(save_card)

        # ---- 自动备份：数据安全铁律的落地（定时 zip + 保留份数） ----
        ab_card = Card("自动备份")
        af = QFormLayout()
        af.setSpacing(12)
        af.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        af.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.auto_backup_check = QCheckBox(
            "开启自动备份（启动软件时检查，到期才执行，后台压缩不影响写作）")
        af.addRow(self.auto_backup_check)

        ab_row = QHBoxLayout()
        ab_row.setSpacing(8)
        self.auto_backup_interval = QComboBox()
        self.auto_backup_interval.addItems(["每天", "每周"])
        self.auto_backup_interval.setMinimumWidth(100)
        self.auto_backup_keep = QSpinBox()
        self.auto_backup_keep.setRange(1, 30)
        self.auto_backup_keep.setSuffix(" 份")
        self.auto_backup_keep.setMinimumWidth(90)
        self.auto_backup_keep.setMaximumWidth(120)
        ab_row.addWidget(QLabel("频率"))
        ab_row.addWidget(self.auto_backup_interval)
        ab_row.addSpacing(12)
        ab_row.addWidget(QLabel("保留"))
        ab_row.addWidget(self.auto_backup_keep)
        ab_row.addStretch()
        af.addRow("备份策略", ab_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.backup_now_btn = QPushButton("立即备份一次")
        self.backup_now_btn.setToolTip("在后台立刻生成一份备份 zip")
        self.backup_now_btn.clicked.connect(self._backup_now)
        self.open_backup_dir_btn = QPushButton("打开备份目录")
        self.open_backup_dir_btn.clicked.connect(self._open_backup_dir)
        btn_row.addWidget(self.backup_now_btn)
        btn_row.addWidget(self.open_backup_dir_btn)
        btn_row.addStretch()
        af.addRow(btn_row)

        ab_hint = QLabel(
            "备份为 zip，存在「数据目录 / auto_backups」，包含全部作品、历史快照、"
            "词库与统计；不含 API Key。\n"
            "超出保留份数的旧备份会自动删除；手动打包仍可用「作品 → 备份所有作品」。"
        )
        ab_hint.setWordWrap(True)
        ab_hint.setStyleSheet(
            f"font-size: 12px; color: {self._resolve_theme().get('muted', '#8a8070')};"
        )
        af.addRow(ab_hint)
        ab_card.body().addLayout(af)
        lay.addWidget(ab_card)

        reminder_card = Card("久坐提醒")
        rf = QFormLayout()
        rf.setSpacing(12)
        rf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rf.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.reminder_check = QCheckBox("开启定时休息提醒")
        rf.addRow(self.reminder_check)
        self.reminder_interval = QComboBox()
        self.reminder_interval.addItems(["30分钟", "1小时", "2小时", "4小时", "自定义"])
        self.reminder_interval.setMinimumWidth(160)
        rf.addRow("提醒间隔", self.reminder_interval)
        self.reminder_custom = QSpinBox()
        self.reminder_custom.setRange(5, 240)
        self.reminder_custom.setSuffix(" 分钟")
        self.reminder_custom.setMinimumWidth(120)
        self.reminder_custom.setMaximumWidth(160)
        rf.addRow("自定义分钟数", self.reminder_custom)
        self.reminder_msg = QLineEdit()
        self.reminder_msg.setPlaceholderText("起来活动一下")
        self.reminder_msg.setMinimumWidth(300)
        rf.addRow("提醒语", self.reminder_msg)
        reminder_card.body().addLayout(rf)
        lay.addWidget(reminder_card)

        lay.addStretch()
        return self._scroll_wrap(content)

    def _backup_now(self):
        """设置页「立即备份一次」：找主窗口发起后台自动备份。"""
        p = self.parent()
        while p is not None:
            if hasattr(p, "_run_auto_backup"):
                try:
                    started = p._run_auto_backup()
                    if started:
                        QMessageBox.information(
                            self, "自动备份",
                            "已在后台开始备份，完成后会有提示。")
                    else:
                        # 返回 False 只可能是上一次备份仍在压缩，
                        # 不能装作已经开始，否则用户以为备份成功了。
                        QMessageBox.information(
                            self, "自动备份",
                            "已有备份正在进行，请等待其完成后再试。")
                except Exception as e:
                    logger.warning(f"触发立即备份失败: {e}")
                    QMessageBox.warning(
                        self, "自动备份", "暂时无法开始备份，请稍后重试。")
                return
            p = p.parent()

    def _open_backup_dir(self):
        try:
            from utils.auto_backup import backup_dir_for

            path = backup_dir_for(self.cfg.data_dir)
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            logger.warning(f"打开备份目录失败: {e}")

    def _check_update_now(self):
        p = self.parent()
        while p is not None:
            if hasattr(p, "_manual_check_update"):
                try:
                    p._manual_check_update()
                except Exception as e:
                    logger.warning(f"触发手动检查更新失败: {e}")
                    QMessageBox.warning(
                        self, "检查更新失败", "无法发起更新检查，请稍后重试。"
                    )
                return
            p = p.parent()
        QMessageBox.information(
            self,
            "检查更新",
            f"当前版本 v{APP_VERSION}。\n请通过「帮助 → 检查更新」菜单手动检查。",
        )

    # ---------------- 页头 ----------------
    def _build_page_header(self, icon_name: str, title: str, subtitle: str):
        """各设置页顶部统一的小标题条，返回 (QWidget 外层, 状态 QLabel)。"""
        wrap = QWidget()
        head = QHBoxLayout(wrap)
        head.setContentsMargins(4, 2, 4, 0)
        head.setSpacing(10)
        accent = self._resolve_accent()
        head_icon = QLabel()
        try:
            head_icon.setPixmap(qta.icon(icon_name, color=accent).pixmap(20, 20))
        except Exception:
            pass
        head.addWidget(head_icon)
        box = QVBoxLayout()
        box.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {accent};")
        box.addWidget(t)
        s = QLabel(subtitle)
        s.setObjectName("cardDesc")
        box.addWidget(s)
        head.addLayout(box)
        head.addStretch()
        return wrap

    # ---------------- 关于 ----------------
    def _build_about_tab(self) -> QWidget:
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        muted = self._resolve_theme().get("muted", "#9a8e76")

        lay.addWidget(
            self._build_page_header(
                "fa5s.pen-nib", "墨码写作",
                f"沉浸式网文写作工具 · v{APP_VERSION} · 本地运行，稿子不上云"
            )
        )

        # 软件更新
        update_card = Card("软件更新")
        ul = update_card.body()
        ul.setSpacing(10)
        self.auto_update_check = QCheckBox("启动时自动检查更新")
        self.auto_update_check.setToolTip(
            "启动后 5 秒静默检查，每天最多一次；\n"
            "有新版本时会弹窗提示。检查失败不打扰。\n"
            "关闭后仍可在「帮助 → 检查更新」手动触发。"
        )
        ul.addWidget(self.auto_update_check)

        update_row_w = QWidget()
        update_row = QHBoxLayout(update_row_w)
        update_row.setContentsMargins(0, 0, 0, 0)
        update_row.setSpacing(8)
        self.current_version_label = QLabel(f"当前版本 v{APP_VERSION}")
        self.current_version_label.setObjectName("cardDesc")
        update_row.addWidget(self.current_version_label)
        update_row.addStretch()
        self.check_update_btn = QPushButton("立即检查更新")
        self.check_update_btn.setToolTip("忽略每日限制，立即发起一次检查")
        self.check_update_btn.clicked.connect(self._check_update_now)
        update_row.addWidget(self.check_update_btn)
        ul.addWidget(update_row_w)

        update_hint = QLabel(
            "更新检查请求只发送当前版本号，不含任何个人数据；"
            "所有稿件仍保存在本机。"
        )
        update_hint.setWordWrap(True)
        update_hint.setObjectName("cardDesc")
        ul.addWidget(update_hint)
        lay.addWidget(update_card)

        # 赞助
        sponsor_card = Card("赞助作者", "墨码写作全部功能免费开放、持续更新，如果它对你的写作有帮助，欢迎赞助支持")
        sponsor_row = QHBoxLayout()
        sponsor_row.addStretch()
        sponsor_btn = QPushButton(qta.icon("fa5s.heart", color="#e91e63"), " 爱发电赞助")
        sponsor_btn.setCursor(Qt.PointingHandCursor)
        sponsor_btn.setToolTip("打开爱发电主页，支持作者持续开发")
        sponsor_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AFDIAN_HOME)))
        sponsor_row.addWidget(sponsor_btn)
        sponsor_card.body().addLayout(sponsor_row)
        lay.addWidget(sponsor_card)

        # 联系作者
        contact_card = Card("联系作者", "问题反馈 / 写作交流 / 功能建议")
        cl = QHBoxLayout()
        cl.setSpacing(18)
        for icon_name, text in (
            ("fa5b.qq", f"QQ {CONTACT_QQ}"),
            ("fa5b.weixin", f"微信 {CONTACT_WECHAT}"),
        ):
            sub = QHBoxLayout()
            sub.setSpacing(6)
            ico = QLabel()
            try:
                ico.setPixmap(qta.icon(icon_name, color=muted).pixmap(14, 14))
            except Exception:
                pass
            sub.addWidget(ico)
            lab = QLabel(text)
            lab.setObjectName("cardDesc")
            sub.addWidget(lab)
            cl.addLayout(sub)
        mail_sub = QHBoxLayout()
        mail_sub.setSpacing(6)
        mail_ico = QLabel()
        try:
            mail_ico.setPixmap(qta.icon("fa5s.envelope", color=muted).pixmap(14, 14))
        except Exception:
            pass
        mail_sub.addWidget(mail_ico)
        mail_lab = QLabel(
            f"<a href='mailto:{CONTACT_EMAIL}' style='color:{muted};'>"
            f"{CONTACT_EMAIL}</a>"
        )
        mail_lab.setOpenExternalLinks(True)
        mail_sub.addWidget(mail_lab)
        cl.addLayout(mail_sub)
        cl.addStretch()
        contact_card.body().addLayout(cl)
        lay.addWidget(contact_card)

        lay.addStretch()
        return self._scroll_wrap(content)

    def _pick_save_path(self):
        cur = self.save_path_edit.text()
        start = cur if cur else ""
        path = QFileDialog.getExistingDirectory(self, "选择作品存储位置", start)
        if path:
            self.save_path_edit.setText(path)

    def _reset_save_path(self):
        self.save_path_edit.setText("")

    # ---------------- 读写配置 ----------------
    def _load_values(self):
        get = lambda k, d: self.cfg.get_config("Writing", k, d)
        self.target_spin.setValue(int(get("daily_target", "4000") or 4000))
        self._custom_theme = load_custom_theme(self.cfg)
        self._refresh_color_buttons()
        idx = self.theme_combo.findData(get("theme", "paper"))
        self.theme_combo.setCurrentIndex(max(0, idx))
        family = get("font_family", "Microsoft YaHei")
        # 默认字体保持微软雅黑（屏幕阅读体验最稳），
        # 但字体下拉框顶部先列几个"印刷体"，让想换的用户一眼看到。
        fi = self.font_combo.findText(family)
        if fi >= 0:
            self.font_combo.setCurrentIndex(fi)
        else:
            self.font_combo.setEditText(family)
        self.font_size.setValue(int(get("font_size", "15")))
        self.line_spacing.setValue(float(get("line_spacing", "1.8")))

        # 正文宽度：优先匹配预设档位，不在预设里则追加"自定义"
        try:
            cw = int(get("content_width", "720") or 720)
        except (TypeError, ValueError):
            cw = 720
        cw_idx = self.content_width_combo.findData(str(cw))
        if cw_idx < 0:
            self.content_width_combo.addItem(f"自定义（{cw}px）", str(cw))
            cw_idx = self.content_width_combo.count() - 1
        self.content_width_combo.setCurrentIndex(cw_idx)

        pi = self.para_indent_combo.findData(get("para_indent", "full"))
        self.para_indent_combo.setCurrentIndex(max(0, pi))
        self.typewriter_check.setChecked(get("typewriter", "false").lower() == "true")
        self.paragraph_focus_check.setChecked(
            get("paragraph_focus", "false").lower() == "true")
        self.auto_indent_check.setChecked(get("auto_indent", "true").lower() == "true")
        self.auto_quote_pair_check.setChecked(
            get("auto_quote_pair", "true").lower() == "true"
        )
        self.tab_insert_check.setChecked(get("tab_insert", "true").lower() == "true")
        self.dict_highlight_check.setChecked(
            get("dict_highlight", "true").lower() == "true"
        )
        self.tray_combo.setCurrentText(
            self.cfg.get_config("Writing", "tray_behavior", "点击关闭：最小化到托盘")
        )

        idx = self.ui_font_combo.findData(self.cfg.get_config("UI", "font_pt", "10"))
        self.ui_font_combo.setCurrentIndex(max(0, idx))

        # AI 输出字号
        try:
            n = int(self.cfg.get_config("AI", "output_font_size", "14"))
        except (TypeError, ValueError):
            n = 14
        self.ai_font_size_spin.setValue(max(12, min(20, n)))

        self.save_path_edit.setText(self.cfg.get_config("Storage", "data_dir", ""))
        self.save_debounce_spin.setValue(
            int(self.cfg.get_config("Storage", "save_debounce_sec", "3"))
        )
        self.save_tick_spin.setValue(
            int(self.cfg.get_config("Storage", "save_tick_sec", "30"))
        )

        self.reminder_check.setChecked(
            self.cfg.get_config("Reminder", "enabled", "false").lower() == "true"
        )
        self.reminder_interval.setCurrentText(
            self.cfg.get_config("Reminder", "interval", "1小时")
        )
        self.reminder_custom.setValue(
            int(self.cfg.get_config("Reminder", "custom_minutes", "60"))
        )
        self.reminder_msg.setText(
            self.cfg.get_config("Reminder", "message", "起来活动一下")
        )

        self.auto_backup_check.setChecked(
            self.cfg.get_config("Backup", "auto_backup", "true").lower()
            == "true"
        )
        self.auto_backup_interval.setCurrentText(
            self.cfg.get_config("Backup", "auto_interval", "每周")
        )
        try:
            keep_n = int(self.cfg.get_config("Backup", "auto_keep", "3"))
        except (TypeError, ValueError):
            keep_n = 3
        self.auto_backup_keep.setValue(max(1, min(30, keep_n)))

        self.auto_update_check.setChecked(
            self.cfg.get_config("Update", "auto_check", "true").lower() == "true"
        )

        # 墨灵
        def _pet_bool(key, default):
            v = str(self.cfg.get_config("Pet", key, default) or default)
            return v.strip() not in ("0", "false", "False", "no")

        self.pet_roam_check.setChecked(_pet_bool("roam_enabled", "1"))
        self.pet_proactive_check.setChecked(
            _pet_bool("proactive_talk", "1"))
        self.pet_chat_memory_check.setChecked(
            _pet_bool("chat_memory", "1"))

        cfg_ai = load_ai_config()
        self._loading = True
        self._apply_provider_ui(cfg_ai["provider"], cfg_ai["base_url"], cfg_ai["model"])
        self._loading = False
        self._saved_key = cfg_ai["api_key"]
        self._reload_key_for_provider(cfg_ai["provider"])
        self.system_prompt_edit.setPlainText(cfg_ai.get("system_prompt", ""))
        # 创作温度滑条
        try:
            t = float(cfg_ai.get("creative_temperature", 0.85))
        except (TypeError, ValueError):
            t = 0.85
        self.ai_temp_slider.setValue(int(round(max(0.0, min(1.0, t)) * 100)))

    def accept(self):
        # 存储位置是否发生变更（保存前取旧值，保存后再决定迁移与重启提示）
        storage_change = None
        try:
            from utils.config import default_config_dir
            from utils.work_store import get_data_root
            old_root = os.path.abspath(get_data_root())
            new_text = self.save_path_edit.text().strip()
            new_root = os.path.abspath(
                new_text if new_text else default_config_dir())
            if os.path.normcase(old_root) != os.path.normcase(new_root):
                storage_change = (old_root, new_root)
        except Exception:
            logger.warning("存储位置变更检测失败", exc_info=True)

        configs = [
            ("Writing", "daily_target", self.target_spin.value()),
            ("Writing", "theme", self.theme_combo.currentData()),
            (
                "Writing",
                "custom_theme",
                json.dumps(self._custom_theme, ensure_ascii=False),
            ),
            (
                "Writing",
                "font_family",
                self.font_combo.currentData() or self.font_combo.currentText(),
            ),
            ("Writing", "font_size", self.font_size.value()),
            ("Writing", "line_spacing", round(self.line_spacing.value(), 1)),
            (
                "Writing",
                "content_width",
                self.content_width_combo.currentData() or "720",
            ),
            ("Writing", "para_indent", self.para_indent_combo.currentData()),
            (
                "Writing",
                "typewriter",
                "true" if self.typewriter_check.isChecked() else "false",
            ),
            (
                "Writing",
                "paragraph_focus",
                "true" if self.paragraph_focus_check.isChecked() else "false",
            ),
            (
                "Writing",
                "auto_indent",
                "true" if self.auto_indent_check.isChecked() else "false",
            ),
            (
                "Writing",
                "auto_quote_pair",
                "true" if self.auto_quote_pair_check.isChecked() else "false",
            ),
            (
                "Writing",
                "tab_insert",
                "true" if self.tab_insert_check.isChecked() else "false",
            ),
            (
                "Writing",
                "dict_highlight",
                "true" if self.dict_highlight_check.isChecked() else "false",
            ),
            ("Writing", "tray_behavior", self.tray_combo.currentText()),
            ("UI", "font_pt", self.ui_font_combo.currentData() or "10"),
            ("AI", "output_font_size", str(self.ai_font_size_spin.value())),
            ("Storage", "data_dir", self.save_path_edit.text().strip()),
            ("Storage", "save_debounce_sec", str(self.save_debounce_spin.value())),
            ("Storage", "save_tick_sec", str(self.save_tick_spin.value())),
            (
                "Reminder",
                "enabled",
                "true" if self.reminder_check.isChecked() else "false",
            ),
            ("Reminder", "interval", self.reminder_interval.currentText()),
            ("Reminder", "custom_minutes", str(self.reminder_custom.value())),
            ("Reminder", "message", self.reminder_msg.text() or "起来活动一下"),
            (
                "Backup",
                "auto_backup",
                "true" if self.auto_backup_check.isChecked() else "false",
            ),
            ("Backup", "auto_interval", self.auto_backup_interval.currentText()),
            ("Backup", "auto_keep", str(self.auto_backup_keep.value())),
            (
                "Update",
                "auto_check",
                "true" if self.auto_update_check.isChecked() else "false",
            ),
            (
                "Pet",
                "roam_enabled",
                "1" if self.pet_roam_check.isChecked() else "0",
            ),
            (
                "Pet",
                "proactive_talk",
                "1" if self.pet_proactive_check.isChecked() else "0",
            ),
            (
                "Pet",
                "chat_memory",
                "1" if self.pet_chat_memory_check.isChecked() else "0",
            ),
        ]
        self._hotkeys.save()
        self.cfg.batch_set_config(configs, validate=False)

        if storage_change is not None:
            self._handle_storage_change(storage_change[0], storage_change[1])

        # 立即让运行中的墨灵应用新设置（漫游 / 主动搭话等）
        pet_float = self._find_pet_float()
        if pet_float is not None:
            try:
                pet_float.refresh_pet_settings()
            except Exception as e:
                logger.warning(f"应用墨灵设置失败: {e}")

        ai = self._collect_ai()
        if ai["api_key"] and not (
            ai["provider"] == "custom" and (not ai["base_url"] or not ai["model"])
        ):
            save_ai_config(
                ai["api_key"],
                ai["base_url"],
                ai["model"],
                ai["provider"],
                system_prompt=self.system_prompt_edit.toPlainText().strip(),
                creative_temperature=self.ai_temp_slider.value() / 100.0,
            )
            self._saved_key = ai["api_key"]

        self.settings_saved.emit()
        self._retire_test_worker()
        super().accept()

    # 迁移时不复制的运行期文件（锁/日志）
    _STORAGE_SKIP_ITEMS = (".moma.lock", "logs", "__pycache__")

    def _handle_storage_change(self, old_root: str, new_root: str):
        """存储位置切换：可选复制旧数据到新位置，并强制提示重启生效。"""
        try:
            os.makedirs(new_root, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(
                self, "存储位置不可用",
                f"无法创建新的存储目录：\n{new_root}\n\n{e}\n"
                "设置已保存，但请重新选择一个有效位置后重启软件。")
            return

        copied, failed = [], []
        try:
            existing = [
                n for n in os.listdir(new_root)
                if n not in self._STORAGE_SKIP_ITEMS
            ]
        except OSError:
            existing = []

        if existing:
            # 目标非空：绝不覆盖，避免两套数据误合并
            QMessageBox.warning(
                self, "目标目录已有数据",
                f"新位置不是空目录（已有：{'、'.join(existing[:5])} 等），\n"
                "为避免覆盖，本次不复制旧数据。请确认选择的目录是否正确。")
        else:
            reply = QMessageBox.question(
                self, "迁移现有数据？",
                f"作品存储位置将切换到：\n{new_root}\n\n"
                "是否把现有作品、历史快照、词库、统计等全部数据复制到新位置？\n"
                "（复制不会删除旧目录里的数据，确认无误后可手动删除）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                import shutil
                try:
                    names = [
                        n for n in os.listdir(old_root)
                        if n not in self._STORAGE_SKIP_ITEMS
                    ]
                except OSError as e:
                    names = []
                    failed.append(f"读取旧目录失败（{e}）")
                for name in names:
                    src = os.path.join(old_root, name)
                    dst = os.path.join(new_root, name)
                    try:
                        if os.path.isdir(src):
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)
                        copied.append(name)
                    except OSError as e:
                        failed.append(f"{name}（{e}）")

        detail = ""
        if copied:
            detail += f"\n已复制 {len(copied)} 项（{ '、'.join(copied[:6]) }"
            detail += " 等）" if len(copied) > 6 else "）"
        if failed:
            detail += f"\n失败：{'、'.join(failed)}（旧目录数据仍完整保留）"
        QMessageBox.information(
            self, "存储位置已变更",
            f"新存储位置：\n{new_root}{detail}\n\n"
            "请立即重启软件使其生效；重启前新写的内容仍会保存到旧位置。")

    def _retire_test_worker(self):
        """停止并收留"测试连接" worker（保存/关窗/Esc 时统一调用）。"""
        w = getattr(self, "_test_worker", None)
        if w is None:
            return
        try:
            from utils.ai_client import retire_worker

            retire_worker(w)
        except Exception:
            pass
        self._test_worker = None

    def closeEvent(self, event):
        self._retire_test_worker()
        super().closeEvent(event)


class _KeyCaptureEdit(QLineEdit):
    """只读输入框，捕获用户按下的键序列，显示为 PortableText 格式。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("在此按下新的快捷键组合…")
        self.setAlignment(Qt.AlignCenter)
        f = self.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.setFont(f)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key in (
            Qt.Key_Control,
            Qt.Key_Shift,
            Qt.Key_Alt,
            Qt.Key_Meta,
            Qt.Key_AltGr,
            Qt.Key_CapsLock,
            Qt.Key_NumLock,
            Qt.Key_ScrollLock,
        ):
            return
        if key == Qt.Key_Escape:
            event.ignore()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.setText("")
            event.accept()
            return
        has_mod = bool(
            mods
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        )
        is_func = bool(Qt.Key_F1 <= key <= Qt.Key_F35)
        if not has_mod and not is_func:
            return
        try:
            seq = QKeySequence(int(mods.value) | int(key))
        except Exception:
            return
        text = seq.toString(QKeySequence.PortableText)
        if not text:
            return
        self.setText(text)
        event.accept()


class _HotkeyEditDialog(QDialog):
    """修改单个快捷键：捕获键盘组合。"""

    def __init__(self, action_label: str, current_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("修改快捷键")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        lab = QLabel(f"动作：<b>{action_label}</b>")
        lab.setTextFormat(Qt.RichText)
        lay.addWidget(lab)

        lay.addWidget(QLabel("请按下新的快捷键组合："))
        self.capture = _KeyCaptureEdit()
        self.capture.setText(current_key or "")
        self.capture.setMinimumHeight(52)
        lay.addWidget(self.capture)

        tip = QLabel(
            "提示：\n"
            "  · Esc 取消修改\n"
            "  · Backspace / Delete 清空（禁用此快捷键）\n"
            "  · 单按字母不生效，需搭配 Ctrl / Alt / Shift\n"
            "  · F1～F12 可以单独使用"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("font-size: 12px; color: gray;")
        lay.addWidget(tip)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.capture.setFocus()

    def get_key(self) -> str:
        return self.capture.text().strip()
