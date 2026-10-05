# ui/pet_system/state.py
"""墨码写作 - 宠物状态数据模型。

纯数据 + 计算，不依赖 Qt。所有生理值归一化到 0-100。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, date

from . import constants as C


@dataclass
class PetState:
    """宠物的全部状态。序列化后即为 pet_data.json 的 state 字段。"""

    # ---- 生理值（0-100）----
    hunger: float = C.INIT_HUNGER          # 饱腹度：100 很饱 / 0 很饿
    thirst: float = C.INIT_THIRST          # 水分：100 不渴 / 0 口干舌燥
    energy: float = C.INIT_ENERGY          # 精力：100 精神 / 0 累趴
    cleanliness: float = C.INIT_CLEANLINESS  # 清洁度
    mood: float = C.INIT_MOOD              # 心情

    # ---- 长期值 ----
    intimacy: int = 0                       # 亲密度（累计）

    # ---- 计数 ----
    total_feed: int = 0
    total_pet: int = 0
    total_play: int = 0
    total_clean: int = 0
    total_sleep_hours: float = 0.0

    # ---- 时间戳 / 每日计数 ----
    last_update: str = ""                   # ISO 格式，用于离线补算
    last_interact: str = ""                 # ISO 格式，上次与主人互动
    writing_bonus_date: str = ""            # 当日写作亲密度计数归属日（YYYY-MM-DD）
    writing_bonus_today: int = 0            # 今日已通过写作获得的亲密度

    # ---- 存档迁移标记 ----
    # 老版本把击键数存在 config.ini 的 pet_intimacy_total，
    # 首次读到新体系时一次性折算亲密度，之后永不再折算。
    legacy_typing_migrated: bool = False

    # ==================== 状态判断 ====================

    @property
    def is_hungry(self) -> bool:
        return self.hunger < C.THRESHOLD_HUNGRY

    @property
    def is_thirsty(self) -> bool:
        return self.thirst < C.THRESHOLD_THIRSTY

    @property
    def is_tired(self) -> bool:
        return self.energy < C.THRESHOLD_TIRED

    @property
    def is_dirty(self) -> bool:
        return self.cleanliness < C.THRESHOLD_DIRTY

    @property
    def is_sad(self) -> bool:
        return self.mood < C.THRESHOLD_SAD

    def current_need(self) -> str:
        """返回当前最紧迫的需求（用于 Brain 决策）。"""
        # 优先级：渴 > 饿 > 累 > 脏 > 心情
        if self.is_thirsty:
            return C.NEED_THIRSTY
        if self.is_hungry:
            return C.NEED_HUNGRY
        if self.is_tired:
            return C.NEED_TIRED
        if self.is_dirty:
            return C.NEED_DIRTY
        if self.is_sad:
            return C.NEED_SAD
        return C.NEED_NONE

    @property
    def level(self) -> int:
        return C.level_from_intimacy(self.intimacy)[0]

    @property
    def level_name(self) -> str:
        return C.level_from_intimacy(self.intimacy)[1]

    @property
    def level_next_threshold(self):
        return C.level_from_intimacy(self.intimacy)[2]

    # ==================== 时间推进 ====================

    def tick(self, dt_hours: float, sleeping: bool = False):
        """按经过的小时数衰减状态。

        sleeping=True 时：energy 恢复而非衰减，mood 缓慢恢复。
        """
        if dt_hours <= 0:
            return
        self.hunger = max(C.STAT_MIN,
                          self.hunger - C.DECAY_HUNGER * dt_hours)
        self.thirst = max(C.STAT_MIN,
                          self.thirst - C.DECAY_THIRST * dt_hours)
        self.cleanliness = max(
            C.STAT_MIN,
            self.cleanliness - C.DECAY_CLEANLINESS * dt_hours)

        if sleeping:
            self.energy = min(
                C.STAT_MAX,
                self.energy + C.RECOVER_ENERGY_SLEEP * dt_hours)
            self.mood = min(
                C.STAT_MAX,
                self.mood + C.RECOVER_MOOD_SLEEP * dt_hours)
            self.total_sleep_hours += dt_hours
        else:
            self.energy = max(
                C.STAT_MIN,
                self.energy - C.DECAY_ENERGY * dt_hours)
            self.mood = max(
                C.STAT_MIN,
                self.mood - C.DECAY_MOOD * dt_hours)

        self.last_update = datetime.now().isoformat(timespec="seconds")

    def apply_offline_decay(self):
        """按 last_update 到现在的时间补算衰减。

        处理"关机了 3 天再打开"的场景：
        - 前 8 小时按睡眠算（恢复精力）
        - 之后的清醒时间按正常衰减
        - 超过 30 天：直接重置（避免"走了 3 个月回来猫饿死"）
        """
        if not self.last_update:
            return
        try:
            last = datetime.fromisoformat(self.last_update)
        except (ValueError, TypeError):
            return
        now = datetime.now()
        dt_hours = (now - last).total_seconds() / 3600.0

        if dt_hours <= 0:
            return

        if dt_hours > 24 * 30:
            # 太久了：重置到初始状态（但保留亲密度/等级）
            self.hunger = C.INIT_HUNGER
            self.energy = C.INIT_ENERGY
            self.cleanliness = C.INIT_CLEANLINESS
            self.mood = C.INIT_MOOD
            self.last_update = now.isoformat(timespec="seconds")
            return

        # 前 8 小时按睡眠算
        sleep_hours = min(dt_hours, 8.0)
        awake_hours = max(0.0, dt_hours - sleep_hours)
        if sleep_hours > 0:
            self.tick(sleep_hours, sleeping=True)
        if awake_hours > 0:
            self.tick(awake_hours, sleeping=False)

        # 亲密度缓慢衰减（长时间不互动的惩罚）
        days_offline = dt_hours / 24.0
        if days_offline >= 1:
            decay = int(C.INTIMACY_DECAY_PER_DAY * days_offline)
            floor = int(self._level_floor() * C.INTIMACY_DECAY_FLOOR_RATIO)
            self.intimacy = max(floor, self.intimacy - decay)

    def _level_floor(self) -> int:
        """返回当前等级的门槛值（用于亲密度衰减保底）。"""
        lv = self.level
        return C.LEVELS[lv - 1][0] if 1 <= lv <= len(C.LEVELS) else 0

    # ==================== 互动 ====================

    def touch_interact(self):
        """记录"刚刚和主人互动过"。喂食/抚摸/对话等动作都应调用。"""
        self.last_interact = datetime.now().isoformat(timespec="seconds")

    def hours_since_interact(self) -> float:
        """距上次互动的小时数。从未互动过返回 -1。"""
        if not self.last_interact:
            return -1.0
        try:
            last = datetime.fromisoformat(self.last_interact)
        except (ValueError, TypeError):
            return -1.0
        return max(0.0, (datetime.now() - last).total_seconds() / 3600.0)

    def add_intimacy(self, n: int):
        """亲密度只增不减的对外入口（负向调整走离线衰减）。"""
        if n > 0:
            self.intimacy += int(n)

    def apply_writing_bonus(self, delta_words: int):
        """主人码字的陪伴奖励：亲密度（每日封顶）+ 少量心情恢复。

        - 每 WRITING_WORDS_PER_POINT 字 = 1 点亲密度
        - 每天最多 WRITING_INTIMACY_DAILY_CAP 点，防刷
        - 每 1000 字心情 +2，单次最多 +10
        """
        if delta_words <= 0:
            return
        today = date.today().strftime("%Y-%m-%d")
        if self.writing_bonus_date != today:
            self.writing_bonus_date = today
            self.writing_bonus_today = 0

        budget = max(0, C.WRITING_INTIMACY_DAILY_CAP
                     - self.writing_bonus_today)
        gained = min(budget,
                     delta_words // C.WRITING_WORDS_PER_POINT)
        if gained > 0:
            self.intimacy += gained
            self.writing_bonus_today += gained

        mood_gain = min(C.WRITING_MOOD_GAIN_CAP,
                        delta_words / 1000.0 * C.WRITING_MOOD_PER_1000)
        if mood_gain > 0:
            self.mood = min(C.STAT_MAX, self.mood + mood_gain)

    def migrate_legacy_typing(self, total_chars: int) -> bool:
        """把老版本 config.ini 里的击键总数一次性折算成亲密度。

        只执行一次（legacy_typing_migrated 落盘）。
        返回是否发生了迁移。
        """
        if self.legacy_typing_migrated:
            return False
        self.legacy_typing_migrated = True
        if total_chars > 0:
            bonus = min(
                C.LEGACY_TYPING_MIGRATE_CAP,
                int(total_chars) // 100
                * C.LEGACY_TYPING_POINTS_PER_100,
            )
            self.intimacy += bonus
        return True

    def feed(self):
        self.hunger = min(C.STAT_MAX,
                          self.hunger + C.EFFECT_FEED_HUNGER)
        self.mood = min(C.STAT_MAX,
                        self.mood + C.EFFECT_FEED_MOOD)
        self.intimacy += C.INTIMACY_PER_FEED
        self.total_feed += 1

    def drink(self):
        self.thirst = min(C.STAT_MAX,
                          self.thirst + C.EFFECT_DRINK_THIRST)
        self.mood = min(C.STAT_MAX,
                        self.mood + C.EFFECT_DRINK_MOOD)

    def pet_head(self):
        self.mood = min(C.STAT_MAX,
                        self.mood + C.EFFECT_PET_MOOD)
        self.intimacy += C.INTIMACY_PER_PET
        self.total_pet += 1

    def clean(self):
        self.cleanliness = min(
            C.STAT_MAX,
            self.cleanliness + C.EFFECT_CLEAN_CLEANLINESS)
        self.mood = min(C.STAT_MAX,
                        self.mood + C.EFFECT_CLEAN_MOOD)
        self.intimacy += C.INTIMACY_PER_CLEAN
        self.total_clean += 1

    def play(self):
        self.mood = min(C.STAT_MAX,
                        self.mood + C.EFFECT_PLAY_MOOD)
        self.energy = max(C.STAT_MIN,
                          self.energy + C.EFFECT_PLAY_ENERGY)
        self.hunger = max(C.STAT_MIN,
                          self.hunger + C.EFFECT_PLAY_HUNGER)
        self.intimacy += C.INTIMACY_PER_PLAY
        self.total_play += 1

    # ==================== 序列化 ====================

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PetState":
        st = cls()
        for k, v in (d or {}).items():
            if not hasattr(st, k):
                continue
            try:
                cur = getattr(st, k)
                if isinstance(cur, bool):
                    setattr(st, k, bool(v))
                elif isinstance(cur, int) and not isinstance(cur, bool):
                    setattr(st, k, int(v))
                elif isinstance(cur, float):
                    setattr(st, k, float(v))
                elif isinstance(cur, str):
                    setattr(st, k, str(v))
                else:
                    setattr(st, k, v)
            except (TypeError, ValueError):
                pass
        return st
