# ui/relation_extract_dialog.py
"""墨码写作 - 人物关系提取确认对话框

关系来源分两种，都在同一棵树里勾选：
  · source="local"：本地规则零 token 抠出的「白纸黑字」关系（标签「本地」）
  · source="ai"：AI 跨句推断的关系（标签「AI」）
用户可先看本地结果，再点「用 AI 深挖本章」追加 AI 结果，去重后统一写入。
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
from utils.relation_rules import SYMMETRIC_RELS


def _edge_key(rel: str, f: str, to: str):
    """对称关系的反向边视为同一条（A 是 B 的好友 == B 是 A 的好友）。"""
    if rel in SYMMETRIC_RELS:
        return (rel, frozenset((f, to)))
    return (f, rel, to)


class RelationExtractDialog(QDialog):
    """展示提取到的关系（本地规则 + AI），勾选后写入人物卡。"""

    def __init__(
        self, relations: list, char_names: set, parent=None, theme: dict = None
    ):
        super().__init__(parent)
        self._relations = list(relations or [])
        self._char_names = set(char_names or [])
        self._theme = theme or {}
        self._ai_callback = None
        self._seen_keys = {
            _edge_key(r.get("rel", ""), r.get("from", ""), r.get("to", ""))
            for r in self._relations
        }

        self.setWindowTitle("确认人物关系")
        self.setMinimumSize(720, 560)
        self._build_ui()
        apply_dialog_theme(self, theme=self._theme if self._theme else None)
        self._populate()

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("确认人物关系")
        title.setObjectName("reTitle")
        root.addWidget(title)

        hint = QLabel(
            "勾选要写入人物卡的关系，写入后打开「人物关系图」即可看到。\n"
            "「本地」是按称谓句式直接识别的，不花 token；"
            "「AI」是跨句推断的，可能有误，请逐条核对。\n"
            "「from」一方若还没有人物卡，写入时会自动创建空白卡，"
            "可到「人物」面板双击补全。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("reHint")
        root.addWidget(hint)

        self.count_label = QLabel("")
        self.count_label.setObjectName("reCount")
        root.addWidget(self.count_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["关系", "原文引用"])
        self.tree.setColumnWidth(0, 340)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(True)
        root.addWidget(self.tree, 1)

        btn_row = QHBoxLayout()
        toggle_btn = QPushButton("全选/取消")
        toggle_btn.clicked.connect(self._toggle_all)
        btn_row.addWidget(toggle_btn)

        self.ai_btn = QPushButton("用 AI 深挖本章（跨句推断）")
        self.ai_btn.clicked.connect(self._on_ai_deepen)
        self.ai_btn.setVisible(False)
        btn_row.addWidget(self.ai_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.apply_btn = QPushButton("写入选中")
        self.apply_btn.setProperty("variant", "primary")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self.apply_btn)
        root.addLayout(btn_row)

    def set_ai_deepen(self, callback):
        """挂上「AI 深挖」回调：callback() 返回 list[relation] 或 None。

        回调内部自行处理 key/会员门控、等待框、错误提示；
        返回的关系会自动去重后追加进列表。
        """
        self._ai_callback = callback
        self.ai_btn.setVisible(True)

    def _source_chip(self, r: dict) -> str:
        src = r.get("source", "ai")
        hint = r.get("chapter_hint")
        if src == "local":
            return f"本地·{hint}" if hint else "本地"
        if src == "batch":
            return f"AI·{hint}" if hint else "AI·多章"
        return "AI"

    def _populate(self):
        self.tree.clear()
        for r in self._relations:
            f = r["from"]
            to = r["to"]
            rel = r["rel"]
            has_f = f in self._char_names
            has_to = to in self._char_names
            if has_f and has_to:
                tag = "✓ 已有卡"
            elif has_f or has_to:
                tag = "部分新角色"
            else:
                tag = "新角色"

            text0 = (
                f"{f} ——[{rel}]——→ {to}"
                f"    （{self._source_chip(r)} · {tag}）"
            )
            text1 = r.get("evidence", "")
            it = QTreeWidgetItem([text0, text1])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
            it.setData(0, Qt.UserRole, r)
            it.setToolTip(0, text0)
            if text1:
                it.setToolTip(1, text1)
            self.tree.addTopLevelItem(it)

        if not self._relations:
            empty = QTreeWidgetItem(
                ["（本章没有识别到白纸黑字的明确关系，可点下方 AI 深挖）", ""])
            empty.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(empty)

        self._refresh_count()

    def _refresh_count(self):
        n_local = sum(
            1 for r in self._relations if r.get("source") == "local")
        n_ai = len(self._relations) - n_local
        self.count_label.setText(
            f"共 {len(self._relations)} 条"
            f"（本地 {n_local} · AI {n_ai}）")

    def merge_relations(self, new_relations: list) -> int:
        """把新关系按边去重后追加，返回实际新增条数。对称边反向也算重复。"""
        added = 0
        for r in new_relations or []:
            key = _edge_key(r.get("rel", ""), r.get("from", ""),
                            r.get("to", ""))
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            self._relations.append(r)
            added += 1
        self._populate()
        return added

    def _on_ai_deepen(self):
        if not self._ai_callback:
            return
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("AI 深挖中…")
        try:
            new_rels = self._ai_callback() or []
        finally:
            self.ai_btn.setEnabled(True)
            self.ai_btn.setText("再用 AI 深挖一次")
        if new_rels is None:
            return
        added = self.merge_relations(new_rels)
        if added == 0:
            QMessageBox.information(
                self, "AI 深挖", "AI 没有发现本地规则之外的新关系。")
        else:
            self.count_label.setText(
                self.count_label.text() + f"　本次新增 {added} 条")

    def _toggle_all(self):
        states = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                states.append(it.checkState(0) == Qt.Checked)
        target = not all(states) if states else True
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(0, Qt.Checked if target else Qt.Unchecked)

    def get_selected(self) -> list:
        """返回勾选的关系列表（原始 dict）。"""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) != Qt.Checked:
                continue
            data = it.data(0, Qt.UserRole)
            if data:
                out.append(data)
        return out

    def _on_apply(self):
        if not self.get_selected():
            QMessageBox.information(self, "提示", "没有勾选任何关系。")
            return
        self.accept()
