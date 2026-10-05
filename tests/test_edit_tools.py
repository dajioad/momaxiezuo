# -*- coding: utf-8 -*-
"""编辑工具箱 - 段落处理纯函数回归测试（pytest）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.edit_tools.paragraph import (
    ParagraphParams, process_paragraphs, count_chars,
    is_title_line, split_long_paragraph, merge_short_lines,
)


def test_split_long_paragraph():
    para = "这是一个很长的段落。" * 20
    parts = split_long_paragraph(para, threshold=100, min_keep=40,
                                 break_chars="。！？…")
    assert len(parts) >= 2
    for p in parts:
        assert count_chars(p) <= 180


def test_split_no_punctuation():
    parts = split_long_paragraph("a" * 200, threshold=100, min_keep=40,
                                 break_chars="。！？…")
    assert len(parts) == 1


def test_merge_short_lines():
    lines = ["短句一。", "短句二。", "短句三。", "这是一段很长的正文内容。" * 20]
    out = merge_short_lines(lines, threshold=20, max_merged=200,
                            skip_dialogue=True, skip_titles=True)
    assert len(out) == 2
    assert out[0] == "短句一。短句二。短句三。"


def test_merge_skips_dialogue():
    lines = ['"你好。"', '"好久不见。"', "他是这样说的。"]
    out = merge_short_lines(lines, threshold=20, max_merged=200,
                            skip_dialogue=True, skip_titles=True)
    assert len(out) == 3


def test_title_line_guard():
    assert is_title_line("第一章 起")
    assert is_title_line("第 12 回 缘起")
    assert is_title_line("序章")
    assert not is_title_line("他在第一章里说的话。")


def test_process_blank_one():
    text = "段落 A\n\n\n段落 B\n\n\n\n段落 C\n"
    params = ParagraphParams(
        split_enabled=False, merge_enabled=False,
        blank_mode="one", indent_mode="keep")
    new, _stats = process_paragraphs(text, params)
    assert new == "段落 A\n\n段落 B\n\n段落 C\n"


def test_process_indent_skips_title():
    text = "段落一\n\n段落二\n\n第三章 标题\n"
    params = ParagraphParams(
        split_enabled=False, merge_enabled=False,
        blank_mode="keep", indent_mode="add")
    new, stats = process_paragraphs(text, params)
    assert "　　段落一" in new
    assert "　　段落二" in new
    assert "第三章 标题" in new and "　　第三章" not in new
    assert stats["indent_added"] == 2


def test_full_pipeline():
    text = ("这是一个很长的段落。" * 15) + "\n下一段。\n"
    params = ParagraphParams(
        split_enabled=True, split_threshold=80, split_min_keep=30,
        merge_enabled=False, blank_mode="one", indent_mode="add")
    _new, stats = process_paragraphs(text, params)
    assert stats["split"] >= 1
    assert stats["indent_added"] >= 2
