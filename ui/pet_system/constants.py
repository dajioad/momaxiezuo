# ui/pet_system/constants.py
"""墨码写作 - 宠物系统常量。

所有可调参数集中在这里，方便后续微调。
"""

# ---------- 状态值范围 ----------
STAT_MIN = 0.0
STAT_MAX = 100.0

# ---------- 初始值 ----------
INIT_HUNGER = 70.0
INIT_THIRST = 70.0
INIT_ENERGY = 80.0
INIT_CLEANLINESS = 80.0
INIT_MOOD = 70.0

# ---------- 衰减速率（每小时）----------
# 设计原则：一个活跃用户（每天用软件 2-3 小时）不需要频繁照顾；
# 但离线一两天后回来，需要互动一次。
# 注意：每个常量只允许定义一次（历史上这里曾被重复赋值，
# 旧值被静默覆盖，导致 8 小时就饿和 22 小时才饿两套体感并存）。
DECAY_HUNGER = 4.5        # 满 → 空：约 22 小时
DECAY_THIRST = 5.0        # 满 → 空：20 小时（比饿得更快一点）
DECAY_ENERGY = 2.0        # 满 → 空：50 小时（清醒时）
DECAY_CLEANLINESS = 1.5   # 满 → 空：约 67 小时
DECAY_MOOD = 1.0          # 满 → 空：100 小时（无交互时）

# ---------- 睡觉恢复速率（每小时）----------
RECOVER_ENERGY_SLEEP = 15.0   # 睡 6 小时左右恢复满
RECOVER_MOOD_SLEEP = 1.0

# ---------- 写作陪伴：写作时的心情恢复 ----------
# 主人认真码字时墨灵在旁边陪着也开心。按净增字数计，
# 在 PetState.apply_writing_bonus 里做上限钳制。
WRITING_WORDS_PER_POINT = 100     # 每 100 字 = 1 点亲密度
WRITING_INTIMACY_DAILY_CAP = 30   # 每天通过写作最多获得 30 点亲密度
WRITING_MOOD_PER_1000 = 2.0       # 每 1000 字心情 +2
WRITING_MOOD_GAIN_CAP = 10.0      # 每次写作奖励最多 +10 心情

# ---------- 阈值（低于此值触发"需求"）----------
THRESHOLD_HUNGRY = 30.0
THRESHOLD_THIRSTY = 30.0
THRESHOLD_TIRED = 25.0
THRESHOLD_DIRTY = 30.0
THRESHOLD_SAD = 30.0

# ---------- 动作效果 ----------
EFFECT_FEED_HUNGER = 35.0
EFFECT_FEED_MOOD = 5.0

EFFECT_DRINK_THIRST = 40.0
EFFECT_DRINK_MOOD = 3.0

EFFECT_PET_MOOD = 10.0

EFFECT_CLEAN_CLEANLINESS = 40.0
EFFECT_CLEAN_MOOD = 3.0

EFFECT_PLAY_MOOD = 20.0
EFFECT_PLAY_ENERGY = -10.0
EFFECT_PLAY_HUNGER = -5.0

# ---------- 亲密度增长（每次互动）----------
INTIMACY_PER_PET = 1
INTIMACY_PER_FEED = 2
INTIMACY_PER_PLAY = 3
INTIMACY_PER_CLEAN = 2

# ---------- 旧版本"按打字字符数"亲密度迁移 ----------
# 老版本在 config.ini 里累计击键次数 pet_intimacy_total，
# 与 pet_data.json 的亲密度是两套体系。首次启动时一次性折算进新体系：
# 每 100 次击键 = 1 点，最多补 2000 点，补完后旧计数清零。
LEGACY_TYPING_POINTS_PER_100 = 1
LEGACY_TYPING_MIGRATE_CAP = 2000

# ---------- 亲密度衰减 ----------
# 长时间不互动会缓慢下降（1 周不互动 -21），但不会跌破当前等级的起点
INTIMACY_DECAY_PER_DAY = 3
INTIMACY_DECAY_FLOOR_RATIO = 0.9  # 衰减不会跌破本等级起点 * 0.9

# ---------- 等级门槛 ----------
# (累计亲密度阈值, 显示名)
LEVELS = [
    (0,      "初识"),
    (500,    "熟悉"),
    (2000,   "亲密"),
    (5000,   "挚友"),
    (15000,  "相伴"),
]


def level_from_intimacy(n: int) -> tuple:
    """按亲密度返回 (等级索引, 等级名, 下一等级门槛)。

    等级索引从 1 开始。已是最高级时下一门槛为 None。
    """
    n = max(0, int(n))
    for i in range(len(LEVELS) - 1, -1, -1):
        if n >= LEVELS[i][0]:
            next_threshold = (LEVELS[i + 1][0]
                              if i + 1 < len(LEVELS) else None)
            return i + 1, LEVELS[i][1], next_threshold
    return 1, LEVELS[0][1], LEVELS[1][0]


# ---------- 状态键 ----------
STATE_HUNGER = "hunger"
STATE_THIRST = "thirst"
STATE_ENERGY = "energy"
STATE_CLEANLINESS = "cleanliness"
STATE_MOOD = "mood"

# ---------- 需求键（对应行为）----------
NEED_HUNGRY = "hungry"
NEED_THIRSTY = "thirsty"
NEED_TIRED = "tired"
NEED_DIRTY = "dirty"
NEED_SAD = "sad"
NEED_NONE = ""

# 需求 → 中文短描述（状态面板 / AI 上下文共用）
NEED_LABELS = {
    NEED_HUNGRY:  "饿了",
    NEED_THIRSTY: "渴了",
    NEED_TIRED:   "累了",
    NEED_DIRTY:   "想清洁",
    NEED_SAD:     "情绪低落",
    NEED_NONE:    "",
}

# ---------- 需求提醒节流 ----------
# brain 边沿触发：新需求出现立即提醒一次；同一需求持续未解决时，
# 最多每隔这么多秒重复提醒一次（默认 10 分钟）。
NEED_REMIND_INTERVAL_SEC = 600
