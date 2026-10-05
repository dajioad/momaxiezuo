# ui/pet_system/persona.py
"""墨码写作 - 墨灵的「AI 人格层」。

两类纯函数，不依赖 Qt，方便单测：

1. build_pet_status_block()
   把宠物的实时状态 + 主人的写作数据渲染成一段系统提示词，
   注入到 AI 对话的 system message 里。
   这是「AI 宠物」区别于普通桌宠的核心：她知道自己是谁、
   和主人关系如何、此刻心情怎样、主人今天写了多少。

2. detect_emotion()
   从 AI 回复文本里识别情绪，返回 _PetFace 的临时状态键，
   让回复结束时宠物做出对应表情。纯本地启发式，零额外 API 开销。
"""

from __future__ import annotations

from . import constants as C
from .state import PetState


# ---------- 亲密度等级 → 说话方式指引 ----------
# 等级索引 1-5，与 constants.LEVELS 对应。
# 设计意图：等级低时有分寸感，等级高了才撒娇任性——
# 「刚认识就叫主人贴贴」是廉价的，陪伴久了才放得开。
LEVEL_TONE = {
    1: (
        "你和主人刚刚认识，保持礼貌、温和、有分寸，"
        "不要过度撒娇，不要使用「主人」以外过于亲昵的称呼。"
    ),
    2: (
        "你和主人已经熟悉，语气轻松自然，可以适度关心，"
        "偶尔在句尾用「~」，但卖萌点到为止。"
    ),
    3: (
        "你和主人关系亲密，会主动关心对方的状态，"
        "开心时可以自然地撒娇，合适的场合用「喵」结尾。"
    ),
    4: (
        "你是主人信赖的挚友与陪伴者，可以撒娇也可以打趣，"
        "会敏锐察觉主人的情绪并给出回应。"
    ),
    5: (
        "你与主人相伴已久，如同家人，温柔体贴又带点小任性，"
        "能毫无保留地表达喜欢，也会在主人逞强时直接点破。"
    ),
}


# ---------- 生理状态描述 ----------
# 只在数值偏低/偏高时描述，避免每次回复都塞一堆"状态良好"。
def _stat_lines(s: PetState) -> list:
    lines = []
    if s.is_thirsty:
        lines.append("- 你现在有点口渴")
    if s.is_hungry:
        lines.append("- 你现在肚子有点饿")
    if s.is_tired:
        lines.append("- 你现在有点犯困、精力不足")
    if s.is_dirty:
        lines.append("- 你觉得身上毛毛黏黏的，想清洁一下")
    if s.is_sad:
        lines.append("- 你现在情绪有点低落，比平时更想被主人理睬")
    if s.energy >= 85 and s.mood >= 70:
        lines.append("- 你此刻精神很好，心情也不错")
    return lines


def _format_hours(h: float) -> str:
    if h < 0:
        return "今天第一次见到主人"
    if h < 1 / 60:
        return "主人刚刚还在和你互动"
    if h < 1:
        return f"主人上次和你互动是在 {int(h * 60)} 分钟前"
    if h < 24:
        return f"主人上次和你互动是在 {int(h)} 小时前"
    return f"主人已经有 {int(h // 24)} 天多没和你互动了"


def build_pet_status_block(
    pet_state: PetState,
    writing: dict | None = None,
    work_title: str = "",
    chapter_title: str = "",
) -> str:
    """渲染注入 system prompt 的【墨灵此刻的状态】文本块。

    writing 可包含：today_words / streak_days / today_minutes /
    total_words（缺省按 0 处理）。
    """
    writing = writing or {}
    today_words = int(writing.get("today_words", 0) or 0)
    streak = int(writing.get("streak_days", 0) or 0)
    minutes = int(writing.get("today_minutes", 0) or 0)

    lv = pet_state.level
    tone = LEVEL_TONE.get(lv, LEVEL_TONE[1])
    need = C.NEED_LABELS.get(pet_state.current_need(), "")

    parts = [
        "【墨灵此刻的状态】（以下是你的实时状态，用于让反应更自然；"
        "不要像念报表一样复述数字，只在情境合适时自然流露）",
        f"- 你与主人的关系阶段：{pet_state.level_name}（Lv.{lv}）",
        f"- 关系阶段决定你的说话方式：{tone}",
    ]

    parts.extend(_stat_lines(pet_state))
    if need:
        parts.append(
            f"- 你当前最想要的是「{need}」，可以在合适时机自然地表达，"
            "但不要每次对话都讨要")

    parts.append(f"- {_format_hours(pet_state.hours_since_interact())}")

    # 写作上下文（她是「写作助手」，这些数字是和主人最好的谈资）
    if work_title:
        parts.append(f"- 主人正在创作的作品叫《{work_title}》")
    if chapter_title:
        parts.append(f"- 当前章节：{chapter_title}")
    if today_words > 0:
        parts.append(f"- 主人今天已经写了 {today_words:,} 字"
                     + (f"，活跃写作 {minutes} 分钟" if minutes else ""))
    else:
        parts.append("- 主人今天还没有开始写正文")
    if streak >= 2:
        parts.append(f"- 主人已经连续 {streak} 天坚持写作，这很了不起，"
                     "值得你记住并在合适的时候鼓励")
    elif streak <= 0 and today_words == 0:
        parts.append("- 今天还没有动笔，你可以温柔地推主人一把，但别催")

    parts.append(
        "- 当主人写得顺利时真诚祝贺，卡住时陪伴而不是说教；"
        "深夜写作时提醒休息。所有状态只能影响你的语气和话题，"
        "不能改变你作为写作助手的专业判断。")

    return "\n".join(parts)


# ---------- 回复情绪识别 ----------
# 顺序即优先级：靠前的情绪先命中。
# 全部用短词/语气词作为信号，避免对正常文本误伤。
# 返回 _PetFace 的临时状态名；空串表示"无明显情绪"，调用方不切表情。
_EMOTION_RULES = [
    # (状态, [关键词...])
    ("love", [
        "喜欢你", "爱你", "抱抱", "摸摸头", "贴贴", "亲亲",
        "最喜欢主人",
    ]),
    ("excited", [
        "哈哈哈", "哈哈", "太棒了", "太好了", "好耶", "耶！",
        "厉害", "强啊", "绝了", "真好",
    ]),
    ("proud", [
        "加油", "我相信你", "你可以的", "一定可以", "干得漂亮",
        "不愧是主人", "进步很大",
    ]),
    ("sleepy", [
        "早点休息", "该睡觉", "别熬夜", "去睡吧", "晚安",
        "休息一下", "睡一觉",
    ]),
    ("surprised", [
        "！？", "?!", "居然", "竟然", "没想到", "出乎意料",
    ]),
    ("worried", [
        "别难过", "别灰心", "我很担心", "心疼", "辛苦你了",
        "别硬撑",
    ]),
    ("shy", [
        "讨厌啦", "人家会害羞", "别说了", "嘿嘿……",
    ]),
    ("happy", [
        "喵~", "喵～", "开心", "高兴", "真好呢", "=w=",
    ]),
]


def detect_emotion(text: str) -> str:
    """从回复文本猜一个表情状态。无明显情绪返回空串。"""
    if not text:
        return ""
    # 只扫前 200 字：情绪通常在开头寒暄句里，
    # 长篇正文建议里出现"加油"不该把宠物切成 proud。
    sample = text[:200]
    for state, words in _EMOTION_RULES:
        for w in words:
            if w in sample:
                return state
    return ""
