# ui/outline_panel.py
"""墨码写作 - 资料面板（书架侧栏标签）

对标番茄作家助手的「作品大纲」与阅文作家助手的「章纲」，并向
结构化资料扩展：

- 大纲   ：整部书的设定、主线、分卷大纲（outline.txt）
- 时间线 ：结构化事件列表（timeline.json）            【新】
- 世界观 ：结构化条目库（worldbuilding.json）          【新】
- 梗概   ：跟随当前章节的章纲（notes/<章节id>.txt）
- 灵感   ：随手记录的素材与灵感卡片（ideas.json）

前三个是"作品的宏观资料"，后两个是"当前章节的笔记"。
全部本地存储、防抖自动保存、随作品独立。
"""

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QTextCursor,
    QTextCharFormat,
    QColor,
    QFont,
    QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QStackedWidget,
    QFrame,
    QPushButton,
    QLineEdit,
    QMenu,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QTabBar,
)
import qtawesome as qta  # noqa: F401（保留供未来用）

from utils.idea_cards import (
    load_cards,
    add_card,
    update_card,
    delete_card,
    CARD_TYPES,
    type_label,
    type_color,
)
from ui.timeline_panel import TimelinePanel
from ui.worldbuilding_panel import WorldbuildingPanel
from ui.theme_style import qss_alpha

# ---------- @ 引用识别 ----------
_AT_REF_RE = re.compile(
    r"@(?:"
    r"第\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回]"
    r"|"
    r"[^\s，。！？；：、（）【】《》“”‘’「」]{1,20}"
    r")"
)
_REF_STOP_CHARS = set(" \t\n，。！？；：、（）【】《》“”‘’「」")


class _RefHighlighter(QSyntaxHighlighter):
    """@引用高亮器：绑在 document 上，文本一变就自动重扫。"""

    def __init__(self, document, color_provider):
        super().__init__(document)
        self._color_provider = color_provider

    def highlightBlock(self, text):
        if not text:
            return
        accent = self._color_provider()
        fmt = QTextCharFormat()
        fmt.setForeground(accent)
        fmt.setFontWeight(QFont.Weight.DemiBold)
        fmt.setUnderlineStyle(QTextCharFormat.DotLine)
        fmt.setUnderlineColor(accent)
        for m in _AT_REF_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), fmt)


class _OutlineEditor(QTextEdit):
    """大纲/笔记编辑器：支持 @ 引用点击跳转。"""

    reference_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._theme = {}
        self._highlighter = _RefHighlighter(self.document(), self._accent_color)

    def _accent_color(self) -> QColor:
        t = self._theme or {}
        return QColor(t.get("accent", "#b08a4e"))

    def apply_theme(self, theme: dict):
        self._theme = theme or {}
        self._highlighter.rehighlight()

    def _ref_at_pos(self, pos):
        cursor = self.cursorForPosition(pos)
        block = cursor.block()
        if not block.isValid():
            return None
        block_text = block.text()
        block_pos = block.position()
        cursor_in_block = cursor.position() - block_pos

        start = -1
        for i in range(cursor_in_block - 1, max(-1, cursor_in_block - 40), -1):
            ch = block_text[i]
            if ch == "@":
                start = i
                break
            if ch in _REF_STOP_CHARS:
                return None
        if start < 0:
            return None

        m = _AT_REF_RE.match(block_text, start)
        if m is None:
            return None
        end = m.end()

        if cursor_in_block >= end:
            return None

        word = block_text[start + 1 : end].strip()
        if not word:
            return None
        return (start + block_pos, end + block_pos, word)

    def mouseMoveEvent(self, event):
        ref = self._ref_at_pos(event.pos())
        if ref is not None:
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.IBeamCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            ref = self._ref_at_pos(event.pos())
            if ref is not None:
                _, _, word = ref
                self.reference_clicked.emit(word)
                event.accept()
                return
        super().mousePressEvent(event)


PLACEHOLDERS = {
    "outline": "在此搭建整部作品：世界观、人物关系、主线脉络、分卷大纲……\n内容保存在作品目录的 outline.txt，随时可改。",
    "note": "记录当前章节的梗概、出场人物、伏笔回收点……\n切换章节自动切换，保存在作品目录 notes 下。",
}

# 5 个子 tab → (显示名, kind 键)
_KIND_TABS = [
    ("大纲", "outline"),
    ("时间线", "timeline"),
    ("世界观", "worldbuilding"),
    ("梗概", "note"),
    ("灵感", "ideas"),
]

# stack 索引（固定的 4 个 widget）
_STACK_EDITOR = 0  # 大纲 / 梗概 共用
_STACK_TIMELINE = 1
_STACK_WORLDBUILDING = 2
_STACK_IDEAS = 3


class OutlinePanel(QWidget):
    """资料面板：大纲 / 时间线 / 世界观 / 梗概 / 灵感。"""

    # @ 引用点击 → 章节 id（由 workspace 连接 open_chapter）
    chapter_reference_clicked = Signal(str)
    # 子面板请求把文本插入编辑器（世界观双击）
    insert_text = Signal(str)
    # 子面板数据变化（世界观/时间线增删改）
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("outlinePanel")
        self.work = None
        self._chapter_id = ""
        self._chapter_title = ""
        self._kind = "outline"
        self._loading = False
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._flush_current)
        self.apply_theme(None)
        self.set_work(None)

    # ==================== UI ====================
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---- 子 tab 栏 ----
        self.kind_tabs = QTabBar()
        self.kind_tabs.setExpanding(False)
        self.kind_tabs.setDrawBase(False)
        self.kind_tabs.setUsesScrollButtons(False)
        for label, _ in _KIND_TABS:
            self.kind_tabs.addTab(label)
        self.kind_tabs.setCurrentIndex(0)
        self.kind_tabs.currentChanged.connect(self._on_kind_tab_changed)
        root.addWidget(self.kind_tabs)

        # ---- 章节徽章（仅"梗概"页显示） ----
        self.chapter_label = QLabel("")
        self.chapter_label.setTextFormat(Qt.RichText)
        self.chapter_label.setWordWrap(True)
        self.chapter_label.setVisible(False)
        root.addWidget(self.chapter_label)

        # ---- 内容区（stack） ----
        self.stack = QStackedWidget()

        # index 0：文本编辑器（大纲 / 梗概共用）
        self.editor = _OutlineEditor()
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.reference_clicked.connect(self._on_reference_clicked)
        self.stack.addWidget(self.editor)

        # index 1：时间线
        self.timeline_panel = TimelinePanel()
        self.timeline_panel.goto_chapter.connect(self.chapter_reference_clicked.emit)
        self.timeline_panel.data_changed.connect(self.data_changed.emit)
        self.stack.addWidget(self.timeline_panel)

        # index 2：世界观
        self.worldbuilding_panel = WorldbuildingPanel()
        self.worldbuilding_panel.insert_text.connect(self.insert_text.emit)
        self.worldbuilding_panel.data_changed.connect(self.data_changed.emit)
        self.stack.addWidget(self.worldbuilding_panel)

        # index 3：灵感卡片
        self.idea_view = _IdeaCardsView(self)
        self.stack.addWidget(self.idea_view)

        root.addWidget(self.stack, 1)

        # ---- 状态栏 ----
        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.RichText)
        root.addWidget(self.status_label)

    # ==================== 生命周期 ====================
    def set_work(self, work):
        self._timer.stop()
        self._flush_current()
        self.work = work
        self._chapter_id = ""
        self._chapter_title = ""
        self.set_enabled_all(work is not None)

        # 新面板也要注入 work
        self.timeline_panel.set_work(work)
        self.worldbuilding_panel.set_work(work)

        if work is None:
            self._set_text("")
            self.chapter_label.setText("（请先打开作品）")
            self.chapter_label.setVisible(self._kind == "note")
            self.status_label.setText("")
            return
        self._reload_current()

    def set_chapter(self, chapter_id: str, title: str = ""):
        self._chapter_id = str(chapter_id or "")
        self._chapter_title = title or ""
        if self._kind == "note":
            self._timer.stop()
            self._reload_current()
        else:
            self._update_chapter_label()

    def set_enabled_all(self, enabled: bool):
        self.kind_tabs.setEnabled(enabled)
        self.editor.setEnabled(enabled)

    # ==================== 子 tab 切换 ====================
    def _on_kind_tab_changed(self, idx: int):
        """切换子 tab。5 个 tab 映射到 4 个 stack widget。"""
        self._timer.stop()
        self._flush_current()

        kind = _KIND_TABS[idx][1] if 0 <= idx < len(_KIND_TABS) else "outline"
        self._kind = kind

        if kind == "outline":
            self.stack.setCurrentIndex(_STACK_EDITOR)
            self.chapter_label.setVisible(False)
            self._reload_current()
        elif kind == "timeline":
            self.stack.setCurrentIndex(_STACK_TIMELINE)
            self.chapter_label.setVisible(False)
            self.timeline_panel.set_work(self.work)
            self.status_label.setText("")
        elif kind == "worldbuilding":
            self.stack.setCurrentIndex(_STACK_WORLDBUILDING)
            self.chapter_label.setVisible(False)
            self.worldbuilding_panel.set_work(self.work)
            self.status_label.setText("")
        elif kind == "note":
            self.stack.setCurrentIndex(_STACK_EDITOR)
            self.chapter_label.setVisible(True)
            self._reload_current()
        elif kind == "ideas":
            self.stack.setCurrentIndex(_STACK_IDEAS)
            self.chapter_label.setVisible(False)
            self._reload_current()

    # ==================== 文本类页面加载 ====================
    def _reload_current(self):
        if self.work is None:
            self._set_text("")
            return
        kind = self._kind

        # 结构化面板自己处理
        if kind in ("timeline", "worldbuilding"):
            return

        self.chapter_label.setVisible(kind == "note")
        self._update_chapter_label()

        if kind == "ideas":
            self.idea_view.set_work(self.work)
            n = len(load_cards(self.work))
            if n:
                self.status_label.setText(
                    f"<span style='color:#67c23a;'>●</span> 共 {n} 张卡片"
                )
            else:
                self.status_label.setText(
                    "<span style='color:#9a8e76;'>●</span> 还没有卡片"
                )
            return

        self.editor.setPlaceholderText(PLACEHOLDERS.get(kind, ""))
        try:
            if kind == "outline":
                text = self.work.read_side_doc("outline")
            else:  # note
                text = self.work.read_note(self._chapter_id) if self._chapter_id else ""
        except OSError:
            text = ""

        self._set_text(text)
        if text:
            self.status_label.setText(
                "<span style='color:#67c23a;'>●</span> 已载入 · 自动保存"
            )
        else:
            self.status_label.setText("<span style='color:#9a8e76;'>●</span> 自动保存")

    def _update_chapter_label(self):
        if self._kind != "note":
            return
        title = self._chapter_title or "（未打开章节）"
        self.chapter_label.setText(
            f"<span style='color:#9a8e76;'>当前章节</span>&nbsp;"
            f"<span style='background:rgba(176,138,78,0.15);"
            f"color:#b08a4e;font-size:11px;padding:2px 8px;"
            f"border-radius:8px;font-weight:600;'>"
            f"{title}</span>"
        )

    # ==================== @ 引用解析 ====================
    def _on_reference_clicked(self, word: str):
        if self.work is None or not word:
            return
        cid = self._resolve_reference(word)
        if cid:
            self.chapter_reference_clicked.emit(cid)

    def _resolve_reference(self, word: str) -> str:
        w = (word or "").strip()
        if not w:
            return ""
        chapters = self.work.chapters or []

        try:
            from utils.chapter_order import extract_chapter_number

            num = extract_chapter_number(w)
        except Exception:
            num = None
        if num is not None:
            for c in chapters:
                if extract_chapter_number(c.get("title", "")) == num:
                    return c.get("id", "")

        for c in chapters:
            if (c.get("title") or "").strip() == w:
                return c.get("id", "")

        candidates = []
        for c in chapters:
            title = (c.get("title") or "").strip()
            if not title:
                continue
            core = re.sub(
                r"^第\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回]\s*", "", title
            ).strip()
            if core and (core == w or core.startswith(w)):
                candidates.append(c)
            elif title.startswith(w):
                candidates.append(c)
        if len(candidates) == 1:
            return candidates[0].get("id", "")
        return ""

    # ==================== 文本类编辑 / 保存 ====================
    def _set_text(self, text: str):
        self._loading = True
        self.editor.setPlainText(text or "")
        self._loading = False

    def _on_text_changed(self):
        if self._loading or self.work is None:
            return
        # 只在处理"大纲/梗概"时刷新状态，切到其它 tab 期间不响应
        if self._kind not in ("outline", "note"):
            return
        self.status_label.setText("<span style='color:#e6a23c;'>●</span> 编辑中…")
        self._timer.start()

    def _flush_current(self) -> bool:
        if self.work is None:
            return False
        kind = self._kind

        # 结构化面板与灵感卡片自己管理数据
        if kind in ("timeline", "worldbuilding", "ideas"):
            return True
        if kind == "note" and not self._chapter_id:
            return False

        text = self.editor.toPlainText()
        try:
            if kind == "outline":
                self.work.write_side_doc("outline", text)
            else:  # note
                self.work.write_note(self._chapter_id, text)
        except OSError:
            self.status_label.setText("<span style='color:#e74c3c;'>●</span> 保存失败")
            return False
        self.status_label.setText("<span style='color:#67c23a;'>●</span> 已保存")
        return True

    def flush(self):
        self._timer.stop()
        self._flush_current()

    # ==================== 主题 ====================
    def apply_theme(self, theme: dict):
        theme = theme or {}
        bg = theme.get("sidebar", "#EFE8D8")
        editor_bg = theme.get("editor_bg", theme.get("bg", "#F7F2E6"))
        text_color = theme.get("text", "#3B342A")
        muted = theme.get("muted", "#9A8E76")
        accent = theme.get("accent", "#B08A4E")
        accent_fg = theme.get("accent_fg", "white")

        self.setStyleSheet(
            f"#outlinePanel {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text_color}; background: transparent; }}"
            # 子 tab 栏
            f"QTabBar {{ background: transparent; }}"
            f"QTabBar::tab {{"
            f"  background: transparent; color: {muted};"
            f"  padding: 4px 12px; border: none; border-radius: 6px;"
            f"  font-size: 12px; margin: 2px 2px; }}"
            f"QTabBar::tab:selected {{"
            f"  background: {qss_alpha(accent, 0x20)};"
            f"  color: {accent}; font-weight: 600; }}"
            f"QTabBar::tab:hover:!selected {{"
            f"  background: {qss_alpha(muted, 0x18)}; color: {text_color}; }}"
            # 下拉框（灵感卡片内可能还有）
            f"QComboBox {{"
            f"  color: {text_color}; background: {editor_bg};"
            f"  border: 1px solid {qss_alpha(muted, 0x55)};"
            f"  border-radius: 8px; padding: 6px 10px;"
            f"  min-height: 20px; font-size: 13px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox:focus {{ border: 1px solid {accent}; }}"
            f"QComboBox::drop-down {{ border: none; width: 22px; }}"
            f"QComboBox QAbstractItemView {{"
            f"  background: {editor_bg}; color: {text_color};"
            f"  border: 1px solid {qss_alpha(muted, 0x55)};"
            f"  border-radius: 6px; padding: 4px;"
            f"  selection-background-color: {qss_alpha(accent, 0x33)};"
            f"  selection-color: {text_color}; outline: none; }}"
            # 文本编辑器
            f"QTextEdit, QPlainTextEdit {{"
            f"  color: {text_color}; background: {editor_bg};"
            f"  border: 1px solid {qss_alpha(muted, 0x33)};"
            f"  border-radius: 10px; padding: 10px 12px;"
            f"  selection-background-color: {accent};"
            f"  selection-color: {accent_fg}; }}"
            f"QTextEdit:focus, QPlainTextEdit:focus {{"
            f"  border: 1px solid {accent}; }}"
        )

        # 转发给子控件 / 子面板
        if hasattr(self, "editor") and isinstance(self.editor, _OutlineEditor):
            self.editor.apply_theme(theme)
        if hasattr(self, "idea_view"):
            self.idea_view.apply_theme(theme)
        if hasattr(self, "timeline_panel"):
            self.timeline_panel.apply_theme(theme)
        if hasattr(self, "worldbuilding_panel"):
            self.worldbuilding_panel.apply_theme(theme)

    def refresh_chapter_note_if_open(self, chapter_id: str, new_text: str):
        if self.work is None or self._kind != "note":
            return
        if str(chapter_id) != str(self._chapter_id):
            return
        self._set_text(new_text)
        self.status_label.setText(
            "<span style='color:#67c23a;'>●</span> 已载入 · 自动保存"
        )


# ============================================================
# 灵感卡片视图（内部类，原样保留）
# ============================================================


class _IdeaCardEditDialog(QDialog):
    """新建/编辑一张卡片。"""

    def __init__(self, parent=None, card: dict = None):
        super().__init__(parent)
        card = card or {}
        self.setWindowTitle("编辑卡片" if card.get("id") else "新建卡片")
        self.setMinimumWidth(460)
        self.setMinimumHeight(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self.type_combo = QComboBox()
        for k, label, _ in CARD_TYPES:
            self.type_combo.addItem(label, k)
        idx = self.type_combo.findData(card.get("type", "idea"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        form.addRow("类型", self.type_combo)

        self.title_edit = QLineEdit(card.get("title", ""))
        self.title_edit.setPlaceholderText("简短标题，如：朝堂对峙")
        form.addRow("标题", self.title_edit)

        self.body_edit = QTextEdit()
        self.body_edit.setPlainText(card.get("body", ""))
        self.body_edit.setPlaceholderText("详细内容……")
        self.body_edit.setMinimumHeight(140)
        form.addRow("内容", self.body_edit)

        self.tags_edit = QLineEdit()
        tags = card.get("tags") or []
        self.tags_edit.setText("、".join(tags))
        self.tags_edit.setPlaceholderText("用「、」或「,」分隔，如：李斯、王绾")
        form.addRow("标签", self.tags_edit)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _validate(self):
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写标题。")
            return
        self.accept()

    def get_data(self) -> dict:
        tags_text = self.tags_edit.text().strip()
        tags = []
        if tags_text:
            for sep in ("、", ",", "，"):
                tags_text = tags_text.replace(sep, "\n")
            tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
        return {
            "type": self.type_combo.currentData() or "idea",
            "title": self.title_edit.text().strip(),
            "body": self.body_edit.toPlainText().strip(),
            "tags": tags,
        }


class _IdeaCardWidget(QFrame):
    """单张卡片的显示控件。悬停时边框变金 + 微阴影上浮。"""

    def __init__(self, card: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("ideaCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.card = card
        self._theme = {}
        self._hover_effect = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.type_lab = QLabel(type_label(card.get("type", "idea")))
        self.type_lab.setObjectName("ideaType")
        head.addWidget(self.type_lab)
        head.addStretch()
        self.time_lab = QLabel((card.get("created_at", "") or "")[:10])
        self.time_lab.setObjectName("ideaTime")
        head.addWidget(self.time_lab)
        lay.addLayout(head)

        self.title_lab = QLabel(card.get("title", "未命名"))
        self.title_lab.setObjectName("ideaTitle")
        lay.addWidget(self.title_lab)

        body = (card.get("body", "") or "").replace("\n", " ")
        if len(body) > 60:
            body = body[:60] + "…"
        self.body_lab = QLabel(body or "（无内容）")
        self.body_lab.setObjectName("ideaBody")
        self.body_lab.setWordWrap(True)
        lay.addWidget(self.body_lab)

        tags = card.get("tags") or []
        if tags:
            tag_row = QHBoxLayout()
            tag_row.setSpacing(3)
            for t in tags[:4]:
                lab = QLabel(f"#{t}")
                lab.setObjectName("ideaTag")
                tag_row.addWidget(lab)
            tag_row.addStretch()
            lay.addLayout(tag_row)

    def enterEvent(self, event):
        try:
            from PySide6.QtWidgets import QGraphicsDropShadowEffect
            from PySide6.QtGui import QColor

            if self._hover_effect is None:
                self._hover_effect = QGraphicsDropShadowEffect(self)
                self._hover_effect.setOffset(0, 3)
                self.setGraphicsEffect(self._hover_effect)
            accent = self._theme.get("accent", "#b08a4e")
            c = QColor(accent)
            c.setAlpha(60)
            self._hover_effect.setColor(c)
            self._hover_effect.setBlurRadius(14)
        except Exception:
            pass
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hover_effect is not None:
            self._hover_effect.setBlurRadius(0)
        super().leaveEvent(event)

    def apply_theme(self, theme: dict):
        self._theme = theme or {}
        t = self._theme
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        tcolor = type_color(self.card.get("type", "idea"))
        self.setStyleSheet(
            f"QFrame#ideaCard {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x30)}; border-radius: 10px; }}"
            f"QFrame#ideaCard:hover {{ border: 1px solid {accent}; }}"
            f"QLabel#ideaType {{ background: {tcolor}; color: white;"
            f" border-radius: 3px; padding: 2px 7px; font-size: 10px;"
            f" font-weight: bold; }}"
            f"QLabel#ideaTime {{ color: {muted}; font-size: 10px; }}"
            f"QLabel#ideaTitle {{ color: {text}; font-size: 13px;"
            f" font-weight: bold; }}"
            f"QLabel#ideaBody {{ color: {muted}; font-size: 11px; }}"
            f"QLabel#ideaTag {{ color: {accent}; font-size: 10px; }}"
        )


class _IdeaCardsView(QWidget):
    """灵感卡片列表视图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.work = None
        self._theme = {}
        self._filter_type = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(4)

        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", None)
        for k, label, _ in CARD_TYPES:
            self.filter_combo.addItem(label, k)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self.filter_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh)
        bar.addWidget(self.search_edit, 1)

        self.add_btn = QPushButton()
        self.add_btn.setObjectName("ideaAddBtn")
        self.add_btn.setFixedSize(28, 28)
        self.add_btn.setToolTip("新建卡片")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._on_add)
        bar.addWidget(self.add_btn)
        lay.addLayout(bar)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("ideaCardsList")
        self.list_widget.setSpacing(4)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_menu)
        lay.addWidget(self.list_widget, 1)

        self.empty_hint = QLabel("还没有卡片，点右上角「＋」新建")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet("color: #9a8e76; font-size: 12px; padding: 20px;")
        lay.addWidget(self.empty_hint)

    def set_work(self, work):
        self.work = work
        self._refresh()

    def apply_theme(self, theme: dict):
        self._theme = theme or {}
        t = self._theme
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            f"QListWidget#ideaCardsList {{ background: transparent;"
            f" border: none; outline: none; }}"
            f"QListWidget#ideaCardsList::item {{ border-radius: 6px; }}"
            f"QListWidget#ideaCardsList::item:selected {{"
            f" background: {qss_alpha(accent, 0x26)}; }}"
            f"QComboBox {{ background: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x50)};"
            f" border-radius: 4px; padding: 2px 6px; font-size: 12px; }}"
            f"QLineEdit {{ background: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x50)};"
            f" border-radius: 4px; padding: 3px 6px; font-size: 12px; }}"
            f"QPushButton#ideaAddBtn {{"
            f"  background: {accent}; color: white;"
            f"  border: none; border-radius: 6px;"
            f"  padding: 0; }}"
            f"QPushButton#ideaAddBtn:hover {{"
            f"  background: {accent}; }}"
        )
        from PySide6.QtGui import QIcon

        self.add_btn.setIcon(qta.icon("fa5s.plus", color="white"))
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self.list_widget.itemWidget(item)
            if isinstance(w, _IdeaCardWidget):
                w.apply_theme(t)

    def _on_filter_changed(self, _):
        self._filter_type = self.filter_combo.currentData()
        self._refresh()

    def _refresh(self):
        self.list_widget.clear()
        if self.work is None:
            self.empty_hint.setVisible(True)
            return
        cards = load_cards(self.work)
        kw = self.search_edit.text().strip().lower()

        shown = []
        for c in cards:
            if self._filter_type and c.get("type") != self._filter_type:
                continue
            if kw:
                hay = (
                    c.get("title", "")
                    + " "
                    + c.get("body", "")
                    + " "
                    + " ".join(c.get("tags") or [])
                ).lower()
                if kw not in hay:
                    continue
            shown.append(c)

        if not shown:
            self.empty_hint.setVisible(True)
            self.empty_hint.setText(
                "没有匹配的卡片"
                if (kw or self._filter_type)
                else "还没有卡片，点右上角「＋」新建"
            )
            return
        self.empty_hint.setVisible(False)

        for c in shown:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, c["id"])
            w = _IdeaCardWidget(c)
            w.apply_theme(self._theme)
            item.setSizeHint(w.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, w)

    def _current_card_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_add(self):
        if self.work is None:
            return
        dlg = _IdeaCardEditDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        add_card(
            self.work, data["type"], data["title"], data["body"], tags=data["tags"]
        )
        self._refresh()

    def _on_item_double_clicked(self, item):
        cid = item.data(Qt.UserRole)
        if cid:
            self._edit_card(cid)

    def _edit_card(self, cid: str):
        if self.work is None:
            return
        cards = load_cards(self.work)
        card = next((c for c in cards if c["id"] == cid), None)
        if not card:
            return
        dlg = _IdeaCardEditDialog(self, card)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        update_card(self.work, cid, **data)
        self._refresh()

    def _delete_card(self, cid: str):
        if self.work is None:
            return
        cards = load_cards(self.work)
        card = next((c for c in cards if c["id"] == cid), None)
        if not card:
            return
        title = card.get("title", "未命名")
        if (
            QMessageBox.question(
                self, "删除卡片", f"确定删除「{title}」吗？此操作不可恢复。"
            )
            != QMessageBox.Yes
        ):
            return
        delete_card(self.work, cid)
        self._refresh()

    def _copy_card(self, cid: str):
        from PySide6.QtWidgets import QApplication

        if self.work is None:
            return
        cards = load_cards(self.work)
        card = next((c for c in cards if c["id"] == cid), None)
        if not card:
            return
        text = f"{card.get('title', '')}\n\n{card.get('body', '')}"
        QApplication.clipboard().setText(text)

    def _use_in_ai(self, cid: str):
        """把卡片标题插入到 AI 输入框。仅在 AI 面板隐藏时展开它。"""
        if self.work is None:
            return
        cards = load_cards(self.work)
        card = next((c for c in cards if c["id"] == cid), None)
        if not card:
            return
        title = card.get("title", "")
        try:
            w = self.window()
            ws = getattr(w, "workspace", None)
            if ws is None:
                return
            ai_panel = getattr(ws, "ai_panel", None)
            if ai_panel is None:
                return
            if not ai_panel.isVisible():
                ws.toggle_ai()
            existing = ai_panel.input_edit.toPlainText().rstrip()
            new_text = (existing + " " + title + " ") if existing else (title + " ")
            ai_panel.input_edit.setPlainText(new_text)
            ai_panel.input_edit.moveCursor(QTextCursor.End)
            ai_panel.input_edit.setFocus()
        except Exception:
            pass

    def _show_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        cid = item.data(Qt.UserRole)
        if not cid:
            return
        menu = QMenu(self)
        act_edit = menu.addAction("编辑")
        act_use = menu.addAction("在 AI 中使用")
        menu.addSeparator()
        act_copy = menu.addAction("复制全文")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._edit_card(cid)
        elif chosen == act_use:
            self._use_in_ai(cid)
        elif chosen == act_copy:
            self._copy_card(cid)
        elif chosen == act_del:
            self._delete_card(cid)
