# utils/publish_check.py
"""墨码写作 - 发布前体检（纯逻辑，无 Qt 依赖）

一键跑完 4 项检查：
1. 排版格式  —— 复用 format_engine，检测有多少处需要规范
2. 校对      —— 复用 proofreader，检测错别字 / 敏感词 / 标点
3. 追读体检  —— 复用 readability，检测章节可读性
4. 一致性    —— 复用 consistency，检测同人不同名（全书级）

每个检查返回 dict：
    {
      "status": "ok" | "warn" | "bad" | "skipped",
      "msg":    "给用户看的一句话（10-20 字）",
      ...各自专有字段
    }

纯检测，不修改任何文件。
"""
from __future__ import annotations

from typing import List, Tuple, Dict, Any

from .format_engine import (
    format_text, BLANK_KEEP_ONE, INDENT_FULL2,
)
from .proofreader import check_text
from .readability import check_chapter
from .consistency import scan_work, load_ignored


STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_BAD = "bad"
STATUS_SKIPPED = "skipped"


# 排版体检用这一套规则：只做"硬规范"（缩进 / 空行 / 行尾空格），
# 不做标点 / 引号转换（那些是风格偏好，不同平台要求不同，不该强制）
_FORMAT_RULES = {
    "punct": False,
    "fullwidth_digit": False,
    "quote": False,
    "single_quote": False,
    "ellipsis": False,
    "dash": False,
    "cn_space": False,
    "trailing": True,
    "indent": True,
    "title_blank": True,
    "blank": True,
}


def check_format_all(texts: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    """检查排版。texts: [(chapter_id, title, text), ...]"""
    total = 0
    problem_chapters = []

    for cid, title, text in texts:
        if not text.strip():
            continue
        _, stats = format_text(
            text, rules=_FORMAT_RULES,
            blank_mode=BLANK_KEEP_ONE,
            indent_style=INDENT_FULL2)
        n = sum(stats.values())
        if n > 0:
            total += n
            problem_chapters.append({
                "chapter_id": cid,
                "title": title,
                "total": n,
                "stats": stats,
            })

    if total == 0:
        return {
            "status": STATUS_OK, "total": 0,
            "chapters": [], "msg": "格式规范，无需调整",
        }
    if total <= 20:
        status = STATUS_WARN
        msg = f"发现 {total} 处可优化（较少）"
    else:
        status = STATUS_BAD
        msg = f"发现 {total} 处格式需要规范"
    return {
        "status": status, "total": total,
        "chapters": problem_chapters, "msg": msg,
    }


def check_proofread_all(texts: List[Tuple[str, str, str]],
                         data_root: str = "") -> Dict[str, Any]:
    """检查错别字 / 敏感词 / 标点。"""
    cats = {
        "敏感词": 0, "错别字": 0, "自定义错别字": 0,
        "标点": 0, "引号配对": 0, "格式": 0, "用语提示": 0,
    }
    chapter_results = []

    for cid, title, text in texts:
        if not text:
            continue
        items = check_text(text, data_root)
        if not items:
            continue
        for r in items:
            r["chapter_id"] = cid
            r["chapter_title"] = title
            cat = r.get("category", "")
            if "敏感词" in cat:
                cats["敏感词"] += 1
            elif "自定义" in cat:
                cats["自定义错别字"] += 1
            elif "错别字" in cat:
                cats["错别字"] += 1
            elif "标点" in cat:
                cats["标点"] += 1
            elif "格式" in cat:
                cats["格式"] += 1
            elif "引号" in cat:
                cats["引号配对"] += 1
            else:
                cats["用语提示"] += 1
        chapter_results.append({
            "chapter_id": cid, "title": title, "items": items,
        })

    total = sum(cats.values())
    if total == 0:
        return {
            "status": STATUS_OK, "total": 0, "cats": cats,
            "chapters": [], "msg": "未发现错别字或敏感词",
        }

    if cats["敏感词"] > 0:
        status = STATUS_BAD
    else:
        status = STATUS_WARN

    parts = []
    for k in ("敏感词", "错别字", "自定义错别字",
              "标点", "引号配对", "格式", "用语提示"):
        if cats[k] > 0:
            parts.append(f"{k} {cats[k]}")

    return {
        "status": status, "total": total, "cats": cats,
        "chapters": chapter_results, "msg": " · ".join(parts),
    }


def check_readability_all(texts: List[Tuple[str, str, str]]) -> Dict[str, Any]:
    """检查追读。多章取最低分。"""
    results = []
    for cid, title, text in texts:
        if not text.strip():
            continue
        report = check_chapter(text)
        results.append({
            "chapter_id": cid,
            "chapter_title": title,
            "score": report.total,
            "level": report.level,
            "report": report,
        })

    if not results:
        return {
            "status": STATUS_OK, "msg": "无内容可检查", "results": [],
        }

    worst = min(results, key=lambda r: r["score"])
    score = worst["score"]

    if score >= 85:
        status = STATUS_OK
    elif score >= 70:
        status = STATUS_WARN
    else:
        status = STATUS_BAD

    if len(results) == 1:
        msg = f"{score} 分（{worst['level']}）"
    else:
        msg = f"最低 {score} 分 · 《{worst['chapter_title']}》"

    return {
        "status": status,
        "worst_score": score,
        "worst_level": worst["level"],
        "worst_chapter_id": worst["chapter_id"],
        "worst_chapter_title": worst["chapter_title"],
        "results": results, "msg": msg,
    }


def check_consistency_all(work, data_root: str = "") -> Dict[str, Any]:
    """检查全书一致性（同人不同名）。"""
    try:
        ignored = load_ignored(work)
        pairs = scan_work(work, ignored_pairs=ignored)
    except Exception:
        pairs = []

    total = len(pairs)
    if total == 0:
        return {
            "status": STATUS_OK, "total": 0, "pairs": [],
            "msg": "未发现同人不同名",
        }

    high = sum(1 for p in pairs
               if getattr(p, "confidence", "medium") == "high")
    if high > 0:
        status = STATUS_BAD
        msg = f"发现 {total} 组疑似同人不同名（{high} 组高置信度）"
    else:
        status = STATUS_WARN
        msg = f"发现 {total} 组疑似同人不同名"

    return {
        "status": status, "total": total, "high": high,
        "pairs": pairs, "msg": msg,
    }


def overall_status(result: dict) -> str:
    """汇总 4 项检查的整体状态。skipped 不参与评估。"""
    statuses = []
    for key in ("format", "proofread", "readability", "consistency"):
        s = result.get(key, {}).get("status", STATUS_OK)
        if s != STATUS_SKIPPED:
            statuses.append(s)
    if STATUS_BAD in statuses:
        return STATUS_BAD
    if STATUS_WARN in statuses:
        return STATUS_WARN
    return STATUS_OK