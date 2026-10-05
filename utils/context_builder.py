# utils/context_builder.py
"""墨码写作 - AI 上下文构造

从用户输入 + 作品数据里，自动决定该给 AI 提供哪些上下文。

能力：
1. 章节引用识别：输入里出现"第3章""第 5 回"等，自动加载对应章节正文
2. 角色卡锚定：输入里出现人物卡中的名字/别名，自动加载该角色卡
3. 作品概要：人物卡汇总 + 词条 + 大纲
4. 长度裁剪：总字数控制在 MAX_CONTEXT_CHARS 以内

优先级（高→低）：
1. 用户显式 @ 引用的章节 / 角色
2. 用户问题里提到的章节
3. 用户问题里提到的角色
4. 作品概要
"""
from __future__ import annotations

import re
from .cn_number import cn_to_int as _cn_to_int
from dataclasses import dataclass
from typing import List, Optional
from .idea_cards import (
    load_cards, find_cards_in_text, format_card_for_ai, type_label,
)

MAX_CONTEXT_CHARS = 8000

_CHAPTER_REF_RE = re.compile(
    r"第\s*([0-9零一二三四五六七八九十百千万]+)\s*[章节回]"
)



def extract_chapter_number(title: str) -> Optional[int]:
    m = _CHAPTER_REF_RE.search(title or "")
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.isdigit():
        return int(raw)
    return _cn_to_int(raw)


def find_chapters_in_text(text: str, chapters: list) -> list:
    """从用户输入里找提到的章节。"""
    if not text or not chapters:
        return []
    hits = []
    seen_ids = set()

    # "第X章" 模式
    for m in _CHAPTER_REF_RE.finditer(text):
        raw = m.group(1).strip()
        num = int(raw) if raw.isdigit() else _cn_to_int(raw)
        if num is None:
            continue
        for ch in chapters:
            ch_num = extract_chapter_number(ch.get("title", ""))
            if ch_num == num:
                cid = ch.get("id")
                if cid not in seen_ids:
                    hits.append(ch)
                    seen_ids.add(cid)
                break

    # 章节标题关键词匹配（如"徙木立信"）
    for ch in chapters:
        title = ch.get("title", "") or ""
        if not title:
            continue
        core = re.sub(
            r"^第\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回]\s*",
            "", title).strip()
        if len(core) >= 3 and core in text:
            cid = ch.get("id")
            if cid not in seen_ids:
                hits.append(ch)
                seen_ids.add(cid)
    return hits


def find_characters_in_text(text: str, characters: list) -> list:
    """从用户输入里找提到的人物卡。"""
    if not text or not characters:
        return []
    hits = []
    for c in characters:
        names = [c.get("name", "")]
        aliases = c.get("aliases", [])
        if isinstance(aliases, str):
            names.extend(a.strip() for a in aliases.split("，") if a.strip())
        elif isinstance(aliases, list):
            names.extend(str(a).strip() for a in aliases if a)
        names = [n for n in names if n and len(n) >= 2]
        if any(n in text for n in names):
            hits.append(c)
    return hits


def format_character_card(c: dict) -> str:
    """人物卡 dict → 可读文本。"""
    lines = []
    name = (c.get("name") or "").strip()
    if not name:
        return ""
    header = [name]
    if c.get("role"):
        header.append(f"（{c['role']}）")
    lines.append("【" + "".join(header) + "】")

    aliases = c.get("aliases") or []
    if isinstance(aliases, list) and aliases:
        lines.append(f"  别名：{'、'.join(str(a) for a in aliases)}")

    fields = [
        ("gender", "性别"), ("age", "年龄"),
        ("appearance", "外貌"), ("personality", "性格"),
        ("background", "背景"), ("relations", "人物关系"),
        ("first_appear", "首次出场"), ("notes", "备注"),
    ]
    for key, label in fields:
        val = (c.get(key) or "").strip()
        if val:
            lines.append(f"  {label}：{val}")
    return "\n".join(lines)


def format_chapter_text(ch: dict, text: str,
                        is_current: bool = False) -> str:
    """格式化章节正文给 AI。

    is_current=True 时标签写成「【当前章：标题】」，
    让模型明确知道这是用户此刻在看的章节——避免和对话历史里的
    其他章节引用混淆。
    """
    title = ch.get("title", "") or ch.get("id", "")
    prefix = "【当前章：" if is_current else "【"
    return f"{prefix}{title}】\n{text.strip()}"


@dataclass
class ContextItem:
    key: str
    label: str
    content: str
    char_count: int
    kind: str   # "chapter" / "character" / "work_summary"


class ContextBuilder:
    """上下文构造器。"""

    def __init__(self, work, max_chars: int = MAX_CONTEXT_CHARS):
        self.work = work
        self.max_chars = max_chars

    def build(self, user_input: str, extra_refs: list = None,
              current_chapter_id: str = None) -> List[ContextItem]:
        items: List[ContextItem] = []
        seen_keys = set()

        def _add(kind, key, label, content):
            if key in seen_keys or not content.strip():
                return
            seen_keys.add(key)
            items.append(ContextItem(
                key=key, label=label, content=content,
                char_count=len(content), kind=kind))

        if self.work is None:
            return items

        chapters = self.work.chapters or []

        # 0. 显式 @ 引用（最高优先级）
        for ref in (extra_refs or []):
            if not isinstance(ref, tuple) or len(ref) != 2:
                continue
            kind, ident = ref
            if kind == "chapter":
                ch = next((c for c in chapters if c.get("id") == ident), None)
                if ch:
                    text = self.work.read_chapter(ch["id"])
                    _add("chapter", f"chapter::{ch['id']}",
                         f"第{ch.get('title', '')}",
                         format_chapter_text(ch, text))
            elif kind == "character":
                chars = self._safe_chars()
                c = next((x for x in chars if x.get("name") == ident), None)
                if c:
                    _add("character", f"character::{ident}",
                         f"角色卡·{ident}", format_character_card(c))
            elif kind == "card":
                try:
                    from .idea_cards import load_cards, format_card_for_ai
                    cards = load_cards(self.work)
                    card = next(
                        (x for x in cards if x.get("title") == ident), None)
                    if card:
                        _add("card", f"idea::{card['id']}",
                             f"灵感·{ident}", format_card_for_ai(card))
                except Exception:
                    pass

        # 1. 用户输入里提到的章节
        found_chapters = find_chapters_in_text(user_input, chapters)
        for ch in found_chapters:
            text = self.work.read_chapter(ch["id"])
            _add("chapter", f"chapter::{ch['id']}",
                 f"第{ch.get('title', '')}",
                 format_chapter_text(ch, text))

        # 2. 兜底：用户没引用任何章节，用当前章节
        if not found_chapters and current_chapter_id:
            ch = next(
                (c for c in chapters if c.get("id") == current_chapter_id),
                None)
            if ch:
                text = self.work.read_chapter(ch["id"])
                _add("chapter", f"chapter::{ch['id']}",
                     f"当前章·{ch.get('title', '')}",
                     format_chapter_text(ch, text, is_current=True))

        # 3. 用户输入里提到的角色
        for c in find_characters_in_text(user_input, self._safe_chars()):
            name = c.get("name", "")
            _add("character", f"character::{name}",
                 f"角色卡·{name}", format_character_card(c))

        # 4. 作品概要
        summary = self._build_work_summary()
        if summary:
            _add("work_summary", "work_summary",
                 "作品资料", summary)

        return self._trim(items)
        
    def _trim(self, items: List[ContextItem]) -> List[ContextItem]:
        result = []
        total = 0
        for it in items:
            if total + it.char_count > self.max_chars:
                remain = self.max_chars - total
                # remain <= 0：一点都放不下，直接停
                # remain > 0：把能放下的部分塞进去
                # （不设 500 字硬门槛，否则 max_chars 配小了会静默返回空上下文）
                if remain > 0:
                    # 创建副本再截断，不原地改原 ContextItem：
                    # 调用方（ai_panel._last_context_items）还持有原对象
                    # 引用，用于"参考了哪些资料"回执显示。原地改会让
                    # 回执显示的字数偏小。
                    truncated = ContextItem(
                        key=it.key,
                        label=it.label,
                        content=it.content[:remain] + "\n…（已截断）",
                        char_count=0,
                        kind=it.kind,
                    )
                    truncated.char_count = len(truncated.content)
                    result.append(truncated)
                break
            result.append(it)
            total += it.char_count
        return result

    def _safe_chars(self) -> list:
        try:
            return self.work.load_characters() or []
        except Exception:
            return []

    def _build_work_summary(self) -> str:
        if self.work is None:
            return ""
        parts = [f"作品：《{self.work.title}》"]

        try:
            chars = self._safe_chars()
            if chars:
                lines = ["【角色简表】"]
                for c in chars[:30]:
                    name = (c.get("name") or "").strip()
                    if not name:
                        continue
                    role = (c.get("role") or "").strip()
                    bits = [name]
                    if role:
                        bits.append(f"（{role}）")
                    one = (c.get("personality") or "").strip()[:30]
                    if one:
                        bits.append(f"——{one}")
                    lines.append("  · " + " ".join(bits))
                if len(lines) > 1:
                    parts.append("\n".join(lines))
        except Exception:
            pass

        try:
            wb = self.work.load_wordbook() or {}
            for cat in wb.get("categories", []):
                words = cat.get("words", [])
                if not words:
                    continue
                names = []
                for w in words[:15]:
                    n = (w.get("word") if isinstance(w, dict) else w) or ""
                    n = str(n).strip()
                    if n:
                        names.append(n)
                if names:
                    parts.append(f"【{cat.get('name', '')}】" + "、".join(names))
        except Exception:
            pass

        try:
            outline = self.work.read_side_doc("outline")
            if outline.strip():
                parts.append(f"【作品大纲】\n{outline.strip()[:800]}")
        except Exception:
            pass

        try:
            cards = load_cards(self.work)
            if cards:
                lines = ["【灵感卡片索引】"]
                for c in cards[:10]:
                    t = type_label(c.get("type", "idea"))
                    title = c.get("title", "")
                    lines.append(f"  · [{t}] {title}")
                if len(lines) > 1:
                    parts.append("\n".join(lines))
        except Exception:
            pass


        return "\n".join(parts)

    @staticmethod
    def render(items: List[ContextItem]) -> str:
        if not items:
            return ""
        return "\n\n".join(it.content for it in items)