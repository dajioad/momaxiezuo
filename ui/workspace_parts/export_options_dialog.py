"""墨码写作 - 导出选项对话框"""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)


class _ExportOptionsDialog(QDialog):
    """导出选项对话框：方式 / 格式 / 范围 / 选项。"""

    def __init__(self, total_count: int, done_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出选项")
        self.setMinimumWidth(420)
        self._total_count = total_count
        self._done_count = done_count
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        # ---- 导出方式 ----
        lay.addWidget(QLabel("导出方式"))
        self.rb_merged = QRadioButton("合并为一个文件")
        self.rb_merged.setToolTip("所有章节拼成一个文件")
        self.rb_split = QRadioButton("分章导出（每章一个文件）")
        self.rb_split.setToolTip(
            "每章导出为独立文件，命名 <序号>_<章节标题>.<扩展名>\n"
            "需要选择一个输出目录"
        )
        self.rb_merged.setChecked(True)
        self.rb_merged.toggled.connect(self._on_mode_changed)
        lay.addWidget(self.rb_merged)
        lay.addWidget(self.rb_split)

        # ---- 格式 ----
        lay.addSpacing(4)
        lay.addWidget(QLabel("格式"))
        self.format_combo = QComboBox()
        # 初始就是"合并模式"，所以先填全套
        self._rebuild_format_combo(is_split=False)
        lay.addWidget(self.format_combo)

        # ---- 范围 ----
        lay.addSpacing(4)
        lay.addWidget(QLabel("导出范围"))
        self.rb_all = QRadioButton(f"全部章节（{self._total_count} 章）")
        self.rb_done = QRadioButton(f"仅已完成章节（{self._done_count} 章）")
        self.rb_all.setChecked(True)
        if self._done_count == 0:
            self.rb_done.setEnabled(False)
        lay.addWidget(self.rb_all)
        lay.addWidget(self.rb_done)

        # ---- 选项 ----
        lay.addSpacing(4)
        self.cb_blank = QCheckBox("章节之间空一行")
        self.cb_volume = QCheckBox("包含卷名")
        self.cb_volume.setChecked(True)
        self.cb_seq = QCheckBox("文件名加序号前缀（如 001_第一章）")
        self.cb_seq.setToolTip(
            "勾选后文件名按 001 / 002 / 003 编号，文件管理器里能正确排序。\n"
            "不勾则直接用章节标题做文件名。"
        )
        self.cb_seq.setChecked(False)  # 默认不勾：用户明确说不要序号
        lay.addWidget(self.cb_blank)
        lay.addWidget(self.cb_volume)
        lay.addWidget(self.cb_seq)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("下一步")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")  # ← 汉化
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _rebuild_format_combo(self, is_split: bool):
        """重建格式下拉。

        合并模式：UTF-8 / GBK / Markdown / Word docx / Word doc
        分章模式：只留 UTF-8 / GBK / Markdown
                 （每章一个 docx 没意义，直接从列表移除，不做灰色禁用
                  —— 部分 Qt 版本下拉弹出层不渲染 disabled 项）
        """
        # 记住当前选择，重建后尽量恢复
        prev = self.format_combo.currentData() if self.format_combo.count() else None

        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self.format_combo.addItem("UTF-8 文本 (*.txt)", "utf8_txt")
        self.format_combo.addItem("GBK 文本 (*.txt)", "gbk_txt")
        self.format_combo.addItem("Markdown (*.md)", "md")
        if not is_split:
            self.format_combo.addItem("Word 文档 (*.docx)", "docx")
            self.format_combo.addItem("Word 老格式 (*.doc)", "doc")

        # 恢复原选择；原选择在分章模式被移除了 → 落到第一项
        idx = self.format_combo.findData(prev) if prev else -1
        self.format_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.format_combo.blockSignals(False)

    def _on_mode_changed(self, *_):
        is_split = self.rb_split.isChecked()
        self._rebuild_format_combo(is_split)
        self.cb_blank.setEnabled(not is_split)
        self.cb_seq.setEnabled(is_split)  # ← 新增

    def options(self) -> dict:
        return {
            "mode": "split" if self.rb_split.isChecked() else "merged",
            "format": self.format_combo.currentData() or "utf8_txt",
            "only_done": self.rb_done.isChecked(),
            "blank_between": self.cb_blank.isChecked(),
            "volume_headers": self.cb_volume.isChecked(),
            "seq_prefix": self.cb_seq.isChecked(),  # ← 新增
        }
