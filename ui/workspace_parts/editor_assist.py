# ui/workspace_parts/editor_assist.py
"""墨码写作 - 编辑辅助子系统（Mixin）

从 workspace.py 拆出的：
- 校对（单章 / 全书 / 词库管理）
- 查找替换浮条 + 高亮 + 跳转
- 发布前体检（排版 / 校对 / 追读 / 一致性四合一）
- 词条高亮（人物卡 + 高频词在正文中着色）
- 一致性检查（同人不同名）
- 冲刺计划
- 平台格式一键

按 Mixin 方式合并到 WritingWorkspace。
"""

import os
import json

from PySide6.QtWidgets import QMessageBox, QApplication, QProgressDialog
from PySide6.QtCore import Qt, QTimer, QUrl, QThread, Signal
from PySide6.QtGui import QDesktopServices
import qtawesome as qta

from ui.proofread_dialog import ProofreadDialog
from utils.logger import get_logger

logger = get_logger()


class _ProofreadAllWorker(QThread):
    """全书校对后台线程：长篇逐章扫描不再冻结界面。"""

    progress = Signal(int, int, str)          # done, total, 章节名
    finished_ok = Signal(list, bool)          # 结果, 是否被取消
    failed = Signal(str)

    def __init__(self, work, data_root, parent=None):
        super().__init__(parent)
        self._work = work
        self._data_root = data_root
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def stop(self):
        # 与其他 worker 统一：on_shutdown 会调 stop()
        self._cancel = True

    def run(self):
        try:
            from utils.proofreader import check_work

            def cb(done, total, title):
                self.progress.emit(done, total, title)
                return not self._cancel

            results = check_work(self._work, self._data_root, progress_cb=cb)
            self.finished_ok.emit(results, self._cancel)
        except Exception as e:
            logger.error(f"全书校对失败: {e}", exc_info=True)
            self.failed.emit(str(e))


class EditorAssistMixin:
    """编辑辅助子系统。合并到 WritingWorkspace。"""

    # ▼ 块 1 ▼
    # ---------- 校对 ----------
    def open_proofread(self):
        if not self.current_chapter_id:
            QMessageBox.information(self, "校对", "请先打开一个章节。")
            return
        self.flush_save()
        text = self.editor.get_text()
        import utils.work_store as ws_mod

        data_root = ws_mod.get_data_root()
        from utils.proofreader import check_text

        results = check_text(text, data_root)
        if not results:
            self._clear_proofread()
            QMessageBox.information(
                self,
                "校对完成",
                "本章未发现错别字或敏感词。\n\n"
                "如需管理自定义词库，请在「工具」菜单中"
                "打开「校对词库管理」。",
            )
            return
        self._proof_results = results
        self._proof_filter = ""
        self.editor.highlight_proofread(results, filter_cat="")
        self.proof_bar.show_results(results)
        self.proof_bar.setVisible(True)
        self.proof_bar.raise_()
        self._reposition_proofbar()

    def _on_proof_filter_changed(self, cat: str):
        self._proof_filter = cat or ""
        self.editor.highlight_proofread(
            self._proof_results, filter_cat=self._proof_filter
        )

    def _open_proofread_detail(self):
        if not self._proof_results:
            return
        import utils.work_store as ws_mod

        data_root = ws_mod.get_data_root()
        dlg = ProofreadDialog(
            list(self._proof_results), self, data_root=data_root, whole_book=False
        )
        dlg.apply_fix.connect(self._apply_proof_fix)
        dlg.locate.connect(self._locate_in_editor)
        dlg.ignored_changed.connect(lambda: None)
        dlg.exec()
        self._refresh_proofread()

    def _refresh_proofread(self):
        if not self.current_chapter_id:
            self._clear_proofread()
            return
        self.flush_save()
        text = self.editor.get_text()
        import utils.work_store as ws_mod

        data_root = ws_mod.get_data_root()
        from utils.proofreader import check_text

        results = check_text(text, data_root)
        if not results:
            self._clear_proofread()
            return
        self._proof_results = results
        self.editor.highlight_proofread(results, filter_cat=self._proof_filter)
        self.proof_bar.show_results(results)
        self.proof_bar.setVisible(True)

    def _clear_proofread(self):
        self._proof_results = []
        self._proof_filter = ""
        if hasattr(self, "proof_bar"):
            self.proof_bar.setVisible(False)
        if hasattr(self, "editor"):
            self.editor.clear_proofread_highlights()

    def open_proofread_all(self):
        if not self.work or not self.work.chapters:
            QMessageBox.information(self, "全书校对", "请先打开一部作品。")
            return
        # 上一轮还在跑时忽略连点
        old = getattr(self, "_proofread_all_worker", None)
        if old is not None and old.isRunning():
            return
        self.flush_save()
        import utils.work_store as ws_mod

        data_root = ws_mod.get_data_root()
        total = len(self.work.chapters)

        progress = QProgressDialog(
            "正在校对全书，请稍候…", "取消", 0, total, self)
        progress.setWindowTitle("全书校对")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(400)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        worker = _ProofreadAllWorker(self.work, data_root)
        self._proofread_all_worker = worker
        # 不强设 parent：避免 workspace 析构连带销毁运行中线程，
        # finished 后自动 deleteLater；on_shutdown 另有 stop/wait 兜底。

        def _on_progress(done, tot, title):
            progress.setMaximum(tot)
            progress.setValue(done)
            if title:
                progress.setLabelText(f"正在校对：{title}（{done}/{tot}）")

        def _show_results(results):
            if not results:
                QMessageBox.information(
                    self, "全书校对完成", "整部作品未发现错别字或敏感词。"
                )
                return
            dlg = ProofreadDialog(
                results, self, data_root=data_root, whole_book=True)
            dlg.apply_fix.connect(self._apply_proof_fix_whole_book)
            dlg.locate_in_chapter.connect(self._locate_in_chapter)
            dlg.ignored_changed.connect(lambda: None)
            dlg.exec()

        def _on_ok(results, cancelled):
            try:
                progress.close()
            except RuntimeError:
                pass
            self._proofread_all_worker = None
            worker.deleteLater()
            if not cancelled:
                _show_results(results)

        def _on_fail(msg):
            try:
                progress.close()
            except RuntimeError:
                pass
            self._proofread_all_worker = None
            worker.deleteLater()
            QMessageBox.warning(
                self, "全书校对失败",
                f"校对过程中出错：{msg}\n可稍后重试。")

        worker.progress.connect(_on_progress)
        worker.finished_ok.connect(_on_ok)
        worker.failed.connect(_on_fail)
        progress.canceled.connect(worker.cancel)
        worker.start()

    def open_proof_dict_manage(self):
        import utils.work_store as ws_mod

        data_root = ws_mod.get_data_root()
        from ui.proof_dict_manage import ProofDictManageDialog

        dlg = ProofDictManageDialog(data_root, self)
        dlg.exec()

    # ▼ 块 2 ▼
    def toggle_findbar(self):
        if self._is_gallery():
            return
        if self.find_bar.isVisible():
            self.find_bar.setVisible(False)
            return
        self.find_bar.setVisible(True)
        self.find_bar.find_edit.setFocus()
        self.find_bar.raise_()
        self._reposition_findbar()

    def _reposition_findbar(self):
        self.find_bar.adjustSize()
        x = max(20, (self._editor_holder.width() - self.find_bar.width()) // 2)
        self.find_bar.move(x, 16)  # 原来是 8，改成 16

    def _search_editor_selection(self, text: str):
        """右键"查找选中文字"：打开查找条、填入选区并跳到第一处匹配。"""
        if self._is_gallery():
            return
        text = (text or "").replace("\u2029", "\n").strip()
        if not text:
            return
        if not self.find_bar.isVisible():
            self.find_bar.setVisible(True)
        self.find_bar.raise_()
        self._reposition_findbar()
        # setText 会触发 request_highlight（含首次跳转），这里聚焦即可
        self.find_bar.find_edit.setText(text)
        self.find_bar.find_edit.selectAll()
        self.find_bar.find_edit.setFocus()

    def _reposition_proofbar(self):
        if not hasattr(self, "proof_bar"):
            return
        self.proof_bar.adjustSize()
        x = max(20, (self._editor_holder.width() - self.proof_bar.width()) // 2)
        y = 16  # 原来是 8
        if self.find_bar.isVisible():
            y = 74  # 查找条高度 46 + 间距 12 + 上边距 16
        self.proof_bar.move(x, y)

    # ▼ 块 3 ▼
    def _do_highlight(self, target: str):
        count = self.editor.highlight_all_matches(target)
        if count > 0:
            self.editor.goto_match(0)
            self.find_bar.update_count(0, count)
        else:
            self.find_bar.update_count(-1, 0)

    def _do_find_next(self, target: str):
        count = self.editor.match_count()
        if count == 0:
            self._do_highlight(target)
            return
        cur = self.editor.current_match_index()
        self.editor.goto_match(cur + 1)
        self.find_bar.update_count(self.editor.current_match_index(), count)

    def _do_find_prev(self, target: str):
        count = self.editor.match_count()
        if count == 0:
            self._do_highlight(target)
            return
        cur = self.editor.current_match_index()
        self.editor.goto_match(cur - 1)
        self.find_bar.update_count(self.editor.current_match_index(), count)

    def _do_replace_one(self, target: str, replacement: str):
        from PySide6.QtGui import QTextCursor

        edit = self.editor.edit
        cursor = edit.textCursor()
        if not cursor.hasSelection():
            return
        selected = cursor.selectedText()
        if target and selected == target:
            cursor.insertText(replacement)
            self._save_debounce.start()
            self._do_highlight(target)

    def _do_replace_all(self, target: str, replacement: str):
        if not target:
            return
        from PySide6.QtGui import QTextCursor

        edit = self.editor.edit
        doc = edit.document()

        # 独立的编辑块光标（不依赖编辑器的当前光标）
        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()

        count = 0
        # 从文档开头开始搜索
        search_from = QTextCursor(doc)
        search_from.movePosition(QTextCursor.Start)

        while True:
            # 只传两个参数，用默认的 FindFlags
            match = doc.find(target, search_from)
            if match.isNull():
                break
            # 用独立光标精确替换
            c = QTextCursor(doc)
            c.setPosition(match.selectionStart())
            c.setPosition(match.selectionEnd(), QTextCursor.KeepAnchor)
            c.insertText(replacement)
            count += 1
            # 替换后从该位置继续往后找，避免死循环
            search_from = c

        edit_cursor.endEditBlock()
        self.save_state_label.setText(f"已替换 {count} 处")
        self.editor.clear_highlights()
        self.find_bar.update_count(-1, 0)

    def _on_find_closed(self):
        self.editor.clear_highlights()

    # ▼ 块 4 ▼
    # ---------- 发布前体检 ----------
    def open_publish_check(self):
        """发布前体检：一条龙检查排版 / 校对 / 追读 / 一致性。"""
        if not self.work:
            QMessageBox.information(self, "发布前体检", "请先打开一部作品。")
            return
        if not self.current_chapter_id:
            QMessageBox.information(self, "发布前体检", "请先打开一个章节。")
            return

        self.flush_save()

        box = QMessageBox(self)
        box.setWindowTitle("发布前体检")
        box.setIcon(QMessageBox.Question)
        box.setText("选择检查范围")
        box.setInformativeText(
            "当前章：只检查当前打开的章节，快（1-2 秒）\n"
            "全书：检查全部章节，慢一些（每 10 章约 1 秒）"
        )
        cur_btn = box.addButton("当前章", QMessageBox.AcceptRole)
        book_btn = box.addButton("全书", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cur_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cur_btn:
            scope = "current"
        elif clicked == book_btn:
            scope = "book"
        else:
            return

        self._run_publish_check(scope)

    def _run_publish_check(self, scope: str):
        if not self.work:
            return

        work = self.work

        if scope == "current":
            cid = self.current_chapter_id
            ch = work.get_chapter(cid)
            title = ch.get("title", "") if ch else cid
            texts = [(cid, title, work.read_chapter(cid))]
        else:
            texts = [
                (c["id"], c.get("title", ""), work.read_chapter(c["id"]))
                for c in work.chapters
            ]

        import utils.work_store as ws_mod

        data_root = ws_mod.get_data_root()

        from utils.publish_check import (
            check_format_all,
            check_proofread_all,
            check_readability_all,
            check_consistency_all,
        )

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.save_state_label.setText("发布前体检：正在检查排版格式…")
            QApplication.processEvents()
            format_r = check_format_all(texts)

            self.save_state_label.setText("发布前体检：正在校对错别字 / 敏感词…")
            QApplication.processEvents()
            proofread_r = check_proofread_all(texts, data_root)

            self.save_state_label.setText("发布前体检：正在跑追读体检…")
            QApplication.processEvents()
            readability_r = check_readability_all(texts)

            if scope == "book":
                self.save_state_label.setText("发布前体检：正在做一致性检查…")
                QApplication.processEvents()
                consistency_r = check_consistency_all(work, data_root)
            else:
                consistency_r = {
                    "status": "skipped",
                    "msg": "单章模式跳过（全书体检才检查一致性）",
                }
        finally:
            QApplication.restoreOverrideCursor()
            self.save_state_label.setText("")

        chapter_title = ""
        if scope == "current":
            ch = work.get_chapter(self.current_chapter_id)
            chapter_title = ch.get("title", "") if ch else ""

        result = {
            "scope": scope,
            "work_title": work.title,
            "chapter_title": chapter_title,
            "chapter_count": len(work.chapters),
            "format": format_r,
            "proofread": proofread_r,
            "readability": readability_r,
            "consistency": consistency_r,
        }

        self._show_publish_check_dialog(result, scope, readability_r)

    def _show_publish_check_dialog(self, result: dict, scope: str, readability_r: dict):
        from ui.publish_check_dialog import PublishCheckDialog

        dlg = PublishCheckDialog(result, self)
        dlg.open_format_detail.connect(lambda: self._open_publish_format_detail(scope))
        dlg.open_proofread_detail.connect(
            lambda: self._open_publish_proofread_detail(scope)
        )
        dlg.open_readability_detail.connect(
            lambda: self._open_publish_readability_detail(scope, readability_r)
        )
        dlg.open_consistency_detail.connect(self._open_publish_consistency_detail)
        dlg.rerun_requested.connect(lambda: self._rerun_publish_check(scope, dlg))
        dlg.exec()

    def _open_publish_format_detail(self, scope: str):
        if not self.work:
            return
        self.flush_save()
        from ui.format_dialog import FormatDialog

        cur = self.current_chapter_id if scope == "current" else None
        d = FormatDialog(self.work, cur, self)
        d.applied.connect(self._on_format_applied)
        d.exec()

    def _open_publish_proofread_detail(self, scope: str):
        if scope == "book":
            self.open_proofread_all()
        else:
            self.open_proofread()
            if self._proof_results:
                self._open_proofread_detail()

    def _open_publish_readability_detail(self, scope: str, readability_r: dict):
        if scope == "book":
            worst_cid = readability_r.get("worst_chapter_id")
            if worst_cid and worst_cid != self.current_chapter_id:
                self.flush_save()
                self.open_chapter(worst_cid)
        self.open_readability_check()

    def _open_publish_consistency_detail(self):
        if not self.work:
            return
        self.open_consistency_check()

    def _rerun_publish_check(self, scope: str, old_dlg):
        try:
            old_dlg.accept()
        except RuntimeError:
            pass
        QTimer.singleShot(0, lambda: self._run_publish_check(scope))

    def _locate_in_chapter(self, chapter_id: str, start: int, end: int):
        if not chapter_id or not self.work:
            return
        if chapter_id != self.current_chapter_id:
            self.open_chapter(chapter_id)
        self._locate_in_editor(start, end)

    def _apply_proof_fix_whole_book(self, start: int, end: int, replacement: str):
        self._apply_proof_fix(start, end, replacement)

    def _locate_in_editor(self, start: int, end: int):
        from PySide6.QtGui import QTextCursor

        edit = self.editor.edit
        doc = edit.document()
        start = max(0, min(start, doc.characterCount() - 1))
        end = max(start, min(end, doc.characterCount() - 1))
        c = QTextCursor(doc)
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        edit.setTextCursor(c)
        edit.ensureCursorVisible()
        edit.setFocus()

    def _apply_proof_fix(self, start: int, end: int, replacement: str):
        from PySide6.QtGui import QTextCursor

        c = self.editor.edit.textCursor()
        c.beginEditBlock()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        c.insertText(replacement)
        c.endEditBlock()
        self.editor.edit.setTextCursor(c)
        self._save_debounce.start()

    # ---------- 词条高亮 ----------
    def _refresh_dictionary_highlights(self):
        """扫描当前章节正文，把人物卡 + 高频词里出现的名字高亮。

        每个词条带自己的颜色：人物卡用其全局色，高频词用分类色。
        可在「设置 → 写作偏好」中整体关闭。
        """
        if not getattr(self, "_dict_highlight_enabled", True):
            self.editor.clear_dict_highlights()
            return
        if not hasattr(self.editor, "highlight_dictionary"):
            return
        if not self.current_chapter_id or not self.work:
            self.editor.clear_dict_highlights()
            return
        text = self.editor.get_text()
        if not text:
            self.editor.clear_dict_highlights()
            return

        from ui.color_picker import (
            character_default_color,
            default_category_colors,
        )

        items = []

        char_color = character_default_color()
        try:
            c = self.work.load_characters_color()
            if c:
                char_color = c
        except Exception:
            pass
        try:
            for ch in self.work.load_characters():
                name = (ch.get("name") or "").strip()
                if name:
                    items.append({"word": name, "color": char_color, "label": "人物"})
                for a in ch.get("aliases") or []:
                    a = str(a).strip()
                    if a:
                        items.append({"word": a, "color": char_color, "label": "人物"})
        except Exception:
            pass

        try:
            wb = self.work.load_wordbook() or {}
            defaults = default_category_colors()
            for cat in wb.get("categories", []):
                cat_name = cat.get("name", "")
                color = cat.get("color") or defaults.get(cat_name, "#64748B")
                for w in cat.get("words", []):
                    word = (w.get("word") if isinstance(w, dict) else w) or ""
                    word = str(word).strip()
                    if word:
                        items.append({"word": word, "color": color, "label": cat_name})
        except Exception:
            pass

        self.editor.highlight_dictionary(items)

    # ▼ 块 5 ▼
    # ---------- 平台格式一键 ----------
    def open_platform_format_dialog(self):
        if not self.work:
            QMessageBox.information(self, "平台格式", "请先打开一部作品。")
            return
        self.flush_save()
        from ui.platform_format_dialog import PlatformFormatDialog

        dlg = PlatformFormatDialog(self.work, self.current_chapter_id or "", self)
        dlg.applied.connect(self._on_platform_format_applied)
        dlg.exec()

    def _on_platform_format_applied(self, affected: set):
        if self.current_chapter_id in affected:
            pos = self.editor.edit.textCursor().position()
            scroll = self.editor.edit.verticalScrollBar().value()
            text = self.work.read_chapter(self.current_chapter_id)
            self.editor.load_text(text)
            cur = self.editor.edit.textCursor()
            cur.setPosition(min(pos, len(text)))
            self.editor.edit.setTextCursor(cur)
            self.editor.edit.verticalScrollBar().setValue(scroll)
            self._baseline_words = self.work.get_chapter(self.current_chapter_id).get(
                "word_count", 0
            )
            self._update_word_status()
        self.bookshelf.refresh_tree()

    # ▼ 块 6 ▼
    # ---------- 冲刺计划 ----------
    def open_sprint_dialog(self):
        if not self.work:
            QMessageBox.information(self, "冲刺计划", "请先打开一部作品。")
            return
        self.flush_save()
        from ui.sprint_dialog import SprintDialog

        dlg = SprintDialog(self.work, self)
        dlg.exec()

    # ▼ 块 7 ▼
    # ---------- 一致性检查 ----------
    def open_consistency_check(self):
        if not self.work:
            QMessageBox.information(self, "一致性检查", "请先打开一部作品。")
            return
        self.flush_save()
        from utils.consistency import scan_work, load_ignored
        from ui.consistency_dialog import ConsistencyDialog

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ignored = load_ignored(self.work)
            pairs = scan_work(self.work, ignored_pairs=ignored)
        finally:
            QApplication.restoreOverrideCursor()

        dlg = ConsistencyDialog(pairs, self, work=self.work)
        dlg.goto_requested.connect(self._on_consistency_goto)
        dlg.replace_all_requested.connect(self._on_consistency_replace)
        dlg.exec()

        # 对话框关闭后：如果替换改动了当前章节，刷新编辑器
        if self.current_chapter_id and self.work:
            disk = self.work.read_chapter(self.current_chapter_id)
            if disk != self.editor.get_text():
                pos = self.editor.edit.textCursor().position()
                scroll = self.editor.edit.verticalScrollBar().value()
                self.editor.load_text(disk)
                cur = self.editor.edit.textCursor()
                cur.setPosition(min(pos, len(disk)))
                self.editor.edit.setTextCursor(cur)
                self.editor.edit.verticalScrollBar().setValue(scroll)
                self._baseline_words = self.work.get_chapter(
                    self.current_chapter_id
                ).get("word_count", 0)
                self._update_word_status()
        self.bookshelf.refresh_tree()

    def _on_consistency_goto(self, chapter_id: str, start: int, end: int):
        """定位到某章的某段文字。必要时先切章。"""
        if not self.work or not chapter_id:
            return
        if chapter_id != self.current_chapter_id:
            self.flush_save()
            self.open_chapter(chapter_id)
        self._locate_in_editor(start, end)

    def _on_consistency_replace(self, anchor: str, variant: str, positions: list):
        """把全书所有 variant 替换成 anchor。每章替换前自动打快照。"""
        if not self.work or not positions:
            return

        # 二次确认
        ret = QMessageBox.question(
            self,
            "批量替换",
            f"确定把全书 {len(positions)} 处「{variant}」\n"
            f"全部替换为「{anchor}」吗？\n\n"
            "每章替换前会自动打快照，可随时恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return

        # 按章节分组
        by_chapter: dict = {}
        for cid, s, e in positions:
            by_chapter.setdefault(cid, []).append((s, e))

        affected = set()
        try:
            for cid, spans in by_chapter.items():
                old = self.work.read_chapter(cid)
                if not old:
                    continue
                # 从后往前替换，避免偏移错乱
                new = old
                for s, e in sorted(spans, reverse=True):
                    if 0 <= s < e <= len(new):
                        new = new[:s] + anchor + new[e:]
                if new == old:
                    continue
                self.work.create_snapshot(cid)
                self.work.write_chapter(cid, new)
                affected.add(cid)
        except OSError as e:
            logger.error(f"一致性替换失败: {e}", exc_info=True)
            QMessageBox.critical(
                self, "替换失败", f"写入章节时出错：{e}\n已处理的章节可从历史快照恢复。"
            )
            return

        if not affected:
            QMessageBox.information(self, "替换完成", "没有内容需要修改。")
            return

        QMessageBox.information(
            self,
            "替换完成",
            f"已替换 {len(positions)} 处，涉及 {len(affected)} 章。\n"
            "原稿已自动打快照。",
        )

        # 若当前章节被改，刷新编辑器
        if self.current_chapter_id in affected:
            pos = self.editor.edit.textCursor().position()
            scroll = self.editor.edit.verticalScrollBar().value()
            disk = self.work.read_chapter(self.current_chapter_id)
            self.editor.load_text(disk)
            cur = self.editor.edit.textCursor()
            cur.setPosition(min(pos, len(disk)))
            self.editor.edit.setTextCursor(cur)
            self.editor.edit.verticalScrollBar().setValue(scroll)
            self._baseline_words = self.work.get_chapter(self.current_chapter_id).get(
                "word_count", 0
            )
            self._update_word_status()
        self.bookshelf.refresh_tree()
        self._refresh_dictionary_highlights()
