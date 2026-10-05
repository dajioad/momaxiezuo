# utils/platform_format.py
"""墨码写作 - 平台格式一键（纯逻辑，无 Qt）

各平台对投稿格式的要求略有差异。本模块把差异固化成"平台预设"，
底层复用 format_engine.format_text()。

设计：
- 平台预设只描述"规则组合 + 空行模式 + 缩进样式"
- 用户选平台 → 直接跑 format_text，不暴露细粒度开关
- 想细调的用户请用「一键排版」对话框
"""
from __future__ import annotations

from typing import Dict, List

from .format_engine import (
    format_text,
    BLANK_KEEP_ONE,
    BLANK_REMOVE_ALL,
    INDENT_FULL2,
    INDENT_KEEP,
)


# 通用规则组合：段首缩进 + 标点规范 + 清理
_BASE_RULES = {
    "punct": True,
    "fullwidth_digit": False,
    "quote": True,
    "single_quote": True,
    "ellipsis": True,
    "dash": True,
    "cn_space": True,
    "trailing": True,
    "indent": True,
    "title_blank": True,
    "blank": True,
}

# 保守规则组合：只做排版，不动标点（老稿/文学向）
_CONSERVATIVE_RULES = dict(_BASE_RULES)
_CONSERVATIVE_RULES.update({
    "punct": False,
    "quote": False,
    "single_quote": False,
    "ellipsis": False,
    "dash": False,
})


PLATFORMS: Dict[str, dict] = {
    "fanqie": {
        "name": "番茄小说",
        "desc": "段首两全角缩进，段间不留空行，标点全中文",
        "rules": dict(_BASE_RULES),
        "blank_mode": BLANK_REMOVE_ALL,
        "indent_style": INDENT_FULL2,
    },
    "qidian": {
        "name": "起点 / 阅文",
        "desc": "段首两全角缩进，段间不留空行，标点规范",
        "rules": dict(_BASE_RULES),
        "blank_mode": BLANK_REMOVE_ALL,
        "indent_style": INDENT_FULL2,
    },
    "qimao": {
        "name": "七猫免费小说",
        "desc": "段首两全角缩进，段间不留空行（同番茄）",
        "rules": dict(_BASE_RULES),
        "blank_mode": BLANK_REMOVE_ALL,
        "indent_style": INDENT_FULL2,
    },
    "jjwxc": {
        "name": "晋江文学城",
        "desc": "段首两全角缩进，段间保留一个空行，标点规范",
        "rules": dict(_BASE_RULES),
        "blank_mode": BLANK_KEEP_ONE,
        "indent_style": INDENT_FULL2,
    },
    "zongheng": {
        "name": "纵横中文网",
        "desc": "段首两全角缩进，段间不留空行",
        "rules": dict(_BASE_RULES),
        "blank_mode": BLANK_REMOVE_ALL,
        "indent_style": INDENT_FULL2,
    },
    "generic": {
        "name": "通用投稿（推荐）",
        "desc": "段首两全角缩进，段间保留一个空行，兼容性最好",
        "rules": dict(_BASE_RULES),
        "blank_mode": BLANK_KEEP_ONE,
        "indent_style": INDENT_FULL2,
    },
    "conservative": {
        "name": "只做缩进（不动标点）",
        "desc": "段首两全角缩进 + 清理行尾，标点保持原样（文学向/老稿）",
        "rules": dict(_CONSERVATIVE_RULES),
        "blank_mode": BLANK_KEEP_ONE,
        "indent_style": INDENT_FULL2,
    },
}

PLATFORM_ORDER: List[str] = [
    "generic", "fanqie", "qidian", "qimao", "jjwxc", "zongheng",
    "conservative",
]


def apply_platform(text: str, platform_key: str) -> tuple:
    """按平台预设排版一段文本。

    返回 (新文本, 统计字典)。
    """
    p = PLATFORMS.get(platform_key)
    if p is None:
        p = PLATFORMS["generic"]
    return format_text(
        text,
        rules=dict(p["rules"]),
        blank_mode=p.get("blank_mode", BLANK_KEEP_ONE),
        indent_style=p.get("indent_style", INDENT_FULL2),
    )