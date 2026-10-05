# ui/import_folder_dialog.py
"""墨码写作 - 从文件夹导入的选项对话框

检测到文件夹里有多少文件；让用户选择：
- 每个文件作为一章（推荐）
- 合并所有文件后按"第X章"切分
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QRadioButton,
    QButtonGroup,
    QDialogButtonBox,
    QFrame,
)
from PySide6.QtCore import Qt

from ui.theme_style import qss_alpha, apply_dialog_theme


class ImportFolderDialog(QDialog):
    """从文件夹导入前的选项对话框。"""

    def __init__(self, folder: str, file_count: int, parent=None):
        super().__init__(parent)
        self._folder = folder
        self._file_count = file_count
        self._theme = self._resolve_theme()

        self.setWindowTitle("从文件夹导入")
        self.setMinimumWidth(540)
        self._build_ui()
        apply_dialog_theme(self, theme=self._theme if self._theme else None)
        self._apply_extra_theme()

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

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        title = QLabel("从文件夹导入")
        title.setObjectName("ifTitle")
        root.addWidget(title)

        # 检测结果卡片
        detected = QFrame()
        detected.setObjectName("ifBox")
        dl = QVBoxLayout(detected)
        dl.setContentsMargins(14, 12, 14, 12)
        dl.setSpacing(4)
        folder_lbl = QLabel(f"文件夹：{self._folder}")
        folder_lbl.setObjectName("ifFolder")
        folder_lbl.setWordWrap(True)
        dl.addWidget(folder_lbl)
        count_lbl = QLabel(
            f"检测到 <b>{self._file_count}</b> 个可导入文件" f"（.txt / .md / .docx）"
        )
        count_lbl.setObjectName("ifCount")
        count_lbl.setTextFormat(Qt.RichText)
        dl.addWidget(count_lbl)
        root.addWidget(detected)

        # 导入方式
        mode_lbl = QLabel("导入方式")
        mode_lbl.setObjectName("ifFieldLabel")
        root.addWidget(mode_lbl)

        mode_box = QFrame()
        mode_box.setObjectName("ifRadioBox")
        ml = QVBoxLayout(mode_box)
        ml.setContentsMargins(14, 10, 14, 10)
        ml.setSpacing(8)

        self.mode_group = QButtonGroup(self)
        self.rb_per_file = QRadioButton("每个文件作为一章（推荐）")
        self.rb_per_file.setChecked(True)
        self.rb_per_file.setToolTip("适合'分章导出'的文件，或已按章切分好的文件夹")
        hint1 = QLabel(
            "文件名 <code>001_第一章 山雨欲来.txt</code> → "
            "标题提取为「第一章 山雨欲来」"
        )
        hint1.setObjectName("ifHint")
        hint1.setTextFormat(Qt.RichText)
        hint1.setWordWrap(True)

        self.rb_merge = QRadioButton("合并所有文件后按「第X章」切分")
        self.rb_merge.setToolTip("适合老稿：文件里含多章、需按章号重新切割")
        hint2 = QLabel("所有文件按文件名自然排序后拼接，再用「第X章」正则切分")
        hint2.setObjectName("ifHint")
        hint2.setWordWrap(True)

        self.mode_group.addButton(self.rb_per_file)
        self.mode_group.addButton(self.rb_merge)

        ml.addWidget(self.rb_per_file)
        ml.addWidget(hint1)
        ml.addSpacing(4)
        ml.addWidget(self.rb_merge)
        ml.addWidget(hint2)
        root.addWidget(mode_box)

        # 卷名处理
        vol_lbl = QLabel("卷名处理")
        vol_lbl.setObjectName("ifFieldLabel")
        root.addWidget(vol_lbl)

        vol_box = QFrame()
        vol_box.setObjectName("ifRadioBox")
        vl = QVBoxLayout(vol_box)
        vl.setContentsMargins(14, 10, 14, 10)
        vl.setSpacing(6)

        self.vol_group = QButtonGroup(self)
        self.rb_vol_yes = QRadioButton("识别卷名，按卷章分层")
        self.rb_vol_yes.setChecked(True)
        self.rb_vol_no = QRadioButton("忽略卷名，全部归到「正文」")
        self.vol_group.addButton(self.rb_vol_yes)
        self.vol_group.addButton(self.rb_vol_no)
        vl.addWidget(self.rb_vol_yes)
        vl.addWidget(self.rb_vol_no)
        root.addWidget(vol_box)

        hint3 = QLabel(
            "文件里的「第X卷 卷名」行会被识别为卷；" "不识别时该行作为正文保留。"
        )
        hint3.setObjectName("ifHint")
        hint3.setWordWrap(True)
        root.addWidget(hint3)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("开始导入")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _apply_extra_theme(self):
        t = self._theme or {}
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")

        self.setStyleSheet(
            f"QLabel#ifTitle {{ color: {text}; font-size: 15px;"
            f" font-weight: 700; }}"
            f"QLabel#ifFolder {{ color: {text}; font-size: 12px; }}"
            f"QLabel#ifCount {{ color: {text}; font-size: 12.5px; }}"
            f"QLabel#ifFieldLabel {{ color: {text}; font-size: 13px;"
            f" font-weight: 600; padding-top: 4px; }}"
            f"QLabel#ifHint {{ color: {muted}; font-size: 11px;"
            f" line-height: 1.7; padding-left: 24px; }}"
            f"QFrame#ifBox {{"
            f" background: {qss_alpha(accent, 0x10)};"
            f" border: 1px solid {qss_alpha(accent, 0x33)};"
            f" border-radius: 8px; }}"
            f"QFrame#ifRadioBox {{"
            f" background: {qss_alpha(accent, 0x08)};"
            f" border-radius: 8px; }}"
            f"QRadioButton {{ color: {text}; font-size: 13px;"
            f" spacing: 8px; }}"
        )

    def get_mode(self) -> str:
        return "per_file" if self.rb_per_file.isChecked() else "merge_split"

    def use_volumes(self) -> bool:
        return self.rb_vol_yes.isChecked()
