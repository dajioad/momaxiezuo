# ui/timeline_panel.py
"""墨码写作 - 时间线 / 事件线面板

竖向时间轴：按时代分段，每段列出事件。
单击选中，双击编辑，右键增删。

主题跟随写作主题（apply_theme）。数据落盘到 timeline.json。
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QMenu,
    QMessageBox,
    QDialog,
    QApplication,
)
from PySide6.QtCore import Signal, Qt, QSize
import qtawesome as qta

from ui.theme_style import qss_alpha
from utils.logger import get_logger
from utils import timeline as tl

logger = get_logger()


class _EventCard(QFrame):
    """单事件卡片（时间 · 圆点 · 标题 · 关联 · 描述）。"""

    clicked = Signal(str)  # event_id

    def __init__(self, ev: dict, chapter_title: str, theme: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("tlCard")
        self.ev = ev
        self._theme = theme or {}
        self._build(chapter_title)

    def _build(self, chapter_title: str):
        t = self._theme
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        editor_bg = t.get("editor_bg", "#fcfaf4")

        self.setStyleSheet(
            f"#tlCard {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x28)};"
            f" border-radius: 8px; }}"
            f"#tlCard:hover {{ border-color: {qss_alpha(accent, 0x66)}; }}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(8)

        # 左：时间标签（无内容时完全不占宽度）
        time_text = self.ev.get("story_time", "") or ""
        if time_text:
            time_lab = QLabel(time_text)
            time_lab.setStyleSheet(
                f"color: {accent}; font-size: 11px; font-weight: 600;"
                f" background: transparent;"
            )
            time_lab.setAlignment(Qt.AlignRight | Qt.AlignTop)
            time_lab.setFixedWidth(64)
            lay.addWidget(time_lab)
        # 无时间时：不添加任何 widget，让内容直接从左侧开始

        # 中：圆点
        dot_col = QVBoxLayout()
        dot_col.setContentsMargins(0, 0, 0, 0)
        dot_col.setSpacing(0)
        dot = QLabel("●")
        if self.ev.get("importance") == "minor":
            dot.setStyleSheet(
                f"color: {muted}; font-size: 9px; background: transparent;"
            )
        else:
            dot.setStyleSheet(
                f"color: {accent}; font-size: 11px; background: transparent;"
            )
        dot.setAlignment(Qt.AlignCenter)
        dot.setFixedWidth(14)
        dot_col.addWidget(dot)
        dot_col.addStretch()
        lay.addLayout(dot_col)

        # 右：内容
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)

        title = QLabel(self.ev.get("title", ""))
        title.setStyleSheet(
            f"color: {text}; font-size: 13px; font-weight: 600;"
            f" background: transparent;"
        )
        body.addWidget(title)

        # 元信息行：章节 + 人物 + 标签
        meta_bits = []
        if chapter_title:
            meta_bits.append(f"第 {chapter_title}")
        chars = self.ev.get("characters") or []
        if chars:
            meta_bits.append("、".join(chars[:3]))
        tags = self.ev.get("tags") or []
        if tags:
            meta_bits.append(" · ".join(tags[:2]))
        if meta_bits:
            meta_lab = QLabel("  ·  ".join(meta_bits))
            meta_lab.setStyleSheet(
                f"color: {muted}; font-size: 11px; background: transparent;"
            )
            meta_lab.setWordWrap(True)
            body.addWidget(meta_lab)

        desc = self.ev.get("description", "").strip()
        if desc:
            # 只显示一行，超长用省略号——避免一条描述撑高整张卡片
            desc_lab = QLabel(desc[:80] + ("…" if len(desc) > 80 else ""))
            desc_lab.setStyleSheet(
                f"color: {muted}; font-size: 11px; background: transparent;"
            )
            desc_lab.setWordWrap(False)
            body.addWidget(desc_lab)

        lay.addLayout(body, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.ev["id"])
        super().mousePressEvent(event)


class TimelinePanel(QWidget):
    """时间线面板。"""

    insert_text = Signal(str)
    goto_chapter = Signal(str)  # chapter_id
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tlRoot")
        self.work = None
        self._data = {"eras": [], "events": []}
        self._selected_id = None
        self._theme = {
            "sidebar": "#efe8d8",
            "accent": "#b08a4e",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "bg": "#f7f2e8",
        }
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 2)
        lay.setSpacing(6)

        # 顶部：搜索 + 新建
        top = QHBoxLayout()
        top.setSpacing(4)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤事件…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._refresh)
        top.addWidget(self.filter_edit, 1)

        self.add_btn = QPushButton()
        self.add_btn._icon_name = "fa5s.plus"
        self.add_btn.setFixedSize(30, 30)
        self.add_btn.setToolTip("新建事件")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_event)
        top.addWidget(self.add_btn)
        lay.addLayout(top)

        # 事件列表滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll = scroll

        self._container = QWidget()
        self._container_lay = QVBoxLayout(self._container)
        self._container_lay.setContentsMargins(0, 0, 6, 0)
        self._container_lay.setSpacing(6)
        self._container_lay.addStretch()
        scroll.setWidget(self._container)
        lay.addWidget(scroll, 1)

        self.tip_label = QLabel("单击选中 · 双击编辑 · 右键跳章 / 删除")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.tip_label)

        self._apply_styles()

    def _apply_styles(self):
        t = self._theme
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "white")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")

        self.setStyleSheet(f"""
            #tlRoot {{ background-color: {t.get('sidebar', '#efe8d8')}; }}
            QWidget {{ color: {text}; background: transparent; }}
            QScrollArea {{ border: none; background: transparent; }}
            QLineEdit {{
                background-color: {editor_bg}; color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 6px; padding: 5px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {accent}; }}
            QLabel {{ color: {muted}; }}
        """)
        self.add_btn.setStyleSheet(
            f"QPushButton {{ background-color: {accent}; color: {accent_fg};"
            f" border: none; border-radius: 6px; padding: 0; }}"
        )
        self.add_btn.setIcon(qta.icon("fa5s.plus", color=accent_fg))

    def apply_theme(self, theme: dict):
        self._theme = theme or self._theme
        self._apply_styles()
        self._refresh()

    # ---------------- 数据 ----------------
    def set_work(self, work):
        self.work = work
        self._reload()

    def _reload(self):
        self._data = tl.load_all(self.work) if self.work else {"eras": [], "events": []}
        self.add_btn.setEnabled(self.work is not None)
        self._refresh()

    def _refresh(self):
        # 清空容器
        while self._container_lay.count() > 1:
            item = self._container_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self._data.get("events"):
            empty = QLabel("（还没有事件，点右上角 + 新建）")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {self._theme.get('muted', '#9a8e76')};"
                f" font-size: 12px; padding: 40px 10px;"
            )
            self._container_lay.insertWidget(0, empty)
            return

        kw = self.filter_edit.text().strip().lower()
        eras = {e["id"]: e["label"] for e in self._data.get("eras", [])}
        era_order = sorted(
            self._data.get("eras", []), key=lambda e: e.get("sort_key", 0)
        )

        events = tl.sorted_events(self.work)
        # 按 era 分组
        by_era = {}
        for ev in events:
            eid = ev.get("era_id", "")
            by_era.setdefault(eid, []).append(ev)

        # 按 era_order 遍历
        ordered_era_ids = [e["id"] for e in era_order]
        # 追加未在 era 里出现的（含空 era）
        for eid in by_era.keys():
            if eid not in ordered_era_ids:
                ordered_era_ids.append(eid)

        row = 0
        for eid in ordered_era_ids:
            evs = by_era.get(eid, [])
            if kw:
                evs = [
                    ev
                    for ev in evs
                    if kw
                    in (
                        ev.get("title", "")
                        + " "
                        + ev.get("description", "")
                        + " "
                        + " ".join(ev.get("characters") or [])
                    ).lower()
                ]
            if not evs:
                continue

            # 时代标题
            label = eras.get(eid, "")
            if label:
                era_lab = QLabel(f"● {label}")
                era_lab.setStyleSheet(
                    f"color: {self._theme.get('accent', '#b08a4e')};"
                    f" font-size: 11px; font-weight: 700;"
                    f" letter-spacing: 1px; padding: 4px 4px 2px 4px;"
                    f" background: transparent;"
                )
                self._container_lay.insertWidget(row, era_lab)
                row += 1

            for ev in evs:
                chapter_title = ""
                cid = ev.get("chapter_id", "")
                if cid and self.work:
                    ch = self.work.get_chapter(cid)
                    if ch:
                        chapter_title = ch.get("title", "")
                card = _EventCard(ev, chapter_title, self._theme)
                card.clicked.connect(self._on_card_clicked)
                card.setContextMenuPolicy(Qt.CustomContextMenu)
                card.customContextMenuRequested.connect(
                    lambda pos, eid=ev["id"], c=card: self._show_menu(c, pos)
                )
                self._container_lay.insertWidget(row, card)
                row += 1

    def _current_id(self):
        return self._selected_id

    def _find(self, event_id: str):
        for ev in self._data.get("events", []):
            if ev["id"] == event_id:
                return ev
        return None

    # ---------------- 交互 ----------------
    def _on_card_clicked(self, event_id: str):
        self._selected_id = event_id

    def _add_event(self):
        if self.work is None:
            return
        from ui.timeline_edit_dialog import TimelineEditDialog

        dlg = TimelineEditDialog(self, work=self.work)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not data.get("title"):
            return
        tl.add_event(
            self.work,
            data["title"],
            story_time=data.get("story_time", ""),
            story_time_sort=data.get("story_time_sort", 0),
            era_id=data.get("era_id", ""),
            chapter_id=data.get("chapter_id", ""),
            description=data.get("description", ""),
            characters=data.get("characters") or [],
            importance=data.get("importance", "major"),
            tags=data.get("tags") or [],
            duration=data.get("duration", ""),
        )
        self._reload()
        self.data_changed.emit()

    def _edit_event(self, event_id: str):
        ev = self._find(event_id)
        if ev is None:
            return
        from ui.timeline_edit_dialog import TimelineEditDialog

        dlg = TimelineEditDialog(self, work=self.work, event=ev)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        tl.update_event(self.work, event_id, **data)
        self._reload()
        self.data_changed.emit()

    def _delete_event(self, event_id: str):
        ev = self._find(event_id)
        if ev is None:
            return
        title = ev.get("title", "")
        if (
            QMessageBox.question(
                self, "删除事件", f"确定删除「{title}」？此操作不可恢复。"
            )
            != QMessageBox.Yes
        ):
            return
        tl.delete_event(self.work, event_id)
        self._reload()
        self.data_changed.emit()

    def _show_menu(self, card: QFrame, pos):
        event_id = card.ev["id"]
        menu = QMenu(self)
        act_edit = menu.addAction("编辑…")
        # 有关联章节时提供跳转
        cid = card.ev.get("chapter_id", "")
        act_goto = None
        if cid:
            act_goto = menu.addAction("跳转到关联章节")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        chosen = menu.exec(card.mapToGlobal(pos))
        if chosen == act_edit:
            self._edit_event(event_id)
        elif act_goto is not None and chosen == act_goto:
            self.goto_chapter.emit(cid)
        elif chosen == act_del:
            self._delete_event(event_id)
