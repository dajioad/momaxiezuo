# utils/consistency.py
"""墨码写作 - 一致性检查（纯规则引擎，无 Qt、无网络）

找什么：**同一人的不同写法**。

算法（v3）：
- 以人物卡（含别名）+ 高频词「角色姓名」里的名字为**锚点**
- 到全书正文里扫**与锚点相似**的串，两条通道满足任一即视为疑似：
  1. 编辑距离 ≤ 1     —— 覆盖单字错（"方知有" / "方只有"）
  2. 拼音完全相同     —— 覆盖同音错（"方知有" / "方只又"）
- 出现 ≥ 2 次的相似串才报告（避免偶然字符组合）
- 排除"锚点的前缀/后缀延伸"：
  "方知有立" = "方知有"+"立"（人名接动词），不是另一个人名
  "方知" = "方知有" 缺尾字（被截断），也不是名字

命中分类（match_type）：
- "both"  同音 + 形近（编辑距离 ≤ 1）→ 极可疑
- "sound" 仅同音（编辑距离 ≥ 2，如"方只又"）→ 可疑
- "shape" 仅形近（拼音不同）→ 可能巧合

置信分层（confidence）：
- "high"   → UI 优先展示（both / sound）
- "medium" → UI 排中（仅形近的单字错）
- "low"    → UI 排末（形近且出现少）

排序：confidence 优先，同档内按出现次数降序。

v3 新增：
- 拼音通道（需 pypinyin；缺失时静默降级，只跑编辑距离通道）
- 前缀/后缀延伸过滤，避免"方知有立"、"方知有看"这类垃圾
- match_type / confidence 分类，供 UI 分层展示
- SimilarPair 携带精确坐标 (chapter_id, start, end)，供 UI 定位 / 替换
- 忽略列表持久化到 works/<作品>/consistency_ignored.json
"""
from __future__ import annotations

import bisect
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Dict, Tuple

from .logger import get_logger

logger = get_logger()


_HANZI_RE = re.compile(r"[\u4e00-\u9fff]+")

# 置信度排序权重：high 在前
_CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class SimilarPair:
    anchor: str          # 人物卡里的名字（权威写法）
    variant: str         # 正文里发现的疑似不同写法
    distance: int
    count_anchor: int
    count_variant: int
    # 分类 / 分层（v3 新增）
    match_type: str = "shape"    # "both" / "sound" / "shape"
    confidence: str = "medium"   # "high" / "medium" / "low"
    # 章节归属
    chapters_anchor: List[str] = field(default_factory=list)
    chapters_variant: List[str] = field(default_factory=list)
    # 精确坐标：[(chapter_id, start, end), ...]，供 UI 定位 / 替换用
    anchor_positions: List[Tuple[str, int, int]] = field(default_factory=list)
    variant_positions: List[Tuple[str, int, int]] = field(default_factory=list)


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离，最大只算到 3。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 3:
        return 3
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


@lru_cache(maxsize=8192)
def _pinyin_key(name: str) -> str:
    """名字转拼音（去声调、小写、连写）。

    pypinyin 缺失或转换失败时返回一个**不可能冲突**的哨兵值（而不是
    统一空串），否则所有名字的拼音都是空串，会导致"拼音相同"这一通道
    完全失效，反而把所有编辑距离 > 1 的无关串都误报出来。

    哨兵格式：__no_pinyin__<name>__，每个名字各不相同。
    """
    if not name:
        return ""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return f"__no_pinyin__{name}__"
    try:
        # lazy_pinyin 默认 style=NORMAL，去声调
        return "".join(lazy_pinyin(name)).lower()
    except Exception:
        return f"__no_pinyin__{name}__"


def _is_real_pinyin(key: str) -> bool:
    """判断 _pinyin_key 返回的是真实拼音，还是降级哨兵。"""
    return bool(key) and not key.startswith("__no_pinyin__")


def _classify(seg: str, anchor: str) -> Tuple[str, str]:
    """判断命中类型和置信度。

    返回 (match_type, confidence)：
      match_type: "both" / "sound" / "shape"
      confidence: "high" / "medium" / "low"

    规则：
      - 拼音同 + 编辑距离 ≤ 1  → (both, high)   极可疑
      - 拼音同 + 编辑距离 ≥ 2  → (sound, high)  同音多字错（"方只又"）
      - 拼音不同 + 编辑距离 ≤ 1 → (shape, medium) 单字形近（可能是巧合）
    """
    dist = _edit_distance(seg, anchor)
    dist_ok = dist <= 1
    py_a = _pinyin_key(anchor)
    py_b = _pinyin_key(seg)
    # 哨兵值不算"同音"——pypinyin 缺失时全部走 shape 通道
    py_ok = _is_real_pinyin(py_a) and py_a == py_b

    if py_ok and dist_ok:
        return "both", "high"
    if py_ok:
        return "sound", "high"
    # 到这里 dist_ok 必为 True（否则上游已 continue）
    return "shape", "medium"


def collect_names(work) -> List[str]:
    """从人物卡 + 高频词「角色姓名」分类收集锚点名字（含别名）。"""
    names = set()
    try:
        for c in work.load_characters():
            n = (c.get("name") or "").strip()
            if n:
                names.add(n)
            for a in (c.get("aliases") or []):
                a = str(a).strip()
                if a:
                    names.add(a)
    except Exception:
        pass
    try:
        wb = work.load_wordbook() or {}
        for cat in wb.get("categories", []):
            if cat.get("name") != "角色姓名":
                continue
            for w in cat.get("words", []):
                n = (w.get("word") if isinstance(w, dict) else w) or ""
                n = str(n).strip()
                if n:
                    names.add(n)
    except Exception:
        pass
    result = []
    for n in names:
        if 2 <= len(n) <= 4 and _HANZI_RE.fullmatch(n):
            result.append(n)
    return sorted(result)


def scan_work(work, max_chapters: int = 3000,
              min_variant_count: int = 2,
              ignored_pairs: set = None) -> List[SimilarPair]:
    """扫描全书，找疑似"同人不同名"。

    max_chapters：扫描章节上限（安全阀，防止极端规模作品卡死 UI）。
        长篇压测：2000 章 / 150 万字实测 < 1 秒，故默认放到 3000。
    min_variant_count：疑似串至少出现几次才报告。
    ignored_pairs：已忽略的 (anchor, variant) 集合（元组，已排序）。

    检测两条通道（满足任一即视为疑似）：
    1. 编辑距离 ≤ 1     —— 覆盖单字错（"方知有"/"方只有"）
    2. 拼音完全相同     —— 覆盖同音错（"方知有"/"方只又"）

    过滤规则：变体不能是"锚点 + 后续字"（"方知有立"）或"锚点缺尾字"
    （"方知"）——这类匹配本质是"人名接动词"或"被截断"，不是另一个
    人名，必须排除。

    排序：confidence 优先（high 在前），档内按出现次数降序。
    """
    anchors = collect_names(work)
    if not anchors:
        return []
    ignored = ignored_pairs or set()

    # 1. 全书拼成一段，记章节起止位置
    chapters = work.chapters[:max_chapters]
    full_text_parts = []
    offsets: List[tuple] = []   # [(start, end, cid), ...]
    pos = 0
    for ch in chapters:
        cid = ch.get("id")
        text = work.read_chapter(cid) or ""
        start = pos
        full_text_parts.append(text)
        full_text_parts.append("\n")
        pos += len(text) + 1
        offsets.append((start, pos, cid))
    full_text = "".join(full_text_parts)

    # offsets 按 start 严格递增，二分定位：
    # 旧实现每次命中都线性扫章数，2000 章时单次 O(2000)。
    _starts = [o[0] for o in offsets]

    def _cid_local(offset: int) -> Tuple[str, int]:
        """全文偏移 → (章节 id, 章节内局部偏移)。失败返回 ("", -1)。"""
        idx = bisect.bisect_right(_starts, offset) - 1
        if idx < 0:
            return "", -1
        s, e, cid = offsets[idx]
        if offset < e:
            return cid, offset - s
        return "", -1

    # 2. 每个锚点在正文里扫相似串
    pairs: List[SimilarPair] = []
    seen_pairs = set()

    for anchor in anchors:
        n = len(anchor)
        first_ch = anchor[0]

        # {variant: [position, ...]}
        variants: Dict[str, List[int]] = {}

        # 按首字快速定位所有可能位置，避免逐字符扫全文
        for win_len in (n - 1, n, n + 1):
            if win_len < 2 or win_len > 6:
                continue
            idx = 0
            while True:
                p = full_text.find(first_ch, idx)
                if p < 0:
                    break
                idx = p + 1
                if p + win_len > len(full_text):
                    continue
                seg = full_text[p:p + win_len]
                if not _HANZI_RE.fullmatch(seg):
                    continue
                if seg == anchor:
                    continue

                # ---- 排除"锚点的前缀/后缀延伸" ----
                # "方知有立" = "方知有"+"立"（人名接动词）
                # "方知" = "方知有" 缺尾字（被截断）
                # 这两种都不是另一个人名，必须排除
                if seg.startswith(anchor) or anchor.startswith(seg):
                    continue

                # ---- 两条检测通道（短路求值） ----
                # 1) 编辑距离 ≤ 1：覆盖单字错
                # 2) 拼音完全相同：覆盖同音错
                # 编辑距离放前面：绝大多数无关串在这一步就被过滤，
                # 只有"疑似但不接近"的才会去跑拼音，性能友好。
                if _edit_distance(seg, anchor) > 1 \
                        and _pinyin_key(seg) != _pinyin_key(anchor):
                    continue

                variants.setdefault(seg, []).append(p)

        # 3. 过滤 + 生成 pair
        for variant, var_positions in variants.items():
            if len(var_positions) < min_variant_count:
                continue
            key = tuple(sorted([anchor, variant]))
            if key in seen_pairs:
                continue
            if key in ignored:
                continue
            seen_pairs.add(key)

            # 找 anchor 在正文里的所有位置
            anchor_positions = []
            idx = 0
            while True:
                p = full_text.find(anchor, idx)
                if p < 0:
                    break
                anchor_positions.append(p)
                idx = p + 1

            # 转成章节内坐标
            anchor_xy: List[Tuple[str, int, int]] = []
            for p in anchor_positions:
                cid, local = _cid_local(p)
                if cid:
                    anchor_xy.append((cid, local, local + len(anchor)))

            variant_xy: List[Tuple[str, int, int]] = []
            for p in var_positions:
                cid, local = _cid_local(p)
                if cid:
                    variant_xy.append((cid, local, local + len(variant)))

            ch_a = sorted({x[0] for x in anchor_xy})
            ch_b = sorted({x[0] for x in variant_xy})

            match_type, conf = _classify(variant, anchor)

            pairs.append(SimilarPair(
                anchor=anchor,
                variant=variant,
                distance=_edit_distance(anchor, variant),
                count_anchor=len(anchor_positions),
                count_variant=len(var_positions),
                match_type=match_type,
                confidence=conf,
                chapters_anchor=ch_a,
                chapters_variant=ch_b,
                anchor_positions=anchor_xy,
                variant_positions=variant_xy,
            ))

    # 排序：confidence 优先（high 在前），档内按出现次数降序
    pairs.sort(key=lambda p: (
        _CONF_ORDER.get(p.confidence, 99),
        -(p.count_anchor + p.count_variant),
    ))
    return pairs


# ============================================================
# 忽略列表持久化：works/<作品>/consistency_ignored.json
# ============================================================

_IGNORE_FILE = "consistency_ignored.json"


def _ignore_path(work) -> str:
    return os.path.join(work.root_dir, _IGNORE_FILE)


def load_ignored(work) -> set:
    """返回已忽略的 (anchor, variant) 集合（元组，已排序）。"""
    path = _ignore_path(work)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        out = set()
        for p in pairs:
            if isinstance(p, list) and len(p) == 2:
                out.add(tuple(sorted([str(p[0]), str(p[1])])))
        return out
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取一致性忽略列表失败: {e}")
        return set()


def save_ignored(work, ignored_set: set):
    path = _ignore_path(work)
    pairs = [list(p) for p in sorted(ignored_set)]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pairs": pairs}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"保存一致性忽略列表失败: {e}")


def add_ignored(work, anchor: str, variant: str):
    s = load_ignored(work)
    s.add(tuple(sorted([anchor, variant])))
    save_ignored(work, s)


def remove_ignored(work, anchor: str, variant: str):
    s = load_ignored(work)
    s.discard(tuple(sorted([anchor, variant])))
    save_ignored(work, s)


def clear_ignored(work):
    save_ignored(work, set())