# utils/cn_number.py
"""墨码写作 - 中文数字与阿拉伯数字互转（纯函数，无依赖）

三处模块曾各自复制同一套逻辑（chapter_order / context_builder / unify），
统一到这里，避免将来改一处忘另一处、行为漂移。

用法：
    cn_to_int("三十五")   → 35
    cn_to_int("一万零三") → 10003
    cn_to_int("ABC")      → None
    int_to_cn(35)         → "三十五"
    int_to_cn(-5)         → "负五"
    year_to_cn(2026)      → "二零二六"
"""
from __future__ import annotations
from typing import Optional

# 数字字符表。对外暴露，方便其它模块构造正则或做预校验。
CN_DIGITS = "零一二三四五六七八九"

_CN_DIGIT_MAP = {c: i for i, c in enumerate(CN_DIGITS)}
_CN_UNIT_MAP = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def cn_to_int(s: str) -> Optional[int]:
    """中文数字 → 整数。失败返回 None。

    支持：
      三十五    → 35
      一百零八  → 108
      一万零三  → 10003
      十        → 10    （"十"本身等于 10）
      负五      → -5    （前缀"负"）
    """
    if not s:
        return None
    s = s.strip()
    if not s:
        return None

    negative = False
    if s.startswith("负"):
        negative = True
        s = s[1:]
        if not s:
            return None

    if "万" in s:
        idx = s.index("万")
        high = cn_to_int(s[:idx]) if s[:idx] else 1
        low = cn_to_int(s[idx + 1:]) if s[idx + 1:] else 0
        if high is None or low is None:
            return None
        result = high * 10000 + low
        return -result if negative else result

    total = 0
    current = 0
    has_digit = False
    for ch in s:
        if ch in _CN_DIGIT_MAP:
            current = _CN_DIGIT_MAP[ch]
            has_digit = True
        elif ch in _CN_UNIT_MAP:
            unit = _CN_UNIT_MAP[ch]
            if not has_digit:
                current = 1
            total += current * unit
            current = 0
            has_digit = False
        else:
            return None
    total += current
    return -total if negative else total


def int_to_cn(n: int) -> str:
    """整数 → 中文数字。支持负数（前缀"负"）。

    覆盖 0 到 9999 万；超过 1 亿返回 str(n) 兜底。
    网文章节号、年龄、人数都在此范围内。
    """
    if n < 0:
        return "负" + int_to_cn(-n)
    if n < 10:
        return CN_DIGITS[n]
    if n < 20:
        return "十" + (CN_DIGITS[n - 10] if n > 10 else "")
    if n < 100:
        tens, ones = divmod(n, 10)
        return CN_DIGITS[tens] + "十" + (CN_DIGITS[ones] if ones else "")
    if n < 1000:
        h, rest = divmod(n, 100)
        s = CN_DIGITS[h] + "百"
        if rest == 0:
            return s
        if rest < 10:
            return s + "零" + CN_DIGITS[rest]
        return s + int_to_cn(rest)
    if n < 10000:
        t, rest = divmod(n, 1000)
        s = CN_DIGITS[t] + "千"
        if rest == 0:
            return s
        if rest < 100:
            return s + "零" + int_to_cn(rest)
        return s + int_to_cn(rest)
    if n < 100000000:
        w, rest = divmod(n, 10000)
        s = int_to_cn(w) + "万"
        if rest == 0:
            return s
        if rest < 1000:
            return s + "零" + int_to_cn(rest)
        return s + int_to_cn(rest)
    return str(n)


def year_to_cn(n: int) -> str:
    """年份 → 逐位中文。2026 → "二零二六"。"""
    return "".join(CN_DIGITS[int(d)] for d in str(n))