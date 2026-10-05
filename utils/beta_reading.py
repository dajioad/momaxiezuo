# utils/beta_reading.py
"""墨码写作 - 墨灵试读（纯逻辑，无 Qt、无网络）。

与已有的"追读体检"（编辑视角、10 维打分）不同，墨灵试读是
**读者视角的连续追更体验**：
- 墨灵按章节顺序一章章读，每章给一条很短的"追更读后感"
  （追读欲 / 开头钩子 / 结尾钩子 / 当场感受 / 是否想弃读）；
- 读完全部选定章节后，再用一次极短请求汇总总评
  （亮点 / 弃书点 / 节奏 / 修改建议）。

省 token 设计：
- 每章只发"开头 3000 字 + 结尾 800 字"，输出被严格限制在几句话；
- 总评请求只带上每章的短速记（几百字），不带正文；
- 章节本地统计（字数/对白占比/开头结尾句）零 token，
  AI 失败的章节也有东西可看。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List

HEAD_CHARS = 3000      # 每章送给 AI 的开头字数
TAIL_CHARS = 800       # 每章送给 AI 的结尾字数
MAX_CHAPTERS = 30      # 一次试读最多 30 章，防止误操作烧 token

_CHAPTER_SYSTEM = """你是墨灵，一只住在作者书里的小书灵，也是这本书的追更读者。
你现在按顺序读这本书的章节，请用真实追更读者的口吻反应，不要像编辑写审稿报告，不要复述剧情。"""

_CHAPTER_TEMPLATE = """作品：《{work}》　这是第 {index} 章《{title}》（共 {total} 章试读）。
{prev_block}
读完后只输出 JSON，不要解释、不要 markdown 代码块：
{{
  "want": 读完后想立刻追下一章的欲望，1 到 5 的整数,
  "hook": "开头抓不抓人，20 字以内",
  "ending": "结尾有没有留下钩子，20 字以内；没有就填「平」",
  "feeling": "你作为读者此刻的真实感受，第一人称，25 字以内",
  "drop": "最想弃读的原因，一句话；不想弃读就填「无」"
}}

【本章正文】（超长章节只截取开头与结尾）
{text}"""

_SUMMARY_TEMPLATE = """你是墨灵，刚按顺序读完《{work}》的 {total} 章。
下面是你每章读完时随手记的短评：

{digest}

请把它们汇总成一份给作者的试读报告，只输出 JSON，不要解释、不要 markdown：
{{
  "verdict": "一句话总评，30 字以内",
  "strengths": ["最抓人的地方，最多 3 条，每条 25 字以内"],
  "drop_points": ["最可能让读者弃书的地方，最多 3 条，要标明第几章，每条 30 字以内"],
  "rhythm": "节奏问题一句话；没有明显问题就填「节奏整体顺畅」",
  "advice": ["最值得改的，最多 3 条，每条具体可执行，30 字以内"]
}}"""


# ---------------- 发送内容截断与预估 ----------------

def clip_chapter_text(text: str) -> str:
    text = text or ""
    if len(text) <= HEAD_CHARS + TAIL_CHARS + 200:
        return text
    head = text[:HEAD_CHARS]
    tail = text[-TAIL_CHARS:]
    return f"{head}\n\n……（中略 {len(text) - HEAD_CHARS - TAIL_CHARS} 字）……\n\n{tail}"


def estimate_chars(text: str) -> int:
    """预估该章实际会发送的字数。"""
    return len(clip_chapter_text(text))


# ---------------- 本地零 token 统计 ----------------

def local_stats(text: str) -> Dict:
    """章节本地统计：字数 / 段落 / 对白占比 / 开篇句 / 收尾句 / 悬念启发式。"""
    text = text or ""
    body = re.sub(r"\s+", "", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    dialogue_chars = 0
    for l in lines:
        if ("“" in l and "”" in l) or l.startswith("「") or "：" in l[:8] and '"' in l:
            dialogue_chars += len(re.sub(r"\s+", "", l))
    dialogue_ratio = round(dialogue_chars / len(body), 2) if body else 0.0

    opening = lines[0][:40] if lines else ""
    ending = lines[-1][-40:] if lines else ""

    # 结尾悬念启发式（纯字符判断，给报告做个零成本小标签）
    cliff = "平"
    if ending:
        tail = ending[-12:]
        if re.search(r"[？?…]$", tail) or tail.endswith("」”"):
            cliff = "疑问/留白"
        elif re.search(r"[！!]$", tail):
            cliff = "强情绪收尾"
        elif re.search(r"(说|问|道|低声|开口|抬眼|望去)", tail):
            cliff = "动作/对话留白"
    return {
        "chars": len(body),
        "paras": len(lines),
        "dialogue_ratio": dialogue_ratio,
        "opening": opening,
        "ending": ending,
        "cliff": cliff,
    }


# ---------------- prompt 构造 ----------------

def build_chapter_messages(work_title: str, index: int, total: int,
                           title: str, text: str,
                           prev_digest: str = "") -> List[Dict[str, str]]:
    prev_block = (
        "你之前几章的读后速记（要保持连续追读的感觉，"
        "注意钩子有没有被接住）：\n" + prev_digest.strip()
        if prev_digest.strip() else
        "这是你读到的第一章，没有前文。"
    )
    user = _CHAPTER_TEMPLATE.format(
        work=work_title or "未命名作品",
        index=index,
        total=total,
        title=title or f"第{index}章",
        prev_block=prev_block,
        text=clip_chapter_text(text),
    )
    return [
        {"role": "system", "content": _CHAPTER_SYSTEM},
        {"role": "user", "content": user},
    ]


def build_summary_messages(work_title: str, total: int,
                           digest_lines: List[str]) -> List[Dict[str, str]]:
    digest = "\n".join(digest_lines) or "（各章均未读到内容）"
    user = _SUMMARY_TEMPLATE.format(
        work=work_title or "未命名作品",
        total=total,
        digest=digest,
    )
    return [
        {"role": "system", "content": _CHAPTER_SYSTEM},
        {"role": "user", "content": user},
    ]


# ---------------- 响应解析 ----------------

def _loads_lenient(text: str):
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
    return None


def _clamp_int(v, lo=1, hi=5) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 3
    return max(lo, min(hi, n))


def _short(v, limit: int) -> str:
    s = str(v or "").strip().replace("\n", " ")
    return s[:limit]


def parse_chapter_response(text: str) -> Dict:
    data = _loads_lenient(text)
    if not isinstance(data, dict):
        return {}
    return {
        "want": _clamp_int(data.get("want", 3)),
        "hook": _short(data.get("hook"), 40),
        "ending": _short(data.get("ending"), 40),
        "feeling": _short(data.get("feeling"), 50),
        "drop": _short(data.get("drop"), 60),
    }


def parse_summary_response(text: str) -> Dict:
    data = _loads_lenient(text)
    if not isinstance(data, dict):
        return {}

    def _lst(key, limit_items=3, limit_len=40):
        v = data.get(key)
        if not isinstance(v, list):
            return []
        out = []
        for x in v[:limit_items]:
            s = _short(x, limit_len)
            if s:
                out.append(s)
        return out

    return {
        "verdict": _short(data.get("verdict"), 60),
        "strengths": _lst("strengths"),
        "drop_points": _lst("drop_points"),
        "rhythm": _short(data.get("rhythm"), 60),
        "advice": _lst("advice"),
    }


def digest_line(index: int, title: str, reaction: Dict) -> str:
    """把一章的短评压成一行，供后续章节和总评请求携带。"""
    if not reaction:
        return f"第{index}章《{title}》：（未读到有效内容）"
    parts = [
        f"追读欲{reaction.get('want', 3)}",
        reaction.get("hook", ""),
        reaction.get("ending", ""),
        reaction.get("feeling", ""),
    ]
    drop = reaction.get("drop", "")
    if drop and drop != "无":
        parts.append(f"想弃读：{drop}")
    return f"第{index}章《{title}》：" + "；".join(p for p in parts if p)
