# ui/script_dialog.py
"""墨码写作 - 小说转剧本对话框

非模态，流式生成。支持标准剧本 / 分场大纲 / 短剧分镜。

着色：流式期间增量着色（每收到 chunk 就处理已完成的行），
完成后做一次全量兜底（force_last=True）。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QComboBox,
    QCheckBox,
    QPlainTextEdit,
    QMessageBox,
    QApplication,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QFont,
    QColor,
    QTextCharFormat,
    QTextCursor,
    QTextBlockFormat,
)

from ui.theme_style import qss_alpha, accent_button_qss, apply_dialog_theme
import qtawesome as qta
from utils.ai_client import AIWorker, load_ai_config, has_api_key
from utils.script_converter import (
    FORMATS,
    build_prompt,
    _fmt_label,
)
from utils.logger import get_logger

logger = get_logger()

# 剧本生成输出量大，超时放宽到 240 秒
_SCRIPT_TIMEOUT = 240.0


class ScriptConvertDialog(QDialog):
    """小说转剧本（非模态）。"""

    def __init__(self, workspace, chapter_id: str, parent=None):
        super().__init__(parent)
        self.ws = workspace
        self.chapter_id = chapter_id
        self._worker = None
        self._buffer = []
        self._running = False
        self._theme = self._resolve_theme()

        # 增量着色：记录已着色到的块号和跨块状态
        # -1 表示还没有任何块被着色
        self._last_colored_block = -1
        self._last_block_was_role = False

        # 章节信息
        self._chapter_title = ""
        self._chapter_text = ""
        if workspace and workspace.work:
            ch = workspace.work.get_chapter(chapter_id)
            if ch:
                self._chapter_title = ch.get("title", "")
            self._chapter_text = workspace.work.read_chapter(chapter_id) or ""

        self.setWindowTitle(f"小说转剧本 · {self._chapter_title}")
        # 720 超出小屏工作区高度；结果区 QPlainTextEdit 自带滚动。
        self.setMinimumSize(920, 620)
        self.setModal(False)

        self._build_ui()
        self._apply_theme()

    def _resolve_theme(self) -> dict:
        editor = getattr(self.ws, "editor", None)
        if editor is not None and hasattr(editor, "_theme"):
            return editor._theme or {}
        return {}

    # =================== UI ===================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # ---- 顶部：标题 + 范围 ----
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon(
            "fa5s.film",
            color=(self._theme or {}).get("accent", "#b08a4e")).pixmap(20, 20))
        head.addWidget(icon)
        title = QLabel("小说转剧本")
        title.setObjectName("scTitle")
        head.addWidget(title)
        head.addStretch()
        self.chapter_label = QLabel(
            f"{self._chapter_title}  ·  {len(self._chapter_text):,} 字"
        )
        self.chapter_label.setObjectName("scChapterInfo")
        head.addWidget(self.chapter_label)
        root.addLayout(head)

        # ---- 选项区 ----
        opt_box = QFrame()
        opt_box.setObjectName("scOptBox")
        ol = QVBoxLayout(opt_box)
        ol.setContentsMargins(14, 10, 14, 10)
        ol.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(QLabel("输出格式"))
        self.format_combo = QComboBox()
        self.format_combo.setMinimumWidth(220)
        for key, label, desc in FORMATS:
            self.format_combo.addItem(f"{label} —— {desc}", key)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        row1.addWidget(self.format_combo)
        row1.addStretch()
        ol.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.cb_action = QCheckBox("保留动作描写")
        self.cb_action.setChecked(True)
        self.cb_voiceover = QCheckBox("保留内心独白（转旁白）")
        self.cb_voiceover.setChecked(True)
        row2.addWidget(self.cb_action)
        row2.addWidget(self.cb_voiceover)
        row2.addStretch()
        ol.addLayout(row2)

        # 提示
        self.hint_label = QLabel(
            "生成的剧本会保留原文情节与对话；对白用全角冒号 + 缩进，"
            "场景标题用【场景N】标注。"
        )
        self.hint_label.setObjectName("scHint")
        self.hint_label.setWordWrap(True)
        ol.addWidget(self.hint_label)

        root.addWidget(opt_box)

        # ---- 结果区 ----
        result_box = QFrame()
        result_box.setObjectName("scResultBox")
        rl = QVBoxLayout(result_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self.stats_bar = QFrame()
        self.stats_bar.setObjectName("scStatsBar")
        sl = QHBoxLayout(self.stats_bar)
        sl.setContentsMargins(12, 6, 12, 6)
        self.status_label = QLabel("选择格式后点「开始生成」")
        self.status_label.setObjectName("scStatus")
        sl.addWidget(self.status_label)
        sl.addStretch()
        rl.addWidget(self.stats_bar)

        self.result_view = QPlainTextEdit()
        self.result_view.setPlaceholderText(
            "生成的剧本会显示在这里。\n\n"
            "生成过程中可以预览，完成后可直接编辑并导出。"
        )
        self.result_view.setFont(QFont("Consolas, Microsoft YaHei", 12))
        self.result_view.setFrameShape(QFrame.NoFrame)
        rl.addWidget(self.result_view, 1)

        root.addWidget(result_box, 1)

        # ---- 底部按钮 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()

        self.gen_btn = QPushButton(" 开始生成")
        self.gen_btn.setProperty("variant", "primary")
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.clicked.connect(self._on_generate)
        bottom.addWidget(self.gen_btn)

        self.copy_btn = QPushButton("复制")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._on_copy)
        bottom.addWidget(self.copy_btn)

        self.export_btn = QPushButton("导出…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)
        bottom.addWidget(self.export_btn)

        bottom.addSpacing(12)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)

        root.addLayout(bottom)
        apply_dialog_theme(self)

    def _on_format_changed(self, *_):
        key = self.format_combo.currentData()
        # 分场大纲 / 短剧分镜：动作/旁白选项无效（结构本身已包含）
        enable_extra = key == "standard"
        self.cb_action.setEnabled(enable_extra)
        self.cb_voiceover.setEnabled(enable_extra)

    # =================== 主题 ===================

    def _apply_theme(self):
        t = self._theme or {
            "bg": "#f7f2e8",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "accent": "#b08a4e",
            "sidebar": "#efe8d8",
        }
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)
        sidebar = t.get("sidebar", "#efe8d8")

        # AI 输出字号（与 AI 气泡共用 [AI] output_font_size）
        ai_fs = 14
        cfg = getattr(self.ws, "cfg", None)
        if cfg is not None:
            try:
                ai_fs = int(cfg.get_config("AI", "output_font_size", "14"))
            except (TypeError, ValueError):
                ai_fs = 14
        ai_fs = max(12, min(20, ai_fs))

        self.setStyleSheet(
            f"ScriptConvertDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#scTitle {{ font-size: 16px; font-weight: bold; }}"
            f"QLabel#scChapterInfo {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#scHint {{ color: {muted}; font-size: 11px; }}"
            f"QLabel#scStatus {{ color: {accent}; font-size: 12px;"
            f" font-weight: bold; }}"
            f"QFrame#scOptBox {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x25)};"
            f" border-radius: 10px; }}"
            f"QFrame#scResultBox {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x25)};"
            f" border-radius: 10px; }}"
            f"QFrame#scStatsBar {{ background-color: {qss_alpha(accent, 0x15)};"
            f" border-top-left-radius: 10px; border-top-right-radius: 10px;"
            f" border-bottom: 1px solid {qss_alpha(accent, 0x33)}; }}"
            f"QComboBox {{ color: {text}; background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x55)};"
            f" border-radius: 6px; padding: 4px 10px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox QAbstractItemView {{ background-color: {editor_bg};"
            f" color: {text}; selection-background-color:"
            f" {qss_alpha(accent, 0x22)}; selection-color: {text}; }}"
            f"QCheckBox {{ color: {text}; }}"
            f"QPlainTextEdit {{ background-color: transparent; color: {text};"
            f" padding: 14px 16px; border: none;"
            f" selection-background-color: {qss_alpha(accent, 0x55)};"
            f" font-size: {ai_fs}px; line-height: 1.8; }}"
        )

        btn_ss = accent_button_qss(
            accent,
            accent_fg,
            accent_hover,
            selector="QPushButton",
            padding="8px 22px",
            bold=True,
        )
        self.gen_btn.setStyleSheet(btn_ss)

    # =================== 着色 ===================

    def _recolor_script(self, *, force_last: bool = False):
        """增量着色，突出剧本结构。

        判断顺序（重要）：
        1. 空行 → 清状态、跳过
        2. 场景标题【...】→ accent 金色加粗
        3. 旁白头（"旁白...："）→ accent 加深加粗斜体
        4. 动作描写（整行（）包裹）→ muted 灰色斜体
        5. 角色名行（简短 + 以「：」结尾）→ accent 加深加粗
        6. 上一行是角色名 → 对白内容（缩进 24px，不改色）
        7. 其他 → 正文色（不动）

        force_last=False（流式期间）：
            跳过最后一块——它可能还没输入完，类型判断不准。
        force_last=True（生成完成）：
            处理所有块，确保末行也被着色。

        跨块状态用 self._last_colored_block / _last_block_was_role 记录，
        每次只处理新增块，O(新增行数) 而非 O(总行数)。
        """
        t = self._theme or {}
        accent = QColor(t.get("accent", "#b08a4e"))
        accent_deep = accent.darker(135)
        muted = QColor(t.get("muted", "#9a8e76"))

        doc = self.result_view.document()
        block = doc.findBlockByNumber(self._last_colored_block + 1)
        prev_is_role = self._last_block_was_role

        while block.isValid():
            next_block = block.next()
            is_last = not next_block.isValid()
            if is_last and not force_last:
                # 最后一块还未输入完，下次再处理
                break

            line = block.text()
            stripped = line.strip()
            fmt = QTextCharFormat()
            colored = False
            now_is_role = False

            if not stripped:
                pass
            elif stripped.startswith("【") and 0 < stripped.find("】") <= 12:
                fmt.setForeground(accent)
                fmt.setFontWeight(QFont.Bold)
                colored = True
            elif stripped.startswith("旁白") and stripped.endswith(("：", ":")):
                fmt.setForeground(accent_deep)
                fmt.setFontWeight(QFont.Bold)
                fmt.setFontItalic(True)
                colored = True
                now_is_role = True
            elif stripped.startswith("（") and stripped.endswith("）"):
                fmt.setForeground(muted)
                fmt.setFontItalic(True)
                colored = True
            elif stripped.endswith(("：", ":")) and len(stripped) <= 20:
                fmt.setForeground(accent_deep)
                fmt.setFontWeight(QFont.Bold)
                colored = True
                now_is_role = True
            elif prev_is_role:
                bfmt = QTextBlockFormat()
                bfmt.setLeftMargin(24)
                cur0 = QTextCursor(block)
                cur0.setBlockFormat(bfmt)

            if colored:
                cur = QTextCursor(block)
                cur.setPosition(block.position())
                cur.setPosition(block.position() + len(line), QTextCursor.KeepAnchor)
                cur.mergeCharFormat(fmt)

            prev_is_role = now_is_role
            self._last_colored_block = block.blockNumber()
            self._last_block_was_role = now_is_role
            block = next_block

    # =================== 生成 ===================

    def _detach_worker(self):
        """断开信号 + 退休当前 worker（停止/关窗/重新生成时统一调用）。

        必须先断信号：stop 主动断连后可能还有在途 chunk，
        不断开会继续 append 到结果框，与"已停止"状态冲突。
        """
        w = self._worker
        if w is None:
            return
        for sig, slot in (
            (w.chunk_received, self._on_chunk),
            (w.finished_ok, self._on_finished),
            (w.failed, self._on_failed),
        ):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            from utils.ai_client import retire_worker

            retire_worker(w)
        except Exception:
            pass
        self._worker = None

    def _on_generate(self):
        if self._running:
            return

        if not has_api_key():
            QMessageBox.information(
                self, "提示", "尚未配置 API Key。请到 设置 → AI 接口 配置后重试。"
            )
            return

        if not self._chapter_text.strip():
            QMessageBox.information(self, "提示", "当前章节没有正文。")
            return

        fmt = self.format_combo.currentData() or "standard"
        include_action = self.cb_action.isChecked()
        include_voiceover = self.cb_voiceover.isChecked()

        # 作品上下文
        context = ""
        try:
            if hasattr(self.ws, "_build_inline_context"):
                context = self.ws._build_inline_context() or ""
        except Exception:
            pass

        prompt = build_prompt(
            self._chapter_text,
            self._chapter_title,
            fmt,
            include_action,
            include_voiceover,
            context,
        )

        self.result_view.clear()
        self._buffer = []
        # 重置增量着色状态：清空后块号从 0 开始
        self._last_colored_block = -1
        self._last_block_was_role = False
        self._running = True
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText(" 生成中…")
        self.status_label.setText(f"正在生成{_fmt_label(fmt)}…（可随时点停止）")
        self.copy_btn.setEnabled(False)
        self.export_btn.setEnabled(False)

        # 生成按钮改成"停止"
        try:
            self.gen_btn.clicked.disconnect()
        except Exception:
            pass
        self.gen_btn.setText(" 停止")
        self.gen_btn.clicked.connect(self._on_stop)
        self.gen_btn.setEnabled(True)

        cfg = load_ai_config()
        # 剧本比小说更结构化，温度取用户设定 × 0.7，避免偏离格式。
        base_temp = cfg.get("creative_temperature", 0.85)
        # 保险：上一轮 worker 若仍在（罕见），先断信号退休，
        # 不能直接覆盖 self._worker（运行中析构 → abort）。
        self._detach_worker()
        self._worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            # round 到 2 位小数：智谱 GLM 等厂商要求 temperature
            # 小数点后最多 2 位，0.595 这类值会被 400 拒绝。
            temperature=round(max(0.0, min(1.0, base_temp * 0.7)), 2),
            stream=True,
            timeout=_SCRIPT_TIMEOUT,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_stop(self):
        self._detach_worker()
        self._finalize_running(stopped=True)

    def _on_chunk(self, piece: str):
        self._buffer.append(piece)
        self.result_view.moveCursor(QTextCursor.End)
        self.result_view.insertPlainText(piece)
        sb = self.result_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        # 增量着色：把已完整结束的行立即上色
        self._recolor_script()

    def _on_finished(self, full_text: str):
        self._worker = None
        if not self._buffer and full_text:
            self.result_view.setPlainText(full_text)
            self._buffer.append(full_text)
            # setPlainText 会重置文档块号，重置着色状态
            self._last_colored_block = -1
            self._last_block_was_role = False
        self._finalize_running(stopped=False)

    def _on_failed(self, msg: str):
        self._worker = None
        self._finalize_running(stopped=False, error=msg)

    def _finalize_running(self, stopped=False, error=""):
        self._running = False
        # 按钮恢复
        try:
            self.gen_btn.clicked.disconnect()
        except Exception:
            pass
        self.gen_btn.setText(" 重新生成")
        self.gen_btn.clicked.connect(self._on_generate)
        self.gen_btn.setEnabled(True)

        text = self.result_view.toPlainText().strip()
        if error:
            self.status_label.setText(f"✗ {error}")
            self.copy_btn.setEnabled(False)
            self.export_btn.setEnabled(False)
            return
        if text:
            # force_last=True：最后一块也处理，确保末行着色
            self._recolor_script(force_last=True)
        if stopped:
            self.status_label.setText(f"已停止 · 当前 {len(text):,} 字")
        else:
            self.status_label.setText(f"✓ 生成完成 · 共 {len(text):,} 字")
        has_content = bool(text)
        self.copy_btn.setEnabled(has_content)
        self.export_btn.setEnabled(has_content)

    # =================== 复制 / 导出 ===================

    def _on_copy(self):
        text = self.result_view.toPlainText().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("✓ 已复制到剪贴板")

    def _on_export(self):
        text = self.result_view.toPlainText().strip()
        if not text:
            return
        from PySide6.QtWidgets import QFileDialog
        import os

        title = self._chapter_title or self.chapter_id or "剧本"
        default_name = f"{title}_剧本.txt"
        path, selected = QFileDialog.getSaveFileName(
            self,
            "导出剧本",
            default_name,
            "文本文件 (*.txt);;Markdown (*.md);;Fountain 剧本 (*.fountain);;所有文件 (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            QMessageBox.information(self, "导出完成", f"已导出到：\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # =================== 生命周期 ===================

    def closeEvent(self, event):
        self._detach_worker()
        super().closeEvent(event)
