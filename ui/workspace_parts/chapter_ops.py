# ui/workspace_parts/chapter_ops.py
"""墨码写作 - 章节导航与结构联动（Mixin）

从 workspace.py 拆出的章节级操作：
- 打开/新建/前后翻章/跳转、章节上移下移、当前章丢失处理
- 标题更新、回到画廊
- 大纲面板引用跳转、章节改名/结构增删前后的编辑器状态保存与恢复
"""

from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import QTimer

from utils.logger import get_logger

logger = get_logger()


class ChapterOpsMixin:
    """章节打开/导航/结构联动。方法直接访问 self.*。"""

    def _on_outline_reference_clicked(self, chapter_id: str):
        if not chapter_id or not self.work:
            return
        if self.work.get_chapter(chapter_id) is None:
            return
        self.open_chapter(chapter_id)

    def _on_chapter_title_changed(self, chapter_id: str, new_title: str):
        if not self.work or not chapter_id:
            return
        ch = self.work.get_chapter(chapter_id)
        if ch is None:
            return
        old = (ch.get("title") or "").strip()
        new = (new_title or "").strip()
        if old == new:
            return
        self.work.rename_chapter(chapter_id, new)
        self.bookshelf.refresh_tree()
        self.bookshelf.set_current_chapter(self.current_chapter_id)
        if chapter_id == self.current_chapter_id:
            self._update_titles()
        self.save_state_label.setText("章节标题已更新")
        QTimer.singleShot(1500, lambda: self.save_state_label.setText(""))

    def _before_structure_change(self):
        try:
            self.flush_save()
        except Exception as e:
            logger.error(f"结构变更前保存失败: {e}", exc_info=True)
            try:
                self._backup_draft_now()
            except Exception as _e:
                logger.warning(f"结构变更前草稿兜底失败: {_e}")
            ret = QMessageBox.warning(
                self, "保存失败",
                f"章节结构变更前，当前编辑内容保存失败：\n\n{e}\n\n"
                "已写一份恢复草稿作为兜底，但建议先处理磁盘问题。\n\n"
                "是否继续结构变更？\n"
                "（继续后草稿仍可在下次启动时恢复）",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                self._structure_change_aborted = True
                raise

    def _on_structure_changed(self):
        if not self.work:
            return
        if not self.current_chapter_id:
            return
        ch = self.work.get_chapter(self.current_chapter_id)
        if ch is None or ch.get("deleted"):
            if self.work.chapters:
                self.open_chapter(self.work.chapters[0]["id"])
            else:
                self._handle_current_chapter_gone()
            return
        disk_text, status = self.work.read_chapter_checked(
            self.current_chapter_id)
        if status != "ok":
            logger.warning(
                f"结构变更后重载章节 {self.current_chapter_id} 失败"
                f"（status={status}），保留编辑器当前内容")
            return
        if disk_text != self.editor.get_text():
            pos = self.editor.edit.textCursor().position()
            scroll = self.editor.edit.verticalScrollBar().value()
            self.editor.load_text(disk_text)
            cur = self.editor.edit.textCursor()
            cur.setPosition(min(pos, len(disk_text)))
            self.editor.edit.setTextCursor(cur)
            self.editor.edit.verticalScrollBar().setValue(scroll)
            self._baseline_words = int(ch.get("word_count", 0))
            self._update_word_status()

    def _open_recent_chapter(self):
        if not self.work or not self.work.chapters:
            return
        chapters = self.work.chapters
        has_time = [c for c in chapters if c.get("updated_at")]
        if has_time:
            target = max(has_time, key=lambda c: c.get("updated_at", ""))
            self.open_chapter(target["id"])
        else:
            self.open_chapter(chapters[0]["id"])

    def open_chapter(self, chapter_id: str):
        if self.bookshelf.work is None:
            return
        if self.work is not self.bookshelf.work:
            self.work = self.bookshelf.work
            self.stack.setCurrentIndex(1)
        same_chapter = (self.current_chapter_id == chapter_id)
        if self.current_chapter_id and not same_chapter:
            self.editor.abort_streams_for_reload()
            self._retire_inline_worker()
        if self.current_chapter_id:
            # B4：先记下离开的章节，flush_save 会把最后一笔字数结算进去
            old_chapter_id = self.current_chapter_id
            self.flush_save()
            if not same_chapter:
                self._maybe_chapter_switch_cta(old_chapter_id)
        chapter = self.work.get_chapter(chapter_id)
        if chapter is None:
            return
        if same_chapter:
            self.editor.set_focus()
            return
        # 章节真的要切换：关闭非模态 AI 改写框，防止跨章误应用
        self._close_rewrite_dialog()
        text, status = self.work.read_chapter_checked(chapter_id)
        if status == "error":
            QMessageBox.warning(
                self, "章节暂时无法打开",
                f"章节「{chapter.get('title', '')}」的内容文件读取失败，\n"
                "可能正被同步盘、杀毒软件或其他程序占用。\n\n"
                "请稍后重试；当前章节未被修改，编辑器内容也未受影响。")
            if self.current_chapter_id:
                self.bookshelf.set_current_chapter(self.current_chapter_id)
            return
        if status == "missing":
            box = QMessageBox(self)
            box.setWindowTitle("章节内容文件缺失")
            box.setIcon(QMessageBox.Warning)
            box.setText(
                f"章节「{chapter.get('title', '')}」的内容文件不存在。")
            box.setInformativeText(
                "可能被外部程序（同步盘、杀毒软件）移动或删除。\n\n"
                "· 打开文件夹查看 → 可能找回文件后手动放回\n"
                "· 以空白章节打开 → 该章节内容会变成空白，"
                "原内容在下次自动保存后即被覆盖，不可恢复\n\n"
                "建议先「打开文件夹查看」，确认文件确实不见了再"
                "「以空白章节打开」。")
            open_btn = box.addButton(
                "打开文件夹查看", QMessageBox.ActionRole)
            empty_btn = box.addButton(
                "以空白章节打开", QMessageBox.DestructiveRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(open_btn)
            box.exec()
            chosen = box.clickedButton()
            if chosen == open_btn:
                import os as _os
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl as _QUrl
                ch_path = self.work.chapter_path(chapter_id) or ""
                folder = _os.path.dirname(ch_path) if ch_path else ""
                if folder and _os.path.isdir(folder):
                    QDesktopServices.openUrl(_QUrl.fromLocalFile(folder))
                if self.current_chapter_id:
                    self.bookshelf.set_current_chapter(
                        self.current_chapter_id)
                return
            if chosen != empty_btn:
                if self.current_chapter_id:
                    self.bookshelf.set_current_chapter(
                        self.current_chapter_id)
                return
            text = ""
        self.editor.load_text(text)
        self.current_chapter_id = chapter_id
        self._baseline_words = int(chapter.get("word_count", 0))
        self.bookshelf.set_current_chapter(chapter_id)
        self.bookshelf.outline.set_chapter(chapter_id, chapter.get("title", ""))
        self._update_titles()
        self._update_word_status()
        self.save_state_label.setText("")
        self._clear_proofread()
        self._refresh_dictionary_highlights()
        self.editor.set_focus()
        # B5：卡文检测的写作会话随换章重新计时
        self._writing_session_start = None
        self._block_words_at_session_start = None

    def _new_chapter(self):
        if self.work is None:
            self.create_new_work()
            return
        self.bookshelf._add_chapter()

    def _prev_chapter(self):
        self._goto_chapter(-1)

    def _next_chapter(self):
        self._goto_chapter(1)

    def _goto_chapter(self, delta: int):
        if not self.work or not self.current_chapter_id:
            return
        idx = self.work.chapter_index(self.current_chapter_id)
        new_idx = idx + delta
        if 0 <= new_idx < len(self.work.chapters):
            self.open_chapter(self.work.chapters[new_idx]["id"])

    def _move_chapter_up(self):
        self._move_current_chapter(-1)

    def _move_chapter_down(self):
        self._move_current_chapter(1)

    def _move_current_chapter(self, delta: int):
        if not self.work or not self.current_chapter_id:
            return
        cid = self.current_chapter_id
        cur = self.work.get_chapter(cid)
        if cur is None:
            return
        cur_vol = (cur.get("volume") or "").strip()
        same_vol = [
            c for c in self.work.chapters if (c.get("volume") or "").strip() == cur_vol
        ]
        idx_in_vol = next((i for i, c in enumerate(same_vol) if c["id"] == cid), -1)
        if idx_in_vol < 0:
            return
        new_idx_in_vol = idx_in_vol + delta
        if not (0 <= new_idx_in_vol < len(same_vol)):
            self.save_state_label.setText("已到本卷首/尾，无法继续移动")
            QTimer.singleShot(1500, lambda: self.save_state_label.setText(""))
            return
        target_global_idx = self.work.chapter_index(same_vol[new_idx_in_vol]["id"])
        if target_global_idx < 0:
            return
        self.flush_save()
        self.work.move_chapter(cid, target_global_idx)
        self.bookshelf.refresh_tree()
        self.bookshelf.set_current_chapter(cid)
        self._update_titles()
        self.save_state_label.setText("章节已移动")
        QTimer.singleShot(1500, lambda: self.save_state_label.setText(""))

    def _handle_current_chapter_gone(self):
        self.current_chapter_id = None
        self.editor.load_text("")
        self._baseline_words = 0
        self.save_state_label.setText("")
        if self.work and self.work.chapters:
            self.open_chapter(self.work.chapters[0]["id"])

    def _update_titles(self):
        if self.work:
            if (
                self.current_chapter_id
                and self.work.get_chapter(self.current_chapter_id) is None
            ):
                self._handle_current_chapter_gone()
                return
            self.work_title_label.setText(self.work.title)
            ch = (
                self.work.get_chapter(self.current_chapter_id)
                if self.current_chapter_id
                else None
            )
            if ch:
                volume = ch.get("volume", "") or "正文"
                self.volume_label.setText(f"· {volume}")
            else:
                self.volume_label.setText("")
            self.title_changed.emit(f"{self.work.title} - 墨码写作")
            self._update_word_status()
        else:
            self.work_title_label.setText("墨码写作")
            self.volume_label.setText("")
            self.title_changed.emit("墨码写作")
            self.chapter_target_label.setText("")
            self.chapter_target_label.setVisible(False)

    def _back_to_gallery(self):
        self.flush_save()
        # 状态复位：避免停留在画廊期间仍有对话框/定时器持有过期章节
        self._close_rewrite_dialog()
        toolbox = getattr(self, "_edit_toolbox_dlg", None)
        if toolbox is not None:
            try:
                toolbox.close()
            except RuntimeError:
                pass
        self._save_debounce.stop()
        self._refresh_gallery()
        self._refresh_gallery_theme()
        self.stack.setCurrentIndex(0)
        self.work = None
        self.current_chapter_id = None
        self.editor.load_text("")
        self._baseline_words = 0
        self.chapter_header.clear()
        self._pet_bubble.set_conversation_key("")

