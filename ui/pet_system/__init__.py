# ui/pet_system/__init__.py
"""墨码写作 - 宠物系统。

分层：
- state          数据层：生理值 + 亲密度 + 衰减计算
- persistence    存档：pet_data.json 读写
- constants      常量
- actions        行为定义（后续）
- brain          行为引擎（后续）
- animations     动画播放（后续）
"""

from .state import PetState
from .persistence import load_state, save_state

__all__ = ["PetState", "load_state", "save_state"]