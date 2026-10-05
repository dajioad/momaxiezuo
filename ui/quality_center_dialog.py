# ui/quality_center_dialog.py
"""墨码写作 - 质量中心。

汇总当前作品里所有已落盘的质量分析报告（墨灵试读 / 追读体检），
支持回看与删除。报告由 utils/quality_reports 持久化在作品目录
``quality_reports/`` 下，一报告一 JSON 文件。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ui.theme_style import apply_dialog_theme

KIND_LABELS = {
    "beta_reading": "墨灵试读",
    "readability": "追读体检",
}


class QualityCenterDialog(QDialog):
    def __init__(self, work, parent=None, theme: dict = None,
                 on_jump=None, current_chapter_id="", on_locate=None):
        super().__init__(parent)
        self._work = work
        self._theme = theme or {}
        self._on_jump = on_jump
        self._current_cid = str(current_chapter_id or "")
        self._on_locate = on_locate
        self._records = []

        self.setWindowTitle("质量中心")
        self.resize(680, 520)
        self._build()
        apply_dialog_theme(self, theme=theme)
        self._apply_style()
        self.refresh()

    # ---------------- UI ----------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel("质量中心")
        title.setObjectName("qcTitle")
        root.addWidget(title)

        sub = QLabel(
            f"《{getattr(self._work, 'title', '') or '未命名作品'}》"
            "的墨灵试读与追读体检历史。报告自动保存在作品目录，"
            "随作品备份一起带走。")
        sub.setWordWrap(True)
        sub.setObjectName("qcSub")
        root.addWidget(sub)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["类型", "报告", "时间", "结果"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setHighlightSections(False)
        self.table.doubleClicked.connect(self._view)
        root.addWidget(self.table, 1)

        self.empty_lab = QLabel("还没有报告。跑完一次「墨灵试读」或「追读体检」后会自动出现在这里。")
        self.empty_lab.setAlignment(Qt.AlignCenter)
        self.empty_lab.setObjectName("qcEmpty")
        root.addWidget(self.empty_lab)
        self.empty_lab.hide()

        row = QHBoxLayout()
        self.del_btn = QPushButton("删除")
        self.del_btn.clicked.connect(self._delete)
        self.view_btn = QPushButton("查看报告")
        self.view_btn.setProperty("variant", "primary")
        self.view_btn.clicked.connect(self._view)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        row.addWidget(self.view_btn)
        row.addWidget(close_btn)
        root.addLayout(row)

    def refresh(self):
        from utils import quality_reports as qr
        self._records = qr.list_reports(self._work.root_dir)
        self.table.setRowCount(0)
        for rec in self._records:
            r = self.table.rowCount()
            self.table.insertRow(r)
            kind = rec.get("kind", "")
            kind_item = QTableWidgetItem(KIND_LABELS.get(kind, kind or "报告"))
            kind_item.setData(Qt.UserRole, rec.get("id"))
            self.table.setItem(r, 0, kind_item)
            self.table.setItem(r, 1, QTableWidgetItem(rec.get("title", "")))
            self.table.setItem(r, 2, QTableWidgetItem(rec.get("created_at", "")))
            self.table.setItem(r, 3, QTableWidgetItem(self._score_text(rec)))
        has = bool(self._records)
        self.table.setVisible(has)
        self.empty_lab.setVisible(not has)
        self.view_btn.setEnabled(has)
        self.del_btn.setEnabled(has)

    @staticmethod
    def _score_text(rec: dict) -> str:
        kind = rec.get("kind")
        score = rec.get("score")
        if kind == "beta_reading":
            if isinstance(score, (int, float)):
                n = max(0, min(5, int(score)))
                return f"追读欲 {'★' * n}{'☆' * (5 - n)}"
            return "—"
        if kind == "readability":
            level = rec.get("level") or ""
            return f"{score} 分 · {level}" if score is not None else level or "—"
        return "—"

    # ---------------- 操作 ----------------

    def _selected_record(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def _view(self, *args):
        rec = self._selected_record()
        if not rec:
            return
        from utils import quality_reports as qr
        full = qr.load_report(self._work.root_dir, rec["id"]) or rec
        kind = full.get("kind")
        theme = self._theme
        if kind == qr.KIND_BETA:
            self._view_beta(full, theme)
        elif kind == qr.KIND_READABILITY:
            self._view_readability(full, theme)

    def _view_beta(self, rec: dict, theme: dict):
        from ui.beta_reading_dialog import BetaReportDialog
        data = rec.get("data") or {}
        dlg = BetaReportDialog(
            data.get("entries") or [], data.get("summary") or {},
            self, theme=theme,
            on_jump=(lambda cid: self._on_jump(cid)) if callable(self._on_jump)
            else None,
            saved=True)
        dlg.exec()

    def _view_readability(self, rec: dict, theme: dict):
        from utils.readability import Report
        from ui.readability_dialog import ReadabilityDialog
        report = qr_report_from_record(rec)
        if report is None:
            QMessageBox.warning(self, "质量中心", "这份体检报告内容已损坏，无法打开。")
            return
        dlg = ReadabilityDialog(report, self)
        ai_data = (rec.get("data") or {}).get("ai")
        # 先回填已保存的 AI 结果（show_ai_result 内部会启用按钮）
        if ai_data:
            dlg.show_ai_result(ai_data)
        # 历史报告统一禁用 AI 按钮：回填后再禁用，避免被 show_ai_result 覆盖
        btn = getattr(dlg, "_ai_button", None)
        if btn is not None:
            btn.setEnabled(False)
            if ai_data:
                btn.setText(" 已保存 AI 深度结果")
                btn.setToolTip("下方展示的是本次体检时保存的 AI 结果")
            else:
                btn.setToolTip("历史报告不支持重新分析，请在正文中重新运行追读体检")
        # 定位仅在当前打开章节与报告章节一致时接通，避免定位到错误章节
        if (self._current_cid and rec.get("chapter_id")
                and str(rec["chapter_id"]) == self._current_cid
                and callable(self._on_locate)):
            dlg.goto_position.connect(self._on_locate)
        dlg.exec()

    def _delete(self):
        rec = self._selected_record()
        if not rec:
            return
        reply = QMessageBox.question(
            self, "删除报告",
            f"确定删除「{rec.get('title', '')}」吗？\n"
            "只删除这份分析记录，不影响正文与章节。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        from utils import quality_reports as qr
        if qr.delete_report(self._work.root_dir, rec["id"]):
            self.refresh()

    # ---------------- 样式（克制国风：细边、小圆角、无渐变无发光） ----------------

    def _apply_style(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        panel = t.get("editor_bg", "#fcfaf4")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#928a76")
        accent = t.get("accent", "#b08a4e")

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"#qcTitle {{ color: {text_c}; font-size: 18px; font-weight: 700;"
            f" background: transparent; }}"
            f"#qcSub, #qcEmpty {{ color: {muted}; font-size: 12.5px;"
            f" background: transparent; }}"
            f"QTableWidget {{ background: {panel}; border: 1px solid {muted};"
            f" border-radius: 9px; gridline-color: transparent;"
            f" color: {text_c}; font-size: 12.5px;"
            f" alternate-background-color: {bg}; }}"
            f"QTableWidget::item {{ padding: 6px 8px; border: none; }}"
            f"QTableWidget::item:selected {{ background: {accent};"
            f" color: #fff; }}"
            f"QHeaderView::section {{ background: {bg}; color: {muted};"
            f" border: none; border-bottom: 1px solid {muted};"
            f" padding: 6px 8px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton {{ color: {text_c}; background: transparent;"
            f" border: 1px solid {muted}; border-radius: 6px;"
            f" padding: 6px 16px; font-size: 12.5px; }}"
            f"QPushButton:hover {{ color: {accent}; border-color: {accent}; }}"
            f"QPushButton:disabled {{ color: {muted}; }}"
            f"QPushButton[variant=\"primary\"] {{ background: {accent};"
            f" color: #fff; border: 1px solid {accent}; }}"
            f"QPushButton[variant=\"primary\"]:hover {{ color: #fff;"
            f" border-color: {accent}; }}"
        )


def qr_report_from_record(rec: dict):
    """从记录恢复追读体检 Report；失败返回 None。"""
    from utils import quality_reports as qr
    data = rec.get("data") or {}
    return qr.report_from_dict(data.get("report") or {})
