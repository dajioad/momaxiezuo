# ui/readability_dialog.py
"""墨码写作 - 追读体检结果对话框

结构：
- Tab 1「规则检测」：总分 + 情绪曲线 + 9 张规则卡片（可定位）
- Tab 2「AI 深度体检」：AI 按钮 + 结果卡片（独立滚动）
- 底部：关闭

AI 结果独立成页，不再和规则卡片挤在一起；流式输出时内容
始终在 AI 页自己的滚动区里，不会被底部按钮遮挡。
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
    QPlainTextEdit,
    QTabWidget,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import (
    QTextCursor,
    QPainter,
    QColor,
    QPen,
    QPainterPath,
    QFont,
)

from ui.theme_style import (
    qss_alpha,
    accent_button_qss,
    SEMANTIC_OK,
    SEMANTIC_WARN,
    SEMANTIC_BAD,
)
from utils.readability import Report, CheckItem

# ============================================================
# 情绪曲线
# ============================================================


class _EmotionCurve(QWidget):
    """情绪曲线：把整章切成 N 个窗口画折线。

    - X 轴：章节进度（0 → 100%）
    - Y 轴：该窗口的情绪密度
    - 底部：把 < 平均分 60% 的窗口标红，提示"这段太平"
    - 鼠标悬停：显示当前窗口的情绪分
    """

    def __init__(self, curve, theme: dict, parent=None):
        super().__init__(parent)
        self._curve = list(curve or [])
        self._theme = theme or {}
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self._hover_idx = -1

    def set_data(self, curve, theme=None):
        self._curve = list(curve or [])
        if theme is not None:
            self._theme = theme
        self.update()

    def mouseMoveEvent(self, event):
        if not self._curve:
            return
        x = event.position().x() if hasattr(event, "position") else event.pos().x()
        n = len(self._curve)
        pad_l, pad_r = 8, 8
        w = max(1, self.width() - pad_l - pad_r)
        idx = int((x - pad_l) / w * n) if w else -1
        idx = max(0, min(n - 1, idx))
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        t = self._theme
        text_c = QColor(t.get("text", "#3b342a"))
        muted = QColor(t.get("muted", "#9a8e76"))
        accent = QColor(t.get("accent", "#b08a4e"))
        editor_bg = QColor(t.get("editor_bg", "#fcfaf4"))

        p.fillRect(self.rect(), editor_bg)

        if not self._curve:
            p.setPen(muted)
            p.setFont(QFont(self.font().family(), 10))
            p.drawText(self.rect(), Qt.AlignCenter, "（章节过短，无情绪曲线）")
            p.end()
            return

        n = len(self._curve)
        pad_l, pad_r = 8, 8
        pad_t, pad_b = 8, 18
        w = max(1, self.width() - pad_l - pad_r)
        h = max(1, self.height() - pad_t - pad_b)

        max_score = max((c[2] for c in self._curve), default=1) or 1
        avg_score = sum(c[2] for c in self._curve) / n
        step = w / n

        flat_threshold = max(1, avg_score * 0.6)
        flat_flags = [c[2] < flat_threshold for c in self._curve]

        # 中线
        mid_y = pad_t + h * 0.5
        grid = QColor(muted)
        grid.setAlpha(0x22)
        p.setPen(QPen(grid, 1, Qt.DashLine))
        p.drawLine(pad_l, int(mid_y), pad_l + w, int(mid_y))

        # 折线
        path = QPainterPath()
        points = []
        for i, (_s, _e, score) in enumerate(self._curve):
            cx = pad_l + step * (i + 0.5)
            cy = pad_t + h * (1 - score / max_score)
            points.append((cx, cy))
            if i == 0:
                path.moveTo(cx, cy)
            else:
                path.lineTo(cx, cy)

        # 填充
        fill_path = QPainterPath(path)
        fill_path.lineTo(points[-1][0], pad_t + h)
        fill_path.lineTo(points[0][0], pad_t + h)
        fill_path.closeSubpath()
        fill_color = QColor(accent)
        fill_color.setAlpha(0x30)
        p.fillPath(fill_path, fill_color)

        # 折线
        p.setPen(QPen(accent, 2))
        p.drawPath(path)

        # 平段红条
        red = QColor(SEMANTIC_BAD)
        for i, is_flat in enumerate(flat_flags):
            if not is_flat:
                continue
            x0 = pad_l + step * i
            p.fillRect(int(x0), pad_t + h + 2, max(1, int(step)), 6, red)

        # 悬停气泡
        if 0 <= self._hover_idx < n:
            cx, cy = points[self._hover_idx]
            p.setPen(QPen(accent, 1.5))
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(int(cx) - 3, int(cy) - 3, 6, 6)
            score = self._curve[self._hover_idx][2]
            label = f"#{self._hover_idx + 1}  情绪 {score}"
            p.setPen(text_c)
            p.setFont(QFont(self.font().family(), 9, QFont.Bold))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(label) + 12
            tx = max(pad_l, min(int(cx) - tw / 2, pad_l + w - tw))
            ty = max(0, int(cy) - 22)
            bubble = QColor(t.get("sidebar", "#efe8d8"))
            p.setBrush(bubble)
            p.setPen(QPen(accent, 1))
            p.drawRoundedRect(tx, ty, tw, 18, 6, 6)
            p.setPen(text_c)
            p.drawText(tx + 6, ty + 13, label)

        # 轴标签
        p.setPen(muted)
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(pad_l, self.height() - 4, "0%")
        right_text = "100%"
        fm = p.fontMetrics()
        p.drawText(
            pad_l + w - fm.horizontalAdvance(right_text),
            self.height() - 4,
            right_text,
        )
        p.end()


# ============================================================
# 规则检查卡片
# ============================================================


class _ItemCard(QFrame):
    """规则检查项卡片。"""

    goto = Signal(int, int)

    def __init__(self, item: CheckItem, theme: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("readItemCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._item = item

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        score_lab = QLabel(f"{item.score} / {item.max_score}")
        score_lab.setObjectName("readScore")
        # 用动态属性控制颜色：QSS 里按 level 匹配（ok/warn/bad）
        score_lab.setProperty("level", item.level)
        score_lab.setFixedWidth(56)
        head.addWidget(score_lab)

        title = QLabel(item.label)
        title.setObjectName("readItemTitle")
        head.addWidget(title)

        level_lab = QLabel(
            {"ok": "良好", "warn": "注意", "bad": "待改"}.get(item.level, "")
        )
        level_lab.setObjectName(f"readLevel_{item.level}")
        head.addWidget(level_lab)
        head.addStretch()

        if item.positions:
            goto_btn = QPushButton("定位")
            goto_btn.setToolTip("跳转到正文对应位置")
            goto_btn.clicked.connect(
                lambda: self.goto.emit(item.positions[0][0], item.positions[0][1])
            )
            head.addWidget(goto_btn)
        lay.addLayout(head)

        msg = QLabel(item.message)
        msg.setWordWrap(True)
        msg.setObjectName("readMessage")
        lay.addWidget(msg)

        for sug in item.suggestions:
            s = QLabel(f"· {sug}")
            s.setWordWrap(True)
            s.setObjectName("readSuggestion")
            lay.addWidget(s)

        self._apply_theme(theme)

    def _apply_theme(self, theme):
        t = theme or {}
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        level_color = {
            "ok": SEMANTIC_OK,
            "warn": SEMANTIC_WARN,
            "bad": SEMANTIC_BAD,
        }.get(self._item.level, muted)

        self.setStyleSheet(
            f"#readItemCard {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px; }}"
            f"#readItemCard:hover {{"
            f" border: 1px solid {qss_alpha(accent, 0x88)}; }}"
            f"QLabel#readScore {{ color: {accent}; font-weight: 700;"
            f" font-size: 15px; }}"
            f'QLabel#readScore[level="ok"] {{ color: {SEMANTIC_OK}; }}'
            f'QLabel#readScore[level="warn"] {{ color: {SEMANTIC_WARN}; }}'
            f'QLabel#readScore[level="bad"] {{ color: {SEMANTIC_BAD}; }}'
            f"QLabel#readItemTitle {{ color: {text_c}; font-weight: 600;"
            f" font-size: 13px; }}"
            f"QLabel#readLevel_ok {{ color: {SEMANTIC_OK};"
            f" background: {qss_alpha(SEMANTIC_OK, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#readLevel_warn {{ color: {SEMANTIC_WARN};"
            f" background: {qss_alpha(SEMANTIC_WARN, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#readLevel_bad {{ color: {SEMANTIC_BAD};"
            f" background: {qss_alpha(SEMANTIC_BAD, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#readMessage {{ color: {text_c}; }}"
            f"QLabel#readSuggestion {{ color: {muted}; font-size: 12px;"
            f" padding-left: 4px; }}"
        )


# ============================================================
# AI 结果卡片
# ============================================================


class _AICard(QFrame):
    """AI 深度体检结果卡片。"""

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("readAICard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._theme = theme or {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("AI 内容诊断")
        title.setObjectName("readAITitle")
        head.addWidget(title)
        head.addStretch()
        lay.addLayout(head)

        # 流式预览
        self._stream_view = QPlainTextEdit()
        self._stream_view.setReadOnly(True)
        self._stream_view.setPlaceholderText("AI 正在分析…")
        self._stream_view.setMinimumHeight(140)
        self._stream_view.setMaximumHeight(240)
        self._stream_view.setObjectName("readAIStream")
        self._stream_view.setVisible(False)
        lay.addWidget(self._stream_view)

        # 结构化区域
        self._structured = QWidget()
        sl = QVBoxLayout(self._structured)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        lay.addWidget(self._structured)

        # 总评（一句话）
        self.verdict_label = QLabel("")
        self.verdict_label.setWordWrap(True)
        self.verdict_label.setObjectName("readAIVerdict")
        sl.addWidget(self.verdict_label)

        # 弃读预警（最多一处）
        self._drop_box = QFrame()
        self._drop_box.setObjectName("readAIDropBox")
        dl = QVBoxLayout(self._drop_box)
        dl.setContentsMargins(10, 8, 10, 8)
        dl.setSpacing(4)
        drop_title = QLabel("弃读预警")
        drop_title.setObjectName("readAISection")
        dl.addWidget(drop_title)
        self._drop_point = QLabel("")
        self._drop_point.setObjectName("readAIText")
        self._drop_point.setWordWrap(True)
        dl.addWidget(self._drop_point)
        self._drop_reason = QLabel("")
        self._drop_reason.setObjectName("readAITextMuted")
        self._drop_reason.setWordWrap(True)
        dl.addWidget(self._drop_reason)
        sl.addWidget(self._drop_box)

        # 问题清单（动态渲染）
        self._issues_title = QLabel("问题清单（按严重度）")
        self._issues_title.setObjectName("readAISection")
        sl.addWidget(self._issues_title)
        self._issues_holder = QWidget()
        self._issues_layout = QVBoxLayout(self._issues_holder)
        self._issues_layout.setContentsMargins(0, 0, 0, 0)
        self._issues_layout.setSpacing(6)
        sl.addWidget(self._issues_holder)

        # 改写示范
        self._rewrite_title = QLabel("✍  改写示范")
        self._rewrite_title.setObjectName("readAISection")
        sl.addWidget(self._rewrite_title)
        self._rewrite_holder = QWidget()
        rl = QVBoxLayout(self._rewrite_holder)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl_row = QHBoxLayout()
        rl_row.setSpacing(8)

        left_box = QFrame()
        left_box.setObjectName("readAIOrigBox")
        ll = QVBoxLayout(left_box)
        ll.setContentsMargins(8, 6, 8, 6)
        ll.setSpacing(2)
        lab = QLabel("原文")
        lab.setObjectName("readAIBoxLabel")
        ll.addWidget(lab)
        self._rewrite_orig = QLabel("")
        self._rewrite_orig.setWordWrap(True)
        self._rewrite_orig.setObjectName("readAIText")
        self._rewrite_orig.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ll.addWidget(self._rewrite_orig)
        rl_row.addWidget(left_box, 1)

        right_box = QFrame()
        right_box.setObjectName("readAIRewriteBox")
        rr = QVBoxLayout(right_box)
        rr.setContentsMargins(8, 6, 8, 6)
        rr.setSpacing(2)
        lab2 = QLabel("改写")
        lab2.setObjectName("readAIBoxLabel")
        rr.addWidget(lab2)
        self._rewrite_new = QLabel("")
        self._rewrite_new.setWordWrap(True)
        self._rewrite_new.setObjectName("readAIText")
        self._rewrite_new.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rr.addWidget(self._rewrite_new)
        rl_row.addWidget(right_box, 1)

        rl.addLayout(rl_row)
        self._rewrite_why = QLabel("")
        self._rewrite_why.setWordWrap(True)
        self._rewrite_why.setObjectName("readAITextMuted")
        rl.addWidget(self._rewrite_why)
        sl.addWidget(self._rewrite_holder)

        self._apply_theme(theme)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _show_only_stream(self):
        self._stream_view.setVisible(True)
        self._stream_view.clear()
        self._stream_view.setPlaceholderText("AI 正在分析…")
        self._structured.setVisible(False)

    def _show_only_structured(self):
        self._stream_view.setVisible(False)
        self._structured.setVisible(True)

    def start_streaming(self):
        self._show_only_stream()
        self.verdict_label.setText("")

    def append_stream_chunk(self, piece: str):
        if not piece or not self._stream_view.isVisible():
            return
        self._stream_view.moveCursor(QTextCursor.End)
        self._stream_view.insertPlainText(piece)
        sb = self._stream_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _build_issue_row(self, issue: dict) -> QFrame:
        """一条问题卡：严重度 + 类型标签 + 原文引用 + 病因 + 改法。"""
        card = QFrame()
        card.setObjectName("readAIIssue")
        card.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        sev = QLabel(issue.get("severity", "中"))
        sev.setObjectName(f"readAIPrio_{issue.get('severity', '中')}")
        sev.setFixedWidth(28)
        sev.setAlignment(Qt.AlignCenter)
        top.addWidget(sev)
        type_lab = QLabel(issue.get("type", "其他"))
        type_lab.setObjectName("readAITypeTag")
        top.addWidget(type_lab)
        top.addStretch()
        v.addLayout(top)

        quote = issue.get("quote", "")
        if quote:
            q = QLabel(f"「{quote}」")
            q.setWordWrap(True)
            q.setObjectName("readAIQuote")
            v.addWidget(q)
        problem = issue.get("problem", "")
        if problem:
            p = QLabel(problem)
            p.setWordWrap(True)
            p.setObjectName("readAIText")
            v.addWidget(p)
        fix = issue.get("fix", "")
        if fix:
            f = QLabel(f"改法：{fix}")
            f.setWordWrap(True)
            f.setObjectName("readAIFix")
            v.addWidget(f)
        return card

    def set_result(self, data: dict):
        from utils.readability import normalize_ai_data
        self._show_only_structured()
        d = normalize_ai_data(data or {})

        self.verdict_label.setText(d.get("verdict", "") or "（无总评）")

        # 弃读预警
        warn = d.get("drop_warning") or {}
        point = warn.get("point", "")
        reason = warn.get("reason", "")
        self._drop_point.setText(point or "（未指明位置）")
        self._drop_point.setVisible(bool(point))
        self._drop_reason.setText(f"原因：{reason}" if reason else "")
        self._drop_reason.setVisible(bool(reason))
        self._drop_box.setVisible(bool(point or reason))

        # 问题清单
        self._clear_layout(self._issues_layout)
        issues = d.get("issues") or []
        if issues:
            self._issues_title.setText(
                f"问题清单 · {len(issues)} 条（按严重度）")
            for issue in issues:
                self._issues_layout.addWidget(self._build_issue_row(issue))
        else:
            lab = QLabel("未发现内容层面的明显问题。")
            lab.setObjectName("readAITextMuted")
            self._issues_layout.addWidget(lab)
        self._issues_title.setVisible(True)
        self._issues_holder.setVisible(True)

        # 改写示范
        demo = d.get("rewrite_demo") or {}
        if demo.get("original") or demo.get("rewritten"):
            self._rewrite_title.setVisible(True)
            self._rewrite_holder.setVisible(True)
            self._rewrite_orig.setText(demo.get("original", "—"))
            self._rewrite_new.setText(demo.get("rewritten", "—"))
            why = demo.get("why", "")
            if why:
                self._rewrite_why.setText(f"为什么：{why}")
                self._rewrite_why.setVisible(True)
            else:
                self._rewrite_why.setVisible(False)
        else:
            self._rewrite_title.setVisible(False)
            self._rewrite_holder.setVisible(False)

    def set_error(self, msg: str):
        self._stream_view.setVisible(False)
        self._structured.setVisible(True)
        for w in (
            self._drop_box,
            self._rewrite_title,
            self._rewrite_holder,
        ):
            w.setVisible(False)
        self._issues_title.setVisible(False)
        self._issues_holder.setVisible(False)
        self.verdict_label.setVisible(True)
        self.verdict_label.setText(f"⚠ {msg}")
        self._clear_layout(self._issues_layout)

    def _apply_theme(self, theme):
        t = theme or {}
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        sidebar = t.get("sidebar", "#efe8d8")

        self.setStyleSheet(
            f"#readAICard {{ background-color: {editor_bg};"
            f" border: 2px solid {qss_alpha(accent, 0x55)};"
            f" border-radius: 10px; }}"
            f"QLabel#readAITitle {{ color: {accent}; font-size: 14px;"
            f" font-weight: bold; }}"
            f"QLabel#readAIVerdict {{ color: {text_c}; font-size: 14px;"
            f" font-weight: bold; padding: 4px 0; }}"
            f"QLabel#readAISection {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; padding-top: 4px; }}"
            f"QLabel#readAIText {{ color: {text_c}; font-size: 13px; }}"
            f"QLabel#readAITextMuted {{ color: {muted}; font-size: 12px;"
            f" font-style: italic; }}"
            f"QLabel#readAIBoxLabel {{ color: {muted}; font-size: 11px;"
            f" font-weight: bold; }}"
            f"QFrame#readAIDropBox {{ background: {qss_alpha(SEMANTIC_WARN, 0x10)};"
            f" border: 1px solid {qss_alpha(SEMANTIC_WARN, 0x44)};"
            f" border-radius: 6px; }}"
            f"QFrame#readAIIssue {{ background: {sidebar};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 6px; }}"
            f"QLabel#readAITypeTag {{ color: {muted}; font-size: 11px;"
            f" font-weight: bold; background: {qss_alpha(muted, 0x18)};"
            f" border-radius: 4px; padding: 1px 8px; }}"
            f"QLabel#readAIQuote {{ color: {muted}; font-size: 12px;"
            f" font-style: italic; }}"
            f"QLabel#readAIFix {{ color: {text_c}; font-size: 13px; }}"
            f"QFrame#readAIOrigBox {{ background: {qss_alpha(SEMANTIC_BAD, 0x10)};"
            f" border: 1px solid {qss_alpha(SEMANTIC_BAD, 0x33)};"
            f" border-radius: 6px; }}"
            f"QFrame#readAIRewriteBox {{ background: {qss_alpha(SEMANTIC_OK, 0x10)};"
            f" border: 1px solid {qss_alpha(SEMANTIC_OK, 0x33)};"
            f" border-radius: 6px; }}"
            f"QLabel#readAIPrio_高 {{ background: {SEMANTIC_BAD}; color: #ffffff;"
            f" border-radius: 4px; font-size: 11px; font-weight: bold;"
            f" padding: 2px 0; }}"
            f"QLabel#readAIPrio_中 {{ background: {SEMANTIC_WARN}; color: #ffffff;"
            f" border-radius: 4px; font-size: 11px; font-weight: bold;"
            f" padding: 2px 0; }}"
            f"QLabel#readAIPrio_低 {{ background: {muted}; color: #ffffff;"
            f" border-radius: 4px; font-size: 11px; font-weight: bold;"
            f" padding: 2px 0; }}"
            f"QPlainTextEdit#readAIStream {{ background-color: {editor_bg};"
            f" color: {muted}; border: 1px dashed {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 6px; font-size: 12px; }}"
        )


# ============================================================
# 主对话框
# ============================================================


class ReadabilityDialog(QDialog):
    """追读体检结果面板（Tab 分页：规则 / AI）。"""

    goto_position = Signal(int, int)
    request_ai = Signal()

    def __init__(self, report: Report, parent=None):
        super().__init__(parent)
        self._report = report
        self._theme = self._resolve_theme()
        self._ai_card = None
        self._ai_button = None
        self._ai_worker = None
        self._emotion_curve = None
        self._ai_scroll = None
        self._ai_placeholder = None
        self.setWindowTitle("追读体检")
        self.setMinimumSize(760, 760)
        self._build_ui()
        self._apply_theme()

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

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_rule_page(), "规则检测")
        self.tabs.addTab(self._build_ai_page(), "AI 深度体检")
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _build_rule_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)

        # 头部：大分数
        head = QFrame()
        head.setObjectName("readHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 16, 20, 16)

        self.score_label = QLabel(str(self._report.total))
        self.score_label.setObjectName("readBigScore")
        hl.addWidget(self.score_label)

        meta = QVBoxLayout()
        self.level_label = QLabel(self._report.level)
        self.level_label.setObjectName("readBigLevel")
        meta.addWidget(self.level_label)

        stats = self._report.stats
        self.stat_label = QLabel(
            f"{stats.get('chars', 0):,} 字 · "
            f"{stats.get('lines', 0)} 段 · "
            f"对话 {stats.get('dialogues', 0)} 行"
        )
        self.stat_label.setObjectName("readStat")
        meta.addWidget(self.stat_label)
        hl.addLayout(meta)
        hl.addStretch()
        lay.addWidget(head)

        notice = QLabel(
            "规则分只评估形式与节奏（段落 / 对话 / 钩子 / 情绪密度 / 出戏穿帮），"
            "不评判剧情、逻辑与套路——形式满分但内容荒谬也会得高分，"
            "内容层面请看「AI 深度体检」。"
        )
        notice.setWordWrap(True)
        notice.setObjectName("readNotice")
        lay.addWidget(notice)

        # 情绪曲线
        curve_data = getattr(self._report, "curve", None)
        if curve_data:
            curve_title = QLabel(
                "情绪曲线  ·  鼠标悬停查看每 200 字窗口的情绪密度"
                "（底部红条 = 平静段落）"
            )
            curve_title.setObjectName("readCurveTitle")
            lay.addWidget(curve_title)
            self._emotion_curve = _EmotionCurve(curve_data, self._theme)
            self._emotion_curve.setFixedHeight(90)
            lay.addWidget(self._emotion_curve)

        # 规则卡片滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        for item in self._report.items:
            card = _ItemCard(item, self._theme)
            card.goto.connect(self._on_goto)
            cl.addWidget(card)
        cl.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, 1)

        return page

    def _build_ai_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)

        # 顶部：说明 + 按钮
        top_bar = QFrame()
        top_bar.setObjectName("readAITopBar")
        tl = QHBoxLayout(top_bar)
        tl.setContentsMargins(14, 10, 14, 10)
        tl.setSpacing(10)

        tip = QLabel(
            "规则管形式，AI 管内容：AI 专查规则读不出来的剧情逻辑、"
            "人设、动机、套路、对白与情绪问题，约需 20-60 秒。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("readAITopTip")
        tl.addWidget(tip, 1)

        self._ai_button = QPushButton(" 开始分析")
        self._ai_button.setProperty("variant", "primary")
        self._ai_button.setCursor(Qt.PointingHandCursor)
        self._ai_button.clicked.connect(self._on_ai_clicked)
        tl.addWidget(self._ai_button)
        lay.addWidget(top_bar)

        # AI 结果滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        self._ai_container_layout = cl

        # 占位引导
        self._ai_placeholder = QLabel(
            "点击右上角「开始分析」，AI 以资深编辑视角审读这一章。\n\n"
            "结果包括：一句话总评 / 问题清单（逻辑穿帮 · 人设崩 ·\n"
            "动机牵强 · 套路陈词 · 对白出戏 · 情绪虚假）/\n"
            "弃读预警 / 改写示范。"
        )
        self._ai_placeholder.setAlignment(Qt.AlignCenter)
        self._ai_placeholder.setObjectName("readAIPlaceholder")
        self._ai_placeholder.setWordWrap(True)
        cl.addStretch(1)
        cl.addWidget(self._ai_placeholder)
        cl.addStretch(1)

        scroll.setWidget(container)
        lay.addWidget(scroll, 1)
        self._ai_scroll = scroll

        return page

    # ---------- 事件 ----------
    def _on_ai_clicked(self):
        if not self._ai_button.isEnabled():
            return
        self._ai_button.setEnabled(False)
        self._ai_button.setText(" AI 分析中…（预计 20-60 秒）")

        # 隐藏占位（占位在布局里的 stretch 之间，直接 setVisible(False)）
        if self._ai_placeholder is not None:
            self._ai_placeholder.setVisible(False)

        if self._ai_card is None:
            self._ai_card = _AICard(self._theme)
            # 插到最前面（占位之前）
            self._ai_container_layout.insertWidget(0, self._ai_card)
        self._ai_card.start_streaming()

        QTimer.singleShot(
            0,
            lambda: self._ai_scroll.verticalScrollBar().setValue(
                self._ai_scroll.verticalScrollBar().maximum()
            ),
        )
        self.request_ai.emit()

    def append_ai_chunk(self, piece: str):
        """流式：workspace 每收到一块就调这里。"""
        if self._ai_card is None:
            return
        self._ai_card.append_stream_chunk(piece)
        # 流式期间持续滚到底
        if self._ai_scroll is not None:
            sb = self._ai_scroll.verticalScrollBar()
            sb.setValue(sb.maximum())

    def show_ai_result(self, data: dict, error: str = ""):
        self._ai_button.setEnabled(True)
        self._ai_button.setText(" 重新分析")
        if self._ai_card is None:
            return
        if error:
            self._ai_card.set_error(error)
        else:
            self._ai_card.set_result(data)
        if self._ai_scroll is not None:
            QTimer.singleShot(
                0, lambda: self._ai_scroll.verticalScrollBar().setValue(0)
            )

    def _on_goto(self, start: int, end: int):
        self.goto_position.emit(start, end)
        self.raise_()

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

        score_color = {
            "优": SEMANTIC_OK,
            "良": SEMANTIC_OK,
            "中": SEMANTIC_WARN,
            "差": SEMANTIC_BAD,
        }.get(self._report.level, muted)

        self.setStyleSheet(
            f"ReadabilityDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            # 头部
            f"QFrame#readHead {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px; }}"
            f"QLabel#readBigScore {{ color: {score_color};"
            f" font-size: 48px; font-weight: bold; min-width: 100px; }}"
            f"QLabel#readBigLevel {{ color: {score_color};"
            f" font-size: 20px; font-weight: bold; }}"
            f"QLabel#readStat {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#readNotice {{ color: {muted}; font-size: 12px;"
            f" padding: 0 2px; }}"
            f"QLabel#readCurveTitle {{ color: {muted}; font-size: 12px;"
            f" font-weight: bold; padding: 4px 0 0 0; }}"
            # AI 页顶部栏
            f"QFrame#readAITopBar {{ background-color: {qss_alpha(accent, 0x10)};"
            f" border: 1px solid {qss_alpha(accent, 0x33)};"
            f" border-radius: 8px; }}"
            f"QLabel#readAITopTip {{ color: {text}; font-size: 12px; }}"
            f"QLabel#readAIPlaceholder {{ color: {muted}; font-size: 13px;"
            f" padding: 40px 20px; }}"
            # Tab
            f"QTabWidget::pane {{ border: none;"
            f" border-top: 1px solid {qss_alpha(muted, 0x33)};"
            f" background: transparent; top: -1px; }}"
            f"QTabBar {{ qproperty-drawBase: 0; }}"
            f"QTabBar::tab {{ background-color: {qss_alpha(muted, 0x08)};"
            f" color: {muted}; border: none; border-radius: 4px;"
            f" padding: 7px 16px; }}"
            f"QTabBar::tab:selected {{ color: {text}; font-weight: bold;"
            f" border-bottom: 2px solid {accent}; margin-bottom: -1px; }}"
            f"QTabBar::tab:hover {{ color: {accent}; }}"
        )

        if self._ai_button is not None:
            self._ai_button.setStyleSheet(
                accent_button_qss(
                    accent,
                    accent_fg,
                    accent_hover,
                    selector="QPushButton",
                    padding="6px 16px",
                    bold=True,
                )
            )

    def _retire_ai_worker(self):
        """确保 AI worker 已停止（Esc/按钮/标题栏 × 三条关闭路径共用）。

        worker 由 workspace._run_ai_readability 创建并挂在 self._ai_worker；
        stop() 主动断连让线程快速结束，未结束的交给全局 Reaper 保活，
        杜绝 dialog 被 GC 时 "QThread 仍在运行" 导致进程 abort。
        """
        w = getattr(self, "_ai_worker", None)
        if w is not None:
            try:
                from utils.ai_client import retire_worker

                retire_worker(w)
            except Exception:
                pass
            self._ai_worker = None

    def done(self, result):
        # Esc / 对话框按钮走 reject()→done()，不发送 QCloseEvent，
        # 必须在这里统一收口，否则流式分析中 Esc 关闭会漏退休 worker。
        self._retire_ai_worker()
        super().done(result)

    def closeEvent(self, event):
        self._retire_ai_worker()
        super().closeEvent(event)
