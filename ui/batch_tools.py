# ui/batch_tools.py
"""墨码写作 - 批量工具

面向写作者保留 6 个批量功能（全部本地执行）：
1. 批量重命名（前缀 + 序号，可选改后缀，预览后执行）
2. 文件名清理（删除/替换文件名中的指定字符，保留扩展名）
3. 批量改后缀
4. 批量内容替换（支持正则，执行前自动 .bak.<时间戳> 备份）—— 全书改名/术语统一
5. 编码转换（GBK↔UTF-8 等，默认 .bak.<时间戳> 备份）—— 老稿乱码修复
6. 合并文件（可排序，合并为投稿 TXT）

备份文件用 .bak.<时间戳>，多次执行不会互相覆盖。
批量替换的「统计匹配数」同时显示前 10 条差异预览。
"""

import os
import re
import shutil
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QListWidget,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QPlainTextEdit,
)
from PySide6.QtCore import Qt
import qtawesome as qta

from ui.dialog_styles import apply_dialog_layout
from utils.logger import get_logger

logger = get_logger()

TEXT_FILTER = (
    "文本文件 (*.txt *.md *.csv *.json *.xml *.html *.log *.ini *.yaml *.yml);;"
    "所有文件 (*.*)"
)


class _FileList(QListWidget):
    """文件列表：支持从外部拖入文件/文件夹"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        self.add_paths(paths)

    def add_paths(self, paths):
        existing = {self.item(i).text() for i in range(self.count())}
        for p in paths:
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in sorted(files):
                        fp = os.path.join(root, f)
                        if fp not in existing:
                            self.addItem(fp)
                            existing.add(fp)
            elif os.path.isfile(p) and p not in existing:
                self.addItem(p)
                existing.add(p)

    def files(self):
        return [self.item(i).text() for i in range(self.count())]


class BatchToolsDialog(QDialog):
    def __init__(self, initial_files=None, initial_dir: str = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量工具")
        self.setMinimumSize(820, 600)
        self._initial_dir = initial_dir or ""
        self._build_ui()
        self.apply_theme_colors()
        if initial_files:
            files = list(initial_files)
            self.replace_list.add_paths(files)
            self.merge_list.add_paths(files)
            self.clean_list.add_paths(files)  # ← 文件名清理也带入初始文件

    # ---------------- 通用 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        apply_dialog_layout(root)
        tabs = QTabWidget()
        tabs.addTab(self._build_rename_tab(), "批量重命名")
        tabs.addTab(self._build_filename_clean_tab(), "文件名清理")
        tabs.addTab(self._build_suffix_tab(), "批量改后缀")
        tabs.addTab(self._build_replace_tab(), "批量内容替换")
        tabs.addTab(self._build_encoding_tab(), "编码转换")
        tabs.addTab(self._build_merge_tab(), "合并文件")
        root.addWidget(tabs)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        root.addLayout(row)

        # 美化：输入框 + 按钮 + 列表 统一风格
        t = self._resolve_theme()
        from ui.dialog_styles import input_qss, button_qss, list_qss
        from ui.theme_style import qss_alpha, accent_button_qss

        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg") or "white"
        accent_hover = t.get("accent_hover", accent)
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        warn = "#e74c3c"

        # 三档按钮样式：主按钮强调色实底 / 次按钮描边 / 危险按钮无边框 hover 变红。
        # 几何参数与全局 controls_qss 对齐（圆角 9、min-height 18）。
        tier_qss = (
            # 主按钮
            f'QPushButton[btn_tier="primary"] {{'
            f"  background-color: {accent}; color: {accent_fg};"
            f"  border: 1px solid {accent}; border-radius: 9px;"
            f"  padding: 7px 18px; font-size: 12px; font-weight: 600;"
            f"  min-height: 18px; }}"
            f'QPushButton[btn_tier="primary"]:hover {{'
            f"  background-color: {accent_hover};"
            f"  border-color: {accent_hover}; }}"
            f'QPushButton[btn_tier="primary"]:pressed {{'
            f"  background-color: {accent}; }}"
            # 次按钮
            f'QPushButton[btn_tier="ghost"] {{'
            f"  background-color: {editor_bg}; color: {text};"
            f"  border: 1px solid {qss_alpha(muted, 0x55)};"
            f"  border-radius: 9px; padding: 7px 18px;"
            f"  font-size: 12px; font-weight: 500; min-height: 18px; }}"
            f'QPushButton[btn_tier="ghost"]:hover {{'
            f"  background-color: {qss_alpha(accent, 0x10)};"
            f"  color: {accent}; border-color: {accent}; }}"
            # 危险按钮
            f'QPushButton[btn_tier="danger"] {{'
            f"  background-color: transparent; color: {muted};"
            f"  border: none; border-radius: 9px;"
            f"  padding: 7px 14px; font-size: 12px; font-weight: 500;"
            f"  min-height: 18px; }}"
            f'QPushButton[btn_tier="danger"]:hover {{'
            f"  background-color: {qss_alpha(warn, 0x10)};"
            f"  color: {warn}; }}"
        )

        self.setStyleSheet(input_qss(t) + button_qss(t) + list_qss(t) + tier_qss)

        # 给按钮设置图标（颜色随 tier 变）
        for btn in self.findChildren(QPushButton):
            name = getattr(btn, "_icon_name", "")
            if not name:
                continue
            tier = btn.property("btn_tier")
            if tier == "primary":
                btn.setIcon(qta.icon(name, color=accent_fg))
            elif tier == "danger":
                btn.setIcon(qta.icon(name, color=muted))
            else:
                btn.setIcon(qta.icon(name, color=text))

    def _file_controls(
        self, list_widget: _FileList, title: str = "文件列表（可拖入文件或文件夹）"
    ):
        box = QVBoxLayout()
        box.addWidget(QLabel(title))
        box.addWidget(list_widget, 1)
        row = QHBoxLayout()
        row.setSpacing(8)

        # 主按钮：添加文件
        add_files = QPushButton(" 添加文件")
        add_files._icon_name = (
            "fa5s.file-medical"  # 带加号的文件图标，bookshelf.py 里也用的这个
        )
        add_files.setProperty("btn_tier", "primary")
        add_files.setCursor(Qt.PointingHandCursor)
        add_files.clicked.connect(lambda: self._pick_files(list_widget))

        # 次按钮：添加文件夹
        add_dir = QPushButton(" 添加文件夹")
        add_dir._icon_name = "fa5s.folder-plus"
        add_dir.setProperty("btn_tier", "ghost")
        add_dir.setCursor(Qt.PointingHandCursor)
        add_dir.clicked.connect(lambda: self._pick_dir(list_widget))

        # 次按钮：移除选中
        remove = QPushButton(" 移除选中")
        remove._icon_name = "fa5s.minus-circle"
        remove.setProperty("btn_tier", "ghost")
        remove.setCursor(Qt.PointingHandCursor)
        remove.clicked.connect(lambda: self._remove_selected(list_widget))

        # 危险按钮：清空（无边框，hover 变红）
        clear = QPushButton(" 清空")
        clear._icon_name = "fa5s.trash-alt"
        clear.setProperty("btn_tier", "danger")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(list_widget.clear)

        for b in (add_files, add_dir, remove, clear):
            row.addWidget(b)
        row.addStretch()
        box.addLayout(row)
        return box

    def _pick_files(self, target: _FileList):
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", self._initial_dir)
        if files:
            target.add_paths(files)

    def _pick_dir(self, target: _FileList):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹", self._initial_dir)
        if d:
            target.add_paths([d])

    @staticmethod
    def _remove_selected(target: _FileList):
        for item in target.selectedItems():
            target.takeItem(target.row(item))

    @staticmethod
    def _backup_path(path: str) -> str:
        """给批量替换/编码转换的备份文件加时间戳，避免多次执行互相覆盖。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{path}.bak.{ts}"

    # ---------------- 1. 批量重命名 ----------------
    def _build_rename_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        apply_dialog_layout(lay)
        self.rename_list = _FileList()
        lay.addLayout(self._file_controls(self.rename_list))

        form = QHBoxLayout()
        form.addWidget(QLabel("前缀："))
        self.rename_prefix = QLineEdit()
        self.rename_prefix.setPlaceholderText("如：第01章_")
        form.addWidget(self.rename_prefix)
        form.addWidget(QLabel("起始序号："))
        self.rename_start = QSpinBox()
        self.rename_start.setMinimum(0)
        self.rename_start.setValue(1)
        self.rename_start.setMaximum(999999)
        form.addWidget(self.rename_start)
        form.addWidget(QLabel("序号位数："))
        self.rename_pad = QSpinBox()
        self.rename_pad.setMinimum(1)
        self.rename_pad.setValue(3)
        self.rename_pad.setMaximum(10)
        form.addWidget(self.rename_pad)
        form.addWidget(QLabel("后缀："))
        self.rename_ext = QComboBox()
        self.rename_ext.addItems(["保持原后缀", ".txt", ".md"])
        form.addWidget(self.rename_ext)
        lay.addLayout(form)

        self.rename_preview = QPlainTextEdit()
        self.rename_preview.setReadOnly(True)
        self.rename_preview.setMaximumHeight(120)
        self.rename_preview.setPlaceholderText("点击「预览」查看改名结果…")
        lay.addWidget(self.rename_preview)

        row = QHBoxLayout()
        row.addStretch()
        preview_btn = QPushButton(qta.icon("fa5s.eye", color="#b08a4e"), " 预览")
        preview_btn.clicked.connect(self._rename_preview)
        exec_btn = QPushButton(qta.icon("fa5s.check", color="white"), " 执行重命名")
        exec_btn.setProperty("variant", "primary")
        exec_btn.clicked.connect(self._rename_exec)
        row.addWidget(preview_btn)
        row.addWidget(exec_btn)
        lay.addLayout(row)
        return page

    def _new_name(self, index: int, path: str) -> str:
        ext = (
            os.path.splitext(path)[1]
            if self.rename_ext.currentText() == "保持原后缀"
            else self.rename_ext.currentText()
        )
        num = str(self.rename_start.value() + index).zfill(self.rename_pad.value())
        return f"{self.rename_prefix.text()}{num}{ext}"

    def _rename_preview(self):
        files = self.rename_list.files()
        if not files:
            return
        lines = [
            f"{os.path.basename(f)}  →  {self._new_name(i, f)}"
            for i, f in enumerate(files)
        ]
        self.rename_preview.setPlainText("\n".join(lines))

    def _rename_exec(self):
        files = self.rename_list.files()
        if not files:
            return
        # 冲突预检（目标路径不能与现有文件撞名，也不能互相重复）
        targets = []
        for i, f in enumerate(files):
            new_path = os.path.join(os.path.dirname(f), self._new_name(i, f))
            targets.append(new_path)
        if len(set(targets)) != len(targets):
            QMessageBox.warning(
                self, "无法执行", "生成的新文件名存在重复，请调整前缀/序号设置。"
            )
            return
        conflicts = [
            p
            for p in targets
            if os.path.exists(p)
            and os.path.normcase(p) not in {os.path.normcase(x) for x in files}
        ]
        if conflicts:
            QMessageBox.warning(
                self,
                "无法执行",
                f"有 {len(conflicts)} 个目标文件已存在，为避免覆盖已中止。",
            )
            return
        done = 0
        for f, new_path in zip(files, targets):
            try:
                os.rename(f, new_path)
                done += 1
            except OSError as e:
                logger.warning(f"重命名失败 {f}: {e}")
        QMessageBox.information(
            self, "完成", f"成功重命名 {done}/{len(files)} 个文件。"
        )
        self.rename_list.clear()
        self.rename_list.add_paths(targets)

    # ---------------- 2. 文件名清理 ----------------
    def _build_filename_clean_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        apply_dialog_layout(lay)
        self.clean_list = _FileList()
        lay.addLayout(
            self._file_controls(self.clean_list, "待处理文件（可拖入文件或文件夹）")
        )

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("要删除的字符："))
        self.clean_chars = QLineEdit()
        self.clean_chars.setPlaceholderText("如：_    （多个字符用 | 分隔，如 _|-）")
        row1.addWidget(self.clean_chars, 1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("替换为："))
        self.clean_replacement = QLineEdit()
        self.clean_replacement.setPlaceholderText("留空 = 直接删除")
        row2.addWidget(self.clean_replacement, 1)
        lay.addLayout(row2)

        self.clean_ignore_ext = QCheckBox("保留文件扩展名不变（推荐）")
        self.clean_ignore_ext.setChecked(True)
        lay.addWidget(self.clean_ignore_ext)

        self.clean_preview = QPlainTextEdit()
        self.clean_preview.setReadOnly(True)
        self.clean_preview.setMaximumHeight(160)
        self.clean_preview.setPlaceholderText("点击「预览」查看改名结果…")
        lay.addWidget(self.clean_preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        preview_btn = QPushButton(qta.icon("fa5s.eye", color="#b08a4e"), " 预览")
        preview_btn.clicked.connect(self._clean_preview)
        exec_btn = QPushButton(qta.icon("fa5s.check", color="white"), " 执行清理")
        exec_btn.setProperty("variant", "primary")
        exec_btn.clicked.connect(self._clean_exec)
        btn_row.addWidget(preview_btn)
        btn_row.addWidget(exec_btn)
        lay.addLayout(btn_row)
        lay.addStretch()
        return page

    def _clean_new_name(self, path: str) -> str:
        """根据规则生成新文件名（含扩展名）。"""
        base = os.path.basename(path)
        stem, ext = os.path.splitext(base)

        raw = self.clean_chars.text().strip()
        if not raw:
            return base

        # 支持用 | 分隔多个要删除/替换的字符
        chars = [c for c in raw.split("|") if c]
        replacement = self.clean_replacement.text()

        new_stem = stem
        for ch in chars:
            new_stem = new_stem.replace(ch, replacement)

        if self.clean_ignore_ext.isChecked():
            return new_stem + ext
        return new_stem

    def _clean_preview(self):
        files = self.clean_list.files()
        if not files:
            QMessageBox.information(self, "提示", "请先添加文件。")
            return
        raw = self.clean_chars.text().strip()
        if not raw:
            QMessageBox.information(self, "提示", "请输入要删除的字符。")
            return
        lines = []
        for f in files:
            old = os.path.basename(f)
            new = self._clean_new_name(f)
            flag = "  " if old != new else "  （无变化）"
            lines.append(f"{old}  →  {new}{flag}")
        self.clean_preview.setPlainText("\n".join(lines))

    def _clean_exec(self):
        files = self.clean_list.files()
        if not files:
            QMessageBox.information(self, "提示", "请先添加文件。")
            return
        raw = self.clean_chars.text().strip()
        if not raw:
            QMessageBox.information(self, "提示", "请输入要删除的字符。")
            return

        # 计算目标路径
        targets = []
        for f in files:
            new_name = self._clean_new_name(f)
            new_path = os.path.join(os.path.dirname(f), new_name)
            targets.append(new_path)

        # 目标重名预检
        if len(set(targets)) != len(targets):
            QMessageBox.warning(
                self, "无法执行", "生成的新文件名存在重复，请调整规则。"
            )
            return

        # 与已有文件冲突预检（自身重命名不算冲突）
        conflicts = [
            p
            for p in targets
            if os.path.exists(p)
            and os.path.normcase(p) not in {os.path.normcase(x) for x in files}
        ]
        if conflicts:
            QMessageBox.warning(
                self,
                "无法执行",
                f"有 {len(conflicts)} 个目标文件已存在，为避免覆盖已中止。",
            )
            return

        done = skipped = failed = 0
        for f, new_path in zip(files, targets):
            if os.path.normcase(f) == os.path.normcase(new_path):
                skipped += 1
                continue
            try:
                os.rename(f, new_path)
                done += 1
            except OSError as e:
                failed += 1
                logger.warning(f"文件名清理失败 {f}: {e}")
        QMessageBox.information(
            self,
            "完成",
            f"成功清理 {done} 个，跳过 {skipped} 个（无变化），失败 {failed} 个。",
        )
        self.clean_list.clear()
        self.clean_list.add_paths(targets)

    # ---------------- 3. 批量改后缀 ----------------
    def _build_suffix_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        apply_dialog_layout(lay)
        self.suffix_list = _FileList()
        lay.addLayout(self._file_controls(self.suffix_list))
        row = QHBoxLayout()
        row.addWidget(QLabel("原后缀："))
        self.suffix_from = QComboBox()
        self.suffix_from.setEditable(True)
        self.suffix_from.addItems([".txt", ".md", ".text", ".doc", ".docx"])
        row.addWidget(self.suffix_from)
        row.addWidget(QLabel("新后缀："))
        self.suffix_to = QComboBox()
        self.suffix_to.setEditable(True)
        self.suffix_to.addItems([".txt", ".md", ".text"])
        row.addWidget(self.suffix_to)
        row.addStretch()
        lay.addLayout(row)
        exec_btn = QPushButton(qta.icon("fa5s.check", color="white"), " 批量修改后缀")
        exec_btn.setProperty("variant", "primary")
        exec_btn.clicked.connect(self._suffix_exec)
        exec_row = QHBoxLayout()
        exec_row.addStretch()
        exec_row.addWidget(exec_btn)
        lay.addLayout(exec_row)
        lay.addStretch()
        return page

    def _suffix_exec(self):
        files = self.suffix_list.files()
        old = self.suffix_from.currentText().strip().lower()
        new = self.suffix_to.currentText().strip()
        if not files or not old or not new:
            QMessageBox.information(self, "提示", "请先添加文件并填写后缀。")
            return
        if not old.startswith(".") or not new.startswith("."):
            QMessageBox.warning(self, "格式错误", "后缀应以点号开头，如 .txt")
            return
        done = skipped = failed = 0
        for f in files:
            if os.path.splitext(f)[1].lower() != old:
                skipped += 1
                continue
            new_path = os.path.splitext(f)[0] + new
            if os.path.exists(new_path):
                skipped += 1
                continue
            try:
                os.rename(f, new_path)
                done += 1
            except OSError:
                failed += 1
        QMessageBox.information(
            self, "完成", f"成功 {done} 个，跳过 {skipped} 个，失败 {failed} 个。"
        )

    # ---------------- 4. 批量内容替换 ----------------
    def _build_replace_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        apply_dialog_layout(lay)
        self.replace_list = _FileList()
        lay.addLayout(
            self._file_controls(
                self.replace_list, "目标文件（可拖入整部作品的 chapters 文件夹）"
            )
        )

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("查找："))
        self.replace_find = QLineEdit()
        self.replace_find.setPlaceholderText("如：王小明（全书改名/术语统一）")
        row1.addWidget(self.replace_find)
        lay.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("替换："))
        self.replace_to = QLineEdit()
        row2.addWidget(self.replace_to)
        lay.addLayout(row2)
        opt_row = QHBoxLayout()
        self.replace_regex = QCheckBox("使用正则表达式")
        self.replace_bak = QCheckBox("替换前自动备份（.bak.<时间戳>）")
        self.replace_bak.setChecked(True)
        opt_row.addWidget(self.replace_regex)
        opt_row.addWidget(self.replace_bak)
        opt_row.addStretch()
        lay.addLayout(opt_row)

        self.replace_output = QPlainTextEdit()
        self.replace_output.setReadOnly(True)
        self.replace_output.setMaximumHeight(220)
        lay.addWidget(self.replace_output, 1)

        row = QHBoxLayout()
        row.addStretch()
        preview_btn = QPushButton("统计匹配数 + 差异预览")
        preview_btn.clicked.connect(self._replace_preview)
        exec_btn = QPushButton(qta.icon("fa5s.check", color="white"), " 执行替换")
        exec_btn.setProperty("variant", "primary")
        exec_btn.clicked.connect(self._replace_exec)
        row.addWidget(preview_btn)
        row.addWidget(exec_btn)
        lay.addLayout(row)
        return page

    def _iter_file_text(self):
        """逐个读出文件文本，自动尝试 UTF-8/GBK"""
        for path in self.replace_list.files():
            text = None
            used_enc = "utf-8"
            for enc in ("utf-8", "gbk", "utf-16"):
                try:
                    with open(path, "r", encoding=enc) as f:
                        text = f.read()
                    used_enc = enc
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            if text is None:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text, used_enc = f.read(), "utf-8"
            yield path, text, used_enc

    def _collect_diff_samples(
        self, text: str, find: str, replacement: str, max_samples: int
    ):
        """从一段文本中收集最多 max_samples 个 (before, after) 样本。"""
        samples = []
        if self.replace_regex.isChecked():
            try:
                for m in re.finditer(find, text):
                    if len(samples) >= max_samples:
                        break
                    s, e = m.start(), m.end()
                    before = text[max(0, s - 10) : e + 10]
                    replaced = text[:s] + replacement + text[e:]
                    after = replaced[max(0, s - 10) : s + len(replacement) + 10]
                    samples.append((before, after))
            except re.error:
                pass
        else:
            idx = 0
            while len(samples) < max_samples:
                pos = text.find(find, idx)
                if pos < 0:
                    break
                before = text[max(0, pos - 10) : pos + len(find) + 10]
                replaced = text[:pos] + replacement + text[pos + len(find) :]
                after = replaced[max(0, pos - 10) : pos + len(replacement) + 10]
                samples.append((before, after))
                idx = pos + len(find)
        return samples

    def _replace_preview(self):
        find = self.replace_find.text()
        if not find:
            return
        replacement = self.replace_to.text()
        total = 0
        lines = []
        diff_lines = []
        MAX_SAMPLES = 10
        for path, text, _ in self._iter_file_text():
            if self.replace_regex.isChecked():
                try:
                    n = len(re.findall(find, text))
                except re.error as e:
                    QMessageBox.warning(self, "正则错误", str(e))
                    return
            else:
                n = text.count(find)
            total += n
            lines.append(f"{os.path.basename(path)}：{n} 处")
            # 差异样本（最多 10 条，全局）
            if n > 0 and len(diff_lines) < MAX_SAMPLES:
                remaining = MAX_SAMPLES - len(diff_lines)
                for before, after in self._collect_diff_samples(
                    text, find, replacement, remaining
                ):
                    diff_lines.append(
                        f"  {os.path.basename(path)}\n"
                        f"    - …{before}…\n"
                        f"    + …{after}…"
                    )
                    if len(diff_lines) >= MAX_SAMPLES:
                        break

        summary = f"共 {total} 处匹配\n" + "\n".join(lines)
        if diff_lines:
            summary += "\n\n── 前 10 条差异预览 ──\n" + "\n".join(diff_lines)
        self.replace_output.setPlainText(summary)

    def _replace_exec(self):
        find = self.replace_find.text()
        if not find:
            QMessageBox.information(self, "提示", "请输入查找内容。")
            return
        if not self.replace_list.files():
            return
        changed_files = total = failed = 0
        for path, text, enc in self._iter_file_text():
            try:
                if self.replace_regex.isChecked():
                    new_text, n = re.subn(find, self.replace_to.text(), text)
                else:
                    n = text.count(find)
                    new_text = text.replace(find, self.replace_to.text())
                if n <= 0:
                    continue
                if self.replace_bak.isChecked():
                    shutil.copy2(path, self._backup_path(path))
                with open(path, "w", encoding=enc, newline="") as f:
                    f.write(new_text)
                changed_files += 1
                total += n
            except (OSError, re.error) as e:
                failed += 1
                logger.warning(f"批量替换失败 {path}: {e}")
        QMessageBox.information(
            self,
            "完成",
            f"已修改 {changed_files} 个文件、共 {total} 处。"
            + (
                "\n原稿已备份为 .bak.<时间戳> 文件。"
                if self.replace_bak.isChecked()
                else ""
            )
            + (f"\n失败 {failed} 个。" if failed else ""),
        )

    # ---------------- 5. 编码转换 ----------------
    def _build_encoding_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        apply_dialog_layout(lay)
        self.encoding_list = _FileList()
        lay.addLayout(self._file_controls(self.encoding_list))
        row = QHBoxLayout()
        encs = ["utf-8", "gbk", "gb2312", "gb18030", "big5", "utf-16", "latin1"]
        row.addWidget(QLabel("源编码："))
        self.enc_from = QComboBox()
        self.enc_from.addItems(encs)
        self.enc_from.setCurrentText("gbk")
        row.addWidget(self.enc_from)
        row.addWidget(QLabel("→ 目标编码："))
        self.enc_to = QComboBox()
        self.enc_to.addItems(encs)
        self.enc_to.setCurrentText("utf-8")
        row.addWidget(self.enc_to)
        auto_btn = QPushButton("自动识别源编码")
        auto_btn.setToolTip("按 UTF-8 → GBK 顺序尝试读取")
        self.enc_auto = QCheckBox("自动识别")
        self.enc_auto.setChecked(True)
        row.addWidget(self.enc_auto)
        row.addStretch()
        lay.addLayout(row)
        self.enc_bak = QCheckBox("转换前自动备份（.bak.<时间戳>）")
        self.enc_bak.setChecked(True)
        lay.addWidget(self.enc_bak)
        exec_btn = QPushButton(qta.icon("fa5s.check", color="white"), " 开始转换编码")
        exec_btn.setProperty("variant", "primary")
        exec_btn.clicked.connect(self._encoding_exec)
        exec_row = QHBoxLayout()
        exec_row.addStretch()
        exec_row.addWidget(exec_btn)
        lay.addLayout(exec_row)
        lay.addStretch()
        return page

    def _encoding_exec(self):
        files = self.encoding_list.files()
        if not files:
            return
        to_enc = self.enc_to.currentText()
        done = failed = 0
        for path in files:
            text = None
            candidates = (
                ["utf-8", "gbk", "gb18030", "utf-16"]
                if self.enc_auto.isChecked()
                else [self.enc_from.currentText()]
            )
            for enc in candidates:
                try:
                    with open(path, "r", encoding=enc) as f:
                        text = f.read()
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            if text is None:
                failed += 1
                continue
            try:
                if self.enc_bak.isChecked():
                    shutil.copy2(path, self._backup_path(path))
                with open(path, "w", encoding=to_enc, newline="") as f:
                    f.write(text)
                done += 1
            except (OSError, UnicodeEncodeError) as e:
                failed += 1
                logger.warning(f"编码转换失败 {path}: {e}")
        QMessageBox.information(
            self,
            "完成",
            f"成功转换 {done} 个，失败 {failed} 个。\n"
            "失败通常是因为源编码识别不准或目标编码不支持某些字符。",
        )

    # ---------------- 6. 合并文件 ----------------
    def _build_merge_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        apply_dialog_layout(lay)
        self.merge_list = _FileList()
        lay.addLayout(
            self._file_controls(
                self.merge_list, "待合并文件（可拖入；用上下按钮调整顺序）"
            )
        )
        order_row = QHBoxLayout()
        _muted = self._resolve_theme().get("muted", "#9a8e76")
        up_btn = QPushButton(qta.icon("fa5s.arrow-up", color=_muted), " 上移")
        down_btn = QPushButton(qta.icon("fa5s.arrow-down", color=_muted), " 下移")
        up_btn.clicked.connect(lambda: self._move_merge_item(-1))
        down_btn.clicked.connect(lambda: self._move_merge_item(1))
        order_row.addWidget(up_btn)
        order_row.addWidget(down_btn)
        order_row.addStretch()
        lay.addLayout(order_row)
        opt_row = QHBoxLayout()
        self.merge_header = QCheckBox("在每段前加入文件名作为标题")
        self.merge_blank = QCheckBox("文件之间空一行")
        self.merge_blank.setChecked(True)
        opt_row.addWidget(self.merge_header)
        opt_row.addWidget(self.merge_blank)
        opt_row.addStretch()
        lay.addLayout(opt_row)
        row = QHBoxLayout()
        row.addStretch()
        exec_btn = QPushButton(
            qta.icon("fa5s.file-export", color="white"), " 合并并另存为"
        )
        exec_btn.setProperty("variant", "primary")
        exec_btn.clicked.connect(self._merge_exec)
        row.addWidget(exec_btn)
        lay.addLayout(row)
        lay.addStretch()
        return page

    def _move_merge_item(self, delta: int):
        row = self.merge_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.merge_list.count()):
            return
        item = self.merge_list.takeItem(row)
        self.merge_list.insertItem(target, item)
        self.merge_list.setCurrentRow(target)

    def _merge_exec(self):
        files = self.merge_list.files()
        if not files:
            return
        save_path, selected = QFileDialog.getSaveFileName(
            self,
            "保存合并文件",
            self._initial_dir or "合并文稿.txt",
            "UTF-8 文本 (*.txt);;GBK 文本 (*.txt)",
        )
        if not save_path:
            return
        encoding = "gbk" if "GBK" in selected else "utf-8"
        errors = "replace" if encoding == "gbk" else "strict"
        sep = "\n\n" if self.merge_blank.isChecked() else "\n"
        try:
            with open(
                save_path, "w", encoding=encoding, errors=errors, newline=""
            ) as out:
                for i, f in enumerate(files):
                    if i > 0:
                        out.write(sep)
                    if self.merge_header.isChecked():
                        out.write(os.path.splitext(os.path.basename(f))[0] + "\n\n")
                    text = None
                    for enc in ("utf-8", "gbk", "utf-16"):
                        try:
                            with open(f, "r", encoding=enc) as src:
                                text = src.read()
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
                    out.write((text or "").strip())
                    out.write("\n")
            QMessageBox.information(
                self, "完成", f"已合并 {len(files)} 个文件：\n{save_path}"
            )
        except OSError as e:
            QMessageBox.critical(self, "失败", str(e))

    def _resolve_accent(self) -> str:
        """沿 parent 链查找当前写作主题强调色，找不到则用米白兜底"""
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    t = editor._theme or {}
                    if t.get("accent"):
                        return t["accent"]
                break
            p = p.parent()
        return "#b08a4e"

    def apply_theme_colors(self):
        """主题切换后刷新强调色相关按钮图标/样式"""
        t = self._resolve_theme()
        accent = t.get("accent") or self._resolve_accent()
        muted = t.get("muted", "#9a8e76")
        text = t.get("text", "#3b342a")
        from ui.writing_editor import accent_foreground

        fg = t.get("accent_fg") or accent_foreground(accent)

        # 更新所有「预览」/「执行」/「上移」/「下移」按钮
        for btn in self.findChildren(QPushButton):
            txt = btn.text()
            tier = btn.property("btn_tier")
            name = getattr(btn, "_icon_name", "")

            # 带 btn_tier 标记的按钮（批量工具顶部按钮行）：按 tier 决定图标色
            if name and tier in ("primary", "ghost", "danger"):
                if tier == "primary":
                    btn.setIcon(qta.icon(name, color=fg))
                elif tier == "danger":
                    btn.setIcon(qta.icon(name, color=muted))
                else:
                    btn.setIcon(qta.icon(name, color=text))
                continue

            # 原来的图标刷新逻辑（保持）
            if txt.startswith(" 预览") or txt.startswith("预览"):
                btn.setIcon(qta.icon("fa5s.eye", color=accent))
            elif (
                txt.startswith(" 执行")
                or txt.startswith("执行")
                or txt.startswith(" 批量修改")
                or txt.startswith(" 开始转换编码")
                or txt.startswith(" 合并并另存为")
            ):
                icon_name = (
                    "fa5s.file-export"
                    if txt.startswith(" 合并并另存为")
                    else "fa5s.check"
                )
                btn.setIcon(qta.icon(icon_name, color=fg))
            elif txt.startswith(" 上移"):
                btn.setIcon(qta.icon("fa5s.arrow-up", color=muted))
            elif txt.startswith(" 下移"):
                btn.setIcon(qta.icon("fa5s.arrow-down", color=muted))

    def _resolve_theme(self) -> dict:
        """沿 parent 链查找写作主题（含 muted/accent 等）"""
        p = self.parent()
        while p is not None:
            ws = getattr(p, "workspace", None)
            if ws is not None:
                editor = getattr(ws, "editor", None)
                if editor is not None and hasattr(editor, "_theme"):
                    return editor._theme or {}
                break
            p = p.parent()
        return {}
