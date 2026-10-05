# ui/workspace_parts/dialog_launchers.py
"""墨码写作 - 工具栏对话框入口（Mixin）

从 workspace.py 拆出的「点按钮 → 打开对应对话框/面板」一层胶水：
- 发布菜单与发布平台管理
- 编辑工具箱、回收站、阅读器预览、写作统计、章节字数图
- 人物关系、全局搜索、排版（全书规整/光标拆章）、历史版本、批量工具
- 符号面板开关
各业务对话框自身的实现仍在各自模块中，这里只负责前置保存与实例化。
"""

from PySide6.QtWidgets import QMenu, QMessageBox, QInputDialog
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
import qtawesome as qta

from ui.format_dialog import FormatDialog
from ui.batch_tools import BatchToolsDialog
from ui.reader_preview import ReaderPreviewDialog
from utils.logger import get_logger

logger = get_logger()


class DialogLaunchersMixin:
    """工具栏 / 菜单的对话框入口。方法直接访问 self.*。"""

    # ==================== 发布 ====================

    def _show_publish_menu(self):
        from utils.publish_targets import load_groups

        menu = QMenu(self)
        groups = load_groups()
        if not groups:
            act_empty = menu.addAction("尚未配置发布平台")
            act_empty.setEnabled(False)
        for g in groups:
            sub = menu.addMenu(g["group"])
            for name, url, note in g["targets"]:
                text = f"{name}  ·  {note}" if note else name
                act = sub.addAction(text)
                act.setData(url)
        menu.addSeparator()
        act_manage = menu.addAction(
            qta.icon("fa5s.cog",
                     color=self._toolbar_icon_color()), "管理发布平台…"
        )
        act_manage.setData("__manage__")
        chosen = menu.exec(
            self.btn_tb_export.mapToGlobal(
                self.btn_tb_export.rect().bottomLeft())
        )
        if chosen is None:
            return
        data = chosen.data()
        if data == "__manage__":
            self.open_publish_manage()
            return
        if data:
            QDesktopServices.openUrl(QUrl(data))
            self.save_state_label.setText("已打开浏览器，请手动粘贴存稿")

    def open_publish_manage(self):
        from ui.publish_manage import PublishManageDialog

        dlg = PublishManageDialog(self)
        dlg.exec()

    def open_edit_toolbox(self):
        if not self.work:
            QMessageBox.information(self, "编辑工具箱", "请先打开一部作品。")
            return
        self.flush_save()
        from ui.edit_toolbox import EditToolboxDialog

        dlg = getattr(self, "_edit_toolbox_dlg", None)
        if dlg is None:
            dlg = EditToolboxDialog(self, self)
            dlg.applied.connect(self._on_toolbox_applied)
            dlg.finished.connect(lambda _: setattr(self, "_edit_toolbox_dlg", None))
            self._edit_toolbox_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_toolbox_applied(self, affected: set):
        # 工具箱是非模态的：用户可能已回画廊/切作品，此时 self.work 为
        # None，直接读章节会 AttributeError 并静默失败。
        if not self.work or not self.current_chapter_id:
            return
        if self.current_chapter_id in affected:
            pos = self.editor.edit.textCursor().position()
            scroll = self.editor.edit.verticalScrollBar().value()
            text = self.work.read_chapter(self.current_chapter_id)
            self.editor.load_text(text)
            cursor = self.editor.edit.textCursor()
            cursor.setPosition(min(pos, len(text)))
            self.editor.edit.setTextCursor(cursor)
            self.editor.edit.verticalScrollBar().setValue(scroll)
            ch = self.work.get_chapter(self.current_chapter_id)
            if ch:
                self._baseline_words = int(ch.get("word_count", 0))
            self._update_word_status()
        self.bookshelf.refresh_tree()

    def open_trash_dialog(self):
        from ui.trash_dialog import TrashDialog

        dlg = TrashDialog(self.store, self)
        dlg.changed.connect(self._on_trash_changed)
        dlg.exec()

    def open_chapter_trash(self):
        if not self.work:
            QMessageBox.information(self, "章节回收站", "请先打开一部作品。")
            return
        self.bookshelf.open_chapter_trash()

    def _on_trash_changed(self):
        self._refresh_gallery()
        self._refresh_gallery_theme()

    # ==================== 其他功能 ====================

    def open_reader_preview(self):
        text = self.editor.get_text()
        if not text.strip():
            QMessageBox.information(self, "预览", "当前章节没有内容。")
            return
        title = ""
        if self.work and self.current_chapter_id:
            ch = self.work.get_chapter(self.current_chapter_id)
            if ch:
                title = ch.get("title", "")
        dlg = ReaderPreviewDialog(text, title, self)
        dlg.exec()

    def open_writing_stats(self):
        from ui.writing_stats_dialog import WritingStatsDialog

        self.flush_save()
        dlg = WritingStatsDialog(self.stats, self, self)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_chapter_length_chart(self):
        if not self.work:
            QMessageBox.information(
                self, "章节字数分布", "请先打开一部作品。")
            return
        self.flush_save()
        from ui.chapter_length_dialog import ChapterLengthDialog

        dlg = ChapterLengthDialog(self.work, self)
        dlg.goto_chapter.connect(self.open_chapter)
        dlg.exec()

    def open_character_relation_dialog(self):
        if not self.work:
            QMessageBox.information(
                self, "人物关系图", "请先打开一部作品。")
            return
        self.flush_save()
        chars = self.work.load_characters() or []
        if not chars:
            QMessageBox.information(
                self, "人物关系图",
                "这部作品还没有人物卡。\n\n"
                "请先到「人物」面板新建几个角色，并在角色卡的\n"
                "「人物关系」字段里填写关系（每行一条，如：师父：李长生）。")
            return
        from ui.character_relation_dialog import CharacterRelationDialog

        dlg = CharacterRelationDialog(self.work, self)
        dlg.exec()

    def open_global_search(self):
        if not self.work:
            QMessageBox.information(self, "全局搜索", "请先打开一部作品。")
            return
        dlg = getattr(self, "_global_search_dlg", None)
        if dlg is None:
            from ui.global_search_dialog import GlobalSearchDialog

            dlg = GlobalSearchDialog(self, self)
            dlg.goto_match.connect(self._locate_in_chapter)
            dlg.finished.connect(lambda _: setattr(self, "_global_search_dlg", None))
            self._global_search_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_format_dialog(self):
        if not self.work:
            QMessageBox.information(self, "排版", "请先打开一部作品。")
            return
        self.flush_save()
        dialog = FormatDialog(self.work, self.current_chapter_id, self)
        dialog.applied.connect(self._on_format_applied)
        dialog.exec()

    def _organize_all_titles(self):
        if not self.work or not self.work.chapters:
            QMessageBox.information(
                self, "整理章节标题", "请先打开一部作品。")
            return
        self.flush_save()

        chapters = [
            {"id": c.get("id", ""), "title": c.get("title", "")}
            for c in self.work.chapters
        ]
        from ui.import_title_dialog import open_organize_dialog
        renames = open_organize_dialog(
            chapters, parent=self, scope_label="整理章节标题")
        if not renames:
            return

        n = self.work.rename_chapters_batch(renames)
        self.bookshelf.refresh_tree()
        if self.current_chapter_id:
            self.bookshelf.set_current_chapter(self.current_chapter_id)
        self._update_titles()
        self.save_state_label.setText(f"已整理 {n} 个章节标题")
        QTimer.singleShot(2500, lambda: self.save_state_label.setText(""))

    def _split_at_cursor(self):
        if not self.work or not self.current_chapter_id:
            QMessageBox.information(self, "拆分章节", "请先打开一个章节。")
            return
        self.flush_save()
        edit = self.editor.edit
        cursor = edit.textCursor()
        pos = cursor.position()
        text = edit.toPlainText()
        if pos <= 0 or pos >= len(text):
            QMessageBox.information(
                self, "拆分章节",
                "光标位置太靠前/靠后，无法拆分。\n\n"
                "请把光标放到要拆分的位置（前有内容、后有内容）。")
            return

        cur_ch = self.work.get_chapter(self.current_chapter_id)
        cur_title = cur_ch.get("title", "") if cur_ch else ""
        default_new = f"{cur_title}（续）" if cur_title else "新章节"

        new_title, ok = QInputDialog.getText(
            self, "拆分章节",
            "新章节的标题：", text=default_new)
        if not ok or not new_title.strip():
            return

        new_ch = self.work.split_chapter(
            self.current_chapter_id, pos, new_title.strip())
        if new_ch is None:
            QMessageBox.warning(self, "拆分失败", "拆分位置无效。")
            return

        self.bookshelf.refresh_tree()
        self.bookshelf.set_current_chapter(self.current_chapter_id)
        self.editor.load_text(
            self.work.read_chapter(self.current_chapter_id))
        self._baseline_words = self.work.get_chapter(
            self.current_chapter_id).get("word_count", 0)
        self._update_titles()
        self._update_word_status()
        self.save_state_label.setText(f"已拆分为新章节「{new_title}」")
        QTimer.singleShot(2500, lambda: self.save_state_label.setText(""))

    def _on_format_applied(self, affected: set):
        if self.current_chapter_id in affected:
            pos = self.editor.edit.textCursor().position()
            scroll = self.editor.edit.verticalScrollBar().value()
            text = self.work.read_chapter(self.current_chapter_id)
            self.editor.load_text(text)
            cursor = self.editor.edit.textCursor()
            cursor.setPosition(min(pos, len(text)))
            self.editor.edit.setTextCursor(cursor)
            self.editor.edit.verticalScrollBar().setValue(scroll)
            self._baseline_words = self.work.get_chapter(self.current_chapter_id).get(
                "word_count", 0
            )
            self._update_word_status()
        self.bookshelf.refresh_tree()

    def open_version_history(self, chapter_id: str = None):
        if not self.work:
            QMessageBox.information(self, "历史版本", "请先打开一部作品。")
            return
        cid = chapter_id or self.current_chapter_id
        if not cid:
            QMessageBox.information(self, "历史版本", "请先打开一个章节。")
            return
        if cid == self.current_chapter_id:
            self.flush_save()
        from ui.version_history_dialog import VersionHistoryDialog

        dlg = VersionHistoryDialog(self.work, cid, self)
        dlg.version_restored.connect(self._on_version_restored)
        dlg.exec()

    def _on_version_restored(self, chapter_id: str):
        if chapter_id == self.current_chapter_id:
            pos = self.editor.edit.textCursor().position()
            scroll = self.editor.edit.verticalScrollBar().value()
            text = self.work.read_chapter(chapter_id)
            self.editor.load_text(text)
            cursor = self.editor.edit.textCursor()
            cursor.setPosition(min(pos, len(text)))
            self.editor.edit.setTextCursor(cursor)
            self.editor.edit.verticalScrollBar().setValue(scroll)
            self._baseline_words = self.work.get_chapter(chapter_id).get(
                "word_count", 0
            )
            self._update_word_status()
        self.bookshelf.refresh_tree()

    def open_version_browser(self):
        if not self.work:
            QMessageBox.information(self, "历史快照", "请先打开一部作品。")
            return
        self.flush_save()
        from ui.version_browser_dialog import VersionBrowserDialog

        dlg = VersionBrowserDialog(self.work, self)
        dlg.chapter_restored.connect(self._on_version_browser_restored)
        dlg.exec()

    def _on_version_browser_restored(self, chapter_id: str):
        self.bookshelf.refresh_tree()
        self._update_titles()
        self.open_chapter(chapter_id)

    def open_batch_tools_dialog(self):
        initial_files = []
        initial_dir = ""
        if self.work:
            self.flush_save()
            initial_dir = self.work.chapters_dir
            initial_files = [
                self.work.chapter_path(c["id"])
                for c in self.work.chapters
                if self.work.chapter_path(c["id"])
            ]
        dialog = BatchToolsDialog(
            initial_files=initial_files, initial_dir=initial_dir, parent=self
        )
        dialog.exec()
        if self.work and self.current_chapter_id:
            disk_text = self.work.read_chapter(self.current_chapter_id)
            if disk_text != self.editor.get_text():
                self.editor.load_text(disk_text)
                self._baseline_words = self.work.get_chapter(
                    self.current_chapter_id
                ).get("word_count", 0)
                self._update_word_status()

    def toggle_symbol_panel(self):
        if self._is_gallery():
            return
        self.symbol_panel.setVisible(not self.symbol_panel.isVisible())
        self._relayout()

