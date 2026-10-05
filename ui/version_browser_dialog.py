# ui/version_browser_dialog.py
"""墨码写作 - 历史快照浏览器

三种视图（tab 切换）：
- 全部：所有快照，按时间正序（最下面是最新的）
- 在用章节：只显示 book.json 里还有的章节的快照
- 已删章节：只显示已删除章节的快照

列表项：`[时间分类]  时间  字数（变化）  章节标题  「名字」`
- 双击重命名（改完立即更新行文字）
- 右键删除 / 另存为新章节
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
    QPlainTextEdit,
    QMessageBox,
    QInputDialog,
    QWidget,
    QMenu,
    QTabWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from utils.work_store import Work
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


def _format_row(version: dict, chapter_title: str, in_book: bool) -> str:
    parts = []
    grp = _time_group(version.get("mtime", 0))
    if grp:
        parts.append(f"[{grp}]")
    parts.append(_format_time_short(version["time"]))
    wc = version.get("word_count", 0)
    if wc:
        parts.append(f"{wc:,} 字{_format_delta(version.get('word_delta'))}")
    title = (chapter_title or "").strip()
    if not title:
        title = version.get("chapter_title") or "（未知章节）"
    if not in_book:
        title = f"{title}（已删除）"
    parts.append(title)
    name = (version.get("name") or "").strip()
    if name:
        parts.append(f"「{name}」")
    return "    ".join(parts)


class VersionBrowserDialog(QDialog):
    chapter_restored = Signal(str)

    def __init__(self, work: Work, parent=None):
        super().__init__(parent)
        self.work = work
        self._theme = self._resolve_theme()
        self._groups = []
        self._rows = []
        self.setWindowTitle(f"历史快照 - {work.title}")
        self.setMinimumSize(960, 640)
        self._build_ui()
        self._apply_theme()
        self._reload()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None and hasattr(ed, "_theme"):
                    return ed._theme or {}
                break
            ed = getattr(p, "editor", None)
            if ed is not None and hasattr(ed, "_theme"):
                return ed._theme or {}
            if hasattr(p, "_theme") and getattr(p, "_theme"):
                return p._theme
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
        title = QLabel("历史快照浏览器")
        title.setObjectName("vbTitle")
        head.addWidget(title)
        head.addStretch()

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._reload)
        head.addWidget(refresh_btn)
        root.addLayout(head)

        tip = QLabel(
            "从上到下按时间排列（最下面是最近的快照）。\n"
            "双击重命名 · 右键删除 / 另存为新章节。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("vbTip")
        root.addWidget(tip)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        def _make_list():
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 6, 0, 0)
            lst = QListWidget()
            lst.setContextMenuPolicy(Qt.CustomContextMenu)
            lst.itemDoubleClicked.connect(self._on_double_clicked)
            lst.customContextMenuRequested.connect(self._on_context_menu)
            lst.currentRowChanged.connect(self._on_row_changed)
            lay.addWidget(lst, 1)
            return w, lst

        page_all, self.list_all = _make_list()
        self.tabs.addTab(page_all, "全部")
        page_live, self.list_live = _make_list()
        self.tabs.addTab(page_live, "在用章节")
        page_del, self.list_del = _make_list()
        self.tabs.addTab(page_del, "已删章节")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root.addWidget(self.tabs, 1)

        preview_head = QLabel("快照内容预览")
        preview_head.setObjectName("vbSection")
        root.addWidget(preview_head)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.preview.setMaximumHeight(180)
        self.preview.setPlaceholderText("（选择左侧任一快照以预览）")
        root.addWidget(self.preview)

        bottom = QHBoxLayout()
        self.stat_label = QLabel("")
        self.stat_label.setObjectName("vbStat")
        bottom.addWidget(self.stat_label)
        bottom.addStretch()

        self.restore_btn = QPushButton("另存为新章节")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._on_restore)
        bottom.addWidget(self.restore_btn)

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
            f"VersionBrowserDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#vbTitle {{ font-size: 15px; font-weight: 700; }}"
            f"QLabel#vbTip {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#vbStat {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#vbSection {{ color: {text}; font-size: 13px;"
            f" font-weight: 600; padding: 4px 0; }}"
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

    def _reload(self):
        try:
            self._groups = self.work.list_all_versions()
        except Exception as e:
            logger.error(f"列出所有快照失败: {e}", exc_info=True)
            self._groups = []

        all_rows = []
        for g in self._groups:
            cid = g["chapter_id"]
            in_book = g["in_book"]
            title = g.get("title", "")
            versions = list(g.get("versions", []))
            # 组内正序（旧 → 新）
            versions.sort(key=lambda v: v["mtime"])
            for i, v in enumerate(versions):
                if i == 0:
                    v["word_delta"] = None
                else:
                    prev_wc = versions[i - 1].get("word_count", 0)
                    cur_wc = v.get("word_count", 0)
                    v["word_delta"] = (
                        (cur_wc - prev_wc) if (prev_wc and cur_wc) else None
                    )
            for v in versions:
                all_rows.append(
                    {
                        "version": v,
                        "chapter_title": title,
                        "in_book": in_book,
                        "chapter_id": cid,
                    }
                )

        # 全局时间正序（旧 → 新）
        all_rows.sort(key=lambda r: r["version"]["mtime"])

        self._rows = all_rows
        self._fill_list(self.list_all, all_rows)
        self._fill_list(self.list_live, [r for r in all_rows if r["in_book"]])
        self._fill_list(self.list_del, [r for r in all_rows if not r["in_book"]])

        total = sum(len(g["versions"]) for g in self._groups)
        total_ch = len(self._groups)
        total_del = sum(1 for g in self._groups if not g["in_book"])
        self.stat_label.setText(
            f"共 {total_ch} 章有快照（其中 {total_del} 章已删）· "
            f"合计 {total} 条快照"
        )

    def _fill_list(self, widget: QListWidget, rows: list):
        widget.clear()
        for r in rows:
            v = r["version"]
            item = QListWidgetItem(_format_row(v, r["chapter_title"], r["in_book"]))
            item.setData(Qt.UserRole, v["file"])
            item.setToolTip(
                f"章节：{r['chapter_title'] or v.get('chapter_title') or '（未知）'}\n"
                f"文件：{v['file']}\n"
                f"双击重命名 · 右键删除 / 另存为新章节"
            )
            widget.addItem(item)

    def _current_list(self):
        idx = self.tabs.currentIndex()
        if idx == 0:
            return self.list_all
        if idx == 1:
            return self.list_live
        return self.list_del

    def _row_for(self, widget: QListWidget, row: int):
        if row < 0 or row >= widget.count():
            return None
        item = widget.item(row)
        file_path = item.data(Qt.UserRole)
        for r in self._rows:
            if r["version"]["file"] == file_path:
                return r
        return None

    def _on_tab_changed(self, _idx: int):
        widget = self._current_list()
        self._on_row_changed(widget.currentRow())

    def _on_row_changed(self, row: int):
        widget = self._current_list()
        r = self._row_for(widget, row)
        if r is None:
            self.preview.setPlainText("")
            self.restore_btn.setEnabled(False)
            return
        v = r["version"]
        try:
            with open(v["file"], "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as e:
            self.preview.setPlainText(f"（无法读取：{e}）")
            self.restore_btn.setEnabled(False)
            return
        self.preview.setPlainText(text)
        self.restore_btn.setEnabled(True)

    def _on_double_clicked(self, item: QListWidgetItem):
        widget = self._current_list()
        row = widget.row(item)
        r = self._row_for(widget, row)
        if r is None:
            return
        v = r["version"]
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
        item.setText(_format_row(v, r["chapter_title"], r["in_book"]))
        widget.viewport().update()

    def _on_context_menu(self, pos):
        widget = self._current_list()
        item = widget.itemAt(pos)
        if item is None:
            return
        row = widget.row(item)
        r = self._row_for(widget, row)
        if r is None:
            return
        v = r["version"]
        menu = QMenu(self)
        act_rename = menu.addAction("重命名…")
        act_delete = menu.addAction("删除此快照")
        menu.addSeparator()
        act_restore = menu.addAction("另存为新章节…")
        chosen = menu.exec(widget.viewport().mapToGlobal(pos))
        if chosen == act_rename:
            self._on_double_clicked(item)
        elif chosen == act_delete:
            self._delete_snapshot(v)
        elif chosen == act_restore:
            self._restore_as_new_chapter(r)

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
        self._reload()

    def _on_restore(self):
        widget = self._current_list()
        row = widget.currentRow()
        r = self._row_for(widget, row)
        if r is None:
            return
        self._restore_as_new_chapter(r)

    def _restore_as_new_chapter(self, r: dict):
        v = r["version"]
        # 标题优先级：快照自带标题 > 章节当前标题（在用章节） > 兜底
        # 快照自带标题来自 create_snapshot 里存的 chapter_title，
        # 代表"这条快照产生时该章节叫什么"。用它做恢复默认标题，
        # 才能让用户看到"被合并章原名"，而不是被合并进 prev 后的当前名。
        snap_title = (v.get("chapter_title") or "").strip()
        if snap_title:
            title_default = f"{snap_title}（恢复）"
        elif r["in_book"] and r["chapter_title"]:
            title_default = f"{r['chapter_title']}（恢复）"
        else:
            title_default = f"恢复的章节 {r['chapter_id']}"

        # 卷名：优先快照元数据里记录的，提示用户
        volume = (v.get("chapter_volume") or "").strip()
        vol_hint = f"（将归入卷「{volume}」）" if volume else "（将归入默认卷「正文」）"

        title, ok = QInputDialog.getText(
            self,
            "另存为新章节",
            f"快照时间：{_format_time(v['time'])}\n"
            f"原章节：{v.get('chapter_title') or r['chapter_title'] or '（未知）'}\n"
            f"{vol_hint}\n\n"
            f"新章节标题：",
            text=title_default,
        )
        if not ok or not title.strip():
            return
        try:
            new_ch = self.work.restore_version_as_new_chapter(
                v["file"], title.strip(), volume=volume
            )
        except Exception as e:
            logger.error(f"另存新章节失败: {e}", exc_info=True)
            QMessageBox.critical(self, "恢复失败", str(e))
            return
        if new_ch is None:
            QMessageBox.warning(self, "恢复失败", "快照文件不可读。")
            return
        QMessageBox.information(
            self, "已恢复", f"新章节「{title.strip()}」已添加到书架。"
        )
        self.chapter_restored.emit(new_ch["id"])
        self._reload()
