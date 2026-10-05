# ui/timeline_edit_dialog.py
"""墨码写作 - 时间线事件编辑对话框"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QSpinBox,
    QDialogButtonBox,
    QMessageBox,
    QCheckBox,
    QLabel,
    QScrollArea,
    QWidget,
    QFrame,
)
from PySide6.QtCore import Qt
from ui.theme_style import apply_dialog_theme
from utils import timeline as tl


class TimelineEditDialog(QDialog):
    """新建 / 编辑时间线事件。"""

    def __init__(self, parent=None, work=None, event: dict = None):
        super().__init__(parent)
        self._work = work
        self._event = event or {}
        self.setWindowTitle("编辑事件" if event else "新建事件")
        self.setMinimumSize(540, 560)
        self._build_ui()
        apply_dialog_theme(self)
        if event:
            self._load()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("必填，如：林墨拜师")
        form.addRow("标题 *", self.title_edit)

        # 时代（下拉 + 可编辑）
        self.era_combo = QComboBox()
        self.era_combo.setEditable(True)
        self.era_combo.addItem("（不指定）", "")
        if self._work:
            data = tl.load_all(self._work)
            for era in data.get("eras", []):
                self.era_combo.addItem(era["label"], era["id"])
        form.addRow("时代", self.era_combo)

        # 故事内时间（文本，如"开元三年春"）
        self.story_time_edit = QLineEdit()
        self.story_time_edit.setPlaceholderText("如：开元三年春 / 三百年前")
        form.addRow("故事内时间", self.story_time_edit)

        # 排序值（数字）
        self.sort_spin = QSpinBox()
        self.sort_spin.setRange(-999999, 999999)
        self.sort_spin.setValue(0)
        self.sort_spin.setToolTip(
            "用于排序。数值小的排前面。\n"
            '建议：把"开元元年"填 1000，"开元三百年"填 1300，\n'
            '把"三百年前"填 -300（负数表示更早）'
        )
        form.addRow("排序值", self.sort_spin)

        # 关联章节
        self.chapter_combo = QComboBox()
        self.chapter_combo.addItem("（不关联）", "")
        if self._work:
            for ch in self._work.chapters:
                self.chapter_combo.addItem(
                    f"{ch.get('id', '')}  {ch.get('title', '')}", ch.get("id", "")
                )
        form.addRow("关联章节", self.chapter_combo)

        # 重要性
        self.importance_combo = QComboBox()
        self.importance_combo.addItem("主要事件", "major")
        self.importance_combo.addItem("次要事件", "minor")
        form.addRow("重要性", self.importance_combo)

        # 持续
        self.duration_edit = QLineEdit()
        self.duration_edit.setPlaceholderText("如：当日 / 三天 / 半年")
        form.addRow("持续", self.duration_edit)

        # 关联人物
        self.chars_edit = QLineEdit()
        self.chars_edit.setPlaceholderText("人物卡名字，用逗号分隔")
        form.addRow("关联人物", self.chars_edit)

        # 描述
        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText("详细描述…")
        self.desc_edit.setFixedHeight(80)
        form.addRow("描述", self.desc_edit)

        # 标签
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用「、」分隔")
        form.addRow("标签", self.tags_edit)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load(self):
        ev = self._event
        self.title_edit.setText(ev.get("title", ""))
        idx = self.era_combo.findData(ev.get("era_id", ""))
        if idx >= 0:
            self.era_combo.setCurrentIndex(idx)
        self.story_time_edit.setText(ev.get("story_time", ""))
        self.sort_spin.setValue(ev.get("story_time_sort", 0))
        idx = self.chapter_combo.findData(ev.get("chapter_id", ""))
        if idx >= 0:
            self.chapter_combo.setCurrentIndex(idx)
        idx = self.importance_combo.findData(ev.get("importance", "major"))
        if idx >= 0:
            self.importance_combo.setCurrentIndex(idx)
        self.duration_edit.setText(ev.get("duration", ""))
        self.chars_edit.setText("，".join(ev.get("characters") or []))
        self.desc_edit.setPlainText(ev.get("description", ""))
        self.tags_edit.setText("、".join(ev.get("tags") or []))

    def _validate(self):
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "提示", "事件标题不能为空。")
            return
        self.accept()

    def get_data(self) -> dict:
        chars_text = self.chars_edit.text()
        for sep in ("，", ",", "、"):
            chars_text = chars_text.replace(sep, "\n")
        chars = [c.strip() for c in chars_text.splitlines() if c.strip()]

        tags_text = self.tags_edit.text()
        for sep in ("、", ",", "，"):
            tags_text = tags_text.replace(sep, "\n")
        tags = [t.strip() for t in tags_text.splitlines() if t.strip()]

        return {
            "title": self.title_edit.text().strip(),
            "era_id": self.era_combo.currentData() or "",
            "story_time": self.story_time_edit.text().strip(),
            "story_time_sort": self.sort_spin.value(),
            "chapter_id": self.chapter_combo.currentData() or "",
            "importance": self.importance_combo.currentData() or "major",
            "duration": self.duration_edit.text().strip(),
            "characters": chars,
            "description": self.desc_edit.toPlainText().strip(),
            "tags": tags,
        }
