# ui/pet_system/actions.py
"""墨码写作 - 宠物动作定义。

每个动作 = 对 state 的改动 + 动画 key + 气泡文案 + 时长 + 前置条件。

设计原则：
- 动作是"数据"，不是"逻辑"。行为决策在 brain 里。
- effect 是纯函数：接收 PetState，就地修改。
- guard 返回 False 时动作不可用（如精力太低不能玩耍）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .state import PetState


@dataclass
class Action:
    key: str                          # 内部标识：feed / pet / clean / play / sleep
    label: str                        # 显示名："喂食"
    duration_ms: int                  # 播放时长（0 = 长时状态，由 brain 特殊处理）
    anim_key: str                     # 素材目录名（默认同 key）
    bubbles: List[str] = field(default_factory=list)
    effect: Optional[Callable[[PetState], None]] = None
    guard: Optional[Callable[[PetState], bool]] = None
    hint: str = ""                    # 右键菜单 tooltip

    def can_run(self, state: PetState) -> bool:
        if self.guard is None:
            return True
        try:
            return bool(self.guard(state))
        except Exception:
            return True

    def run_effect(self, state: PetState):
        if self.effect is None:
            return
        try:
            self.effect(state)
        except Exception:
            pass


# ---------- 具体动作 ----------

ACTIONS = {
    "feed": Action(
        key="feed",
        label="喂食",
        duration_ms=4000,       # 2.5s → 4s
        anim_key="eating",
        bubbles=["谢谢主人~", "呜呜好好吃", "最喜欢主人了"],
        effect=lambda s: s.feed(),
        hint="饱腹度 +35 · 心情 +5",
    ),

    "drink": Action(
        key="drink",
        label="喝水",
        duration_ms=3500,       # 2s → 3.5s
        anim_key="drinking",
        bubbles=["咕嘟咕嘟…", "好解渴", "谢谢主人"],
        effect=lambda s: s.drink(),
        hint="水分 +40 · 心情 +3",
    ),

    "pet": Action(
        key="pet",
        label="摸摸头",
        duration_ms=3000,       # 1.8s → 3s
        anim_key="pet_head",
        bubbles=["喵~", "舒服…", "再摸摸"],
        effect=lambda s: s.pet_head(),
        hint="心情 +10",
    ),

    "clean": Action(
        key="clean",
        label="清洁",
        duration_ms=4500,       # 3s → 4.5s
        anim_key="cleaning",
        bubbles=["洗香香~", "泡沫好多…", "干净了"],
        effect=lambda s: s.clean(),
        hint="清洁度 +40 · 心情 +3",
    ),

    "play": Action(
        key="play",
        label="玩耍",
        duration_ms=5000,       # 3.5s → 5s
        anim_key="playing",
        bubbles=["嘿嘿嘿", "再玩一会儿嘛", "好好玩~"],
        effect=lambda s: s.play(),
        guard=lambda s: s.energy >= 30.0,
        hint="心情 +20 · 精力 -10 · 饱腹 -5（精力 < 30 不可用）",
    ),

    # sleep 比较特殊：不是"一次性动作"，而是"进入状态"。
    # duration_ms=0 表示由 brain 维护持续状态（默认 30 秒后自动醒）。
    "sleep": Action(
        key="sleep",
        label="睡觉",
        duration_ms=0,
        anim_key="sleeping",
        bubbles=["晚安…", "Zzz…"],
        effect=None,  # 精力恢复由 brain 在睡眠期间持续 tick
        hint="精力恢复（进入睡眠状态）",
    ),

    "wake": Action(
        key="wake",
        label="叫醒",
        duration_ms=1000,
        anim_key="idle",
        bubbles=["唔…醒了"],
        effect=None,
    ),
}


# 动作 key → 显示名
def action_label(key: str) -> str:
    a = ACTIONS.get(key)
    return a.label if a else key