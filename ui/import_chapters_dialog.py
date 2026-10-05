# ui/import_chapters_dialog.py
"""墨码写作 - 导入章节前的结构确认对话框

当导入的文件里检测到"卷"结构时（第X卷/部/篇/集），
弹此对话框让用户确认导入方式。
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


class ImportChaptersDialog(QDialog):
    """导入前显示检测结果 + 导入方式选择。"""

    def __init__(self, file_name: str, structure: dict, parent=None):
        """
        structure: Work.detect_chapter_structure() 的返回
        """
        super().__init__(parent)
        self._structure = structure or {}
        self._file_name = file_name or ""
        self._theme = self._resolve_theme()

        self.setWindowTitle("导入章节")
        self.setMinimumWidth(520)
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

        # 标题
        title = QLabel("导入章节")
        title.setObjectName("icTitle")
        root.addWidget(title)

        # 检测结果卡片
        detected = QFrame()
        detected.setObjectName("icDetectedBox")
        dl = QVBoxLayout(detected)
        dl.setContentsMargins(14, 12, 14, 12)
        dl.setSpacing(4)

        file_name = getattr(self, "_file_name", "") or "(文件)"
        name_lbl = QLabel(f"文件：{file_name}")
        name_lbl.setObjectName("icFileName")
        dl.addWidget(name_lbl)

        volumes = self._structure.get("volumes", []) or []
        ch_count = self._structure.get("chapter_count", 0)

        info_lbl = QLabel(
            f"检测到结构：<br>"
            f"&nbsp;&nbsp;· <b>{len(volumes)} 个卷</b><br>"
            f"&nbsp;&nbsp;· <b>{ch_count} 个章节</b>"
        )
        info_lbl.setObjectName("icInfo")
        info_lbl.setTextFormat(Qt.RichText)
        dl.addWidget(info_lbl)

        if volumes:
            vol_preview = "、".join(volumes[:3])
            if len(volumes) > 3:
                vol_preview += f" 等 {len(volumes)} 卷"
            vol_lbl = QLabel(f"卷名：{vol_preview}")
            vol_lbl.setObjectName("icVolPreview")
            vol_lbl.setWordWrap(True)
            dl.addWidget(vol_lbl)

        root.addWidget(detected)

        # 导入方式选择
        mode_label = QLabel("导入方式")
        mode_label.setObjectName("icFieldLabel")
        root.addWidget(mode_label)

        mode_box = QFrame()
        mode_box.setObjectName("icRadioGroup")
        ml = QVBoxLayout(mode_box)
        ml.setContentsMargins(14, 10, 14, 10)
        ml.setSpacing(6)

        self.mode_group = QButtonGroup(self)
        self.rb_vol_aware = QRadioButton("按卷和章分层导入（推荐）")
        self.rb_vol_aware.setChecked(True)
        self.rb_vol_aware.setToolTip("卷名作为卷，章节归到对应卷下。还原文件里的结构。")
        self.rb_flat = QRadioButton("只按章导入，卷名忽略")
        self.rb_flat.setToolTip(
            "所有章节都归到默认卷「正文」下，卷名行被当作正文段落。"
        )
        self.mode_group.addButton(self.rb_vol_aware)
        self.mode_group.addButton(self.rb_flat)
        ml.addWidget(self.rb_vol_aware)
        ml.addWidget(self.rb_flat)
        root.addWidget(mode_box)

        # 提示
        hint = QLabel(
            "「按卷和章分层导入」会在作品里创建对应的卷，"
            "并把每章放到所属卷下。卷名行本身不写入正文。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("icHint")
        root.addWidget(hint)

        # 按钮
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
        editor_bg = t.get("editor_bg", "#fcfaf4")

        self.setStyleSheet(
            f"QLabel#icTitle {{ color: {text}; font-size: 15px;"
            f" font-weight: 700; }}"
            f"QLabel#icFileName {{ color: {text}; font-size: 12.5px;"
            f" font-weight: 600; }}"
            f"QLabel#icInfo {{ color: {text}; font-size: 12.5px;"
            f" line-height: 1.7; }}"
            f"QLabel#icVolPreview {{ color: {muted}; font-size: 11.5px;"
            f" margin-top: 4px; }}"
            f"QLabel#icFieldLabel {{ color: {text}; font-size: 13px;"
            f" font-weight: 600; padding-top: 4px; }}"
            f"QLabel#icHint {{ color: {muted}; font-size: 11.5px;"
            f" line-height: 1.7; }}"
            f"QFrame#icDetectedBox {{"
            f" background: {qss_alpha(accent, 0x10)};"
            f" border: 1px solid {qss_alpha(accent, 0x33)};"
            f" border-radius: 8px; }}"
            f"QFrame#icRadioGroup {{"
            f" background: {qss_alpha(accent, 0x08)};"
            f" border-radius: 8px; }}"
            f"QRadioButton {{ color: {text}; font-size: 13px;"
            f" spacing: 8px; }}"
        )

    def use_volumes(self) -> bool:
        """返回用户是否选择"按卷章分层"。"""
        return self.rb_vol_aware.isChecked()
