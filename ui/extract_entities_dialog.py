# ui/extract_entities_dialog.py
"""墨码写作 - AI 提取新词条对话框

AI 扫一章，输出本章出现的新角色、新地名、新技能。
用户勾选后，一键加入：
- 角色 → characters.json（人物卡）
- 地名 → wordbook.json「地名势力」分类
- 技能 → wordbook.json「技能名称」分类

交互：
- 2 列布局（名称 / AI 备注），复选框挂在名称列，能正常显示
- 自定义 mousePressEvent：点名称或备注任意位置都切换勾选
- 顶层分组不可勾选
- "全选/取消"按钮一键切换所有子项
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QMessageBox,
    QHeaderView,
)
from PySide6.QtCore import Qt

from ui.theme_style import apply_dialog_theme

# 复选框大约占行开头多少像素（缩进后）
_CHECKBOX_HIT_WIDTH = 26


class _EntityTree(QTreeWidget):
    """自定义树：支持点名称或备注也能切换复选框。

    Qt 默认只有点复选框本身才切换；用户点名称文字没反应，体验差。
    这里重写 mousePressEvent：
    - 点在复选框区域（行开头约 26px）→ 交给 Qt 处理
    - 点在其他区域（名称/备注文字）→ 手动切换勾选
    """

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item is None or item.parent() is None:
            # 空白处或顶层分组：交给 Qt
            super().mousePressEvent(event)
            return

        rect = self.visualItemRect(item)
        x_offset = event.pos().x() - rect.x()
        if x_offset < _CHECKBOX_HIT_WIDTH:
            # 落在复选框上，Qt 自己处理
            super().mousePressEvent(event)
            return

        # 落在名称/备注文字上 → 手动切换
        cur = item.checkState(0)
        item.setCheckState(0, Qt.Unchecked if cur == Qt.Checked else Qt.Checked)
        self.setCurrentItem(item)
        event.accept()


class ExtractEntitiesDialog(QDialog):
    """展示 AI 提取的实体，让用户勾选。"""

    def __init__(self, entities: dict, parent=None, theme: dict = None):
        """entities: {
            "characters": [{"name": str, "note": str}, ...],
            "locations": [{"name": str, "note": str}, ...],
            "skills":    [{"name": str, "note": str}, ...],
        }
        """
        super().__init__(parent)
        self._entities = entities or {}
        self._theme = theme or {}
        self.setWindowTitle("AI 提取的新词条")
        self.setMinimumSize(640, 560)
        self._build_ui()
        apply_dialog_theme(self, theme=self._theme if self._theme else None)
        self._populate()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("AI 提取结果")
        title.setObjectName("exTitle")
        root.addWidget(title)

        hint = QLabel(
            "勾选你想加入的条目：角色会加入人物卡，"
            "地名加入高频词「地名势力」，技能加入高频词「技能名称」。\n"
            "提示：点复选框或名称/备注任意位置都能切换。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("exHint")
        root.addWidget(hint)

        self.tree = _EntityTree()
        self.tree.setHeaderLabels(["名称", "AI 备注"])
        self.tree.setColumnCount(2)
        self.tree.setColumnWidth(0, 220)
        # 名称列固定 220，备注列随窗口伸缩，避免右侧大片空白
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.setIndentation(16)
        self.tree.setAlternatingRowColors(True)
        root.addWidget(self.tree, 1)

        btn_row = QHBoxLayout()
        toggle_btn = QPushButton("全选/取消")
        toggle_btn.clicked.connect(self._toggle_all)
        btn_row.addWidget(toggle_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.apply_btn = QPushButton("加入选中项")
        self.apply_btn.setProperty("variant", "primary")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self.apply_btn)
        root.addLayout(btn_row)

    def _populate(self):
        cat_names = {
            "characters": "角色",
            "locations": "地名",
            "skills": "技能",
        }
        for key in ("characters", "locations", "skills"):
            items = self._entities.get(key) or []
            if not items:
                continue
            # 顶层分组：只显示类别名，不可勾选
            group = QTreeWidgetItem([cat_names[key], ""])
            group.setFlags(Qt.ItemIsEnabled)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            group.setExpanded(True)
            for it in items:
                name = it.get("name", "").strip()
                if not name:
                    continue
                # 2 列：第 0 列名称（checkbox 也挂这里，保证能显示），
                # 第 1 列备注
                note = it.get("note", "")
                child = QTreeWidgetItem([name, note])
                child.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                child.setCheckState(0, Qt.Checked)
                child.setData(0, Qt.UserRole, (key, name))
                # 长名称/长备注靠悬停看全文
                child.setToolTip(0, name)
                if note:
                    child.setToolTip(1, note)
                group.addChild(child)

    def _toggle_all(self):
        states = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                states.append(top.child(j).checkState(0) == Qt.Checked)
        target = not all(states) if states else True
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.Checked if target else Qt.Unchecked)

    def get_selected(self) -> dict:
        """返回 {'characters': [name,...], 'locations': [...], 'skills': [...]}"""
        out = {"characters": [], "locations": [], "skills": []}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0).value != Qt.Checked.value:
                    continue
                data = child.data(0, Qt.UserRole)
                if data:
                    out[data[0]].append(data[1])
        return out

    def _on_apply(self):
        sel = self.get_selected()
        total = sum(len(v) for v in sel.values())
        if total == 0:
            QMessageBox.information(self, "提示", "没有勾选任何条目。")
            return
        self.accept()
