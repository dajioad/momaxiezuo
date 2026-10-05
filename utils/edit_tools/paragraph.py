# utils/edit_tools/paragraph.py
"""段落处理工具（纯函数）

解决什么：
- 从 Word / PDF / 网页 / AI 复制来的文本，段落结构混乱
- 长段没断、短行连成一片、缩进缺失、空行数量不统一

处理顺序：
1. 拆分长段（超阈值的段落在句末标点处断成多段）
2. 合并短行（连续多个短段合并成一段）
3. 空行统一（保持原样 / 全删 / 恰好一个）
4. 缩进统一（加两个全角空格 / 去缩进 / 保持原样）

保护规则：
- 章节标题行（第X章、序章、尾声、番外）不参与拆分/合并/缩进
- 对话行（引号开头）默认不合并、不加缩进
- 合并只在"块"内进行，不跨越空行
- 拆分保留原段的前导缩进（半角空格、Tab、全角空格）
"""
import re
from dataclasses import dataclass

from ._common import is_title_line  # noqa: F401  对外保持原有导出


# 对话行开头字符（ASCII 直引号 + 中文弯引号 + 直角引号）
_DIALOGUE_OPEN = ('"', '"', "「", "『", "'", "'")

# 全角缩进（两个全角空格）
_FULL_INDENT = "　　"

# 缩进字符集（半角空格、Tab、全角空格）
_INDENT_CHARS = " \t　"


def is_blank(line: str) -> bool:
    return not (line or "").strip()


def is_dialogue_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    return s[0] in _DIALOGUE_OPEN


def count_chars(text: str) -> int:
    """非空白字符数（和 work_store.count_words 同口径）"""
    return len(re.sub(r"\s", "", text or ""))


def _extract_indent(line: str) -> str:
    """提取行首缩进（半角空格、Tab、全角空格）"""
    for i, ch in enumerate(line):
        if ch not in _INDENT_CHARS:
            return line[:i]
    return line


@dataclass
class ParagraphParams:
    # A. 拆分长段
    split_enabled: bool = True
    split_threshold: int = 120
    split_min_keep: int = 40
    split_chars: str = "。！？…"

    # B. 合并短行
    merge_enabled: bool = False
    merge_threshold: int = 20
    merge_max: int = 200
    merge_skip_dialogue: bool = True
    merge_skip_titles: bool = True

    # C. 空行
    # "keep" 保持原样 / "none" 全删 / "one" 恰好一个
    blank_mode: str = "keep"

    # D. 缩进
    # "keep" 保持 / "add" 加缩进 / "remove" 去缩进
    indent_mode: str = "keep"
    indent_dialogue: bool = False


# ---------------- 拆分 ----------------

def _find_break_point(text: str, start: int, threshold: int,
                      min_keep: int, break_chars: str) -> int:
    """从 start 位置开始，找一个合适的拆分点下标。

    规则：
    - 从 start + min_keep 开始扫描
    - 命中句末标点（且不是连续标点的一部分）时，返回标点之后的位置
    - 若累计字符数已到 threshold 的 80% 且命中标点，立刻返回（就近原则）
    - 找不到合法拆分点返回 -1
    """
    n = len(text)
    if n - start <= threshold:
        return -1
    scan_end = min(n, start + threshold + 40)
    fallback = -1
    for i in range(start + min_keep, scan_end):
        if text[i] not in break_chars:
            continue
        # 连续标点只认最后一个（如 …… 里的第二个）
        if i + 1 < n and text[i + 1] in break_chars:
            continue
        pos = i + 1
        if pos - start < min_keep:
            continue
        # 就近原则：到达阈值 80% 立刻拆
        if pos - start >= int(threshold * 0.8):
            return pos
        # 否则记下第一个合法位置作为兜底
        if fallback < 0:
            fallback = pos
    return fallback


def split_long_paragraph(para: str, threshold: int, min_keep: int,
                         break_chars: str):
    """把一段超长文字拆成多段，返回段落列表。

    保留原段落的前导缩进（半角空格、Tab、全角空格），每一段都带相同缩进前缀。
    内部用无缩进的 body 计算切点，保证 threshold/min_keep 反映的是有效字符数。
    """
    if count_chars(para) <= threshold:
        return [para]

    indent = _extract_indent(para)
    body = para[len(indent):]
    if not body:
        return [para]

    result = []
    start = 0
    while start < len(body):
        pos = _find_break_point(body, start, threshold, min_keep, break_chars)
        if pos < 0:
            rest = body[start:].strip()
            if rest:
                result.append(indent + rest)
            break
        seg = body[start:pos].strip()
        if seg:
            result.append(indent + seg)
        start = pos
    return result if result else [para]


# ---------------- 合并 ----------------

def merge_short_lines(lines, threshold: int, max_merged: int,
                      skip_dialogue: bool, skip_titles: bool):
    """把一个块内的连续短行合并成整段。

    lines: list[str] 一个块内的非空行（不含空行）
    """
    if not lines:
        return lines
    out = []
    buf = ""
    for line in lines:
        # 标题行 / 对话行：遇到则中断合并，单独保留
        if (skip_titles and is_title_line(line)) or \
                (skip_dialogue and is_dialogue_line(line)):
            if buf:
                out.append(buf)
                buf = ""
            out.append(line)
            continue
        n = count_chars(line)
        if buf:
            if n < threshold and count_chars(buf) + n <= max_merged:
                buf = buf + line
                continue
            out.append(buf)
            buf = ""
        if n < threshold:
            buf = line
        else:
            out.append(line)
    if buf:
        out.append(buf)
    return out


# ---------------- 主函数 ----------------

def _split_into_blocks(text: str):
    """把文本按空行拆成"块"。

    返回 (blocks, seps)
    - blocks: [[line, line, ...], ...]  每个块内的非空行
    - seps:   [n, n, ...]  长度 = len(blocks)-1，
              seps[i] 表示 blocks[i] 与 blocks[i+1] 之间的空行数

    文首/文末的空行被忽略。
    """
    lines = text.split("\n")
    blocks = []
    seps = []
    cur = []
    pending_blanks = 0
    first_seen = False
    for line in lines:
        if is_blank(line):
            if cur:
                blocks.append(cur)
                cur = []
                first_seen = True
            pending_blanks += 1
        else:
            if not cur:
                if first_seen:
                    seps.append(pending_blanks)
                pending_blanks = 0
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks, seps


def process_paragraphs(text: str, params: ParagraphParams):
    """段落处理主入口，返回 (new_text, stats)。"""
    if text is None:
        return "", {}
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    blocks, seps = _split_into_blocks(text)

    stats = {
        "split": 0,
        "merged": 0,
        "blank_added": 0,
        "blank_removed": 0,
        "indent_added": 0,
        "indent_removed": 0,
    }

    new_blocks = []
    for block in blocks:
        b = list(block)

        # 1. 拆分长段
        if params.split_enabled:
            expanded = []
            for line in b:
                if is_title_line(line):
                    expanded.append(line)
                    continue
                parts = split_long_paragraph(
                    line, params.split_threshold, params.split_min_keep,
                    params.split_chars)
                if len(parts) > 1:
                    stats["split"] += len(parts) - 1
                expanded.extend(parts)
            b = expanded

        # 2. 合并短行
        if params.merge_enabled:
            before = len(b)
            b = merge_short_lines(
                b, params.merge_threshold, params.merge_max,
                params.merge_skip_dialogue, params.merge_skip_titles)
            stats["merged"] += max(0, before - len(b))

        # 3. 缩进（放在拆分/合并之后，保证基于无缩进正文判断更准）
        if params.indent_mode == "add":
            new_b = []
            for line in b:
                if is_title_line(line):
                    new_b.append(line)
                    continue
                if is_dialogue_line(line) and not params.indent_dialogue:
                    new_b.append(line)
                    continue
                if line.startswith(_FULL_INDENT):
                    new_b.append(line)
                else:
                    s = line.lstrip(_INDENT_CHARS)
                    new_b.append(_FULL_INDENT + s)
                    stats["indent_added"] += 1
            b = new_b
        elif params.indent_mode == "remove":
            new_b = []
            for line in b:
                s = line.lstrip(_INDENT_CHARS)
                if s != line:
                    stats["indent_removed"] += 1
                new_b.append(s)
            b = new_b

        new_blocks.append(b)

    # 4. 组装
    result_lines = []
    for i, block in enumerate(new_blocks):
        if i > 0:
            n_orig = seps[i - 1] if i - 1 < len(seps) else 1
            if params.blank_mode == "none":
                n_new = 0
            elif params.blank_mode == "one":
                n_new = 1
            else:
                n_new = n_orig
            if n_new > n_orig:
                stats["blank_added"] += n_new - n_orig
            elif n_new < n_orig:
                stats["blank_removed"] += n_orig - n_new
            result_lines.extend([""] * n_new)
        result_lines.extend(block)

    new_text = "\n".join(result_lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, stats


def format_stats(stats: dict) -> str:
    """把统计 dict 转成人类可读的一行。"""
    if not stats:
        return ""
    parts = []
    mapping = [
        ("split", "拆分", "处"),
        ("merged", "合并", "处"),
        ("blank_added", "增加空行", "个"),
        ("blank_removed", "删除空行", "个"),
        ("indent_added", "加缩进", "段"),
        ("indent_removed", "去缩进", "段"),
    ]
    for key, label, unit in mapping:
        n = int(stats.get(key, 0))
        if n > 0:
            parts.append(f"{label} {n} {unit}")
    return " · ".join(parts) if parts else "无需改动"