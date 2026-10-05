# ui/workspace_parts/pet_integration.py
"""墨码写作 - 宠物（墨灵）集成层（Mixin）

从 workspace.py 拆出的 WritingWorkspace 与桌面宠物之间的全部胶水逻辑：
- 宠物创建后的首显、需求/动作/奖励/成就回调
- 旧版亲密度迁移、闲置检测与主动问候
- 单击气泡、快捷操作、位置/关闭回调，宠物状态设置
- 喂给宠物 AI 的写作上下文组装（章节/卡点/引用回溯）
- 墨灵主动陪伴：切章 CTA、章末点评邀约、卡文 nudging
"""

import re
import time
import random
from datetime import datetime

from PySide6.QtCore import QTimer

from utils.logger import get_logger

logger = get_logger()


class PetIntegrationMixin:
    """工作台与 _PetFloat/_PetBubble 的交互胶水。方法直接访问 self.*。"""

    def _show_pet_initial(self):
        try:
            if self._pet_float is None:
                return
            # 每次启动都强制显示（隐藏只做会话内有效）
            self._pet_float.show()
            self._pet_float.raise_()
        except RuntimeError:
            pass

    def _on_pet_need(self, need: str):
        """brain 检测到需求 → 切表情 + 冒一句"自言自语"。"""
        import time as _time
        logger.debug(f"[Pet] need_detected: {need}")

        if self._pet_float is None:
            return

        # 二次校验：brain 是边沿触发（新需求出现/10 分钟重复一次），
        # 但用户可能恰好在这一瞬间点了道具——state 已不满足就别喊了。
        try:
            brain = getattr(self, "_pet_brain", None)
            state = brain.state if brain is not None else None
        except Exception:
            state = None
        if state is not None:
            still = {
                "thirsty": state.is_thirsty,
                "hungry":  state.is_hungry,
                "tired":   state.is_tired,
                "dirty":   state.is_dirty,
                "sad":     state.is_sad,
            }.get(need, False)
            if not still:
                return
        need_map = {
            "thirsty": ("worried", "好渴…想喝水"),
            "hungry":  ("worried", "肚子在叫…"),
            "tired":   ("sleepy",  "眼皮好重…"),
            "dirty":   ("shy",     "毛毛粘粘的…"),
            "sad":     ("worried", "有点没精神…"),
        }
        info = need_map.get(need)
        if not info:
            logger.warning(f"[Pet] need_map 无 '{need}' 条目")
            return

        # 同一需求 90 秒内不重复念
        now = _time.monotonic()
        cache = getattr(self, "_last_need_ts", None)
        if cache is None:
            cache = {}
            self._last_need_ts = cache
        if now - cache.get(need, 0.0) < 90.0:
            return
        cache[need] = now

        state, line = info
        try:
            self._pet_float.set_pet_state(state, hold_ms=2500)
            self._pet_float.say(line)
            logger.info(f"[Pet] need 响应: {need} → {state} / {line}")
        except (RuntimeError, AttributeError) as e:
            logger.warning(f"[Pet] need 响应失败: {e}")

    def _on_pet_action_record(self, action_key: str, anim_key: str):
        """brain 动作真正开始：记互动墨玉 + 长期记忆。"""
        economy = getattr(self, "_pet_economy", None)
        memory = getattr(self, "_pet_memory", None)
        try:
            if economy is not None:
                economy.on_care(action_key)
        except RuntimeError:
            pass
        try:
            if memory is not None and action_key in (
                    "pet", "feed", "drink", "clean", "play"):
                memory.note_care()
        except RuntimeError:
            pass

    def _on_pet_reward(self, title: str, desc: str, coins: int):
        """每日任务自动领奖：飘字 + 气泡。"""
        if self._pet_float is None:
            return
        try:
            self._pet_float.show_coin_toast(f"+{coins}")
            short = title.split("·")[-1].strip()
            self._pet_float.say(f"完成「{short}」，墨玉 +{coins}！", 3000)
        except RuntimeError:
            pass

    def _on_pet_achievement(self, ach: dict):
        """成就解锁：气泡庆祝 + 飘字（0 墨玉成就只说话）。"""
        if self._pet_float is None:
            return
        try:
            name = ach.get("name", "新成就")
            reward = int(ach.get("reward", 0) or 0)
            self._pet_float.say(
                f"解锁成就「{name}」"
                + (f"，墨玉 +{reward}！" if reward > 0 else "！"), 3200)
            if reward > 0:
                self._pet_float.show_coin_toast(f"+{reward}")
        except RuntimeError:
            pass

    def _migrate_legacy_pet_intimacy(self):
        """老用户升级：把 pet_intimacy_total 折算成亲密度，只执行一次。"""
        brain = getattr(self, "_pet_brain", None)
        if brain is None:
            return
        try:
            old_total = int(self.cfg.get_config(
                "Writing", "pet_intimacy_total", "0") or 0)
        except (TypeError, ValueError):
            old_total = 0
        try:
            if brain.state.migrate_legacy_typing(old_total):
                brain.flush()
                logger.info(
                    f"[Pet] 旧版击键亲密度已迁移: {old_total} 次击键")
                try:
                    self.cfg.set_config(
                        "Writing", "pet_intimacy_total", "0",
                        validate=False)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[Pet] 旧版亲密度迁移失败: {e}")

    def _check_pet_idle(self):
        if self._pet_bubble is not None and self._pet_bubble.isVisible():
            return
        now = time.monotonic()
        idle = now - getattr(self, "_last_user_activity", now)
        if idle > 300:
            self.set_base_pet_state("sleepy")
        elif idle > 180 and self._pet_float is not None:
            try:
                self._pet_float.set_modifier("bored", hold_ms=0)
            except RuntimeError:
                pass

    def _maybe_greet(self):
        """时段问候：每 30 分钟最多一次。"""
        now = time.monotonic()
        if now - getattr(self, "_last_greeting_ts", 0) < 1800:
            return
        if self.work is None or self._is_gallery():
            return
        self._last_greeting_ts = now
        hour = datetime.now().hour
        greetings = [
            (5, 11, "happy",    ["早，今天也一起写吧～", "早上好呀，主人~"]),
            (11, 14, "happy",   ["午安，别忘吃饭~", "中场休息一下？"]),
            (14, 18, "idle",    ["下午好~", "这段最有灵感了"]),
            (18, 22, "excited", ["晚上精神最好呢", "今晚写点什么？"]),
            (22, 24, "sleepy",  ["夜深了，早点睡哦", "别熬太晚~"]),
            (0, 5, "sleepy",    ["还没睡呀…", "主人该休息了"]),
        ]
        for h0, h1, state, msgs in greetings:
            if h0 <= hour < h1:
                text = random.choice(msgs)
                self.set_pet_state(state, hold_ms=2500)
                if self._pet_bubble is not None:
                    try:
                        self._pet_bubble.append_message("assistant", text)
                    except Exception:
                        pass
                break

    # ==================== 宠物接口 ====================

    def _on_pet_clicked(self):
        if self._pet_bubble is None or self._pet_float is None:
            return
        try:
            if not self._pet_float.isVisible():
                self._pet_float.show()
                self._pet_float.raise_()
        except RuntimeError:
            return
        if self._pet_bubble.isVisible():
            self._pet_bubble.request_close()
            return
        # 用"猫娘实际绘制区域"而不是整个窗口矩形——
        # 窗口有透明留白，直接传 geometry 气泡会飘到很上方
        pet_rect = self._pet_float.visual_rect_global()
        self._pet_bubble.show_from_pet(pet_rect)
        self._pet_float.set_hold_expanded(True)
        # 主动找墨灵聊天本身就是一次互动（影响 AI 上下文里
        # "主人上次和你互动是多久前"）
        brain = getattr(self, "_pet_brain", None)
        if brain is not None:
            try:
                brain.state.touch_interact()
            except Exception:
                pass
        self.set_base_pet_state("thinking")
        self._pet_float.set_offset_y(-6)

    def _on_pet_quick_action(self, action: str):
        """猫娘右键菜单的快捷操作。

        气泡没开 → 先弹出来，等动画（220ms）走完再触发。
        """
        if self._pet_bubble is None:
            return
        if not self._pet_bubble.isVisible():
            self._on_pet_clicked()
            QTimer.singleShot(
                260, lambda a=action: self._pet_quick_action_deferred(a))
        else:
            self._pet_bubble._quick_action(action)

    def _pet_quick_action_deferred(self, action: str):
        try:
            if self._pet_bubble is not None:
                self._pet_bubble._quick_action(action)
        except RuntimeError:
            pass

    def _on_pet_bubble_closed(self):
        """气泡关闭后，宠物继续待在屏幕上。"""
        if self._pet_float is not None:
            self._pet_float.set_base_pet_state("idle")
            self._pet_float.set_hold_expanded(False)
            self._pet_float.set_offset_y(0)
            self._pet_float.set_modifier("")

    def _on_pet_moved(self):
        """宠物移动 → 气泡跟随。"""
        if self._pet_bubble is None or self._pet_float is None:
            return
        if not self._pet_bubble.isVisible():
            return
        try:
            self._pet_bubble.reposition_follow_pet(
                self._pet_float.visual_rect_global())
        except RuntimeError:
            pass

    def set_pet_state(self, state: str, hold_ms: int = 3000):
        """临时状态（happy / proud / love / shy / surprised / excited / worried）。

        超时后自动回到底层状态。
        """
        if self._pet_float is not None:
            try:
                self._pet_float.set_pet_state(state, hold_ms)
            except RuntimeError:
                pass

    def set_base_pet_state(self, state: str):
        """底层状态（idle / writing / thinking / sleepy）。"""
        if self._pet_float is not None:
            try:
                self._pet_float.set_base_pet_state(state)
            except RuntimeError:
                pass

    def pet_ai_context(self) -> str:
        """构造注入 AI 对话的【墨灵此刻的状态】文本块。

        AI 宠物的核心：她知道自己和主人的关系阶段、
        此刻心情状态、主人今天写了多少。brain 不存在时返回空串。
        """
        brain = getattr(self, "_pet_brain", None)
        if brain is None:
            return ""
        try:
            from ui.pet_system.persona import build_pet_status_block
            writing = {}
            if getattr(self, "stats", None) is not None:
                writing = {
                    "today_words": self.stats.today_words(),
                    "streak_days": self.stats.streak_days(),
                    "today_minutes": self.stats.today_minutes(),
                }
            work_title = ""
            chapter_title = ""
            if self.work is not None:
                work_title = getattr(self.work, "title", "") or ""
                try:
                    ch = self.work.get_chapter(self.current_chapter_id)
                    if isinstance(ch, dict):
                        chapter_title = ch.get("title", "") or ""
                except Exception:
                    pass
            block = build_pet_status_block(
                brain.state, writing=writing,
                work_title=work_title,
                chapter_title=chapter_title)
            # 拼入长期记忆块（作品 / 写作时段 / 近日回顾）
            memory = getattr(self, "_pet_memory", None)
            if memory is not None:
                try:
                    persona_block = memory.render_persona_block()
                    if persona_block:
                        block = (block or "") + "\n\n" + persona_block
                except Exception:
                    pass
            return block
        except Exception as e:
            logger.debug(f"pet_ai_context 构造失败: {e}")
            return ""

    # ==================== 墨灵主动陪伴（B 批次） ====================

    # 卡文判定：连续打字满此时长，净增字数却不足阈值
    _BLOCK_SPAN_SEC = 12 * 60
    _BLOCK_MIN_WORDS = 40
    # 切章时：离开的章节本次运行净增达到此时长字数才 offer 点评
    _REVIEW_SWITCH_WORDS = 400
    _REVIEW_DONE_WORDS = 300

    def _pet_proactive_enabled(self) -> bool:
        """主动搭话总开关（墨灵设置页，默认开）。"""
        try:
            return str(self.cfg.get_config(
                "Pet", "proactive_talk", "1") or "1").strip() == "1"
        except Exception:
            return True

    def _pet_can_offer(self) -> bool:
        """气泡开着 / 宠物不可见时不主动打扰。"""
        bubble = getattr(self, "_pet_bubble", None)
        if bubble is not None:
            try:
                if bubble.isVisible():
                    return False
            except RuntimeError:
                return False
        pf = getattr(self, "_pet_float", None)
        if pf is None:
            return False
        try:
            return pf.isVisible()
        except RuntimeError:
            return False

    def _chapter_review_prompt(self, chapter_id: str) -> str:
        title = "这一章"
        try:
            ch = self.work.get_chapter(chapter_id)
            if ch:
                title = ch.get("title", "") or "这一章"
        except Exception:
            pass
        return (
            f"请以追读这本书的读者视角，点评我刚写完的「{title}」这一章："
            "①开头能不能一下抓住人；②中段节奏有没有拖沓；"
            "③结尾有没有留下让人想追下一章的悬念。"
            "每点一句话说清，最后只给一个最值得改的具体建议，"
            "整体简短一点，不要客套。"
        )

    def _block_prompt(self) -> str:
        return (
            "我好像在这一段卡住了，打了很久也没能往下推进。"
            "请先结合当前章节的内容判断我可能卡在哪，"
            "再给我三个可以立刻往下写的具体方向，每个一两句话，"
            "不要空泛鼓励。"
        )

    def _pet_open_and_send(self, prompt: str):
        """CTA 气泡点击：打开对话面板并直接发出诉求（用户点击即同意）。"""
        bubble = getattr(self, "_pet_bubble", None)
        if bubble is None:
            return
        try:
            visible = bubble.isVisible()
        except RuntimeError:
            return
        if not visible:
            self._on_pet_clicked()
            QTimer.singleShot(300, lambda p=prompt: self._ask_pet(p))
        else:
            self._ask_pet(prompt)

    def _ask_pet(self, prompt: str):
        try:
            if self._pet_bubble is not None:
                self._pet_bubble.ask(prompt)
        except RuntimeError:
            pass

    # ---------- B4：章末点评 ----------

    def _maybe_chapter_switch_cta(self, old_chapter_id: str):
        """离开一个刚写了不少字的章节时，offer 点评。"""
        if not old_chapter_id:
            return
        if self._pet_chapter_words.get(old_chapter_id, 0) \
                < self._REVIEW_SWITCH_WORDS:
            return
        self._offer_chapter_review(old_chapter_id)

    def _maybe_chapter_done_cta(self, chapter_id: str, chapter_words: int):
        """章节被显式标记为完成。"""
        if not chapter_id or chapter_words < self._REVIEW_DONE_WORDS:
            return
        QTimer.singleShot(
            1200,
            lambda c=chapter_id: self._offer_chapter_review(c))

    def _offer_chapter_review(self, chapter_id: str):
        try:
            if chapter_id in self._pet_review_offered:
                return
            if not self._pet_proactive_enabled() or not self._pet_can_offer():
                return
            if self.work is None:
                return
            ch = self.work.get_chapter(chapter_id)
            if not ch:
                return
            prompt = self._chapter_review_prompt(chapter_id)
            line = random.choice((
                "这一章收工啦！点我，我以追更读者的身份帮你读读？",
                "写完一章辛苦了——点我，我来看看这章抓不抓人？",
                "这章先别急着发，点我，我帮你把把关？",
            ))
            self._pet_review_offered.add(chapter_id)
            self._pet_float.say(
                line, duration_ms=9000,
                on_click=lambda p=prompt: self._pet_open_and_send(p))
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug(f"章末点评 offer 失败: {e}")

    # ---------- B5：卡文检测（纯本地启发式，零 token） ----------

    def _maybe_block_nudge(self, now: float):
        cid = self.current_chapter_id
        if not cid:
            return
        if cid in self._pet_block_nudged:
            return
        if not self._pet_proactive_enabled() or not self._pet_can_offer():
            return
        start = getattr(self, "_writing_session_start", None)
        w0 = getattr(self, "_block_words_at_session_start", None)
        if start is None or w0 is None:
            return
        if now - start < self._BLOCK_SPAN_SEC:
            return
        try:
            from utils.text_utils import count_words
            cur = count_words(self.editor.get_text())
        except Exception:
            return
        if cur - w0 >= self._BLOCK_MIN_WORDS:
            return
        # 连续打字 12 分钟却几乎没有净产出，视为卡住；每章只提醒一次
        self._pet_block_nudged.add(cid)
        prompt = self._block_prompt()
        line = random.choice((
            "写得有点费劲呢……别硬撑，点我，我们一起捋捋这一段？",
            "这一段磨了好久啦。点我，我帮你想想下一步怎么走？",
            "卡住了不丢人——点我，我陪你把这一段拆开看看？",
        ))
        try:
            self._pet_float.say(
                line, duration_ms=10000,
                on_click=lambda p=prompt: self._pet_open_and_send(p))
        except RuntimeError:
            pass

    # ---------- B6：引用回复 → 回到原文定位 ----------

    def goto_chapter_quote(self, chapter_id: str, reply_text: str = ""):
        if not self.work or not chapter_id:
            return
        if chapter_id != self.current_chapter_id:
            self.open_chapter(chapter_id)
        if self.current_chapter_id != chapter_id:
            return
        span = self._find_quote_in_chapter(chapter_id, reply_text)
        try:
            if span is not None:
                self.editor.locate_position(span[0], span[1])
            else:
                self.editor.set_focus()
        except RuntimeError:
            pass

    @staticmethod
    def _strip_for_match(text: str) -> str:
        return re.sub(r"[\s*#`>「」『』【】（）()，,。！？!？·…—\-:：;；]",
                      "", text or "")

    def _find_quote_in_chapter(self, chapter_id: str, reply_text: str):
        """在章节原文里找 AI 回复中引用得最长的一句，返回原文 (起, 止)。"""
        try:
            raw = self.work.read_chapter(chapter_id) or ""
        except Exception:
            return None
        if not raw:
            return None
        # 压缩时保留"压缩位置 → 原文位置"的映射，定位必须落回原文坐标
        compact_chars, index_map = [], []
        for i, ch in enumerate(raw):
            if self._strip_for_match(ch):
                # 该字符在正则的否定意义下"幸存"（非空白/标点），保留
                compact_chars.append(ch)
                index_map.append(i)
        compact = "".join(compact_chars)
        if len(compact) < 8:
            return None
        # AI 引用时常夹带自己的话（"我觉得「…」这句不错"），
        # 整句匹配会失败——取每个分句与原文的最长公共子串。
        from difflib import SequenceMatcher
        best_at, best_len = -1, 0
        for seg in re.split(r"[\n。！？!?；;]", reply_text or ""):
            seg = self._strip_for_match(seg)
            if len(seg) < 8:
                continue
            matcher = SequenceMatcher(None, seg, compact, autojunk=False)
            m = matcher.find_longest_match(0, len(seg), 0, len(compact))
            if m.size >= 8 and m.size > best_len:
                best_len = m.size
                best_at = m.b
        if best_at < 0:
            return None
        start = index_map[best_at]
        end = index_map[min(best_at + best_len - 1,
                            len(index_map) - 1)] + 1
        return start, max(end, start + 1)

