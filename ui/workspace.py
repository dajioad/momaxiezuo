# ui/workspace.py
"""墨码写作 - 写作工作台

三栏结构：书架章节树 | 沉浸式编辑器 | 符号面板
AI 助手改为独立顶层宠物窗口（可跨屏幕 / 托盘态存活 / 可拖动 / 3 档尺寸）。

职责：
- 组合 bookshelf / writing_editor / symbol_panel / _PetFloat / _PetBubble
- 章节切换、自动保存（停顿3秒 + 30秒兜底）、码字统计联动
- 顶栏（作品操作/排版/校对/导出/发布/AI/专注）与底部统计条
- 写作主题与字号行距配置应用
- 发布：按分组跳转到各平台作家后台（只跳转，不自动提交）
- 封面全自绘：paintEvent 一次性完成物理像素映射，适配任意 dpr / 比例 / 平台
- 校对：单章校对（工具栏）+ 全书校对、词库管理（工具菜单）
- 校对高亮浮条：直接高亮正文 + 顶部浮条显示统计
- AI 改写 / AI 梗概 / AI 提取词条
- 崩溃恢复草稿
- 词条高亮：人物卡 + 高频词按各自颜色在正文中高亮

宠物交互：
- 单击 → toggle 对话面板
- 双击 → 切换尺寸档位（扒窗 / 半身 / 全身）
- 3 连击 → love
- 右键 → 功能菜单（对话 / 快捷操作 / 切换尺寸 / 隐藏）
- 拖动 → 自由移动（跨屏幕 / 位置持久化）

代码组织（3000+ 行时按职责拆成 Mixin，全部合并到本类）：
- workspace_parts/workspace_ui.py   界面组装、工具栏工厂、信号接线
- workspace_parts/stats_goals.py    字数统计/目标/里程碑/章末与晚报卡片
- workspace_parts/pet_integration.py 墨灵宠物胶水与主动陪伴
- workspace_parts/save_drafts.py    自动保存/定时快照/崩溃草稿恢复
- workspace_parts/dialog_launchers.py 工具栏各对话框入口
- workspace_parts/chapter_ops.py    章节导航与结构联动
- workspace_parts/workspace_theme.py 写作主题与字号偏好应用
- 画廊 / AI 功能 / 编辑辅助仍在 workspace_parts/ 下对应 Mixin
本文件只保留 __init__、定时器、作品切换、事件、布局/专注与退出收口。
"""

import os
import time

from PySide6.QtWidgets import QWidget, QMessageBox, QDialog
from PySide6.QtCore import QTimer, Signal, QEvent
import qtawesome as qta

from utils.work_store import WorkStore, Work
from utils.writing_stats import WritingStats
from utils.logger import get_logger
# 从 workspace.py 拆出的独立 widget/dialog
from ui.workspace_parts.gallery import GalleryMixin
from ui.workspace_parts.ai_features import AIFeaturesMixin
from ui.workspace_parts.editor_assist import EditorAssistMixin
from ui.workspace_parts.stats_goals import StatsGoalsMixin
from ui.workspace_parts.pet_integration import PetIntegrationMixin
from ui.workspace_parts.save_drafts import SaveDraftMixin
from ui.workspace_parts.dialog_launchers import DialogLaunchersMixin
from ui.workspace_parts.chapter_ops import ChapterOpsMixin
from ui.workspace_parts.workspace_theme import WorkspaceThemeMixin
from ui.workspace_parts.workspace_ui import WorkspaceUiMixin

logger = get_logger()


class WritingWorkspace(GalleryMixin, EditorAssistMixin, AIFeaturesMixin,
                      WorkspaceUiMixin, StatsGoalsMixin, PetIntegrationMixin,
                      SaveDraftMixin, DialogLaunchersMixin, ChapterOpsMixin,
                      WorkspaceThemeMixin, QWidget):
    open_settings = Signal()
    title_changed = Signal(str)
    focus_changed = Signal(bool)
    gallery_changed = Signal(bool)

    def __init__(self, config_manager, data_root: str = None, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.store = WorkStore(data_root) if data_root else WorkStore()
        self.stats = WritingStats(data_root) if data_root else WritingStats()
        self.work: Work = None
        self.current_chapter_id = None
        self._baseline_words = 0
        self._focus_mode = False
        self._last_heartbeat = 0.0
        # 画廊拖拽状态
        self._drag_src_card = None
        self._drag_insert_idx = 0
        self._drag_esc_shortcut = None
        self._app_state_connected = False
        # 崩溃恢复草稿
        self._draft_dirty = False
        self._deferred_drafts = []
        self._pending_drafts = {}
        # 校对结果缓存
        self._proof_results = []
        self._proof_filter = ""
        # 改写对话框缓存
        self._rewrite_dlg = None
        self._rewrite_range = None
        self._rewrite_original = ""
        self._edit_toolbox_dlg = None
        self._global_search_dlg = None
        self._inline_worker = None
        self._readability_dlg = None
        # hero 卡片右栏"最近章节"点击 → 打开作品后要跳的章节 id
        self._pending_chapter_id = None
        # 每日结算：本次会话内是否已弹过卡
        self._evening_summary_shown = False
        # 状态栏"已收工"灰化状态缓存
        self._last_settled_state = None
        # 宠物相关（构造 _build_ui 时初始化）
        self._last_user_activity = time.monotonic()
        self._intimacy_total = 0   # 已废弃，仅保留兼容（亲密度归 brain 管）
        self._last_greeting_ts = 0.0
        # 写作会话计时：距上次击键 >60s 视为新会话
        self._last_keystroke_ts = 0.0
        self._writing_session_start = 0.0
        self._long_session_reminded = False
        # 今日千字里程碑：已庆祝档位 + 归属日期
        self._milestone_k_celebrated = 0
        self._milestone_day = ""
        self._last_milestone_ts = 0.0
        self._today_goal_celebrated = False

        self._build_ui()
        self._connect_signals()
        self._build_timers()
        self.reload_preferences()
        self._refresh_welcome()
        QTimer.singleShot(2000, self._scan_pending_drafts)

    def _on_bookshelf_status(self, msg: str):
        self.save_state_label.setText(msg)
        QTimer.singleShot(2500, lambda: self.save_state_label.setText(""))

    def show_status_message(self, msg: str, timeout_ms: int = 3000):
        try:
            self.save_state_label.setText(msg or "")
            if msg and timeout_ms > 0:
                QTimer.singleShot(
                    timeout_ms, lambda: self.save_state_label.setText(""))
        except Exception:
            pass

    def _set_save_state(self, state: str, text: str = None):
        icon = getattr(self, "save_icon", None)
        if icon is not None:
            try:
                icon.set_state(state)
            except RuntimeError:
                pass
        if text is not None:
            self.save_state_label.setText(text)

    def _build_timers(self):
        cfg = self.cfg
        debounce_sec = 3
        tick_sec = 30
        if cfg:
            try:
                debounce_sec = int(cfg.get_config("Storage", "save_debounce_sec", "3"))
            except (TypeError, ValueError):
                pass
            try:
                tick_sec = int(cfg.get_config("Storage", "save_tick_sec", "30"))
            except (TypeError, ValueError):
                pass
        debounce_sec = max(1, min(30, debounce_sec))
        tick_sec = max(5, min(300, tick_sec))

        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(debounce_sec * 1000)
        self._save_debounce.timeout.connect(self.flush_save)

        self._save_tick = QTimer(self)
        self._save_tick.setInterval(tick_sec * 1000)
        self._save_tick.timeout.connect(self.flush_save)
        self._save_tick.start()

        # 字数状态显示节流：状态栏/标题头的更新要对全章正文跑一次
        # 去空白正则 + 全章查找，每次按键都跑在百万字大章上会明显
        # 吃输入性能。改为 350ms 尾随刷新；保存/切章等离散事件仍直接调
        # _update_word_status 立即刷新。
        self._word_status_timer = QTimer(self)
        self._word_status_timer.setSingleShot(True)
        self._word_status_timer.setInterval(350)
        self._word_status_timer.timeout.connect(self._update_word_status)

        self._auto_snapshot_timer = QTimer(self)
        self._auto_snapshot_timer.setInterval(15 * 60 * 1000)
        self._auto_snapshot_timer.timeout.connect(self._auto_snapshot)
        self._auto_snapshot_timer.start()

        self._draft_tick = QTimer(self)
        self._draft_tick.setInterval(3000)
        self._draft_tick.timeout.connect(self._write_draft_if_dirty)
        self._draft_tick.start()

        self._dict_refresh_timer = QTimer(self)
        self._dict_refresh_timer.setSingleShot(True)
        self._dict_refresh_timer.setInterval(800)
        self._dict_refresh_timer.timeout.connect(self._refresh_dictionary_highlights)

        # 猫娘：空闲检查（每 60 秒看一眼，>3min bored，>5min sleepy）
        self._pet_idle_timer = QTimer(self)
        self._pet_idle_timer.setInterval(60 * 1000)
        self._pet_idle_timer.timeout.connect(self._check_pet_idle)
        self._pet_idle_timer.start()

        # 猫娘：定时问候（每 30 分钟最多一次）
        self._pet_greeting_timer = QTimer(self)
        self._pet_greeting_timer.setInterval(30 * 60 * 1000)
        self._pet_greeting_timer.timeout.connect(self._maybe_greet)
        # 打开作品后 20 秒内先问候一次（等用户有初步交互）
        QTimer.singleShot(20000, self._maybe_greet)

        # 旧版亲密度（击键计数，存在 config.ini）一次性迁移到
        # pet_data.json；迁移后亲密度的唯一事实源是
        # PetBrain.state.intimacy
        self._intimacy_total = 0
        self._migrate_legacy_pet_intimacy()

    # ==================== 作品 ====================

    def create_new_work(self):
        from ui.work_edit_dialog import WorkCreateDialog
        dlg = WorkCreateDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not data.get("title"):
            return

        try:
            work = self.store.create_work(
                data["title"],
                data.get("author", ""),
                data.get("description", ""),
            )
            from utils.work_store import VALID_WORK_STATUSES
            meta_updates = {}
            if data.get("status") in VALID_WORK_STATUSES:
                meta_updates["status"] = data["status"]
            target = int(data.get("daily_target", 0) or 0)
            if target > 0:
                meta_updates["daily_target"] = target
            if meta_updates:
                work.meta.update(meta_updates)
                work.save_meta()

            self.bookshelf.refresh_works(work.root_dir)
            self.bookshelf.refresh_tree()
        except OSError as e:
            logger.error(f"新建作品失败: {e}", exc_info=True)
            QMessageBox.critical(self, "新建失败", str(e))

    def _on_work_switched(self, path: str):
        self.editor.abort_streams_for_reload()
        self._retire_inline_worker()
        self._close_rewrite_dialog()
        self.flush_save()
        self.work = self.bookshelf.work
        self.current_chapter_id = None
        self.editor.load_text("")
        self._baseline_words = 0
        self.stack.setCurrentIndex(1 if self.work else 0)
        self._update_titles()
        self._update_word_status()
        pending = getattr(self, "_pending_chapter_id", None)
        self._pending_chapter_id = None
        if pending and self.work and self.work.get_chapter(pending):
            self.open_chapter(pending)
        else:
            self._open_recent_chapter()
        if self.work:
            self._pet_bubble.set_conversation_key(
                os.path.basename(self.work.root_dir.rstrip("/\\"))
            )
            self.set_pet_state("surprised", hold_ms=1500)
        else:
            self._pet_bubble.set_conversation_key("")

        # 宠物 brain：切作品时 flush（保存旧状态）+ 重建（加载新状态）
        old_brain = getattr(self, "_pet_brain", None)
        if old_brain is not None:
            try:
                old_brain.flush()
                old_brain.shutdown()
                # 断开 workspace 直连的信号并销毁旧 brain，
                # 否则多次切作品会累积 QObject 与信号连接
                for sig_name, slot in (
                    ("need_detected", self._on_pet_need),
                    ("action_started", self._on_pet_action_record),
                ):
                    try:
                        getattr(old_brain, sig_name).disconnect(slot)
                    except (RuntimeError, AttributeError):
                        pass
                old_brain.setParent(None)
                old_brain.deleteLater()
            except Exception:
                pass
        try:
            from ui.pet_system.brain import PetBrain
            data_root = self.store.data_root
            self._pet_brain = PetBrain(data_root, parent=self)
            self._pet_float.set_brain(self._pet_brain)
            self._pet_brain.need_detected.connect(self._on_pet_need)
            # 经济系统重新挂到新 brain（成就判定要读 state）
            if getattr(self, "_pet_economy", None) is not None:
                self._pet_economy.attach_sources(
                    self._pet_brain, self.stats)
                self._pet_brain.action_started.connect(
                    self._on_pet_action_record)
            self._pet_float.refresh_pet_settings()
        except Exception as e:
            logger.warning(f"宠物 brain 重建失败: {e}")
            self._pet_brain = None
        if self.work:
            QTimer.singleShot(
                400,
                lambda p=self.work.root_dir:
                    self._prompt_recover_drafts_quiet(p),
            )

    def _is_gallery(self) -> bool:
        return self.stack.currentIndex() == 0

    def _on_stack_page_changed(self, idx: int):
        gallery = idx == 0
        chrome_visible = not gallery and not self._focus_mode
        self.toolbar.setVisible(chrome_visible)
        self.statusbar.setVisible(chrome_visible)
        if gallery:
            self.find_bar.setVisible(False)
            if hasattr(self, "proof_bar"):
                self.proof_bar.setVisible(False)
            # 回画廊：气泡收起（宠物保留，独立顶层）
            if self._pet_bubble is not None and self._pet_bubble.isVisible():
                try:
                    self._pet_bubble.request_close()
                except RuntimeError:
                    pass
        self.gallery_changed.emit(gallery)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is self._gallery_scroll.viewport():
            self._gallery_reflow_timer.start()
        return super().eventFilter(obj, event)

    def _refresh_welcome(self):
        self._refresh_gallery()
        self.stack.setCurrentIndex(0)
        self._refresh_gallery_theme()
        self._on_stack_page_changed(0)

    # ==================== 事件 ====================

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.find_bar.isVisible():
            self._reposition_findbar()
        if getattr(self, "proof_bar", None) and self.proof_bar.isVisible():
            self._reposition_proofbar()
        self._relayout()

    def showEvent(self, event):
        super().showEvent(event)

    # ==================== 布局 ====================

    def _relayout(self):
        total = max(self.splitter.width(), 600)
        book = 0 if self._focus_mode else min(260, total // 4)
        if not self.symbol_panel.isHidden():
            sym = min(max(220, total // 4), 300)
        else:
            sym = 0
        editor = max(total - book - sym, 320)
        self.splitter.setSizes([book, editor, sym])

    def toggle_ai(self):
        """工具栏 AI 图标 / Ctrl+J：切换对话面板。"""
        if self._is_gallery():
            return
        if self._pet_bubble.isVisible():
            self._pet_bubble.request_close()
            return
        self._on_pet_clicked()

    # ---------- 侧栏活动栏：收起 / 宽度 / 标签持久化 ----------

    def _restore_sidebar_state(self):
        """启动时恢复侧栏：活动标签 + 收起状态（[UI] 段）。"""
        tab, collapsed = 0, False
        if self.cfg is not None:
            try:
                tab = int(self.cfg.get_config("UI", "sidebar_tab", "0") or 0)
                collapsed = str(
                    self.cfg.get_config("UI", "sidebar_collapsed", "0")
                    or "0").strip() in ("1", "true", "True")
                w = int(self.cfg.get_config(
                    "UI", "sidebar_width", "260") or 260)
                self._saved_sidebar_width = max(180, min(w, 600))
            except (TypeError, ValueError):
                pass
        self.sidebar.set_tab(tab)
        self.bookshelf.tabs.currentChanged.connect(
            lambda idx: self._save_ui_cfg("sidebar_tab", str(idx)))
        if collapsed:
            # 直接压到活动栏宽度；展开宽度留到展开时恢复
            self.sidebar.set_collapsed(True)
            self.splitter.setSizes([48, 900, 0])
        else:
            self.splitter.setSizes(
                [48 + self._saved_sidebar_width, 720, 0])

    def _save_ui_cfg(self, key: str, value: str):
        if self.cfg is None:
            return
        try:
            self.cfg.set_config("UI", key, value, validate=False)
        except Exception:
            pass

    def _on_splitter_moved(self, pos: int, index: int):
        """侧栏展开时，记录书架宽度（去掉 48px 活动栏）。"""
        if index != 0 or self.sidebar.collapsed:
            return
        total = self.splitter.widget(0).width()
        self._saved_sidebar_width = max(
            180, total - 48)
        self._save_ui_cfg(
            "sidebar_width", str(self._saved_sidebar_width))

    def _on_sidebar_collapsed(self, collapsed: bool):
        self._save_ui_cfg(
            "sidebar_collapsed", "1" if collapsed else "0")
        sizes = self.splitter.sizes()
        if not sizes:
            return
        if collapsed:
            # 活动栏 48，其余空间给编辑器
            self.splitter.setSizes([48, max(400, sizes[1] + sizes[0] - 48),
                                    sizes[2]])
        else:
            want = self._saved_sidebar_width + 48
            self.splitter.setSizes(
                [want, max(400, sizes[1] - (want - sizes[0])), sizes[2]])

    def toggle_focus(self):
        if self._is_gallery():
            return
        self._focus_mode = not self._focus_mode
        focus = self._focus_mode
        self.toolbar.setVisible(not focus)
        self.sidebar.setVisible(not focus)
        self.statusbar.setVisible(not focus)
        self.editor.set_focus_mode(focus)
        icon_color = (
            self.editor._theme.get("text", "#3b342a") if self.editor else "#3b342a"
        )
        if focus:
            self._focus_stash_symbol = self.symbol_panel.isVisible()
            self._focus_stash_ai = self._pet_bubble.isVisible()
            self.symbol_panel.setVisible(False)
            self._pet_bubble.hide()
            self._pet_float.set_hold_expanded(False)
            self.find_bar.setVisible(False)
            self.btn_focus.setIcon(qta.icon("fa5s.compress", color=icon_color))
            self.btn_focus.setToolTip("退出专注模式(F11 / ESC)")
        else:
            self.sidebar.setVisible(True)
            if getattr(self, "_focus_stash_symbol", False):
                self.symbol_panel.setVisible(True)
            if getattr(self, "_focus_stash_ai", False):
                self._on_pet_clicked()
            self.btn_focus.setIcon(qta.icon("fa5s.expand", color=icon_color))
            self.btn_focus.setToolTip("专注模式(F11)")
        self._relayout()
        self.focus_changed.emit(focus)

    def export_merged(self):
        if not self.work:
            QMessageBox.information(self, "导出", "请先打开一部作品。")
            return
        self.flush_save()
        self._export_work_obj(self.work)

    # ==================== 退出 ====================

    def on_shutdown(self):
        """退出保存。

        重要：QThread 不是守护线程。若 worker 在跑却被 GC，会触发
        "QThread: Destroyed while thread is still running" 使进程 abort。
        退出前必须 stop + wait 所有 worker。
        """
        workers = []

        # 1. AI 宠物气泡对话
        try:
            w = getattr(self._pet_bubble, "_worker", None)
            if w is not None:
                workers.append(w)
        except Exception:
            pass

        # 2. 编辑器光标续写
        try:
            w = getattr(self, "_inline_worker", None)
            if w is not None:
                workers.append(w)
        except Exception:
            pass

        # 3. AI 追读体检
        try:
            dlg = getattr(self, "_readability_dlg", None)
            if dlg is not None:
                w = getattr(dlg, "_ai_worker", None)
                if w is not None:
                    workers.append(w)
        except Exception:
            pass

        # 4. 剧本对话框
        try:
            dlg = getattr(self, "_script_dlg", None)
            if dlg is not None:
                w = getattr(dlg, "_worker", None)
                if w is not None:
                    workers.append(w)
        except Exception:
            pass

        # 5. AI 改写对比
        try:
            dlg = getattr(self, "_rewrite_dlg", None)
            if dlg is not None:
                for w in getattr(dlg, "_workers", []) or []:
                    if w is not None:
                        workers.append(w)
        except Exception:
            pass

        # 6-9. 其他 AI worker
        for attr in ("_foreshadow_worker", "_summary_worker",
                     "_entities_worker", "_relation_worker",
                     "_proofread_all_worker"):
            try:
                w = getattr(self, attr, None)
                if w is not None:
                    workers.append(w)
            except Exception:
                pass

        # 10. 编辑器幽灵续写
        try:
            w = getattr(self.editor, "_quick_continue_worker", None)
            if w is not None:
                workers.append(w)
        except Exception:
            pass

        # 11. 批量 AI（墨灵试读 / 批量提取）在途 worker
        try:
            w = getattr(self, "_batch_ai_worker", None)
            if w is not None:
                workers.append(w)
        except Exception:
            pass

        # 统一 stop + wait
        for w in workers:
            if w is None:
                continue
            try:
                if w.isRunning():
                    w.stop()
            except (RuntimeError, AttributeError):
                pass
        for w in workers:
            if w is None:
                continue
            try:
                if w.isRunning():
                    w.wait(2000)
            except RuntimeError:
                pass

        # 兜底 terminate
        for w in workers:
            if w is None:
                continue
            try:
                if w.isRunning():
                    w.terminate()
                    w.wait(500)
            except RuntimeError:
                pass

        # 排空 Reaper 收留的其他 worker（如快速续写骨架框取消后、
        # 各对话框 reject 后仍在收尾的流式线程）
        try:
            from utils.ai_client import drain_workers
            drain_workers()
        except Exception:
            pass

        # 保存
        save_errors = []
        try:
            self.flush_save()
        except Exception as e:
            logger.error(f"退出保存章节失败: {e}", exc_info=True)
            save_errors.append(e)
        # 亲密度已迁移到 pet_data.json（由 _pet_brain.shutdown 落盘），
        # 不再写 config.ini 的 pet_intimacy_total
        try:
            self.bookshelf.outline.flush()
        except Exception as e:
            logger.error(f"退出保存大纲失败: {e}", exc_info=True)
            save_errors.append(e)
        try:
            self._pet_bubble.flush_conversation()
        except Exception as e:
            logger.error(f"退出保存 AI 对话失败: {e}", exc_info=True)
            save_errors.append(e)

        # 关闭宠物 brain（保存状态）前，先落经济/记忆
        try:
            if getattr(self, "_pet_economy", None) is not None:
                self._pet_economy.save()
        except Exception:
            pass
        try:
            if getattr(self, "_pet_memory", None) is not None:
                self._pet_memory.settle_today(self.stats)
                self._pet_memory.save()
        except Exception:
            pass
        try:
            if getattr(self, "_pet_brain", None) is not None:
                self._pet_brain.shutdown()
        except Exception:
            pass
        try:
            if getattr(self, "_pet_chat_extractor", None) is not None:
                self._pet_chat_extractor.shutdown()
        except Exception:
            pass

        # 显式关闭顶层宠物窗口（否则进程可能不退出）
        try:
            if self._pet_bubble is not None:
                self._pet_bubble.close()
        except Exception:
            pass
        try:
            if self._pet_float is not None:
                self._pet_float.close()
        except Exception:
            pass

        if save_errors:
            raise save_errors[0]