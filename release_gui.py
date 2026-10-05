# release_gui.py
"""墨码写作 - 一键发版 GUI 工具（Gitee 版）

双击「启动发版工具.bat」运行，填表即自动完成 9 步：
  1. 改版本号（ui/constants.py + 打包.iss）
  2. PyInstaller 打包主程序
  3. 生成 update.zip（自动更新包，排除 updater.exe）
  4. 生成绿色包 zip（完整，含 updater.exe）
  5. 编译 Inno Setup 安装包（可选）
  6. 计算 SHA-256，用本地私钥签名并生成 latest.json
  7. 提交并推送 master 与版本标签
  8. 创建 Gitee 发行版
  9. 上传附件（update.zip / 绿色包 / 安装包），已存在则跳过

前置条件：
  - 本地签名私钥：%USERPROFILE%\\.moma_release\\keys\\update_private.key
  - Gitee 私人令牌（projects 权限），只填在界面里，不保存、不入库
  - 可用的 git（官方安装版或免安装 MinGit）+ Gitee SSH 公钥

只在开发环境使用，不打包进产品。
"""
import base64
import datetime
import hashlib
import os
import pathlib
import re
import subprocess
import zipfile

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import sys

# ---------- 配置 ----------
ROOT = pathlib.Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
CONSTANTS_FILE = ROOT / "ui" / "constants.py"
ISS_FILE = ROOT / "打包.iss"
MANIFEST_FILE = ROOT / "latest.json"
DIST_DIR = ROOT / "dist" / "墨码写作"
INSTALLER_OUT_DIR = ROOT / "installer_output"
ISCC_CANDIDATES = [
    pathlib.Path(r"D:\Inno Setup 6\ISCC.exe"),
    pathlib.Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
]

# Gitee 仓库
GITEE_OWNER = "momaxiezuo"
GITEE_REPO = "ink-code-writing"
GITEE_API = "https://gitee.com/api/v5"
# 附件直链是确定性 URL（标签名+文件名），可在上传前写进清单
RELEASE_PAGE = (f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/releases")

# 本地签名私钥（从旧服务器抢救，仓库外保存）
SIGN_KEY_FILE = (pathlib.Path.home() / ".moma_release" / "keys"
                 / "update_private.key")

MIN_SUPPORTED = "1.0.0"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def find_git() -> str:
    """找可用的 git：优先免安装 MinGit，其次 PATH 上的 git。"""
    mingit = (pathlib.Path(os.environ.get("LOCALAPPDATA", ""))
              / "Programs" / "MinGit" / "cmd" / "git.exe")
    if mingit.exists():
        return str(mingit)
    return "git"


def find_iscc() -> pathlib.Path:
    for p in ISCC_CANDIDATES:
        if p.exists():
            return p
    return None


# ---------- 后台发版 Worker ----------
class ReleaseWorker(QThread):
    log = Signal(str)                    # 一行日志
    step_changed = Signal(int, str)      # (第几步, 步骤名)
    finished_ok = Signal(str, str)       # (SHA, 发行版地址)
    failed = Signal(str)                 # 失败，带错误

    TOTAL = 9

    def __init__(self, version: str, notes: str, token: str,
                 build_installer: bool):
        super().__init__()
        self.version = version
        self.notes = notes
        self.token = token.strip()
        self.build_installer = build_installer
        self._stop = False
        self.setObjectName("GiteeReleaseWorker")
        self._git = find_git()

    def stop(self):
        self._stop = True

    # ---------- 通用工具 ----------
    def _check_stop(self):
        if self._stop:
            raise Exception("用户取消发版")

    def _run_stream(self, cmd, cwd=None):
        """运行命令，逐行推日志。失败抛异常。"""
        self._check_stop()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        system_ssh = r"C:\Windows\System32\OpenSSH\ssh.exe"
        if os.path.exists(system_ssh):
            env["GIT_SSH"] = system_ssh
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
        if proc.returncode != 0:
            raise Exception(f"命令失败（退出码 {proc.returncode}）：{' '.join(map(str, cmd[:3]))}")

    def _git_cmd(self, *args, check=True):
        """执行 git，返回 stdout 文本（不逐行刷屏）。"""
        self._check_stop()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        system_ssh = r"C:\Windows\System32\OpenSSH\ssh.exe"
        if os.path.exists(system_ssh):
            env["GIT_SSH"] = system_ssh
        creationflags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.run(
            [self._git, *args],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
        if check and proc.returncode != 0:
            raise Exception(f"git {' '.join(args[:2])} 失败：{proc.stderr.strip()}")
        return proc.stdout.strip()

    def _api(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{GITEE_API}/repos/{GITEE_OWNER}/{GITEE_REPO}/{path.lstrip('/')}"
        kwargs.setdefault("timeout", 60)
        kwargs.setdefault("headers", {"User-Agent": "MoMa-Release/1.0"})
        return requests.request(method, url, **kwargs)

    # ---------- 各步骤 ----------
    def _bump_version(self):
        # ui/constants.py
        text = CONSTANTS_FILE.read_text(encoding="utf-8")
        new_text, n = re.subn(
            r'APP_VERSION\s*=\s*"[^"]+"',
            f'APP_VERSION = "{self.version}"',
            text,
        )
        if n == 0:
            raise Exception("ui/constants.py 里没找到 APP_VERSION")
        CONSTANTS_FILE.write_text(new_text, encoding="utf-8")

        # 打包.iss
        if ISS_FILE.exists():
            iss = ISS_FILE.read_text(encoding="utf-8")
            iss_new = re.subn(
                r'#define\s+MyAppVersion\s+"[^"]+"',
                f'#define MyAppVersion     "{self.version}"',
                iss,
            )[0]
            if iss_new != iss:
                ISS_FILE.write_text(iss_new, encoding="utf-8")
        self.log.emit(f"  版本号已改为 {self.version}（constants + iss）")

    def _make_zip(self, include_updater: bool, out_path: pathlib.Path,
                 label: str) -> pathlib.Path:
        if not DIST_DIR.exists():
            raise Exception(f"未找到打包产物: {DIST_DIR}")
        if out_path.exists():
            out_path.unlink()
        file_count = skip_count = 0
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for f in DIST_DIR.rglob("*"):
                self._check_stop()
                if not f.is_file():
                    continue
                if not include_updater and f.name == "updater.exe":
                    skip_count += 1
                    continue
                zf.write(f, f.relative_to(DIST_DIR))
                file_count += 1
        size_mb = out_path.stat().st_size / (1024 * 1024)
        self.log.emit(f"  {label}: {out_path.name} "
                      f"{file_count} 文件 / {size_mb:.1f} MB"
                      + ("，跳过 updater.exe" if skip_count else ""))
        return out_path

    def _build_installer(self) -> pathlib.Path:
        iscc = find_iscc()
        if iscc is None:
            raise Exception("找不到 Inno Setup 6（ISCC.exe），"
                            "请取消勾选安装包或安装 Inno Setup 6")
        if not INSTALLER_OUT_DIR.exists():
            INSTALLER_OUT_DIR.mkdir(parents=True, exist_ok=True)
        # 记录编译前已有 exe 的时间戳，编译后靠“新/变化的文件”定位产物
        before = {}
        for f in INSTALLER_OUT_DIR.glob("*.exe"):
            before[f.resolve()] = f.stat().st_mtime

        self._run_stream([str(iscc), str(ISS_FILE)])

        candidates = [
            f for f in INSTALLER_OUT_DIR.glob("*.exe")
            if f.resolve() not in before or f.stat().st_mtime > before[f.resolve()]
        ]
        if not candidates:
            raise Exception("Inno 编译后未在 installer_output 发现新 exe")
        src = max(candidates, key=lambda f: f.stat().st_mtime)
        dst = ROOT / "dist" / f"moma-{self.version}-win-x64.exe"
        dst.write_bytes(src.read_bytes())
        self.log.emit(f"  安装包: {dst.name} "
                      f"{dst.stat().st_size / (1024 * 1024):.1f} MB")
        return dst

    def _sha256(self, path: pathlib.Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                self._check_stop()
                h.update(chunk)
        return h.hexdigest().lower()

    def _sign_manifest(self, sha: str) -> dict:
        """用本地私钥签 sha256，生成 latest.json。"""
        if not SIGN_KEY_FILE.exists():
            raise Exception(f"找不到签名私钥: {SIGN_KEY_FILE}")
        pk = serialization.load_pem_private_key(
            SIGN_KEY_FILE.read_bytes(), password=None)
        signature = base64.b64encode(
            pk.sign(sha.encode("utf-8"), padding.PKCS1v15(),
                    hashes.SHA256())).decode("ascii")

        import json
        data = {
            "version": self.version,
            "pub_date": datetime.date.today().isoformat(),
            "notes": self.notes,
            "min_supported": MIN_SUPPORTED,
            "download_url":
                f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}"
                f"/releases/download/{self.version}/update.zip",
            "download_page": RELEASE_PAGE,
            "sha256": sha,
            "signature": signature,
        }
        MANIFEST_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.log.emit(f"  已用本地私钥签名并写入 {MANIFEST_FILE.name}")
        return data

    def _git_commit_and_push(self):
        """提交版本号改动 + latest.json，推送 master 与标签。"""
        self._git_cmd("add", "ui/constants.py", str(ISS_FILE.name),
                      MANIFEST_FILE.name)
        staged = self._git_cmd("diff", "--cached", "--name-only")
        if staged:
            msg = (f"release: v{self.version}\n\n{self.notes}")
            self._git_cmd("commit", "-m", msg)
            self.log.emit("  已提交版本改动")
        else:
            self.log.emit("  无文件变更需要提交")

        # push master，远端有新提交时 rebase 后重试一次
        pushed = False
        for attempt in range(2):
            r = subprocess.run(
                [self._git, "push", "origin", "master"],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                env={**os.environ,
                     "GIT_SSH": r"C:\Windows\System32\OpenSSH\ssh.exe"},
                creationflags=0x08000000 if os.name == "nt" else 0)
            if r.returncode == 0:
                pushed = True
                break
            if attempt == 0 and ("non-fast-forward" in r.stderr
                                 or "fetch first" in r.stderr
                                 or "rejected" in r.stderr):
                self.log.emit("  远端有新提交，rebase 后重试…")
                self._git_cmd("pull", "--rebase", "origin", "master")
        if not pushed:
            raise Exception("推送 master 失败")
        self.log.emit("  master 已推送")

        # 标签：远端没有才创建并推送（附注标签）
        remote_tags = self._git_cmd("ls-remote", "--tags", "origin")
        if f"refs/tags/{self.version}" not in remote_tags:
            local_tags = self._git_cmd("tag", "--list", self.version)
            if not local_tags:
                self._git_cmd("tag", "-a", self.version, "-m",
                              f"墨码写作 v{self.version}")
            self._git_cmd("push", "origin", self.version)
            self.log.emit(f"  标签 {self.version} 已推送")
        else:
            self.log.emit(f"  标签 {self.version} 已存在，跳过")

    def _ensure_release(self) -> int:
        """创建（或复用）Gitee 发行版，返回 release id。"""
        r = self._api("GET", "releases",
                      params={"access_token": self.token, "page": 1,
                              "per_page": 100})
        if r.status_code != 200:
            raise Exception(f"查询发行版列表 HTTP {r.status_code}: {r.text[:200]}")
        for item in r.json():
            if item.get("tag_name") == self.version:
                self.log.emit(f"  发行版已存在：id={item['id']}，直接补附件")
                return item["id"]

        body = (
            f"## 墨码写作 v{self.version}\n\n{self.notes}\n\n"
            "### 下载\n"
            f"- 安装包：`moma-{self.version}-win-x64.exe`\n"
            f"- 绿色包：`moma-{self.version}-win-x64.zip`（解压即用）\n\n"
            "- 仅支持 Windows x64\n"
            "- 所有稿件保存在本机，AI 能力可选、可关闭\n"
        )
        r = self._api("POST", "releases",
                      data={"access_token": self.token,
                            "tag_name": self.version,
                            "target_commitish": "master",
                            "name": f"墨码写作 v{self.version}",
                            "body": body,
                            "prerelease": "false"})
        if r.status_code not in (200, 201):
            raise Exception(f"创建发行版 HTTP {r.status_code}: {r.text[:300]}")
        rid = r.json()["id"]
        self.log.emit(f"  已创建发行版：id={rid}")
        return rid

    def _upload_attachments(self, release_id: int, files):
        r = self._api("GET", f"releases/{release_id}/attach_files",
                      params={"access_token": self.token, "page": 1,
                              "per_page": 100})
        existing = set()
        if r.status_code == 200:
            existing = {a.get("name") for a in r.json()}

        import time
        for path in files:
            self._check_stop()
            name = path.name
            if name in existing:
                self.log.emit(f"  跳过（附件已存在）: {name}")
                continue
            size_mb = path.stat().st_size / (1024 * 1024)
            # 大文件上传偶发断连（Response ended prematurely 等），
            # 逐文件重试 3 次，指数退避；成功即止。
            last_err = ""
            for attempt in range(1, 4):
                self._check_stop()
                self.log.emit(
                    f"  上传 {name}（{size_mb:.1f} MB）"
                    + (f"第 {attempt} 次尝试…" if attempt > 1 else "…"))
                try:
                    with open(path, "rb") as f:
                        r = self._api(
                            "POST",
                            f"releases/{release_id}/attach_files",
                            data={"access_token": self.token, "name": name},
                            files={"file": (name, f,
                                            "application/octet-stream")},
                            timeout=600)
                    if r.status_code in (200, 201):
                        self.log.emit(
                            f"    OK -> {r.json().get('browser_download_url')}")
                        last_err = ""
                        break
                    last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                except requests.RequestException as e:
                    last_err = str(e)
                if attempt < 3:
                    wait = 5 * attempt
                    self.log.emit(f"    连接中断（{last_err}），{wait}s 后重试")
                    time.sleep(wait)
            if last_err:
                raise Exception(f"上传 {name} 重试 3 次仍失败：{last_err}")

    # ---------- 主流程 ----------
    def run(self):
        try:
            s = lambda i, name: self.step_changed.emit(i, name)  # noqa: E731

            self.log.emit(f"===== 开始发版 v{self.version}（Gitee）=====")

            s(1, "改版本号")
            self._bump_version()

            s(2, "打包主程序（约 3-5 分钟）")
            self._run_stream([str(VENV_PY), "build.py"])

            s(3, "生成 update.zip（自动更新包）")
            update_zip = self._make_zip(
                False, ROOT / "dist" / "update.zip", "自动更新包")

            s(4, "生成绿色包 zip")
            green_zip = self._make_zip(
                True,
                ROOT / "dist" / f"moma-{self.version}-win-x64.zip",
                "绿色包")

            attachments = [update_zip, green_zip]
            s(5, "编译 Inno 安装包")
            if self.build_installer:
                attachments.append(self._build_installer())
            else:
                self.log.emit("  已勾选跳过安装包编译")

            s(6, "计算 SHA-256 并本地签名")
            sha = self._sha256(update_zip)
            self.log.emit(f"  SHA-256: {sha}")
            self._sign_manifest(sha)

            s(7, "提交并推送 master 与标签")
            self._git_commit_and_push()

            s(8, "创建 Gitee 发行版")
            release_id = self._ensure_release()

            s(9, "上传发行版附件")
            self._upload_attachments(release_id, attachments)

            self.log.emit("")
            self.log.emit("=" * 50)
            self.log.emit(f"  发版完成! v{self.version}")
            self.log.emit("=" * 50)
            self.finished_ok.emit(
                sha,
                f"{RELEASE_PAGE}/{self.version}")

        except Exception as e:
            self.failed.emit(str(e))


# ---------- 主对话框 ----------
class ReleaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("墨码写作 - Gitee 一键发版")
        self.setMinimumSize(700, 700)
        self._worker = None
        self._build_ui()
        self._load_current_version()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("Gitee 一键发版")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)

        sub = QLabel("本地打包 → 本地私钥签名 → 推送代码/标签 → "
                     "创建 Gitee 发行版并上传附件，全自动。")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #928a7c; font-size: 12px;")
        root.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.current_label = QLabel("—")
        self.current_label.setStyleSheet(
            "font-family: monospace; font-size: 13px; color: #6a6357;")
        form.addRow("当前版本", self.current_label)

        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("如：1.0.1")
        self.version_edit.setMaximumWidth(220)
        form.addRow("新版本号", self.version_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Gitee 私人令牌（projects 权限），不保存")
        self.token_edit.setEchoMode(QLineEdit.Password)
        if os.environ.get("GITEE_TOKEN"):
            self.token_edit.setText(os.environ["GITEE_TOKEN"])
        form.addRow("Gitee 令牌", self.token_edit)

        self.installer_chk = QCheckBox("同时编译 Inno Setup 安装包（约多 1 分钟）")
        self.installer_chk.setChecked(find_iscc() is not None)
        form.addRow("", self.installer_chk)

        root.addLayout(form)

        notes_label = QLabel("更新说明（会写进 latest.json 并显示在用户更新提示里）")
        notes_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        root.addWidget(notes_label)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("例：\n1. 修复导入时章号乱序\n2. 优化排版预览速度")
        self.notes_edit.setMinimumHeight(70)
        self.notes_edit.setMaximumHeight(110)
        root.addWidget(self.notes_edit)

        key_tip = QLabel(
            f"签名私钥：{SIGN_KEY_FILE}    "
            + ("✓ 已就绪" if SIGN_KEY_FILE.exists() else "✗ 缺失，无法签名"))
        key_tip.setStyleSheet(
            f"font-size: 11px; color: {'#2e7d32' if SIGN_KEY_FILE.exists() else '#c0392b'};")
        root.addWidget(key_tip)

        self.step_label = QLabel("就绪")
        self.step_label.setStyleSheet(
            "font-size: 12px; color: #b08a4e; font-weight: 600;")
        root.addWidget(self.step_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, ReleaseWorker.TOTAL)
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
                    self.version_edit.setText(
                        f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}")
        except Exception as e:
            self.current_label.setText(f"读取失败：{e}")

    def _log(self, line: str):
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_start(self):
        version = self.version_edit.text().strip()
        if not VERSION_RE.match(version):
            QMessageBox.warning(self, "版本号格式错误",
                                "版本号格式应为 x.y.z，如 1.0.1")
            self.version_edit.setFocus()
            return
        notes = self.notes_edit.toPlainText().strip()
        if not notes:
            QMessageBox.warning(self, "提示", "更新说明不能为空。")
            self.notes_edit.setFocus()
            return
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "缺少令牌",
                                "请填写 Gitee 私人令牌（projects 权限）。")
            self.token_edit.setFocus()
            return
        if not SIGN_KEY_FILE.exists():
            QMessageBox.critical(
                self, "缺少签名私钥",
                f"找不到本地签名私钥：\n{SIGN_KEY_FILE}\n\n无法发版。")
            return
        if self.installer_chk.isChecked() and find_iscc() is None:
            QMessageBox.warning(self, "缺少 Inno Setup",
                                "勾选了编译安装包但没找到 ISCC.exe。")
            return

        cur = self.current_label.text()
        warn = ""
        if VERSION_RE.match(cur):
            from utils.updater import compare_version
            if compare_version(cur, version) > 0:
                warn = f"\n\n⚠ 注意：{version} 比当前版本 {cur} 低（降级发布），老用户不会收到更新提示。"

        ret = QMessageBox.question(
            self, "确认发版",
            f"即将发布 v{version}：\n\n"
            f"1. 本地打包（3-5 分钟）\n"
            f"2. 本地私钥签名生成 latest.json\n"
            f"3. 推送 master 与标签 {version} 到 Gitee\n"
            f"4. 创建 Gitee 发行版并上传附件\n\n"
            f"此过程不可撤销。是否继续？{warn}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return

        self.log_view.clear()
        self.progress.setValue(0)
        self.step_label.setText("准备中…")

        self._worker = ReleaseWorker(
            version, notes, token, self.installer_chk.isChecked())
        self._worker.log.connect(self._log)
        self._worker.step_changed.connect(self._on_step)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.failed.connect(self._on_fail)
        self._worker.finished.connect(self._on_worker_finished)

        for w in (self.start_btn, self.close_btn, self.version_edit,
                  self.notes_edit, self.token_edit, self.installer_chk):
            w.setEnabled(False)
        self._worker.start()

    def _on_step(self, step: int, name: str):
        self.progress.setValue(step - 1)
        self.step_label.setText(f"[{step}/{ReleaseWorker.TOTAL}] {name}")

    def _on_ok(self, sha: str, url: str):
        self.progress.setValue(ReleaseWorker.TOTAL)
        self.step_label.setText("✓ 发版完成")
        QMessageBox.information(
            self, "发版完成",
            f"已发布 v{self.version_edit.text().strip()}\n\n"
            f"SHA-256: {sha[:24]}…\n"
            f"发行版：{url}\n\n"
            f"清单已推送到 master，用户最多 1 分钟内检测到更新（CDN 缓存）。")

    def _on_fail(self, msg: str):
        self.step_label.setText("✗ 失败")
        self._log("")
        self._log(f"错误：{msg}")
        QMessageBox.critical(self, "发版失败",
                             f"发版过程出错：\n\n{msg}\n\n详见下方日志。")

    def _on_worker_finished(self):
        for w in (self.start_btn, self.close_btn, self.version_edit,
                  self.notes_edit, self.token_edit, self.installer_chk):
            w.setEnabled(True)
        self._worker = None

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            ret = QMessageBox.question(
                self, "发版进行中",
                "发版尚未完成，确定要关闭吗？\n关闭会中断发版流程。",
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
