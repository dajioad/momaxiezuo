# ui/update_dialog.py
r"""墨码写作 - 更新提示对话框

更新流程（自动更新模式）：
  1. 用户点「前往下载」 → 下载更新包 zip 到 %TEMP%\moma_update\
  2. 校验 SHA-256（与已验签的 latest.json.sha256 比对）
  3. 找到同目录下的 updater.exe → 启动它
  4. 主程序退出
  5. updater 等主程序退出 → 替换文件 → 启动新版

兜底模式（找不到 updater.exe 或用户取消）：
  - 保持原逻辑：下载完成后打开资源管理器选中文件，让用户手动解压覆盖
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QProgressBar,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
import os
import sys
import subprocess
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss


def _find_updater() -> str:
    """查找同目录下的 updater.exe。

    返回 updater.exe 的完整路径；找不到返回空串。
    开发模式（python main.py 运行）下 sys.frozen 不存在，返回空串——
    开发模式禁用自动更新（程序目录是源码目录，覆盖会毁掉工作区）。
    """
    if not getattr(sys, "frozen", False):
        return ""
    exe_dir = os.path.dirname(sys.executable)
    updater = os.path.join(exe_dir, "updater.exe")
    return updater if os.path.exists(updater) else ""


class UpdateDialog(QDialog):
    """发现新版本时的提示。"""

    def __init__(self, info: dict, parent=None, theme: dict = None):
        super().__init__(parent)
        self._info = info
        self._theme = theme or self._resolve_theme()
        # 下载线程引用（防 GC）。下载中若把对话框 Esc / × 关闭，
        # 局部变量会被回收，而 QThread 运行中被销毁会 abort 进程。
        self._download_worker = None
        self.setWindowTitle("发现新版本")
        self.setMinimumSize(520, 420)
        self._build_ui()
        self._apply_theme()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None and hasattr(ed, "_theme"):
                    return ed._theme or {}
                break
            p = p.parent()
        return {}

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.icon_label = QLabel()
        head.addWidget(self.icon_label)
        title = QLabel(f"发现新版本 {self._info.get('latest', '')}")
        title.setObjectName("updateTitle")
        head.addWidget(title)
        head.addStretch()
        root.addLayout(head)

        meta = QLabel(
            f"当前版本：{self._info.get('current', '')}    "
            f"发布日期：{self._info.get('pub_date') or '未知'}"
        )
        meta.setObjectName("updateMeta")
        root.addWidget(meta)

        notes_title = QLabel("更新说明")
        notes_title.setObjectName("updateSection")
        root.addWidget(notes_title)

        self.notes_view = QTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setPlainText(self._info.get("notes") or "（暂无说明）")
        root.addWidget(self.notes_view, 1)

        if self._info.get("force"):
            warn = QLabel("当前版本过旧，为继续正常使用请更新到最新版。")
            warn.setObjectName("updateWarn")
            warn.setWordWrap(True)
            root.addWidget(warn)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.later_btn = QPushButton("稍后提醒")
        self.later_btn.clicked.connect(self.reject)
        if self._info.get("force"):
            self.later_btn.setVisible(False)
        bottom.addWidget(self.later_btn)

        self.download_btn = QPushButton(" 前往下载")
        self.download_btn.setProperty("variant", "primary")
        self.download_btn.clicked.connect(self._open_download)
        bottom.addWidget(self.download_btn)
        root.addLayout(bottom)

    def _open_download(self):
        """下载更新包到用户「下载」文件夹。

        优先从 download_url 直接下载（当前托管在 Gitee 发行版附件）；
        没有 download_url 时退化为打开 download_page。
        """
        url = self._info.get("download_url") or ""
        if not url:
            # 没提供直链，退化为打开浏览器
            page = self._info.get("download_page") or ""
            if page:
                QDesktopServices.openUrl(QUrl(page))
            self.accept()
            return

        # 弹下载对话框（带进度条）
        self._start_download(url)

    def _start_download(self, url: str):
        """启动下载对话框。"""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QProgressBar,
            QPushButton,
            QMessageBox,
        )
        from PySide6.QtCore import QThread, Signal

        # ---- 目标路径：用户「下载」文件夹 ----
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(downloads_dir):
            downloads_dir = os.path.expanduser("~")
        # 从 URL 里取文件名
        filename = url.rsplit("/", 1)[-1] or "墨码写作-更新包.zip"
        from urllib.parse import unquote

        filename = unquote(filename)
        target_path = os.path.join(downloads_dir, filename)

        # 从已验签的 info 里取期望的 SHA-256
        expected_sha256 = (self._info.get("sha256") or "").strip().lower()

        # ---- 下载线程 ----
        class _DownloadWorker(QThread):
            progress = Signal(int, int)  # (已下载, 总字节)
            finished_ok = Signal(str)  # 保存路径
            failed = Signal(str)

            def __init__(self, url, target):
                super().__init__()
                self.url = url
                self.target = target
                self._stop = False

            def stop(self):
                self._stop = True

            def run(self):
                import urllib.request

                try:
                    req = urllib.request.Request(
                        self.url,
                        headers={"User-Agent": "MoMa-Updater/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        total = int(resp.headers.get("Content-Length", 0) or 0)
                        done = 0
                        chunk_size = 64 * 1024  # 64 KB
                        with open(self.target, "wb") as f:
                            while True:
                                if self._stop:
                                    f.close()
                                    try:
                                        os.remove(self.target)
                                    except Exception:
                                        pass
                                    return
                                chunk = resp.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                done += len(chunk)
                                self.progress.emit(done, total)
                    self.finished_ok.emit(self.target)
                except Exception as e:
                    self.failed.emit(str(e))

        # ---- 下载对话框 ----
        dlg = QDialog(self)
        dlg.setWindowTitle("正在下载更新")
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        title_lbl = QLabel(f"正在下载：{filename}")
        title_lbl.setWordWrap(True)
        lay.addWidget(title_lbl)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        lay.addWidget(progress)

        status_lbl = QLabel("准备中…")
        status_lbl.setStyleSheet("font-size: 12px; color: gray;")
        lay.addWidget(status_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        worker = _DownloadWorker(url, target_path)
        # 防 GC：把 worker 挂到 self 上
        self._download_worker = worker

        def on_progress(done, total):
            if total > 0:
                pct = int(done / total * 100)
                progress.setValue(pct)
                status_lbl.setText(f"{done // 1024} KB / {total // 1024} KB ({pct}%)")
            else:
                status_lbl.setText(f"已下载 {done // 1024} KB")

        def on_ok(path):
            """下载完成：SHA-256 校验 → 自动更新（若有 updater）/ 手动兜底。"""
            # ---- SHA-256 校验 ----
            if expected_sha256:
                import hashlib

                h = hashlib.sha256()
                try:
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    actual = h.hexdigest()
                except OSError as e:
                    status_lbl.setText(f"✗ 读取更新包失败：{e}")
                    cancel_btn.setText("关闭")
                    return

                if actual != expected_sha256:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    QMessageBox.critical(
                        dlg,
                        "更新包校验失败",
                        f"下载的更新包与服务器声明不一致。\n\n"
                        f"期望 SHA-256：{expected_sha256[:16]}…\n"
                        f"实际 SHA-256：{actual[:16]}…\n\n"
                        "可能被网络中间人篡改，或下载过程损坏。\n"
                        "为安全起见已删除该文件，请重新下载。",
                    )
                    status_lbl.setText("✗ 校验失败，已删除")
                    cancel_btn.setText("关闭")
                    return

            # ---- 检查 updater.exe 是否可用 ----
            updater = _find_updater()
            if not updater:
                # 兜底：打开资源管理器选中文件，让用户手动解压
                progress.setValue(100)
                status_lbl.setText("✓ 下载完成（手动解压模式）")
                try:
                    if sys.platform == "win32":
                        subprocess.Popen(f'explorer /select,"{path}"')
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", "-R", path])
                    else:
                        subprocess.Popen(["xdg-open", os.path.dirname(path)])
                except Exception:
                    pass
                QMessageBox.information(
                    dlg,
                    "下载完成",
                    f"更新包已保存到：\n{path}\n\n"
                    "请关闭本软件，解压覆盖到原目录即可完成更新。",
                )
                dlg.accept()
                self.accept()
                return

            # ---- 自动更新模式 ----
            progress.setValue(100)
            status_lbl.setText("✓ 下载完成，准备更新…")

            ret = QMessageBox.question(
                dlg,
                "准备更新",
                "更新包已下载完成。\n\n"
                "点击「确定」后：\n"
                "  1. 软件会自动关闭\n"
                "  2. 更新程序替换文件（约 5-10 秒）\n"
                "  3. 软件自动重新打开\n\n"
                "期间请不要手动操作程序目录。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ret != QMessageBox.Yes:
                # 用户取消：保留 zip，打开目录让用户手动处理
                status_lbl.setText("✓ 已下载（取消自动更新）")
                try:
                    if sys.platform == "win32":
                        subprocess.Popen(f'explorer /select,"{path}"')
                except Exception:
                    pass
                cancel_btn.setText("关闭")
                return

            # 启动 updater 独立进程
            target_dir = os.path.dirname(sys.executable)
            pid = os.getpid()
            exe_name = os.path.basename(sys.executable)

            # Windows 进程标志：
            #   DETACHED_PROCESS  → updater 不依赖主程序
            #   CREATE_NO_WINDOW  → updater 不弹控制台窗口
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            try:
                subprocess.Popen(
                    [
                        updater,
                        "--pid",
                        str(pid),
                        "--zip",
                        path,
                        "--target",
                        target_dir,
                        "--exe",
                        exe_name,
                    ],
                    creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                    close_fds=True,
                )
            except Exception as e:
                QMessageBox.critical(
                    dlg,
                    "更新启动失败",
                    f"无法启动更新程序：\n{e}\n\n" f"请手动解压更新包：\n{path}",
                )
                return

            # 退出主程序。updater 会等这个进程退出后再替换文件。
            # 用 QApplication.quit 而不是 sys.exit，让 Qt 走正常退出流程
            # （保存、清理资源、触发 closeEvent）。
            from PySide6.QtWidgets import QApplication

            QApplication.quit()

        def on_fail(msg):
            status_lbl.setText(f"✗ 下载失败：{msg}")
            cancel_btn.setText("关闭")

        def on_cancel():
            # retire：stop 置标志（下载循环下个 64KB 块即退出），
            # 建连阻塞期未结束则由全局 Reaper 保活，不 wait 死等、
            # 不置空运行中线程（旧代码超时即置空可致 abort）。
            try:
                from utils.ai_client import retire_worker

                retire_worker(worker)
            except Exception:
                worker.stop()
            dlg.reject()

        def on_dlg_finished(_result=None):
            """子对话框以任何方式关闭都执行：
            确保线程已停止并断开引用，避免留下孤儿线程。"""
            try:
                from utils.ai_client import retire_worker

                retire_worker(worker)
            except Exception:
                try:
                    worker.stop()
                except RuntimeError:
                    pass
            self._download_worker = None

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        cancel_btn.clicked.connect(on_cancel)
        dlg.finished.connect(on_dlg_finished)

        worker.start()
        dlg.exec()

    def _apply_theme(self):
        t = self._theme
        if not t:
            return
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"UpdateDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QLabel#updateTitle {{ font-size: 16px; font-weight: bold; }}"
            f"QLabel#updateMeta {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#updateSection {{ font-size: 13px; font-weight: bold; }}"
            f"QLabel#updateWarn {{ color: #c0392b; font-size: 12px; }}"
            f"QTextEdit {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x44)};"
            f" border-radius: 6px; padding: 8px; }}"
        )
        self.icon_label.setPixmap(qta.icon("fa5s.gift", color=accent).pixmap(28, 28))
        self.download_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                accent_hover,
                selector="QPushButton",
                padding="7px 18px",
                bold=True,
            )
        )
        self.download_btn.setIcon(qta.icon("fa5s.download", color=accent_fg))
