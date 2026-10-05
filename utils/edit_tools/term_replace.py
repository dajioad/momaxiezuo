# utils/edit_tools/term_replace.py
"""墨码写作 - 术语替换核心逻辑（纯函数，无 Qt 依赖）

与"批量工具 → 批量内容替换"的区别：这是作品级、带上下文预览、
逐条可勾选的。改名/统一术语时，用户能看到全书每一处匹配。

核心接口：
    find_replacements(text, params) -> [(start, end, replacement), ...]
    apply_replacements(text, replacements) -> new_text
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class TermParams:
    find: str = ""
    replace: str = ""
    use_regex: bool = False
    ignore_spaces: bool = False        # 忽略查找串中间的空格
    ignore_width: bool = False         # 忽略全角半角差异
    case_sensitive: bool = True
    whole_word: bool = False


# ---------- 字符类构造 ----------

def _char_class(ch: str) -> str:
    """返回匹配 ch 及其全角/半角对应字符的正则片段。"""
    code = ord(ch)
    if 0x41 <= code <= 0x5A or 0x61 <= code <= 0x7A or 0x30 <= code <= 0x39:
        full = chr(code + 0xFEE0)
        return f"[{re.escape(ch)}{re.escape(full)}]"
    if 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A \
            or 0xFF10 <= code <= 0xFF19:
        half = chr(code - 0xFEE0)
        return f"[{re.escape(half)}{re.escape(ch)}]"
    if ch in (" ", "　"):
        return "[ 　]"
    return re.escape(ch)


def _build_pattern(params: TermParams) -> Optional[re.Pattern]:
    if not params.find:
        return None

    flags = 0 if params.case_sensitive else re.IGNORECASE

    if params.use_regex:
        try:
            pattern = params.find
            if params.whole_word:
                pattern = r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])"
            return re.compile(pattern, flags)
        except re.error:
            return None

    parts: List[str] = []
    n = len(params.find)
    for i, ch in enumerate(params.find):
        if params.ignore_width:
            parts.append(_char_class(ch))
        else:
            parts.append(re.escape(ch))
        if params.ignore_spaces and i < n - 1:
            parts.append(r"\s*")

    pattern = "".join(parts)
    if params.whole_word:
        pattern = r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])"

    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


# ---------- 对外接口 ----------

def find_replacements(
        text: str,
        params: TermParams) -> List[Tuple[int, int, str]]:
    """返回 [(start, end, replacement), ...]，按出现顺序排序。"""
    if not text or not params.find:
        return []
    pattern = _build_pattern(params)
    if pattern is None:
        return []

    results: List[Tuple[int, int, str]] = []
    for m in pattern.finditer(text):
        start, end = m.start(), m.end()
        if end <= start:
            continue
        if params.use_regex:
            try:
                replacement = m.expand(params.replace)
            except re.error:
                replacement = params.replace
        else:
            replacement = params.replace
        results.append((start, end, replacement))
    return results


def apply_replacements(
        text: str,
        replacements: List[Tuple[int, int, str]]) -> str:
    """应用替换。replacements 必须来自同一文本的 find_replacements。

    replacement 已在 find_replacements 中经过 m.expand 展开，这里直接
    拼接即可。旧实现每处替换都整体切片重建字符串，K 处匹配是 O(K·N)；
    改为分段收集后一次 join，总体 O(N+K)。
    """
    if not replacements:
        return text
    parts: List[str] = []
    pos = 0
    for start, end, repl in sorted(replacements):
        if start < pos:
            continue  # 重叠项保险跳过（finditer 正常不会产生）
        parts.append(text[pos:start])
        parts.append(repl)
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


def format_term_stats(total: int, chapters: int) -> str:
    if total == 0:
        return "无匹配"
    return f"共 {total} 处 · 涉及 {chapters} 章"