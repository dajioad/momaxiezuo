# ui/writing_stats_dialog.py
"""墨码写作 - 码字统计面板

展示：
- 四个数字卡片：今日字数 / 连续天数 / 累计字数 / 写作时速
- 中部两个 tab：
  · 全年热力图（默认）——365 天格子，颜色按每日目标分级
  · 30 天趋势——柱状图
- 底部：今日目标进度 + 近 7 天 / 本月汇总

数据全部来自 WritingStats，不写盘、不改状态。
"""

from datetime import date, timedelta
from typing import List, Tuple

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QWidget,
    QPushButton,
    QProgressBar,
    QGridLayout,
    QTabWidget,
)
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPainterPath,
    QFont,
    QPen,
)
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from ui.writing_heatmap import WritingHeatmap

# ============================================================
# 柱状图：30 天
# ============================================================


class _BarChart(QWidget):
    """最近 N 天码字柱状图，自绘。"""

    def __init__(self, data: List[Tuple[str, int]], theme: dict, parent=None):
        super().__init__(parent)
        self._data = list(data)
        self._theme = theme or {}
        self.setMinimumHeight(220)
        self._hover_idx = -1
        self.setMouseTracking(True)

    def set_data(self, data, theme=None):
        self._data = list(data)
        if theme is not None:
            self._theme = theme
        self.update()

    def mouseMoveEvent(self, event):
        idx = self._idx_at(
            event.position().x() if hasattr(event, "position") else event.pos().x()
        )
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()

    def _idx_at(self, x: float) -> int:
        n = len(self._data)
        if n == 0:
            return -1
        pad_l, pad_r = 44, 16
        w = self.width() - pad_l - pad_r
        if w <= 0 or x < pad_l or x > self.width() - pad_r:
            return -1
        step = w / n
        idx = int((x - pad_l) / step)
        return idx if 0 <= idx < n else -1

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        t = self._theme
        try:
            is_light = QColor(t.get("bg", "#f7f2e8")).lightness() > 150
        except Exception:
            is_light = True

        accent = QColor(t.get("accent", "#b08a4e"))
        text_c = QColor(t.get("text", "#3b342a"))
        muted_c = QColor(t.get("muted", "#9a8e76"))
        panel_bg = QColor(t.get("editor_bg", "#fcfaf4"))

        p.fillRect(self.rect(), panel_bg)

        data = self._data
        if not data:
            p.setPen(muted_c)
            p.setFont(QFont(self.font().family(), 11))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            p.end()
            return

        n = len(data)
        pad_l, pad_r, pad_t, pad_b = 44, 16, 22, 32
        cw = self.width() - pad_l - pad_r
        ch = self.height() - pad_t - pad_b
        if cw <= 0 or ch <= 0:
            p.end()
            return

        max_v = max((v for _, v in data), default=0)
        if max_v <= 0:
            max_v = 1
        nice_step = self._nice_step(max_v)
        y_max = ((max_v + nice_step - 1) // nice_step) * nice_step or 1

        p.setFont(QFont(self.font().family(), 9))
        grid_color = QColor(muted_c)
        grid_color.setAlpha(0x22)
        grid_count = 4
        for i in range(grid_count + 1):
            y = pad_t + ch * i / grid_count
            val = int(y_max * (1 - i / grid_count))
            p.setPen(QPen(grid_color, 1))
            p.drawLine(pad_l, int(y), pad_l + cw, int(y))
            p.setPen(muted_c)
            p.drawText(
                QRectF(0, y - 8, pad_l - 6, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                self._fmt_short(val),
            )

        step = cw / n
        bar_w = max(3.0, min(20.0, step * 0.62))
        today_key = date.today().strftime("%Y-%m-%d")

        for i, (dk, v) in enumerate(data):
            cx = pad_l + step * (i + 0.5)
            bh = (v / y_max) * ch if y_max > 0 else 0
            x = cx - bar_w / 2
            y = pad_t + ch - bh
            r = QRectF(x, y, bar_w, max(bh, 1))

            if dk == today_key:
                fill = accent
            elif v == 0:
                fill = QColor(muted_c)
                fill.setAlpha(0x33)
            else:
                ratio = v / max_v
                fill = QColor(accent)
                fill.setAlpha(int(120 + 100 * ratio))
            if i == self._hover_idx and v > 0:
                fill = QColor(accent).lighter(115)

            path = QPainterPath()
            radius = min(bar_w / 2, 4.0)
            path.addRoundedRect(r, radius, radius)
            p.fillPath(path, fill)

        if 0 <= self._hover_idx < n:
            dk, v = data[self._hover_idx]
            cx = pad_l + step * (self._hover_idx + 0.5)
            text = f"{dk[5:]}   {v:,} 字"
            p.setFont(QFont(self.font().family(), 10, QFont.Bold))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(text) + 16
            th = 26
            tx = max(pad_l, min(cx - tw / 2, self.width() - pad_r - tw))
            ty = pad_t + 4
            tip_rect = QRectF(tx, ty, tw, th)
            tip_bg = QColor(t.get("sidebar", "#efe8d8"))
            tip_path = QPainterPath()
            tip_path.addRoundedRect(tip_rect, 6, 6)
            p.fillPath(tip_path, tip_bg)
            tip_border = QColor(accent)
            tip_border.setAlpha(0x66)
            p.setPen(QPen(tip_border, 1))
            p.drawPath(tip_path)
            p.setPen(text_c)
            p.drawText(tip_rect, Qt.AlignCenter, text)

        p.setPen(muted_c)
        p.setFont(QFont(self.font().family(), 9))
        label_step = max(1, n // 6)
        for i, (dk, _) in enumerate(data):
            if i % label_step != 0 and i != n - 1:
                continue
            cx = pad_l + step * (i + 0.5)
            p.drawText(QRectF(cx - 30, pad_t + ch + 6, 60, 18), Qt.AlignCenter, dk[5:])

        p.end()

    @staticmethod
    def _nice_step(max_v: int) -> int:
        if max_v <= 500:
            return 100
        if max_v <= 1000:
            return 200
        if max_v <= 3000:
            return 500
        if max_v <= 5000:
            return 1000
        return 2000

    @staticmethod
    def _fmt_short(v: int) -> str:
        if v >= 10000:
            return f"{v / 10000:.1f}w"
        if v >= 1000:
            return f"{v / 1000:.1f}k"
        return str(v)


# ============================================================
# 单个数字卡片
# ============================================================


class _StatCard(QFrame):
    def __init__(
        self,
        icon_name: str,
        label: str,
        value: str,
        sub: str = "",
        theme: dict = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("statCard")
        self._theme = theme or {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.icon_lab = QLabel()
        head.addWidget(self.icon_lab)
        self.name_lab = QLabel(label)
        self.name_lab.setObjectName("statName")
        head.addWidget(self.name_lab)
        head.addStretch()
        lay.addLayout(head)

        self.value_lab = QLabel(value)
        self.value_lab.setObjectName("statValue")
        lay.addWidget(self.value_lab)

        self.sub_lab = QLabel(sub)
        self.sub_lab.setObjectName("statSub")
        self.sub_lab.setVisible(bool(sub))
        lay.addWidget(self.sub_lab)

        self._set_icon(icon_name)
        self._apply_theme()

    def _set_icon(self, name: str):
        accent = self._theme.get("accent", "#b08a4e")
        self.icon_lab.setPixmap(qta.icon(name, color=accent).pixmap(18, 18))

    def _apply_theme(self):
        t = self._theme
        if not t:
            return
        accent = t.get("accent", "#b08a4e")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        card_bg = t.get("editor_bg", "#fcfaf4")
        self.setStyleSheet(
            f"QFrame#statCard {{ background-color: {card_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 12px; }}"
            f"QFrame#statCard:hover {{ border-color: {qss_alpha(accent, 0x66)}; }}"
        )
        self.name_lab.setStyleSheet(
            f"color: {muted}; font-size: 12px; background: transparent;"
        )
        self.value_lab.setStyleSheet(
            f"color: {text}; font-size: 26px; font-weight: 700;"
            f" background: transparent;"
        )
        self.sub_lab.setStyleSheet(
            f"color: {muted}; font-size: 14px; background: transparent;"
        )

    def update_value(self, value: str, sub: str = ""):
        self.value_lab.setText(value)
        self.sub_lab.setText(sub)
        self.sub_lab.setVisible(bool(sub))


# ============================================================
# 主对话框
# ============================================================


class WritingStatsDialog(QDialog):
    """码字统计面板（非模态）"""

    def __init__(self, stats, workspace=None, parent=None):
        super().__init__(parent)
        self.stats = stats
        self.ws = workspace
        self._theme = self._resolve_theme()
        self.setWindowTitle("码字统计")
        # tab 区可压缩（热力图 min 160、柱图 min 220），压到 600 更稳。
        self.setMinimumSize(820, 600)
        self.setModal(False)
        self._build_ui()
        # 首次 refresh 延后到事件循环下一帧：窗口先显示，
        # 用户感觉瞬时响应；数据渲染随后完成。
        QTimer.singleShot(0, self.refresh)

    def _resolve_theme(self) -> dict:
        # 优先用 self.ws：用户打开对话框时显式传入的 workspace，
        # 比沿 parent 链找更可靠。找不到再退回统一入口。
        editor = getattr(self.ws, "editor", None) if self.ws else None
        if editor is not None and hasattr(editor, "_theme"):
            return editor._theme or {}
        from ui.theme_style import _resolve_dialog_theme
        return _resolve_dialog_theme(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)

        # ---- 顶部：标题 + 刷新 ----
        head = QHBoxLayout()
        title = QLabel("码字统计")
        title.setObjectName("statsTitle")
        head.addWidget(title)
        head.addStretch()
        self.refresh_btn = QPushButton(" 刷新")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        # ---- 四个卡片 ----
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self.card_today = _StatCard("fa5s.pen-fancy", "今日", "0", theme=self._theme)
        self.card_streak = _StatCard("fa5s.fire", "连续", "0 天", theme=self._theme)
        self.card_total = _StatCard("fa5s.book", "累计", "0", theme=self._theme)
        self.card_speed = _StatCard("fa5s.bolt", "时速", "0", theme=self._theme)
        for c in (self.card_today, self.card_streak, self.card_total, self.card_speed):
            cards_row.addWidget(c, 1)
        root.addLayout(cards_row)

        # ---- 中部：两个 tab ----
        self.chart_tabs = QTabWidget()
        self.chart_tabs.setDocumentMode(True)

        heatmap_page = QWidget()
        hl = QVBoxLayout(heatmap_page)
        hl.setContentsMargins(0, 8, 0, 0)
        self.heatmap = WritingHeatmap()
        hl.addWidget(self.heatmap)
        hl.addStretch()
        self.chart_tabs.addTab(heatmap_page, "全年热力图")

        bar_page = QWidget()
        bl = QVBoxLayout(bar_page)
        bl.setContentsMargins(0, 8, 0, 0)
        self.chart = _BarChart([], self._theme)
        bl.addWidget(self.chart)
        bl.addStretch()
        self.chart_tabs.addTab(bar_page, "30 天趋势")

        root.addWidget(self.chart_tabs, 1)

        # ---- 底部：目标进度 + 附加统计 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        goal_box = QFrame()
        goal_box.setObjectName("goalBox")
        gl = QVBoxLayout(goal_box)
        gl.setContentsMargins(14, 12, 14, 12)
        gl.setSpacing(6)

        goal_head = QHBoxLayout()
        self.goal_title = QLabel("今日目标")
        self.goal_title.setObjectName("goalTitle")
        goal_head.addWidget(self.goal_title)
        goal_head.addStretch()
        self.goal_value = QLabel("")
        self.goal_value.setObjectName("goalValue")
        goal_head.addWidget(self.goal_value)
        gl.addLayout(goal_head)

        self.goal_bar = QProgressBar()
        self.goal_bar.setFixedHeight(10)
        self.goal_bar.setTextVisible(False)
        self.goal_bar.setRange(0, 100)
        gl.addWidget(self.goal_bar)

        self.goal_hint = QLabel("")
        self.goal_hint.setObjectName("goalHint")
        gl.addWidget(self.goal_hint)
        bottom.addWidget(goal_box, 2)

        extra_box = QFrame()
        extra_box.setObjectName("goalBox")
        el = QGridLayout(extra_box)
        el.setContentsMargins(14, 12, 14, 12)
        el.setHorizontalSpacing(20)
        el.setVerticalSpacing(8)

        el.addWidget(self._mini_label("近 7 天"), 0, 0)
        self.mini_week = QLabel("0")
        self.mini_week.setObjectName("miniValue")
        el.addWidget(self.mini_week, 0, 1)

        el.addWidget(self._mini_label("本月"), 0, 2)
        self.mini_month = QLabel("0")
        self.mini_month.setObjectName("miniValue")
        el.addWidget(self.mini_month, 0, 3)

        el.addWidget(self._mini_label("今日活跃"), 1, 0)
        self.mini_minutes = QLabel("0 分钟")
        self.mini_minutes.setObjectName("miniValue")
        el.addWidget(self.mini_minutes, 1, 1)

        el.addWidget(self._mini_label("日均"), 1, 2)
        self.mini_avg = QLabel("0")
        self.mini_avg.setObjectName("miniValue")
        el.addWidget(self.mini_avg, 1, 3)

        bottom.addWidget(extra_box, 3)
        root.addLayout(bottom)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

        self._apply_theme()

    def _mini_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("miniLabel")
        return lab

    def _apply_theme(self):
        t = self._theme
        if not t:
            return
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"WritingStatsDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#statsTitle {{ font-size: 18px; font-weight: 700; }}"
            f"QFrame#goalBox {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 12px; }}"
            f"QLabel#goalTitle {{ font-size: 13px; font-weight: 600; color: {text}; }}"
            f"QLabel#goalValue {{ font-size: 13px; font-weight: 700; color: {accent}; }}"
            f"QLabel#goalHint {{ font-size: 14px; color: {muted}; }}"
            f"QLabel#miniLabel {{ font-size: 12px; color: {muted}; }}"
            f"QLabel#miniValue {{ font-size: 15px; font-weight: 700; color: {text}; }}"
            f"QProgressBar {{ background: {qss_alpha(muted, 0x22)};"
            f" border: none; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}"
            f"QTabWidget::pane {{ border: none; }}"
            f"QTabBar::tab {{ background: transparent; color: {muted};"
            f" padding: 6px 16px; border: none;"
            f" border-bottom: 2px solid transparent; }}"
            f"QTabBar::tab:selected {{ color: {text};"
            f" border-bottom: 2px solid {accent}; font-weight: bold; }}"
            f"QTabBar::tab:hover {{ color: {accent}; }}"
        )

        btn_ss = accent_button_qss(
            accent, accent_fg, accent_hover, selector="QPushButton", padding="6px 14px"
        )
        self.refresh_btn.setStyleSheet(btn_ss)
        self.refresh_btn.setIcon(qta.icon("fa5s.sync-alt", color=accent_fg))

    # =================== 数据刷新 ===================

    def refresh(self):
        if self.stats is None:
            return
        try:
            today = self.stats.today_words()
            streak = self.stats.streak_days()
            total = self.stats.total_words()
            speed = self.stats.writing_speed()
            minutes = self.stats.today_minutes()
            last30 = self.stats.last_n_days_words(30)
            # 热力图需要的天数由 heatmap 自己算（往前 12 个自然月），
            # 保证起点是"去年同月的 1 号"而不是"去年同日的 1 号"。
            days_needed = self.heatmap.get_required_days()
            last_full = self.stats.last_n_days_words(days_needed)
        except Exception:
            return

        target = self._get_daily_target()

        # 卡片
        self.card_today.update_value(f"{today:,}", "字")
        if streak > 0:
            self.card_streak.update_value(f"{streak}", "天")
        else:
            self.card_streak.update_value("0", "今天未写")
        self.card_total.update_value(self._fmt_big(total), "字")
        self.card_speed.update_value(f"{speed:,}" if speed else "—", "字/时")

        # 柱状图
        self.chart.set_data(last30, self._theme)

        # 热力图
        self.heatmap.set_data(dict(last_full), target=target, theme=self._theme)

        # 目标进度
        if target > 0:
            pct = min(100, int(today / target * 100)) if target else 0
            self.goal_bar.setValue(pct)
            remain = max(0, target - today)
            if today >= target:
                self.goal_value.setText(f"{today:,} / {target:,}  ✓ 已达成")
                self.goal_hint.setText("今天的目标已经完成，继续保持！")
            else:
                self.goal_value.setText(f"{today:,} / {target:,}")
                self.goal_hint.setText(
                    f"还差 {remain:,} 字，约 {self._estimate_minutes(remain, minutes)} 分钟"
                )
        else:
            self.goal_bar.setValue(0)
            self.goal_value.setText(f"{today:,} 字")
            self.goal_hint.setText("未设置每日目标（设置 → 写作偏好）")

        # 附加统计
        week = sum(v for _, v in last30[-7:])
        month_prefix = date.today().strftime("%Y-%m")
        month = sum(v for k, v in last30 if k.startswith(month_prefix))
        self.mini_week.setText(f"{week:,} 字")
        self.mini_month.setText(f"{month:,} 字")
        self.mini_minutes.setText(f"{minutes} 分钟" if minutes else "—")
        avg = int(week / 7) if week else 0
        self.mini_avg.setText(f"{avg:,} 字")

    def _get_daily_target(self) -> int:
        if self.ws and getattr(self.ws, "work", None):
            try:
                t = int(self.ws.work.meta.get("daily_target", 0) or 0)
                if t > 0:
                    return t
            except (TypeError, ValueError):
                pass
        cfg = getattr(self.ws, "cfg", None) if self.ws else None
        if cfg is not None:
            try:
                return int(cfg.get_config("Writing", "daily_target", "4000") or 4000)
            except (TypeError, ValueError):
                pass
        return 0

    @staticmethod
    def _estimate_minutes(remain: int, today_minutes: int) -> int:
        if today_minutes <= 0:
            return max(1, int(remain / 25))
        return max(1, int(remain / max(1, today_minutes)))

    @staticmethod
    def _fmt_big(n: int) -> str:
        if n >= 100000000:
            return f"{n / 100000000:.2f}亿"
        if n >= 10000:
            return f"{n / 10000:.2f}万"
        return f"{n:,}"
