# utils/sprint.py
"""墨码写作 - 冲刺计划（纯逻辑，无 Qt、无网络）

场景：新书期作者常有明确目标——"X 天内写到 Y 字，用于上架/签约"。
本模块负责计算：进度、剩余天数、每日必达字数、领先/落后。

数据结构（存到 book.json 的 sprint 字段）：
    {
      "target_words": 100000,      # 目标总字数
      "start_date": "2026-09-18",  # 冲刺开始
      "end_date": "2026-10-30",    # 截止
      "start_words": 0,            # 开始时已有字数
      "snapshots": {               # 每日快照 {"YYYY-MM-DD": 总字数}
        "2026-09-18": 3200,
        "2026-09-19": 6800,
      }
    }
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict


@dataclass
class SprintConfig:
    target_words: int = 0
    start_date: str = ""
    end_date: str = ""
    start_words: int = 0
    snapshots: Dict[str, int] = None

    def __post_init__(self):
        if self.snapshots is None:
            self.snapshots = {}


def parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def load_sprint(work) -> SprintConfig:
    s = work.meta.get("sprint") or {}
    snaps = s.get("snapshots") or {}
    if not isinstance(snaps, dict):
        snaps = {}
    return SprintConfig(
        target_words=int(s.get("target_words", 0) or 0),
        start_date=str(s.get("start_date", "") or ""),
        end_date=str(s.get("end_date", "") or ""),
        start_words=int(s.get("start_words", 0) or 0),
        snapshots={str(k): int(v) for k, v in snaps.items()},
    )


def save_sprint(work, cfg: SprintConfig):
    work.meta["sprint"] = {
        "target_words": int(cfg.target_words),
        "start_date": cfg.start_date,
        "end_date": cfg.end_date,
        "start_words": int(cfg.start_words),
        "snapshots": dict(cfg.snapshots or {}),
    }
    work.save_meta()


def clear_sprint(work):
    work.meta.pop("sprint", None)
    work.save_meta()


def update_today_snapshot(work, cfg: SprintConfig):
    """每次打开冲刺对话框时调用，记录今天的总字数。

    只记当天（覆盖写入），不追溯。
    """
    if cfg.target_words <= 0 or not cfg.end_date:
        return
    today_key = date.today().strftime("%Y-%m-%d")
    cfg.snapshots[today_key] = int(work.total_words())
    save_sprint(work, cfg)


def compute(work, cfg: SprintConfig) -> dict:
    """计算冲刺进度。返回 dict，字段见下。"""
    current = int(work.total_words())
    if cfg.target_words <= 0 or not cfg.end_date:
        return {
            "active": False,
            "current": current,
        }

    today = date.today()
    start = parse_date(cfg.start_date) if cfg.start_date else today
    end = parse_date(cfg.end_date)

    total_days = max(1, (end - start).days)
    days_passed = max(0, (today - start).days)
    days_left = max(0, (end - today).days)

    remain_words = max(0, cfg.target_words - current)
    if days_left > 0:
        daily_required = remain_words // days_left
        if remain_words % days_left:
            daily_required += 1
    else:
        daily_required = remain_words

    if total_days > 0:
        ideal_words = int(cfg.target_words * min(1.0, days_passed / total_days))
    else:
        ideal_words = cfg.target_words

    diff = current - ideal_words

    percent = 0
    if cfg.target_words > 0:
        percent = min(100, int(current / cfg.target_words * 100))

    status = "on_track"
    threshold = max(1, cfg.target_words * 0.05)
    if diff < -threshold:
        status = "behind"
    elif diff > threshold:
        status = "ahead"

    # 估算达标日：优先用"实际日均速度"外推；今天才开始（days_passed=0）
    # 或还没产出（actual_written=0）时，用 daily_required 兜底。
    actual_written = max(0, current - cfg.start_words)
    if days_passed > 0 and actual_written > 0:
        actual_daily = max(1, actual_written // days_passed)
    else:
        actual_daily = daily_required
    estimated_finish = estimate_finish_date(remain_words, actual_daily, today)

    return {
        "active": True,
        "current": current,
        "target": cfg.target_words,
        "remain_words": remain_words,
        "percent": percent,
        "start": start,
        "end": end,
        "today": today,
        "total_days": total_days,
        "days_passed": days_passed,
        "days_left": days_left,
        "daily_required": daily_required,
        "actual_daily": actual_daily,
        "ideal_words": ideal_words,
        "diff": diff,
        "status": status,
        "estimated_finish": estimated_finish,
    }


def estimate_finish_date(remain_words: int, daily_words: int,
                         from_date: date = None) -> str:
    """按给定日均产量估算达标日期。

    参数：
      remain_words  还差多少字（<=0 表示已完成，返回 ""）
      daily_words   预计日均产量（<=0 无法估算，返回 ""）
      from_date     起始日期，默认今天

    返回 "YYYY-MM-DD"；无法估算时返回空串。
    """
    if remain_words <= 0:
        return ""
    if daily_words <= 0:
        return ""
    start = from_date or date.today()
    days = (int(remain_words) + int(daily_words) - 1) // int(daily_words)
    return (start + timedelta(days=days)).strftime("%Y-%m-%d")