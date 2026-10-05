# ui/bookshelf.py
"""墨码写作 - 书架与章节树

- 作品的新建/切换/重命名/删除等一律在作品图墙（初始界面）完成
- QTreeWidget 两级：卷（虚拟分组）→ 章节
- 章节支持拖拽排序/跨卷移动，拖拽结果回写 book.json
- 章节状态：草稿（空心圆）/ 已完成（绿勾）；顶部可按状态筛选
- 多选：ExtendedSelection（Ctrl / Shift / Ctrl+A）
- 批量操作：移动、删除
- 拖拽限制：章节只能同级排序或拖入卷，不能作为容器，不能拖成顶层
- 拖拽视觉：自绘强调色横线（Qt 原生 dropIndicator 在短行高下会
  退化为 item 高亮，看不清会插到第几章前/后）
- 边缘自动滚动：拖到视口顶部/底部时列表自动滚动，方便跨长篇移动
- 默认容器「正文」：根容器，可重命名但不可删除（参考 Scrivener
  Manuscript）；用户自定义卷可重命名/删除
- O2：拖拽章节后可按 Ctrl+Z 撤销（只还原顺序与卷归属，一步）
"""

import os
import re
import time
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QLabel,
    QMenu,
    QInputDialog,
    QMessageBox,
    QTabWidget,
    QComboBox,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QApplication,
    QDialog,
    QToolButton,
)
from ui.character_panel import CharacterPanel
from PySide6.QtGui import (
    QAction,
    QIcon,
    QPainter,
    QColor,
    QPen,
    QCursor,
    QShortcut,
    QKeySequence,
    QPainterPath,
    QBrush,
)
from PySide6.QtCore import (
    Signal,
    Qt,
    QTimer,
    QRect,
    QRectF,
    QSize,
)
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss, SEMANTIC_BAD

from utils.work_store import (
    WorkStore,
    Work,
    DEFAULT_VOLUME,
    CHAPTER_STATUS_DONE,
    CHAPTER_STATUS_DRAFT,
    CHAPTER_TRASH_RETENTION_DAYS,
)
from utils.chapter_order import find_out_of_order
from ui.writing_editor import accent_foreground, _mix_hex
from ui.word_book import WordBookPanel
from ui.outline_panel import OutlinePanel
from utils.logger import get_logger

logger = get_logger()

_ITEM_TYPE = Qt.UserRole + 1
_ITEM_ID = Qt.UserRole + 2
_ITEM_WORDS = Qt.UserRole + 3
_ITEM_STATUS = Qt.UserRole + 4  # "done" / "draft"，供 delegate 画书脊
_ITEM_FLASH = Qt.UserRole + 5  # 0-255 闪光强度；0 = 无。驱动书脊脉冲。


class _ChapterTreeDelegate(QStyledItemDelegate):
    """章节树委托：书脊 + 标题 + 右侧字数。

    左侧 3px 竖条（书脊）颜色表示章节状态：
    - 绿  #4CAF50  已完成
    - 灰  半透明 muted  草稿
    - 金  accent  当前选中

    标题在缩进后绘制，长标题尾部用 … 截断；
    右侧右对齐显示字数。用 setClipRect 硬裁保证不重叠。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._muted = QColor("#9a8e76")
        self._accent = QColor("#b08a4e")
        self._done = QColor("#4CAF50")
        self._text = QColor("#3b342a")

    def set_muted_color(self, color: str):
        self._muted = QColor(color)

    def set_accent_color(self, color: str):
        self._accent = QColor(color)

    def set_text_color(self, color: str):
        self._text = QColor(color)

    def paint(self, painter, option, index):
        item_type = index.data(_ITEM_TYPE)
        wc = index.data(_ITEM_WORDS)
        status = index.data(_ITEM_STATUS)

        # ---- 卷节点 / 无字数的项：走默认绘制 ----
        if wc is None and item_type != "chapter":
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        fm = opt.fontMetrics

        # 书脊宽度：占 3px，左侧留 4px 缝隙
        SPINE_W = 3
        SPINE_GAP = 4
        spine_x = opt.rect.left() - SPINE_W - SPINE_GAP

        # 标题绘制区：从 item 左边开始，但要扣掉右侧字数
        if wc is not None:
            count_text = f"{int(wc):,} 字"
            cw = fm.horizontalAdvance(count_text) + 10
        else:
            count_text = ""
            cw = 0

        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        text_left = text_rect.left()
        title_right = opt.rect.right() - cw
        available = max(0, title_right - text_left)

        title = opt.text or ""
        opt.text = fm.elidedText(title, Qt.ElideRight, available)

        painter.save()
        painter.setClipRect(text_left, opt.rect.top(), available, opt.rect.height())
        super().paint(painter, opt, index)
        painter.restore()

        # ---- 右侧字数 ----
        # 选中背景在 QSS 里是"淡金"（accent 20%），不是深色。
        # HighlightedText 是给深色选中背景用的（通常是白字），
        # 叠在淡金上几乎看不见——改成正文色，选中后对比反而更强。
        if count_text:
            painter.save()
            if option.state & QStyle.State_Selected:
                painter.setPen(self._text)
            else:
                painter.setPen(self._muted)
            count_rect = QRect(
                title_right + 2, opt.rect.top(), cw - 2, opt.rect.height()
            )
            painter.drawText(count_rect, Qt.AlignRight | Qt.AlignVCenter, count_text)
            painter.restore()

        # ---- 书脊竖条（只在章节节点画） ----
        if item_type == "chapter":
            is_selected = bool(option.state & QStyle.State_Selected)

            # 闪光强度 0-255 → f ∈ [0.0, 1.0]
            flash = index.data(_ITEM_FLASH) or 0
            try:
                flash = int(flash)
            except (TypeError, ValueError):
                flash = 0
            f = max(0, min(255, flash)) / 255.0

            # 基础色
            if is_selected:
                base = QColor(self._accent)
            elif status == "done":
                base = QColor(self._done)
            else:
                base = QColor(self._muted)
                base.setAlpha(0x99)

            # 闪光时往亮绿插值（不用突变，避免"咣一下变绿"）
            if f > 0:
                bright = QColor("#4CAF50").lighter(125)
                r = int(base.red() + (bright.red() - base.red()) * f)
                g = int(base.green() + (bright.green() - base.green()) * f)
                b = int(base.blue() + (bright.blue() - base.blue()) * f)
                a = int(base.alpha() + (255 - base.alpha()) * f)
                color = QColor(r, g, b, a)
            else:
                color = base

            # 宽度：3px → 6px（扩张）
            extra = int(3 * f)
            bar_rect = QRect(
                spine_x - extra // 2,
                opt.rect.top() + 3 - extra // 2,
                SPINE_W + extra,
                max(2, opt.rect.height() - 6 + extra),
            )

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)

            # 外发光：先画一圈半透明的绿扩散（只在闪光时）
            if f > 0:
                glow = QColor("#4CAF50")
                glow.setAlpha(int(110 * f))
                glow_rect = bar_rect.adjusted(-2, -1, 2, 1)
                painter.setBrush(glow)
                radius = (SPINE_W + extra) / 2.0 + 2
                painter.drawRoundedRect(glow_rect, radius, radius)

            # 主体
            painter.setBrush(color)
            radius = (SPINE_W + extra) / 2.0
            painter.drawRoundedRect(bar_rect, radius, radius)
            painter.restore()


def _event_pos(event):
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


class _DropIndicator(QWidget):
    """拖拽落点提示：一条横跨树宽的强调色水平线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(4)
        self._color = QColor("#b08a4e")
        self.hide()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        mid = self.height() // 2
        p.setPen(QPen(self._color, 2))
        p.drawLine(0, mid, self.width(), mid)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(0, mid - 3, 7, 7)
        p.drawEllipse(self.width() - 8, mid - 3, 7, 7)
        p.end()


class BookshelfPanel(QWidget):
    open_chapter = Signal(str)
    open_history = Signal(str)
    work_switched = Signal(str)
    structure_changed = Signal()
    # 章节被标记为"已完成"时发射，携带第一个章节 id。
    # workspace 收到后弹"通关卡"（视觉反馈，不动数据）。
    chapter_marked_done = Signal(str)
    # 章节结构即将变更（用户已确认、尚未写盘）时发射。
    # workspace 收到后先 flush 当前编辑，保证合并/重排/改名等操作
    # 读到的是最新正文，也避免事后重载用磁盘旧文顶掉未落盘编辑。
    before_structure_change = Signal()
    status_message = Signal(str)
    # 拖拽撤销栈最大深度
    MAX_DRAG_UNDO = 10

    def __init__(self, work_store: WorkStore, parent=None):
        super().__init__(parent)
        self.setObjectName("bookshelfRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.store = work_store
        self.work: Work = None
        self._suppress_events = False
        self._internal_move = False
        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(30)
        self._drag_scroll_timer.timeout.connect(self._do_drag_scroll)
        self._drag_scroll_delta = 0
        # O2：拖拽撤销栈（最多保留 MAX_DRAG_UNDO 步）
        self._drag_undo_stack: list = []
        self._build_ui()
        self.refresh_works(auto_open=False)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        chapter_tab = QWidget()
        cl = QVBoxLayout(chapter_tab)
        cl.setContentsMargins(0, 8, 0, 0)
        cl.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("显示："))
        self.status_filter = QComboBox()
        self.status_filter.setFixedWidth(100)
        self.status_filter.addItem("全部", "all")
        self.status_filter.addItem("草稿", "draft")
        self.status_filter.addItem("已完成", "done")
        self.status_filter.setToolTip(
            "按章节状态筛选\n" "「草稿」= 还没写完；「已完成」= 打过勾的章节"
        )
        self.status_filter.currentIndexChanged.connect(self._on_status_filter_changed)
        filter_row.addWidget(self.status_filter)
        filter_row.addStretch()
        cl.addLayout(filter_row)

        self.tree = QTreeWidget()
        self._tree_delegate = _ChapterTreeDelegate(self.tree)
        self.tree.setItemDelegate(self._tree_delegate)
        self.tree.setHeaderHidden(True)
        # 缩进从 16 减到 8：只有「卷 → 章节」两层，
        # 16px 的缩进让章节标题离左边缘太远。
        self.tree.setIndentation(8)
        self.tree.setAnimated(False)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.setDropIndicatorShown(False)
        self.tree.viewport().setAcceptDrops(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        self.tree.itemChanged.connect(self._on_item_changed)
        # O2：Ctrl+Z 撤销上一次拖拽移动（焦点在章节树或编辑器时都生效；
        # Qt.WidgetWithChildrenShortcut 表示"焦点在本控件或其子控件时"，
        # 不会抢走编辑器的 Ctrl+Z）
        sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self.tree)
        sc_undo.setContext(Qt.WidgetWithChildrenShortcut)
        sc_undo.activated.connect(self._undo_drag)
        self.tree.dragMoveEvent = self._tree_drag_move
        self.tree.dropEvent = self._tree_drop
        self.tree.dragLeaveEvent = self._tree_drag_leave
        cl.addWidget(self.tree, 1)

        self._drop_indicator = _DropIndicator(self.tree.viewport())

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        self.add_chapter_btn = QPushButton(" 新章节")
        self.add_chapter_btn.setCursor(Qt.PointingHandCursor)
        self.add_chapter_btn.setFixedHeight(32)
        self.add_chapter_btn.clicked.connect(self._add_chapter)
        bottom_row.addWidget(self.add_chapter_btn, 1)

        # 「导入」按钮改成带下拉菜单：从文件 / 从文件夹
        # 紧凑图标按钮：侧栏窄，文字按钮三个太挤
        self.import_btn = QToolButton()
        self.import_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.import_btn.setPopupMode(QToolButton.InstantPopup)
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.setFixedSize(36, 32)
        self.import_btn.setToolTip("导入：从文件 / 从文件夹批量导入")
        self.import_btn.setObjectName("importMenuBtn")
        _import_menu = QMenu(self.import_btn)
        act_file = _import_menu.addAction("从文件导入…")
        act_file.triggered.connect(self._import_txt)
        _import_menu.addSeparator()
        act_folder = _import_menu.addAction("从文件夹导入…")
        act_folder.triggered.connect(self._import_folder)
        self.import_btn.setMenu(_import_menu)
        bottom_row.addWidget(self.import_btn)

        # 章节回收站：显式入口按钮（有内容时图标变色，计数见 tooltip）
        self.trash_btn = QPushButton()
        self.trash_btn._icon_name = "fa5s.trash-alt"
        self.trash_btn._has_trash = False
        self.trash_btn.setCursor(Qt.PointingHandCursor)
        self.trash_btn.setFixedSize(36, 32)
        self.trash_btn.setToolTip("打开章节回收站，查看/恢复已删除的章节")
        self.trash_btn.clicked.connect(self.open_chapter_trash)
        bottom_row.addWidget(self.trash_btn)

        cl.addLayout(bottom_row)

        self.stats_label = QLabel("")
        cl.addWidget(self.stats_label)

        self.tabs.addTab(chapter_tab, "章节")
        self.outline = OutlinePanel()  # 变量名保留，避免 workspace 里的引用出错
        self.tabs.addTab(self.outline, "资料")
        self.characters = CharacterPanel()
        self.tabs.addTab(self.characters, "人物")
        self.wordbook = WordBookPanel()
        self.tabs.addTab(self.wordbook, "高频词")
        layout.addWidget(self.tabs, 1)

        self._refresh_styles()

    def _refresh_styles(self, theme: dict = None):
        theme = theme or {
            "sidebar": "#efe8d8",
            "accent": "#b08a4e",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
        }
        self._theme = theme
        sidebar = theme["sidebar"]
        accent = theme["accent"]
        accent_fg = theme.get("accent_fg") or accent_foreground(accent)
        accent_hover = theme.get("accent_hover") or _mix_hex(accent, accent_fg, 0.18)
        text = theme.get("text", "#3b342a")
        muted = theme.get("muted", "#9a8e76")
        self._tree_delegate.set_muted_color(muted)
        self._tree_delegate.set_accent_color(accent)
        self._tree_delegate.set_text_color(text)
        # 注意：不要再往本子树注入 selector="QPushButton" 的实底样式——
        # 那会让大纲/人物/设定等子面板里的每个按钮都变成主色实心。
        # 主按钮只有「新章节」一个（下方 per-widget 着色），其余走全局
        # controls_qss 的中性默认样式，形成主次层级。
        self.setStyleSheet(f"""
            #bookshelfRoot {{ background-color: {sidebar}; }}
            QWidget {{ color: {text}; background: transparent; }}
            QTreeWidget {{
                background-color: {theme.get('bg', sidebar)};
                border: none; color: {text};
                font-size: 13px;
            }}
            QTreeWidget::item {{ padding: 8px 6px; border-radius: 8px;
                margin: 1px 2px; }}
            QTreeWidget::item:selected {{ background-color: {qss_alpha(accent, 0x1F)};
                color: {text}; font-weight: 600; }}
            QTreeWidget::item:hover {{ background-color: {qss_alpha(accent, 0x10)}; }}
            QTreeWidget QLineEdit {{
                background-color: {theme.get('editor_bg', '#fcfaf4')};
                color: {text};
                border: 1px solid {accent};
                border-radius: 8px;
                padding: 4px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            QComboBox {{
                background-color: {theme.get('editor_bg', '#fcfaf4')};
                color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 8px;
                padding: 3px 10px;
                font-size: 12px;
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background-color: {theme.get('editor_bg', '#fcfaf4')};
                color: {text};
                selection-background-color: {qss_alpha(accent, 0x33)};
                selection-color: {text};
                border: 1px solid {qss_alpha(muted, 0x55)};
                border-radius: 8px;
            }}
            QLabel#statsLabel {{ color: {muted};
                border-top: 1px solid {qss_alpha(muted, 0x1E)};
                padding: 8px 2px 0; }}
            /* Tab：与全局统一的胶囊 chips（选中=强调色淡底） */
            QTabBar::tab {{
                background-color: transparent;
                color: {muted}; padding: 6px 16px;
                border: none; border-radius: 9px;
                margin: 1px 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {qss_alpha(accent, 0x18)};
                color: {accent}; font-weight: 600;
            }}
            QTabBar::tab:hover {{
                background-color: {qss_alpha(muted, 0x12)};
                color: {text};
            }}
        """)
        self.stats_label.setObjectName("statsLabel")
        self.stats_label.setStyleSheet(
            f"color: {muted}; font-size: 12px;"
            f" border-top: 1px solid {qss_alpha(muted, 0x1E)};"
            f" padding: 8px 2px 0;")
        btn_ss = accent_button_qss(
            accent, accent_fg, accent_hover, selector="QPushButton",
            padding="0 12px"
        )
        self.add_chapter_btn.setStyleSheet(btn_ss)
        self.add_chapter_btn.setIcon(qta.icon("fa5s.plus", color=accent_fg))

        # 「导入」：中性图标方块，hover 描边/图标变强调色
        self.import_btn.setStyleSheet(f"""
QToolButton#importMenuBtn {{
    color: {text};
    background-color: {qss_alpha(muted, 0x10)};
    border: 1px solid {qss_alpha(muted, 0x35)};
    border-radius: 8px;
    padding: 0; font-size: 12px; }}
QToolButton#importMenuBtn:hover {{
    color: {accent};
    background-color: {qss_alpha(accent, 0x14)};
    border-color: {accent}; }}
QToolButton#importMenuBtn::menu-indicator {{
    image: none; width: 0; }}
QToolButton#importMenuBtn::menu-button {{
    border: none; width: 0; }}
""")
        self.import_btn.setIcon(qta.icon("fa5s.file-import", color=muted))

        # 回收站：图标方块；有回收内容时描边变强调色（计数在 tooltip）
        if hasattr(self, "trash_btn") and self.trash_btn is not None:
            self.trash_btn.setStyleSheet(
                f"QPushButton {{ color: {text};"
                f" background: {qss_alpha(muted, 0x10)};"
                f" border: 1px solid {qss_alpha(muted, 0x35)};"
                f" border-radius: 8px; padding: 0; font-size: 12px; }}"
                f"QPushButton[hasTrash=\"true\"] {{"
                f" border: 1px solid {qss_alpha(accent, 0x88)};"
                f" background: {qss_alpha(accent, 0x12)}; }}"
                f"QPushButton:hover {{ color: {accent};"
                f" border-color: {accent};"
                f" background: {qss_alpha(accent, 0x14)}; }}"
            )
            trash_color = (accent if getattr(
                self.trash_btn, "_has_trash", False) else muted)
            self.trash_btn.setIcon(
                qta.icon("fa5s.trash-alt", color=trash_color))

        if hasattr(self, "_drop_indicator"):
            self._drop_indicator.set_color(accent)

    def apply_theme(self, theme: dict):
        self._refresh_styles(theme)
        self.outline.apply_theme(theme)
        self.characters.apply_theme(theme)
        self.wordbook.apply_theme(theme)
        # 主题切换后重刷一遍章节前景色：
        # 上一版本 _update_order_warnings 会把无警告项的前景色钉死在
        # 旧主题色上；改了 else 分支后不会再钉，但**已经钉过的** item
        # 不会自动解开，必须在这里重跑一次把它清掉。
        if self.work is not None:
            self._update_order_warnings()

    # ---------- 作品 ----------
    def refresh_works(self, select_path: str = None, auto_open: bool = True):
        if not auto_open or not select_path:
            return
        if self.work is not None and os.path.normcase(select_path) == os.path.normcase(
            self.work.root_dir
        ):
            return
        if any(
            os.path.normcase(w["path"]) == os.path.normcase(select_path)
            for w in self.store.list_works()
        ):
            self._open_path(select_path)

    def _open_path(self, path: str):
        work = self.store.open_work(path)
        if work is None:
            QMessageBox.warning(self, "打开失败", "该作品数据已损坏或不存在。")
            return
        self.load_work(work)
        self.work_switched.emit(path)

    def load_work(self, work: Work):
        # 关键修复：切换作品时清空拖拽撤销栈。
        # 栈里的 state 记录的是**旧作品**的章节顺序和卷归属，而
        # _undo_drag 用 self.work.chapters 按 id 匹配还原——不同作品的
        # 章节 id 都是 "0001"/"0002" 这种格式，会撞车：旧作品的卷名/
        # 顺序被套到新作品上（"按 Ctrl+Z 其他作品的卷跑过来"的根因）。
        self._drag_undo_stack.clear()
        self.work = work
        # 打开作品时清理回收站里超期的章节（保留期内不动）
        try:
            n = work.cleanup_trashed()
            if n:
                self.status_message.emit(f"已清理 {n} 个过期章节")
        except Exception as e:
            logger.warning(f"清理过期章节失败: {e}")
        self.refresh_tree()
        self.outline.set_work(work)
        self.characters.set_work(work)
        self.wordbook.set_work(work)

    # ---------- 章节树 ----------
    def _on_status_filter_changed(self, _idx: int):
        self.refresh_tree()

    def refresh_tree(self):
        self._suppress_events = True
        self.tree.clear()
        if self.work is None:
            self.stats_label.setText("")
            self._suppress_events = False
            return

        volume_items = {}
        default_name = self.work.get_default_volume_name()
        filter_val = "all"
        if hasattr(self, "status_filter"):
            filter_val = self.status_filter.currentData() or "all"

        # 顺序检测：全局按章节列表顺序扫描，标红倒退/重复的章节
        # （无论当前筛选是全部/草稿/已完成，都用全量章节做检测，
        # 否则只看已完成章节会跳号）
        order_warnings = find_out_of_order(self.work.chapters)
        # 语义色常量，避免硬编码值漂移（之前有 #E74C3C / #e74c3c
        # 两种写法）。以后想统一调整"错误色"只需改 theme_style。
        warn_color = QColor(SEMANTIC_BAD)

        def _get_volume_item(volume: str) -> QTreeWidgetItem:
            key = volume or DEFAULT_VOLUME
            if key in volume_items:
                return volume_items[key]
            display = default_name if not volume else volume
            item = QTreeWidgetItem([display])
            item.setData(0, _ITEM_TYPE, "volume")
            item.setData(0, _ITEM_ID, key)
            item.setIcon(
                0,
                qta.icon(
                    "fa5s.book",
                    color=(getattr(self, "_theme", None) or {}).get("muted", "#9a8e76"),
                ),
            )
            item.setFlags(
                Qt.ItemIsEnabled
                | Qt.ItemIsSelectable
                | Qt.ItemIsDropEnabled
                | Qt.ItemIsEditable
            )
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)
            volume_items[key] = item
            return item

        if filter_val == "all":
            _get_volume_item("")
            for v in self.work.volumes():
                if v != DEFAULT_VOLUME:
                    _get_volume_item(v)

        for c in self.work.chapters:
            status = c.get("status") or CHAPTER_STATUS_DRAFT
            if filter_val == "draft" and status != CHAPTER_STATUS_DRAFT:
                continue
            if filter_val == "done" and status != CHAPTER_STATUS_DONE:
                continue
            vol_item = _get_volume_item(c.get("volume", ""))
            ch_item = QTreeWidgetItem()
            self._render_chapter_item(ch_item, c)
            ch_item.setData(0, _ITEM_TYPE, "chapter")
            ch_item.setData(0, _ITEM_ID, c["id"])
            ch_item.setFlags(
                Qt.ItemIsEnabled
                | Qt.ItemIsSelectable
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsEditable
            )
            # 顺序警告：倒退 / 重复 → 标题标红 + tooltip 显示原因
            warn = order_warnings.get(c["id"])
            if warn:
                ch_item.setForeground(0, warn_color)
                ch_item.setToolTip(0, warn)
            vol_item.addChild(ch_item)

        if filter_val != "all" and self.tree.topLevelItemCount() == 0:
            placeholder = QTreeWidgetItem(
                [f"没有{'草稿' if filter_val == 'draft' else '已完成'}章节"]
            )
            placeholder.setFlags(Qt.ItemIsEnabled)
            t = getattr(self, "_theme", None) or {}
            placeholder.setForeground(0, QColor(t.get("muted", "#9a8e76")))
            f = placeholder.font(0)
            f.setItalic(True)
            placeholder.setFont(0, f)
            self.tree.addTopLevelItem(placeholder)

        self.stats_label.setText(
            f"共 {len(self.work.chapters)} 章 · {self.work.total_words():,} 字"
        )
        # 回收站里若有内容：图标变强调色 + tooltip 带计数
        if hasattr(self, "trash_btn") and self.trash_btn is not None:
            n_trash = len(self.work.trashed_chapters)
            self.trash_btn._has_trash = bool(n_trash)
            self.trash_btn.setProperty("hasTrash", "true" if n_trash else "false")
            self.trash_btn.setToolTip(
                f"章节回收站：{n_trash} 章待清理\n点击查看/恢复已删除的章节"
                if n_trash else "打开章节回收站，查看/恢复已删除的章节")
            self.trash_btn.style().unpolish(self.trash_btn)
            self.trash_btn.style().polish(self.trash_btn)
            try:
                accent = (self._theme or {}).get("accent", "#b08a4e")
                muted = (self._theme or {}).get("muted", "#9a8e76")
                self.trash_btn.setIcon(qta.icon(
                    "fa5s.trash-alt", color=accent if n_trash else muted))
            except Exception:
                pass
        self._suppress_events = False

    def _render_chapter_item(self, item: QTreeWidgetItem, chapter: dict):
        done = chapter.get("status") == CHAPTER_STATUS_DONE
        t = getattr(self, "_theme", None) or {}
        muted = t.get("muted", "#9a8e76")
        icon = (
            qta.icon("fa5s.check-circle", color=muted)
            if done
            else qta.icon("fa5s.circle", color=muted)
        )
        item.setText(0, chapter.get("title", "未命名"))
        item.setIcon(0, icon)
        item.setData(0, _ITEM_WORDS, int(chapter.get("word_count", 0)))
        # 书脊用：状态透传给 delegate
        item.setData(
            0, _ITEM_STATUS, CHAPTER_STATUS_DONE if done else CHAPTER_STATUS_DRAFT
        )

    def _chapter_item(self, chapter_id: str) -> QTreeWidgetItem:
        for top in range(self.tree.topLevelItemCount()):
            vol = self.tree.topLevelItem(top)
            for j in range(vol.childCount()):
                it = vol.child(j)
                if (
                    it.data(0, _ITEM_TYPE) == "chapter"
                    and it.data(0, _ITEM_ID) == chapter_id
                ):
                    return it
        return None

    def current_chapter_id(self):
        it = self.tree.currentItem()
        if it and it.data(0, _ITEM_TYPE) == "chapter":
            return it.data(0, _ITEM_ID)
        return None

    def set_current_chapter(self, chapter_id: str):
        it = self._chapter_item(chapter_id)
        if it:
            self.tree.setCurrentItem(it)

    def refresh_chapter_status(self, chapter_id: str):
        c = self.work.get_chapter(chapter_id)
        it = self._chapter_item(chapter_id)
        if c and it:
            self._render_chapter_item(it, c)

    def update_chapter_word_count(self, chapter_id: str, word_count: int):
        it = self._chapter_item(chapter_id)
        if it is not None:
            it.setData(0, _ITEM_WORDS, int(word_count))
        if self.work is not None:
            self.stats_label.setText(
                f"共 {len(self.work.chapters)} 章 · {self.work.total_words():,} 字"
            )

    # ---------- 交互 ----------
    def _on_double_clicked(self, item: QTreeWidgetItem, _column):
        if item.data(0, _ITEM_TYPE) == "chapter":
            self.open_chapter.emit(item.data(0, _ITEM_ID))
        elif item.data(0, _ITEM_TYPE) == "volume":
            self.tree.editItem(item)

    def _show_menu(self, pos):
        if self.work is None:
            return
        clicked = self.tree.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { padding: 4px; } QMenu::item { padding: 6px 20px; }")

        selected_items = self.tree.selectedItems()
        selected_ids = [
            it.data(0, _ITEM_ID)
            for it in selected_items
            if it.data(0, _ITEM_TYPE) == "chapter"
        ]
        clicked_id = clicked.data(0, _ITEM_ID) if clicked is not None else None

        if (
            clicked is not None
            and clicked.data(0, _ITEM_TYPE) == "chapter"
            and clicked_id in selected_ids
            and len(selected_ids) > 1
        ):
            chapter_ids = selected_ids
        elif clicked is not None and clicked.data(0, _ITEM_TYPE) == "chapter":
            chapter_ids = [clicked_id]
        else:
            chapter_ids = []

        is_multi = len(chapter_ids) > 1

        if chapter_ids:
            cid = chapter_ids[0]
            chapter = self.work.get_chapter(cid)
            n = len(chapter_ids)

            act_open = act_add = act_rename = act_history = None
            act_status = None
            act_organize = None
            act_merge = None
            act_status_done_batch = act_status_draft_batch = None
            if not is_multi:
                act_open = menu.addAction("打开章节")
                menu.addSeparator()
                act_add = menu.addAction("在其后新建章节")
                act_rename = menu.addAction("重命名")
                # 合并到上一章：判断本卷内是否第一章
                cur_vol = (chapter.get("volume") or "").strip() if chapter else ""
                same_vol = [
                    ch
                    for ch in self.work.chapters
                    if (ch.get("volume") or "").strip() == cur_vol
                ]
                idx_in_vol = next(
                    (i for i, ch in enumerate(same_vol) if ch["id"] == cid), -1
                )
                act_merge = menu.addAction("合并到本卷上一章")
                act_merge.setToolTip(
                    "把本章内容追加到本卷上一章末尾，本章删除；"
                    "合并后按 Ctrl+Z 可撤销"
                )
                if idx_in_vol <= 0:
                    act_merge.setEnabled(False)
                    act_merge.setToolTip("这是本卷第一章，没有上一章可合并")
                act_organize = menu.addAction("整理章节标题…")
                act_history = menu.addAction("历史版本…")
                if chapter and chapter.get("status") == CHAPTER_STATUS_DONE:
                    act_status = menu.addAction("标记为草稿")
                else:
                    act_status = menu.addAction("标记为已完成")
            else:
                header = menu.addAction(f"已选中 {n} 个章节")
                header.setEnabled(False)
                menu.addSeparator()
                act_organize = menu.addAction(f"整理章节标题（{n} 章）…")
                status_menu = menu.addMenu("批量标记状态")
                act_status_done_batch = status_menu.addAction("全部标记为已完成")
                act_status_draft_batch = status_menu.addAction("全部标记为草稿")

            menu.addSeparator()

            custom_vols = [v for v in self.work.volumes() if v]
            move_label = f"移动 {n} 个章节到卷" if is_multi else "移动到卷"

            if custom_vols:
                move_menu = menu.addMenu(move_label)
                for v in custom_vols:
                    act_v = move_menu.addAction(v)
                    act_v.setData(("__vol__", v))
                cur_vol = chapter.get("volume", "") if chapter else ""
                if cur_vol:
                    move_menu.addSeparator()
                    act_back = move_menu.addAction(f"移回「{DEFAULT_VOLUME}」")
                    act_back.setData(("__vol__", DEFAULT_VOLUME))
                move_menu.addSeparator()
                act_new_vol = move_menu.addAction("＋ 新建卷并移入…")
                act_new_vol.setData(("__new_vol__", None))
            else:
                act_new_vol = menu.addAction("移动到新卷…")
                act_new_vol.setData(("__new_vol__", None))

            menu.addSeparator()
            act_delete = menu.addAction(f"删除 {n} 个章节" if is_multi else "删除章节")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action is None:
                return

            if act_open is not None and action == act_open:
                self.open_chapter.emit(cid)
            elif act_add is not None and action == act_add:
                self._add_chapter(after_id=cid)
            elif act_rename is not None and action == act_rename:
                self.tree.editItem(clicked)
            elif act_merge is not None and action == act_merge:
                self._merge_to_previous(cid)
            elif act_organize is not None and action == act_organize:
                self._organize_titles(chapter_ids)
            elif act_history is not None and action == act_history:
                self.open_history.emit(cid)
            elif act_status is not None and action == act_status and chapter:
                new_status = (
                    CHAPTER_STATUS_DRAFT
                    if chapter.get("status") == CHAPTER_STATUS_DONE
                    else CHAPTER_STATUS_DONE
                )
                # 走统一入口，让单章标记也触发闪光 + 通关卡
                self._set_chapters_status([cid], new_status)
            elif act_status_done_batch is not None and action == act_status_done_batch:
                self._set_chapters_status(chapter_ids, CHAPTER_STATUS_DONE)
            elif (
                act_status_draft_batch is not None and action == act_status_draft_batch
            ):
                self._set_chapters_status(chapter_ids, CHAPTER_STATUS_DRAFT)
            elif action == act_delete:
                if is_multi:
                    self._delete_chapters(chapter_ids)
                else:
                    title = chapter.get("title", "") if chapter else ""
                    self._delete_chapter(cid, title)
            elif isinstance(action.data(), tuple):
                self._handle_move_to_volume(chapter_ids, action.data())
            return

        volume = ""
        if clicked is not None and clicked.data(0, _ITEM_TYPE) == "volume":
            volume = clicked.data(0, _ITEM_ID) or ""
        act_add = menu.addAction("新建章节")
        act_new_vol = menu.addAction("新建卷")
        act_rename_vol = act_del_vol = None
        if clicked is not None and clicked.data(0, _ITEM_TYPE) == "volume":
            if volume != DEFAULT_VOLUME:
                menu.addSeparator()
                act_rename_vol = menu.addAction("重命名卷")
                act_del_vol = menu.addAction("删除卷（章节移至正文）")
            else:
                menu.addSeparator()
                act_rename_vol = menu.addAction("重命名")

        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == act_add:
            self._add_chapter(volume=("" if volume == DEFAULT_VOLUME else volume))
        elif action == act_new_vol:
            self._create_volume()
        elif act_rename_vol is not None and action == act_rename_vol:
            self.tree.editItem(clicked)
        elif act_del_vol is not None and action == act_del_vol:
            self._delete_volume(volume)

    def _handle_move_to_volume(self, chapter_ids, data):
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]
        if not chapter_ids:
            return
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind, vol_name = data

        if kind == "__new_vol__":
            text, ok = QInputDialog.getText(self, "新建卷", "卷名（如：第一卷 风起）：")
            if not ok:
                return
            vol_name = text.strip()
            if not vol_name:
                return
            if vol_name == DEFAULT_VOLUME:
                QMessageBox.information(
                    self, "提示", f"「{DEFAULT_VOLUME}」是默认卷，请使用其他名称。"
                )
                return
            self.work.add_volume(vol_name)

        target = "" if vol_name == DEFAULT_VOLUME else vol_name

        # 章节落卷前先让 workspace flush 当前编辑，避免事后重载
        # 用磁盘旧文顶掉未落盘内容。
        self.before_structure_change.emit()
        changed = False
        for cid in chapter_ids:
            ch = self.work.get_chapter(cid)
            if ch is None:
                continue
            if ch.get("volume", "") == target:
                continue
            # 直接改字段，不调 set_chapter_volume —— 那个方法每次都
            # save_meta 一次盘（20 章 = 20 次 JSON 写 + fsync）。
            # 批量改完统一写一次。
            ch["volume"] = target
            changed = True
        if changed:
            self.work.save_meta()
            self.refresh_tree()
            self.structure_changed.emit()

    def _add_chapter(self, after_id: str = None, volume: str = None):
        if self.work is None:
            return
        existing = len(self.work.chapters)
        default_title = f"第{existing + 1}章"
        title, ok = QInputDialog.getText(
            self, "新建章节", "章节标题：", text=default_title
        )
        if not ok or not title.strip():
            return
        if volume is None:
            cur = self.tree.currentItem()
            if cur and cur.data(0, _ITEM_TYPE) == "chapter":
                ch = self.work.get_chapter(cur.data(0, _ITEM_ID))
                volume = ch.get("volume", "") if ch else ""
            elif cur and cur.data(0, _ITEM_TYPE) == "volume":
                v = cur.data(0, _ITEM_ID)
                volume = "" if v == DEFAULT_VOLUME else v
            else:
                volume = ""
        self.before_structure_change.emit()
        chapter = self.work.add_chapter(title.strip(), volume=volume or "")
        if after_id:
            idx = self.work.chapter_index(after_id)
            if idx >= 0:
                self.work.move_chapter(chapter["id"], idx + 1)
        self.refresh_tree()
        self.structure_changed.emit()
        self.open_chapter.emit(chapter["id"])

    def _delete_chapter(self, cid: str, title: str):
        ret = QMessageBox.question(
            self,
            "删除章节",
            f"确定删除「{title}」吗？\n\n"
            "章节会移入「章节回收站」，可以在那里恢复；\n"
            f"{CHAPTER_TRASH_RETENTION_DAYS} 天内不彻底删除，之后自动清理。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self.before_structure_change.emit()
        self.work.delete_chapter(cid)
        self.refresh_tree()
        self.structure_changed.emit()

    def _delete_chapters(self, chapter_ids):
        if not chapter_ids:
            return
        n = len(chapter_ids)
        ret = QMessageBox.question(
            self,
            "批量删除章节",
            f"确定删除选中的 {n} 个章节吗？\n\n"
            "章节会移入「章节回收站」，可以在那里恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self.before_structure_change.emit()
        for cid in chapter_ids:
            self.work.delete_chapter(cid)
        self.refresh_tree()
        self.structure_changed.emit()

    def _set_chapters_status(self, chapter_ids, status):
        if not chapter_ids:
            return
        self.before_structure_change.emit()
        changed_ids = []
        for cid in chapter_ids:
            ch = self.work.get_chapter(cid)
            if ch is None:
                continue
            if ch.get("status") == status:
                continue
            self.work.set_chapter_status(cid, status)
            changed_ids.append(cid)
        if changed_ids:
            self.refresh_tree()
            self.structure_changed.emit()
            # 章节完成时闪一下，给写作者一个"通关"的即时反馈
            if status == CHAPTER_STATUS_DONE:
                for cid in changed_ids:
                    self._flash_chapter_row(cid)
                # 通知 workspace 弹"通关卡"（取第一个章节作为代表）
                self.chapter_marked_done.emit(changed_ids[0])

    def _flash_chapter_row(self, chapter_id: str):
        """章节标记为完成时，书脊脉冲一下。

        用 30ms 一帧的 QTimer 驱动 _ITEM_FLASH 从 0→255→0（sin 曲线），
        delegate 每帧按强度绘制书脊的宽度/亮度/外发光。
        总时长 1200ms，约 40 帧——视觉上是"呼吸一下"，不是"咣一下变绿"。
        """
        item = self._chapter_item(chapter_id)
        if item is None:
            return

        import math

        # 若同一个 item 上还有旧的脉冲在跑，先停掉
        old = getattr(item, "_flash_token", None)
        if old is not None:
            old["abort"] = True

        token = {"abort": False}
        try:
            item._flash_token = token
        except Exception:
            pass

        frame_ms = 30
        n_frames = 40  # 30ms * 40 ≈ 1200ms

        timer = QTimer(self)
        timer.setInterval(frame_ms)
        state = {"i": 0}

        def _tick():
            if token["abort"]:
                timer.stop()
                timer.deleteLater()
                return
            i = state["i"]
            if i >= n_frames:
                try:
                    item.setData(0, _ITEM_FLASH, 0)
                    rect = self.tree.visualItemRect(item)
                    self.tree.viewport().update(rect)
                except RuntimeError:
                    pass
                timer.stop()
                timer.deleteLater()
                return
            t = i / max(1, n_frames - 1)
            level = int(255 * math.sin(math.pi * t))
            try:
                item.setData(0, _ITEM_FLASH, level)
                rect = self.tree.visualItemRect(item)
                # 只重绘那一行的矩形，不整树刷（500 章也不会卡）
                self.tree.viewport().update(rect)
            except RuntimeError:
                timer.stop()
                timer.deleteLater()
                return
            state["i"] = i + 1

        timer.timeout.connect(_tick)
        timer.start()

    def _organize_titles(self, chapter_ids):
        """整理指定章节的标题（章节树右键入口）。

        - 选中 1 章 → 只整理这 1 章
        - 选中 N 章 → 只整理这 N 章
        - 整理全部请走工具栏「整理 ▾」→「整理章节标题…」
        """
        if self.work is None or not chapter_ids:
            return
        if isinstance(chapter_ids, str):
            chapter_ids = [chapter_ids]

        # 按 book.json 的原始顺序取出对应章节
        id_set = set(chapter_ids)
        chapters = [
            {"id": c.get("id", ""), "title": c.get("title", "")}
            for c in self.work.chapters
            if c.get("id") in id_set
        ]
        if not chapters:
            return

        if len(chapters) == 1:
            scope_label = "整理章节标题"
        else:
            scope_label = f"整理章节标题（{len(chapters)} 章）"

        from ui.import_title_dialog import open_organize_dialog

        renames = open_organize_dialog(chapters, parent=self, scope_label=scope_label)
        if not renames:
            return

        self.before_structure_change.emit()
        n = self.work.rename_chapters_batch(renames)
        self.refresh_tree()
        self.structure_changed.emit()
        self.status_message.emit(f"已整理 {n} 个章节标题")

    def _merge_to_previous(self, cid: str):
        """把当前章合并到**同卷内的上一章**。"""
        if self.work is None:
            return
        cur = self.work.get_chapter(cid)
        if cur is None:
            return
        cur_vol = (cur.get("volume") or "").strip()

        same_vol = [
            ch
            for ch in self.work.chapters
            if (ch.get("volume") or "").strip() == cur_vol
        ]
        idx_in_vol = next((i for i, ch in enumerate(same_vol) if ch["id"] == cid), -1)
        if idx_in_vol <= 0:
            return

        prev = same_vol[idx_in_vol - 1]
        cur_title = cur.get("title", "")
        prev_title = prev.get("title", "")
        vol_label = cur_vol or "正文"

        ret = QMessageBox.question(
            self,
            "合并章节",
            f"把「{cur_title}」合并到「{prev_title}」？\n\n"
            f"（都在「{vol_label}」卷内）\n\n"
            "当前章的内容会追加到上一章末尾，当前章被删除。\n"
            "（合并前会自动打快照，可在历史版本中找回）\n"
            "（合并后如果不满意，在章节树按 Ctrl+Z 可撤销）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return

        # 关键：合并前必须让 workspace 把当前编辑落盘。
        # merge 从磁盘取两章正文，未落盘内容不进合并结果，且来源章
        # 随后被永久软删——事后再补保存已经来不及。
        self.before_structure_change.emit()
        result = self.work.merge_chapter_into_previous(cid)
        if result is None:
            QMessageBox.warning(self, "合并失败", "无法合并（没有上一章）。")
            return

        self.refresh_tree()
        self.set_current_chapter(result)
        # 选中项滚到可视区，避免"看不见"被误认为位置错了
        it = self._chapter_item(result)
        if it is not None:
            self.tree.scrollToItem(it)
        self.structure_changed.emit()
        self.status_message.emit(f"已合并到「{prev_title}」· 按 Ctrl+Z 撤销")

    def open_chapter_trash(self):
        """打开章节回收站对话框。"""
        if self.work is None:
            return
        from ui.chapter_trash_dialog import ChapterTrashDialog

        dlg = ChapterTrashDialog(self.work, self)
        dlg.changed.connect(self._on_chapter_trash_changed)
        dlg.exec()

    def _on_chapter_trash_changed(self):
        """回收站里有恢复/彻底删除/清空——刷新章节树。"""
        self.refresh_tree()
        self.structure_changed.emit()

    def _create_volume(self):
        name, ok = QInputDialog.getText(self, "新建卷", "卷名（如：第一卷 风起）：")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name == DEFAULT_VOLUME:
            QMessageBox.information(
                self, "提示", f"「{DEFAULT_VOLUME}」是默认卷，请使用其他名称。"
            )
            return
        self.before_structure_change.emit()
        if not self.work.add_volume(name):
            QMessageBox.information(self, "提示", f"卷「{name}」已存在。")
            return
        self.refresh_tree()
        it = None
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, _ITEM_ID) == name:
                it = top
                break
        if it is not None:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)
        self.structure_changed.emit()

    def _delete_volume(self, volume: str):
        if not volume or volume == DEFAULT_VOLUME:
            return
        n = sum(1 for c in self.work.chapters if (c.get("volume") or "") == volume)
        if n > 0:
            msg = (
                f"删除卷「{volume}」？\n\n"
                f"卷内 {n} 个章节将移至「{DEFAULT_VOLUME}」，不会删除章节。"
            )
        else:
            msg = f"删除空卷「{volume}」？"
        if QMessageBox.question(
            self, "删除卷", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.before_structure_change.emit()
        self.work.delete_volume(volume)
        self.refresh_tree()
        self.structure_changed.emit()
        if n > 0:
            self.status_message.emit(
                f"已删除卷「{volume}」，{n} 个章节移至「{DEFAULT_VOLUME}」"
            )
        else:
            self.status_message.emit(f"已删除空卷「{volume}」")

    def _import_txt(self):
        if self.work is None:
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要导入的文件",
            "",
            "文本文件 (*.txt);;Markdown (*.md);;Word 文档 (*.docx *.doc);;所有文件 (*)",
        )
        if not path:
            return

        # ---- 第一步：读文件前先检测结构（TXT / DOCX / MD 都检测）----
        use_volumes = False
        lower = path.lower()

        # 读探测文本：TXT 直接读，DOCX 走 docx 解析
        try:
            if lower.endswith(".docx"):
                probe = Work._read_docx_text(path) or ""
            else:
                probe = Work._read_text_file(path)
        except Exception:
            probe = ""

        structure = Work.detect_chapter_structure(probe)

        # MD 特殊处理：如果有 Markdown 标题（# 开头），走原生 MD 逻辑
        if lower.endswith(".md") or lower.endswith(".markdown"):
            has_md_heading = any(
                re.match(r"^\s*#{1,3}\s+\S", ln) for ln in probe.splitlines()
            )
            if has_md_heading:
                structure = {"has_volumes": False}

        # 检测到卷结构 → 弹确认对话框
        if structure.get("has_volumes"):
            from ui.import_chapters_dialog import ImportChaptersDialog

            fname = os.path.basename(path)
            dlg = ImportChaptersDialog(fname, structure, parent=self)
            if dlg.exec() != QDialog.Accepted:
                return
            use_volumes = dlg.use_volumes()

        # ---- 第二步：实际导入 ----
        before_count = len(self.work.chapters)
        self.before_structure_change.emit()
        try:
            if lower.endswith(".md") or lower.endswith(".markdown"):
                count = self.work.import_markdown(path, use_volumes=use_volumes)
            elif lower.endswith(".docx"):
                count = self.work.import_docx(path, use_volumes=use_volumes)
            else:
                count = self.work.import_txt_split(path, use_volumes=use_volumes)
        except Exception as e:
            logger.error(f"导入文件失败: {e}", exc_info=True)
            QMessageBox.critical(self, "导入失败", f"读取文件出错：\n{e}")
            return

        if count <= 0:
            QMessageBox.information(self, "导入完成", "文件中没有识别到章节。")
            self.refresh_tree()
            self.structure_changed.emit()
            return

        # ---- 第二步：弹标题整理 ----
        new_chapters = self.work.chapters[before_count:]
        try:
            from ui.import_title_dialog import ImportTitleDialog

            dlg = ImportTitleDialog(new_chapters, parent=self)
            result = dlg.exec()
            if result == QDialog.Accepted:
                renames = dlg.get_renames()
                if renames:
                    n = self.work.rename_chapters_batch(renames)
                    QMessageBox.information(
                        self, "导入完成", f"已导入 {count} 章，整理标题 {n} 个。"
                    )
                else:
                    QMessageBox.information(
                        self, "导入完成", f"已导入 {count} 章（未改动标题）。"
                    )
            else:
                QMessageBox.information(
                    self, "导入完成", f"已导入 {count} 章（标题保持原样）。"
                )
        except Exception as e:
            logger.error(f"标题整理失败: {e}", exc_info=True)
            QMessageBox.warning(
                self,
                "标题整理失败",
                f"章节已导入成功，但整理对话框出错：\n{e}\n\n"
                "可稍后手动右键章节改标题。",
            )
        finally:
            self.refresh_tree()
            self.structure_changed.emit()

    def _import_folder(self):
        """从文件夹批量导入章节。"""
        if self.work is None:
            return
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹", "")
        if not folder:
            return

        # 统计可导入文件
        import glob as _glob

        candidates = []
        for pat in ("*.txt", "*.md", "*.markdown", "*.docx"):
            candidates.extend(_glob.glob(os.path.join(folder, pat)))
        if not candidates:
            QMessageBox.information(
                self, "无文件", "所选文件夹里没有 .txt / .md / .docx 文件。"
            )
            return

        # 弹选项对话框：导入模式 + 排序
        from ui.import_folder_dialog import ImportFolderDialog

        dlg = ImportFolderDialog(folder, len(candidates), parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        mode = dlg.get_mode()
        use_volumes = dlg.use_volumes()

        before_count = len(self.work.chapters)
        self.before_structure_change.emit()
        try:
            n = self.work.import_folder(folder, mode=mode, use_volumes=use_volumes)
        except Exception as e:
            logger.error(f"文件夹导入失败: {e}", exc_info=True)
            QMessageBox.critical(self, "导入失败", f"读取文件夹出错：\n{e}")
            return

        if n <= 0:
            QMessageBox.information(self, "导入完成", "没有识别到章节。")
            self.refresh_tree()
            self.structure_changed.emit()
            return

        # 弹整理标题对话框（复用现有逻辑）
        new_chapters = self.work.chapters[before_count:]
        try:
            from ui.import_title_dialog import ImportTitleDialog

            title_dlg = ImportTitleDialog(
                new_chapters, parent=self, scope_label="导入后整理标题"
            )
            result = title_dlg.exec()
            if result == QDialog.Accepted:
                renames = title_dlg.get_renames()
                if renames:
                    m = self.work.rename_chapters_batch(renames)
                    QMessageBox.information(
                        self, "导入完成", f"已导入 {n} 章，整理标题 {m} 个。"
                    )
                else:
                    QMessageBox.information(
                        self, "导入完成", f"已导入 {n} 章（未改动标题）。"
                    )
            else:
                QMessageBox.information(
                    self, "导入完成", f"已导入 {n} 章（标题保持原样）。"
                )
        except Exception as e:
            logger.warning(f"标题整理失败: {e}")
            QMessageBox.information(self, "导入完成", f"已导入 {n} 章。")
        finally:
            self.refresh_tree()
            self.structure_changed.emit()

    # ---------- 拖拽落点提示线 ----------

    def _show_drop_indicator_at_item(self, item: QTreeWidgetItem, below: bool):
        rect = self.tree.visualItemRect(item)
        if below:
            y = rect.bottom() + 1
        else:
            y = rect.top() - 1
        indent = self.tree.indentation()
        x = max(2, rect.left() - indent // 2)
        w = max(20, self.tree.viewport().width() - x - 6)
        self._drop_indicator.setGeometry(x, y - 1, w, 4)
        self._drop_indicator.raise_()
        self._drop_indicator.show()

    def _hide_drop_indicator(self):
        self._drop_indicator.hide()

    def _update_drop_indicator_for_pos(self, pos):
        target = self.tree.itemAt(pos)
        if target is None:
            self._hide_drop_indicator()
            return
        kind = target.data(0, _ITEM_TYPE)
        if kind == "chapter":
            rect = self.tree.visualItemRect(target)
            if rect.height() > 0 and (pos.y() - rect.top()) < rect.height() / 2:
                self._show_drop_indicator_at_item(target, below=False)
            else:
                self._show_drop_indicator_at_item(target, below=True)
        elif kind == "volume":
            if target.childCount() == 0:
                self._show_drop_indicator_at_item(target, below=True)
            else:
                self._hide_drop_indicator()
        else:
            self._hide_drop_indicator()

    # ---------- 拖拽限制 ----------
    def _tree_drag_move(self, event):
        pos = _event_pos(event)
        self._update_drag_autoscroll(pos)
        target = self.tree.itemAt(pos)

        if target is None:
            self._hide_drop_indicator()
            event.ignore()
            return

        kind = target.data(0, _ITEM_TYPE)
        if kind == "volume":
            if target.childCount() == 0:
                self._show_drop_indicator_at_item(target, below=True)
                event.accept()
            else:
                self._hide_drop_indicator()
                event.ignore()
            return

        if kind == "chapter":
            self._update_drop_indicator_for_pos(pos)
            event.accept()
            return

        self._hide_drop_indicator()
        event.ignore()

    # ---------- 拖拽边缘自动滚动 ----------

    def _update_drag_autoscroll(self, pos):
        margin = 24
        y = pos.y()
        h = self.tree.viewport().height()

        if y < margin:
            speed = -max(1, (margin - y) // 2)
            self._start_drag_autoscroll(speed)
        elif y > h - margin:
            speed = max(1, (y - (h - margin)) // 2)
            self._start_drag_autoscroll(speed)
        else:
            self._stop_drag_autoscroll()

    def _start_drag_autoscroll(self, delta: int):
        self._drag_scroll_delta = delta
        if not self._drag_scroll_timer.isActive():
            self._drag_scroll_timer.start()

    def _stop_drag_autoscroll(self):
        self._drag_scroll_delta = 0
        if self._drag_scroll_timer.isActive():
            self._drag_scroll_timer.stop()

    def _do_drag_scroll(self):
        if self._drag_scroll_delta == 0:
            self._stop_drag_autoscroll()
            return
        bar = self.tree.verticalScrollBar()
        old = bar.value()
        bar.setValue(old + self._drag_scroll_delta)
        if bar.value() == old:
            self._stop_drag_autoscroll()
            return
        try:
            global_pos = QCursor.pos()
            local = self.tree.viewport().mapFromGlobal(global_pos)
            if local.x() >= 0 and local.y() >= 0:
                self._update_drop_indicator_for_pos(local)
        except Exception:
            pass

    def _tree_drag_leave(self, event):
        self._stop_drag_autoscroll()
        self._hide_drop_indicator()

    def _tree_drop(self, event):
        self._stop_drag_autoscroll()
        self._hide_drop_indicator()

        pos = _event_pos(event)
        target = self.tree.itemAt(pos)

        if target is None:
            event.ignore()
            return

        src_chapters = [
            it
            for it in self.tree.selectedItems()
            if it.data(0, _ITEM_TYPE) == "chapter"
        ]
        if not src_chapters:
            event.ignore()
            return

        kind = target.data(0, _ITEM_TYPE)
        if kind == "volume":
            if target.childCount() != 0:
                event.ignore()
                return
            dest_parent = target
            insert_pos = 0
        elif kind == "chapter":
            dest_parent = target.parent()
            if dest_parent is None:
                event.ignore()
                return
            rect = self.tree.visualItemRect(target)
            target_idx = dest_parent.indexOfChild(target)
            if rect.height() > 0 and (pos.y() - rect.top()) < rect.height() / 2:
                insert_pos = target_idx
            else:
                insert_pos = target_idx + 1
        else:
            event.ignore()
            return

        def _tree_pos(it):
            """章节在整棵树里的位置：(顶层卷索引, 卷内章节索引)。

            原来只按父节点内索引排序，跨卷多选时会错乱：卷 B 第 2 章
            的卷内索引是 1，卷 A 第 3 章的卷内索引是 2，排序后变成
            [B2, A3]，源顺序被打乱，拖拽后落到错误位置。
            """
            parent = it.parent()
            if parent is None:
                return (0, 0)
            return (self.tree.indexOfTopLevelItem(parent),
                    parent.indexOfChild(it))

        src_chapters.sort(key=_tree_pos)

        # O2：保存拖拽前状态到撤销栈
        snap = self._snapshot_chapter_state()
        if snap is not None:
            self._drag_undo_stack.append(snap)
            if len(self._drag_undo_stack) > self.MAX_DRAG_UNDO:
                self._drag_undo_stack.pop(0)

        self._internal_move = True
        try:
            adjusted_pos = insert_pos
            for src in src_chapters:
                if src.parent() is dest_parent:
                    src_idx = dest_parent.indexOfChild(src)
                    if src_idx < insert_pos:
                        adjusted_pos -= 1

            for src in src_chapters:
                sp = src.parent()
                if sp is None:
                    continue
                sp.takeChild(sp.indexOfChild(src))

            if adjusted_pos < 0:
                for src in src_chapters:
                    dest_parent.addChild(src)
            else:
                for i, src in enumerate(src_chapters):
                    dest_parent.insertChild(adjusted_pos + i, src)
        finally:
            self._internal_move = False

        event.accept()
        self._after_drag()

    # ---------- 拖拽落盘 ----------
    def _on_rows_moved(self, *_args):
        if getattr(self, "_internal_move", False):
            return
        self._after_drag()

    def _update_order_warnings(self):
        """只刷新章节树的顺序警告颜色，不重建树。

        保留：展开状态、选中项、滚动位置。
        用途：拖拽排序结束后立即刷新标红，不用等下次 refresh_tree。
        性能：N=500 时约 12ms，比 refresh_tree 快 15 倍。
        """
        if self.work is None:
            return
        warnings = find_out_of_order(self.work.chapters)
        # 语义色常量，避免硬编码值漂移（之前有 #E74C3C / #e74c3c
        # 两种写法）。以后想统一调整"错误色"只需改 theme_style。
        warn_color = QColor(SEMANTIC_BAD)

        for top_i in range(self.tree.topLevelItemCount()):
            vol_item = self.tree.topLevelItem(top_i)
            if vol_item.data(0, _ITEM_TYPE) != "volume":
                continue
            for j in range(vol_item.childCount()):
                ch_item = vol_item.child(j)
                if ch_item.data(0, _ITEM_TYPE) != "chapter":
                    continue
                cid = ch_item.data(0, _ITEM_ID)
                warn = warnings.get(cid)
                if warn:
                    ch_item.setForeground(0, warn_color)
                    ch_item.setToolTip(0, warn)
                else:
                    # 清除前景色 → 走 QSS 的 color（自动跟随主题切换）。
                    # 若在这里 setForeground(normal_color)，item 会被钉死在
                    # "当时那个主题"的正文色上——切到对比度相反的主题后，
                    # 章节标题就会变成"深字 on 深底"看不见。
                    ch_item.setForeground(0, QBrush())
                    ch_item.setToolTip(0, "")

    def _after_drag(self):
        if self._suppress_events or self.work is None:
            return
        # 筛选状态下拒绝拖拽：章节树只显示了部分章节（如"仅草稿"），
        # 用树重建 meta["chapters"] 会把隐藏章节强制归到默认卷
        # （volume=""）并挪到列表末尾——这是数据被改坏，不是顺序错乱。
        # 直接刷新恢复原状，并提示用户切回「全部」。
        if (hasattr(self, "status_filter")
                and (self.status_filter.currentData() or "all") != "all"):
            self.refresh_tree()
            try:
                self.status_message.emit(
                    "筛选状态下不支持拖拽，请先切回「全部」")
            except Exception:
                pass
            return
        new_order = []
        for top_i in range(self.tree.topLevelItemCount()):
            vol_item = self.tree.topLevelItem(top_i)
            if vol_item.data(0, _ITEM_TYPE) != "volume":
                continue
            vol_key = vol_item.data(0, _ITEM_ID)
            volume = "" if vol_key == DEFAULT_VOLUME else vol_key
            for j in range(vol_item.childCount()):
                ch_item = vol_item.child(j)
                if ch_item.data(0, _ITEM_TYPE) != "chapter":
                    continue
                cid = ch_item.data(0, _ITEM_ID)
                chapter = self.work.get_chapter(cid)
                if chapter is not None:
                    chapter["volume"] = volume
                    new_order.append(chapter)
        # 已删章节单独拿出来，重构新数组时保留在末尾
        all_ch = self.work.all_chapters
        deleted = [c for c in all_ch if c.get("deleted")]

        accounted = {c["id"] for c in new_order}
        for c in self.work.chapters:  # 活跃章节
            if c["id"] not in accounted:
                c["volume"] = ""
                new_order.append(c)

        old_active_ids = [c["id"] for c in self.work.chapters]
        changed = [c["id"] for c in new_order] != old_active_ids
        self.before_structure_change.emit()
        self.work.meta["chapters"] = new_order + deleted
        self.work.save_meta()
        # 拖拽后立即刷新顺序警告标红（不重建树，保留展开/选中状态）
        self._update_order_warnings()
        if changed:
            self.structure_changed.emit()

    # ---------- O2：拖拽撤销 ----------

    def _snapshot_chapter_state(self):
        """记录当前章节顺序 + 卷归属，用于 Ctrl+Z 撤销拖拽。

        只记录 id 与 volume——章节内容/标题不在此快照范围内，
        撤销只还原"位置"，不还原"内容"。
        """
        if self.work is None:
            return None
        return {
            "order": [c["id"] for c in self.work.chapters],
            "volumes": {c["id"]: (c.get("volume") or "") for c in self.work.chapters},
            "ts": time.time(),
        }

    def _undo_drag(self):
        """Ctrl+Z：撤销上一次章节级操作（拖拽 / 拆分 / 合并）。

        只在章节树有焦点时生效（快捷键绑在 self.tree 上）。
        三种记录按**发生时间**取最新执行；旧实现固定优先级，
        「合并 -> 拆分 -> 撤销」时会错误回退过期的合并记录，
        把拆分出的新章内容冲掉、章节错位。
        某条记录因状态变化失效时自动跳到下一条。
        """
        if self.work is None:
            return
        # 最多三类各试一遍：失效的记录会被实现层作废并返回 False
        for _ in range(3):
            t_drag = (self._drag_undo_stack[-1].get("ts", 0.0)
                      if self._drag_undo_stack else 0.0)
            info_m = getattr(self.work, "_last_merge_undo", None)
            t_merge = (info_m or {}).get("ts", 0.0) if info_m else 0.0
            info_s = getattr(self.work, "_last_split_undo", None)
            t_split = (info_s or {}).get("ts", 0.0) if info_s else 0.0

            if t_split > 0 and t_split >= t_merge and t_split > t_drag:
                if self._undo_split_impl():
                    return
                continue  # 拆分记录失效，重算其余候选
            if t_merge > 0 and t_merge > t_drag:
                if self._undo_merge_impl():
                    return
                continue  # 合并记录失效，重算其余候选
            if t_drag > 0:
                self._undo_drag_impl()
                return
            return  # 没有任何可撤销记录：静默

    def _undo_split_impl(self) -> bool:
        """撤销最近一次光标拆分（Ctrl+Z 候选之一）。

        成功后：拆分出的新章被删除、原章原文还原，章节树选中原章
        并滚动到可视区，通过 structure_changed 通知 workspace 重载。
        """
        if self.work is None:
            return False
        # work.undo_last_split 成功后会清空记录，先取出原章 id
        info = getattr(self.work, "_last_split_undo", None) or {}
        orig_id = str(info.get("orig_id", "") or "")

        try:
            ok = self.work.undo_last_split()
        except Exception as e:
            logger.error(f"撤销拆分失败: {e}", exc_info=True)
            QMessageBox.warning(self, "撤销失败", f"无法撤销拆分：\n{e}")
            return False
        if not ok:
            return False

        self.refresh_tree()
        if orig_id:
            self.set_current_chapter(orig_id)
            it = self._chapter_item(orig_id)
            if it is not None:
                self.tree.scrollToItem(it)
        self.structure_changed.emit()
        self.status_message.emit("已撤销拆分（新章已删除，原文已还原）")
        return True

    def _has_merge_undo(self) -> bool:
        if self.work is None:
            return False
        fn = getattr(self.work, "has_merge_undo", None)
        return bool(callable(fn) and fn())

    def _undo_merge_impl(self) -> bool:
        """撤销最近一次章节合并（Ctrl+Z 第二优先级）。

        成功后：
        - 章节树里被合并的章重新出现，并自动选中、滚动到可视区
        - 上一章内容回退到合并前
        - 通过 structure_changed 通知 workspace 重载编辑器
        """
        if self.work is None:
            return False
        # 关键：先取被恢复的章节 id。undo_last_merge 成功后会把
        # _last_merge_undo 清空，事后就取不到了。
        restored_id = ""
        info = getattr(self.work, "_last_merge_undo", None) or {}
        cur_ch = info.get("cur_chapter") or {}
        restored_id = str(cur_ch.get("id", "") or "")

        try:
            ok = self.work.undo_last_merge()
        except Exception as e:
            logger.error(f"撤销合并失败: {e}", exc_info=True)
            QMessageBox.warning(self, "撤销失败", f"无法撤销合并：\n{e}")
            return False
        if not ok:
            return False

        self.refresh_tree()
        if restored_id:
            self.set_current_chapter(restored_id)
            it = self._chapter_item(restored_id)
            if it is not None:
                self.tree.scrollToItem(it)
        self.structure_changed.emit()
        self.status_message.emit("已撤销合并（章节已恢复）")
        return True

    def _undo_drag_impl(self):
        """拖拽撤销的实际实现（原 _undo_drag 主体）。"""
        if not self._drag_undo_stack or self.work is None:
            return
        state = self._drag_undo_stack.pop()

        current_ids = {c["id"] for c in self.work.chapters}
        recorded_ids = set(state["order"])
        if current_ids != recorded_ids:
            ret = QMessageBox.question(
                self,
                "撤销拖拽",
                "拖拽之后章节列表有变动（新增或删除）。\n"
                "撤销会恢复当时的顺序和卷归属，但不会恢复已删除的章节。\n\n"
                "继续撤销吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                # 用户放弃：把 state 推回栈，下次 Ctrl+Z 还能重试
                self._drag_undo_stack.append(state)
                return

        id_to_ch = {c["id"]: c for c in self.work.chapters}
        new_chapters = []
        for cid in state["order"]:
            ch = id_to_ch.get(cid)
            if ch is not None:
                ch["volume"] = state["volumes"].get(cid, "")
                new_chapters.append(ch)
        # 安全网：拖拽后新增的章节（不在记录里）补回末尾，不丢
        for c in self.work.chapters:
            if c["id"] not in state["order"]:
                new_chapters.append(c)

        self.before_structure_change.emit()
        self.work.meta["chapters"] = new_chapters
        self.work.save_meta()
        self.refresh_tree()
        self.structure_changed.emit()
        remaining = len(self._drag_undo_stack)
        if remaining > 0:
            self.status_message.emit(f"已撤销拖拽（还可撤销 {remaining} 次）")
        else:
            self.status_message.emit("已撤销拖拽移动")

    def commit_renamed_item(self, item: QTreeWidgetItem):
        if self.work is None or self._suppress_events:
            return
        kind = item.data(0, _ITEM_TYPE)
        new_name = item.text(0).strip()
        if kind == "chapter":
            cid = item.data(0, _ITEM_ID)
            if new_name:
                self.before_structure_change.emit()
                self.work.rename_chapter(cid, new_name)
        elif kind == "volume":
            old_key = item.data(0, _ITEM_ID)
            # 关键：比较对象是"当前显示文本"，不是 _ITEM_ID。
            # 默认卷的 _ITEM_ID 恒为 DEFAULT_VOLUME="正文"，但显示名
            # 是 get_default_volume_name()（用户可能改成了"草稿"）。
            # 用 new_name == old_key 判断"未修改"，会导致用户
            # 想把默认卷从"草稿"改回"正文"时被静默拒绝——因为
            # new_name 和 old_key 都是"正文"。
            current_display = item.text(0)
            if not new_name or new_name == current_display:
                item.setText(0, current_display)
                return

            if old_key == DEFAULT_VOLUME:
                try:
                    self.before_structure_change.emit()
                    self.work.set_default_volume_name(new_name)
                except Exception as e:
                    logger.warning(f"重命名默认容器失败: {e}")
                    item.setText(0, current_display)
                    return
                self.refresh_tree()
                self.structure_changed.emit()
                return

            self.before_structure_change.emit()
            if not self.work.rename_volume(old_key, new_name):
                if new_name == DEFAULT_VOLUME:
                    QMessageBox.information(
                        self, "提示", f"「{DEFAULT_VOLUME}」是保留容器名，不能使用。"
                    )
                else:
                    QMessageBox.information(self, "提示", f"卷「{new_name}」已存在。")
                item.setText(0, old_key)
                return
            item.setData(0, _ITEM_ID, new_name)
        # 章节改名后立即刷新顺序警告（章号可能改了）
        self._update_order_warnings()
        self.structure_changed.emit()

    def _on_item_changed(self, item: QTreeWidgetItem, _column):
        self.commit_renamed_item(item)
