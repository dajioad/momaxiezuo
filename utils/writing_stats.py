# utils/writing_stats.py
"""墨码写作 - 码字统计

stats.json 结构：
{
  "days": {
    "2026-09-14": {"words": 3342, "minutes": 95}
  }
}
- words  : 当日净增字数（只累加正增量，删除不扣减，符合网文码字统计惯例）
- minutes: 当日活跃写作分钟数（按真实输入心跳所在的「HH:MM」去重）
"""
import os
import json
from datetime import datetime, date, timedelta
from typing import Optional

from .logger import get_logger
from .work_store import get_data_root, atomic_write_json

logger = get_logger()


def _today_key(d: Optional[date] = None) -> str:
    return (d or date.today()).strftime("%Y-%m-%d")


class WritingStats:
    def __init__(self, data_root: Optional[str] = None):
        self.data_root = data_root or get_data_root()
        self.stats_path = os.path.join(self.data_root, "stats.json")
        self._data = {"days": {}}
        self._load()

    def _load(self):
        if not os.path.exists(self.stats_path):
            return
        try:
            with open(self.stats_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("days"), dict):
                self._data = data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"码字统计文件损坏，将重新创建: {e}")

    def save(self):
        try:
            # 过滤内存态键（_active_xxx 是心跳去重用的临时列表，不入盘）。
            # 否则 stats.json 里会出现文档里没写的字段，与文件头注释不符。
            clean = {k: v for k, v in self._data.items()
                     if not k.startswith("_")}
            atomic_write_json(self.stats_path, clean)
        except OSError as e:
            logger.error(f"码字统计保存失败: {e}")

    def _day_bucket(self, day: Optional[str] = None) -> dict:
        key = day or _today_key()
        bucket = self._data["days"].setdefault(key, {"words": 0, "minutes": 0})
        bucket.setdefault("words", 0)
        bucket.setdefault("minutes", 0)
        return bucket

    # ---------- 写入 ----------
    def add_words(self, n: int):
        """累加当日净增字数。

        允许负数：用户改稿时删掉内容，当日计数会相应减少。
        这样「今天 X 字」反映的是真实净变化，而不是敲了多少字。
        """
        if n == 0:
            return
        bucket = self._day_bucket()
        bucket["words"] = int(bucket["words"]) + n
        self.save()

    def add_heartbeat(self):
        """用户真实输入时调用：把当前分钟计入活跃写作时长（同一分钟不重复计）"""
        now = datetime.now()
        key = _today_key(now.date())
        minute_tag = now.strftime("%H:%M")
        active_key = f"_active_{key}"
        active = self._data.setdefault(active_key, [])
        if minute_tag not in active:
            active.append(minute_tag)
            bucket = self._day_bucket(key)
            bucket["minutes"] = int(bucket["minutes"]) + 1
            self.save()
        # 只保留今天的活跃分钟明细。
        # 用实例属性记上次的 active_key，避免每次心跳都遍历 self._data
        # 的所有键（使用几年后 days 字典会有几百上千个键，白扫一遍）。
        prev = getattr(self, "_last_active_key", None)
        if prev is not None and prev != active_key:
            self._data.pop(prev, None)
        self._last_active_key = active_key

    # ---------- 读取 ----------
    def today_words(self) -> int:
        # 保底为 0：净值模式下用户今天大量删除可能使计数为负，
        # 但 UI 显示「今天 -300 字」会让人困惑。显示 0 即可。
        return max(0, int(self._day_bucket().get("words", 0)))

    def today_minutes(self) -> int:
        return int(self._day_bucket().get("minutes", 0))

    def words_of_day(self, day_offset: int = 0) -> int:
        """day_offset=0 今天，1=昨天..."""
        d = date.today() - timedelta(days=day_offset)
        return int(self._data["days"].get(_today_key(d), {}).get("words", 0))

    def last_n_days_words(self, n: int = 7) -> list:
        """返回最近 n 天 [(日期key, 字数)]，按日期升序"""
        return [(_today_key(date.today() - timedelta(days=i)),
                 self.words_of_day(i)) for i in range(n - 1, -1, -1)]

    def streak_days(self) -> int:
        """连续写作天数。今天还没写不算断签（从昨天起向前数）"""
        streak = 0
        offset = 0 if self.today_words() > 0 else 1
        while self.words_of_day(offset) > 0:
            streak += 1
            offset += 1
        return streak

    def total_words(self) -> int:
        return sum(int(d.get("words", 0)) for d in self._data["days"].values())

    def writing_speed(self) -> int:
        """今日时速 = 今日字数 / 活跃小时（不足1小时按比例外推为 字/分钟*60），无数据返回0"""
        minutes = self.today_minutes()
        if minutes <= 0:
            return 0
        return round(self.today_words() / minutes * 60)
