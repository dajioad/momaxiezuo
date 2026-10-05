# ui/character_edit_dialog.py
"""墨码写作 - 角色卡编辑对话框

字段：名字（必填）、别名、身份、性别、年龄、外貌、性格、背景、
人物关系、首次出场、备注。

主题：使用 ui.theme_style.apply_dialog_theme 统一处理（自动从
parent 链或 app palette 取色，避免深色模式下漏黑底）。
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QDialogButtonBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFrame,
    QLabel,
)
from PySide6.QtCore import Qt

from ui.theme_style import apply_dialog_theme, _resolve_dialog_theme


class CharacterEditDialog(QDialog):
    """新建 / 编辑单个角色。"""

    def __init__(self, parent=None, data: dict = None):
        super().__init__(parent)
        data = data or {}
        self._data = dict(data)
        self.setWindowTitle("编辑角色" if data.get("name") else "新建角色")
        self.setMinimumSize(580, 620)
        self._build_ui()
        # 主题：一行搞定，自动拿 parent 链上的主题或 app palette 兜底
        apply_dialog_theme(self)
        self._apply_extra_style()
        self._load_data()

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

        form.addRow(self._section_label("基本信息"))

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("必填，如：林墨")
        name_label = QLabel("名字 <span style='color:#e74c3c;'>*</span>")
        name_label.setTextFormat(Qt.RichText)
        form.addRow(name_label, self.name_edit)

        self.aliases_edit = QLineEdit()
        self.aliases_edit.setPlaceholderText("用逗号分隔，如：墨哥，林兄")
        form.addRow("别名", self.aliases_edit)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("主角 / 反派 / 配角")
        row1.addWidget(self.role_edit, 2)
        self.gender_edit = QLineEdit()
        self.gender_edit.setPlaceholderText("性别")
        row1.addWidget(self.gender_edit, 1)
        self.age_edit = QLineEdit()
        self.age_edit.setPlaceholderText("年龄")
        row1.addWidget(self.age_edit, 1)
        form.addRow("身份 / 性别 / 年龄", row1)

        form.addRow(self._section_label("详细资料"))

        self.appearance_edit = QTextEdit()
        self.appearance_edit.setPlaceholderText("身高、体型、特征、服饰风格…")
        self.appearance_edit.setFixedHeight(60)
        form.addRow("外貌", self.appearance_edit)

        self.personality_edit = QTextEdit()
        self.personality_edit.setPlaceholderText("性格特点、说话方式、习惯…")
        self.personality_edit.setFixedHeight(60)
        form.addRow("性格", self.personality_edit)

        self.background_edit = QTextEdit()
        self.background_edit.setPlaceholderText("出身、经历、成长、恩怨…")
        self.background_edit.setFixedHeight(80)
        form.addRow("背景", self.background_edit)

        self.relations_edit = QTextEdit()
        self.relations_edit.setPlaceholderText(
            "每行一条，如：\n师父：李长生\n挚友：苏晚棠\n宿敌：赵无咎"
        )
        self.relations_edit.setFixedHeight(80)
        form.addRow("人物关系", self.relations_edit)

        self.first_appear_edit = QLineEdit()
        self.first_appear_edit.setPlaceholderText("首次出场：章节名或说明")
        form.addRow("首次出场", self.first_appear_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("其他想记录的信息、金句、伏笔…")
        self.notes_edit.setFixedHeight(60)
        form.addRow("备注", self.notes_edit)

        # 出场统计（只读；保存后由 CharacterPanel 扫描回填）
        stats = self._data.get("_stats") or {}
        stat_text = "（保存后自动统计）"
        if stats.get("total") is not None:
            if stats.get("chapter_count", 0) > 0:
                stat_text = (
                    f"出场 {stats['chapter_count']} 章 · "
                    f"共 {stats['total']} 次提及 · "
                    f"最后出场：{stats.get('last_title', '—')}"
                )
            elif self._data.get("name"):
                stat_text = "本作品中尚未出现"
        self.stat_label = QLabel(stat_text)
        self.stat_label.setWordWrap(True)
        form.addRow("出场统计", self.stat_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _apply_extra_style(self):
        """分组标题金色小字 + 单行输入框加高（参照 ui优化2 #15）。"""
        t = _resolve_dialog_theme(self)
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            self.styleSheet()
            + f"QLabel#formSection {{ color: {accent}; background: transparent;"
            f" font-size: 11px; font-weight: 600;"
            f" padding: 10px 0 4px 0; letter-spacing: 1px; }}"
            f"QLineEdit {{ min-height: 30px; }}"
        )

    def _load_data(self):
        self.name_edit.setText(self._data.get("name", ""))
        aliases = self._data.get("aliases", "")
        if isinstance(aliases, list):
            aliases = "，".join(aliases)
        self.aliases_edit.setText(str(aliases or ""))
        self.role_edit.setText(self._data.get("role", ""))
        self.gender_edit.setText(self._data.get("gender", ""))
        self.age_edit.setText(self._data.get("age", ""))
        self.appearance_edit.setPlainText(self._data.get("appearance", ""))
        self.personality_edit.setPlainText(self._data.get("personality", ""))
        self.background_edit.setPlainText(self._data.get("background", ""))
        self.relations_edit.setPlainText(self._data.get("relations", ""))
        self.first_appear_edit.setText(self._data.get("first_appear", ""))
        self.notes_edit.setPlainText(self._data.get("notes", ""))
        self.name_edit.setFocus()

    def _validate(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "名字不能为空。")
            self.name_edit.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        aliases_text = self.aliases_edit.text().strip()
        aliases = []
        if aliases_text:
            for sep in ("，", ","):
                aliases_text = aliases_text.replace(sep, "\n")
            aliases = [a.strip() for a in aliases_text.splitlines() if a.strip()]
        return {
            "name": self.name_edit.text().strip(),
            "aliases": aliases,
            "role": self.role_edit.text().strip(),
            "gender": self.gender_edit.text().strip(),
            "age": self.age_edit.text().strip(),
            "appearance": self.appearance_edit.toPlainText().strip(),
            "personality": self.personality_edit.toPlainText().strip(),
            "background": self.background_edit.toPlainText().strip(),
            "relations": self.relations_edit.toPlainText().strip(),
            "first_appear": self.first_appear_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
        }
