# utils/relation_rules.py
"""墨码写作 - 人物关系本地规则提取（L0，零 token、无 Qt、无网络）。

D 批次三层方案的第一层：
- L0（本模块）：用人物卡名词库（名字+别名）+ 中文句式规则，
  从章节正文里抠"白纸黑字写明"的关系。不需要 AI，不花 token。
- L1：AI 按章增量提取（utils/relation_extract.py），补 L0 抓不到的
  跨句推断。
- 全书扫描：对每章依次跑 L0+L1，结果聚合去重后二次确认。

输出结构与 relation_extract.parse_response 完全一致：
    [{"from": str, "rel": str, "to": str, "evidence": str,
      "source": "local"}, ...]
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# 口语/古风称谓 → 标准关系词（与 relation_extract 的词表对齐）
_REL_SYNONYMS = {
    # 师门
    "师傅": "师父", "老师": "师父", "恩师": "师父", "师尊": "师父",
    "师父": "师父",
    "徒弟": "弟子", "弟子": "弟子", "学生": "弟子", "徒儿": "弟子",
    "师兄": "同门", "师姐": "同门", "师弟": "同门", "师妹": "同门",
    "师兄弟": "同门", "师姐妹": "同门", "同窗": "同门", "同门": "同门",
    # 血亲长辈
    "爹": "父亲", "爹爹": "父亲", "阿爹": "父亲", "父亲": "父亲",
    "父皇": "父亲", "父王": "父亲", "爹地": "父亲",
    "娘": "母亲", "娘亲": "母亲", "母亲": "母亲",
    "母后": "母亲", "母妃": "母亲",
    "祖父": "祖父", "爷爷": "祖父",
    "祖母": "祖母", "奶奶": "祖母",
    # 平辈
    "哥": "哥哥", "哥哥": "哥哥", "兄长": "哥哥", "大哥": "哥哥",
    "二哥": "哥哥", "三哥": "哥哥",
    "姐": "姐姐", "姐姐": "姐姐", "大姐": "姐姐", "二姐": "姐姐",
    "弟": "弟弟", "弟弟": "弟弟",
    "妹": "妹妹", "妹妹": "妹妹",
    # 晚辈
    "儿子": "儿子", "孩儿": "儿子", "犬子": "儿子",
    "女儿": "女儿", "闺女": "女儿",
    "孙子": "孙子", "孙女": "孙女",
    # 婚恋
    "妻子": "妻子", "夫人": "妻子", "娘子": "妻子", "妻": "妻子",
    "夫君": "丈夫", "相公": "丈夫", "丈夫": "丈夫",
    "未婚夫": "未婚夫", "未婚妻": "未婚妻",
    # 友敌
    "挚友": "好友", "好友": "好友", "朋友": "好友", "知己": "好友",
    "闺蜜": "好友",
    "宿敌": "敌人", "仇人": "敌人", "敌人": "敌人", "死敌": "敌人",
    "对手": "敌人",
}

# 可直接跟在"X 和 Y 是…"后面的对称关系
_SYMMETRIC = {
    "同门": "同门", "同窗": "同门", "师兄弟": "同门",
    "好友": "好友", "朋友": "好友", "挚友": "好友", "闺蜜": "好友",
    "知己": "好友",
    "宿敌": "敌人", "仇人": "敌人", "敌人": "敌人", "死敌": "敌人",
    "对手": "敌人",
    "夫妻": "妻子", "夫妇": "妻子",
    "兄弟": "哥哥", "兄妹": "哥哥", "姐弟": "哥哥", "姐妹": "姐姐",
    "父子": "父亲", "父女": "父亲", "母子": "母亲", "母女": "母亲",
}

# 对称关系：同一条边反向算重复
SYMMETRIC_RELS = {"同门", "好友", "敌人", "妻子", "丈夫"}

_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
_MAX_EVIDENCE = 30
_MAX_RESULTS = 25


def build_name_alias(characters: List[dict]) -> Dict[str, str]:
    """从人物卡构造 {别名(含本名): 规范名}。长名优先匹配由调用方排序。"""
    alias_to_canonical: Dict[str, str] = {}
    for c in characters or []:
        canonical = (c.get("name") or "").strip()
        if not canonical:
            continue
        names = [canonical]
        aliases = c.get("aliases") or []
        if isinstance(aliases, list):
            names.extend(str(a).strip() for a in aliases if str(a).strip())
        elif isinstance(aliases, str) and aliases.strip():
            names.append(aliases.strip())
        for n in names:
            n = n.strip()
            # 单字别名误报率太高（"然""天"满地都是），跳过
            if len(n) < 2:
                continue
            # 后到的卡不覆盖先前的同名指向
            alias_to_canonical.setdefault(n, canonical)
    return alias_to_canonical


def _find_names(sentence: str, alias_map: Dict[str, str]
                ) -> List[Tuple[int, int, str]]:
    """句中出现的所有人名片段 (start, end, canonical)，非重叠、长名优先。"""
    spans: List[Tuple[int, int, str]] = []
    for alias, canonical in alias_map.items():
        start = 0
        while True:
            at = sentence.find(alias, start)
            if at < 0:
                break
            start = at + len(alias)
            spans.append((at, at + len(alias), canonical))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    picked: List[Tuple[int, int, str]] = []
    for s in spans:
        if any(not (s[1] <= p[0] or s[0] >= p[1]) for p in picked):
            continue  # 与已选（更长的）名字重叠
        picked.append(s)
    picked.sort()
    return picked


def _evidence_window(sentence: str, *names: str) -> str:
    """截取包含这些名字的 ≤30 字证据窗。"""
    s = sentence.strip()
    if len(s) <= _MAX_EVIDENCE:
        return s
    first = s.find(names[0]) if names else 0
    if first < 0:
        first = 0
    half = _MAX_EVIDENCE // 2
    lo = max(0, first - half)
    hi = min(len(s), lo + _MAX_EVIDENCE)
    lo = max(0, hi - _MAX_EVIDENCE)
    piece = s[lo:hi]
    if lo > 0:
        piece = "…" + piece
    if hi < len(s):
        piece = piece + "…"
    return piece


def extract_local(text: str, characters: List[dict]) -> List[dict]:
    """从章节正文提取规则能确定的人物关系。

    characters 为人物卡原始 dict 列表（用 name/aliases 限定名词库）。
    """
    if not text or not characters:
        return []
    alias_map = build_name_alias(characters)
    if not alias_map:
        return []
    rel_words = sorted(_REL_SYNONYMS.keys(), key=len, reverse=True)
    rel_alt = "|".join(re.escape(w) for w in rel_words)
    sym_words = sorted(_SYMMETRIC.keys(), key=len, reverse=True)
    sym_alt = "|".join(re.escape(w) for w in sym_words)

    out: List[dict] = []
    seen = set()

    def _add(frm: str, rel: str, to: str, sentence: str):
        if not frm or not to or frm == to:
            return
        key = (frm, rel, to)
        if key in seen:
            return
        if rel in SYMMETRIC_RELS:
            sym = (tuple(sorted([frm, to])), rel)
            if sym in seen:
                return
            seen.add(sym)
        seen.add(key)
        out.append({
            "from": frm,
            "rel": rel,
            "to": to,
            "evidence": _evidence_window(sentence, frm, to),
            "source": "local",
        })

    for raw_sentence in _SENT_SPLIT.split(text or ""):
        sentence = raw_sentence.strip()
        if len(sentence) < 4:
            continue
        spans = _find_names(sentence, alias_map)
        if len(spans) < 2:
            continue
        # 以 (位置, 规范名) 序列为锚，逐个模板在原文上匹配
        # 模板里直接用真实名字（转义），命中后经 alias_map 归一。
        names = sorted(
            {s[2] for s in spans},
            key=len, reverse=True)
        if len(names) < 2:
            continue
        # 注意：裸交替必须整体包一层 (?:…)，否则在 A(?:…|name){1,3}
        # 这类结构里分隔符只会和第一个名字绑定。
        nalt = "(?:%s)" % "|".join(re.escape(n) for n in names)
        N1 = f"(?P<N1>{nalt})"
        N2 = f"(?P<N2>{nalt})"
        N3 = f"(?P<N3>{nalt})"

        def canon(matched_name: str) -> str:
            return alias_map.get(matched_name, matched_name)

        # 1) A 是 B 的 <rel>：「方知有是李长生的弟子」
        for m in re.finditer(rf"{N1}是{N2}的(?:{rel_alt})", sentence):
            rel = _REL_SYNONYMS[_word_in(m, sentence, rel_words)]
            _add(canon(m.group("N1")), rel, canon(m.group("N2")), sentence)

        # 2) A 的 <rel> 是 B：「贾宝玉的父亲是贾政」
        for m in re.finditer(rf"{N1}的(?:{rel_alt})是{N2}", sentence):
            rel = _REL_SYNONYMS[_word_in(m, sentence, rel_words)]
            _add(canon(m.group("N1")), rel, canon(m.group("N2")), sentence)

        # 3) A，B 的 <rel> / A（B 的 <rel>）：「方知有，李长生的弟子」
        for m in re.finditer(
                rf"{N1}\s*[，,、（(]\s*{N2}的(?:{rel_alt})", sentence):
            rel = _REL_SYNONYMS[_word_in(m, sentence, rel_words)]
            _add(canon(m.group("N1")), rel, canon(m.group("N2")), sentence)

        # 4) B 的 <rel> A 紧邻：「李长生的弟子方知有」
        for m in re.finditer(rf"{N1}的(?:{rel_alt}){N2}", sentence):
            rel = _REL_SYNONYMS[_word_in(m, sentence, rel_words)]
            _add(canon(m.group("N2")), rel, canon(m.group("N1")), sentence)

        # 5) A 拜 B 为师 / 拜入 B 门下
        for m in re.finditer(rf"{N1}\s*拜(?:入|投)?{N2}\s*(?:门下|为师)",
                             sentence):
            _add(canon(m.group("N1")), "师父", canon(m.group("N2")),
                 sentence)

        # 6) A 娶了 B → A 是 B 的丈夫；A 嫁给 B → A 是 B 的妻子
        for m in re.finditer(rf"{N1}\s*娶(?:了|过)?\s*{N2}", sentence):
            _add(canon(m.group("N1")), "丈夫", canon(m.group("N2")),
                 sentence)
        for m in re.finditer(rf"{N1}\s*嫁给?\s*{N2}", sentence):
            _add(canon(m.group("N1")), "妻子", canon(m.group("N2")),
                 sentence)

        # 7) A 和 B 是 <对称关系>；A 与 B 成亲/结为夫妻
        for m in re.finditer(
                rf"{N1}\s*[和与跟]\s*{N2}\s*是(?:一对)?(?:{sym_alt})",
                sentence):
            rel = _SYMMETRIC[_word_in(m, sentence, sym_words)]
            _add(canon(m.group("N1")), rel, canon(m.group("N2")), sentence)
        for m in re.finditer(
                rf"{N1}\s*[和与跟]\s*{N2}\s*(?:成亲|结为夫妻|结为夫妇)",
                sentence):
            _add(canon(m.group("N1")), "妻子", canon(m.group("N2")),
                 sentence)

        # 8) A1、A2（、A3）三人都是 B 的 <rel>
        for m in re.finditer(
                rf"(?P<alist>{nalt}(?:\s*[、，,]\s*{nalt}){{1,3}})"
                rf"\s*(?:三人|二人|两人|俩人|等人|几个)?\s*都是?\s*{N1}的(?:{rel_alt})",
                sentence):
            b = canon(m.group("N1"))
            rel_word = _word_in(m, sentence, rel_words)
            rel = _REL_SYNONYMS[rel_word]
            for nm in re.finditer(nalt, m.group("alist")):
                _add(canon(nm.group(0)), rel, b, sentence)

        if len(out) >= _MAX_RESULTS:
            break

    return out[:_MAX_RESULTS]


# 关系词没有单独的捕获组，取"匹配区间内命中的最长词"回填。
def _word_in(match, sentence: str, words: List[str]) -> str:
    segment = sentence[match.start():match.end()]
    for w in words:  # words 已按长度降序
        if w in segment:
            return w
    return words[-1]
