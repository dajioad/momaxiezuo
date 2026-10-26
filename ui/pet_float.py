# ui/pet_float.py
"""墨码写作 - 猫娘宠物助手（悬浮）

完整功能：
- 流式对话 / 上下文注入 / 预设 / @ 引用 / 历史 / 导出 / 重发 / 插入替换
- 状态优先队列（base + temp 两层，临时状态过期回底层）
- 右键菜单（对话 / 道具箱 / 快捷操作 / 换装 / 位置 / 隐藏）
- 单击摸头 / 双击爱心表情 / 悬停 shy/ 视线跟随
- 呼吸 / 摇摆 / 随机倾斜 / 待机张望 / 眨眼 / 交叉淡化 / 打字轻弹
- 多图待机轮换（idle.png / idle_2.png / idle_3.png）
- 时段感知（早上 happy / 深夜 sleepy）
- 亲密度分级（按累计打字量）
- 修饰符：curious / bored / focused / tense

位置模型：
- 独立浮标（始终可拖动）
- 位置持久化：用户拖到哪，下次启动还在那
- 右键"贴回右下角"：一键回到默认位置
"""

import os
import re
import math
import time
import random
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QTextEdit, QApplication, QSizePolicy, QToolButton,
    QMenu, QDialog, QListWidget, QListWidgetItem, QMessageBox,
    QFileDialog, QProgressBar, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QPoint, QPointF, QSize, QRect, QRectF, QTimer,
    QPropertyAnimation, QEasingCurve, Signal, Property,
    QAbstractAnimation,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush, QPolygonF,
    QTextCursor, QPixmap, QCursor,
    QFont, QFontMetrics,
)
import qtawesome as qta

from utils.ai_client import (
    AIWorker, load_ai_config, has_api_key, get_system_prompt,
    build_quick_prompt, estimate_tokens, load_presets,
)
from ui.pet_system.animations import load_frames
from ui.pet_system.jade_icon import jade_pixmap
from utils import ai_conversations
from utils.context_builder import ContextBuilder
from utils.logger import get_logger
from ui.theme_style import qss_alpha
from ui.ai_widgets import _Bubble, _MentionPopup, _PresetManageDialog

logger = get_logger()

# 右键菜单快捷操作（键 = build_quick_prompt 的 action）
QUICK_ACTIONS = [
    ("polish",   "润色", "fa5s.magic"),
    ("expand",   "扩写", "fa5s.expand"),
    ("condense", "缩写", "fa5s.compress"),
    ("rewrite",  "改写", "fa5s.pen-nib"),
    ("continue", "续写", "fa5s.forward"),
]

# 可换装列表：(outfit 目录名, 菜单文案)。
# 立绘放 ui/pet/{outfit}/ 目录，缺素材时自动回退默认外观。
DEFAULT_OUTFIT = "whale"
OUTFIT_DEFS = [
    (DEFAULT_OUTFIT, "蓝鲸大肥鱼（默认）"),
]
OUTFIT_KEYS = {key for key, _label in OUTFIT_DEFS}

# 说明：
# - 时段问候文案在 ui/workspace.py 的 _maybe_greet（避免两处各存一份）
# - 亲密度等级门槛在 ui/pet_system/constants.py 的 LEVELS（唯一事实源）


# ============================================================
# 猫娘头像
# ============================================================
class _PetFace(QWidget):
    """猫娘头像本体。

    状态分两层：
    - base（底层）：idle / writing / thinking / sleepy，长期存在
    - temp（临时）：其它状态，超时自动回底层

    动画：呼吸 / 摇摆 / 倾斜 / 张望 / 眨眼 / 交叉淡化 / 鼠标跟随 / 轻弹
    """

    STATE_IDLE = "idle"
    STATE_WRITING = "writing"
    STATE_THINKING = "thinking"
    STATE_HAPPY = "happy"
    STATE_SLEEPY = "sleepy"
    STATE_EXCITED = "excited"
    STATE_SHY = "shy"
    STATE_LOVE = "love"
    STATE_SURPRISED = "surprised"
    STATE_PROUD = "proud"
    STATE_WORRIED = "worried"

    _VALID_STATES = {
        STATE_IDLE, STATE_WRITING, STATE_THINKING, STATE_HAPPY,
        STATE_SLEEPY, STATE_EXCITED, STATE_SHY, STATE_LOVE,
        STATE_SURPRISED, STATE_PROUD, STATE_WORRIED,
    }

    _BASE_STATES = frozenset({
        STATE_IDLE, STATE_WRITING, STATE_THINKING, STATE_SLEEPY,
    })

    # 所有状态 → 蓝鲸动画 key（进入状态时自动播放）
    _STATE_ANIM_KEYS = {
        STATE_IDLE:     "idle",
        STATE_WRITING:  "work_typing",
        STATE_THINKING: "work_thinking",
        STATE_SLEEPY:   "daily_sleep",
        STATE_HAPPY:    "click_happy",
        STATE_EXCITED:  "fun_jump",
        STATE_SHY:      "click_shy",
        STATE_LOVE:     "daily_curtsy",
        STATE_SURPRISED: "surprised",
        STATE_PROUD:    "work_celebrate",
        STATE_WORRIED:  "work_stressed",
    }

    _STATE_FALLBACK = {
        STATE_SURPRISED: (STATE_EXCITED, STATE_HAPPY),
        STATE_PROUD:     (STATE_HAPPY, STATE_LOVE),
        STATE_WORRIED:   (STATE_SHY, STATE_SLEEPY),
    }

    _FRAME_MS = 42
    _BLINK_DURATION = 0.18
    _LOOK_AMPLITUDE = 5.0
    _LOOK_SMOOTH = 0.14
    _TRANSITION_MS = 420
    _TRANSITION_PULSE = 0.04
    _MODIFIER_SMOOTH = 0.08
    _TILT_ANGLE = 4.0
    _TILT_DURATION = 2.5
    _TILT_INTERVAL_MIN = 8000
    _TILT_INTERVAL_MAX = 15000
    _LOOK_AROUND_DURATION = 0.5
    _LOOK_AROUND_PX = 4.0
    _LOOK_AROUND_INTERVAL_MIN = 15000
    _LOOK_AROUND_INTERVAL_MAX = 30000
    _TAP_DURATION = 0.18
    _TAP_PX = 1.8
    # 摸头热度：连续点击累积，影响弹跳幅度，热度高了冒爱心
    _PET_HEAT_PER_TAP = 0.3
    _PET_HEAT_DECAY = 0.22   # 每秒衰减（约 4.5 秒回到平静）
    _CURSOR_POLL_MS = 100
    _OFFSET_SMOOTH = 0.12
    _IDLE_ROTATE_MS = 30000  # 多图待机轮换间隔

    # 打字状态保持时长（毫秒）。用户停手后多久回 idle。
    # 太短 → "稍有停顿就退出"；太长 → 打完字猫娘还假装在打字。
    # 15 秒：足够覆盖"看一眼笔记、想一下词"这类短停顿，
    # 超过 15 秒才是真的不打了。
    _WRITING_HOLD_MS = 15000

    _STATE_ANIM = {
        STATE_IDLE:     (3.0, 0.012, 5.5, 0.8),
        STATE_WRITING:  (2.0, 0.015, 4.0, 1.0),
        STATE_THINKING: (4.0, 0.008, 7.0, 0.5),
        STATE_HAPPY:    (1.5, 0.025, 3.0, 2.0),
        STATE_SLEEPY:   (5.0, 0.010, 8.0, 0.4),
        STATE_EXCITED:  (1.2, 0.030, 2.5, 2.5),
        STATE_SHY:      (3.5, 0.010, 6.5, 0.6),
        STATE_LOVE:     (2.0, 0.020, 3.5, 1.8),
        STATE_SURPRISED:(1.8, 0.022, 3.0, 1.5),
        STATE_PROUD:    (2.5, 0.018, 5.0, 1.2),
        STATE_WORRIED:  (3.0, 0.012, 4.5, 1.5),
    }

    _MODIFIERS = {
        "":         (1.0, 1.0, 1.0, 0.0),
        "curious":  (1.1, 0.9, 0.8, -2.0),
        "bored":    (0.7, 1.6, 0.4, 3.5),
        "focused":  (0.5, 0.7, 0.3, 0.0),
        "tense":    (1.3, 0.6, 1.5, -0.8),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 140)
        self._hover = False
        self._state = self.STATE_IDLE
        self._prev_state = None
        self._accent = QColor("#b08a4e")
        self._text = QColor("#3b342a")

        # 图片：state -> [pix1, pix2, ...]（idle 支持多张轮换）
        self._pixmaps = {}
        # 缩放缓存：{(pix_cacheKey, w): scaled_pix}
        # 避免每帧重复缩放（1024 大图缩到 220 宽很吃 CPU）
        self._scaled_cache = {}
        self._idle_variant = 0
        self._outfit = DEFAULT_OUTFIT
        self._display_region = "full"   # head / upper / full
        self._load_pixmaps(self._outfit)

        # 序列帧动画（独立 timer，不能和主 tick 共用 _anim_timer）
        self._anim_frames = []       # [(pix, ms), ...]
        self._anim_index = 0
        self._anim_key = ""          # 正在播的动画 key（空 = 无动画）
        self._anim_loop = True       # True=循环, False=播完即停
        self._anim_is_action = False # True=动作动画，False=基础状态循环
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._advance_frame)

        # 相位
        self._breath_phase = 0.0
        self._sway_phase = 0.0
        self._blink_progress = -1.0
        self._transition_t = 1.0
        self._offset_target = 0.0
        self._offset_cur = 0.0
        self._look_target = 0.0
        self._look_cur = 0.0
        self._look_y_bias = 0.0
        self._look_y_bias_target = 0.0
        self._tilt_target = 0.0
        self._tilt_cur = 0.0
        self._tilt_t = -1.0
        self._look_around_t = -1.0
        self._look_around_dir = 1.0
        self._tap_t = -1.0
        self._pet_heat = 0.0

        # 程序化动作（无序列帧素材时的动作补偿）
        # _proc_key 为空表示无；_proc_t 为动作内进度（秒）
        self._proc_key = ""
        self._proc_t = -1.0
        self._proc_dur = 0.0
        self._proc_dir = 1

        # 被抓起状态：根据拖动速度朝运动方向倾斜、轻微下垂
        self._carried = False
        self._carried_tilt_target = 0.0
        self._carried_tilt_cur = 0.0

        # 修饰符
        self._modifier = ""
        self._mod_breath_amp = 1.0
        self._mod_breath_period = 1.0
        self._mod_sway_amp = 1.0
        self._mod_offset_y = 0.0
        self._cur_mod_breath_amp = 1.0
        self._cur_mod_breath_period = 1.0
        self._cur_mod_sway_amp = 1.0
        self._cur_mod_offset_y = 0.0

        # 状态优先队列
        self._base_state = self.STATE_IDLE
        self._temp_state = ""

        # 主驱动
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(self._FRAME_MS)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start()

        # 多图待机轮换
        self._idle_rotate_timer = QTimer(self)
        self._idle_rotate_timer.setInterval(self._IDLE_ROTATE_MS)
        self._idle_rotate_timer.timeout.connect(self._rotate_idle)
        self._idle_rotate_timer.start()

        # 眨眼调度
        self._blink_schedule = QTimer(self)
        self._blink_schedule.setSingleShot(True)
        self._blink_schedule.timeout.connect(self._start_blink)
        self._schedule_next_blink()

        # 随机倾斜
        self._tilt_schedule = QTimer(self)
        self._tilt_schedule.setSingleShot(True)
        self._tilt_schedule.timeout.connect(self._start_tilt)
        self._schedule_next_tilt()

        # 待机张望
        self._look_around_schedule = QTimer(self)
        self._look_around_schedule.setSingleShot(True)
        self._look_around_schedule.timeout.connect(self._start_look_around)
        self._schedule_next_look_around()

        # 鼠标跟随
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(self._CURSOR_POLL_MS)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._cursor_timer.start()

        # 修饰符超时
        self._modifier_timer = QTimer(self)
        self._modifier_timer.setSingleShot(True)
        self._modifier_timer.timeout.connect(lambda: self.set_modifier(""))

        # 临时状态超时
        self._temp_timer = QTimer(self)
        self._temp_timer.setSingleShot(True)
        self._temp_timer.timeout.connect(self._on_temp_timeout)

        # base writing 停手回 idle
        self._base_timeout_timer = QTimer(self)
        self._base_timeout_timer.setSingleShot(True)
        self._base_timeout_timer.timeout.connect(self._on_base_timeout)

        # 深夜启动 → 5 秒后 base sleepy
        try:
            h = datetime.now().hour
            if h >= 23 or h < 6:
                QTimer.singleShot(
                    5000,
                    lambda: self.set_base_state(self.STATE_SLEEPY))
        except Exception:
            pass

        self.setMouseTracking(True)

        # 启动后播放 idle 循环动画（_apply_state 因状态未变不会触发）
        QTimer.singleShot(0, lambda: self._play_state_anim(self.STATE_IDLE))

    def _load_pixmaps(self, outfit: str = DEFAULT_OUTFIT):
        """加载指定服装的图片。支持三种素材布局，按优先级：

        1. ui/pet/{outfit}/ 子目录：整套多状态立绘（idle.png/writing.png…）
        2. ui/pet/{outfit}.png 单图：所有状态共用这一张立绘
        3. ui/pet/*.png 平铺默认立绘（hanfu 的状态图就放这里，
           扫描时跳过其他服装的单图，避免串台）
        """
        self._pixmaps.clear()
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            pet_root = os.path.join(here, "pet")
            if not os.path.isdir(pet_root):
                return

            # 1. outfit 子目录（只扫顶层 PNG，动作序列帧在子子目录，
            #    由 play_anim 单独加载）
            outfit_dir = os.path.join(pet_root, outfit)
            if os.path.isdir(outfit_dir):
                self._scan_png_dir(outfit_dir)

            # 2. 单图服装：ui/pet/{outfit}.png → 映射到全部状态
            if not self._pixmaps:
                single = os.path.join(pet_root, f"{outfit}.png")
                if os.path.isfile(single):
                    pix = QPixmap(single)
                    if not pix.isNull():
                        for state in self._all_state_keys():
                            self._pixmaps[state] = [pix]

            # 3. 回退平铺默认立绘（排除其他服装的单图）
            if not self._pixmaps:
                for name in os.listdir(pet_root):
                    if not name.lower().endswith(".png"):
                        continue
                    stem = os.path.splitext(name)[0]
                    if stem in OUTFIT_KEYS and stem != outfit:
                        continue
                    pix = QPixmap(os.path.join(pet_root, name))
                    if pix.isNull():
                        continue
                    self._pixmaps.setdefault(stem, []).append(pix)
        except Exception:
            pass

    def _scan_png_dir(self, directory: str):
        """把目录顶层的 PNG 按文件名存入 _pixmaps。"""
        for name in os.listdir(directory):
            if not name.lower().endswith(".png"):
                continue
            stem = os.path.splitext(name)[0]
            pix = QPixmap(os.path.join(directory, name))
            if pix.isNull():
                continue
            self._pixmaps.setdefault(stem, []).append(pix)

    def _all_state_keys(self) -> list:
        """所有表情/状态键（STATE_* 常量）。"""
        return [
            getattr(self, name)
            for name in dir(self)
            if name.startswith("STATE_") and isinstance(getattr(self, name), str)
        ]

    def set_outfit(self, outfit: str):
        """切换服装：重载图片，保留当前状态。"""
        if not outfit:
            return
        if getattr(self, "_outfit", "") == outfit:
            return
        self._outfit = outfit
        self._load_pixmaps(outfit)
        self._idle_variant = 0
        self.update()

    def get_base_aspect(self) -> float:
        """返回原图宽高比（高/宽）。用于动态算窗口高度。"""
        for _key, pixs in self._pixmaps.items():
            if pixs and not pixs[0].isNull():
                pix = pixs[0]
                return pix.height() / max(1, pix.width())
        return 1.0

    def set_display_region(self, region: str):
        """切换显示范围。"""
        if region not in ("head", "upper", "full"):
            return
        self._display_region = region
        self.update()


    def play_anim(self, anim_key: str, fallback_state: str = "",
                  frames=None, loop: bool = True, is_action: bool = True):
        """播放序列帧动画。找不到素材时退回静态状态。

        frames 可由调用方预先 load_frames 后传入（action_started 需要
        先判断"是否真动画"再决定走帧还是程序化补偿），避免一次动作
        重复加载/解码同一套帧。

        loop=True 循环播放（待机等持续动画）；loop=False 播一遍后自动停。
        is_action=True 表示这是动作动画，结束后会自动回到基础状态动画。
        """
        if not anim_key:
            return
        if frames is None:
            here = os.path.dirname(os.path.abspath(__file__))
            pet_root = os.path.join(here, "pet")
            frames = load_frames(pet_root, self._outfit, anim_key)
        if not frames:
            # 无素材：退回静态
            if fallback_state:
                self.set_state(fallback_state)
            return
        self._anim_frames = frames
        self._anim_key = anim_key
        self._anim_index = 0
        self._anim_loop = loop
        self._anim_is_action = is_action
        self.update()
        self._schedule_next_frame()

    def play_anim_once(self, anim_key: str, **kwargs):
        """播一遍就停的便捷方法。"""
        self.play_anim(anim_key, loop=False, **kwargs)

    def stop_anim(self, fallback_state: str = ""):
        """停止动画。如果是动作动画，自动恢复当前状态的循环动画。"""
        was_action = self._anim_is_action
        self._frame_timer.stop()
        self._anim_frames = []
        self._anim_key = ""
        self._anim_index = 0
        self._anim_is_action = False
        self._anim_loop = True
        if fallback_state:
            self.set_state(fallback_state)
        elif was_action:
            # 动作结束 → 回到当前状态的循环动画
            self._play_state_anim(self._state or self.STATE_IDLE)
        self.update()

    def _play_state_anim(self, state: str):
        """播放状态对应的循环动画（若素材存在且未被动作动画覆盖）。"""
        if self._anim_is_action and self._anim_frames:
            return
        anim_key = self._STATE_ANIM_KEYS.get(state)
        if not anim_key:
            return
        here = os.path.dirname(os.path.abspath(__file__))
        pet_root = os.path.join(here, "pet")
        frames = load_frames(pet_root, self._outfit, anim_key)
        if frames:
            self._anim_frames = frames
            self._anim_key = anim_key
            self._anim_index = 0
            self._anim_loop = True
            self._anim_is_action = False
            self.update()
            self._schedule_next_frame()

    # ---------- 程序化动作（无素材时的动作补偿） ----------
    # 素材补齐之前，用位移/旋转/压缩曲线让动作"看得出来"；
    # 将来有序列帧时展示层会优先播帧，这套代码自动退居二线。
    _PROC_PERIODS = {
        # key: 单次循环秒数（循环次数由 duration 决定）
        "pet_head": 0.85,
        "drinking": 1.2,
        "cleaning": 0.55,
        "playing": 0.62,
        "eating":  0.5,
        "walking": 0.42,
    }

    def start_procedural(self, key: str, duration_ms: int,
                         direction: int = 1):
        """启动程序化动作补偿。key 同 actions 的 anim/action key。

        walking 时 direction=1 向右、-1 向左（决定身体前倾方向）。
        """
        if not key or key not in self._PROC_PERIODS:
            return
        self._proc_key = key
        self._proc_t = 0.0
        self._proc_dir = 1 if direction >= 0 else -1
        # 动作结束前留 0.25s 收尾，避免在最大幅度处戛然而止
        self._proc_dur = max(0.3, duration_ms / 1000.0)

    def stop_procedural(self):
        self._proc_key = ""
        self._proc_t = -1.0
        self._proc_dur = 0.0
        self.update()

    def set_carried(self, carried: bool, vx: float = 0.0):
        """被抓起 / 放下。vx 为当前水平拖动速度（px/s），用于倾斜方向。"""
        self._carried = bool(carried)
        if carried:
            self._carried_tilt_target = max(-8.0, min(8.0, vx / 60.0))
        else:
            self._carried_tilt_target = 0.0

    def _proc_transform(self, t: float) -> tuple:
        """返回程序化动作的 (dx, dy, 角度增量, 纵向缩放增量)。"""
        key = self._proc_key
        period = self._PROC_PERIODS.get(key, 1.0)
        phase = (t % period) / period          # 0..1 单次循环
        s = math.sin(math.pi * 2 * phase)
        if key == "pet_head":
            # 周期性"被按下"：半正弦下压 + 纵向微缩
            press = abs(math.sin(math.pi * ((t % period) / period)))
            return 0.0, 4.0 * press, 0.0, -0.02 * press
        if key == "drinking":
            # 前倾喝水：前 0.3s 倾到 -5°，之后小幅前后晃动
            lean = min(1.0, t / 0.3) * -5.0
            return 0.0, 0.0, lean + s * 1.2, 0.0
        if key == "cleaning":
            # 搓澡式左右摇摆
            return 0.0, 0.0, 6.0 * s, 0.0
        if key == "playing":
            # 连续蹦跳：抛物线腾空，落地压缩
            air = max(0.0, math.sin(math.pi * 2 * phase))
            dy = -16.0 * air * air
            sy = -0.03 * (1.0 - air)
            rot = 3.0 * s * air
            return 0.0, dy, rot, sy
        if key == "eating":
            return 0.0, 2.5 * s, 0.0, 0.0
        if key == "walking":
            # 小步溜达：轻微起伏 + 朝行进方向微微前倾
            bob = -2.5 * abs(s)
            return 0.0, bob, self._proc_dir * 1.6 * s, 0.0
        return 0.0, 0.0, 0.0, 0.0

    def _schedule_next_frame(self):
        if not self._anim_frames:
            return
        _, ms = self._anim_frames[self._anim_index]
        if ms <= 0:
            # 静态图：不循环
            return
        self._frame_timer.start(ms)

    def _advance_frame(self):
        if not self._anim_frames:
            return
        if not self._anim_loop:
            # 非循环：播完最后一帧就停
            if self._anim_index >= len(self._anim_frames) - 1:
                self.stop_anim()
                return
            self._anim_index += 1
        else:
            self._anim_index = (self._anim_index + 1) % len(self._anim_frames)
        self.update()
        self._schedule_next_frame()

    def _rotate_idle(self):
        """待机多图轮换：只在 idle 状态生效。"""
        if self._state != self.STATE_IDLE:
            return
        variants = [k for k in self._pixmaps if k == "idle"
                    or k.startswith("idle_")]
        # 排除 idle_blink / idle_2_blink 之类
        variants = [v for v in variants if not v.endswith("_blink")]
        if len(variants) <= 1:
            return
        self._idle_variant = (self._idle_variant + 1) % len(variants)
        self.update()

    # ---------- 状态优先队列 ----------

    def set_base_state(self, state: str):
        """底层状态：idle / writing / thinking / sleepy。"""
        if state not in self._BASE_STATES:
            return
        if state == self._base_state:
            if state == self.STATE_WRITING:
                self._base_timeout_timer.start(self._WRITING_HOLD_MS)
            return
        self._base_state = state
        if self._temp_state:
            return
        self._apply_state(state)
        if state == self.STATE_WRITING:
            self._base_timeout_timer.start(self._WRITING_HOLD_MS)
        else:
            self._base_timeout_timer.stop()

    def _on_base_timeout(self):
        if self._base_state != self.STATE_WRITING:
            return
        self._base_state = self.STATE_IDLE
        if not self._temp_state:
            self._apply_state(self.STATE_IDLE)

    def set_state(self, state: str, hold_ms: int = 3000):
        """临时状态：超时自动回底层。传 base 名字也行（转给 set_base_state）。"""
        if state not in self._VALID_STATES:
            return
        if state in self._BASE_STATES:
            self.set_base_state(state)
            return
        self._temp_state = state
        self._apply_state(state)
        if hold_ms > 0:
            self._temp_timer.start(hold_ms)
        else:
            self._temp_timer.stop()

    def _on_temp_timeout(self):
        self._temp_state = ""
        self._apply_state(self._base_state)
        if self._base_state == self.STATE_WRITING:
            self._base_timeout_timer.start(2000)

    def _apply_state(self, state: str):
        if state == self._state:
            return
        self._prev_state = self._state
        self._transition_t = 0.0
        self._state = state
        # 进入新状态时自动播放对应循环动画（动作动画进行中不打断）
        self._play_state_anim(state)

    # ---------- 外部接口 ----------

    def set_colors(self, accent: str, text: str):
        self._accent = QColor(accent)
        self._text = QColor(text)
        self.update()

    def tap(self):
        if self._state in (self.STATE_IDLE, self.STATE_WRITING):
            self._tap_t = 0.0

    def add_pet_heat(self):
        """真正的"摸头"动作累积热度（打字的 tap 不算）。"""
        self._pet_heat = min(
            1.0, self._pet_heat + self._PET_HEAT_PER_TAP)

    def set_offset_y(self, dy: float):
        self._offset_target = float(dy)

    def set_modifier(self, name: str, hold_ms: int = 0):
        name = name or ""
        if name == self._modifier and hold_ms <= 0:
            return
        self._modifier = name
        amp, period, sway, dy = self._MODIFIERS.get(
            name, self._MODIFIERS[""])
        self._mod_breath_amp = amp
        self._mod_breath_period = period
        self._mod_sway_amp = sway
        self._mod_offset_y = dy
        if hold_ms > 0:
            self._modifier_timer.start(hold_ms)
        else:
            self._modifier_timer.stop()

    # ---------- 动画驱动 ----------

    def _tick_anim(self):
        # 双保险：正常路径 hideEvent 已停表，若隐藏期间仍有残留 tick
        # （如事件队列里已排队的 timeout），直接跳过相位推进与重绘。
        if not self.isVisible():
            return
        dt = self._FRAME_MS / 1000.0
        self._breath_phase += dt
        self._sway_phase += dt
        self._offset_cur += (
            self._offset_target - self._offset_cur) * self._OFFSET_SMOOTH
        s = self._MODIFIER_SMOOTH
        self._cur_mod_breath_amp += (
            self._mod_breath_amp - self._cur_mod_breath_amp) * s
        self._cur_mod_breath_period += (
            self._mod_breath_period - self._cur_mod_breath_period) * s
        self._cur_mod_sway_amp += (
            self._mod_sway_amp - self._cur_mod_sway_amp) * s
        self._cur_mod_offset_y += (
            self._mod_offset_y - self._cur_mod_offset_y) * s

        if self._blink_progress >= 0:
            self._blink_progress += dt / self._BLINK_DURATION
            if self._blink_progress >= 1.0:
                self._blink_progress = -1.0

        if self._transition_t < 1.0:
            self._transition_t += dt / (self._TRANSITION_MS / 1000.0)
            if self._transition_t >= 1.0:
                self._transition_t = 1.0
                self._prev_state = None

        self._look_cur += (
            self._look_target - self._look_cur) * self._LOOK_SMOOTH
        self._look_y_bias += (
            self._look_y_bias_target - self._look_y_bias) * self._LOOK_SMOOTH

        if self._tilt_t >= 0:
            self._tilt_t += dt
            if self._tilt_t >= self._TILT_DURATION:
                self._tilt_t = -1.0
                self._tilt_cur = 0.0
            else:
                progress = self._tilt_t / self._TILT_DURATION
                self._tilt_cur = self._tilt_target * math.sin(
                    math.pi * progress)

        if self._look_around_t >= 0:
            self._look_around_t += dt
            if self._look_around_t >= self._LOOK_AROUND_DURATION:
                self._look_around_t = -1.0

        if self._tap_t >= 0:
            self._tap_t += dt
            if self._tap_t >= self._TAP_DURATION:
                self._tap_t = -1.0

        if self._pet_heat > 0:
            self._pet_heat = max(
                0.0, self._pet_heat - self._PET_HEAT_DECAY * dt)

        # 程序化动作进度（到时自动收，brain 的 action_finished 也会收）
        if self._proc_t >= 0:
            self._proc_t += dt
            if self._proc_t >= self._proc_dur:
                self.stop_procedural()

        # 被抓起时的倾斜平滑
        self._carried_tilt_cur += (
            self._carried_tilt_target - self._carried_tilt_cur) * 0.18

        self.update()

    def _schedule_next_blink(self):
        self._blink_schedule.start(random.randint(3000, 8000))

    def _start_blink(self):
        self._blink_progress = 0.0
        self._schedule_next_blink()

    def _schedule_next_tilt(self):
        self._tilt_schedule.start(
            random.randint(self._TILT_INTERVAL_MIN, self._TILT_INTERVAL_MAX))

    def _start_tilt(self):
        self._tilt_target = random.choice([-1, 1]) * self._TILT_ANGLE
        self._tilt_t = 0.0
        self._schedule_next_tilt()

    def _schedule_next_look_around(self):
        self._look_around_schedule.start(
            random.randint(self._LOOK_AROUND_INTERVAL_MIN,
                           self._LOOK_AROUND_INTERVAL_MAX))

    def _start_look_around(self):
        self._look_around_dir = random.choice([-1.0, 1.0])
        self._look_around_t = 0.0
        self._schedule_next_look_around()

    def _poll_cursor(self):
        # 视线跟随豁免状态：专注/困倦/亢奋时不跟
        if self._state in (self.STATE_THINKING, self.STATE_WRITING,
                           self.STATE_SLEEPY, self.STATE_EXCITED):
            self._look_target = 0.0
            return
        if not self.isVisible():
            return
        try:
            gpos = QCursor.pos()
            center = self.mapToGlobal(
                QPoint(self.width() // 2, self.height() // 2))
            dx = gpos.x() - center.x()
            dy = gpos.y() - center.y()
            # 只用水平分量映射为旋转（转头感）
            # 死区：鼠标在猫娘中心 80px 内不做视线跟随。
            # 悬停时鼠标就在猫娘身上，参数微小波动会造成猫娘抖动。
            # 就像手拿着玩偶——玩偶不需要转头看你的手。
            if abs(dx) < 80 and abs(dy) < 80:
                self._look_target = 0.0
                self._look_y_bias_target = 0.0
                return

            ratio_x = max(-1.0, min(1.0, dx / 500.0))
            ratio_y = max(-1.0, min(1.0, dy / 500.0))
            self._look_target = ratio_x * self._LOOK_AMPLITUDE
            self._look_y_bias_target = ratio_y * 0.8
        except Exception:
            self._look_y_bias_target = 0.0

    # ---------- 鼠标 ----------

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # ---------- 绘制 ----------

    def _pick_pixmap(self, state, blinking):
        # idle 多图轮换
        if state == self.STATE_IDLE:
            variants = [k for k in self._pixmaps
                        if k == "idle" or k.startswith("idle_")]
            variants = [v for v in variants if not v.endswith("_blink")]
            variants.sort()
            if variants:
                chosen = variants[self._idle_variant % len(variants)]
                if blinking:
                    key = f"{chosen}_blink"
                    if key in self._pixmaps:
                        return self._pixmaps[key][0]
                if chosen in self._pixmaps:
                    return self._pixmaps[chosen][0]

        # 常规查找（含 fallback 链）
        candidates = [state] + list(self._STATE_FALLBACK.get(state, ()))
        candidates.append(self.STATE_IDLE)
        for st in candidates:
            if blinking:
                key = f"{st}_blink"
                if key in self._pixmaps:
                    return self._pixmaps[key][0]
            if st in self._pixmaps:
                return self._pixmaps[st][0]
        return None

    @staticmethod
    def _ease_in_out(t: float) -> float:
        t = max(0.0, min(1.0, t))
        if t < 0.5:
            return 4 * t * t * t
        return 1 - (-2 * t + 2) ** 3 / 2

    def _draw_pixmap(self, p, pix, w, h):
        """按容器宽度缩放 + 顶部对齐，带缩放缓存。"""
        key = (pix.cacheKey(), w)
        scaled = self._scaled_cache.get(key)
        if scaled is None:
            scaled = pix.scaled(w, 10000, Qt.KeepAspectRatio,
                                Qt.SmoothTransformation)
            # 简单 LRU：超过 32 项清一半
            if len(self._scaled_cache) > 32:
                self._scaled_cache.clear()
            self._scaled_cache[key] = scaled
        p.drawPixmap(0, 0, scaled)


    def drawn_rect(self) -> QRect:
        """猫娘**实际绘制**的矩形（相对本控件）。

        气泡定位要用这个，而不是整个控件矩形——控件可能比图大，
        上方有大片透明留白。
        """
        # 优先用当前正在播的动画帧
        if self._anim_frames and self._anim_key:
            pix = self._anim_frames[self._anim_index][0]
        else:
            pix = self._pick_pixmap(
                self._state, self._blink_progress >= 0)
        if pix is None or pix.isNull():
            return self.rect()
        key = (pix.cacheKey(), self.width())
        scaled = self._scaled_cache.get(key)
        if scaled is None:
            scaled = pix.scaled(self.width(), 10000, Qt.KeepAspectRatio,
                                Qt.SmoothTransformation)
            if len(self._scaled_cache) > 32:
                self._scaled_cache.clear()
            self._scaled_cache[key] = scaled
        h = min(self.height(), scaled.height())
        return QRect(0, 0, self.width(), h)

    def clear_pixmap_cache(self):
        """窗口尺寸变化时清缓存。"""
        self._scaled_cache.clear()

    def resizeEvent(self, event):
        """窗口尺寸变化 → 清缩放缓存。"""
        super().resizeEvent(event)
        try:
            self._scaled_cache.clear()
        except AttributeError:
            pass

    def hideEvent(self, event):
        """隐藏时停掉全部驱动定时器。

        宠物被收起/最小化到托盘后，旧实现仍按 42ms 空转主 tick
        （呼吸/眨眼/光标轮询 + update()），笔记本上持续吃 CPU/电量。
        """
        super().hideEvent(event)
        for name in ("_anim_timer", "_idle_rotate_timer", "_cursor_timer",
                     "_blink_schedule", "_tilt_schedule",
                     "_look_around_schedule", "_frame_timer"):
            t = getattr(self, name, None)
            if t is not None:
                t.stop()

    def showEvent(self, event):
        """重新显示时恢复驱动定时器（与 hideEvent 配对）。"""
        super().showEvent(event)
        self._anim_timer.start()
        self._idle_rotate_timer.start()
        self._cursor_timer.start()
        # 三条 singleshot 链停后不会自续，重新排一次
        self._schedule_next_blink()
        self._schedule_next_tilt()
        self._schedule_next_look_around()
        # 正在播放序列帧动画时恢复帧推进
        if self._anim_frames:
            self._schedule_next_frame()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        blinking = self._blink_progress >= 0

        # 动画播放中：直接画当前帧，不叠加呼吸/摇摆（避免破帧）
        if self._anim_frames and self._anim_key:
            pix = self._anim_frames[self._anim_index][0]
            if pix is not None and not pix.isNull():
                # 呼吸幅度压低到 1/3——动画本身就有运动，再叠加会晕
                breath_scale = 1.0 + (self._cur_mod_breath_amp * 0.004) * math.sin(
                    2 * math.pi * self._breath_phase / max(0.1,
                    self._STATE_ANIM.get(self._state,
                                        self._STATE_ANIM[self.STATE_IDLE])[0] *
                    self._cur_mod_breath_period))
                p.translate(w / 2.0, h)
                p.scale(1.0, breath_scale)
                p.translate(-w / 2.0, -h)
                self._draw_pixmap(p, pix, w, h)
                p.end()
                return

        cur_pix = self._pick_pixmap(self._state, blinking)
        prev_pix = None
        if self._prev_state is not None and self._transition_t < 1.0:
            prev_pix = self._pick_pixmap(self._prev_state, blinking)

        if (cur_pix is None or cur_pix.isNull()) and (
                prev_pix is None or prev_pix.isNull()):
            self._paint_minimal(p)
            p.end()
            return

        bp, ba, sp, sa = self._STATE_ANIM.get(
            self._state, self._STATE_ANIM[self.STATE_IDLE])
        bp *= self._cur_mod_breath_period
        ba *= self._cur_mod_breath_amp
        sa *= self._cur_mod_sway_amp
        breath_scale = 1.0 + ba * math.sin(
            2 * math.pi * self._breath_phase / max(0.1, bp))
        sway_angle = sa * math.sin(
            2 * math.pi * self._sway_phase / max(0.1, sp))
        total_angle = sway_angle + self._look_cur + self._tilt_cur

        if self._prev_state is not None and self._transition_t < 1.0:
            pulse = self._TRANSITION_PULSE * math.sin(
                math.pi * self._transition_t)
            breath_scale *= (1.0 - pulse)

        offset_x = 0.0
        if self._look_around_t >= 0:
            progress = self._look_around_t / self._LOOK_AROUND_DURATION
            offset_x = (self._look_around_dir * self._LOOK_AROUND_PX
                        * math.sin(math.pi * progress))
        offset_y = self._offset_cur + self._cur_mod_offset_y
        offset_y += getattr(self, "_look_y_bias", 0.0)
        if self._tap_t >= 0:
            progress = self._tap_t / self._TAP_DURATION
            offset_y += -self._TAP_PX * math.sin(math.pi * progress)

        # 程序化动作补偿（摸头/喝水/清洁/玩耍，无素材时）
        proc_sy = 0.0
        if self._proc_key and self._proc_t >= 0:
            pdx, pdy, prot, psy = self._proc_transform(self._proc_t)
            offset_x += pdx
            offset_y += pdy
            total_angle += prot
            proc_sy = psy

        # 被抓起：朝运动方向倾斜 + 身体轻微下垂压缩
        if self._carried:
            total_angle += self._carried_tilt_cur
            offset_y += 3.0
            proc_sy -= 0.03

        p.translate(w / 2.0 + offset_x, h + offset_y)
        p.scale(1.0, breath_scale + proc_sy)
        p.rotate(total_angle)
        p.translate(-w / 2.0, -h)

        if prev_pix is not None and not prev_pix.isNull():
            eased = 1.0 - self._ease_in_out(self._transition_t)
            p.setOpacity(eased)
            self._draw_pixmap(p, prev_pix, w, h)
        if cur_pix is not None and not cur_pix.isNull():
            eased = self._ease_in_out(self._transition_t)
            p.setOpacity(eased if prev_pix is not None else 1.0)
            self._draw_pixmap(p, cur_pix, w, h)

        # 摸头热度超过半满 → 头顶冒小爱心
        if self._pet_heat > 0.55:
            alpha = int(220 * min(
                1.0, (self._pet_heat - 0.55) / 0.45))
            p.setPen(QColor(232, 110, 150, alpha))
            hf = QFont(self.font().family())
            hf.setPixelSize(int(w * 0.15))
            hf.setBold(True)
            p.setFont(hf)
            bob = -3.0 * self._pet_heat
            p.drawText(
                QRectF(w * 0.58, h * 0.04 + bob,
                       w * 0.3, h * 0.2),
                Qt.AlignCenter, "♥")

        p.setOpacity(1.0)
        p.end()

    def _paint_minimal(self, p: QPainter):
        w, h = self.width(), self.height()
        cx = w / 2.0
        cy = h / 2.0 + h * 0.05
        outline = self._accent
        ink = self._text
        body_r = min(w, h) * 0.34

        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawPolygon(QPolygonF([
            QPointF(cx - body_r * 0.72, cy - body_r * 0.45),
            QPointF(cx - body_r * 0.92, cy - body_r * 1.35),
            QPointF(cx - body_r * 0.18, cy - body_r * 0.92),
        ]))
        p.drawPolygon(QPolygonF([
            QPointF(cx + body_r * 0.72, cy - body_r * 0.45),
            QPointF(cx + body_r * 0.92, cy - body_r * 1.35),
            QPointF(cx + body_r * 0.18, cy - body_r * 0.92),
        ]))

        p.setPen(QPen(outline, max(2.0, body_r * 0.08)))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(cx, cy), body_r, body_r)

        eye_y = cy - body_r * 0.15
        eye_dx = body_r * 0.42
        eye_r = body_r * 0.14
        lx = cx - eye_dx
        rx = cx + eye_dx
        blinking = self._blink_progress >= 0
        if self._state == self.STATE_THINKING or blinking:
            pen = QPen(ink, max(2.0, body_r * 0.08))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for ex in (lx, rx):
                p.drawLine(QPointF(ex - eye_r, eye_y),
                           QPointF(ex + eye_r, eye_y))
        elif self._state in (self.STATE_HAPPY, self.STATE_LOVE):
            pen = QPen(ink, max(2.0, body_r * 0.08))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for ex in (lx, rx):
                path = QPainterPath()
                path.moveTo(ex - eye_r, eye_y + eye_r * 0.4)
                path.quadTo(ex, eye_y - eye_r * 1.2,
                            ex + eye_r, eye_y + eye_r * 0.4)
                p.drawPath(path)
        elif self._state == self.STATE_SLEEPY:
            pen = QPen(ink, max(2.0, body_r * 0.08))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for ex in (lx, rx):
                p.drawLine(QPointF(ex - eye_r, eye_y),
                           QPointF(ex + eye_r, eye_y))
        else:
            p.setPen(Qt.NoPen)
            for ex in (lx, rx):
                p.setBrush(ink)
                p.drawEllipse(QPointF(ex, eye_y), eye_r, eye_r * 1.15)
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(QPointF(ex - eye_r * 0.28,
                                      eye_y - eye_r * 0.35),
                              eye_r * 0.35, eye_r * 0.35)

        if (self._state in (self.STATE_IDLE, self.STATE_HAPPY,
                            self.STATE_LOVE, self.STATE_EXCITED)
                or self._hover):
            pen = QPen(ink, max(1.8, body_r * 0.06))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            mouth_y = cy + body_r * 0.34
            mw = body_r * 0.20
            path = QPainterPath()
            path.moveTo(cx - mw, mouth_y)
            path.quadTo(cx - mw * 0.5, mouth_y + mw * 0.9, cx, mouth_y)
            path.quadTo(cx + mw * 0.5, mouth_y + mw * 0.9, cx + mw, mouth_y)
            p.drawPath(path)


# ============================================================
# 内心独白气泡（QPainter 自绘，不依赖 stylesheet）
# ============================================================
class _ThoughtBubble(QWidget):
    """宠物头顶的短暂台词气泡。

    为什么不用 QLabel + stylesheet：
    WA_TranslucentBackground 会隐式打开 WA_NoSystemBackground，
    覆盖掉 stylesheet 的 background-color —— 气泡变透明只留字，
    用户以为"没显示"。改为 QPainter 自绘，背景/边框/圆角全掌握。
    """

    _PAD_H = 12      # 左右内边距
    _PAD_V = 8       # 上下内边距
    _MAX_W = 240     # 最大宽度（超出自动换行）
    _RADIUS = 10
    _TAIL_H = 6      # 底部指向宠物的小尾巴

    def __init__(self):
        super().__init__(None)
        # Qt.Tool：短暂气泡不进任务栏 / Alt+Tab
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(False)

        self._text = ""
        self._bg = QColor("#fcfaf4")
        self._fg = QColor("#3b342a")
        self._border = QColor("#b08a4e")
        self._font = QFont(self.font().family())
        self._font.setPixelSize(12)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # 点击气泡 → 打开对话面板（由 _PetFloat.say 时注入）
        self.on_clicked = None
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and callable(self.on_clicked):
            try:
                self.on_clicked()
                # 点击后立即收起，避免 CTA 气泡还在头上又触发第二次
                self._hide_timer.stop()
                self.hide()
            except RuntimeError:
                pass
        super().mousePressEvent(event)

    def set_theme(self, theme: dict):
        t = theme or {}
        self._bg = QColor(t.get("editor_bg", "#fcfaf4"))
        self._fg = QColor(t.get("text", "#3b342a"))
        accent = QColor(t.get("accent", "#b08a4e"))
        accent.setAlpha(0xB3)     # 70%，比实色柔和
        self._border = accent

    def say(self, text: str, duration_ms: int = 2500):
        self._text = text or ""
        if not self._text:
            self.hide()
            return
        self._resize_to_text()
        self.update()
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def _resize_to_text(self):
        fm = QFontMetrics(self._font)
        # 多行布局：以 _MAX_W - 2*_PAD_H 为每行宽度上限，算需要的行高
        inner_max_w = self._MAX_W - self._PAD_H * 2
        rect = fm.boundingRect(
            0, 0, inner_max_w, 10000,
            Qt.TextWordWrap | Qt.AlignLeft, self._text)
        inner_w = rect.width()
        inner_h = rect.height()

        w = inner_w + self._PAD_H * 2 + 2   # +2 给 border
        h = inner_h + self._PAD_V * 2 + 2 + self._TAIL_H
        w = max(w, 60)
        w = min(w, self._MAX_W)
        self.setFixedSize(w, h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        w = self.width()
        h = self.height()
        body_h = h - self._TAIL_H

        # 主体：圆角矩形
        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, body_h - 2,
                            self._RADIUS, self._RADIUS)

        # 底部小三角（指向宠物）
        cx = w / 2.0
        tail = QPainterPath()
        tail.moveTo(cx - 6, body_h - 1)
        tail.lineTo(cx, body_h + self._TAIL_H - 1)
        tail.lineTo(cx + 6, body_h - 1)
        tail.closeSubpath()
        path = path.united(tail)

        # 填充
        p.setPen(Qt.NoPen)
        p.setBrush(self._bg)
        p.drawPath(path)

        # 边框
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._border, 1.0))
        p.drawPath(path)

        # 文字
        p.setPen(self._fg)
        p.setFont(self._font)
        text_rect = self.rect().adjusted(
            self._PAD_H, self._PAD_V,
            -self._PAD_H, -self._PAD_V - self._TAIL_H)
        p.drawText(text_rect,
                   Qt.AlignCenter | Qt.TextWordWrap,
                   self._text)
        p.end()


# ============================================================
# 悬浮宠物本体
# ============================================================
class _PetFloat(QWidget):
    """独立顶层宠物窗口。

    位置模型：
    - 始终独立浮标：主窗口开着也能拖，最小化后依然在屏幕上
    - 位置持久化：用户拖到哪，下次启动还在那
    - 右键"贴回右下角"：一键回到默认位置
    """

    clicked = Signal()
    geometry_changed = Signal()
    request_chat = Signal()
    request_quick_action = Signal(str)

    # 显示范围 → 显示图片顶部多少比例
    REGION_RATIOS = {
        "head":  0.35,   # 只露头
        "upper": 0.65,   # 半身
        "full":  1.00,   # 全身
    }
    # 缩放系数 1.0 时的基础宽度
    BASE_WIDTH = 220
    # 缩放范围
    SCALE_MIN = 0.5
    SCALE_MAX = 2.0

    def __init__(self, workspace=None):
        super().__init__(None)
        self._ws = workspace
        self._brain = None
        self._theme = {}
        self._thought = None      # 内心独白气泡（懒创建）
        self._coin_toast = None   # +墨玉 飘字（懒创建）

        # 顶层窗口标志。
        # 用 Qt.Tool 而非 Qt.Window：Windows 上工具窗口不进任务栏、
        # 不进 Alt+Tab，宠物只是桌面上的悬浮层而不是"第二个程序窗口"。
        # 无 parent，主窗口最小化时它依然留在桌面。
        # 不用 Qt.WindowDoesNotAcceptFocus：它会让窗口收不到持续的
        # mouseMove（主窗口在前台时鼠标路由被抢），拖动时断时续。
        # 用 WA_ShowWithoutActivating 保证首次弹出不抢焦点即可。
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("单击摸头 · 双击爱心 · 拖动移动 · 右键菜单")

        # 显示范围 + 缩放
        self._display_region = "full"
        self._scale_factor = 1.0
        self.W, self.H = 220, 220   # 占位，_restore_position 会重算

        # 拖动状态
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        # 拖动速度采样：抓起时身体倾斜 + 松手时决定是否抛扔
        self._drag_last_pos = None
        self._drag_last_ms = 0.0
        self._drag_vx = 0.0
        self._drag_vy = 0.0

        # 抛扔物理（松手速度超过阈值 → 抛物飞行 → 撞边反弹 → 落地头晕）
        self._flying = False
        self._phys_vx = 0.0
        self._phys_vy = 0.0
        self._phys_bounces = 0
        self._phys_last_ms = 0.0
        self._phys_timer = QTimer(self)
        self._phys_timer.setInterval(16)
        self._phys_timer.timeout.connect(self._physics_step)
        self._PHYS_GRAVITY = 2600.0   # px/s²
        self._PHYS_WALL_DAMP = 0.5    # 撞墙/天花板水平/竖直速度保留
        self._PHYS_FLOOR_DAMP = 0.42  # 落地反弹保留
        self._PHYS_MAX_BOUNCES = 3
        self._PHYS_STOP_VY = 160.0    # 落地竖直速度低于此值就停下
        self._PHYS_THROW_SPEED = 260.0   # 抛扔触发速度
        self._PHYS_MAX_SPEED = 2200.0

        # 自主漫游（brain.roam_pulse 触发，位置移动由本层负责）
        self._roam_active = False
        self._roam_anchor_x = 0
        self._roam_steps_left = 0
        self._roam_target_x = 0
        self._roam_dir = 1
        self._roam_timer = QTimer(self)
        self._roam_timer.setInterval(16)
        self._roam_timer.timeout.connect(self._roam_step_move)
        self._roam_pause_timer = QTimer(self)
        self._roam_pause_timer.setSingleShot(True)
        self._roam_pause_timer.timeout.connect(self._roam_next)
        self._ROAM_SPEED = 85.0          # px/s
        self._ROAM_RADIUS = 150          # 离开起始点的最大水平距离
        self._ROAM_YAWN_LINES = [
            "呼啊……有点困了呢",
            "（安静地看看四周）",
            "主人在写什么呢……",
            "（甩甩尾巴）",
            "喵～",
        ]

        # 外部系统：经济（workspace 装配）
        self._economy = None

        # 折叠定时器（保留以兼容旧接口，不再自动折叠）
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(500)
        self._collapse_timer.timeout.connect(self._do_collapse)

        # 悬停 shy
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(3000)
        self._hover_timer.timeout.connect(self._on_hover_timeout)
        self._hover_shy_given = False

        # 连击检测
        self._click_streak = 0
        self._click_streak_timer = QTimer(self)
        self._click_streak_timer.setSingleShot(True)
        self._click_streak_timer.setInterval(400)
        self._click_streak_timer.timeout.connect(self._reset_click_streak)

        # 尺寸动画（宽高同时变）
        self._size_anim = QPropertyAnimation(self, b"petGeometry", self)
        self._size_anim.setDuration(200)
        self._size_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 内部 face 控件
        self._face = _PetFace(self)
        self._face.setFixedSize(self.W, self.H)
        self._face.move(0, 0)

        # 从 config 恢复位置 + 尺寸
        self._restore_position()
        self.refresh_pet_settings()

    # ---------- 漫游开关（config.ini [Pet]） ----------

    def refresh_pet_settings(self):
        """从配置同步：自主漫游。"""
        cfg = self._cfg()
        roam_on = True
        if cfg is not None:
            try:
                v = str(cfg.get_config("Pet", "roam_enabled", "1") or "1")
                roam_on = v.strip() not in ("0", "false", "False", "no")
            except (TypeError, ValueError):
                pass
        if self._brain is not None:
            try:
                self._brain.set_roam_enabled(roam_on)
            except RuntimeError:
                pass

    def save_pet_setting(self, key: str, value: str):
        cfg = self._cfg()
        if cfg is None:
            return
        try:
            cfg.set_config("Pet", key, value, validate=False)
        except Exception:
            pass
        self.refresh_pet_settings()

    # ---------- 位置 / 尺寸持久化 ----------

    def _cfg(self):
        if self._ws is None:
            return None
        return getattr(self._ws, "cfg", None)

    def _restore_position(self):
        cfg = self._cfg()
        saved_pos = ""
        saved_size = ""
        if cfg is not None:
            try:
                saved_pos = cfg.get_config("Writing", "pet_win_pos", "") or ""
                saved_size = cfg.get_config("Writing", "pet_win_size", "") or ""
            except Exception:
                pass

        # 读 region + scale（兼容旧 pet_win_size）
        region = ""
        scale_str = ""
        if cfg is not None:
            try:
                region = cfg.get_config("Writing", "pet_region", "") or ""
                scale_str = cfg.get_config("Writing", "pet_scale", "") or ""
                # 旧格式兼容：pet_win_size = 0/1/2 → head/upper/full
                if not region and saved_size:
                    try:
                        lv = int(saved_size)
                        region = {0: "head", 1: "upper", 2: "full"}.get(
                            lv, "full")
                    except (TypeError, ValueError):
                        region = "full"
            except Exception:
                pass

        if region in self.REGION_RATIOS:
            self._display_region = region
        else:
            self._display_region = "full"

        try:
            if scale_str:
                self._scale_factor = max(
                    self.SCALE_MIN,
                    min(self.SCALE_MAX, float(scale_str)))
        except (TypeError, ValueError):
            self._scale_factor = 1.0

        # 同步给 face
        if self._face is not None:
            try:
                self._face.set_display_region(self._display_region)
            except Exception:
                pass

        self.W, self.H = self._calc_window_size()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.resize(self.W, self.H)
        if self._face is not None:
            self._face.setFixedSize(self.W, self.H)

        # 位置
        pos = None
        if saved_pos:
            try:
                parts = saved_pos.split(",")
                if len(parts) == 2:
                    pos = QPoint(int(parts[0]), int(parts[1]))
            except (TypeError, ValueError):
                pos = None

        if pos is None:
            # 默认：主屏右下角
            pos = self._default_corner_pos()
        self.move(self._clamp_to_screen(pos, self.W, self.H))

        # 恢复服装（旧版本存档里可能是已下架的服装，回退默认）
        saved_outfit = ""
        if cfg is not None:
            try:
                saved_outfit = cfg.get_config(
                    "Writing", "pet_outfit", "") or ""
            except Exception:
                pass
        if saved_outfit not in OUTFIT_KEYS:
            saved_outfit = DEFAULT_OUTFIT
        if self._face is not None:
            self._face.set_outfit(saved_outfit)

    def _default_corner_pos(self) -> QPoint:
        """主屏右下角默认位置。"""
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is None:
            return QPoint(800, 500)
        geo = screen.availableGeometry()
        return QPoint(
            geo.x() + geo.width() - self.W - 24,
            geo.y() + geo.height() - self.H - 60,
        )

    def snap_to_corner(self):
        """贴回主屏右下角。右键菜单"贴回右下角"用。"""
        target = self._clamp_to_screen(
            self._default_corner_pos(), self.width(), self.height())
        self.move(target)
        self._save_position()

    def _save_position(self):
        cfg = self._cfg()
        if cfg is None:
            return
        try:
            cfg.set_config("Writing", "pet_win_pos",
                           f"{self.x()},{self.y()}", validate=False)
            cfg.set_config("Writing", "pet_region",
                           self._display_region, validate=False)
            cfg.set_config("Writing", "pet_scale",
                           f"{self._scale_factor:.2f}", validate=False)
        except Exception:
            pass

    def _calc_window_size(self) -> tuple:
        """根据 region + scale + 原图比例算窗口尺寸。"""
        ratio = self.REGION_RATIOS.get(self._display_region, 1.0)
        w = int(self.BASE_WIDTH * self._scale_factor)
        aspect = 1.0
        if self._face is not None:
            try:
                aspect = self._face.get_base_aspect()
            except Exception:
                pass
        h = int(w * aspect * ratio)
        w = max(80, min(w, 1200))
        h = max(60, min(h, 1200))
        return w, h

    def set_region(self, region: str):
        """切换显示范围：head / upper / full。"""
        if region not in self.REGION_RATIOS:
            return
        if region == self._display_region:
            return
        self._display_region = region
        if self._face is not None:
            try:
                self._face.set_display_region(region)
            except Exception:
                pass
        self._apply_size()

    def set_scale(self, factor: float):
        """微调大小（0.5 ~ 2.0）。"""
        factor = max(self.SCALE_MIN, min(self.SCALE_MAX, factor))
        if abs(factor - self._scale_factor) < 0.01:
            return
        self._scale_factor = factor
        self._apply_size()

    def set_outfit(self, outfit: str):
        """换装：切立绘并写入 config.ini，下次启动恢复。"""
        if outfit not in OUTFIT_KEYS:
            return
        if self.get_outfit() == outfit:
            return
        if self._face is not None:
            self._face.set_outfit(outfit)
        cfg = self._cfg()
        if cfg is not None:
            try:
                cfg.set_config("Writing", "pet_outfit", outfit, validate=False)
            except Exception:
                pass

    def _apply_size(self):
        """按新的 region + scale 重算窗口尺寸，动画过渡。"""
        new_w, new_h = self._calc_window_size()

        # 中心锚定
        old_cx = self.x() + self.W // 2
        old_cy = self.y() + self.H // 2
        new_x = old_cx - new_w // 2
        new_y = old_cy - new_h // 2
        target = self._clamp_to_screen(QPoint(new_x, new_y), new_w, new_h)

        self._size_anim.stop()
        self._size_anim.setStartValue(self.geometry())
        self._size_anim.setEndValue(QRect(target, QSize(new_w, new_h)))
        self._size_anim.start()

        self.W, self.H = new_w, new_h
        self._save_position()

    @staticmethod
    def _clamp_to_screen(pos: QPoint, w: int, h: int) -> QPoint:
        """把位置夹到某个屏幕的可视区内。"""
        app = QApplication.instance()
        if app is None:
            return pos
        # 找包含 pos 的屏幕；找不到用主屏
        screen = app.screenAt(pos)
        if screen is None:
            screen = app.primaryScreen()
        if screen is None:
            return pos
        geo = screen.availableGeometry()
        x = max(geo.x(), min(pos.x(), geo.x() + geo.width() - w))
        y = max(geo.y(), min(pos.y(), geo.y() + geo.height() - h))
        return QPoint(x, y)

    def _get_pet_geometry(self):
        return self.geometry()

    def _set_pet_geometry(self, rect):
        self.setGeometry(rect)
        self._on_geometry_applied()

    petGeometry = Property(QRect, _get_pet_geometry, _set_pet_geometry)

    def _on_geometry_applied(self):
        if self._face is not None:
            try:
                self._face.setFixedSize(self.width(), self.height())
            except RuntimeError:
                pass
        # 内心独白气泡是顶层窗口：宠物动/变尺寸时重新定位
        self._follow_overlays()
        self.geometry_changed.emit()

    def move(self, *args):
        # 所有移动路径（拖动 / 自主漫游 / 抛扔物理 / 贴回角落）都经过
        # 这里。在 super().move() 后同步对齐头顶气泡，比依赖 moveEvent
        # 更可靠——部分 QPA 平台（如 offscreen）不保证投递 MoveEvent。
        super().move(*args)
        self._follow_overlays()

    def moveEvent(self, event):  # noqa: N802
        # 兜底：petGeometry 属性动画走的是 setGeometry（C++ 层，
        # 不经过上面的 Python move()），位置变化由这里接住。
        super().moveEvent(event)
        self._follow_overlays()

    def _follow_overlays(self):
        """宠物移动/缩放时，把顶层叠加窗口（头顶气泡）重新对齐。"""
        thought = getattr(self, "_thought", None)
        if thought is not None and thought.isVisible():
            try:
                self._reposition_thought()
            except RuntimeError:
                pass

    # ---------- 折叠 / 展开 ----------

    def set_collapsed(self, collapsed: bool):
        """已废弃：尺寸由双击 / 右键菜单控制。保留方法体便于外部兼容。"""
        return

    def _do_collapse(self):
        """已废弃：不再自动折叠。"""
        return

    def _on_hover_timeout(self):
        if not self.underMouse():
            return
        if self._hover_shy_given:
            return
        if self._face is None:
            return
        if getattr(self._face, "_base_state", "") == self._face.STATE_IDLE:
            self._hover_shy_given = True
            self._face.set_state(self._face.STATE_SHY, hold_ms=2000)

    # ---------- 鼠标交互 ----------

    def enterEvent(self, event):
        self._hover_timer.start()
        # 不再设 curious modifier：它在 enter/leave 反复触发时
        # 会让呼吸/摇摆参数来回切换，视觉上抖动。
        # 悬停反馈改由两条独立路径承担：
        # - 3 秒后 _on_hover_timeout 切 shy 表情
        # - 光标离得远时视线跟随（死区已排除鼠标在猫娘身上的情况）
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_timer.stop()
        self._hover_shy_given = False
        self._click_streak = 0
        self._click_streak_timer.stop()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        # 飞行 / 落地反弹途中不接受抓起，让物理跑完
        if self._flying:
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._drag_last_pos = event.globalPosition().toPoint()
            self._drag_last_ms = time.monotonic()
            self._drag_vx = 0.0
            self._drag_vy = 0.0
            self._click_streak += 1
            self._click_streak_timer.start()

            # 显式抓鼠标：鼠标一旦移出窗口矩形，Qt 会停止把 mouseMove
            # 送过来（尤其主窗口在前台时）。grabMouse 强制接管鼠标路由，
            # 直到 releaseMouse 释放。
            try:
                self.grabMouse()
            except RuntimeError:
                pass

            if self._click_streak == 1:
                # 单击：轻弹一下。真正的"摸头"等 400ms 后确认。
                if self._face is not None:
                    try:
                        self._face.tap()
                    except RuntimeError:
                        pass
            else:
                # 双击 / 三连击：轻弹 + love 爱心表情。
                # 不触发 pet 动作（避免 pet_head 动画覆盖爱心）
                if self._face is not None:
                    try:
                        self._face.tap()
                        self._face.set_state(
                            self._face.STATE_LOVE, hold_ms=2500)
                    except RuntimeError:
                        pass

        if event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        cur = event.globalPosition().toPoint()
        new_pos = cur - self._drag_offset
        # 超过阈值才算拖动（避免点击时的手抖被当拖动）
        if not self._dragging:
            if (new_pos - self.pos()).manhattanLength() < 5:
                return
            self._dragging = True
            self._click_streak_timer.stop()  # 取消单击
            self._click_streak = 0
            # 被抓起来了：身体下垂、朝运动方向倾斜
            if self._face is not None:
                try:
                    self._face.set_carried(True, 0.0)
                    # 播拖拽动画
                    from .pet_system import whale_pool
                    self._face.play_anim(whale_pool.pick_drag(), loop=True, is_action=True)
                except RuntimeError:
                    pass

        # 采样拖动速度（用于身体倾斜 + 松手抛扔）
        now = time.monotonic()
        if self._drag_last_pos is not None:
            dt = now - self._drag_last_ms
            if dt >= 0.02:
                self._drag_vx = (
                    cur.x() - self._drag_last_pos.x()) / dt
                self._drag_vy = (
                    cur.y() - self._drag_last_pos.y()) / dt
                self._drag_last_pos = cur
                self._drag_last_ms = now
                if self._face is not None:
                    try:
                        self._face.set_carried(True, self._drag_vx)
                    except RuntimeError:
                        pass

        # 夹到屏幕可视区
        clamped = self._clamp_to_screen(new_pos, self.width(), self.height())
        self.move(clamped)
        self.geometry_changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 释放 grabMouse（与 mousePressEvent 中的 grabMouse 配对）
            try:
                self.releaseMouse()
            except RuntimeError:
                pass
            if self._dragging:
                self._dragging = False
                if self._face is not None:
                    try:
                        self._face.set_carried(False)
                        # 停拖拽动画，回到当前状态循环
                        self._face.stop_anim()
                    except RuntimeError:
                        pass
                self._save_position()
                # 松手速度够快 → 抛出去；普通拖动 → 原地放下
                speed = math.hypot(self._drag_vx, self._drag_vy)
                if speed >= self._PHYS_THROW_SPEED:
                    self._start_flying(self._drag_vx, self._drag_vy)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    # ---------- 抛扔物理 ----------

    def _start_flying(self, vx: float, vy: float):
        """以给定初速度把墨灵抛出去。"""
        speed = math.hypot(vx, vy)
        if speed <= 0:
            return
        # 限制最大初速度，避免一帧飞出屏幕外
        scale = min(1.0, self._PHYS_MAX_SPEED / speed)
        self._phys_vx = vx * scale
        # 向上甩时给点加成感，但仍受重力支配
        self._phys_vy = min(vy * scale, self._PHYS_MAX_SPEED)
        self._phys_bounces = 0
        self._phys_last_ms = time.monotonic()
        self._flying = True
        if self._face is not None:
            try:
                self._face.set_state(
                    self._face.STATE_SURPRISED, hold_ms=6000)
            except RuntimeError:
                pass
        self._phys_timer.start()

    def _physics_step(self):
        """16ms 一步：重力积分 + 屏幕边界反弹。"""
        if not self._flying:
            return
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._phys_last_ms))
        self._phys_last_ms = now

        self._phys_vy += self._PHYS_GRAVITY * dt
        x = self.x() + self._phys_vx * dt
        y = self.y() + self._phys_vy * dt

        w, h = self.width(), self.height()
        app = QApplication.instance()
        screen = app.screenAt(QPoint(int(x), int(y))) if app else None
        if screen is None and app is not None:
            screen = app.primaryScreen()
        if screen is None:
            self._stop_flying()
            return
        geo = screen.availableGeometry()
        left = geo.x()
        top = geo.y()
        right = geo.x() + geo.width() - w
        bottom = geo.y() + geo.height() - h

        # 左右墙
        if x < left:
            x = float(left)
            self._phys_vx = abs(self._phys_vx) * self._PHYS_WALL_DAMP
        elif x > right:
            x = float(right)
            self._phys_vx = -abs(self._phys_vx) * self._PHYS_WALL_DAMP

        # 天花板
        if y < top:
            y = float(top)
            self._phys_vy = abs(self._phys_vy) * self._PHYS_WALL_DAMP

        # 地面：速度够大就反弹，否则落地
        if y >= bottom:
            y = float(bottom)
            if (self._phys_vy > self._PHYS_STOP_VY
                    and self._phys_bounces < self._PHYS_MAX_BOUNCES):
                self._phys_vy = -self._phys_vy * self._PHYS_FLOOR_DAMP
                self._phys_vx *= 0.7
                self._phys_bounces += 1
            else:
                self._stop_flying()
                return

        self.move(int(x), int(y))
        self.geometry_changed.emit()

    # ---------- 自主漫游 ----------

    def set_economy(self, economy):
        self._economy = economy

    def _roam_allowed_by_config(self) -> bool:
        ws = self._ws
        if ws is None:
            return True
        try:
            v = str(ws.cfg.get_config("Pet", "roam_enabled", "1") or "1")
            return v.strip() not in ("0", "false", "False", "no")
        except Exception:
            return True

    def _on_roam_pulse(self):
        """brain 觉得"主人在看但没在打字" → 自己溜达一会儿。"""
        if self._flying or self._dragging or self._roam_active:
            return
        if not self.isVisible() or self._face is None:
            return
        if not self._roam_allowed_by_config():
            return
        ws = self._ws
        if ws is not None:
            bubble = getattr(ws, "_pet_bubble", None)
            if bubble is not None:
                try:
                    if bubble.isVisible():
                        return
                except RuntimeError:
                    return
        self._roam_begin()

    def _roam_begin(self):
        self._roam_active = True
        self._roam_anchor_x = self.x()
        self._roam_steps_left = random.randint(2, 3)
        self._roam_returning = False
        self._roam_next()

    def _roam_next(self):
        if not self._roam_active:
            return
        # 睡着/动作进行中不能继续溜达（睡眠可能在半途中开始）
        if self._brain is not None:
            try:
                if (getattr(self._brain, "_sleeping", False)
                        or self._brain._current_action is not None):
                    self._roam_end()
                    return
            except RuntimeError:
                self._roam_end()
                return
        anchor = self._roam_anchor_x

        if self._roam_steps_left <= 0 or self._roam_returning:
            target = anchor
            self._roam_returning = True
        else:
            direction = random.choice([-1, 1])
            target = self.x() + direction * random.randint(60, 130)
            # 不离开锚点半径
            target = max(anchor - self._ROAM_RADIUS,
                         min(anchor + self._ROAM_RADIUS, target))
            # 到边了 / 距离太近 → 掉头往锚点方向走
            if abs(target - self.x()) < 14:
                target = anchor + (1 if self.x() < anchor else -1) * 80
                target = max(anchor - self._ROAM_RADIUS,
                             min(anchor + self._ROAM_RADIUS, target))
            self._roam_steps_left -= 1

        # 夹到当前屏幕
        clamped = self._clamp_to_screen(
            QPoint(int(target), self.y()), self.width(), self.height())
        target = float(clamped.x())
        if abs(target - self.x()) < 4:
            # 无处可走，结束本轮
            self._roam_end()
            return

        self._roam_target_x = target
        self._roam_dir = 1 if target > self.x() else -1
        walk_ms = int(abs(target - self.x())
                      / self._ROAM_SPEED * 1000) + 120
        try:
            self._face.start_procedural(
                "walking", walk_ms, self._roam_dir)
        except RuntimeError:
            self._roam_end()
            return
        self._roam_timer.start()

    def _roam_step_move(self):
        if not self._roam_active:
            return
        # 中途睡着/被动作打断：立刻停步，不能让睡觉图继续滑
        if self._brain is not None:
            try:
                if (getattr(self._brain, "_sleeping", False)
                        or self._brain._current_action is not None):
                    self._roam_end()
                    return
            except RuntimeError:
                self._roam_end()
                return
        step = self._ROAM_SPEED * 0.016
        x = float(self.x())
        if abs(self._roam_target_x - x) <= step:
            self.move(int(self._roam_target_x), self.y())
            self._roam_timer.stop()
            if self._face is not None:
                try:
                    self._face.stop_procedural()
                except RuntimeError:
                    pass
            self.geometry_changed.emit()
            # 途中偶尔发个呆、冒一句话
            if not self._roam_returning and random.random() < 0.4:
                try:
                    self.say(random.choice(self._ROAM_YAWN_LINES), 1900)
                except RuntimeError:
                    pass
            self._roam_pause_timer.start(random.randint(700, 1900))
            return
        x += self._roam_dir * step
        self.move(int(x), self.y())
        self.geometry_changed.emit()

    def abort_roam(self):
        """主人有操作（打字/点击/拖拋/对话）→ 立刻原地停下。"""
        if not self._roam_active:
            return
        self._roam_active = False
        self._roam_timer.stop()
        self._roam_pause_timer.stop()
        if self._face is not None:
            try:
                self._face.stop_procedural()
            except RuntimeError:
                pass

    def _roam_end(self):
        self._roam_active = False
        self._roam_timer.stop()
        self._roam_pause_timer.stop()
        if self._face is not None:
            try:
                self._face.stop_procedural()
            except RuntimeError:
                pass

    def _stop_flying(self):
        """落定：停物理、存档、头晕一会儿。"""
        if not self._flying:
            return
        self._flying = False
        self._phys_timer.stop()
        self._phys_vx = 0.0
        self._phys_vy = 0.0
        # 最终位置夹回屏幕（可能在屏间边界飞过头）
        self.move(self._clamp_to_screen(
            self.pos(), self.width(), self.height()))
        self._save_position()
        if self._economy is not None:
            try:
                self._economy.mark_thrown()
            except RuntimeError:
                pass
        if self._face is not None:
            try:
                # 落地后晕一下：worried 表情 + 残余摇头晃脑由随机倾斜接管
                self._face.set_state(
                    self._face.STATE_WORRIED, hold_ms=2200)
            except RuntimeError:
                pass

    def _reset_click_streak(self):
        """400ms 无新点击后：确认点击语义。

        - 单击 → 摸头（brain 层，播放 pet_head 动画）
        - 双击/三连击 → 只保留爱心表情（press 里已设），
          不触发 pet 动作——否则 pet_head 动画会把爱心顶掉，
          视觉上看起来像"同时触发两个效果"
        """
        streak = self._click_streak
        self._click_streak = 0
        if streak == 1:
            self.do_action("pet")
        # streak >= 2：不 do_action，press 里设的 love 表情独立生效

    def _show_context_menu(self, global_pos):
        menu = QMenu(self)
        t = {}
        if self._ws is not None:
            try:
                t = getattr(self._ws.editor, "_theme", {}) or {}
            except Exception:
                t = {}
        bg = t.get("editor_bg", "#fcfaf4")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")

        # 统一风格：图标左对齐、项间紧凑、hover 淡金底
        menu.setStyleSheet(
            f"QMenu {{ background-color: {bg}; color: {text_c};"
            f" border: 1px solid {qss_alpha(muted, 0x30)};"
            f" border-radius: 10px; padding: 6px 4px;"
            f" font-size: 12.5px; }}"
            f"QMenu::item {{ padding: 6px 20px 6px 6px;"
            f" border-radius: 6px; min-height: 22px; }}"
            f"QMenu::item:selected {{"
            f" background-color: {qss_alpha(accent, 0x1E)};"
            f" color: {text_c}; }}"
            f"QMenu::item:disabled {{ color: {muted}; }}"
            f"QMenu::separator {{ height: 1px;"
            f" background: {qss_alpha(muted, 0x20)};"
            f" margin: 5px 12px; }}"
            f"QMenu::icon {{ padding-left: 6px; }}"
        )

        # ========== 主操作 ==========
        act_chat = menu.addAction(
            qta.icon("fa5s.comment-dots", color=accent),
            "对话面板")
        act_status = menu.addAction(
            qta.icon("fa5s.heart", color=accent),
            "墨灵状态…")
        act_shop = menu.addAction(
            qta.icon("fa5s.store", color=accent),
            "墨灵小屋（商店/任务/成就）…")

        menu.addSeparator()

        # ========== 外观 ==========
        region_menu = menu.addMenu(
            qta.icon("fa5s.crop-alt", color=text_c), "显示范围")
        for key, label in (
            ("head",  "只露头"),
            ("upper", "半身"),
            ("full",  "全身"),
        ):
            a = region_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(key == self._display_region)
            a.setData(("__region__", key))

        scale_menu = menu.addMenu(
            qta.icon("fa5s.search-plus", color=text_c),
            f"大小（{self._scale_factor:.2f}x）")
        for delta, label in (
            (0.20,  "大两级"),
            (0.10,  "大一级"),
            (-0.10, "小一级"),
            (-0.20, "小两级"),
        ):
            a = scale_menu.addAction(label)
            a.setData(("__scale_delta__", delta))
        scale_menu.addSeparator()
        a_reset = scale_menu.addAction("重置为 1.00x")
        a_reset.setData(("__scale_reset__", None))

        outfit_menu = menu.addMenu(
            qta.icon("fa5s.tshirt", color=text_c), "换装")
        current_outfit = self.get_outfit()
        for key, label in OUTFIT_DEFS:
            mark = "● " if key == current_outfit else "○ "
            act = outfit_menu.addAction(mark + label)
            act.setData(("__outfit__", key))

        menu.addSeparator()

        # ========== 道具箱 ==========
        care_menu = menu.addMenu(
            qta.icon("fa5s.gift", color=text_c), "道具箱")
        care_acts = self.fill_care_menu(
            care_menu, icon_color=text_c, muted_color=muted)

        menu.addSeparator()

        # ========== AI 工具 ==========
        quick_menu = menu.addMenu(
            qta.icon("fa5s.magic", color=text_c),
            "选中文本快捷操作")
        quick_acts = {}
        for action, label, icon_name in QUICK_ACTIONS:
            icon = qta.icon(icon_name, color=text_c)
            act = quick_menu.addAction(icon, label)
            quick_acts[act] = action

        act_compare = menu.addAction(
            qta.icon("fa5s.random", color=text_c),
            "对比改写…")

        menu.addSeparator()

        # ========== 漫游开关 ==========
        act_roam = menu.addAction(
            qta.icon("fa5s.shoe-prints", color=text_c),
            "空闲时自主溜达")
        act_roam.setCheckable(True)
        _roam_on = True
        _cfg = self._cfg()
        if _cfg is not None:
            try:
                _v = str(_cfg.get_config("Pet", "roam_enabled", "1") or "1")
                _roam_on = _v.strip() not in ("0", "false", "False", "no")
            except Exception:
                pass
        act_roam.setChecked(_roam_on)
        act_roam.toggled.connect(
            lambda checked:
                self.save_pet_setting(
                    "roam_enabled", "1" if checked else "0"))

        # ========== 窗口 ==========
        act_snap = menu.addAction(
            qta.icon("fa5s.thumbtack", color=text_c),
            "贴回右下角")
        act_hide = menu.addAction(
            qta.icon("fa5s.eye-slash", color=text_c),
            "隐藏墨灵")
        act_show_main = menu.addAction(
            qta.icon("fa5s.window-maximize", color=text_c),
            "显示主窗口")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return

        if chosen == act_chat:
            self.request_chat.emit()
            return

        if chosen == act_status:
            self._show_status_dialog()
            return

        if chosen == act_shop:
            self._show_shop_dialog()
            return

        data = chosen.data() if chosen is not None else None
        # 显示范围
        if isinstance(data, tuple) and data[0] == "__region__":
            self.set_region(data[1])
            return
        # 大小微调
        if isinstance(data, tuple) and data[0] == "__scale_delta__":
            self.set_scale(self._scale_factor + data[1])
            return
        if isinstance(data, tuple) and data[0] == "__scale_reset__":
            self.set_scale(1.0)
            return
        # 换装
        if isinstance(data, tuple) and data[0] == "__outfit__":
            self.set_outfit(data[1])
            return

        # 道具箱
        if chosen in care_acts:
            from utils.logger import get_logger
            get_logger().info(f"[Pet] 道具点击 → {care_acts[chosen]}")
            self.do_action(care_acts[chosen])
            return

        # 快捷操作
        if chosen in quick_acts:
            self.request_quick_action.emit(quick_acts[chosen])
            return

        if chosen == act_compare:
            try:
                if self._ws is not None:
                    self._ws.open_rewrite_dialog()
            except Exception:
                pass
            return

        if chosen == act_snap:
            self.snap_to_corner()
            return

        if chosen == act_hide:
            self.hide()
            return

        if chosen == act_show_main:
            try:
                from PySide6.QtWidgets import QApplication
                for wgt in QApplication.topLevelWidgets():
                    if wgt.metaObject().className() == "MainWindow":
                        wgt.showNormal()
                        wgt.activateWindow()
                        wgt.raise_()
                        break
            except Exception:
                pass
            return

    def _show_status_dialog(self):
        """打开墨灵状态面板（养成数值可视化）。"""
        try:
            from ui.pet_system.status_dialog import PetStatusDialog
            ws = self._ws
            brain = getattr(ws, "_pet_brain", None)
            stats = getattr(ws, "stats", None)
            theme = {}
            if ws is not None:
                try:
                    theme = getattr(ws.editor, "_theme", {}) or {}
                except Exception:
                    theme = {}
            parent = ws.window() if ws is not None else self
            dlg = PetStatusDialog(brain, stats, theme, parent=parent)
            dlg.exec()
        except Exception as e:
            logger.warning(f"打开墨灵状态面板失败: {e}")

    def _show_shop_dialog(self):
        """打开墨灵小屋：商店 / 背包 / 每日任务 / 成就。

        parent 必须给主窗口而不是宠物窗：宠物窗停在屏幕角落，
        QDialog 默认相对父窗口居中，挂在宠物窗上就会弹在右下角。
        """
        try:
            from ui.pet_system.shop_dialog import ShopDialog
            if self._economy is None:
                QMessageBox.information(
                    self, "墨灵小屋",
                    "经济系统还没准备好，稍等一下再试试~")
                return
            theme = {}
            parent = self
            if self._ws is not None:
                parent = self._ws.window() or self._ws
                try:
                    theme = getattr(self._ws.editor, "_theme", {}) or {}
                except Exception:
                    theme = {}
            dlg = ShopDialog(
                self._economy, theme, parent=parent,
                on_use=lambda key: self.do_action(key))
            dlg.exec()
        except Exception as e:
            logger.warning(f"打开墨灵小屋失败: {e}")

    # ---------- 顶部菜单栏复用入口 ----------

    def open_chat(self):
        """打开对话面板（与单击墨灵等价）。"""
        self.request_chat.emit()

    def open_status_panel(self):
        self._show_status_dialog()

    def open_shop(self):
        self._show_shop_dialog()

    # 道具箱菜单项定义（右键菜单 / 顶部「墨灵」菜单共用）
    _CARE_ITEMS = (
        ("drink", "fa5s.tint",         "喝口水", "水分 +40 · 心情 +3"),
        ("feed",  "fa5s.fish",         "小鱼干", "饱腹 +35 · 心情 +5"),
        ("pet",   "fa5s.hand-paper",   "摸摸头", "心情 +10"),
        ("clean", "fa5s.bath",         "洗香香", "清洁 +40 · 心情 +3"),
        ("play",  "fa5s.baseball-ball","毛线球", "心情 +20 · 精力 -10"),
        ("sleep", "fa5s.moon",         "睡觉觉", "恢复精力"),
    )
    _CARE_CONSUMABLES = {"drink", "feed", "clean", "play"}

    def fill_care_menu(self, menu, icon_color=None,
                       muted_color=None) -> dict:
        """把道具动作填入给定 QMenu，返回 {action: action_key}。

        库存/前置条件实时计算，顶部菜单每次 aboutToShow 可重建。
        动作的触发连接由调用方负责（右键走 menu.exec 分支，
        顶部菜单连 triggered）。
        """
        care_acts = {}
        try:
            from ui.pet_system.actions import ACTIONS as _ACT
            state = self._brain.state if self._brain is not None else None
        except Exception:
            _ACT = {}
            state = None
        inv = {}
        if self._economy is not None:
            try:
                inv = self._economy.inventory
            except RuntimeError:
                inv = {}

        for key, icon_name, label, hint in self._CARE_ITEMS:
            act_info = _ACT.get(key)
            usable = True
            if act_info is not None and state is not None:
                usable = act_info.can_run(state)
            count = inv.get(key) if key in self._CARE_CONSUMABLES else None
            count_txt = f"×{count}" if count is not None else ""
            if count is not None and count <= 0:
                usable = False
                count_txt = "（没了，去小屋买）"
            color = icon_color if usable else (muted_color or icon_color)
            kwargs = {"color": color} if color else {}
            a = menu.addAction(
                qta.icon(icon_name, **kwargs),
                f"{label} {count_txt}    {hint}")
            a.setEnabled(usable)
            if usable:
                care_acts[a] = key
        return care_acts

    # ---------- 外部接口（保持原 API） ----------

    def set_pet_state(self, state: str, hold_ms: int = 3000):
        self._face.set_state(state, hold_ms)

    def set_base_pet_state(self, state: str):
        self._face.set_base_state(state)

    def set_hold_expanded(self, hold: bool):
        """兼容旧接口：现在尺寸由用户手动控制，不再自动折叠。"""
        self._hold_expanded = bool(hold)

    def tap(self):
        # 主人在打字 → 墨灵停止自主溜达、重置漫游计时
        self.abort_roam()
        if self._brain is not None:
            try:
                self._brain.note_presence()
            except RuntimeError:
                pass
        if self._face is not None:
            try:
                self._face.tap()
            except RuntimeError:
                pass

    def set_offset_y(self, dy: float):
        if self._face is not None:
            try:
                self._face.set_offset_y(dy)
            except RuntimeError:
                pass

    def set_modifier(self, name: str, hold_ms: int = 0):
        if self._face is not None:
            try:
                self._face.set_modifier(name, hold_ms)
            except RuntimeError:
                pass

    def get_outfit(self) -> str:
        if self._face is None:
            return DEFAULT_OUTFIT
        return getattr(self._face, "_outfit", DEFAULT_OUTFIT)

    # ---------- Brain 接入 ----------

    _BRAIN_SIGNALS = (
        "action_started",
        "action_finished",
        "bubble_requested",
        "sleep_changed",
        "roam_pulse",
        "work_status",
        "whisper",
        "balance",
    )

    def set_brain(self, brain):
        """绑定 PetBrain。订阅其信号（先断开旧 brain，切作品时防泄漏）。"""
        from utils.logger import get_logger
        log = get_logger()
        log.info(f"[Pet] set_brain called, brain={brain}, "
                 f"face={self._face}")
        old = getattr(self, "_brain", None)
        if old is not None and old is not brain:
            for sig_name in self._BRAIN_SIGNALS:
                try:
                    getattr(old, sig_name).disconnect(
                        getattr(self, f"_on_brain_{sig_name}")
                        if sig_name != "roam_pulse" else self._on_roam_pulse)
                except (RuntimeError, AttributeError):
                    pass
        self._brain = brain
        if brain is None or self._face is None:
            log.warning(f"[Pet] set_brain 提前返回："
                        f"brain={brain}, face={self._face}")
            return
        for sig_name, slot in (
            ("action_started", self._on_brain_action_started),
            ("action_finished", self._on_brain_action_finished),
            ("bubble_requested", self._on_brain_bubble),
            ("sleep_changed", self._on_brain_sleep_changed),
            ("roam_pulse", self._on_roam_pulse),
            ("work_status", self._on_brain_work_status),
            ("whisper", self._on_brain_whisper),
            ("balance", self._on_brain_balance),
        ):
            try:
                getattr(brain, sig_name).connect(slot)
                log.info(f"[Pet] {sig_name} 已连接")
            except Exception as e:
                log.warning(f"[Pet] {sig_name} 连接失败: {e}")

    def _on_brain_action_started(self, action_key: str, anim_key: str):
        """Brain 开始动作 → face 播放动画。

        素材策略：
        - 有多帧序列帧 → 播序列帧（真动画）
        - 只有单张静态帧或没有素材 → 程序化动作补偿（位移/旋转曲线）
          + 对应临时表情，保证补素材之前动作也"看得出来"
        """
        from utils.logger import get_logger
        log = get_logger()
        log.info(f"[Pet] action_started: {action_key} / {anim_key}")
        if self._face is None:
            log.warning("[Pet] action_started: face 为 None")
            return
        try:
            # 真正的摸头动作累积"好感热度"（打字 tap 不走这里）
            if action_key == "pet":
                self._face.add_pet_heat()

            # 不同道具给不同的临时表情，用户能看出差异。
            fallback = {
                "drink": self._face.STATE_HAPPY,
                "feed":  self._face.STATE_HAPPY,
                "pet":   self._face.STATE_LOVE,
                "clean": self._face.STATE_SHY,
                "play":  self._face.STATE_EXCITED,
                "sleep": self._face.STATE_SLEEPY,
                "whisper": self._face.STATE_IDLE,
                "balance": self._face.STATE_HAPPY,
                "random": self._face.STATE_IDLE,
            }.get(action_key, "")

            # 动作时长（与 actions.py 保持一致，程序化动作要跟它等长）
            try:
                from ui.pet_system.actions import ACTIONS as _A
                act = _A.get(action_key)
                duration_ms = int(act.duration_ms or 3000) if act else 3000
            except Exception:
                duration_ms = 3000

            # 瞬态动作（碎碎念/余额/随机）播一次就停，不进持续动作队列
            transient = action_key in ("whisper", "balance", "random")

            frames = load_frames(
                self._anim_root(), self._face._outfit, anim_key)
            # 多帧 / 带时长的帧才算真动画；单张静态帧走程序化补偿
            animated = bool(frames) and (
                len(frames) > 1 or frames[0][1] > 0)

            self._face.stop_procedural()
            if animated:
                self._face.play_anim(
                    anim_key, fallback_state=fallback, frames=frames,
                    loop=not transient, is_action=not transient)
            elif action_key != "sleep":
                # sleep 由 sleep_changed 切 sleepy base 状态，不做蹦跳
                self._face.start_procedural(anim_key, duration_ms)
                if fallback:
                    # hold_ms=2500：2.5 秒后自动回 base 状态（通常是 idle）。
                    # 绝不能传 0 —— set_state 里 hold_ms<=0 会停掉超时定时器，
                    # 表情永久卡在那个临时状态上。
                    self._face.set_state(fallback, hold_ms=2500)
        except Exception as e:
            import traceback
            log.warning(f"[Pet] action_started 失败: {e}\n"
                        f"{traceback.format_exc()}")

    def _on_brain_action_finished(self, action_key: str):
        """动作结束 → 回 base 状态。"""
        if self._face is None:
            return
        try:
            self._face.stop_anim()
            self._face.stop_procedural()
        except Exception:
            pass

    def _on_brain_bubble(self, text: str):
        """Brain 请求台词 → 宠物头顶冒个泡。"""
        from utils.logger import get_logger
        log = get_logger()
        log.info(f"[Pet] bubble_requested: {text!r}")
        try:
            # 4 秒：用户点菜单后视线从菜单移到宠物头顶大约 1-2 秒，
            # 2.5 秒可能已经错过。动作类气泡给足反应时间。
            ok = self.say(text, duration_ms=4000)
            log.info(f"[Pet] say() 返回 {ok}")
        except Exception as e:
            import traceback
            log.warning(f"[Pet] say() 抛异常: {e}\n"
                        f"{traceback.format_exc()}")

    def _on_brain_sleep_changed(self, sleeping: bool):
        if self._face is None:
            return
        try:
            if sleeping:
                # 睡着就不能再溜达：停位移、停走路程序化动画
                self.abort_roam()
            self._face.set_base_state(
                self._face.STATE_SLEEPY if sleeping
                else self._face.STATE_IDLE)
        except Exception:
            pass

    def _on_brain_work_status(self, level: int):
        """工作状态变化 → 播对应工作动画。"""
        if self._face is None:
            return
        from .pet_system import whale_pool
        anim_key = whale_pool.pick_work_status(level)
        self._face.play_anim_once(anim_key)

    def _on_brain_whisper(self, text: str):
        """碎碎念 → 气泡（动画由 action_started 触发）。"""
        if text:
            self.say(text, duration_ms=4000)

    def _on_brain_balance(self, level: int):
        """余额变化（动画由 action_started 触发）。"""
        pass

    def _on_brain_state_changed(self):
        """状态变化时的钩子（当前无额外动作）。

        状态由 brain 自主管理，需求触发时通过 workspace._on_pet_need
        切表情 + 冒台词。
        """
        pass

    def _anim_root(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "pet")

    def visual_rect_global(self) -> QRect:
        """猫娘**实际绘制**区域的全局矩形。

        气泡按这个定位，而不是整个窗口 geometry——
        窗口带了透明留白，直接用 geometry 会让气泡离猫娘很远。
        """
        if self._face is None:
            return self.geometry()
        r = self._face.drawn_rect()
        # 转成全局坐标
        top_left_global = self._face.mapToGlobal(r.topLeft())
        return QRect(top_left_global, r.size())

    # ---------- 内心独白 ----------

    def say(self, text: str, duration_ms: int = 2500,
            on_click=None) -> bool:
        """在宠物头顶冒一句话。返回是否成功显示。

        on_click 给定时，点击这条气泡执行它（章末点评/卡文捋剧情等
        CTA）；不给则沿用默认行为——打开对话面板。
        """
        from utils.logger import get_logger
        log = get_logger()
        if not text:
            return False
        thought = getattr(self, "_thought", None)
        if thought is None:
            try:
                thought = _ThoughtBubble()
            except Exception as e:
                log.warning(f"[Pet] 创建气泡失败: {e}")
                return False
            self._thought = thought
        try:
            thought.set_theme(getattr(self, "_theme", None) or {})
            thought.on_clicked = (on_click if callable(on_click)
                                  else self.request_chat.emit)
            thought.say(text, duration_ms)
            self._reposition_thought()
            log.info(f"[Pet] 气泡已显示: {text!r} @ "
                     f"({thought.x()},{thought.y()}) "
                     f"{thought.width()}x{thought.height()}")
            return True
        except RuntimeError:
            self._thought = None
            return False

    def _reposition_thought(self):
        thought = getattr(self, "_thought", None)
        if thought is None:
            return
        bw = thought.width()
        bh = thought.height()
        # 按"猫娘实际绘制区域"定位，而不是整个窗口——
        # 窗口有透明留白，直接用 self.x()/self.y() 会飘很高
        vis = self.visual_rect_global()
        x = vis.center().x() - bw // 2
        y = vis.top() - bh - 4
        # 屏幕边界 clamp（沿用原逻辑）
        app = QApplication.instance()
        screen = app.screenAt(QPoint(x, y)) if app else None
        if screen is None and app is not None:
            screen = app.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.x() + 8, min(x, geo.x() + geo.width() - bw - 8))
            if y < geo.y() + 8:
                y = vis.bottom() + 4   # 空间不够放下面
        thought.move(x, y)

    def show_coin_toast(self, text: str = "+1"):
        """宠物身上飘一个「古钱币 +N」（获得墨玉时的即时反馈）。"""
        old = self._coin_toast
        if old is not None:
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        wrap = QWidget(self)
        wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        ico = QLabel()
        ico.setPixmap(jade_pixmap(15))
        ico.setStyleSheet("background: transparent;")
        row.addWidget(ico)
        lab = QLabel(text)
        lab.setStyleSheet(
            "color: #b7893f; font-weight: 800; font-size: 15px;"
            " background: transparent;")
        row.addWidget(lab)
        wrap.adjustSize()
        start = QPoint(self.width() // 2 - wrap.width() // 2,
                       int(self.height() * 0.20))
        wrap.move(start)
        eff = QGraphicsOpacityEffect(wrap)
        eff.setOpacity(0.95)
        wrap.setGraphicsEffect(eff)
        wrap.show()
        wrap.raise_()

        from PySide6.QtCore import QParallelAnimationGroup
        grp = QParallelAnimationGroup(wrap)
        a_op = QPropertyAnimation(eff, b"opacity", wrap)
        a_op.setDuration(1000)
        a_op.setKeyValueAt(0.65, 0.9)
        a_op.setEndValue(0.0)
        a_pos = QPropertyAnimation(wrap, b"pos", wrap)
        a_pos.setDuration(1000)
        a_pos.setStartValue(start)
        a_pos.setEndValue(start + QPoint(0, -38))
        a_pos.setEasingCurve(QEasingCurve.OutQuad)
        grp.addAnimation(a_op)
        grp.addAnimation(a_pos)
        grp.finished.connect(wrap.deleteLater)
        wrap._anim = grp
        self._coin_toast = wrap
        grp.start(QAbstractAnimation.DeleteWhenStopped)

    # ---------- 用户交互（转发到 brain） ----------

    def do_action(self, action_key: str):
        """右键菜单或单击触发动作。"""
        from utils.logger import get_logger
        log = get_logger()
        if self._brain is None:
            log.warning(f"[Pet] do_action({action_key})：brain 为 None")
            return
        # 消耗品门控：前置条件不满足直接放弃（道具不能白花）；
        # 库存 0 不触发动作，提示去商店
        if self._economy is not None:
            from ui.pet_system.actions import ACTIONS as _A
            act = _A.get(action_key)
            if act is not None and not act.can_run(self._brain.state):
                return
            if not self._economy.consume(action_key):
                try:
                    self.say("这个用完啦，去墨灵小屋买点吧？", 2600)
                except RuntimeError:
                    pass
                return
        self.abort_roam()
        log.info(f"[Pet] do_action({action_key}) → brain")
        self._brain.request_action(action_key)

    def apply_theme(self, theme: dict):
        t = theme or {}
        self._theme = t
        self._face.set_colors(
            t.get("accent", "#b08a4e"),
            t.get("text", "#3b342a"))
        self.update()


# ============================================================
# @ 引用输入框
# ============================================================
class _InputEdit(QTextEdit):
    mention_triggered = Signal()
    popup_move = Signal(int)
    popup_accept = Signal()
    popup_cancel = Signal()
    submitted = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_open = False

    def set_popup_open(self, opened: bool):
        self._popup_open = bool(opened)

    def keyPressEvent(self, event):
        if self._popup_open:
            if event.key() == Qt.Key_Up:
                self.popup_move.emit(-1); event.accept(); return
            if event.key() == Qt.Key_Down:
                self.popup_move.emit(1); event.accept(); return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier):
                self.popup_accept.emit(); event.accept(); return
            if event.key() == Qt.Key_Escape:
                self.popup_cancel.emit(); event.accept(); return

        # @ 引用弹窗未打开时，Esc 关闭整个气泡；
        # 弹窗打开时 Esc 只关弹窗（上面的分支已 return）。
        if event.key() == Qt.Key_Escape and not self._popup_open:
            self.close_requested.emit()
            event.accept()
            return

        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            event.accept()
            return

        if event.key() == Qt.Key_At and not (
            event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            super().keyPressEvent(event)
            QTimer.singleShot(0, self.mention_triggered.emit)
            return

        super().keyPressEvent(event)


# ============================================================
# 历史对话对话框
# ============================================================
class _HistoryDialog(QDialog):
    conversation_selected = Signal(str)

    def __init__(self, current_key: str, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme or {}
        self._current_key = current_key or ""
        self.setWindowTitle("历史对话")
        self.setMinimumSize(420, 460)
        self._build_ui()
        self._apply_theme()
        self._refresh()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_pick)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_menu)
        lay.addWidget(self.list_widget, 1)
        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        lay.addLayout(row)

    def _refresh(self):
        self.list_widget.clear()
        try:
            convs = ai_conversations.list_conversations()
        except Exception as e:
            logger.warning(f"读取历史对话失败: {e}")
            convs = []
        if not any(c.get("key") == "" for c in convs):
            convs.insert(0, {"key": "", "updated_at": "", "message_count": 0})
        for c in convs:
            key = c.get("key", "")
            name = "通用对话" if not key else key
            n = c.get("message_count", 0)
            when = (c.get("updated_at", "") or "")[:16].replace("T", " ")
            text = f"{name}    {n} 条    {when}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, key)
            self.list_widget.addItem(item)
            if key == self._current_key:
                self.list_widget.setCurrentItem(item)
                QTimer.singleShot(
                    0, lambda it=item: self.list_widget.scrollToItem(it))

    def _on_pick(self, item):
        key = item.data(Qt.UserRole)
        self.conversation_selected.emit(key or "")
        self.accept()

    def _on_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        key = item.data(Qt.UserRole) or ""
        name = "通用对话" if not key else key
        menu = QMenu(self)
        act_del = menu.addAction(f"删除「{name}」")
        if menu.exec(self.list_widget.viewport().mapToGlobal(pos)) == act_del:
            if QMessageBox.question(
                self, "删除对话",
                f"确定删除「{name}」的历史对话吗？此操作不可恢复。"
            ) == QMessageBox.Yes:
                try:
                    ai_conversations.clear_messages(key)
                except Exception as e:
                    logger.warning(f"删除对话失败: {e}")
                self._refresh()

    def _apply_theme(self):
        t = self._theme or {}
        bg = t.get("bg", "#f7f2e8")
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        editor_bg = t.get("editor_bg", "#fcfaf4")
        accent = t.get("accent", "#b08a4e")
        self.setStyleSheet(
            f"QDialog {{ background-color: {bg}; }}"
            f"QLabel {{ color: {text}; }}"
            f"QListWidget {{ background-color: {editor_bg}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 8px; padding: 4px; outline: none; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:hover {{ background: {qss_alpha(accent, 0x10)}; }}"
            f"QListWidget::item:selected {{"
            f" background: {qss_alpha(accent, 0x22)}; color: {accent}; }}")


# ============================================================
# AI 助手气泡
# ============================================================
class _PetBubble(QFrame):
    closed = Signal()
    request_started = Signal()
    request_finished = Signal()

    COMPACT_W = 360
    COMPACT_H = 320
    EXPANDED_W = 440
    EXPANDED_H = 660

    def __init__(self, parent=None, workspace=None):
        super().__init__(parent)
        self.setObjectName("petBubble")
        # Qt.Tool：对话面板不进任务栏 / Alt+Tab；仍可正常激活、
        # 接收键盘输入（工具窗口不等于不接受焦点）
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        self._ws = workspace
        self._theme = {}
        self._compact = True
        self._tail_x = 180
        self._tail_bottom = True   # 尾巴在底部（猫娘在上方）；贴顶时翻到顶部
        self._closing = False

        self._messages = [{"role": "system", "content": get_system_prompt()}]
        self._worker = None
        self._request_seq = 0
        self._ai_bubble = None
        self._buffer_parts = []
        self._last_rendered_text = ""
        self._last_request = None
        self._request_selection_range = None
        self._show_insert = False
        self._auto_scroll = True
        self._conversation_key = ""
        self._output_font_size = 13

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(80)
        self._flush_timer.timeout.connect(self._flush_stream)

        self._context_builder = None
        self._last_context_items = []
        self._explicit_refs = []
        self._mention_popup = None

        self.setFixedSize(self.COMPACT_W, self.COMPACT_H)
        self._build_ui()
        self.refresh_key_state()
        self._load_conversation()

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 24, 16, 24)
        root.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("墨灵")
        title.setObjectName("petBubbleTitle")
        head.addWidget(title)
        self.ctx_badge = QLabel("")
        self.ctx_badge.setObjectName("petCtxBadge")
        self.ctx_badge.setToolTip("AI 本次请求会看到的资料")
        self.ctx_badge.setVisible(False)
        head.addWidget(self.ctx_badge)
        head.addStretch()

        self.more_btn = QPushButton()
        self.more_btn.setFixedSize(24, 24)
        self.more_btn.setObjectName("petBubbleIconBtn")
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setIcon(qta.icon(
            "fa5s.ellipsis-h",
            color=(self._theme or {}).get("muted", "#9a8e76")))
        self.more_btn.setIconSize(QSize(13, 13))
        self.more_btn.setToolTip("更多：历史 / 导出 / 清空")
        self.more_btn.clicked.connect(self._show_more_menu)
        head.addWidget(self.more_btn)

        self.expand_btn = QPushButton()
        self.expand_btn.setFixedSize(24, 24)
        self.expand_btn.setObjectName("petBubbleIconBtn")
        self.expand_btn.setCursor(Qt.PointingHandCursor)
        self.expand_btn.clicked.connect(self._toggle_expand)
        head.addWidget(self.expand_btn)

        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setObjectName("petBubbleIconBtn")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setIcon(qta.icon(
            "fa5s.times",
            color=(self._theme or {}).get("muted", "#9a8e76")))
        self.close_btn.setIconSize(QSize(13, 13))
        self.close_btn.setToolTip("关闭")
        self.close_btn.clicked.connect(self.request_close)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self.msg_container = QWidget()
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(0, 0, 4, 0)
        self.msg_layout.setSpacing(6)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(self.msg_container)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.scroll, 1)

        self._empty_hint = QLabel("主人，跟我说点什么吧～")
        self._empty_hint.setObjectName("petBubbleEmpty")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self.msg_layout.insertWidget(0, self._empty_hint)

        # 预设行（快捷操作已移到猫娘右键菜单）
        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        self.preset_btn = QToolButton()
        self.preset_btn.setIcon(qta.icon(
            "fa5s.bullseye",
            color=(self._theme or {}).get("muted", "#9a8e76")))
        self.preset_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.preset_btn.setText(" 预设 ")
        self.preset_btn.setPopupMode(QToolButton.InstantPopup)
        self.preset_btn.setCursor(Qt.PointingHandCursor)
        self.preset_btn.setToolTip("一键使用 AI 写作模板")
        self._presets = load_presets()
        preset_menu = QMenu(self.preset_btn)
        preset_menu.aboutToShow.connect(self._build_preset_menu)
        preset_menu.triggered.connect(self._on_preset_clicked)
        self.preset_btn.setMenu(preset_menu)
        preset_row.addWidget(self.preset_btn)
        preset_row.addStretch()
        root.addLayout(preset_row)

        self.input_edit = _InputEdit()
        self.input_edit.setObjectName("petBubbleInput")
        self.input_edit.setFixedHeight(62)
        self.input_edit.textChanged.connect(self._update_token_hint)
        self.input_edit.mention_triggered.connect(self._on_mention_triggered)
        self.input_edit.popup_move.connect(self._on_popup_move)
        self.input_edit.popup_accept.connect(self._on_popup_accept)
        self.input_edit.popup_cancel.connect(self._on_popup_cancel)
        self.input_edit.submitted.connect(self._on_send)
        self.input_edit.close_requested.connect(self.request_close)
        root.addWidget(self.input_edit)

        send_row = QHBoxLayout()
        send_row.setSpacing(6)
        self.tip_label = QLabel("")
        self.tip_label.setObjectName("petTipLabel")
        send_row.addWidget(self.tip_label)
        send_row.addStretch()

        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("petBubbleSendBtn")
        self.stop_btn.setFixedSize(32, 32)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setIcon(qta.icon("fa5s.stop", color="#ffffff"))
        self.stop_btn.setToolTip("停止（保留已收到内容）")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.hide()
        send_row.addWidget(self.stop_btn)

        self.send_btn = QPushButton()
        self.send_btn.setObjectName("petBubbleSendBtn")
        self.send_btn.setFixedSize(32, 32)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setIcon(qta.icon("fa5s.arrow-up", color="#ffffff"))
        self.send_btn.setToolTip("发送")
        self.send_btn.clicked.connect(self._on_send)
        send_row.addWidget(self.send_btn)
        root.addLayout(send_row)

        self._refresh_expand_icon()

    def _refresh_expand_icon(self):
        t = self._theme or {}
        muted = t.get("muted", "#9a8e76")
        icon = ("fa5s.angle-double-up" if self._compact
                else "fa5s.angle-double-down")
        tip = "展开完整对话" if self._compact else "收起"
        self.expand_btn.setIcon(qta.icon(icon, color=muted))
        self.expand_btn.setIconSize(QSize(14, 14))
        self.expand_btn.setToolTip(tip)

    def _toggle_expand(self):
        self._compact = not self._compact
        if self._compact:
            self.setFixedSize(self.COMPACT_W, self.COMPACT_H)
        else:
            self.setFixedSize(self.EXPANDED_W, self.EXPANDED_H)
        self._refresh_expand_icon()
        # 气泡自身没有 parent，宠物位置从 workspace 拿。
        # 必须用 visual_rect_global（猫娘实际绘制区域），
        # 不能用 geometry()——窗口带透明留白，会差一截导致气泡跳位。
        ws = self._ws
        if ws is not None:
            pf = getattr(ws, "_pet_float", None)
            if pf is not None:
                try:
                    self._position_near_pet(
                        pf.visual_rect_global(), animate=False)
                except RuntimeError:
                    pass

    def refresh_key_state(self):
        ok = has_api_key()
        if ok:
            self.input_edit.setPlaceholderText(
                "跟墨灵聊聊吧… @ 引用章节/角色，Enter 发送")
        else:
            self.input_edit.setPlaceholderText(
                "尚未配置 API Key（设置 → AI 接口）")

    def reload_system_prompt(self):
        self._messages[0] = {"role": "system", "content": get_system_prompt()}

    def set_output_font_size(self, size: int):
        try:
            self._output_font_size = max(12, min(20, int(size)))
        except (TypeError, ValueError):
            return
        for i in range(self.msg_layout.count()):
            w = self.msg_layout.itemAt(i).widget()
            if isinstance(w, _Bubble):
                try:
                    w.set_font_size(self._output_font_size)
                except RuntimeError:
                    pass

    def _on_send(self):
        self._explicit_refs = []
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if not has_api_key():
            self.refresh_key_state()
            b = self._add_bubble(
                "assistant",
                "主人，还没配置 API Key 呢。\n"
                "请到 设置 → AI 接口 里配置一下吧。")
            # 直接给跳转入口，不让用户自己翻菜单
            ws = self._ws
            open_sig = getattr(ws, "open_settings", None)
            if open_sig is not None:
                btn = b.add_action_button("打开设置")
                btn.clicked.connect(lambda: open_sig.emit())
            return
        if self._worker is not None:
            # 静默 return 会让用户以为按钮坏了，明确提示
            self.tip_label.setText("正在等待回复，请稍候…")
            QTimer.singleShot(3000, self._update_token_hint)
            return

        # 零 token 的显式记忆：「记住，xxx」「帮我记一下：xxx」
        # 「别忘了 xxx」——消息照常发给 AI，同时本地直接落一条事实。
        self._maybe_remember_explicit(text)

        ref = self._get_selection_text()
        self.input_edit.clear()
        if ref:
            ref_trim = (ref if len(ref) <= 4000
                        else ref[:4000] + "\n…（已截断）")
            composed = (
                f"{text}\n\n"
                f"【引用的正文】（请基于这段正文完成我的上述要求；"
                f"需要修改时直接给出修改后的完整正文，不要解释）\n"
                f"{ref_trim}"
            )
            display = f"{text}\n已引用 {len(ref)} 字正文"
            self._save_selection_range()
            self._start_request(
                composed, display_text=display, show_insert=True)
        else:
            self._start_request(text)

    def _start_request(self, api_content, *, display_text=None,
                       show_insert=False):
        if not has_api_key():
            self.refresh_key_state()
            return
        if self._worker is not None:
            return
        if display_text is None:
            display_text = api_content
        self._last_request = (api_content, display_text, show_insert)
        self._show_insert = show_insert

        self._messages.append({"role": "user", "content": api_content})
        self._trim_messages()
        idx = len(self._messages) - 1
        user_b = self._add_bubble("user", display_text, message_index=idx)
        btn_edit = user_b.add_action_button("编辑")
        btn_edit.clicked.connect(
            lambda _=False, b=user_b, t=api_content:
                self._edit_user_message(b, t))

        self._ai_bubble = self._add_bubble("assistant", thinking=True)
        self._set_running(True)
        self.request_started.emit()

        cfg = load_ai_config()
        send = self._build_send_messages(api_content)
        self._request_seq += 1
        seq = self._request_seq
        self._worker = AIWorker(
            send, cfg["api_key"], cfg["base_url"], cfg["model"],
            temperature=cfg.get("creative_temperature", 0.85),
            stream=True,
        )
        self._buffer_parts = []
        self._last_rendered_text = ""
        self._flush_timer.stop()
        self._worker.chunk_received.connect(
            lambda p, s=seq: self._on_chunk(p, s))
        self._worker.finished_ok.connect(
            lambda t, s=seq: self._on_finished(t, s))
        self._worker.failed.connect(
            lambda m, s=seq: self._on_failed(m, s))
        self._worker.start()

    def _set_running(self, running: bool):
        self.send_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.input_edit.setEnabled(not running)

    def _on_chunk(self, piece: str, seq: int):
        if seq != self._request_seq:
            return
        if not self._bubble_alive(self._ai_bubble):
            return
        self._buffer_parts.append(piece)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

        import time as _t
        now_ms = int(_t.monotonic() * 1000)
        if now_ms - getattr(self, "_last_pet_tap_ms", 0) >= 400:
            self._last_pet_tap_ms = now_ms
            ws = self._ws
            pf = getattr(ws, "_pet_float", None) if ws is not None else None
            if pf is not None:
                try:
                    pf.tap()
                except RuntimeError:
                    pass

    def _flush_stream(self):
        if not self._bubble_alive(self._ai_bubble):
            self._buffer_parts = []
            self._last_rendered_text = ""
            return
        rendered = "".join(self._buffer_parts)
        if rendered == self._last_rendered_text:
            return
        self._last_rendered_text = rendered
        try:
            self._ai_bubble.set_text(rendered)
            if self._auto_scroll:
                sb = self.scroll.verticalScrollBar()
                sb.setValue(sb.maximum())
        except RuntimeError:
            self._ai_bubble = None
            self._buffer_parts = []

    def _on_finished(self, full_text: str, seq: int):
        if seq != self._request_seq:
            return
        if not self._bubble_alive(self._ai_bubble):
            return
        self._flush_timer.stop()
        bubble = self._ai_bubble
        if full_text and full_text != self._last_rendered_text:
            try:
                bubble.set_text(full_text)
            except RuntimeError:
                pass
        self._messages.append({"role": "assistant", "content": full_text})
        self._worker = None
        self._set_running(False)
        # 先通知 workspace 回 idle 底层状态，再叠加情绪临时状态
        self.request_finished.emit()

        # 回复结束 → 按文本情绪给墨灵一个对应表情
        # （纯本地关键词识别，不增加任何 API 开销）
        if full_text.strip():
            try:
                from ui.pet_system.persona import detect_emotion
                emo = detect_emotion(full_text)
                setter = getattr(self._ws, "set_pet_state", None)
                if emo and callable(setter):
                    setter(emo, hold_ms=2500)
            except Exception:
                pass

        if full_text.strip() and self._bubble_alive(bubble):
            try:
                b_copy = bubble.add_action_button("复制")
                b_copy.clicked.connect(
                    lambda _=False, t=full_text:
                        QApplication.clipboard().setText(t))
                if self._show_insert:
                    self._add_insert_or_replace(bubble, full_text)
                req = self._last_request
                if req is not None:
                    b_regen = bubble.add_action_button("重新生成")
                    b_regen.clicked.connect(self._make_regen_callback(req))
                # 回复涉及的章节给一个"回到原文"入口（B6）
                self._add_chapter_chips(bubble, full_text)
            except RuntimeError:
                pass

        self._refresh_ctx_badge()
        self._save_conversation()
        # 对话记忆：攒够几轮后由提炼器异步、低 token 地沉淀事实（B2）
        self._maybe_extract_memory()

    def _on_failed(self, msg: str, seq: int):
        if seq != self._request_seq:
            return
        if not self._bubble_alive(self._ai_bubble):
            return
        self._flush_timer.stop()
        # 流式过程中失败：已收到的文字必须保留（与 _stop 一致），
        # 只追加一行中断原因；一个字都没收到时才删气泡弹错误。
        partial = "".join(self._buffer_parts).strip()
        bubble = self._ai_bubble
        if partial:
            try:
                bubble.set_text(partial + f"\n\n（已中断：{msg}）")
                self._messages.append(
                    {"role": "assistant", "content": partial})
                b_copy = bubble.add_action_button("复制")
                b_copy.clicked.connect(
                    lambda _=False, t=partial:
                        QApplication.clipboard().setText(t))
                if self._show_insert:
                    self._add_insert_or_replace(bubble, partial)
            except RuntimeError:
                pass
        else:
            try:
                bubble.set_text("")
                bubble.deleteLater()
            except RuntimeError:
                pass
            self._add_bubble("error", msg)
        self._ai_bubble = None
        self._worker = None
        self._set_running(False)
        self.request_finished.emit()
        self._save_conversation()

    def _stop(self):
        self._flush_timer.stop()
        if self._worker is not None:
            try:
                from utils.ai_client import retire_worker
                retire_worker(self._worker)
            except Exception:
                pass
            self._worker = None
        text = "".join(self._buffer_parts)
        if self._bubble_alive(self._ai_bubble):
            try:
                if text.strip():
                    self._ai_bubble.set_text(text + "\n\n（已停止）")
                    self._messages.append(
                        {"role": "assistant", "content": text})
                    b_copy = self._ai_bubble.add_action_button("复制")
                    b_copy.clicked.connect(
                        lambda _=False, t=text:
                            QApplication.clipboard().setText(t))
                    if self._show_insert:
                        self._add_insert_or_replace(self._ai_bubble, text)
                else:
                    self._ai_bubble.set_text("")
                    self._ai_bubble.deleteLater()
                    self._ai_bubble = None
            except RuntimeError:
                self._ai_bubble = None
        self._request_seq += 1
        self._set_running(False)
        self.request_finished.emit()
        self._save_conversation()

    def _make_regen_callback(self, request):
        def cb(_=False):
            if self._last_request is request and self._worker is None:
                self._regenerate(request)
        return cb

    def _regenerate(self, request):
        if self._worker is not None or not has_api_key():
            return
        api, display, show_insert = request
        if self._messages and self._messages[-1]["role"] == "assistant":
            self._messages.pop()
        if self._messages and self._messages[-1]["role"] == "user":
            self._messages.pop()
        self._remove_last_bubbles(2)
        self._start_request(api, display_text=display,
                            show_insert=show_insert)

    def _edit_user_message(self, bubble, original_text):
        idx = bubble.message_index
        if idx is None or idx < 1 or idx >= len(self._messages):
            return
        if self._messages[idx].get("role") != "user":
            return
        del self._messages[idx:]
        self._remove_from_bubble(bubble)
        self.input_edit.setPlainText(original_text)
        self.input_edit.moveCursor(QTextCursor.End)

    def _add_insert_or_replace(self, bubble, text):
        sel = self._request_selection_range
        if sel is not None:
            btn = bubble.add_action_button("替换选区")
            btn.setToolTip("用 AI 结果替换发起请求时选中的那段文字")
            btn.clicked.connect(
                lambda _=False, t=text, s=sel:
                    self._replace_range(s[0], s[1], t, s[2], s[3]))
        else:
            btn = bubble.add_action_button("插入正文")
            btn.clicked.connect(lambda _=False, t=text: self._insert_text(t))

    def _insert_text(self, text: str):
        ws = self._ws
        if ws is None:
            return
        editor = getattr(ws, "editor", None)
        if editor is not None and hasattr(editor, "insert_text"):
            editor.insert_text(text)

    def _replace_range(self, start: int, end: int, text: str,
                       cid=None, expected: str = ""):
        ws = self._ws
        if ws is None:
            return
        try:
            ws._replace_editor_range(start, end, text, cid, expected)
        except Exception as e:
            logger.warning(f"替换选区失败: {e}")

    def _get_selection_text(self) -> str:
        ws = self._ws
        if ws is None:
            return ""
        editor = getattr(ws, "editor", None)
        if editor is None or not editor.has_selection():
            return ""
        return editor.selected_text().replace("\u2029", "\n").strip()

    def _save_selection_range(self):
        """记录发起请求时的选区：(start, end, 章节id, 选中文本)。

        章节 id 与选中文本用于「替换选区」时防止跨章节/编辑漂移误覆盖。
        """
        ws = self._ws
        editor = getattr(ws, "editor", None) if ws else None
        if editor is None or not editor.has_selection():
            self._request_selection_range = None
            return
        try:
            c = editor.edit.textCursor()
            snippet = c.selectedText().replace("\u2029", "\n")
            self._request_selection_range = (
                c.selectionStart(),
                c.selectionEnd(),
                getattr(ws, "current_chapter_id", None),
                snippet,
            )
        except Exception:
            self._request_selection_range = None

    # ---------- 长期记忆 / 章节跳转（B 批次） ----------

    # 「记住，xxx」「帮我记一下：xxx」「别忘了 xxx」
    _REMEMBER_PATTERNS = (
        re.compile(
            r"^(?:请\s*)?(?:帮我|替我)?\s*记(?:住|一下)"
            r"[了啊呀吧呢呗哦么哇嘛的]*[，,：:、\s]*(.+)$"),
        re.compile(r"^别忘了[啊呀吧呢哦嘛]?[，,：:、\s]*(.+)$"),
    )

    def ask(self, prompt: str):
        """外部入口：墨灵主动 CTA 被点击后，带完整诉求直接发起对话。"""
        prompt = (prompt or "").strip()
        if not prompt or self._worker is not None:
            return
        self._explicit_refs = []
        self._start_request(prompt)

    def _maybe_remember_explicit(self, text: str):
        try:
            memory = getattr(self._ws, "_pet_memory", None)
            if memory is None:
                return
            body = ""
            for pat in self._REMEMBER_PATTERNS:
                m = pat.match(text)
                if m:
                    body = m.group(1).strip(" 。.！!？?")
                    break
            if len(body) < 4:
                return
            if len(body) > 80:
                body = body[:80]
            if memory.add_fact(body, src="explicit"):
                tip = f"墨灵记住了：{body[:24]}"
                self.tip_label.setText(tip)
                QTimer.singleShot(4000, self._update_token_hint)
        except Exception:
            pass

    def _maybe_extract_memory(self):
        try:
            extractor = getattr(self._ws, "_pet_chat_extractor", None)
            if extractor is not None:
                extractor.maybe_extract(list(self._messages))
        except Exception:
            pass

    def _add_chapter_chips(self, bubble, reply_text: str):
        ws = self._ws
        goto = getattr(ws, "goto_chapter_quote", None)
        if ws is None or not callable(goto):
            return
        ids, chips = set(), []
        for item in (self._last_context_items or []):
            if getattr(item, "kind", "") != "chapter":
                continue
            key = getattr(item, "key", "")
            if "::" not in key:
                continue
            cid = key.split("::", 1)[1]
            if cid in ids:
                continue
            ids.add(cid)
            # ContextBuilder 的 label 固定加了"第"前缀，而章节默认名
            # 本身已是"第N章"，这里去重避免按钮显示成"第第N章"
            label = getattr(item, "label", "") or "章节"
            if label.startswith("第第"):
                label = label[1:]
            chips.append((cid, label))
            if len(chips) >= 2:
                break
        for cid, label in chips:
            short = label if len(label) <= 10 else label[:10] + "…"
            btn = bubble.add_action_button(f"回到原文 · {short}")
            try:
                btn.setIcon(qta.icon(
                    "fa5s.bookmark",
                    color=(self._theme or {}).get("muted", "#9a8e76")))
            except Exception:
                pass
            btn.clicked.connect(
                lambda _=False, c=cid, t=reply_text: goto(c, t))

    def _quick_action(self, action: str):
        """右键菜单触发的快捷操作入口。"""
        self._explicit_refs = []
        ref = self._get_selection_text()
        if not ref:
            msg = "⚠ 请先在编辑器里选中要处理的正文"
            self.tip_label.setText(msg)
            QTimer.singleShot(3000, self._update_token_hint)
            if not self.isVisible() and self._ws is not None:
                try:
                    self._ws.show_status_message(msg, 3000)
                except Exception:
                    pass
            return
        if len(ref) > 4000:
            ref = ref[:4000]
        api = build_quick_prompt(action, ref)
        label = next((l for a, l, *_ in QUICK_ACTIONS if a == action),
                     action)
        display = f"{label} · 已引用 {len(ref)} 字正文"
        self._save_selection_range()
        self._start_request(api, display_text=display, show_insert=True)

    def _build_preset_menu(self):
        menu = self.preset_btn.menu()
        menu.clear()
        self._presets = load_presets()
        for label, key, _tpl in self._presets:
            act = menu.addAction(label)
            act.setData(key)
        menu.addSeparator()
        manage = menu.addAction(
            qta.icon("fa5s.cog",
                     color=(self._theme or {}).get("muted", "#9a8e76")),
            "管理预设…")
        manage.setData("__manage__")

    def _on_preset_clicked(self, action):
        key = action.data()
        if key == "__manage__":
            dlg = _PresetManageDialog(self)
            dlg.exec()
            return
        tpl = None
        for _label, k, t in self._presets:
            if k == key:
                tpl = t
                break
        if not tpl:
            return
        filled = self._fill_preset_context(tpl)
        self.input_edit.setPlainText(filled)
        self.input_edit.moveCursor(QTextCursor.End)
        self.input_edit.setFocus()

    def _fill_preset_context(self, template: str) -> str:
        needs = any(
            ph in template
            for ph in ("{last_para}", "{summary}", "{outlines}", "{style}"))
        if not needs:
            return template
        ws = self._ws
        if ws is None:
            return template
        try:
            text = ws.editor.get_text() if hasattr(ws, "editor") else ""
        except Exception:
            text = ""
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        last_para = paras[-1] if paras else ""
        outlines = ""
        if getattr(ws, "work", None):
            for c in ws.work.chapters[:3]:
                outlines += (
                    f"- {c.get('title', '')}："
                    f"{ws.work.read_chapter(c['id'])[:100]}\n")
        ctx = {
            "last_para": last_para,
            "summary": last_para[:200] if last_para else "（暂无正文）",
            "outlines": outlines or "（暂无前文）",
            "style": ws.work.title if getattr(ws, "work", None) else "不限",
        }
        try:
            return template.format(**ctx)
        except (KeyError, IndexError):
            return template

    # ---------- @ 引用 ----------

    def _collect_mention_items(self):
        items = []
        ws = self._ws
        if ws is None:
            return items
        work = getattr(ws, "work", None)
        if work is None:
            return items
        cur_cid = getattr(ws, "current_chapter_id", "") or ""
        for ch in (work.chapters or [])[:20]:
            cid = ch.get("id", "")
            title = ch.get("title", "未命名")
            if cid == cur_cid:
                items.append(
                    ("chapter", cid, f"{title}  · 当前章", "当前打开的章节"))
            else:
                items.append(("chapter", cid, title, "章节正文"))
        try:
            chars = work.load_characters() or []
            for c in chars[:20]:
                name = (c.get("name") or "").strip()
                if name:
                    role = (c.get("role") or "").strip()
                    items.append(
                        ("character", name, name,
                         f"角色卡 · {role}" if role else "角色卡"))
        except Exception:
            pass
        return items

    def _on_mention_triggered(self):
        items = self._collect_mention_items()
        if not items:
            return
        if self._mention_popup is None:
            self._mention_popup = _MentionPopup(self)
            self._mention_popup.item_selected.connect(self._on_mention_selected)
        self._mention_popup.set_items(items, self._theme)
        rect = self.input_edit.rect()
        pos = self.input_edit.mapToGlobal(QPoint(0, 0))
        popup_h = 340
        pos.setY(pos.y() - popup_h - 4)
        pos.setX(pos.x() + rect.left())
        self._mention_popup.move(pos)
        self._mention_popup.show()
        self.input_edit.set_popup_open(True)

    def _on_popup_move(self, delta):
        if self._mention_popup and self._mention_popup.isVisible():
            self._mention_popup.move_selection(delta)

    def _on_popup_accept(self):
        if self._mention_popup and self._mention_popup.isVisible():
            self._mention_popup.accept_current()

    def _on_popup_cancel(self):
        if self._mention_popup and self._mention_popup.isVisible():
            self._mention_popup.close()
            self.input_edit.set_popup_open(False)

    def _on_mention_selected(self, kind, ident):
        self.input_edit.set_popup_open(False)
        text = self.input_edit.toPlainText()
        cursor = self.input_edit.textCursor()
        pos = cursor.position()
        at_pos = text.rfind("@", 0, pos)
        if at_pos >= 0:
            cursor.setPosition(at_pos)
            cursor.setPosition(pos, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            self.input_edit.setTextCursor(cursor)
        label = ident
        if kind == "chapter":
            ws = self._ws
            work = getattr(ws, "work", None) if ws else None
            if work is not None:
                ch = work.get_chapter(ident)
                if ch:
                    label = ch.get("title", ident)
        self.input_edit.insertPlainText(f"{label} ")
        self.input_edit.setFocus()
        if not hasattr(self, "_explicit_refs"):
            self._explicit_refs = []
        self._explicit_refs.append((kind, ident))

    # ---------- 上下文注入 ----------

    def _get_context_builder(self):
        ws = self._ws
        if ws is None:
            return None
        work = getattr(ws, "work", None)
        if work is None:
            return None
        if (self._context_builder is not None
                and getattr(self._context_builder, "work", None) is work):
            return self._context_builder
        self._context_builder = ContextBuilder(work)
        return self._context_builder

    def _get_current_chapter_id(self) -> str:
        ws = self._ws
        if ws is None:
            return ""
        return getattr(ws, "current_chapter_id", "") or ""

    def _build_send_messages(self, user_input: str) -> list:
        base_system = (
            self._messages[0]["content"]
            if self._messages and self._messages[0].get("role") == "system"
            else get_system_prompt())
        context_items = []
        context_text = ""
        builder = self._get_context_builder()
        if builder is not None:
            try:
                context_items = builder.build(
                    user_input,
                    extra_refs=self._explicit_refs or None,
                    current_chapter_id=self._get_current_chapter_id(),
                )
                rendered = ContextBuilder.render(context_items)
                if rendered.strip():
                    context_text = rendered
            except Exception as e:
                logger.warning(f"ContextBuilder 构造失败: {e}")
        self._last_context_items = context_items

        if context_text.strip():
            system_content = (
                f"{base_system}\n\n"
                f"【当前作品资料】（供参考，不要在回复里重复）\n"
                f"{context_text}")
        else:
            system_content = base_system

        # 墨灵的实时状态（关系阶段/心情需求/今日码字），
        # 让她"带着自己的状态"对话，而不是通用聊天机器人
        try:
            ws = self._ws
            pet_block = ws.pet_ai_context() if ws is not None else ""
        except Exception:
            pet_block = ""
        if pet_block:
            system_content = f"{system_content}\n\n{pet_block}"

        send = [{"role": "system", "content": system_content}]
        send.extend(self._messages[1:])
        return send

    def _refresh_ctx_badge(self):
        items = self._last_context_items or []
        if not items:
            self.ctx_badge.setVisible(False)
            return
        total = sum(it.char_count for it in items)
        self.ctx_badge.setText(f"{len(items)} 项 · {total:,} 字")
        self.ctx_badge.setVisible(True)

    # ---------- 更多菜单 ----------

    def _show_more_menu(self):
        t = self._theme or {}
        bg = t.get("editor_bg", "#fcfaf4")
        text_c = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {bg}; color: {text_c};"
            f" border: 1px solid {qss_alpha(muted, 0x33)};"
            f" border-radius: 10px; padding: 6px; }}"
            f"QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 5px; }}"
            f"QMenu::item:selected {{"
            f" background-color: {qss_alpha(accent, 0x22)}; }}")
        act_history = menu.addAction("历史对话…")
        menu.addSeparator()
        act_copy = menu.addAction("复制全部对话")
        act_md = menu.addAction("导出为 Markdown…")
        act_txt = menu.addAction("导出为 TXT…")
        menu.addSeparator()
        act_clear = menu.addAction("清空当前对话")

        chosen = menu.exec(
            self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen == act_history:
            self._show_history()
        elif chosen == act_copy:
            self._copy_all()
        elif chosen == act_md:
            self._export("md")
        elif chosen == act_txt:
            self._export("txt")
        elif chosen == act_clear:
            if QMessageBox.question(
                self, "清空对话", "确定清空当前对话吗？"
            ) == QMessageBox.Yes:
                self.clear_conversation()

    def _show_history(self):
        dlg = _HistoryDialog(self._conversation_key, self._theme, self)
        dlg.conversation_selected.connect(self.set_conversation_key)
        dlg.exec()

    def _get_conversation_text(self, fmt="md") -> str:
        msgs = [m for m in self._messages
                if m.get("role") in ("user", "assistant")]
        if not msgs:
            return ""
        name = self._conversation_key or "通用对话"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = []
        if fmt == "md":
            lines.append("# AI 对话记录\n")
            lines.append(f"**作品**：{name}  ")
            lines.append(f"**导出时间**：{ts}  \n---\n")
            for m in msgs:
                role = "用户" if m["role"] == "user" else "AI"
                lines.append(f"## {role}\n")
                lines.append(m["content"])
                lines.append("\n---\n")
        else:
            lines.append(f"AI 对话记录 · {name} · {ts}\n" + "=" * 40)
            for m in msgs:
                role = "用户" if m["role"] == "user" else "AI"
                lines.append(f"【{role}】")
                lines.append(m["content"])
                lines.append("-" * 40)
        return "\n".join(lines)

    def _copy_all(self):
        text = self._get_conversation_text("txt")
        if not text:
            QMessageBox.information(self, "提示", "当前没有对话内容。")
            return
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "已复制", "对话已复制到剪贴板。")

    def _export(self, fmt="md"):
        text = self._get_conversation_text(fmt)
        if not text:
            QMessageBox.information(self, "提示", "当前没有对话内容。")
            return
        work_name = self._conversation_key or "AI对话"
        safe = re.sub(r'[\\/:*?"<>|]', "_", work_name).strip() or "AI对话"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = f"{safe}_AI对话_{ts}.{fmt}"
        flt = "Markdown (*.md)" if fmt == "md" else "文本文件 (*.txt)"
        path, _ = QFileDialog.getSaveFileName(self, "导出 AI 对话", default, flt)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            QMessageBox.information(self, "导出完成", f"已导出到：\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ---------- 会话持久化 ----------

    def set_conversation_key(self, key: str):
        key = key or ""
        if key == self._conversation_key:
            return
        self._save_conversation()
        self._conversation_key = key
        self._reset_ui()
        self._load_conversation()

    def clear_conversation(self):
        self._reset_ui()
        try:
            ai_conversations.clear_messages(self._conversation_key)
        except Exception as e:
            logger.warning(f"清空对话失败: {e}")

    def flush_conversation(self):
        self._save_conversation()

    def _reset_ui(self):
        self._flush_timer.stop()
        self._buffer_parts = []
        self._last_rendered_text = ""
        if self._worker is not None:
            try:
                from utils.ai_client import retire_worker
                retire_worker(self._worker)
            except Exception:
                pass
            self._worker = None
        self._request_seq += 1
        self._ai_bubble = None
        self._last_request = None
        self._request_selection_range = None
        self._last_context_items = []
        self.ctx_badge.setVisible(False)
        self._set_running(False)
        self._messages = [
            {"role": "system", "content": get_system_prompt()}]
        while self.msg_layout.count() > 2:
            item = self.msg_layout.takeAt(1)
            w = item.widget()
            if w is not None and w is not self._empty_hint:
                w.deleteLater()
        if self._empty_hint is not None:
            self._empty_hint.setVisible(True)

    def _load_conversation(self):
        try:
            history = ai_conversations.load_messages(self._conversation_key)
        except Exception as e:
            logger.warning(f"加载对话失败: {e}")
            return
        if not history:
            return
        self._messages = [
            {"role": "system", "content": get_system_prompt()}]
        self._messages.extend(history)
        if self._empty_hint is not None:
            self._empty_hint.setVisible(False)
        for i, m in enumerate(history):
            role = m["role"]
            content = m["content"]
            b = self._add_bubble(role, content, message_index=i + 1)
            if role == "user":
                btn_edit = b.add_action_button("编辑")
                btn_edit.clicked.connect(
                    lambda _=False, bub=b, t=content:
                        self._edit_user_message(bub, t))
            elif role == "assistant" and content.strip():
                btn = b.add_action_button("复制")
                btn.clicked.connect(
                    lambda _=False, t=content:
                        QApplication.clipboard().setText(t))
                # 只有最后一条 assistant 给"重新生成"——更早的回复
                # 语义上属于历史，重发会破坏对话分支。
                # 历史里没有 _last_request，用上一条 user 原文重建请求。
                if (i == len(history) - 1 and i >= 1
                        and history[i - 1].get("role") == "user"):
                    user_text = history[i - 1].get("content", "")
                    req = (user_text, user_text, False)
                    b_regen = b.add_action_button("重新生成")
                    b_regen.clicked.connect(
                        lambda _=False, r=req: self._regenerate(r))

    def _save_conversation(self):
        try:
            msgs = [m for m in self._messages
                    if m.get("role") in ("user", "assistant")]
            ai_conversations.save_messages(self._conversation_key, msgs)
        except Exception as e:
            logger.warning(f"保存对话失败: {e}")

    def _trim_messages(self):
        if not self._messages or self._messages[0].get("role") != "system":
            return
        rest = self._messages[1:]
        limit = ai_conversations.MAX_TURNS * 2
        if len(rest) <= limit:
            return
        drop = len(rest) - limit
        while drop < len(rest) and rest[drop].get("role") != "user":
            drop += 1
        if drop >= len(rest):
            drop = len(rest) - limit
        if drop > 0:
            self._messages = [self._messages[0]] + rest[drop:]

    def _add_bubble(self, role, text="", message_index=None, thinking=False):
        if self._empty_hint is not None:
            self._empty_hint.setVisible(False)
        bubble = _Bubble(role, text, thinking=thinking)
        bubble.apply_colors(self._theme)
        bubble.set_font_size(self._output_font_size)
        bubble.message_index = message_index
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, bubble)
        QTimer.singleShot(0, self._scroll_bottom)
        return bubble

    def append_message(self, role, text):
        return self._add_bubble(role, text)

    def _scroll_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._auto_scroll = True

    def _on_scroll(self, value):
        sb = self.scroll.verticalScrollBar()
        self._auto_scroll = value >= sb.maximum() - 30

    def _bubble_alive(self, bubble) -> bool:
        if bubble is None:
            return False
        try:
            _ = bubble.label.objectName()
            _ = bubble.action_row.count()
            return True
        except (RuntimeError, AttributeError):
            return False

    def _remove_last_bubbles(self, n):
        removed = 0
        for i in range(self.msg_layout.count() - 1, -1, -1):
            if removed >= n:
                break
            item = self.msg_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, _Bubble):
                self.msg_layout.takeAt(i)
                w.deleteLater()
                removed += 1

    def _remove_from_bubble(self, start_bubble):
        start = -1
        for i in range(self.msg_layout.count()):
            if self.msg_layout.itemAt(i).widget() is start_bubble:
                start = i
                break
        if start < 0:
            return
        for i in range(self.msg_layout.count() - 1, start - 1, -1):
            item = self.msg_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, _Bubble):
                self.msg_layout.takeAt(i)
                w.deleteLater()

    def _update_token_hint(self):
        text = self.input_edit.toPlainText()
        n = estimate_tokens(text)
        if n > 0:
            self.tip_label.setText(f"约 {n:,} tokens")
        else:
            self.tip_label.setText("")

    # ---------- 定位 / 生命周期 ----------

    def show_from_pet(self, pet_rect_in_host):
        self._closing = False
        self._position_near_pet(pet_rect_in_host, animate=True)

    def reposition_follow_pet(self, pet_rect_in_host):
        if not self.isVisible():
            return
        self._position_near_pet(pet_rect_in_host, animate=False)

    def _position_near_pet(self, pet_rect_global, animate=False):
        """pet_rect_global: 猫娘实际绘制区域的全局矩形（QRect）。"""
        pet_cx = pet_rect_global.center().x()
        pet_top = pet_rect_global.top()
        pet_bottom = pet_rect_global.bottom()
        height = self.height()
        target_x = pet_cx - self.width() // 2
        target_y = pet_top - height - 2
        tail_bottom = True
        # 屏幕边界 clamp
        app = QApplication.instance()
        screen = app.screenAt(QPoint(target_x, pet_top)) if app else None
        if screen is None and app is not None:
            screen = app.primaryScreen()
        min_top = max_bottom = None
        if screen is not None:
            geo = screen.availableGeometry()
            target_x = max(geo.x() + 8,
                           min(target_x, geo.x() + geo.width()
                               - self.width() - 8))
            min_top = geo.y() + 8
            max_bottom = geo.y() + geo.height() - 8

        # 优先放猫娘上方；上方放不下（猫娘贴近屏幕顶）时翻到下方，
        # 不能只做顶部 clamp——那样气泡会直接盖住猫娘。
        if min_top is not None and target_y < min_top:
            below_y = pet_bottom + 2
            if max_bottom is not None and below_y + height <= max_bottom:
                target_y = below_y
                tail_bottom = False
            elif max_bottom is not None:
                # 上下都放不下（矮屏 + 大气泡）：让位给剩余空间更大的一侧
                above_room = max(0, pet_top - min_top)
                below_room = max(0, max_bottom - pet_bottom)
                if below_room > above_room:
                    target_y = max(min_top,
                                   min(below_y, max_bottom - height))
                    tail_bottom = False
                else:
                    target_y = min_top
        if min_top is not None:
            target_y = max(min_top, target_y)
        if max_bottom is not None:
            target_y = min(target_y, max_bottom - height)

        self._tail_bottom = tail_bottom
        self._tail_x = max(24, min(pet_cx - target_x, self.width() - 24))

        if animate:
            # 从靠近猫娘的一侧滑入：在上方就向上滑，在下方就向下滑
            start_y = target_y + 18 if tail_bottom else target_y - 18
            self.move(target_x, start_y)
            self.show()
            self.raise_()
            self.input_edit.setFocus()
            self._anim.stop()
            self._anim.setStartValue(QPoint(target_x, start_y))
            self._anim.setEndValue(QPoint(target_x, target_y))
            self._anim.start()
        else:
            self.move(target_x, target_y)
            self.update()

    def request_close(self):
        if self._closing:
            return
        self._closing = True
        start = self.pos()
        end = QPoint(start.x(), start.y() + 18)
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def _on_anim_finished(self):
        if self._closing:
            self.hide()
            self.closed.emit()

    def apply_theme(self, theme: dict):
        self._theme = theme or {}
        t = self._theme
        text = t.get("text", "#3b342a")
        muted = t.get("muted", "#9a8e76")
        accent = t.get("accent", "#b08a4e")
        accent_fg = t.get("accent_fg", "#ffffff")
        accent_hover = t.get("accent_hover", accent)

        self.setStyleSheet(
            f"#petBubble {{ background: transparent; }}"
            f"#petBubbleTitle {{ color: {accent}; font-size: 13px;"
            f" font-weight: 700; background: transparent; }}"
            f"#petCtxBadge {{ color: {muted};"
            f" background: {qss_alpha(accent, 0x14)};"
            f" border-radius: 8px; padding: 1px 7px; font-size: 10.5px; }}"
            f"#petBubbleIconBtn {{ border: none; background: transparent;"
            f" border-radius: 6px; padding: 0; }}"
            f"#petBubbleIconBtn:hover {{"
            f" background: {qss_alpha(accent, 0x22)}; }}"
            f"#petBubbleSendBtn {{ background: {accent};"
            f" border: none; border-radius: 16px; }}"
            f"#petBubbleSendBtn:hover {{ background: {accent_hover}; }}"
            f"#petBubbleEmpty {{ color: {muted}; font-size: 12px;"
            f" background: transparent; padding: 12px 8px; }}"
            f"#petQuickBtn {{ color: {text};"
            f" background: {qss_alpha(muted, 0x12)};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f"#petQuickBtn:hover {{ color: {accent};"
            f" border-color: {accent};"
            f" background: {qss_alpha(accent, 0x14)}; }}"
            f"#petQuickBtn:disabled {{ color: {muted};"
            f" border-color: {qss_alpha(muted, 0x15)}; }}"
            f"#petTipLabel {{ color: {muted}; font-size: 10px; }}"
            f"QTextEdit#petBubbleInput {{"
            f" background: {qss_alpha(muted, 0x14)}; color: {text};"
            f" border: 1px solid {qss_alpha(muted, 0x22)};"
            f" border-radius: 10px; padding: 6px 10px; font-size: 12px;"
            f" selection-background-color: {accent};"
            f" selection-color: {accent_fg}; }}"
            f"QTextEdit#petBubbleInput:focus {{"
            f" background: {qss_alpha(accent, 0x10)};"
            f" border: 1px solid {accent}; }}"
            f"QToolButton {{ border: none; background: transparent;"
            f" color: {muted}; font-size: 11.5px; padding: 2px 8px; }}"
            f"QToolButton:hover {{ color: {accent}; }}"
            f"QToolButton::menu-indicator {{ image: none; }}")
        self._refresh_expand_icon()
        self.close_btn.setIcon(qta.icon("fa5s.times", color=muted))
        self.more_btn.setIcon(qta.icon("fa5s.ellipsis-h", color=muted))
        self.preset_btn.setIcon(qta.icon("fa5s.bullseye", color=muted))
        self.send_btn.setIcon(qta.icon("fa5s.arrow-up", color=accent_fg))
        self.stop_btn.setIcon(qta.icon("fa5s.stop", color=accent_fg))
        for i in range(self.msg_layout.count()):
            w = self.msg_layout.itemAt(i).widget()
            if isinstance(w, _Bubble):
                try:
                    w.apply_colors(t)
                    w.set_font_size(self._output_font_size)
                except RuntimeError:
                    pass
        self.update()

    def paintEvent(self, event):
        """绘制气泡外框。

        折叠和展开使用**相同的形态**（圆角矩形 + 尾巴），
        只有尺寸不同——避免切换时视觉跳跃。
        尾巴默认在底部指向猫娘；猫娘贴近屏幕顶、气泡翻到下方时，
        尾巴翻到顶部（_tail_bottom=False）。
        尾巴用 united 真正融合到主体，避免描边重复。
        """
        t = self._theme or {}
        bg = QColor(t.get("editor_bg", "#fcfaf4"))
        accent = QColor(t.get("accent", "#b08a4e"))

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        margin = 2          # 边缘留白，给描边空间
        tail_h = 14         # 尾巴高度
        radius = 14         # 圆角半径

        # 主体矩形：尾巴在哪一侧，就让出哪一侧
        body_h = h - tail_h - margin * 2
        body_y = margin if self._tail_bottom else margin + tail_h
        path = QPainterPath()
        path.addRoundedRect(
            margin, body_y,
            w - margin * 2, body_h,
            radius, radius)

        # 尾巴（指向猫娘）
        tail_tip_x = max(margin + 26, min(self._tail_x, w - margin - 26))
        tail = QPainterPath()
        if self._tail_bottom:
            tail_base_y = body_y + body_h - 1
            tail.moveTo(tail_tip_x - 9, tail_base_y)
            tail.lineTo(tail_tip_x, tail_base_y + tail_h)
            tail.lineTo(tail_tip_x + 9, tail_base_y)
        else:
            # 气泡在猫娘下方：尖角朝上
            tail_base_y = body_y
            tail.moveTo(tail_tip_x - 9, tail_base_y + 1)
            tail.lineTo(tail_tip_x, margin)
            tail.lineTo(tail_tip_x + 9, tail_base_y + 1)
        tail.closeSubpath()
        path = path.united(tail)   # 真正融合，描边不重复

        # 填充
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawPath(path)

        # 描边：清晰可辨（0x44 → 0x88）
        outline = QColor(accent)
        outline.setAlpha(0x88)
        pen = QPen(outline, 1.2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()