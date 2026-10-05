# utils/ai_conversations.py
"""墨码写作 - AI 对话历史持久化

按"对话上下文"分桶存储：
- "" (空字符串)  通用对话（未打开作品时）
- 其他 key        用作品目录名，如 "我的小说" / "我的小说_2"

每个上下文独立存 messages，**不含 system prompt**。
system prompt 每次从 ai_client.get_system_prompt() 取，
设置里改了立即对下一条消息生效。

存储：data_root/ai_conversations.json
"""
import os
import json
from datetime import datetime
from typing import List, Dict

from .logger import get_logger
from .work_store import get_data_root, atomic_write_json

logger = get_logger()

_FILE = "ai_conversations.json"
_VERSION = 1
# 单次对话最多保留的轮数（一轮 = user + assistant）。
# 必须与 ui/ai_panel.py 里的 MAX_HISTORY_TURNS 保持一致：
# 若这边存得比那边多，加载后会在首次新请求时被裁掉一半，看起来像"历史丢了"。
MAX_TURNS = 20


def _file_path() -> str:
    return os.path.join(get_data_root(), _FILE)


def _load_all() -> dict:
    path = _file_path()
    if not os.path.exists(path):
        return {"version": _VERSION, "conversations": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": _VERSION, "conversations": {}}
        convs = data.get("conversations", {})
        if not isinstance(convs, dict):
            convs = {}
        return {"version": _VERSION, "conversations": convs}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"AI 对话历史读取失败: {e}")
        return {"version": _VERSION, "conversations": {}}


def _save_all(data: dict):
    try:
        atomic_write_json(_file_path(), data)
    except OSError as e:
        logger.warning(f"AI 对话历史保存失败: {e}")


def load_messages(key: str) -> List[Dict[str, str]]:
    """加载指定上下文的 messages（不含 system）。"""
    data = _load_all()
    conv = data["conversations"].get(key or "")
    if not isinstance(conv, dict):
        return []
    msgs = conv.get("messages", [])
    if not isinstance(msgs, list):
        return []
    result = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            content = m.get("content", "")
            if isinstance(content, str):
                result.append({"role": m["role"], "content": content})
    return result


def save_messages(key: str, messages: List[Dict[str, str]]):
    """保存指定上下文的 messages（不含 system）。

    自动裁剪到 MAX_TURNS * 2 条，防止文件无限增长。
    """
    key = key or ""
    filtered = [m for m in messages
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)]
    limit = MAX_TURNS * 2
    if len(filtered) > limit:
        filtered = filtered[-limit:]

    data = _load_all()
    data["conversations"][key] = {
        "messages": filtered,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_all(data)


def clear_messages(key: str):
    """清空指定上下文（保留其他上下文）。"""
    key = key or ""
    data = _load_all()
    if key in data["conversations"]:
        del data["conversations"][key]
        _save_all(data)


def list_conversations() -> List[dict]:
    """列出所有对话的元信息（未来给"历史对话"列表用）。

    返回 [{"key": "...", "updated_at": "...", "message_count": N}, ...]
    按更新时间倒序。
    """
    data = _load_all()
    result = []
    for k, v in data["conversations"].items():
        if not isinstance(v, dict):
            continue
        msgs = v.get("messages", [])
        result.append({
            "key": k,
            "updated_at": v.get("updated_at", ""),
            "message_count": len(msgs) if isinstance(msgs, list) else 0,
        })
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result