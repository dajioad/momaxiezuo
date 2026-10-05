# ui/workspace_parts/workspace_ui.py
"""墨码写作 - 工作台界面组装（Mixin）

从 workspace.py 拆出的纯 UI 构建层：
- _build_ui：三栏结构（活动侧栏+书架 | 编辑器 | 符号面板）、顶部工具栏、
  底部状态条、画廊/写作两页 QStackedWidget、查找/校对浮条等全部子控件创建
- 工具栏按钮工厂（主按钮/图标按钮/次按钮/分隔条/带菜单工具按钮）
- 空状态欢迎页
- _connect_signals：子控件信号到工作台处理槽的接线
方法只负责创建与接线，业务逻辑在其它 Mixin / 子控件自身。
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QLabel,
    QPushButton,
    QStackedWidget,
    QProgressBar,
    QLineEdit,
    QFrame,
    QScrollArea,
    QMenu,
    QToolButton,
)
from PySide6.QtCore import Qt, QTimer, QSize
import qtawesome as qta

from ui.writing_editor import WritingEditor
from ui.symbol_panel import SymbolPanel
from ui.pet_float import _PetFloat, _PetBubble
from ui.workspace_parts.find_bar import _FindBar
from ui.workspace_parts.proof_bar import _ProofBar
from ui.workspace_parts.chapter_header import _ChapterHeader
from ui.workspace_parts.activity_sidebar import _ActivitySidebar
from ui.workspace_parts.save_icon import _SaveIcon
from utils.logger import get_logger

logger = get_logger()


class WorkspaceUiMixin:
    """工作台 UI 构建与信号接线。方法直接访问 self.*。"""

    # ==================== UI 构建 ====================

    def _build_ui(self):
        self.setObjectName("wsRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = QFrame()
        self.toolbar.setObjectName("wsToolbar")
        self.toolbar.setFixedHeight(48)
        self._tb_seps = []
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(16, 8, 16, 8)
        tb.setSpacing(8)

        self.btn_back_gallery = self._secondary_btn(
            "fa5s.th-large", "作品", self._back_to_gallery
        )
        self.btn_back_gallery.setToolTip("返回作品画廊（Ctrl+W）")
        self.btn_prev = self._icon_btn(
            "fa5s.chevron-left", "上一章 (Ctrl+PgUp)", self._prev_chapter
        )
        self.btn_next = self._icon_btn(
            "fa5s.chevron-right", "下一章 (Ctrl+PgDown)", self._next_chapter
        )
        self.work_title_label = QLabel("墨码写作")
        self.work_title_label.setObjectName("workTitle")
        self.volume_label = QLabel("")
        self.volume_label.setObjectName("volumeLabel")
        for w in (
            self.btn_back_gallery,
            self.btn_prev,
            self.btn_next,
            self.work_title_label,
            self.volume_label,
        ):
            tb.addWidget(w)

        tb.addWidget(self._tb_sep())
        # 三个语义菜单（替代原来一个塞了 14 项的「更多」）
        self.btn_tb_tools = self._menu_tool_button(
            "fa5s.toolbox",
            "工具",
            [
                ("fa5s.search", "查找替换…\tCtrl+F", self.toggle_findbar),
                None,
                ("fa5s.spell-check", "校对当前章", self.open_proofread),
                ("fa5s.magic", "一键排版…", self.open_format_dialog),
                ("fa5s.wrench", "编辑工具箱…",
                 self.open_edit_toolbox),
                ("fa5s.clipboard-check", "发布前体检…",
                 self.open_publish_check),
                ("fa5s.mobile-alt", "读者视角预览…",
                 self.open_reader_preview),
            ],
        )
        self.btn_tb_tools.setToolTip("写作工具（校对 / 排版 / 查找 / 体检）")
        tb.addWidget(self.btn_tb_tools)

        self.btn_tb_chapter = self._menu_tool_button(
            "fa5s.list-ul",
            "章节",
            [
                ("fa5s.tags", "整理章节标题…", self._organize_all_titles),
                ("fa5s.object-ungroup", "从光标处拆分为新章节…",
                 self._split_at_cursor),
                None,
                ("fa5s.trash-alt", "章节回收站…", self.open_chapter_trash),
            ],
        )
        self.btn_tb_chapter.setToolTip("章节整理（拆分 / 标题 / 回收站）")
        tb.addWidget(self.btn_tb_chapter)

        self.btn_tb_export = self._menu_tool_button(
            "fa5s.paper-plane",
            "导出",
            [
                ("fa5s.file-export", "导出文档…", self.export_merged),
                ("fa5s.paper-plane", "发布到平台…", self._show_publish_menu),
            ],
        )
        self.btn_tb_export.setToolTip("导出 / 发布到平台")
        tb.addWidget(self.btn_tb_export)

        tb.addWidget(self._tb_sep())
        self.btn_ai = self._icon_btn(
            "fa5s.robot", "AI 助手 (Ctrl+J)", self.toggle_ai
        )
        self.btn_focus = self._icon_btn(
            "fa5s.expand", "专注模式 (F11)", self.toggle_focus
        )
        self.btn_settings = self._icon_btn(
            "fa5s.cog", "设置", lambda: self.open_settings.emit()
        )
        for b in (self.btn_ai, self.btn_focus, self.btn_settings):
            tb.addWidget(b)
        root.addWidget(self.toolbar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.welcome_page = self._build_welcome()
        self.stack.addWidget(self.welcome_page)

        work_page = QWidget()
        wp = QVBoxLayout(work_page)
        wp.setContentsMargins(0, 0, 0, 0)
        wp.setSpacing(0)

        editor_holder = QWidget()
        eh = QVBoxLayout(editor_holder)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.setSpacing(0)
        self.chapter_header = _ChapterHeader()
        self.chapter_header.title_changed.connect(self._on_chapter_title_changed)
        eh.addWidget(self.chapter_header)
        self.editor = WritingEditor()
        eh.addWidget(self.editor)
        self.find_bar = _FindBar()
        self.find_bar.setVisible(False)
        self.find_bar.setParent(editor_holder)
        self.find_bar.closed.connect(lambda: self.find_bar.setVisible(False))

        self.proof_bar = _ProofBar()
        self.proof_bar.setVisible(False)
        self.proof_bar.setParent(editor_holder)
        self.proof_bar.detail_clicked.connect(self._open_proofread_detail)
        self.proof_bar.clear_clicked.connect(self._clear_proofread)
        self.proof_bar.closed.connect(self._clear_proofread)
        self.proof_bar.filter_changed.connect(self._on_proof_filter_changed)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet(
            "QSplitter::handle { background: transparent; }"
            "QSplitter::handle:hover { background-color:"
            " rgba(176, 138, 78, 0.22); }"
        )
        # 活动栏侧栏：48px 图标栏 + 书架（可收起，VS Code 式）
        self.sidebar = _ActivitySidebar(self.store)
        self.bookshelf = self.sidebar.bookshelf
        self.sidebar.collapse_changed.connect(self._on_sidebar_collapsed)
        self.symbol_panel = SymbolPanel(self.cfg)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(editor_holder)
        self.splitter.addWidget(self.symbol_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([308, 672, 0])
        self._saved_sidebar_width = 260
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self._restore_sidebar_state()
        self.symbol_panel.setVisible(False)
        wp.addWidget(self.splitter)
        self.stack.addWidget(work_page)
        self._editor_holder = editor_holder

        # 状态栏
        self.statusbar = QFrame()
        self.statusbar.setObjectName("wsStatusbar")
        self.statusbar.setFixedHeight(32)
        sb = QHBoxLayout(self.statusbar)
        sb.setContentsMargins(16, 0, 16, 0)
        sb.setSpacing(8)
        self._sb_seps = []
        self._sb_icons = []

        def _sb_icon(name: str) -> QLabel:
            ic = QLabel()
            ic._icon_name = name
            ic.setFixedSize(14, 14)
            ic.setAlignment(Qt.AlignCenter)
            self._sb_icons.append(ic)
            return ic

        def _sb_sep() -> QFrame:
            sep = QFrame()
            sep.setObjectName("sbSep")
            sep.setFixedSize(1, 14)
            self._sb_seps.append(sep)
            return sep

        self.today_words_label = QLabel("今天 0 字")
        self.today_words_label.setObjectName("sbToday")

        self.word_count_label = QLabel("本章 0")
        self.word_count_label.setObjectName("sbChapter")
        self.chapter_target_label = QLabel("")
        self.chapter_target_label.setObjectName("sbTarget")
        self.chapter_target_label.setCursor(Qt.PointingHandCursor)
        self.chapter_target_label.setToolTip("点击设置本章字数目标")
        self.chapter_target_label.mousePressEvent = (
            lambda e: self._edit_chapter_target()
        )
        self.chapter_progress = QProgressBar()
        self.chapter_progress.setObjectName("sbChapterBar")
        self.chapter_progress.setTextVisible(False)
        self.chapter_progress.setFixedSize(72, 4)
        self.chapter_progress.setRange(0, 100)
        self.chapter_progress.setValue(0)
        self.chapter_progress.setVisible(False)

        self.draft_notice_btn = QPushButton(
            qta.icon("fa5s.exclamation-triangle", color="#c0392b"), ""
        )
        self.draft_notice_btn.setObjectName("draftNoticeBtn")
        self.draft_notice_btn.setVisible(False)
        self.draft_notice_btn.setCursor(Qt.PointingHandCursor)
        self.draft_notice_btn.setToolTip("点击恢复未保存内容")
        self.draft_notice_btn.clicked.connect(self._on_draft_notice_clicked)
        self.save_state_label = QLabel("")

        self.today_progress = QProgressBar()
        self.today_progress.setObjectName("sbTodayBar")
        self.today_progress.setTextVisible(False)
        self.today_progress.setFixedSize(96, 4)
        self.today_progress.setRange(0, 100)
        self.today_progress.setValue(0)
        self.today_progress.setToolTip("今日字数 / 每日目标")
        self.today_percent_label = QLabel("")
        self.today_percent_label.setObjectName("sbTodayPercent")
        self.today_percent_label.setFixedWidth(40)

        self.save_icon = _SaveIcon()
        sb.addWidget(self.save_icon)
        sb.addWidget(self.save_state_label)
        sb.addWidget(_sb_sep())
        sb.addWidget(_sb_icon("fa5s.pen-nib"))
        sb.addWidget(self.today_words_label)
        sb.addWidget(self.today_progress)
        sb.addWidget(self.today_percent_label)
        sb.addStretch()
        sb.addWidget(self.draft_notice_btn)

        # 本章字数/目标进度上移到章节标题行右侧
        rb = self.chapter_header.right_bar()
        rb.addWidget(self.word_count_label)
        rb.addWidget(self.chapter_target_label)
        rb.addWidget(self.chapter_progress)
        root.addWidget(self.statusbar)

        # ============ 独立顶层宠物（无父控件）============
        self._pet_float = _PetFloat(workspace=self)
        # clicked 不再连 _on_pet_clicked：request_chat 已经承担"打开对话"，
        # 两个信号都连同一个槽会导致单击打开后被第二次调用立刻关闭。
        self._pet_float.geometry_changed.connect(self._on_pet_moved)
        self._pet_float.request_chat.connect(self._on_pet_clicked)
        self._pet_float.request_quick_action.connect(
            self._on_pet_quick_action)

        self._pet_bubble = _PetBubble(None, workspace=self)
        self._pet_bubble.closed.connect(self._on_pet_bubble_closed)
        self._pet_bubble.request_started.connect(
            lambda: self.set_base_pet_state("thinking"))
        self._pet_bubble.request_finished.connect(
            lambda: self.set_base_pet_state("idle"))

        # 宠物 brain
        try:
            from ui.pet_system.brain import PetBrain
            self._pet_brain = PetBrain(self.store.data_root, parent=self)
            logger.info(f"[Pet] PetBrain 创建成功: {self._pet_brain}")
            self._pet_float.set_brain(self._pet_brain)
            # brain 有需求时切状态
            self._pet_brain.need_detected.connect(self._on_pet_need)
            logger.info("[Pet] need_detected 已连接")
        except Exception as e:
            import traceback
            logger.warning(f"[Pet] brain 初始化失败: {e}\n"
                           f"{traceback.format_exc()}")
            self._pet_brain = None

        # 经济系统（墨玉/背包/任务/成就）+ 长期记忆
        self._pet_economy = None
        self._pet_memory = None
        try:
            from ui.pet_system.economy import PetEconomy
            from ui.pet_system.memory import PetMemory
            self._pet_memory = PetMemory(self.store.data_root)
            self._pet_economy = PetEconomy(
                self.store.data_root, self._pet_brain, self.stats,
                parent=self)
            self._pet_float.set_economy(self._pet_economy)
            self._pet_economy.reward_granted.connect(self._on_pet_reward)
            self._pet_economy.achievement_unlocked.connect(
                self._on_pet_achievement)
            if self._pet_brain is not None:
                self._pet_brain.action_started.connect(
                    self._on_pet_action_record)
            # 启动时把今天的历史数据补一帧进每日回顾
            try:
                self._pet_memory.settle_today(self.stats)
            except Exception:
                pass
            logger.info("[Pet] 经济系统 + 长期记忆初始化完成")
        except Exception as e:
            import traceback
            logger.warning(f"[Pet] 经济/记忆初始化失败: {e}\n"
                           f"{traceback.format_exc()}")

        # 对话记忆提炼器（B2/B3：攒几轮后低 token 提炼事实）
        self._pet_chat_extractor = None
        try:
            from ui.pet_system.chat_memory import ChatMemoryExtractor
            self._pet_chat_extractor = ChatMemoryExtractor(
                self._pet_memory,
                config_getter=lambda: getattr(self, "cfg", None),
                parent=self)
        except Exception as e:
            logger.warning(f"[Pet] 对话记忆提炼器初始化失败: {e}")

        # B 批次主动陪伴的运行时状态
        # 本章会话内净增字数 {chapter_id: words}（切章/标记完成用）
        self._pet_chapter_words = {}
        # 本次运行已经 offer 过点评的章节（防刷屏）
        self._pet_review_offered = set()
        # 卡文提醒：本轮已提醒的章节 + 记录起点
        self._pet_block_nudged = set()
        self._block_words_at_session_start = None

        # brain 就绪后再同步一次体验开关（漫游门控依赖 brain）
        try:
            self._pet_float.refresh_pet_settings()
        except RuntimeError:
            pass

        # 宠物独立顶层：延迟到主窗口显示后再 show
        QTimer.singleShot(200, self._show_pet_initial)

    def _tb_btn(self, icon: str, text: str, slot) -> QPushButton:
        btn = QPushButton(qta.icon(icon, color="white"), f" {text}")
        btn._icon_name = icon
        btn.setProperty("tier", "primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _icon_btn(self, icon: str, tip: str, slot) -> QPushButton:
        btn = QPushButton()
        btn._icon_name = icon
        btn.setProperty("tier", "icon")
        btn.setIcon(qta.icon(icon, color=self._toolbar_icon_color()))
        btn.setFixedSize(32, 32)
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _secondary_btn(self, icon: str, text: str, slot) -> QPushButton:
        btn = QPushButton(
            qta.icon(icon, color=self._toolbar_icon_color()), f" {text}")
        btn._icon_name = icon
        btn.setProperty("tier", "secondary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _tb_sep(self) -> QFrame:
        sep = QFrame()
        sep.setObjectName("tbSep")
        sep.setFixedSize(1, 24)
        self._tb_seps.append(sep)
        return sep

    def _menu_tool_button(self, icon: str, text: str, items: list) -> QToolButton:
        btn = QToolButton()
        btn._icon_name = icon
        if text:
            btn.setText(f" {text}")
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        # 菜单弹出后 QAction 图标不归 QSS 管，必须显式按主题着色，
        # 否则深色模式下 qta 取系统默认色（黑），黑底黑图标看不见
        icon_color = self._toolbar_icon_color()
        btn.setIcon(qta.icon(icon, color=icon_color))
        btn.setIconSize(QSize(14, 14))
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setObjectName("tbMenuBtn")
        menu = QMenu(btn)
        for item in items:
            if item is None:
                menu.addSeparator()
                continue
            ic, action_text, cb = item
            act = menu.addAction(qta.icon(ic, color=icon_color), action_text)
            act._icon_name = ic   # 供主题切换时重新着色
            act.triggered.connect(lambda _=False, f=cb: f())
        btn.setMenu(menu)
        return btn

    def _toolbar_icon_color(self) -> str:
        """工具栏/菜单图标的当前主题色（editor 主题就绪前用浅色默认）。"""
        ed = getattr(self, "editor", None)
        t = getattr(ed, "_theme", None) if ed is not None else None
        if t:
            return t.get("text", "#3b342a")
        return "#3b342a"

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        page.setObjectName("galleryPlaceholder")
        self._gallery_placeholder = page
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._gallery_keyword = ""
        self._gallery_sort = self._load_gallery_sort_pref()

        identity = QWidget()
        identity.setObjectName("galleryIdentity")
        il = QHBoxLayout(identity)
        il.setContentsMargins(32, 20, 32, 16)
        il.setSpacing(12)

        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(2)
        self.gallery_title = QLabel("我的作品")
        self.gallery_title.setObjectName("galleryTitle")
        title_block.addWidget(self.gallery_title)
        self.gallery_stats_label = QLabel("")
        self.gallery_stats_label.setObjectName("galleryStatsLabel")
        title_block.addWidget(self.gallery_stats_label)
        il.addLayout(title_block)
        il.addStretch()

        self.gallery_search = QLineEdit()
        self.gallery_search.setObjectName("gallerySearch")
        self.gallery_search.setPlaceholderText("搜索书名 / 作者")
        self.gallery_search.setFixedSize(210, 34)
        self.gallery_search.setClearButtonEnabled(True)
        self.gallery_search.textChanged.connect(self._on_gallery_search)
        il.addWidget(self.gallery_search)

        self.gallery_trash_btn = QPushButton()
        self.gallery_trash_btn.setObjectName("galleryTrash")
        self.gallery_trash_btn.setFixedSize(34, 34)
        self.gallery_trash_btn.setCursor(Qt.PointingHandCursor)
        self.gallery_trash_btn.setToolTip("回收站 · 已删除的作品可恢复")
        self.gallery_trash_btn.clicked.connect(self.open_trash_dialog)
        il.addWidget(self.gallery_trash_btn)

        root.addWidget(identity)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        self._gallery_container = QWidget()
        self._gallery_container.setObjectName("galleryContainer")
        self._gallery_cards = []
        self._gallery_cols = 4
        self._gallery_max_cols = 8
        self._gallery_gap = 20
        self._hero_height = 380
        self._hero_gap = 28
        scroll.setWidget(self._gallery_container)

        self._insert_line = QFrame(self._gallery_container)
        self._insert_line.setObjectName("galleryInsertLine")
        self._insert_line.setFixedWidth(3)
        self._insert_line.setVisible(False)
        self._insert_line.setAttribute(Qt.WA_TransparentForMouseEvents)
        root.addWidget(scroll, 1)
        self._gallery_scroll = scroll
        scroll.viewport().installEventFilter(self)
        self._gallery_reflow_timer = QTimer(self)
        self._gallery_reflow_timer.setSingleShot(True)
        self._gallery_reflow_timer.setInterval(120)
        self._gallery_reflow_timer.timeout.connect(self._reflow_gallery)

        self._refresh_gallery()
        return page

    def _connect_signals(self):
        self.bookshelf.status_message.connect(self._on_bookshelf_status)
        self.bookshelf.open_chapter.connect(self.open_chapter)
        self.bookshelf.open_history.connect(self.open_version_history)
        self.bookshelf.work_switched.connect(self._on_work_switched)
        self.bookshelf.structure_changed.connect(self._update_titles)
        self.bookshelf.structure_changed.connect(self._on_structure_changed)
        self.bookshelf.chapter_marked_done.connect(
            self._show_chapter_done_toast)
        self.bookshelf.chapter_marked_done.connect(
            lambda _=None: self.set_pet_state("proud", hold_ms=3500))
        self.bookshelf.before_structure_change.connect(
            self._before_structure_change)
        self.editor.content_changed.connect(self._on_content_changed)
        self.editor.font_size_changed.connect(self._on_font_size_changed)
        self.editor.selection_changed.connect(self.symbol_panel.set_selection_text)
        self.editor.request_rewrite.connect(self.open_rewrite_dialog)
        self.editor.search_selection.connect(self._search_editor_selection)
        self.editor.inline_continue_requested.connect(self._on_inline_continue)
        sp = self.symbol_panel
        sp.insert_symbol.connect(self.editor.insert_text)
        sp.wrap_selected.connect(self.editor.wrap_selected)
        sp.prefix_lines.connect(lambda s: self.editor.modify_selected_lines(prefix=s))
        sp.suffix_lines.connect(lambda s: self.editor.modify_selected_lines(suffix=s))
        sp.convert_selected.connect(self.editor.convert_selected)
        sp.insert_divider.connect(self.editor.insert_divider)
        sp.edge_apply.connect(self.editor.apply_symbol_live)
        sp.revert_live.connect(self.editor.revert_symbol_live)
        self.bookshelf.wordbook.insert_word.connect(self.editor.insert_text)
        self.bookshelf.characters.insert_text.connect(self.editor.insert_text)
        self.bookshelf.outline.chapter_reference_clicked.connect(
            self._on_outline_reference_clicked)
        self.bookshelf.characters.data_changed.connect(
            self._refresh_dictionary_highlights
        )
        self.bookshelf.wordbook.data_changed.connect(
            self._refresh_dictionary_highlights
        )
        self.find_bar.prev.connect(self._do_find_prev)
        self.find_bar.next_.connect(self._do_find_next)
        self.find_bar.replace_one.connect(self._do_replace_one)
        self.find_bar.replace_all.connect(self._do_replace_all)
        self.find_bar.request_highlight.connect(self._do_highlight)
        self.find_bar.closed.connect(self._on_find_closed)
        self.stack.currentChanged.connect(self._on_stack_page_changed)

