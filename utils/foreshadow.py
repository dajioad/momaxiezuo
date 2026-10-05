# utils/foreshadow.py
"""墨码写作 - 伏笔追踪（纯逻辑，无 Qt、无网络）

场景：几十万字后，作者自己都忘了哪些伏笔没回收。
本模块提供：录入、修改、标记回收、AI 扫描本章提示新伏笔。

存储：works/<作品>/foreshadow.json
结构：
    {
      "items": [
        {
          "id": "abc123",
          "content": "老乞丐送的玉佩",
          "planted_chapter": "0001",
          "planned_payoff": "第50章左右",
          "actual_payoff": "",
          "notes": "",
          "created_at": "2026-09-18 10:00:00",
        },
      ]
    }

"是否已回收" 由 actual_payoff 是否非空判断。
"""
from __future__ import annotations

import os
import re
import uuid
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List

from .logger import get_logger
from .work_store import atomic_write_json

logger = get_logger()

_FILE = "foreshadow.json"


@dataclass
class ForeshadowItem:
    id: str
    content: str
    planted_chapter: str = ""
    planned_payoff: str = ""
    actual_payoff: str = ""
    notes: str = ""
    created_at: str = ""

    @property
    def is_closed(self) -> bool:
        return bool(self.actual_payoff.strip())


# ---------- 存储 ----------

def _path(work) -> str:
    return os.path.join(work.root_dir, _FILE)


def load_all(work) -> List[ForeshadowItem]:
    p = _path(work)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取伏笔失败: {e}")
        return []
    raw = data.get("items", []) if isinstance(data, dict) else []
    result = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        result.append(ForeshadowItem(
            id=str(r.get("id", "") or uuid.uuid4().hex[:12]),
            content=str(r.get("content", "") or ""),
            planted_chapter=str(r.get("planted_chapter", "") or ""),
            planned_payoff=str(r.get("planned_payoff", "") or ""),
            actual_payoff=str(r.get("actual_payoff", "") or ""),
            notes=str(r.get("notes", "") or ""),
            created_at=str(r.get("created_at", "") or ""),
        ))
    result.sort(key=lambda x: (x.is_closed, x.created_at), reverse=False)
    return result


def save_all(work, items: List[ForeshadowItem]):
    data = {"items": [asdict(it) for it in items]}
    atomic_write_json(_path(work), data)


def add_item(work, content: str, planted_chapter: str = "",
             planned_payoff: str = "", actual_payoff: str = "",
             notes: str = "") -> ForeshadowItem:
    items = load_all(work)
    item = ForeshadowItem(
        id=uuid.uuid4().hex[:12],
        content=(content or "").strip(),
        planted_chapter=(planted_chapter or "").strip(),
        planned_payoff=(planned_payoff or "").strip(),
        actual_payoff=(actual_payoff or "").strip(),
        notes=(notes or "").strip(),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    items.append(item)
    save_all(work, items)
    return item


def update_item(work, item_id: str, **changes):
    items = load_all(work)
    for it in items:
        if it.id == item_id:
            for k, v in changes.items():
                if hasattr(it, k):
                    setattr(it, k, (v or "").strip() if isinstance(v, str) else v)
            break
    save_all(work, items)


def delete_item(work, item_id: str):
    items = [it for it in load_all(work) if it.id != item_id]
    save_all(work, items)


def close_item(work, item_id: str, payoff_chapter: str):
    update_item(work, item_id, actual_payoff=payoff_chapter)


def reopen_item(work, item_id: str):
    update_item(work, item_id, actual_payoff="")


# ---------- AI 扫描（构造 prompt + 解析） ----------

_SCAN_PROMPT = """你是一位网文编辑。请从下面这一章正文中，找出作者可能"埋了但没揭"的**伏笔线索**。

伏笔线索的特征：
- 有意提到的细节（一个物件、一句话、一个可疑人物、一个异常现象）
- 当下没解释，但暗示"后面会用到"
- 例如：老乞丐送的东西、一句意味深长的台词、主角做的奇怪的梦

不要提取：
- 已经解释过的东西
- 普通的场景描写
- 主角的日常行为

输出要求：
- 只输出 JSON，不要任何解释，不要 markdown 代码块标记
- 格式严格为：
{{
  "hints": [
    {{"content": "简短描述（20 字内）", "quote": "原文中相关的一句话（50 字内）"}}
  ]
}}
- 最多 5 条，只列最有价值的

【章节标题】
{title}

【章节正文】
{text}"""


def build_scan_prompt(text: str, title: str = "") -> str:
    return _SCAN_PROMPT.format(
        title=title or "（无标题）",
        text=(text or "")[:6000],
    )


def parse_scan_response(text: str) -> List[dict]:
    if not text:
        return []
    # 统一走 ai_json：平衡扫描比贪婪正则 r"\{[\s\S]*\}" 稳
    # （后者遇到嵌套对象或字符串内的 "}" 会吃错）。
    from .ai_json import parse_json_response
    data = parse_json_response(text)
    if not isinstance(data, dict):
        return []
    hints = data.get("hints") or []
    if not isinstance(hints, list):
        return []
    result = []
    for h in hints:
        if isinstance(h, str):
            result.append({"content": h.strip(), "quote": ""})
        elif isinstance(h, dict):
            c = str(h.get("content", "") or "").strip()
            q = str(h.get("quote", "") or "").strip()
            if c:
                result.append({"content": c, "quote": q})
    return result[:5]