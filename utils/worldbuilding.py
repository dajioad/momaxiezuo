# utils/worldbuilding.py
"""墨码写作 - 世界观设定库

存储：works/<作品>/worldbuilding.json

结构：
    {
      "version": 1,
      "entries": [
        {
          "id": "wb_a1b2c3",
          "type": "location",         # location/faction/era/system/creature
          "name": "青锋山",
          "aliases": ["青锋", "剑山"],
          "parent": "青云宗",           # 上级条目名（可为空）
          "attributes": {              # 自由键值对（"位置": "东荒北境"）
            "位置": "东荒北境",
            "海拔": "三千丈"
          },
          "description": "青云宗所在，山巅有剑冢。",
          "chapter_refs": ["0001", "0005"],
          "tags": ["名山", "剑修"],
          "color": "#3B82F6",
          "created_at": "2026-09-21 10:00:00",
          "updated_at": ""
        }
      ]
    }

设计取舍：
- type 只有 5 种，UI 上按 type 过滤；type 外的自由扩展不做
- attributes 用 dict 而不是 list，方便"位置=东荒北境"这样直读
- chapter_refs 允许关联多个章节，为"跳转"和"AI 上下文"用
- 跟人物卡一样的 id/created_at/updated_at 骨架，方便复用现有代码思路
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict

from .logger import get_logger
from .work_store import atomic_write_json

logger = get_logger()

_FILE = "worldbuilding.json"

# 5 种类型：type_key → (显示名, 颜色)
ENTRY_TYPES = [
    ("location", "地理", "#3B82F6"),
    ("faction", "势力", "#8B5CF6"),
    ("era", "年代", "#10B981"),
    ("system", "体系", "#F59E0B"),
    ("creature", "生物", "#EC4899"),
]
_TYPE_KEYS = {k for k, _, _ in ENTRY_TYPES}
_TYPE_LABELS = {k: lab for k, lab, _ in ENTRY_TYPES}
_TYPE_COLORS = {k: col for k, _, col in ENTRY_TYPES}


def type_label(t: str) -> str:
    return _TYPE_LABELS.get(t, "其他")


def type_color(t: str) -> str:
    return _TYPE_COLORS.get(t, "#64748B")


def _path(work) -> str:
    return os.path.join(work.root_dir, _FILE)


def _default() -> dict:
    return {"version": 1, "entries": []}


def load_entries(work) -> List[dict]:
    """读取全部条目。文件不存在返回空列表。"""
    path = _path(work)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取世界观失败: {e}")
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("entries", [])
    if not isinstance(raw, list):
        return []
    result = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        t = it.get("type", "location")
        if t not in _TYPE_KEYS:
            t = "location"
        attrs = it.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}
        result.append({
            "id": str(it.get("id") or uuid.uuid4().hex[:12]),
            "type": t,
            "name": str(it.get("name", "") or "").strip(),
            "aliases": [str(a).strip() for a in (it.get("aliases") or []) if a],
            "parent": str(it.get("parent", "") or "").strip(),
            "attributes": {str(k): str(v) for k, v in attrs.items()},
            "description": str(it.get("description", "") or ""),
            "chapter_refs": [str(x) for x in (it.get("chapter_refs") or []) if x],
            "tags": [str(x).strip() for x in (it.get("tags") or []) if x],
            "color": str(it.get("color", "") or "") or type_color(t),
            "created_at": str(it.get("created_at", "") or ""),
            "updated_at": str(it.get("updated_at", "") or ""),
        })
    return result


def save_entries(work, entries: List[dict]):
    data = {"version": 1, "entries": list(entries or [])}
    atomic_write_json(_path(work), data)


def add_entry(work, entry_type: str, name: str,
              description: str = "", parent: str = "",
              attributes: Dict[str, str] = None,
              aliases: List[str] = None,
              tags: List[str] = None,
              chapter_refs: List[str] = None) -> dict:
    if entry_type not in _TYPE_KEYS:
        entry_type = "location"
    name = (name or "").strip()
    if not name:
        raise ValueError("条目名称不能为空")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "type": entry_type,
        "name": name,
        "aliases": list(aliases or []),
        "parent": (parent or "").strip(),
        "attributes": dict(attributes or {}),
        "description": (description or "").strip(),
        "chapter_refs": list(chapter_refs or []),
        "tags": list(tags or []),
        "color": type_color(entry_type),
        "created_at": now,
        "updated_at": "",
    }
    entries = load_entries(work)
    entries.insert(0, entry)  # 新建排最前
    save_entries(work, entries)
    return entry


def update_entry(work, entry_id: str, **changes):
    entries = load_entries(work)
    for e in entries:
        if e["id"] != entry_id:
            continue
        for k, v in changes.items():
            if k == "name":
                e["name"] = str(v or "").strip()
            elif k == "type" and v in _TYPE_KEYS:
                # 先记下旧类型默认色，再改 e["type"]。
                # 原实现把 e["type"] 改成新值后才比对颜色，
                # 等于永远和"新类型色"比 → 颜色不跟随。
                old_default = type_color(e["type"])
                e["type"] = v
                # 只有当前颜色恰好是"旧类型的默认色"时才刷新；
                # 用户自定义过的颜色保持不变。
                if e.get("color") == old_default:
                    e["color"] = type_color(v)
            elif k == "aliases":
                e["aliases"] = [str(x).strip() for x in (v or []) if x]
            elif k == "tags":
                e["tags"] = [str(x).strip() for x in (v or []) if x]
            elif k == "chapter_refs":
                e["chapter_refs"] = [str(x) for x in (v or []) if x]
            elif k == "attributes" and isinstance(v, dict):
                e["attributes"] = {str(kk): str(vv) for kk, vv in v.items()}
            elif k == "parent":
                e["parent"] = str(v or "").strip()
            elif k == "description":
                e["description"] = str(v or "")
            elif k == "color":
                e["color"] = str(v or "")
        e["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        break
    save_entries(work, entries)


def delete_entry(work, entry_id: str):
    entries = [e for e in load_entries(work) if e["id"] != entry_id]
    save_entries(work, entries)


def format_entry_for_ai(e: dict) -> str:
    """给 AI 上下文用的紧凑文本。"""
    label = type_label(e.get("type", ""))
    lines = [f"【{label}·{e.get('name', '')}】"]
    if e.get("parent"):
        lines.append(f"  上级：{e['parent']}")
    for k, v in (e.get("attributes") or {}).items():
        lines.append(f"  {k}：{v}")
    if e.get("description"):
        lines.append(f"  {e['description']}")
    return "\n".join(lines)