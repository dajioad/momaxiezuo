# utils/format_engine.py
"""墨码写作 - 排版规范化引擎

纯函数实现，不依赖 Qt。每条规则独立开关，返回替换统计，
UI 层配合 difflib 做差异预览。

铁律：排版前由上层自动生成快照，引擎只负责文本变换。
"""
import re
from typing import Tuple, Dict, List
from dataclasses import dataclass

# 标点映射（ASCII -> 中文全角）
_PUNCT_MAP = {
    ",": "，",
    ".": "。",
    "!": "！",
    "?": "？",
    ";": "；",
    ":": "：",
}

# 章节标题模式：第X章 / 回 / 卷 / 部 / 篇、序章、楔子、尾声、番外……
# 标题行不参与段首缩进，也不参与"标题前后空行"以外的规则处理
_TITLE_RE = re.compile(
    r"^\s*(?:第\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回卷部篇]"
    r"|序章|序幕|尾声|楔子|番外|后记|前言|序言)"
)

# URL / 邮箱保护
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s一-鿿，。！？；：、""''（）【】]+", re.I)
_MAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 数字上下文：千分位 3,000 / 小数 3.14 / 日期 2026/1/1 / 时间 12:30 / 版本 v1.2
_NUM_TOKEN_RE = re.compile(r"\d[\d,\./:%-]*\d|[A-Za-z]\d*\.\d")
# 英文缩写撇号 don't  Jim's
_APOS_RE = re.compile(r"(?<=[A-Za-z])'(?=[A-Za-z])")
# CJK 统一表意文字范围
_CJK = "\u4e00-\u9fff"


# ============================================================
# 缩进样式常量
# ============================================================
INDENT_KEEP = "keep"        # 保持原样（不做任何缩进处理）
INDENT_FULL2 = "full2"      # 两个全角空格（网文默认）
INDENT_HALF2 = "half2"      # 两个半角空格
INDENT_HALF4 = "half4"      # 四个半角空格（Markdown 风）
INDENT_NONE = "none"        # 去掉段首缩进

_INDENT_PREFIX = {
    INDENT_FULL2: "　　",
    INDENT_HALF2: "  ",
    INDENT_HALF4: "    ",
}


@dataclass
class FormatRule:
    rule_id: str
    label: str
    desc: str
    default_on: bool = True


# 规则清单（顺序即执行顺序）
RULES: List[FormatRule] = [
    FormatRule("punct", "英文标点转中文",
               "，。！？；：（）与直引号自动转中文；保护数字、网址、邮箱"),
    FormatRule("fullwidth_digit", "数字全角转半角",
               "１２３ → 123，老稿常见问题", default_on=False),
    FormatRule("quote", "直引号配对",
               '半角 " 按配对规则转为中文 “ ”；支持跨段对话'),
    FormatRule("single_quote", "单引号配对",
               "半角 ' 转为 ‘ ’，自动跳过英文缩写（如 don't）",
               default_on=False),
    FormatRule("ellipsis", "省略号规范化", ".../。。。统一为 ……"),
    FormatRule("dash", "破折号规范化", "连续的 -- 统一为 ——"),
    FormatRule("cn_space", "删除汉字间空格", "删除两个汉字之间误输入的空格"),
    FormatRule("trailing", "清理行尾空格", "删除每行末尾多余的空格/制表符"),
    FormatRule("indent", "段首缩进",
               "正文段落开头统一缩进；缩进样式由下方下拉框控制"),
    FormatRule("title_blank", "标题前后空行",
               "章节标题前后各留一个空行，文首文末不留空行",
               default_on=False),
    FormatRule("blank", "压缩多余空行",
               "连续多个空行压缩为一个（可在选项中改为全部删除）"),
]

RULE_IDS = {r.rule_id for r in RULES}
DEFAULT_RULES = {r.rule_id: r.default_on for r in RULES}

# blank 规则选项
BLANK_KEEP_ONE = "keep_one"
BLANK_REMOVE_ALL = "remove_all"


def _protected_spans(text: str) -> List[Tuple[int, int]]:
    """返回需要保护的字符区间（URL、邮箱、数字词法单元）"""
    spans = []
    for rx in (_URL_RE, _MAIL_RE, _NUM_TOKEN_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    # 合并重叠区间
    spans.sort()
    merged = []
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


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


# ---------- 各规则实现，返回 (新文本, 替换次数) ----------

def _rule_punct(text: str) -> Tuple[str, int]:
    spans = _protected_spans(text)
    chars = list(text)
    count = 0
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch in _PUNCT_MAP:
            if _in_spans(i, spans):
                continue
            # 句点/逗号在 ASCII 字母数字之间也保留（如 a.txt, a, b 参数）
            if ch in (".", ","):
                prev_ch = chars[i - 1] if i > 0 else ""
                next_ch = chars[i + 1] if i + 1 < n else ""
                if (prev_ch.isascii() and (prev_ch.isalnum() or prev_ch in "_-")
                        and next_ch.isascii()
                        and (next_ch.isalnum() or next_ch in "_-/")):
                    continue
            chars[i] = _PUNCT_MAP[ch]
            count += 1
        elif ch in "()":
            if _in_spans(i, spans):
                continue
            # 括号：相邻任一侧是中文才转，避免误伤代码/英文
            prev_ch = chars[i - 1] if i > 0 else ""
            next_ch = chars[i + 1] if i + 1 < n else ""
            if _is_cjk(prev_ch) or _is_cjk(next_ch):
                chars[i] = "（" if ch == "(" else "）"
                count += 1

    # 顺带把直引号也转成中文引号。
    # 用户视角"英文标点转中文"= 所有英文标点，包括引号。
    # 引号不能简单 1:1 映射，要走配对逻辑（见 _rule_quote）。
    result = "".join(chars)
    result, n_q = _rule_quote(result)
    result, n_sq = _rule_single_quote(result)
    count += n_q + n_sq
    return result, count


def _rule_fullwidth_digit(text: str) -> Tuple[str, int]:
    """全角数字 ０-９ 转半角 0-9。"""
    out = []
    count = 0
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:
            out.append(chr(code - 0xFEE0))
            count += 1
        else:
            out.append(ch)
    return "".join(out), count


def _rule_quote(text: str) -> Tuple[str, int]:
    """半角直引号 " 按配对规则转为中文 “ ”。

    关键设计（与 unify.py 的 _unify_quotes 保持一致）：
    - 全局配对状态（open_quote 跨行不重置），正确处理网文的"跨段对话"：
      第一段只有开引号，最后一段末尾才出现闭引号。
    - 行首引号强制视为开引号。判断标准是"引号前面只有空白字符"，
      而不是绝对的行首（行首通常是全角缩进 　　）。
    - 非行首引号按全局配对状态翻。
    - 只处理半角 "，不碰已配对的中文 “ ”。
    """
    count = 0
    out_lines = []
    open_quote = True   # 全局状态，跨行保留
    indent_chars = " \t　"

    for line in text.split("\n"):
        chars = list(line)
        seen_content = False
        for i, ch in enumerate(chars):
            if ch != '"':
                if ch not in indent_chars:
                    seen_content = True
                continue
            is_line_start = not seen_content
            if is_line_start:
                chars[i] = "“"
                open_quote = False
            else:
                chars[i] = "“" if open_quote else "”"
                open_quote = not open_quote
            count += 1
            seen_content = True
        out_lines.append("".join(chars))
    return "\n".join(out_lines), count


def _rule_single_quote(text: str) -> Tuple[str, int]:
    """半角单引号 ' 转为 ‘ ’。

    与 _rule_quote 相同的设计：
    - 全局配对状态跨行保留（跨段对话）
    - 行首引号强制开
    - 英文缩写撇号（don't）不处理
    """
    # 先把英文缩写撇号占位保护
    placeholders = {}

    def _protect(m):
        key = f"\x00{len(placeholders)}\x00"
        placeholders[key] = "'"
        return key

    protected = _APOS_RE.sub(_protect, text)
    count = 0
    out_lines = []
    open_quote = True   # 全局状态，跨行保留
    indent_chars = " \t　"

    for line in protected.split("\n"):
        chars = list(line)
        seen_content = False
        for i, ch in enumerate(chars):
            if ch != "'":
                if ch not in indent_chars:
                    seen_content = True
                continue
            is_line_start = not seen_content
            if is_line_start:
                chars[i] = "‘"
                open_quote = False
            else:
                chars[i] = "‘" if open_quote else "’"
                open_quote = not open_quote
            count += 1
            seen_content = True
        out_lines.append("".join(chars))
    result = "\n".join(out_lines)
    for key, val in placeholders.items():
        result = result.replace(key, val)
    return result, count


def _rule_ellipsis(text: str) -> Tuple[str, int]:
    new_text, n1 = re.subn(r"\.{3,}|。{3,}|．{3,}|…+", "……", text)
    return new_text, n1


def _rule_dash(text: str) -> Tuple[str, int]:
    return re.subn(r"-{2,}", "——", text)


def _rule_indent(text: str, style: str) -> Tuple[str, int]:
    """段首缩进。

    style:
      INDENT_FULL2  两个全角空格（网文默认）
      INDENT_HALF2  两个半角空格
      INDENT_HALF4  四个半角空格（Markdown 风）
      INDENT_NONE   去掉段首缩进
      INDENT_KEEP   不做任何处理（调用方应跳过本规则）
    """
    if style == INDENT_KEEP:
        return text, 0
    prefix = _INDENT_PREFIX.get(style, "")
    count = 0
    out = []
    for line in text.split("\n"):
        raw = line.rstrip(" \t")
        stripped = raw.lstrip(" \t　")
        if not stripped:
            out.append(raw)
            continue
        if _TITLE_RE.match(stripped):
            # 标题行不缩进，同时去掉原有误加缩进
            if raw != stripped:
                count += 1
            out.append(stripped)
            continue
        if style == INDENT_NONE:
            if raw != stripped:
                count += 1
            out.append(stripped)
        else:
            new_line = prefix + stripped
            if new_line != raw:
                count += 1
            out.append(new_line)
    return "\n".join(out), count


def _rule_cn_space(text: str) -> Tuple[str, int]:
    """删除汉字之间的多余空格。

    增强点：
    - 支持全角空格 U+3000 和 NBSP U+00A0（从 Word / 网页复制常见）
    - 汉字旁边是中文标点时也删（"你好 ！" → "你好！"）
    - 章节标题行（第X章 觉醒）保持原样，不去动标题里的分隔空格

    用 [^\\S\\n]+ 匹配"非换行的所有空白"，一次覆盖半角/全角/Tab/NBSP。
    """
    # 汉字 + 中文标点（用于判断空格一侧是否是"中文内容"）
    CN_CHARS = r"\u4e00-\u9fff\u3400-\u4dbf"
    CN_PUNCT = r"，。！？；：、（）【】《》""''「」『』…—"
    left_cls = f"[{CN_CHARS}{CN_PUNCT}]"
    right_cls = f"[{CN_CHARS}{CN_PUNCT}]"

    total = 0
    out_lines = []
    pattern = re.compile(
        rf"(?<={left_cls})[^\S\n]+(?={right_cls})")

    for line in text.split("\n"):
        # 标题行整行豁免（含"第一章 觉醒"这种标题内部的空格）
        if _TITLE_RE.match(line.strip()):
            out_lines.append(line)
            continue
        # 反复替换直到稳定，处理「你 好 啊」这类连续情况
        while True:
            new_line, n = pattern.subn("", line)
            if n == 0:
                break
            line = new_line
            total += n
        out_lines.append(line)
    return "\n".join(out_lines), total


def _rule_trailing(text: str) -> Tuple[str, int]:
    lines = text.split("\n")
    count = sum(1 for ln in lines if ln != ln.rstrip(" \t"))
    return "\n".join(ln.rstrip(" \t") for ln in lines), count


def _rule_title_blank(text: str) -> Tuple[str, int]:
    """标题前后各补一个空行；文首文末不留空行。

    只补空行，不删多余空行——多余空行交给 blank 规则压缩。
    幂等：已规范的文本再跑一次不会产生新改动。
    """
    lines = text.split("\n")
    # 去文首文末空行
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return "", 0

    result = []
    count = 0
    n = len(lines)
    for i, line in enumerate(lines):
        is_title = bool(_TITLE_RE.match(line.strip()))
        if is_title:
            # 前一行不是空行（且不是文首），插一个空行
            if result and result[-1].strip():
                result.append("")
                count += 1
            result.append(line)
            # 后一行不是空行（且不是文末），插一个空行
            if i + 1 < n and lines[i + 1].strip():
                result.append("")
                count += 1
        else:
            result.append(line)
    return "\n".join(result), count


def _rule_blank(text: str, mode: str) -> Tuple[str, int]:
    if mode == BLANK_REMOVE_ALL:
        new_text, n = re.subn(r"\n[ \t　]*\n+", "\n", text)
        return new_text.strip("\n") + "\n", n
    # 三个及以上换行（含空白行）压缩为两个换行（保留一个空行）
    new_text, n = re.subn(r"\n{3,}", "\n\n", text)
    return new_text, n


_RULE_FUNCS = {
    "punct": _rule_punct,
    "fullwidth_digit": _rule_fullwidth_digit,
    "quote": _rule_quote,
    "single_quote": _rule_single_quote,
    "ellipsis": _rule_ellipsis,
    "dash": _rule_dash,
    "cn_space": _rule_cn_space,
    "trailing": _rule_trailing,
    "title_blank": _rule_title_blank,
}


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def format_text(text: str, rules: Dict[str, bool] = None,
                blank_mode: str = BLANK_KEEP_ONE,
                indent_style: str = INDENT_FULL2) -> Tuple[str, Dict[str, int]]:
    """按启用的规则依次排版，返回 (新文本, 各规则替换次数)。

    indent_style 控制段首缩进样式（见 INDENT_* 常量）。
    若规则表里 indent 是开的但 indent_style=INDENT_KEEP，
    则缩进规则等同关闭。
    """
    rules = rules or DEFAULT_RULES
    text = normalize_newlines(text)
    stats: Dict[str, int] = {}
    for rule in RULES:
        if not rules.get(rule.rule_id, rule.default_on):
            continue
        if rule.rule_id == "blank":
            text, n = _rule_blank(text, blank_mode)
        elif rule.rule_id == "indent":
            if indent_style == INDENT_KEEP:
                continue
            text, n = _rule_indent(text, indent_style)
        else:
            text, n = _RULE_FUNCS[rule.rule_id](text)
        if n:
            stats[rule.rule_id] = n
    return text, stats


def build_diff(old: str, new: str, context: int = 1) -> str:
    """生成简易行级差异文本（供预览）"""
    import difflib
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines,
                                fromfile="原文", tofile="排版后",
                                lineterm="", n=context)
    return "\n".join(diff)