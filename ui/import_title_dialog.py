# ui/import_title_dialog.py
"""墨码写作 - 章节标题整理对话框

三个入口共用同一个对话框：
1. 导入完成 → 自动弹窗（只整理本次导入的）
2. 工具栏「整理 ▾」→「整理章节标题…」（整理全部）
3. 章节树右键 →「整理章节标题…」（只整理选中的）

导入 TXT / Markdown / Word 后，章节标题常常不规范：
    "第1章 山雨欲来"  /  "第 1 章  山雨欲来"  /  "第1章-山雨欲来"
    "第1章"（无标题）  /  "第1章 未命名"

本对话框提供一键整理 + 预览，用户可勾选应用哪些规则。
只改标题（`book.json` 的 title），不动正文。

默认规则：
- 阿拉伯数字 → 中文数字（网文习惯：第1章 → 第一章）
- 统一格式（去多余空格/符号）
- 无标题补「（未命名）」
"""

import re

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialogButtonBox,
    QMessageBox,
    QAbstractItemView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
import qtawesome as qta

from ui.theme_style import qss_alpha, accent_button_qss, apply_dialog_theme

# 章节号模式：第X章 / 第X回 / 第X卷 等
_NUM_RE = re.compile(r"^\s*第\s*([0-9零一二三四五六七八九十百千万]+)\s*([章回卷部篇])")

# 中文数字 → 阿拉伯（简版，够用于章号）
_CN_DIGIT = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}

# 空标题的占位词（统一转为"（未命名）"）
_EMPTY_MARKERS = (
    "无标题",
    "未命名",
    "未命名章节",
    "（无标题）",
    "(无标题)",
    "（未命名）",
    "(未命名)",
    "-",
    "—",
    "－",
)

# 标题尾部/头部要清理的符号
_TAIL_STRIP_CHARS = " \t　-—－·•:：。．.,，、_"

# 章节号后面的单位字符（章/回/卷…）
_UNIT_CHARS = "章回卷部篇"


def _cn_to_int(s: str):
    """中文数字 → 整数。失败返回 None。"""
    if not s:
        return None
    if "万" in s:
        idx = s.index("万")
        high = _cn_to_int(s[:idx]) if s[:idx] else 1
        low = _cn_to_int(s[idx + 1 :]) if s[idx + 1 :] else 0
        if high is None or low is None:
            return None
        return high * 10000 + low
    total = 0
    current = 0
    has_digit = False
    for ch in s:
        if ch in _CN_DIGIT:
            current = _CN_DIGIT[ch]
            has_digit = True
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if not has_digit:
                current = 1
            total += current * unit
            current = 0
            has_digit = False
        else:
            return None
    total += current
    return total


def _to_cn(n: int) -> str:
    """阿拉伯 → 中文数字。

    支持 1-9999。超范围返回 str(n) 兜底。
    网文场景下章号几乎不会超过 9999，覆盖足够。
    """
    if n <= 0:
        return "零"
    if n < 10:
        return "零一二三四五六七八九"[n]
    if n < 20:
        return "十" + ("零一二三四五六七八九"[n - 10] if n > 10 else "")
    if n < 100:
        tens, ones = divmod(n, 10)
        return (
            "零一二三四五六七八九"[tens]
            + "十"
            + ("零一二三四五六七八九"[ones] if ones else "")
        )
    if n < 1000:
        h, rest = divmod(n, 100)
        s = "零一二三四五六七八九"[h] + "百"
        if rest == 0:
            return s
        if rest < 10:
            return s + "零" + "零一二三四五六七八九"[rest]
        return s + _to_cn(rest)
    if n < 10000:
        t, rest = divmod(n, 1000)
        s = "零一二三四五六七八九"[t] + "千"
        if rest == 0:
            return s
        if rest < 100:
            return s + "零" + _to_cn(rest)
        return s + _to_cn(rest)
    return str(n)


class ImportTitleDialog(QDialog):
    """标题整理对话框。

    用法：
        dlg = ImportTitleDialog(chapters, parent=panel)
        if dlg.exec() == QDialog.Accepted:
            renames = dlg.get_renames()   # {cid: new_title}
            work.rename_chapters_batch(renames)

    或者用模块函数（推荐）：
        from ui.import_title_dialog import open_organize_dialog
        renames = open_organize_dialog(chapters, parent=panel)
    """

    # 规则 ID 与默认状态
    RULE_CN_NUM = "cn_num"  # 章节号用中文数字（第1章 → 第一章）
    RULE_FORMAT = "format"  # 统一「第 N 章 标题」格式
    RULE_EMPTY = "empty"  # 无标题补「（未命名）」

    def __init__(
        self,
        chapters: list,
        parent=None,
        scope_label: str = "导入后整理标题",
        scope_hint: str = None,
    ):
        """
        chapters: [{"id": ..., "title": ...}, ...]
        scope_label: 窗口标题
        scope_hint: 顶部提示文案；None 时按 scope_label 自动推断
        """
        super().__init__(parent)
        self._chapters = list(chapters or [])
        self._theme = self._resolve_theme()
        self._renames = {}  # {cid: new_title}
        self._scope_label = scope_label
        self._scope_hint = scope_hint

        self.setWindowTitle(scope_label)
        self.setMinimumSize(720, 560)
        self._build_ui()
        apply_dialog_theme(self, theme=self._theme if self._theme else None)
        self._apply_extra_theme()
        self._recompute()

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

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        # 顶部提示（文案随 scope 变化）
        if self._scope_hint:
            hint_text = self._scope_hint
        elif self._scope_label.startswith("导入"):
            hint_text = (
                f"导入完成，本次新增 {len(self._chapters)} 章。\n"
                "下面是标题整理预览。勾选想应用的规则，点「应用」写回作品。"
            )
        else:
            hint_text = (
                f"本次处理 {len(self._chapters)} 章。\n"
                "下面是标题整理预览。勾选想应用的规则，点「应用」写回作品。"
            )
        tip = QLabel(hint_text)
        tip.setWordWrap(True)
        tip.setObjectName("itTip")
        root.addWidget(tip)

        # 规则复选框（横向排布）
        rule_row = QHBoxLayout()
        rule_row.setSpacing(14)
        self.chk_cn_num = QCheckBox("章节号用中文数字（第1章 → 第一章）")
        self.chk_cn_num.setChecked(True)
        self.chk_format = QCheckBox("统一格式（去多余空格/符号）")
        self.chk_format.setChecked(True)
        self.chk_empty = QCheckBox("无标题补「（未命名）」")
        self.chk_empty.setChecked(True)
        for cb in (self.chk_cn_num, self.chk_format, self.chk_empty):
            cb.stateChanged.connect(self._recompute)
            rule_row.addWidget(cb)
        rule_row.addStretch()
        root.addLayout(rule_row)

        # 预览表格
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["章节号", "原标题", "整理后"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 70)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setHighlightSections(False)
        root.addWidget(self.table, 1)

        # 底部统计
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("itSummary")
        root.addWidget(self.summary_label)

        # 按钮行
        btn_row = QHBoxLayout()
        self.toggle_all_btn = QPushButton("全选/取消")
        self.toggle_all_btn.setToolTip("一键切换所有规则的勾选状态")
        self.toggle_all_btn.clicked.connect(self._toggle_all_rules)
        btn_row.addWidget(self.toggle_all_btn)
        btn_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("应用")
        btns.button(QDialogButtonBox.Cancel).setText("跳过（保持原名）")
        btns.accepted.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        btn_row.addWidget(btns)
        root.addLayout(btn_row)

    def _apply_extra_theme(self):
        """局部样式：表格边框 + 高亮色。"""
        t = self._theme or {}
        bg = t.get("bg", "#f7f2e8")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")

        self.setStyleSheet(
            f"QLabel#itTip {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#itSummary {{ color: {accent}; font-size: 13px;"
            f" font-weight: 600; padding: 4px 0; }}"
            f"QCheckBox {{ color: {text}; font-size: 12.5px; }}"
            f"QTableWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; gridline-color: transparent;"
            f" alternate-background-color: {qss_alpha(muted, 0x08)};"
            f" outline: none; }}"
            f"QTableWidget::item {{ padding: 8px 10px; }}"
            f"QTableWidget::item:selected {{"
            f" background: {qss_alpha(accent, 0x22)}; color: {text}; }}"
            f"QHeaderView::section {{ background: transparent;"
            f" color: {muted}; border: none;"
            f" border-bottom: 2px solid {qss_alpha(muted, 0x33)};"
            f" padding: 9px 10px; font-weight: 600; font-size: 11px; }}"
        )

    # ---------------- 逻辑 ----------------
    def _toggle_all_rules(self):
        """全选/取消：三个规则一起切。"""
        any_off = not (
            self.chk_cn_num.isChecked()
            and self.chk_format.isChecked()
            and self.chk_empty.isChecked()
        )
        target = any_off
        for cb in (self.chk_cn_num, self.chk_format, self.chk_empty):
            cb.blockSignals(True)
            cb.setChecked(target)
            cb.blockSignals(False)
        self._recompute()

    @staticmethod
    def _normalize_title(
        raw: str, index: int, use_cn_num: bool, use_format: bool, use_empty: bool
    ) -> str:
        """根据启用的规则整理一个标题。

        index: 章节在列表中的序号（0-based），用于推断章节号
        """
        raw = (raw or "").strip()

        # 1) 提取章节号 + 单位 + 剩余标题
        m = _NUM_RE.match(raw)
        num = None
        unit = "章"
        rest = raw

        if m:
            num_raw = m.group(1)
            unit = m.group(2)
            if num_raw.isdigit():
                num = int(num_raw)
            else:
                num = _cn_to_int(num_raw)
            rest = raw[m.end() :]

        # 2) 处理剩余标题
        rest = rest.strip(_TAIL_STRIP_CHARS).strip()
        # 去掉空的占位词
        if rest in _EMPTY_MARKERS:
            rest = ""

        # 3) 如果没有章节号，按位置补一个
        if num is None:
            m2 = re.search(r"(\d+)", raw)
            if m2:
                num = int(m2.group(1))
            else:
                num = index + 1

        # 4) 组合
        # 单位保持原样（章/回/卷…），用户导入什么就保持
        # use_cn_num=True：章节号统一成中文（默认，网文习惯）
        # use_cn_num=False：章节号统一成阿拉伯
        if use_cn_num:
            num_str = _to_cn(num)
        else:
            num_str = str(num)

        if not rest and use_empty:
            rest = "（未命名）"

        if use_format:
            # 用单空格分隔，无标题时直接拼「第N章（未命名）」
            if rest:
                return f"第{num_str}{unit} {rest}"
            return f"第{num_str}{unit}"

        # 不启用 format 规则时：只做数字替换，保留原样
        if use_cn_num and m:
            return f"第{num_str}{unit}{raw[m.end():]}"
        return raw

    def _recompute(self):
        """根据当前勾选状态重算预览 + renames。"""
        use_cn_num = self.chk_cn_num.isChecked()
        use_format = self.chk_format.isChecked()
        use_empty = self.chk_empty.isChecked()

        self._renames = {}
        self.table.setRowCount(len(self._chapters))

        accent_qc = QColor(self._theme.get("accent", "#b08a4e"))
        text_qc = QColor(self._theme.get("text", "#3b342a"))
        muted_qc = QColor(self._theme.get("muted", "#9a8e76"))
        changed_count = 0

        for i, ch in enumerate(self._chapters):
            cid = ch.get("id", "")
            orig = ch.get("title", "") or ""

            new_title = self._normalize_title(
                orig, i, use_cn_num, use_format, use_empty
            )

            if new_title and new_title != orig:
                self._renames[cid] = new_title
                changed_count += 1

            # 章节号列
            id_item = QTableWidgetItem(str(cid))
            id_item.setForeground(muted_qc)
            id_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 0, id_item)

            # 原标题列
            o_item = QTableWidgetItem(orig or "（空）")
            if not orig:
                o_item.setForeground(muted_qc)
            else:
                o_item.setForeground(text_qc)
            self.table.setItem(i, 1, o_item)

            # 整理后列
            n_item = QTableWidgetItem(new_title)
            if new_title == orig:
                # 未变化：暗灰显示
                n_item.setForeground(muted_qc)
                f = n_item.font()
                f.setItalic(True)
                n_item.setFont(f)
            else:
                # 有变化：金色加粗
                n_item.setForeground(accent_qc)
                f = n_item.font()
                f.setBold(True)
                n_item.setFont(f)
            self.table.setItem(i, 2, n_item)

        total = len(self._chapters)
        self.summary_label.setText(
            f"共 {total} 章 · 将修改 {changed_count} 章 · "
            f"保持不变 {total - changed_count} 章"
        )

    def _on_apply(self):
        if not self._renames:
            # 无需改动，直接接受
            self.accept()
            return
        ret = QMessageBox.question(
            self,
            "确认应用",
            f"将修改 {len(self._renames)} 个章节标题，是否继续？\n\n"
            "（正文内容不受影响，可随时手动改回）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret == QMessageBox.Yes:
            self.accept()

    def get_renames(self) -> dict:
        """返回 {chapter_id: new_title}，只含实际变化的。"""
        return dict(self._renames)


def open_organize_dialog(
    chapters, parent=None, scope_label: str = "整理章节标题", scope_hint: str = None
) -> dict:
    """打开整理对话框，返回 {cid: new_title}；用户跳过返回 {}。

    统一入口：导入完成 / 工具栏"整理 ▾" / 章节树右键 都走这里。

    chapters: [{"id": ..., "title": ...}, ...]
    scope_label: 窗口标题（如"整理章节标题（5 章）"）
    scope_hint: 顶部提示文案（None 时按 scope_label 自动推断）
    """
    dlg = ImportTitleDialog(
        chapters, parent=parent, scope_label=scope_label, scope_hint=scope_hint
    )
    if dlg.exec() != QDialog.Accepted:
        return {}
    return dlg.get_renames()
