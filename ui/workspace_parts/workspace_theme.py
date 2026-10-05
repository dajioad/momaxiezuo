# ui/workspace_parts/workspace_theme.py
"""墨码写作 - 写作主题与字号偏好应用（Mixin）

从 workspace.py 拆出的「设置 → 界面」落地层：
- reload_preferences：字号/主题等偏好读取
- apply_writing_theme：把写作主题 dict 应用到编辑器、工具栏、菜单图标、
  书架/面板等全部子控件（qta 图标色不跟随 QSS，需显式重着色）
- 主题深浅判断与颜色明暗工具
- 字号增减/重置联动
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss
from ui.writing_editor import get_writing_theme, with_computed_colors
from utils.logger import get_logger

logger = get_logger()


class WorkspaceThemeMixin:
    """写作主题与字号偏好应用。方法直接访问 self.*。"""

    # ==================== 配置 / 主题 ====================

    def reload_preferences(self):
        get = lambda k, d: self.cfg.get_config("Writing", k, d) if self.cfg else d
        theme_key = get("theme", "paper")
        family = get("font_family", "Microsoft YaHei")
        try:
            size = int(get("font_size", "15"))
        except (TypeError, ValueError):
            size = 15
        try:
            spacing = float(get("line_spacing", "1.8"))
        except (TypeError, ValueError):
            spacing = 1.8
        try:
            content_width = int(get("content_width", "720") or 720)
        except (TypeError, ValueError):
            content_width = 720
        indent_mode = (get("para_indent", "full") or "full").lower()
        para_indent = {"full": "\u3000\u3000", "half": "  ", "none": ""}.get(
            indent_mode, "\u3000\u3000"
        )
        self.editor.apply_typography(
            family=family, size=size, line_spacing=spacing,
            content_width=content_width, para_indent=para_indent,
        )
        self.editor.set_typewriter(get("typewriter", "true").lower() == "true")
        self.editor.set_paragraph_focus(
            get("paragraph_focus", "false").lower() == "true")
        self.editor.set_behavior(
            auto_indent=get("auto_indent", "true").lower() == "true",
            auto_quote_pair=get("auto_quote_pair", "true").lower() == "true",
            tab_insert=get("tab_insert", "true").lower() == "true",
        )
        self._dict_highlight_enabled = get("dict_highlight", "true").lower() == "true"
        if self._dict_highlight_enabled:
            self._dict_refresh_timer.start()
        else:
            self.editor.clear_dict_highlights()
        # AI 输出字号
        if self.cfg:
            try:
                ai_fs = int(self.cfg.get_config("AI", "output_font_size", "14"))
            except (TypeError, ValueError):
                ai_fs = 14
            self._pet_bubble.set_output_font_size(ai_fs)
        self.apply_writing_theme(theme_key)

    def apply_writing_theme(self, theme_key_or_dict):
        t0 = time.perf_counter()

        is_dict = isinstance(theme_key_or_dict, dict)
        theme = (
            theme_key_or_dict
            if is_dict
            else get_writing_theme(theme_key_or_dict, self.cfg)
        )
        theme = with_computed_colors(theme)
        t_compute = time.perf_counter()

        self.editor.apply_theme(theme if is_dict else theme_key_or_dict)
        t_editor = time.perf_counter()

        self.sidebar.apply_theme(theme)
        t_bookshelf = time.perf_counter()

        # 宠物气泡
        self._pet_bubble.apply_theme(theme)
        t_ai = time.perf_counter()

        self.symbol_panel.apply_colors(theme)
        t_symbol = time.perf_counter()

        if hasattr(self, "chapter_header") and self.chapter_header is not None:
            self.chapter_header.apply_theme(theme)

        accent = theme["accent"]
        accent_fg = theme.get("accent_fg", "white")
        accent_hover = theme.get("accent_hover", self._lighten_or_darken(accent))
        muted = theme["muted"]
        icon_hover = (
            "rgba(0,0,0,0.07)" if self._is_light(theme) else "rgba(255,255,255,0.08)"
        )
        self.toolbar.setStyleSheet(f"""
QFrame#wsToolbar {{ background-color: {theme['bg']};
    border-bottom: 1px solid {qss_alpha(muted, 0x1E)}; border-top: none; }}
QFrame#wsToolbar QLabel {{ background: transparent; }}
QLabel#workTitle {{ font-size: 14px; font-weight: 700; color: {theme['text']};
    letter-spacing: 0.5px; }}
QLabel#volumeLabel {{ font-size: 12px; color: {muted}; }}
QFrame#tbSep {{ background-color: {qss_alpha(muted, 0x30)}; }}
QPushButton[tier="secondary"] {{ color: {theme['text']}; background: transparent;
    border: 1px solid {qss_alpha(muted, 0x73)}; border-radius: 8px; padding: 6px 12px; }}
QPushButton[tier="secondary"]:hover {{ color: {accent}; border: 1px solid {accent};
    background: {qss_alpha(accent, 0x14)}; }}
QPushButton[tier="icon"] {{ border: none; border-radius: 8px; background: transparent; }}
QPushButton[tier="icon"]:hover {{ background-color: {icon_hover}; }}
QToolButton#tbMenuBtn {{ color: {theme['text']};
    background: transparent; border: none; border-radius: 8px;
    padding: 6px 10px; font-size: 12.5px; font-weight: 600; }}
QToolButton#tbMenuBtn:hover {{ background-color: {icon_hover}; }}
QToolButton#tbMenuBtn::menu-indicator {{ image: none; width: 0; }}
QToolButton#tbMenuBtn::menu-button {{ border: none; width: 0; }}
QToolButton#tbMenuBtn QMenu {{
    background-color: {theme['bg']}; color: {theme['text']};
    border: 1px solid {qss_alpha(muted, 0x33)}; border-radius: 10px;
    padding: 6px; }}
QToolButton#tbMenuBtn QMenu::item {{
    padding: 7px 22px 7px 14px; border-radius: 6px;
    color: {theme['text']}; }}
QToolButton#tbMenuBtn QMenu::item:selected {{
    background-color: {qss_alpha(accent, 0x22)}; color: {theme['text']}; }}
{accent_button_qss(accent, accent_fg, accent_hover,
                   selector='QPushButton[tier="primary"]', padding="6px 10px")}
""")
        self.statusbar.setStyleSheet(f"""
QFrame#wsStatusbar {{ background-color: {theme['sidebar']};
    border-top: 1px solid {qss_alpha(muted, 0x1E)}; border-bottom: none; }}
QFrame#wsStatusbar QLabel {{ color: {theme['text']}; font-size: 12px; background: transparent; }}
QFrame#sbSep {{ background-color: {qss_alpha(muted, 0x30)}; }}
QLabel#sbToday {{ color: {theme['text']}; font-weight: 600; }}
QLabel#sbTodayPercent {{ color: {muted}; font-size: 11px;
    font-weight: 600; }}
QProgressBar#sbTodayBar {{ background: {qss_alpha(muted, 0x22)};
    border: none; border-radius: 3px; }}
QProgressBar#sbTodayBar::chunk {{ background-color: {accent};
    border-radius: 3px; }}
QPushButton#draftNoticeBtn {{ border: 1px solid {accent}; border-radius: 8px;
    padding: 2px 10px; font-size: 12px; color: {accent}; background: transparent; }}
QPushButton#draftNoticeBtn:hover {{ background: {qss_alpha(accent, 0x33)}; }}
""")
        # 状态栏小图标：此前只建了空 QLabel 从未赋图，这里统一着色
        for ic in getattr(self, "_sb_icons", []):
            try:
                ic.setPixmap(qta.icon(
                    ic._icon_name, color=muted).pixmap(13, 13))
            except Exception:
                pass
        if hasattr(self, "save_icon"):
            self.save_icon.set_colors(accent)

        if self._is_today_settled():
            self._apply_settled_state()
        self._last_settled_state = True

        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(theme["bg"]))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        if getattr(self, "find_bar", None) is not None:
            self.find_bar.apply_theme(theme)
        if getattr(self, "proof_bar", None) is not None:
            self.proof_bar.apply_theme(theme)

        t_bars = time.perf_counter()

        icon_sig = (accent_fg, theme["text"])
        if getattr(self, "_last_toolbar_icon_sig", None) != icon_sig:
            self._last_toolbar_icon_sig = icon_sig
            for name in (
                "btn_back_gallery",
                "btn_prev",
                "btn_next",
                "btn_tb_tools",
                "btn_tb_chapter",
                "btn_tb_export",
                "btn_ai",
                "btn_focus",
                "btn_settings",
            ):
                b = getattr(self, name, None)
                if b is not None and hasattr(b, "_icon_name"):
                    b.setIcon(qta.icon(b._icon_name, color=theme["text"]))

        # 三个下拉菜单（工具 / 章节 / 导出）里的 QAction 图标：
        # QAction 不继承 QSS，深色模式下必须同步重着色，否则黑底黑图标
        for name in ("btn_tb_tools", "btn_tb_chapter", "btn_tb_export"):
            mb = getattr(self, name, None)
            if mb is None:
                continue
            for act in mb.menu().actions():
                icon_name = getattr(act, "_icon_name", None)
                if icon_name:
                    act.setIcon(qta.icon(icon_name, color=theme["text"]))

        t_buttons = time.perf_counter()

        # 宠物
        if getattr(self, "_pet_float", None) is not None:
            try:
                self._pet_float.apply_theme(theme)
            except RuntimeError:
                pass
        if getattr(self, "_pet_bubble", None) is not None:
            try:
                self._pet_bubble.apply_theme(theme)
            except RuntimeError:
                pass

        if self._is_gallery():
            self._refresh_gallery_theme()

        t_done = time.perf_counter()
        logger.debug(
            f"[THEME-WS] compute={1000*(t_compute-t0):.0f}ms | "
            f"editor={1000*(t_editor-t_compute):.0f}ms | "
            f"bookshelf={1000*(t_bookshelf-t_editor):.0f}ms | "
            f"ai={1000*(t_ai-t_bookshelf):.0f}ms | "
            f"symbol={1000*(t_symbol-t_ai):.0f}ms | "
            f"bars={1000*(t_bars-t_symbol):.0f}ms | "
            f"buttons={1000*(t_buttons-t_bars):.0f}ms | "
            f"gallery={1000*(t_done-t_buttons):.0f}ms | "
            f"TOTAL={1000*(t_done-t0):.0f}ms"
        )

    @staticmethod
    def _is_light(theme: dict) -> bool:
        try:
            c = QColor(theme.get("bg", "#f7f2e8"))
            return c.lightness() > 150
        except Exception:
            return True

    @staticmethod
    def _lighten_or_darken(hex_color: str, amount: int = 15) -> str:
        try:
            c = QColor(hex_color)
            h, s, l, _ = c.getHsl()
            l = max(0, min(255, l + amount))
            c.setHsl(h, s, l)
            return c.name()
        except Exception:
            return hex_color

    def _on_font_size_changed(self, size: int):
        if self.cfg:
            try:
                self.cfg.set_config("Writing", "font_size", str(size), validate=False)
            except Exception as e:
                logger.warning(f"保存字号失败: {e}")
        self.save_state_label.setText(f"字号 {size}px")
        QTimer.singleShot(1500, lambda: self.save_state_label.setText(""))

    def _font_size_up(self):
        self.editor.change_font_size(+1)

    def _font_size_down(self):
        self.editor.change_font_size(-1)

    def _font_size_reset(self):
        self.editor.set_font_size(15)

