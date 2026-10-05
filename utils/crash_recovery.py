# utils/crash_recovery.py
"""墨码写作 - 崩溃恢复草稿

机制：
- 用户编辑时，每 3 秒把编辑器内容写到 data_root/drafts/<作品名>/<章节id>.draft
- 正式保存（flush_save）成功后删除对应 draft
- 启动时扫描所有 draft：
  - 章节文件不存在 → 删 draft（章节被删了）
  - 章节内容与 draft 完全一致 → 删 draft（已保存过）
  - 有差异 → 保留，等用户打开作品时提示恢复

draft 文件格式（JSON）：
    {
        "chapter_id": "0001",
        "title": "第一章 开端",
        "text": "……",
        "updated_at": "2026-09-16 10:23:45",
        "work_root": "D:/.../works/我的小说"
    }

设计取舍：
- 用 JSON 而非常规 .txt：方便带元数据（标题、时间戳）
- 每章一个文件：避免单点损坏，写入快
- 3 秒周期：比 debounce 的 3 秒同级，但走独立计时器，
  用户持续输入时也能每 3 秒兜一次
"""
import os
import json
from datetime import datetime
from typing import List

from .logger import get_logger
from .work_store import get_data_root, atomic_write_text, safe_dir_name

logger = get_logger()

_DRAFTS_SUBDIR = "drafts"


def _drafts_root() -> str:
    root = os.path.join(get_data_root(), _DRAFTS_SUBDIR)
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        pass
    return root


def _work_draft_dir(work_root: str) -> str:
    name = safe_dir_name(os.path.basename(work_root.rstrip("/\\")) or "未命名")
    d = os.path.join(_drafts_root(), name)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def draft_path(work_root: str, chapter_id: str) -> str:
    return os.path.join(_work_draft_dir(work_root), f"{chapter_id}.draft")


def write_draft(work_root: str, chapter_id: str, title: str, text: str):
    """写一份草稿。空内容不写（避免把清空的编辑器当成有效草稿）。"""
    if not work_root or not chapter_id or not text:
        return
    data = {
        "chapter_id": str(chapter_id),
        "title": title or "",
        "text": text,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "work_root": work_root,
    }
    try:
        atomic_write_text(draft_path(work_root, chapter_id),
                          json.dumps(data, ensure_ascii=False))
    except OSError as e:
        logger.warning(f"写草稿失败: {e}")


def delete_draft(work_root: str, chapter_id: str):
    if not work_root or not chapter_id:
        return
    path = draft_path(work_root, chapter_id)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def list_pending_for_work(work_root: str) -> List[dict]:
    """列出某个作品目录下的所有残留 draft，按时间倒序。"""
    d = _work_draft_dir(work_root)
    result = []
    if not os.path.isdir(d):
        return result
    for fname in os.listdir(d):
        if not fname.endswith(".draft"):
            continue
        full = os.path.join(d, fname)
        try:
            with open(full, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "text" not in data:
                continue
            result.append({
                "file": full,
                "chapter_id": str(data.get("chapter_id", fname[:-6])),
                "title": data.get("title", ""),
                "text": data.get("text", ""),
                "updated_at": data.get("updated_at", ""),
                "work_root": work_root,
            })
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"读草稿失败 {full}: {e}")
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result


def list_all_pending() -> List[dict]:
    """扫描所有作品下的残留 draft（启动时用）。

    做法：遍历 data_root/drafts/ 的子目录，用目录名反查
    data_root/works/<同名>，找得到才算有效（作品可能已被删除）。
    """
    root = _drafts_root()
    if not os.path.isdir(root):
        return []
    works_dir = os.path.join(get_data_root(), "works")
    result = []
    for name in os.listdir(root):
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        work_root = os.path.join(works_dir, name)
        if not os.path.isdir(work_root):
            # 作品已不在，直接清掉这些孤儿草稿
            for fname in os.listdir(sub):
                if fname.endswith(".draft"):
                    try:
                        os.remove(os.path.join(sub, fname))
                    except OSError:
                        pass
            continue
        result.extend(list_pending_for_work(work_root))
    return result