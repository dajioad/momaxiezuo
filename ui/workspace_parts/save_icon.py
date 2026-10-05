# ui/workspace_parts/save_icon.py
"""墨码写作 - 状态栏保存状态小图标。

三态自绘：saving 转圈弧 / saved 对勾 / error 感叹号。
从 workspace.py 拆出，供 WritingWorkspace 状态栏使用。
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from ui.theme_style import SEMANTIC_OK, SEMANTIC_BAD


class _SaveIcon(QWidget):
    """状态栏保存图标：三态（saving 转圈 / saved 对勾 / error 感叹号）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._state = "saved"
        self._accent = QColor("#C19860")
        self._ok = QColor(SEMANTIC_OK)
        self._bad = QColor(SEMANTIC_BAD)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str):
        if state == self._state:
            return
        self._state = state
        if state == "saving":
            self._angle = 0
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def set_colors(self, accent: str):
        self._accent = QColor(accent)
        self.update()

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        if self._state == "saving":
            pen = QPen(self._accent, 1.6)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            rect = QRectF(cx - 5, cy - 5, 10, 10)
            p.drawArc(rect, -self._angle * 16, 280 * 16)
        elif self._state == "saved":
            pen = QPen(self._ok, 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(cx - 4.0, cy + 0.5),
                       QPointF(cx - 1.0, cy + 3.5))
            p.drawLine(QPointF(cx - 1.0, cy + 3.5),
                       QPointF(cx + 4.5, cy - 3.5))
        elif self._state == "error":
            p.setPen(Qt.NoPen)
            p.setBrush(self._bad)
            path = QPainterPath()
            path.moveTo(cx, 1.5)
            path.lineTo(w - 1.5, h - 1.5)
            path.lineTo(1.5, h - 1.5)
            path.closeSubpath()
            p.drawPath(path)
            p.setPen(QPen(QColor("#ffffff"), 1.2))
            p.drawLine(QPointF(cx, 5.5), QPointF(cx, 9.5))
            p.drawPoint(QPointF(cx, 11.5))
        p.end()
