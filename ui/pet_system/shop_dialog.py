# ui/pet_system/shop_dialog.py
"""墨码写作 - 墨灵小屋：墨玉商店 / 背包 / 每日任务 / 成就。

一个对话框四个标签页。打开期间每 2 秒刷新（码字赚的墨玉、
任务进度会实时跳）。背包里的道具可直接"使用"。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QFrame, QScrollArea, QProgressBar, QGridLayout, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer

import qtawesome as qta

from .economy import SHOP_ITEMS, DAILY_TASKS, ACHIEVEMENTS
from .jade_icon import jade_pixmap, jade_icon

# 购买数量选项（按钮文案, 数量）
_BUY_QTYS = ((1, "买 1 个"), (5, "买 5 个"))

_TAB_ICONS = {
    "shop":  "fa5s.shopping-basket",
    "bag":   "fa5s.shopping-bag",
    "tasks": "fa5s.tasks",
    "ach":   "fa5s.trophy",
}


class ShopDialog(QDialog):
    def __init__(self, economy, theme: dict | None = None,
                 parent=None, on_use=None):
        super().__init__(parent)
        self._eco = economy
        self._theme = theme or {}
        self._on_use = on_use  # 背包里点"使用" → pet_float.do_action
        self.setWindowTitle("墨灵小屋")
        self.setMinimumSize(580, 640)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ========== 顶部横幅：墨玉余额 ==========
        self.header = QFrame()
        self.header.setObjectName("petShopHeader")
        self.header.setFixedHeight(86)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(22, 12, 22, 12)
        hl.setSpacing(14)

        coin_icon = QLabel()
        coin_icon.setPixmap(qta.icon("fa5s.coins",
                                     color="#ffe9a8").pixmap(34, 34))
        hl.addWidget(coin_icon)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.balance_label = QLabel()
        self.balance_label.setObjectName("balanceBig")
        self.earned_label = QLabel()
        self.earned_label.setObjectName("earnedHint")
        col.addWidget(self.balance_label)
        col.addWidget(self.earned_label)
        hl.addLayout(col, 1)

        tip = QLabel("码字 · 陪伴 · 里程碑\n都能赚墨玉")
        tip.setObjectName("headerTip")
        tip.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hl.addWidget(tip)
        root.addWidget(self.header)

        # ========== 标签页 ==========
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self._tab_shop = self._make_scroll_tab()
        self._tab_bag = self._make_scroll_tab()
        self._tab_tasks = self._make_scroll_tab()
        self._tab_ach = self._make_scroll_tab()
        self.tabs.addTab(self._tab_shop["w"], " 商店  ")
        self.tabs.addTab(self._tab_bag["w"], " 背包  ")
        self.tabs.addTab(self._tab_tasks["w"], " 每日任务  ")
        self.tabs.addTab(self._tab_ach["w"], " 成就  ")

        self._apply_theme()

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ---------- 构建工具 ----------

    def _make_scroll_tab(self) -> dict:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("shopInner")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        lay.addStretch(1)
        scroll.setWidget(inner)
        return {"w": scroll, "inner": inner, "lay": lay}

    def _card(self, parent_lay) -> QFrame:
        card = QFrame()
        card.setObjectName("ecoCard")
        parent_lay.insertWidget(parent_lay.count() - 1, card)
        return card

    def _empty_tip(self, tab: dict, text: str):
        tip = QLabel(text)
        tip.setObjectName("emptyTip")
        tip.setAlignment(Qt.AlignCenter)
        tab["lay"].insertWidget(0, tip)

    def _price_widget(self, amount: int, suffix: str = "",
                      big: bool = False) -> QWidget:
        """古钱币图标 + 金色数字（替代 🪙 emoji）。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        ico = QLabel()
        ico.setPixmap(jade_pixmap(13 if big else 12))
        h.addWidget(ico)
        num = QLabel(f"{amount}{suffix}")
        num.setStyleSheet(
            "color:#b7893f; font-weight: 800;"
            f" font-size: {13 if big else 12}px;")
        h.addWidget(num)
        h.addStretch()
        return w

    def _status_chip(self, text: str, kind: str) -> QLabel:
        chip = QLabel(text)
        chip.setObjectName(f"chip_{kind}")
        chip.setAlignment(Qt.AlignCenter)
        chip.setFixedHeight(22)
        return chip

    def _apply_theme(self):
        t = self._theme
        bg = t.get("bg", "#f7f2e8")
        card_bg = t.get("editor_bg", "#fcfaf4")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        accent_soft = _mix(card_bg, accent, 0.12)
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"QLabel {{ color: {text_c}; background: transparent; }}"
            f"#petShopHeader {{ background: {accent}; }}"
            f"QLabel#balanceBig {{ color: #fff8e6; font-size: 22px;"
            f" font-weight: 800; background: transparent; }}"
            f"QLabel#earnedHint {{ color: rgba(255,255,255,0.85);"
            f" font-size: 12px; background: transparent; }}"
            f"QLabel#headerTip {{ color: rgba(255,255,255,0.78);"
            f" font-size: 11px; background: transparent; }}"
            f"QTabWidget::pane {{ border: none; }}"
            f"QTabBar::tab {{ padding: 9px 18px; color: {muted};"
            f" background: transparent; font-size: 13px;"
            f" border: none; }}"
            f"QTabBar::tab:selected {{ color: {accent};"
            f" border-bottom: 2px solid {accent}; font-weight: 700; }}"
            f"QFrame#ecoCard {{ background: {card_bg};"
            f" border: 1px solid {_mix(card_bg, muted, 0.18)};"
            f" border-radius: 12px; }}"
            f"QFrame#ecoCard:hover {{"
            f" border: 1px solid {_mix(card_bg, accent, 0.45)}; }}"
            f"QWidget#shopInner, QScrollArea {{ background: transparent;"
            f" border: none; }}"
            f"QLabel#emptyTip {{ color: {muted}; font-size: 13px;"
            f" padding: 40px 0; }}"
            f"QLabel#cardTitle {{ font-size: 14px; font-weight: 700; }}"
            f"QLabel#cardDesc {{ color: {muted}; font-size: 11.5px; }}"
            f"QLabel#itemIcon {{ background: {accent_soft};"
            f" border-radius: 10px; }}"
            f"QLabel#countBadge {{ background: {accent}; color: white;"
            f" border-radius: 9px; font-size: 11px; font-weight: 800;"
            f" padding: 1px 8px; }}"
            f"QPushButton {{ background: {accent}; color: white;"
            f" border: none; border-radius: 8px; font-size: 12px;"
            f" padding: 6px 14px; }}"
            f"QPushButton:hover {{ background:"
            f" {_mix('#000000', accent, 0.88)}; }}"
            f"QPushButton:disabled {{ background: {muted};"
            f" color: rgba(255,255,255,0.75); }}"
            f"QPushButton#ghostBtn {{ background: transparent;"
            f" color: {accent}; border: 1px solid {accent}; }}"
            f"QPushButton#ghostBtn:hover {{ background: {accent_soft}; }}"
            f"QPushButton#ghostBtn:disabled {{ color: {muted};"
            f" border-color: {muted}; background: transparent; }}"
            f"QLabel#chip_done {{ color: {accent}; font-weight: 800;"
            f" font-size: 12px; }}"
            f"QLabel#chip_claimed {{ color: {muted}; font-size: 12px; }}"
            f"QLabel#chip_running {{ color: {muted}; font-size: 12px; }}"
            f"QProgressBar {{ background: {_mix(card_bg, muted, 0.15)};"
            f" border: none; border-radius: 5px; height: 10px;"
            f" text-align: center; }}"
            f"QProgressBar::chunk {{ background: {accent};"
            f" border-radius: 5px; }}"
        )

    # ---------- 刷新 ----------

    def refresh(self):
        if self._eco is None:
            return
        try:
            coins = self._eco.coins
            daily = self._eco.daily_progress()
        except RuntimeError:
            return
        self.balance_label.setText(f"墨玉 {coins:,}")
        earned = int(daily.get("earned_words", 0)) + int(
            daily.get("earned_cares", 0))
        words = int(daily.get("words", 0))
        self.earned_label.setText(
            f"今日已赚 +{earned}　·　今日码字 {words:,}")
        self._fill_shop(coins)
        self._fill_bag()
        claimable = self._fill_tasks()
        self._fill_achievements()
        # 任务标签角标：有可领奖的任务时提示
        idx = self.tabs.indexOf(self._tab_tasks["w"])
        self.tabs.setTabText(
            idx, f" 每日任务{' · ' + str(claimable) if claimable else ''}  ")

    def _clear_cards(self, tab: dict):
        while tab["lay"].count() > 1:
            item = tab["lay"].takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---------- 商店 ----------

    def _fill_shop(self, coins: int):
        lay = self._tab_shop["lay"]
        self._clear_cards(self._tab_shop)
        accent = self._theme.get("accent", "#b08a4e")
        muted = self._theme.get("muted", "#9a8e76")
        for key, item in SHOP_ITEMS.items():
            card = self._card(lay)
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(12)

            icon = QLabel()
            icon.setObjectName("itemIcon")
            icon.setPixmap(qta.icon(item["icon"], color=accent).pixmap(28, 28))
            icon.setFixedSize(48, 48)
            icon.setAlignment(Qt.AlignCenter)
            row.addWidget(icon)

            info = QVBoxLayout()
            info.setSpacing(3)
            title = QLabel(
                f"<span id='cardTitle'>{item['name']}</span>"
                f"　<span style='font-size:11px;color:{muted};'>"
                f"背包 ×{self._eco.inventory_count(key)}</span>")
            title.setObjectName("cardTitle")
            desc = QLabel(item["desc"])
            desc.setObjectName("cardDesc")
            info.addWidget(title)
            info.addWidget(desc)
            price = self._price_widget(item["price"], " / 个")
            info.addWidget(price)
            row.addLayout(info, 1)

            btn_col = QVBoxLayout()
            btn_col.setSpacing(6)
            for qty, label in _BUY_QTYS:
                cost = item["price"] * qty
                btn = QPushButton(jade_icon(13, on_dark=qty == 1),
                                  f"{label} · {cost}")
                btn.setObjectName("ghostBtn" if qty > 1 else "")
                btn.setFixedWidth(118)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setEnabled(coins >= cost)
                btn.clicked.connect(
                    lambda _=False, k=key, q=qty: self._buy(k, q))
                btn_col.addWidget(btn)
            row.addLayout(btn_col)

    # ---------- 背包 ----------

    def _fill_bag(self):
        tab = self._tab_bag
        lay = tab["lay"]
        self._clear_cards(tab)
        total = 0
        accent = self._theme.get("accent", "#b08a4e")
        muted = self._theme.get("muted", "#9a8e76")
        for key, item in SHOP_ITEMS.items():
            n = self._eco.inventory_count(key)
            total += n
            card = self._card(lay)
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 10, 14, 10)
            row.setSpacing(12)

            icon = QLabel()
            icon.setObjectName("itemIcon")
            icon.setPixmap(qta.icon(
                item["icon"],
                color=accent if n > 0 else muted).pixmap(24, 24))
            icon.setFixedSize(44, 44)
            icon.setAlignment(Qt.AlignCenter)
            row.addWidget(icon)

            name = QLabel(item["name"])
            name.setObjectName("cardTitle")
            row.addWidget(name, 1)

            badge = QLabel(f"× {n}")
            badge.setObjectName("countBadge" if n > 0 else "")
            badge.setAlignment(Qt.AlignCenter)
            badge.setMinimumWidth(40)
            if n == 0:
                badge.setStyleSheet(f"color: {muted}; font-weight: 700;")
            row.addWidget(badge)

            use_btn = QPushButton("使用")
            use_btn.setObjectName("ghostBtn")
            use_btn.setFixedWidth(64)
            use_btn.setCursor(Qt.PointingHandCursor)
            use_btn.setEnabled(n > 0 and callable(self._on_use))
            use_btn.clicked.connect(
                lambda _=False, k=key: self._use(k))
            row.addWidget(use_btn)
        if total == 0:
            self._empty_tip(tab, "背包空空的，去商店买点东西吧～\n"
                                 "（摸头和睡觉不需要道具）")

    # ---------- 每日任务 ----------

    def _fill_tasks(self) -> int:
        tab = self._tab_tasks
        lay = tab["lay"]
        self._clear_cards(tab)
        status = {s["task"]["id"]: s for s in self._eco.task_status()}
        claimable = 0
        for t in DAILY_TASKS:
            s = status[t["id"]]
            if s["done"] and not s["claimed"]:
                claimable += 1
            card = self._card(lay)
            v = QVBoxLayout(card)
            v.setContentsMargins(14, 12, 14, 12)
            v.setSpacing(8)

            head = QHBoxLayout()
            name = QLabel(t["name"])
            name.setObjectName("cardTitle")
            head.addWidget(name, 1)
            head.addWidget(self._price_widget(t["reward"], " 墨玉"))
            if s["claimed"]:
                head.addWidget(self._status_chip("已领取", "claimed"))
            elif s["done"]:
                head.addWidget(self._status_chip("奖励已发放", "done"))
            else:
                head.addWidget(self._status_chip("进行中", "running"))
            v.addLayout(head)

            bar = QProgressBar()
            bar.setRange(0, t["goal"])
            bar.setValue(min(s["current"], t["goal"]))
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            v.addWidget(bar)

            foot = QLabel(
                f"{t['desc']}　"
                f"<span style='color:{self._theme.get('muted', '#9a8e76')};'>"
                f"{s['current']} / {t['goal']}</span>")
            foot.setObjectName("cardDesc")
            v.addWidget(foot)
        return claimable

    # ---------- 成就 ----------

    def _fill_achievements(self):
        tab = self._tab_ach
        lay = tab["lay"]
        self._clear_cards(tab)
        unlocked = 0
        accent = self._theme.get("accent", "#b08a4e")
        muted = self._theme.get("muted", "#9a8e76")
        for ach in ACHIEVEMENTS:
            got = self._eco.is_achievement_unlocked(ach["id"])
            if got:
                unlocked += 1
            card = self._card(lay)
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 10, 14, 10)
            row.setSpacing(12)

            medal = QLabel()
            medal.setAlignment(Qt.AlignCenter)
            medal.setFixedSize(44, 44)
            if got:
                medal.setPixmap(qta.icon("fa5s.medal",
                                         color=accent).pixmap(28, 28))
            else:
                medal.setPixmap(qta.icon("fa5s.lock",
                                         color=muted).pixmap(22, 22))
            row.addWidget(medal)

            info = QVBoxLayout()
            info.setSpacing(2)
            name = QLabel(ach["name"])
            name.setObjectName("cardTitle")
            if not got:
                name.setStyleSheet(f"color: {muted}; font-weight: 700;")
            desc = QLabel(
                ach["desc"]
                + (f"　奖励 {ach['reward']} 墨玉" if ach["reward"]
                   and got else ("　奖励 ?? 墨玉" if ach["reward"] else "")))
            desc.setObjectName("cardDesc")
            info.addWidget(name)
            info.addWidget(desc)
            row.addLayout(info, 1)

            if got:
                row.addWidget(self._status_chip("已解锁", "done"))
        pct = int(unlocked / max(1, len(ACHIEVEMENTS)) * 100)
        self._empty_tip(
            tab, f"已解锁 {unlocked} / {len(ACHIEVEMENTS)}（{pct}%）")

    # ---------- 动作 ----------

    def _buy(self, key: str, qty: int):
        if self._eco is None:
            return
        self._eco.buy(key, qty)
        self.refresh()

    def _use(self, key: str):
        if callable(self._on_use):
            self._on_use(key)
        QTimer.singleShot(300, self.refresh)


def _mix(hex_a: str, hex_b: str, ratio: float) -> str:
    """两个十六进制颜色按比例混合（ratio 为 b 的占比）。"""
    try:
        def _rgb(h):
            h = h.lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        a, b = _rgb(hex_a), _rgb(hex_b)
        r = ratio if 0.0 <= ratio <= 1.0 else 0.0
        return "#%02x%02x%02x" % tuple(
            int(a[i] * (1 - r) + b[i] * r) for i in range(3))
    except Exception:
        return hex_b
