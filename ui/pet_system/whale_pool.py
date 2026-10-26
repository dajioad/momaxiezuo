# ui/pet_system/whale_pool.py
"""蓝鲸大肥鱼动画池 —— 移植 dsh-pet 的分类动画系统。

动画素材位于 ui/pet/whale/anims/{key}/（WebP 帧序列）。
本模块只负责"按场景选哪个动画"，不负责加载/播放。
"""
from __future__ import annotations

import random
from typing import List, Optional

# ---------- 动画分类池 ----------

# 待机（循环播放）
IDLE = ["idle"]

# 点击回应（被点击时随机抽 1）
CLICKS = [
    "click_tsundere", "click_wave", "click_shy",
    "click_happy", "click_tickle",
]

# 拖拽
DRAG = ["drag"]

# 移动（漫游时随机抽 1）
MOVES = ["move_crab", "move_float", "move_run"]

# 转向 / 张望
TURN = ["look_around", "surprised"]

# 随机动作分类（带权重）—— dsh-pet 的 categories
CATEGORIES = [
    # (权重, [动画 key 列表])
    (3, ["work_coding", "work_thinking", "work_typing", "work_notes",
         "work_deepthought"]),          # 工作类（写作 app 高频）
    (2, ["eat_rice", "eat_snack", "eat_icecream", "eat_watermelon",
         "eat_token"]),                  # 吃
    (2, ["daily_yawn", "daily_stretch", "daily_startled", "daily_mirror",
         "daily_brush", "daily_curtsy", "daily_spin"]),  # 日常
    (2, ["fun_animals", "fun_butterflies", "fun_flower", "fun_cat",
         "fun_dove", "fun_cards", "fun_balloon", "fun_tailslap",
         "fun_jump", "fun_squash", "fun_tapdesk"]),  # 趣味
    (2, ["game_gomoku", "game_juggle", "game_top", "game_gun",
         "game_shuttle", "game_horse", "game_swing", "game_car",
         "game_cube", "game_rage"]),     # 游戏
    (2, ["music_maid", "music_otaku", "music_sway", "music_flute",
         "music_violin", "music_hum"]),  # 音乐
    (1, ["special_bubbles", "special_appear"]),  # 鲸鱼特效
    (1, ["whisper_daze", "whisper_screen", "whisper_clean"]),  # 碎碎念
    (1, ["balance_normal", "balance_coins", "balance_full"]),  # 余额
]

# 工作状态 6 档（0=空闲 → 5=庆祝）
WORK_STATUS = [
    "work_pacing",      # 0 踱步张望（空闲）
    "work_thinking",    # 1 思考冒泡
    "work_typing",      # 2 忙碌点按
    "work_archiving",   # 3 清点归档
    "work_stressed",    # 4 垂头叹气
    "work_celebrate",   # 5 雀跃庆祝
]

# 碎碎念
WHISPERS = ["whisper_daze", "whisper_screen", "whisper_clean"]

# 余额 6 档
BALANCE = [
    "balance_broke",      # 0 分文不剩
    "balance_empty",      # 1 袋空如洗
    "balance_counting",   # 2 数金皱眉
    "balance_normal",     # 3 钱袋如常
    "balance_coins",      # 4 金袋叮当
    "balance_full",       # 5 钱袋满溢
]

# ---------- 旧 action_key -> 新动画 key 的映射 ----------
# 墨码原有的喂食/喝水/清洁/玩耍/睡觉/摸头动作，映射到蓝鲸动画
ACTION_ANIM_MAP = {
    "feed":   ["eat_rice", "eat_snack", "eat_breakfast", "eat_lunch",
               "eat_dinner", "eat_token"],
    "drink":  ["eat_watermelon", "eat_icecream"],
    "pet":    ["click_tickle", "click_happy", "click_shy"],
    "clean":  ["daily_brush", "daily_mirror"],
    "play":   ["game_swing", "game_car", "game_cube", "game_juggle",
               "fun_jump", "music_otaku"],
    "sleep":  ["daily_sleep"],
    "wake":   ["idle"],
}


def pick(pool: List[str], exclude: Optional[str] = None) -> str:
    """从池中随机抽一个，尽量不与上一个重复。"""
    if not pool:
        return "idle"
    if len(pool) == 1:
        return pool[0]
    candidates = [a for a in pool if a != exclude] or pool
    return random.choice(candidates)


def pick_idle(exclude: Optional[str] = None) -> str:
    return pick(IDLE, exclude)


def pick_click(exclude: Optional[str] = None) -> str:
    return pick(CLICKS, exclude)


def pick_move(exclude: Optional[str] = None) -> str:
    return pick(MOVES, exclude)


def pick_turn(exclude: Optional[str] = None) -> str:
    return pick(TURN, exclude)


def pick_drag() -> str:
    return DRAG[0]


def pick_action(exclude: Optional[str] = None) -> str:
    """按权重从随机动作分类里抽一个动画。"""
    total = sum(w for w, _ in CATEGORIES)
    r = random.uniform(0, total)
    acc = 0.0
    for w, anims in CATEGORIES:
        acc += w
        if r <= acc:
            return pick(anims, exclude)
    return pick(CATEGORIES[0][1], exclude)


def pick_work_status(level: int, exclude: Optional[str] = None) -> str:
    """level: 0..5"""
    level = max(0, min(5, int(level)))
    return pick([WORK_STATUS[level]], exclude) if exclude else WORK_STATUS[level]


def pick_whisper(exclude: Optional[str] = None) -> str:
    return pick(WHISPERS, exclude)


def pick_balance(level: int, exclude: Optional[str] = None) -> str:
    level = max(0, min(5, int(level)))
    return pick([BALANCE[level]], exclude) if exclude else BALANCE[level]


def anim_for_action(action_key: str, exclude: Optional[str] = None) -> str:
    """旧动作 key → 蓝鲸动画 key（随机抽一个对应池）。"""
    pool = ACTION_ANIM_MAP.get(action_key)
    if not pool:
        return "idle"
    return pick(pool, exclude)
