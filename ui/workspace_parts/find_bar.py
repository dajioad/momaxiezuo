"""墨码写作 - 查找替换浮条"""

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtCore import Qt, Signal
import qtawesome as qta

from ui.theme_style import qss_alpha


class _FindBar(QFrame):
    """轻量查找替换条（悬浮于编辑器顶部）"""

    prev = Signal(str)
    next_ = Signal(str)
    replace_one = Signal(str, str)
    replace_all = Signal(str, str)
    request_highlight = Signal(str)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = {
            "sidebar": "#efe8d8",
            "accent": "#b08a4e",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "bg": "#f7f2e8",
        }
        # 去掉原生边框，使用 QSS 绘制阴影和圆角
        self.setFrameShape(QFrame.NoFrame)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)  # 增加内边距
        lay.setSpacing(10)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("查找内容...")
        self.find_edit.setMinimumWidth(180)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("替换为...")
        self.replace_edit.setMinimumWidth(180)

        for ed in (self.find_edit, self.replace_edit):
            lay.addWidget(ed)

        self.count_label = QLabel("")
        self.count_label.setFixedWidth(50)
        self.count_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.count_label)

        # 上一项、下一项改用图标按钮
        self.btn_prev = QPushButton()
        self.btn_prev.setIcon(qta.icon("fa5s.chevron-left"))
        self.btn_prev.setFixedSize(28, 28)
        self.btn_prev.setToolTip("上一个")

        self.btn_next = QPushButton()
        self.btn_next.setIcon(qta.icon("fa5s.chevron-right"))
        self.btn_next.setFixedSize(28, 28)
        self.btn_next.setToolTip("下一个")

        lay.addWidget(self.btn_prev)
        lay.addWidget(self.btn_next)

        # 替换按钮
        self.btn_rep = QPushButton("替换")
        self.btn_rep_all = QPushButton("全部替换")
        self.btn_rep.setCursor(Qt.PointingHandCursor)
        self.btn_rep_all.setCursor(Qt.PointingHandCursor)

        lay.addWidget(self.btn_rep)
        lay.addWidget(self.btn_rep_all)
        lay.addStretch()

        self.close = QPushButton()
        self.close.setIcon(qta.icon("fa5s.times"))
        self.close.setFixedSize(28, 28)
        self.close.setToolTip("关闭查找")
        self.close.setCursor(Qt.PointingHandCursor)
        self.close.clicked.connect(self.closed.emit)
        lay.addWidget(self.close)

        self.btn_next.clicked.connect(lambda: self.next_.emit(self.find_edit.text()))
        self.btn_prev.clicked.connect(lambda: self.prev.emit(self.find_edit.text()))
        self.btn_rep.clicked.connect(
            lambda: self.replace_one.emit(
                self.find_edit.text(), self.replace_edit.text()
            )
        )
        self.btn_rep_all.clicked.connect(
            lambda: self.replace_all.emit(
                self.find_edit.text(), self.replace_edit.text()
            )
        )
        self.find_edit.textChanged.connect(lambda t: self.request_highlight.emit(t))

        QShortcut(
            QKeySequence("Return"),
            self.find_edit,
            lambda: self.next_.emit(self.find_edit.text()),
        )
        self._apply_theme_colors()

        # ▼▼▼ 把阴影代码移到这里（只创建一次） ▼▼▼
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(176, 138, 78, 60))  # 金色，和边框呼应
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        # ▲▲▲ ▲▲▲

    def update_count(self, current: int, total: int):
        if total <= 0:
            self.count_label.setText("无匹配")
        elif current < 0:
            self.count_label.setText(f"0/{total}")
        else:
            self.count_label.setText(f"{current + 1}/{total}")

    def apply_theme(self, theme: dict):
        self._theme = theme or self._theme
        self._apply_theme_colors()

    def _apply_theme_colors(self):
        t = self._theme
        # 关键 1：背景改用 sidebar（暖黄），和编辑器米白区分开 —— 与校对条一致
        bg = t.get("sidebar", "#efe8d8")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        text_c = t.get("text", "#3b342a")
        hover = self._lighten_or_darken(accent)
        accent_fg = t.get("accent_fg", "white")
        input_bg = t.get("editor_bg", "#fcfaf4")

        # 关键 2：边框用实色强调色（金色），和校对条的红色形成"功能 / 警告"区分
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; "
            f"border: 1px solid {accent}; "
            f"border-radius: 10px; }}"
            # 防止子 QLabel 被外层 QFrame 规则命中
            f"QFrame > QLabel {{ border: none; background: transparent; }}"
        )

        # 输入框：白底 + 描边，聚焦时边框变主题色
        line_style = (
            f"QLineEdit {{ border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 5px 12px;"
            f" color: {text_c}; background-color: {input_bg};"
            f" font-size: 13px; }}"
            f"QLineEdit:focus {{ border: 1px solid {accent}; }}"
        )
        self.find_edit.setStyleSheet(line_style)
        self.replace_edit.setStyleSheet(line_style)
        self.count_label.setStyleSheet(
            f"color: {muted}; font-size: 12px; border: none; background: transparent;"
        )

        # 导航按钮：透明底 + hover 灰底
        nav_style = (
            f"QPushButton {{ border: none; border-radius: 6px; background: transparent;"
            f" color: {text_c}; }}"
            f"QPushButton:hover {{ background: {qss_alpha(muted, 0x22)}; }}"
            f"QPushButton:pressed {{ background: {qss_alpha(muted, 0x44)}; }}"
        )
        self.btn_prev.setStyleSheet(nav_style)
        self.btn_next.setStyleSheet(nav_style)
        self.btn_prev.setIcon(qta.icon("fa5s.chevron-left", color=text_c))
        self.btn_next.setIcon(qta.icon("fa5s.chevron-right", color=text_c))

        # 替换按钮：实色主按钮（金色）
        btn_style = (
            f"QPushButton {{ border: none; border-radius: 6px; padding: 6px 14px;"
            f" background: {accent}; color: {accent_fg}; font-size: 12px;"
            f" font-weight: 500; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
        )
        self.btn_rep.setStyleSheet(btn_style)
        self.btn_rep_all.setStyleSheet(btn_style)

        # 关闭按钮：hover 淡红底（和校对条一致）
        close_style = (
            f"QPushButton {{ border: none; border-radius: 6px; background: transparent; }}"
            f"QPushButton:hover {{ background: {qss_alpha('#e74c3c', 0x22)}; }}"
        )
        self.close.setStyleSheet(close_style)
        self.close.setIcon(qta.icon("fa5s.times", color=muted))

    @staticmethod
    def _lighten_or_darken(hex_color: str) -> str:
        # (保持原有逻辑不变)
        try:
            c = QColor(hex_color)
            h, s, l, _ = c.getHsl()
            l = min(255, l + 15) if l < 180 else max(0, l - 15)
            c.setHsl(h, s, l)
            return c.name()
        except Exception:
            return hex_color
