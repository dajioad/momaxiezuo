# ui/workspace_parts/ai_features.py
"""墨码写作 - AI 功能子系统（Mixin）

从 workspace.py 拆出的 AI 相关逻辑：
- AI 光标续写（Ctrl+Enter 流式）
- AI 改写 / 梗概 / 提取词条 / 提取关系
- 追读体检（AI 深度分析）
- 伏笔 AI 扫描
- 小说转剧本
- AI 上下文构造（作品资料汇总）

按 Mixin 方式合并到 WritingWorkspace，所有方法直接访问 self.*。
"""

import os
import json
import re

from PySide6.QtWidgets import (
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt

from utils.logger import get_logger

logger = get_logger()


class AIFeaturesMixin:
    """AI 功能子系统。合并到 WritingWorkspace。"""

    # ▼ 块 B ▼
    # ---------- AI 光标续写 ----------
    def _on_inline_continue(self, pos: int, direction: str):
        """编辑器 Ctrl+Enter 触发。"""
        if not self.current_chapter_id:
            return
        # 重入防护：上一轮续写仍在跑时，直接覆盖 self._inline_worker
        # 会让旧线程失去引用（运行中析构 → abort）。提示等当前轮结束。
        old = getattr(self, "_inline_worker", None)
        if old is not None:
            try:
                if old.isRunning():
                    self.save_state_label.setText("AI 续写进行中，请稍候或先停止")
                    return
            except RuntimeError:
                self._inline_worker = None
        from utils.ai_client import has_api_key, load_ai_config, AIWorker

        if not has_api_key():
            self.editor.finish_inline_stream()
            QMessageBox.information(
                self, "AI 续写", "尚未配置 API Key。请在 设置 → AI 接口 中配置后重试。"
            )
            return

        full = self.editor.get_text()
        before = full[max(0, pos - 500) : pos]

        ctx = self._build_inline_context()
        direction_line = f"\n【作者方向】{direction}" if direction else ""

        prompt = (
            "你是一位网文写手，正在帮作者续写小说正文。\n\n"
            "【要求】\n"
            "- 紧接前文继续写，不要重复已有内容\n"
            "- 保持人物性格、语气、文风一致\n"
            "- 网文节奏，每段 2-4 行\n"
            "- 直接输出续写的正文，不要任何解释、不要加「续写」等前缀\n"
            "- 长度约 300-500 字，写完一个完整的场景片段就停\n"
            f"{direction_line}\n\n"
            f"【前文】\n{before}\n"
        )
        if ctx:
            prompt = f"【作品背景】\n{ctx}\n\n{prompt}"

        cfg = load_ai_config()

        self.editor.start_inline_stream(pos)

        self._inline_worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            temperature=cfg.get("creative_temperature", 0.85),
            stream=True,
        )
        self._inline_worker.chunk_received.connect(self.editor.append_inline_chunk)
        self._inline_worker.finished_ok.connect(self._on_inline_finished)
        self._inline_worker.failed.connect(self._on_inline_failed)
        self._inline_worker.start()

    def _build_inline_context(self) -> str:
        """AI 用的轻量上下文：本章梗概 + 主要角色。"""
        if not self.work or not self.current_chapter_id:
            return ""
        parts = []
        try:
            note = self.work.read_note(self.current_chapter_id)
            if note.strip():
                parts.append(f"【本章梗概】{note.strip()[:200]}")
        except Exception:
            pass
        try:
            chars = self.work.load_characters()
            if chars:
                names = []
                for c in chars[:8]:
                    n = (c.get("name") or "").strip()
                    role = (c.get("role") or "").strip()
                    if n:
                        names.append(f"{n}（{role}）" if role else n)
                if names:
                    parts.append("【主要角色】" + "、".join(names))
        except Exception:
            pass
        return "\n".join(parts)

    def _retire_inline_worker(self):
        """停止并收留光标续写 worker（切章/切作品时必须先调用）。

        断开三个信号，防止在途 chunk / 完成回调穿过章节切换
        写进新文档或误弹"续写完成"。
        """
        w = getattr(self, "_inline_worker", None)
        if w is None:
            return
        for sig, slot in (
            (w.chunk_received, self.editor.append_inline_chunk),
            (w.finished_ok, self._on_inline_finished),
            (w.failed, self._on_inline_failed),
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
        self._inline_worker = None

    def _ai_worker_busy(self, attr: str, what: str) -> bool:
        """重入防护：同一 AI 任务仍在跑时拒绝再次发起。

        旧代码直接覆盖 owner 上的 worker 引用，旧线程运行中失引用
        会被 GC → abort 进程。what 为状态条提示文案。
        """
        w = getattr(self, attr, None)
        if w is None:
            return False
        try:
            if w.isRunning():
                self.save_state_label.setText(f"{what}正在进行中，请稍候…")
                return True
        except RuntimeError:
            setattr(self, attr, None)
        return False

    def _on_inline_finished(self, full_text: str):
        self.editor.finish_inline_stream()
        # finished_ok 在 run() 结束后发射，线程已停，直接断引用即可
        # （旧代码的 wait(500) 是 UI 线程无意义阻塞）。
        self._inline_worker = None
        self.save_state_label.setText("AI 续写完成")

    def _on_inline_failed(self, msg: str):
        self.editor.finish_inline_stream()
        self._inline_worker = None
        QMessageBox.warning(self, "AI 续写失败", msg)

    def open_script_converter(self):
        """打开小说转剧本对话框。"""
        if not self.current_chapter_id:
            QMessageBox.information(self, "小说转剧本", "请先打开一个章节。")
            return
        self.flush_save()
        from ui.script_dialog import ScriptConvertDialog

        dlg = getattr(self, "_script_dlg", None)
        if dlg is not None:
            try:
                dlg.close()
            except RuntimeError:
                pass
        dlg = ScriptConvertDialog(self, self.current_chapter_id, self)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._script_dlg = dlg

    # ---------- 追读体检 ----------
    def open_readability_check(self):
        if not self.current_chapter_id:
            QMessageBox.information(self, "追读体检", "请先打开一个章节。")
            return
        self.flush_save()
        text = self.editor.get_text()
        if not text.strip():
            QMessageBox.information(self, "追读体检", "当前章节没有内容。")
            return
        from utils.readability import check_chapter
        from ui.readability_dialog import ReadabilityDialog

        report = check_chapter(text)

        # 规则报告立即落盘（零成本），AI 深度结果回来后更新同一条记录
        record_id = ""
        try:
            from utils import quality_reports as qr
            cid = self.current_chapter_id or ""
            ch_title = cid
            for ch in (getattr(self.work, "chapters", None) or []):
                if str(ch.get("id")) == str(cid):
                    ch_title = ch.get("title") or cid
                    break
            rec = qr.make_record(
                qr.KIND_READABILITY,
                f"追读体检 · {ch_title}",
                {"report": qr.report_to_dict(report), "ai": None},
                chapter_id=str(cid), chapter_title=ch_title,
                score=report.total, level=report.level)
            qr.save_report(self.work.root_dir, rec)
            qr.prune_reports(self.work.root_dir)
            record_id = rec["id"]
        except Exception as e:
            logger.warning(f"追读体检报告保存失败（不影响本次查看）: {e}")

        dlg = ReadabilityDialog(report, self)
        dlg.goto_position.connect(self._locate_in_editor)
        dlg.request_ai.connect(
            lambda d=dlg, t=text, rid=record_id:
            self._run_ai_readability(d, t, rid))
        self._readability_dlg = dlg
        dlg.exec()
        self._readability_dlg = None

    def _run_ai_readability(self, dlg, text: str, record_id: str = ""):
        """调 AI 做深度追读体检（流式）。"""
        from utils.ai_client import has_api_key, load_ai_config, AIWorker
        from utils.readability import build_ai_prompt, parse_ai_response

        if not has_api_key():
            dlg.show_ai_result({}, error="尚未配置 API Key，请到设置里配置")
            return

        ctx = self._build_inline_context()
        prompt = build_ai_prompt(text, ctx)
        cfg = load_ai_config()

        # 深度体检输出含问题清单 + 改写示范，慢模型首 token 可能等
        # 60-90 秒。默认 timeout=60 会误判超时。
        # 放宽到 180 秒；流式下这是"两次数据间最长等待"，只要持续吐字
        # 就不会触发，所以对正常情况无影响。
        worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            temperature=0.5,
            stream=True,
            timeout=180.0,
        )
        dlg._ai_worker = worker

        # 流式：每收到一块就追加到 AI 卡片的流式视图
        worker.chunk_received.connect(dlg.append_ai_chunk)

        def on_ok(raw: str):
            data = parse_ai_response(raw)
            if not data:
                dlg.show_ai_result({}, error="AI 返回格式异常，无法解析")
                return
            dlg.show_ai_result(data)
            # AI 深度结果并入此前落盘的规则报告
            if record_id and getattr(self, "work", None):
                try:
                    from utils import quality_reports as qr
                    rec = qr.load_report(self.work.root_dir, record_id)
                    if rec:
                        rec.setdefault("data", {})["ai"] = data
                        qr.save_report(self.work.root_dir, rec)
                except Exception as e:
                    logger.warning(f"AI 深度体检结果落盘失败: {e}")

        def on_fail(msg: str):
            dlg.show_ai_result({}, error=msg)

        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        worker.start()

    # ▼ 块 C ▼
    # ---------- 伏笔追踪 ----------
    def open_foreshadow_dialog(self):
        if not self.work:
            QMessageBox.information(self, "伏笔追踪", "请先打开一部作品。")
            return
        self.flush_save()
        from ui.foreshadow_dialog import ForeshadowDialog

        dlg = ForeshadowDialog(self.work, self.current_chapter_id, self)
        dlg.request_scan.connect(lambda d=dlg: self._run_foreshadow_scan(d))
        dlg.exec()

    def _run_foreshadow_scan(self, foreshadow_dlg):
        """AI 扫描当前章节找疑似伏笔。

        worker 挂 workspace（不挂 dialog），原因：
        - dialog 关闭后若 worker 只挂在 dialog 上，会被 GC 掉 → QThread
          destroyed while running → 进程 abort
        - 挂 workspace 后，dialog 关闭时只断信号，worker 后台跑完自己清理
        """
        if not self.current_chapter_id:
            QMessageBox.information(
                foreshadow_dlg, "AI 扫描", "请先在编辑器里打开一个章节。"
            )
            return
        from utils.ai_client import has_api_key, load_ai_config, AIWorker
        from utils.foreshadow import build_scan_prompt, parse_scan_response

        if not has_api_key():
            QMessageBox.information(
                foreshadow_dlg, "AI 扫描", "尚未配置 API Key，请到设置里配置。"
            )
            return

        # 已有扫描在跑：忽略重复触发
        existing = getattr(self, "_foreshadow_worker", None)
        if existing is not None:
            try:
                if existing.isRunning():
                    QMessageBox.information(
                        foreshadow_dlg, "AI 扫描", "上次扫描还未结束，请稍候。"
                    )
                    return
            except RuntimeError:
                pass
            # 已结束：清引用，允许重新发起
            self._foreshadow_worker = None

        self.flush_save()
        text = self.editor.get_text()
        if not text.strip():
            QMessageBox.information(foreshadow_dlg, "AI 扫描", "当前章节没有内容。")
            return

        ch = self.work.get_chapter(self.current_chapter_id)
        title = ch.get("title", "") if ch else ""
        prompt = build_scan_prompt(text, title)
        cfg = load_ai_config()

        from ui.dialog_styles import AIWaitingDialog

        # 扫描整章正文 + 输出 JSON，慢模型可能超过默认 60 秒；
        # 放宽到 120 秒，避免正常响应被误判为超时。
        worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            temperature=0.4,
            # stream=True：非流式请求无法中途取消；改流式后点「取消」
            # 能立刻生效（worker 在下一个 chunk 到达时退出）。
            stream=True,
            timeout=120.0,
        )
        wait_dlg = AIWaitingDialog(foreshadow_dlg, title="正在扫描伏笔")
        wait_dlg.attach(worker, owner=self, owner_attr="_foreshadow_worker")
        worker.start()
        wait_dlg.exec()

        # 用户取消：worker 还在后台跑（等下一个 chunk 或 timeout），
        # self._foreshadow_worker 保留引用防止 GC。下次触发扫描时会
        # 检查 isRunning()：还在跑就提示"请稍候"，已结束就清引用。
        if wait_dlg.cancelled:
            self.save_state_label.setText("已取消 AI 扫描")
            return
        if wait_dlg.error:
            if foreshadow_dlg.isVisible():
                QMessageBox.warning(foreshadow_dlg, "AI 扫描失败", wait_dlg.error)
            return

        # 扫描结束：清引用
        try:
            if not worker.isRunning():
                self._foreshadow_worker = None
        except RuntimeError:
            self._foreshadow_worker = None

        hints = parse_scan_response(wait_dlg.text)
        if not hints:
            if foreshadow_dlg.isVisible():
                QMessageBox.information(
                    foreshadow_dlg, "AI 扫描", "AI 没有找到疑似伏笔。"
                )
            return
        if foreshadow_dlg.isVisible():
            self._show_scan_hints(foreshadow_dlg, hints)

    def _show_scan_hints(self, dlg, hints):
        """展示 AI 找到的伏笔候选，让用户勾选加入追踪。"""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QTreeWidget,
            QTreeWidgetItem,
            QDialogButtonBox,
        )
        from utils.foreshadow import add_item

        d = QDialog(dlg)
        d.setWindowTitle("AI 找到的疑似伏笔")
        d.setMinimumSize(640, 420)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        hint = QLabel(
            "勾选要加入追踪的条目。加入后会记录在当前作品下，"
            "你可随时回来编辑或标记回收。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 12px; color: gray;")
        lay.addWidget(hint)

        tree = QTreeWidget()
        tree.setHeaderLabels(["候选伏笔", "原文引用"])
        tree.setColumnWidth(0, 260)
        tree.setIndentation(0)
        lay.addWidget(tree, 1)

        for h in hints:
            it = QTreeWidgetItem([h.get("content", ""), h.get("quote", "")])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
            it.setData(0, Qt.UserRole, h)
            tree.addTopLevelItem(it)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("加入选中")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(d.accept)
        btns.rejected.connect(d.reject)
        lay.addWidget(btns)

        if d.exec() != QDialog.Accepted:
            return

        added = 0
        for i in range(tree.topLevelItemCount()):
            it = tree.topLevelItem(i)
            if it.checkState(0) != Qt.Checked:
                continue
            data = it.data(0, Qt.UserRole) or {}
            content = data.get("content", "").strip()
            if not content:
                continue
            quote = data.get("quote", "").strip()
            notes = f"AI 引用原文：{quote}" if quote else ""
            add_item(
                self.work,
                content,
                planted_chapter=self.current_chapter_id or "",
                notes=notes,
            )
            added += 1

        if added:
            QMessageBox.information(dlg, "完成", f"已加入 {added} 条伏笔。")
            dlg.reload()

    # ▼ 块 D ▼
    # ---------- AI 改写 ----------
    def open_rewrite_dialog(self):
        if not self.current_chapter_id:
            QMessageBox.information(self, "AI 改写", "请先打开一个章节。")
            return
        editor = self.editor
        if not editor.has_selection():
            QMessageBox.information(self, "AI 改写", "请先在编辑器里选中一段文字。")
            return
        selected = editor.selected_text().replace("\u2029", "\n")
        if not selected.strip():
            QMessageBox.information(self, "AI 改写", "选中的内容为空。")
            return
        c = editor.edit.textCursor()
        self._rewrite_range = (c.selectionStart(), c.selectionEnd())
        # 保存原始选中文本：AI 生成期间用户可能继续编辑，apply 时要校验
        # 该位置内容是否仍是当初选中的那段——否则会替换错误区域。
        self._rewrite_original = selected
        # 记住发起章节：对话框是非模态的，用户可能在生成期间切到别的
        # 章节，应用时必须校验章节身份，防止把 A 章结果写进 B 章。
        self._rewrite_cid = self.current_chapter_id

        from ui.rewrite_dialog import RewriteDialog

        dlg = getattr(self, "_rewrite_dlg", None)
        if dlg is not None:
            try:
                dlg.close()
            except RuntimeError:
                pass
        theme = getattr(editor, "_theme", {}) or {}
        dlg = RewriteDialog(selected, self, theme=theme)
        dlg.apply_text.connect(self._apply_rewrite)
        self._rewrite_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _close_rewrite_dialog(self):
        """切章/切作品/回画廊时关闭非模态改写框，避免跨章误应用。"""
        dlg = getattr(self, "_rewrite_dlg", None)
        if dlg is not None:
            try:
                dlg.close()
            except RuntimeError:
                pass
            self._rewrite_dlg = None
        self._rewrite_range = None
        self._rewrite_original = ""
        self._rewrite_cid = None

    def _apply_rewrite(self, new_text: str):
        rng = getattr(self, "_rewrite_range", None)
        original = getattr(self, "_rewrite_original", "")
        if not rng:
            return
        # 章节身份守卫：发起改写后若切换了章节，拒绝写入当前章
        rewrite_cid = getattr(self, "_rewrite_cid", None)
        if rewrite_cid and rewrite_cid != self.current_chapter_id:
            QMessageBox.warning(
                self, "未替换",
                "发起 AI 改写后已切换到其他章节。为避免误覆盖当前章节正文，\n"
                "本次替换未执行；请切回原章节后重新发起。")
            return
        edit = self.editor.edit
        start, end = rng
        doc = edit.document()
        n = doc.characterCount() - 1
        start = max(0, min(start, n))
        end = max(start, min(end, n))

        # 校验：AI 生成期间用户可能编辑过。用当时保存的选区文本与当前位置
        # 的内容比对——不一致说明位置已失效，替换会吃掉别的内容。
        current = edit.toPlainText()[start:end]
        original_normalized = original.replace("\u2029", "\n")
        if current != original_normalized:
            ret = QMessageBox.question(
                self,
                "选区已变化",
                "发起 AI 改写后，选中的文字已被编辑或移动。\n\n"
                "继续替换会把 AI 结果写入当前该位置的内容，"
                "可能覆盖你刚写的文字。\n\n"
                "建议：取消，回到编辑器重新选中后再发起改写。\n\n"
                "是否仍然继续替换？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                # 保留 _rewrite_range / _rewrite_original，让用户可以：
                #   1. 回编辑器重新选中（会通过 open_rewrite_dialog 更新）
                #   2. 或再次点「应用」重试（比如刚才误触了别的键又撤销回来）
                # 之前清空状态会让 RewriteDialog 的后续「应用」全部失效，
                # 用户以为软件失灵。保留状态是安全的——下次 _apply_rewrite
                # 会再次做"当前内容 == 原选区"校验，不匹配还会弹窗。
                return

        from PySide6.QtGui import QTextCursor

        c = edit.textCursor()
        c.beginEditBlock()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        c.insertText(new_text)
        c.endEditBlock()
        edit.setTextCursor(c)
        self._draft_dirty = True
        self._save_debounce.start()
        self.save_state_label.setText("已应用 AI 改写")
        # 用完后清空，防止重复触发
        self._rewrite_range = None
        self._rewrite_original = ""
        self._rewrite_cid = None

    def _replace_editor_range(self, start: int, end: int, text: str,
                              cid=None, expected: str = ""):
        """用 AI 结果替换指定区间（墨灵气泡「替换选区」入口）。

        安全校验：
        - 必须有当前章节；
        - cid 与发起请求时一致（防止 A 章提问、切到 B 章后误覆盖）；
        - 区间当前文本与发起时一致，否则用户继续编辑过 → 二次确认；
        - 替换前先打历史快照，可在「历史版本」里找回。
        """
        if not self.current_chapter_id:
            return
        from PySide6.QtGui import QTextCursor

        edit = self.editor.edit
        doc = edit.document()
        n = doc.characterCount() - 1
        start = max(0, min(start, n))
        end = max(start, min(end, n))

        # 章节已切换：直接拒绝
        if cid is not None and cid != self.current_chapter_id:
            QMessageBox.warning(
                self, "未替换",
                "提问后已切换到其他章节。为避免误覆盖当前章节正文，\n"
                "本次替换未执行；请切回原章节后重新发起。")
            return

        # 选区漂移检测：取区间当前文本与发起时对比
        if expected:
            probe = edit.textCursor()
            probe.setPosition(start)
            probe.setPosition(end, QTextCursor.KeepAnchor)
            current = probe.selectedText().replace("\u2029", "\n")
            if current != expected:
                reply = QMessageBox.question(
                    self, "选区内容已变化",
                    "发起请求后该区间的正文已被编辑过。\n\n"
                    "仍要用 AI 结果替换「当前选区」吗？\n"
                    "（替换前会自动保存一份历史快照）",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply != QMessageBox.Yes:
                    return

        # 替换前快照兜底
        try:
            work = getattr(self, "work", None)
            if work is not None:
                work.create_snapshot(self.current_chapter_id)
        except Exception as e:
            logger.warning(f"替换前快照失败: {e}")

        c = edit.textCursor()
        c.beginEditBlock()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        c.insertText(text)
        c.endEditBlock()
        edit.setTextCursor(c)
        self._draft_dirty = True
        self._save_debounce.start()
        self.save_state_label.setText("已替换 AI 结果")

    # ---------- AI 梗概 ----------
    def generate_chapter_summary(self):
        if not self.current_chapter_id:
            QMessageBox.information(self, "AI 梗概", "请先打开一个章节。")
            return
        from utils.ai_client import has_api_key, load_ai_config, AIWorker

        if not has_api_key():
            QMessageBox.information(
                self, "AI 梗概", "尚未配置 API Key。请在 设置 → AI 接口 中配置后重试。"
            )
            return
        self.flush_save()
        text = self.editor.get_text().strip()
        if not text:
            QMessageBox.information(self, "AI 梗概", "当前章节没有内容。")
            return

        existing = ""
        if self.work:
            try:
                existing = self.work.read_note(self.current_chapter_id)
            except Exception:
                existing = ""

        prompt = (
            "你是小说编辑。请用 100 字以内的简体中文，"
            "为以下这一章内容写一段梗概，供作者本人在大纲面板查看。\n\n"
            "要求：\n"
            "- 客观陈述这一章发生了什么（人物、事件、结果）\n"
            "- 不要评价、不要客套、不要分点\n"
            "- 只输出梗概正文，不要任何前缀后缀\n"
        )
        if existing.strip():
            prompt += (
                f"\n（作者之前写过的梗概，供参考，可覆盖）\n{existing.strip()[:500]}\n"
            )
        prompt += f"\n本章正文：\n{text[:4000]}"

        cfg = load_ai_config()

        from ui.dialog_styles import AIWaitingDialog

        # 重入防护：上一轮梗概仍在后台跑（用户取消后请求可能未结束）时，
        # 不能覆盖 self._summary_worker（运行中线程失引用 → abort）。
        if self._ai_worker_busy("_summary_worker", "AI 梗概"):
            return

        # ---------- 第一段：等待 AI 返回 ----------
        worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            temperature=0.4,
            # stream=True：非流式请求会阻塞到服务端返回，中途无法响应
            # 「取消」按钮；改流式后 worker 会在下一个 chunk 到达时
            # 检查 _stop 标志并退出（通常 < 1 秒）。
            stream=True,
            timeout=120.0,
        )
        wait_dlg = AIWaitingDialog(self, title="正在生成梗概")
        wait_dlg.attach(worker, owner=self, owner_attr="_summary_worker")
        worker.start()
        wait_dlg.exec()
        # 关对话框后不 wait：worker 已挂 self._summary_worker，
        # 用户点取消时 stop() 已设标志，worker 在下一个 chunk 到达时退出。

        if wait_dlg.cancelled:
            self.save_state_label.setText("已取消 AI 梗概")
            return
        if wait_dlg.error:
            QMessageBox.critical(self, "AI 梗概失败", wait_dlg.error)
            return

        new_summary = wait_dlg.text.strip()
        if not new_summary:
            QMessageBox.information(self, "AI 梗概", "AI 返回了空内容。")
            return

        # ---------- 第二段：编辑后写入 ----------
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 生成梗概")
        dlg.setMinimumSize(560, 400)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        title = QLabel("✓ 生成完成，可修改后写入")
        lay.addWidget(title)
        result_edit = QPlainTextEdit()
        result_edit.setPlainText(new_summary)
        lay.addWidget(result_edit, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        apply_btn = QPushButton("写入梗概")
        apply_btn.setProperty("variant", "primary")
        apply_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(apply_btn)
        lay.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return
        new_summary = result_edit.toPlainText().strip()
        if not new_summary:
            return

        try:
            self.work.write_note(self.current_chapter_id, new_summary)
            if hasattr(self.bookshelf.outline, "refresh_chapter_note_if_open"):
                self.bookshelf.outline.refresh_chapter_note_if_open(
                    self.current_chapter_id, new_summary
                )
        except Exception as e:
            QMessageBox.critical(self, "写入失败", str(e))
            return
        self.save_state_label.setText("已写入本章梗概")

    # ---------- AI 提取词条 ----------
    def extract_entities_with_ai(self):
        """用 AI 扫当前章节，提取新角色/地名/技能，让用户勾选后入库。

        只处理**当前章节**（截断到 6000 字），不做全书扫描。
        """
        if not self.current_chapter_id or not self.work:
            QMessageBox.information(self, "AI 提取", "请先打开一个章节。")
            return
        from utils.ai_client import has_api_key, load_ai_config, AIWorker

        if not has_api_key():
            QMessageBox.information(
                self, "AI 提取", "尚未配置 API Key。请在 设置 → AI 接口 中配置后重试。"
            )
            return
        self.flush_save()
        text = self.editor.get_text().strip()
        if not text:
            QMessageBox.information(self, "AI 提取", "当前章节没有内容。")
            return

        # 已存在的名字（避免重复提示）
        existing_char_names = set()
        try:
            for c in self.work.load_characters():
                n = (c.get("name") or "").strip()
                if n:
                    existing_char_names.add(n)
        except Exception:
            pass
        existing_words = set()
        try:
            wb = self.work.load_wordbook() or {}
            for cat in wb.get("categories", []):
                for w in cat.get("words", []):
                    n = (w.get("word") if isinstance(w, dict) else w) or ""
                    n = str(n).strip()
                    if n:
                        existing_words.add(n)
        except Exception:
            pass

        prompt = (
            "你是一个小说编辑助手。请从以下章节正文中提取三类命名实体：\n"
            "1. 人名 / 角色名（characters）\n"
            "2. 地名 / 势力名（locations）\n"
            "3. 技能名 / 招式名 / 法宝名（skills）\n\n"
            "要求：\n"
            "- 只输出 JSON，不要任何解释，不要 markdown 代码块标记\n"
            '- 每个条目形如 {"name": "...", "note": "一句话简短说明"}\n'
            "- 不要提取已有的常见词；只列本章新出现的、值得记录的名称\n"
            "- 没有的类别返回空数组\n\n"
            "格式严格为：\n"
            '{"characters": [...], "locations": [...], "skills": [...]}\n\n'
            f"章节正文：\n{text[:6000]}"
        )

        cfg = load_ai_config()

        # ---------- 等待 AI 返回 ----------
        from ui.dialog_styles import AIWaitingDialog

        # 重入防护：避免覆盖仍在跑的提取线程（运行中析构 → abort）
        if self._ai_worker_busy("_entities_worker", "AI 提取词条"):
            return

        worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            # temperature=0：结构化提取不需要创造性，
            # 同一段文本多次运行结果稳定。
            temperature=0.0,
            # stream=True：非流式请求会阻塞到服务端返回，中途无法响应
            # 「取消」按钮；改流式后 worker 会在下一个 chunk 到达时退出。
            stream=True,
            timeout=120.0,
        )
        wait_dlg = AIWaitingDialog(self, title="正在提取新词条")
        wait_dlg.attach(worker, owner=self, owner_attr="_entities_worker")
        worker.start()
        wait_dlg.exec()

        if wait_dlg.cancelled:
            self.save_state_label.setText("已取消 AI 提取")
            return
        if wait_dlg.error:
            QMessageBox.critical(self, "AI 提取失败", wait_dlg.error)
            return

        entities = self._parse_entities(wait_dlg.text)
        if not entities or not any(entities.values()):
            QMessageBox.information(self, "AI 提取", "AI 没有找到可提取的新词条。")
            return

        for key in ("characters", "locations", "skills"):
            cur = entities.get(key) or []
            entities[key] = [
                it
                for it in cur
                if it.get("name") not in existing_char_names
                and it.get("name") not in existing_words
            ]

        from ui.extract_entities_dialog import ExtractEntitiesDialog

        theme = getattr(self.editor, "_theme", {}) or {}
        dlg2 = ExtractEntitiesDialog(entities, self, theme=theme)
        if dlg2.exec() != QDialog.Accepted:
            return
        sel = dlg2.get_selected()
        self._apply_extracted(sel)

    # ---------- 提取人物关系（本地规则优先，可追加 AI 深挖） ----------
    def extract_relations_with_ai(self):
        """从**当前章节**提取人物关系，勾选后写入人物卡。

        D 批次改造成两层：
        1. 先用本地规则（utils/relation_rules.py）零 token 扫描，
           称谓句式里白纸黑字写明的关系立刻呈现，没配 Key 也能用；
        2. 用户可点「用 AI 深挖本章」，走 AI 跨句推断，
           结果去重后追加到同一张列表，统一勾选写入。
        """
        if not self.current_chapter_id or not self.work:
            QMessageBox.information(self, "提取人物关系", "请先打开一个章节。")
            return

        self.flush_save()
        text = self.editor.get_text().strip()
        if not text:
            QMessageBox.information(self, "提取人物关系", "当前章节没有内容。")
            return

        try:
            characters = self.work.load_characters() or []
        except Exception:
            characters = []
        char_names = [
            (c.get("name") or "").strip()
            for c in characters
            if (c.get("name") or "").strip()
        ]

        from utils.relation_rules import extract_local
        local_rels = extract_local(text, characters)

        from ui.relation_extract_dialog import RelationExtractDialog
        theme = getattr(self.editor, "_theme", {}) or {}
        dlg2 = RelationExtractDialog(
            local_rels, set(char_names), self, theme=theme)
        dlg2.set_ai_deepen(
            lambda: self._deepen_relations_ai(text, char_names))
        if dlg2.exec() != QDialog.Accepted:
            return
        sel = dlg2.get_selected()
        self._apply_extracted_relations(sel)

    def _deepen_relations_ai(self, text: str, char_names: list):
        """AI 深挖当前章关系（由 RelationExtractDialog 的按钮调用）。

        返回提取到的关系列表；取消/失败返回 None，空结果返回 []。
        """
        from utils.ai_client import has_api_key, load_ai_config, AIWorker

        if not has_api_key():
            QMessageBox.information(
                self, "AI 深挖",
                "尚未配置 API Key。请在 设置 → AI 接口 中配置后重试。\n"
                "（本地规则识别的关系不花 token，仍可直接勾选写入。）")
            return None

        from utils.relation_extract import build_prompt, parse_response
        prompt = build_prompt(text, char_names)
        cfg = load_ai_config()

        # ---------- 等待 AI 返回（通用组件） ----------
        from ui.dialog_styles import AIWaitingDialog

        # 重入防护：避免覆盖仍在跑的关系提取线程（运行中析构 → abort）
        if self._ai_worker_busy("_relation_worker", "AI 提取人物关系"):
            return None

        worker = AIWorker(
            [{"role": "user", "content": prompt}],
            cfg["api_key"],
            cfg["base_url"],
            cfg["model"],
            # temperature=0：结构化提取不需要创造性，
            # 同一段文本多次运行结果稳定。
            temperature=0.0,
            # stream=True：非流式请求会阻塞到服务端返回，中途无法响应
            # 「取消」按钮；改流式后 worker 会在下一个 chunk 到达时退出。
            stream=True,
            timeout=120.0,
        )
        wait_dlg = AIWaitingDialog(self, title="正在用 AI 深挖人物关系")
        # owner + owner_attr：worker 挂到 self._relation_worker，
        # 用户点取消后函数返回，worker 不会被 GC——QThread 还在跑就 GC
        # 会触发 "Destroyed while running" 并 abort 进程。
        wait_dlg.attach(worker, owner=self, owner_attr="_relation_worker")
        worker.start()
        wait_dlg.exec()
        # 关键：**不在主线程 wait**。
        # 之前 worker.wait(3000) 会阻塞 GUI 3 秒——这是"取消卡死"的
        # 直接原因。现在 worker 挂 self 上，不会 GC，让它后台自然结束：
        # - 用户点取消：worker 在下一个 chunk 到达时（通常 < 1 秒）退出
        # - 服务端迟迟不返回：worker 会等到 timeout（120 秒）后自己结束
        # 两种情况都不影响主线程，UI 立刻恢复响应。

        if wait_dlg.cancelled:
            self.save_state_label.setText("已取消 AI 深挖")
            return None
        if wait_dlg.error:
            QMessageBox.critical(self, "AI 深挖失败", wait_dlg.error)
            return None

        relations = parse_response(wait_dlg.text)
        for r in relations:
            r["source"] = "ai"
        return relations

    def _apply_extracted_relations(self, relations):
        """把 AI 提取（用户勾选后）的关系写入人物卡。

        规则：
        - from 不存在人物卡时自动创建空白卡（只填名字）
        - 关系追加到 from 的 relations 字段，每行 "关系：目标"
        - 已存在的相同关系行会跳过（去重）
        """
        if not self.work or not relations:
            return
        import uuid as _uuid

        try:
            data = self.work.load_characters()
        except Exception as e:
            QMessageBox.critical(self, "写入失败", f"读取人物卡失败：{e}")
            return

        by_name = {c.get("name"): c for c in data if c.get("name")}
        created = 0
        added = 0
        skipped = 0

        def _ensure_card(name):
            nonlocal created
            if name in by_name:
                return by_name[name]
            card = {
                "id": _uuid.uuid4().hex[:12],
                "name": name,
                "aliases": [],
                "role": "",
                "gender": "",
                "age": "",
                "appearance": "",
                "personality": "",
                "background": "",
                "relations": "",
                "first_appear": "",
                "notes": "",
            }
            data.append(card)
            by_name[name] = card
            created += 1
            return card

        for r in relations:
            f = (r.get("from") or "").strip()
            rel = (r.get("rel") or "").strip()
            to = (r.get("to") or "").strip()
            if not (f and rel and to):
                continue
            card = _ensure_card(f)

            # 解析已有关系行，按"关系：目标"整行去重
            existing_lines = [
                ln.strip()
                for ln in (card.get("relations") or "").splitlines()
                if ln.strip()
            ]
            new_line = f"{rel}：{to}"
            if new_line in existing_lines:
                skipped += 1
                continue

            existing_lines.append(new_line)
            card["relations"] = "\n".join(existing_lines)
            added += 1

        if added == 0 and created == 0:
            QMessageBox.information(self, "完成", "没有新增任何关系。")
            return

        try:
            self.work.save_characters(data)
        except OSError as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return

        # 刷新人物面板
        if hasattr(self.bookshelf, "characters"):
            self.bookshelf.characters._reload()

        parts = []
        if added:
            parts.append(f"新增 {added} 条关系")
        if created:
            parts.append(f"自动创建 {created} 张角色卡")
        if skipped:
            parts.append(f"跳过 {skipped} 条重复")
        QMessageBox.information(self, "完成", "，".join(parts) + "。")

    @staticmethod
    def _parse_entities(text: str) -> dict:
        import json
        import re

        if not text:
            return {}
        s = text.strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\s*\n?", "", s)
            s = re.sub(r"\n?```\s*$", "", s)
        data = None
        try:
            data = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\{[\s\S]*\}", s)
            if m:
                try:
                    data = json.loads(m.group(0))
                except (json.JSONDecodeError, ValueError):
                    data = None
        if not isinstance(data, dict):
            return {}
        out = {"characters": [], "locations": [], "skills": []}
        for key in out:
            items = data.get(key) or []
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, str):
                    if it.strip():
                        out[key].append({"name": it.strip(), "note": ""})
                elif isinstance(it, dict):
                    name = (it.get("name") or it.get("word") or "").strip()
                    if name:
                        out[key].append(
                            {
                                "name": name,
                                "note": (
                                    it.get("note") or it.get("desc") or ""
                                ).strip(),
                            }
                        )
        return out

    def _apply_extracted(self, sel: dict):
        if not self.work:
            return
        import uuid as _uuid

        n_chars = n_words = 0
        chars = sel.get("characters") or []
        if chars:
            try:
                data = self.work.load_characters()
                existing = {c.get("name") for c in data if c.get("name")}
                for name in chars:
                    if name in existing:
                        continue
                    data.append(
                        {
                            "id": _uuid.uuid4().hex[:12],
                            "name": name,
                            "aliases": [],
                            "role": "",
                            "gender": "",
                            "age": "",
                            "appearance": "",
                            "personality": "",
                            "background": "",
                            "relations": "",
                            "first_appear": "",
                            "notes": "",
                        }
                    )
                    n_chars += 1
                if n_chars:
                    self.work.save_characters(data)
                    if hasattr(self.bookshelf, "characters"):
                        self.bookshelf.characters._reload()
            except Exception as e:
                logger.warning(f"写入人物卡失败: {e}")

        cat_map = {"locations": "地名势力", "skills": "技能名称"}
        wb = None
        try:
            wb = self.work.load_wordbook() or {"categories": []}
        except Exception:
            wb = {"categories": []}
        cats = wb.setdefault("categories", [])
        for key, cat_name in cat_map.items():
            names = sel.get(key) or []
            if not names:
                continue
            cat = None
            for c in cats:
                if c.get("name") == cat_name:
                    cat = c
                    break
            if cat is None:
                cat = {"name": cat_name, "words": []}
                cats.append(cat)
            existing = set()
            for w in cat.get("words", []):
                n = (w.get("word") if isinstance(w, dict) else w) or ""
                if n:
                    existing.add(str(n).strip())
            for name in names:
                if name in existing:
                    continue
                cat.setdefault("words", []).append({"word": name, "desc": ""})
                n_words += 1
        if n_words:
            try:
                self.work.save_wordbook(wb)
                if hasattr(self.bookshelf, "wordbook"):
                    self.bookshelf.wordbook.set_work(self.work)
            except Exception as e:
                logger.warning(f"写入高频词失败: {e}")

        parts = []
        if n_chars:
            parts.append(f"新增 {n_chars} 个角色")
        if n_words:
            parts.append(f"新增 {n_words} 个词条")
        QMessageBox.information(
            self, "完成", "\n".join(parts) if parts else "没有新增任何条目。"
        )

    # ---------- 质量中心（已落盘报告的回看/删除） ----------
    def open_quality_center(self):
        if not self.work:
            QMessageBox.information(self, "质量中心", "请先打开一部作品。")
            return
        from ui.quality_center_dialog import QualityCenterDialog
        theme = getattr(self.editor, "_theme", {}) or {}
        dlg = QualityCenterDialog(
            self.work, self, theme=theme,
            on_jump=lambda cid: self.open_chapter(cid),
            current_chapter_id=getattr(self, "current_chapter_id", "") or "",
            on_locate=self._locate_in_editor)
        self._quality_center_dlg = dlg
        dlg.exec()
        self._quality_center_dlg = None

    # ---------- 墨灵试读（读者视角逐章追读 + 总评） ----------
    def open_beta_reading(self):
        """选一个章节区间，墨灵以追更读者身份逐章读完，
        每章给追读欲/开头钩子/结尾钩子/短感受，读完再汇总总评。

        与"追读体检"的区别：体检是单章编辑视角（规则+AI 10 维诊断），
        试读是连续追更的读者视角，跨章汇总弃书风险与节奏问题。
        """
        if not self.work:
            QMessageBox.information(self, "墨灵试读", "请先打开一部作品。")
            return
        from utils.ai_client import has_api_key
        if not has_api_key():
            QMessageBox.information(
                self, "墨灵试读",
                "尚未配置 API Key。请在 设置 → AI 接口 中配置后重试。")
            return

        chapters = list(getattr(self.work, "chapters", []) or [])
        if not chapters:
            QMessageBox.information(self, "墨灵试读", "这部作品还没有章节。")
            return

        from ui.chapter_range_dialog import ChapterRangeDialog
        theme = getattr(self.editor, "_theme", {}) or {}
        rng = ChapterRangeDialog(
            self.work, self, theme=theme,
            title="墨灵试读 · 选择章节区间",
            per_chapter_cap=3800,
            current_chapter_id=getattr(self, "current_chapter_id", "") or "",
        )
        if rng.exec() != QDialog.Accepted:
            return
        selected = rng.selected_chapters()
        if not selected:
            return

        from utils import beta_reading as br
        if len(selected) > br.MAX_CHAPTERS:
            QMessageBox.information(
                self, "墨灵试读",
                f"一次最多试读 {br.MAX_CHAPTERS} 章，请缩小区间（可分多次试读）。")
            return

        # 读正文 + 零 token 本地统计；空章不发请求
        entries = []
        readable = []          # (entry, chapter, text)
        total_est = 0
        for i, ch in enumerate(selected, 1):
            try:
                text = self.work.read_chapter(ch.get("id")) or ""
            except Exception as e:
                logger.warning(f"墨灵试读读取章节失败 {ch.get('id')}: {e}")
                text = ""
            stats = br.local_stats(text)
            entry = {
                "index": i,
                "chapter_id": ch.get("id", ""),
                "title": ch.get("title", "") or f"第{i}章",
                "stats": stats,
                "reaction": None,
                "skipped": not text.strip(),
            }
            entries.append(entry)
            if text.strip():
                readable.append((entry, ch, text))
                total_est += br.estimate_chars(text)

        if not readable:
            QMessageBox.information(self, "墨灵试读", "选定章节都还没有正文。")
            return

        n = len(readable)
        total_ch = len(selected)
        total_est += min(200 * n, 3000)      # 总评请求粗估
        empty_n = total_ch - n

        reply = QMessageBox.question(
            self, "确认开始试读",
            f"墨灵将按顺序试读 {total_ch} 章"
            f"（{empty_n} 章无正文自动跳过，实际发送 {n} 章）。\n\n"
            f"预计发送正文约 {total_est:,} 字，共 {n + 1} 次 AI 请求"
            f"（含 1 次总评）。\n\n"
            f"处理逐章进行，可随时取消，已读完的章节结果保留。\n"
            f"确定开始吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        from ui.batch_runner import BatchAIProgress, BatchTask

        work_title = getattr(self.work, "title", "") or "未命名作品"
        reactions = {}        # entry index -> 已解析的本章短评

        def _make_chapter(entry, ch, text):
            def _build():
                prev = []
                for j in range(1, entry["index"]):
                    r = reactions.get(j)
                    if r:
                        prev.append(br.digest_line(
                            j, entries[j - 1]["title"], r))
                return br.build_chapter_messages(
                    work_title, entry["index"], total_ch,
                    entry["title"], text, "\n".join(prev))
            return _build

        tasks = []
        for entry, ch, text in readable:
            tasks.append(BatchTask(
                label=f"第{entry['index']}章 {entry['title']}",
                messages=_make_chapter(entry, ch, text),
                temperature=0.6,
                timeout=120.0,
            ))

        def _build_summary():
            lines = []
            for e in entries:
                r = reactions.get(e["index"])
                if r:
                    lines.append(br.digest_line(e["index"], e["title"], r))
            return br.build_summary_messages(work_title, total_ch, lines)

        tasks.append(BatchTask(
            label="汇总试读总评",
            messages=_build_summary,
            temperature=0.4,
            timeout=120.0,
        ))

        runner = BatchAIProgress(
            tasks, parent=self, owner=self,
            title="墨灵试读中",
            hint="墨灵正在按顺序追读，单章失败会自动跳过继续。")
        runner.exec()

        for k, (ok, val) in enumerate(runner.results):
            if k >= n:
                break
            entry = readable[k][0]
            if ok:
                parsed = br.parse_chapter_response(val)
                if parsed:
                    entry["reaction"] = parsed
                    reactions[entry["index"]] = parsed
            else:
                entry["error"] = (val or "请求失败")[:120]

        summary = {}
        if len(runner.results) > n and runner.results[n][0]:
            summary = br.parse_summary_response(runner.results[n][1]) or {}

        if runner.cancelled and runner.success_count() == 0:
            self.save_state_label.setText("已取消墨灵试读")
            return

        # 报告自动落盘到作品目录 quality_reports/，可在「质量中心」回看
        saved_id = ""
        try:
            from utils import quality_reports as qr
            avg_want = None
            wants = [e["reaction"].get("want") for e in entries
                     if (e.get("reaction") or {}).get("want")]
            if wants:
                avg_want = round(sum(wants) / len(wants))
            span = (f"第1–{total_ch}章" if total_ch > 1
                    else f"第1章 · {entries[0]['title'] if entries else ''}")
            rec = qr.make_record(
                qr.KIND_BETA,
                f"墨灵试读 · {span}",
                {"entries": entries, "summary": summary},
                score=avg_want,
            )
            qr.save_report(self.work.root_dir, rec)
            qr.prune_reports(self.work.root_dir)
            saved_id = rec["id"]
        except Exception as e:
            logger.warning(f"墨灵试读报告保存失败（不影响本次查看）: {e}")

        from ui.beta_reading_dialog import BetaReportDialog
        report = BetaReportDialog(
            entries, summary, self, theme=theme,
            on_jump=lambda cid: self.open_chapter(cid),
            saved=bool(saved_id))
        report.exec()

    # ---------- 批量提取人物关系（区间 / 全书） ----------
    def batch_extract_relations(self):
        """选定章节区间，逐章跑 L0 本地规则（零 token），
        可选再逐章叠加 L1 AI 深挖；全部结果跨章聚合去重后一次勾选写入。

        聚合规则：同一关系边（对称关系反向也算同一条）只留一行，
        标注出现章节数，证据保留首次出现的原文。
        """
        BATCH_MAX = 50
        AI_CLIP = 6000       # 与 relation_extract.build_prompt 的截断一致

        if not self.work:
            QMessageBox.information(self, "批量提取", "请先打开一部作品。")
            return

        chapters = list(getattr(self.work, "chapters", []) or [])
        if not chapters:
            QMessageBox.information(self, "批量提取", "这部作品还没有章节。")
            return

        try:
            characters = self.work.load_characters() or []
        except Exception:
            characters = []
        char_names = [
            (c.get("name") or "").strip()
            for c in characters
            if (c.get("name") or "").strip()
        ]

        from ui.chapter_range_dialog import ChapterRangeDialog
        theme = getattr(self.editor, "_theme", {}) or {}
        rng = ChapterRangeDialog(
            self.work, self, theme=theme,
            title="批量提取人物关系 · 选择区间",
            per_chapter_cap=AI_CLIP,
            current_chapter_id=getattr(self, "current_chapter_id", "") or "",
            default_all=True,
            note="可先用本地规则免费扫一遍，再决定是否逐章花 token 让 AI 深挖。")
        if rng.exec() != QDialog.Accepted:
            return
        selected = rng.selected_chapters()
        if not selected:
            return
        if len(selected) > BATCH_MAX:
            QMessageBox.information(
                self, "批量提取",
                f"一次最多处理 {BATCH_MAX} 章，请缩小区间（可分批处理）。")
            return

        # ---- 读正文 + L0 本地提取（逐章即时累积，零 token） ----
        from utils.relation_rules import extract_local
        readable = []        # (序号, 章dict, text, local_rels)
        for i, ch in enumerate(selected, 1):
            try:
                text = self.work.read_chapter(ch.get("id")) or ""
            except Exception as e:
                logger.warning(f"批量关系读取章节失败 {ch.get('id')}: {e}")
                continue
            if not text.strip():
                continue
            local_rels = extract_local(text, characters)
            for r in local_rels:
                r["source"] = "local"
            readable.append((i, ch, text, local_rels))

        if not readable:
            QMessageBox.information(self, "批量提取", "选定章节都还没有正文。")
            return

        est_send = sum(min(len(t), AI_CLIP) for _, _, t, _ in readable)

        # ---- 三选一：AI 深挖 / 仅本地 / 取消 ----
        box = QMessageBox(self)
        box.setWindowTitle("批量提取方式")
        box.setText(
            f"共 {len(readable)} 章有正文，本地规则已先行扫描。\n\n"
            f"· 本地 + AI 深挖：在本地结果上再逐章 AI 跨句推断，"
            f"预计发送约 {est_send:,} 字、{len(readable)} 次请求，"
            f"逐章进行可取消；\n"
            f"· 仅本地规则：不花 token，只识别称谓句式里写明的关系。")
        b_ai = box.addButton(
            f"本地 + AI 深挖（{len(readable)} 次请求）",
            QMessageBox.AcceptRole)
        b_local = box.addButton("仅本地规则（零 token）", QMessageBox.NoRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_ai:
            use_ai = True
        elif clicked is b_local:
            use_ai = False
        else:
            return

        ai_by_chapter = {}    # 章节序号 -> ai relations
        if use_ai:
            from utils.ai_client import has_api_key
            if not has_api_key():
                QMessageBox.information(
                    self, "批量提取",
                    "尚未配置 API Key。请在 设置 → AI 接口 中配置后"
                    "重试，或选择「仅本地规则」。")
                return

            from utils.relation_extract import build_prompt, parse_response
            from ui.batch_runner import BatchAIProgress, BatchTask

            tasks = [
                BatchTask(
                    label=f"第{i}章 {ch.get('title', '')}",
                    messages=[{"role": "user",
                               "content": build_prompt(text, char_names)}],
                    temperature=0.0,
                    timeout=120.0,
                )
                for i, ch, text, _ in readable
            ]
            runner = BatchAIProgress(
                tasks, parent=self, owner=self,
                title="批量提取人物关系中",
                hint="逐章扫描中，单章失败会自动跳过，可随时取消。")
            runner.exec()

            for k, (ok, val) in enumerate(runner.results):
                if ok:
                    rels = parse_response(val)
                    for r in rels:
                        r["source"] = "ai"
                    ai_by_chapter[readable[k][0]] = rels

            if runner.cancelled and runner.success_count() == 0:
                self.save_state_label.setText("已取消批量提取")
                return

        # ---- 跨章聚合去重 ----
        from ui.relation_extract_dialog import _edge_key

        merged = {}           # edge key -> 聚合关系
        for i, ch, _t, local_rels in readable:
            rels = list(local_rels)
            rels.extend(ai_by_chapter.get(i, []))
            ch_title = ch.get("title", "") or f"第{i}章"
            for r in rels:
                key = _edge_key(r.get("rel", ""), r.get("from", ""),
                                r.get("to", ""))
                agg = merged.get(key)
                src = "batch" if r.get("source") == "ai" else "local"
                if agg is None:
                    merged[key] = {
                        "from": r.get("from", ""),
                        "rel": r.get("rel", ""),
                        "to": r.get("to", ""),
                        "evidence": r.get("evidence", "") or "",
                        "source": src,
                        "_chapters": [ch_title],
                    }
                else:
                    agg["_chapters"].append(ch_title)
                    if src == "batch":
                        agg["source"] = "batch"
                        if not agg.get("evidence") and r.get("evidence"):
                            agg["evidence"] = r["evidence"]

        agg_rels = []
        for agg in merged.values():
            chs = agg.pop("_chapters")
            uniq = []
            for c in chs:
                if c not in uniq:
                    uniq.append(c)
            agg["chapter_hint"] = f"{len(uniq)}章"
            # 原文引用列补一句出现在哪些章（至多 3 个章名）
            where = "、".join(uniq[:3]) + ("…" if len(uniq) > 3 else "")
            base_ev = agg.get("evidence") or ""
            agg["evidence"] = (
                f"{base_ev}（见于 {where}）" if base_ev else f"见于 {where}")
            agg_rels.append(agg)

        # AI 命中排在前面，其次按出现章数降序
        agg_rels.sort(
            key=lambda r: (0 if r["source"] == "batch" else 1,
                           -int(r["chapter_hint"].rstrip("章"))))

        from ui.relation_extract_dialog import RelationExtractDialog
        dlg2 = RelationExtractDialog(
            agg_rels, set(char_names), self, theme=theme)
        if dlg2.exec() != QDialog.Accepted:
            return
        sel = dlg2.get_selected()
        self._apply_extracted_relations(sel)


