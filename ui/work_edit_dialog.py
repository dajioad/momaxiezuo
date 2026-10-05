# ui/work_edit_dialog.py
"""墨码写作 - 新建作品对话框

字段：书名（必填）、作者、作品状态、简介、每日字数目标。
主题：使用 ui.theme_style.apply_dialog_theme 统一处理（自动从
parent 链或 app palette 取色，避免深色模式下漏黑底）。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QDialogButtonBox,
    QMessageBox,
    QComboBox,
    QSpinBox,
    QLabel,
    QScrollArea,
    QWidget,
    QFrame,
)
from PySide6.QtCore import Qt

from ui.theme_style import apply_dialog_theme, _resolve_dialog_theme


class WorkCreateDialog(QDialog):
    """新建作品对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建作品")
        self.setMinimumSize(560, 520)
        self._build_ui()
        apply_dialog_theme(self)
        self._apply_extra_style()

    # ---------- UI ----------
    def _section_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("formSection")
        return lab

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # ---------- 基本信息 ----------
        form.addRow(self._section_label("基本信息"))

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("必填，如：长安诡事簿")
        title_label = QLabel("书名 <span style='color:#e74c3c;'>*</span>")
        title_label.setTextFormat(Qt.RichText)
        form.addRow(title_label, self.title_edit)

        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("笔名（可留空）")
        form.addRow("作者", self.author_edit)

        self.status_combo = QComboBox()
        for key, label in (
            ("ongoing", "连载中"),
            ("planning", "构思中"),
            ("paused", "暂停更新"),
            ("completed", "已完结"),
        ):
            self.status_combo.addItem(label, key)
        self.status_combo.setCurrentIndex(0)  # 默认连载中
        self.status_combo.setToolTip(
            "新书默认「连载中」。开始动笔前可选「构思中」。\n"
            "状态会以彩色标签显示在作品卡片上。"
        )
        form.addRow("作品状态", self.status_combo)

        # ---------- 作品详情 ----------
        form.addRow(self._section_label("作品详情"))

        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText(
            "一句话说说这个故事。\n"
            "例：长安城接连出现离奇命案，死者身上都有一枚相同的玉佩……"
        )
        self.desc_edit.setFixedHeight(110)
        form.addRow("简介", self.desc_edit)

        self.target_spin = QSpinBox()
        self.target_spin.setRange(0, 100000)
        self.target_spin.setSingleStep(500)
        self.target_spin.setSuffix(" 字")
        self.target_spin.setSpecialValueText("不设定目标")
        self.target_spin.setValue(0)
        self.target_spin.setMinimumWidth(160)
        self.target_spin.setToolTip(
            "每日码字目标。设定后，作品画廊和码字统计会显示进度。"
        )
        form.addRow("每日字数目标", self.target_spin)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("开始创作")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _apply_extra_style(self):
        """分组标题金色小字 + 单行输入框加高。"""
        t = _resolve_dialog_theme(self)
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            self.styleSheet()
            + f"QLabel#formSection {{ color: {accent}; background: transparent;"
            f" font-size: 11px; font-weight: 600;"
            f" padding: 10px 0 4px 0; letter-spacing: 1px; }}"
            f"QLineEdit {{ min-height: 30px; }}"
        )

    def _validate(self):
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "提示", "书名不能为空。")
            self.title_edit.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "title": self.title_edit.text().strip(),
            "author": self.author_edit.text().strip(),
            "status": self.status_combo.currentData() or "ongoing",
            "description": self.desc_edit.toPlainText().strip(),
            "daily_target": int(self.target_spin.value()),
        }
