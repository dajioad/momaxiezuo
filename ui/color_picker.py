# ui/color_picker.py
"""墨码写作 - 颜色选择对话框

12 色预置色板 + "自定义颜色…"入口。用于词条分类 / 人物高亮配色。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLabel,
    QDialogButtonBox,
    QColorDialog,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ui.theme_style import apply_dialog_theme

# 12 色预置色板（明暗主题下都清晰）
PRESET_COLORS = [
    "#3B82F6",  # 蓝
    "#8B5CF6",  # 紫
    "#10B981",  # 绿
    "#F59E0B",  # 橙
    "#EF4444",  # 红
    "#06B6D4",  # 青
    "#EC4899",  # 粉
    "#84CC16",  # 黄绿
    "#A855F7",  # 深紫
    "#F97316",  # 深橙
    "#14B8A6",  # 蓝绿
    "#64748B",  # 灰
]


def default_category_colors() -> dict:
    """内置分类的默认色。"""
    return {
        "角色姓名": "#3B82F6",
        "技能名称": "#8B5CF6",
        "地名势力": "#10B981",
        "物品法宝": "#F59E0B",
        "其他": "#64748B",
    }


def character_default_color() -> str:
    return "#3B82F6"


class ColorPickerDialog(QDialog):
    """颜色选择对话框。"""

    def __init__(self, current: str = "", title: str = "选择颜色", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self._color = current or PRESET_COLORS[0]
        self._build_ui()
        apply_dialog_theme(self)

    def _accent_color(self) -> str:
        """沿 parent 链找当前主题 accent，兜底纸墨金。"""
        p = self.parent()
        while p is not None:
            t = getattr(p, "_theme", None)
            if isinstance(t, dict) and t.get("accent"):
                return t["accent"]
            p = p.parent()
        return "#b08a4e"

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hint = QLabel("点击色块选择，或自定义：")
        root.addWidget(hint)

        grid = QGridLayout()
        grid.setSpacing(10)
        self._buttons = []
        self._rings = []
        for i, c in enumerate(PRESET_COLORS):
            btn = QPushButton()
            btn.setFixedSize(56, 40)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(c)
            # 金色外环用零偏移柔光模拟（QSS 不支持双 outline）
            ring = QGraphicsDropShadowEffect(btn)
            ring.setBlurRadius(4)
            ring.setOffset(0)
            ring.setColor(QColor(0, 0, 0, 0))
            btn.setGraphicsEffect(ring)
            self._rings.append(ring)
            self._buttons.append((btn, c))
            btn.clicked.connect(lambda _=False, col=c: self._pick(col))
            grid.addWidget(btn, i // 4, i % 4)
        root.addLayout(grid)

        custom_row = QHBoxLayout()
        custom_row.addStretch()
        custom_btn = QPushButton("自定义颜色…")
        custom_btn.clicked.connect(self._pick_custom)
        custom_row.addWidget(custom_btn)
        custom_row.addStretch()
        root.addLayout(custom_row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._refresh_buttons()

    def _refresh_buttons(self):
        accent = self._accent_color()
        for (btn, c), ring_eff in zip(self._buttons, self._rings):
            selected = c.lower() == self._color.lower()
            if selected:
                # 白内边 + 金色外环（参照 ui优化2 #16）
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {c};"
                    f" border: 2px solid #ffffff; border-radius: 8px; }}"
                )
                ring_eff.setColor(QColor(accent))
                ring_eff.setBlurRadius(4)
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {c};"
                    f" border: 1px solid rgba(0,0,0,0.15);"
                    f" border-radius: 8px; }}"
                    f"QPushButton:hover {{ border: 2px solid {accent}; }}"
                )
                ring_eff.setColor(QColor(0, 0, 0, 0))

    def _pick(self, color: str):
        self._color = color
        self._refresh_buttons()

    def _pick_custom(self):
        # 用实例化方式创建 QColorDialog（静态方法 QColorDialog.getColor()
        # 拿不到对象引用，事件过滤器无法对其应用主题和汉化）
        dlg = QColorDialog(QColor(self._color), self)
        dlg.setWindowTitle("自定义颜色")
        dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        if dlg.exec() == QColorDialog.Accepted:
            c = dlg.currentColor()
            if c.isValid():
                self._color = c.name()
                self._refresh_buttons()

    def get_color(self) -> str:
        return self._color
