# -*- coding: utf-8 -*-
"""AI 人物关系响应解析：JSON 容错、同义词归一、对称边去重。"""
import json

from utils.relation_extract import parse_response


def test_parse_basic_json():
    raw = json.dumps({"relations": [
        {"from": "方知有", "rel": "弟子", "to": "李长生",
         "evidence": "方知有是李长生的弟子"},
    ]}, ensure_ascii=False)
    rels = parse_response(raw)
    assert len(rels) == 1
    assert rels[0]["from"] == "方知有"


def test_parse_code_fenced_and_synonym_normalization():
    raw = "```json\n" + json.dumps({"relations": [
        {"from": "甲", "rel": "师傅", "to": "乙", "evidence": "师傅"},
    ]}) + "\n```"
    rels = parse_response(raw)
    assert rels and rels[0]["rel"] == "师父"


def test_symmetric_edge_dedup():
    raw = json.dumps({"relations": [
        {"from": "甲", "rel": "好友", "to": "乙", "evidence": "a"},
        {"from": "乙", "rel": "好友", "to": "甲", "evidence": "b"},
    ]}, ensure_ascii=False)
    rels = parse_response(raw)
    assert len(rels) == 1


def test_garbage_returns_empty():
    assert parse_response("") == []
    assert parse_response("模型跑飞了的一段白话，没有 JSON") == []
