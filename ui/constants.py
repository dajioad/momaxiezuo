# ui/constants.py
"""墨码写作 - UI 全局常量

包含应用名称与版本号，以及标准对话框/菜单的主题应用辅助函数。
"""

import os
import sys
from functools import lru_cache

from PySide6.QtGui import QIcon

# 应用名称与版本
APP_NAME = "墨码写作"
APP_VERSION = "1.0.0"

# 爱发电赞助主页（软件完全免费，赞助为自愿支持）
AFDIAN_HOME = "https://afdian.com/a/xiaoyu_tool"


def modern_scrollbar_qss(
    handle: str, hover: str, *, width: int = 10, scope: str = ""
) -> str:
    """现代软件风格细滚动条：无箭头、圆角半透明滑块、透明轨道。

    scope 可限定选择器前缀（如 "QDialog"）；不限定则全局生效。
    内容不足时滑块透明（配合 ScrollBarAlwaysOn 也不显脏）。
    """
    p = f"{scope} " if scope else ""
    return f"""
{p}QScrollBar:vertical {{ background: transparent; width: {width}px;
    margin: 2px 1px; }}
{p}QScrollBar:horizontal {{ background: transparent; height: {width}px;
    margin: 1px 2px; }}
{p}QScrollBar::handle:vertical {{ background: {handle}; border-radius: 4px;
    min-height: 40px; }}
{p}QScrollBar::handle:horizontal {{ background: {handle}; border-radius: 4px;
    min-width: 40px; }}
{p}QScrollBar::handle:vertical:hover, {p}QScrollBar::handle:horizontal:hover {{
    background: {hover}; }}
{p}QScrollBar::handle:vertical:disabled, {p}QScrollBar::handle:horizontal:disabled {{
    background: transparent; }}
{p}QScrollBar::add-line, {p}QScrollBar::sub-line {{
    width: 0; height: 0; border: none; background: transparent; }}
{p}QScrollBar::add-page, {p}QScrollBar::sub-page {{ background: transparent; }}
"""


def _current_controls_qss() -> str:
    """当前主题的全套基础控件 QSS（弹窗弹出瞬间按此快照着色）。"""
    from ui.theme_style import controls_qss, get_active_theme

    return controls_qss(get_active_theme())


def _apply_dialog_theme(dialog):
    """给独立顶层对话框显式应用当前 app 主题（QSS + palette）。"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from ui.theme_style import get_active_theme_version

    app = QApplication.instance()
    if app is None:
        return
    ver = get_active_theme_version()
    if getattr(dialog, "_snap_version", None) == ver:
        return
    try:
        dialog.setAttribute(Qt.WA_StyledBackground, True)
        dialog.setStyleSheet(_current_controls_qss())
        dialog.setPalette(app.palette())
        dialog.style().unpolish(dialog)
        dialog.style().polish(dialog)
        dialog.update()
        dialog._snap_version = ver
    except Exception:
        pass


def _apply_custom_dialog_theme(dialog, force: bool = False):
    """自定义对话框首次显示时快照注入当前主题。"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from ui.theme_style import get_active_theme_version

    ver = get_active_theme_version()
    app = QApplication.instance()
    try:
        if not force and getattr(dialog, "_snap_version", None) == ver:
            return
        if not hasattr(dialog, "_orig_sheet"):
            dialog._orig_sheet = dialog.styleSheet()
        dialog.setAttribute(Qt.WA_StyledBackground, True)
        dialog.setStyleSheet(_current_controls_qss() + (dialog._orig_sheet or ""))
        if app is not None:
            dialog.setPalette(app.palette())
        dialog._snap_version = ver
    except Exception:
        pass


def _apply_menu_theme(menu):
    """给独立弹出的 QMenu 显式应用当前 app 主题。"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from ui.theme_style import get_active_theme_version

    app = QApplication.instance()
    if app is None:
        return
    ver = get_active_theme_version()
    if getattr(menu, "_snap_version", None) == ver:
        return
    try:
        menu.setAttribute(Qt.WA_StyledBackground, True)
        menu.setPalette(app.palette())
        menu.setStyleSheet(_current_controls_qss())
        menu.style().unpolish(menu)
        menu.style().polish(menu)
        menu.update()
        menu._snap_version = ver
    except Exception:
        pass


def _apply_color_dialog_theme(dialog):
    """给 QColorDialog 应用主题 + 汉化按钮。"""
    from PySide6.QtWidgets import QApplication, QPushButton
    from PySide6.QtCore import Qt

    app = QApplication.instance()
    if app is None:
        return
    try:
        dialog.setAttribute(Qt.WA_StyledBackground, True)
        dialog.setStyleSheet(_current_controls_qss())
        dialog.setPalette(app.palette())
    except Exception:
        pass

    _BTN_TEXT = {
        "&OK": "确定",
        "OK": "确定",
        "&Cancel": "取消",
        "Cancel": "取消",
        "Pick Screen Color": "屏幕取色",
        "Add to Custom Colors": "添加到自定义",
        "&Add to Custom Colors": "添加到自定义",
        "&Pick Screen Color": "屏幕取色",
    }
    try:
        for btn in dialog.findChildren(QPushButton):
            t = btn.text()
            if t in _BTN_TEXT:
                btn.setText(_BTN_TEXT[t])
            else:
                tl = t.lower().replace("&", "").strip()
                if tl == "ok":
                    btn.setText("确定")
                elif tl == "cancel":
                    btn.setText("取消")
                elif "pick screen color" in tl:
                    btn.setText("屏幕取色")
                elif "add to custom" in tl:
                    btn.setText("添加到自定义")
    except Exception:
        pass


def install_dialog_localization(app) -> object:
    """安装全局对话框按钮汉化 + 主题修复。"""
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtWidgets import (
        QMessageBox,
        QInputDialog,
        QProgressDialog,
        QFileDialog,
        QDialogButtonBox,
        QMenu,
        QColorDialog,
        QDialog,
    )

    _BB_TEXT = {
        QDialogButtonBox.Ok: "确定",
        QDialogButtonBox.Cancel: "取消",
        QDialogButtonBox.Save: "保存",
        QDialogButtonBox.Open: "打开",
        QDialogButtonBox.Close: "关闭",
        QDialogButtonBox.Discard: "不保存",
        QDialogButtonBox.Yes: "是",
        QDialogButtonBox.No: "否",
        QDialogButtonBox.Retry: "重试",
        QDialogButtonBox.Ignore: "忽略",
        QDialogButtonBox.RestoreDefaults: "恢复默认",
        QDialogButtonBox.Help: "帮助",
        QDialogButtonBox.SaveAll: "全部保存",
    }

    _MB_TEXT = {
        QMessageBox.Ok: "确定",
        QMessageBox.Cancel: "取消",
        QMessageBox.Yes: "确定",
        QMessageBox.No: "取消",
        QMessageBox.Save: "保存",
        QMessageBox.Discard: "不保存",
        QMessageBox.Close: "关闭",
        QMessageBox.Retry: "重试",
        QMessageBox.Ignore: "忽略",
        QMessageBox.Abort: "中止",
        QMessageBox.RestoreDefaults: "恢复默认",
        QMessageBox.Help: "帮助",
        QMessageBox.SaveAll: "全部保存",
    }

    class _DialogLocalizer(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Show:
                try:
                    if isinstance(obj, QMenu):
                        _apply_menu_theme(obj)
                        return False
                    if isinstance(obj, QColorDialog):
                        _apply_color_dialog_theme(obj)
                        return False
                    is_top_dialog = isinstance(
                        obj, (QMessageBox, QInputDialog, QProgressDialog, QFileDialog)
                    )
                    if is_top_dialog:
                        _apply_dialog_theme(obj)
                    elif isinstance(obj, QDialog):
                        _apply_custom_dialog_theme(obj)

                    if isinstance(obj, QMessageBox):
                        for btn in obj.buttons():
                            text = _MB_TEXT.get(obj.standardButton(btn))
                            if text:
                                btn.setText(text)
                    elif isinstance(obj, QInputDialog):
                        obj.setOkButtonText("确定")
                        obj.setCancelButtonText("取消")
                    elif isinstance(obj, QProgressDialog):
                        obj.setCancelButtonText("取消")
                    elif isinstance(obj, QFileDialog):
                        for box in obj.findChildren(QDialogButtonBox):
                            for btn in box.buttons():
                                text = _BB_TEXT.get(box.standardButton(btn))
                                if text:
                                    btn.setText(text)
                except RuntimeError:
                    pass
            return False

    localizer = _DialogLocalizer(app)
    app.installEventFilter(localizer)
    return localizer


@lru_cache(maxsize=1)
def get_app_icon() -> QIcon:
    """从本地文件系统加载应用图标（打包后从 exe 所在目录查找）。"""
    icon = QIcon()
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.dirname(current_dir)
    for name in _ICON_CANDIDATES:
        icon_path = os.path.join(base_dir, name)
        if os.path.exists(icon_path):
            icon.addFile(icon_path)
            return icon
    return icon


# 应用图标文件名
_ICON_CANDIDATES = ["墨码写作.ico"]
