# ui/main_window.py
"""墨码写作 - 主窗口

精简外壳：菜单栏 + 系统托盘 + 久坐提醒；
中央区域为 WritingWorkspace（书架/编辑器/AI 三栏工作台）。

菜单结构：
- 作品(F)：新建 / 导出 / 发布 / 备份 / 打开数据目录 / 退出
- 工具(T)：码字统计 / AI 改写 / AI 梗概 / AI 提取词条 / 编辑工具箱 / 一键排版
           / 校对 / 全局搜索 / 读者预览 / 批量工具
- 视图(V)：符号面板 / AI 助手 / 专注模式 / 查找替换 / 写作主题
- 帮助(H)：设置 / 爱发电赞助 / 检查更新 / 关于

退出保护：flush_save 失败时弹窗提示，不静默退出，
避免磁盘满/权限问题导致用户以为保存成功、实际丢稿。
"""

import os
import sys

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QMenu,
    QSystemTrayIcon,
    QMessageBox,
    QApplication,
    QFileDialog,
    QLineEdit,
    QDialog,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtCore import Qt, QTimer, QUrl, QEvent, QByteArray
import qtawesome as qta

from utils.logger import get_logger
from ui.constants import (
    APP_NAME, APP_VERSION, AFDIAN_HOME, get_app_icon, _apply_custom_dialog_theme,
)
from ui.workspace import WritingWorkspace
from ui.writing_editor import WRITING_THEMES
from ui.settings_writing import SettingsWritingDialog
from ui.update_dialog import UpdateDialog
from ui.theme_style import qss_alpha
from utils.hotkeys import HotkeyManager

logger = get_logger()


def build_global_qss(theme: dict = None) -> str:
    """菜单栏（及其弹出菜单）局部 QSS。

    只设置在 QMenuBar 单个控件上（其子树极小，re-polish <1ms）。
    基础控件统一样式在 ui.theme_style.controls_qss 中，main.py 启动时
    注入一次到 QApplication；对话框/菜单弹出时再按当前主题即时快照着色。
    """
    if not theme:
        return ""
    sidebar = theme.get("sidebar", "#efe8d8")
    text = theme.get("text", "#3b342a")
    muted = theme.get("muted", "#9a8e76")
    accent = theme.get("accent", "#b08a4e")
    editor_bg = theme.get("editor_bg", theme.get("bg", "#f7f2e8"))

    return f"""
/* ==== 菜单栏（规则挂在 QMenuBar 上，QMenu 作为其子对象继承；
       几何参数与 theme_style.controls_qss 的 QMenu 保持一致） ==== */
QMenuBar {{ background-color: {sidebar}; color: {text}; padding: 3px 4px;
    border-bottom: 1px solid {qss_alpha(muted, 0x33)}; }}
QMenuBar::item {{ background: transparent; padding: 6px 12px; border-radius: 7px; }}
QMenuBar::item:selected {{ background-color: {qss_alpha(accent, 0x22)}; }}
QMenu {{ background-color: {editor_bg}; color: {text};
    border: 1px solid {qss_alpha(muted, 0x44)}; padding: 6px;
    border-radius: 12px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 7px; }}
QMenu::item:selected {{ background-color: {qss_alpha(accent, 0x22)}; }}
QMenu::separator {{ height: 1px; background: {qss_alpha(muted, 0x33)};
    margin: 6px 10px; }}
"""


_STYLE_INITIALIZED = False


def apply_app_theme(theme: dict = None):
    """更新应用级配色（palette + 当前主题注册表）。

    首次调用时确保应用样式为 Fusion：
    Windows 平台原生样式（windowsvista）在系统深色模式下会忽略 QSS 和
    palette 的很多设置，导致 QInputDialog / QMessageBox 露出纯黑底。
    Fusion 是 Qt 自带跨平台样式，完全遵循 palette 和 QSS，跨平台一致。

    性能（关键）：本函数**绝不再调用 app.setStyleSheet**。全局 controls_qss
    已在 main.py 启动早期、控件创建之前注入一次；之后替换它会让全应用
    数百个控件整体 re-polish（实测 200ms+，是"切主题卡 1-2 秒"的主因）。
    切主题时只更新：
    - palette（QPalette 变更只发轻量 PaletteChange，~10ms）；
    - 当前主题注册表（弹窗/菜单/对话框在 show 瞬间按它即时着色）。
    """
    global _STYLE_INITIALIZED
    from PySide6.QtGui import QPalette, QColor
    from ui.theme_style import _t, set_active_theme

    app = QApplication.instance()
    if app is None:
        return

    # main.py 已在创建任何控件之前把样式切成 Fusion（避免控件建好后再切
    # 导致全树 re-polish，约 300ms）；这里识别后直接标记完成。
    if not _STYLE_INITIALIZED:
        try:
            if str(app.style().objectName()).lower() != "fusion":
                app.setStyle("Fusion")
        except Exception:
            pass
        _STYLE_INITIALIZED = True

    set_active_theme(theme)

    t = _t(theme)
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(t["bg"]))
    pal.setColor(QPalette.WindowText, QColor(t["text"]))
    pal.setColor(QPalette.Base, QColor(t["editor_bg"]))
    pal.setColor(QPalette.AlternateBase, QColor(t["sidebar"]))
    pal.setColor(QPalette.Text, QColor(t["text"]))
    pal.setColor(QPalette.Button, QColor(t["bg"]))
    pal.setColor(QPalette.ButtonText, QColor(t["text"]))
    pal.setColor(QPalette.Highlight, QColor(t["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(t["accent_fg"]))
    pal.setColor(QPalette.ToolTipBase, QColor(t["editor_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(t["text"]))
    app.setPalette(pal)


class MainWindow(QMainWindow):
    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self.tray_icon = None
        self._reminder_timer = None
        self._settings_dialog = None
        # 更新检查 worker 引用（防止被 GC）
        self._update_worker = None
        # 自动备份 worker 引用（防止被 GC）
        self._auto_backup_worker = None
        # 动态安装的快捷键对象（reload_hotkeys 时统一清理重建）
        self._hotkey_shortcuts: list = []
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1000, 680)
        self.resize(1280, 820)
        self.setWindowIcon(get_app_icon())
        # 屏幕外构造：让所有 layout / 主题 / 字体应用完再显示，
        # 避免窗口出现时从 minimumSize 被撑到目标尺寸的"拉扯"感
        self.setAttribute(Qt.WA_DontShowOnScreen, True)

        # 中央写作工作台
        self.workspace = WritingWorkspace(config_manager)
        self.workspace.open_settings.connect(lambda: self.show_settings(0))
        self.workspace.title_changed.connect(self.setWindowTitle)
        self.workspace.focus_changed.connect(self._on_focus_changed)
        self.workspace.gallery_changed.connect(self._on_gallery_changed)
        self.setCentralWidget(self.workspace)

        self._build_menus()
        self._init_tray()
        self._setup_reminder()
        self._install_global_shortcuts()
        # 隐藏 QMainWindow 自带的原生状态条：workspace 内部已有更完整的
        # 状态条。原生条即使不显示内容也会占约 22-26px 高度。
        sb = self.statusBar()
        sb.setVisible(False)
        sb.setSizeGripEnabled(False)
        # QLineEdit 右键菜单汉化 + ESC 快捷键链。
        # 性能：事件过滤器装在 QApplication 上会让每个事件都进一次 Python
        # （实测启动构造期间 4 万+次、累计 0.5s）。延迟到事件循环启动后
        # 再安装，构造期间的大量事件就不必穿过 Python 过滤器。
        QTimer.singleShot(0, lambda: QApplication.instance().installEventFilter(self))
        # 启动即用当前主题渲染外壳，避免"首启动菜单栏是浅色、切一次主题才对"
        theme_dict = getattr(self.workspace.editor, "_theme", None)
        self._apply_shell_theme(theme_dict)
        apply_app_theme(theme_dict)
        self._apply_ui_font()  # ← 新增
        # 启动即图墙模式：同步隐藏菜单栏
        self._on_gallery_changed(self.workspace._is_gallery())
        # 启动后延迟检查更新（不阻塞启动）
        QTimer.singleShot(5000, self._auto_check_update)
        # 首次启动（引导未完成且画廊为空）延迟弹新手引导，等窗口稳定
        QTimer.singleShot(1500, self._maybe_start_onboarding)
        # 启动后检查自动备份是否到期（再延后，避开启动高峰）
        QTimer.singleShot(60000, self._maybe_run_auto_backup)

    # ==================== 快捷键 ====================
    def _install_global_shortcuts(self):
        """从 HotkeyManager 读绑定并安装 QShortcut。

        每个快捷键一个 QShortcut 对象，存到 self._hotkey_shortcuts，
        方便 reload_hotkeys() 时整体清理重建。

        只有应用级快捷键走这里；菜单项自带的快捷键（如 Ctrl+Shift+R）
        由 QAction 管理，二者独立。为避免重复触发，默认表里
        rewrite / global_search 与菜单快捷键一致，但用户改了绑定后
        只会改 QShortcut 这边，菜单显示文本不变（这是已知取舍）。
        """
        ws = self.workspace
        # action_id → 触发函数
        slot_map = {
            "prev_chapter": ws._prev_chapter,
            "next_chapter": ws._next_chapter,
            "move_chapter_up": ws._move_chapter_up,
            "move_chapter_down": ws._move_chapter_down,
            "back_to_gallery": ws._back_to_gallery,
            "save": ws._manual_save,
            "find": ws.toggle_findbar,
            "symbol_panel": ws.toggle_symbol_panel,
            "ai_panel": ws.toggle_ai,
            "focus_mode": ws.toggle_focus,
            "rewrite": ws.open_rewrite_dialog,
            "global_search": ws.open_global_search,
            "version_history": ws.open_version_history,
            "quit": self.close,
        }
        mgr = HotkeyManager(self.config_manager)
        for aid, slot in slot_map.items():
            key = mgr.get(aid)
            if not key:
                continue
            try:
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.ApplicationShortcut)
                sc.activated.connect(slot)
                self._hotkey_shortcuts.append(sc)
            except Exception as e:
                logger.warning(f"安装快捷键失败 {aid}={key}: {e}")

    def reload_hotkeys(self):
        """设置里改了快捷键后调用：清旧的、按新绑定重装。"""
        for sc in self._hotkey_shortcuts:
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except RuntimeError:
                pass
        self._hotkey_shortcuts = []
        self._install_global_shortcuts()

    # ==================== 菜单 ====================
    def _build_menus(self):
        menubar = self.menuBar()

        # ============================================================
        # 作品(F)：书级操作
        # ============================================================
        m_work = menubar.addMenu("作品(&F)")
        self._add_action(
            m_work, "新建作品", self.workspace.create_new_work, "fa5s.plus"
        )
        self._add_action(
            m_work, "导出投稿 TXT", self.workspace.export_merged, "fa5s.file-export"
        )
        self._add_action(
            m_work, "发布到平台…", self.workspace._show_publish_menu, "fa5s.paper-plane"
        )
        m_work.addSeparator()
        self._add_action(
            m_work,
            "历史快照浏览器…",
            self.workspace.open_version_browser,
            "fa5s.history",
        )
        m_work.addSeparator()
        self._add_action(m_work, "备份所有作品…", self._backup_all, "fa5s.archive")
        self._add_action(
            m_work, "打开数据目录", self._open_data_dir, "fa5s.folder-open"
        )
        m_work.addSeparator()
        self._add_action(m_work, "退出", self.close, "fa5s.sign-out-alt")

        # ============================================================
        # 写作(W)：作品级分析与写作辅助
        # ============================================================
        m_writing = menubar.addMenu("写作(&W)")

        # 进度与规划
        self._add_action(
            m_writing,
            "码字统计",
            self.workspace.open_writing_stats,
            "fa5s.chart-bar",
        )
        self._add_action(
            m_writing,
            "章节字数分布图…",
            self.workspace.open_chapter_length_chart,
            "fa5s.chart-bar",
        )
        self._add_action(
            m_writing,
            "冲刺计划",
            self.workspace.open_sprint_dialog,
            "fa5s.flag-checkered",
        )
        m_writing.addSeparator()

        # 质量分析
        self._add_action(
            m_writing,
            "追读体检",
            self.workspace.open_readability_check,
            "fa5s.stethoscope",
        )
        self._add_action(
            m_writing,
            "一致性检查",
            self.workspace.open_consistency_check,
            "fa5s.users",
        )
        self._add_action(
            m_writing,
            "人物关系图…",
            self.workspace.open_character_relation_dialog,
            "fa5s.project-diagram",
        )
        self._add_action(
            m_writing,
            "伏笔追踪",
            self.workspace.open_foreshadow_dialog,
            "fa5s.bookmark",
        )
        self._add_action(
            m_writing,
            "质量中心…",
            self.workspace.open_quality_center,
            "fa5s.archive",
        )
        m_writing.addSeparator()

        # 视角
        self._add_action(
            m_writing,
            "读者视角预览",
            self.workspace.open_reader_preview,
            "fa5s.mobile-alt",
        )
        m_writing.addSeparator()
        self._add_action(
            m_writing,
            "发布前体检…",
            self.workspace.open_publish_check,
            "fa5s.clipboard-check",
        )

        # ============================================================
        # 文本(T)：当前文字的排版、清洗、搜索、批量处理
        # ============================================================
        m_text = menubar.addMenu("文本(&T)")
        self._add_action(
            m_text,
            "查找替换\tCtrl+F",
            self.workspace.toggle_findbar,
            "fa5s.search",
        )
        m_text.addSeparator()
        self._add_action(
            m_text,
            "编辑工具箱",
            self.workspace.open_edit_toolbox,
            "fa5s.toolbox",
        )
        self._add_action(
            m_text,
            "一键排版",
            self.workspace.open_format_dialog,
            "fa5s.magic",
        )
        self._add_action(
            m_text,
            "平台格式一键",
            self.workspace.open_platform_format_dialog,
            "fa5s.industry",
        )
        m_text.addSeparator()
        self._add_action(
            m_text,
            "全局搜索\tCtrl+Alt+F",
            self.workspace.open_global_search,
            "fa5s.search-plus",
        )
        m_text.addSeparator()
        self._add_action(
            m_text,
            "批量工具",
            self.workspace.open_batch_tools_dialog,
            "fa5s.tools",
        )

        # ============================================================
        # 校对(P)：错别字 / 敏感词
        # ============================================================
        m_proof = menubar.addMenu("校对(&P)")
        self._add_action(
            m_proof,
            "校对当前章节",
            self.workspace.open_proofread,
            "fa5s.spell-check",
        )
        self._add_action(
            m_proof,
            "全书校对…",
            self.workspace.open_proofread_all,
            "fa5s.book",
        )
        m_proof.addSeparator()
        self._add_action(
            m_proof,
            "校对词库管理",
            self.workspace.open_proof_dict_manage,
            "fa5s.book-open",
        )

        # ============================================================
        # 智能体(A)：AI 功能
        # ============================================================
        m_agent = menubar.addMenu("智能体(&A)")
        self._add_action(
            m_agent,
            "AI 写作助手\tCtrl+J",
            self.workspace.toggle_ai,
            "fa5s.robot",
        )
        m_agent.addSeparator()
        self._add_action(
            m_agent,
            "AI 改写…\tCtrl+Shift+R",
            self.workspace.open_rewrite_dialog,
            "fa5s.random",
        )
        self._add_action(
            m_agent,
            "AI 生成本章梗概",
            self.workspace.generate_chapter_summary,
            "fa5s.file-alt",
        )
        self._add_action(
            m_agent,
            "AI 提取新词条",
            self.workspace.extract_entities_with_ai,
            "fa5s.tags",
        )
        self._add_action(
            m_agent,
            "AI 提取人物关系",
            self.workspace.extract_relations_with_ai,
            "fa5s.user-friends",
        )
        self._add_action(
            m_agent,
            "批量提取人物关系（区间）…",
            self.workspace.batch_extract_relations,
            "fa5s.people-arrows",
        )
        self._add_action(
            m_agent,
            "墨灵试读…",
            self.workspace.open_beta_reading,
            "fa5s.book-reader",
        )
        self._add_action(
            m_agent,
            "小说转剧本",
            self.workspace.open_script_converter,
            "fa5s.film",
        )
        # 不加分隔线：它下面直接是下一个顶级菜单（视图），
        # 悬空的分隔线会孤零零挂在「智能体」菜单底部。

        # ============================================================
        # 视图(V)：界面 / 面板 / 主题
        # ============================================================
        m_view = menubar.addMenu("视图(&V)")
        self._add_action(
            m_view,
            "符号面板\tCtrl+K",
            self.workspace.toggle_symbol_panel,
            "fa5s.asterisk",
        )
        self._add_action(
            m_view, "专注模式\tF11", self.workspace.toggle_focus, "fa5s.expand"
        )
        m_view.addSeparator()
        theme_menu = m_view.addMenu("写作主题")
        for key, theme in WRITING_THEMES.items():
            act = QAction(theme["name"], self)
            act.triggered.connect(lambda _=False, k=key: self._switch_writing_theme(k))
            theme_menu.addAction(act)
        theme_menu.addSeparator()
        act_custom = QAction("我的主题（自定义配色）", self)
        act_custom.triggered.connect(lambda: self._switch_writing_theme("custom"))
        theme_menu.addAction(act_custom)
        act_custom_cfg = QAction("个性化配色设置…", self)
        act_custom_cfg.triggered.connect(lambda: self.show_settings(0))
        theme_menu.addAction(act_custom_cfg)

        # ============================================================
        # 墨灵(L)：AI 宠物互动（对话 / 状态 / 小屋 / 道具 / 开关）
        # 与"右键墨灵"菜单共用同一批动作，顶部菜单负责可发现性。
        # ============================================================
        m_pet = menubar.addMenu("墨灵(&L)")
        self._pet_care_menu = None
        self._pet_toggle_acts = {}
        pet = getattr(self.workspace, "_pet_float", None)
        if pet is not None:
            self._add_action(
                m_pet, "对话面板", pet.open_chat, "fa5s.comment-dots"
            )
            self._add_action(
                m_pet, "墨灵状态…", pet.open_status_panel, "fa5s.heart"
            )
            self._add_action(
                m_pet, "墨灵小屋（商店/任务/成就）…",
                pet.open_shop, "fa5s.store"
            )
            m_pet.addSeparator()
            # 道具箱：展开时实时重建，库存与可用性始终最新
            self._pet_care_menu = m_pet.addMenu("道具箱")
            self._pet_care_menu.aboutToShow.connect(
                self._refresh_pet_care_menu)
            m_pet.addSeparator()
            self._add_action(
                m_pet, "显示/隐藏墨灵", self._toggle_pet_visible, "fa5s.cat"
            )
            act_roam = m_pet.addAction("空闲时自主溜达")
            act_roam.setCheckable(True)
            act_roam.setToolTip("开启后墨灵会在空闲时自己走动、发呆、打盹")
            self._pet_toggle_acts = {act_roam: "roam_enabled"}
            act_roam.toggled.connect(
                lambda checked:
                    pet.save_pet_setting(
                        "roam_enabled", "1" if checked else "0"))
            m_pet.aboutToShow.connect(self._sync_pet_toggle_checks)

        # ============================================================
        # 帮助(H)
        # ============================================================
        m_help = menubar.addMenu("帮助(&H)")
        self._add_action(m_help, "新手引导", self._open_onboarding, "fa5s.compass")
        self._add_action(m_help, "设置", lambda: self.show_settings(0), "fa5s.cog")
        self.act_sponsor = self._add_action(
            m_help,
            "爱发电赞助",
            self._open_sponsor_page,
            "fa5s.heart",
        )
        m_help.addSeparator()
        self._add_action(m_help, "检查更新", self._manual_check_update, "fa5s.sync-alt")
        self._add_action(m_help, "关于", self._show_about, "fa5s.info-circle")

    def _add_action(self, menu, text, slot, icon_name=None, shortcut=None) -> QAction:
        action = QAction(text, menu)
        if icon_name:
            # 图标颜色随当前写作主题强调色（避免硬编码）
            t = (
                self.workspace.editor._theme
                if self.workspace and self.workspace.editor
                else {}
            )
            icon_color = t.get("accent", "#b08a4e")
            action.setIcon(qta.icon(icon_name, color=icon_color))
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda checked=False: slot())
        menu.addAction(action)
        return action

    # ==================== 更新检查 ====================
    def _auto_check_update(self):
        """启动后自动检查更新。仅打包后启用；每天最多一次；失败静默。"""
        try:
            # 开发模式：默认跳过；想测试就加一行开发配置项
            if not getattr(sys, "frozen", False):
                dev_check = self.config_manager.get_config(
                    "Update", "dev_check", "false"
                ).lower()
                if dev_check != "true":
                    return
            # 用户开关
            if (
                self.config_manager.get_config("Update", "auto_check", "true").lower()
                != "true"
            ):
                return
            # 每天最多一次
            from datetime import date

            today = date.today().strftime("%Y-%m-%d")
            last = self.config_manager.get_config("Update", "last_check_date", "")
            if last == today:
                return

            from utils.update_worker import UpdateCheckWorker

            self._stop_update_worker()
            self._update_worker = UpdateCheckWorker()
            self._update_worker.finished_ok.connect(self._on_auto_update_result)
            self._update_worker.start()
        except Exception as e:
            logger.warning(f"启动更新检查失败: {e}")

    def _on_auto_update_result(self, info: dict):
        """自动检查结束：无论成功失败都写日期，避免同一天反复请求。"""
        try:
            from datetime import date

            self.config_manager.set_config(
                "Update",
                "last_check_date",
                date.today().strftime("%Y-%m-%d"),
                validate=False,
            )
        except Exception:
            pass
        if not info:
            return  # 无更新或失败，静默
        try:
            theme = getattr(self.workspace.editor, "_theme", None)
            dlg = UpdateDialog(info, self, theme=theme)
            dlg.exec()
        except Exception as e:
            logger.warning(f"更新弹窗失败: {e}")

    def _manual_check_update(self):
        """用户主动点"检查更新"：忽略每日限制。"""
        # 停掉上一次还在跑的，否则 self._update_worker 被覆盖时旧 QThread
        # 失去 Python 引用 → GC → "Destroyed while thread is still running"
        # → abort。用户连点两次就能复现。
        self._stop_update_worker()
        from utils.update_worker import UpdateCheckWorker

        # 可见反馈：状态栏默认隐藏，改用等待光标，结束后在结果槽恢复
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._update_worker = UpdateCheckWorker()
        self._update_worker.finished_ok.connect(self._on_manual_update_result)
        self._update_worker.failed.connect(self._on_manual_update_failed)
        self._update_worker.start()

    def _on_manual_update_failed(self, err: str):
        """网络/服务器失败不能再报"已是最新版本"，明确告知用户。"""
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass
        low = (err or "").lower()
        if "timeout" in low or "timed out" in low:
            reason = "连接更新服务器超时，可能是网络较慢。"
        elif any(k in low for k in ("connection", "connect", "dns",
                                    "name or service", "unreachable")):
            reason = "无法连接更新服务器，请检查网络后重试。"
        elif "signature" in low or "验签" in (err or ""):
            reason = "更新清单校验失败（可能被网络代理篡改），已中止。"
        else:
            reason = "更新检查失败，请稍后重试。"
        QMessageBox.warning(self, "检查更新", f"{reason}\n\n详情：{err}")

    def _stop_update_worker(self):
        """停掉并回收 _update_worker（连点/自动+手动交叉时防 QThread 崩溃）。

        退休前断开 finished_ok/failed：旧检查的结果不能再触发任何弹窗。
        stop 对阻塞中的请求不能立刻中断（更新检查超时 5 秒），
        未结束的由全局 Reaper 保活，不在 UI 线程死等。
        """
        w = getattr(self, "_update_worker", None)
        if w is not None:
            # 断开该 worker 的全部结果槽（自动检查连的是
            # _on_auto_update_result，手动检查连的是 _on_manual...，
            # 不带参数 disconnect 一次性全断），旧结果不再触发任何弹窗。
            for sig in (w.finished_ok, w.failed):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                from utils.ai_client import retire_worker

                retire_worker(w)
            except Exception:
                try:
                    if w.isRunning():
                        w.stop()
                except RuntimeError:
                    pass
        self._update_worker = None
        # 手动检查设置的等待光标若因连点/自动交叉而中途停 worker，
        # 结果槽不会再执行，必须在这里兜底恢复。
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def _on_manual_update_result(self, info: dict):
        """手动检查结束：始终有反馈（无论成功/失败/无更新）。"""
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass
        if not info:
            QMessageBox.information(
                self, "检查更新", f"已是最新版本（v{APP_VERSION}）。"
            )
            return
        theme = getattr(self.workspace.editor, "_theme", None)
        UpdateDialog(info, self, theme=theme).exec()

    # ==================== 事件过滤 ====================
    def eventFilter(self, obj, event):
        """QLineEdit 右键菜单汉化 + ESC 快捷键链

        性能：本过滤器挂在 QApplication 上，所有事件都会进来，所以必须先
        用最便宜的事件类型判断快速返回，绝不在无关事件上做 isinstance。
        """
        et = event.type()
        if et != QEvent.KeyPress and et != QEvent.ContextMenu:
            return False
        # ESC 优先级链（弹窗/菜单打开时一律不拦截，交给弹窗自身处理）：
        # 退出专注模式 → 关闭查找条 → 退出写作返回作品画廊
        if et == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            ws = getattr(self, "workspace", None)
            if ws and (
                QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
            ):
                # Esc 只做"关闭当前浮层"——与主流写作软件（Scrivener /
                # Obsidian / Word）一致。不再返回作品画廊：用户按 Esc
                # 是"想取消"，不是"想离开当前章节"。返回画廊改到 Ctrl+W。
                if ws._focus_mode:
                    ws.toggle_focus()
                    return True
                # 查找条
                if ws.find_bar.isVisible():
                    ws.find_bar.closed.emit()
                    return True
                # 校对结果浮条
                proof_bar = getattr(ws, "proof_bar", None)
                if proof_bar is not None and proof_bar.isVisible():
                    ws._clear_proofread()
                    return True
                # 侧栏面板：符号 / AI
                if ws.symbol_panel.isVisible():
                    ws.toggle_symbol_panel()
                    return True
                if ws._pet_bubble.isVisible():
                    ws._pet_bubble.request_close()
                    return True
            return False
        if isinstance(obj, QLineEdit) and et == QEvent.ContextMenu:
            menu = obj.createStandardContextMenu()
            if menu:
                for action in menu.actions():
                    text = action.text()
                    mapping = {
                        "&Undo": "撤销",
                        "Undo": "撤销",
                        "&Redo": "重做",
                        "Redo": "重做",
                        "Cu&t": "剪切",
                        "Cut": "剪切",
                        "&Copy": "复制",
                        "Copy": "复制",
                        "&Paste": "粘贴",
                        "Paste": "粘贴",
                        "Delete": "删除",
                        "Select All": "全选",
                        "Select all": "全选",
                    }
                    for en, zh in mapping.items():
                        if en.replace("&", "") in text.replace("&", ""):
                            action.setText(zh)
                            break
                # 关键：显示菜单。之前只 return True 消费事件，
                # 但从未调用 exec()，导致 QLineEdit 右键菜单完全不显示。
                menu.exec(event.globalPos())
                return True
        return super().eventFilter(obj, event)

    # ==================== 主题切换 ====================
    def _apply_shell_theme(self, theme_dict: dict):
        """菜单栏（含其菜单）局部着色。子树极小，单次 <1ms。"""
        mb = self.menuBar()
        mb.setStyleSheet(build_global_qss(theme_dict))

    @staticmethod
    def _refresh_open_dialog_snapshot():
        """主题实时预览时，把当前打开的模态对话框也重刷成新主题。"""
        dlg = QApplication.activeModalWidget()
        if isinstance(dlg, QDialog):
            _apply_custom_dialog_theme(dlg, force=True)

    def _switch_writing_theme(self, theme_key: str):
        self.config_manager.set_config("Writing", "theme", theme_key, validate=False)
        self.workspace.reload_preferences()
        # 菜单栏局部 QSS + 全局 palette（不再触发全应用控件 re-polish）
        theme_dict = self.workspace.editor._theme
        self._apply_shell_theme(theme_dict)
        apply_app_theme(theme_dict)

    def _on_focus_changed(self, focused: bool):
        """专注模式切换：隐藏/显示菜单栏，最大化窗口"""
        mb = self.menuBar()
        if focused or self.workspace._is_gallery():
            mb.hide()
        else:
            mb.show()
        if focused:
            self.showMaximized()

    def _on_gallery_changed(self, gallery: bool):
        """图墙模式：隐藏菜单栏，画面只保留「我的作品」画廊区域"""
        mb = self.menuBar()
        if gallery or self.workspace._focus_mode:
            mb.hide()
        else:
            mb.show()

    # ==================== 系统托盘 ====================
    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(get_app_icon(), self)
        self.tray_icon.setToolTip(APP_NAME)
        menu = QMenu(self)
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self._show_window)
        menu.addAction(act_show)
        menu.addSeparator()
        act_quit = QAction("退出", self)
        # triggered 会带一个 checked 参数，用 lambda 包一层避免被当成 force
        act_quit.triggered.connect(lambda checked=False: self._quit_app())
        menu.addAction(act_quit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_window() if not self.isVisible() else self.hide()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    # ==================== 久坐提醒 ====================
    def _setup_reminder(self):
        if self._reminder_timer:
            self._reminder_timer.stop()
            self._reminder_timer = None
        enabled = (
            self.config_manager.get_config("Reminder", "enabled", "false").lower()
            == "true"
        )
        if not enabled:
            return
        interval_text = self.config_manager.get_config("Reminder", "interval", "1小时")
        interval_map = {"30分钟": 1800, "1小时": 3600, "2小时": 7200, "4小时": 14400}
        if interval_text == "自定义":
            try:
                seconds = (
                    int(
                        self.config_manager.get_config(
                            "Reminder", "custom_minutes", "60"
                        )
                    )
                    * 60
                )
            except ValueError:
                seconds = 3600
        else:
            seconds = interval_map.get(interval_text, 3600)
        self._reminder_timer = QTimer(self)
        self._reminder_timer.timeout.connect(self._show_reminder)
        self._reminder_timer.start(seconds * 1000)

    def _show_reminder(self):
        if not self.isVisible():
            return
        message = self.config_manager.get_config("Reminder", "message", "起来活动一下")
        if self.tray_icon:
            self.tray_icon.showMessage(
                "墨码写作提醒", message, QSystemTrayIcon.Information, 8000
            )
        else:
            QMessageBox.information(self, "提醒", message)

    # ==================== 备份 / 数据目录 ====================
    def _backup_all(self):
        """把所有作品、快照、词库、统计打包成 zip（不含 API Key 与日志）。"""
        import zipfile
        from datetime import datetime

        default_name = (
            f"墨码写作备份_" f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "备份所有作品", default_name, "ZIP 压缩包 (*.zip)"
        )
        if not path:
            return
        data_root = self.config_manager.data_dir
        items = [
            "works",
            "versions",
            "stats.json",
            "proof_dict.json",
            "ai_presets.json",
            "publish_targets.json",
        ]
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name in items:
                    full = os.path.join(data_root, name)
                    if os.path.isdir(full):
                        for root, _dirs, files in os.walk(full):
                            for f in files:
                                fp = os.path.join(root, f)
                                arc = os.path.relpath(fp, data_root)
                                try:
                                    zf.write(fp, arc)
                                except OSError as e:
                                    logger.warning(f"打包失败 {fp}: {e}")
                    elif os.path.isfile(full):
                        zf.write(full, name)
            QMessageBox.information(
                self,
                "备份完成",
                f"已备份到：\n{path}\n\n"
                "包含所有作品、历史快照、词库、统计与发布平台配置；\n"
                "不含 API Key（ai.dat）与日志。",
            )
        except OSError as e:
            logger.error(f"备份失败: {e}", exc_info=True)
            QMessageBox.critical(self, "备份失败", str(e))

    def _open_data_dir(self):
        """在系统文件管理器里打开数据根目录。"""
        path = self.config_manager.data_dir
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ==================== 新手引导 ====================
    def _maybe_start_onboarding(self):
        """首次启动（引导未完成且画廊为空）才自动弹；老用户不打扰。"""
        try:
            done = (
                self.config_manager.get_config(
                    "Onboarding", "done", "false") or "false"
            ).lower() == "true"
            if done:
                return
            from utils.work_store import WorkStore

            store = WorkStore(self.config_manager.data_dir)
            if store.list_works():
                # 已写书的老用户升级后不自动弹，可从「帮助 → 新手引导」重看
                return
            self._open_onboarding()
        except Exception as e:
            logger.warning(f"新手引导自动弹出失败: {e}")

    def _open_onboarding(self):
        """打开新手引导向导（帮助菜单可随时重放）。"""
        from ui.onboarding_dialog import OnboardingDialog

        theme = getattr(self.workspace.editor, "_theme", {}) or {}
        dlg = OnboardingDialog(
            self,
            theme=theme,
            actions={
                "new_work": self.workspace.create_new_work,
                "appearance_settings": lambda: self.show_settings(0),
                "ai_settings": lambda: self.show_settings(2),
                "backup_settings": lambda: self.show_settings(5),
            },
        )
        dlg.finished_guide.connect(self._mark_onboarding_done)
        dlg.exec()

    def _mark_onboarding_done(self):
        try:
            self.config_manager.set_config(
                "Onboarding", "done", "true", validate=False)
        except Exception:
            pass

    # ==================== 自动备份 ====================
    def _maybe_run_auto_backup(self):
        """启动后检查自动备份是否到期；到期则后台线程打 zip，绝不弹模态。"""
        try:
            if self._auto_backup_worker is not None and \
                    self._auto_backup_worker.isRunning():
                return
            enabled = (
                self.config_manager.get_config(
                    "Backup", "auto_backup", "false") or "false"
            ).lower() == "true"
            if not enabled:
                return
            interval = self.config_manager.get_config(
                "Backup", "auto_interval", "每周") or "每周"
            last_date = self.config_manager.get_config(
                "Backup", "last_backup_date", "") or ""
            from utils.auto_backup import backup_due

            if not backup_due(last_date, interval):
                return

            self._run_auto_backup()
        except Exception as e:
            logger.warning(f"检查自动备份失败: {e}")

    def _run_auto_backup(self):
        """实际发起一次后台自动备份（也供设置页「立即备份」复用）。"""
        from utils.auto_backup import AutoBackupWorker

        if self._auto_backup_worker is not None and \
                self._auto_backup_worker.isRunning():
            return False
        worker = AutoBackupWorker(self.config_manager.data_dir, self)
        self._auto_backup_worker = worker
        worker.finished_ok.connect(self._on_auto_backup_ok)
        worker.failed.connect(self._on_auto_backup_fail)
        worker.start()
        return True

    def _on_auto_backup_ok(self, path: str, size: int):
        from datetime import date

        if not path:
            return  # 数据目录里还没有任何作品/快照，不写标记，下次再试
        # 无论保留份数配置是否合法，先落日期，避免失败当天反复压缩
        try:
            self.config_manager.set_config(
                "Backup", "last_backup_date",
                date.today().strftime("%Y-%m-%d"), validate=False)
        except Exception:
            pass

        try:
            keep = int(self.config_manager.get_config(
                "Backup", "auto_keep", "3") or "3")
        except (TypeError, ValueError):
            keep = 3
        keep = max(1, min(30, keep))
        try:
            from utils.auto_backup import backup_dir_for, prune_backups

            prune_backups(backup_dir_for(self.config_manager.data_dir), keep)
        except Exception as e:
            logger.warning(f"清理旧自动备份失败: {e}")

        size_mb = size / (1024 * 1024)
        msg = f"已自动备份作品（{size_mb:.1f} MB），旧备份仅留最近 {keep} 份"
        try:
            self.workspace.save_state_label.setText(msg)
        except Exception:
            pass
        logger.info(msg)

    def _on_auto_backup_fail(self, err: str):
        logger.warning(f"自动备份未完成: {err}")
        # 备份关乎数据安全，失败必须让用户可见（不静默丢保护）：
        # 优先写入编辑器状态标签；托盘存在时再补一条系统通知。
        msg = f"自动备份失败：{err}，请检查磁盘空间或备份目录权限"
        try:
            self.workspace.save_state_label.setText(msg)
        except Exception:
            pass
        try:
            tray = getattr(self, "tray_icon", None)
            if tray is not None:
                tray.showMessage("墨码写作 - 备份失败", err,
                                 QSystemTrayIcon.Warning, 5000)
        except Exception:
            pass

    def _stop_auto_backup_worker(self):
        """退出程序时收留备份线程，杜绝运行中 QThread 被析构 abort。"""
        w = self._auto_backup_worker
        if w is None:
            return
        self._auto_backup_worker = None
        try:
            # 必须解除父子关系：否则主窗口析构会连带销毁仍在压缩的线程
            w.setParent(None)
            from utils.ai_client import retire_worker
            retire_worker(w)   # stop + Reaper 保活，aboutToQuit 再 drain
        except RuntimeError:
            pass

    # ==================== 设置 / 关于 ====================
    def show_settings(self, tab_index: int = 0):
        dlg = SettingsWritingDialog(self.config_manager, self)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.theme_preview.connect(self._on_theme_preview)
        # 取消个性化预览后恢复已保存的主题
        dlg.rejected.connect(self._on_theme_preview_cancelled)
        dlg.select_tab(tab_index)
        dlg.exec()

    def _on_theme_preview(self, theme_data):
        """主题预览：同时更新 workspace、菜单栏、全局 palette 与打开中的设置框"""
        self.workspace.apply_writing_theme(theme_data)
        theme_dict = self.workspace.editor._theme
        self._apply_shell_theme(theme_dict)
        apply_app_theme(theme_dict)
        self._refresh_open_dialog_snapshot()

    def _on_theme_preview_cancelled(self):
        """取消预览：恢复已保存主题 + 菜单栏/palette/设置框"""
        self.workspace.reload_preferences()
        theme_dict = self.workspace.editor._theme
        self._apply_shell_theme(theme_dict)
        apply_app_theme(theme_dict)
        self._refresh_open_dialog_snapshot()

    def _apply_ui_font(self):
        """按配置的 [UI] font_pt 设置 QApplication 默认字体。

        影响范围：所有没有显式 font-size 的控件（菜单、按钮、树、列表…）。
        已经硬编码 font-size 的提示文字不受影响——那些由各面板自己控制。
        """
        raw = self.config_manager.get_config("UI", "font_pt", "10")
        try:
            pt = int(raw)
        except (TypeError, ValueError):
            pt = 10
        pt = max(8, min(16, pt))
        app = QApplication.instance()
        if app is None:
            return
        f = app.font()
        f.setPointSize(pt)
        app.setFont(f)

    def _on_settings_saved(self):
        self.workspace.reload_preferences()
        self.workspace._pet_bubble.refresh_key_state()
        # 系统提示词可能已改动：让 AI 气泡更新 messages[0]
        self.workspace._pet_bubble.reload_system_prompt()
        self._setup_reminder()
        # 快捷键可能已改动：清理重装
        self.reload_hotkeys()
        # 界面字号可能已改动：立即应用
        self._apply_ui_font()
        # AI 输出字号可能已改动：刷新已打开的剧本对话框
        # （AI 面板由 reload_preferences → ai_panel.set_output_font_size 处理）
        dlg = getattr(self.workspace, "_script_dlg", None)
        if dlg is not None:
            try:
                dlg._apply_theme()
            except RuntimeError:
                # C++ 对象已删（父窗口关闭等），忽略
                pass

    def _open_sponsor_page(self):
        """打开爱发电赞助主页（软件完全免费，赞助自愿）。"""
        QDesktopServices.openUrl(QUrl(AFDIAN_HOME))

    def _toggle_pet_visible(self):
        """显示 / 隐藏宠物。"""
        ws = getattr(self, "workspace", None)
        if ws is None:
            return
        pet = getattr(ws, "_pet_float", None)
        if pet is None:
            return
        try:
            if pet.isVisible():
                pet.hide()
            else:
                # 位置校正：防止之前存的位置在屏幕外
                pet.move(pet._clamp_to_screen(
                    pet.pos(), pet.width(), pet.height()))
                pet.show()
                pet.raise_()
        except RuntimeError:
            pass

    def _refresh_pet_care_menu(self):
        """展开「道具箱」子菜单时实时重建（库存/前置条件可能刚变化）。"""
        ws = getattr(self, "workspace", None)
        menu = getattr(self, "_pet_care_menu", None)
        if ws is None or menu is None:
            return
        pet = getattr(ws, "_pet_float", None)
        if pet is None:
            return
        try:
            t = ws.editor._theme if ws.editor else {}
            accent = t.get("accent", "#b08a4e")
            muted = t.get("muted", "#9a8e76")
            menu.clear()
            care_acts = pet.fill_care_menu(
                menu, icon_color=accent, muted_color=muted)
            for act, key in care_acts.items():
                act.triggered.connect(
                    lambda _=False, k=key: pet.do_action(k))
        except RuntimeError:
            pass

    def _sync_pet_toggle_checks(self):
        """打开「墨灵」菜单时，让体验勾选态与 config 保持一致。"""
        ws = getattr(self, "workspace", None)
        if ws is None:
            return
        cfg = getattr(ws, "cfg", None)
        for act, key in getattr(self, "_pet_toggle_acts", {}).items():
            on = True
            if cfg is not None:
                try:
                    v = str(cfg.get_config("Pet", key, "1") or "1")
                    on = v.strip() not in ("0", "false", "False", "no")
                except Exception:
                    pass
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)

    def _show_about(self):
        t = (
            self.workspace.editor._theme
            if self.workspace and self.workspace.editor
            else {}
        )
        muted = t.get("muted", "#9a8e76")
        QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"<h3>{APP_NAME} v{APP_VERSION}</h3>"
            "<p>本地运行、稿子不上云的沉浸式写作软件，全部功能免费开放。</p>"
            f"<p>喜欢的话可以在「帮助 → 爱发电赞助」支持作者持续更新。</p>"
            f"<p style='color:{muted}'>所有稿件保存在本地，可在「设置 → 通用 → 存储位置」查看。</p>",
        )

    # ==================== 关闭 / 退出 ====================
    def closeEvent(self, event):
        # 更新检查 worker 若还在跑，先停掉——QThread 析构时会 abort。
        # 更新检查是启动后 5 秒触发的，用户有可能在此期间关闭软件。
        # 统一走退休机制：断连 + stop，未结束的 Reaper 保活不死等。
        self._stop_update_worker()

        behavior = self.config_manager.get_config(
            "Writing", "tray_behavior", "点击关闭：最小化到托盘"
        )
        if (
            self.tray_icon
            and self.tray_icon.isVisible()
            and behavior == "点击关闭：最小化到托盘"
        ):
            event.ignore()
            self.hide()
            # 宠物是独立顶层窗口：主窗口隐藏后自动保留在屏幕上
            # （无需显式操作，_PetFloat 的 parent 是 None）
            self.tray_icon.showMessage(
                APP_NAME,
                "程序已最小化到托盘，墨灵会留在屏幕上陪你。\n双击图标恢复主窗口。",
                QSystemTrayIcon.Information,
                3000,
            )
            return
        if self.tray_icon and self.tray_icon.isVisible() and behavior == "每次询问":
            ret = QMessageBox.question(
                self,
                "关闭确认",
                "选「是」退出程序，选「否」最小化到系统托盘。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret == QMessageBox.No:
                event.ignore()
                self.hide()
                return
        # 允许退出：先尝试保存
        if not self._quit_app():
            event.ignore()
            return
        event.accept()

    def _quit_app(self, force: bool = False) -> bool:
        """退出主窗口。

        返回 True 表示"已经执行退出"，False 表示"保存失败且用户放弃退出，
        窗口应保持打开"。flush_save 失败时不再静默 quit，
        避免磁盘满/权限问题导致用户以为保存成功、实际丢稿。
        """
        if not force:
            try:
                self.workspace.on_shutdown()
            except Exception as e:
                logger.error(f"退出保存失败: {e}", exc_info=True)
                ret = QMessageBox.critical(
                    self,
                    "保存失败",
                    f"退出前自动保存失败：{e}\n\n"
                    "为避免丢失稿件，程序暂不退出。\n"
                    "请检查磁盘空间/权限，或先使用「作品 → 备份所有作品」"
                    "手动保存后再试。\n\n"
                    "是否强制退出（未保存的编辑将丢失）？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return False
                # 用户选择强制退出，继续往下
        if self.tray_icon:
            self.tray_icon.hide()
        # 备份线程可能正在压缩：取消并交给 Reaper，aboutToQuit 统一排空
        self._stop_auto_backup_worker()
        # 销毁宠物顶层窗口（否则 Qt 可能不退出）
        try:
            ws = getattr(self, "workspace", None)
            if ws is not None:
                pf = getattr(ws, "_pet_float", None)
                if pf is not None:
                    pf.close()
                    pf.deleteLater()
                pb = getattr(ws, "_pet_bubble", None)
                if pb is not None:
                    pb.close()
                    pb.deleteLater()
        except Exception:
            pass
        QApplication.quit()
        return True
