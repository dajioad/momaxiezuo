# ui/platform_format_dialog.py
"""墨码写作 - 平台格式一键

选平台 → 预览差异 → 应用到当前章 / 全书。
应用前自动为每章打快照（铁律）。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QRadioButton,
    QButtonGroup,
    QPlainTextEdit,
    QFrame,
    QMessageBox,
    QApplication,
    QSplitter,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from utils.platform_format import (
    PLATFORMS,
    PLATFORM_ORDER,
    apply_platform,
)
from utils.format_engine import build_diff


class PlatformFormatDialog(QDialog):
    """平台格式一键面板。"""

    applied = Signal(set)

    def __init__(self, work, current_chapter_id: str = "", parent=None):
        super().__init__(parent)
        self._work = work
        self._current_chapter_id = current_chapter_id
        self._preview = {}  # {cid: new_text}
        self._stats = {}  # {cid: {rule_id: count}}
        self._theme = self._resolve_theme()
        self.setWindowTitle("平台格式一键")
        self.setMinimumSize(880, 680)
        self._build_ui()
        self._apply_theme()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ed = getattr(p, "editor", None)
            if ed is not None and hasattr(ed, "_theme"):
                return ed._theme or {}
            ws = getattr(p, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None and hasattr(ed, "_theme"):
                    return ed._theme or {}
                break
            p = p.parent()
        return {}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ---- 平台选择 ----
        top = QFrame()
        top.setObjectName("pfTop")
        tl = QVBoxLayout(top)
        tl.setContentsMargins(14, 12, 14, 12)
        tl.setSpacing(8)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("目标平台"))
        self.platform_combo = QComboBox()
        for key in PLATFORM_ORDER:
            self.platform_combo.addItem(PLATFORMS[key]["name"], key)
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        row1.addWidget(self.platform_combo, 1)
        tl.addLayout(row1)

        self.desc_label = QLabel("")
        self.desc_label.setObjectName("pfDesc")
        self.desc_label.setWordWrap(True)
        tl.addWidget(self.desc_label)

        # ---- 范围 ----
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("应用范围"))
        self.scope_group = QButtonGroup(self)
        self.rb_current = QRadioButton("仅当前章节")
        self.rb_book = QRadioButton("全书所有章节")
        if self._current_chapter_id:
            self.rb_current.setChecked(True)
        else:
            self.rb_book.setChecked(True)
            self.rb_current.setEnabled(False)
        self.scope_group.addButton(self.rb_current)
        self.scope_group.addButton(self.rb_book)
        row2.addWidget(self.rb_current)
        row2.addWidget(self.rb_book)
        row2.addStretch()
        hint = QLabel("应用前每章会自动打快照")
        hint.setObjectName("pfHint")
        row2.addWidget(hint)
        tl.addLayout(row2)

        root.addWidget(top)

        # ---- 预览区 ----
        preview_box = QFrame()
        preview_box.setObjectName("pfPreview")
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        stats_bar = QFrame()
        stats_bar.setObjectName("pfStatsBar")
        sb = QHBoxLayout(stats_bar)
        sb.setContentsMargins(14, 6, 8, 6)
        self.stats_label = QLabel("点下方「预览差异」查看效果")
        self.stats_label.setObjectName("pfStats")
        sb.addWidget(self.stats_label)
        sb.addStretch()
        pv.addWidget(stats_bar)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.diff_view.setFrameShape(QFrame.NoFrame)
        self.diff_view.setPlaceholderText("差异会显示在这里")
        pv.addWidget(self.diff_view, 1)

        root.addWidget(preview_box, 1)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.preview_btn = QPushButton(" 预览差异")
        self.preview_btn.setCursor(Qt.PointingHandCursor)
        self.preview_btn.clicked.connect(self._do_preview)
        btn_row.addWidget(self.preview_btn)
        self.apply_btn = QPushButton(" 应用")
        self.apply_btn.setProperty("variant", "primary")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.clicked.connect(self._do_apply)
        btn_row.addWidget(self.apply_btn)
        btn_row.addSpacing(12)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._on_platform_changed()

    def _on_platform_changed(self):
        key = self.platform_combo.currentData()
        p = PLATFORMS.get(key, {})
        self.desc_label.setText(p.get("desc", ""))
        self._clear_preview()

    def _clear_preview(self, *_):
        self._preview.clear()
        self._stats.clear()
        self.apply_btn.setEnabled(False)
        self.stats_label.setText("点下方「预览差异」查看效果")
        self.diff_view.setPlainText("")

    def _scope(self) -> str:
        return "current" if self.rb_current.isChecked() else "book"

    def _target_chapters(self):
        if self._scope() == "current":
            return [self._current_chapter_id] if self._current_chapter_id else []
        return [c["id"] for c in self._work.chapters]

    # ---------- 预览 ----------
    def _do_preview(self):
        platform = self.platform_combo.currentData()
        self._preview.clear()
        self._stats.clear()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            total = 0
            changed = 0
            for cid in self._target_chapters():
                old = self._work.read_chapter(cid)
                new, st = apply_platform(old, platform)
                if new == old:
                    continue
                self._preview[cid] = new
                self._stats[cid] = st
                total += sum(st.values())
                changed += 1
        finally:
            QApplication.restoreOverrideCursor()

        self._render(changed, total)

    def _render(self, changed: int, total: int):
        if not self._preview:
            self.stats_label.setText("✓ 当前平台规则下没有需要修改的内容")
            self.diff_view.setPlainText("")
            self.apply_btn.setEnabled(False)
            return

        parts = []
        for cid, new in self._preview.items():
            ch = self._work.get_chapter(cid)
            title = ch.get("title", cid) if ch else cid
            old = self._work.read_chapter(cid)
            parts.append(f"▎{title}")
            parts.append(self._clean_diff(old, new))
            parts.append("")
        self.diff_view.setPlainText("\n".join(parts).rstrip())
        self._recolor()
        self.stats_label.setText(f"将修改 {changed} 章 · 共 {total} 处")
        self.apply_btn.setEnabled(True)

    def _clean_diff(self, old: str, new: str, context: int = 1) -> str:
        raw = build_diff(old, new, context=context)
        if not raw:
            return ""
        lines = []
        for ln in raw.splitlines():
            if (
                ln.startswith("---")
                or ln.startswith("+++")
                or ln.startswith("--- ")
                or ln.startswith("+++ ")
            ):
                continue
            if ln.startswith("@@"):
                lines.append("  ···")
                continue
            lines.append(ln)
        return "\n".join(lines)

    def _recolor(self):
        t = self._theme or {}
        try:
            is_light = QColor(t.get("bg", "#f7f2e8")).lightness() > 150
        except Exception:
            is_light = True
        if is_light:
            c_del = QColor("#a93326")
            c_add = QColor("#137333")
            bg_del = QColor(192, 57, 43, 22)
            bg_add = QColor(26, 127, 55, 22)
        else:
            c_del = QColor("#ff8a80")
            c_add = QColor("#7ee787")
            bg_del = QColor(255, 138, 128, 36)
            bg_add = QColor(126, 231, 135, 36)
        accent = QColor(t.get("accent", "#b08a4e"))
        muted = QColor(t.get("muted", "#9a8e76"))

        doc = self.diff_view.document()
        block = doc.begin()
        while block.isValid():
            line = block.text()
            fmt = QTextCharFormat()
            colored = True
            if line.startswith("▎"):
                fmt.setForeground(accent)
                fmt.setFontWeight(QFont.Bold)
            elif line.startswith("  ···"):
                fmt.setForeground(muted)
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

    # ---------- 应用 ----------
    def _do_apply(self):
        if not self._preview:
            QMessageBox.information(self, "提示", "请先点「预览差异」。")
            return
        n = len(self._preview)
        if n > 5:
            if (
                QMessageBox.question(
                    self,
                    "确认应用",
                    f"即将修改 {n} 个章节，每章都会自动打快照。\n" "继续？",
                )
                != QMessageBox.Yes
            ):
                return
        affected = set()
        try:
            for cid, new_text in self._preview.items():
                old = self._work.read_chapter(cid)
                if old == new_text:
                    continue
                self._work.create_snapshot(cid)
                self._work.write_chapter(cid, new_text)
                affected.add(cid)
        except OSError as e:
            QMessageBox.critical(
                self, "应用失败", f"写入章节时出错：{e}\n已处理的章节可从历史快照恢复。"
            )
            if affected:
                self.applied.emit(affected)
            return
        if affected:
            self.applied.emit(affected)
        QMessageBox.information(
            self, "完成", f"已按平台格式处理 {len(affected)} 个章节，原稿已打快照。"
        )
        self.accept()

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
            f"PlatformFormatDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#pfDesc {{ color: {accent}; font-size: 12px;"
            f" font-weight: 500; padding: 2px 0; }}"
            f"QLabel#pfHint {{ color: {muted}; font-size: 11px; }}"
            f"QFrame#pfTop {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 8px; }}"
            f"QFrame#pfPreview {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x44)};"
            f" border-radius: 10px; }}"
            f"QFrame#pfStatsBar {{ background-color: {qss_alpha(accent, 0x18)};"
            f" border-top-left-radius: 10px; border-top-right-radius: 10px;"
            f" border-bottom: 1px solid {qss_alpha(accent, 0x33)}; }}"
            f"QLabel#pfStats {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; }}"
            f"QComboBox {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
        )
        self.preview_btn.setStyleSheet(
            f"QPushButton {{ color: {text}; background: transparent;"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 8px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent};"
            f" background: {qss_alpha(accent, 0x14)}; }}"
        )
        self.apply_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                accent_hover,
                selector="QPushButton",
                padding="8px 22px",
                bold=True,
            )
        )
        self.preview_btn.setIcon(qta.icon("fa5s.eye", color=muted))
        self.apply_btn.setIcon(qta.icon("fa5s.check", color=accent_fg))
        self.diff_view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {editor_bg}; color: {text};"
            f" border: none; border-radius: 10px;"
            f" padding: 12px 14px;"
            f" selection-background-color: {qss_alpha(accent, 0x55)}; }}"
        )
