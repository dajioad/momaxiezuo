# ui/workspace_parts/stats_goals.py
"""墨码写作 - 字数统计与每日目标（Mixin）

从 workspace.py 拆出的码字激励相关逻辑，按 Mixin 方式合并到 WritingWorkspace：
- 今日字数 / 本章字数状态条与进度
- 章节字数目标、每日目标
- 今日千字里程碑庆祝
- 章末完成 Toast
- 晚间总结卡片与「收工」日结状态
"""

import time
from datetime import datetime

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QInputDialog,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
)

from ui.theme_style import qss_alpha
from utils.work_store import count_words
from utils.logger import get_logger

logger = get_logger()


class StatsGoalsMixin:
    """字数统计、目标、里程碑、章末/晚间卡片。方法直接访问 self.*。"""

    def _pulse_today(self):
        if not hasattr(self, "today_words_label"):
            return
        if self._is_today_settled():
            return
        t = self.editor._theme if self.editor else {}
        accent = t.get("accent", "#b08a4e")
        text = t.get("text", "#3b342a")
        try:
            self.today_words_label.setStyleSheet(
                f"color: {accent}; font-weight: 700; background: transparent;"
            )
        except RuntimeError:
            return

        def _restore():
            try:
                self.today_words_label.setStyleSheet(
                    f"color: {text}; font-weight: 600;"
                    f" background: transparent;"
                )
            except RuntimeError:
                pass
        QTimer.singleShot(400, _restore)

    # 今日字数里程碑台词（1/2/3/5/10 千字各一句，其余走通用句）
    _MILESTONE_LINES = {
        1:  "今天的第一个一千字！万事开头难，主人做到了～",
        2:  "两千字啦，手感正好，趁势写下去～",
        3:  "三千字！今天的墨灵也为主人骄傲",
        5:  "五千字！这一章已经稳稳立住了",
        10: "一万字！！主人今天是码字之神！",
    }

    def _maybe_word_milestone(self):
        """今日字数每破一个千字档 → 墨灵庆祝。

        - 每天 0 点重置（按日期判断）
        - 两次庆祝至少间隔 10 分钟，防止连续保存刷屏
        """
        if self._pet_float is None:
            return
        now = time.monotonic()
        if now - getattr(self, "_last_milestone_ts", 0.0) < 600:
            return

        from datetime import date as _date
        today_key = _date.today().isoformat()
        if self._milestone_day != today_key:
            self._milestone_day = today_key
            self._milestone_k_celebrated = 0

        try:
            today_words = self.stats.today_words()
        except Exception:
            return
        k = today_words // 1000
        if k <= self._milestone_k_celebrated:
            return
        self._milestone_k_celebrated = k
        self._last_milestone_ts = now

        # 每日任务「拿下一个千字」（economy 内部当天只记一次）
        economy = getattr(self, "_pet_economy", None)
        if economy is not None:
            try:
                economy.on_milestone()
            except RuntimeError:
                pass

        line = self._MILESTONE_LINES.get(
            k, f"今天已经写了 {k} 千字了，主人好厉害！")
        try:
            self.set_pet_state("proud", hold_ms=3000)
            self._pet_float.say(line)
        except RuntimeError:
            pass

    def _show_chapter_done_toast(self, chapter_id: str = ""):
        if not self.work:
            return

        old = getattr(self, "_done_toast", None)
        if old is not None:
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        self._done_toast = None

        try:
            streak = self.stats.streak_days()
        except Exception:
            streak = 0

        total_words = 0
        chapter_words = 0
        chapter_title = ""
        try:
            total_words = self.work.total_words()
            if chapter_id:
                ch = self.work.get_chapter(chapter_id)
                if ch:
                    chapter_title = ch.get("title", "") or ""
                    chapter_words = int(ch.get("word_count", 0) or 0)
        except Exception:
            pass

        # B4：标记完成 → 墨灵 offer 章末点评（延迟到 toast 动画之后）
        self._maybe_chapter_done_cta(chapter_id, chapter_words)

        t = self.editor._theme if self.editor else {}
        accent = t.get("accent", "#C19860")
        accent_deep = t.get("accent_hover", accent)
        text = t.get("text", "#2D2820")
        muted = t.get("muted", "#928A7C")
        editor_bg = t.get("editor_bg", "#FFFFFF")
        green = "#4CAF50"

        card = QFrame(self)
        card.setObjectName("chapterDoneToast")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setFixedWidth(280)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        badge = QLabel("✓")
        badge.setFixedSize(26, 26)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"QLabel {{ background: {green}; color: #fff;"
            f" border-radius: 13px; font-size: 14px; font-weight: 700; }}"
        )
        head.addWidget(badge)

        title_text = (
            f"{chapter_title} · 已完成" if chapter_title
            else "本章已完成"
        )
        title_lab = QLabel(title_text)
        title_lab.setStyleSheet(
            f"color: {text}; font-size: 13px; font-weight: 700;"
            f" background: transparent;"
        )
        head.addWidget(title_lab)
        head.addStretch()
        lay.addLayout(head)

        rows = QLabel(
            f"本章 <b style='color:{accent_deep};'>{chapter_words:,}</b> 字"
            f" &nbsp;·&nbsp; 累计 "
            f"<b style='color:{accent_deep};'>{total_words:,}</b> 字"
        )
        rows.setTextFormat(Qt.RichText)
        rows.setStyleSheet(
            f"color: {text}; font-size: 12.5px; background: transparent;"
        )
        lay.addWidget(rows)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(
            f"background: {qss_alpha(muted, 0x22)}; border: none;"
        )
        lay.addWidget(sep)

        if streak >= 1:
            streak_lab = QLabel(
                f"<span style='color:{accent};'>★</span>"
                f" 今天已完成一章 · 连续写作第 {streak} 天"
            )
            streak_lab.setTextFormat(Qt.RichText)
            streak_lab.setStyleSheet(
                f"color: {accent_deep}; font-size: 12px;"
                f" background: transparent;"
            )
            lay.addWidget(streak_lab)

        card.setStyleSheet(
            f"#chapterDoneToast {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(accent, 0x55)};"
            f" border-radius: 14px; }}"
        )

        card.adjustSize()
        w = card.width()
        h = card.height()
        x = self.width() - w - 20
        y = self.height() - h - 20
        # 避开宠物（宠物在屏幕右下角；卡片在 workspace 内右下角，
        # 用全局坐标转局部来碰撞检测）
        try:
            pet_geo = self._pet_float.geometry()
            pet_tl_local = self.mapFromGlobal(pet_geo.topLeft())
            if x + w > pet_tl_local.x() and y + h > pet_tl_local.y() - 20:
                y = pet_tl_local.y() - h - 12
        except Exception:
            pass
        card.move(max(0, x), max(0, y))
        card.raise_()
        card.show()

        self._done_toast = card

        effect = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

        anim = QPropertyAnimation(effect, b"opacity", card)
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _start_fade():
            try:
                anim.start()
            except RuntimeError:
                pass

        def _on_fade_done():
            try:
                card.deleteLater()
            except RuntimeError:
                pass
            if getattr(self, "_done_toast", None) is card:
                self._done_toast = None

        anim.finished.connect(_on_fade_done)
        QTimer.singleShot(2000, _start_fade)

    # ==================== 每日结算 ====================

    def _is_today_settled(self) -> bool:
        if not self.cfg:
            return False
        try:
            today_key = datetime.now().strftime("%Y-%m-%d")
            settled = self.cfg.get_config(
                "Writing", "today_settled_date", "") or ""
            return settled == today_key
        except Exception:
            return False

    def _maybe_show_evening_summary(self):
        if not self.work or not self.cfg:
            return
        if self._evening_summary_shown:
            return
        now = datetime.now()
        if now.hour < 21:
            return
        today_key = now.strftime("%Y-%m-%d")
        try:
            last = self.cfg.get_config(
                "Writing", "evening_summary_date", "") or ""
        except Exception:
            last = ""
        if last == today_key:
            self._evening_summary_shown = True
            return
        if self._is_today_settled():
            self._evening_summary_shown = True
            return
        self._evening_summary_shown = True
        try:
            self.cfg.set_config(
                "Writing", "evening_summary_date", today_key,
                validate=False)
        except Exception:
            pass
        self._show_evening_summary_card()

    def _show_evening_summary_card(self):
        if not self.work:
            return

        try:
            today = self.stats.today_words()
            streak = self.stats.streak_days()
            yesterday = self.stats.words_of_day(1)
        except Exception:
            return

        t = self.editor._theme if self.editor else {}
        accent = t.get("accent", "#C19860")
        accent_deep = t.get("accent_hover", accent)
        text = t.get("text", "#2D2820")
        muted = t.get("muted", "#928A7C")
        editor_bg = t.get("editor_bg", "#FFFFFF")
        card_border = qss_alpha(muted, 0x44)

        old = getattr(self, "_evening_toast", None)
        if old is not None:
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        self._evening_toast = None

        card = QFrame(self)
        card.setObjectName("eveningSummaryCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setFixedWidth(340)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        badge = QLabel("今")
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"QLabel {{ background: {accent}; color: #fff;"
            f" border-radius: 14px; font-size: 14px; font-weight: 700; }}"
        )
        head.addWidget(badge)
        title = QLabel("今天的写作")
        title.setStyleSheet(
            f"color: {text}; font-size: 13.5px; font-weight: 700;"
            f" background: transparent;"
        )
        head.addWidget(title)
        head.addStretch()
        lay.addLayout(head)

        diff = today - yesterday
        if yesterday > 0 and diff > 0:
            diff_line = (
                f"比昨天多 <b style='color:{accent_deep};'>{diff:,}</b> 字"
            )
        elif yesterday > 0 and diff < 0:
            diff_line = (
                f"比昨天少 <b style='color:{accent_deep};'>{-diff:,}</b> 字"
            )
        else:
            diff_line = "和昨天差不多"

        content = QLabel(
            f"写了 <b style='color:{accent_deep};'>{today:,}</b> 字"
            f" &nbsp;·&nbsp; 连续第 "
            f"<b style='color:{accent_deep};'>{streak}</b> 天<br>"
            f"{diff_line}"
        )
        content.setTextFormat(Qt.RichText)
        content.setStyleSheet(
            f"color: {text}; font-size: 12.5px; background: transparent;"
        )
        lay.addWidget(content)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(
            f"background: {qss_alpha(muted, 0x22)}; border: none;"
        )
        lay.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_continue = QPushButton("继续写")
        btn_continue.setCursor(Qt.PointingHandCursor)
        btn_continue.setFixedHeight(32)
        btn_continue.setStyleSheet(
            f"QPushButton {{ background-color: {accent};"
            f" color: #fff; border: 1px solid {accent_deep};"
            f" border-radius: 8px; font-size: 12.5px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {accent_deep}; }}"
        )
        btn_continue.clicked.connect(
            lambda _=False: self._dismiss_evening_card()
        )
        btn_row.addWidget(btn_continue, 1)

        btn_stop = QPushButton("今天到此为止")
        btn_stop.setCursor(Qt.PointingHandCursor)
        btn_stop.setFixedHeight(32)
        btn_stop.setStyleSheet(
            f"QPushButton {{ background-color: {editor_bg};"
            f" color: {text}; border: 1px solid {card_border};"
            f" border-radius: 8px; font-size: 12.5px; }}"
            f"QPushButton:hover {{ color: {accent};"
            f" border-color: {accent};"
            f" background-color: {qss_alpha(accent, 0x12)}; }}"
        )
        btn_stop.clicked.connect(self._on_settle_today)
        btn_row.addWidget(btn_stop, 1)

        lay.addLayout(btn_row)

        card.setStyleSheet(
            f"#eveningSummaryCard {{"
            f" background-color: {editor_bg};"
            f" border: 1px solid {qss_alpha(accent, 0x55)};"
            f" border-radius: 14px; }}"
        )

        card.adjustSize()
        w = card.width()
        h = card.height()
        x = max(10, self.width() - w - 20)
        y = max(10, self.height() - h - 20)
        try:
            pet_geo = self._pet_float.geometry()
            pet_tl_local = self.mapFromGlobal(pet_geo.topLeft())
            if x + w > pet_tl_local.x() and y + h > pet_tl_local.y() - 20:
                y = pet_tl_local.y() - h - 12
        except Exception:
            pass
        card.move(x, max(10, y))
        card.raise_()
        card.show()

        self._evening_toast = card

        QTimer.singleShot(3 * 60 * 1000, self._dismiss_evening_card)

    def _dismiss_evening_card(self):
        card = getattr(self, "_evening_toast", None)
        if card is not None:
            try:
                card.deleteLater()
            except RuntimeError:
                pass
        self._evening_toast = None

    def _on_settle_today(self):
        try:
            today_key = datetime.now().strftime("%Y-%m-%d")
            if self.cfg:
                self.cfg.set_config(
                    "Writing", "today_settled_date", today_key,
                    validate=False)
        except Exception as e:
            logger.warning(f"记录收工状态失败: {e}")
        self._dismiss_evening_card()
        self._apply_settled_state()
        self.save_state_label.setText("今天已收工 · 明天见")

    def _apply_settled_state(self):
        t = self.editor._theme if self.editor else {}
        muted = t.get("muted", "#928A7C")
        try:
            self.today_words_label.setStyleSheet(
                f"color: {muted}; font-weight: 600;"
                f" background: transparent;"
            )
        except RuntimeError:
            pass

    # ==================== 字数状态 ====================

    def _update_word_status(self):
        text = self.editor.get_text()
        words = count_words(text)

        saved_today = self.stats.today_words()
        unsaved_delta = words - self._baseline_words
        today_display = max(0, saved_today + unsaved_delta)
        settled = self._is_today_settled()
        if settled:
            self.today_words_label.setText(
                f"今天 {today_display:,} 字 · 已收工")
        else:
            self.today_words_label.setText(f"今天 {today_display:,} 字")

        daily_target = self._daily_target()
        if daily_target > 0:
            pct = min(100, int(today_display / daily_target * 100))
            self.today_progress.setValue(pct)
            self.today_percent_label.setText(f"{pct}%")
            self.today_progress.setVisible(True)
            self.today_percent_label.setVisible(True)
            if today_display >= daily_target:
                if not getattr(self, "_today_goal_celebrated", False):
                    self._today_goal_celebrated = True
                    self.set_pet_state("excited", hold_ms=4000)
                t = self.editor._theme if self.editor else {}
                accent = t.get("accent", "#b08a4e")
                self.today_progress.setStyleSheet(
                    f"QProgressBar#sbTodayBar {{"
                    f" background: rgba(176,138,78,0.22);"
                    f" border: none; border-radius: 2px; }}"
                    f"QProgressBar#sbTodayBar::chunk {{"
                    f" background: qlineargradient("
                    f"x1:0, y1:0, x2:1, y2:0,"
                    f" stop:0 {accent}, stop:1 #e8c890);"
                    f" border-radius: 2px; }}"
                )
                self.today_percent_label.setStyleSheet(
                    f"color: {accent}; font-weight: 700;")
            else:
                self.today_progress.setStyleSheet("")
                self.today_percent_label.setStyleSheet("")
                self._today_goal_celebrated = False
        else:
            self.today_progress.setVisible(False)
            self.today_percent_label.setVisible(False)

        if settled != self._last_settled_state:
            self._last_settled_state = settled
            if settled:
                self._apply_settled_state()
            else:
                self.today_words_label.setStyleSheet("")

        self.word_count_label.setText(f"本章 {words:,}")

        streak = self.stats.streak_days()
        speed = self.stats.writing_speed()
        daily_target = self._daily_target()
        tip_lines = [f"本章 {words:,} 字"]
        if streak:
            tip_lines.append(f"连续写作 {streak} 天")
        if speed:
            tip_lines.append(f"当前时速约 {speed:,} 字/小时")
        if daily_target > 0:
            pct = min(100, int(saved_today / daily_target * 100))
            tip_lines.append(
                f"今日 {saved_today:,} / {daily_target:,} 字（{pct}%）")
        else:
            tip_lines.append(f"今日 {saved_today:,} 字")
        self.today_words_label.setToolTip("\n".join(tip_lines))

        self._update_chapter_target(words)
        if self.work and self.current_chapter_id:
            ch = self.work.get_chapter(self.current_chapter_id)
            if ch:
                self.chapter_header.set_info(
                    ch.get("title", ""),
                    ch.get("volume", "") or "正文",
                    words,
                    self.current_chapter_id,
                )

    def _update_chapter_target(self, words: int):
        if not self.work or not self.current_chapter_id:
            self.chapter_target_label.setText("")
            self.chapter_target_label.setVisible(False)
            self.chapter_progress.setVisible(False)
            return
        target = self.work.get_chapter_word_target(self.current_chapter_id)
        if target <= 0:
            self.chapter_target_label.setText("")
            self.chapter_target_label.setVisible(False)
            self.chapter_progress.setVisible(False)
            return
        self.chapter_target_label.setVisible(True)
        self.chapter_progress.setVisible(True)

        pct = min(100, int(words / target * 100)) if target > 0 else 0
        self.chapter_progress.setValue(pct)

        t = self.editor._theme if self.editor else {}
        accent = t.get("accent", "#b08a4e")
        muted = t.get("muted", "#9a8e76")
        if words >= target:
            self.chapter_target_label.setText(f" / {target:,}  ✓")
            self.chapter_target_label.setStyleSheet(
                f"color: {accent}; font-size: 12px; font-weight: 700;"
                f" padding: 0 4px; background: transparent;"
            )
        else:
            self.chapter_target_label.setText(f" / {target:,}")
            self.chapter_target_label.setStyleSheet(
                f"color: {muted}; font-size: 12px;"
                f" padding: 0 4px; background: transparent;"
            )

    def _edit_chapter_target(self):
        if not self.work or not self.current_chapter_id:
            return
        cur = self.work.get_chapter_word_target(self.current_chapter_id)
        val, ok = QInputDialog.getInt(
            self,
            "章节字数目标",
            "本章目标字数（0 = 取消目标）：",
            cur if cur > 0 else 3000,
            0,
            100000,
            100,
        )
        if not ok:
            return
        self.work.set_chapter_word_target(self.current_chapter_id, val)
        self._update_word_status()

    def _daily_target(self) -> int:
        target = 0
        if self.work:
            try:
                target = int(self.work.meta.get("daily_target", 0) or 0)
            except (TypeError, ValueError):
                target = 0
        if target <= 0 and self.cfg:
            try:
                target = int(
                    self.cfg.get_config("Writing", "daily_target", "4000") or 4000
                )
            except (TypeError, ValueError):
                target = 4000
        return target

