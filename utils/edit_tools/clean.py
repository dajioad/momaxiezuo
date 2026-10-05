# utils/edit_tools/clean.py
"""墨码写作 - 文本清洗核心逻辑（纯函数，无 Qt 依赖）

处理从 Word / 网页 / AI / 微信复制过来的"脏东西"。

八组能力，独立开关：
1. 删除行尾空白
2. 删除行首半角空白（保留全角缩进）
3. 删除行首全角缩进
4. 压缩段间空行（保留 0 / 1 / 2 个空行）
5. 删除不可见字符（零宽、BOM、NBSP、软连字符）
6. 删除连续标点（只合并 。，、；： 类）
7. 删除行号残留
8. 删除复制残留（[图片]、[插图]、HTML 注释、代码块标记）
   并清理 Markdown 标记（**粗体**、~~删除线~~、`行内代码`，只去标记留内容）

边界（与「第一版四件套 · 规格」一致）：
- 章节标题行不参与行首清理（避免破坏「第一章」等标题的排版）
- 空行"保留 0"= 任何两个非空行之间不留空行
- 「……」是正常省略号，不在"连续标点"里
- 「！！！」「？？？」保留
- 行号残留只在行首识别，避免误伤正文中「2023 年」这类

参数说明（strip_leading_fullwidth）：
- 勾上 = 连全角缩进一起删（半角+全角全删），与规格措辞"含全角缩进"一致
- 不勾但勾了 strip_leading_halfwidth = 只删半角空格和 Tab，全角 `　` 保留
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Tuple, List

from ._common import is_title_line


# ---------- 参数 ----------

@dataclass
class CleanParams:
    """文本清洗参数。所有字段有默认值，便于单独构造测试。"""

    # 1 / 2 / 3：行首行尾
    strip_trailing: bool = True
    strip_leading_halfwidth: bool = True
    strip_leading_fullwidth: bool = False

    # 4：空行。keep_all / keep_one / keep_two / remove_all
    collapse_blanks: str = "keep_one"

    # 5 / 6 / 7 / 8
    remove_invisible: bool = True
    collapse_punctuation: bool = False
    strip_line_numbers: bool = False
    strip_paste_artifacts: bool = True


# ---------- 常量 ----------


# 不可见字符：严格对齐规格列出的 5 个
# U+200B 零宽空格、U+200C 零宽非连接符、U+FEFF BOM、
# U+00A0 不换行空格、U+00AD 软连字符
# 故意不含 U+200D（零宽连接符，会破坏 emoji 序列）和 U+2060
_INVISIBLE_CHARS = "\u200b\u200c\ufeff\u00a0\u00ad"
_INVISIBLE_RE = re.compile("[" + re.escape(_INVISIBLE_CHARS) + "]")

# 连续标点：只合并相同字符的连续段，且只针对 。，、；： 五个
# 「。」是省略号的正常写法，不在候选里；「！！！」「？？？」同理保留
_PUNCT_COLLAPSE_RE = re.compile(r"([。，、；：])\1+")

# 行号残留：
#   "1. 第一行"  → 匹配 "1. "
#   "12. 第二行" → 匹配 "12. "
#   "3、第三行"  → 匹配 "3、"（顿号后有没有空格都行）
#   "12."        → 行尾单独一个点，也认为是残留（(?=[ \t]|$) 前瞻）
# 只认 ASCII 数字，避免全角数字误伤
# 用 (?=[ \t]|$) 而不是 \s+，避免把 "1. \n" 里的换行一并吃掉
_LINE_NUM_RE = re.compile(
    r"^[ \t]*[0-9]+、[ \t]*|^[ \t]*[0-9]+\.(?=[ \t]|$)[ \t]*",
    re.MULTILINE,
)

# 复制残留：每条一个正则。
# 元组结构 (pattern, replacement, label)：
#   - 第一类：replacement="" 表示整段删除（图片、注释、代码块标记）
#   - 第二类：replacement=r"\1" 表示只去掉标记、保留内容（Markdown 粗体/删除线/行内代码）
#
# Markdown 规则设计说明：
# - 粗体 \*{2,3} 兼容 **粗体** 和 ***粗体斜体*** 两种写法
# - 中间用 [^*\n]+? 排除换行和星号，避免误伤 "* 项目" 这类列表、"*** 分隔线"
# - 单星号 *斜体* 故意不处理：网文里 * 常出现在数学表达式和分隔线中，误伤成本太高
# - 下划线 __粗体__ 故意不处理：容易误伤 Python 变量名（__init__）
_PASTE_ARTIFACT_RES: List[Tuple[re.Pattern, str, str]] = [
    # ---- 删除型 ----
    (re.compile(r"\[图片\]"), "", "图片标记"),
    (re.compile(r"\[插图\]"), "", "插图标记"),
    (re.compile(r"<!--[\s\S]*?-->"), "", "HTML注释"),
    (re.compile(r"`{3,}\w*"), "", "代码块标记"),
    # ---- 清理型（保留内容）----
    (re.compile(r"\*{2,3}([^*\n]+?)\*{2,3}"), r"\1", "Markdown粗体"),
    (re.compile(r"~~([^~\n]+?)~~"), r"\1", "Markdown删除线"),
    (re.compile(r"`([^`\n]+?)`"), r"\1", "Markdown行内代码"),
]


# ---------- 各步骤实现 ----------

def _remove_invisible(text: str) -> Tuple[str, int]:
    """删除不可见字符，返回 (新文本, 删除的字符数)。"""
    count = len(_INVISIBLE_RE.findall(text))
    if count == 0:
        return text, 0
    return _INVISIBLE_RE.sub("", text), count


def _strip_paste_artifacts(text: str) -> Tuple[str, int]:
    """清理复制残留，返回 (新文本, 处理的处数)。

    两类规则合并处理：
    - 删除型：图片/插图标记、HTML 注释、代码块标记 —— 整个删掉
    - 清理型：Markdown 粗体 / 删除线 / 行内代码 —— 只去掉标记，保留内容
    """
    total = 0
    for pat, repl, _label in _PASTE_ARTIFACT_RES:
        text, n = pat.subn(repl, text)
        total += n
    return text, total


def _strip_line_numbers(text: str) -> Tuple[str, int]:
    """删除行首的行号残留，返回 (新文本, 删除的行数)。"""
    return _LINE_NUM_RE.subn("", text)


def _collapse_punctuation(text: str) -> Tuple[str, int]:
    """合并连续标点（只针对 。，、；：），返回 (新文本, 合并处数)。"""
    return _PUNCT_COLLAPSE_RE.subn(r"\1", text)


def _strip_trailing(text: str) -> Tuple[str, int]:
    """删除每行行尾的半角空白，返回 (新文本, 受影响的行数)。"""
    lines = text.split("\n")
    count = 0
    out: List[str] = []
    for line in lines:
        stripped = line.rstrip(" \t")
        if stripped != line:
            count += 1
        out.append(stripped)
    return "\n".join(out), count


def _strip_leading(text: str, halfwidth: bool, fullwidth: bool) -> Tuple[str, int]:
    """删除行首空白，返回 (新文本, 受影响的行数)。

    - fullwidth=True：删半角+全角（规格里"含全角缩进"的语义）
    - fullwidth=False 且 halfwidth=True：只删半角，全角 `　` 保留
    - 标题行豁免（不参与行首清理）
    """
    if not halfwidth and not fullwidth:
        return text, 0

    if fullwidth:
        strip_chars = " \t　"
    else:
        strip_chars = " \t"

    lines = text.split("\n")
    count = 0
    out: List[str] = []
    for line in lines:
        if is_title_line(line):
            out.append(line)   # 标题行不动
            continue
        stripped = line.lstrip(strip_chars)
        if stripped != line:
            count += 1
        out.append(stripped)
    return "\n".join(out), count


def _collapse_blanks(text: str, mode: str) -> Tuple[str, int]:
    """压缩段间空行，返回 (新文本, 删除的空行数)。

    - keep_all：不动
    - keep_one：连续空行最多保留 1 个
    - keep_two：连续空行最多保留 2 个
    - remove_all：删除所有空行
    """
    if mode == "keep_all":
        return text, 0

    lines = text.split("\n")
    if mode == "remove_all":
        out = [ln for ln in lines if ln.strip()]
        removed = len(lines) - len(out)
        return "\n".join(out), removed

    max_blanks = 1 if mode == "keep_one" else 2
    out = []
    removed = 0
    streak = 0
    for line in lines:
        if line.strip():
            streak = 0
            out.append(line)
        else:
            streak += 1
            if streak <= max_blanks:
                out.append(line)
            else:
                removed += 1
    return "\n".join(out), removed


# ---------- 对外接口 ----------

def clean_text(text: str, params: CleanParams) -> Tuple[str, Dict[str, int]]:
    """按 params 清洗文本，返回 (新文本, 统计字典)。

    统计键（只在触发时出现）：
      trailing        受影响的行数（行尾空白）
      leading         受影响的行数（行首空白）
      blanks          删除的空行数
      invisible       删除的不可见字符数
      punct_collapsed 合并的连续标点处数
      line_numbers    删除的行号处数
      artifacts       删除的复制残留处数

    执行顺序（对结果的影响尽量小，但按此顺序最稳）：
      1. 不可见字符   —— 先删掉 NBSP/零宽，避免影响后续行首/行尾判断
      2. 复制残留     —— HTML 注释可能跨行，先删干净
      3. 行号残留     —— 行首是 "1. " 时整段当行号处理
      4. 连续标点     —— 纯字符变换，和前后步骤不冲突
      5. 行尾空白     —— 逐行
      6. 行首空白     —— 逐行，标题行豁免
      7. 空行压缩     —— 放最后，把前面删除留下的空行一起收掉
    """
    if text is None:
        return "", {}
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    stats: Dict[str, int] = {}

    if params.remove_invisible:
        text, n = _remove_invisible(text)
        if n:
            stats["invisible"] = n

    if params.strip_paste_artifacts:
        text, n = _strip_paste_artifacts(text)
        if n:
            stats["artifacts"] = n

    if params.strip_line_numbers:
        text, n = _strip_line_numbers(text)
        if n:
            stats["line_numbers"] = n

    if params.collapse_punctuation:
        text, n = _collapse_punctuation(text)
        if n:
            stats["punct_collapsed"] = n

    if params.strip_trailing:
        text, n = _strip_trailing(text)
        if n:
            stats["trailing"] = n

    if params.strip_leading_halfwidth or params.strip_leading_fullwidth:
        text, n = _strip_leading(text, params.strip_leading_halfwidth,
                                 params.strip_leading_fullwidth)
        if n:
            stats["leading"] = n

    if params.collapse_blanks != "keep_all":
        text, n = _collapse_blanks(text, params.collapse_blanks)
        if n:
            stats["blanks"] = n

    return text, stats


def format_clean_stats(stats: Dict[str, int]) -> str:
    """把统计字典格式化为一行中文摘要，供 UI 顶部统计条展示。

    格式与规格一致：
      清理了 12 处行尾空白 · 8 处行首空白 · 3 个空行 · 5 个不可见字符
    """
    if not stats:
        return "无改动"
    parts: List[str] = []
    if stats.get("trailing"):
        parts.append(f"{stats['trailing']} 处行尾空白")
    if stats.get("leading"):
        parts.append(f"{stats['leading']} 处行首空白")
    if stats.get("blanks"):
        parts.append(f"{stats['blanks']} 个空行")
    if stats.get("invisible"):
        parts.append(f"{stats['invisible']} 个不可见字符")
    if stats.get("punct_collapsed"):
        parts.append(f"{stats['punct_collapsed']} 处连续标点")
    if stats.get("line_numbers"):
        parts.append(f"{stats['line_numbers']} 处行号")
    if stats.get("artifacts"):
        parts.append(f"{stats['artifacts']} 处复制残留")
    if not parts:
        return "无改动"
    return "清理了 " + " · ".join(parts)