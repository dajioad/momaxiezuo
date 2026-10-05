# -*- coding: utf-8 -*-
"""D-L0 本地人物关系规则提取回归。"""
from utils.relation_rules import (
    build_name_alias, extract_local, SYMMETRIC_RELS,
)

CHARS = [{"name": n} for n in
         ["方知有", "李长生", "苏晚棠", "沈无咎", "贾宝玉", "贾政", "林墨"]]


def _edges(text, chars=None):
    return {(r["from"], r["rel"], r["to"]) for r in
            extract_local(text, chars or CHARS)}


def test_build_alias_skips_single_char():
    am = build_name_alias([{"name": "方知有", "aliases": ["他", "小方"]}])
    assert "小方" in am and am["小方"] == "方知有"
    assert "他" not in am  # 单字别名误报率太高


def test_template_a_is_b_relation():
    edges = _edges("方知有是李长生的弟子。")
    assert ("方知有", "弟子", "李长生") in edges


def test_template_b_relation_is_a():
    edges = _edges("贾宝玉的父亲是贾政，此事尽人皆知。")
    assert ("贾宝玉", "父亲", "贾政") in edges


def test_template_appositive_and_master():
    edges = _edges("那李长生的弟子沈无咎早已叛出师门。林墨拜李长生为师。")
    assert ("沈无咎", "弟子", "李长生") in edges
    assert ("林墨", "师父", "李长生") in edges


def test_symmetric_and_plural_enumeration():
    text = (
        "方知有和苏晚棠是同门。"
        "方知有、苏晚棠、沈无咎三人都是李长生的弟子。"
    )
    edges = _edges(text)
    assert ("方知有", "同门", "苏晚棠") in edges
    for n in ("方知有", "苏晚棠", "沈无咎"):
        assert (n, "弟子", "李长生") in edges


def test_no_false_positive_on_plain_cooccurrence():
    # 只是同窗读书的叙述，不应凭空产出关系边
    edges = _edges("三人曾在青锋山读书。")
    assert edges == set()


def test_synonym_normalization():
    edges = _edges("贾宝玉的爹是贾政。",
                   [{"name": "贾宝玉"}, {"name": "贾政"}])
    assert ("贾宝玉", "父亲", "贾政") in edges


def test_output_contract():
    rels = extract_local("方知有是李长生的弟子。", CHARS)
    assert rels and all(
        set(["from", "rel", "to", "evidence", "source"]) <= set(r)
        for r in rels
    )
    assert all(r["source"] == "local" for r in rels)
    assert "好友" in SYMMETRIC_RELS and "弟子" not in SYMMETRIC_RELS
    assert len(extract_local("方知有是李长生的弟子。" * 200, CHARS)) <= 25
