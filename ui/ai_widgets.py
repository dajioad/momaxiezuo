# ui/ai_widgets.py
"""墨码写作 - AI 对话通用组件（浮动气泡 / 未来任何 AI 界面复用）

从原 ui/ai_panel.py 抽出的**呈现层**部件：
- _ThinkingDots        思考中三点跳动动效
- _MarkdownView        Markdown 渲染气泡
- _Bubble              对话气泡（user / assistant / error / system）
- _PresetEditDialog    新建/编辑预设
- _PresetManageDialog  管理预设
- _MentionPopup        @ 引用弹窗（章节 / 角色 / 词条）

不含面板逻辑（会话管理 / 请求发起 / 上下文注入），
那些由使用方（如 ui/pet_float.py 的 _PetBubble）实现。
"""
import math
import re

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QFrame, QSizePolicy, QDialog, QDialogButtonBox,
    QLineEdit, QFormLayout, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QTextEdit, QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, QTimer, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QFontMetrics
import qtawesome as qta

from ui.writing_editor import accent_foreground
from ui.theme_style import qss_alpha
from utils.ai_client import (
    PRESET_PROMPTS, load_presets, add_custom_preset, delete_preset,
    move_preset, restore_default_presets, update_preset,
)


# ============================================================
# 思考中动效
# ============================================================
class _ThinkingDots(QWidget):
    """AI "思考中" 三点跳动动效。"""

    _FRAMES_PER_CYCLE = 20
    _FRAME_MS = 60
    _PHASE_STEP = 1.0 / 6.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._accent = QColor("#b08a4e")
        self._muted = QColor("#9a8e76")
        self._timer = QTimer(self)
        self._timer.setInterval(self._FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(24)
        self.setMinimumWidth(88)

    def start(self):
        self._phase = 0
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()

    def set_colors(self, accent: str, muted: str):
        self._accent = QColor(accent)
        self._muted = QColor(muted)
        self.update()

    def _tick(self):
        self._phase = (self._phase + 1) % self._FRAMES_PER_CYCLE
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        f = self.font()
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(self._muted)
        fm = QFontMetrics(f)
        label = "思考中"
        text_w = fm.horizontalAdvance(label)
        h = self.height()
        p.drawText(
            QRectF(0, 0, text_w + 2, h),
            Qt.AlignLeft | Qt.AlignVCenter, label)

        base_r = 2.6
        gap = 3.0
        dot_d = base_r * 2
        start_x = text_w + 8 + base_r
        cy = h / 2.0
        t = self._phase / float(self._FRAMES_PER_CYCLE)

        p.setPen(Qt.NoPen)
        for i in range(3):
            dot_t = (t - i * self._PHASE_STEP) % 1.0
            s = math.sin(math.pi * dot_t)
            intensity = s ** 4
            opacity = 0.25 + 0.75 * intensity
            r = base_r * (1.0 + 0.15 * intensity)
            c = QColor(self._accent)
            c.setAlphaF(opacity)
            p.setBrush(c)
            cx = start_x + i * (dot_d + gap)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        p.end()


# ============================================================
# Markdown 气泡
# ============================================================
class _MarkdownView(QTextBrowser):
    """气泡内的 Markdown 渲染视图：无边框透明底、高度随内容自适应。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setReadOnly(True)
        self.document().setDocumentMargin(2)
        self.textChanged.connect(self._fit_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self):
        doc = self.document()
        vw = self.viewport().width()
        doc.setTextWidth(vw if vw > 0 else -1)
        h = doc.documentLayout().documentSize().height()
        if not (h > 0):
            h = doc.size().height()
        new_h = int(h) + 8
        cur = self.height()
        if cur == 0 or abs(new_h - cur) >= 3:
            self.setFixedHeight(new_h)

    def set_markdown_text(self, md: str):
        self.setMarkdown(md or "")
        self._fit_height()


# ============================================================
# 对话气泡
# ============================================================
class _Bubble(QFrame):
    """对话气泡。role: user / assistant / error / system。"""

    def __init__(self, role: str, text: str = "", parent=None,
                 thinking: bool = False):
        super().__init__(parent)
        self.setObjectName("aiBubble")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.role = role
        self.message_index = None
        self._font_px = 13
        if thinking:
            self.label = _ThinkingDots()
            self.label.start()
        elif role == "assistant":
            self.label = _MarkdownView()
            self.label.set_markdown_text(text)
        else:
            self.label = QLabel(text)
            self.label.setWordWrap(True)
            self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self.label)

        self.action_wrap = QWidget()
        self.action_wrap.setFixedHeight(28)
        self._opacity = QGraphicsOpacityEffect(self.action_wrap)
        self._opacity.setOpacity(0.0)
        self.action_wrap.setGraphicsEffect(self._opacity)
        self.action_row = QHBoxLayout(self.action_wrap)
        self.action_row.setContentsMargins(0, 0, 0, 0)
        self.action_row.setSpacing(6)
        layout.addWidget(self.action_wrap)
        self.apply_colors(None)

    def set_text(self, text: str, markdown: bool = None):
        if markdown is None:
            markdown = self.role == "assistant"
        if isinstance(self.label, _ThinkingDots):
            self.label.stop()
            self.label.setParent(None)
            self.label.deleteLater()
            if markdown:
                new_label = _MarkdownView()
                new_label.set_markdown_text(text)
            else:
                new_label = QLabel(text)
                new_label.setWordWrap(True)
                new_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.layout().insertWidget(0, new_label)
            self.label = new_label
            return
        if markdown and isinstance(self.label, _MarkdownView):
            self.label.set_markdown_text(text)
        else:
            self.label.setText(text)
            self.adjustSize()

    def enterEvent(self, event):
        try:
            self._opacity.setOpacity(1.0)
        except RuntimeError:
            pass
        super().enterEvent(event)

    def leaveEvent(self, event):
        try:
            self._opacity.setOpacity(0.0)
        except RuntimeError:
            pass
        super().leaveEvent(event)

    def apply_colors(self, theme):
        self._theme = theme or {}
        theme = self._theme
        accent = theme.get("accent", "#b08a4e")
        editor_bg = theme.get("editor_bg", "#fcfaf4")
        sidebar = theme.get("sidebar", "#efe8d8")
        text_color = theme.get("text", "#3b342a")
        muted = theme.get("muted", "#9a8e76")
        bg_qc = QColor(theme.get("bg", "#f7f2e8"))
        is_light = bg_qc.lightness() > 150

        if self.role == "user":
            bg = accent
            fg = theme.get("accent_fg") or accent_foreground(accent)
            align = "margin-left: 32px;"
        elif self.role == "error":
            bg, fg = ("#fdecea", "#c0392b") if is_light else ("#5a2a2a", "#ff8a80")
            align = "margin-right: 32px;"
        elif self.role == "system":
            bg, fg = sidebar, muted
            align = "margin-right: 32px;"
        else:
            bg, fg = editor_bg, text_color
            align = "margin-right: 32px;"

        self.setStyleSheet(
            f"#aiBubble {{ background-color: {bg}; color: {fg};"
            f" border-radius: 12px; {align} }}"
            f"QLabel {{ background: transparent; color: {fg};"
            f" font-size: {self._font_px}px; }}"
            f"QTextBrowser {{ background: transparent; color: {fg};"
            f" font-size: {self._font_px}px; border: none; }}"
        )
        for i in range(self.action_row.count()):
            w = self.action_row.itemAt(i).widget()
            if isinstance(w, QPushButton):
                w.setStyleSheet(
                    f"QPushButton {{ border: 1px solid {qss_alpha(muted, 0x66)};"
                    f" border-radius: 4px; padding: 2px 8px; font-size: 12px;"
                    f" background: transparent; color: {text_color}; }}"
                    f"QPushButton:hover {{"
                    f" background-color: {qss_alpha(accent, 0x22)};"
                    f" color: {accent}; }}")
        if isinstance(self.label, _MarkdownView):
            self.label._fit_height()
            QTimer.singleShot(0, self.label._fit_height)
        elif isinstance(self.label, _ThinkingDots):
            self.label.set_colors(accent, muted)

    def set_font_size(self, px: int):
        self._font_px = px
        try:
            cur = self.styleSheet()
        except RuntimeError:
            return
        new_ss = re.sub(r"font-size:\s*\d+px", f"font-size: {px}px", cur)
        if new_ss != cur:
            self.setStyleSheet(new_ss)
        if isinstance(self.label, _MarkdownView):
            self.label._fit_height()
            QTimer.singleShot(0, self.label._fit_height)

    def add_action_button(self, text: str) -> QPushButton:
        theme = getattr(self, "_theme", None) or {}
        text_c = theme.get("text", "#3b342a")
        muted = theme.get("muted", "#9a8e76")
        accent = theme.get("accent", "#b08a4e")
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {qss_alpha(muted, 0x44)};"
            f" border-radius: 5px; padding: 3px 10px; font-size: 11px;"
            f" background: transparent; color: {muted}; }}"
            f"QPushButton:hover {{ background-color: {qss_alpha(accent, 0x14)};"
            f" color: {accent}; border-color: {accent}; }}")
        try:
            self.action_row.addWidget(btn)
        except RuntimeError:
            pass
        return btn


# ============================================================
# 预设编辑 / 管理
# ============================================================
class _PresetEditDialog(QDialog):
    def __init__(self, parent=None, label: str = "", template: str = ""):
        super().__init__(parent)
        self.setWindowTitle("编辑预设" if label else "新建预设")
        self.setMinimumWidth(480)
        self.setMinimumHeight(360)
        form = QFormLayout(self)
        form.setContentsMargins(12, 12, 12, 12)
        self.name_edit = QLineEdit(label)
        self.name_edit.setPlaceholderText("如：角色设定生成")
        form.addRow("名称：", self.name_edit)
        self.tpl_edit = QTextEdit(template)
        self.tpl_edit.setPlaceholderText(
            "提示词模板，可使用占位符：\n"
            "  {last_para} — 最后一段正文\n"
            "  {summary} — 当前摘要\n"
            "  {outlines} — 前文梗概\n"
            "  {style} — 作品类型/风格")
        self.tpl_edit.setMinimumHeight(160)
        form.addRow("模板：", self.tpl_edit)
        hint = QLabel(
            "含占位符的模板会在发送前自动填充当前作品上下文；"
            "无占位符则直接作为提示词。")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 12px; color: gray;")
        form.addRow("", hint)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _validate_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请输入预设名称。")
            return
        if not self.tpl_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "请输入提示词模板。")
            return
        self.accept()

    def get_data(self):
        return self.name_edit.text().strip(), self.tpl_edit.toPlainText().strip()


class _PresetManageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理 AI 预设")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self._default_keys = {k for _, k, _ in PRESET_PROMPTS}
        # 沿父链取当前主题图标色（父窗口通常是宠物气泡，带 _theme）
        icon_text = "#9a8e76"
        p = parent
        while p is not None:
            theme = getattr(p, "_theme", None)
            if theme:
                icon_text = theme.get("muted", icon_text)
                break
            p = p.parent()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        hint = QLabel(
            "预设支持新建、编辑、删除、上移/下移排序。"
            "编辑默认预设后，「恢复默认」不会覆盖你的改动。")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 12px; color: gray;")
        layout.addWidget(hint)
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setMinimumHeight(200)
        layout.addWidget(self.list_widget)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.add_btn = QPushButton(qta.icon("fa5s.plus", color=icon_text), " 新建")
        self.del_btn = QPushButton(qta.icon("fa5s.trash-alt", color=icon_text), " 删除")
        self.up_btn = QPushButton(qta.icon("fa5s.arrow-up", color=icon_text), " 上移")
        self.down_btn = QPushButton(qta.icon("fa5s.arrow-down", color=icon_text), " 下移")
        self.restore_btn = QPushButton(qta.icon("fa5s.undo", color=icon_text), " 恢复默认")
        for btn in (self.add_btn, self.del_btn, self.up_btn,
                    self.down_btn, self.restore_btn):
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.button(QDialogButtonBox.Close).setText("关闭")
        close_box.rejected.connect(self.reject)
        layout.addWidget(close_box)
        self.add_btn.clicked.connect(self._add_preset)
        self.del_btn.clicked.connect(self._delete_preset)
        self.up_btn.clicked.connect(self._move_up)
        self.down_btn.clicked.connect(self._move_down)
        self.restore_btn.clicked.connect(self._restore_defaults)
        self.list_widget.itemDoubleClicked.connect(lambda: self._edit_preset())
        self._refresh()

    def _refresh(self, select_key: str = None):
        if select_key is None:
            item = self.list_widget.currentItem()
            select_key = item.data(Qt.UserRole) if item else None
        self.list_widget.clear()
        self._presets = load_presets()
        row_to_select = 0
        for i, (label, key, _tpl) in enumerate(self._presets):
            tag = "默认" if key in self._default_keys else "自定义"
            item = QListWidgetItem(f"{i + 1}. [{tag}] {label}")
            item.setData(Qt.UserRole, key)
            self.list_widget.addItem(item)
            if key == select_key:
                row_to_select = i
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(row_to_select)

    def _current_key(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _add_preset(self):
        dlg = _PresetEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            label, template = dlg.get_data()
            if label and template:
                new_key = add_custom_preset(label, template)
                self._refresh(select_key=new_key)

    def _edit_preset(self):
        key = self._current_key()
        if not key:
            return
        item = next((p for p in self._presets if p[1] == key), None)
        if not item:
            return
        label, _, template = item
        dlg = _PresetEditDialog(self, label, template)
        if dlg.exec() != QDialog.Accepted:
            return
        new_label, new_template = dlg.get_data()
        if new_label and new_template:
            update_preset(key, new_label, new_template)
            self._refresh(select_key=key)

    def _delete_preset(self):
        key = self._current_key()
        if not key:
            return
        label = ""
        for l, k, _ in self._presets:
            if k == key:
                label = l
                break
        if key in self._default_keys:
            text = f"确定删除默认预设「{label}」？\n可通过「恢复默认」找回。"
        else:
            text = f"确定删除自定义预设「{label}」？此操作不可恢复。"
        if QMessageBox.question(self, "删除预设", text) == QMessageBox.Yes:
            delete_preset(key)
            self._refresh()

    def _move_up(self):
        key = self._current_key()
        if key:
            move_preset(key, -1)
            self._refresh(select_key=key)

    def _move_down(self):
        key = self._current_key()
        if key:
            move_preset(key, 1)
            self._refresh(select_key=key)

    def _restore_defaults(self):
        if QMessageBox.question(
            self, "恢复默认预设",
            "找回所有被删除的默认预设？\n"
            "已编辑过的默认预设与自定义预设、以及现有排序均不受影响。"
        ) == QMessageBox.Yes:
            restore_default_presets()
            self._refresh()


# ============================================================
# @ 引用弹窗
# ============================================================
class _MentionPopup(QFrame):
    """@ 引用弹窗：列出章节 / 角色 / 词条。"""

    item_selected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("mentionPopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(320)
        self.setMaximumHeight(340)
        self._items = []
        self._current_row = 0
        self._theme = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("mentionList")
        self.list_widget.setFrameShape(QFrame.NoFrame)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.list_widget)

    def set_items(self, items, theme):
        self._items = list(items or [])
        self._theme = theme or {}
        self._current_row = 0
        self.list_widget.clear()
        for kind, ident, label, meta in self._items:
            icon_name = {
                "chapter": "fa5s.file-alt", "character": "fa5s.user",
                "word": "fa5s.bookmark", "card": "fa5s.lightbulb",
            }.get(kind, "fa5s.circle")
            item = QListWidgetItem(f"  {label}")
            try:
                item.setIcon(qta.icon(
                    icon_name,
                    color=self._theme.get("muted", "#9a8e76")))
            except Exception:
                pass
            if meta:
                item.setToolTip(meta)
            self.list_widget.addItem(item)
        if self._items:
            self.list_widget.setCurrentRow(0)
        self._apply_theme()

    def _apply_theme(self):
        t = self._theme
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            f"#mentionPopup {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 8px; }}"
            f"#mentionList {{ background: transparent; border: none;"
            f" outline: none; }}"
            f"#mentionList::item {{ padding: 6px 8px; border-radius: 5px;"
            f" color: {text}; font-size: 12px; }}"
            f"#mentionList::item:hover {{ background: {qss_alpha(accent, 0x14)}; }}"
            f"#mentionList::item:selected {{"
            f" background: {qss_alpha(accent, 0x28)}; color: {text}; }}")

    def move_selection(self, delta: int):
        n = len(self._items)
        if n == 0:
            return
        self._current_row = (self._current_row + delta) % n
        self.list_widget.setCurrentRow(self._current_row)

    def accept_current(self) -> bool:
        if not self._items:
            return False
        kind, ident, _label, _meta = self._items[self._current_row]
        self.item_selected.emit(kind, ident)
        self.close()
        return True

    def _on_item_clicked(self, item):
        row = self.list_widget.row(item)
        if 0 <= row < len(self._items):
            self._current_row = row
            self.accept_current()