# utils/proofreader.py
"""墨码写作 - 错别字/敏感词检测（纯本地，无需联网）

数据来源：
- 内置常见错别字规则与网文敏感词
- 用户自定义词库（data_root/proof_dict.json），支持：
  · typos:     {错词: 正确词}
  · sensitive: {分类: [词, ...]}
  · ignored:   被忽略的规则/词条列表（不再报告）

敏感词支持批量导入 / 导出：
- 导入：多行文本，每行一个词；兼容 CSV/TSV（只取第一个字段）
- 导出：内置 + 自定义全部导出为 TSV，便于 Excel 查看或重新导入

全书校对：work 层传入章节列表即可，本模块保持无状态。
"""
import os
import re
import json
from typing import List, Tuple
from .logger import get_logger
logger = get_logger()

# 常见错别字：正确 -> [常见误用]（保留供参考，暂不作为强制规则）
COMMON_TYPOS = {
    "既": ["即"], "即": ["既"],
    "在": ["再"], "再": ["在"],
    "做": ["作"], "作": ["做"],
    "戴": ["带"], "带": ["戴"],
    "幸": ["辛"], "辛": ["幸"],
    "部": ["布"], "布": ["部"],
    "采": ["彩"], "彩": ["采"],
    "园": ["圆"], "圆": ["园"],
    "废": ["费"], "费": ["废"],
    "练": ["炼"], "炼": ["练"],
    "象": ["像"], "像": ["象"],
    "附": ["负"], "负": ["附"],
    "历": ["厉"], "厉": ["历"],
    "代": ["带"], "题": ["提"], "提": ["题"],
}

# 规则型检测：pattern -> (类别, 提示语, 建议)
# 每项都可被"忽略"（按 pattern 字符串作为 key）
TYPO_RULES: List[Tuple[str, str, str, str]] = [
    (r"的的", "错别字", "连续的\"的的\"，可能笔误", ""),
    (r"了了", "错别字", "连续的\"了了\"，可能笔误", ""),
    (r"是是", "错别字", "连续的\"是是\"，可能笔误", ""),
    (r"在在", "错别字", "连续的\"在在\"，可能笔误", ""),
    (r"和和", "错别字", "连续的\"和和\"，可能笔误", ""),
    (r"(\S)\1{3,}", "错别字", "同一字符连续出现 4 次以上，检查是否笔误", ""),
    (r"[，。！？；：、]{2,}", "标点", "连续标点，检查是否笔误", ""),
    # 以下 7 条：同字符标点重复，可安全批量替换
    (r"。。+", "标点", "多余的句号", "。"),
    (r"，，+", "标点", "多余的逗号", "，"),
    (r"！！+", "标点", "多余的感叹号", "！"),
    (r"？？+", "标点", "多余的问号", "？"),
    (r"；；+", "标点", "多余的分号", "；"),
    (r"：：+", "标点", "多余的冒号", "："),
    (r"、、+", "标点", "多余的顿号", "、"),
    # 以下三条保留"只提示不批量"：
    (r"[ \t]{4,}", "格式", "连续空格过多，建议精简", ""),
    (r"^[\s]*[。]", "标点", "段落以句号开头，检查是否漏字", ""),
    (r"[，。！？][，。！？]", "标点", "标点叠用，检查是否笔误", ""),
    # ---- 英文标点混入中文 ----
    # 定位：写作软件默认全中文标点，任何英文标点出现在中文上下文里都是问题。
    # 判定：标点**任一侧是中文**就报。这样中X、X中、中X、X中四种都能覆盖。
    # 用环视 `(?<=中)|(?=中)` 只匹配标点本身，避免"好,世"被两边各匹配一次。
    # 不误报的场景：a,b 英文逗号 / 1,000 千分位 / URL / 邮箱 / 纯英文句子。
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff])),", "标点",
     "中文里用了英文逗号「,」，建议改为「，」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))\.", "标点",
     "中文里用了英文句号「.」，建议改为「。」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))!", "标点",
     "中文里用了英文感叹号「!」，建议改为「！」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))\?", "标点",
     "中文里用了英文问号「?」，建议改为「？」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]));", "标点",
     "中文里用了英文分号「;」，建议改为「；」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff])):", "标点",
     "中文里用了英文冒号「:」，建议改为「：」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))\(", "标点",
     "中文里用了英文左括号「(」，建议改为「（」", ""),
    (r"(?:(?<=[\u4e00-\u9fff])|(?=[\u4e00-\u9fff]))\)", "标点",
     "中文里用了英文右括号「)」，建议改为「）」", ""),
]

# 内置敏感词（网文常见）
SENSITIVE_WORDS = {
    "暴力": ["屠杀", "灭绝", "大屠杀"],
    "色情": ["淫秽", "色情", "裸体"],
    "赌博": ["赌场", "博彩", "六合彩"],
    "毒品": ["冰毒", "海洛因", "摇头丸", "K粉"],
    "其他": ["自杀方法", "炸弹制作"],
}

# 轻度提示词
HINT_WORDS = {
    "呵呵": "网络用语，正式出版可能需改写",
    "哈哈": "网络用语，注意使用场景",
    "么么": "网络用语",
    "666": "网络用语",
    "nb": "网络用语/粗俗缩写",
}

# 内置词库中不可被"忽略"的类别（保护基本功能）
_PROTECTED_CATS = {"敏感词"}


# ---------- 引号配对检测 ----------

_QUOTE_PAIRS = {
    "“": "”", "‘": "’",
    "「": "」", "『": "』",
}
_QUOTE_OPENERS = frozenset(_QUOTE_PAIRS.keys())
_QUOTE_CLOSERS = frozenset(_QUOTE_PAIRS.values())


def check_quote_balance(text: str) -> List[dict]:
    """检测引号配对错误（栈式扫描，能处理嵌套与跨行）。"""
    if not text:
        return []
    results: List[dict] = []
    stack: List[Tuple[str, int]] = []

    for pos, ch in enumerate(text):
        if ch in _QUOTE_OPENERS:
            stack.append((ch, pos))
        elif ch in _QUOTE_CLOSERS:
            if not stack:
                results.append(_make_quote_issue(
                    text, pos,
                    f"孤儿闭引号「{ch}」——前面没有对应的开引号",
                    "检查这一行是否丢了开引号"))
            else:
                open_ch, _ = stack.pop()
                expected = _QUOTE_PAIRS[open_ch]
                if ch != expected:
                    results.append(_make_quote_issue(
                        text, pos,
                        f"闭引号「{ch}」与开引号「{open_ch}」不匹配",
                        f"建议把「{ch}」改成「{expected}」"))

    for open_ch, pos in stack:
        results.append(_make_quote_issue(
            text, pos,
            f"开引号「{open_ch}」没有闭合",
            f"若是跨段对话请忽略；否则补上「{_QUOTE_PAIRS[open_ch]}」"
            "或删掉这个开引号"))

    # 半角双引号：**每个位置都报一条**，用户可在列表里逐个定位。
    # 不再区分"成对/奇数"——写作软件里任何半角引号都要改。
    pos = 0
    while True:
        idx = text.find('"', pos)
        if idx < 0:
            break
        results.append(_make_quote_issue(
            text, idx,
            "半角双引号「\"」，建议改为中文引号「“ ”」",
            "可用「一键排版 → 英文标点转中文」批量转换"))
        pos = idx + 1

    # 半角单引号同理（英文缩写撇号已在上面的扫描里排除）
    pos = 0
    while True:
        idx = text.find("'", pos)
        if idx < 0:
            break
        results.append(_make_quote_issue(
            text, idx,
            "半角单引号「'」，建议改为中文引号「‘ ’」",
            "可用「一键排版 → 英文标点转中文」批量转换"))
        pos = idx + 1

    return results


def _make_quote_issue(text: str, pos: int,
                      message: str, suggestion: str) -> dict:
    """构造引号问题条目，与 check_text 返回结构保持一致。"""
    return {
        "start": pos,
        "end": pos + 1,
        "snippet": _make_snippet(text, pos, pos + 1, ctx=12),
        "category": "引号配对",
        "message": message,
        "suggestion": suggestion,
        "replaceable": False,
        "ignore_key": f"quote::{pos}",
    }


# ---------- 用户词库读写 ----------

def get_dict_path(data_root: str) -> str:
    return os.path.join(data_root, "proof_dict.json")


def _default_dict() -> dict:
    return {"typos": {}, "sensitive": {}, "ignored": []}


# 进程内缓存：{data_root: (mtime, data_dict)}
# 校对结果里连点 N 次「忽略」会触发 N 次 load + N 次 check_text（内部
# 也 load 一次），没有缓存时每次都重读 JSON + 反序列化；敏感词库几万条
# 时单次几十 ms，对话框会明显卡。写路径 save 会清掉对应缓存，下次读时
# 自然重新加载。
_DICT_CACHE: dict = {}


def load_custom_dict(data_root: str) -> dict:
    """读取用户自定义词库，返回 {"typos": {}, "sensitive": {}, "ignored": []}

    命中缓存时直接返回缓存对象；调用方若会修改返回值，请**先就地修改再
    立即 save**——save 会清缓存，下一次 load 从磁盘重读。
    目前所有调用方（check_text / add_typo / ignore_item / unignore_item）
    都遵守这个约定。
    """
    data = _default_dict()
    if not data_root:
        return data
    path = get_dict_path(data_root)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # 文件不存在：清缓存（用户可能删过），返回默认
        _DICT_CACHE.pop(data_root, None)
        return data

    cached = _DICT_CACHE.get(data_root)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            if isinstance(raw.get("typos"), dict):
                data["typos"] = {str(k): str(v) for k, v in raw["typos"].items()}
            if isinstance(raw.get("sensitive"), dict):
                data["sensitive"] = {
                    str(k): [str(x) for x in (v or [])]
                    for k, v in raw["sensitive"].items()
                }
            if isinstance(raw.get("ignored"), list):
                data["ignored"] = [str(x) for x in raw["ignored"]]
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    _DICT_CACHE[data_root] = (mtime, data)
    return data


def save_custom_dict(data_root: str, data: dict):
    """原子写入用户词库。写完后清缓存，下次 load 从磁盘重读。"""
    if not data_root:
        return
    os.makedirs(data_root, exist_ok=True)
    path = get_dict_path(data_root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    _DICT_CACHE.pop(data_root, None)


def add_typo(data_root: str, wrong: str, correct: str) -> bool:
    """新增/更新一条错别字对。返回是否写入成功。"""
    wrong = (wrong or "").strip()
    correct = (correct or "").strip()
    if not wrong or not correct:
        return False
    data = load_custom_dict(data_root)
    data["typos"][wrong] = correct
    save_custom_dict(data_root, data)
    return True


def remove_typo(data_root: str, wrong: str) -> bool:
    data = load_custom_dict(data_root)
    if wrong in data["typos"]:
        del data["typos"][wrong]
        save_custom_dict(data_root, data)
        return True
    return False


def add_sensitive(data_root: str, category: str, word: str) -> bool:
    category = (category or "").strip()
    word = (word or "").strip()
    if not category or not word:
        return False
    data = load_custom_dict(data_root)
    cat_list = data["sensitive"].setdefault(category, [])
    if word not in cat_list:
        cat_list.append(word)
    save_custom_dict(data_root, data)
    return True


def remove_sensitive(data_root: str, category: str, word: str) -> bool:
    data = load_custom_dict(data_root)
    cat_list = data["sensitive"].get(category) or []
    if word in cat_list:
        cat_list.remove(word)
        if not cat_list:
            del data["sensitive"][category]
        save_custom_dict(data_root, data)
        return True
    return False


def is_ignored(data_root: str, key: str) -> bool:
    return key in load_custom_dict(data_root)["ignored"]


def ignore_item(data_root: str, key: str):
    data = load_custom_dict(data_root)
    if key and key not in data["ignored"]:
        data["ignored"].append(key)
        save_custom_dict(data_root, data)


def unignore_item(data_root: str, key: str):
    data = load_custom_dict(data_root)
    if key in data["ignored"]:
        data["ignored"].remove(key)
        save_custom_dict(data_root, data)


def clear_ignored(data_root: str):
    data = load_custom_dict(data_root)
    data["ignored"] = []
    save_custom_dict(data_root, data)


# ---------- 检测核心 ----------

def _make_snippet(text: str, start: int, end: int, ctx: int = 8) -> str:
    """生成带匹配标记的上下文片段，匹配部分用【】包裹。"""
    pre = text[max(0, start - ctx):start].replace("\n", " ")
    mid = text[start:end].replace("\n", " ")
    post = text[end:end + ctx].replace("\n", " ")
    prefix = "…" if start - ctx > 0 else ""
    suffix = "…" if end + ctx < len(text) else ""
    return f"{prefix}{pre}【{mid}】{post}{suffix}"


def check_text(text: str, data_root: str = "") -> List[dict]:
    """检查文本，返回问题列表。

    每项字段：
      start, end        字符位置
      snippet           带【】标记的上下文
      category          类别
      message           说明
      suggestion        建议替换（空串表示无建议）
      replaceable       True 表示可一键替换（双击/批量替换生效）
      ignore_key        用于"忽略此条"的唯一键
    """
    results: List[dict] = []
    if not text:
        return results

    custom = load_custom_dict(data_root) if data_root else _default_dict()
    ignored = set(custom.get("ignored") or [])

    # 1. 规则型检测
    for pattern, cat, msg, sugg in TYPO_RULES:
        if pattern in ignored:
            continue
        try:
            rx = re.compile(pattern, re.MULTILINE)
        except re.error:
            continue
        for m in rx.finditer(text):
            start, end = m.start(), m.end()
            results.append({
                "start": start, "end": end,
                "snippet": _make_snippet(text, start, end),
                "category": cat,
                "message": msg,
                "suggestion": sugg,
                "replaceable": bool(sugg),   # 有具体替换词才可一键替换
                "ignore_key": pattern,
            })

    # 2. 敏感词（内置 + 自定义）
    all_sensitive = {}
    for cat, words in SENSITIVE_WORDS.items():
        for w in words:
            all_sensitive[w] = f"敏感词({cat})"
    for cat, words in (custom.get("sensitive") or {}).items():
        for w in (words or []):
            all_sensitive[w] = f"敏感词({cat})"

    for word, cat_label in all_sensitive.items():
        ignore_key = f"word::{word}"
        if ignore_key in ignored:
            continue
        idx = 0
        while True:
            pos = text.find(word, idx)
            if pos < 0:
                break
            results.append({
                "start": pos, "end": pos + len(word),
                "snippet": _make_snippet(text, pos, pos + len(word)),
                "category": cat_label,
                "message": f"包含敏感词「{word}」",
                "suggestion": "建议替换或删除",
                "replaceable": False,   # 敏感词只是提示，不自动替换
                "ignore_key": ignore_key,
            })
            idx = pos + len(word)

    # 3. 用语提示
    for word, msg in HINT_WORDS.items():
        ignore_key = f"hint::{word}"
        if ignore_key in ignored:
            continue
        idx = 0
        while True:
            pos = text.find(word, idx)
            if pos < 0:
                break
            results.append({
                "start": pos, "end": pos + len(word),
                "snippet": _make_snippet(text, pos, pos + len(word)),
                "category": "用语提示",
                "message": f"「{word}」{msg}",
                "suggestion": "",
                "replaceable": False,
                "ignore_key": ignore_key,
            })
            idx = pos + len(word)

    # 4. 自定义错别字
    for wrong, correct in (custom.get("typos") or {}).items():
        ignore_key = f"typo::{wrong}"
        if ignore_key in ignored:
            continue
        idx = 0
        while True:
            pos = text.find(wrong, idx)
            if pos < 0:
                break
            results.append({
                "start": pos, "end": pos + len(wrong),
                "snippet": _make_snippet(text, pos, pos + len(wrong)),
                "category": "自定义错别字",
                "message": f"「{wrong}」建议改为「{correct}」",
                "suggestion": correct,
                "replaceable": True,   # 有明确替换词
                "ignore_key": ignore_key,
            })
            idx = pos + len(wrong)

    # 5. 引号配对
    try:
        results.extend(check_quote_balance(text))
    except Exception as e:
        logger.warning(f"引号配对检测失败: {e}")

    results.sort(key=lambda x: (x["start"], x["end"]))
    return results


def check_work(work, data_root: str = "", progress_cb=None) -> List[dict]:
    """全书校对。返回问题列表，每项在 check_text 基础上加：
      chapter_id, chapter_title, chapter_index

    参数：
      work        Work 实例
      data_root   数据根目录（读词库用）
      progress_cb 可选回调 (done, total, title)，用于显示进度
    """
    if work is None:
        return []
    chapters = work.chapters or []
    total = len(chapters)
    results: List[dict] = []
    for i, ch in enumerate(chapters):
        cid = ch.get("id")
        title = ch.get("title", "")
        if progress_cb:
            try:
                # 回调返回 False 表示用户取消，立即停止扫描
                if progress_cb(i, total, title) is False:
                    break
            except Exception:
                pass
        text = work.read_chapter(cid)
        if not text:
            continue
        for item in check_text(text, data_root):
            item["chapter_id"] = cid
            item["chapter_title"] = title
            item["chapter_index"] = i
            results.append(item)
    else:
        # 只有正常跑完（未 break）才上报 100%
        if progress_cb:
            try:
                progress_cb(total, total, "")
            except Exception:
                pass
    return results


# ---------- 敏感词批量导入 / 导出 ----------

def import_sensitive_words(data_root: str, text: str,
                           category: str = "自定义") -> int:
    """批量导入敏感词。返回实际新增的数量（去重后的）。

    text 每行一个词；空行忽略。
    支持 CSV / TSV 格式：每行取第一个字段作为词，
    例如 "涉政\\t某某" 只取 "某某"。
    """
    if not data_root or not text:
        return 0
    category = (category or "自定义").strip() or "自定义"
    seen = set()
    words = []
    for line in text.splitlines():
        w = line.strip()
        if not w:
            continue
        # 兼容 csv/tsv：只取第一个字段
        if "\t" in w:
            w = w.split("\t", 1)[0].strip()
        elif "," in w:
            w = w.split(",", 1)[0].strip()
        if not w or w in seen:
            continue
        seen.add(w)
        words.append(w)
    if not words:
        return 0
    data = load_custom_dict(data_root)
    cat_list = data["sensitive"].setdefault(category, [])
    existing = set(cat_list)
    added = 0
    for w in words:
        if w not in existing:
            cat_list.append(w)
            existing.add(w)
            added += 1
    if added:
        save_custom_dict(data_root, data)
    return added


def export_sensitive_words(data_root: str) -> str:
    """导出所有敏感词（内置 + 自定义）为文本。

    格式：每行 `分类\\t词`，便于 Excel 打开或重新导入。
    """
    lines = []
    for cat, words in SENSITIVE_WORDS.items():
        for w in words:
            lines.append(f"{cat}\t{w}")
    data = load_custom_dict(data_root)
    for cat, words in (data.get("sensitive") or {}).items():
        for w in words:
            lines.append(f"{cat}\t{w}")
    return "\n".join(lines)