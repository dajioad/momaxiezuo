# ui/writing_editor.py
"""墨码写作 - 沉浸式纯文本编辑器

特性：
- QPlainTextEdit 纯文本，单章性能无忧
- 限宽居中（默认 720px），米白/羊皮纸/深夜三套写作主题
- 打字机滚动：光标始终保持在视口约 45% 位置
  · 只在"纯键盘文本输入"时触发（字母/数字/标点/回车/退格/Delete）
  · 撤销、重做、粘贴、方向键、鼠标操作、程序化改动都不触发
- 排版级行距（QTextBlockFormat 比例行距），字号/字体可配
- Ctrl+滚轮 缩放字号（写盘到 cfg，下次启动保持）
- 自动保存信号由上层 workspace 统一调度（停顿 2 秒 + 定时 30 秒）
- 符号核心逻辑已抽到 utils/symbol_logic.py，本文件只负责编辑器和主题
- 查找 / 校对 / 词条 / 闪烁四组 ExtraSelections 独立缓存，合并提交
- Ctrl+Enter：唤起 AI 光标续写浮动输入条，流式写入正文
"""

from PySide6.QtWidgets import (
    QPlainTextEdit,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QTextEdit,
    QApplication,
    QPushButton,
    QLabel,
    QLineEdit,
    QDialog,
)
from PySide6.QtGui import (
    QFont,
    QTextCursor,
    QTextBlockFormat,
    QPainter,
    QColor,
    QPalette,
    QTextCharFormat,
    QShortcut,
    QKeySequence,
)
from PySide6.QtCore import Signal, Qt, QTimer

from ui.theme_style import qss_alpha
from utils.symbol_logic import build_edge_result

# 沉浸式写作主题（编辑器区域专用，区别于应用主主题）
WRITING_THEMES = {
    "paper": {
        "name": "护眼米白",
        "bg": "#FAF8F3",  # 页面底：比原来干净
        "editor_bg": "#FFFFFF",  # 写作区：纯白，最亮层
        "text": "#2D2820",  # 正文：加深，对比提升
        "caret": "#AB8250",  # 光标：与 accent 协调
        "sidebar": "#F3EEE1",  # 侧栏：暖米黄，跟页面底区分
        "accent": "#C19860",  # 强调色：更暖的金
        "muted": "#928A7C",  # 辅助色：提升对比
    },
    "parchment": {
        "name": "羊皮纸",
        "bg": "#F3E9D2",
        "editor_bg": "#F9F2E2",
        "text": "#4A3B26",
        "caret": "#9C6B3F",
        "sidebar": "#E8DCC0",
        "accent": "#A67C52",
        "muted": "#8A7856",
    },
    "snow": {
        "name": "雪白极简",
        "bg": "#F2F3F5",
        "editor_bg": "#FFFFFF",
        "text": "#2B2F36",
        "caret": "#3A6EA5",
        "sidebar": "#E9EBEE",
        "accent": "#4C7DA0",
        "muted": "#7F858F",
    },
    "bean_green": {
        "name": "豆沙绿",
        "bg": "#DDEBD8",
        "editor_bg": "#EAF5E4",
        "text": "#2F3D2B",
        "caret": "#4E7A45",
        "sidebar": "#CFE0C6",
        "accent": "#5C8C4E",
        "muted": "#6E7F63",
    },
    "sky": {
        "name": "晴空蓝",
        "bg": "#E8F0F8",
        "editor_bg": "#F7FBFF",
        "text": "#27405A",
        "caret": "#3A76B8",
        "sidebar": "#D8E6F2",
        "accent": "#4A86C5",
        "muted": "#68839A",
    },
    "rose": {
        "name": "樱花信笺",
        "bg": "#F9EDF0",
        "editor_bg": "#FFF8F9",
        "text": "#5A3440",
        "caret": "#C0607D",
        "sidebar": "#F2DDE4",
        "accent": "#D07A95",
        "muted": "#A47083",
    },
    "midnight": {
        "name": "深夜黑",
        "bg": "#20212A",
        "editor_bg": "#262833",
        "text": "#D6D8E0",
        "caret": "#8AB4F8",
        "sidebar": "#1B1C24",
        "accent": "#8AB4F8",
        "muted": "#7A7E8C",
    },
    "carbon": {
        "name": "炭灰护眼",
        "bg": "#2A2A28",
        "editor_bg": "#33332F",
        "text": "#DDD8C8",
        "caret": "#D8C08A",
        "sidebar": "#232321",
        "accent": "#C8A86A",
        "muted": "#8C887A",
    },
    "ocean_dark": {
        "name": "深海蓝",
        "bg": "#16222E",
        "editor_bg": "#1C2B3A",
        "text": "#CFE0EE",
        "caret": "#6FC3DF",
        "sidebar": "#111B25",
        "accent": "#5AB1D6",
        "muted": "#71889C",
    },
    "forest_night": {
        "name": "竹林夜",
        "bg": "#1B2420",
        "editor_bg": "#222E28",
        "text": "#D4E2D4",
        "caret": "#8FCB9B",
        "sidebar": "#151D19",
        "accent": "#7AB384",
        "muted": "#77887D",
    },
}

CUSTOM_THEME_KEY = "custom"
THEME_COLOR_FIELDS = [
    ("bg", "页面底色"),
    ("editor_bg", "正文纸色"),
    ("text", "正文文字"),
    ("sidebar", "侧栏底色"),
    ("accent", "强调色"),
    ("muted", "辅助文字"),
]
_THEME_REQUIRED_KEYS = (
    "bg",
    "editor_bg",
    "text",
    "caret",
    "sidebar",
    "accent",
    "muted",
)


def default_custom_theme() -> dict:
    t = dict(WRITING_THEMES["paper"])
    t["name"] = "我的主题"
    return t


def load_custom_theme(cfg) -> dict:
    base = default_custom_theme()
    if cfg is not None:
        import json

        raw = cfg.get_config("Writing", "custom_theme", "{}")
        try:
            data = json.loads(raw or "{}")
            if isinstance(data, dict):
                for k in _THEME_REQUIRED_KEYS:
                    v = data.get(k)
                    if isinstance(v, str) and v.strip().startswith("#"):
                        base[k] = v.strip()
                if isinstance(data.get("name"), str) and data["name"].strip():
                    base["name"] = data["name"].strip()
        except (ValueError, TypeError):
            pass
    return base


def _hex_luminance(hex_color: str) -> float:
    try:
        h = (hex_color or "").lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return 0.3
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

        def f(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    except (ValueError, TypeError):
        return 0.3


def accent_foreground(accent: str) -> str:
    return "#FFFFFF" if _hex_luminance(accent) <= 0.30 else "#262833"


def _mix_hex(a: str, b: str, ratio: float) -> str:
    try:
        ha, hb = a.lstrip("#"), b.lstrip("#")
        if len(ha) == 3:
            ha = "".join(c * 2 for c in ha)
        if len(hb) == 3:
            hb = "".join(c * 2 for c in hb)
        if len(ha) != 6 or len(hb) != 6:
            return a
        chans = []
        for i in (0, 2, 4):
            ca, cb = int(ha[i : i + 2], 16), int(hb[i : i + 2], 16)
            chans.append(round(ca + (cb - ca) * ratio))
        return "#%02X%02X%02X" % tuple(chans)
    except (ValueError, TypeError):
        return a


def with_computed_colors(theme: dict) -> dict:
    if not isinstance(theme, dict):
        return theme
    t = dict(theme)
    accent = t.get("accent", "#B08A4E")
    fg = accent_foreground(accent)
    t.setdefault("accent_fg", fg)
    t["accent_fg"] = fg
    t["accent_hover"] = _mix_hex(accent, fg, 0.18)
    return t


def get_writing_theme(theme_key: str, cfg=None) -> dict:
    if theme_key == CUSTOM_THEME_KEY:
        return with_computed_colors(load_custom_theme(cfg))
    return with_computed_colors(WRITING_THEMES.get(theme_key, WRITING_THEMES["paper"]))


_TYPING_KEYS = {
    Qt.Key_Return,
    Qt.Key_Enter,
    Qt.Key_Backspace,
    Qt.Key_Delete,
}


class _InlineContinueBar(QFrame):
    """编辑器内浮动的 AI 续写输入条（Ctrl+Enter 唤起）。"""

    generate = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inlineContinueBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(6)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("想让 AI 往哪写？（可留空，回车生成，Esc 取消）")
        self.edit.returnPressed.connect(self._on_generate)
        lay.addWidget(self.edit, 1)

        self.gen_btn = QPushButton("生成")
        self.gen_btn.setProperty("variant", "primary")
        self.gen_btn.clicked.connect(self._on_generate)
        lay.addWidget(self.gen_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.cancelled.emit)
        lay.addWidget(self.stop_btn)

        esc_sc = QShortcut(QKeySequence(Qt.Key_Escape), self.edit)
        esc_sc.setContext(Qt.WidgetShortcut)
        esc_sc.activated.connect(self.cancelled.emit)

        self.setFixedHeight(38)

    def _on_generate(self):
        self.generate.emit(self.edit.text().strip())

    def focus_input(self):
        self.edit.setFocus()
        self.edit.selectAll()

    def set_running(self, running: bool):
        self.gen_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.edit.setReadOnly(running)

    def apply_theme(self, theme):
        t = theme or {}
        bg = t.get("editor_bg", "#fcfaf4")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        text_c = t.get("text", "#3b342a")
        self.setStyleSheet(
            f"#inlineContinueBar {{ background-color: {bg};"
            f" border: 1px solid {accent}; border-radius: 8px; }}"
            f"QLineEdit {{ background-color: {bg}; color: {text_c};"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 4px; padding: 3px 6px; }}"
            f"QLineEdit:focus {{ border: 1px solid {accent}; }}"
        )


class _SkeletonDialog(QDialog):
    """AI 续写骨架确认对话框。

    打开时自动拉骨架（流式），用户可编辑，然后选择：
      - 按骨架写正文：返回 ('confirm', skeleton_text)
      - 只要骨架    ：返回 ('skeleton_only', skeleton_text)
      - 重新生成    ：不关闭，重新拉一次
      - 取消        ：返回 ('cancel', '')

    为什么需要这个对话框：
      AI 擅长编事件、不擅长写句子。让用户先看到结构决策（3-5 个事件），
      一眼能判断方向对不对，比"生成一整段再判断"效率高得多。
    """

    ACTION_CONFIRM = "confirm"
    ACTION_SKELETON_ONLY = "skeleton_only"
    ACTION_CANCEL = "cancel"

    def __init__(self, before: str, ctx: str, parent=None):
        super().__init__(parent)
        self._before = before
        self._ctx = ctx
        self._worker = None
        self._buffer = []
        self._action = self.ACTION_CANCEL
        self._skeleton_text = ""

        self.setWindowTitle("AI 续写 · 骨架确认")
        self.setMinimumSize(600, 460)
        self.setModal(True)
        self._build_ui()
        QTimer.singleShot(0, self._start_generate)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        title = QLabel("AI 续写 · 骨架")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel(
            "AI 先只出 3-5 个事件（不写正文），你看方向对不对。\n"
            "可以直接在下面改；改完选「按骨架写正文」让 AI 填血肉，\n"
            "或「只要骨架」把骨架插进编辑器自己写。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9a8e76; font-size: 12px;")
        root.addWidget(hint)

        self.skeleton_edit = QPlainTextEdit()
        self.skeleton_edit.setPlaceholderText("正在生成骨架…")
        self.skeleton_edit.setMinimumHeight(200)
        self.skeleton_edit.setFont(QFont("Microsoft YaHei", 11))
        root.addWidget(self.skeleton_edit, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9a8e76; font-size: 12px;")
        root.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.regen_btn = QPushButton("重新生成")
        self.regen_btn.clicked.connect(self._on_regenerate)
        btn_row.addWidget(self.regen_btn)

        btn_row.addStretch()

        self.skeleton_only_btn = QPushButton("只要骨架")
        self.skeleton_only_btn.setToolTip("把骨架直接插到编辑器光标处，正文你自己写")
        self.skeleton_only_btn.clicked.connect(self._on_skeleton_only)
        btn_row.addWidget(self.skeleton_only_btn)

        self.confirm_btn = QPushButton("按骨架写正文")
        self.confirm_btn.setProperty("variant", "primary")
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self.confirm_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        root.addLayout(btn_row)

    # ---------- 生成流程 ----------

    def _start_generate(self):
        self._stop_worker()
        self._buffer = []
        self.skeleton_edit.clear()
        self.skeleton_edit.setPlaceholderText("正在生成骨架…")
        self.status_label.setText("正在生成骨架…")
        self._set_buttons_enabled(False)

        try:
            from utils.ai_client import (
                AIWorker,
                load_ai_config,
                SKELETON_PROMPT,
                has_api_key,
            )
        except Exception as e:
            self.status_label.setText(f"加载 AI 模块失败：{e}")
            self._set_buttons_enabled(True)
            return

        if not has_api_key():
            self.status_label.setText("尚未配置 API Key")
            self._set_buttons_enabled(True)
            return

        prompt = SKELETON_PROMPT.format(before=self._before or "（无）")
        if self._ctx.strip():
            prompt = f"【作品背景】\n{self._ctx}\n\n{prompt}"

        cfg = load_ai_config()
        self._worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            temperature=0.6,  # 骨架不要创造性，温度调低
            stream=True,
            timeout=120.0,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_chunk(self, piece: str):
        self._buffer.append(piece)
        self.skeleton_edit.setPlainText("".join(self._buffer))
        sb = self.skeleton_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_finished(self, full_text: str):
        self._worker = None
        text = (full_text or "").strip() or "".join(self._buffer).strip()
        if not text:
            self.status_label.setText("AI 返回了空内容，可点「重新生成」")
            self._set_buttons_enabled(True)
            return
        self.skeleton_edit.setPlainText(text)
        self.status_label.setText("可编辑骨架，然后选择下方操作")
        self._set_buttons_enabled(True)

    def _on_failed(self, msg: str):
        self._worker = None
        self.status_label.setText(f"生成失败：{msg}")
        self._set_buttons_enabled(True)

    def _set_buttons_enabled(self, enabled: bool):
        self.regen_btn.setEnabled(enabled)
        self.skeleton_only_btn.setEnabled(enabled)
        self.confirm_btn.setEnabled(enabled)

    def _stop_worker(self):
        w = self._worker
        if w is None:
            return
        # 先断开信号：退休后旧线程可能还有在途 chunk（stop 主动断连，
        # 通常几十毫秒内结束），不能让它写进"重新生成"的新 buffer。
        for sig, slot in (
            (w.chunk_received, self._on_chunk),
            (w.finished_ok, self._on_finished),
            (w.failed, self._on_failed),
        ):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            from utils.ai_client import retire_worker

            retire_worker(w)
        except Exception:
            pass
        self._worker = None

    # ---------- 用户操作 ----------

    def _on_regenerate(self):
        self._start_generate()

    def _on_skeleton_only(self):
        text = self.skeleton_edit.toPlainText().strip()
        if not text:
            return
        self._skeleton_text = text
        self._action = self.ACTION_SKELETON_ONLY
        self._stop_worker()
        self.accept()

    def _on_confirm(self):
        text = self.skeleton_edit.toPlainText().strip()
        if not text:
            return
        self._skeleton_text = text
        self._action = self.ACTION_CONFIRM
        self._stop_worker()
        self.accept()

    def reject(self):
        self._stop_worker()
        self._action = self.ACTION_CANCEL
        super().reject()

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    @property
    def action(self) -> str:
        return self._action

    @property
    def skeleton_text(self) -> str:
        return self._skeleton_text


class WritingEditor(QWidget):
    """限宽居中的写作编辑器容器。"""

    content_changed = Signal()
    selection_changed = Signal(str)
    request_rewrite = Signal()
    search_selection = Signal(str)  # 右键菜单"查找选中文字"
    font_size_changed = Signal(int)  # Ctrl+滚轮 触发的字号变化
    inline_continue_requested = Signal(int, str)  # (pos, direction) AI 光标续写

    def __init__(self, parent=None):
        super().__init__(parent)
        self._typing = False
        self._theme_key = "paper"
        self._theme = get_writing_theme("paper")
        self._font_family = "Microsoft YaHei"
        self._font_size = 15
        self._line_spacing = 1.8
        self._content_width = 720
        # 段首缩进前缀（"\u3000\u3000"=两个全角空格 / "  "=两个半角 / ""=不缩进）
        self._para_indent = "\u3000\u3000"
        self._loading = False
        self._sym_session = None
        self._applying_live = False
        # 查找高亮
        self._find_target = ""
        self._find_matches = []
        self._find_current = -1
        self._find_selections = []
        # 校对高亮
        self._proof_selections = []
        # 词条高亮
        self._dict_selections = []
        # 闪烁反馈
        self._flash_selections = []
        # AI 光标续写
        self._inline_bar = None
        self._inline_anchor = -1
        self._inline_streaming = False
        self._inline_cursor = None
        # 快速续写（Ctrl+Shift+Space）的幽灵文本状态
        self._quick_continue_start = -1
        # 幽灵文本的结束位置。生成期间用户可移动光标，
        # 采纳/放弃时必须用这个记录值，不能用实时 textCursor()。
        self._quick_continue_end = -1
        self._quick_continue_active = False
        self._quick_continue_worker = None
        self._quick_continue_seq = 0

        self._build_ui()
        # Ctrl+滚轮 改字号：把内部编辑器的信号接到自己的处理逻辑
        self.edit.font_size_delta.connect(self._on_font_size_delta)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self.content_changed.emit)

        self._typewriter_timer = QTimer(self)
        self._typewriter_timer.setSingleShot(True)
        self._typewriter_timer.setInterval(0)
        self._typewriter_timer.timeout.connect(self._do_typewriter_scroll)

        # 段落聚焦：光标所在段 100% 不透明，其它段降到 ~32%。
        # 默认关闭，由 workspace 从配置注入。
        self._paragraph_focus_enabled = False
        self._para_focus_timer = QTimer(self)
        self._para_focus_timer.setSingleShot(True)
        self._para_focus_timer.setInterval(80)
        self._para_focus_timer.timeout.connect(self._apply_paragraph_focus)

        self.apply_typography()

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.edit = _InnerPlainEdit(self)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.cursorPositionChanged.connect(self._on_cursor_moved)
        self.edit.selectionChanged.connect(self._emit_selection)
        self.edit.selectionChanged.connect(self._check_live_session)
        self.edit.search_selection.connect(self.search_selection.emit)
        outer.addWidget(self.edit, 1)

    def _on_font_size_delta(self, delta: int):
        """Ctrl+滚轮：±1px 调整字号，范围 12-28（和设置里的边界一致）。

        每次滚轮 1 格 = 1px；滚快了会触发多次，按步进累加。
        """
        new_size = self._font_size + delta
        new_size = max(12, min(28, new_size))
        if new_size == self._font_size:
            return
        self.apply_typography(size=new_size)
        self.font_size_changed.emit(new_size)
        # 字号变化导致行高变化，下一事件循环把光标拉回可视区，
        # 否则连续滚轮缩放后光标会跑到视口外
        QTimer.singleShot(0, self.edit.ensureCursorVisible)

    def change_font_size(self, delta: int):
        """按 ±delta 调整字号（供快捷键使用）。"""
        self._on_font_size_delta(delta)

    def set_font_size(self, size: int):
        """设置到具体字号（供 Ctrl+0 复位使用）。"""
        size = max(12, min(28, size))
        if size == self._font_size:
            return
        self.apply_typography(size=size)
        self.font_size_changed.emit(size)

    def set_focus_mode(self, enabled: bool):
        pass

    # ---------- 快速续写（Ctrl+Shift+Space，两步走） ----------

    def _trigger_quick_continue(self):
        """Ctrl+Shift+Space：AI 快速续写（两步走）。

        第一步：弹骨架对话框，AI 出 3-5 个事件，用户看/编辑后确认。
        第二步：按骨架写正文，流式写入编辑器。

        为什么拆两步：
          AI 擅长编事件、不擅长写句子。让用户先对结构拍板，
          再让 AI 填血肉。用户也可以只要骨架，正文自己写。
        """
        from utils.ai_client import has_api_key

        if not has_api_key():
            self._show_quick_continue_hint("尚未配置 API Key")
            return
        if self._quick_continue_active:
            return

        cursor = self.edit.textCursor()
        if cursor.hasSelection():
            self._show_quick_continue_hint("请先取消文本选择再续写")
            return

        pos = cursor.position()
        full = self.get_text()

        # 前文取 600 字；能对齐到段落边界就对齐，避免从半句切进去。
        raw_before = full[max(0, pos - 800) : pos]
        cut = raw_before.find("\n")
        if 0 <= cut < 200:
            before = raw_before[cut + 1 :]
        else:
            before = raw_before[-600:]

        ctx = ""
        try:
            w = self.window()
            ws = getattr(w, "workspace", None)
            if ws is not None and hasattr(ws, "_build_inline_context"):
                ctx = ws._build_inline_context() or ""
        except Exception:
            pass

        # ---- 第一步：骨架确认 ----
        dlg = _SkeletonDialog(before, ctx, parent=self)
        dlg.exec()

        if dlg.action == _SkeletonDialog.ACTION_CANCEL:
            return
        skeleton = dlg.skeleton_text
        if not skeleton:
            return

        # ---- 分支 A：只要骨架 ----
        if dlg.action == _SkeletonDialog.ACTION_SKELETON_ONLY:
            cur = self.edit.textCursor()
            cur.setPosition(pos)
            cur.insertText(f"\n{skeleton}\n\n")
            self.edit.setTextCursor(cur)
            self.edit.ensureCursorVisible()
            self._debounce.start()
            self._show_quick_continue_hint("骨架已插入，可自己写正文")
            return

        # ---- 分支 B：按骨架写正文 ----
        self._start_continue_from_skeleton(pos, before, ctx, skeleton)

    def _start_continue_from_skeleton(
        self, pos: int, before: str, ctx: str, skeleton: str
    ):
        """第二步：按骨架写正文，流式写入编辑器。

        与旧的 _trigger_quick_continue 后半段一致，只是 prompt 换成
        「按骨架写正文」，并把前文替换成骨架。
        """
        from utils.ai_client import (
            AIWorker,
            load_ai_config,
            FULL_FROM_SKELETON_PROMPT,
        )

        prompt = FULL_FROM_SKELETON_PROMPT.format(
            skeleton=skeleton, before=before or "（无）"
        )
        if ctx.strip():
            prompt = f"【作品背景】\n{ctx}\n\n{prompt}"

        cfg = load_ai_config()
        self._quick_continue_seq += 1
        seq = self._quick_continue_seq
        self._quick_continue_start = pos
        self._quick_continue_end = pos
        self._quick_continue_active = True
        self._quick_continue_first_chunk = False

        placeholder = "⋯ AI 按骨架写正文中 ⋯"
        self._quick_continue_placeholder_len = len(placeholder)
        cur = self.edit.textCursor()
        cur.setPosition(pos)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor((self._theme or {}).get("muted", "#9A8E76")))
        fmt.setFontItalic(True)
        cur.insertText(placeholder, fmt)
        self.edit.setTextCursor(cur)
        self.edit.ensureCursorVisible()

        self._show_quick_continue_hint(
            "AI 正按骨架写正文…（Tab 采纳 / Esc 放弃 / Ctrl+Enter 重写）"
        )

        worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            temperature=cfg.get("creative_temperature", 0.85),
            stream=True,
            timeout=120.0,
        )
        self._quick_continue_worker = worker
        worker.chunk_received.connect(
            lambda piece, s=seq: self._on_quick_continue_chunk(piece, s)
        )
        worker.finished_ok.connect(
            lambda text, s=seq: self._on_quick_continue_finished(text, s)
        )
        worker.failed.connect(lambda msg, s=seq: self._on_quick_continue_failed(msg, s))
        worker.start()

    def _delete_quick_continue_placeholder(self):
        """删除光标处的占位符。只在首 chunk 前调用一次。"""
        n = getattr(self, "_quick_continue_placeholder_len", 0)
        if n <= 0:
            return
        start = self._quick_continue_start
        cur = self.edit.textCursor()
        cur.setPosition(start)
        cur.setPosition(start + n, QTextCursor.KeepAnchor)
        cur.removeSelectedText()
        cur.setPosition(start)
        self.edit.setTextCursor(cur)
        self._quick_continue_placeholder_len = 0

    def _on_quick_continue_chunk(self, piece: str, seq: int):
        if seq != self._quick_continue_seq or not piece:
            return

        # 首个 chunk：删掉占位符
        if not getattr(self, "_quick_continue_first_chunk", False):
            self._quick_continue_first_chunk = True
            self._delete_quick_continue_placeholder()

        # 插入灰色斜体内容
        cur = self.edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor((self._theme or {}).get("muted", "#9A8E76")))
        fmt.setFontItalic(True)
        cur.insertText(piece, fmt)
        self.edit.setTextCursor(cur)
        self.edit.ensureCursorVisible()
        # 记录幽灵文本的结束位置。用户可能在生成过程中用方向键移动
        # 光标（方向键在 keyPressEvent 里放行），采纳/放弃时不能再实时
        # 读 textCursor().position()，否则会把光标移动后的位置当结尾。
        self._quick_continue_end = cur.position()

    def _on_quick_continue_finished(self, full_text: str, seq: int):
        if seq != self._quick_continue_seq:
            return
        self._quick_continue_worker = None
        # 若一个 chunk 都没到（罕见），删占位
        if not getattr(self, "_quick_continue_first_chunk", False):
            self._delete_quick_continue_placeholder()
        # 保持 active，等 Tab / Esc
        self._show_quick_continue_hint(
            "生成完成 · Tab 采纳 / Esc 放弃 / Ctrl+Enter 重写"
        )

    def _on_quick_continue_failed(self, msg: str, seq: int):
        if seq != self._quick_continue_seq:
            return
        self._quick_continue_worker = None
        self._delete_quick_continue_placeholder()
        self._quick_continue_active = False
        self._quick_continue_start = -1
        self._quick_continue_end = -1
        self._show_quick_continue_hint(f"AI 续写失败：{msg}")

    def _accept_quick_continue(self):
        """Tab：幽灵文本转为正常格式。"""
        if not self._quick_continue_active:
            return
        start = self._quick_continue_start
        # 用记录的结束位置，不用实时光标位置：生成期间用户可能
        # 用方向键移动过光标，实时位置和幽灵文本范围不一致，
        # 会把用户没想动的区域也一并改格式。
        end = getattr(self, "_quick_continue_end", -1)
        if end <= start:
            # 兜底：无记录时退回实时光标（罕见）
            end = self.edit.textCursor().position()
        if end <= start:
            self._quick_continue_active = False
            self._quick_continue_start = -1
            self._quick_continue_end = -1
            self._quick_continue_placeholder_len = 0
            return
        cur = self.edit.textCursor()
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setFontItalic(False)
        try:
            t = self._theme or {}
            fmt.setForeground(QColor(t.get("text", "#3b342a")))
        except Exception:
            pass
        cur.setCharFormat(fmt)
        cur.setPosition(end)
        self.edit.setTextCursor(cur)
        self._quick_continue_active = False
        self._quick_continue_start = -1
        self._quick_continue_end = -1
        self._quick_continue_placeholder_len = 0

        # 采纳后必须停 worker + 递增 seq。
        # 否则：用户在流式输出中按 Tab 采纳，worker 继续吐 chunk，
        # _on_quick_continue_chunk 的 seq 仍匹配 → 继续 insert 到正文，
        # 用户看到"采纳后 AI 还在自己往外冒字"。
        # retire_worker 主动关闭连接让线程快速结束；万一没结束也由
        # 全局 Reaper 保活，不再"wait 超时即置空"导致运行中析构。
        self._quick_continue_seq += 1
        w = self._quick_continue_worker
        if w is not None:
            try:
                from utils.ai_client import retire_worker

                retire_worker(w)
            except Exception:
                pass
        self._quick_continue_worker = None

        self._debounce.start()
        self._show_quick_continue_hint("已采纳 AI 续写")

    def _reject_quick_continue(self):
        """Esc：删除占位符 + 幽灵文本。"""
        if not self._quick_continue_active:
            return
        start = self._quick_continue_start
        # 若有占位符未删，先删它
        self._delete_quick_continue_placeholder()
        # 用记录的结束位置，不用实时光标位置（用户可能移动过光标）
        end = getattr(self, "_quick_continue_end", -1)
        if end <= start:
            end = self.edit.textCursor().position()
        if end > start:
            cur = self.edit.textCursor()
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            cur.removeSelectedText()
            cur.setPosition(start)
            self.edit.setTextCursor(cur)
        self._quick_continue_active = False
        self._quick_continue_start = -1
        self._quick_continue_end = -1
        self._quick_continue_placeholder_len = 0
        # 若 worker 还在跑，退休它（stop 主动断连；Reaper 兜底保活，
        # 杜绝 "QThread: Destroyed while thread is still running"）。
        w = self._quick_continue_worker
        if w is not None:
            try:
                from utils.ai_client import retire_worker

                retire_worker(w)
            except Exception:
                pass
        self._quick_continue_worker = None
        self._quick_continue_seq += 1  # 让旧的回调失效

    def _restart_quick_continue(self):
        """Ctrl+Enter：清掉当前幽灵文本，重新生成。"""
        self._reject_quick_continue()
        self._trigger_quick_continue()

    def _show_quick_continue_hint(self, msg: str):
        """状态栏短暂提示。"""
        try:
            w = self.window()
            ws = getattr(w, "workspace", None)
            if ws is not None and hasattr(ws, "save_state_label"):
                ws.save_state_label.setText(msg)
                # 提示 4 秒后自动清空（若未被新提示覆盖）
                from PySide6.QtCore import QTimer

                QTimer.singleShot(4000, lambda: ws.save_state_label.setText(""))
        except Exception:
            pass

    # ---------- AI 光标续写 ----------
    def _trigger_inline_continue(self):
        """由 _InnerPlainEdit 在 Ctrl+Enter 时调用。"""
        cursor = self.edit.textCursor()
        self._inline_anchor = cursor.position()
        self._show_inline_bar()

    def _show_inline_bar(self):
        if self._inline_bar is None:
            self._inline_bar = _InlineContinueBar(self)
            self._inline_bar.generate.connect(self._on_inline_generate)
            self._inline_bar.cancelled.connect(self._cancel_inline)
        self._inline_bar.set_running(False)
        self._inline_bar.apply_theme(self._theme)
        w = min(520, max(320, self.width() - 80))
        self._inline_bar.setFixedWidth(w)
        self._inline_bar.move((self.width() - w) // 2, 16)
        self._inline_bar.show()
        self._inline_bar.raise_()
        self._inline_bar.focus_input()

    def _hide_inline_bar(self):
        if self._inline_bar is not None:
            self._inline_bar.hide()

    def _on_inline_generate(self, direction: str):
        if self._inline_anchor < 0:
            return
        if self._inline_bar is not None:
            self._inline_bar.set_running(True)
        self.inline_continue_requested.emit(self._inline_anchor, direction)

    def _cancel_inline(self):
        """用户取消（Esc 或点"停止"）。"""
        self.finish_inline_stream()

    def start_inline_stream(self, pos: int):
        cursor = self.edit.textCursor()
        cursor.setPosition(pos)
        cursor.beginEditBlock()
        self._inline_cursor = cursor
        self._inline_streaming = True
        self.edit.setTextCursor(cursor)

    def append_inline_chunk(self, text: str):
        if not self._inline_streaming or self._inline_cursor is None:
            return
        self._inline_cursor.insertText(text)
        self.edit.setTextCursor(self._inline_cursor)
        self.edit.ensureCursorVisible()

    def finish_inline_stream(self):
        if self._inline_streaming and self._inline_cursor is not None:
            try:
                self._inline_cursor.endEditBlock()
            except Exception:
                pass
        self._inline_streaming = False
        self._inline_cursor = None
        self._inline_anchor = -1
        if self._inline_bar is not None:
            self._inline_bar.set_running(False)
        self._hide_inline_bar()
        self.edit.setFocus()
        self._debounce.start()

    def abort_streams_for_reload(self):
        """切章 / 切作品前中止一切会继续往文档里写内容的 AI 流。

        若不中止：inline 流持有的 QTextCursor 会在 setPlainText 清空后的
        新文档上继续 insertText，把旧章节的 AI 续写写进新章节，并经
        自动保存落盘（跨章内容污染）。幽灵续写同理。

        注意：本方法只负责编辑器自身状态与 _quick_continue_worker；
        挂在 workspace 上的 _inline_worker 由 workspace 先退休。
        不启动 debounce——调用方随后会显式保存旧章 / 加载新章。
        """
        # 1. inline 流状态复位（endEditBlock 收口已插入内容，
        #    交给调用方的 flush 存进旧章）
        if self._inline_streaming and self._inline_cursor is not None:
            try:
                self._inline_cursor.endEditBlock()
            except Exception:
                pass
        self._inline_streaming = False
        self._inline_cursor = None
        self._inline_anchor = -1
        if self._inline_bar is not None:
            try:
                self._inline_bar.set_running(False)
                self._inline_bar.hide()
            except RuntimeError:
                pass
        # 2. 幽灵续写：停 worker（retire 保活，杜绝运行中析构），
        #    递增 seq 让在途 chunk 回调全部失效，重置采纳状态。
        w = getattr(self, "_quick_continue_worker", None)
        if w is not None:
            try:
                from utils.ai_client import retire_worker

                retire_worker(w)
            except Exception:
                pass
            self._quick_continue_worker = None
        self._quick_continue_seq += 1
        self._quick_continue_active = False
        self._quick_continue_start = -1
        self._quick_continue_end = -1
        self._quick_continue_placeholder_len = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._inline_bar is not None and self._inline_bar.isVisible():
            w = self._inline_bar.width()
            self._inline_bar.move((self.width() - w) // 2, 16)

    # ---------- 对外 API ----------
    def load_text(self, text: str):
        # 兜底：任何路径调用 load_text 切换文档前，先中止 AI 流，
        # 防止流式 chunk 穿过 setPlainText 污染新章节。
        if (
            self._inline_streaming
            or getattr(self, "_quick_continue_active", False)
            or getattr(self, "_quick_continue_worker", None) is not None
        ):
            self.abort_streams_for_reload()
        self._loading = True
        self._sym_session = None
        self._find_target = ""
        self._find_matches = []
        self._find_current = -1
        self._find_selections = []
        self._proof_selections = []
        self._dict_selections = []
        self._flash_selections = []
        self.edit.setExtraSelections([])
        self.edit.setPlainText(text or "")
        self._apply_block_format()
        # 空白新章节：直接带上段首缩进前缀，光标落在前缀之后。
        # 必须在 _loading 解除前插入，避免触发 content_changed 保存链。
        prefix = self._para_indent
        if prefix and not (text or ""):
            cur = self.edit.textCursor()
            cur.movePosition(QTextCursor.Start)
            cur.insertText(prefix)
            self.edit.setTextCursor(cur)
        else:
            self.edit.moveCursor(QTextCursor.Start)
        self.edit.document().setModified(False)
        self._loading = False

    def get_text(self) -> str:
        return self.edit.toPlainText()

    def set_focus(self):
        self.edit.setFocus()

    def insert_text(self, text: str):
        """在光标处插入文本，滚动到光标，并短暂闪烁高亮。"""
        if not text:
            return
        cursor = self.edit.textCursor()
        start_pos = cursor.position()
        cursor.insertText(text)
        end_pos = cursor.position()
        # 关键：textCursor() 返回的是值拷贝。insertText 改了文档但不会
        # 移动编辑器自身持有的光标；不 setTextCursor 同步的话，用户
        # 继续打字会插到刚插入文本的前面。
        self.edit.setTextCursor(cursor)
        self.edit.ensureCursorVisible()
        self.edit.setFocus()
        self._flash_inserted_text(start_pos=start_pos, end_pos=end_pos)

    def _flash_inserted_text(self, start_pos: int = None,
                             end_pos: int = None):
        if start_pos is None or end_pos is None or end_pos <= start_pos:
            return
        doc = self.edit.document()
        doc_len = doc.characterCount() - 1
        start_pos = max(0, min(start_pos, doc_len))
        end_pos = max(start_pos, min(end_pos, doc_len))
        if start_pos >= end_pos:
            return
        sel = QTextEdit.ExtraSelection()
        c = QTextCursor(doc)
        c.setPosition(start_pos)
        c.setPosition(end_pos, QTextCursor.KeepAnchor)
        sel.cursor = c
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 210, 80, 200))
        fmt.setForeground(QColor(60, 30, 0))
        sel.format = fmt
        self._flash_selections = [sel]
        self._refresh_all_highlights()
        QTimer.singleShot(600, self._clear_flash)

    def _clear_flash(self):
        self._flash_selections = []
        self._refresh_all_highlights()

    def locate_position(self, start: int, end: int = None):
        """把光标移动并选中正文里的指定区间，滚动可见并短暂高亮。

        供 AI 对话"回到原文"跳转使用（坐标为纯文本字符偏移）。
        """
        doc = self.edit.document()
        doc_len = max(0, doc.characterCount() - 1)
        start = max(0, min(int(start), doc_len))
        if end is None:
            end = min(start + 24, doc_len)
        else:
            end = max(start, min(int(end), doc_len))
        c = QTextCursor(doc)
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        self.edit.setTextCursor(c)
        self.edit.ensureCursorVisible()
        self.edit.setFocus()
        self._flash_inserted_text(start_pos=start, end_pos=end)

    def has_selection(self) -> bool:
        return self.edit.textCursor().hasSelection()

    def selected_text(self) -> str:
        return self.edit.textCursor().selectedText()

    def replace_selected_text(self, text: str):
        cursor = self.edit.textCursor()
        if cursor.hasSelection():
            cursor.insertText(text)

    # ---------- 符号面板操作 ----------
    def wrap_selected(self, left: str, right: str = ""):
        right = right or left
        cursor = self.edit.textCursor()
        if cursor.hasSelection():
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            cursor.beginEditBlock()
            cursor.setPosition(end)
            cursor.insertText(right)
            cursor.setPosition(start)
            cursor.insertText(left)
            cursor.endEditBlock()
            new_c = self.edit.textCursor()
            new_c.setPosition(start + len(left))
            new_c.setPosition(end + len(left), QTextCursor.KeepAnchor)
            self.edit.setTextCursor(new_c)
        else:
            self.edit.insertPlainText(left + right)
            c = self.edit.textCursor()
            c.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, len(right))
            self.edit.setTextCursor(c)
        self.edit.setFocus()

    def apply_symbol_to_selection(
        self,
        kind: str,
        sym: str,
        every: int = 1,
        skip_ws: bool = True,
        delimiter: str = "",
        connector: str = "delim",
    ) -> bool:
        sym = sym or ""
        if not sym:
            return False
        cursor = self.edit.textCursor()
        if not cursor.hasSelection():
            return False
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        text = self.get_text()[start:end]
        new_text = build_edge_result(
            text, kind, sym, every, skip_ws, delimiter, connector
        )
        if new_text is None:
            return False
        self._replace_span(start, end, new_text, select_new=True)
        self._sym_session = None
        self.edit.setFocus()
        return True

    def apply_symbol_live(
        self,
        kind: str,
        sym: str,
        every: int = 1,
        skip_ws: bool = True,
        delimiter: str = "",
        connector: str = "delim",
    ) -> bool:
        sym = sym or ""
        if not sym:
            return False
        edit = self.edit
        if self._sym_session is None:
            cursor = edit.textCursor()
            if not cursor.hasSelection():
                return False
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            base = self.get_text()[start:end]
            self._sym_session = {"start": start, "base": base, "last_len": end - start}
        s = self._sym_session
        new_text = build_edge_result(
            s["base"], kind, sym, every, skip_ws, delimiter, connector
        )
        if new_text is None:
            return False
        start = s["start"]
        end = start + s["last_len"]
        self._replace_span(start, end, new_text, select_new=True)
        s["last_len"] = len(new_text)
        self.edit.setFocus()
        return True

    def revert_symbol_live(self):
        s = self._sym_session
        if s is None:
            return
        start = s["start"]
        end = start + s["last_len"]
        self._replace_span(start, end, s["base"], select_new=True)
        self._sym_session = None

    def _replace_span(
        self, start: int, end: int, new_text: str, select_new: bool = True
    ):
        self._applying_live = True
        try:
            c = self.edit.textCursor()
            c.beginEditBlock()
            c.setPosition(start)
            c.setPosition(end, QTextCursor.KeepAnchor)
            c.insertText(new_text)
            c.endEditBlock()
            if select_new:
                nc = self.edit.textCursor()
                nc.setPosition(start)
                nc.setPosition(start + len(new_text), QTextCursor.KeepAnchor)
                self.edit.setTextCursor(nc)
        finally:
            self._applying_live = False

    def _check_live_session(self):
        s = self._sym_session
        if s is None or self._applying_live or self._loading:
            return
        c = self.edit.textCursor()
        if (c.selectionStart(), c.selectionEnd()) != (
            s["start"],
            s["start"] + s["last_len"],
        ):
            self._sym_session = None

    def modify_selected_lines(self, prefix: str = "", suffix: str = "") -> int:
        cursor = self.edit.textCursor()
        if not cursor.hasSelection():
            return 0
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        block = self.get_text()[start:end]
        lines = block.split("\n")
        changed = 0
        out = []
        for ln in lines:
            if ln.strip():
                out.append(prefix + ln + suffix)
                changed += 1
            else:
                out.append(ln)
        new_text = "\n".join(out)
        c = self.edit.textCursor()
        c.beginEditBlock()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        c.insertText(new_text)
        c.endEditBlock()
        nc = self.edit.textCursor()
        nc.setPosition(start)
        nc.setPosition(start + len(new_text), QTextCursor.KeepAnchor)
        self.edit.setTextCursor(nc)
        self.edit.setFocus()
        return changed

    def convert_selected(self, kind: str) -> int:
        cursor = self.edit.textCursor()
        if not cursor.hasSelection():
            return 0
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        text = self.get_text()[start:end]

        if kind == "halfwidth":
            table = {
                ",": "，",
                ".": "。",
                "!": "！",
                "?": "？",
                ":": "：",
                ";": "；",
                "(": "（",
                ")": "）",
                "~": "～",
            }
            new_text = "".join(table.get(ch, ch) for ch in text)
        else:
            open_ch, close_ch = ("「", "」") if kind == "corner" else ("『", "』")
            src_ch = '"' if kind == "corner" else "'"
            out = []
            opened = False
            for ch in text:
                if ch == src_ch:
                    out.append(open_ch if not opened else close_ch)
                    opened = not opened
                else:
                    out.append(ch)
            new_text = "".join(out)

        if new_text == text:
            return 0
        cursor.beginEditBlock()
        cursor.insertText(new_text)
        cursor.endEditBlock()
        nc = self.edit.textCursor()
        nc.setPosition(start)
        nc.setPosition(start + len(new_text), QTextCursor.KeepAnchor)
        self.edit.setTextCursor(nc)
        self.edit.setFocus()
        return 1

    def insert_divider(self, line: str):
        cursor = self.edit.textCursor()
        pos = cursor.position()
        full = self.get_text()
        before = full[:pos]
        prefix = (
            ""
            if (not before or before.endswith("\n\n"))
            else ("\n" if before.endswith("\n") else "\n\n")
        )
        after = full[pos:]
        if after.startswith("\n\n"):
            suffix = ""
        elif after.startswith("\n"):
            suffix = "\n"
        else:
            suffix = "\n\n" if after else "\n"
        self.edit.insertPlainText(prefix + line + suffix)
        self.edit.setFocus()

    def undo(self):
        self.edit.undo()

    def redo(self):
        self.edit.redo()

    def find_text(self, target: str, forward: bool = True) -> bool:
        from PySide6.QtGui import QTextDocument

        flags = QTextDocument.FindFlag(0) if forward else QTextDocument.FindBackward
        found = self.edit.document().find(target, self.edit.textCursor(), flags)
        if not found.isNull():
            self.edit.setTextCursor(found)
            self.edit.ensureCursorVisible()
            return True
        return False

    # ---------- 查找高亮 ----------
    def highlight_all_matches(self, target: str) -> int:
        self._find_target = target
        self._find_matches = []
        self._find_current = -1
        if not target:
            self._find_selections = []
            self._refresh_all_highlights()
            return 0
        doc = self.edit.document()
        pos = 0
        while True:
            found = doc.find(target, pos)
            if found.isNull():
                break
            start = found.selectionStart()
            end = found.selectionEnd()
            if end <= start:
                break
            self._find_matches.append((start, end))
            pos = end
        self._apply_highlights(self._find_matches, current=-1)
        return len(self._find_matches)

    def clear_highlights(self):
        self._find_target = ""
        self._find_matches = []
        self._find_current = -1
        self._find_selections = []
        self._refresh_all_highlights()

    def goto_match(self, index: int) -> bool:
        if not getattr(self, "_find_matches", None):
            return False
        n = len(self._find_matches)
        if n == 0:
            return False
        if index < 0:
            index = n - 1
        elif index >= n:
            index = 0
        start, end = self._find_matches[index]
        c = QTextCursor(self.edit.document())
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        self.edit.setTextCursor(c)
        self.edit.ensureCursorVisible()
        self._find_current = index
        self._apply_highlights(self._find_matches, current=index)
        return True

    def current_match_index(self) -> int:
        if not getattr(self, "_find_matches", None):
            return -1
        return getattr(self, "_find_current", -1)

    def match_count(self) -> int:
        return len(getattr(self, "_find_matches", []))

    def _apply_highlights(self, matches: list, current: int = -1):
        selections = []
        doc = self.edit.document()
        for i, (s, e) in enumerate(matches):
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(doc)
            cur.setPosition(s)
            cur.setPosition(e, QTextCursor.KeepAnchor)
            sel.cursor = cur
            fmt = QTextCharFormat()
            if i == current:
                fmt.setBackground(QColor(255, 196, 0, 230))
                fmt.setForeground(QColor(20, 20, 20))
            else:
                # 深色主题下浅黄半透明压在暗底上会发脏，改用琥珀色
                try:
                    dark = QColor(
                        (self._theme or {}).get("bg", "#f7f2e8")).lightness() < 128
                except Exception:
                    dark = False
                fmt.setBackground(
                    QColor(245, 170, 40, 110) if dark
                    else QColor(255, 240, 100, 150))
            sel.format = fmt
            selections.append(sel)
        self._find_selections = selections
        self._refresh_all_highlights()

    def _refresh_all_highlights(self):
        all_sel = list(getattr(self, "_find_selections", []))
        all_sel += list(getattr(self, "_proof_selections", []))
        all_sel += list(getattr(self, "_dict_selections", []))
        all_sel += list(getattr(self, "_flash_selections", []))
        try:
            self.edit.setExtraSelections(all_sel)
        except Exception as e:
            from utils.logger import get_logger

            get_logger().warning(f"setExtraSelections 失败: {e}")

    # ---------- 校对高亮 ----------
    def highlight_proofread(self, items: list, filter_cat: str = ""):
        # 深色写作主题（如深夜黑/竹林夜）下，旧的深色前景+半透明深底
        # 会和暗背景融成一团看不清，因此按页面底色亮度给两套配色：
        # 半透明高亮底 + 与暗背景高对比的浅前景。
        try:
            is_dark = QColor((self._theme or {}).get("bg", "#f7f2e8")).lightness() < 128
        except Exception:
            is_dark = False

        if is_dark:
            palette = {
                "敏感词": (QColor(248, 81, 73, 72), QColor(255, 179, 174)),
                "错别字": (QColor(245, 180, 60, 72), QColor(255, 214, 140)),
                "标点": (QColor(167, 139, 250, 72), QColor(205, 192, 255)),
                "格式": (QColor(190, 190, 190, 60), QColor(226, 226, 226)),
                "其他": (QColor(96, 178, 255, 64), QColor(172, 213, 255)),
            }
        else:
            palette = {
                "敏感词": (QColor(220, 70, 70, 100), QColor(120, 20, 20)),
                "错别字": (QColor(240, 180, 40, 120), QColor(90, 60, 0)),
                "标点": (QColor(140, 120, 220, 100), QColor(60, 40, 120)),
                "格式": (QColor(140, 140, 140, 100), QColor(40, 40, 40)),
                "其他": (QColor(80, 160, 240, 90), QColor(20, 60, 120)),
            }

        def _color_for(category: str):
            if "敏感词" in category:
                return palette["敏感词"]
            if "错别字" in category or "自定义" in category:
                return palette["错别字"]
            if "标点" in category:
                return palette["标点"]
            if "格式" in category:
                return palette["格式"]
            return palette["其他"]

        doc = self.edit.document()
        doc_len = doc.characterCount() - 1
        selections = []
        for r in items:
            if filter_cat and filter_cat not in r.get("category", ""):
                continue
            s, e = r.get("start", 0), r.get("end", 0)
            if e <= s or s < 0 or e > doc_len:
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(doc)
            cur.setPosition(s)
            cur.setPosition(e, QTextCursor.KeepAnchor)
            sel.cursor = cur
            bg, fg = _color_for(r.get("category", ""))
            fmt = QTextCharFormat()
            fmt.setBackground(bg)
            fmt.setForeground(fg)
            sel.format = fmt
            selections.append(sel)

        self._proof_selections = selections
        self._refresh_all_highlights()

    def clear_proofread_highlights(self):
        self._proof_selections = []
        self._refresh_all_highlights()

    # ---------- 词条高亮（人物卡 + 高频词） ----------
    def highlight_dictionary(self, items: list):
        """把人物卡 + 高频词里出现的名字在正文中高亮。

        items: [{"word": str, "color": "#rrggbb", "label": str}, ...]

        性能护栏：词条数超过 400 时截断；这足以覆盖任何小说的常用词库，
        避免 O(N×M) 高亮扫描把主线程拖卡。
        """
        # ---- 性能护栏 ----
        if len(items) > 400:
            items = items[:400]
        # ---- 护栏结束 ----

        self._dict_selections = []
        text = self.edit.toPlainText()
        if not text or not items:
            self._refresh_all_highlights()
            return

        sorted_items = sorted(items, key=lambda it: -len(it.get("word", "")))
        occupied = [False] * len(text)
        hits = []

        for it in sorted_items:
            word = it.get("word", "")
            if not word:
                continue
            color = it.get("color", "#64748B")
            wl = len(word)
            start = 0
            while True:
                pos = text.find(word, start)
                if pos < 0:
                    break
                end = pos + wl
                if not any(occupied[pos:end]):
                    hits.append((pos, end, color))
                    for i in range(pos, end):
                        occupied[i] = True
                start = pos + 1

        if not hits:
            self._refresh_all_highlights()
            return

        doc = self.edit.document()
        doc_len = doc.characterCount() - 1
        selections = []

        try:
            is_dark = QColor((self._theme or {}).get("bg", "#f7f2e8")).lightness() < 128
        except Exception:
            is_dark = False

        for s, e, color_hex in hits:
            if s < 0 or e > doc_len:
                continue
            c = QColor(color_hex)
            if not c.isValid():
                c = QColor("#64748B")
            bg = QColor(c)
            bg.setAlpha(80)
            fg = QColor(c).lighter(160) if is_dark else QColor(c).darker(180)
            ul = QColor(c)

            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(doc)
            cur.setPosition(s)
            cur.setPosition(e, QTextCursor.KeepAnchor)
            sel.cursor = cur
            fmt = QTextCharFormat()
            fmt.setBackground(bg)
            fmt.setForeground(fg)
            fmt.setUnderlineStyle(QTextCharFormat.SingleUnderline)
            fmt.setUnderlineColor(ul)
            sel.format = fmt
            selections.append(sel)

        self._dict_selections = selections
        self._refresh_all_highlights()

    def clear_dict_highlights(self):
        self._dict_selections = []
        self._refresh_all_highlights()

    # ---------- 排版 / 主题 ----------
    def apply_typography(
        self,
        family: str = None,
        size: int = None,
        line_spacing: float = None,
        content_width: int = None,
        para_indent: str = None,
    ):
        if family is not None:
            self._font_family = family
        if size is not None:
            self._font_size = size
        if line_spacing is not None:
            self._line_spacing = line_spacing
        if content_width is not None:
            self._content_width = content_width
        if para_indent is not None:
            self._para_indent = para_indent
            self.edit.set_para_indent(para_indent)
        font = QFont(self._font_family, self._font_size)
        self.edit.setFont(font)
        self._apply_block_format()
        # 把限宽交给内部编辑器计算居中边距
        self.edit.set_content_width(self._content_width)
        self.apply_theme(self._theme_key)

    def _apply_block_format(self):
        doc = self.edit.document()
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(float(self._line_spacing * 100), 1)
        fmt.setBottomMargin(6)
        cursor.mergeBlockFormat(fmt)

    def apply_theme(self, theme_key_or_dict):
        if isinstance(theme_key_or_dict, dict):
            self._theme_key = CUSTOM_THEME_KEY
            theme = theme_key_or_dict
        else:
            self._theme_key = theme_key_or_dict
            theme = get_writing_theme(theme_key_or_dict)
        self._theme = theme
        self.setStyleSheet(f"background-color: {theme['bg']};")
        self.edit.apply_editor_colors(theme)
        # 段落聚焦和空章节引导语都要从 theme 取色。
        # 传入 _InnerPlainEdit 缓存一份；如果聚焦正在开启，
        # 立刻刷新全文透明度以用上新主题色。
        self.edit.set_para_focus_theme(theme)
        if self._paragraph_focus_enabled:
            self.edit.apply_paragraph_focus(False)
            self.edit.apply_paragraph_focus(True)

    def set_typewriter(self, enabled: bool):
        self._typing = enabled
        if enabled:
            self._do_typewriter_scroll()

    def set_paragraph_focus(self, enabled: bool):
        """开关段落聚焦。开启后立即刷新当前段落；关闭后恢复全文透明度。"""
        enabled = bool(enabled)
        if enabled == self._paragraph_focus_enabled:
            return
        self._paragraph_focus_enabled = enabled
        if enabled:
            self.edit.apply_paragraph_focus(True)
            self._apply_paragraph_focus()
        else:
            self.edit.apply_paragraph_focus(False)

    def _apply_paragraph_focus(self):
        if not self._paragraph_focus_enabled:
            return
        self.edit.refresh_paragraph_focus()

    def set_behavior(
        self,
        auto_indent: bool = None,
        auto_quote_pair: bool = None,
        tab_insert: bool = None,
    ):
        """设置编辑器行为开关。由 workspace 从配置注入。"""
        self.edit.set_behavior(auto_indent, auto_quote_pair, tab_insert)

    # ---------- 内部事件 ----------
    def _on_text_changed(self):
        if self._loading:
            return
        # 段落聚焦会通过 mergeCharFormat 改字符格式，触发 textChanged。
        # 那不是"用户编辑"，必须屏蔽，否则每移动一次光标都会
        # 让 debounce 定时器启动 → 3 秒后自动保存一次 → 无谓写盘。
        if getattr(self.edit, "_applying_focus", False):
            return
        if not self._applying_live:
            self._sym_session = None
        self._debounce.start()

    def _on_cursor_moved(self):
        if self._typing and not self.edit._mouse_active and self.edit._keyboard_typing:
            self._typewriter_timer.start()
        # 光标移动 → 段落聚焦刷新（80ms 节流；同段落不重绘）
        if self._paragraph_focus_enabled:
            self._para_focus_timer.start()

    def _emit_selection(self):
        self.selection_changed.emit(self.edit.textCursor().selectedText())

    def _do_typewriter_scroll(self):
        edit = self.edit
        cursor = edit.textCursor()
        cursor_rect = edit.cursorRect(cursor)
        viewport_h = edit.viewport().height()
        target_y = int(viewport_h * 0.45)
        bar = edit.verticalScrollBar()
        bar.setValue(bar.value() + cursor_rect.center().y() - target_y)


class _InnerPlainEdit(QPlainTextEdit):
    """内部编辑器：无边框、自定义颜色、插入文本时不自动加富文本"""

    # Ctrl+滚轮 请求字号变化，参数是 ±1
    font_size_delta = Signal(int)
    # 右键菜单"查找选中文字"，参数为选中文本
    search_selection = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setAcceptDrops(False)
        self.setUndoRedoEnabled(True)
        self.setTabChangesFocus(False)
        self.setViewportMargins(28, 24, 28, 24)
        self._mouse_active = False
        self._keyboard_typing = False
        # 正文限宽（px）。<=0 表示不限宽（铺满）。由 WritingEditor 注入。
        self._content_width = 0
        # 段首缩进前缀（回车新段时自动插入）；空串表示不自动缩进
        self._para_indent = "\u3000\u3000"
        # 行为开关（可由 workspace 注入）
        self._auto_indent_enabled = True  # 回车新段自动补段首缩进
        self._auto_quote_pair_enabled = True  # 直引号自动配对
        self._tab_insert_enabled = True  # Tab 插入制表符（False 则切焦点）

        # 段落聚焦状态
        self._para_focus_on = False          # 是否正在聚焦
        self._para_focus_block = -1          # 当前"亮"的段落号
        self._applying_focus = False         # 应用格式时屏蔽 textChanged
        self._para_focus_theme = {}          # 缓存的 theme（取正文色）

    def set_para_indent(self, prefix: str):
        self._para_indent = prefix or ""

    def set_content_width(self, width: int):
        """设置正文限宽（px）。<=0 表示不限宽（铺满）。"""
        try:
            width = int(width or 0)
        except (TypeError, ValueError):
            width = 0
        self._content_width = max(0, width)
        self._apply_content_margins()

    def _apply_content_margins(self):
        """按 _content_width 计算左右 viewport 边距，让正文限宽居中。

        用**控件自身宽度**（self.width()）算，不要用 viewport().width()：
        setViewportMargins 会改变 viewport 宽度，用它算会导致下一帧又
        变化，反复震荡。
        """
        w = self._content_width
        if w <= 0:
            lm = rm = 28
        else:
            total = max(0, self.width())
            if total <= w + 56:
                lm = rm = 28
            else:
                lm = rm = max(28, (total - w) // 2)
        cur = self.viewportMargins()
        if cur.left() == lm and cur.right() == rm:
            return
        self.setViewportMargins(lm, 24, rm, 24)

    def paintEvent(self, event):
        """空文档时在视口中央绘制引导语。

        打字后自动消失（document().isEmpty() 为 False 时跳过绘制）。
        引导语用很淡的主题色，不喧宾夺主。
        """
        super().paintEvent(event)
        # 只在「没有实际文字」时显示。不能只用 document().isEmpty()：
        # load_text 会给空章节插入段首缩进（\u3000\u3000），此时文档
        # 不为空但视觉上什么字都没有，引导语画不出来。
        doc = self.document()
        # 快速路径：字符数超过 8 一定不是空章节，不复制文档。
        # 长篇（百万字）每次重绘 toPlainText 会复制整份文本，很贵。
        if doc.characterCount() > 8:
            return
        if doc.toPlainText().strip():
            return
        theme = self._para_focus_theme or {}
        text_c = QColor(theme.get("text", "#2D2820"))
        muted = QColor(theme.get("muted", "#928A7C"))

        from PySide6.QtGui import QFont as _QFont, QFontMetrics as _FM
        from PySide6.QtCore import QRect as _QRect

        vp = self.viewport()
        rect = vp.rect()

        # 主引导语（大号，浅）
        main_font = _QFont(self.font().family())
        main_font.setPixelSize(34)
        main_font.setLetterSpacing(_QFont.PercentageSpacing, 110)

        # 快捷键提示（小号，更浅）
        hint_font = _QFont(self.font().family())
        hint_font.setPixelSize(17)

        # 计算整体高度
        fm_main = _FM(main_font)
        fm_hint = _FM(hint_font)
        line_main_h = fm_main.height()
        line_hint_h = fm_hint.height()
        gap = 34
        hint_lines = [
            "Enter 换行  ·  Ctrl+S 手动保存",
            "Ctrl+J 唤起 AI  ·  Ctrl+F 查找",
            "Ctrl+Shift+Space  AI 快速续写",
        ]
        total_h = line_main_h + gap + len(hint_lines) * (line_hint_h + 4)

        # 顶部起点（居中）
        top = (rect.height() - total_h) // 2
        if top < 40:
            top = 40

        painter = QPainter(vp)

        # 主引导语
        main_c = QColor(text_c)
        main_c.setAlpha(90)
        painter.setPen(main_c)
        painter.setFont(main_font)
        main_rect = _QRect(0, top, rect.width(), line_main_h)
        painter.drawText(main_rect, Qt.AlignCenter, "写下第一句话。")

        # 快捷键提示
        hint_c = QColor(muted)
        hint_c.setAlpha(140)
        painter.setPen(hint_c)
        painter.setFont(hint_font)
        y = top + line_main_h + gap
        for line in hint_lines:
            lr = _QRect(0, y, rect.width(), line_hint_h)
            painter.drawText(lr, Qt.AlignCenter, line)
            y += line_hint_h + 4

        painter.end()

    def apply_paragraph_focus(self, enabled: bool):
        """开启/关闭段落聚焦的总开关。"""
        if enabled == self._para_focus_on:
            return
        self._para_focus_on = bool(enabled)
        if enabled:
            # 全文变暗（当前段暂设当前光标所在段）
            self._para_focus_block = -1
            self._refresh_focus_all()
            self.refresh_paragraph_focus()
        else:
            # 恢复所有段落为正常色
            self._applying_focus = True
            try:
                doc = self.document()
                fmt = QTextCharFormat()
                fmt.setForeground(
                    QColor(self._para_focus_theme.get("text", "#2D2820")))
                cur = QTextCursor(doc)
                cur.beginEditBlock()
                b = doc.begin()
                while b.isValid():
                    c = QTextCursor(b)
                    c.select(QTextCursor.BlockUnderCursor)
                    c.mergeCharFormat(fmt)
                    b = b.next()
                cur.endEditBlock()
            finally:
                self._applying_focus = False
            self._para_focus_block = -1

    def _refresh_focus_all(self):
        """把所有段落设成暗色（首次开启时用）。"""
        theme = self._para_focus_theme or {}
        base = QColor(theme.get("text", "#2D2820"))
        dim = QColor(base)
        dim.setAlpha(82)   # 32%
        fmt = QTextCharFormat()
        fmt.setForeground(dim)

        self._applying_focus = True
        try:
            doc = self.document()
            cur = QTextCursor(doc)
            cur.beginEditBlock()
            b = doc.begin()
            while b.isValid():
                c = QTextCursor(b)
                c.select(QTextCursor.BlockUnderCursor)
                c.mergeCharFormat(fmt)
                b = b.next()
            cur.endEditBlock()
        finally:
            self._applying_focus = False

    def refresh_paragraph_focus(self):
        """只更新"旧当前段"和"新当前段"两个块的格式，不重刷全文。"""
        if not self._para_focus_on:
            return
        theme = self._para_focus_theme or {}
        base = QColor(theme.get("text", "#2D2820"))
        dim = QColor(base)
        dim.setAlpha(82)

        cur_block = self.textCursor().blockNumber()
        old_block = self._para_focus_block
        if cur_block == old_block:
            return
        self._para_focus_block = cur_block

        bright_fmt = QTextCharFormat()
        bright_fmt.setForeground(base)
        dim_fmt = QTextCharFormat()
        dim_fmt.setForeground(dim)

        doc = self.document()
        self._applying_focus = True
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        try:
            if old_block >= 0:
                b = doc.findBlockByNumber(old_block)
                if b.isValid():
                    c = QTextCursor(b)
                    c.select(QTextCursor.BlockUnderCursor)
                    c.mergeCharFormat(dim_fmt)
            b = doc.findBlockByNumber(cur_block)
            if b.isValid():
                c = QTextCursor(b)
                c.select(QTextCursor.BlockUnderCursor)
                c.mergeCharFormat(bright_fmt)
        finally:
            cur.endEditBlock()
            self._applying_focus = False

    def set_para_focus_theme(self, theme: dict):
        """theme 切换时更新缓存色（不重刷，等下次光标移动自然生效）。"""
        self._para_focus_theme = dict(theme or {})

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_content_margins()

    def set_behavior(
        self,
        auto_indent: bool = None,
        auto_quote_pair: bool = None,
        tab_insert: bool = None,
    ):
        """由 workspace 注入编辑器行为开关（None 表示不改）。"""
        if auto_indent is not None:
            self._auto_indent_enabled = bool(auto_indent)
        if auto_quote_pair is not None:
            self._auto_quote_pair_enabled = bool(auto_quote_pair)
        if tab_insert is not None:
            self._tab_insert_enabled = bool(tab_insert)
            # Tab 键行为：True=插入制表符，False=切换焦点
            self.setTabChangesFocus(not self._tab_insert_enabled)

    def apply_editor_colors(self, theme: dict):
        self.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  background-color: {theme['editor_bg']};"
            f"  color: {theme['text']};"
            f"  border: 1px solid {qss_alpha(theme.get('muted', '#9a8e76'), 0x22)};"
            f"  border-radius: 10px;"
            f"  selection-background-color: {theme['accent']};"
            f"  selection-color: {theme.get('accent_fg', 'white')};"
            f"}}"
        )
        palette = self.palette()
        palette.setColor(QPalette.Text, QColor(theme["text"]))
        palette.setColor(QPalette.WindowText, QColor(theme["text"]))
        palette.setColor(QPalette.Base, QColor(theme["editor_bg"]))
        self.setPalette(palette)
        self.setCursorWidth(2)

    def mousePressEvent(self, event):
        self._mouse_active = True
        self._keyboard_typing = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._mouse_active = False

    def wheelEvent(self, event):
        # Ctrl+滚轮 缩放字号；普通滚轮保持默认滚动
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.font_size_delta.emit(+1)
            elif delta < 0:
                self.font_size_delta.emit(-1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        self._mouse_active = False
        mods = event.modifiers()
        ctrl_alt_meta = bool(
            mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
        )
        key = event.key()

        # 找所属的 WritingEditor
        p = self.parent()
        while p is not None and not isinstance(p, WritingEditor):
            p = p.parent()
        we = p if isinstance(p, WritingEditor) else None

        # 幽灵文本生成中：Tab 采纳 / Esc 放弃 / Ctrl+Enter 重写
        if we is not None and we._quick_continue_active:
            if key == Qt.Key_Tab:
                we._accept_quick_continue()
                event.accept()
                return
            if key == Qt.Key_Escape:
                we._reject_quick_continue()
                event.accept()
                return
            if key in (Qt.Key_Return, Qt.Key_Enter) and mods & Qt.ControlModifier:
                we._restart_quick_continue()
                event.accept()
                return
            # 导航键：放行（允许用户在 AI 文本里移动光标）
            if key in (
                Qt.Key_Left,
                Qt.Key_Right,
                Qt.Key_Up,
                Qt.Key_Down,
                Qt.Key_Home,
                Qt.Key_End,
                Qt.Key_PageUp,
                Qt.Key_PageDown,
            ):
                super().keyPressEvent(event)
                event.accept()
                return
            # 其他字符输入：用户想手打 → 视为放弃 AI 结果。
            # 判断依据是 event.text()：修饰键（Ctrl/Shift/Alt）单独按下时
            # text() 为空串，不该触发"放弃"；可打印字符和 IME 提交才触发。
            text_in = event.text()
            if text_in:
                we._reject_quick_continue()
                super().keyPressEvent(event)
                event.accept()
                return
            # 非文本键（Ctrl+S / F5 / F11 / Ctrl+W 等）：放行到默认处理。
            # 之前这里 event.accept() 直接吞掉，导致所有应用级快捷键
            # 在幽灵续写期间全部失效，用户以为软件卡死。
            super().keyPressEvent(event)
            return

        # Ctrl+Z：编辑器没有可撤销内容时，尝试撤销"合并章节"。
        # 应用场景：用户合并章节后想反悔、习惯性按 Ctrl+Z —— 此时编辑
        # 器撤销栈是空的（合并是章节级操作，不进编辑器撤销栈），借机
        # 把最近一次合并还原。注意：不抢编辑器自己的撤销，只做兜底。
        if key == Qt.Key_Z and mods == Qt.ControlModifier:
            if not self.document().isUndoAvailable():
                w = self.window()
                ws = getattr(w, "workspace", None)
                undo_merge = getattr(ws, "_undo_last_merge", None)
                if callable(undo_merge) and undo_merge():
                    event.accept()
                    return
            # 编辑器有可撤销内容，或没有可撤销的合并 → 走默认编辑器撤销

        # Ctrl+Shift+Space：唤起快速续写。
        # 不用 Ctrl+Space——Windows 下它默认被 IME 抢走做中英文切换，
        # 编辑器根本收不到这个按键事件。
        if (
            key == Qt.Key_Space
            and (mods & Qt.ControlModifier)
            and (mods & Qt.ShiftModifier)
        ):
            if we is not None:
                we._trigger_quick_continue()
            event.accept()
            return

        # Ctrl+Enter：唤起 AI 光标续写（原有功能）
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods & Qt.ControlModifier:
            if we is not None:
                we._trigger_inline_continue()
            event.accept()
            return

        # 回车新段自动补段首缩进（保留原逻辑 + 开关）
        if (
            key in (Qt.Key_Return, Qt.Key_Enter)
            and not ctrl_alt_meta
            and self._auto_indent_enabled
            and self._para_indent
            and not self.textCursor().hasSelection()
        ):
            cur = self.textCursor()
            block_text = cur.block().text()
            if self._para_indent and block_text.startswith(self._para_indent):
                block_text = block_text[len(self._para_indent) :]
            prev_empty = not block_text.strip()
            super().keyPressEvent(event)
            new_cur = self.textCursor()
            if not prev_empty and new_cur.atBlockStart():
                new_cur.insertText(self._para_indent)
            self._keyboard_typing = False
            return

        # 直引号自动配对（保留原逻辑 + 开关）
        if (
            key in (Qt.Key_QuoteDbl, Qt.Key_Apostrophe)
            and not ctrl_alt_meta
            and self._auto_quote_pair_enabled
        ):
            open_ch, close_ch = ("“", "”") if key == Qt.Key_QuoteDbl else ("‘", "’")
            cursor = self.textCursor()
            if cursor.hasSelection():
                sel = cursor.selectedText()
                cursor.insertText(open_ch + sel + close_ch)
                self._keyboard_typing = False
                return
            text = self.toPlainText()
            pos = cursor.position()
            if pos > 0 and text[pos - 1] == open_ch:
                cursor.insertText(close_ch)
                self.setTextCursor(cursor)
                self._keyboard_typing = False
                return
            if pos < len(text) and text[pos] == close_ch:
                cursor.movePosition(QTextCursor.NextCharacter)
                self.setTextCursor(cursor)
                self._keyboard_typing = False
                return
            cursor.insertText(open_ch + close_ch)
            cursor.movePosition(QTextCursor.PreviousCharacter)
            self.setTextCursor(cursor)
            self._keyboard_typing = False
            return

        # 打字机滚动标记（保留原逻辑）
        nav_keys = {
            Qt.Key_Left,
            Qt.Key_Right,
            Qt.Key_Up,
            Qt.Key_Down,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_PageUp,
            Qt.Key_PageDown,
        }
        if ctrl_alt_meta:
            self._keyboard_typing = False
        elif key in nav_keys:
            self._keyboard_typing = False
        elif key in _TYPING_KEYS:
            self._keyboard_typing = True
        elif Qt.Key_Space <= key <= Qt.Key_AsciiTilde:
            self._keyboard_typing = True
        elif key >= 0x80:
            self._keyboard_typing = True
        else:
            self._keyboard_typing = False

        try:
            super().keyPressEvent(event)
        finally:
            self._keyboard_typing = False

    def insertFromMimeData(self, source):
        prev = self._keyboard_typing
        self._keyboard_typing = False
        try:
            super().insertFromMimeData(source)
        finally:
            self._keyboard_typing = prev

    def inputMethodEvent(self, event):
        """拦截中文输入法 commit，处理中文引号配对。

        中文引号由输入法通过 inputMethodEvent 一次性 commit 进来，
        不触发 keyPressEvent 的 Key_QuoteDbl 分支。这里单独处理：

        - commit "“"：右侧已是 "”" → 光标跳过去；否则插 "“”" 光标居中
        - commit "”"：右侧已是 "”" → 光标跳过去（避免 "“””" 三连）
        - 其它（多字符 commit / 无关字符）→ 交给 Qt 默认处理
        """
        if not getattr(self, "_auto_quote_pair_enabled", True):
            super().inputMethodEvent(event)
            return
        commit = event.commitString()
        if commit in ("“", "”"):
            cursor = self.textCursor()
            text = self.toPlainText()
            pos = cursor.position()

            if commit == "“":
                # 光标右侧已经是 "”" → 用户想输入闭合引号，光标跳过
                if pos < len(text) and text[pos] == "”":
                    cursor.movePosition(QTextCursor.NextCharacter)
                    self.setTextCursor(cursor)
                    event.accept()
                    return
                # 否则自动配对
                cursor.insertText("“”")
                cursor.movePosition(QTextCursor.PreviousCharacter)
                self.setTextCursor(cursor)
                event.accept()
                return

            # commit == "”"
            # 右侧已经是 "”" → 跳过，不再插入一个
            if pos < len(text) and text[pos] == "”":
                cursor.movePosition(QTextCursor.NextCharacter)
                self.setTextCursor(cursor)
                event.accept()
                return
            # 其它情况：正常插入

        super().inputMethodEvent(event)

    def contextMenuEvent(self, event):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction

        menu = QMenu(self)
        menu.setStyleSheet(self._ctx_menu_qss())

        act_undo = QAction("撤销", menu)
        act_undo.setShortcut("Ctrl+Z")
        act_redo = QAction("重做", menu)
        act_redo.setShortcut("Ctrl+Y")
        act_cut = QAction("剪切", menu)
        act_cut.setShortcut("Ctrl+X")
        act_copy = QAction("复制", menu)
        act_copy.setShortcut("Ctrl+C")
        act_paste = QAction("粘贴", menu)
        act_paste.setShortcut("Ctrl+V")
        act_del = QAction("删除", menu)
        act_del.setShortcut("Delete")
        act_select_all = QAction("全选", menu)
        act_select_all.setShortcut("Ctrl+A")
        act_ai_rewrite = QAction("AI 改写选中文本…", menu)
        act_search_sel = QAction("查找选中文字…", menu)

        act_undo.triggered.connect(self.undo)
        act_redo.triggered.connect(self.redo)
        act_cut.triggered.connect(self.cut)
        act_copy.triggered.connect(self.copy)
        act_paste.triggered.connect(self.paste)
        act_del.triggered.connect(self._delete_selected)
        act_select_all.triggered.connect(self.selectAll)
        act_ai_rewrite.triggered.connect(self._emit_rewrite_request)
        act_search_sel.triggered.connect(
            lambda: self.search_selection.emit(self.textCursor().selectedText())
        )

        act_undo.setEnabled(self.document().isUndoAvailable())
        act_redo.setEnabled(self.document().isRedoAvailable())
        act_cut.setEnabled(self.textCursor().hasSelection())
        act_copy.setEnabled(self.textCursor().hasSelection())
        act_del.setEnabled(self.textCursor().hasSelection())
        act_ai_rewrite.setEnabled(self.textCursor().hasSelection())
        act_search_sel.setEnabled(self.textCursor().hasSelection())
        cb = QApplication.clipboard()
        act_paste.setEnabled(bool(cb and cb.text()))

        menu.addAction(act_undo)
        menu.addAction(act_redo)
        menu.addSeparator()
        menu.addAction(act_cut)
        menu.addAction(act_copy)
        menu.addAction(act_paste)
        menu.addAction(act_del)
        menu.addSeparator()
        menu.addAction(act_ai_rewrite)
        menu.addAction(act_search_sel)
        menu.addSeparator()
        menu.addAction(act_select_all)
        menu.exec(event.globalPos())

    def _delete_selected(self):
        c = self.textCursor()
        if c.hasSelection():
            c.deleteChar()

    def _emit_rewrite_request(self):
        p = self.parent()
        while p is not None and not isinstance(p, WritingEditor):
            p = p.parent()
        if isinstance(p, WritingEditor):
            p.request_rewrite.emit()

    def _ctx_menu_qss(self) -> str:
        parent = self.parent()
        while parent and not isinstance(parent, WritingEditor):
            parent = parent.parent()
        if isinstance(parent, WritingEditor):
            t = parent._theme or {}
            bg = t.get("editor_bg", "#FCFAF4")
            text = t.get("text", "#3B342A")
            muted = t.get("muted", "#9A8E76")
            accent = t.get("accent", "#B08A4E")
            return (
                f"QMenu {{ background-color: {bg}; color: {text};"
                f" border: 1px solid {qss_alpha(muted, 0x44)}; border-radius: 6px;"
                f" padding: 4px; }}"
                f"QMenu::item {{ padding: 6px 28px; border-radius: 4px; }}"
                f"QMenu::item:selected {{ background-color: {qss_alpha(accent, 0x33)};"
                f" color: {text}; }}"
                f"QMenu::separator {{ height: 1px; background: {qss_alpha(muted, 0x33)};"
                f" margin: 4px 8px; }}"
            )
        return ""
