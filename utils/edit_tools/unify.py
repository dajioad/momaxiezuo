# utils/edit_tools/unify.py
"""墨码写作 - 格式统一核心逻辑（纯函数，无 Qt 依赖）

四组能力：
A. 标点转换    英↔中、省略号统一、破折号统一
B. 引号统一    "" / '' / 「」 / 『』
C. 数字统一    阿拉伯↔中文（支持仅年份）
D. 全角半角    字母、数字、括号、空格

边界保护（与规格严格对齐）：
- URL、邮箱、数字词法单元不进任何规则
- 小数点、千分位、时间不碰
- 英文缩写撇号（don't / Jim's）不碰，只认 ASCII 字母夹着的撇号
- 段首全角缩进不碰
- 引号逐行配对，行首引号强制视为开引号（网文跨段对话场景）
"""
from __future__ import annotations

import re
from ..cn_number import int_to_cn as _int_to_cn, year_to_cn as _year_to_cn
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class UnifyParams:
    # A. 标点
    punct_direction: str = "keep"        # keep / to_cn / to_en
    ellipsis_unify: bool = False
    dash_unify: bool = False

    # B. 引号：keep / cn（“”‘’）/ corner（「」『』）
    quote_style: str = "keep"

    # C. 数字：keep / to_cn / to_en；scope：all / year
    number_direction: str = "keep"
    number_scope: str = "all"

    # D. 全角半角
    fullwidth_letters: bool = False
    fullwidth_digits: bool = False
    bracket_style: str = "keep"         # keep / cn / en
    space_unify: bool = False


# ---------- 保护范围 ----------

_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s一-鿿，。！？；：、“”‘’（）【】]+", re.I)
_MAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NUM_TOKEN_RE = re.compile(r"\d[\d,\./:%-]*\d|[A-Za-z]\d*\.\d")


def _protected_spans(text: str) -> List[Tuple[int, int]]:
    spans = []
    for rx in (_URL_RE, _MAIL_RE, _NUM_TOKEN_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _in_spans(pos: int, spans: List[Tuple[int, int]]) -> bool:
    for s, e in spans:
        if s <= pos < e:
            return True
    return False


# ---------- A. 标点 ----------

_EN_TO_CN = {",": "，", ".": "。", "!": "！", "?": "？", ";": "；", ":": "："}
_CN_TO_EN = {v: k for k, v in _EN_TO_CN.items()}


def _convert_punct(text: str, direction: str,
                   spans: List[Tuple[int, int]]) -> Tuple[str, int]:
    if direction == "to_cn":
        mapping = _EN_TO_CN
    elif direction == "to_en":
        mapping = _CN_TO_EN
    else:
        return text, 0

    out: List[str] = []
    count = 0
    n = len(text)
    for i, ch in enumerate(text):
        if ch not in mapping or _in_spans(i, spans):
            out.append(ch)
            continue
        # . 和 , 夹在 ASCII 字母数字之间时跳过
        if ch in ".,":
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < n else ""
            if (prev.isascii() and (prev.isalnum() or prev in "_-")
                    and nxt.isascii() and (nxt.isalnum() or nxt in "_-/")):
                out.append(ch)
                continue
        out.append(mapping[ch])
        count += 1
    return "".join(out), count


_ELLIPSIS_PATTERN = re.compile(r"\.{3,}|。{3,}|．{3,}|…+")


def _unify_ellipsis(text: str,
                    spans: List[Tuple[int, int]]) -> Tuple[str, int]:
    count = [0]

    def _rep(m):
        if _in_spans(m.start(), spans):
            return m.group()
        count[0] += 1
        return "……"

    return _ELLIPSIS_PATTERN.sub(_rep, text), count[0]


_DASH_PATTERN = re.compile(r"-{2,}|—+|－+")


def _unify_dash(text: str,
                spans: List[Tuple[int, int]]) -> Tuple[str, int]:
    count = [0]

    def _rep(m):
        if _in_spans(m.start(), spans):
            return m.group()
        count[0] += 1
        return "——"

    return _DASH_PATTERN.sub(_rep, text), count[0]


# ---------- B. 引号 ----------

_DOUBLE_QUOTE_CHARS = '"“”「」『』'
_SINGLE_QUOTE_CHARS = "'‘’"


def _unify_quotes(text: str, style: str) -> Tuple[str, int]:
    """统一引号风格。

    关键设计：
    - 全局配对状态（d_open/s_open 跨行不重置），正确处理网文的
      "跨段对话"——第一段只有开引号，最后一段末尾才出现闭引号。
    - 行首引号强制视为开引号。判断标准是"引号前面只有空白"，
      而不是绝对的行首（行首通常有 　　 全角缩进）。
    - 非行首引号按全局配对状态翻：等开→开，等闭→闭。
    - 英文缩写撇号（don't / Jim's）保护：只认 ASCII 字母夹着的撇号。
    """
    if style == "keep":
        return text, 0
    if style == "cn":
        open_d, close_d = "“", "”"
        open_s, close_s = "‘", "’"
    elif style == "corner":
        open_d, close_d = "「", "」"
        open_s, close_s = "『", "』"
    else:
        return text, 0

    double_set = set(_DOUBLE_QUOTE_CHARS)
    single_set = set(_SINGLE_QUOTE_CHARS)
    out_lines: List[str] = []
    count = 0
    # 全局状态：跨行保留，不重置
    # True = 下一个应该开；False = 下一个应该闭
    d_open = True
    s_open = True
    # 视为"行首空白"的字符集
    indent_chars = " \t　"

    for line in text.split("\n"):
        chars = list(line)
        n = len(chars)
        # 替代 O(n²) 的 `all(c in indent_chars for c in chars[:i])`：
        # 用"是否已见过非缩进内容"做行首判断，逐字符推进。
        # 与 format_engine._rule_quote 保持同一套语义。
        seen_content = False
        for i, ch in enumerate(chars):
            is_line_start = not seen_content

            if ch in double_set:
                if is_line_start:
                    chars[i] = open_d
                    d_open = False
                else:
                    chars[i] = open_d if d_open else close_d
                    d_open = not d_open
                count += 1
                seen_content = True
            elif ch in single_set:
                prev = chars[i - 1] if i > 0 else ""
                nxt = chars[i + 1] if i + 1 < n else ""
                if (prev.isascii() and prev.isalpha()
                        and nxt.isascii() and nxt.isalpha()):
                    # 英文缩写撇号：不动它，也不把它当"内容"。
                    # 它前面必是字母，seen_content 早已为 True。
                    continue
                if is_line_start:
                    chars[i] = open_s
                    s_open = False
                else:
                    chars[i] = open_s if s_open else close_s
                    s_open = not s_open
                count += 1
                seen_content = True
            else:
                if ch not in indent_chars:
                    seen_content = True
        out_lines.append("".join(chars))
    return "\n".join(out_lines), count


# ---------- C. 数字 ----------

def _arabic_to_cn(text: str, scope: str) -> Tuple[str, int]:
    replacements: List[Tuple[int, int, str]] = []
    for m in re.finditer(r"\d+", text):
        start, end = m.start(), m.end()
        prev = text[start - 1] if start > 0 else ""
        nxt = text[end] if end < len(text) else ""
        if prev in ".,:/-%" or nxt in ".,:/-%":
            continue
        raw = m.group()
        n = int(raw)
        if scope == "year":
            if len(raw) == 4 and 1000 <= n <= 2999:
                cn = _year_to_cn(n)
            else:
                continue
        else:
            cn = _int_to_cn(n)
        replacements.append((start, end, cn))
    if not replacements:
        return text, 0
    out = text
    for start, end, cn in reversed(replacements):
        out = out[:start] + cn + out[end:]
    return out, len(replacements)


_CN_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九")}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_NUM_RX = re.compile(r"[零一二三四五六七八九十百千万]{2,}")


def _cn_to_int(s: str):
    if not s:
        return None
    if "万" in s:
        idx = s.index("万")
        high = _cn_to_int(s[:idx]) if s[:idx] else 1
        low = _cn_to_int(s[idx + 1:]) if s[idx + 1:] else 0
        if high is None or low is None:
            return None
        return high * 10000 + low
    total = 0
    current = 0
    has_digit = False
    for ch in s:
        if ch in _CN_DIGIT:
            current = _CN_DIGIT[ch]
            has_digit = True
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if not has_digit:
                current = 1
            total += current * unit
            current = 0
            has_digit = False
        else:
            return None
    total += current
    return total


def _cn_to_arabic(text: str) -> Tuple[str, int]:
    count = [0]

    def _rep(m):
        n = _cn_to_int(m.group())
        if n is None:
            return m.group()
        count[0] += 1
        return str(n)

    return _CN_NUM_RX.sub(_rep, text), count[0]


# ---------- D. 全角半角 ----------

def _fullwidth_letters(text: str) -> Tuple[str, int]:
    out: List[str] = []
    count = 0
    for ch in text:
        code = ord(ch)
        if 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFEE0))
            count += 1
        else:
            out.append(ch)
    return "".join(out), count


def _fullwidth_digits(text: str) -> Tuple[str, int]:
    out: List[str] = []
    count = 0
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:
            out.append(chr(code - 0xFEE0))
            count += 1
        else:
            out.append(ch)
    return "".join(out), count


def _unify_brackets(text: str, style: str) -> Tuple[str, int]:
    if style == "cn":
        mapping = {"(": "（", ")": "）"}
    elif style == "en":
        mapping = {"（": "(", "）": ")"}
    else:
        return text, 0
    out: List[str] = []
    count = 0
    for ch in text:
        if ch in mapping:
            out.append(mapping[ch])
            count += 1
        else:
            out.append(ch)
    return "".join(out), count


def _unify_spaces(text: str) -> Tuple[str, int]:
    """全角空格 → 半角，行首连续的全角空格（缩进）保留。"""
    out_lines: List[str] = []
    count = 0
    for line in text.split("\n"):
        indent = ""
        rest = line
        while rest.startswith("　"):
            indent += "　"
            rest = rest[1:]
        buf: List[str] = []
        for ch in rest:
            if ch == "　":
                buf.append(" ")
                count += 1
            else:
                buf.append(ch)
        out_lines.append(indent + "".join(buf))
    return "\n".join(out_lines), count


# ---------- 对外接口 ----------

def unify_text(text: str, params: UnifyParams) -> Tuple[str, Dict[str, int]]:
    """按 params 统一格式，返回 (新文本, 统计字典)。

    统计键（只在触发时出现）：
      punct / ellipsis / dash / quotes / numbers
      fullwidth_letters / fullwidth_digits / brackets / spaces
    """
    if text is None:
        return "", {}
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    stats: Dict[str, int] = {}
    spans = _protected_spans(text)

    if params.punct_direction != "keep":
        text, n = _convert_punct(text, params.punct_direction, spans)
        if n:
            stats["punct"] = n
        spans = _protected_spans(text)

    if params.ellipsis_unify:
        text, n = _unify_ellipsis(text, spans)
        if n:
            stats["ellipsis"] = n
        spans = _protected_spans(text)

    if params.dash_unify:
        text, n = _unify_dash(text, spans)
        if n:
            stats["dash"] = n

    if params.quote_style != "keep":
        text, n = _unify_quotes(text, params.quote_style)
        if n:
            stats["quotes"] = n

    if params.number_direction == "to_cn":
        text, n = _arabic_to_cn(text, params.number_scope)
        if n:
            stats["numbers"] = n
    elif params.number_direction == "to_en":
        text, n = _cn_to_arabic(text)
        if n:
            stats["numbers"] = n

    if params.fullwidth_letters:
        text, n = _fullwidth_letters(text)
        if n:
            stats["fullwidth_letters"] = n

    if params.fullwidth_digits:
        text, n = _fullwidth_digits(text)
        if n:
            stats["fullwidth_digits"] = n

    if params.bracket_style != "keep":
        text, n = _unify_brackets(text, params.bracket_style)
        if n:
            stats["brackets"] = n

    if params.space_unify:
        text, n = _unify_spaces(text)
        if n:
            stats["spaces"] = n

    return text, stats


_UNIFY_LABELS = {
    "punct": "标点", "ellipsis": "省略号", "dash": "破折号",
    "quotes": "引号", "numbers": "数字",
    "fullwidth_letters": "全角字母", "fullwidth_digits": "全角数字",
    "brackets": "括号", "spaces": "空格",
}


def format_unify_stats(stats: Dict[str, int]) -> str:
    if not stats:
        return "无改动"
    parts: List[str] = []
    for k, v in stats.items():
        if v:
            parts.append(f"{_UNIFY_LABELS.get(k, k)} {v} 处")
    return " · ".join(parts) if parts else "无改动"