# utils/idea_cards.py
"""墨码写作 - 灵感卡片

从 ideas.txt 升级为结构化卡片（ideas.json）。

数据结构：
{
  "version": 1,
  "cards": [
    {
      "id": "abc123",
      "type": "conflict",       # conflict / style / hook / idea
      "title": "朝堂对峙",
      "body": "李斯在朝堂上公开质疑……",
      "tags": ["李斯", "王绾"],
      "chapter_ref": "",        # 可选：关联的章节 id
      "created_at": "2026-09-18 10:00:00",
      "updated_at": "..."
    }
  ]
}

兼容：老的 ideas.txt 存在且 ideas.json 不存在时，自动迁移。
"""
from __future__ import annotations

import os
import json
import uuid
import re
from datetime import datetime
from typing import List, Optional

from .logger import get_logger
from .work_store import atomic_write_json

logger = get_logger()

_FILE = "ideas.json"
_TXT_FILE = "ideas.txt"

CARD_TYPES = [
    ("conflict", "冲突设计", "#e74c3c"),
    ("style", "文风片段", "#4CAF50"),
    ("hook", "开篇钩子", "#e67e22"),
    ("idea", "灵感碎片", "#3498db"),
]

_TYPE_KEYS = {k for k, _, _ in CARD_TYPES}


def _path(work) -> str:
    return os.path.join(work.root_dir, _FILE)


def _txt_path(work) -> str:
    return os.path.join(work.root_dir, _TXT_FILE)


def _migrate_from_txt(work) -> List[dict]:
    """从 ideas.txt 迁移：每段（按空行分隔）→ 一张卡片。"""
    txt_path = _txt_path(work)
    if not os.path.exists(txt_path):
        return []
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    if not text.strip():
        return []
    cards = []
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        lines = para.split("\n")
        first_raw = lines[0]                # 不 strip，保留长度用于切分
        first = first_raw.strip()           # 只用于标题
        title = first[:20] if len(first) > 20 else first
        # 用原始首行的长度切，不要用 strip 后的长度——strip 会去掉
        # 首尾空白，导致 len(first) < len(first_raw)，para[len(first):]
        # 会错位，把首行尾巴混进 body。
        if len(lines) > 1:
            body = para[len(first_raw):].lstrip("\n")
        else:
            body = first
        cards.append({
            "id": uuid.uuid4().hex[:12],
            "type": "idea",
            "title": title or "未命名",
            "body": body or first,
            "tags": [],
            "chapter_ref": "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": "",
        })
    logger.info(f"灵感便签迁移：{len(cards)} 段 → {len(cards)} 张卡片")
    return cards


def _default_data() -> dict:
    return {"version": 1, "cards": []}


def load_cards(work) -> List[dict]:
    """读取卡片。首次调用自动从 ideas.txt 迁移。"""
    path = _path(work)
    if not os.path.exists(path):
        # 尝试从 txt 迁移
        migrated = _migrate_from_txt(work)
        if migrated:
            data = {"version": 1, "cards": migrated}
            try:
                atomic_write_json(path, data)
                logger.info(f"已迁移 {len(migrated)} 张灵感卡片")
            except OSError as e:
                logger.warning(f"迁移卡片保存失败: {e}")
            return migrated
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取灵感卡片失败: {e}")
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("cards", [])
    if not isinstance(raw, list):
        return []
    result = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or uuid.uuid4().hex[:12])
        ctype = c.get("type", "idea")
        if ctype not in _TYPE_KEYS:
            ctype = "idea"
        tags = c.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        result.append({
            "id": cid,
            "type": ctype,
            "title": str(c.get("title", "") or "").strip(),
            "body": str(c.get("body", "") or ""),
            "tags": [str(t).strip() for t in tags if str(t).strip()],
            "chapter_ref": str(c.get("chapter_ref", "") or ""),
            "created_at": str(c.get("created_at", "") or ""),
            "updated_at": str(c.get("updated_at", "") or ""),
        })
    return result


def save_cards(work, cards: List[dict]):
    """原子写入卡片列表。"""
    data = {"version": 1, "cards": list(cards or [])}
    atomic_write_json(_path(work), data)


def add_card(work, card_type: str, title: str, body: str,
             tags: List[str] = None, chapter_ref: str = "") -> dict:
    if card_type not in _TYPE_KEYS:
        card_type = "idea"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    card = {
        "id": uuid.uuid4().hex[:12],
        "type": card_type,
        "title": (title or "").strip() or "未命名",
        "body": (body or "").strip(),
        "tags": list(tags or []),
        "chapter_ref": chapter_ref or "",
        "created_at": now,
        "updated_at": "",
    }
    cards = load_cards(work)
    cards.insert(0, card)   # 新建的排最前
    save_cards(work, cards)
    return card


def update_card(work, card_id: str, **changes):
    cards = load_cards(work)
    for c in cards:
        if c["id"] == card_id:
            for k, v in changes.items():
                if k in ("title", "body"):
                    c[k] = str(v or "").strip()
                elif k == "type" and v in _TYPE_KEYS:
                    c["type"] = v
                elif k == "tags":
                    if isinstance(v, list):
                        c["tags"] = [str(t).strip() for t in v if str(t).strip()]
                elif k == "chapter_ref":
                    c["chapter_ref"] = str(v or "")
            c["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    save_cards(work, cards)


def delete_card(work, card_id: str):
    cards = [c for c in load_cards(work) if c["id"] != card_id]
    save_cards(work, cards)


def find_cards_in_text(text: str, cards: List[dict]) -> List[dict]:
    """从用户输入里找提到的卡片。匹配标题（≥4 字）。"""
    if not text or not cards:
        return []
    hits = []
    for c in cards:
        title = (c.get("title") or "").strip()
        if len(title) >= 4 and title in text:
            hits.append(c)
    return hits


def type_label(t: str) -> str:
    for k, lab, _ in CARD_TYPES:
        if k == t:
            return lab
    return "灵感碎片"


def type_color(t: str) -> str:
    for k, _, color in CARD_TYPES:
        if k == t:
            return color
    return "#3498db"


def format_card_for_ai(c: dict) -> str:
    """把卡片格式化为给 AI 的文本。"""
    label = type_label(c.get("type", "idea"))
    title = c.get("title", "")
    body = c.get("body", "")
    lines = [f"【灵感卡片·{label}】{title}"]
    if body:
        lines.append(body)
    tags = c.get("tags") or []
    if tags:
        lines.append(f"标签：{'、'.join(tags)}")
    return "\n".join(lines)