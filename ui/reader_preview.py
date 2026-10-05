# ui/reader_preview.py
"""墨码写作 - 读者视角预览（模拟手机屏幕宽度展示正文分段节奏）"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QPlainTextEdit,
    QFrame,
    QComboBox,
)
from PySide6.QtCore import Qt

import qtawesome as qta

from ui.theme_style import qss_alpha, scrollbar_qss

# 手机屏宽档位 (px, 描述)
PREVIEW_WIDTHS = [
    (375, "iPhone (375px)"),
    (360, "Android (360px)"),
    (414, "Plus (414px)"),
    (320, "小屏 (320px)"),
]


class ReaderPreviewDialog(QDialog):
    """以手机屏宽预览正文，让作者感受读者的阅读节奏"""

    def __init__(self, text: str, title: str = "", parent=None):
        super().__init__(parent)
        self.original_text = text
        self.setWindowTitle("读者视角预览")
        self.resize(440, 600)
        self._build_ui(title)
        self.apply_theme_colors()

    def _resolve_theme(self) -> dict:
        """沿 parent 链查找写作主题"""
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    return editor._theme or {}
                break
            if hasattr(p, "_theme"):
                return getattr(p, "_theme") or {}
            p = p.parent()
        return {}

    def apply_theme_colors(self):
        """根据当前主题刷新预览区域配色"""
        t = self._resolve_theme()
        if not t:
            return
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text_color = t.get("text", "#3b342a")
        accent = t.get("accent", "#b08a4e")
        sidebar = t.get("sidebar", "#efe8d8")
        muted = t.get("muted", "#9a8e76")
        # 外框：黑色渐变手机壳
        self.phone_frame.setStyleSheet(
            f"QFrame {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 #2a2a2a, stop:1 #1a1a1a);"
            f" border-radius: 22px; border: none; padding: 8px; }}"
        )
        # 刘海
        self.notch.setStyleSheet(
            "QFrame { background: #0d0d0d; border: none;" " border-radius: 2.5px; }"
        )
        # 预览正文：editor_bg 纸色 + text 文字色；合并细滚动条（自带样式表会屏蔽 app 级 QSS）
        self.preview.setStyleSheet(
            f"QPlainTextEdit {{ background: {editor_bg}; color: {text_color};"
            f" font-size: 16px; line-height: 1.8; padding: 20px 14px;"
            f" border-radius: 16px; border: none;"
            f" selection-background-color: {accent};"
            f" selection-color: {t.get('accent_fg', 'white')}; }}" + scrollbar_qss(t)
        )
        # 统计文字
        self.stats_label.setStyleSheet(f"font-size: 12px; color: {muted};")

    def _build_ui(self, title: str):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 顶部信息
        top = QHBoxLayout()
        if title:
            lab = QLabel(title)
            lab.setStyleSheet("font-size: 14px; font-weight: bold;")
            book_ico = QLabel()
            book_ico.setPixmap(qta.icon(
                "fa5s.book-open",
                color=self._resolve_theme().get("text", "#3b342a")
            ).pixmap(16, 16))
            top.addWidget(book_ico)
            top.addWidget(lab)
        top.addStretch()
        self.width_combo = QComboBox()
        for wpx, desc in PREVIEW_WIDTHS:
            self.width_combo.addItem(desc, wpx)
        self.width_combo.currentIndexChanged.connect(self._refresh)
        top.addWidget(QLabel("屏宽："))
        top.addWidget(self.width_combo)
        lay.addLayout(top)

        # 预览区（模拟手机外框）
        self.phone_frame = QFrame()
        phone_lay = QVBoxLayout(self.phone_frame)
        phone_lay.setContentsMargins(8, 8, 8, 8)
        phone_lay.setSpacing(4)

        # 顶部黑色圆角"刘海"
        notch_row = QHBoxLayout()
        notch_row.setContentsMargins(0, 0, 0, 0)
        self.notch = QFrame()
        self.notch.setFixedSize(60, 5)
        notch_row.addStretch(1)
        notch_row.addWidget(self.notch)
        notch_row.addStretch(1)
        phone_lay.addLayout(notch_row)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFrameShape(QFrame.NoFrame)
        phone_lay.addWidget(self.preview, 1, Qt.AlignHCenter)
        lay.addWidget(self.phone_frame, 1)

        # 底部统计
        self.stats_label = QLabel("")
        lay.addWidget(self.stats_label)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn, alignment=Qt.AlignRight)

        self._refresh()

    def _refresh(self):
        wpx = self.width_combo.currentData() or 375
        self.preview.setMaximumWidth(wpx)
        self.preview.setMinimumWidth(min(wpx, 360))
        self.preview.setPlainText(self.original_text)

        text = self.original_text
        paragraphs = [p for p in text.split("\n") if p.strip()]
        total_chars = len(text.replace("\n", "").replace(" ", ""))
        avg_para = total_chars // max(len(paragraphs), 1) if paragraphs else 0
        self.stats_label.setText(
            f"总字数 {total_chars}  |  段落数 {len(paragraphs)}  |  "
            f"平均段长 {avg_para} 字  |  屏宽 {wpx}px\n"
            f"提示：网文阅读每段建议 2-4 行（约 50-100 字），"
            f"段落过长的读者容易跳读。"
        )
