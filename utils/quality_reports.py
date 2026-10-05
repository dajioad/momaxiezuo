# utils/quality_reports.py
"""质量分析报告的作品级持久化。

墨灵试读、追读体检等分析结果原本只活在对话框内存里，关掉就没了。
本模块把每份报告以单独 JSON 存到作品目录下的 ``quality_reports/``：

    <作品根>/quality_reports/qr_YYYYMMDD_HHMMSS_xxxx.json

一条记录的结构：

    {
      "id": "qr_20260925_141500_a1b2",
      "kind": "beta_reading" | "readability",
      "created_at": "2026-09-25 14:15:00",
      "ts": 1789251300.0,
      "title": "列表中显示的标题",
      "chapter_id": "可选，章节类报告",
      "chapter_title": "可选",
      "score": 0-100 或 null,
      "level": "优/良/中/差" 或 null,
      "data": { ... kind 相关的完整载荷 ... }
    }

设计要点：
- 一报告一文件：单份损坏不牵连其他，写入走 atomic_write_json（断电安全）；
- 列表只读各文件的头部元信息（load 整个 JSON，但容错跳过坏文件）；
- 自动裁剪：试读报告保留最近 BETA_KEEP 份，体检报告每章保留最近
  READABILITY_PER_CHAPTER 份，总数超过 TOTAL_CAP 再兜底删最旧的；
- 报告目录位于作品根内，随「备份所有作品」与自动备份一起打包，无需改备份清单。
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

from utils.work_store import atomic_write_json
from utils.readability import Report, CheckItem

KIND_BETA = "beta_reading"
KIND_READABILITY = "readability"

BETA_KEEP = 20
READABILITY_PER_CHAPTER = 5
TOTAL_CAP = 200

_DIRNAME = "quality_reports"


# ---------- 路径 ----------

def reports_dir(work_root: str) -> str:
    return os.path.join(work_root or "", _DIRNAME)


def _report_path(work_root: str, report_id: str) -> Optional[str]:
    """id 只允许 qr_ 前缀的安全文件名，杜绝路径穿越。"""
    if not report_id or not report_id.startswith("qr_"):
        return None
    if os.sep in report_id or "/" in report_id or ".." in report_id:
        return None
    if not report_id.endswith(".json"):
        report_id += ".json"
    return os.path.join(reports_dir(work_root), report_id)


# ---------- id / 时间 ----------

def new_record_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    suffix = "".join(random.choice("0123456789abcdef") for _ in range(4))
    return f"qr_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


def make_record(kind: str, title: str, data: dict,
                chapter_id: str = None, chapter_title: str = None,
                score=None, level: str = None,
                now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    return {
        "id": new_record_id(now),
        "kind": kind,
        "created_at": now.strftime("%Y-%m-%d %H:%M"),
        "ts": time.mktime(now.timetuple()),
        "title": title or "",
        "chapter_id": chapter_id or None,
        "chapter_title": chapter_title or None,
        "score": score,
        "level": level or None,
        "data": data or {},
    }


# ---------- 读写 ----------

def save_report(work_root: str, record: dict) -> dict:
    """原子写入一条报告，返回原记录。"""
    path = _report_path(work_root, record["id"])
    if path is None:
        raise ValueError(f"非法报告 id: {record.get('id')!r}")
    atomic_write_json(path, record)
    return record


def load_report(work_root: str, report_id: str) -> Optional[dict]:
    path = _report_path(work_root, report_id)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("id") else None


def list_reports(work_root: str) -> List[dict]:
    """按时间倒序返回全部报告（完整记录；坏文件跳过）。"""
    out: List[dict] = []
    d = reports_dir(work_root)
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if not (fn.startswith("qr_") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    # (ts, id) 双键：同一秒内连续保存时用 id 的时间戳+随机后缀兜底，
    # 保证列表顺序稳定（同一次 listdir 不依赖文件系统返回顺序）。
    out.sort(key=lambda r: (r.get("ts", 0), r.get("id", "")), reverse=True)
    return out


def delete_report(work_root: str, report_id: str) -> bool:
    path = _report_path(work_root, report_id)
    if not path or not os.path.isfile(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


# ---------- 裁剪 ----------

def prune_reports(work_root: str,
                  beta_keep: int = BETA_KEEP,
                  readability_per_chapter: int = READABILITY_PER_CHAPTER,
                  total_cap: int = TOTAL_CAP) -> List[str]:
    """删除超量旧报告，返回被删 id 列表。保留判定以时间倒序列表为准。"""
    beta_keep = max(1, beta_keep)
    readability_per_chapter = max(1, readability_per_chapter)
    total_cap = max(1, total_cap)

    records = list_reports(work_root)   # 新 → 旧
    keep_ids = set()
    beta_seen = 0
    per_chapter_seen = {}

    for rec in records:
        kind = rec.get("kind")
        if kind == KIND_BETA:
            beta_seen += 1
            if beta_seen <= beta_keep:
                keep_ids.add(rec["id"])
        elif kind == KIND_READABILITY:
            cid = rec.get("chapter_id") or ""
            seen = per_chapter_seen.get(cid, 0) + 1
            per_chapter_seen[cid] = seen
            if seen <= readability_per_chapter:
                keep_ids.add(rec["id"])
        else:
            keep_ids.add(rec["id"])   # 未知类型不动

    # 总量兜底：只从已保留集合里按时间再砍
    if len(keep_ids) > total_cap:
        kept_in_order = [r["id"] for r in records if r["id"] in keep_ids]
        keep_ids = set(kept_in_order[:total_cap])

    removed = []
    for rec in records:
        if rec["id"] not in keep_ids and delete_report(work_root, rec["id"]):
            removed.append(rec["id"])
    return removed


# ---------- 追读体检 Report 序列化 ----------

def report_to_dict(report: Report) -> dict:
    return asdict(report)


def report_from_dict(d: dict) -> Optional[Report]:
    if not isinstance(d, dict):
        return None
    items = []
    for it in d.get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            items.append(CheckItem(
                key=it.get("key", ""),
                label=it.get("label", ""),
                score=int(it.get("score", 0)),
                max_score=int(it.get("max_score", 0)),
                level=it.get("level", ""),
                message=it.get("message", ""),
                suggestions=list(it.get("suggestions") or []),
                positions=[tuple(p) for p in (it.get("positions") or [])
                           if isinstance(p, (list, tuple)) and len(p) == 2],
            ))
        except (TypeError, ValueError):
            continue
    curve = [tuple(c) for c in (d.get("curve") or [])
             if isinstance(c, (list, tuple)) and len(c) == 3]
    return Report(
        total=int(d.get("total", 0)),
        level=d.get("level", "差"),
        items=items,
        stats=dict(d.get("stats") or {}),
        curve=curve,
    )
