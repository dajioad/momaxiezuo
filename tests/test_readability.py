# -*- coding: utf-8 -*-
"""追读体检纯规则回归（10 维评分契约 + 空文本兜底 + 出戏穿帮 + AI 解析）。"""
import json

from utils.readability import (
    check_chapter,
    build_ai_prompt,
    parse_ai_response,
    normalize_ai_data,
)


def test_empty_text_zero_report():
    r = check_chapter("")
    assert r.total == 0 and r.level == "差"
    assert r.items == [] and r.stats["chars"] == 0


def test_report_has_ten_dimensions():
    text = "他猛地推开门。\n\n“你来了。”她低声说，眼神复杂。\n\n" * 30
    r = check_chapter(text)
    assert len(r.items) == 10
    # 十项满分之和必须严格等于 100（权重契约）
    assert sum(it.max_score for it in r.items) == 100
    assert 0 <= r.total <= 100
    assert r.level in ("优", "良", "中", "差")
    assert r.stats["chars"] > 0
    # 每项分数在合理区间，且带可展示信息
    for it in r.items:
        assert 0 <= it.score <= it.max_score
        assert it.label and it.message


def test_level_boundaries():
    assert check_chapter("某章正文" * 200).level in ("优", "良", "中", "差")


# ---------- 出戏穿帮 ----------

def _meta_item(r):
    return next(i for i in r.items if i.key == "meta_break")


def test_normal_story_no_meta_break():
    text = (
        "林墨推开门，寒风卷着雪沫子灌进来。\n\n"
        "“你来了。”老者没有回头，只把茶盏推到桌边。\n\n"
        "院外马蹄声密，他攥紧了袖中那封信，低声道：“城里出事了。”\n\n"
    ) * 8
    r = check_chapter(text)
    m = _meta_item(r)
    assert m.score == 8 and m.level == "ok"
    assert r.level in ("优", "良")   # 无穿帮不被封顶


def test_meta_break_strong_signals_zero_score():
    text = (
        "凌晨三点她站在风里哭，因为作者是这么安排的。\n\n"
        "他下一章就不会再出现了，读者也不记得他。\n\n"
        "剧情需要她可怜，她就得可怜。以上情节一律作废。\n\n"
        "主角想坐什么车就能坐什么车。\n\n"
    ) * 3
    r = check_chapter(text)
    m = _meta_item(r)
    assert m.score == 0 and m.level == "bad"
    assert m.positions                    # 命中段落可定位
    # 一票封顶：形式分再高也不是优/良
    assert r.level in ("中", "差")


def test_meta_break_weak_only_is_warn_not_zero():
    # 单个弱信号（句中"第三章"）不应重罚
    text = ("他翻到第三章规矩，逐条看了一遍，指尖敲着桌面。\n\n") * 12
    r = check_chapter(text)
    m = _meta_item(r)
    assert 5 <= m.score < 10


def test_payoff_zero_no_checkmark():
    # 有冲突词但无爽点词：不应给 ok/✓
    text = ("他怒喝一声，拔刀撞了过去，伤口渗血，场面惨烈。\n\n") * 30
    r = check_chapter(text)
    p = next(i for i in r.items if i.key == "payoff")
    assert p.level != "ok"
    assert "爽点" in p.message and "✓" not in p.message


# ---------- AI 深度体检：prompt + 解析 ----------

def test_ai_prompt_contains_six_types_and_chapter_text():
    p = build_ai_prompt("她不知道自己为什么哭。" * 10, "作品：测试")
    for t in ("逻辑穿帮", "人设崩", "动机牵强", "套路陈词", "对白出戏", "情绪虚假"):
        assert t in p
    assert "作品：测试" in p
    assert "issues" in p and "drop_warning" in p and "rewrite_demo" in p
    # 旧设计的区块不应再出现在 prompt 里
    assert "老白" not in p and "segment_scores" not in p


def test_ai_parse_new_format():
    raw = json.dumps({
        "verdict": "情绪按头，逻辑全靠作者安排",
        "issues": [
            {"severity": "高", "type": "逻辑穿帮",
             "quote": "转身走进了一辆劳斯莱斯",
             "problem": "前方无车，凭空上车",
             "fix": "提前一句写车停在道旁"},
            {"severity": "中", "type": "情绪虚假",
             "quote": "她不知道自己为什么哭",
             "problem": "悲伤没有来由", "fix": "补一个触发点"},
            {"severity": "奇怪", "type": "瞎编的类型",
             "problem": "越界字段", "fix": "x"},
            {"severity": "低"},  # problem/fix 全缺 → 丢弃
        ],
        "drop_warning": {"point": "打胎对话处", "reason": "男主反应无人味"},
        "rewrite_demo": {"original": "哦。打掉吧。",
                         "rewritten": "他喉结滚动：“……打掉。”",
                         "why": "用动作藏情绪"},
    }, ensure_ascii=False)
    d = parse_ai_response(raw)
    assert d["verdict"].startswith("情绪按头")
    assert len(d["issues"]) == 3
    assert d["issues"][0]["severity"] == "高"
    assert d["issues"][0]["type"] == "逻辑穿帮"
    # 越界枚举归一
    assert d["issues"][2]["severity"] == "中"
    assert d["issues"][2]["type"] == "其他"
    assert d["drop_warning"]["point"] == "打胎对话处"
    assert d["rewrite_demo"]["rewritten"].endswith("”")
    # 新结构不含任何旧字段
    assert "reader_types" not in d and "suggestions" not in d


def test_ai_parse_issues_capped_at_five():
    raw = json.dumps({
        "verdict": "v",
        "issues": [{"severity": "低", "problem": f"问题{i}", "fix": ""}
                   for i in range(8)],
    }, ensure_ascii=False)
    assert len(parse_ai_response(raw)["issues"]) == 5


def test_ai_parse_markdown_fenced_json():
    raw = '```json\n{"verdict": "还行", "issues": []}\n```'
    d = parse_ai_response(raw)
    assert d["verdict"] == "还行" and d["issues"] == []


def test_ai_parse_garbage_returns_empty():
    assert parse_ai_response("我觉得这章写得不错，但问题是……") == {}


def test_ai_normalize_legacy_report_shape():
    """质量中心里的旧版落盘 dict（已解析过的旧结构）也要能看。"""
    legacy = {
        "verdict": "旧总评",
        "reader_simulation": {
            "read_until_percent": 60,
            "drop_point": "第三章开头",
            "drop_reason": "太拖",
            "retention_feeling": "一般",
        },
        "suggestions": [
            {"priority": "高", "action": "删掉环境描写", "target": "开头三段"},
            "加强对白",
        ],
        "rewrite_demo": {"original": "甲", "rewritten": "乙", "why": "更紧"},
    }
    d = normalize_ai_data(legacy)
    assert d["verdict"] == "旧总评"
    assert len(d["issues"]) == 2
    assert d["issues"][0]["type"] == "其他"
    assert d["issues"][0]["quote"] == "开头三段"
    assert d["issues"][1]["problem"] == "加强对白"
    assert d["drop_warning"] == {"point": "第三章开头", "reason": "太拖"}
    assert d["rewrite_demo"]["why"] == "更紧"
    # 幂等：再过一遍结构不变
    assert normalize_ai_data(d)["issues"][0]["problem"] == "删掉环境描写"


def test_ai_normalize_legacy_drop_point_none():
    d = normalize_ai_data({
        "reader_simulation": {"drop_point": "无", "drop_reason": ""},
    })
    assert d["drop_warning"] == {}
