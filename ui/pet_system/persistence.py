# ui/pet_system/persistence.py
"""墨码写作 - 宠物存档读写。

存档位置：<data_root>/pet_data.json
结构：
    {
      "version": 1,
      "saved_at": "2026-09-23T14:30:00",
      "state": { ...PetState 的 to_dict()... }
    }
"""

from __future__ import annotations

import os
import json
from datetime import datetime

from utils.logger import get_logger
from .state import PetState

logger = get_logger()

_FILENAME = "pet_data.json"
_VERSION = 1


def _path(data_root: str) -> str:
    return os.path.join(data_root, _FILENAME)


def load_state(data_root: str) -> PetState:
    """加载存档 + 补算离线衰减。文件不存在时返回初始状态。"""
    if not data_root:
        return PetState()
    p = _path(data_root)
    if not os.path.exists(p):
        st = PetState()
        # 新档案：记录当前时间，避免下次启动时补算一段不存在的离线时间
        st.last_update = datetime.now().isoformat(timespec="seconds")
        return st
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取宠物存档失败: {e}")
        return PetState()
    st = PetState.from_dict(data.get("state", {}))
    st.apply_offline_decay()
    return st


def save_state(data_root: str, state: PetState):
    """写盘。失败静默（不阻塞主流程）。"""
    if not data_root or state is None:
        return
    p = _path(data_root)
    try:
        os.makedirs(data_root, exist_ok=True)
        payload = {
            "version": _VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "state": state.to_dict(),
        }
        # 原子写：临时文件 + os.replace，避免写一半断电损坏存档
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError as e:
        logger.warning(f"保存宠物存档失败: {e}")