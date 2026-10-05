# utils/edit_tools/_common.py
"""编辑工具箱共享常量：章节标题识别。

clean.py 与 paragraph.py 都需要判断"这一行是不是章节标题"，
标题行需要豁免缩进/清理等操作。为避免两处逻辑漂移，统一放在这里。
"""
import re

# 章节标题模式：第X章/回/卷/部/篇、序章、序幕、尾声、楔子、番外、后记、前言、序言
# 数字前后允许任意空白（第 12 回、第  三  章 都算）
TITLE_RE = re.compile(
    r"^\s*(?:第\s*[0-9零一二三四五六七八九十百千万]+\s*[章节回卷部篇]"
    r"|序章|序幕|尾声|楔子|番外|后记|前言|序言)"
)


def is_title_line(line: str) -> bool:
    """判断一行是否为章节标题行。"""
    return bool(TITLE_RE.match(line or ""))