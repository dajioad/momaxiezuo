# ui/pet_system/economy.py
"""墨码写作 - 墨灵养成经济系统。

为什么要有经济系统：
- 道具无限免费点击 → 互动三天就腻，养成没有"稀缺感"
- 写作产出「墨玉」→ 买零食/玩具 → 照顾墨灵 → 亲密度，
  把"写作"和"养宠"两个循环咬合成一个飞轮

数据文件 pet_economy.json（与 pet_data.json 同目录）：
{
  "coins": 60,
  "inventory": {"drink": 5, "feed": 5, "clean": 3, "play": 3},
  "daily": {每日进度 + 已领取任务},
  "achievements": ["已解锁成就 id"],
  "stats": {"total_cares": 0}
}

设计原则：
- 所有奖励都有每日上限，防止刷
- 任务达成即自动领取（轻量正反馈，不逼用户点按钮）
- 纯数据层 + Qt 信号，UI 只订阅不计算
"""

from __future__ import annotations

import os
from datetime import date, datetime

from PySide6.QtCore import QObject, Signal

from utils.logger import get_logger
from utils.work_store import atomic_write_json
from . import constants as C

logger = get_logger()

_ECONOMY_FILE = "pet_economy.json"

# ---------- 商店物品 ----------
# key 与 actions.ACTIONS 的 key 对齐；pet / sleep 是免费动作，不卖
SHOP_ITEMS = {
    "drink": {
        "name": "山泉水", "icon": "fa5s.tint",
        "price": 2, "desc": "水分 +40 · 心情 +3",
    },
    "feed": {
        "name": "小鱼干", "icon": "fa5s.fish",
        "price": 5, "desc": "饱腹 +35 · 心情 +5",
    },
    "clean": {
        "name": "香氛浴", "icon": "fa5s.bath",
        "price": 8, "desc": "清洁 +40 · 心情 +3",
    },
    "play": {
        "name": "毛线球", "icon": "fa5s.baseball-ball",
        "price": 6, "desc": "心情 +20 · 消耗一点精力",
    },
}

# 初始状态（新用户首次建档）
_START_COINS = 60
_START_INVENTORY = {"drink": 5, "feed": 5, "clean": 3, "play": 3}

# ---------- 墨玉获取规则 ----------
COINS_PER_100_WORDS = 1          # 每 100 字 1 墨玉
COINS_DAILY_WORDS_CAP = 50       # 每天写作最多赚 50（=5000 字）
COINS_PER_CARE = 1               # 每次道具互动 1 墨玉
COINS_DAILY_CARE_CAP = 10        # 每天互动最多赚 10

# ---------- 每日任务 ----------
# metric: words / cares / milestone
DAILY_TASKS = [
    {"id": "write500", "name": "今日码字 500",
     "goal": 500, "reward": 15, "metric": "words",
     "desc": "写 500 字即可，重要的是开始"},
    {"id": "care3", "name": "陪伴墨灵 3 次",
     "goal": 3, "reward": 10, "metric": "cares",
     "desc": "摸头、喂食或一起玩都算"},
    {"id": "milestone", "name": "拿下一个千字",
     "goal": 1, "reward": 10, "metric": "milestone",
     "desc": "今日字数突破任意一个千字档"},
]

# ---------- 成就 ----------
# checker(economy, ctx) -> bool；ctx 含 stats / brain_state
ACHIEVEMENTS = [
    {"id": "first_pet", "name": "初次见面",
     "desc": "第一次摸墨灵的头", "reward": 5,
     "check": lambda e, ctx: ctx.brain.total_pet > 0},
    {"id": "first_feed", "name": "投喂官",
     "desc": "第一次给墨灵喂食", "reward": 5,
     "check": lambda e, ctx: ctx.brain.total_feed > 0},
    {"id": "toss", "name": "飞起来咯",
     "desc": "把墨灵抛出去一次", "reward": 8,
     "check": lambda e, ctx: e._flags.get("thrown", False)},
    {"id": "night_owl", "name": "夜战派",
     "desc": "在 23 点后还在码字", "reward": 8,
     "check": lambda e, ctx: e._daily.get("night", False)},
    {"id": "coins_200", "name": "小有积蓄",
     "desc": "同时持有 200 墨玉", "reward": 0,
     "check": lambda e, ctx: e.coins >= 200},
    {"id": "words_10k", "name": "万字户",
     "desc": "累计写作 1 万字", "reward": 20,
     "check": lambda e, ctx: ctx.total_words >= 10000},
    {"id": "words_100k", "name": "十万字大神",
     "desc": "累计写作 10 万字", "reward": 50,
     "check": lambda e, ctx: ctx.total_words >= 100000},
    {"id": "streak_7", "name": "周更达人",
     "desc": "连续 7 天写作", "reward": 30,
     "check": lambda e, ctx: ctx.streak_days >= 7},
    {"id": "level_2", "name": "渐渐熟悉",
     "desc": "与墨灵达到「熟悉」阶段", "reward": 15,
     "check": lambda e, ctx: ctx.brain.intimacy >= C.LEVELS[1][0]},
    {"id": "level_4", "name": "无可取代",
     "desc": "与墨灵达到「挚友」阶段", "reward": 60,
     "check": lambda e, ctx: ctx.brain.intimacy >= C.LEVELS[3][0]},
]


class _EvalCtx:
    """成就判定上下文（避免 checker 直接依赖 workspace）。"""

    def __init__(self, brain=None, stats=None):
        # 成就 checker 读 total_pet / total_feed / intimacy，
        # 这些字段都在 PetState 上（brain.state）
        self.brain = getattr(brain, "state", None)
        self.stats = stats
        self.total_words = stats.total_words() if stats else 0
        self.streak_days = stats.streak_days() if stats else 0


class PetEconomy(QObject):
    """墨玉 / 背包 / 任务 / 成就。"""

    coins_changed = Signal(int)
    inventory_changed = Signal()
    # (标题, 说明, 墨玉数)
    reward_granted = Signal(str, str, int)
    # 成就 dict
    achievement_unlocked = Signal(dict)

    def __init__(self, data_root: str, brain=None, stats=None,
                 parent=None):
        super().__init__(parent)
        self._path = os.path.join(data_root, _ECONOMY_FILE)
        self._brain = brain
        self._stats = stats

        self.coins = 0
        self.inventory: dict = {}
        self._daily: dict = {}
        self._unlocked: list = []
        self._persistent_stats: dict = {}
        self._flags: dict = {}      # 成就用的一次性标记（随档保存）
        self._load()
        self._ensure_day()

    # ==================== 持久化 ====================

    def _default_data(self) -> dict:
        return {
            "version": 1,
            "coins": _START_COINS,
            "inventory": dict(_START_INVENTORY),
            "daily": {},
            "achievements": [],
            "stats": {"total_cares": 0},
            "flags": {},
        }

    def _load(self):
        data = self._default_data()
        if os.path.exists(self._path):
            try:
                import json
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    data.update(saved)
            except (OSError, ValueError) as e:
                logger.warning(f"墨灵经济存档损坏，重建: {e}")
        self.coins = int(data.get("coins", _START_COINS))
        inv = data.get("inventory") or {}
        self.inventory = {
            k: int(inv.get(k, v)) for k, v in _START_INVENTORY.items()}
        self._daily = data.get("daily") or {}
        self._unlocked = [str(x) for x in (data.get("achievements") or [])]
        self._persistent_stats = data.get("stats") or {}
        self._flags = data.get("flags") or {}

    def save(self):
        try:
            atomic_write_json(self._path, {
                "version": 1,
                "coins": int(self.coins),
                "inventory": dict(self.inventory),
                "daily": dict(self._daily),
                "achievements": list(self._unlocked),
                "stats": dict(self._persistent_stats),
                "flags": dict(self._flags),
            })
        except OSError as e:
            logger.warning(f"墨灵经济存档保存失败: {e}")

    # ==================== 每日重置 ====================

    def _ensure_day(self):
        today = date.today().isoformat()
        if self._daily.get("date") != today:
            self._daily = {
                "date": today,
                "words": 0,
                "cares": 0,
                "milestone": False,
                "night": False,
                "earned_words": 0,
                "earned_cares": 0,
                "claimed_tasks": [],
            }
            self.save()

    # ==================== 查询 ====================

    def inventory_count(self, key: str) -> int:
        if key not in SHOP_ITEMS:
            return -1  # 非消耗品（摸头/睡觉）
        return int(self.inventory.get(key, 0))

    def daily_progress(self) -> dict:
        self._ensure_day()
        return dict(self._daily)

    def is_achievement_unlocked(self, ach_id: str) -> bool:
        return ach_id in self._unlocked

    def task_status(self) -> list:
        """每日任务当前状态（供 UI）：[{task, current, done, claimed}]"""
        self._ensure_day()
        out = []
        for t in DAILY_TASKS:
            if t["metric"] == "words":
                cur = int(self._daily.get("words", 0))
            elif t["metric"] == "cares":
                cur = int(self._daily.get("cares", 0))
            else:
                cur = 1 if self._daily.get("milestone") else 0
            claimed = t["id"] in self._daily.get("claimed_tasks", [])
            out.append({"task": t, "current": min(cur, t["goal"]),
                        "done": cur >= t["goal"], "claimed": claimed})
        return out

    # ==================== 商店 ====================

    def buy(self, key: str, qty: int = 1) -> tuple:
        """返回 (成功, 提示)。"""
        item = SHOP_ITEMS.get(key)
        if item is None or qty <= 0:
            return False, "没有这件商品"
        cost = item["price"] * qty
        if self.coins < cost:
            return False, f"墨玉不够（需要 {cost}）"
        self.coins -= cost
        self.inventory[key] = self.inventory.get(key, 0) + qty
        self.coins_changed.emit(self.coins)
        self.inventory_changed.emit()
        self.save()
        self.evaluate()
        return True, f"买到了 {item['name']} ×{qty}"

    def consume(self, key: str) -> bool:
        """使用一个消耗品。库存 0 返回 False（UI 应提前拦截）。"""
        if key not in SHOP_ITEMS:
            return True  # 摸头 / 睡觉不消耗
        n = self.inventory.get(key, 0)
        if n <= 0:
            return False
        self.inventory[key] = n - 1
        self.inventory_changed.emit()
        self.save()
        return True

    # ==================== 行为事件 ====================

    def on_words(self, delta_words: int):
        """净增字数（workspace flush_save 时调用）。"""
        if delta_words <= 0:
            return
        self._ensure_day()
        self._daily["words"] = int(self._daily.get("words", 0)) \
            + int(delta_words)

        # 23 点后写作 → 夜战标记
        if datetime.now().hour >= 23:
            self._daily["night"] = True

        # 写作墨玉（每日封顶）
        budget = max(0, COINS_DAILY_WORDS_CAP
                     - int(self._daily.get("earned_words", 0)))
        earned = min(budget,
                     int(delta_words) // 100 * COINS_PER_100_WORDS)
        if earned > 0:
            self._grant_coins(earned)
            self._daily["earned_words"] = int(
                self._daily.get("earned_words", 0)) + earned
        self.save()
        self.evaluate()

    def on_care(self, action_key: str):
        """使用道具 / 摸头（brain.action_started 时调用）。"""
        self._ensure_day()
        if action_key not in ("pet", "feed", "drink", "clean", "play"):
            return
        self._daily["cares"] = int(self._daily.get("cares", 0)) + 1
        self._persistent_stats["total_cares"] = int(
            self._persistent_stats.get("total_cares", 0)) + 1

        budget = max(0, COINS_DAILY_CARE_CAP
                     - int(self._daily.get("earned_cares", 0)))
        if budget > 0:
            self._grant_coins(COINS_PER_CARE)
            self._daily["earned_cares"] = int(
                self._daily.get("earned_cares", 0)) + COINS_PER_CARE
        self.save()
        self.evaluate()

    def on_milestone(self):
        """今日字数破千字档（workspace 庆祝里程碑时调用）。"""
        self._ensure_day()
        if not self._daily.get("milestone"):
            self._daily["milestone"] = True
            self.save()
            self.evaluate()

    def mark_thrown(self):
        if not self._flags.get("thrown"):
            self._flags["thrown"] = True
            self.save()
            self.evaluate()

    # ==================== 奖励 / 成就判定 ====================

    def _grant_coins(self, n: int):
        self.coins += int(n)
        self.coins_changed.emit(self.coins)

    def evaluate(self):
        """检查每日任务 + 成就，达成即自动发放。可在任何事件后调用。"""
        self._ensure_day()
        ctx = _EvalCtx(self._brain, self._stats)

        # --- 每日任务 ---
        for st in self.task_status():
            t = st["task"]
            if st["done"] and not st["claimed"]:
                self._daily.setdefault("claimed_tasks", []).append(t["id"])
                self._grant_coins(t["reward"])
                self.reward_granted.emit(
                    f"每日任务 · {t['name']}", t["desc"], t["reward"])

        # --- 成就 ---
        for ach in ACHIEVEMENTS:
            if ach["id"] in self._unlocked:
                continue
            try:
                hit = bool(ach["check"](self, ctx))
            except Exception:
                hit = False
            if hit:
                self._unlocked.append(ach["id"])
                if ach["reward"] > 0:
                    self._grant_coins(ach["reward"])
                self.achievement_unlocked.emit(ach)

        self.save()

    def attach_sources(self, brain, stats):
        """延迟注入数据源（workspace 里 brain/stats 先于 economy 创建时用）。"""
        self._brain = brain
        self._stats = stats
