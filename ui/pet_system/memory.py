# ui/pet_system/memory.py
"""墨码写作 - 墨灵的长期记忆。

普通 AI 宠物每次对话都是失忆的；墨灵要"记得主人"：
- 主人写过哪些作品
- 主人习惯在什么时段写作
- 最近几天写了多少、互动多不多
- 聊天里提到的写作偏好、创作计划、作品设定（facts）

数据文件 pet_memory.json（轻量，只存提炼后的事实，不存聊天记录）。
渲染成一小段提示词，拼进 AI 对话的 system message。
"""

from __future__ import annotations

import os
import re
import json
from datetime import date, datetime

from utils.logger import get_logger
from utils.work_store import atomic_write_json

logger = get_logger()

_MEMORY_FILE = "pet_memory.json"
_KEEP_DAYS = 14          # 只保留最近 14 天的每日回顾
_KEEP_WORKS = 8          # 最多记住 8 个作品名
_KEEP_FACTS = 40         # 最多记住 40 条事实（超出淘汰最旧的）
_FACT_MAX_LEN = 60       # 单条事实最长 60 字，太长的截断
_RENDER_FACTS = 12       # 注入提示词时最多带 12 条
_KEEP_EXTRACT_DAYS = 7   # 只保留近 7 天的 AI 提炼计数

# 时段划分（用于"你习惯在晚上写作"这类描述）
def _period_of_hour(h: int) -> str:
    if 5 <= h < 11:
        return "清晨/上午"
    if 11 <= h < 14:
        return "中午"
    if 14 <= h < 18:
        return "下午"
    if 18 <= h < 23:
        return "晚上"
    return "深夜"


class PetMemory:
    def __init__(self, data_root: str):
        self._path = os.path.join(data_root, _MEMORY_FILE)
        # {作品名: {"last": "YYYY-MM-DD"}}
        self.works: dict = {}
        # 24 个桶：在该小时活跃过的次数
        self.hours: list = [0] * 24
        # {"YYYY-MM-DD": {"words": int, "minutes": int, "cares": int}}
        self.days: dict = {}
        # [{"t": iso, "text": str, "src": "explicit"|"chat"}]
        # 聊天中提炼/主人明确要求记住的事实（按时间从旧到新）
        self.facts: list = []
        # {"YYYY-MM-DD": int} 当天已发起的 AI 提炼次数（节流用）
        self.extract_days: dict = {}
        self._last_hour_tag = ""
        self._load()

    # ---------- 持久化 ----------

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("works"), dict):
                self.works = data["works"]
            hours = data.get("hours")
            if isinstance(hours, list) and len(hours) == 24:
                self.hours = [int(x) for x in hours]
            if isinstance(data.get("days"), dict):
                self.days = data["days"]
            if isinstance(data.get("facts"), list):
                cleaned = []
                for item in data["facts"]:
                    if isinstance(item, dict) and item.get("text"):
                        cleaned.append({
                            "t": str(item.get("t", "")),
                            "text": str(item["text"])[:_FACT_MAX_LEN],
                            "src": str(item.get("src", "chat")),
                        })
                self.facts = cleaned[-_KEEP_FACTS:]
            if isinstance(data.get("extract_days"), dict):
                self.extract_days = {
                    str(k): int(v) for k, v in data["extract_days"].items()
                    if str(v).isdigit()
                }
                self._prune_extract_days()
        except (OSError, ValueError) as e:
            logger.warning(f"墨灵记忆文件损坏，将重建: {e}")

    def save(self):
        try:
            atomic_write_json(self._path, {
                "works": self.works,
                "hours": self.hours,
                "days": self.days,
                "facts": self.facts,
                "extract_days": self.extract_days,
            })
        except OSError as e:
            logger.warning(f"墨灵记忆保存失败: {e}")

    # ---------- 事实记忆（聊天 / 明确要求记住） ----------

    @staticmethod
    def _normalize_fact(text: str) -> str:
        text = re.sub(r"\s+", " ", (text or "").strip())
        # 去掉开头的列举符号 / 语气词
        text = re.sub(r"^[\-·•\*\d\.、，,：:]+", "", text).strip()
        if len(text) > _FACT_MAX_LEN:
            text = text[:_FACT_MAX_LEN].rstrip("，,、；;。 ")
        return text

    def add_fact(self, text: str, src: str = "chat") -> bool:
        """记住一条事实。重复/过短返回 False。新旧包含视为重复。"""
        fact = self._normalize_fact(text)
        if len(fact) < 4:
            return False
        for old in self.facts:
            ot = old.get("text", "")
            if not ot:
                continue
            # 互相包含即视为同一条（短的 4 字以下不做包含判断，防误伤）
            if fact == ot or (len(fact) >= 6 and len(ot) >= 6
                              and (fact in ot or ot in fact)):
                return False
        self.facts.append({
            "t": datetime.now().isoformat(timespec="seconds"),
            "text": fact,
            "src": src if src in ("explicit", "chat") else "chat",
        })
        if len(self.facts) > _KEEP_FACTS:
            self.facts = self.facts[-_KEEP_FACTS:]
        self.save()
        return True

    def clear_facts(self):
        self.facts = []
        self.save()

    def fact_count(self) -> int:
        return len(self.facts)

    # ---------- AI 提炼节流 ----------

    def _prune_extract_days(self):
        if len(self.extract_days) <= _KEEP_EXTRACT_DAYS:
            return
        keep = sorted(self.extract_days.keys(), reverse=True)[:_KEEP_EXTRACT_DAYS]
        self.extract_days = {k: self.extract_days[k] for k in keep}

    def extract_count_today(self) -> int:
        return int(self.extract_days.get(date.today().isoformat(), 0))

    def bump_extract_today(self):
        key = date.today().isoformat()
        self.extract_days[key] = self.extract_days.get(key, 0) + 1
        self._prune_extract_days()
        self.save()

    # ---------- 事件 ----------

    def note_work(self, title: str):
        """记住主人在写的作品（去重、更新最近日期）。"""
        title = (title or "").strip()
        if not title:
            return
        today = date.today().isoformat()
        if title not in self.works:
            self.works[title] = {"last": today}
            # 超出上限：淘汰最久没碰的
            if len(self.works) > _KEEP_WORKS:
                oldest = sorted(
                    self.works.items(),
                    key=lambda kv: kv[1].get("last", ""))[0][0]
                self.works.pop(oldest, None)
            self.save()
        elif self.works[title].get("last") != today:
            self.works[title]["last"] = today
            self.save()

    def note_active_hour(self):
        """记录当前小时在写作（同一小时只记一次）。"""
        now = datetime.now()
        tag = now.strftime("%Y-%m-%d-%H")
        if tag == self._last_hour_tag:
            return
        self._last_hour_tag = tag
        self.hours[now.hour] = int(self.hours[now.hour]) + 1
        self.save()

    def note_care(self):
        """今日与墨灵互动 +1（用于每日回顾）。"""
        key = date.today().isoformat()
        bucket = self.days.setdefault(
            key, {"words": 0, "minutes": 0, "cares": 0})
        bucket["cares"] = int(bucket.get("cares", 0)) + 1
        self._prune_days()

    def settle_today(self, stats):
        """把今天的码字统计快照进每日回顾（启动/退出/换天时调用）。"""
        if stats is None:
            return
        key = date.today().isoformat()
        bucket = self.days.setdefault(
            key, {"words": 0, "minutes": 0, "cares": 0})
        try:
            bucket["words"] = int(stats.today_words())
            bucket["minutes"] = int(stats.today_minutes())
        except Exception:
            return
        self._prune_days()
        self.save()

    def _prune_days(self):
        if len(self.days) <= _KEEP_DAYS:
            return
        keep = sorted(self.days.keys(), reverse=True)[:_KEEP_DAYS]
        self.days = {k: self.days[k] for k in keep}

    # ---------- 渲染成提示词 ----------

    def render_persona_block(self) -> str:
        lines = ["【你记得关于主人的事】（长期记忆，自然地运用，"
                 "不要像报档案一样逐条念）"]

        # 最近在写的作品
        if self.works:
            recent = sorted(
                self.works.items(),
                key=lambda kv: kv[1].get("last", ""),
                reverse=True)[:3]
            names = "、".join(f"《{k}》" for k, _ in recent)
            lines.append(f"- 主人近期在写的作品：{names}")

        # 写作时段偏好（样本太少不说）
        total_active = sum(self.hours)
        if total_active >= 3:
            period_scores = {"清晨/上午": 0, "中午": 0, "下午": 0,
                             "晚上": 0, "深夜": 0}
            for h, n in enumerate(self.hours):
                period_scores[_period_of_hour(h)] += n
            top_period = max(period_scores, key=period_scores.get)
            if period_scores[top_period] >= total_active * 0.4:
                lines.append(
                    f"- 主人似乎最常在{top_period}写作，"
                    "这个时段可以多配合、少打扰")

        # 最近三天回顾
        recent_days = sorted(self.days.keys(), reverse=True)[:3]
        for dkey in reversed(recent_days):
            b = self.days[dkey]
            w = int(b.get("words", 0))
            m = int(b.get("minutes", 0))
            cares = int(b.get("cares", 0))
            if w <= 0 and cares == 0:
                continue
            md = dkey[5:]  # MM-DD
            parts = [f"{md} 写了 {w:,} 字"]
            if m > 0:
                parts.append(f"活跃 {m} 分钟")
            if cares > 0:
                parts.append(f"和你互动 {cares} 次")
            lines.append("- 近日回顾：" + "，".join(parts))

        # 对话中沉淀下来的事实（偏好/计划/设定）
        if self.facts:
            recent_facts = self.facts[-_RENDER_FACTS:]
            for item in recent_facts:
                text = item.get("text", "").strip()
                if text:
                    lines.append(f"- {text}")

        if len(lines) == 1:
            return ""  # 还没有任何记忆，不注入空段落
        lines.append("- 如果主人提到计划或目标，记住它并在之后自然跟进；"
                     "但不要编造记忆里没有的事。")
        return "\n".join(lines)
