# utils/chapter_order.py
"""墨码写作 - 章节顺序检测（纯函数）

用「最长递增子序列」（LIS）找出**被挪动位置**的章节。

为什么不用相邻比较：
- 相邻比较："29, 38, 30, 31" 会标 30（30 < 38），但用户实际挪动的是 38
- LIS 思路：找出序列里最长的递增链（= 没被动的那些），剩下的就是被挪动的

例：
    29, 38, 30, 31, 32, ..., 39
    LIS = 29, 30, 31, ..., 39（10 个）
    剩下 38 → 标红 38  ✓

    29, 30, 30, 31
    LIS = 29, 30, 31
    剩下一个 30（重复的）→ 标红它  ✓

支持阿拉伯数字和中文数字：第3章 / 第三章 / 第 3 章 / 第10回
不检测"跳跃"（29 → 31）——删章节后跳号是正常的。
"""
from __future__ import annotations

import re
import bisect
from typing import Optional, List, Tuple
from .cn_number import cn_to_int as _cn_to_int


_CHAPTER_NUM_RE = re.compile(
    r"第\s*([0-9]+|[零一二三四五六七八九十百千万]+)\s*[章节回卷部篇]"
)


def extract_chapter_number(title: str) -> Optional[int]:
    """从标题里提取章节号。找不到返回 None。

    支持：第1章 / 第一章 / 第 10 回 / 第3卷 / 第三部 等。
    """
    if not title:
        return None
    m = _CHAPTER_NUM_RE.search(title)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.isdigit():
        return int(raw)
    return _cn_to_int(raw)


def _lis_indices(seq: List[int]) -> List[int]:
    """返回最长**严格**递增子序列的元素索引（O(n log n)）。

    用标准 patience sorting + parent 数组回溯。
    严格递增：相同数字只保留一个（重复的被排除）。
    """
    n = len(seq)
    if n == 0:
        return []
    tails: List[int] = []
    tails_idx: List[int] = []
    parent = [-1] * n

    for i, x in enumerate(seq):
        j = bisect.bisect_left(tails, x)
        if j == len(tails):
            tails.append(x)
            tails_idx.append(i)
            parent[i] = tails_idx[j - 1] if j > 0 else -1
        elif tails[j] != x:
            # 只在严格更小时替换 tails。
            # 重复值不替换 → 保留"先出现的索引"，排除"后出现的"。
            # 原因：用户把某章标题改成和前面一章重复的章号时，
            # "后出现的那一章"（改动引入者）才是应该标红的对象。
            # 若替换 tails，LIS 会保留后出现的索引、排除先出现的，
            # 结果标红了"原章节"，和用户直觉完全相反。
            tails[j] = x
            tails_idx[j] = i
            parent[i] = tails_idx[j - 1] if j > 0 else -1
        # tails[j] == x 时跳过：该元素不进入 LIS，等价于被标记为
        # 异常。parent[i] 保持 -1，不会被回溯路径引用到。

    result: List[int] = []
    k = tails_idx[-1] if tails_idx else -1
    while k >= 0:
        result.append(k)
        k = parent[k]
    result.reverse()
    return result


def _diagnose_reason(numbered: List[Tuple[str, int, str]],
                     i: int) -> str:
    """给第 i 个"位置不对"的章节生成原因提示。"""
    _, num, title = numbered[i]
    prev = numbered[i - 1] if i > 0 else None
    nxt = numbered[i + 1] if i + 1 < len(numbered) else None

    if prev is not None and prev[1] == num:
        return f"⚠ 与上一章章节号重复（都是第 {num} 章）"
    if nxt is not None and nxt[1] == num:
        return f"⚠ 与下一章章节号重复（都是第 {num} 章）"
    if prev is not None and num < prev[1]:
        return (f"⚠ 章节号倒退：本章第 {num} 章，"
                f"但上一章是「{prev[2]}」（第 {prev[1]} 章）")
    if nxt is not None and num > nxt[1]:
        return (f"⚠ 章节号与显示位置不符：本章标题是「{title}」"
                f"（第 {num} 章），却排在第 {nxt[1]} 章前面。"
                f"可能是被拖动了位置，或标题里的章节号写错了。")
    return f"⚠ 章节号与显示顺序不符（第 {num} 章）"


def find_out_of_order(chapters: list) -> dict:
    """用 LIS 找出"位置不对"的章节，返回 {chapter_id: reason}。

    参数 chapters: [{"id": ..., "title": ...}, ...]，顺序即显示顺序。
    跳过没有章节号的章节（序章/楔子/番外等）。
    """
    numbered: List[Tuple[str, int, str]] = []
    for ch in chapters:
        cid = ch.get("id")
        if not cid:
            continue
        num = extract_chapter_number(ch.get("title", ""))
        if num is not None:
            numbered.append((cid, num, ch.get("title", "")))

    if len(numbered) < 2:
        return {}

    nums = [n for _, n, _ in numbered]
    in_lis = set(_lis_indices(nums))

    result = {}
    for i, (cid, _num, _title) in enumerate(numbered):
        if i in in_lis:
            continue
        result[cid] = _diagnose_reason(numbered, i)
    return result