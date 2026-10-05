# utils/readability.py
"""墨码写作 - 追读体检（纯规则引擎，无 Qt、无网络）

评估一章的"读者留存潜力"，给出 0-100 分 + 明细 + 段落级定位 + 情绪曲线。

10 个维度（满分 100）：
1. 开头钩子（16）：前 300 字是否迅速建立张力
2. 章末钩子（16）：末 200 字是否留下"想读下一章"的悬念
3. 对话密度（10）：对话行 / 非空行 是否落在网文甜点区（25%-45%）
4. 段落长度（8）：长段落（>200 字）占比
5. 节奏曲线（10）：每 500 字窗口内的"情绪点"是否均匀
6. 信息密度（10）：水分词 / 超长句 占比
7. 句式变化（8）：句长变异系数 + 连续同结构句式
8. 冲突爽点（8）：打斗 / 悬念 / 爽感词按窗口分布
9. 感官层次（6）：视觉之外的感官（听 / 触 / 嗅 / 味）
10. 出戏穿帮（8）：打破第四面墙的元叙述（作者 / 剧情需要 / 下一章…）

设计原则：
- 只检测形式，不评判内容。分数低不等于写得差，分数高更不等于写得好——
  一段套路正确但剧情荒谬的文字也能拿高分，逻辑与人设问题请看 AI 深度体检
- 每条问题都定位到具体段落，让作者能直接跳过去改
- build_emotion_curve() 输出曲线数据，供 UI 画节奏图
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Dict

# ---------- 词表 ----------

_DIALOGUE_OPEN = (
    '"',        # ASCII 双引号
    '\u201c',   # 中文左双引号 "
    '\u201d',   # 中文右双引号 "
    '「',       # 中文左直角引号
    '『',       # 中文左双直角引号
    "'",        # ASCII 单引号
    '\u2018',   # 中文左单引号 '
    '\u2019',   # 中文右单引号 '
)

_ACTION_WORDS = re.compile(r"[喊叫骂打冲闯撞摔砸抓扯拽推拉跑走看盯拳掌踢跳]")

_CONFLICT_WORDS = re.compile(r"[怒恨仇怨斗战厮杀拼拔刀剑枪冷笑冷哼]")

_HOOK_WORDS = re.compile(
    r"却|竟|忽然|骤然|猛然|突然|谁知|岂料|偏偏|难道|到底|究竟"
    r"|仿佛|似乎|只是|可是|然而|不过|没想到|哪知"
    r"|不对劲|有问题|不对|诡异|古怪|奇怪"
)

_HOOK_PUNCT = re.compile(r"[？！]|……|—{2,}")

_EMOTION_WORDS = re.compile(
    r"[！？]|[\u201c][^\u201d]{2,}[\u201d]|[\u2018][^\u2019]{2,}[\u2019]"
    r"|「[^」]{2,}」|『[^』]{2,}』"
    r"|喊|吼|骂|笑|哭|怒|惊|慌|急|痛|怕|喜|悲|恨"
)

_SCENERY_WORDS = re.compile(
    r"天空|云|月|阳光|夕阳|朝霞|晚霞|风|雨|雪|雾|空气|景色|风景"
    r"|山林|河流|湖|海|街|巷|楼|房间|屋|宫殿|庭院|花园"
)

# ---------- 深化：新增词表 ----------

# 水分词：删掉不影响语义的副词/填充词
_WATER_WORDS = re.compile(
    r"其实|事实上|实际上|基本上|总体来说|总的来说|"
    r"某种程度上|某种程度上说|可以说|不得不说|"
    r"显而易见|毋庸置疑|众所周知|"
    r"突然之间|一瞬间|刹那之间|"
    r"点了点头|摇了摇头|叹了口气|皱了皱眉"   # 泛用动作
)

# 爽点词：网文读者情绪被推高的标志
_PAYOFF_WORDS = re.compile(
    r"震惊|骇然|悚然|愕然|惊讶|吃惊|"
    r"不敢置信|难以置信|万万没想到|没想到|"
    r"拍案叫绝|大快人心|痛快|畅快|解气|"
    r"敬畏|佩服|叹服|崇拜|仰慕|"
    r"逆转|反杀|翻盘|力挽狂澜|绝处逢生|"
    r"心生敬畏|叹为观止|震撼"
)

# 冲突词：打斗、对峙、情感碰撞
_CONFLICT_WORDS_FULL = re.compile(
    r"怒|恨|仇|怨|斗|战|杀|嘶|拼|拔刀|亮剑|枪|拳|掌|踢|撞|砸|摔|"
    r"冷笑|冷哼|厉声|喝道|怒斥|斥责|"
    r"对峙|冲突|交锋|争锋|"
    r"血|伤口|剧痛|惨|死|亡"
)

# 悬念词：让读者想继续读的钩子
_SUSPENSE_WORDS = re.compile(
    r"难道|究竟|到底|莫非|难道说|"
    r"秘密|谜团|真相|隐情|内幕|"
    r"不对劲|不寻常|异常|诡异|古怪|"
    r"究竟是谁|到底是谁|为什么|为何"
)

# ---------- 深化：感官词 ----------

_SENSE_VISUAL = re.compile(r"看|望|瞧|盯|瞥|视|见|色|光|亮|暗|影|明|显|映|照")
_SENSE_AUDIO = re.compile(
    r"听|声响|声音|嗡|响|吼|吟|啸|喊|叫|嚷|叱|鸣|"
    r"寂静|嘈杂|喧嚣|低语|耳语|雷鸣|风声|雨声"
)
_SENSE_TOUCH = re.compile(
    r"触|摸|抚|碰|感到|冰凉|滚烫|温暖|寒冷|坚硬|柔软|"
    r"粗糙|光滑|刺痛|麻木|痒|刺痛"
)
_SENSE_SMELL = re.compile(
    r"嗅|闻|气味|香味|异味|腥|臭|香|芬芳|馥郁|"
    r"气息|空气中弥漫"
)
_SENSE_TASTE = re.compile(r"尝|味道|甜|酸|苦|辣|咸|涩|腻|鲜")

# ---------- 出戏穿帮：打破第四面墙的元叙述 ----------
# 强信号：正经故事正文里几乎不该出现（叙述者直接谈论"作品本身"）。
# 每处重扣；刻意只收零误报写法，宁可漏报不误报。
_META_BREAK_STRONG = re.compile(
    r"作者"            # "因为作者是这么安排的"
    r"|剧情需要"       # "因为剧情需要她可怜"
    r"|读者"           # "读者也不记得了"
    r"|下一章|上一章|本章"  # "他在下一章就再也不会出现"
    r"|主角|男一号|女一号"  # "主角想坐什么车就坐"
    r"|一律作废|情节[一-鿿]{0,8}作废"
)

# 弱信号：单独出现可能是故事内容，但多个叠加即为元叙述。
# (?m)(?!^) 让行首的"第X章"（正文里重复的章节标题）豁免，只抓句中叙述。
_META_BREAK_WEAK = re.compile(
    r"(?m)(?!^)第[一二三四五六七八九十百千零两0-9]+章"
    r"|这个设定|设定(?:需要|就是|是不是)|大纲|人设"
    r"|没有逻辑|不需要逻辑|逻辑不通"
)

# ---------- 深化：句式检测 ----------

# 常见单调句式（连续出现 3 次以上扣分）
_MONOTONE_HEADS = (
    "他说", "她说", "我道", "他道", "她道", "只听", "只见",
    "忽然", "突然", "然后", "接着", "随即", "于是",
)


@dataclass
class CheckItem:
    key: str
    label: str
    score: int
    max_score: int
    level: str
    message: str
    suggestions: List[str] = field(default_factory=list)
    positions: List[Tuple[int, int]] = field(default_factory=list)


@dataclass
class Report:
    total: int
    level: str
    items: List[CheckItem] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)
    # 深化：情绪曲线 [(start, end, score), ...]，供 UI 画图
    curve: List[Tuple[int, int, int]] = field(default_factory=list)


# ---------- 工具 ----------


def _strip_ws_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _split_paras_with_pos(text: str) -> List[Tuple[int, str]]:
    result = []
    pos = 0
    for para in text.split("\n"):
        result.append((pos, para))
        pos += len(para) + 1
    return result


def _first_n_chars(text: str, n: int) -> Tuple[str, int]:
    count = 0
    for i, ch in enumerate(text):
        if not ch.isspace():
            count += 1
            if count >= n:
                return text[: i + 1], i + 1
    return text, len(text)


def _last_n_chars(text: str, n: int) -> Tuple[str, int]:
    count = 0
    for i in range(len(text) - 1, -1, -1):
        if not text[i].isspace():
            count += 1
            if count >= n:
                return text[i:], i
    return text, 0


def _is_dialogue_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return s[0] in _DIALOGUE_OPEN


def _split_sentences(text: str) -> List[str]:
    """按句末标点切句，返回非空句子列表。"""
    parts = re.split(r"[。！？…]+", text)
    return [p.strip() for p in parts if p.strip()]


# ============================================================
# 维度 1-5：原有维度
# ============================================================


def _check_opening(text: str) -> CheckItem:
    head, end_pos = _first_n_chars(text, 300)
    lines = [l for l in head.split("\n") if l.strip()]
    if not lines:
        return CheckItem(
            key="opening", label="开头钩子",
            score=0, max_score=16, level="bad", message="正文为空",
        )

    has_dialogue = any(_is_dialogue_line(l) for l in lines[:5])
    has_action = bool(_ACTION_WORDS.search(head))
    has_conflict = bool(_CONFLICT_WORDS.search(head))
    first_para = lines[0].strip()
    first_para_long = len(first_para) > 150

    sentences = _split_sentences(head)
    scenery_n = sum(1 for s in sentences if _SCENERY_WORDS.search(s))
    scenery_ratio = scenery_n / len(sentences) if sentences else 0
    scenery_heavy = scenery_ratio > 0.6 and len(sentences) >= 3

    score = 16
    suggestions = []
    if not has_dialogue and not has_action and not has_conflict:
        score -= 8
        suggestions.append(
            "开头 300 字没有对话、动作或冲突，读者容易划走。"
            "建议用一句对话或一个动作场景开场。"
        )
    elif not (has_dialogue or has_action):
        score -= 4
        suggestions.append("开头可以再加一点动作或对话，让节奏更快。")
    if first_para_long:
        score -= 4
        suggestions.append(
            f"首段 {len(first_para)} 字，太长。"
            "建议拆成 2-3 段，或把描写换成一句对话。"
        )
    if scenery_heavy:
        score -= 4
        suggestions.append(
            "开头大段环境描写容易让读者跳读。"
            "建议把场景信息融进动作或对话里。"
        )
    score = max(0, min(16, score))

    if score >= 14:
        level, msg = "ok", "开头迅速建立张力 ✓"
    elif score >= 8:
        level, msg = "warn", "开头略平，可能留不住读者"
    else:
        level, msg = "bad", "开头较难吸引读者"

    return CheckItem(
        key="opening", label="开头钩子",
        score=score, max_score=16, level=level, message=msg,
        suggestions=suggestions,
        positions=[(0, end_pos)] if suggestions else [],
    )


def _check_closing(text: str) -> CheckItem:
    tail, start_pos = _last_n_chars(text, 200)
    if not tail.strip():
        return CheckItem(
            key="closing", label="章末钩子",
            score=0, max_score=16, level="bad", message="正文为空",
        )

    has_hook_word = bool(_HOOK_WORDS.search(tail))
    has_hook_punct = bool(_HOOK_PUNCT.search(tail))
    has_question = "？" in tail or "?" in tail
    last_para = tail.strip().split("\n")[-1].strip()
    ends_flat = bool(re.search(r"[。\.]$", last_para)) and not (
        has_hook_word or has_question
    )

    score = 16
    suggestions = []
    if has_question:
        pass
    elif has_hook_word and has_hook_punct:
        pass
    elif has_hook_word or has_hook_punct:
        score -= 6
        suggestions.append(
            "章末有轻微钩子，但不够强。"
            "可以加一句悬念、疑问，或一个未完成的动作。"
        )
    else:
        score -= 10
        suggestions.append(
            "章末没有明显的悬念或钩子，读者可能不会点下一章。"
            "建议用一句疑问、一个突然的转折，或一个未解决的冲突收尾。"
        )
    if ends_flat and score >= 10:
        score -= 2
        suggestions.append("末尾以句号平稳收尾，缺少「再看一眼」的张力。")

    score = max(0, min(16, score))
    if score >= 14:
        level, msg = "ok", "章末有强钩子 ✓"
    elif score >= 8:
        level, msg = "warn", "章末钩子略弱"
    else:
        level, msg = "bad", "章末缺少钩子"

    return CheckItem(
        key="closing", label="章末钩子",
        score=score, max_score=16, level=level, message=msg,
        suggestions=suggestions,
        positions=[(start_pos, start_pos + len(tail))] if suggestions else [],
    )


def _check_dialogue_ratio(text: str) -> CheckItem:
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return CheckItem(
            key="dialogue", label="对话密度",
            score=0, max_score=10, level="bad", message="正文为空",
        )
    dlg = sum(1 for l in lines if _is_dialogue_line(l))
    ratio = dlg / len(lines)

    if 0.25 <= ratio <= 0.45:
        score, level = 10, "ok"
        msg = f"对话占 {ratio*100:.0f}%，节奏舒适 ✓"
    elif 0.15 <= ratio < 0.25 or 0.45 < ratio <= 0.6:
        score, level = 7, "warn"
        msg = (
            f"对话占 {ratio*100:.0f}%，略偏少"
            if ratio < 0.25
            else f"对话占 {ratio*100:.0f}%，略偏多"
        )
    else:
        score, level = 3, "bad"
        msg = f"对话占 {ratio*100:.0f}%，" + (
            "几乎无对话，容易叙述过多" if ratio < 0.15
            else "全是对话，可能缺少必要的描写"
        )

    suggestions = []
    if level == "bad":
        if ratio < 0.15:
            suggestions.append("把关键情节用对话呈现，能让读者看得更快、更投入。")
        else:
            suggestions.append("适当加入心理、动作、环境描写，避免剧本化。")

    return CheckItem(
        key="dialogue", label="对话密度",
        score=score, max_score=10, level=level, message=msg,
        suggestions=suggestions,
    )


def _check_paragraphs(text: str) -> CheckItem:
    paras = _split_paras_with_pos(text)
    non_empty = [(s, p) for s, p in paras if p.strip()]
    if not non_empty:
        return CheckItem(
            key="paragraph", label="段落长度",
            score=0, max_score=8, level="bad", message="正文为空",
        )
    threshold = 200
    long_paras = [(s, p) for s, p in non_empty if len(p) > threshold]
    ratio = len(long_paras) / len(non_empty)

    if ratio <= 0.10:
        score, level = 8, "ok"
        msg = "段落节奏良好，几乎没有长段落 ✓"
    elif ratio <= 0.25:
        score, level = 5, "warn"
        msg = f"{len(long_paras)} 段超过 {threshold} 字，稍长"
    else:
        score, level = 2, "bad"
        msg = f"{len(long_paras)} 段超过 {threshold} 字，读者容易跳读"

    suggestions = []
    positions = []
    if long_paras:
        for s, p in long_paras[:5]:
            positions.append((s, s + len(p)))
        if level != "ok":
            suggestions.append(
                f"建议把超过 {threshold} 字的段落拆成 2-3 段，每段 2-4 行最佳。"
            )

    return CheckItem(
        key="paragraph", label="段落长度",
        score=score, max_score=8, level=level, message=msg,
        suggestions=suggestions, positions=positions,
    )


def _check_rhythm(text: str) -> CheckItem:
    clean = re.sub(r"\s", "", text)
    total = len(clean)
    if total < 500:
        return CheckItem(
            key="rhythm", label="节奏曲线",
            score=10, max_score=10, level="ok",
            message="章节较短，不评估节奏",
        )
    window = 500
    n_windows = max(1, total // window)
    n_chars = len(text)
    step = max(1, n_chars // n_windows)
    emotion_count = []
    for i in range(n_windows):
        seg = text[i * step : (i + 1) * step]
        emotion_count.append(len(_EMOTION_WORDS.findall(seg)))

    flat_windows = sum(
        1
        for i in range(len(emotion_count) - 1)
        if emotion_count[i] == 0 and emotion_count[i + 1] == 0
    )
    avg = sum(emotion_count) / len(emotion_count) if emotion_count else 0

    if flat_windows == 0 and avg >= 3:
        score, level = 10, "ok"
        msg = "节奏起伏均匀 ✓"
    elif flat_windows <= 1:
        score, level = 7, "warn"
        msg = "节奏基本均匀，偶有平淡段落"
    else:
        score, level = 3, "bad"
        msg = f"有 {flat_windows} 处连续平淡段落，可能拖慢节奏"

    suggestions = []
    positions = []
    if flat_windows > 0:
        for i in range(len(emotion_count) - 1):
            if emotion_count[i] == 0 and emotion_count[i + 1] == 0:
                s = i * step
                e = min(len(text), (i + 2) * step)
                positions.append((s, e))
                if len(positions) >= 3:
                    break
        suggestions.append(
            "连续 1000 字内没有情绪点（对话/感叹/动作）时，"
            "读者容易走神。建议穿插一段对话或一个小冲突。"
        )

    return CheckItem(
        key="rhythm", label="节奏曲线",
        score=score, max_score=10, level=level, message=msg,
        suggestions=suggestions, positions=positions,
    )


# ============================================================
# 维度 6-9：深化新增
# ============================================================


def _check_info_density(text: str) -> CheckItem:
    """信息密度：水分词 + 超长句占比。"""
    clean = re.sub(r"\s", "", text)
    total = len(clean)
    if total < 200:
        return CheckItem(
            key="info_density", label="信息密度",
            score=10, max_score=10, level="ok",
            message="章节较短，不评估信息密度",
        )

    # 水分词：每 100 字的出现次数
    water_hits = _WATER_WORDS.findall(text)
    water_per_100 = len(water_hits) / max(1, total) * 100

    # 超长句：> 80 字
    sentences = _split_sentences(text)
    long_sentences = [s for s in sentences if len(s) > 80]
    long_ratio = len(long_sentences) / max(1, len(sentences))

    score = 10
    suggestions = []
    positions = []

    if water_per_100 > 1.5:
        score -= 4
        suggestions.append(
            f"检测到 {len(water_hits)} 处水分词（其实/事实上/突然之间…），"
            f"平均每 100 字 {water_per_100:.1f} 个。适当删除能让行文更紧凑。"
        )
    if long_ratio > 0.25:
        score -= 3
        suggestions.append(
            f"有 {len(long_sentences)} 句超过 80 字"
            f"（占 {long_ratio*100:.0f}%），长句太多会增加阅读负担。"
        )
        # 定位前 3 个超长句
        pos = 0
        for s in long_sentences[:3]:
            idx = text.find(s, pos)
            if idx >= 0:
                positions.append((idx, idx + len(s)))
                pos = idx + len(s)

    score = max(0, min(10, score))
    if score >= 9:
        level, msg = "ok", "信息密度适中 ✓"
    elif score >= 5:
        level, msg = "warn", "略有水分或长句偏多"
    else:
        level, msg = "bad", "水分明显或长句过多，影响阅读流畅度"

    return CheckItem(
        key="info_density", label="信息密度",
        score=score, max_score=10, level=level, message=msg,
        suggestions=suggestions, positions=positions,
    )


def _check_sentence_variance(text: str) -> CheckItem:
    """句式变化：句长变异系数 + 连续同头句式。"""
    sentences = _split_sentences(text)
    if len(sentences) < 10:
        return CheckItem(
            key="sentence_variance", label="句式变化",
            score=8, max_score=8, level="ok",
            message="句子较少，不评估句式变化",
        )

    lengths = [len(s) for s in sentences]
    avg_len = sum(lengths) / len(lengths)
    if avg_len <= 0:
        return CheckItem(
            key="sentence_variance", label="句式变化",
            score=8, max_score=8, level="ok", message="无法评估",
        )
    variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
    std_dev = variance ** 0.5
    cv = std_dev / avg_len  # 变异系数

    # 连续同头句式：3 句以上以相同二字开头
    repeated_groups = 0
    i = 0
    n = len(sentences)
    while i < n - 2:
        head = sentences[i][:2] if len(sentences[i]) >= 2 else ""
        if not head:
            i += 1
            continue
        j = i + 1
        while j < n and len(sentences[j]) >= 2 and sentences[j][:2] == head:
            j += 1
        run = j - i
        if run >= 3:
            repeated_groups += 1
        i = j if j > i else i + 1

    score = 8
    suggestions = []

    # 变异系数过低 = 句式单调
    if cv < 0.35:
        score -= 3
        suggestions.append(
            f"句长变异系数 {cv:.2f} 偏低，句子长度过于接近，读起来容易疲劳。"
            "建议长短句交错，短句制造节奏，长句铺陈细节。"
        )
    if repeated_groups >= 2:
        score -= 3
        suggestions.append(
            f"检测到 {repeated_groups} 处连续 3 句以上以相同二字开头，"
            "句式单调。建议改写其中几句的开头。"
        )

    score = max(0, min(8, score))
    if score >= 7:
        level, msg = "ok", "句式变化丰富 ✓"
    elif score >= 4:
        level, msg = "warn", "句式略单调"
    else:
        level, msg = "bad", "句式单调，读者容易疲劳"

    return CheckItem(
        key="sentence_variance", label="句式变化",
        score=score, max_score=8, level=level, message=msg,
        suggestions=suggestions,
    )


def _check_payoff(text: str) -> CheckItem:
    """冲突爽点：按窗口统计冲突/爽点/悬念词的分布。"""
    clean = re.sub(r"\s", "", text)
    total = len(clean)
    if total < 500:
        return CheckItem(
            key="payoff", label="冲突爽点",
            score=8, max_score=8, level="ok",
            message="章节较短，不评估冲突分布",
        )

    window = 500
    n_windows = max(1, total // window)
    step = max(1, len(text) // n_windows)

    conflict_counts = []
    payoff_counts = []
    suspense_counts = []

    for i in range(n_windows):
        seg = text[i * step : (i + 1) * step]
        conflict_counts.append(len(_CONFLICT_WORDS_FULL.findall(seg)))
        payoff_counts.append(len(_PAYOFF_WORDS.findall(seg)))
        suspense_counts.append(len(_SUSPENSE_WORDS.findall(seg)))

    # 连续窗口无冲突
    flat_conflict = sum(
        1
        for i in range(len(conflict_counts) - 1)
        if conflict_counts[i] == 0 and conflict_counts[i + 1] == 0
    )
    # 整章无爽点
    total_payoff = sum(payoff_counts)
    # 悬念点总数
    total_suspense = sum(suspense_counts)

    score = 8
    suggestions = []
    positions = []

    if flat_conflict >= 2:
        score -= 4
        suggestions.append(
            f"有 {flat_conflict} 处连续 1000 字以上没有冲突/动作词，"
            "可能让读者感到平淡。建议穿插一段对峙、打斗或争执。"
        )
        for i in range(len(conflict_counts) - 1):
            if conflict_counts[i] == 0 and conflict_counts[i + 1] == 0:
                s = i * step
                e = min(len(text), (i + 2) * step)
                positions.append((s, e))
                if len(positions) >= 3:
                    break
    no_payoff = total_payoff == 0
    if no_payoff and total > 1500:
        score -= 2
        suggestions.append(
            "本章未检测到明显的情绪爽点（震惊/痛快/逆转…）。"
            "网文读者需要定期被「爽到」，建议增加一个情绪转折。"
        )
    elif no_payoff:
        score -= 1
    elif total_payoff > 0 and total_payoff < 2 and total > 3000:
        score -= 1
        suggestions.append(
            "爽点较少（仅 1 处）。3000 字以上章节建议至少 2-3 个情绪高潮。"
        )

    score = max(0, min(8, score))
    if no_payoff:
        # 没有爽点不给"✓"：冲突满格但全程压抑也只是铺垫，
        # 文案保持中性，避免误导作者以为本章节奏完美
        level, msg = (
            "warn" if score >= 4 else "bad",
            f"冲突点 {sum(conflict_counts)} 处 · 本章无明确爽点"
            f"（铺垫/过渡章可忽略）",
        )
    elif score >= 7:
        level, msg = "ok", f"冲突点 {sum(conflict_counts)} 处 · 爽点 {total_payoff} 处 ✓"
    elif score >= 4:
        level, msg = "warn", "冲突或爽点分布偏少"
    else:
        level, msg = "bad", "缺乏冲突或爽点，读者可能弃读"

    return CheckItem(
        key="payoff", label="冲突爽点",
        score=score, max_score=8, level=level, message=msg,
        suggestions=suggestions, positions=positions,
    )


def _check_senses(text: str) -> CheckItem:
    """感官层次：视觉之外的感官是否调动。"""
    clean = re.sub(r"\s", "", text)
    total = len(clean)
    if total < 300:
        return CheckItem(
            key="senses", label="感官层次",
            score=6, max_score=6, level="ok",
            message="章节较短，不评估感官层次",
        )

    v = len(_SENSE_VISUAL.findall(text))
    a = len(_SENSE_AUDIO.findall(text))
    t = len(_SENSE_TOUCH.findall(text))
    s = len(_SENSE_SMELL.findall(text))
    ta = len(_SENSE_TASTE.findall(text))

    non_visual = a + t + s + ta
    # 每 100 字的非视觉感官词密度
    non_visual_per_100 = non_visual / max(1, total) * 100

    # 视觉主导正常，但完全无其他感官 = 单薄
    distinct = sum(1 for n in (a, t, s, ta) if n > 0)

    score = 6
    suggestions = []

    if distinct == 0 and total > 1000:
        score -= 4
        suggestions.append(
            "整章只用了视觉描写（看、望、色、光…），"
            "读者容易觉得单薄。试着加入声音、触感、气味或味道。"
        )
    elif distinct == 1 and total > 2000:
        score -= 2
        suggestions.append(
            "感官以视觉 + 一种其它感官为主，可以再丰富一些。"
            "例如打斗场景加入「刀锋破空声」「掌心发麻」。"
        )
    elif non_visual_per_100 < 0.3 and total > 2000:
        score -= 1
        suggestions.append(
            "非视觉感官词偏少，场景可能略显平淡。"
            "描写环境时多用「听/触/嗅」而非只有「看」。"
        )

    score = max(0, min(6, score))
    if score >= 5:
        level, msg = (
            "ok",
            f"多感官描写 ✓（视 {v} · 听 {a} · 触 {t} · 嗅 {s} · 味 {ta}）",
        )
    elif score >= 3:
        level, msg = (
            "warn",
            f"感官略单薄（视 {v} · 听 {a} · 触 {t} · 嗅 {s} · 味 {ta}）",
        )
    else:
        level, msg = "bad", "感官过于单一（以视觉为主）"

    return CheckItem(
        key="senses", label="感官层次",
        score=score, max_score=6, level=level, message=msg,
        suggestions=suggestions,
    )


def _check_meta_break(text: str) -> CheckItem:
    """出戏穿帮：检测打破第四面墙的元叙述。

    形式规则读不懂剧情，但"作者/剧情需要/下一章/读者/主角"这类
    叙述者直接谈论作品本身的写法是零误报强信号——正经故事正文里
    出现它们，等于亲手把读者拉出故事，比任何节奏问题都致命。
    """
    if not text or not text.strip():
        return CheckItem(
            key="meta_break", label="出戏穿帮",
            score=0, max_score=8, level="bad", message="正文为空",
        )

    strong_hits = list(_META_BREAK_STRONG.finditer(text))
    weak_hits = list(_META_BREAK_WEAK.finditer(text))

    # 满分 8：强信号每处 -4（两处即归零），弱信号每处 -1 封顶 -2
    score = 8 - min(8, len(strong_hits) * 4) - min(2, len(weak_hits))
    score = max(0, min(8, score))

    # 定位到命中词所在段落，强信号优先，最多 5 处
    positions = []
    if strong_hits or weak_hits:
        wanted = [m.start() for m in strong_hits] + \
                 [m.start() for m in weak_hits]
        for s, p in _split_paras_with_pos(text):
            if not p.strip():
                continue
            if any(s <= pos < s + len(p) for pos in wanted):
                positions.append((s, s + len(p)))
            if len(positions) >= 5:
                break

    if not strong_hits and not weak_hits:
        level, msg = "ok", "未发现打破故事语境的写法 ✓"
        return CheckItem(
            key="meta_break", label="出戏穿帮",
            score=score, max_score=8, level=level, message=msg,
        )

    parts = []
    if strong_hits:
        parts.append(f"强出戏 {len(strong_hits)} 处"
                     f"（{('、'.join(sorted({m.group(0) for m in strong_hits})))[:20]}）")
    if weak_hits:
        parts.append(f"疑似 {len(weak_hits)} 处")
    if len(strong_hits) >= 2 or score < 4:
        level = "bad"
    elif score >= 6:
        level = "warn"
    else:
        level = "bad"
    msg = "检测到" + "、".join(parts) + "，会把读者直接拉出故事"
    suggestion = (
        "正文中不要出现「作者 / 剧情需要 / 读者 / 下一章 / 主角」这类"
        "谈论作品本身的说法；章节安排和设定矛盾应改写成故事内的情节，"
        "而不是向读者解释。"
    )
    return CheckItem(
        key="meta_break", label="出戏穿帮",
        score=score, max_score=8, level=level, message=msg,
        suggestions=[suggestion], positions=positions,
    )


# ============================================================
# 情绪曲线：供 UI 画节奏图
# ============================================================


def build_emotion_curve(
    text: str, window: int = 200
) -> List[Tuple[int, int, int]]:
    """把整章切成 window 字一个窗口，每窗算一个情绪分。

    情绪分 = 感叹/疑问(×2) + 对话句(×2) + 动作词 + 冲突词
            + 爽点词(×2) + 悬念词

    返回 [(start, end, score), ...]，供 UI 画折线/柱状图。
    """
    if not text:
        return []
    n = len(text)
    if n <= window:
        window = max(1, n)
    result = []
    for i in range(0, n, window):
        seg = text[i : i + window]
        if not seg:
            continue
        exclaim = len(re.findall(r"[！？]", seg))
        dialogue_lines = sum(
            1 for line in seg.split("\n") if _is_dialogue_line(line)
        )
        actions = len(_ACTION_WORDS.findall(seg))
        conflicts = len(_CONFLICT_WORDS_FULL.findall(seg))
        payoffs = len(_PAYOFF_WORDS.findall(seg))
        suspenses = len(_SUSPENSE_WORDS.findall(seg))

        score = (
            exclaim * 2
            + dialogue_lines * 2
            + actions
            + conflicts
            + payoffs * 2
            + suspenses
        )
        result.append((i, min(n, i + window), score))
    return result


# ============================================================
# 主入口
# ============================================================


def check_chapter(text: str) -> Report:
    """对整章文本跑一次追读体检（10 维度 + 情绪曲线）。

    注意：这是形式/节奏分，不是"文笔与剧情质量分"。段落、对话、钩子、
    情绪密度、出戏穿帮都能用规则判定；剧情是否合理、套路是否油腻、
    人设是否崩，规则读不出来，那是 AI 深度体检的职责。
    """
    if not text or not text.strip():
        return Report(total=0, level="差", items=[], stats={"chars": 0})

    items = [
        _check_opening(text),
        _check_closing(text),
        _check_dialogue_ratio(text),
        _check_paragraphs(text),
        _check_rhythm(text),
        _check_info_density(text),
        _check_sentence_variance(text),
        _check_payoff(text),
        _check_senses(text),
        _check_meta_break(text),
    ]
    total = sum(it.score for it in items)
    if total >= 85:
        level = "优"
    elif total >= 70:
        level = "良"
    elif total >= 50:
        level = "中"
    else:
        level = "差"

    # 出戏穿帮一票封顶：形式分再高，频繁打破第四面墙也不能给"优"。
    # bad（多处强出戏）最高"中"，warn 最高"良"。
    meta_item = next((i for i in items if i.key == "meta_break"), None)
    if meta_item is not None:
        if meta_item.level == "bad" and level in ("优", "良"):
            level = "中"
        elif meta_item.level == "warn" and level == "优":
            level = "良"

    lines = [l for l in text.split("\n") if l.strip()]
    stats = {
        "chars": _strip_ws_len(text),
        "lines": len(lines),
        "dialogues": sum(1 for l in lines if _is_dialogue_line(l)),
    }
    return Report(
        total=total,
        level=level,
        items=items,
        stats=stats,
        curve=build_emotion_curve(text),
    )


# ============================================================
# AI 深度体检（prompt 构造 + 响应解析，纯函数）
# ============================================================

_AI_PROMPT_TEMPLATE = """你是资深网文编辑，给下面这章正文做**内容诊断**。

形式问题（段落长短、对白比例、节奏快慢、错别字、标点、元叙述穿帮）
已由规则引擎检查过，你不要重复。你只评规则读不出来的东西：
剧情逻辑、人物、动机、套路、对白质感、情绪真假。

只输出 JSON，不要任何解释、不要 markdown 代码块。格式：
{{
  "verdict": "一句话总评：这章内容上最大的问题是什么，30 字以内",
  "issues": [
    {{
      "severity": "高",
      "type": "逻辑穿帮",
      "quote": "问题在原文中的原句摘录，30 字以内；摘不到就留空",
      "problem": "为什么是问题，30 字以内",
      "fix": "具体怎么改，30 字以内"
    }}
  ],
  "drop_warning": {{
    "point": "全章最可能让读者弃书的位置，20 字以内",
    "reason": "弃书原因，30 字以内"
  }},
  "rewrite_demo": {{
    "original": "issues 里最严重那处的原文，80 字以内",
    "rewritten": "改写后，80 字以内",
    "why": "为什么更好，一句话"
  }}
}}

【type 只能从这六类里选】
- 逻辑穿帮：因果不通、设定前后矛盾、时间/地点/道具穿帮（如前面没车后面上车）
- 人设崩：行为或台词与人物设定、前文表现不符；性格或情绪转变没有铺垫
- 动机牵强：角色为什么这么做站不住脚，纯粹被剧情推着走
- 套路陈词：用烂的桥段与设定、模板化人物、巧合堆砌、油腻辞藻
- 对白出戏：台词不像人话、所有角色一个腔调、大段背景解说、书面腔
- 情绪虚假：情绪没有来由、作者按头让读者哭/笑、空洞抒情

【硬性约束，直接决定响应速度】
- issues：最多 5 条，按 severity 从高到低排序；真没有问题就给空数组 []
- severity 只能是 "高" / "中" / "低"
- quote 必须逐字摘自正文，不要自己造句
- 每条 problem / fix 各一句短话，fix 必须是能动手的改法，不要"建议加强描写"这种空话
- drop_warning：没有明显弃书点就给 null
- rewrite_demo：只挑 issues 里最严重的一处做示范；issues 为空时给 null
- 所有字段用短句，不要解释性长句

【作品背景】
{context}

【章节正文】
{text}"""


def build_ai_prompt(text: str, context: str = "") -> str:
    """构造 AI 深度体检的 prompt。

    text 截断到 4000 字——绝大多数网文章节都在这个范围内；
    超长章节只评估前 4000 字（"追读"最看重开头到中段）。
    """
    ctx = (context or "").strip() or "（无）"
    return _AI_PROMPT_TEMPLATE.format(
        context=ctx,
        text=(text or "")[:4000],
    )


# 六类内容问题；模型漂移到白名单外时归一为"其他"
_ISSUE_TYPES = (
    "逻辑穿帮", "人设崩", "动机牵强", "套路陈词", "对白出戏", "情绪虚假",
)
_ISSUE_SEVERITIES = ("高", "中", "低")


def _short(v, limit: int) -> str:
    return str(v or "").strip()[:limit]


def _parse_issues(v) -> list:
    """解析问题清单。容错：字段缺失、类型错误都跳过；最多 5 条。"""
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:5]:
        if not isinstance(item, dict):
            continue
        severity = _short(item.get("severity", "中"), 4) or "中"
        if severity not in _ISSUE_SEVERITIES:
            severity = "中"
        itype = _short(item.get("type", ""), 8)
        if itype not in _ISSUE_TYPES:
            itype = "其他"
        problem = _short(item.get("problem", ""), 60)
        fix = _short(item.get("fix", ""), 60)
        if not problem and not fix:
            continue
        out.append({
            "severity": severity,
            "type": itype,
            "quote": _short(item.get("quote", ""), 60),
            "problem": problem,
            "fix": fix,
        })
    return out


def _parse_drop_warning(v) -> dict:
    """解析弃读预警。两段全缺时返回空 dict。"""
    if not isinstance(v, dict):
        return {}
    point = _short(v.get("point", ""), 40)
    reason = _short(v.get("reason", ""), 60)
    if not point and not reason:
        return {}
    return {"point": point, "reason": reason}


def _parse_rewrite_demo(v) -> dict:
    """解析改写示范。原文和改写全缺时返回空 dict。"""
    if not isinstance(v, dict):
        return {}
    orig = _short(v.get("original", ""), 200)
    rewritten = _short(v.get("rewritten", ""), 200)
    why = _short(v.get("why", ""), 80)
    if not orig and not rewritten:
        return {}
    return {"original": orig, "rewritten": rewritten, "why": why}


def _legacy_to_issues(data: dict) -> list:
    """旧版报告（suggestions/highlights 时代）降级为问题清单展示。"""
    out = []
    for item in (data.get("suggestions") or [])[:5]:
        if isinstance(item, str):
            action, target, prio = item.strip(), "", "中"
        elif isinstance(item, dict):
            prio = str(item.get("priority", "中") or "中").strip()
            action = str(item.get("action", "") or "").strip()
            target = str(item.get("target", "") or "").strip()
        else:
            continue
        if not action:
            continue
        if prio not in _ISSUE_SEVERITIES:
            prio = "中"
        out.append({
            "severity": prio, "type": "其他",
            "quote": target[:60], "problem": action[:60], "fix": "",
        })
    return out


def _legacy_drop_warning(data: dict) -> dict:
    """旧版 reader_simulation.drop_point/reason 降级为弃读预警。"""
    sim = data.get("reader_simulation")
    if not isinstance(sim, dict):
        # 更早的扁平字段
        if data.get("drop_point") or data.get("drop_reason"):
            sim = data
        else:
            return {}
    point = str(sim.get("drop_point", "") or "").strip()
    reason = str(sim.get("drop_reason", "") or "").strip()
    if (not point or point == "无") and not reason:
        return {}
    return {"point": point[:40], "reason": reason[:60]}


def normalize_ai_data(data: dict) -> dict:
    """把任意时期的 AI 结果（原始解析 dict 或历史落盘 dict）归一为
    当前四区块结构：verdict / issues / drop_warning / rewrite_demo。
    对新结构幂等，供解析器与历史报告回看共用。
    """
    if not isinstance(data, dict):
        return {}
    issues = _parse_issues(data.get("issues"))
    if not issues:
        issues = _legacy_to_issues(data)
    drop_warning = _parse_drop_warning(data.get("drop_warning"))
    if not drop_warning:
        drop_warning = _legacy_drop_warning(data)
    return {
        "verdict": _short(data.get("verdict", ""), 80),
        "issues": issues,
        "drop_warning": drop_warning,
        "rewrite_demo": _parse_rewrite_demo(data.get("rewrite_demo")),
    }


def parse_ai_response(text: str) -> dict:
    """解析 AI 返回的 JSON。

    新格式：verdict / issues / drop_warning / rewrite_demo。
    兼容旧格式：reader_simulation + suggestions 降级映射，
    让质量中心里的历史报告仍可回看。
    """
    from .ai_json import parse_json_response
    data = parse_json_response(text)
    if not isinstance(data, dict):
        return {}
    return normalize_ai_data(data)