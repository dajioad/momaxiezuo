# ui/version_history_dialog.py
"""墨码写作 - 章节历史版本对话框

- 列出某一章的所有自动快照
- 排序：从上到下 = 旧 → 新（最下面是最新的快照）
- 时间分类标签：[今天] [昨天] [本周] [更早]
- 双击重命名 / 右键删除 / 一键恢复
"""

import os
from datetime import datetime, date

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QPlainTextEdit,
    QMessageBox,
    QTabWidget,
    QWidget,
    QInputDialog,
    QMenu,
    QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QTextCursor
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from utils.work_store import Work
from utils.format_engine import build_diff
from utils.logger import get_logger

logger = get_logger()


def _to_datetime(time_str: str):
    try:
        if time_str.count("_") == 2:
            return datetime.strptime(time_str, "%Y%m%d_%H%M%S_%f")
        return datetime.strptime(time_str, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _format_time(time_str: str) -> str:
    t = _to_datetime(time_str)
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else time_str


def _format_time_short(time_str: str) -> str:
    t = _to_datetime(time_str)
    return t.strftime("%H:%M:%S") if t else time_str


def _time_group(mtime: float) -> str:
    try:
        d = datetime.fromtimestamp(mtime).date()
    except (OSError, ValueError):
        return ""
    delta = (date.today() - d).days
    if delta <= 0:
        return "今天"
    if delta == 1:
        return "昨天"
    if delta < 7:
        return "本周"
    return "更早"


def _format_delta(delta) -> str:
    if not delta:
        return ""
    sign = "+" if delta > 0 else ""
    return f"（{sign}{delta:,}）"


def _format_row(v: dict) -> str:
    parts = []
    grp = _time_group(v.get("mtime", 0))
    if grp:
        parts.append(f"[{grp}]")
    parts.append(_format_time_short(v["time"]))
    wc = v.get("word_count", 0)
    if wc:
        parts.append(f"{wc:,} 字{_format_delta(v.get('word_delta'))}")
    name = (v.get("name") or "").strip()
    if name:
        parts.append(f"「{name}」")
    return "  ".join(parts)


class VersionHistoryDialog(QDialog):
    version_restored = Signal(str)

    def __init__(self, work: Work, chapter_id: str, parent=None):
        super().__init__(parent)
        self.work = work
        self.chapter_id = chapter_id
        ch = work.get_chapter(chapter_id)
        self.chapter_title = ch.get("title", "未命名") if ch else ""
        self._theme = self._resolve_theme()
        self._versions = []
        self.setWindowTitle(f"历史版本 - {self.chapter_title}")
        self.setMinimumSize(900, 580)
        self._build_ui()
        self._apply_theme()
        self._load_versions()

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
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        ico = QLabel()
        ico.setPixmap(qta.icon(
            "fa5s.history",
            color=(self._theme or {}).get("text", "#3b342a")).pixmap(16, 16))
        head.addWidget(ico)
        title = QLabel(f"「{self.chapter_title}」的历史快照")
        title.setObjectName("vhTitle")
        head.addWidget(title)
        head.addStretch()
        root.addLayout(head)

        muted = self._theme.get("muted", "#9a8e76")
        hint = QLabel(
            "从上到下按时间排列（最下面是最近的快照）。\n"
            "双击重命名 · 右键删除 · 点「恢复」覆盖当前章节。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {muted}; font-size: 12px;")
        root.addWidget(hint)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selected)
        self.list_widget.itemDoubleClicked.connect(self._on_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        ll.addWidget(self.list_widget, 1)

        self.empty_label = QLabel("（尚无历史快照）")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {muted}; font-size: 13px;")
        self.empty_label.setVisible(False)
        ll.addWidget(self.empty_label)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.content_view = QPlainTextEdit()
        self.content_view.setReadOnly(True)
        self.content_view.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.tabs.addTab(self.content_view, "快照内容")
        self.tabs.addTab(self.diff_view, "与当前对比")
        rl.addWidget(self.tabs, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 500])
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.restore_btn = QPushButton("恢复此版本")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._do_restore)
        bottom.addWidget(self.restore_btn)
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _apply_theme(self):
        t = self._theme or {}
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"VersionHistoryDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#vhTitle {{ font-size: 14px; font-weight: 600; }}"
            f"QListWidget {{ background-color: {editor_bg};"
            f" color: {text}; border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; padding: 6px; outline: none; }}"
            f"QListWidget::item {{ padding: 8px 12px; border-radius: 6px;"
            f" margin: 1px 2px; font-family: Consolas, 'Microsoft YaHei';"
            f" font-size: 12px; }}"
            f"QListWidget::item:hover {{"
            f" background: {qss_alpha(accent, 0x10)}; }}"
            f"QListWidget::item:selected {{"
            f" background: {qss_alpha(accent, 0x22)}; color: {accent}; }}"
            f"QPlainTextEdit {{ background-color: {editor_bg};"
            f" color: {text}; border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; padding: 12px 14px; }}"
            f"QTabWidget::pane {{ border: none; }}"
            f"QTabBar::tab {{ background: transparent; color: {muted};"
            f" padding: 7px 16px; border: none;"
            f" border-bottom: 2px solid transparent; }}"
            f"QTabBar::tab:selected {{ color: {accent};"
            f" border-bottom: 2px solid {accent}; font-weight: 600; }}"
        )
        self.restore_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                accent_hover,
                selector="QPushButton",
                padding="7px 18px",
                bold=True,
            )
        )
        self.restore_btn.setIcon(qta.icon("fa5s.undo", color=accent_fg))

    def _load_versions(self, keep_file: str = None):
        self.list_widget.clear()
        try:
            self._versions = self.work.list_versions(self.chapter_id)
        except Exception as e:
            logger.error(f"列出历史版本失败: {e}", exc_info=True)
            self._versions = []

        # 时间正序（旧 → 新）
        self._versions.sort(key=lambda x: x["mtime"])

        for i, v in enumerate(self._versions):
            if i == 0:
                v["word_delta"] = None
            else:
                prev_wc = self._versions[i - 1].get("word_count", 0)
                cur_wc = v.get("word_count", 0)
                v["word_delta"] = (cur_wc - prev_wc) if (prev_wc and cur_wc) else None

        if not self._versions:
            self.empty_label.setVisible(True)
            self.list_widget.setVisible(False)
            self.restore_btn.setEnabled(False)
            self.content_view.setPlainText("")
            self.diff_view.setPlainText("")
            return

        self.empty_label.setVisible(False)
        self.list_widget.setVisible(True)
        row_to_select = len(self._versions) - 1  # 默认选最新一条
        for i, v in enumerate(self._versions):
            item = QListWidgetItem(_format_row(v))
            item.setData(Qt.UserRole, v["file"])
            item.setToolTip(f"文件：{v['file']}\n" f"双击重命名 · 右键删除")
            self.list_widget.addItem(item)
            if keep_file and v["file"] == keep_file:
                row_to_select = i
        self.list_widget.setCurrentRow(row_to_select)

    def _current_version(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._versions):
            return None
        return self._versions[row]

    def _on_selected(self, row: int):
        if row < 0 or row >= len(self._versions):
            self.content_view.setPlainText("")
            self.diff_view.setPlainText("")
            self.restore_btn.setEnabled(False)
            return
        v = self._versions[row]
        try:
            with open(v["file"], "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as e:
            self.content_view.setPlainText(f"（无法读取：{e}）")
            self.diff_view.setPlainText("")
            self.restore_btn.setEnabled(False)
            return

        self.content_view.setPlainText(text)

        current = self.work.read_chapter(self.chapter_id)
        if current == text:
            self.diff_view.setPlainText("（此版本与当前内容一致，无需恢复）")
            self.restore_btn.setEnabled(False)
        else:
            diff = build_diff(text, current, context=2)
            if not diff:
                diff = "（差异为空）"
            lines = diff.splitlines()
            if len(lines) > 5000:
                diff = (
                    "\n".join(lines[:5000])
                    + f"\n\n…（还有 {len(lines) - 5000} 行未显示）"
                )
            self.diff_view.setPlainText(diff)
            self._recolor_diff()
            self.restore_btn.setEnabled(True)

    def _recolor_diff(self):
        t = self._theme or {}
        # 深色主题下暗红/暗绿对比度不足（约 2.3:1），按 editor_bg 亮度切换
        # 到提亮变体。
        try:
            is_light = QColor(t.get("editor_bg", "#fcfaf4")).lightness() > 150
        except Exception:
            is_light = True
        if is_light:
            c_del = QColor("#c0392b")
            c_add = QColor("#1a7f37")
            bg_del = QColor(231, 76, 60, 20)
            bg_add = QColor(76, 175, 80, 25)
        else:
            c_del = QColor("#ff8a80")
            c_add = QColor("#7ee787")
            bg_del = QColor(255, 138, 128, 36)
            bg_add = QColor(126, 231, 135, 36)
        c_head = QColor(t.get("muted", "#9a8e76"))
        doc = self.diff_view.document()
        block = doc.begin()
        while block.isValid():
            line = block.text()
            fmt = QTextCharFormat()
            colored = True
            if line.startswith(("---", "+++", "@@")):
                fmt.setForeground(c_head)
            elif line.startswith("-"):
                fmt.setForeground(c_del)
                fmt.setBackground(bg_del)
            elif line.startswith("+"):
                fmt.setForeground(c_add)
                fmt.setBackground(bg_add)
            else:
                colored = False
            if colored:
                cur = QTextCursor(block)
                cur.setPosition(block.position())
                cur.setPosition(block.position() + len(line), QTextCursor.KeepAnchor)
                cur.mergeCharFormat(fmt)
            block = block.next()

    def _on_double_clicked(self, item: QListWidgetItem):
        row = self.list_widget.row(item)
        if row < 0 or row >= len(self._versions):
            return
        v = self._versions[row]
        cur = (v.get("name") or "").strip()
        new_name, ok = QInputDialog.getText(
            self,
            "重命名快照",
            f"为 {_format_time(v['time'])} 的快照起个名字：\n" "（留空 = 清空名字）",
            text=cur,
        )
        if not ok:
            return
        new_name = new_name.strip()
        try:
            self.work.rename_snapshot(v["file"], new_name)
        except Exception as e:
            logger.warning(f"重命名写盘失败: {e}")
            QMessageBox.warning(self, "重命名失败", f"快照名字未能保存到磁盘：\n{e}")
            return
        v["name"] = new_name
        item.setText(_format_row(v))
        self.list_widget.viewport().update()

    def _on_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        row = self.list_widget.row(item)
        if row < 0 or row >= len(self._versions):
            return
        v = self._versions[row]
        menu = QMenu(self)
        act_rename = menu.addAction("重命名…")
        act_delete = menu.addAction("删除此快照")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen == act_rename:
            self._on_double_clicked(item)
        elif chosen == act_delete:
            self._delete_snapshot(v)

    def _delete_snapshot(self, v: dict):
        name = (v.get("name") or "").strip()
        label = name or _format_time(v["time"])
        ret = QMessageBox.question(
            self,
            "删除快照",
            f"确定删除快照「{label}」吗？\n\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            self.work.delete_snapshot(v["file"])
        except Exception as e:
            logger.warning(f"删除快照失败: {e}")
            QMessageBox.warning(self, "删除失败", f"快照未能从磁盘删除：\n{e}")
            return
        for i, ver in enumerate(self._versions):
            if ver["file"] == v["file"]:
                self._versions.pop(i)
                self.list_widget.takeItem(i)
                break
        if not self._versions:
            self.empty_label.setVisible(True)
            self.list_widget.setVisible(False)
            self.restore_btn.setEnabled(False)

    def _do_restore(self):
        v = self._current_version()
        if v is None:
            return
        t_str = _format_time(v["time"])
        ret = QMessageBox.question(
            self,
            "恢复历史版本",
            f"确定用「{t_str}」的版本覆盖当前章节吗？\n\n"
            "当前内容会自动保存为一份新快照（可再次恢复），因此不会丢失。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            self.work.restore_version(self.chapter_id, v["file"])
        except Exception as e:
            logger.error(f"恢复历史版本失败: {e}", exc_info=True)
            QMessageBox.critical(self, "恢复失败", str(e))
            return
        self.version_restored.emit(self.chapter_id)
        QMessageBox.information(self, "恢复完成", f"已恢复到 {t_str} 的版本。")
        self._load_versions()
