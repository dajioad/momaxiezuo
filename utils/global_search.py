# utils/global_search.py
"""墨码写作 - 全局搜索核心逻辑（纯函数，无 Qt 依赖）

扫描整部作品的所有章节，返回结构化结果供 UI 展示。

返回结构：
    [
        {
            "chapter_id": "0001",
            "chapter_title": "第一章 开端",
            "chapter_index": 0,
            "match_count": 3,
            "matches": [
                {
                    "start": 100,          # 正文里的绝对位置
                    "end": 103,
                    "snippet": "...他叫【王小明】,是这次...",
                    "match_in_snippet_start": 20,   # 相对位置（冗余，便于 UI）
                    "match_in_snippet_end": 23,
                },
            ],
        },
    ]
"""
from __future__ import annotations

import re
from typing import List, Tuple


def _build_pattern(keyword: str, use_regex: bool, case_sensitive: bool):
    """构造编译好的正则表达式；出错时返回 (None, 错误消息)。"""
    if not keyword:
        return None, "关键词为空"
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern_str = keyword if use_regex else re.escape(keyword)
    try:
        return re.compile(pattern_str, flags), ""
    except re.error as e:
        return None, f"正则错误：{e}"


def _search_text_with_pattern(text: str, pattern, *,
                              context: int = 20,
                              max_hits_per_chapter: int = 200) -> List[dict]:
    """用已编译正则在单段文本里搜索（全书搜索时只编译一次，逐章复用）。"""
    if not text or pattern is None:
        return []
    results = []
    for m in pattern.finditer(text):
        if len(results) >= max_hits_per_chapter:
            break
        start, end = m.start(), m.end()
        if end <= start:
            continue
        pre_start = max(0, start - context)
        post_end = min(len(text), end + context)

        pre = text[pre_start:start].replace("\n", " ")
        mid = text[start:end].replace("\n", " ")
        post = text[end:post_end].replace("\n", " ")

        prefix = "…" if pre_start > 0 else ""
        suffix = "…" if post_end < len(text) else ""
        snippet = f"{prefix}{pre}【{mid}】{post}{suffix}"

        rel_start = len(prefix) + len(pre) + 1   # +1 因为中间的 【
        rel_end = rel_start + len(mid)

        results.append({
            "start": start,
            "end": end,
            "snippet": snippet,
            "match_in_snippet_start": rel_start,
            "match_in_snippet_end": rel_end,
        })
    return results


def search_text(text: str, keyword: str, *,
                use_regex: bool = False,
                case_sensitive: bool = False,
                context: int = 20,
                max_hits_per_chapter: int = 200) -> List[dict]:
    """在单段文本里搜索，返回匹配列表。

    每项：
        start / end               匹配在原文中的位置
        snippet                   带上下文的展示文本（匹配处用【】包裹）
        match_in_snippet_start    匹配在 snippet 里的相对起点
        match_in_snippet_end      匹配在 snippet 里的相对终点

    context 表示匹配前后各取多少个字符作为上下文。
    """
    if not text or not keyword:
        return []
    pattern, _err = _build_pattern(keyword, use_regex, case_sensitive)
    if pattern is None:
        return []
    return _search_text_with_pattern(
        text, pattern, context=context,
        max_hits_per_chapter=max_hits_per_chapter)


def search_work(work, keyword: str, *,
                use_regex: bool = False,
                case_sensitive: bool = False,
                context: int = 20,
                max_total: int = 500,
                progress_cb=None) -> Tuple[List[dict], str, bool]:
    """扫描整部作品，返回 (结果列表, 错误消息, 是否被截断)。

    - work 为 None 时返回空
    - 最多收集 max_total 条匹配（防止极端关键词把 UI 撑爆）
    - progress_cb(done, total, title) 会在每章开始前回调一次

    结果按章节顺序排列；某一章的匹配数超过 max_hits_per_chapter
    （内部固定 200）会被截断但不影响其他章节。
    """
    if work is None or not keyword:
        return [], "", False

    pattern, err = _build_pattern(keyword, use_regex, case_sensitive)
    if pattern is None:
        return [], err, False

    chapters = work.chapters or []
    total = len(chapters)
    results: List[dict] = []
    truncated = False
    used = 0  # 已收集匹配总数；旧实现每章用 sum 重算，O(章数²)

    for idx, ch in enumerate(chapters):
        if progress_cb:
            try:
                progress_cb(idx, total, ch.get("title", ""))
            except Exception:
                pass

        cid = ch.get("id", "")
        title = ch.get("title", "")
        text = work.read_chapter(cid)
        if not text:
            continue

        matches = _search_text_with_pattern(text, pattern, context=context)
        if not matches:
            continue

        # 全局总数截断
        remain = max_total - used
        if remain <= 0:
            truncated = True
            break
        if len(matches) > remain:
            matches = matches[:remain]
            truncated = True

        results.append({
            "chapter_id": cid,
            "chapter_title": title or cid,
            "chapter_index": idx,
            "match_count": len(matches),
            "matches": matches,
        })
        used += len(matches)

        if truncated:
            break

    if progress_cb:
        try:
            progress_cb(total, total, "")
        except Exception:
            pass

    return results, "", truncated


def format_search_summary(results: List[dict], truncated: bool = False) -> str:
    """把结果列表汇总成一行中文摘要。"""
    if not results:
        return "没有找到匹配"
    total = sum(r["match_count"] for r in results)
    chapters = len(results)
    text = f"共 {total} 处匹配 · 涉及 {chapters} 章"
    if truncated:
        text += "（已截断，仅显示前 500 条）"
    return text