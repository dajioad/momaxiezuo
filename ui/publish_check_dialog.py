# ui/publish_check_dialog.py
"""墨码写作 - 发布前体检报告

接收 utils.publish_check 的结果，展示 4 项检查。
每项可点击「查看详情」跳转到对应对话框。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
)
from PySide6.QtCore import Qt, Signal

import qtawesome as qta

from ui.theme_style import (
    qss_alpha,
    SEMANTIC_OK,
    SEMANTIC_WARN,
    SEMANTIC_BAD,
)
from utils.publish_check import (
    STATUS_OK,
    STATUS_WARN,
    STATUS_BAD,
    STATUS_SKIPPED,
    overall_status,
)

_STATUS_META = {
    STATUS_OK: {"icon": "✓", "color": SEMANTIC_OK},
    STATUS_WARN: {"icon": "!", "color": SEMANTIC_WARN},
    STATUS_BAD: {"icon": "×", "color": SEMANTIC_BAD},
    STATUS_SKIPPED: {"icon": "·", "color": "#9a8e76"},
}


class _CheckCard(QFrame):
    """一项检查的卡片。"""

    detail_requested = Signal()

    def __init__(
        self,
        title: str,
        status: str,
        msg: str,
        theme: dict = None,
        can_open_detail: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("pubCheckCard")
        self.setAttribute(Qt.WA_StyledBackground, True)

        t = theme or {}
        editor_bg = t.get("editor_bg", "#fcfaf4")
        muted = t.get("muted", "#9a8e76")
        text_c = t.get("text", "#3b342a")

        meta = _STATUS_META.get(status, _STATUS_META[STATUS_WARN])
        color = meta["color"]
        icon = meta["icon"]

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)

        icon_lab = QLabel(icon)
        icon_lab.setFixedSize(32, 32)
        icon_lab.setAlignment(Qt.AlignCenter)
        icon_lab.setStyleSheet(
            f"color: {color}; font-size: 18px; font-weight: bold;"
            f" background: {qss_alpha(color, 0x20)};"
            f" border-radius: 16px;"
        )
        lay.addWidget(icon_lab)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)

        title_lab = QLabel(title)
        title_lab.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {text_c};"
            f" background: transparent;"
        )
        info.addWidget(title_lab)

        msg_lab = QLabel(msg or "—")
        msg_lab.setWordWrap(True)
        msg_lab.setStyleSheet(
            f"font-size: 12px; color: {color}; background: transparent;"
        )
        info.addWidget(msg_lab)

        lay.addLayout(info, 1)

        if can_open_detail and status != STATUS_SKIPPED:
            btn = QPushButton("查看详情")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ color: {text_c};"
                f" background: transparent;"
                f" border: 1px solid {qss_alpha(muted, 0x55)};"
                f" border-radius: 6px; padding: 5px 12px;"
                f" font-size: 12px; }}"
                f"QPushButton:hover {{ color: {color};"
                f" border-color: {color};"
                f" background: {qss_alpha(color, 0x14)}; }}"
            )
            btn.clicked.connect(self.detail_requested.emit)
            lay.addWidget(btn)

        self.setStyleSheet(
            f"#pubCheckCard {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-left: 4px solid {color};"
            f" border-radius: 10px; }}"
        )


class PublishCheckDialog(QDialog):
    """发布前体检报告。"""

    open_format_detail = Signal()
    open_proofread_detail = Signal()
    open_readability_detail = Signal()
    open_consistency_detail = Signal()
    rerun_requested = Signal()

    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self._result = result
        self._theme = self._resolve_theme()

        scope = result.get("scope", "current")
        work_title = result.get("work_title", "未命名作品")
        chapter_count = result.get("chapter_count", 0)
        chapter_title = result.get("chapter_title", "")

        if scope == "current":
            subtitle = f"《{work_title}》 · {chapter_title or '当前章'}"
        else:
            subtitle = f"《{work_title}》 · 全书 {chapter_count} 章"

        self.setObjectName("publishCheckDialog")
        self.setWindowTitle("发布前体检")
        # 4 张卡片 + 底部按钮实际只需 ~470；620 会在小屏上超出工作区，
        # 且卡片列表已自带滚动。
        self.setMinimumSize(640, 470)
        self._build_ui(subtitle)
        self._apply_theme()
        self._populate()

    def _resolve_theme(self) -> dict:
        # 统一走 theme_style._resolve_dialog_theme。
        # 原实现没有 break，会一直沿 parent 找到 root 才停——
        # 效率低且可能命中比 workspace 更上层的 widget。统一入口
        # 后既修了这个隐患，也让本对话框和其它对话框的主题解析一致。
        from ui.theme_style import _resolve_dialog_theme
        return _resolve_dialog_theme(self)

    def _build_ui(self, subtitle: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)

        icon = QLabel()
        icon.setPixmap(qta.icon(
            "fa5s.clipboard-check",
            color=self._theme.get("accent", "#b08a4e")).pixmap(24, 24))
        head.addWidget(icon)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)

        title = QLabel("发布前体检")
        title.setObjectName("pubTitle")
        title_box.addWidget(title)

        sub = QLabel(subtitle)
        sub.setObjectName("pubSubtitle")
        title_box.addWidget(sub)

        head.addLayout(title_box)
        head.addStretch()

        self.overall_badge = QLabel("")
        self.overall_badge.setObjectName("pubOverallBadge")
        head.addWidget(self.overall_badge)

        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(0, 4, 0, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()

        self.rerun_btn = QPushButton("重新体检")
        self.rerun_btn.setCursor(Qt.PointingHandCursor)
        self.rerun_btn.clicked.connect(self.rerun_requested.emit)
        bottom.addWidget(self.rerun_btn)

        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        root.addLayout(bottom)

    def _populate(self):
        r = self._result

        self._add_card("排版格式", r.get("format", {}), self.open_format_detail)
        self._add_card(
            "校对（错别字 / 敏感词 / 标点）",
            r.get("proofread", {}),
            self.open_proofread_detail,
        )
        self._add_card(
            "追读体检", r.get("readability", {}), self.open_readability_detail
        )
        self._add_card(
            "一致性检查（同人不同名）",
            r.get("consistency", {}),
            self.open_consistency_detail,
        )

        oa = overall_status(r)
        meta = _STATUS_META.get(oa, _STATUS_META[STATUS_WARN])
        color = meta["color"]
        text = {
            STATUS_OK: "可以发布",
            STATUS_WARN: "建议处理后发布",
            STATUS_BAD: "建议处理后再发布",
        }.get(oa, "")
        self.overall_badge.setText(f"  {text}  ")
        self.overall_badge.setStyleSheet(
            f"color: {color};"
            f" background: {qss_alpha(color, 0x20)};"
            f" padding: 6px 14px;"
            f" border-radius: 14px;"
            f" font-size: 13px; font-weight: 600;"
        )

    def _add_card(self, title: str, data: dict, signal):
        card = _CheckCard(
            title,
            data.get("status", STATUS_OK),
            data.get("msg", ""),
            theme=self._theme,
            can_open_detail=True,
        )
        card.detail_requested.connect(signal.emit)
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

    def _apply_theme(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")

        self.setStyleSheet(
            f"#publishCheckDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text_c}; background: transparent; }}"
            f"QLabel#pubTitle {{ font-size: 18px; font-weight: 700; }}"
            f"QLabel#pubSubtitle {{ font-size: 12px; color: {muted}; }}"
            f"QPushButton {{ color: {text_c};"
            f" background: transparent;"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 6px 16px;"
            f" font-size: 13px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent};"
            f" background: {qss_alpha(accent, 0x14)}; }}"
            f"QScrollArea {{ border: none; background: transparent; }}"
        )
