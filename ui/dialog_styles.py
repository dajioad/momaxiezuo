# ui/dialog_styles.py
"""墨码写作 - 对话框美化样式（单一事实来源）

各对话框在自己的 _apply_theme / _build_ui 里拼接这些 QSS 片段。
改一处风格，全局统一。

设计语言：
- 输入框：圆角 8px，卡片色底，hover 边框变强调色，focus 实色边
- 表格：无网格线，行高 44-48px，hover 高亮，表头浅色+加粗+单下划线
- 按钮：圆角 9px 胶囊，主按钮带阴影，次按钮透明+描边
- 卡片：圆角 10px，淡边框
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDialog,
    QPushButton,
    QProgressBar,
    QSizePolicy,
)
from PySide6.QtGui import QFontMetrics

from ui.theme_style import qss_alpha

# 重导出设计令牌，各对话框统一 `from ui.dialog_styles import DLG_MARGIN_H ...`
# 用法：root.setContentsMargins(DLG_MARGIN_H, DLG_MARGIN_V,
#                               DLG_MARGIN_H, DLG_MARGIN_V)
#       root.setSpacing(DLG_SPACING)
from ui.theme_style import (  # noqa: E402,F401
    SPACE_XXS,
    SPACE_XS,
    SPACE_SM,
    SPACE_MD,
    SPACE_LG,
    SPACE_XL,
    DLG_MARGIN_H,
    DLG_MARGIN_V,
    DLG_SPACING,
    RADIUS_SM,
    RADIUS_CTL,
    RADIUS_CARD,
    CONTROL_HEIGHT,
    BUTTON_HEIGHT,
)


def apply_dialog_layout(layout, *, margins=None, spacing=DLG_SPACING):
    """一次性把对话框主布局收敛到令牌刻度。

    margins 为 None 时取标准 (DLG_MARGIN_H, DLG_MARGIN_V)；
    也可传 (l, t, r, b) 或单个 int 四同。
    """
    if margins is None:
        l = r = DLG_MARGIN_H
        t = b = DLG_MARGIN_V
    elif isinstance(margins, int):
        l = r = t = b = margins
    else:
        l, t, r, b = margins
    layout.setContentsMargins(l, t, r, b)
    layout.setSpacing(spacing)


class ElidedLabel(QLabel):
    """自动省略号标签：宽度不够时按模式截断，悬停显示全文。

    替代「setWordWrap(True) + setFixedHeight」导致第二行被切掉的写法，
    也替代全角空格凑宽度。setElideMode: Qt.ElideRight / ElideMiddle。
    """

    def __init__(
        self,
        text: str = "",
        parent=None,
        elide=Qt.ElideRight,
        *,
        show_tooltip: bool = True,
    ):
        super().__init__(parent)
        self._full_text = text
        self._elide = elide
        self._show_tooltip = show_tooltip
        # 水平方向可压缩，不撑开父布局
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWordWrap(False)
        self.setText(text)

    def setText(self, text: str):  # noqa: N802（Qt 命名）
        self._full_text = text or ""
        if self._show_tooltip:
            super().setToolTip(self._full_text)
        self._update_elided()

    def full_text(self) -> str:
        return self._full_text

    def setElideMode(self, mode):  # noqa: N802
        self._elide = mode
        self._update_elided()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self):
        fm = QFontMetrics(self.font())
        w = max(0, self.width())
        elided = fm.elidedText(self._full_text, self._elide, w) if w else ""
        super().setText(elided)


class Card(QFrame):
    """设置/对话框通用卡片：金色竖条标题 + 内容区。

    用法：
        card = Card("陪伴体验")
        card.body().addWidget(SettingRow("空闲漫游", check, "空闲时自己走动、发呆"))
        page.addWidget(card)
    无标题卡片传 title=None，内容直接贴边（自行留内距）。
    """

    def __init__(self, title: str = None, desc: str = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            lay.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setObjectName("cardDesc")
            d.setWordWrap(True)
            lay.addWidget(d)
        self._body = QVBoxLayout()
        self._body.setSpacing(10)
        lay.addLayout(self._body)

    def body(self) -> QVBoxLayout:
        return self._body


class SettingRow(QWidget):
    """设置行：左标题(+说明) 右控件，垂直居中对齐。"""

    def __init__(self, title: str, control: QWidget, desc: str = None,
                 parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        lab = QLabel(title)
        lab.setStyleSheet("font-size: 13px;")
        left.addWidget(lab)
        if desc:
            d = QLabel(desc)
            d.setObjectName("cardDesc")
            d.setWordWrap(True)
            left.addWidget(d)
        row.addLayout(left, 1)
        control.setMaximumHeight(34)
        row.addWidget(control, 0, Qt.AlignVCenter)


class SectionTitle(QLabel):
    """卡片之间的分区小标题（如“隐私与数据”）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("sectionTitle")


def role_label(text: str, role: str) -> QLabel:
    """语义文字：role ∈ ok/warn/bad/info。

    dynamic property 需要重新 polish 才能让 [role="x"] QSS 命中。
    """
    lab = QLabel(text)
    lab.setProperty("role", role)
    lab.style().unpolish(lab)
    lab.style().polish(lab)
    return lab


def make_empty_state(
    icon_name: str, title: str, desc: str, theme: dict = None
) -> QWidget:
    """统一空状态：圆形虚线图标框 + 加粗标题 + 弱化描述。

    icon_name 为 qtawesome 图标名（如 "fa5s.search"），
    不要再传 emoji——emoji 字形随系统版本漂移且不统一。
    """
    import qtawesome as qta

    t = theme or {}
    text = t.get("text", "#3b342a")
    muted = t.get("muted", "#9a8e76")
    w = QWidget()
    w.setObjectName("emptyState")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(20, 28, 20, 28)
    lay.setSpacing(10)
    lay.setAlignment(Qt.AlignCenter)

    icon = QLabel()
    icon.setObjectName("emptyStateIcon")
    icon.setFixedSize(60, 60)
    icon.setAlignment(Qt.AlignCenter)
    try:
        icon.setPixmap(qta.icon(icon_name, color=muted).pixmap(26, 26))
    except Exception:
        icon.setText("")
    lay.addWidget(icon, 0, Qt.AlignHCenter)

    title_lab = QLabel(title)
    title_lab.setObjectName("emptyStateTitle")
    title_lab.setAlignment(Qt.AlignCenter)
    lay.addWidget(title_lab, 0, Qt.AlignHCenter)

    desc_lab = QLabel(desc)
    desc_lab.setObjectName("emptyStateDesc")
    desc_lab.setAlignment(Qt.AlignCenter)
    desc_lab.setWordWrap(True)
    lay.addWidget(desc_lab, 0, Qt.AlignHCenter)

    w.setStyleSheet(
        f"#emptyState {{ background: transparent; }}"
        f"QLabel#emptyStateIcon {{"
        f" color: {muted}; background: transparent;"
        f" border: 2px dashed {qss_alpha(muted, 0x55)};"
        f" border-radius: 30px; }}"
        f"QLabel#emptyStateTitle {{"
        f" color: {text}; background: transparent;"
        f" font-size: 14px; font-weight: bold; }}"
        f"QLabel#emptyStateDesc {{"
        f" color: {muted}; background: transparent; font-size: 12px; }}"
    )
    return w


def input_qss(theme: dict) -> str:
    """输入类控件：QLineEdit / QTextEdit / QComboBox / QSpinBox / QPlainTextEdit。"""
    t = theme or {}
    editor_bg = t.get("editor_bg", "#fcfaf4")
    sidebar = t.get("sidebar", "#efe8d8")
    text = t.get("text", "#3b342a")
    muted = t.get("muted", "#9a8e76")
    accent = t.get("accent", "#b08a4e")
    return (
        f"QPlainTextEdit, QTextEdit {{"
        f" background-color: {sidebar}; color: {text};"
        f" border: 1px solid {qss_alpha(muted, 0x33)};"
        f" border-radius: 10px; padding: 8px 12px;"
        f" selection-background-color: {qss_alpha(accent, 0x55)};"
        f" selection-color: {text}; }}"
        f"QPlainTextEdit:hover, QTextEdit:hover {{"
        f" border-color: {qss_alpha(accent, 0x66)}; }}"
        f"QPlainTextEdit:focus, QTextEdit:focus {{"
        f" background-color: {editor_bg};"
        f" border: 1px solid {accent}; }}"
        f"QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit,"
        f" QDateTimeEdit {{"
        f" background-color: {sidebar}; color: {text};"
        f" border: 1px solid {qss_alpha(muted, 0x33)};"
        f" border-radius: 9px; padding: 5px 12px; min-height: 20px;"
        f" selection-background-color: {qss_alpha(accent, 0x55)};"
        f" selection-color: {text}; }}"
        f"QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{"
        f" border-color: {qss_alpha(accent, 0x66)}; }}"
        f"QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{"
        f" background-color: {editor_bg};"
        f" border: 1px solid {accent}; }}"
        f"QComboBox {{"
        f" background-color: {sidebar}; color: {text};"
        f" border: 1px solid {qss_alpha(muted, 0x33)};"
        f" border-radius: 9px; padding: 5px 12px; min-height: 20px; }}"
        f"QComboBox:hover {{ border-color: {qss_alpha(accent, 0x66)}; }}"
        f"QComboBox:focus {{ background-color: {editor_bg};"
        f" border-color: {accent}; }}"
        f"QComboBox::drop-down {{ border: none; width: 26px; }}"
        f"QComboBox QAbstractItemView {{"
        f" background-color: {editor_bg}; color: {text};"
        f" border: 1px solid {qss_alpha(muted, 0x44)};"
        f" border-radius: 10px; padding: 4px;"
        f" selection-background-color: {qss_alpha(accent, 0x33)};"
        f" selection-color: {text}; outline: none; }}"
    )


def table_qss(theme: dict) -> str:
    """表格：无网格、行高、悬停、表头浅色。"""
    t = theme or {}
    editor_bg = t.get("editor_bg", "#fcfaf4")
    sidebar = t.get("sidebar", "#efe8d8")
    text = t.get("text", "#3b342a")
    muted = t.get("muted", "#9a8e76")
    accent = t.get("accent", "#b08a4e")
    return (
        f"QTableWidget, QTableView {{"
        f" background-color: {editor_bg}; color: {text};"
        f" border: 1px solid {qss_alpha(muted, 0x22)};"
        f" border-radius: 12px;"
        f" alternate-background-color: {qss_alpha(sidebar, 0x55)};"
        f" gridline-color: transparent; outline: none; }}"
        f"QTableWidget::item, QTableView::item {{ padding: 8px 12px; }}"
        f"QTableWidget::item:hover, QTableView::item:hover {{"
        f" background-color: {qss_alpha(accent, 0x14)}; }}"
        f"QTableWidget::item:selected, QTableView::item:selected {{"
        f" background-color: {qss_alpha(accent, 0x28)}; color: {text}; }}"
        f"QHeaderView {{ background: transparent; }}"
        f"QHeaderView::section {{"
        f" background-color: {sidebar}; color: {muted};"
        f" border: none; padding: 8px 10px;"
        f" font-size: 11px; font-weight: 600;"
        f" border-bottom: 1px solid {qss_alpha(muted, 0x22)}; }}"
        f"QTableCornerButton::section {{"
        f" background-color: {sidebar}; border: none; }}"
    )


def button_qss(theme: dict) -> str:
    """次按钮：圆角胶囊 + hover 变强调色。"""
    t = theme or {}
    text = t.get("text", "#3b342a")
    muted = t.get("muted", "#9a8e76")
    accent = t.get("accent", "#b08a4e")
    return (
        f"QPushButton {{"
        f" color: {text}; background: transparent;"
        f" border: 1px solid {qss_alpha(muted, 0x44)};"
        f" border-radius: 9px; padding: 7px 18px; font-size: 13px;"
        f" min-height: 18px; }}"
        f"QPushButton:hover {{"
        f" background: {qss_alpha(accent, 0x1a)};"
        f" border-color: {accent}; color: {accent}; }}"
        f"QPushButton:pressed {{"
        f" background: {qss_alpha(accent, 0x2e)}; }}"
        f"QPushButton:disabled {{"
        f" color: {muted}; border-color: {qss_alpha(muted, 0x22)}; }}"
    )


def list_qss(theme: dict) -> str:
    """列表：圆角 + 悬停 + 选中。"""
    t = theme or {}
    editor_bg = t.get("editor_bg", "#fcfaf4")
    text = t.get("text", "#3b342a")
    muted = t.get("muted", "#9a8e76")
    accent = t.get("accent", "#b08a4e")
    return (
        f"QListWidget {{"
        f" background-color: {editor_bg}; color: {text};"
        f" border: 1px solid {qss_alpha(muted, 0x22)};"
        f" border-radius: 12px; padding: 4px; outline: none; }}"
        f"QListWidget::item {{"
        f" padding: 5px 8px; border-radius: 6px;"
        f" margin: 1px 2px; }}"
        f"QListWidget::item:hover {{"
        f" background-color: {qss_alpha(accent, 0x14)}; }}"
        f"QListWidget::item:selected {{"
        f" background-color: {qss_alpha(accent, 0x28)}; color: {text}; }}"
    )


def category_color(category: str, is_light: bool = True) -> str:
    """校对/校验类别的 pill 配色（统一引 SEMANTIC_*）。

    深色写作主题下用提亮一档的颜色，保证暗底上的文字对比度。
    """
    from ui.theme_style import (
        SEMANTIC_WARN,
        SEMANTIC_BAD,
        SEMANTIC_INFO,
    )

    if is_light:
        if "敏感词" in category:
            return SEMANTIC_BAD
        if "错别字" in category or "自定义" in category:
            return SEMANTIC_WARN
        if "标点" in category:
            return "#6a4ec0"
        if "引号" in category:
            return SEMANTIC_INFO
        if "格式" in category:
            return "#5a5a5a"
        return "#6a5a3a"

    if "敏感词" in category:
        return "#ff8a80"
    if "错别字" in category or "自定义" in category:
        return "#e5b450"
    if "标点" in category:
        return "#a78bfa"
    if "引号" in category:
        return "#7db8f5"
    if "格式" in category:
        return "#b8b8b8"
    return "#c8a878"


def pill_qss(color: str, bg_alpha: int = 0x28) -> str:
    """类型标签 pill 的 QSS（供 QLabel 使用）。"""
    return (
        f"QLabel {{ background: {qss_alpha(color, bg_alpha)};"
        f" color: {color};"
        f" border-radius: 10px; padding: 3px 10px;"
        f" font-size: 11px; font-weight: 600; }}"
    )


class AIWaitingDialog(QDialog):
    """AI 请求等待对话框：无限进度条 + 点点动画 + 已等待秒数 + 取消。

    设计要点：
    - 无限循环进度条（QProgressBar range(0,0)）：AI 请求是 HTTP 长连接，
      服务端不告知百分比，用有限进度条反而误导。
    - 协作式取消：用户点取消时调 worker.stop()，worker 在下一个 chunk
      到达时检查标志并退出（依赖 AIWorker._run_stream 的 _stop 检查）。
      若服务端长时间不返回，worker 会等 timeout 后自己结束。
    - 不 wait 主线程：dialog 关闭后立即返回，worker 由调用方持有并后台跑完。
    - worker 生命周期由外部管理（owner + owner_attr），防止 dialog 关闭后
      worker 被 GC——QThread 还在跑就 GC 会 abort 进程。

    用法：
        worker = AIWorker(...)
        dlg = AIWaitingDialog(self, title="AI 提取人物关系")
        dlg.attach(worker, owner=self, owner_attr="_relation_worker")
        worker.start()
        dlg.exec()
        if dlg.cancelled:
            ...  # 用户取消
        elif dlg.error:
            ...  # 失败
        else:
            result = dlg.text  # 成功
    """

    def __init__(
        self,
        parent=None,
        *,
        title: str = "AI 请求中",
        status: str = "正在请求 AI",
        hint: str = "首次请求可能较慢，请稍候",
        width: int = 420,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)

        # 结果（调用方 exec() 后从这里取）
        self.text = ""
        self.error = ""
        self.cancelled = False

        self._worker = None
        self._owner = None
        self._owner_attr = None
        self._status_base = status
        self._hint_base = hint
        self._tick = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        self._status_label = QLabel(status)
        self._status_label.setWordWrap(True)
        lay.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # 无限循环
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        lay.addWidget(self._progress)

        self._hint_label = QLabel(hint)
        self._hint_label.setWordWrap(True)
        self._hint_label.setObjectName("aiWaitHint")
        lay.addWidget(self._hint_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        lay.addLayout(btn_row)

        # 主题：从 parent 链读主题，走 apply_dialog_theme 统一处理
        try:
            from ui.theme_style import apply_dialog_theme

            apply_dialog_theme(self)
        except Exception:
            pass
        # hint 弱化样式（用 palette 的 PlaceholderText，跟随主题）
        try:
            from PySide6.QtGui import QPalette

            muted = self.palette().color(QPalette.PlaceholderText).name()
        except Exception:
            muted = "#928A7C"
        self._hint_label.setStyleSheet(
            f"#aiWaitHint {{ font-size: 12px; color: {muted};"
            f" background: transparent; }}"
        )

        # 点点 + 秒数动画
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(500)
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

    def attach(self, worker, *, owner=None, owner_attr: str = None):
        """绑定 AIWorker。

        owner_attr 非空时把 worker 挂到 owner 上（如 workspace._xxx_worker），
        避免 dialog 关闭后 worker 被 Python GC——QThread 还在跑就 GC
        会触发 "QThread: Destroyed while thread is still running" 并
        abort 进程。
        """
        self._worker = worker
        if owner is not None and owner_attr:
            self._owner = owner
            self._owner_attr = owner_attr
            try:
                setattr(owner, owner_attr, worker)
            except Exception:
                pass
        worker.finished_ok.connect(self._on_ok)
        worker.failed.connect(self._on_fail)

    # ---------- 内部回调 ----------

    def _on_ok(self, text: str):
        self.text = text or ""
        self.accept()

    def _on_fail(self, msg: str):
        self.error = msg or "未知错误"
        self.reject()

    def _on_cancel(self):
        self.cancelled = True
        w = self._worker
        if w is not None:
            try:
                w.stop()
            except RuntimeError:
                pass
        self.reject()

    def _on_tick(self):
        self._tick += 1
        t = self._tick
        dots = "·" * (((t - 1) % 3) + 1)
        self._status_label.setText(f"{self._status_base}{dots}")
        secs = t // 2
        if secs > 0:
            self._hint_label.setText(f"{self._hint_base}（已等待 {secs} 秒）")

    # ---------- 生命周期 ----------

    def done(self, result: int):
        """关对话框时：
        1. 停动画
        2. 兜底判定 cancelled——用户按 Esc / 点 × 关闭时，done() 会收到
           Rejected，但 _on_cancel 没被触发，cancelled 仍是 False。
           若不修正，调用方会把"空文本"当成"AI 返回空"处理，误报
           "AI 没有找到…"。这里补一刀：既不是正常完成（有 text）、
           也不是失败（有 error），就视为取消。
        3. 断开 worker 信号——dialog 关闭后 worker 可能还在后台跑；
           信号若仍连着，worker 完成时会访问已删的 C++ 对象 → RuntimeError。
        4. 不 wait 主线程——worker 由调用方持有，后台跑完自然回收。
        """
        try:
            self._anim_timer.stop()
        except RuntimeError:
            pass

        # 兜底：非成功、非失败 → 视为取消
        if not self.error and not self.text and not self.cancelled:
            self.cancelled = True

        w = self._worker
        if w is not None:
            # 只断开 attach 里实际连过的信号，避免 PySide6 对未连接的
            # 信号 disconnect 打 RuntimeWarning（chunk_received 从没连过）。
            for sig, slot in (
                (w.finished_ok, self._on_ok),
                (w.failed, self._on_fail),
            ):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            # 用户取消（含 Esc / ×，由上面的兜底标记）时真正中断请求：
            # stop() 会主动关闭底层连接，线程几十毫秒内结束；
            # 成功/失败时线程已结束，retire 为空操作。
            # 若 stop 后仍未结束（极端情况），retire_worker 会把它
            # 交给全局 Reaper 保活，绝不在运行时被 GC 导致进程 abort。
            if self.cancelled:
                try:
                    w.stop()
                except (RuntimeError, AttributeError):
                    pass
            try:
                from utils.ai_client import retire_worker

                retire_worker(w)
            except Exception:
                pass
            # 清掉 owner 上的引用：收留工作已交给 Reaper，
            # 用户立刻再次发起同一任务时不会覆盖到仍在跑的旧线程。
            if self._owner is not None and self._owner_attr:
                try:
                    if getattr(self._owner, self._owner_attr, None) is w:
                        setattr(self._owner, self._owner_attr, None)
                except Exception:
                    pass
            self._worker = None

        super().done(result)
