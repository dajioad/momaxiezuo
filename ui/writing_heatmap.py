# ui/writing_heatmap.py
"""墨码写作 - 写作日历热力图（GitHub 贡献图风格）

范围：往前 12 个自然月 + 本月到今天的格子（约 55 列 × 7 行）。
- 每格 = 一天，颜色深浅 = 当天字数（按每日目标的 25% / 50% / 75% / 100% 分四档）
- 鼠标悬停显示日期 + 字数
- 顶部按"每月 1 号所在列"标月份，1 月显示年份
- 底部图例：少 → 多
- **格子大小自适应窗口宽度**：窄窗口下自动缩小，保证整幅图完整显示

自绘 QWidget，不依赖第三方库。
"""

from datetime import date, timedelta

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QFont, QPainterPath, QPen


class WritingHeatmap(QWidget):
    """写作日历热力图。"""

    # 默认/最大格子尺寸；实际使用时会根据宽度自适应缩小
    DEFAULT_CELL = 13
    MIN_CELL = 6
    DEFAULT_GAP = 3
    MIN_GAP = 2
    LEFT_PAD = 36  # 左侧周几标签宽度
    TOP_PAD = 22  # 顶部月份标签高度
    BOTTOM_PAD = 26  # 底部图例高度

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}  # {"2026-01-01": 字数, ...}
        self._theme = {}
        self._target = 4000
        self._hover = None
        self.setMouseTracking(True)
        self.setMinimumWidth(300)
        self.setMinimumHeight(
            self.TOP_PAD + 7 * (self.DEFAULT_CELL + self.DEFAULT_GAP) + self.BOTTOM_PAD
        )

    def set_data(self, days_data: dict, target: int = 4000, theme: dict = None):
        """days_data: {"YYYY-MM-DD": 字数}"""
        self._data = dict(days_data or {})
        try:
            self._target = max(1, int(target or 4000))
        except (TypeError, ValueError):
            self._target = 4000
        if theme is not None:
            self._theme = theme
        self.update()

    def get_required_days(self) -> int:
        """返回热力图需要的数据天数（从网格起点到今天的总天数）。"""
        return (date.today() - self._start_date()).days + 1

    # ---------- 内部 ----------
    def _level(self, words: int) -> int:
        if words <= 0:
            return 0
        ratio = words / self._target
        if ratio < 0.25:
            return 1
        if ratio < 0.5:
            return 2
        if ratio < 0.75:
            return 3
        return 4

    def _level_colors(self):
        t = self._theme or {}
        accent = t.get("accent", "#b08a4e")
        muted = t.get("muted", "#9a8e76")
        empty = QColor(muted)
        empty.setAlpha(40)
        levels = [empty]
        for alpha in (70, 120, 175, 235):
            c = QColor(accent)
            c.setAlpha(alpha)
            levels.append(c)
        return levels

    def _start_date(self) -> date:
        """网格起点：往前推 12 个自然月的 1 号，对齐到该周周一。

        举例：今天 2026-09-16 → 起点月 = 2025-09-01 → 对齐周一 = 2025-09-01
        """
        today = date.today()
        y, m = today.year, today.month - 12
        while m <= 0:
            m += 12
            y -= 1
        first = date(y, m, 1)
        return first - timedelta(days=first.weekday())

    def _layout(self):
        """根据当前窗口宽度，计算 (cell, gap, col_step, row_step)。

        目标：55 列在可用宽度内完整放下。
        - 可用宽度 = width() - LEFT_PAD - 右padding(8)
        - 每列步长 = 可用宽度 / 列数
        - gap 先定 3（或窄时降到 2），cell = 步长 - gap
        - cell 限制在 [MIN_CELL, DEFAULT_CELL]
        """
        days_total = self.get_required_days()
        n_cols = max(1, (days_total + 6) // 7)
        avail = max(100, self.width() - self.LEFT_PAD - 8)
        step = avail / n_cols

        gap = self.DEFAULT_GAP if step >= 11 else self.MIN_GAP
        cell = int(step - gap)
        cell = max(self.MIN_CELL, min(self.DEFAULT_CELL, cell))
        col_step = cell + gap
        row_step = cell + gap
        return cell, gap, col_step, row_step

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        t = self._theme or {}
        text_c = QColor(t.get("text", "#3b342a"))
        muted_c = QColor(t.get("muted", "#9a8e76"))
        panel_bg = QColor(t.get("editor_bg", "#fcfaf4"))
        accent = QColor(t.get("accent", "#b08a4e"))
        p.fillRect(self.rect(), panel_bg)

        today = date.today()
        start = self._start_date()
        cell, gap, col_step, row_step = self._layout()

        # 字体大小跟着 cell 走：cell 越小字越小
        label_font_size = 8 if cell >= 9 else 7

        # 周几标签
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        p.setFont(QFont(self.font().family(), label_font_size))
        p.setPen(muted_c)
        for i, name in enumerate(weekday_names):
            y = self.TOP_PAD + i * row_step
            p.drawText(
                QRectF(0, y, self.LEFT_PAD - 8, cell),
                Qt.AlignRight | Qt.AlignVCenter,
                name,
            )

        days_total = (today - start).days + 1

        # 月份标签：按"每月 1 号所在列"定位
        month_positions = {}
        for i in range(days_total):
            d = start + timedelta(days=i)
            if d.day == 1:
                col = i // 7
                key = (d.year, d.month)
                if key not in month_positions:
                    month_positions[key] = col

        last_x = -1000
        p.setFont(QFont(self.font().family(), label_font_size))
        min_gap_px = 26 if cell >= 9 else 22
        for (y, m), col in sorted(month_positions.items(), key=lambda x: x[1]):
            x = self.LEFT_PAD + col * col_step
            if x - last_x < min_gap_px:
                continue
            last_x = x
            label = f"{y}年" if m == 1 else f"{m}月"
            p.setPen(muted_c)
            p.drawText(QRectF(x - 4, 2, 50, 16), Qt.AlignLeft | Qt.AlignVCenter, label)

        # 格子
        levels = self._level_colors()
        radius = max(1.5, cell / 5.0)
        for i in range(days_total):
            d = start + timedelta(days=i)
            col = i // 7
            row = i % 7
            x = self.LEFT_PAD + col * col_step
            y = self.TOP_PAD + row * row_step

            key = d.strftime("%Y-%m-%d")
            words = self._data.get(key, 0)
            lv = self._level(words)

            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y, cell, cell), radius, radius)
            p.fillPath(path, levels[lv])

            if self._hover == key:
                p.setPen(QPen(accent, 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)

        # 图例
        legend_y = self.TOP_PAD + 7 * row_step + 6
        legend_x = self.LEFT_PAD
        p.setPen(muted_c)
        p.setFont(QFont(self.font().family(), label_font_size))
        p.drawText(
            QRectF(legend_x, legend_y, 30, cell), Qt.AlignLeft | Qt.AlignVCenter, "少"
        )
        for i, c in enumerate(levels):
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(legend_x + 28 + i * (cell + 2), legend_y, cell, cell),
                radius,
                radius,
            )
            p.fillPath(path, c)
        p.setPen(muted_c)
        p.drawText(
            QRectF(legend_x + 28 + 5 * (cell + 2) + 4, legend_y, 30, cell),
            Qt.AlignLeft | Qt.AlignVCenter,
            "多",
        )

        p.end()

    # ---------- 鼠标 ----------
    def _cell_at(self, pos) -> str:
        x, y = pos.x(), pos.y()
        if x < self.LEFT_PAD or y < self.TOP_PAD:
            return None
        cell, gap, col_step, row_step = self._layout()
        col = int((x - self.LEFT_PAD) // col_step)
        row = int((y - self.TOP_PAD) // row_step)
        if row < 0 or row >= 7 or col < 0:
            return None
        today = date.today()
        start = self._start_date()
        d = start + timedelta(days=col * 7 + row)
        if d > today:
            return None
        return d.strftime("%Y-%m-%d")

    def mouseMoveEvent(self, event):
        pt = event.position().toPoint() if hasattr(event, "position") else event.pos()
        key = self._cell_at(pt)
        if key != self._hover:
            self._hover = key
            self.update()
            if key:
                words = self._data.get(key, 0)
                self.setToolTip(f"{key}  ·  {words:,} 字")
            else:
                self.setToolTip("")

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
