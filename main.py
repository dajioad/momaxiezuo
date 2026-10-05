# main.py
"""墨码写作 - 程序入口

打包注意（--windowed 模式）：
  PyInstaller 用 --windowed 打包时没有控制台，sys.stdout / sys.stderr
  都是 None。所有涉及这两者的调用（faulthandler.enable、第三方库初始化、
  未捕获异常打印）都必须先做 None 判断，否则进程会在启动瞬间崩掉。

启动性能：
  1. 硬件指纹采集改后台线程，不阻塞窗口显示（首次运行省 5~15 秒）。
  2. 单实例锁（QLockFile）防止重复双击开出多个进程。
"""

import sys
import os
import time

# ============================================================
# 第 0 步：最早期兜底 —— 必须在任何第三方 import 之前
# ============================================================
# --windowed 打包后 sys.stdout / sys.stderr 为 None。
# 第三方库（如某些日志/调试库）初始化时会访问它们，直接抛 AttributeError。
# 这里先把它们指向 devnull，保证后续 import 不炸。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


# ============================================================
# 第 1 步：启动耗时打点（写入 logs/startup.log，方便排查）
# ============================================================
_t0 = time.perf_counter()
_startup_marks = []


def _mark(label: str):
    """记录一个时间点。开销极小，可以放心插桩。"""
    _startup_marks.append(f"{label}: {(time.perf_counter() - _t0) * 1000:.0f}ms")


def _dump_startup_marks():
    """启动完成后写一次，覆盖旧文件（只保留最近一次）。"""
    try:
        log_dir = os.path.join(
            os.path.expanduser("~"), "Documents", "墨码写作", "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "startup.log"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(_startup_marks))
    except Exception:
        pass


# ============================================================
# 第 2 步：崩溃日志（faulthandler）
# ============================================================
# 原来直接 faulthandler.enable(file=sys.stderr) 会在 windowed 模式下
# 抛 RuntimeError，导致软件根本打不开。
# 现在：有 stderr 走 stderr；没有就写到用户数据目录的 crash.log。
# 整个过程包在 try 里 —— 崩溃日志只是辅助，绝不能因为它拒绝启动。
import faulthandler


def _setup_faulthandler():
    try:
        if sys.stderr is not None and hasattr(sys.stderr, "fileno"):
            faulthandler.enable(file=sys.stderr)
            return
    except Exception:
        pass
    # 没有可用的 stderr：写文件
    try:
        log_dir = os.path.join(
            os.path.expanduser("~"), "Documents", "墨码写作", "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = open(os.path.join(log_dir, "crash.log"), "a", encoding="utf-8")
        faulthandler.enable(file=fh)
    except Exception:
        pass


_setup_faulthandler()


# ============================================================
# 第 3 步：未捕获异常兜底
# ============================================================
# windowed 模式下未捕获异常会直接静默退出，用户看不到任何提示。
# 这里把异常写入 crash.log 并弹窗告知，方便用户反馈问题。
import traceback


def _excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    # 写日志
    try:
        log_dir = os.path.join(
            os.path.expanduser("~"), "Documents", "墨码写作", "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "crash.log"), "a",
                  encoding="utf-8") as f:
            f.write("\n" + "=" * 60 + "\n")
            f.write(msg + "\n")
    except Exception:
        pass
    # 弹窗（若 Qt 已初始化）
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None,
                "程序出现异常",
                "程序遇到了一个未处理的错误。\n\n"
                "崩溃日志已保存到：\n"
                "  我的文档/墨码写作/logs/crash.log\n\n"
                "最后一段信息：\n" + msg[-600:],
            )
    except Exception:
        pass


sys.excepthook = _excepthook


# ============================================================
# 第 4 步：常规 import
# ============================================================
import platform

from PySide6.QtCore import Qt, QSize, QEvent, QLockFile
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
)
from PySide6.QtGui import QFont, QIcon, QGuiApplication, QScreen
import qtawesome as qta

from ui.constants import get_app_icon, install_dialog_localization
from utils.logger import get_logger

logger = get_logger()

# 核心依赖 - 静态导入确保 PyInstaller 能检测到
import cryptography  # AI Key 加密存储（SecureConfig）
import requests      # AI 接口网络请求

_mark("第三方 import 完成")


# 高 DPI 缩放策略：PassThrough 让 Qt 在非整数缩放下自由处理像素对齐
# AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps 在 Qt 6 中默认启用且已弃用，无需显式设置
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

sys.dont_write_bytecode = True
os.environ["PYTHONIOENCODING"] = "utf-8"

# 跨平台 QT 主题设置
# Windows 平台不设 QT_QPA_PLATFORMTHEME / QT_QUICK_CONTROLS_STYLE：
#   - 前者在 Windows 上无效（有效值通常是 gtk3 / qt5ct / qt6ct）
#   - 后者是给 QML Quick Controls 用的，本项目是纯 QtWidgets
#   - 平台样式在 main() 里显式切到 Fusion，无需这些变量
os_type = platform.system().lower()
if os_type == "linux":
    os.environ["QT_QPA_PLATFORMTHEME"] = "gtk3"
elif os_type == "darwin":
    os.environ["QT_MAC_WANTS_LAYER"] = "1"


# ============================================================
# 全局滚轮守卫（通过重写 QApplication.notify 实现）
# ============================================================
# 关键：QApplication.installEventFilter 只拦截发给 QApplication 自身的
# 事件，不拦截子控件的事件。要真正全局拦截，必须重写 notify()——
# 它是 Qt 所有事件的统一入口。
#
# 规则：数字框（QSpinBox / QDoubleSpinBox）/ 下拉框（QComboBox）未获得
# 焦点时，忽略鼠标滚轮（穿透给最近的滚动区域），避免用户滚页面时误改值。


class GuardedApplication(QApplication):
    """重写 notify() 的 QApplication，实现全局滚轮守卫。"""

    def notify(self, receiver, event):
        if (
            event.type() == QEvent.Wheel
            and isinstance(receiver, (QSpinBox, QDoubleSpinBox, QComboBox))
            and not receiver.hasFocus()
        ):
            # Ctrl+滚轮：视为用户明确想改值，放行走 Qt 默认处理。
            # 这样"防误触"和"有意改值"都有清晰的操作路径。
            if event.modifiers() & Qt.ControlModifier:
                return super().notify(receiver, event)

            # 未聚焦 + 无 Ctrl：转发给最近的父级滚动区域
            from PySide6.QtWidgets import QAbstractScrollArea
            from PySide6.QtGui import QWheelEvent
            from PySide6.QtCore import QPointF

            parent = receiver.parentWidget()
            while parent is not None:
                if isinstance(parent, QAbstractScrollArea):
                    vp = parent.viewport()
                    # 关键：事件坐标是相对 receiver 的，直接转发给 viewport
                    # 会让滚动区的边缘判断/平滑滚动读到错误的 position。
                    # 先把坐标映射过去，再构造一份新事件发送。
                    local = vp.mapFrom(receiver, event.position().toPoint())
                    new_event = QWheelEvent(
                        QPointF(local),
                        event.globalPosition(),
                        event.pixelDelta(),
                        event.angleDelta(),
                        event.buttons(),
                        event.modifiers(),
                        event.phase(),
                        event.inverted(),
                    )
                    self.sendEvent(vp, new_event)
                    return True
                parent = parent.parentWidget()
            # 未找到滚动区：吃掉事件（不改值）。用户想改值时
            # 点一下聚焦控件，或按住 Ctrl 再滚。
            return True

        return super().notify(receiver, event)


def get_system_dpi():
    """获取系统 DPI。

    注意：QGuiApplication.primaryScreen() 需要 QApplication 已实例化，
    因此本函数只能在 QApplication 创建之后调用。
    """
    try:
        screen = QGuiApplication.primaryScreen()
        if screen:
            dpi = screen.logicalDotsPerInch()
            if dpi > 0:
                return int(dpi)
    except Exception as e:
        logger.warning(f"读取系统 DPI 失败，回退 96: {e}")
    return 96


def get_font_size_for_dpi(dpi):
    """根据 DPI 计算合适的字体大小"""
    base_size = 10
    scale_factor = dpi / 96.0
    return int(base_size * scale_factor)


from utils.config import ConfigManager
from ui.main_window import MainWindow

_mark("项目模块 import 完成")


def main():
    _mark("进入 main")

    # 关键：用自定义的 GuardedApplication 替代标准 QApplication
    app = GuardedApplication(sys.argv)
    app.setApplicationName("墨码写作")
    _mark("QApplication 创建")

    # 退出事件循环前排空在途 AI worker：慢网络下点过取消的流式请求线程
    # 可能还活着，不排空会在进程退出时触发「QThread 运行中析构」硬崩溃。
    def _drain_ai_workers():
        try:
            from utils.ai_client import drain_workers
            drain_workers()
        except Exception:
            pass

    app.aboutToQuit.connect(_drain_ai_workers)

    # 在创建任何控件之前切换 Fusion 样式：若等控件建好再切，Qt 会对整棵
    # 控件树做一次 re-polish（实测约 300ms 启动开销）。
    # Windows 原生 windowsvista 样式在系统深色模式下会忽略大量 QSS/palette。
    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    # ---- DPI 相关计算：必须等 QApplication 创建之后再做 ----
    # QGuiApplication.primaryScreen() 在 QApplication 实例化之前返回 None，
    # 之前把 SYSTEM_DPI 放在模块级会导致永远回退到 96，高 DPI 屏字体不缩放。
    system_dpi = get_system_dpi()
    font_size = get_font_size_for_dpi(system_dpi)

    # QtAwesome 的缩放因子必须早于任何 qta.icon 调用设置
    qta.set_defaults(scale_factor=system_dpi / 96)

    # 跨平台字体 - 根据 DPI 自适应
    if os_type == "windows":
        app.setFont(QFont("微软雅黑", font_size))
    elif os_type == "darwin":
        app.setFont(QFont("PingFang SC", font_size + 3))
    else:
        app.setFont(QFont("Noto Sans CJK SC", font_size))

    # 预热 QtAwesome，确保字体加载
    try:
        _ = qta.icon("fa5s.cog")
    except Exception as e:
        logger.warning(f"QtAwesome 预热失败: {e}")

    # 使用统一的图标加载函数设置全局窗口图标
    app_icon = get_app_icon()
    app.setWindowIcon(app_icon)
    _mark("DPI / 字体 / 图标 完成")

    cfg = ConfigManager()
    cfg.init_config()
    _mark("配置加载完成")

    # ============================================================
    # 单实例守卫：QLockFile
    # ============================================================
    # 防止用户重复双击图标开出多个进程互相抢数据目录。
    # - setStaleLockTime(0)：长期持锁不自动判陈旧。否则在缺少原生文件锁
    #   的卷（exFAT U 盘、部分 SMB/网盘挂载）上，持锁超过陈旧时间后
    #   第二实例会"偷锁"，两进程并发写同一批数据。
    # - 崩溃残留改为读锁内 PID：进程已不存在才手动清锁（PID 复用概率
    #   极低，且宁可误判"在运行"也不能双开）。
    # - 更新器重启新进程时，旧进程已退出、Qt 已自动释放锁，不会误判。
    # - 环境变量 MOMA_SKIP_LOCK=1 可跳过（供更新器场景使用）。
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            if os.name == "nt":
                import ctypes
                # QUERY_LIMITED_INFORMATION，不需要高权限
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if not h:
                    return False
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True   # 拿不准时按"存活"处理，绝不双开

    lock_file = None
    if os.environ.get("MOMA_SKIP_LOCK", "") != "1":
        lock_path = os.path.join(cfg.data_dir, ".moma.lock")
        lock_file = QLockFile(lock_path)
        lock_file.setStaleLockTime(0)
        if not lock_file.tryLock(100):
            # 抢锁失败：区分"真有实例在跑"与"崩溃残留锁"
            _ok, _host, _app, lock_pid = lock_file.getLockInfo()
            if lock_pid > 0 and not _pid_alive(lock_pid):
                lock_file.removeStaleLockFile()
            if not lock_file.tryLock(100):
                QMessageBox.information(
                    None, "墨码写作",
                    "墨码写作已在运行中。\n\n"
                    "如果你没看到它的窗口，请检查系统托盘或任务栏。")
                sys.exit(0)
    _mark("单实例锁获取")

    # 全局控件 QSS 只在启动时、控件创建之前注入一次。
    # 之后切主题绝不替换它：替换会让全应用数百个控件整体 re-polish
    # （实测 200ms+，是"切主题卡 1-2 秒"的主因）。切主题改走 palette +
    # 弹窗/对话框 show 瞬间按当前主题快照着色（见 ui.constants）。
    try:
        from ui.writing_editor import get_writing_theme
        from ui.theme_style import controls_qss, set_active_theme

        _init_key = cfg.get_config("Writing", "theme", "paper") or "paper"
        _init_theme = get_writing_theme(_init_key, cfg)
        set_active_theme(_init_theme)
        # with_tooltip=False：提示框颜色由 palette 提供，可随主题即时变化
        app.setStyleSheet(controls_qss(_init_theme, with_tooltip=False))
    except Exception as e:
        logger.warning(f"初始全局 QSS 注入失败: {e}")
    _mark("QSS 注入完成")

    # ============================================================
    # 直接构建并显示主窗口
    # ============================================================
    win = None
    try:
        _mark("MainWindow 构建开始")
        win = MainWindow(cfg)
        _mark("MainWindow 创建完成")

        win.setAttribute(Qt.WA_DontShowOnScreen, False)
        win.show()
        win.raise_()
        win.activateWindow()
        _mark("窗口显示")

        # 主窗口构造完成后再挂对话框汉化事件过滤器。
        install_dialog_localization(app)
        _mark("对话框汉化安装完成")

        # 首次启动打点先落一次盘
        _dump_startup_marks()

    except Exception as e:
        import traceback
        QMessageBox.critical(
            None, "启动失败",
            f"{str(e)}\n{traceback.format_exc()}",
        )
        app.quit()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()