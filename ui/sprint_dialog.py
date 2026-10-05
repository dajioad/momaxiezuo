# ui/sprint_dialog.py
"""墨码写作 - 冲刺计划对话框"""

from datetime import date, datetime

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSpinBox,
    QDateEdit,
    QMessageBox,
    QProgressBar,
)
from PySide6.QtCore import Qt, QDate

from ui.theme_style import qss_alpha, accent_button_qss
from utils.sprint import (
    SprintConfig,
    load_sprint,
    save_sprint,
    clear_sprint,
    update_today_snapshot,
    compute,
)


class SprintDialog(QDialog):
    """冲刺计划面板。"""

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work
        self._theme = self._resolve_theme()
        self.setWindowTitle(f"冲刺计划 - {work.title}")
        self.setMinimumSize(560, 560)
        self._build_ui()
        self._apply_theme()
        self._load()

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
                break
            p = p.parent()
        return {}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(14)

        # ---- 设置区 ----
        set_box = QFrame()
        set_box.setObjectName("setBox")
        sl = QVBoxLayout(set_box)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(10)

        title = QLabel("目标设置")
        title.setObjectName("spTitle")
        sl.addWidget(title)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("目标总字数"))
        self.target_spin = QSpinBox()
        self.target_spin.setRange(0, 10_000_000)
        self.target_spin.setSingleStep(10000)
        self.target_spin.setSuffix(" 字")
        self.target_spin.setSpecialValueText("未设置")
        self.target_spin.setMinimumWidth(160)
        row1.addWidget(self.target_spin, 1)
        row1.addSpacing(12)
        row1.addWidget(QLabel("截止日期"))
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_date.setMinimumWidth(140)
        row1.addWidget(self.end_date)
        row1.addStretch()
        sl.addLayout(row1)

        hint = QLabel(
            "设定后，每天打开此对话框都会自动记录当前字数。\n"
            "达到目标或过期后可重新设置新的冲刺。"
        )
        hint.setObjectName("spHint")
        hint.setWordWrap(True)
        sl.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.save_btn = QPushButton("保存目标")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        self.clear_btn = QPushButton("清除目标")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(self.clear_btn)
        sl.addLayout(btn_row)
        root.addWidget(set_box)

        # ---- 进度区 ----
        self.progress_box = QFrame()
        self.progress_box.setObjectName("progBox")
        pl = QVBoxLayout(self.progress_box)
        pl.setContentsMargins(20, 18, 20, 18)
        pl.setSpacing(12)

        # 大数字
        num_row = QHBoxLayout()
        num_row.addStretch()
        self.big_label = QLabel("")
        self.big_label.setObjectName("spBigNum")
        self.big_label.setAlignment(Qt.AlignCenter)
        num_row.addWidget(self.big_label)
        num_row.addStretch()
        pl.addLayout(num_row)

        # 进度条
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        pl.addWidget(self.bar)

        # 关键数字网格：三张等宽卡片
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_remain = self._make_stat("剩余", "")
        self.stat_days = self._make_stat("剩余天数", "")
        self.stat_daily = self._make_stat("每日必达", "")
        for w in (self.stat_remain, self.stat_days, self.stat_daily):
            stats_row.addWidget(w, 1)
        pl.addLayout(stats_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("spStatus")
        self.status_label.setWordWrap(True)
        pl.addWidget(self.status_label)

        root.addWidget(self.progress_box, 1)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _make_stat(self, label: str, value: str) -> QFrame:
        """统计卡片：淡边框 + 圆角 + 悬停高亮。"""
        box = QFrame()
        box.setObjectName("statBox")
        box.setAttribute(Qt.WA_Hover, True)  # 启用 hover 状态
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        title = QLabel(label)
        title.setObjectName("statLabel")
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title)
        value_lab = QLabel(value)
        value_lab.setObjectName("statValue")
        value_lab.setAlignment(Qt.AlignCenter)
        v.addWidget(value_lab)
        box._value_label = value_lab  # 便于后续更新
        return box

    # ---------- 数据 ----------
    def _load(self):
        cfg = load_sprint(self._work)

        self.target_spin.setValue(cfg.target_words)
        if cfg.end_date:
            d = datetime.strptime(cfg.end_date, "%Y-%m-%d").date()
            self.end_date.setDate(QDate(d.year, d.month, d.day))
        else:
            self.end_date.setDate(QDate.currentDate().addDays(30))

        update_today_snapshot(self._work, cfg)
        result = compute(self._work, cfg)
        self._render_progress(result)

    def _save(self):
        target = self.target_spin.value()
        if target <= 0:
            QMessageBox.information(self, "提示", "目标字数必须大于 0。")
            return
        end = self.end_date.date()
        end_d = date(end.year(), end.month(), end.day())
        if end_d <= date.today():
            QMessageBox.information(
                self, "提示", "截止日期必须晚于今天。要结束冲刺请点「清除目标」。"
            )
            return

        cfg = load_sprint(self._work)
        new_start = date.today().strftime("%Y-%m-%d")
        new_end = end_d.strftime("%Y-%m-%d")
        if cfg.target_words != target or cfg.end_date != new_end or not cfg.start_date:
            cfg = SprintConfig(
                target_words=target,
                start_date=new_start,
                end_date=new_end,
                start_words=int(self._work.total_words()),
                snapshots={},
            )
        else:
            cfg.target_words = target
            cfg.end_date = new_end
        save_sprint(self._work, cfg)
        self._load()

    def _clear(self):
        if (
            QMessageBox.question(
                self, "清除冲刺", "确定清除当前冲刺目标吗？\n历史快照一并删除。"
            )
            != QMessageBox.Yes
        ):
            return
        clear_sprint(self._work)
        self._load()

    # ---------- 渲染 ----------
    def _render_progress(self, result: dict):
        if not result.get("active"):
            self.big_label.setText(f"当前总字数 {result.get('current', 0):,}")
            self.bar.setValue(0)
            self._set_stat(self.stat_remain, "—")
            self._set_stat(self.stat_days, "—")
            self._set_stat(self.stat_daily, "—")
            self.status_label.setText("请先设置目标字数和截止日期。")
            self.status_label.setProperty("level", "muted")
            self._restyle_status()
            return

        cur = result["current"]
        tgt = result["target"]
        self.big_label.setText(f"{cur:,} / {tgt:,}")
        self.bar.setValue(result["percent"])
        self._set_stat(self.stat_remain, f"{result['remain_words']:,} 字")
        self._set_stat(self.stat_days, f"{result['days_left']} 天")
        daily = result["daily_required"]
        self._set_stat(self.stat_daily, f"{daily:,} 字" if daily else "✓ 已达成")

        diff = result["diff"]
        status = result["status"]
        if status == "ahead":
            self.status_label.setText(f"领先理想进度 {diff:,} 字，保持这个节奏！")
            self.status_label.setProperty("level", "ahead")
        elif status == "behind":
            self.status_label.setText(
                f"落后理想进度 {-diff:,} 字，每天多写 "
                f"{-diff // max(1, result['days_left']):,} 字可追上"
            )
            self.status_label.setProperty("level", "behind")
        else:
            self.status_label.setText(f"与理想进度基本一致（差距 {abs(diff):,} 字）")
            self.status_label.setProperty("level", "on_track")
        self._restyle_status()

    def _set_stat(self, box: QFrame, text: str):
        box._value_label.setText(text)

    def _restyle_status(self):
        t = self._theme or {}
        muted = t.get("muted", "#9a8e76")
        level = self.status_label.property("level") or "muted"
        color = {
            "ahead": "#4CAF50",
            "behind": "#F59E0B",
            "on_track": "#4CAF50",
            "muted": muted,
        }.get(level, muted)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 500; padding: 8px 0;"
        )

    # ---------- 主题 ----------
    def _apply_theme(self):
        t = self._theme or {
            "bg": "#f7f2e8",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "accent": "#b08a4e",
        }
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"SprintDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            # 标题
            f"QLabel#spTitle {{ color: {accent}; font-size: 14px; font-weight: bold; }}"
            f"QLabel#spHint {{ color: {muted}; font-size: 12px; }}"
            # 大数字：主题金色 + 字距
            f"QLabel#spBigNum {{ color: {accent}; font-size: 34px;"
            f" font-weight: 700; letter-spacing: 1px; }}"
            # 卡片容器
            f"QFrame#setBox, QFrame#progBox {{"
            f"  background-color: {editor_bg};"
            f"  border: 1px solid {qss_alpha(muted, 0x25)};"
            f"  border-radius: 12px; }}"
            # 统计卡片
            f"QFrame#statBox {{"
            f"  background-color: {qss_alpha(editor_bg, 0x80)};"
            f"  border: 1px solid {qss_alpha(muted, 0x22)};"
            f"  border-radius: 10px; }}"
            f"QFrame#statBox:hover {{"
            f"  border: 1px solid {qss_alpha(accent, 0x88)};"
            f"  background-color: {qss_alpha(accent, 0x08)}; }}"
            f"QLabel#statLabel {{ color: {muted}; font-size: 11px;"
            f" font-weight: 500; letter-spacing: 1px; }}"
            f"QLabel#statValue {{ color: {text}; font-size: 16px;"
            f" font-weight: 700; }}"
            # 进度条：金色渐变 + 圆角 + 阴影
            f"QProgressBar {{"
            f"  background: {qss_alpha(muted, 0x22)};"
            f"  border: none; border-radius: 7px; }}"
            f"QProgressBar::chunk {{"
            f"  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f"    stop:0 {accent}, stop:1 {accent_hover});"
            f"  border-radius: 7px; }}"
            # 输入框：统一高度和圆角
            f"QDateEdit, QSpinBox {{"
            f"  background-color: {editor_bg}; color: {text};"
            f"  border: 1px solid {qss_alpha(muted, 0x55)};"
            f"  border-radius: 6px; padding: 6px 10px; min-height: 20px; }}"
            f"QDateEdit:focus, QSpinBox:focus {{"
            f"  border: 1px solid {accent}; }}"
        )

        # 主按钮样式
        self.save_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                accent_hover,
                selector="QPushButton",
                padding="7px 18px",
                bold=True,
            )
        )
