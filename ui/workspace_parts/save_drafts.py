# ui/workspace_parts/save_drafts.py
"""墨码写作 - 自动保存与崩溃草稿恢复（Mixin）

从 workspace.py 拆出的存稿链路：
- 正文变化 → 停顿 3 秒自动保存 + 30 秒兜底定时
- 手动保存、定时快照（历史版本）
- 崩溃草稿：备份、扫描、恢复弹窗（含安静模式）、草稿提示条
"""

import os
import time
from datetime import datetime

from PySide6.QtWidgets import QMessageBox

from utils.work_store import count_words
from utils.logger import get_logger

logger = get_logger()


class SaveDraftMixin:
    """自动保存、快照、崩溃草稿恢复。方法直接访问 self.*。"""

    # ==================== 内容变化 ====================

    def _on_content_changed(self):
        if not self.current_chapter_id:
            return
        self._save_debounce.start()
        self._set_save_state("saving", "编辑中…")
        now = time.monotonic()
        if now - self._last_heartbeat >= 10:
            self.stats.add_heartbeat()
            self._last_heartbeat = now

        old_words = getattr(self, "_last_pulse_words", 0)
        last_pulse = getattr(self, "_last_pulse_time", 0.0)
        if now - last_pulse >= 0.4:
            cur_chars = self.editor.edit.document().characterCount()
            if cur_chars != getattr(self, "_last_char_count", -1):
                self._last_char_count = cur_chars
                new_words = count_words(self.editor.get_text())
                if new_words > old_words:
                    self._last_pulse_time = now
                    self._pulse_today()
                self._last_pulse_words = new_words

        # 状态栏字数等尾随 350ms 刷新，避免每次按键都全章正则统计
        self._word_status_timer.start()
        self._draft_dirty = True
        if hasattr(self, "_dict_refresh_timer"):
            self._dict_refresh_timer.start()

        # ---- 猫娘：打字反馈 + focused 修饰符 + 久坐提醒 ----
        self._last_user_activity = now
        if self._pet_float is not None:
            try:
                self._pet_float.tap()
                self._pet_float.set_modifier("")
            except RuntimeError:
                pass

        # 写作会话计时：距上次击键超过 60s 视为新会话。
        # 用于「30s 进入专注」「45min 久坐提醒」「12min 卡文检测」，
        # 一个会话只提醒一次。
        if now - getattr(self, "_last_keystroke_ts", 0.0) > 60:
            self._writing_session_start = now
            self._long_session_reminded = False
            # B5：记录会话起点的章节字数，净产出不足即"打了字但没推进"
            try:
                self._block_words_at_session_start = count_words(
                    self.editor.get_text())
            except Exception:
                self._block_words_at_session_start = None
        self._last_keystroke_ts = now
        session_span = now - getattr(
            self, "_writing_session_start", now)

        if self._pet_float is not None:
            try:
                if session_span >= 30:
                    self._pet_float.set_modifier("focused", hold_ms=3000)
            except RuntimeError:
                pass
        if self._pet_bubble is not None and not self._pet_bubble.isVisible():
            self.set_base_pet_state("writing")

        # 连续奋笔 45 分钟 → 墨灵提醒休息眼睛
        if (self._pet_float is not None
                and session_span >= 2700
                and not getattr(self, "_long_session_reminded", False)):
            self._long_session_reminded = True
            try:
                self.set_pet_state("worried", hold_ms=4000)
                self._pet_float.say(
                    "主人已经写了好久啦，起来动动、看看远处吧？")
            except RuntimeError:
                pass

        # B5：连续打字 12 分钟却几乎没有净增字数 → 卡文关心（零 token）
        self._maybe_block_nudge(now)

    # ==================== 保存 ====================

    def flush_save(self):
        if not self.work or not self.current_chapter_id:
            return
        text = self.editor.get_text()

        try:
            disk_text, disk_status = self.work.read_chapter_checked(
                self.current_chapter_id)
        except Exception:
            disk_text, disk_status = "", "error"
        if disk_status == "error":
            logger.error(
                f"flush_save 跳过：章节 {self.current_chapter_id} 磁盘读取失败")
            self._backup_draft_now(text)
            self._set_save_state(
                "error",
                "⚠ 磁盘暂不可读，已跳过保存（内容保留并已备份草稿）")
            self.set_pet_state("worried", hold_ms=4000)
            if self._pet_float is not None:
                try:
                    self._pet_float.set_modifier("tense", hold_ms=4000)
                except RuntimeError:
                    pass
            return
        if disk_text == text:
            self._set_save_state(
                "saved",
                f"已保存 {datetime.now().strftime('%H:%M:%S')}",
            )
            return

        new_count = self.work.write_chapter(self.current_chapter_id, text)
        delta = new_count - self._baseline_words
        if delta != 0:
            self.stats.add_words(delta)
        self._baseline_words = new_count
        # 写作陪伴奖励：净增字数 → 亲密度/心情（brain 内部每日封顶）
        if delta > 0 and self._pet_brain is not None:
            try:
                self._pet_brain.notify_writing(delta)
            except Exception:
                pass
            self._maybe_word_milestone()
            # B4：累计本章本次运行的净增字数，供切章章末点评使用
            cid = self.current_chapter_id
            self._pet_chapter_words[cid] = (
                self._pet_chapter_words.get(cid, 0) + delta)
        # 墨玉 / 长期记忆（净增字数才算产出；活跃时段只要有改动就记）
        economy = getattr(self, "_pet_economy", None)
        memory = getattr(self, "_pet_memory", None)
        if delta > 0 and economy is not None:
            try:
                economy.on_words(delta)
            except RuntimeError:
                pass
        if delta != 0 and memory is not None:
            try:
                memory.note_active_hour()
                memory.note_work(getattr(self.work, "title", ""))
            except RuntimeError:
                pass
        self.bookshelf.update_chapter_word_count(self.current_chapter_id, new_count)
        self._set_save_state(
            "saved",
            f"已自动保存 {datetime.now().strftime('%H:%M:%S')}",
        )
        self._update_word_status()
        if self._draft_dirty:
            try:
                from utils.crash_recovery import delete_draft

                delete_draft(self.work.root_dir, self.current_chapter_id)
            except Exception as e:
                logger.warning(f"删草稿失败: {e}")
            self._draft_dirty = False
        self._maybe_show_evening_summary()

    def _manual_save(self):
        if not self.work or not self.current_chapter_id:
            return
        try:
            self.flush_save()
        except Exception as e:
            logger.error(f"手动保存失败: {e}", exc_info=True)
            QMessageBox.warning(self, "保存失败", str(e))

    # ==================== 草稿 ====================

    def _backup_draft_now(self, text: str = None):
        if not self.work or not self.current_chapter_id:
            return
        if text is None:
            text = self.editor.get_text()
        if not text:
            return
        ch = self.work.get_chapter(self.current_chapter_id)
        title = ch.get("title", "") if ch else ""
        try:
            from utils.crash_recovery import write_draft

            write_draft(self.work.root_dir, self.current_chapter_id,
                        title, text)
            self._draft_dirty = False
        except Exception as e:
            logger.warning(f"兜底草稿写入失败: {e}")

    def _write_draft_if_dirty(self):
        if not self._draft_dirty:
            return
        if not self.work or not self.current_chapter_id:
            return
        text = self.editor.get_text()
        if not text:
            return
        ch = self.work.get_chapter(self.current_chapter_id)
        title = ch.get("title", "") if ch else ""
        try:
            from utils.crash_recovery import write_draft

            write_draft(self.work.root_dir, self.current_chapter_id, title, text)
            self._draft_dirty = False
        except Exception as e:
            logger.warning(f"写草稿失败: {e}")

    def _scan_pending_drafts(self):
        try:
            from utils.crash_recovery import list_all_pending, delete_draft

            drafts = list_all_pending()
        except Exception as e:
            logger.warning(f"扫描草稿失败: {e}")
            return
        if not drafts:
            return
        need = {}
        for d in drafts:
            work_root = d["work_root"]
            cid = d["chapter_id"]
            draft_text = d["text"]
            chapter_path = os.path.join(work_root, "chapters", f"{cid}.txt")
            if not os.path.exists(chapter_path):
                delete_draft(work_root, cid)
                continue
            try:
                with open(chapter_path, "r", encoding="utf-8", errors="ignore") as f:
                    chapter_text = f.read()
            except OSError:
                chapter_text = ""
            if chapter_text == draft_text:
                delete_draft(work_root, cid)
                continue
            key = os.path.normcase(work_root)
            need.setdefault(key, []).append(d)
        self._pending_drafts = need
        total = sum(len(v) for v in need.values())
        if total:
            logger.info(f"发现 {total} 份待恢复草稿")

    def _prompt_recover_drafts(self, work_root: str):
        if not work_root:
            return
        key = os.path.normcase(work_root)
        drafts = self._pending_drafts.pop(key, [])
        for d in drafts:
            self._prompt_one_draft(d)

    def _prompt_recover_drafts_quiet(self, work_root: str):
        if not work_root:
            return
        same_work = (
            self.work is not None
            and os.path.normcase(self.work.root_dir)
            == os.path.normcase(work_root)
        )
        if same_work:
            self._prompt_recover_drafts(work_root)
            return
        key = os.path.normcase(work_root)
        drafts = self._pending_drafts.pop(key, [])
        for d in drafts:
            self._deferred_drafts.append(d)
        self._update_draft_notice()

    def _prompt_one_draft(self, d):
        cid = d["chapter_id"]
        title = d["title"] or cid
        ts = d["updated_at"]
        text = d["text"]
        preview = text[:200].replace("\n", " ").strip()
        if len(text) > 200:
            preview += "…"
        box = QMessageBox(self)
        box.setWindowTitle("发现未保存内容")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"《{title}》有一份未保存内容（{ts}）")
        box.setInformativeText(
            f"共 {len(text):,} 字，预览：\n{preview}\n\n是否恢复到编辑器？"
        )
        recover_btn = box.addButton("恢复", QMessageBox.AcceptRole)
        discard_btn = box.addButton("放弃", QMessageBox.DestructiveRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.setDefaultButton(recover_btn)
        box.exec()
        chosen = box.clickedButton()
        work_root = d["work_root"]
        if chosen == recover_btn:
            same_work = self.work and os.path.normcase(
                self.work.root_dir
            ) == os.path.normcase(work_root)
            if same_work:
                self.open_chapter(cid)
                self.editor.load_text(text)
                self._draft_dirty = True
                self.save_state_label.setText(
                    "已恢复未保存内容，请检查后按 Ctrl+S 保存"
                )
                try:
                    self.flush_save()
                except Exception as e:
                    logger.warning(f"恢复草稿后立即保存失败: {e}")
            else:
                QMessageBox.information(
                    self,
                    "请先打开该作品",
                    f"这份草稿属于《{os.path.basename(work_root)}》，"
                    "请先在作品画廊打开它，再点状态栏的按钮恢复。",
                )
                self._deferred_drafts.append(d)
        elif chosen == discard_btn:
            try:
                from utils.crash_recovery import delete_draft

                delete_draft(work_root, cid)
            except Exception as e:
                logger.warning(f"删草稿失败: {e}")
        else:
            self._deferred_drafts.append(d)
            self._update_draft_notice()

    def _update_draft_notice(self):
        self._deferred_drafts = [
            d for d in self._deferred_drafts if os.path.exists(d["file"])
        ]
        n = len(self._deferred_drafts)
        if n == 0:
            self.draft_notice_btn.setVisible(False)
            self.draft_notice_btn.setText("")
            return
        self.draft_notice_btn.setText(f"  {n} 份未保存内容")
        self.draft_notice_btn.setVisible(True)

    def _on_draft_notice_clicked(self):
        if not self._deferred_drafts:
            return
        pending = list(self._deferred_drafts)
        self._deferred_drafts = []
        for d in pending:
            if not os.path.exists(d["file"]):
                continue
            self._prompt_one_draft(d)
        self._update_draft_notice()

    # ==================== 草稿提示与定时快照 ====================

    def _draft_notice_qss(self):
        t = self.editor._theme if self.editor else {}
        accent = t.get("accent", "#b08a4e")
        return (
            f"QPushButton {{ border: 1px solid {accent};"
            f" border-radius: 4px; padding: 2px 8px; font-size: 12px;"
            f" color: {accent}; background: transparent; }}"
            f"QPushButton:hover {{ background: {accent}33; }}"
        )

    def _auto_snapshot(self):
        if not self.work or not self.current_chapter_id:
            return
        try:
            self.flush_save()
        except Exception as e:
            logger.warning(f"定时快照前保存失败: {e}")
            return
        try:
            cur = self.work.read_chapter(self.current_chapter_id)
            versions = self.work.list_versions(self.current_chapter_id)
            if versions:
                with open(
                    versions[0]["file"], "r", encoding="utf-8", errors="ignore"
                ) as f:
                    if f.read() == cur:
                        return
            self.work.create_snapshot(self.current_chapter_id)
            logger.info(f"自动快照已生成: {self.current_chapter_id}")
        except Exception as e:
            logger.warning(f"定时快照失败: {e}")

