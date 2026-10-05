# -*- coding: utf-8 -*-
"""墨灵试读纯逻辑：截断/统计/prompt 构造/JSON 解析/速记。"""
import json

from utils import beta_reading as br


def test_clip_short_text_untouched():
    assert br.clip_chapter_text("短正文") == "短正文"


def test_clip_long_text_head_tail_with_marker():
    text = "开" * 3000 + "中" * 5000 + "尾" * 800
    clipped = br.clip_chapter_text(text)
    assert len(clipped) < len(text)
    assert "中略" in clipped
    assert clipped.startswith("开") and clipped.endswith("尾")
    assert br.estimate_chars(text) == len(clipped)


def test_local_stats_basic():
    stats = br.local_stats("第一段。\n他说：“对白内容在这里。”\n最后一段未完")
    assert stats["paras"] == 3
    assert stats["chars"] > 0
    assert 0.0 <= stats["dialogue_ratio"] <= 1.0
    assert stats["opening"] and stats["ending"]


def test_chapter_messages_carry_prev_digest():
    msgs = br.build_chapter_messages(
        "书", 2, 5, "风起", "正文", prev_digest="第1章《初遇》：追读欲4")
    assert msgs[0]["role"] == "system" and "墨灵" in msgs[0]["content"]
    assert "第 2 章" in msgs[1]["content"]
    assert "初遇" in msgs[1]["content"]

    first = br.build_chapter_messages("书", 1, 5, "初遇", "正文", "")
    assert "第一章" in first[1]["content"]


def test_parse_chapter_response_lenient_json():
    raw = "```json\n" + json.dumps({
        "want": 5, "hook": "血案开场", "ending": "陌生人敲门",
        "feeling": "想追更", "drop": "无",
    }, ensure_ascii=False) + "\n```"
    r = br.parse_chapter_response(raw)
    assert r["want"] == 5 and r["hook"] == "血案开场"
    assert r["drop"] == "无"


def test_parse_chapter_response_clamps_and_garbage():
    assert br.parse_chapter_response("not json") == {}
    assert br.parse_chapter_response('{"want": 99}')["want"] == 5
    assert br.parse_chapter_response('{"want": 0}')["want"] == 1


def test_parse_summary_response():
    s = br.parse_summary_response(json.dumps({
        "verdict": "开篇稳",
        "strengths": ["a", "b", "c", "d"],   # 超过 3 条要截断
        "drop_points": ["第2章信息倾倒"],
        "rhythm": "前紧后松",
        "advice": ["拆对话"],
    }, ensure_ascii=False))
    assert s["verdict"] == "开篇稳"
    assert len(s["strengths"]) == 3
    assert s["drop_points"][0].startswith("第2章")


def test_digest_line_contains_drop():
    line = br.digest_line(3, "转折", {
        "want": 2, "hook": "平", "ending": "平",
        "feeling": "闷", "drop": "节奏拖"})
    assert "追读欲2" in line and "节奏拖" in line
