# ui/pet_system/brain.py
"""墨码写作 - 宠物行为引擎。

职责：
1. 每秒 tick：衰减状态（1s / 3600 = 小时数）
2. 消费用户动作队列（request_action 入队 → 依次执行）
3. 状态需求检测（饿了/累了 → emit need_detected，边沿触发 + 节流）
4. 通过 signal 通知展示层

不做的事：
- 不做绘图（展示层负责）
- 不做素材查找（展示层 + animations 负责）
- 不做文件读写（persistence 负责，但 brain 会在 flush 时调 save）
"""

from __future__ import annotations

import random
import time
from collections import deque
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from utils.logger import get_logger
from .actions import ACTIONS, Action
from .state import PetState
from . import constants as C
from .persistence import load_state, save_state

logger = get_logger()

# 睡眠默认时长（秒）
_SLEEP_DEFAULT_SEC = 30.0


class PetBrain(QObject):
    """宠物行为引擎。"""

    # 请求展示层播放某动作动画
    action_started = Signal(str, str)     # (action_key, anim_key)
    action_finished = Signal(str)         # action_key
    # 请求展示层显示气泡
    bubble_requested = Signal(str)
    # 状态重大变化（供状态面板刷新）
    state_changed = Signal()
    # 需求提醒（饿了/累了等）。边沿触发：新需求出现立刻提醒，
    # 同一需求持续未解决时按 NEED_REMIND_INTERVAL_SEC 重复。
    need_detected = Signal(str)
    # 进入/退出睡眠
    sleep_changed = Signal(bool)
    # 自主漫游时机到了（展示层决定怎么动）：
    # 用户在看但没在打字（阅读/思考）时，墨灵会自己溜达、发呆
    roam_pulse = Signal()

    # 用户安静多久后允许自主漫游（秒）
    ROAM_IDLE_GRACE = 45.0

    def __init__(self, data_root: str, parent=None):
        super().__init__(parent)
        self._data_root = data_root or ""
        self._state = load_state(data_root) if data_root else PetState()

        # 自主漫游
        self._roam_enabled = True
        self._presence_ts = time.monotonic()
        self._roam_timer = QTimer(self)
        self._roam_timer.setSingleShot(True)
        self._roam_timer.timeout.connect(self._maybe_roam)
        self._schedule_roam(12.0)   # 启动后先等 12 秒

        # 主 tick
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        # 状态
        self._pending_actions: deque = deque()   # 队列（不覆盖）
        self._current_action: Optional[Action] = None
        self._current_timer = QTimer(self)
        self._current_timer.setSingleShot(True)
        self._current_timer.timeout.connect(self._on_action_timeout)

        # 睡眠
        self._sleeping = False
        self._sleep_elapsed = 0.0
        self._sleep_duration = _SLEEP_DEFAULT_SEC

        # 需求节流：need_key -> 上次 emit 的 tick 计数
        self._need_last_emitted: dict = {}

        # 上次 emit state_changed 的整数量，用于节流
        self._last_emit_key = None

        # 未持久化的脏标记 + tick 计数
        self._dirty = False
        self._tick_counter = 0
    # ==================== 对外接口 ====================

    @property
    def state(self) -> PetState:
        return self._state

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    def request_action(self, key: str):
        """外部触发动作（右键菜单 / 交互）。

        空闲 → 立即执行（不等下一次 tick，用户感知"秒响应"）；
        忙   → 排队，不覆盖已有动作。
        """
        if key not in ACTIONS:
            return
        self.note_presence()
        if self._current_action is None and not self._sleeping:
            self._start_action(key)
        else:
            self._pending_actions.append(key)

    # ---------- 自主漫游 ----------

    def set_roam_enabled(self, enabled: bool):
        self._roam_enabled = bool(enabled)
        if enabled:
            self._schedule_roam(8.0)

    def note_presence(self):
        """主人在活动（打字 / 点击 / 开对话 / 喂道具）。

        活动期间墨灵乖乖陪着，不自己溜达；安静 45 秒后才开始自主行为。
        """
        self._presence_ts = time.monotonic()

    def _schedule_roam(self, delay: float = 0.0):
        if not self._roam_enabled:
            return
        self._roam_timer.start(int(max(2.0, delay) * 1000))

    def _maybe_roam(self):
        if not self._roam_enabled:
            return
        idle_for = time.monotonic() - self._presence_ts
        busy = (self._sleeping or self._current_action is not None
                or bool(self._pending_actions))
        if idle_for >= self.ROAM_IDLE_GRACE and not busy:
            self.roam_pulse.emit()
            # 一轮漫游展示层大约跑 10 秒；下一轮 25-60 秒后
            self._schedule_roam(random.uniform(25.0, 60.0))
        else:
            # 还在忙/主人还在动，15 秒后再看
            self._schedule_roam(15.0)

    def notify_writing(self, delta_words: int):
        """主人码字了（净增字数），给陪伴奖励。workspace 保存时调用。"""
        if delta_words <= 0:
            return
        self.note_presence()
        try:
            self._state.apply_writing_bonus(int(delta_words))
            self._dirty = True
        except Exception as e:
            logger.warning(f"写作亲密度奖励失败: {e}")

    def start_sleep(self, duration_sec: float = _SLEEP_DEFAULT_SEC):
        """进入睡眠状态。"""
        if self._sleeping:
            return
        self._sleeping = True
        self._sleep_elapsed = 0.0
        self._sleep_duration = max(5.0, float(duration_sec))
        self._current_action = ACTIONS["sleep"]
        self._state.touch_interact()
        self.action_started.emit("sleep", "sleeping")
        self.bubble_requested.emit(random.choice(
            ACTIONS["sleep"].bubbles or ["Zzz…"]))
        self.sleep_changed.emit(True)

    def wake(self):
        """主动唤醒。"""
        if not self._sleeping:
            return
        self._sleeping = False
        self._sleep_elapsed = 0.0
        self._current_action = None
        # 睡一觉 = 精力恢复到满（简化模型，不用"按小时"这种
        # 现实世界的缓慢速率——宠物是虚拟陪伴，让"点一下"立刻见效）
        self._state.energy = min(C.STAT_MAX,
                                 self._state.energy + 60.0)
        self._state.mood = min(C.STAT_MAX,
                               self._state.mood + 10.0)
        self._state.total_sleep_hours += self._sleep_elapsed / 3600.0
        self._state.touch_interact()
        self._dirty = True
        self.action_finished.emit("sleep")
        self.bubble_requested.emit("唔…醒了，精神多了")
        self.sleep_changed.emit(False)

    def flush(self):
        """保存状态。退出 / 切换作品时调用。"""
        if not self._data_root:
            return
        try:
            save_state(self._data_root, self._state)
            self._dirty = False
        except Exception as e:
            logger.warning(f"宠物状态保存失败: {e}")

    def shutdown(self):
        """停止所有定时器 + 保存。"""
        try:
            self._tick_timer.stop()
            self._current_timer.stop()
        except RuntimeError:
            pass
        self.flush()

    # ==================== 主循环 ====================

    def _on_tick(self):
        self._tick_counter += 1

        # 1. 每秒衰减或恢复
        dt_hours = 1.0 / 3600.0
        self._state.tick(dt_hours, sleeping=self._sleeping)
        self._dirty = True

        # 2. 睡眠进度
        if self._sleeping:
            self._sleep_elapsed += 1.0
            if self._sleep_elapsed >= self._sleep_duration:
                self.wake()

        # 3. 处理排队的动作（队列，先进先出）
        if self._pending_actions and self._current_action is None:
            key = self._pending_actions.popleft()
            self._start_action(key)

        # 4. 空闲时检查需求（边沿触发 + 节流；睡着不催）
        if self._current_action is None and not self._sleeping:
            self._check_needs()

        # 5. 节流 state_changed：整数没变就不 emit
        sig = (
            round(self._state.hunger, 1),
            round(self._state.thirst, 1),
            round(self._state.energy, 1),
            round(self._state.cleanliness, 1),
            round(self._state.mood, 1),
            self._state.level,
        )
        if sig != self._last_emit_key:
            self._last_emit_key = sig
            self.state_changed.emit()

        # 6. 每 30 秒自动存一次盘
        if self._dirty and self._tick_counter % 30 == 0:
            self.flush()

    def _check_needs(self):
        """需求边沿检测：新需求立即提醒；持续需求按间隔重复；已解决则清除。"""
        need = self._state.current_need()

        # 已解决的需求清掉节流记录，下次再饿时能立刻提醒
        for old_need in list(self._need_last_emitted.keys()):
            if old_need != need:
                self._need_last_emitted.pop(old_need, None)

        if not need:
            return
        last = self._need_last_emitted.get(need, -10 ** 9)
        if self._tick_counter - last < C.NEED_REMIND_INTERVAL_SEC:
            return
        self._need_last_emitted[need] = self._tick_counter
        self.need_detected.emit(need)

    def _start_action(self, key: str):
        act = ACTIONS.get(key)
        if act is None:
            return
        if not act.can_run(self._state):
            self.bubble_requested.emit("现在不行呢…")
            return

        # sleep 走特殊入口
        if key == "sleep":
            self.start_sleep()
            return

        self._current_action = act
        self._state.touch_interact()
        self.action_started.emit(act.key, act.anim_key)
        if act.bubbles:
            self.bubble_requested.emit(random.choice(act.bubbles))

        act.run_effect(self._state)
        self._dirty = True

        if act.duration_ms > 0:
            self._current_timer.start(act.duration_ms)
        else:
            self._on_action_timeout()

    def _on_action_timeout(self):
        if self._current_action is None:
            return
        key = self._current_action.key
        self._current_action = None
        self.action_finished.emit(key)
