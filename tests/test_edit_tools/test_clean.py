# tests/test_edit_tools/test_clean.py
"""文本清洗核心逻辑的单元测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pytest

from utils.edit_tools.clean import (
    CleanParams, clean_text, format_clean_stats,
)


def _only(**kwargs) -> CleanParams:
    """所有开关默认关，只开 kwargs 里指定的。方便逐个测。"""
    base = dict(
        strip_trailing=False,
        strip_leading_halfwidth=False,
        strip_leading_fullwidth=False,
        collapse_blanks="keep_all",
        remove_invisible=False,
        collapse_punctuation=False,
        strip_line_numbers=False,
        strip_paste_artifacts=False,
    )
    base.update(kwargs)
    return CleanParams(**base)


# ---------- 行尾空白 ----------

def test_trailing_basic():
    out, stats = clean_text(
        "abc   \ndef\t\nghi", _only(strip_trailing=True))
    assert out == "abc\ndef\nghi"
    assert stats["trailing"] == 2


def test_trailing_preserves_fullwidth_space():
    # 全角空格在行尾不应被当成空白删掉
    out, stats = clean_text(
        "abc　\n", _only(strip_trailing=True))
    assert out == "abc　\n"
    assert "trailing" not in stats


# ---------- 行首空白 ----------

def test_leading_halfwidth_only():
    out, stats = clean_text(
        "  abc\n\tdef\n　ghi", _only(strip_leading_halfwidth=True))
    assert out == "abc\ndef\n　ghi"   # 全角保留
    assert stats["leading"] == 2


def test_leading_include_fullwidth():
    out, stats = clean_text(
        "  abc\n　def\n　  ghi", _only(strip_leading_fullwidth=True))
    assert out == "abc\ndef\nghi"
    assert stats["leading"] == 3


def test_leading_skips_title():
    text = "  第一章 开端\n  正文一段。"
    out, stats = clean_text(text, _only(strip_leading_halfwidth=True))
    assert out == "  第一章 开端\n正文一段。"
    assert stats["leading"] == 1


def test_leading_skips_all_title_variants():
    text = "\n".join([
        "  第一章 开端",
        "  序章",
        "  楔子",
        "  尾声",
        "  番外 一",
        "  前言",
        "  正文",
    ])
    out, stats = clean_text(text, _only(strip_leading_halfwidth=True))
    assert out.endswith("正文")           # 正文被清理
    assert "  序章" in out                # 标题全部豁免
    assert "  楔子" in out
    assert stats["leading"] == 1


def test_leading_idempotent():
    out, stats = clean_text("abc\n", _only(strip_leading_halfwidth=True))
    assert out == "abc\n"
    assert "leading" not in stats


# ---------- 空行 ----------

def test_blanks_keep_one_from_multiple():
    out, stats = clean_text(
        "a\n\n\n\nb", _only(collapse_blanks="keep_one"))
    assert out == "a\n\nb"
    assert stats["blanks"] == 2


def test_blanks_keep_two():
    out, stats = clean_text(
        "a\n\n\n\n\nb", _only(collapse_blanks="keep_two"))
    assert out == "a\n\n\nb"
    assert stats["blanks"] == 2


def test_blanks_remove_all():
    out, stats = clean_text(
        "a\n\nb\n\n\nc", _only(collapse_blanks="remove_all"))
    assert out == "a\nb\nc"
    assert stats["blanks"] == 3


def test_blanks_keep_all_no_change():
    text = "a\n\n\n\nb"
    out, stats = clean_text(text, _only(collapse_blanks="keep_all"))
    assert out == text
    assert "blanks" not in stats


def test_blanks_keep_one_inserts_nothing_when_missing():
    # 只有非空行、没有多余空行时不做多余插入
    text = "a\nb\nc"
    out, stats = clean_text(text, _only(collapse_blanks="keep_one"))
    assert out == text
    assert "blanks" not in stats


# ---------- 不可见字符 ----------

def test_invisible_basic():
    text = "a\u200bb\ufeffc\u00a0d\u00ade\u200cf"
    out, stats = clean_text(text, _only(remove_invisible=True))
    assert out == "abcdef"
    assert stats["invisible"] == 5


def test_invisible_zero_when_none():
    out, stats = clean_text("abc", _only(remove_invisible=True))
    assert out == "abc"
    assert "invisible" not in stats


# ---------- 连续标点 ----------

def test_punct_collapse_period():
    out, stats = clean_text(
        "你好。。。\n什么？？？\n等等，，，",
        _only(collapse_punctuation=True))
    # 。和，合并；？保留
    assert out == "你好。\n什么？？？\n等等，"
    assert stats["punct_collapsed"] == 2


def test_punct_keeps_ellipsis():
    # 「……」不是 。的连续，不动
    out, stats = clean_text(
        "他说……好。", _only(collapse_punctuation=True))
    assert out == "他说……好。"
    assert "punct_collapsed" not in stats


def test_punct_mixed_different():
    # 不同标点相邻不合并
    out, stats = clean_text("。。，。，", _only(collapse_punctuation=True))
    assert out == "。，。，"
    assert stats["punct_collapsed"] == 1


# ---------- 行号残留 ----------

def test_line_numbers_basic():
    text = "1. 第一行\n12. 第二行\n3、第三行\n正文一行"
    out, stats = clean_text(text, _only(strip_line_numbers=True))
    assert out == "第一行\n第二行\n第三行\n正文一行"
    assert stats["line_numbers"] == 3


def test_line_numbers_does_not_touch_body():
    # 正文里出现的 2023. 不该被当行号
    text = "他在 2023. 那年离开。"
    out, stats = clean_text(text, _only(strip_line_numbers=True))
    assert out == text
    assert "line_numbers" not in stats


def test_line_numbers_does_not_touch_year_start():
    # 行首是 "2023 年"，没有点号，不该被删
    text = "2023 年的秋天，他回来了。"
    out, stats = clean_text(text, _only(strip_line_numbers=True))
    assert out == text


# ---------- 复制残留 ----------

def test_artifacts_basic():
    text = "正文一段。\n[图片]\n[插图]\n<!-- 注释 -->\n正文二段。"
    out, stats = clean_text(text, _only(strip_paste_artifacts=True))
    assert "[图片]" not in out
    assert "[插图]" not in out
    assert "<!--" not in out
    assert stats["artifacts"] == 3


def test_artifacts_code_fence():
    text = "正文。\n```python\nprint('x')\n```\n结束。"
    out, stats = clean_text(text, _only(strip_paste_artifacts=True))
    assert "```python" not in out
    assert "```" not in out
    # 代码内容保留（用户自己决定要不要删）
    assert "print('x')" in out
    assert stats["artifacts"] == 2


def test_artifacts_multiline_comment():
    text = "前。\n<!--\n多行\n注释\n-->\n后。"
    out, stats = clean_text(text, _only(strip_paste_artifacts=True))
    assert "多行" not in out
    assert "注释" not in out
    assert stats["artifacts"] == 1


# ---------- 组合 ----------

def test_combination_typical_paste():
    """典型场景：从网页复制的一段，带各种脏东西。"""
    text = (
        "  第一章 起点  \n"
        "\n\n\n"
        "[图片]\n"
        "  他走进房间。　\n"
        "\t他看了看四周。。。\n"
        "1. 他叹了口气。\n"
    )
    out, stats = clean_text(text, CleanParams(
        strip_trailing=True,
        strip_leading_halfwidth=True,
        strip_leading_fullwidth=False,
        collapse_blanks="keep_one",
        remove_invisible=True,
        collapse_punctuation=True,
        strip_line_numbers=True,
        strip_paste_artifacts=True,
    ))
    lines = out.split("\n")
    # 标题行不被处理（"第一章"前有两个空格保留）
    assert lines[0] == "  第一章 起点"
    # 多余空行被压到 1 个
    assert lines[1] == ""
    assert lines[2] == "他走进房间。　"      # 行首半角删，行尾全角保留
    assert lines[3] == "他看了看四周。"       # 行首 tab 删，连续句号合并
    assert lines[4] == "他叹了口气。"         # 行号删除


def test_format_stats_empty():
    assert format_clean_stats({}) == "无改动"


def test_format_stats_full():
    s = format_clean_stats({
        "trailing": 12, "leading": 8, "blanks": 3,
        "invisible": 5, "punct_collapsed": 2,
        "line_numbers": 1, "artifacts": 4,
    })
    assert "12 处行尾空白" in s
    assert "8 处行首空白" in s
    assert "3 个空行" in s
    assert "5 个不可见字符" in s
    assert "2 处连续标点" in s
    assert "1 处行号" in s
    assert "4 处复制残留" in s


# ---------- 边界 ----------

def test_empty_text():
    out, stats = clean_text("", CleanParams())
    assert out == ""
    assert stats == {}


def test_none_text():
    out, stats = clean_text(None, CleanParams())
    assert out == ""
    assert stats == {}


def test_crlf_normalized():
    out, _ = clean_text("a\r\nb\rc", _only())
    assert out == "a\nb\nc"


def test_idempotent_on_clean_text():
    """对已经清洗过的文本再次清洗，不应产生变化。"""
    text = "第一章 开端\n\n他走进房间。\n\n他看了看四周。\n"
    p = CleanParams()
    once, _ = clean_text(text, p)
    twice, stats = clean_text(once, p)
    assert once == twice
    
 