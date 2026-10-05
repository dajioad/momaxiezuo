# ui/rewrite_dialog.py
"""墨码写作 - AI 改写对比对话框

选中一段文本，AI 生成 3 个不同风格的候选版本并排显示，挑一个替换原文。

设计（重写版）：
- **3 个独立请求并行**：每个候选独立一个 worker，流式生成
- 每个卡片独立更新：哪个 worker 先回来哪个先填
- 支持中途停止：点"停止"按钮中断全部 3 个 worker，已生成内容保留
- 长文本友好：单次请求只生成 1 个版本，超时阈值 180 秒
- 顶部可填改写要求，点"重新生成"重新请求
- 三个候选风格固定：口语化 / 文学化 / 紧凑
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QFrame,
    QLineEdit,
    QApplication,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QTextCursor, QColor
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from utils.ai_client import AIWorker, load_ai_config, has_api_key
from utils.logger import get_logger

logger = get_logger()


# 单次请求超时（秒）。比 ai_client._REQUEST_TIMEOUT 默认 60 大，
# 因为长文本改写单版本可能就要 30-60 秒；流式下这个超时是"两次
# 数据之间的最长等待"，只要服务端持续吐 token 就不会触发。
_REWRITE_TIMEOUT = 180.0


# 三个候选的风格。顺序即卡片顺序。
# (标签, prompt 里对风格的描述)
_STYLES = [
    (
        "更口语化",
        "让语言更贴近日常对话，减少书面腔，增加生活化的语气词和短句，"
        "但不要使用网络流行语",
    ),
    (
        "更文学化",
        "注重画面感与氛围描写，用更精致的修辞和节奏，营造沉浸感，" "但不要堆砌辞藻",
    ),
    (
        "更紧凑",
        "砍掉冗余的修饰和铺垫，保留核心情节，节奏更快，冲击力更强，"
        "但不要丢失关键信息",
    ),
]


_REWRITE_PROMPT = """你是小说编辑。请对以下原文进行改写。

【改写风格】
{style_desc}

【硬性要求】
- 保持原意和情节不变
- 篇幅控制在原文的 0.8~1.2 倍
- 只输出改写后的正文，不要任何解释、不要 markdown 代码块标记、不要"版本1"这类前缀
- 不要分点、不要加小标题
- 直接开始写正文

【原文】
{original}
{extra}"""


class _CandidateCard(QFrame):
    """一个候选版本的卡片。支持流式追加。"""

    replace_requested = Signal(str)  # 携带候选文本

    def __init__(self, index: int, style_label: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("candidateCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._text = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        title_text = f"候选 {index + 1}"
        if style_label:
            title_text += f"  ·  {style_label}"
        self.title = QLabel(title_text)
        self.title.setObjectName("candTitle")
        lay.addWidget(self.title)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("生成中…")
        self.text_edit.setMinimumHeight(140)
        lay.addWidget(self.text_edit, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setEnabled(False)
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._on_copy)
        btn_row.addWidget(self.copy_btn)
        self.replace_btn = QPushButton("替换原文")
        self.replace_btn.setEnabled(False)
        self.replace_btn.setCursor(Qt.PointingHandCursor)
        self.replace_btn.clicked.connect(self._on_replace)
        btn_row.addWidget(self.replace_btn, 1)
        lay.addLayout(btn_row)

        # hover 金色浮影（QSS 不支持 box-shadow，只能用 DropShadow）
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(0)
        self._shadow.setOffset(0, 2)
        self._shadow.setColor(QColor(176, 138, 78, 60))
        self.setGraphicsEffect(self._shadow)

    def set_shadow_accent(self, accent_hex: str):
        """切主题时更新阴影颜色（默认金色，alpha 60）。"""
        c = QColor(accent_hex)
        if c.isValid():
            c.setAlpha(60)
            self._shadow.setColor(c)

    def enterEvent(self, event):
        self._shadow.setBlurRadius(14)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._shadow.setBlurRadius(0)
        super().leaveEvent(event)

    def append_chunk(self, piece: str):
        """流式追加一个片段。"""
        if not piece:
            return
        self._text += piece
        # 光标移到末尾，insertPlainText 会自动把光标带过去
        self.text_edit.moveCursor(QTextCursor.End)
        self.text_edit.insertPlainText(piece)
        sb = self.text_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def finalize(self):
        """标记完成，启用按钮。"""
        has = bool(self._text.strip())
        self.replace_btn.setEnabled(has)
        self.copy_btn.setEnabled(has)

    def set_loading(self):
        self._text = ""
        self.text_edit.clear()
        self.text_edit.setPlaceholderText("生成中…")
        self.replace_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)

    def set_error(self, msg: str):
        self._text = ""
        self.text_edit.clear()
        self.text_edit.setPlaceholderText(msg)
        self.replace_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)

    def _on_replace(self):
        if self._text.strip():
            self.replace_requested.emit(self._text)

    def _on_copy(self):
        if self._text.strip():
            QApplication.clipboard().setText(self._text)


class RewriteDialog(QDialog):
    """AI 改写对比对话框（非模态）。"""

    # 由外部（workspace）连接：用新文本替换编辑器里当初选中的范围
    apply_text = Signal(str)

    def __init__(self, original_text: str, parent=None, theme: dict = None):
        super().__init__(parent)
        self._original = original_text or ""
        self._theme = theme or {}
        # 每个候选一个 worker
        self._workers = [None, None, None]
        # 每个候选的流式缓冲
        self._buffers = ["", "", ""]
        # 完成标记（含失败）
        self._finished = [False, False, False]
        # 失败标记
        self._failed = [False, False, False]
        # 请求序号，用于让旧回调失效
        self._request_seq = 0
        self._running = False
        self.setWindowTitle("AI 改写对比")
        self.setMinimumSize(900, 620)
        self.setModal(False)
        self._build_ui()
        self._apply_theme()
        # 打开即发请求（延迟一下让窗口先显示）
        QTimer.singleShot(100, self._do_rewrite)

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("AI 改写对比")
        title.setObjectName("rwTitle")
        head.addWidget(title)
        head.addStretch()
        self.orig_info = QLabel(f"原文 {len(self._original)} 字")
        self.orig_info.setObjectName("rwInfo")
        head.addWidget(self.orig_info)
        root.addLayout(head)

        orig_box = QFrame()
        orig_box.setObjectName("origBox")
        ol = QVBoxLayout(orig_box)
        ol.setContentsMargins(10, 8, 10, 8)
        ol.setSpacing(4)
        orig_label = QLabel("原文")
        orig_label.setObjectName("rwSectionLabel")
        ol.addWidget(orig_label)
        self.orig_view = QTextEdit()
        self.orig_view.setReadOnly(True)
        self.orig_view.setPlainText(self._original)
        self.orig_view.setFixedHeight(80)
        ol.addWidget(self.orig_view)
        root.addWidget(orig_box)

        req_row = QHBoxLayout()
        req_row.setSpacing(8)
        req_row.addWidget(QLabel("改写要求"))
        self.req_edit = QLineEdit()
        self.req_edit.setPlaceholderText(
            "可选，如：保留所有对话 / 压缩到 300 字 / 增加环境描写"
        )
        req_row.addWidget(self.req_edit, 1)
        self.action_btn = QPushButton(
            qta.icon("fa5s.sync",
                     color=(self._theme or {}).get("text", "#3b342a")),
            " 重新生成")
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.clicked.connect(self._on_action_clicked)
        req_row.addWidget(self.action_btn)
        root.addLayout(req_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("rwStatus")
        root.addWidget(self.status_label)

        cand_row = QHBoxLayout()
        cand_row.setSpacing(10)
        self.cards = []
        for i, (style_label, _desc) in enumerate(_STYLES):
            card = _CandidateCard(i, style_label)
            card.replace_requested.connect(self._on_replace)
            self.cards.append(card)
            cand_row.addWidget(card, 1)
        root.addLayout(cand_row, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _apply_theme(self):
        t = self._theme
        if not t:
            return
        bg = t.get("bg", "#f7f2e8")
        sidebar = t.get("sidebar", "#efe8d8")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"RewriteDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#rwTitle {{ font-size: 16px; font-weight: bold; }}"
            f"QLabel#rwInfo {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#rwSectionLabel {{ color: {muted}; font-size: 14px;"
            f" font-weight: bold; }}"
            f"QLabel#rwStatus {{ color: {muted}; font-size: 12px; }}"
            f"QFrame#origBox {{ background-color: {sidebar};"
            f" border-radius: 8px; }}"
            f"QFrame#candidateCard {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x25)};"
            f" border-radius: 10px; }}"
            f"QFrame#candidateCard:hover {{"
            f" border: 1px solid {qss_alpha(accent, 0x66)}; }}"
            f"QLabel#candTitle {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; }}"
            f"QTextEdit {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x44)};"
            f" border-radius: 6px; padding: 6px; }}"
            f"QLineEdit {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 5px 8px; }}"
            f"QLineEdit:focus {{ border: 1px solid {accent}; }}"
        )

        replace_ss = accent_button_qss(
            accent,
            accent_fg,
            accent_hover,
            selector="QPushButton",
            padding="6px 14px",
            bold=True,
        )
        copy_ss = (
            f"QPushButton {{ background: transparent; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x66)};"
            f" border-radius: 6px; padding: 6px 12px; }}"
            f"QPushButton:hover {{ background: {qss_alpha(accent, 0x22)};"
            f" color: {accent}; border-color: {accent}; }}"
            f"QPushButton:disabled {{ color: {muted};"
            f" border-color: {qss_alpha(muted, 0x33)}; }}"
        )
        for card in self.cards:
            card.replace_btn.setStyleSheet(replace_ss)
            card.copy_btn.setStyleSheet(copy_ss)
            card.set_shadow_accent(accent)

        self.action_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {text};"
            f" border: 1px solid {qss_alpha(accent, 0x88)};"
            f" border-radius: 6px; padding: 6px 12px; }}"
            f"QPushButton:hover {{ background: {qss_alpha(accent, 0x22)};"
            f" color: {accent}; }}"
        )
        # 图标颜色不跟随 QSS，主题切换时显式重着色
        self._update_action_btn()

    # ---------- 请求 ----------
    def _on_action_clicked(self):
        """同一个按钮：生成中显示「停止」→ 点击中断；空闲时显示「重新生成」→ 点击发起。"""
        if self._running:
            self._stop_all()
            self.status_label.setText("已停止（已生成的内容保留）")
        else:
            self._do_rewrite()

    def _stop_all(self):
        """停止所有正在运行的 worker。已收到的内容保留在卡片里。"""
        # 递增 seq，让所有挂起的回调失效
        self._request_seq += 1
        # stop() 主动断连，线程快速结束；未结束的交给全局 Reaper
        # 保活，杜绝"wait 超时即置空 → 运行中析构 abort"。
        try:
            from utils.ai_client import retire_worker

            for w in self._workers:
                if w is not None:
                    retire_worker(w)
        except Exception:
            pass
        self._workers = [None, None, None]
        self._running = False
        # 保留已生成内容：把卡片 finalize（启用按钮）
        for i, card in enumerate(self.cards):
            try:
                if self._buffers[i].strip():
                    card.finalize()
                else:
                    card.set_error("（已停止）")
            except RuntimeError:
                pass
        self._finished = [True, True, True]
        self.req_edit.setEnabled(True)
        self._update_action_btn()

    def _do_rewrite(self):
        if not has_api_key():
            self.status_label.setText("尚未配置 API Key，请先到设置里配置")
            for card in self.cards:
                card.set_error("未配置 API Key")
            return
        if not self._original.strip():
            self.status_label.setText("没有原文可改写")
            return

        # 先停掉上一次
        self._stop_all()
        # 本次请求的序号（_stop_all 已经 +1，这里再 +1 保证与所有旧回调不同）
        self._request_seq += 1
        seq = self._request_seq

        # 重置状态
        self._running = True
        self._buffers = ["", "", ""]
        self._finished = [False, False, False]
        self._failed = [False, False, False]

        for card in self.cards:
            card.set_loading()
        self.req_edit.setEnabled(False)
        self._update_action_btn()
        self.status_label.setText("正在并行生成 3 个候选…")

        cfg = load_ai_config()
        extra = self.req_edit.text().strip()
        extra_line = f"\n【额外要求】\n{extra}" if extra else ""

        for i, (_style_label, style_desc) in enumerate(_STYLES):
            prompt = _REWRITE_PROMPT.format(
                style_desc=style_desc,
                original=self._original.strip(),
                extra=extra_line,
            )
            worker = AIWorker(
                [{"role": "user", "content": prompt}],
                cfg["api_key"],
                cfg["base_url"],
                cfg["model"],
                temperature=0.9,
                stream=True,
                timeout=_REWRITE_TIMEOUT,
            )
            # lambda 默认参数捕获 i 和 seq（闭包陷阱）
            worker.chunk_received.connect(
                lambda piece, idx=i, s=seq: self._on_chunk(idx, piece, s)
            )
            worker.finished_ok.connect(
                lambda text, idx=i, s=seq: self._on_finished(idx, text, s)
            )
            worker.failed.connect(
                lambda msg, idx=i, s=seq: self._on_failed(idx, msg, s)
            )
            self._workers[i] = worker
            worker.start()

    def _update_action_btn(self):
        icon_color = (self._theme or {}).get("text", "#3b342a")
        if self._running:
            self.action_btn.setText(" 停止")
            self.action_btn.setIcon(
                qta.icon("fa5s.stop", color=icon_color))
        else:
            self.action_btn.setText(" 重新生成")
            self.action_btn.setIcon(
                qta.icon("fa5s.sync", color=icon_color))

    # ---------- 流式回调 ----------
    def _on_chunk(self, idx: int, piece: str, seq: int):
        if seq != self._request_seq:
            return
        self._buffers[idx] += piece
        try:
            self.cards[idx].append_chunk(piece)
        except RuntimeError:
            pass

    def _on_finished(self, idx: int, full_text: str, seq: int):
        if seq != self._request_seq:
            return
        self._finished[idx] = True
        try:
            card = self.cards[idx]
            # 兜底：流式理论上 buffer == full_text，但万一 worker 走了
            # 非流式分支（不会发生，这里只是防御），用 full_text 补齐
            if not self._buffers[idx].strip() and full_text.strip():
                card.set_loading()
                card.text_edit.setPlainText(full_text)
                self._buffers[idx] = full_text
            card.finalize()
        except RuntimeError:
            pass
        self._check_all_done()

    def _on_failed(self, idx: int, msg: str, seq: int):
        if seq != self._request_seq:
            return
        self._failed[idx] = True
        self._finished[idx] = True
        try:
            self.cards[idx].set_error(msg)
        except RuntimeError:
            pass
        self._check_all_done()

    def _check_all_done(self):
        if not all(self._finished):
            done = sum(self._finished)
            self.status_label.setText(f"正在并行生成…（已完成 {done}/3）")
            return
        self._running = False
        self.req_edit.setEnabled(True)
        self._update_action_btn()
        ok_count = sum(
            1 for i in range(3) if not self._failed[i] and self._buffers[i].strip()
        )
        if ok_count == 3:
            self.status_label.setText("✓ 3 个候选已生成 · 点「替换原文」应用")
        elif ok_count > 0:
            self.status_label.setText(
                f"✓ 生成完成（{ok_count}/3 个成功）· 点「替换原文」应用"
            )
        else:
            self.status_label.setText("✗ 所有候选生成失败")

    # ---------- 替换 / 关闭 ----------
    def _on_replace(self, text: str):
        self.apply_text.emit(text)
        self.close()

    def closeEvent(self, event):
        self._stop_all()
        super().closeEvent(event)
