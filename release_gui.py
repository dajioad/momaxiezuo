# release_gui.py
"""墨码写作 - 一键发版 GUI 工具

双击运行，填表即发版。复用 release.ps1 的所有逻辑，但完全图形化：
- 自动读取当前版本号
- 自动填下一个 patch 版本
- 分 6 步显示进度
- 实时日志
- 失败可重试

只在开发环境使用，不打包进产品。
"""
import os
import sys
import re
import subprocess
import hashlib
import zipfile
import base64
import pathlib

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QMessageBox,
    QProgressBar,
)

# ---------- 配置 ----------
ROOT = pathlib.Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
CONSTANTS_FILE = ROOT / "ui" / "constants.py"
DIST_DIR = ROOT / "dist" / "墨码写作"
ZIP_FILE = ROOT / "dist" / "update.zip"

SSH_HOST = "root@39.96.64.137"
REMOTE_DIR = "/var/www/moma"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


# ---------- 后台发版 Worker ----------
class ReleaseWorker(QThread):
    log = Signal(str)                    # 一行日志
    step_changed = Signal(int, str)      # (第几步, 步骤名)
    finished_ok = Signal(str)            # 成功，带 SHA
    failed = Signal(str)                 # 失败，带错误

    def __init__(self, version: str, notes: str):
        super().__init__()
        self.version = version
        self.notes = notes
        self._stop = False
        self.setObjectName("ReleaseWorker")

    def stop(self):
        self._stop = True

    # ---------- 工具 ----------
    def _run_stream(self, cmd, cwd=None, allow_fail=False):
        """运行命令，逐行推日志。失败抛异常。"""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = 0x08000000 if os.name == "nt" else 0

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd or ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        for line in iter(proc.stdout.readline, ""):
            if self._stop:
                proc.terminate()
                raise Exception("用户取消发版")
            line = line.rstrip("\r\n")
            if line:
                self.log.emit(line)
        proc.wait()
        if proc.returncode != 0 and not allow_fail:
            raise Exception(f"命令失败（退出码 {proc.returncode}）")

    def _bump_version(self):
        text = CONSTANTS_FILE.read_text(encoding="utf-8")
        new_text, n = re.subn(
            r'APP_VERSION\s*=\s*"[^"]+"',
            f'APP_VERSION = "{self.version}"',
            text,
        )
        if n == 0:
            raise Exception("ui/constants.py 里没找到 APP_VERSION")
        CONSTANTS_FILE.write_text(new_text, encoding="utf-8")
        self.log.emit(f"  版本号已改为 {self.version}")

    def _make_zip(self):
        """把 dist 打包为 update.zip，排除 updater.exe。"""
        if not DIST_DIR.exists():
            raise Exception(f"未找到打包产物: {DIST_DIR}")
        if ZIP_FILE.exists():
            ZIP_FILE.unlink()
        skip_count = 0
        file_count = 0
        self.log.emit("  正在压缩（约 30-60 秒）…")
        with zipfile.ZipFile(
                ZIP_FILE, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in DIST_DIR.rglob("*"):
                if not f.is_file():
                    continue
                if f.name == "updater.exe":
                    skip_count += 1
                    continue
                arc = f.relative_to(DIST_DIR)
                zf.write(f, arc)
                file_count += 1
        size_mb = ZIP_FILE.stat().st_size / (1024 * 1024)
        self.log.emit(f"  已写入 {file_count} 个文件，跳过 {skip_count} 个（updater.exe）")
        self.log.emit(f"  update.zip: {size_mb:.1f} MB")
        return ZIP_FILE

    def _sha256(self, path: pathlib.Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest().lower()

    def _upload(self, zip_path: pathlib.Path):
        remote_path = f"{SSH_HOST}:{REMOTE_DIR}/downloads/update.zip"
        self._run_stream(["scp", str(zip_path), remote_path])

    def _sign(self, sha: str):
        notes_b64 = base64.b64encode(
            self.notes.encode("utf-8")).decode("ascii")
        cmd = [
            "ssh", SSH_HOST,
            f"python3 /root/moma/sign.py '{self.version}' '{sha}' '{notes_b64}'"
        ]
        result_lines = []
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = 0x08000000 if os.name == "nt" else 0

        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env, creationflags=creationflags,
        )
        for line in iter(proc.stdout.readline, ""):
            if self._stop:
                proc.terminate()
                raise Exception("用户取消发版")
            line = line.rstrip("\r\n")
            if line:
                result_lines.append(line)
                self.log.emit(line)
        proc.wait()
        if proc.returncode != 0:
            raise Exception(f"ssh 命令失败（退出码 {proc.returncode}）")
        if not any("OK" in l for l in result_lines):
            raise Exception("服务器签名未返回 OK")

    # ---------- 主流程 ----------
    def run(self):
        try:
            self.log.emit(f"===== 开始发版 v{self.version} =====")
            self.log.emit("")

            self.step_changed.emit(1, "改版本号")
            self._bump_version()
            self.log.emit("")

            self.step_changed.emit(2, "打包主程序（约 3-5 分钟）")
            self._run_stream([str(VENV_PY), "build.py"])
            self.log.emit("")

            self.step_changed.emit(3, "生成 update.zip")
            zip_path = self._make_zip()
            self.log.emit("")

            self.step_changed.emit(4, "计算 SHA-256")
            sha = self._sha256(zip_path)
            self.log.emit(f"  {sha}")
            self.log.emit("")

            self.step_changed.emit(5, "上传 zip 到服务器")
            self._upload(zip_path)
            self.log.emit("  上传完成")
            self.log.emit("")

            self.step_changed.emit(6, "服务器签名并更新 latest.json")
            self._sign(sha)
            self.log.emit("")

            self.log.emit("=" * 50)
            self.log.emit(f"  发版完成!  v{self.version}")
            self.log.emit("=" * 50)
            self.finished_ok.emit(sha)

        except Exception as e:
            self.failed.emit(str(e))


# ---------- 主对话框 ----------
class ReleaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("墨码写作 - 一键发版")
        self.setMinimumSize(680, 640)
        self._worker = None
        self._build_ui()
        self._load_current_version()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        title = QLabel("一键发版")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)

        sub = QLabel("填写版本号和更新说明，点「开始发版」自动完成打包、上传、签名。")
        sub.setStyleSheet("color: #928a7c; font-size: 12px;")
        root.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.current_label = QLabel("—")
        self.current_label.setStyleSheet(
            "font-family: monospace; font-size: 13px; color: #6a6357;")
        form.addRow("当前版本", self.current_label)

        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("如：1.0.5")
        self.version_edit.setMaximumWidth(220)
        self.version_edit.setStyleSheet(
            "font-family: monospace; font-size: 14px; padding: 6px 10px;")
        form.addRow("新版本号", self.version_edit)

        root.addLayout(form)

        notes_label = QLabel("更新说明（会显示在用户的更新提示框里）")
        notes_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        root.addWidget(notes_label)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText(
            "例：\n1. 修复导入时章号乱序\n2. 优化排版预览速度")
        self.notes_edit.setMinimumHeight(80)
        self.notes_edit.setMaximumHeight(120)
        root.addWidget(self.notes_edit)

        self.step_label = QLabel("就绪")
        self.step_label.setStyleSheet(
            "font-size: 12px; color: #b08a4e; font-weight: 600;")
        root.addWidget(self.step_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 6)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)

        log_label = QLabel("运行日志")
        log_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        root.addWidget(log_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas, Microsoft YaHei", 10))
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background-color: #fcfaf4;"
            " border: 1px solid rgba(146,138,124,0.3);"
            " border-radius: 8px; padding: 8px 12px; }")
        root.addWidget(self.log_view, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.start_btn = QPushButton("开始发版")
        self.start_btn.setStyleSheet(
            "QPushButton { background-color: #c19860; color: white;"
            " border: none; border-radius: 8px; padding: 10px 28px;"
            " font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background-color: #d4a968; }"
            "QPushButton:disabled { background-color: #d8c8a8; }")
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #3b342a;"
            " border: 1px solid rgba(146,138,124,0.5);"
            " border-radius: 8px; padding: 10px 20px; font-size: 13px; }"
            "QPushButton:hover { background: rgba(193,152,96,0.14);"
            " border-color: #c19860; }")
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn)

        root.addLayout(btn_row)

    def _load_current_version(self):
        try:
            text = CONSTANTS_FILE.read_text(encoding="utf-8")
            m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
            if m:
                cur = m.group(1)
                self.current_label.setText(cur)
                parts = cur.split(".")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    next_ver = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
                    self.version_edit.setText(next_ver)
        except Exception as e:
            self.current_label.setText(f"读取失败：{e}")

    def _log(self, line: str):
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_start(self):
        version = self.version_edit.text().strip()
        if not VERSION_RE.match(version):
            QMessageBox.warning(
                self, "版本号格式错误",
                "版本号格式应为 x.y.z，如 1.0.5")
            self.version_edit.setFocus()
            return
        notes = self.notes_edit.toPlainText().strip()
        if not notes:
            QMessageBox.warning(self, "提示", "更新说明不能为空。")
            self.notes_edit.setFocus()
            return

        ret = QMessageBox.question(
            self, "确认发版",
            f"即将发布 v{version}：\n\n"
            f"1. 修改 ui/constants.py 的版本号\n"
            f"2. 打包主程序（约 3-5 分钟）\n"
            f"3. 上传到 39.96.64.137\n"
            f"4. 服务器签名并更新 latest.json\n\n"
            f"此过程不可撤销。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return

        self.log_view.clear()
        self.progress.setValue(0)
        self.step_label.setText("准备中…")

        self._worker = ReleaseWorker(version, notes)
        self._worker.log.connect(self._log)
        self._worker.step_changed.connect(self._on_step)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.failed.connect(self._on_fail)
        self._worker.finished.connect(self._on_worker_finished)

        self.start_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.version_edit.setEnabled(False)
        self.notes_edit.setEnabled(False)

        self._worker.start()

    def _on_step(self, step: int, name: str):
        self.progress.setValue(step - 1)
        self.step_label.setText(f"[{step}/6] {name}")

    def _on_ok(self, sha: str):
        self.progress.setValue(6)
        self.step_label.setText("✓ 发版完成")
        QMessageBox.information(
            self, "发版完成",
            f"已发布 v{self.version_edit.text().strip()}\n\n"
            f"SHA-256: {sha[:24]}…\n\n"
            f"用户下次启动软件时会收到更新提示。")

    def _on_fail(self, msg: str):
        self.step_label.setText("✗ 失败")
        self._log("")
        self._log(f"错误：{msg}")
        QMessageBox.critical(
            self, "发版失败",
            f"发版过程出错：\n\n{msg}\n\n"
            f"详见下方日志。")

    def _on_worker_finished(self):
        self.start_btn.setEnabled(True)
        self.close_btn.setEnabled(True)
        self.version_edit.setEnabled(True)
        self.notes_edit.setEnabled(True)
        self._worker = None

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            ret = QMessageBox.question(
                self, "发版进行中",
                "发版尚未完成，确定要关闭吗？\n"
                "关闭会中断发版，可能导致服务器上的版本不一致。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()


def main():
    os.chdir(ROOT)
    app = QApplication(sys.argv)
    app.setApplicationName("墨码发版工具")
    dlg = ReleaseDialog()
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()