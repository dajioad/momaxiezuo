"""墨码写作 - 校对结果浮条"""

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, Signal
import qtawesome as qta

from ui.theme_style import qss_alpha


class _ProofBar(QFrame):
    """校对结果浮条（悬浮在编辑器顶部）。"""

    detail_clicked = Signal()
    clear_clicked = Signal()
    filter_changed = Signal(str)
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
        self.setFrameShape(QFrame.NoFrame)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        self.icon_label = QLabel()
        lay.addWidget(self.icon_label)

        self.summary_label = QLabel("")
        lay.addWidget(self.summary_label)

        lay.addSpacing(8)
        self.filter_combo = QComboBox()
        self.filter_combo.setFixedWidth(140)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        lay.addWidget(self.filter_combo)

        lay.addStretch()

        self.detail_btn = QPushButton("详情")
        self.detail_btn.setFixedHeight(28)
        self.detail_btn.setCursor(Qt.PointingHandCursor)
        self.detail_btn.clicked.connect(self.detail_clicked.emit)
        lay.addWidget(self.detail_btn)

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setFixedHeight(28)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        lay.addWidget(self.clear_btn)

        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.closed.emit)
        lay.addWidget(self.close_btn)

        self._apply_theme_colors()

        # ▼▼▼ 把阴影代码移到这里（只创建一次） ▼▼▼
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)  # 12 → 24
        shadow.setColor(QColor(231, 76, 60, 60))  # 用警告红色调，更贴合语义
        shadow.setOffset(0, 4)  # 2 → 4
        self.setGraphicsEffect(shadow)
        # ▲▲▲ ▲▲▲

    def show_results(self, items: list):
        from collections import Counter

        cats = Counter()
        for r in items:
            cat = r.get("category", "")
            if "敏感词" in cat:
                cats["敏感词"] += 1
            elif "错别字" in cat or "自定义" in cat:
                cats["错别字"] += 1
            elif "引号" in cat:  # ← "引号配对" 独立成一档
                cats["引号配对"] += 1
            elif "标点" in cat:
                cats["标点"] += 1
            elif "格式" in cat:
                cats["格式"] += 1
            else:
                cats["用语提示"] += 1
        parts = []
        order = ["敏感词", "错别字", "引号配对", "标点", "格式", "用语提示"]
        for k in order:
            if cats.get(k):
                parts.append(f"{k} {cats[k]}")
        self.summary_label.setText(f"共 {len(items)} 处：" + " · ".join(parts))

        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("全部", "")
        for k in order:
            if cats.get(k):
                self.filter_combo.addItem(k, k)
        self.filter_combo.setCurrentIndex(0)
        self.filter_combo.blockSignals(False)

    def _on_filter_changed(self, _idx):
        self.filter_changed.emit(self.filter_combo.currentData() or "")

    def apply_theme(self, theme: dict):
        self._theme = theme or self._theme
        self._apply_theme_colors()

    def _apply_theme_colors(self):
        t = self._theme
        # 关键 1：背景改用 sidebar（暖黄），和编辑器米白区分开
        bg = t.get("sidebar", "#efe8d8")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        text_c = t.get("text", "#3b342a")

        warn = "#e74c3c"

        self.icon_label.setPixmap(
            qta.icon("fa5s.exclamation-triangle", color=warn).pixmap(16, 16)
        )

        # 关键 2：边框用实红（不再是 27% 透明）
        # 关键 3：内侧加一层淡淡的红晕背景，提示这是"警告卡片"
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; "
            f"border: 1px solid {warn}; "
            f"border-radius: 10px; }}"
            f"QFrame > QLabel {{ border: none; background: transparent; }}"
        )

        self.summary_label.setStyleSheet(
            f"color: {text_c}; font-size: 13px; font-weight: 600;"
        )

        # 详情按钮：次级按钮
        detail_ss = (
            f"QPushButton {{ border: 1px solid {qss_alpha(muted, 0x66)};"
            f" border-radius: 6px; padding: 5px 14px;"
            f" background: {t.get('editor_bg', '#fcfaf4')}; color: {text_c};"
            f" font-size: 12px; }}"
            f"QPushButton:hover {{ background: {accent}; color: white;"
            f" border-color: {accent}; }}"
        )
        self.detail_btn.setStyleSheet(detail_ss)

        # 清除按钮：危险动作，红色主题
        clear_ss = (
            f"QPushButton {{ border: 1px solid {qss_alpha('#e74c3c', 0x66)};"
            f" border-radius: 6px; padding: 5px 14px;"
            f" background: {t.get('editor_bg', '#fcfaf4')}; color: {warn};"
            f" font-size: 12px; }}"
            f"QPushButton:hover {{ background: {warn}; color: white;"
            f" border-color: {warn}; }}"
        )
        self.clear_btn.setStyleSheet(clear_ss)

        # 关闭按钮
        close_ss = (
            f"QPushButton {{ border: none; border-radius: 6px; background: transparent; }}"
            f"QPushButton:hover {{ background: {qss_alpha(muted, 0x33)}; }}"
        )
        self.close_btn.setStyleSheet(close_ss)
        self.close_btn.setIcon(qta.icon("fa5s.times", color=muted))

        # 下拉框
        self.filter_combo.setStyleSheet(
            f"QComboBox {{ background-color: {t.get('editor_bg', '#fcfaf4')};"
            f" color: {text_c}; border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 4px 10px; font-size: 12px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox::drop-down {{ border: none; width: 22px; }}"
        )
