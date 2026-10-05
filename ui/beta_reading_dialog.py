# ui/beta_reading_dialog.py
"""墨码写作 - 墨灵试读报告对话框。

上半部分：墨灵读完选定区间后的总评（亮点 / 弃书点 / 节奏 / 建议）。
下半部分：逐章卡片——追读欲星级、开头钩子、结尾钩子、当场感受、
本地零 token 统计；每章可点"查看本章"跳回正文。
AI 失败/空章仍展示本地统计，不白跑一趟。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget,
)
from PySide6.QtCore import Qt

from ui.theme_style import apply_dialog_theme


def _stars(n: int) -> str:
    n = max(0, min(5, int(n or 0)))
    return "★" * n + "☆" * (5 - n)


class BetaReportDialog(QDialog):
    def __init__(self, entries: list, summary: dict = None,
                 parent=None, theme: dict = None, on_jump=None,
                 saved: bool = False):
        super().__init__(parent)
        self._entries = list(entries or [])
        self._summary = summary or {}
        self._theme = theme or {}
        self._on_jump = on_jump
        self._saved = saved
        self.setWindowTitle("墨灵试读报告")
        self.resize(620, 680)
        self._build()
        apply_dialog_theme(self, theme=theme)
        self._apply_card_style()

    # ---------------- UI ----------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("betaHost")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        head = QLabel("墨灵试读报告")
        head.setObjectName("betaTitle")
        lay.addWidget(head)

        if self._saved:
            hint = QLabel("报告已自动保存，可随时在「写作 → 质量中心」回看。")
            hint.setWordWrap(True)
            hint.setObjectName("betaMuted")
            lay.addWidget(hint)

        lay.addWidget(self._build_summary_card())

        for e in self._entries:
            lay.addWidget(self._build_chapter_card(e))

        lay.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setProperty("variant", "primary")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(18, 0, 18, 12)
        wrap.addStretch()
        wrap.addWidget(close_btn)
        root.addLayout(wrap)

    def _section_card(self, title: str) -> tuple:
        card = QFrame()
        card.setProperty("betaCard", True)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("betaCardTitle")
        cl.addWidget(t)
        return card, cl

    def _build_summary_card(self) -> QFrame:
        s = self._summary
        card, cl = self._section_card("墨灵总评")

        verdict = QLabel(s.get("verdict") or "（墨灵这次没给出总评）")
        verdict.setWordWrap(True)
        verdict.setObjectName("betaVerdict")
        cl.addWidget(verdict)

        if s.get("rhythm"):
            cl.addWidget(self._line("节奏", s["rhythm"]))

        for label, key in (("最抓人", "strengths"),
                           ("弃书风险", "drop_points"),
                           ("修改建议", "advice")):
            items = s.get(key) or []
            if not items:
                continue
            cl.addWidget(self._bullet_block(label, items,
                                            warn=(key == "drop_points")))
        return card

    def _line(self, label: str, text: str) -> QLabel:
        lab = QLabel(f"<b>{label}</b>　{text}")
        lab.setWordWrap(True)
        lab.setObjectName("betaLine")
        return lab

    def _bullet_block(self, label: str, items: list, warn: bool = False
                      ) -> QFrame:
        wrap = QFrame()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        head = QLabel(f"<b>{label}</b>")
        head.setObjectName("betaLineWarn" if warn else "betaLine")
        wl.addWidget(head)
        for it in items:
            bullet = "！" if warn else "·"
            row = QLabel(f"　{bullet} {it}")
            row.setWordWrap(True)
            row.setObjectName("betaBulletWarn" if warn else "betaBullet")
            wl.addWidget(row)
        return wrap

    def _build_chapter_card(self, e: dict) -> QFrame:
        idx = e.get("index", 0)
        title = e.get("title", "")
        stats = e.get("stats") or {}
        reaction = e.get("reaction") or {}
        card, cl = self._section_card(f"第 {idx} 章 · {title}")

        top = QHBoxLayout()
        top.setSpacing(10)
        if reaction.get("want"):
            want = QLabel(f"追读欲 {_stars(reaction['want'])}")
            want.setObjectName("betaWant")
            top.addWidget(want)
        else:
            tag = "空章已跳过" if e.get("skipped") else "本章 AI 读取失败"
            want = QLabel(tag)
            want.setObjectName("betaMuted")
            top.addWidget(want)
        top.addStretch()

        jump_btn = QPushButton("查看本章")
        cid = e.get("chapter_id", "")
        if cid and callable(self._on_jump):
            jump_btn.clicked.connect(lambda _=False, c=cid: self._on_jump(c))
        else:
            jump_btn.setEnabled(False)
        top.addWidget(jump_btn)
        cl.addLayout(top)

        stat_bits = [f"{int(stats.get('chars', 0)):,} 字"]
        if stats.get("paras"):
            stat_bits.append(f"{stats['paras']} 段")
        if stats.get("dialogue_ratio") is not None:
            stat_bits.append(f"对白约 {round(stats['dialogue_ratio'] * 100)}%")
        if stats.get("cliff") and stats["cliff"] != "平":
            stat_bits.append(f"结尾：{stats['cliff']}")
        stat_lab = QLabel("　·　".join(stat_bits))
        stat_lab.setObjectName("betaMuted")
        cl.addWidget(stat_lab)

        if reaction:
            for label, key in (("开头", "hook"), ("结尾", "ending"),
                               ("感受", "feeling")):
                v = reaction.get(key, "")
                if v:
                    cl.addWidget(self._line(label, v))
            drop = reaction.get("drop", "")
            if drop and drop != "无":
                cl.addWidget(self._bullet_block("可能弃读", [drop],
                                                warn=True))
        elif e.get("error"):
            err = QLabel(f"AI 请求失败：{e['error']}（本地统计不受影响）")
            err.setWordWrap(True)
            err.setObjectName("betaMuted")
            cl.addWidget(err)
        return card

    # ---------------- 样式（克制国风，无渐变/发光） ----------------

    def _apply_card_style(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        card_bg = t.get("editor_bg", "#fcfaf4")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#928a76")
        accent = t.get("accent", "#b08a4e")
        warn = "#b4543a"

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"QLabel {{ background: transparent; }}"
            f"#betaHost {{ background: {bg}; }}"
            f"#betaTitle {{ color: {text_c}; font-size: 18px;"
            f" font-weight: 700; }}"
            f"QFrame[betaCard=\"true\"] {{"
            f" background: {card_bg}; border: 1px solid {muted};"
            f" border-radius: 9px; }}"
            f"#betaCardTitle {{ color: {accent}; font-size: 13.5px;"
            f" font-weight: 700; }}"
            f"#betaVerdict {{ color: {text_c}; font-size: 14px;"
            f" font-weight: 700; }}"
            f"#betaLine, #betaLineWarn, #betaBullet {{ color: {text_c};"
            f" font-size: 12.5px; }}"
            f"#betaBulletWarn, #betaLineWarn {{ color: {warn}; }}"
            f"#betaWant {{ color: {accent}; font-size: 13px;"
            f" font-weight: 700; }}"
            f"#betaMuted {{ color: {muted}; font-size: 12px; }}"
            f"QPushButton {{ color: {text_c}; background: transparent;"
            f" border: 1px solid {muted}; border-radius: 6px;"
            f" padding: 5px 14px; font-size: 12.5px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent}; }}"
            f"QPushButton:disabled {{ color: {muted}; }}"
        )
