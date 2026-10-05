# ui/proof_dict_manage.py
"""墨码写作 - 校对词库管理

三类词库：
- 自定义错别字：{错词: 正确词}
- 自定义敏感词：{分类: [词, ...]}
- 忽略列表：不再报告的问题键（规则 pattern / 词 / 提示）

全部落到 data_root/proof_dict.json。

敏感词支持批量导入 / 导出：
- 导入：粘贴多行文本（每行一个词），选择分类后一次性入库
- 导出：内置 + 自定义全部导出为 TSV，便于 Excel 查看或重新导入
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QFormLayout,
    QMessageBox,
    QDialogButtonBox,
    QInputDialog,
    QPlainTextEdit,
    QFileDialog,
)
from PySide6.QtCore import Qt

import qtawesome as qta

from ui.dialog_styles import list_qss, input_qss
from utils.proofreader import (
    load_custom_dict,
    save_custom_dict,
    add_typo,
    remove_typo,
    add_sensitive,
    remove_sensitive,
    clear_ignored,
    import_sensitive_words,
    export_sensitive_words,
)


class _SensitiveImportDialog(QDialog):
    """批量导入敏感词：粘贴多行文本 / 从文件导入（多选，支持 DOCX/JSON）+ 选择分类。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入敏感词")
        self.setMinimumSize(580, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 沿父链取当前主题图标色（父窗口通常是词库管理对话框）
        icon_text = "#3b342a"
        p = parent
        while p is not None:
            theme = getattr(p, "_theme", None)
            if theme:
                icon_text = theme.get("text", icon_text)
                break
            p = p.parent()

        hint = QLabel(
            "支持两种方式：\n"
            "  · 手动粘贴：每行一个词，空行忽略\n"
            "  · 从文件导入：可多选 TXT / CSV / TSV / MD / JSON / DOCX\n"
            "CSV / TSV / DOCX 表格：每行只取第一个字段\n"
            'JSON：支持 ["词1","词2"] 或 {"分类":["词",...]}\n\n'
            "词库来源推荐：\n"
            "  github.com/konsheng/Sensitive-lexicon\n"
            "  github.com/CCCpan/chinese-sensitive-words-mcp"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 14px; color: gray;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("分类："))
        self.cat_edit = QLineEdit("自定义")
        self.cat_edit.setPlaceholderText("如：涉政 / 色情 / 暴力")
        row.addWidget(self.cat_edit, 1)

        import_file_btn = QPushButton(
            qta.icon("fa5s.folder-open", color=icon_text), " 从文件导入…"
        )
        import_file_btn.setToolTip("可一次选多个文件；追加到当前文本末尾")
        import_file_btn.clicked.connect(self._pick_files)
        row.addWidget(import_file_btn)
        layout.addLayout(row)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "在此粘贴词表，每行一个词…\n"
            "或点上方「从文件导入」选择多个文件（内容会追加）。"
        )
        self.text_edit.setMinimumHeight(240)
        layout.addWidget(self.text_edit, 1)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("font-size: 14px; color: gray;")
        layout.addWidget(self.stats_label)
        self.text_edit.textChanged.connect(self._refresh_stats)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("导入")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ---------- 文件导入 ----------

    def _pick_files(self):
        from PySide6.QtWidgets import QFileDialog
        import os

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择词库文件（可多选）",
            "",
            "支持的格式 (*.txt *.csv *.tsv *.md *.json *.docx);;"
            "文本文件 (*.txt *.csv *.tsv *.md);;"
            "JSON (*.json);;"
            "Word 文档 (*.docx);;"
            "所有文件 (*)",
        )
        if not paths:
            return

        chunks = []
        failed = []
        for p in paths:
            text = self._read_file(p)
            if text is None:
                failed.append(os.path.basename(p))
                continue
            if text.strip():
                chunks.append(text.rstrip("\n"))

        if not chunks:
            QMessageBox.warning(
                self,
                "读取失败",
                "无法读取以下文件（编码或格式不支持）：\n" + "\n".join(failed),
            )
            return

        merged = "\n".join(chunks)
        # 追加到已有内容后面
        existing = self.text_edit.toPlainText().strip()
        if existing:
            merged = existing + "\n\n" + merged
        self.text_edit.setPlainText(merged)

        # 分类推名：只有单个文件时才用文件名做默认分类
        if len(paths) == 1 and self.cat_edit.text().strip() in ("", "自定义"):
            name = os.path.splitext(os.path.basename(paths[0]))[0]
            if name:
                self.cat_edit.setText(name)

        if failed:
            QMessageBox.information(
                self,
                "部分文件未读取",
                "以下文件无法识别编码或格式，已跳过：\n" + "\n".join(failed),
            )

    @staticmethod
    def _read_file(path: str):
        """读取文件文本。返回 None 表示读取失败。"""
        lower = path.lower()
        if lower.endswith(".docx"):
            return _SensitiveImportDialog._read_docx(path)
        if lower.endswith(".doc"):
            # 老式 .doc 是 OLE 二进制，没有内置解析器，跳过
            return None
        if lower.endswith(".json"):
            return _SensitiveImportDialog._read_json(path)
        # 纯文本类：多编码尝试
        return _SensitiveImportDialog._read_text(path)

    @staticmethod
    def _read_text(path: str):
        """多编码尝试读取纯文本。"""
        encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16")
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
            except OSError:
                return None
        # 最后一搏：忽略错误字符
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return None

    @staticmethod
    def _read_docx(path: str):
        """从 DOCX 提取纯文本。不依赖 python-docx，直接解 ZIP + XML。

        优先处理表格（每行 cell 用 tab 拼成 TSV，保留"词\\t分类"结构），
        再补充普通段落，按文档中出现的顺序。
        """
        try:
            import zipfile
            import xml.etree.ElementTree as ET
        except ImportError:
            return None

        ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        try:
            with zipfile.ZipFile(path) as z:
                with z.open("word/document.xml") as f:
                    tree = ET.parse(f)
        except Exception:
            return None

        def _para_text(p):
            return "".join(t.text or "" for t in p.iter(f"{{{ns_w}}}t"))

        lines = []
        root = tree.getroot()
        body = root.find(f"{{{ns_w}}}body")
        if body is None:
            body = root

        for child in body:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "p":
                text = _para_text(child).strip()
                if text:
                    lines.append(text)
            elif tag == "tbl":
                for tr in child.findall(f"{{{ns_w}}}tr"):
                    cells = []
                    for tc in tr.findall(f"{{{ns_w}}}tc"):
                        cell_text = "".join(
                            _para_text(p) for p in tc.findall(f"{{{ns_w}}}p")
                        ).strip()
                        cells.append(cell_text)
                    if any(cells):
                        lines.append("\t".join(cells))
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _read_json(path: str):
        """从 JSON 读词表。

        支持两种结构：
        - ["词1", "词2", ...]
        - {"分类A": ["词1", ...], "分类B": [...]}
          → 展开成 "词\\t分类"
        """
        import json

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        lines = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    lines.append(item)
                elif isinstance(item, dict):
                    w = item.get("word") or item.get("text") or item.get("value")
                    if w:
                        cat = (
                            item.get("category")
                            or item.get("cat")
                            or item.get("type")
                            or ""
                        )
                        lines.append(f"{w}\t{cat}" if cat else str(w))
        elif isinstance(data, dict):
            for cat, words in data.items():
                if isinstance(words, list):
                    for w in words:
                        if isinstance(w, str):
                            lines.append(f"{w}\t{cat}")
        return "\n".join(lines) if lines else ""

    # ---------- 交互 ----------

    def _refresh_stats(self):
        n = sum(1 for ln in self.text_edit.toPlainText().splitlines() if ln.strip())
        self.stats_label.setText(f"当前 {n} 行非空内容")

    def _validate(self):
        if not self.text_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "请粘贴或导入要添加的词表。")
            return
        self.accept()

    def get_data(self) -> tuple:
        return (self.cat_edit.text().strip() or "自定义", self.text_edit.toPlainText())


class ProofDictManageDialog(QDialog):
    """校对词库管理对话框。"""

    def __init__(self, data_root: str, parent=None):
        super().__init__(parent)
        self.data_root = data_root
        self.setWindowTitle("校对词库管理")
        self.setMinimumSize(680, 520)
        self._theme = self._resolve_theme()
        self._build_ui()
        self._apply_theme()
        self._reload_all()

    def _resolve_theme(self) -> dict:
        p = self.parent()
        while p is not None:
            ed = getattr(p, "editor", None)
            if ed is not None and hasattr(ed, "_theme"):
                return ed._theme or {}
            ws = getattr(p, "workspace", None)
            if ws is not None:
                ed = getattr(ws, "editor", None)
                if ed is not None and hasattr(ed, "_theme"):
                    return ed._theme or {}
                break
            p = p.parent()
        return {}

    def _apply_theme(self):
        t = self._theme or {
            "bg": "#f7f2e8",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "accent": "#b08a4e",
        }
        bg = t.get("bg", "#f7f2e8")
        muted = t.get("muted", "#9a8e76")
        self.setStyleSheet(
            f"ProofDictManageDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {t.get('text', '#3b342a')};"
            f" background: transparent; }}"
            + list_qss(t)
            + input_qss(t)
            + (f"QLineEdit, QComboBox {{ min-height: 32px; }}")
        )

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        tip = QLabel(
            "自定义词条会在下次校对时生效。忽略列表保存的是「不再报告」的规则键："
            "清空后所有被忽略的问题会重新出现。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("font-size: 14px; color: gray;")
        root.addWidget(tip)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_typo_tab(), "错别字")
        self.tabs.addTab(self._build_sensitive_tab(), "敏感词")
        self.tabs.addTab(self._build_ignored_tab(), "忽略列表")
        root.addWidget(self.tabs, 1)

        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.button(QDialogButtonBox.Close).setText("关闭")
        close_box.rejected.connect(self.reject)
        root.addWidget(close_box)

    # ---------------- 错别字 ----------------
    def _build_typo_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        self.typo_list = QListWidget()
        lay.addWidget(self.typo_list, 1)

        form = QHBoxLayout()
        form.addWidget(QLabel("错词："))
        self.typo_wrong = QLineEdit()
        self.typo_wrong.setPlaceholderText("如：辛好")
        form.addWidget(self.typo_wrong, 1)
        form.addWidget(QLabel("→ 正确："))
        self.typo_correct = QLineEdit()
        self.typo_correct.setPlaceholderText("如：幸好")
        form.addWidget(self.typo_correct, 1)
        add_btn = QPushButton("添加")
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._add_typo)
        form.addWidget(add_btn)
        lay.addLayout(form)

        row = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_typo)
        row.addWidget(del_btn)
        row.addStretch()
        lay.addLayout(row)
        return page

    def _reload_typos(self):
        self.typo_list.clear()
        data = load_custom_dict(self.data_root)
        accent = (self._theme or {}).get("accent", "#b08a4e")
        for wrong, correct in sorted(data["typos"].items()):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, wrong)
            lab = QLabel(
                f"{wrong}  <span style='color: {accent}; font-weight: 600;'>→</span>"
                f"  {correct}"
            )
            item.setSizeHint(lab.sizeHint())
            self.typo_list.addItem(item)
            self.typo_list.setItemWidget(item, lab)

    def _add_typo(self):
        wrong = self.typo_wrong.text().strip()
        correct = self.typo_correct.text().strip()
        if not wrong or not correct:
            QMessageBox.warning(self, "提示", "请填写错词和正确词。")
            return
        if add_typo(self.data_root, wrong, correct):
            self.typo_wrong.clear()
            self.typo_correct.clear()
            self._reload_typos()

    def _delete_typo(self):
        it = self.typo_list.currentItem()
        if not it:
            return
        wrong = it.data(Qt.UserRole)
        if QMessageBox.question(self, "删除", f"删除「{wrong}」？") == QMessageBox.Yes:
            remove_typo(self.data_root, wrong)
            self._reload_typos()

    # ---------------- 敏感词 ----------------
    def _build_sensitive_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        # 顶部工具行：批量导入 / 导出 / 清空
        tool_row = QHBoxLayout()
        import_btn = QPushButton(
            qta.icon("fa5s.file-import", color="#ffffff"), " 批量导入"
        )
        import_btn.setProperty("variant", "primary")
        import_btn.setToolTip(
            "粘贴多行文本批量导入。可从开源词库项目下载 TXT 后整段粘贴。"
        )
        import_btn.clicked.connect(self._import_sensitive_batch)
        tool_row.addWidget(import_btn)

        export_btn = QPushButton(
            qta.icon("fa5s.file-export",
                     color=(self._theme or {}).get("text", "#3b342a")),
            " 导出全部"
        )
        export_btn.setToolTip("导出内置 + 自定义全部敏感词（TSV 格式）")
        export_btn.clicked.connect(self._export_sensitive)
        tool_row.addWidget(export_btn)

        clear_all_btn = QPushButton(
            qta.icon("fa5s.trash-alt",
                     color=(self._theme or {}).get("text", "#3b342a")),
            " 清空自定义"
        )
        clear_all_btn.setToolTip("只清空用户添加的，内置词不变")
        clear_all_btn.clicked.connect(self._clear_custom_sensitive)
        tool_row.addWidget(clear_all_btn)
        tool_row.addStretch()
        lay.addLayout(tool_row)

        self.sens_list = QListWidget()
        lay.addWidget(self.sens_list, 1)

        form = QHBoxLayout()
        form.addWidget(QLabel("分类："))
        self.sens_cat = QLineEdit()
        self.sens_cat.setPlaceholderText("如：涉政 / 宗教")
        form.addWidget(self.sens_cat, 1)
        form.addWidget(QLabel("词："))
        self.sens_word = QLineEdit()
        form.addWidget(self.sens_word, 1)
        add_btn = QPushButton("添加")
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._add_sensitive)
        form.addWidget(add_btn)
        lay.addLayout(form)

        row = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_sensitive)
        row.addWidget(del_btn)
        row.addStretch()
        lay.addLayout(row)
        return page

    def _reload_sensitive(self):
        self.sens_list.clear()
        data = load_custom_dict(self.data_root)
        for cat, words in sorted(data["sensitive"].items()):
            for w in words:
                item = QListWidgetItem(f"[{cat}] {w}")
                item.setData(Qt.UserRole, (cat, w))
                self.sens_list.addItem(item)

    def _import_sensitive_batch(self):
        dlg = _SensitiveImportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        category, text = dlg.get_data()
        added = import_sensitive_words(self.data_root, text, category)
        if added <= 0:
            QMessageBox.information(
                self, "导入完成", "没有新增词条（全部已存在，或输入内容为空）。"
            )
            return
        self._reload_sensitive()
        QMessageBox.information(
            self,
            "导入完成",
            f"新增 {added} 个词，分类：{category}。\n" "下次校对时生效。",
        )

    def _export_sensitive(self):
        text = export_sensitive_words(self.data_root)
        if not text.strip():
            QMessageBox.information(self, "提示", "当前没有敏感词可导出。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出敏感词",
            "敏感词库.tsv",
            "TSV 文件 (*.tsv);;文本文件 (*.txt);;所有文件 (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write("分类\t词\n")
                f.write(text)
            QMessageBox.information(
                self, "导出完成", f"已导出到：\n{path}\n\n可用 Excel 打开或重新导入。"
            )
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _clear_custom_sensitive(self):
        if (
            QMessageBox.question(
                self,
                "清空自定义敏感词",
                "确定清空所有用户添加的敏感词？\n" "内置的示例词条不受影响。",
            )
            != QMessageBox.Yes
        ):
            return
        data = load_custom_dict(self.data_root)
        data["sensitive"] = {}
        save_custom_dict(self.data_root, data)
        self._reload_sensitive()

    def _add_sensitive(self):
        cat = self.sens_cat.text().strip()
        word = self.sens_word.text().strip()
        if not cat or not word:
            QMessageBox.warning(self, "提示", "请填写分类和词。")
            return
        if add_sensitive(self.data_root, cat, word):
            self.sens_word.clear()
            self._reload_sensitive()

    def _delete_sensitive(self):
        it = self.sens_list.currentItem()
        if not it:
            return
        cat, word = it.data(Qt.UserRole)
        if QMessageBox.question(self, "删除", f"删除「{word}」？") == QMessageBox.Yes:
            remove_sensitive(self.data_root, cat, word)
            self._reload_sensitive()

    # ---------------- 忽略列表 ----------------
    def _build_ignored_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        hint = QLabel(
            "这些是你在校对结果里选择「忽略」的规则或词条。"
            "清空后它们会重新参与校对。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 14px; color: gray;")
        lay.addWidget(hint)

        self.ign_list = QListWidget()
        lay.addWidget(self.ign_list, 1)

        row = QHBoxLayout()
        clear_btn = QPushButton("清空忽略列表")
        clear_btn.clicked.connect(self._clear_ignored)
        row.addWidget(clear_btn)
        row.addStretch()
        lay.addLayout(row)
        return page

    def _reload_ignored(self):
        self.ign_list.clear()
        data = load_custom_dict(self.data_root)
        for key in sorted(data["ignored"]):
            self.ign_list.addItem(key)

    def _clear_ignored(self):
        if (
            QMessageBox.question(
                self, "清空", "清空全部忽略条目？所有被忽略的问题将重新参与校对。"
            )
            == QMessageBox.Yes
        ):
            clear_ignored(self.data_root)
            self._reload_ignored()

    # ---------------- 通用 ----------------
    def _reload_all(self):
        self._reload_typos()
        self._reload_sensitive()
        self._reload_ignored()
