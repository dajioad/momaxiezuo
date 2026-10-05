# -*- coding: utf-8 -*-
"""质量报告持久化：存取 / 列表 / 删除 / 裁剪 / 体检报告序列化。"""
import os
from datetime import datetime, timedelta

import pytest

from utils import quality_reports as qr
from utils.readability import check_chapter


def _beta(root, n, title=None, when=None):
    rec = qr.make_record(
        qr.KIND_BETA, title or f"试读 {n}",
        {"entries": [{"index": 1, "title": "第一章"}],
         "summary": {"verdict": "稳"}},
        now=when)
    return qr.save_report(root, rec)


def _readability(root, cid, n, when=None, score=None):
    text = ("他猛地推开门。\n\n“你来了。”她低声说。\n\n") * 20
    rep = check_chapter(text)
    rec = qr.make_record(
        qr.KIND_READABILITY,
        f"体检 · 第{cid}章",
        {"report": qr.report_to_dict(rep), "ai": None},
        chapter_id=str(cid), chapter_title=f"第{cid}章",
        score=score if score is not None else rep.total,
        level=rep.level, now=when)
    return qr.save_report(root, rec)


def test_save_list_load_roundtrip(tmp_path):
    rec = _beta(tmp_path, 1)
    got = qr.load_report(tmp_path, rec["id"])
    assert got is not None and got["id"] == rec["id"]
    assert got["data"]["summary"]["verdict"] == "稳"
    assert len(qr.list_reports(tmp_path)) == 1


def test_list_sorted_newest_first(tmp_path):
    base = datetime(2026, 9, 1, 12, 0, 0)
    _beta(tmp_path, 1, when=base)
    _beta(tmp_path, 2, when=base + timedelta(days=2))
    _beta(tmp_path, 3, when=base + timedelta(days=1))
    titles = [r["title"] for r in qr.list_reports(tmp_path)]
    assert titles == ["试读 2", "试读 3", "试读 1"]


def test_load_missing_and_bad_id(tmp_path):
    assert qr.load_report(tmp_path, "qr_nope") is None
    assert qr.load_report(tmp_path, "../../etc/passwd") is None
    assert qr.delete_report(tmp_path, "../../x") is False
    with pytest.raises(ValueError):
        qr.save_report(tmp_path, {"id": "../escape", "kind": "x", "data": {}})


def test_corrupt_file_skipped(tmp_path):
    _beta(tmp_path, 1)
    d = qr.reports_dir(tmp_path)
    with open(os.path.join(d, "qr_broken_0000.json"), "w",
              encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON")
    recs = qr.list_reports(tmp_path)
    assert len(recs) == 1


def test_delete(tmp_path):
    rec = _beta(tmp_path, 1)
    assert qr.delete_report(tmp_path, rec["id"]) is True
    assert qr.list_reports(tmp_path) == []
    assert qr.delete_report(tmp_path, rec["id"]) is False


def test_prune_beta_keep_limit(tmp_path):
    base = datetime(2026, 9, 1, 9, 0, 0)
    for i in range(qr.BETA_KEEP + 5):
        _beta(tmp_path, i, when=base + timedelta(hours=i))
    removed = qr.prune_reports(tmp_path)
    assert len(removed) == 5
    remain = qr.list_reports(tmp_path)
    assert len(remain) == qr.BETA_KEEP
    # 留下的是最新的（标题编号最大）
    assert "试读 24" in [r["title"] for r in remain]
    assert "试读 0" not in [r["title"] for r in remain]


def test_prune_readability_per_chapter(tmp_path):
    base = datetime(2026, 9, 1, 9, 0, 0)
    for i in range(8):
        _readability(tmp_path, cid=3, n=i, when=base + timedelta(hours=i))
    for i in range(2):
        _readability(tmp_path, cid=9, n=i, when=base + timedelta(days=1,
                                                                  hours=i))
    qr.prune_reports(tmp_path)
    cid3 = [r for r in qr.list_reports(tmp_path)
            if r.get("chapter_id") == "3"]
    cid9 = [r for r in qr.list_reports(tmp_path)
            if r.get("chapter_id") == "9"]
    assert len(cid3) == qr.READABILITY_PER_CHAPTER
    assert len(cid9) == 2


def test_report_serialize_roundtrip(tmp_path):
    text = ("他猛地推开门。\n\n“你来了。”她低声说，眼神复杂。\n\n") * 30
    rep = check_chapter(text)
    restored = qr.report_from_dict(qr.report_to_dict(rep))
    assert restored.total == rep.total
    assert restored.level == rep.level
    assert len(restored.items) == 10
    assert restored.stats["chars"] == rep.stats["chars"]
    # 明细与曲线都应完整恢复
    assert restored.items[0].key == rep.items[0].key
    assert restored.items[0].max_score == rep.items[0].max_score
    assert len(restored.curve) == len(rep.curve)


def test_persisted_readability_loads_back(tmp_path):
    rec = _readability(tmp_path, cid=7, n=1)
    got = qr.load_report(tmp_path, rec["id"])
    rep = qr.report_from_dict(got["data"]["report"])
    assert rep is not None and 0 <= rep.total <= 100
