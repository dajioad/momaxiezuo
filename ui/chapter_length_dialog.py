# ui/chapter_length_dialog.py
"""墨码写作 - 章节字数分布图

- 每章一行：章节号 + 标题 + 条形（按字数比例）+ 字数
- 条形四档配色：偏短/正常/偏长/过长
- 目标参考线：2000 / 4000
- 悬停高亮 + tooltip，点击行跳到那章
- 自绘 + QScrollArea，500 章也能流畅滚
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QWidget,
)
from PySide6.QtCore import Qt, Signal, QRect, QRectF, QTimer
from PySide6.QtGui import (
    QPainter,
    QColor,
    QFont,
    QFontMetrics,
    QPainterPath,
    QPen,
)

from ui.theme_style import (
    qss_alpha,
    SEMANTIC_OK,
    SEMANTIC_WARN,
    SEMANTIC_BAD,
    SEMANTIC_INFO,
)

# 字数区间（硬编码；后续可挪到配置里）
TARGET_MIN = 2000
TARGET_MAX = 4000
HUGE = 6000

ROW_H = 30
TOP_PAD = 20  # 顶部留给参考线标签的高度
PAD_H = 6
TITLE_W = 130
WORDS_W = 68
GAP = 12


class _BarChart(QWidget):
    """自绘：每章一行，条形长度按比例。"""

    row_clicked = Signal(str)  # chapter_id

    def __init__(self, data: list, theme: dict, parent=None):
        super().__init__(parent)
        self._data = list(data)
        self._theme = theme or {}
        self.setMouseTracking(True)
        self._hover_row = -1
        total_h = TOP_PAD + max(1, len(self._data)) * ROW_H
        self.setMinimumHeight(total_h)
        self._scale_max = self._compute_scale_max()

    def _compute_scale_max(self) -> int:
        if not self._data:
            return 1
        m = max(d["words"] for d in self._data)
        return max(int(m * 1.05), int(TARGET_MAX * 1.2), 1)

    def _level(self, words: int) -> str:
        if words < TARGET_MIN:
            return "short"
        if words > HUGE:
            return "huge"
        if words > TARGET_MAX:
            return "long"
        return "ok"

    def _color_for(self, level: str) -> QColor:
        if level == "ok":
            return QColor(SEMANTIC_OK)
        if level == "huge":
            return QColor(SEMANTIC_BAD)
        if level == "short":
            return QColor(SEMANTIC_INFO)  # 蓝：偏短
        return QColor(SEMANTIC_WARN)  # 橙：偏长

    def _track_rect(self) -> QRect:
        left = PAD_H + TITLE_W + GAP
        right = self.width() - PAD_H - WORDS_W - GAP
        w = max(10, right - left)
        return QRect(left, 0, w, self.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        t = self._theme
        text_c = QColor(t.get("text", "#3b342a"))
        muted = QColor(t.get("muted", "#9a8e76"))
        accent = QColor(t.get("accent", "#b08a4e"))
        editor_bg = QColor(t.get("editor_bg", "#fcfaf4"))
        bg_hover = QColor(accent)
        bg_hover.setAlpha(0x14)

        p.fillRect(self.rect(), editor_bg)

        if not self._data:
            p.setPen(muted)
            p.setFont(QFont(self.font().family(), 11))
            p.drawText(self.rect(), Qt.AlignCenter, "本作品还没有章节。")
            p.end()
            return

        track = self._track_rect()
        track_left = track.left()
        track_w = track.width()

        # 目标参考线（竖直虚线）
        pen = QPen(accent)
        pen.setStyle(Qt.DashLine)
        pen.setWidthF(1.0)
        p.setPen(pen)
        for words in (TARGET_MIN, TARGET_MAX):
            x = track_left + int(words / self._scale_max * track_w)
            p.drawLine(x, TOP_PAD, x, self.height())

        # 参考线标签
        p.setFont(QFont(self.font().family(), 9))
        p.setPen(accent)
        for words, label in ((TARGET_MIN, "2000"), (TARGET_MAX, "4000")):
            x = track_left + int(words / self._scale_max * track_w)
            p.drawText(
                QRect(x - 22, 0, 44, TOP_PAD - 2),
                Qt.AlignHCenter | Qt.AlignBottom,
                label,
            )

        # 可见行范围
        visible_top = max(0, (event.rect().top() - TOP_PAD) // ROW_H)
        visible_bottom = min(
            len(self._data), (event.rect().bottom() - TOP_PAD) // ROW_H + 2
        )

        title_font = QFont(self.font().family(), 12)
        num_font = QFont(self.font().family(), 11)
        words_font = QFont(self.font().family(), 11)
        words_font.setBold(True)
        fm_title = QFontMetrics(title_font)

        for row in range(visible_top, visible_bottom):
            d = self._data[row]
            y = TOP_PAD + row * ROW_H

            if row == self._hover_row:
                p.fillRect(QRect(PAD_H, y, self.width() - 2 * PAD_H, ROW_H), bg_hover)

            # 章节号
            p.setFont(num_font)
            p.setPen(muted)
            p.drawText(
                QRect(PAD_H, y, 26, ROW_H),
                Qt.AlignVCenter | Qt.AlignRight,
                f"{d['num']:02d}",
            )

            # 标题
            p.setFont(title_font)
            p.setPen(text_c)
            title_x = PAD_H + 30
            title_w = TITLE_W - 30
            elided = fm_title.elidedText(d["title"], Qt.ElideRight, title_w)
            p.drawText(
                QRect(title_x, y, title_w, ROW_H),
                Qt.AlignVCenter | Qt.AlignLeft,
                elided,
            )

            # 条形
            bar_h = 16
            bar_y = y + (ROW_H - bar_h) // 2
            bar_w = max(2, int(d["words"] / self._scale_max * track_w))
            color = self._color_for(self._level(d["words"]))
            path = QPainterPath()
            path.addRoundedRect(QRectF(track_left, bar_y, bar_w, bar_h), 3, 3)
            p.fillPath(path, color)

            # 字数
            p.setFont(words_font)
            p.setPen(text_c)
            words_x = self.width() - PAD_H - WORDS_W
            p.drawText(
                QRect(words_x, y, WORDS_W, ROW_H),
                Qt.AlignVCenter | Qt.AlignRight,
                f"{d['words']:,}",
            )

        p.end()

    def _row_at(self, y: float) -> int:
        row = int((y - TOP_PAD) // ROW_H)
        if row < 0 or row >= len(self._data):
            return -1
        return row

    def mouseMoveEvent(self, event):
        y = event.position().y() if hasattr(event, "position") else event.pos().y()
        row = self._row_at(y)
        if row != self._hover_row:
            self._hover_row = row
            self.update()
        if row >= 0:
            d = self._data[row]
            self.setToolTip(f"第 {d['num']} 章 · {d['title']}\n{d['words']:,} 字")
        else:
            self.setToolTip("")

    def leaveEvent(self, event):
        if self._hover_row != -1:
            self._hover_row = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        y = event.position().y() if hasattr(event, "position") else event.pos().y()
        row = self._row_at(y)
        if row >= 0:
            self.row_clicked.emit(self._data[row]["cid"])


class ChapterLengthDialog(QDialog):
    """章节字数分布图。"""

    goto_chapter = Signal(str)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work
        self._theme = self._resolve_theme()

        self.setObjectName("chapterLengthDialog")
        self.setWindowTitle(f"章节字数分布 - {work.title}")
        self.setMinimumSize(820, 620)

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
            p = p.parent()
        return {}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("章节字数分布")
        title.setObjectName("chartTitle")
        root.addWidget(title)

        chapters = self._work.chapters or []
        sub = QLabel(f"《{self._work.title}》 · 共 {len(chapters)} 章")
        sub.setObjectName("chartSubtitle")
        root.addWidget(sub)

        # 数据准备
        self._data = []
        for i, c in enumerate(chapters):
            self._data.append(
                {
                    "num": i + 1,
                    "title": c.get("title", "") or "未命名",
                    "words": int(c.get("word_count", 0)),
                    "cid": c.get("id", ""),
                }
            )

        # 顶部统计
        stat_row = QHBoxLayout()
        stat_row.setSpacing(24)
        if self._data:
            words = [d["words"] for d in self._data]
            avg = sum(words) // len(words)
            mx = max(words)
            mn = min(words)
            for label, value, accent in (
                ("平均", f"{avg:,} 字", True),
                ("最长", f"{mx:,} 字", False),
                ("最短", f"{mn:,} 字", False),
                ("目标区间", f"{TARGET_MIN:,} – {TARGET_MAX:,}", False),
            ):
                col = QVBoxLayout()
                col.setSpacing(2)
                lab = QLabel(label)
                lab.setObjectName("statLabel")
                col.addWidget(lab)
                val = QLabel(value)
                val.setObjectName("statValueAccent" if accent else "statValue")
                col.addWidget(val)
                wrap = QWidget()
                wrap.setLayout(col)
                stat_row.addWidget(wrap)
        stat_row.addStretch()
        root.addLayout(stat_row)

        # 分隔线
        sep = QFrame()
        sep.setObjectName("chartSep")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # 图表
        self._chart = _BarChart(self._data, self._theme, self)
        self._chart.row_clicked.connect(self._on_row_clicked)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self._chart)
        root.addWidget(scroll, 1)

        # 图例
        legend = QHBoxLayout()
        legend.setSpacing(20)
        legend.addStretch()
        for color, text in (
            (SEMANTIC_INFO, f"偏短（< {TARGET_MIN:,}）"),
            (SEMANTIC_OK, "正常"),
            (SEMANTIC_WARN, f"偏长（> {TARGET_MAX:,}）"),
            (SEMANTIC_BAD, f"过长（> {HUGE:,}）"),
        ):
            item = QHBoxLayout()
            item.setSpacing(6)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
            item.addWidget(dot)
            lab = QLabel(text)
            lab.setObjectName("legendLabel")
            item.addWidget(lab)
            legend.addLayout(item)
        legend.addStretch()
        root.addLayout(legend)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _on_row_clicked(self, cid: str):
        if not cid:
            return
        # 先排队等对话框关闭，再 emit——否则 workspace 收到信号时
        # 对话框还挡着编辑器，用户看不到跳转结果
        QTimer.singleShot(0, lambda c=cid: self.goto_chapter.emit(c))
        self.accept()

    def _apply_theme(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")

        self.setStyleSheet(
            f"#chapterLengthDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text_c}; background: transparent; }}"
            f"QLabel#chartTitle {{ font-size: 18px; font-weight: 700; }}"
            f"QLabel#chartSubtitle {{ font-size: 12px; color: {muted}; }}"
            f"QLabel#statLabel {{ font-size: 11px; color: {muted}; }}"
            f"QLabel#statValue {{ font-size: 17px; font-weight: 700;"
            f" color: {text_c}; }}"
            f"QLabel#statValueAccent {{ font-size: 17px; font-weight: 700;"
            f" color: {accent}; }}"
            f"QLabel#legendLabel {{ font-size: 11.5px; color: {muted}; }}"
            f"QFrame#chartSep {{ background: {qss_alpha(muted, 0x22)};"
            f" border: none; }}"
            f"QPushButton {{ color: {text_c}; background: transparent;"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 6px 16px; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {accent}; border-color: {accent};"
            f" background: {qss_alpha(accent, 0x14)}; }}"
            f"QScrollArea {{ border: none; background: transparent; }}"
        )
