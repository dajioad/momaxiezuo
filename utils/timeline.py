# utils/timeline.py
"""墨码写作 - 时间线 / 事件线

存储：works/<作品>/timeline.json

结构：
    {
      "version": 1,
      "eras": [
        {"id": "era_xxx", "label": "开元纪 · 一千年前", "sort_key": 1000}
      ],
      "events": [
        {
          "id": "evt_xxx",
          "era_id": "era_xxx",             # 可选，为空则不分段
          "title": "剑冢之变",
          "story_time": "开元元年",          # 给用户看的（"开元三年春"）
          "story_time_sort": 1001,          # 给机器排序的（数字，可空）
          "chapter_id": "0003",             # 关联章节 id（可空）
          "duration": "当日",                # 持续（可空）
          "description": "",
          "characters": ["林墨", "李长生"],   # 关联人物卡名字
          "importance": "major",            # major / minor
          "tags": [],
          "created_at": "",
          "updated_at": ""
        }
      ]
    }

设计取舍：
- era 让用户把事件按大时代分段展示，UI 效果更好；也可以不建 era
  （此时全部事件按 story_time_sort 排在一段里）
- story_time 是"故事内时间"（"开元三年春"），跟"章节顺序"解耦——支持
  倒叙/插叙/闪回
- story_time_sort 用于排序；用户不想填时按创建顺序排
- chapter_id 关联真实章节，双击事件可跳章节
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import List, Optional

from .logger import get_logger
from .work_store import atomic_write_json

logger = get_logger()

_FILE = "timeline.json"

IMPORTANCE_MAJOR = "major"
IMPORTANCE_MINOR = "minor"


def _path(work) -> str:
    return os.path.join(work.root_dir, _FILE)


def _default() -> dict:
    return {"version": 1, "eras": [], "events": []}


def load_all(work) -> dict:
    """读取 {eras, events}。文件不存在返回空结构。"""
    path = _path(work)
    if not os.path.exists(path):
        return _default()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取时间线失败: {e}")
        return _default()
    if not isinstance(data, dict):
        return _default()

    eras = []
    for e in (data.get("eras") or []):
        if not isinstance(e, dict):
            continue
        eras.append({
            "id": str(e.get("id") or uuid.uuid4().hex[:12]),
            "label": str(e.get("label", "") or "").strip(),
            "sort_key": int(e.get("sort_key", 0) or 0),
        })

    events = []
    for ev in (data.get("events") or []):
        if not isinstance(ev, dict):
            continue
        events.append({
            "id": str(ev.get("id") or uuid.uuid4().hex[:12]),
            "era_id": str(ev.get("era_id", "") or ""),
            "title": str(ev.get("title", "") or "").strip(),
            "story_time": str(ev.get("story_time", "") or "").strip(),
            "story_time_sort": int(ev.get("story_time_sort", 0) or 0),
            "chapter_id": str(ev.get("chapter_id", "") or ""),
            "duration": str(ev.get("duration", "") or "").strip(),
            "description": str(ev.get("description", "") or ""),
            "characters": [str(x).strip() for x in (ev.get("characters") or []) if x],
            "importance": str(ev.get("importance", "major") or "major"),
            "tags": [str(x).strip() for x in (ev.get("tags") or []) if x],
            "created_at": str(ev.get("created_at", "") or ""),
            "updated_at": str(ev.get("updated_at", "") or ""),
        })

    return {"version": 1, "eras": eras, "events": events}


def save_all(work, data: dict):
    atomic_write_json(_path(work), {
        "version": 1,
        "eras": data.get("eras", []),
        "events": data.get("events", []),
    })


def add_era(work, label: str, sort_key: int = 0) -> dict:
    data = load_all(work)
    era = {
        "id": uuid.uuid4().hex[:12],
        "label": (label or "").strip() or "未命名时代",
        "sort_key": int(sort_key or 0),
    }
    data["eras"].append(era)
    save_all(work, data)
    return era


def delete_era(work, era_id: str):
    """删除一个时代，其下事件归到"无时代"（era_id 清空）。"""
    data = load_all(work)
    data["eras"] = [e for e in data["eras"] if e["id"] != era_id]
    for ev in data["events"]:
        if ev.get("era_id") == era_id:
            ev["era_id"] = ""
    save_all(work, data)


def add_event(work, title: str, story_time: str = "",
              story_time_sort: int = 0, era_id: str = "",
              chapter_id: str = "", description: str = "",
              characters: List[str] = None,
              importance: str = IMPORTANCE_MAJOR,
              tags: List[str] = None,
              duration: str = "") -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("事件标题不能为空")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ev = {
        "id": uuid.uuid4().hex[:12],
        "era_id": era_id or "",
        "title": title,
        "story_time": (story_time or "").strip(),
        "story_time_sort": int(story_time_sort or 0),
        "chapter_id": chapter_id or "",
        "duration": (duration or "").strip(),
        "description": (description or "").strip(),
        "characters": list(characters or []),
        "importance": importance if importance in ("major", "minor") else "major",
        "tags": list(tags or []),
        "created_at": now,
        "updated_at": "",
    }
    data = load_all(work)
    data["events"].append(ev)
    save_all(work, data)
    return ev


def update_event(work, event_id: str, **changes):
    data = load_all(work)
    for ev in data["events"]:
        if ev["id"] != event_id:
            continue
        for k, v in changes.items():
            if k in ("title", "story_time", "duration", "description"):
                ev[k] = str(v or "").strip()
            elif k in ("chapter_id", "era_id"):
                ev[k] = str(v or "")
            elif k == "story_time_sort":
                try:
                    ev[k] = int(v or 0)
                except (TypeError, ValueError):
                    ev[k] = 0
            elif k == "importance":
                ev[k] = v if v in ("major", "minor") else "major"
            elif k == "characters":
                ev[k] = [str(x).strip() for x in (v or []) if x]
            elif k == "tags":
                ev[k] = [str(x).strip() for x in (v or []) if x]
        ev["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        break
    save_all(work, data)


def delete_event(work, event_id: str):
    data = load_all(work)
    data["events"] = [e for e in data["events"] if e["id"] != event_id]
    save_all(work, data)


def sorted_events(work) -> List[dict]:
    """返回排好序的事件列表（先按 era.sort_key，再按 story_time_sort，
    最后按 created_at）。供 UI 直接遍历。"""
    data = load_all(work)
    era_sort = {e["id"]: e["sort_key"] for e in data["eras"]}
    # 空 era 归到 0 段
    def key(ev):
        return (
            era_sort.get(ev.get("era_id", ""), 0),
            ev.get("story_time_sort", 0),
            ev.get("created_at", ""),
        )
    return sorted(data["events"], key=key)


def format_timeline_for_ai(work) -> str:
    """AI 上下文用的紧凑文本。"""
    data = load_all(work)
    if not data["events"]:
        return ""
    lines = []
    current_era = None
    era_map = {e["id"]: e["label"] for e in data["eras"]}
    for ev in sorted_events(work):
        era = era_map.get(ev.get("era_id", ""), "")
        if era and era != current_era:
            lines.append(f"【{era}】")
            current_era = era
        time = ev.get("story_time", "")
        title = ev.get("title", "")
        chars = "、".join(ev.get("characters") or []) or ""
        bits = []
        if time:
            bits.append(time)
        bits.append(title)
        if chars:
            bits.append(f"（{chars}）")
        lines.append("  · " + "  ".join(bits))
    return "\n".join(lines)