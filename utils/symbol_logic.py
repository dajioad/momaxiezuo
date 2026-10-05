# utils/symbol_logic.py
"""符号处理核心逻辑（无 Qt 依赖）

供 ui/writing_editor.py 与 ui/symbol_panel.py 共用。
"""
import re

# 符号成对关系：左符号 -> 右符号（两端添加时使用）
PAIR_OPENS = {
    "「": "」", "『": "』", "“": "”", "‘": "’",
    "（": "）", "《": "》", "【": "】", "(": ")",
    "[": "]", "{": "}", "<": ">", "\"": "\"", "'": "'",
}


def parse_delims(s: str):
    """把分隔符输入解析为列表。

    - 用 | 分隔多个：如 "，|。" → ["，", "。"]
    - 转义：\\t → Tab、\\n → 换行、\\s → 空格
    - 去重并保持顺序
    """
    if not s:
        return []
    s = (s.replace("\\t", "\t")
          .replace("\\n", "\n")
          .replace("\\s", " "))
    parts = [p for p in s.split("|") if p]
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def wrap_pair(sym: str):
    """根据 sym 判断包裹时的左右符号。

    - 双字符（如 "《》"）：取首尾
    - 单字符在 PAIR_OPENS 里（如 "《"）：用成对映射
    - 其他：左右都是同一字符
    """
    if len(sym) >= 2:
        return sym[0], sym[-1]
    if sym in PAIR_OPENS:
        return sym, PAIR_OPENS[sym]
    return sym, sym


def apply_single(text: str, kind: str, sym: str,
                 every: int = 1, skip_ws: bool = True):
    """对单段文本应用插入方式（不含分隔符分段）。

    kind: head / tail / both / split
    - head/tail/both：返回加符号后的字符串
    - split：每隔 every 个字符插一次符号；无插入点返回 None
    """
    if kind == "head":
        return sym + text
    if kind == "tail":
        return text + sym
    if kind == "both":
        left, right = wrap_pair(sym)
        return left + text + right
    if kind == "split":
        step = max(1, int(every))
        out = []
        count = 0
        for ch in text:
            if ch.isspace() and skip_ws:
                out.append(ch)
                if ch in "\r\n":
                    count = 0
                continue
            if count > 0 and count % step == 0:
                out.append(sym)
            out.append(ch)
            count += 1
        new_text = "".join(out)
        return new_text if new_text != text else None
    return None


def build_edge_result(text: str, kind: str, sym: str,
                      every: int = 1, skip_ws: bool = True,
                      delimiter: str = "", connector: str = "delim"):
    """按位置类型计算选区文本加符号后的结果。

    两种模式（由 delimiter 是否非空区分）：

    1. delimiter 为空：对整段文本应用插入方式（旧行为）
    2. delimiter 非空：先按分隔符拆成多段，对每段独立应用插入方式，
       段之间用 connector 指定的连接符拼接。
       例：123，45，6789 + 《 + 分隔符"，" + 两端
           → 《123》，《45》，《6789》

    返回 None 表示无需修改（无插入点）。
    """
    if not text:
        return None

    delims = parse_delims(delimiter)
    if delims:
        pattern = "|".join(re.escape(d) for d in delims if d)
        if not pattern:
            return None
        parts = [p.strip() for p in re.split(pattern, text)]
        parts = [p for p in parts if p]
        if not parts:
            return None
        results = []
        for p in parts:
            r = apply_single(p, kind, sym, every, skip_ws)
            results.append(r if r is not None else p)
        if connector == "comma":
            sep = ", "
        elif connector == "none":
            sep = ""
        else:
            sep = delims[0]
        new_text = sep.join(results)
        return new_text if new_text != text else None

    return apply_single(text, kind, sym, every, skip_ws)