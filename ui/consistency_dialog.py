# ui/consistency_dialog.py
"""墨码写作 - 一致性检查结果对话框

功能：
- 按置信度分组展示疑似同人不同名：
  高置信度（拼音完全相同）—— 极可能是同一人的不同写法
  中置信度（拼音相同但用字不同）—— 需要人工确认
  低置信度（字形相近但拼音不同）—— 可能是巧合
- 每条带匹配类型标签：同音+形近 / 同音 / 形近
- 支持：定位下一个 / 全部替换为角色卡里的正确写法 / 忽略
- 忽略后写入 works/<作品>/consistency_ignored.json，下次不再报告
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal

from ui.theme_style import (
    qss_alpha,
    accent_button_qss,
    SEMANTIC_OK,
    SEMANTIC_WARN,
    SEMANTIC_BAD,
    SEMANTIC_INFO,
)
from utils.consistency import (
    SimilarPair,
    add_ignored,
    remove_ignored,
    clear_ignored,
    load_ignored,
)

# 置信度分组元数据
_CONF_META = {
    "high": {
        "label": "高置信度",
        "hint": "拼音完全相同，极可能是同一人的不同写法",
        "color": SEMANTIC_BAD,
    },
    "medium": {
        "label": "中置信度",
        "hint": "拼音相同但用字不同，需要人工确认",
        "color": SEMANTIC_WARN,
    },
    "low": {
        "label": "低置信度",
        "hint": "字形相近但拼音不同，可能是巧合",
        "color": "#9a8e76",
    },
}

# 匹配类型标签
_MATCH_TYPE_META = {
    "both": {"label": "同音+形近", "color": SEMANTIC_BAD},
    "sound": {"label": "同音", "color": SEMANTIC_WARN},
    "shape": {"label": "形近", "color": "#9a8e76"},
}


class _ConfidenceHeader(QFrame):
    """置信度分组标题栏。"""

    def __init__(self, level: str, count: int, theme: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("consConfHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        meta = _CONF_META.get(level, {})
        color = meta.get("color", "#9a8e76")
        label = meta.get("label", level)
        hint = meta.get("hint", "")
        t = theme or {}
        muted = t.get("muted", "#9a8e76")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        lay.addWidget(dot)

        title = QLabel(label)
        title.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700;"
            f" background: transparent;"
        )
        lay.addWidget(title)

        count_lab = QLabel(f"{count} 组")
        count_lab.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600;"
            f" background: {qss_alpha(color, 0x18)};"
            f" padding: 2px 9px; border-radius: 9px;"
        )
        lay.addWidget(count_lab)

        lay.addStretch()

        hint_lab = QLabel(hint)
        hint_lab.setStyleSheet(
            f"color: {muted}; font-size: 11px; background: transparent;"
        )
        lay.addWidget(hint_lab)

        self.setStyleSheet(
            f"#consConfHeader {{"
            f" background-color: {qss_alpha(color, 0x0F)};"
            f" border-left: 3px solid {color};"
            f" border-radius: 6px; }}"
        )


class _PairCard(QFrame):
    """一组疑似同人的卡片。"""

    goto_requested = Signal(str, int, int)  # chapter_id, start, end
    replace_all_requested = Signal(str, str, list)  # anchor, variant, positions
    ignore_requested = Signal(str, str)  # anchor, variant

    def __init__(self, pair: SimilarPair, theme: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("consCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._pair = pair
        self._theme = theme or {}
        self._goto_index = 0  # 循环定位用

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # ---- 头部：名字 + 匹配类型 tag + 编辑距离 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        self.names_label = QLabel(f"{pair.anchor}  ↔  {pair.variant}")
        self.names_label.setObjectName("consNames")
        head.addWidget(self.names_label)
        head.addStretch()

        match_type = getattr(pair, "match_type", "shape")
        meta = _MATCH_TYPE_META.get(match_type, {})
        if meta:
            tag = QLabel(meta["label"])
            tag.setObjectName(f"consMatch_{match_type}")
            head.addWidget(tag)

        dist_label = QLabel(f"编辑距离 {pair.distance}")
        dist_label.setObjectName("consDist")
        head.addWidget(dist_label)
        lay.addLayout(head)

        # ---- 计数：两个 tag 胶囊（绿=角色卡权威 / 红=正文异常） ----
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 6, 0, 0)
        info_row.setSpacing(6)
        tag_a = QLabel(f"角色卡 {pair.count_anchor} 次")
        tag_a.setObjectName("consTagGreen")
        tag_b = QLabel(f"正文 {pair.count_variant} 次")
        tag_b.setObjectName("consTagRed")
        info_row.addWidget(tag_a)
        info_row.addWidget(tag_b)
        info_row.addStretch()
        lay.addLayout(info_row)

        # ---- 章节归属 ----
        if pair.chapters_anchor or pair.chapters_variant:
            ch_a = "、".join(pair.chapters_anchor[:8]) + (
                f" 等 {len(pair.chapters_anchor)} 章"
                if len(pair.chapters_anchor) > 8
                else ""
            )
            ch_b = "、".join(pair.chapters_variant[:8]) + (
                f" 等 {len(pair.chapters_variant)} 章"
                if len(pair.chapters_variant) > 8
                else ""
            )
            lines = []
            if pair.chapters_anchor:
                lines.append(f"「{pair.anchor}」在章节：{ch_a or '（无）'}")
            if pair.chapters_variant:
                lines.append(f"「{pair.variant}」在章节：{ch_b or '（无）'}")
            ch_label = QLabel("\n".join(lines))
            ch_label.setObjectName("consChapters")
            ch_label.setWordWrap(True)
            lay.addWidget(ch_label)

        # ---- 提示 ----
        hint = QLabel(
            "如确认是同一人 → 点「全部替换」把所有写法统一为角色卡里的名字；"
            "如确实是不同人 → 点「忽略此条」，下次不再报告。"
        )
        hint.setObjectName("consHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # ---- 操作行 ----
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(6)

        self.goto_btn = QPushButton("定位下一个")
        self.goto_btn.setToolTip(
            f"在编辑器里循环跳转到「{pair.variant}」的每一个出现位置"
        )
        self.goto_btn.clicked.connect(self._on_goto)
        action_row.addWidget(self.goto_btn)

        action_row.addStretch()

        self.replace_btn = QPushButton(f"全部替换为「{pair.anchor}」")
        self.replace_btn.setProperty("variant", "primary")
        self.replace_btn.setToolTip(
            f"把全书 {pair.count_variant} 处「{pair.variant}」\n"
            f"全部替换为角色卡里的「{pair.anchor}」（每章替换前自动打快照）"
        )
        self.replace_btn.clicked.connect(self._on_replace)
        action_row.addWidget(self.replace_btn)

        self.ignore_btn = QPushButton("忽略此条")
        self.ignore_btn.setToolTip(
            "不再报告这一组（可在作品目录的 " "consistency_ignored.json 里恢复）"
        )
        self.ignore_btn.clicked.connect(self._on_ignore)
        action_row.addWidget(self.ignore_btn)

        lay.addLayout(action_row)

        self._apply_theme(theme)

    # ---------- 事件 ----------
    def _on_goto(self):
        positions = self._pair.variant_positions
        if not positions:
            return
        idx = self._goto_index % len(positions)
        cid, s, e = positions[idx]
        self.goto_requested.emit(cid, s, e)
        self._goto_index = (idx + 1) % len(positions)

    def _on_replace(self):
        self.replace_all_requested.emit(
            self._pair.anchor, self._pair.variant, list(self._pair.variant_positions)
        )

    def _on_ignore(self):
        self.ignore_requested.emit(self._pair.anchor, self._pair.variant)

    # ---------- 主题 ----------
    def _apply_theme(self, theme):
        t = theme or {}
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        editor_bg = t.get("editor_bg", "#fcfaf4")

        self.setStyleSheet(
            f"#consCard {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px; }}"
            f"#consCard:hover {{ border: 1px solid {qss_alpha(accent, 0x88)}; }}"
            f"QLabel#consNames {{ color: {accent}; font-size: 15px;"
            f" font-weight: 700; letter-spacing: 0.5px; }}"
            f"QLabel#consDist {{ color: {accent}; font-size: 11px;"
            f" font-weight: 600;"
            f" background: {qss_alpha(accent, 0x14)};"
            f" padding: 2px 10px; border-radius: 10px; }}"
            # 匹配类型 tag
            f"QLabel#consMatch_both {{ color: {SEMANTIC_BAD};"
            f" background: {qss_alpha(SEMANTIC_BAD, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#consMatch_sound {{ color: {SEMANTIC_WARN};"
            f" background: {qss_alpha(SEMANTIC_WARN, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#consMatch_shape {{ color: {muted};"
            f" background: {qss_alpha(muted, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            # 计数 tag
            f"QLabel#consTagGreen {{ color: {SEMANTIC_OK};"
            f" background: {qss_alpha(SEMANTIC_OK, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#consTagRed {{ color: {SEMANTIC_BAD};"
            f" background: {qss_alpha(SEMANTIC_BAD, 0x18)};"
            f" padding: 2px 10px; border-radius: 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QLabel#consChapters {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#consHint {{ color: {muted}; font-size: 11px;"
            f" font-style: italic; padding-top: 4px; }}"
            f"QPushButton {{ color: {text_c};"
            f" background: transparent;"
            f" border: 1px solid {qss_alpha(muted, 0x66)};"
            f" border-radius: 6px; padding: 5px 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent};"
            f" background: {qss_alpha(accent, 0x1a)}; }}"
        )
        self.replace_btn.setStyleSheet(
            accent_button_qss(
                accent,
                accent_fg,
                t.get("accent_hover", accent),
                selector="QPushButton",
                padding="5px 12px",
            )
        )


class ConsistencyDialog(QDialog):
    """一致性检查结果面板。"""

    goto_requested = Signal(str, int, int)
    replace_all_requested = Signal(str, str, list)  # anchor, variant, positions

    def __init__(self, pairs: list, parent=None, work=None):
        super().__init__(parent)
        self._pairs = list(pairs or [])
        self._work = work
        self._theme = self._resolve_theme()
        self._cards = []
        self.setWindowTitle("一致性检查")
        self.setMinimumSize(720, 640)
        self._build_ui()
        self._apply_theme()

    def _resolve_theme(self) -> dict:
        # 统一走 theme_style._resolve_dialog_theme：
        #   1. 沿 parent 链找任意带 _theme 属性的 widget（editor / panel
        #      都能命中），与其它对话框保持一致
        #   2. 找不到时用 app palette 兜底
        # 之前每个对话框各写一份 while 循环，逻辑容易漂移；统一下来
        # 避免"切了主题但这个对话框没跟随"的问题。
        from ui.theme_style import _resolve_dialog_theme
        return _resolve_dialog_theme(self)

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        # 头部
        head = QFrame()
        head.setObjectName("consHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        title = QLabel("一致性检查")
        title.setObjectName("consTitle")
        hl.addWidget(title)
        hl.addStretch()
        self.summary = QLabel(f"发现 {len(self._pairs)} 组疑似同人不同名")
        self.summary.setObjectName("consSummary")
        hl.addWidget(self.summary)
        root.addWidget(head)

        if not self._pairs:
            empty = QLabel(
                "✓ 没有发现疑似同人不同名。\n\n"
                "检查基于「人物卡」和「高频词 → 角色姓名」中的名字，\n"
                "请确保你已经把主要角色录入了人物卡。\n\n"
                "被忽略的组合可在作品目录的\n"
                "consistency_ignored.json 里查看或删除。"
            )
            empty.setAlignment(Qt.AlignCenter)
            empty.setObjectName("consEmpty")
            empty.setWordWrap(True)
            root.addWidget(empty, 1)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            container = QWidget()
            cl = QVBoxLayout(container)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(10)

            # 按置信度分组
            groups = {"high": [], "medium": [], "low": []}
            for pair in self._pairs:
                conf = getattr(pair, "confidence", "medium")
                if conf not in groups:
                    conf = "medium"
                groups[conf].append(pair)

            for level in ("high", "medium", "low"):
                plist = groups[level]
                if not plist:
                    continue
                header = _ConfidenceHeader(level, len(plist), self._theme)
                cl.addWidget(header)
                for pair in plist:
                    card = _PairCard(pair, self._theme)
                    card.goto_requested.connect(self.goto_requested.emit)
                    card.replace_all_requested.connect(self.replace_all_requested.emit)
                    card.ignore_requested.connect(self._on_ignore)
                    cl.addWidget(card)
                    self._cards.append(card)
                cl.addSpacing(6)

            cl.addStretch()
            scroll.setWidget(container)
            root.addWidget(scroll, 1)

        tip = QLabel(
            "按置信度从高到低排列：高置信度（拼音完全相同）优先处理；"
            "低置信度可能只是巧合，可逐条判断。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("consTip")
        root.addWidget(tip)

        btn_row = QHBoxLayout()
        if self._pairs:
            self.clear_ignored_btn = QPushButton("恢复所有已忽略")
            self.clear_ignored_btn.setToolTip(
                "把之前忽略的组合全部恢复，下次检查会重新报告"
            )
            self.clear_ignored_btn.clicked.connect(self._on_clear_ignored)
            btn_row.addWidget(self.clear_ignored_btn)
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ---------- 忽略处理 ----------
    def _on_ignore(self, anchor: str, variant: str):
        if self._work is not None:
            try:
                add_ignored(self._work, anchor, variant)
            except Exception as e:
                QMessageBox.warning(self, "忽略失败", str(e))
                return
        for card in self._cards:
            if card._pair.anchor == anchor and card._pair.variant == variant:
                card.setVisible(False)
                break
        self._update_summary()

    def _on_clear_ignored(self):
        if self._work is None:
            return
        ret = QMessageBox.question(
            self,
            "恢复所有已忽略",
            "确定清除所有已忽略的组合吗？\n"
            "下次一致性检查会重新报告这些组合。\n\n"
            "注意：当前对话框里的卡片不会立即恢复，需要重新检查。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            clear_ignored(self._work)
        except Exception as e:
            QMessageBox.warning(self, "清除失败", str(e))
            return
        QMessageBox.information(self, "完成", "已清除所有忽略记录。")

    def _update_summary(self):
        visible = sum(1 for c in self._cards if c.isVisible())
        total = len(self._cards)
        if visible < total:
            self.summary.setText(
                f"发现 {total} 组 · 剩余 {visible} 组（已忽略 {total - visible} 组）"
            )
        else:
            self.summary.setText(f"发现 {total} 组疑似同人不同名")

    # ---------- 主题 ----------
    def _apply_theme(self):
        t = self._theme or {
            "bg": "#f7f2e8",
            "text": "#3b342a",
            "muted": "#9a8e76",
            "editor_bg": "#fcfaf4",
            "accent": "#b08a4e",
        }
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")

        self.setStyleSheet(
            f"ConsistencyDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QFrame#consHead {{ background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px; }}"
            f"QLabel#consTitle {{ color: {accent}; font-size: 16px;"
            f" font-weight: bold; }}"
            f"QLabel#consSummary {{ color: {muted}; font-size: 13px; }}"
            f"QLabel#consEmpty {{ color: {muted}; font-size: 14px;"
            f" padding: 40px 20px; }}"
            f"QLabel#consTip {{ color: {muted}; font-size: 12px;"
            f" padding: 4px; }}"
            f"QPushButton {{ color: {text};"
            f" background: transparent;"
            f" border: 1px solid {qss_alpha(muted, 0x66)};"
            f" border-radius: 6px; padding: 6px 14px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent};"
            f" background: {qss_alpha(accent, 0x1a)}; }}"
        )
